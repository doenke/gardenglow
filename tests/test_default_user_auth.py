import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.auth import DEFAULT_LOCAL_USER_NAME, DEFAULT_LOCAL_USER_SUB
from app.models import User, db


class DefaultUserAuthTest(unittest.TestCase):
    def setUp(self):
        self.original_oidc_env = {
            name: os.environ.get(name)
            for name in (
                'OIDC_SERVER_METADATA_URL',
                'OIDC_CLIENT_ID',
                'OIDC_CLIENT_SECRET',
                'OIDC_LOGOUT_URL',
            )
        }
        for name in self.original_oidc_env:
            os.environ.pop(name, None)

        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)
        for name, value in self.original_oidc_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_protected_page_auto_logs_in_default_user_without_oidc(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(DEFAULT_LOCAL_USER_NAME, html)
        self.assertNotIn('Logout', html)
        with self.app.app_context():
            user = User.query.filter_by(sub=DEFAULT_LOCAL_USER_SUB).one()
            self.assertEqual(user.name, DEFAULT_LOCAL_USER_NAME)

        with self.client.session_transaction() as session:
            self.assertEqual(session.get('user_id'), user.id)

    def test_login_route_uses_default_user_without_oidc(self):
        response = self.client.get('/login')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')
        with self.app.app_context():
            user = User.query.filter_by(sub=DEFAULT_LOCAL_USER_SUB).one()
            self.assertEqual(user.name, DEFAULT_LOCAL_USER_NAME)

        with self.client.session_transaction() as session:
            self.assertEqual(session.get('user_id'), user.id)


if __name__ == '__main__':
    unittest.main()
