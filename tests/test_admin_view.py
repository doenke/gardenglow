import os
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import GardenMap, IrrigationPredictionModel, Location, TimelineEntry, User, db


class AdminViewTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.sqlite')
        self.upload_folder = os.path.join(self.temp_dir, 'uploads')
        self.avatar_folder = os.path.join(self.temp_dir, 'avatars')
        self.map_folder = os.path.join(self.temp_dir, 'maps')
        self.backup_folder = os.path.join(self.temp_dir, 'backups')
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        os.environ['UPLOAD_FOLDER'] = self.upload_folder
        os.environ['AVATAR_FOLDER'] = self.avatar_folder
        os.environ['MAP_FOLDER'] = self.map_folder
        os.environ['BACKUP_FOLDER'] = self.backup_folder
        os.environ['APP_VERSION'] = 'test-version'
        os.environ['GIT_COMMIT'] = 'test-commit'
        os.environ['COMMON_NAME_LOOKUP_LANG'] = 'fr'
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        os.makedirs(self.backup_folder, exist_ok=True)
        with open(os.path.join(self.upload_folder, 'referenced.jpg'), 'wb') as f:
            f.write(b'referenced')
        with open(os.path.join(self.upload_folder, 'orphan.jpg'), 'wb') as f:
            f.write(b'orphan')
        with open(os.path.join(self.avatar_folder, 'avatar.png'), 'wb') as f:
            f.write(b'avatar')
        with open(os.path.join(self.map_folder, 'map.png'), 'wb') as f:
            f.write(b'map')

        with self.app.app_context():
            self.user = User(sub='test-user', name='Test User', avatar_filename='avatar.png')
            self.location = Location(name='Tomatenbeet', target_soil_moisture_percent=62.5)
            db.session.add_all([self.user, self.location])
            db.session.flush()
            db.session.add(TimelineEntry(scope_type='plant', scope_id=1, attachment_filename='referenced.jpg', attachment_kind='image', creator_id=self.user.id))
            db.session.add(GardenMap(filename='map.png', calibration_points='[]', boundary_points='[]'))
            db.session.commit()
            db.session.add(IrrigationPredictionModel(
                location_id=self.location.id,
                trained_at=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
                sample_count=7,
                intercept=0.0,
                coefficients_json='[]',
                feature_names_json='[]',
                metrics_json='{"rmse":3.456}',
            ))
            db.session.commit()
            self.user_id = self.user.id
            self.location_id = self.location.id

        shutil.copy2(self.db_path, os.path.join(self.backup_folder, 'backup.sqlite'))

        with self.client.session_transaction() as session:
            session['user_id'] = self.user_id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        for key in ('DATABASE_URL', 'UPLOAD_FOLDER', 'AVATAR_FOLDER', 'MAP_FOLDER', 'BACKUP_FOLDER', 'APP_VERSION', 'GIT_COMMIT', 'COMMON_NAME_LOOKUP_LANG'):
            os.environ.pop(key, None)
        shutil.rmtree(self.temp_dir)

    def test_admin_page_lists_storage_orphans_backups_and_version(self):
        response = self.client.get('/admin')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Admin &amp; Wartung', html)
        self.assertIn('orphan.jpg', html)
        self.assertNotIn('referenced.jpg</td>', html)
        self.assertIn('backup.sqlite', html)
        self.assertIn('test-version', html)
        self.assertIn('test-commit', html)
        self.assertIn('Umgebungsvariablen', html)
        self.assertIn('COMMON_NAME_LOOKUP_LANG', html)
        self.assertIn('fr', html)
        self.assertIn('MAX_ATTACHMENT_SIZE_BYTES', html)
        self.assertIn('Default', html)
        self.assertIn('Alle verwaisten Uploads löschen', html)
        self.assertIn('Backup anlegen', html)
        self.assertIn('/admin/backup/create', html)
        self.assertIn('/admin/backup/restore', html)
        self.assertIn('/admin/backup/delete', html)
        self.assertIn('Wiederherstellen', html)
        self.assertIn('admin-breakable admin-filename-cell', html)
        self.assertIn('Bewässerungs-Prognose: Modelltraining', html)
        self.assertIn('Tomatenbeet', html)
        self.assertIn('2026-06-01 12:30:00', html)
        self.assertIn('7</td>', html)
        self.assertIn('3.46', html)
        self.assertIn('/admin/irrigation-prediction/train', html)
        self.assertIn('Training starten', html)

    def test_irrigation_training_can_be_started_from_admin_page(self):
        with patch('app.views.influx_service.get_sensor_time_series_adapter', return_value=object()) as adapter_mock, \
                patch('app.views.irrigation_prediction_service.train_model_for_location') as train_mock:
            response = self.client.post(
                '/admin/irrigation-prediction/train',
                data={'location_id': str(self.location_id)},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        adapter_mock.assert_called_once()
        train_mock.assert_called_once()
        args, kwargs = train_mock.call_args
        self.assertEqual(args[0].id, self.location_id)
        self.assertEqual(args[1], 62.5)
        self.assertEqual(kwargs['max_minutes'], 120.0)
        self.assertIn('Modelltraining für 1 Beet(er) abgeschlossen.', response.get_data(as_text=True))

    def test_backup_can_be_created_from_admin_page(self):
        response = self.client.post('/admin/backup/create', follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Backup „garden-backup-', html)
        backup_files = [
            filename for filename in os.listdir(self.backup_folder)
            if filename.startswith('garden-backup-') and filename.endswith('.sqlite')
        ]
        self.assertEqual(len(backup_files), 1)
        self.assertIn(backup_files[0], html)

        backup_path = os.path.join(self.backup_folder, backup_files[0])
        with sqlite3.connect(backup_path) as connection:
            row = connection.execute("select name from user where sub = 'test-user'").fetchone()
        self.assertEqual(row, ('Test User',))

    def test_backup_can_be_deleted_from_admin_page(self):
        response = self.client.post(
            '/admin/backup/delete',
            data={'filename': 'backup.sqlite'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(self.backup_folder, 'backup.sqlite')))
        self.assertIn('Backup „backup.sqlite“ wurde gelöscht.', response.get_data(as_text=True))

    def test_backup_can_be_restored_from_admin_page(self):
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.name = 'Changed User'
            db.session.commit()

        response = self.client.post(
            '/admin/backup/restore',
            data={'filename': 'backup.sqlite'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute("select name from user where sub = 'test-user'").fetchone()
        self.assertEqual(row, ('Test User',))
        self.assertIn('Backup „backup.sqlite“ wurde wiederhergestellt.', response.get_data(as_text=True))

    def test_invalid_backup_is_not_restored(self):
        invalid_backup = os.path.join(self.backup_folder, 'invalid.sqlite')
        with open(invalid_backup, 'wb') as f:
            f.write(b'invalid')

        response = self.client.post(
            '/admin/backup/restore',
            data={'filename': 'invalid.sqlite'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('keine gültige GardenGlow-SQLite-Datenbank', response.get_data(as_text=True))

    def test_orphan_file_can_be_deleted(self):
        response = self.client.post(
            '/admin/orphan-upload/delete',
            data={'folder_key': 'uploads', 'filename': 'orphan.jpg'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(self.upload_folder, 'orphan.jpg')))
        self.assertTrue(os.path.exists(os.path.join(self.upload_folder, 'referenced.jpg')))
        self.assertIn('wurde gelöscht', response.get_data(as_text=True))

    def test_all_orphan_files_in_folder_can_be_deleted(self):
        another_orphan = os.path.join(self.upload_folder, 'another-orphan.jpg')
        with open(another_orphan, 'wb') as f:
            f.write(b'another orphan')

        response = self.client.post(
            '/admin/orphan-uploads/delete-all',
            data={'folder_key': 'uploads'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(self.upload_folder, 'orphan.jpg')))
        self.assertFalse(os.path.exists(another_orphan))
        self.assertTrue(os.path.exists(os.path.join(self.upload_folder, 'referenced.jpg')))
        self.assertIn('verwaiste Upload-Dateien', response.get_data(as_text=True))

    def test_referenced_file_is_not_deleted_as_orphan(self):
        response = self.client.post(
            '/admin/orphan-upload/delete',
            data={'folder_key': 'uploads', 'filename': 'referenced.jpg'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(os.path.exists(os.path.join(self.upload_folder, 'referenced.jpg')))
        self.assertIn('nicht als verwaist erkannt', response.get_data(as_text=True))

    def test_data_export_downloads_zip_with_json_payload(self):
        response = self.client.get('/admin/export')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/zip')
        with zipfile.ZipFile(BytesIO(response.data)) as archive:
            self.assertIn('garden-export.json', archive.namelist())
            payload = archive.read('garden-export.json').decode('utf-8')
        self.assertIn('test-version', payload)
        self.assertIn('timeline_entry', payload)


if __name__ == '__main__':
    unittest.main()
