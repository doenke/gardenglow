import json
import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import db


class ApiRequestHeaderLoggingTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        os.environ['IRRIGATION_PREDICTION_TRAIN_CRON_ENABLED'] = 'false'
        os.environ['WIDGET_API_KEY'] = 'secret'

    def tearDown(self):
        os.environ.pop('DATABASE_URL', None)
        os.environ.pop('IRRIGATION_PREDICTION_TRAIN_CRON_ENABLED', None)
        os.environ.pop('WIDGET_API_KEY', None)
        os.environ.pop('API_REQUEST_HEADER_LOGGING', None)
        os.environ.pop('DEBUG_MODE', None)
        if hasattr(self, 'app'):
            with self.app.app_context():
                db.session.remove()
                db.drop_all()
        os.unlink(self.db_path)

    def _create_client(self, *, header_logging=None, debug_mode=None):
        if header_logging is not None:
            os.environ['API_REQUEST_HEADER_LOGGING'] = header_logging
        if debug_mode is not None:
            os.environ['DEBUG_MODE'] = debug_mode
        self.app = create_app()
        self.app.config['TESTING'] = True
        return self.app.test_client()

    def test_logs_request_headers_for_api_endpoints_when_enabled(self):
        client = self._create_client(header_logging='true')

        with self.assertLogs(self.app.logger.name, level='INFO') as captured:
            response = client.get('/api/stats', headers={
                'X-API-Key': 'secret',
                'X-Debug-Client': 'pytest',
            })

        self.assertEqual(response.status_code, 200)
        log_line = next(line for line in captured.output if 'API request headers:' in line)
        payload = json.loads(log_line.split('API request headers: ', 1)[1])
        self.assertEqual(payload['method'], 'GET')
        self.assertEqual(payload['path'], '/api/stats')
        self.assertEqual(payload['endpoint'], 'main.api_stats')
        self.assertEqual(payload['headers']['X-Debug-Client'], 'pytest')
        self.assertEqual(payload['headers']['X-Api-Key'], 'secret')

    def test_does_not_log_non_api_headers_when_enabled(self):
        client = self._create_client(header_logging='true')

        with self.assertLogs(self.app.logger.name, level='INFO') as captured:
            response = client.get('/healthz', headers={'X-Debug-Client': 'pytest'})
            self.app.logger.info('sentinel')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(any('API request headers:' in line for line in captured.output))

    def test_debug_mode_enables_api_header_logging_by_default(self):
        client = self._create_client(debug_mode='true')

        with self.assertLogs(self.app.logger.name, level='INFO') as captured:
            response = client.get('/api/stats', headers={
                'X-API-Key': 'secret',
                'X-Debug-Client': 'debug-mode',
            })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('API request headers:' in line for line in captured.output))


if __name__ == '__main__':
    unittest.main()
