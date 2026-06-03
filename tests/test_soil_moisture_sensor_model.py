import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import Location, Plant, SoilMoistureSensor, User, db, soil_moisture_sensor_location


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
            self.location = Location(name='Sensorbeet', user_id=self.user.id, creator_id=self.user.id)
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

    def test_location_markers_do_not_include_soil_moisture_sensors(self):
        response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Minze', html)
        self.assertNotIn('Bodenfeuchte Sensor 1', html)
        self.assertNotIn('soil-sensor-1', html)


if __name__ == '__main__':
    unittest.main()
