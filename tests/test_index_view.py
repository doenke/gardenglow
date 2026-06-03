import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import InfluxIntegrationConfig, Location, Plant, SoilMoistureSensor, User, db


class IndexViewTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            self.user = User(sub='test-user', name='Test User')
            db.session.add(self.user)
            db.session.flush()
            self.location = Location(name='Sonnenbeet')
            db.session.add(self.location)
            db.session.flush()
            db.session.add_all([
                Plant(
                    name='Lavendel',
                    common_name='Echter Lavendel',
                    location_id=self.location.id,
                    creator_id=self.user.id,
                ),
                Plant(
                    name='Salbei',
                    common_name=None,
                    location_id=self.location.id,
                    creator_id=self.user.id,
                ),
            ])
            db.session.commit()
            self.user_id = self.user.id

        with self.client.session_transaction() as session:
            session['user_id'] = self.user_id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)

    def test_index_plant_table_marks_empty_mobile_fields(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<table class="index-plants-table">', html)
        self.assertIn('class="index-plant-common-name-cell">Echter Lavendel</td>', html)
        self.assertIn('class="index-plant-common-name-cell is-empty"></td>', html)
        self.assertIn('class="index-plant-location-link"', html)

    def test_index_links_current_sensor_minimum_and_maximum(self):
        with self.app.app_context():
            first_sensor = SoilMoistureSensor(
                name='Trockener Sensor',
                key='dry-sensor',
                creator_id=self.user_id,
            )
            second_sensor = SoilMoistureSensor(
                name='Feuchter Sensor',
                key='wet-sensor',
                creator_id=self.user_id,
            )
            db.session.add_all([
                first_sensor,
                second_sensor,
                InfluxIntegrationConfig(
                    influx_url='https://influx.local',
                    influx_org='Garten',
                    influx_bucket='soil',
                    influx_token='token',
                ),
            ])
            db.session.commit()

        def fake_latest_sensor_value(sensor, adapter=None):
            return {'time': '2026-06-03T08:00:00Z', 'value': 18 if sensor.key == 'dry-sensor' else 46.5}

        with patch('app.views.latest_sensor_value', side_effect=fake_latest_sensor_value):
            response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('href="/sensors" aria-label="Aktuelle Sensorwerte in der Sensorliste anzeigen"', html)
        self.assertIn('Minimum', html)
        self.assertIn('18 %', html)
        self.assertIn('Trockener Sensor', html)
        self.assertIn('Maximum', html)
        self.assertIn('46,5 %', html)
        self.assertIn('Feuchter Sensor', html)

    def test_index_shows_one_current_sensor_value_for_one_sensor(self):
        with self.app.app_context():
            db.session.add_all([
                SoilMoistureSensor(
                    name='Einzelsensor',
                    key='single-sensor',
                    creator_id=self.user_id,
                ),
                InfluxIntegrationConfig(
                    influx_url='https://influx.local',
                    influx_org='Garten',
                    influx_bucket='soil',
                    influx_token='token',
                ),
            ])
            db.session.commit()

        with patch('app.views.latest_sensor_value', return_value={'time': '2026-06-03T08:00:00Z', 'value': 33.3}):
            response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Aktuell', html)
        self.assertIn('33,3 %', html)
        self.assertNotIn('Minimum', html)
        self.assertNotIn('Maximum', html)


if __name__ == '__main__':
    unittest.main()
