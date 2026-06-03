import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import db


class LocationSchemaMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'

    def tearDown(self):
        if hasattr(self, 'app'):
            with self.app.app_context():
                db.session.remove()
                db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)

    def test_create_app_removes_legacy_location_owner_columns(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(
                '''
                CREATE TABLE user (
                    id INTEGER NOT NULL PRIMARY KEY,
                    sub VARCHAR(255) NOT NULL UNIQUE
                );
                INSERT INTO user (id, sub) VALUES (1, 'legacy-user');
                CREATE TABLE location (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    color VARCHAR(7),
                    polygon_points TEXT,
                    user_id INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES user (id),
                    FOREIGN KEY(creator_id) REFERENCES user (id)
                );
                INSERT INTO location (
                    id, name, description, color, polygon_points, user_id, creator_id
                ) VALUES (1, 'Altbeet', 'Bleibt erhalten', '#2f6d40', '[]', 1, 1);
                '''
            )

        self.app = create_app()

        with sqlite3.connect(self.db_path) as connection:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(location)')}
            row = connection.execute(
                'SELECT id, name, description, color, polygon_points FROM location'
            ).fetchone()

        self.assertNotIn('user_id', columns)
        self.assertNotIn('creator_id', columns)
        self.assertEqual(row, (1, 'Altbeet', 'Bleibt erhalten', '#2f6d40', '[]'))
