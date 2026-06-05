"""Weekly-trained irrigation duration prediction from existing sensor data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any

from app.models import (
    db,
    IrrigationPredictionModel,
    Location,
    Sensor,
    SENSOR_TYPE_IRRIGATION,
    SENSOR_TYPE_RAINFALL,
    SENSOR_TYPE_SOIL_MOISTURE,
    SENSOR_TYPE_TEMPERATURE,
)
from app.services import influx_service

TRAINING_INTERVAL = timedelta(days=7)
TRAINING_LOOKBACK = timedelta(days=90)
LATEST_LOOKBACK = timedelta(hours=24)
DEFAULT_TARGET_SOIL_MOISTURE_PERCENT = 55.0
MIN_TRAINING_SAMPLES = 2
RIDGE_LAMBDA = 0.1
FEATURE_NAMES = (
    'soil_moisture_percent',
    'target_soil_moisture_percent',
    'moisture_deficit_percent',
    'temperature_c',
    'rainfall_mm',
    'previous_irrigation_minutes',
)


@dataclass(frozen=True)
class PredictionConfig:
    max_minutes: float = 120.0
    train_interval: timedelta = TRAINING_INTERVAL
    training_lookback: timedelta = TRAINING_LOOKBACK


def prediction_config_from_app(app_config: Any) -> PredictionConfig:
    return PredictionConfig(
        max_minutes=_positive_float(app_config.get('IRRIGATION_PREDICTION_MAX_MINUTES'), 120.0),
        train_interval=timedelta(days=_positive_float(app_config.get('IRRIGATION_PREDICTION_TRAIN_INTERVAL_DAYS'), 7.0)),
        training_lookback=timedelta(days=_positive_float(app_config.get('IRRIGATION_PREDICTION_TRAINING_LOOKBACK_DAYS'), 90.0)),
    )


def predict_for_location(
    location: Location,
    target_soil_moisture_percent: float | None,
    max_minutes: float = 120.0,
    adapter: influx_service.SensorTimeSeriesAdapter | None = None,
    now: datetime | None = None,
    train_if_due: bool = True,
    train_interval: timedelta = TRAINING_INTERVAL,
    training_lookback: timedelta = TRAINING_LOOKBACK,
) -> dict[str, Any]:
    """Return an API-ready irrigation prediction for one bed/location."""
    now = _as_utc(now or datetime.now(timezone.utc))
    target = _target_or_default(target_soil_moisture_percent)
    adapter = adapter or influx_service.get_sensor_time_series_adapter()

    model = _model_for_location(location)
    trained = False
    training_error = None
    if train_if_due and _training_due(model, now, train_interval):
        try:
            model = train_model_for_location(location, target, adapter, now, max_minutes, training_lookback)
            trained = True
        except Exception as exc:  # pragma: no cover - concrete InfluxDB failures are integration-specific
            training_error = str(exc)
            model = _model_for_location(location)

    features, feature_details = _current_features(location, target, adapter, now)
    raw_minutes = None
    source = 'unavailable'
    if features is not None and model and model.sample_count >= MIN_TRAINING_SAMPLES:
        raw_minutes = model.intercept + sum(c * v for c, v in zip(model.coefficients_list, features))
        source = 'model'
    elif features is not None:
        raw_minutes = max(0.0, (target - features[0]) * 5.0)
        source = 'heuristic'

    predicted = clamp_minutes(raw_minutes, max_minutes) if raw_minutes is not None else None
    return {
        'location_id': location.id,
        'location_name': location.name,
        'target_soil_moisture_percent': target,
        'predicted_minutes': predicted,
        'raw_predicted_minutes': round(raw_minutes, 2) if raw_minutes is not None else None,
        'max_minutes': max_minutes,
        'source': source,
        'trained_now': trained,
        'training_error': training_error,
        'model': None if not model else {
            'trained_at': model.trained_at.isoformat() if model.trained_at else None,
            'sample_count': model.sample_count,
            'feature_names': list(FEATURE_NAMES),
            'metrics': model.metrics_dict,
        },
        'features': feature_details,
    }


def train_model_for_location(
    location: Location,
    target_soil_moisture_percent: float | None,
    adapter: influx_service.SensorTimeSeriesAdapter,
    now: datetime | None = None,
    max_minutes: float = 120.0,
    training_lookback: timedelta = TRAINING_LOOKBACK,
) -> IrrigationPredictionModel:
    now = _as_utc(now or datetime.now(timezone.utc))
    target = _target_or_default(target_soil_moisture_percent)
    start = now - training_lookback
    datasets = _load_training_datasets(location, adapter, start, now)
    samples = _training_samples(datasets, target, max_minutes)

    if len(samples) >= MIN_TRAINING_SAMPLES:
        x_rows = [sample[0] for sample in samples]
        y_values = [sample[1] for sample in samples]
        intercept, coefficients = _fit_ridge_regression(x_rows, y_values)
        rmse = _rmse(x_rows, y_values, intercept, coefficients)
    else:
        intercept = 0.0
        coefficients = [5.0 if name == 'moisture_deficit_percent' else 0.0 for name in FEATURE_NAMES]
        rmse = None

    model = _model_for_location(location)
    if model is None:
        model = IrrigationPredictionModel(location_id=location.id)
        db.session.add(model)
    model.trained_at = now
    model.sample_count = len(samples)
    model.intercept = intercept
    model.coefficients_json = json.dumps(coefficients, separators=(',', ':'))
    model.feature_names_json = json.dumps(list(FEATURE_NAMES), separators=(',', ':'))
    model.metrics_json = json.dumps({'rmse': rmse}, separators=(',', ':'))
    db.session.commit()
    return model


def clamp_minutes(value: float | None, max_minutes: float) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    bounded = min(max(value, 0.0), max(0.0, max_minutes))
    return round(bounded, 1)


def _model_for_location(location: Location) -> IrrigationPredictionModel | None:
    if not location.id:
        return None
    return IrrigationPredictionModel.query.filter_by(location_id=location.id).one_or_none()


def _training_due(model: IrrigationPredictionModel | None, now: datetime, interval: timedelta) -> bool:
    if model is None or model.trained_at is None:
        return True
    return now - _as_utc(model.trained_at) >= interval


def _current_features(location: Location, target: float, adapter: influx_service.SensorTimeSeriesAdapter, now: datetime):
    start = now - LATEST_LOOKBACK
    soil_points = _query_points(location, SENSOR_TYPE_SOIL_MOISTURE, adapter, start, now)
    latest_soil = _latest_value(soil_points)
    if latest_soil is None:
        return None, {'error': 'Keine aktuelle Bodenfeuchte für dieses Beet gefunden.'}

    temperature = _average(_query_points(location, SENSOR_TYPE_TEMPERATURE, adapter, start, now), default=15.0)
    rainfall = _sum(_query_points(location, SENSOR_TYPE_RAINFALL, adapter, start, now), default=0.0)
    irrigation = _active_minutes(_query_points(location, SENSOR_TYPE_IRRIGATION, adapter, start, now), start, now)
    features = _feature_row(latest_soil, target, temperature, rainfall, irrigation)
    return features, dict(zip(FEATURE_NAMES, features))


def _load_training_datasets(location: Location, adapter: influx_service.SensorTimeSeriesAdapter, start: datetime, stop: datetime):
    return {
        SENSOR_TYPE_SOIL_MOISTURE: _query_points(location, SENSOR_TYPE_SOIL_MOISTURE, adapter, start, stop),
        SENSOR_TYPE_TEMPERATURE: _query_points(location, SENSOR_TYPE_TEMPERATURE, adapter, start, stop),
        SENSOR_TYPE_RAINFALL: _query_points(location, SENSOR_TYPE_RAINFALL, adapter, start, stop),
        SENSOR_TYPE_IRRIGATION: _query_points(location, SENSOR_TYPE_IRRIGATION, adapter, start, stop),
    }


def _training_samples(datasets: dict[str, list[dict[str, Any]]], target: float, max_minutes: float):
    soil_by_day = _daily_average(datasets.get(SENSOR_TYPE_SOIL_MOISTURE, []))
    temp_by_day = _daily_average(datasets.get(SENSOR_TYPE_TEMPERATURE, []))
    rain_by_day = _daily_sum(datasets.get(SENSOR_TYPE_RAINFALL, []))
    irrigation_by_day = _daily_active_minutes(datasets.get(SENSOR_TYPE_IRRIGATION, []))

    samples = []
    for day, soil in sorted(soil_by_day.items()):
        irrigation = irrigation_by_day.get(day)
        if irrigation is None:
            continue
        features = _feature_row(
            soil,
            target,
            temp_by_day.get(day, 15.0),
            rain_by_day.get(day, 0.0),
            irrigation_by_day.get(day - timedelta(days=1), 0.0),
        )
        samples.append((features, clamp_minutes(irrigation, max_minutes)))
    return samples


def _feature_row(soil: float, target: float, temperature: float, rainfall: float, previous_irrigation: float) -> list[float]:
    return [soil, target, max(target - soil, 0.0), temperature, rainfall, previous_irrigation]


def _query_points(location: Location, sensor_type: str, adapter: influx_service.SensorTimeSeriesAdapter, start: datetime, stop: datetime):
    points = []
    for sensor in _sensors_for_location(location, sensor_type):
        points.extend(adapter.query_sensor(sensor, start, stop) or [])
    return points


def _sensors_for_location(location: Location, sensor_type: str):
    base_query = Sensor.query.filter(Sensor.is_active.is_(True), Sensor.sensor_type == sensor_type)
    explicit = base_query.filter(Sensor.locations.any(Location.id == location.id)).order_by(Sensor.name.asc(), Sensor.id.asc()).all()
    if explicit:
        return explicit
    return base_query.filter(~Sensor.locations.any()).order_by(Sensor.name.asc(), Sensor.id.asc()).all()


def _daily_average(points: list[dict[str, Any]]):
    buckets: dict[datetime, list[float]] = {}
    for time_value, value in _normalized_points(points):
        day = time_value.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets.setdefault(day, []).append(value)
    return {day: sum(values) / len(values) for day, values in buckets.items() if values}


def _daily_sum(points: list[dict[str, Any]]):
    buckets: dict[datetime, float] = {}
    for time_value, value in _normalized_points(points):
        day = time_value.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets[day] = buckets.get(day, 0.0) + value
    return buckets


def _daily_active_minutes(points: list[dict[str, Any]]):
    normalized = _normalized_points(points)
    if len(normalized) < 2:
        return {}
    buckets: dict[datetime, float] = {}
    for index, (point_time, value) in enumerate(normalized[:-1]):
        if value < 0.5:
            continue
        next_time = normalized[index + 1][0]
        current = point_time
        while current < next_time:
            day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
            segment_stop = min(next_time, day_start + timedelta(days=1))
            buckets[day_start] = buckets.get(day_start, 0.0) + (segment_stop - current).total_seconds() / 60
            current = segment_stop
    return buckets


def _active_minutes(points: list[dict[str, Any]], start: datetime, stop: datetime):
    normalized = [(max(t, start), v) for t, v in _normalized_points(points) if start <= t <= stop]
    if len(normalized) < 2:
        return 0.0
    minutes = 0.0
    for index, (point_time, value) in enumerate(normalized[:-1]):
        if value >= 0.5:
            minutes += max(0.0, (min(normalized[index + 1][0], stop) - point_time).total_seconds() / 60)
    return minutes


def _normalized_points(points: list[dict[str, Any]]):
    normalized = []
    for point in points or []:
        point_time = _parse_time(point.get('time'))
        value = _number(point.get('value'))
        if point_time is not None and value is not None:
            normalized.append((point_time, value))
    return sorted(normalized, key=lambda item: item[0])


def _latest_value(points: list[dict[str, Any]]) -> float | None:
    normalized = _normalized_points(points)
    return normalized[-1][1] if normalized else None


def _average(points: list[dict[str, Any]], default: float):
    values = [value for _, value in _normalized_points(points)]
    return sum(values) / len(values) if values else default


def _sum(points: list[dict[str, Any]], default: float):
    values = [value for _, value in _normalized_points(points)]
    return sum(values) if values else default


def _fit_ridge_regression(x_rows: list[list[float]], y_values: list[float]) -> tuple[float, list[float]]:
    means = [sum(row[i] for row in x_rows) / len(x_rows) for i in range(len(FEATURE_NAMES))]
    scales = []
    for i, mean in enumerate(means):
        variance = sum((row[i] - mean) ** 2 for row in x_rows) / len(x_rows)
        scales.append(math.sqrt(variance) or 1.0)
    normalized_x = [[(row[i] - means[i]) / scales[i] for i in range(len(FEATURE_NAMES))] for row in x_rows]
    y_mean = sum(y_values) / len(y_values)
    centered_y = [value - y_mean for value in y_values]

    xtx = [[0.0 for _ in FEATURE_NAMES] for _ in FEATURE_NAMES]
    xty = [0.0 for _ in FEATURE_NAMES]
    for row, y in zip(normalized_x, centered_y):
        for i, xi in enumerate(row):
            xty[i] += xi * y
            for j, xj in enumerate(row):
                xtx[i][j] += xi * xj
    for i in range(len(FEATURE_NAMES)):
        xtx[i][i] += RIDGE_LAMBDA
    normalized_coefficients = _solve_linear_system(xtx, xty)
    coefficients = [normalized_coefficients[i] / scales[i] for i in range(len(FEATURE_NAMES))]
    intercept = y_mean - sum(coefficients[i] * means[i] for i in range(len(FEATURE_NAMES)))
    return intercept, coefficients


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    augmented = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            continue
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        augmented[col] = [value / divisor for value in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            augmented[row] = [value - factor * pivot_value for value, pivot_value in zip(augmented[row], augmented[col])]
    return [augmented[i][-1] for i in range(n)]


def _rmse(x_rows: list[list[float]], y_values: list[float], intercept: float, coefficients: list[float]):
    errors = []
    for row, actual in zip(x_rows, y_values):
        predicted = intercept + sum(c * v for c, v in zip(coefficients, row))
        errors.append((predicted - actual) ** 2)
    return round(math.sqrt(sum(errors) / len(errors)), 3) if errors else None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace('Z', '+00:00')))
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _target_or_default(value: float | None) -> float:
    parsed = _number(value)
    return parsed if parsed is not None else DEFAULT_TARGET_SOIL_MOISTURE_PERCENT


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
