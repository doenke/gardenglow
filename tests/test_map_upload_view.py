import io
import os
import shutil
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import GardenMap, User, db


PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16
PDF_BYTES = b'%PDF-1.7\n% fake pdf\n'


class MapUploadViewTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.sqlite')
        self.map_folder = os.path.join(self.temp_dir, 'maps')
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        os.environ['UPLOAD_FOLDER'] = os.path.join(self.temp_dir, 'uploads')
        os.environ['AVATAR_FOLDER'] = os.path.join(self.temp_dir, 'avatars')
        os.environ['MAP_FOLDER'] = self.map_folder
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            self.user = User(sub='test-user', name='Test User')
            db.session.add(self.user)
            db.session.commit()
            self.user_id = self.user.id

        with self.client.session_transaction() as session:
            session['user_id'] = self.user_id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        for key in ('DATABASE_URL', 'UPLOAD_FOLDER', 'AVATAR_FOLDER', 'MAP_FOLDER'):
            os.environ.pop(key, None)
        shutil.rmtree(self.temp_dir)

    def _upload(self, filename, content, content_type):
        return self.client.post(
            '/map/upload',
            data={'map_image': (io.BytesIO(content), filename, content_type)},
            content_type='multipart/form-data',
            follow_redirects=True,
        )

    def test_allows_real_image_upload(self):
        response = self._upload('luftbild.png', PNG_BYTES, 'image/png')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Luftbild wurde hochgeladen.', response.get_data(as_text=True))
        with self.app.app_context():
            garden_map = GardenMap.query.one()
            self.assertTrue(garden_map.filename.endswith('_luftbild.png'))
            self.assertTrue(os.path.exists(os.path.join(self.map_folder, garden_map.filename)))

    def test_rejects_pdf_upload(self):
        response = self._upload('luftbild.pdf', PDF_BYTES, 'application/pdf')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Dateiendung nicht erlaubt. Bitte ein Luftbild als PNG, JPG, WEBP oder GIF hochladen.', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(GardenMap.query.count(), 0)
        self.assertEqual(os.listdir(self.map_folder), [])

    def test_rejects_non_image_content_with_image_extension(self):
        response = self._upload('luftbild.png', PDF_BYTES, 'image/png')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Luftbild-Dateityp nicht erlaubt. Bitte eine echte Bilddatei (PNG, JPG, WEBP oder GIF) hochladen.', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(GardenMap.query.count(), 0)
        self.assertEqual(os.listdir(self.map_folder), [])


if __name__ == '__main__':
    unittest.main()
