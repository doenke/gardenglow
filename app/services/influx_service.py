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
DEFAULT_HOMEASSISTANT_DOMAIN = 'sensor'
DEFAULT_HOMEASSISTANT_INFLUX_MEASUREMENT = ''
DEFAULT_LOOKBACK = timedelta(days=7)


@dataclass(frozen=True)
class InfluxIntegrationConfig:
    """Connection settings for InfluxDB v2 APIs."""

    url: str = ''
    token: str = ''
    org: str = ''
    bucket: str = ''
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    verify_tls: bool = True

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
            verify_tls=_config_bool(config.get('INFLUX_VERIFY_TLS'), True),
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

    def query_latest_sensor_value(
        self,
        sensor: Any,
        start: datetime | str,
        stop: datetime | str | None = None,
    ) -> dict[str, Any] | None:
        """Return the latest normalized datapoint for a sensor and time range."""


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
                verify_ssl=self.config.verify_tls,
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
            getattr(sensor, 'influx_measurement')
            if hasattr(sensor, 'influx_measurement')
            else getattr(sensor, 'key', '')
        )
        measurement = (measurement or '').strip()
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

    def build_latest_sensor_value_query(
        self,
        sensor: Any,
        start: datetime | str,
        stop: datetime | str | None = None,
    ) -> str:
        """Build a Flux query that returns only the newest value for one sensor."""
        return '\n'.join([
            self.build_sensor_query(sensor, start, stop),
            '  |> group()',
            '  |> sort(columns: ["_time"])',
            '  |> last()',
        ])

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

    def query_latest_sensor_value(
        self,
        sensor: Any,
        start: datetime | str,
        stop: datetime | str | None = None,
    ) -> dict[str, Any] | None:
        if not self.config.enabled:
            return None

        flux = self.build_latest_sensor_value_query(sensor, start, stop)
        datapoints = normalize_flux_result(self.connect().query_api().query(query=flux, org=self.config.org))
        return datapoints[-1] if datapoints else None


def homeassistant_entity_influx_defaults(entity_id: str | None) -> dict[str, str]:
    """Return Influx field defaults for a Home Assistant entity id.

    Home Assistant's InfluxDB integration stores the HA domain as a tag named
    ``domain`` and the entity object id (without ``sensor.``) as ``entity_id``.
    Values are stored in the Flux field ``value``.
    """
    domain, object_id = split_homeassistant_entity_id(entity_id)
    return {
        'measurement': DEFAULT_HOMEASSISTANT_INFLUX_MEASUREMENT,
        'field': DEFAULT_INFLUX_FIELD,
        'tags': json.dumps({'entity_id': object_id, 'domain': domain}, ensure_ascii=False, separators=(',', ':')) if object_id else '',
    }


def split_homeassistant_entity_id(entity_id: str | None) -> tuple[str, str]:
    normalized = (entity_id or '').strip()
    if not normalized:
        return DEFAULT_HOMEASSISTANT_DOMAIN, ''
    if '.' not in normalized:
        return DEFAULT_HOMEASSISTANT_DOMAIN, normalized
    domain, object_id = normalized.split('.', 1)
    domain = domain.strip() or DEFAULT_HOMEASSISTANT_DOMAIN
    return domain, object_id.strip()


def get_sensor_time_series_adapter(config: InfluxIntegrationConfig | None = None) -> SensorTimeSeriesAdapter:
    """Factory for the currently supported sensor time-series adapter."""
    return FluxInfluxQueryAdapter(config or InfluxIntegrationConfig.from_app_config())


def recent_sensor_values(sensor: Any, lookback: timedelta = DEFAULT_LOOKBACK) -> list[dict[str, Any]]:
    """Convenience wrapper for views that need recent sensor datapoints."""
    stop = datetime.now(timezone.utc)
    start = stop - lookback
    return get_sensor_time_series_adapter().query_sensor(sensor, start, stop)


def latest_sensor_value(
    sensor: Any,
    lookback: timedelta = DEFAULT_LOOKBACK,
    adapter: SensorTimeSeriesAdapter | None = None,
) -> dict[str, Any] | None:
    """Return the newest datapoint for one sensor within the lookback window."""
    stop = datetime.now(timezone.utc)
    start = stop - lookback
    return (adapter or get_sensor_time_series_adapter()).query_latest_sensor_value(sensor, start, stop)


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


def _config_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if not normalized:
        return default
    return normalized in {'1', 'true', 'yes', 'on', 'y'}


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
