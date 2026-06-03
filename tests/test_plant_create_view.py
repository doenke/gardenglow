import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import Location, Plant, TimelineEntry, User, db


class PlantCreateViewTest(unittest.TestCase):
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
            self.location = Location(name='Beet')
            db.session.add(self.location)
            db.session.commit()
            self.user_id = self.user.id
            self.location_id = self.location.id

        with self.client.session_transaction() as session:
            session['user_id'] = self.user_id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)

    def test_location_page_uses_create_dialog_instead_of_full_create_form(self):
        response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="plant-create-dialog"', html)
        self.assertIn('id="plant-create-name"', html)
        self.assertIn('name="plant_name"', html)
        self.assertIn('autocomplete="off"', html)
        self.assertIn('data-1p-ignore="true"', html)
        self.assertNotIn('name="name" placeholder="Name der Pflanze"', html)
        self.assertNotIn('id="plant-form"', html)
        self.assertNotIn('placeholder="Sorte/Kultivar"', html)

    def test_create_plant_redirects_to_open_masterdata_editor(self):
        response = self.client.post(f'/locations/{self.location_id}/plants/new', data={'plant_name': '  Salbei  '})

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            plant = Plant.query.filter_by(name='Salbei').one()
            self.assertEqual(response.headers['Location'], f'/plants/{plant.id}?edit=1')
            event = TimelineEntry.query.filter_by(scope_type='plant', scope_id=plant.id).one()
            self.assertEqual(event.title, 'Eingepflanzt')

            detail_response = self.client.get(response.headers['Location'])
            self.assertEqual(detail_response.status_code, 200)
            self.assertIn('<details open>', detail_response.get_data(as_text=True))

    def test_create_plant_requires_name(self):
        response = self.client.post(f'/locations/{self.location_id}/plants/new', data={'plant_name': '   '})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], f'/locations/{self.location_id}')
        with self.app.app_context():
            self.assertEqual(Plant.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
