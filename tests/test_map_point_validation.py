import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.map_data import validate_calibration_points, validate_polygon_points
from app.models import GardenMap, Location, User, db


class MapPointValidationTest(unittest.TestCase):
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
            self.location = Location(
                name='Beet',
                polygon_points='[]',
            )
            db.session.add(self.location)
            db.session.add(GardenMap(filename='map.svg', calibration_points='[]', boundary_points='[]'))
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

    def test_valid_calibration_payload_is_normalized(self):
        payload = '[{"y":20.5,"coord_y":6.1275,"x":10,"coord_x":50.7956}]'

        self.assertEqual(
            validate_calibration_points(payload),
            '[{"x":10,"y":20.5,"coord_x":50.7956,"coord_y":6.1275}]',
        )

    def test_malicious_calibration_payload_is_rejected(self):
        payload = '[{"x":10,"y":20,"coord_x":50,"coord_y":"</script><script>alert(1)</script>"}]'

        response = self.client.post('/map/calibration', data={'calibration_points': payload})

        self.assertEqual(response.status_code, 400)
        self.assertIn('muss eine Zahl sein', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(GardenMap.query.first().calibration_points, '[]')

    def test_valid_boundary_payload_is_normalized_and_persisted(self):
        payload = '[{"x":50.7956,"y":6.1269},{"x":50.7961,"y":6.1275}]'

        response = self.client.post('/map/boundary', data={'boundary_points': payload})

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(
                GardenMap.query.first().boundary_points,
                '[{"x":50.7956,"y":6.1269},{"x":50.7961,"y":6.1275}]',
            )

    def test_malicious_polygon_payload_is_rejected(self):
        payload = '[{"x":50,"y":6,"onclick":"alert(1)"}]'

        response = self.client.post(f'/locations/{self.location_id}/map', data={'polygon_points': payload})

        self.assertEqual(response.status_code, 400)
        self.assertIn('darf nur die Felder x, y enthalten', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(db.session.get(Location, self.location_id).polygon_points, '[]')

    def test_out_of_range_polygon_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'zwischen -90 und 90'):
            validate_polygon_points('[{"x":91,"y":6}]')

    def test_templates_render_stored_malicious_values_as_json_not_executable_script(self):
        malicious = '</script><script>alert(1)</script>'
        with self.app.app_context():
            garden_map = GardenMap.query.first()
            garden_map.boundary_points = malicious
            garden_map.calibration_points = malicious
            db.session.get(Location, self.location_id).polygon_points = malicious
            db.session.commit()

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('const gardenBoundary = [];', html)
        self.assertIn('const calibrationPoints = [];', html)


if __name__ == '__main__':
    unittest.main()
