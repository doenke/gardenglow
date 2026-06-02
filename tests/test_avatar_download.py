import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.auth import _avatar_target_path, _download_avatar
from app.models import User, db


class FakeAvatarResponse:
    def __init__(self, chunks, headers=None):
        self.chunks = chunks
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1):
        yield from self.chunks


class AvatarDownloadSecurityTest(unittest.TestCase):
    def setUp(self):
        self.original_env = {
            name: os.environ.get(name)
            for name in (
                'DATABASE_URL',
                'AVATAR_FOLDER',
                'MAX_AVATAR_SIZE_BYTES',
                'OIDC_SERVER_METADATA_URL',
                'OIDC_CLIENT_ID',
                'OIDC_CLIENT_SECRET',
            )
        }
        for name in ('OIDC_SERVER_METADATA_URL', 'OIDC_CLIENT_ID', 'OIDC_CLIENT_SECRET'):
            os.environ.pop(name, None)

        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, 'garden.sqlite')
        self.avatar_folder = os.path.join(self.tempdir.name, 'avatars')
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        os.environ['AVATAR_FOLDER'] = self.avatar_folder
        os.environ['MAX_AVATAR_SIZE_BYTES'] = '10'
        self.app = create_app()
        self.app.config.update(TESTING=True)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        self.tempdir.cleanup()
        for name, value in self.original_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_avatar_filename_uses_safe_random_name_for_path_like_subject(self):
        user = User(sub='../evil/user')
        response = FakeAvatarResponse(
            [b'avatar'],
            {'Content-Type': 'image/png', 'Content-Length': '6'},
        )

        with self.app.app_context(), patch('app.auth.requests.get', return_value=response) as get:
            _download_avatar(user, 'https://example.test/profile/avatar.png')

        get.assert_called_once_with('https://example.test/profile/avatar.png', timeout=10, stream=True)
        self.assertIsNotNone(user.avatar_filename)
        self.assertTrue(user.avatar_filename.startswith('avatar_'))
        self.assertTrue(user.avatar_filename.endswith('.png'))
        self.assertNotIn('/', user.avatar_filename)
        self.assertNotIn('..', user.avatar_filename)
        self.assertNotIn('evil', user.avatar_filename)

        target = os.path.abspath(os.path.join(self.avatar_folder, user.avatar_filename))
        self.assertEqual(os.path.commonpath([os.path.abspath(self.avatar_folder), target]), os.path.abspath(self.avatar_folder))
        with open(target, 'rb') as f:
            self.assertEqual(f.read(), b'avatar')

    def test_avatar_target_path_rejects_paths_outside_avatar_folder(self):
        with self.assertRaises(ValueError):
            _avatar_target_path(self.avatar_folder, '../escape.png')

    def test_invalid_content_type_is_rejected(self):
        user = User(sub='safe-user')
        response = FakeAvatarResponse(
            [b'not-an-image'],
            {'Content-Type': 'text/html', 'Content-Length': '10'},
        )

        with self.app.app_context(), patch('app.auth.requests.get', return_value=response):
            _download_avatar(user, 'https://example.test/avatar.png')

        self.assertIsNone(user.avatar_filename)
        self.assertEqual(os.listdir(self.avatar_folder), [])

    def test_oversized_avatar_stream_is_aborted_and_removed(self):
        user = User(sub='safe-user')
        response = FakeAvatarResponse(
            [b'12345', b'67890', b'X'],
            {'Content-Type': 'image/jpeg'},
        )

        with self.app.app_context(), patch('app.auth.requests.get', return_value=response):
            _download_avatar(user, 'https://example.test/avatar.jpg')

        self.assertIsNone(user.avatar_filename)
        self.assertEqual(os.listdir(self.avatar_folder), [])


if __name__ == '__main__':
    unittest.main()
