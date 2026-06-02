import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import Location, Plant, User, db


class IndexViewTest(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.close(self.db_fd)
        os.environ['DATABASE_URL'] = f'sqlite:///{self.db_path}'
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            self.user = User(sub='test-user', name='Test User')
            db.session.add(self.user)
            db.session.flush()
            self.location = Location(name='Sonnenbeet', user_id=self.user.id, creator_id=self.user.id)
            db.session.add(self.location)
            db.session.flush()
            db.session.add_all([
                Plant(
                    name='Lavendel',
                    common_name='Echter Lavendel',
                    location_id=self.location.id,
                    creator_id=self.user.id,
                ),
                Plant(
                    name='Salbei',
                    common_name=None,
                    location_id=self.location.id,
                    creator_id=self.user.id,
                ),
            ])
            db.session.commit()
            self.user_id = self.user.id

        with self.client.session_transaction() as session:
            session['user_id'] = self.user_id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)

    def test_index_plant_table_marks_empty_mobile_fields(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<table class="index-plants-table">', html)
        self.assertIn('class="index-plant-common-name-cell">Echter Lavendel</td>', html)
        self.assertIn('class="index-plant-common-name-cell is-empty"></td>', html)
        self.assertIn('class="index-plant-location-link"', html)


if __name__ == '__main__':
    unittest.main()
