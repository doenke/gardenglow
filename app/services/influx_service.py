"""InfluxDB adapter layer for soil moisture sensor time-series queries.

The public entry points in this module intentionally hide the concrete
InfluxDB query API from views/templates.  InfluxDB 2.7.11 uses Flux as the
standard query language here; future versions can add another adapter class
that implements the same small interface.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Mapping, Protocol, Sequence

from flask import current_app


DEFAULT_TIMEOUT_SECONDS = 5
DEFAULT_INFLUX_FIELD = 'value'
DEFAULT_LOOKBACK = timedelta(days=7)


@dataclass(frozen=True)
class InfluxIntegrationConfig:
    """Connection settings for InfluxDB v2 APIs."""

    url: str = ''
    token: str = ''
    org: str = ''
    bucket: str = ''
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def enabled(self) -> bool:
        return all((self.url, self.token, self.org, self.bucket))

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> 'InfluxIntegrationConfig':
        config = config or {}
        return cls(
            url=str(config.get('INFLUX_URL') or '').strip(),
            token=str(config.get('INFLUX_TOKEN') or '').strip(),
            org=str(config.get('INFLUX_ORG') or '').strip(),
            bucket=str(config.get('INFLUX_BUCKET') or '').strip(),
            timeout_seconds=_positive_float(
                config.get('INFLUX_TIMEOUT_SECONDS'),
                DEFAULT_TIMEOUT_SECONDS,
            ),
        )

    @classmethod
    def from_app_config(cls) -> 'InfluxIntegrationConfig':
        return cls.from_mapping(current_app.config)


class SensorTimeSeriesAdapter(Protocol):
    """Interface used by callers that need sensor time-series data."""

    def health(self) -> dict[str, Any]:
        """Return a UI-friendly health payload for the backing service."""

    def query_sensor(
        self,
        sensor: Any,
        start: datetime | str,
        stop: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        """Return normalized datapoints for a sensor and time range."""


class FluxInfluxQueryAdapter:
    """InfluxDB 2.7.11 adapter using Flux queries."""

    def __init__(self, config: InfluxIntegrationConfig, client: Any | None = None):
        self.config = config
        self._client = client

    @classmethod
    def from_app_config(cls) -> 'FluxInfluxQueryAdapter':
        return cls(InfluxIntegrationConfig.from_app_config())

    def connect(self):
        """Create or reuse an InfluxDB client with the configured timeout."""
        if self._client is None:
            from influxdb_client import InfluxDBClient

            self._client = InfluxDBClient(
                url=self.config.url,
                token=self.config.token,
                org=self.config.org,
                timeout=int(self.config.timeout_seconds * 1000),
            )
        return self._client

    def health(self) -> dict[str, Any]:
        if not self.config.enabled:
            return {
                'ok': False,
                'configured': False,
                'message': 'InfluxDB ist nicht vollständig konfiguriert.',
            }

        try:
            ready = bool(self.connect().ping())
        except Exception as exc:  # pragma: no cover - depends on external InfluxDB availability
            return {'ok': False, 'configured': True, 'message': str(exc)}

        return {
            'ok': ready,
            'configured': True,
            'message': 'InfluxDB ist erreichbar.' if ready else 'InfluxDB-Ping fehlgeschlagen.',
        }

    def build_sensor_query(
        self,
        sensor: Any,
        start: datetime | str,
        stop: datetime | str | None = None,
    ) -> str:
        """Build a Flux query for one sensor in a bounded time range."""
        measurement = (
            getattr(sensor, 'influx_measurement', None) or getattr(sensor, 'key', '') or ''
        ).strip()
        field = (getattr(sensor, 'influx_field', None) or DEFAULT_INFLUX_FIELD).strip()
        tags = parse_influx_tags(getattr(sensor, 'influx_tags', None))

        lines = [
            f'from(bucket: {_flux_string(self.config.bucket)})',
            f'  |> range(start: {_flux_time(start)}, stop: {_flux_time(stop or datetime.now(timezone.utc))})',
        ]
        if measurement:
            lines.append(f'  |> filter(fn: (r) => r._measurement == {_flux_string(measurement)})')
        if field:
            lines.append(f'  |> filter(fn: (r) => r._field == {_flux_string(field)})')
        for tag_key, tag_value in tags.items():
            lines.append(f'  |> filter(fn: (r) => r[{_flux_string(tag_key)}] == {_flux_string(tag_value)})')
        lines.extend([
            '  |> keep(columns: ["_time", "_value"])',
            '  |> sort(columns: ["_time"])',
        ])
        return '\n'.join(lines)

    def query_sensor(
        self,
        sensor: Any,
        start: datetime | str,
        stop: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.config.enabled:
            return []

        flux = self.build_sensor_query(sensor, start, stop)
        tables = self.connect().query_api().query(query=flux, org=self.config.org)
        return normalize_flux_result(tables)


def get_sensor_time_series_adapter(config: InfluxIntegrationConfig | None = None) -> SensorTimeSeriesAdapter:
    """Factory for the currently supported sensor time-series adapter."""
    return FluxInfluxQueryAdapter(config or InfluxIntegrationConfig.from_app_config())


def recent_sensor_values(sensor: Any, lookback: timedelta = DEFAULT_LOOKBACK) -> list[dict[str, Any]]:
    """Convenience wrapper for views that need recent sensor datapoints."""
    stop = datetime.now(timezone.utc)
    start = stop - lookback
    return get_sensor_time_series_adapter().query_sensor(sensor, start, stop)


def parse_influx_tags(raw_tags: str | Mapping[str, Any] | None) -> dict[str, str]:
    if not raw_tags:
        return {}
    if isinstance(raw_tags, Mapping):
        return {str(key): str(value) for key, value in raw_tags.items() if key and value is not None}

    try:
        parsed = json.loads(raw_tags)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, Mapping):
        return {}
    return {str(key): str(value) for key, value in parsed.items() if key and value is not None}


def normalize_flux_result(tables: Sequence[Any]) -> list[dict[str, Any]]:
    datapoints: list[dict[str, Any]] = []
    for table in tables or []:
        for record in getattr(table, 'records', []) or []:
            record_time = _record_time(record)
            datapoints.append(
                {
                    'time': record_time.isoformat() if hasattr(record_time, 'isoformat') else str(record_time),
                    'value': _record_value(record),
                }
            )
    return datapoints


def _record_time(record: Any) -> Any:
    if hasattr(record, 'get_time'):
        return record.get_time()
    record_time = getattr(record, 'time', None)
    return record_time if record_time is not None else _record_mapping_value(record, '_time')


def _record_value(record: Any) -> Any:
    if hasattr(record, 'get_value'):
        return record.get_value()
    record_value = getattr(record, 'value', None)
    return record_value if record_value is not None else _record_mapping_value(record, '_value')


def _record_mapping_value(record: Any, key: str) -> Any:
    values = getattr(record, 'values', None)
    if isinstance(values, Mapping):
        return values.get(key)
    if isinstance(record, Mapping):
        return record.get(key)
    return None


def _flux_time(value: datetime | str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    return str(value)


def _flux_string(value: Any) -> str:
    return json.dumps(str(value))


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
