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
            self.location = Location(name='Sonnenbeet', user_id=self.user.id, creator_id=self.user.id)
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

    def test_location_timeline_uses_event_form_layout_and_renders_title(self):
        response = self.client.get(f'/locations/{self.location_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<section class="timeline-panel">', html)
        self.assertIn('<h4 class="timeline-toolbar-title">Neues Event</h4>', html)
        self.assertIn('id="location-timeline-title"', html)
        self.assertIn('class="grid timeline-form"', html)

        response = self.client.post(
            f'/locations/{self.location_id}/timeline/new',
            data={'title': 'Beet vorbereitet', 'description': 'Kompost eingearbeitet'},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<strong>Beet vorbereitet</strong>', html)
        self.assertIn('Kompost eingearbeitet', html)
        with self.app.app_context():
            entry = TimelineEntry.query.filter_by(scope_type='location', scope_id=self.location_id).one()
            self.assertEqual(entry.title, 'Beet vorbereitet')
            self.assertEqual(entry.description, 'Kompost eingearbeitet')


if __name__ == '__main__':
    unittest.main()
