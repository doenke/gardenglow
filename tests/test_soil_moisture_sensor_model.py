import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import InfluxIntegrationConfig, Location, Plant, Sensor, User, db, sensor_location, SENSOR_TYPE_SOIL_MOISTURE, SENSOR_TYPE_TEMPERATURE, SENSOR_TYPE_RAINFALL, SENSOR_TYPE_IRRIGATION


class SensorModelTest(unittest.TestCase):
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
            self.sensor = Sensor(
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
            sensor = db.session.get(Sensor, self.sensor_id)

            self.assertEqual(sensor.key, 'soil-sensor-1')
            self.assertEqual(sensor.homeassistant_entity_id, 'sensor.bodenfeuchte_1')
            self.assertEqual(sensor.sensor_type, SENSOR_TYPE_SOIL_MOISTURE)
            self.assertEqual(sensor.type_label, 'Bodenfeuchte')
            self.assertEqual(sensor.influx_measurement, 'soil_moisture')
            self.assertEqual(sensor.influx_field, 'value')
            self.assertEqual(sensor.influx_tags, '{"source": "homeassistant"}')
            self.assertEqual(sensor.map_x, 30)
            self.assertEqual(sensor.map_y, 40)
            self.assertTrue(sensor.is_active)
            self.assertEqual([location.name for location in sensor.locations], ['Sensorbeet'])

            association_rows = db.session.execute(sensor_location.select()).fetchall()
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
            'sensor_type': SENSOR_TYPE_RAINFALL,
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
            sensor = Sensor.query.filter_by(homeassistant_entity_id='sensor.neuer_bodensensor').one()
            self.assertEqual(sensor.key, 'sensor-neuer-bodensensor')
            self.assertEqual(sensor.sensor_type, SENSOR_TYPE_RAINFALL)
            self.assertEqual(sensor.influx_measurement, 'soil')
            self.assertEqual(sensor.influx_field, 'moisture')
            self.assertEqual(sensor.influx_tags, 'bed=one')
            self.assertEqual(sensor.map_x, 12.5)
            self.assertEqual(sensor.map_y, 34.5)
            self.assertEqual([location.id for location in sensor.locations], [self.location_id, second_location_id])
            sensor_id = sensor.id

        response = self.client.post(f'/sensors/{sensor_id}/edit', data={
            'name': 'Aktualisierter Bodensensor',
            'sensor_type': SENSOR_TYPE_TEMPERATURE,
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
            sensor = db.session.get(Sensor, sensor_id)
            self.assertEqual(sensor.name, 'Aktualisierter Bodensensor')
            self.assertEqual(sensor.key, 'sensor-aktualisierter-bodensensor')
            self.assertEqual(sensor.homeassistant_entity_id, 'sensor.aktualisierter_bodensensor')
            self.assertEqual(sensor.sensor_type, SENSOR_TYPE_TEMPERATURE)
            self.assertEqual(sensor.influx_field, 'value')
            self.assertEqual(sensor.influx_tags, 'bed=two')
            self.assertEqual(sensor.map_x, 98.1)
            self.assertEqual(sensor.map_y, 76.2)
            self.assertEqual([location.id for location in sensor.locations], [second_location_id])

    def test_sensor_routes_fill_homeassistant_influx_defaults_when_blank(self):
        response = self.client.post('/sensors/new', data={
            'name': 'Homeassistant Bodensensor',
            'sensor_type': SENSOR_TYPE_SOIL_MOISTURE,
            'homeassistant_entity_id': 'sensor.third_reality_inc_3rsm0347z_bodenfeuchtigkeit',
            'influx_measurement': '',
            'influx_field': '',
            'influx_tags': '',
            'location_ids': [str(self.location_id)],
        }, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            sensor = Sensor.query.filter_by(name='Homeassistant Bodensensor').one()
            self.assertIsNone(sensor.influx_measurement)
            self.assertEqual(sensor.influx_field, 'value')
            self.assertEqual(
                json.loads(sensor.influx_tags),
                {'entity_id': 'third_reality_inc_3rsm0347z_bodenfeuchtigkeit', 'domain': 'sensor'},
            )

    def test_sensor_routes_override_influx_details_in_ha_entity_mode(self):
        response = self.client.post('/sensors/new', data={
            'name': 'HA-only Bodensensor',
            'sensor_type': SENSOR_TYPE_SOIL_MOISTURE,
            'homeassistant_entity_id': 'sensor.ha_only_bodenfeuchtigkeit',
            'influx_details_mode': 'ha_entity',
            'influx_measurement': 'custom_measurement',
            'influx_field': 'custom_field',
            'influx_tags': '{"custom":"tag"}',
            'location_ids': [str(self.location_id)],
        }, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            sensor = Sensor.query.filter_by(name='HA-only Bodensensor').one()
            self.assertIsNone(sensor.influx_measurement)
            self.assertEqual(sensor.influx_field, 'value')
            self.assertEqual(json.loads(sensor.influx_tags), {'entity_id': 'ha_only_bodenfeuchtigkeit', 'domain': 'sensor'})

    def test_sensor_routes_require_entity_in_ha_entity_mode(self):
        response = self.client.post('/sensors/new', data={
            'name': 'HA-only ohne Entity',
            'sensor_type': SENSOR_TYPE_SOIL_MOISTURE,
            'influx_details_mode': 'ha_entity',
            'influx_measurement': 'custom_measurement',
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Bitte eine Homeassistant Entity-ID angeben.', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertFalse(Sensor.query.filter_by(name='HA-only ohne Entity').first())

    def test_sensor_detail_can_test_latest_influx_value(self):
        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(
                influx_url='https://influx.local',
                influx_org='Garten',
                influx_bucket='soil',
                influx_token='token',
            ))
            db.session.commit()

        class FakeAdapter:
            pass

        with patch('app.views.influx_service.get_sensor_time_series_adapter', return_value=FakeAdapter()) as adapter_factory, \
                patch('app.views.latest_sensor_value', return_value={'time': '2026-06-03T08:00:00Z', 'value': 42.5}) as latest_value:
            response = self.client.post(f'/sensors/{self.sensor_id}/influx/test', follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        adapter_factory.assert_called_once()
        latest_value.assert_called_once()
        html = response.get_data(as_text=True)
        self.assertIn('Letzter Influx-Wert: 42,5 % (2026-06-03T08:00:00Z)', html)

    def test_sensor_pages_render_forms_and_location_action(self):
        sensors_response = self.client.get('/sensors')
        sensor_response = self.client.get(f'/sensors/{self.sensor_id}')
        location_response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(sensors_response.status_code, 200)
        sensors_html = sensors_response.get_data(as_text=True)
        self.assertIn('Sensor anlegen', sensors_html)
        self.assertIn('Sensortyp', sensors_html)
        self.assertIn('Bodenfeuchte', sensors_html)
        self.assertIn('Temperatur', sensors_html)
        self.assertIn('Niederschlag', sensors_html)
        self.assertIn('Bewässerung', sensors_html)
        self.assertIn('data-label="Typ"', sensors_html)
        self.assertIn('Homeassistant Entity-ID', sensors_html)
        self.assertNotIn('Bodenfeuchte-Sensoren', sensors_html)
        self.assertEqual(sensor_response.status_code, 200)
        sensor_html = sensor_response.get_data(as_text=True)
        self.assertIn('/sensors/{}/edit'.format(self.sensor_id), sensor_html)
        self.assertIn('soil_moisture', sensor_html)
        self.assertIn('Sensortyp', sensor_html)
        self.assertIn('Bewässerung', sensor_html)
        self.assertIn('Letzten Influx-Wert testen', sensor_html)
        self.assertIn('Komplette Details', sensor_html)
        self.assertEqual(location_response.status_code, 200)
        self.assertIn('/sensors?location_id={}'.format(self.location_id), location_response.get_data(as_text=True))
        self.assertIn('Zur Sensorübersicht dieses Beets', location_response.get_data(as_text=True))
        location_html = location_response.get_data(as_text=True)
        self.assertIn('Sensor-Verlauf', location_html)
        self.assertIn('Bodenfeuchte-Daten werden im Hintergrund geladen.', location_html)
        self.assertIn('.soil-moisture-chart-wrap{min-height:180px}', location_html)
        self.assertIn("window.matchMedia('(max-width: 700px)').matches ? mobileHeight : desktopHeight", location_html)
        self.assertNotIn('InfluxDB ist nicht vollständig konfiguriert', location_html)


    def test_sensor_list_renders_current_value_instead_of_position(self):
        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(
                influx_url='https://influx.local',
                influx_org='Garten',
                influx_bucket='soil',
                influx_token='token',
            ))
            db.session.add_all([
                Sensor(
                    name='Temperatur Sensor',
                    key='temperature-sensor',
                    sensor_type=SENSOR_TYPE_TEMPERATURE,
                    influx_field='value',
                    creator_id=self.user_id,
                ),
                Sensor(
                    name='Regen Sensor',
                    key='rainfall-sensor',
                    sensor_type=SENSOR_TYPE_RAINFALL,
                    influx_field='value',
                    creator_id=self.user_id,
                ),
            ])
            db.session.commit()

        def current_value_for(sensor, adapter=None):
            values = {
                SENSOR_TYPE_SOIL_MOISTURE: 42.5,
                SENSOR_TYPE_TEMPERATURE: 21.5,
                SENSOR_TYPE_RAINFALL: 4.2,
            }
            return {'time': '2026-06-03T08:00:00Z', 'value': values[sensor.sensor_type]}

        with patch('app.views.latest_sensor_value', side_effect=current_value_for):
            response = self.client.get('/sensors')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<th>Aktueller Wert</th>', html)
        self.assertIn('data-label="Aktueller Wert">42,5 %</td>', html)
        self.assertIn('data-label="Aktueller Wert">21,5 °C</td>', html)
        self.assertIn('data-label="Aktueller Wert">4,2 mm</td>', html)
        self.assertNotIn('<th>Position</th>', html)
        self.assertNotIn('data-label="Position"', html)

    def test_location_detail_uses_unassigned_sensor_for_productive_beds(self):
        with self.app.app_context():
            sensor = db.session.get(Sensor, self.sensor_id)
            sensor.locations.clear()
            db.session.commit()

        response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Sensor-Verlauf', html)
        self.assertIn('Bodenfeuchte-Daten werden im Hintergrund geladen.', html)

    def test_location_detail_does_not_use_unassigned_sensor_for_trash(self):
        with self.app.app_context():
            sensor = db.session.get(Sensor, self.sensor_id)
            sensor.locations.clear()
            trash = Location(name='Papierkorb')
            db.session.add(trash)
            db.session.commit()
            trash_id = trash.id

        page_response = self.client.get(f'/locations/{trash_id}')
        response = self.client.get(f'/locations/{trash_id}/soil-moisture')

        self.assertEqual(page_response.status_code, 200)
        page_html = page_response.get_data(as_text=True)
        self.assertNotIn('Sensor-Verlauf', page_html)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['sensors'], [])
        self.assertEqual(payload['current']['sensor_values'], [])

    def test_empty_sensor_location_selection_means_all_productive_beds(self):
        response = self.client.post('/sensors/new', data={
            'name': 'Globaler Bodensensor',
            'sensor_type': SENSOR_TYPE_IRRIGATION,
            'homeassistant_entity_id': 'sensor.globaler_bodensensor',
            'influx_measurement': 'soil',
            'influx_field': 'moisture',
            'influx_tags': 'bed=all',
        }, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            sensor = Sensor.query.filter_by(homeassistant_entity_id='sensor.globaler_bodensensor').one()
            self.assertEqual(sensor.locations, [])

        list_response = self.client.get('/sensors')
        self.assertEqual(list_response.status_code, 200)
        html = list_response.get_data(as_text=True)
        self.assertIn('Alle Beete', html)
        self.assertIn('Keine Auswahl bedeutet: Sensor gilt für alle produktiven Beete, denen kein Sensor dieses Typs explizit zugeordnet ist.', html)

    def test_sensor_list_filters_by_selected_location(self):
        with self.app.app_context():
            trash = Location(name='Papierkorb')
            other = Location(name='Anderes Beet')
            db.session.add_all([trash, other])
            db.session.flush()

            global_sensor = Sensor(
                name='Globaler Sensor',
                key='global-list-sensor',
                sensor_type=SENSOR_TYPE_TEMPERATURE,
                creator_id=self.user_id,
                is_active=True,
            )
            trash_sensor = Sensor(
                name='Papierkorb Sensor',
                key='trash-list-sensor',
                sensor_type=SENSOR_TYPE_TEMPERATURE,
                creator_id=self.user_id,
                is_active=True,
            )
            trash_sensor.locations.append(trash)
            other_sensor = Sensor(
                name='Anderer Beet Sensor',
                key='other-list-sensor',
                sensor_type=SENSOR_TYPE_TEMPERATURE,
                creator_id=self.user_id,
                is_active=True,
            )
            other_sensor.locations.append(other)
            db.session.add_all([global_sensor, trash_sensor, other_sensor])
            db.session.commit()
            trash_id = trash.id

        all_response = self.client.get('/sensors')
        selected_response = self.client.get(f'/sensors?location_id={self.location_id}')
        selected_soil_response = self.client.get(f'/sensors?location_id={self.location_id}&sensor_type={SENSOR_TYPE_SOIL_MOISTURE}')
        selected_temperature_response = self.client.get(f'/sensors?location_id={self.location_id}&sensor_type={SENSOR_TYPE_TEMPERATURE}')
        all_temperature_response = self.client.get(f'/sensors?sensor_type={SENSOR_TYPE_TEMPERATURE}')
        trash_response = self.client.get(f'/sensors?location_id={trash_id}')

        self.assertEqual(all_response.status_code, 200)
        all_html = all_response.get_data(as_text=True)
        self.assertIn('Bodenfeuchte Sensor 1', all_html)
        self.assertIn('Globaler Sensor', all_html)
        self.assertIn('Papierkorb Sensor', all_html)
        self.assertIn('Anderer Beet Sensor', all_html)

        self.assertEqual(selected_response.status_code, 200)
        selected_html = selected_response.get_data(as_text=True)
        self.assertIn('für Sensorbeet', selected_html)
        self.assertIn('Bodenfeuchte Sensor 1', selected_html)
        self.assertIn('Globaler Sensor', selected_html)
        self.assertIn('Alle Beete', selected_html)
        self.assertIn('>Alle Typen</a>', selected_html)
        self.assertIn('href="/sensors?location_id={}&amp;sensor_type=temperature"'.format(self.location_id), selected_html)
        self.assertIn('href="/sensors" aria-current="page" aria-pressed="true">Sensorbeet</a>', selected_html)
        self.assertIn('href="/sensors?location_id={}"'.format(trash_id), selected_html)
        self.assertNotIn('openSensorCreateDialog();', selected_html)
        self.assertNotIn('Papierkorb Sensor', selected_html)
        self.assertNotIn('Anderer Beet Sensor', selected_html)

        self.assertEqual(selected_soil_response.status_code, 200)
        selected_soil_html = selected_soil_response.get_data(as_text=True)
        self.assertIn('Bodenfeuchte Sensor 1', selected_soil_html)
        self.assertIn('href="/sensors?location_id={}" aria-current="page" aria-pressed="true">Bodenfeuchte</a>'.format(self.location_id), selected_soil_html)
        self.assertNotIn('Globaler Sensor', selected_soil_html)
        self.assertNotIn('Papierkorb Sensor', selected_soil_html)

        self.assertEqual(selected_temperature_response.status_code, 200)
        selected_temperature_html = selected_temperature_response.get_data(as_text=True)
        self.assertIn('Globaler Sensor', selected_temperature_html)
        self.assertNotIn('Bodenfeuchte Sensor 1', selected_temperature_html)
        self.assertNotIn('Papierkorb Sensor', selected_temperature_html)

        self.assertEqual(all_temperature_response.status_code, 200)
        all_temperature_html = all_temperature_response.get_data(as_text=True)
        self.assertIn('Globaler Sensor', all_temperature_html)
        self.assertIn('Papierkorb Sensor', all_temperature_html)
        self.assertIn('Anderer Beet Sensor', all_temperature_html)
        self.assertNotIn('Bodenfeuchte Sensor 1', all_temperature_html)

        self.assertEqual(trash_response.status_code, 200)
        trash_html = trash_response.get_data(as_text=True)
        self.assertIn('für Papierkorb', trash_html)
        self.assertIn('Papierkorb Sensor', trash_html)
        self.assertNotIn('Globaler Sensor', trash_html)
        self.assertNotIn('Bodenfeuchte Sensor 1', trash_html)
        self.assertNotIn('Anderer Beet Sensor', trash_html)

    def test_location_detail_offers_one_year_soil_moisture_range(self):
        response = self.client.get(f'/locations/{self.location_id}?moisture_range=1y')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<option value="1y" selected>1 Jahr</option>', html)

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
            page_response = self.client.get(f'/locations/{self.location_id}?moisture_range=24h')
            adapter_factory.assert_not_called()
            response = self.client.get(f'/locations/{self.location_id}/soil-moisture?moisture_range=24h')

        self.assertEqual(page_response.status_code, 200)
        self.assertEqual(adapter_factory.call_count, 2)
        page_html = page_response.get_data(as_text=True)
        self.assertIn('Bodenfeuchte-Daten werden im Hintergrund geladen.', page_html)
        self.assertNotIn('\"value\": 35.2', page_html)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['range_key'], '24h')
        self.assertEqual(payload['series'][0]['sensor_id'], self.sensor_id)
        self.assertEqual(payload['series'][0]['points'][0]['value'], 35.2)
        self.assertNotIn('Für den gewählten Zeitraum wurden keine Bodenfeuchte-Daten gefunden.', payload['hints'])

    def test_location_soil_moisture_payload_includes_weather_series(self):
        class FakeAdapter:
            def query_sensor(self, source, start, stop):
                if source.key == 'temperature':
                    return [{'time': '2026-06-01T12:00:00+00:00', 'value': 21.5}]
                if source.key == 'rainfall':
                    return [{'time': '2026-06-01T12:00:00+00:00', 'value': 4.2}]
                return [{'time': '2026-06-01T12:00:00+00:00', 'value': 35.2}]

        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(
                influx_url='https://influx.local',
                influx_org='Garten',
                influx_bucket='soil',
                influx_token='token',
            ))
            db.session.add(Sensor(
                name='Temperatur',
                key='temperature',
                sensor_type=SENSOR_TYPE_TEMPERATURE,
                homeassistant_entity_id='sensor.aussentemperatur',
                influx_field='value',
                influx_tags='{"entity_id":"aussentemperatur","domain":"sensor"}',
                creator_id=self.user_id,
                is_active=True,
            ))
            db.session.add(Sensor(
                name='Regenmenge',
                key='rainfall',
                sensor_type=SENSOR_TYPE_RAINFALL,
                homeassistant_entity_id='sensor.regenmenge',
                influx_field='value',
                influx_tags='{"entity_id":"regenmenge","domain":"sensor"}',
                creator_id=self.user_id,
                is_active=True,
            ))
            db.session.commit()

        with patch('app.views.influx_service.get_sensor_time_series_adapter', return_value=FakeAdapter()), \
                patch('app.views.latest_sensor_value', return_value={'time': '2026-06-03T08:00:00Z', 'value': 35.2}):
            response = self.client.get(f'/locations/{self.location_id}/soil-moisture')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['weather_series']['temperature']['label'], 'Temperatur')
        self.assertEqual(payload['weather_series']['temperature']['unit'], '°C')
        self.assertEqual(payload['weather_series']['temperature']['points'][0]['value'], 21.5)
        self.assertEqual(payload['weather_series']['rainfall']['label'], 'Regenmenge')
        self.assertEqual(payload['weather_series']['rainfall']['unit'], 'mm')
        self.assertEqual(payload['weather_series']['rainfall']['points'][0]['value'], 4.2)
        self.assertTrue(payload['has_series_data'])


    def test_weather_sensors_scope_unassigned_fallback_skips_beds_with_explicit_type(self):
        class FakeAdapter:
            def query_sensor(self, sensor, start, stop):
                return [{'time': '2026-06-01T12:00:00+00:00', 'value': sensor.id}]

        with self.app.app_context():
            trash = Location(name='Papierkorb')
            other = Location(name='Anderes Beet')
            db.session.add_all([trash, other])
            db.session.flush()
            db.session.add(InfluxIntegrationConfig(
                influx_url='https://influx.local',
                influx_org='Garten',
                influx_bucket='soil',
                influx_token='token',
            ))
            global_sensor = Sensor(
                name='Globale Temperatur',
                key='temperature-global',
                sensor_type=SENSOR_TYPE_TEMPERATURE,
                creator_id=self.user_id,
                is_active=True,
            )
            local_sensor = Sensor(
                name='Beet Temperatur',
                key='temperature-local',
                sensor_type=SENSOR_TYPE_TEMPERATURE,
                creator_id=self.user_id,
                is_active=True,
            )
            local_sensor.locations.append(self.location)
            trash_sensor = Sensor(
                name='Papierkorb Temperatur',
                key='temperature-trash',
                sensor_type=SENSOR_TYPE_TEMPERATURE,
                creator_id=self.user_id,
                is_active=True,
            )
            trash_sensor.locations.append(trash)
            other_sensor = Sensor(
                name='Anderes Beet Temperatur',
                key='temperature-other',
                sensor_type=SENSOR_TYPE_TEMPERATURE,
                creator_id=self.user_id,
                is_active=True,
            )
            other_sensor.locations.append(other)
            db.session.add_all([global_sensor, local_sensor, trash_sensor, other_sensor])
            db.session.commit()
            expected_sensor_ids = {local_sensor.id}

        with patch('app.views.influx_service.get_sensor_time_series_adapter', return_value=FakeAdapter()), \
                patch('app.views.latest_sensor_value', return_value={'time': '2026-06-03T08:00:00Z', 'value': 35.2}):
            response = self.client.get(f'/locations/{self.location_id}/soil-moisture')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        temperature_series = payload['weather_series']['temperature']['series']
        self.assertEqual({item['sensor_id'] for item in temperature_series}, expected_sensor_ids)
        self.assertEqual(
            {int(item['points'][0]['value']) for item in temperature_series},
            expected_sensor_ids,
        )


    def test_location_sensor_mapping_applies_type_filter_and_global_assignment(self):
        with self.app.app_context():
            from app.views import _location_irrigation_sensors, _location_soil_moisture_sensors

            trash = Location(name='Papierkorb')
            db.session.add(trash)
            db.session.flush()
            global_soil = Sensor(
                name='Globale Bodenfeuchte',
                key='soil-global',
                sensor_type=SENSOR_TYPE_SOIL_MOISTURE,
                creator_id=self.user_id,
                is_active=True,
            )
            global_temperature = Sensor(
                name='Globale Temperatur',
                key='temperature-global-filtered',
                sensor_type=SENSOR_TYPE_TEMPERATURE,
                creator_id=self.user_id,
                is_active=True,
            )
            global_irrigation = Sensor(
                name='Globale Bewässerung',
                key='irrigation-global',
                sensor_type=SENSOR_TYPE_IRRIGATION,
                creator_id=self.user_id,
                is_active=True,
            )
            trash_soil = Sensor(
                name='Papierkorb Bodenfeuchte',
                key='soil-trash',
                sensor_type=SENSOR_TYPE_SOIL_MOISTURE,
                creator_id=self.user_id,
                is_active=True,
            )
            trash_soil.locations.append(trash)
            db.session.add_all([global_soil, global_temperature, global_irrigation, trash_soil])
            db.session.commit()
            global_soil_id = global_soil.id
            global_temperature_id = global_temperature.id
            global_irrigation_id = global_irrigation.id
            trash_soil_id = trash_soil.id

            soil_sensor_ids = {sensor.id for sensor in _location_soil_moisture_sensors(self.location_id)}
            irrigation_sensor_ids = {sensor.id for sensor in _location_irrigation_sensors(self.location_id)}

        self.assertIn(self.sensor_id, soil_sensor_ids)
        self.assertNotIn(global_soil_id, soil_sensor_ids)
        self.assertNotIn(global_temperature_id, soil_sensor_ids)
        self.assertNotIn(trash_soil_id, soil_sensor_ids)
        self.assertEqual(irrigation_sensor_ids, {global_irrigation_id})

    def test_location_detail_hides_soil_moisture_when_influx_fails(self):
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
            page_response = self.client.get(f'/locations/{self.location_id}')
            response = self.client.get(f'/locations/{self.location_id}/soil-moisture')

        self.assertEqual(page_response.status_code, 200)
        self.assertIn('Sensor-Verlauf', page_response.get_data(as_text=True))
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload['has_series_data'])
        self.assertIn('InfluxDB-Fehler beim Laden einzelner Sensoren', payload['hints'][0])
        self.assertIn('Influx nicht erreichbar', payload['hints'][0])

    def test_location_detail_renders_aggregated_current_soil_moisture(self):
        with self.app.app_context():
            first_sensor = db.session.get(Sensor, self.sensor_id)
            second_sensor = Sensor(
                name='Bodenfeuchte Sensor 2',
                key='soil-sensor-2',
                influx_measurement='soil_moisture',
                influx_field='value',
                creator_id=self.user_id,
            )
            second_sensor.locations.append(db.session.get(Location, self.location_id))
            inactive_sensor = Sensor(
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
            page_response = self.client.get(f'/locations/{self.location_id}')
            response = self.client.get(f'/locations/{self.location_id}/soil-moisture')

        self.assertEqual(page_response.status_code, 200)
        page_html = page_response.get_data(as_text=True)
        self.assertIn('Bodenfeuchte-Daten werden im Hintergrund geladen.', page_html)
        self.assertNotIn('37,5 %', page_html)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['current']['label'], '37,5 %')
        self.assertEqual(len([item for item in payload['current']['sensor_values'] if item['value'] is not None]), 2)
        self.assertNotIn('Inaktiver Bodenfeuchte Sensor', response.get_data(as_text=True))

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
