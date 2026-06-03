import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import InfluxIntegrationConfig, User, db


class InfluxConfigViewTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
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
            ))
            db.session.commit()

        response = self.client.get('/config')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Homeassistant / InfluxDB', html)
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
            self.assertFalse(config.verify_tls)
            self.assertEqual(config.timeout_seconds, 15)


if __name__ == '__main__':
    unittest.main()
