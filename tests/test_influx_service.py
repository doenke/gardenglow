from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from app.services.influx_service import (
    FluxInfluxQueryAdapter,
    InfluxIntegrationConfig,
    normalize_flux_result,
    parse_influx_tags,
)


class FakeRecord:
    def __init__(self, time, value):
        self._time = time
        self._value = value

    def get_time(self):
        return self._time

    def get_value(self):
        return self._value


class InfluxServiceTest(unittest.TestCase):
    def test_config_requires_all_connection_values(self):
        self.assertFalse(InfluxIntegrationConfig.from_mapping({}).enabled)
        self.assertTrue(InfluxIntegrationConfig.from_mapping({
            'INFLUX_URL': 'http://influxdb:8086',
            'INFLUX_TOKEN': 'token',
            'INFLUX_ORG': 'garden',
            'INFLUX_BUCKET': 'sensors',
        }).enabled)

    def test_build_sensor_query_uses_flux_measurement_field_tags_and_range(self):
        config = InfluxIntegrationConfig(
            url='http://influxdb:8086',
            token='token',
            org='garden',
            bucket='sensors',
        )
        sensor = SimpleNamespace(
            key='soil-1',
            influx_measurement='soil_moisture',
            influx_field='percent',
            influx_tags='{"entity_id": "sensor.soil_1", "source": "homeassistant"}',
        )

        query = FluxInfluxQueryAdapter(config).build_sensor_query(
            sensor,
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 2, tzinfo=timezone.utc),
        )

        self.assertIn('from(bucket: "sensors")', query)
        self.assertIn('|> range(start: 2026-06-01T00:00:00Z, stop: 2026-06-02T00:00:00Z)', query)
        self.assertIn('r._measurement == "soil_moisture"', query)
        self.assertIn('r._field == "percent"', query)
        self.assertIn('r["entity_id"] == "sensor.soil_1"', query)
        self.assertIn('|> keep(columns: ["_time", "_value"])', query)

    def test_normalize_flux_result_returns_ui_friendly_points(self):
        tables = [SimpleNamespace(records=[FakeRecord(datetime(2026, 6, 1, 12, tzinfo=timezone.utc), 42.1)])]

        self.assertEqual(
            normalize_flux_result(tables),
            [{'time': '2026-06-01T12:00:00+00:00', 'value': 42.1}],
        )

    def test_parse_influx_tags_accepts_mapping_and_json(self):
        self.assertEqual(parse_influx_tags({'a': 1}), {'a': '1'})
        self.assertEqual(parse_influx_tags('{"a":"b"}'), {'a': 'b'})


if __name__ == '__main__':
    unittest.main()
