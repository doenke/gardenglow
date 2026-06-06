import os
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import InfluxIntegrationConfig, User, db


class InfluxConfigViewTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        os.environ['WIDGET_API_KEY'] = 'plain-widget-token'
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            user = User(sub='config-user', name='Config User')
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

        with self.client.session_transaction() as session:
            session['user_id'] = self.user_id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)
        os.environ.pop('WIDGET_API_KEY', None)
        os.environ.pop('GARDENGLOW_EXTERNAL_URL', None)

    def test_saves_influx_integration_config(self):
        response = self.client.post(
            '/config/influx',
            data={
                'influx_url': ' https://influx.local:8086 ',
                'influx_org': 'Garten',
                'influx_bucket': 'soil',
                'influx_token': 'secret-influx-token',
                'homeassistant_url': 'https://ha.local:8123',
                'homeassistant_token': 'secret-ha-token',
                'verify_tls': '1',
                'timeout_seconds': '42',
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            config = InfluxIntegrationConfig.query.one()
            self.assertEqual(config.influx_url, 'https://influx.local:8086')
            self.assertEqual(config.influx_org, 'Garten')
            self.assertEqual(config.influx_bucket, 'soil')
            self.assertEqual(config.influx_token, 'secret-influx-token')
            self.assertEqual(config.homeassistant_url, 'https://ha.local:8123')
            self.assertEqual(config.homeassistant_token, 'secret-ha-token')
            self.assertTrue(config.verify_tls)
            self.assertEqual(config.timeout_seconds, 42)
            self.assertIsNotNone(config.created_at)
            self.assertIsNotNone(config.updated_at)

    def test_config_form_masks_existing_tokens_and_keeps_them_when_blank(self):
        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(
                influx_url='https://old-influx.local',
                influx_org='Old Org',
                influx_bucket='old-bucket',
                influx_token='old-influx-token',
                homeassistant_url='https://old-ha.local',
                homeassistant_token='old-ha-token',
                gardenglow_external_url='https://garden.example',
            ))
            db.session.commit()

        response = self.client.get('/config')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="influxdb-config"', html)
        self.assertIn('id="homeassistant-config"', html)
        self.assertIn('role="menuitem">Sensoren</a>', html)
        self.assertNotIn('Bodenfeuchte-Sensoren', html)
        self.assertIn('Home-Assistant-Blueprint herunterladen', html)
        self.assertIn('id="homeassistant-blueprint-url"', html)
        self.assertIn('data-copy-target="homeassistant-blueprint-url"', html)
        self.assertIn('plain-widget-token', html)
        self.assertIn('https://garden.example', html)
        self.assertNotIn('Inhaltsverzeichnis', html)
        self.assertNotIn('Springe direkt zum passenden Kapitel', html)
        self.assertIn('https://old-influx.local', html)
        self.assertIn('Gespeicherter Token bleibt erhalten', html)
        self.assertNotIn('old-influx-token', html)
        self.assertNotIn('old-ha-token', html)

        response = self.client.post(
            '/config/influx',
            data={
                'influx_url': 'https://new-influx.local',
                'influx_org': 'New Org',
                'influx_bucket': 'new-bucket',
                'homeassistant_url': 'https://new-ha.local',
                'timeout_seconds': '15',
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            config = InfluxIntegrationConfig.query.one()
            self.assertEqual(config.influx_url, 'https://new-influx.local')
            self.assertEqual(config.influx_token, 'old-influx-token')
            self.assertEqual(config.homeassistant_token, 'old-ha-token')
            self.assertEqual(config.gardenglow_external_url, 'https://garden.example')
            self.assertFalse(config.verify_tls)
            self.assertEqual(config.timeout_seconds, 15)

        response = self.client.post(
            '/config/homeassistant',
            data={
                'homeassistant_url': 'https://new-ha.local',
                'gardenglow_external_url': ' https://new-garden.example/ ',
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            config = InfluxIntegrationConfig.query.one()
            self.assertEqual(config.homeassistant_url, 'https://new-ha.local')
            self.assertEqual(config.homeassistant_token, 'old-ha-token')
            self.assertEqual(config.gardenglow_external_url, 'https://new-garden.example')


    def test_homeassistant_blueprint_is_public_and_uses_external_base_url(self):
        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(gardenglow_external_url='https://garden.example/root/'))
            db.session.commit()

        with self.client.session_transaction() as session:
            session.clear()

        response = self.client.get('/homeassistant/gardenglow-irrigation-blueprint.yaml')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/x-yaml')
        yaml = response.get_data(as_text=True)
        self.assertIn('blueprint:', yaml)
        self.assertIn('domain: automation', yaml)
        self.assertIn('default: "https://garden.example/root"', yaml)
        self.assertIn('api_token: !input api_token', yaml)
        self.assertIn('irrigation_start_time: !input irrigation_start_time', yaml)
        self.assertIn('at: !input irrigation_start_time', yaml)
        self.assertIn('watering_entity: !input watering_entity', yaml)
        self.assertIn('minutes_helper_entity: !input minutes_helper_entity', yaml)
        self.assertIn('rest_command.gardenglow_get_irrigation_minutes', yaml)
        self.assertIn('response_variable: gardenglow_response', yaml)
        self.assertIn('valve.open_valve', yaml)
        self.assertIn('switch.turn_on', yaml)


    def test_legacy_homeassistant_template_url_is_removed(self):
        with self.client.session_transaction() as session:
            session.clear()

        response = self.client.get('/homeassistant/gardenglow-irrigation-template.yaml')

        self.assertEqual(response.status_code, 404)

    def test_config_form_shows_connection_test_buttons(self):
        response = self.client.get('/config')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('action="/config/influx/test"', html)
        self.assertIn('InfluxDB-Verbindung testen', html)
        self.assertIn('action="/config/homeassistant"', html)
        self.assertIn('action="/config/homeassistant/test"', html)
        self.assertIn('Homeassistant-Verbindung testen', html)
        self.assertIn('action="/config/connection-options"', html)
        self.assertNotIn('action="/config/weather-sensors"', html)
        self.assertNotIn('Globale Homeassistant-Entities für Wetterdaten', html)

    def test_saves_global_soil_moisture_target(self):
        response = self.client.post(
            '/config/soil-moisture-target',
            data={'target_soil_moisture_percent': '55,5'},
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            config = InfluxIntegrationConfig.query.one()
            self.assertEqual(config.target_soil_moisture_percent, 55.5)

        response = self.client.get('/config')
        html = response.get_data(as_text=True)
        self.assertIn('id="soil-moisture-target"', html)
        self.assertIn('value="55.5"', html)

    def test_global_soil_moisture_target_can_be_cleared(self):
        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(target_soil_moisture_percent=55))
            db.session.commit()

        response = self.client.post('/config/soil-moisture-target', data={'target_soil_moisture_percent': ''})

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            config = InfluxIntegrationConfig.query.one()
            self.assertIsNone(config.target_soil_moisture_percent)

    def test_homeassistant_config_saves_without_changing_influx_values(self):
        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(
                influx_url='https://influx.local',
                influx_org='Garten',
                influx_bucket='soil',
                influx_token='old-influx-token',
            ))
            db.session.commit()

        response = self.client.post(
            '/config/homeassistant',
            data={
                'homeassistant_url': 'https://ha.local:8123',
                'homeassistant_token': 'new-ha-token',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('#homeassistant-config', response.headers['Location'])
        with self.app.app_context():
            config = InfluxIntegrationConfig.query.one()
            self.assertEqual(config.influx_url, 'https://influx.local')
            self.assertEqual(config.influx_token, 'old-influx-token')
            self.assertEqual(config.homeassistant_url, 'https://ha.local:8123')
            self.assertEqual(config.homeassistant_token, 'new-ha-token')


    def test_connection_options_save_separately(self):
        response = self.client.post(
            '/config/connection-options',
            data={
                'timeout_seconds': '23',
                'verify_tls': '1',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('#connection-options', response.headers['Location'])
        with self.app.app_context():
            config = InfluxIntegrationConfig.query.one()
            self.assertEqual(config.timeout_seconds, 23)
            self.assertTrue(config.verify_tls)

    def test_influx_connection_test_uses_saved_config(self):
        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(
                influx_url='https://influx.local:8086',
                influx_org='Garten',
                influx_bucket='soil',
                influx_token='secret-influx-token',
                verify_tls=False,
                timeout_seconds=7,
            ))
            db.session.commit()

        with patch('app.views.FluxInfluxQueryAdapter') as adapter_class:
            adapter_class.return_value.health.return_value = {
                'ok': True,
                'message': 'InfluxDB ist erreichbar.',
            }

            response = self.client.post('/config/influx/test')

        self.assertEqual(response.status_code, 302)
        service_config = adapter_class.call_args.args[0]
        self.assertEqual(service_config.url, 'https://influx.local:8086')
        self.assertEqual(service_config.token, 'secret-influx-token')
        self.assertEqual(service_config.org, 'Garten')
        self.assertEqual(service_config.bucket, 'soil')
        self.assertEqual(service_config.timeout_seconds, 7)
        self.assertFalse(service_config.verify_tls)
        adapter_class.return_value.health.assert_called_once_with()

    def test_homeassistant_connection_test_calls_api_endpoint(self):
        with self.app.app_context():
            db.session.add(InfluxIntegrationConfig(
                homeassistant_url='https://ha.local:8123',
                homeassistant_token='secret-ha-token',
                verify_tls=False,
                timeout_seconds=9,
            ))
            db.session.commit()

        response_mock = Mock()
        response_mock.raise_for_status.return_value = None
        with patch('app.views.requests.get', return_value=response_mock) as requests_get:
            response = self.client.post('/config/homeassistant/test')

        self.assertEqual(response.status_code, 302)
        requests_get.assert_called_once_with(
            'https://ha.local:8123/api/',
            headers={'Authorization': 'Bearer secret-ha-token'},
            timeout=9,
            verify=False,
        )
        response_mock.raise_for_status.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
