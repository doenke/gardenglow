import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import Sensor, db, SENSOR_TYPE_RAINFALL, SENSOR_TYPE_TEMPERATURE


class WeatherSensorMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'

    def tearDown(self):
        if hasattr(self, 'app'):
            with self.app.app_context():
                db.session.remove()
                db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)

    def test_legacy_global_weather_fields_are_migrated_to_sensors(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                '''
                CREATE TABLE influx_integration_config (
                    id INTEGER PRIMARY KEY,
                    influx_url VARCHAR(1024) NOT NULL DEFAULT '',
                    influx_org VARCHAR(255) NOT NULL DEFAULT '',
                    influx_bucket VARCHAR(255) NOT NULL DEFAULT '',
                    influx_token TEXT,
                    temperature_homeassistant_entity_id VARCHAR(255),
                    temperature_influx_measurement VARCHAR(255),
                    temperature_influx_field VARCHAR(255),
                    temperature_influx_tags TEXT,
                    rainfall_homeassistant_entity_id VARCHAR(255),
                    rainfall_influx_measurement VARCHAR(255),
                    rainfall_influx_field VARCHAR(255),
                    rainfall_influx_tags TEXT,
                    verify_tls BOOLEAN NOT NULL DEFAULT 1,
                    timeout_seconds INTEGER NOT NULL DEFAULT 10,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                '''
            )
            connection.execute(
                '''
                INSERT INTO influx_integration_config (
                    influx_url,
                    influx_org,
                    influx_bucket,
                    influx_token,
                    temperature_homeassistant_entity_id,
                    temperature_influx_field,
                    temperature_influx_tags,
                    rainfall_homeassistant_entity_id,
                    rainfall_influx_field,
                    rainfall_influx_tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'https://influx.local',
                    'Garten',
                    'soil',
                    'token',
                    'sensor.aussentemperatur',
                    'value',
                    '{"entity_id":"aussentemperatur","domain":"sensor"}',
                    'sensor.regenmenge',
                    'value',
                    '{"entity_id":"regenmenge","domain":"sensor"}',
                ),
            )

        self.app = create_app()
        self.app.config.update(TESTING=True)

        with self.app.app_context():
            temperature = Sensor.query.filter_by(sensor_type=SENSOR_TYPE_TEMPERATURE).one()
            rainfall = Sensor.query.filter_by(sensor_type=SENSOR_TYPE_RAINFALL).one()
            self.assertEqual(temperature.name, 'Temperatur')
            self.assertEqual(temperature.homeassistant_entity_id, 'sensor.aussentemperatur')
            self.assertEqual(temperature.influx_field, 'value')
            self.assertEqual(rainfall.name, 'Regenmenge')
            self.assertEqual(rainfall.homeassistant_entity_id, 'sensor.regenmenge')
            self.assertEqual(rainfall.influx_field, 'value')

            # Running startup migration again must not create duplicates.
            db.session.remove()
            second_app = create_app()
            second_app.config.update(TESTING=True)
            self.assertEqual(Sensor.query.filter_by(sensor_type=SENSOR_TYPE_TEMPERATURE).count(), 1)
            self.assertEqual(Sensor.query.filter_by(sensor_type=SENSOR_TYPE_RAINFALL).count(), 1)
            self.app = second_app
