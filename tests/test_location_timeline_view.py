import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import Location, TimelineEntry, User, db


class LocationTimelineViewTest(unittest.TestCase):
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
            self.location = Location(name='Beet', user_id=self.user.id, creator_id=self.user.id)
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

    def test_location_timeline_form_accepts_optional_title_description_and_file(self):
        response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="location-timeline-title"', html)
        self.assertIn('name="title"', html)
        self.assertIn('Titel, Text, Bild oder eine Kombination möglich.', html)

    def test_location_timeline_saves_text_only_without_file_warning(self):
        response = self.client.post(
            f'/locations/{self.location_id}/timeline/new',
            data={'description': '  Beet gemulcht  '},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Beet gemulcht', html)
        self.assertNotIn('Bitte eine Datei auswählen.', html)
        self.assertNotIn('Bitte Beschreibung eingeben.', html)
        with self.app.app_context():
            entry = TimelineEntry.query.filter_by(scope_type='location', scope_id=self.location_id).one()
            self.assertIsNone(entry.title)
            self.assertEqual(entry.description, 'Beet gemulcht')
            self.assertIsNone(entry.attachment_filename)

    def test_location_timeline_saves_title_only(self):
        response = self.client.post(
            f'/locations/{self.location_id}/timeline/new',
            data={'title': '  Rückschnitt  '},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Rückschnitt', html)
        self.assertNotIn('Bitte eine Datei auswählen.', html)
        self.assertNotIn('Bitte Beschreibung eingeben.', html)
        with self.app.app_context():
            entry = TimelineEntry.query.filter_by(scope_type='location', scope_id=self.location_id).one()
            self.assertEqual(entry.title, 'Rückschnitt')
            self.assertIsNone(entry.description)
            self.assertIsNone(entry.attachment_filename)

    def test_location_timeline_requires_at_least_one_field(self):
        response = self.client.post(
            f'/locations/{self.location_id}/timeline/new',
            data={'title': ' ', 'description': ' '},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Bitte Titel, Beschreibung oder Datei angeben.', html)
        self.assertNotIn('Bitte eine Datei auswählen.', html)
        with self.app.app_context():
            self.assertEqual(TimelineEntry.query.filter_by(scope_type='location', scope_id=self.location_id).count(), 0)


if __name__ == '__main__':
    unittest.main()
