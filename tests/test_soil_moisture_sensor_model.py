import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import InfluxIntegrationConfig, Location, Plant, SoilMoistureSensor, User, db, soil_moisture_sensor_location


class SoilMoistureSensorModelTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            self.user = User(sub='sensor-user', name='Sensor User')
            db.session.add(self.user)
            db.session.flush()
            self.location = Location(name='Sensorbeet')
            db.session.add(self.location)
            db.session.flush()
            self.plant = Plant(name='Minze', location_id=self.location.id, creator_id=self.user.id, map_x=10, map_y=20)
            self.sensor = SoilMoistureSensor(
                name='Bodenfeuchte Sensor 1',
                key='soil-sensor-1',
                homeassistant_entity_id='sensor.bodenfeuchte_1',
                influx_measurement='soil_moisture',
                influx_field='value',
                influx_tags='{"source": "homeassistant"}',
                map_x=30,
                map_y=40,
                creator_id=self.user.id,
            )
            self.sensor.locations.append(self.location)
            db.session.add_all([self.plant, self.sensor])
            db.session.commit()
            self.user_id = self.user.id
            self.location_id = self.location.id
            self.plant_id = self.plant.id
            self.sensor_id = self.sensor.id

        with self.client.session_transaction() as session:
            session['user_id'] = self.user_id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)

    def test_sensor_model_persists_fields_and_location_relationship(self):
        with self.app.app_context():
            sensor = db.session.get(SoilMoistureSensor, self.sensor_id)

            self.assertEqual(sensor.key, 'soil-sensor-1')
            self.assertEqual(sensor.homeassistant_entity_id, 'sensor.bodenfeuchte_1')
            self.assertEqual(sensor.influx_measurement, 'soil_moisture')
            self.assertEqual(sensor.influx_field, 'value')
            self.assertEqual(sensor.influx_tags, '{"source": "homeassistant"}')
            self.assertEqual(sensor.map_x, 30)
            self.assertEqual(sensor.map_y, 40)
            self.assertTrue(sensor.is_active)
            self.assertEqual([location.name for location in sensor.locations], ['Sensorbeet'])

            association_rows = db.session.execute(soil_moisture_sensor_location.select()).fetchall()
            self.assertEqual(len(association_rows), 1)
            self.assertEqual(association_rows[0].sensor_id, sensor.id)
            self.assertEqual(association_rows[0].location_id, self.location_id)

    def test_sensor_routes_create_and_update_sensor_locations(self):
        with self.app.app_context():
            second_location = Location(name='Zweites Beet')
            db.session.add(second_location)
            db.session.commit()
            second_location_id = second_location.id

        response = self.client.post('/sensors/new', data={
            'name': 'Neuer Bodensensor',
            'homeassistant_entity_id': 'sensor.neuer_bodensensor',
            'influx_measurement': 'soil',
            'influx_field': 'moisture',
            'influx_tags': 'bed=one',
            'map_x': '12.5',
            'map_y': '34.5',
            'location_ids': [str(self.location_id), str(second_location_id)],
        }, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            sensor = SoilMoistureSensor.query.filter_by(homeassistant_entity_id='sensor.neuer_bodensensor').one()
            self.assertEqual(sensor.key, 'sensor-neuer-bodensensor')
            self.assertEqual(sensor.influx_measurement, 'soil')
            self.assertEqual(sensor.influx_field, 'moisture')
            self.assertEqual(sensor.influx_tags, 'bed=one')
            self.assertEqual(sensor.map_x, 12.5)
            self.assertEqual(sensor.map_y, 34.5)
            self.assertEqual([location.id for location in sensor.locations], [self.location_id, second_location_id])
            sensor_id = sensor.id

        response = self.client.post(f'/sensors/{sensor_id}/edit', data={
            'name': 'Aktualisierter Bodensensor',
            'homeassistant_entity_id': 'sensor.aktualisierter_bodensensor',
            'influx_measurement': 'soil',
            'influx_field': 'value',
            'influx_tags': 'bed=two',
            'map_x': '98.1',
            'map_y': '76.2',
            'location_ids': [str(second_location_id)],
        }, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            sensor = db.session.get(SoilMoistureSensor, sensor_id)
            self.assertEqual(sensor.name, 'Aktualisierter Bodensensor')
            self.assertEqual(sensor.key, 'sensor-aktualisierter-bodensensor')
            self.assertEqual(sensor.homeassistant_entity_id, 'sensor.aktualisierter_bodensensor')
            self.assertEqual(sensor.influx_field, 'value')
            self.assertEqual(sensor.influx_tags, 'bed=two')
            self.assertEqual(sensor.map_x, 98.1)
            self.assertEqual(sensor.map_y, 76.2)
            self.assertEqual([location.id for location in sensor.locations], [second_location_id])

    def test_sensor_pages_render_forms_and_location_action(self):
        sensors_response = self.client.get('/sensors')
        sensor_response = self.client.get(f'/sensors/{self.sensor_id}')
        location_response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(sensors_response.status_code, 200)
        self.assertIn('Bodenfeuchte-Sensor anlegen', sensors_response.get_data(as_text=True))
        self.assertIn('Homeassistant Entity-ID', sensors_response.get_data(as_text=True))
        self.assertEqual(sensor_response.status_code, 200)
        self.assertIn('/sensors/{}/edit'.format(self.sensor_id), sensor_response.get_data(as_text=True))
        self.assertIn('soil_moisture', sensor_response.get_data(as_text=True))
        self.assertEqual(location_response.status_code, 200)
        self.assertIn('/sensors?location_id={}'.format(self.location_id), location_response.get_data(as_text=True))
        self.assertIn('Sensor verknüpfen', location_response.get_data(as_text=True))
        self.assertIn('Bodenfeuchte-Verlauf', location_response.get_data(as_text=True))
        self.assertIn('InfluxDB ist nicht vollständig konfiguriert', location_response.get_data(as_text=True))

    def test_location_detail_renders_soil_moisture_series_for_linked_sensors(self):
        class FakeAdapter:
            def query_sensor(self, sensor, start, stop):
                return [{'time': '2026-06-01T12:00:00+00:00', 'value': 35.2}]

        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(
                influx_url='https://influx.local',
                influx_org='Garten',
                influx_bucket='soil',
                influx_token='token',
            ))
            db.session.commit()

        with patch('app.views.influx_service.get_sensor_time_series_adapter', return_value=FakeAdapter()) as adapter_factory:
            response = self.client.get(f'/locations/{self.location_id}?moisture_range=24h')

        self.assertEqual(response.status_code, 200)
        adapter_factory.assert_called_once()
        html = response.get_data(as_text=True)
        self.assertIn('Bodenfeuchte-Verlauf', html)
        self.assertIn('24 Stunden', html)
        self.assertIn('Bodenfeuchte Sensor 1', html)
        self.assertIn(f'\"sensor_id\": {self.sensor_id}', html)
        self.assertIn('\"value\": 35.2', html)
        self.assertNotIn('Für den gewählten Zeitraum wurden keine Bodenfeuchte-Daten gefunden.', html)

    def test_location_detail_shows_influx_errors_as_hints(self):
        class FailingAdapter:
            def query_sensor(self, sensor, start, stop):
                raise RuntimeError('Influx nicht erreichbar')

        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(
                influx_url='https://influx.local',
                influx_org='Garten',
                influx_bucket='soil',
                influx_token='token',
            ))
            db.session.commit()

        with patch('app.views.influx_service.get_sensor_time_series_adapter', return_value=FailingAdapter()):
    def test_location_detail_renders_aggregated_current_soil_moisture(self):
        with self.app.app_context():
            first_sensor = db.session.get(SoilMoistureSensor, self.sensor_id)
            second_sensor = SoilMoistureSensor(
                name='Bodenfeuchte Sensor 2',
                key='soil-sensor-2',
                influx_measurement='soil_moisture',
                influx_field='value',
                creator_id=self.user_id,
            )
            second_sensor.locations.append(db.session.get(Location, self.location_id))
            inactive_sensor = SoilMoistureSensor(
                name='Inaktiver Bodenfeuchte Sensor',
                key='soil-sensor-inactive',
                influx_measurement='soil_moisture',
                creator_id=self.user_id,
                is_active=False,
            )
            inactive_sensor.locations.append(db.session.get(Location, self.location_id))
            db.session.add_all([
                second_sensor,
                inactive_sensor,
                InfluxIntegrationConfig(
                    influx_url='http://influxdb:8086',
                    influx_org='garden',
                    influx_bucket='soil',
                    influx_token='token',
                ),
            ])
            db.session.commit()
            first_sensor_key = first_sensor.key
            second_sensor_key = second_sensor.key

        def fake_latest_sensor_value(sensor, adapter=None):
            if sensor.key == first_sensor_key:
                return {'time': '2026-06-03T08:00:00Z', 'value': 30}
            if sensor.key == second_sensor_key:
                return {'time': '2026-06-03T08:05:00Z', 'value': '45'}
            return {'time': '2026-06-03T08:10:00Z', 'value': 90}

        with patch('app.views.latest_sensor_value', side_effect=fake_latest_sensor_value):
            response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('InfluxDB-Fehler beim Laden einzelner Sensoren', html)
        self.assertIn('Influx nicht erreichbar', html)

    def test_location_markers_do_not_include_soil_moisture_sensor_keys(self):
        self.assertIn('Bodenfeuchte', html)
        self.assertIn('37,5 %', html)
        self.assertIn('2 Sensoren · Durchschnitt', html)
        self.assertNotIn('Inaktiver Bodenfeuchte Sensor', html)

    def test_location_markers_do_not_include_soil_moisture_sensors(self):
        response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        marker_match = re.search(r'const plantsById = (.*?);', html)
        self.assertIsNotNone(marker_match)
        location_plant_markers = json.loads(marker_match.group(1))

        self.assertEqual(location_plant_markers, [
            {'id': self.plant_id, 'name': 'Minze', 'map_x': 10, 'map_y': 20},
        ])
        self.assertNotIn({
            'id': self.sensor_id,
            'name': 'Bodenfeuchte Sensor 1',
            'map_x': 30,
            'map_y': 40,
        }, location_plant_markers)
        self.assertIn('Minze', html)
        self.assertIn('Bodenfeuchte Sensor 1', html)
        self.assertNotIn('soil-sensor-1', html)
        self.assertNotIn('/sensors/{}'.format(self.sensor_id), html)
        self.assertNotIn('class=\"soil-moisture-sensor-marker\"', html)


if __name__ == '__main__':
    unittest.main()
