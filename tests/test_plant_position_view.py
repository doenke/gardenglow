import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import GardenMap, Location, Plant, User, db


class PlantPositionViewTest(unittest.TestCase):
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
                user_id=self.user.id,
                creator_id=self.user.id,
            )
            db.session.add(self.location)
            db.session.flush()
            self.plant = Plant(
                name='Phlox',
                location_id=self.location.id,
                creator_id=self.user.id,
                map_x=52.52,
                map_y=13.405,
            )
            db.session.add(self.plant)
            db.session.flush()
            db.session.add(GardenMap(filename='map.svg', calibration_points='[]', boundary_points='[]'))
            db.session.commit()
            self.user_id = self.user.id
            self.plant_id = self.plant.id

        with self.client.session_transaction() as session:
            session['user_id'] = self.user_id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)

    def test_plant_detail_reveals_position_save_button_via_hidden_class(self):
        response = self.client.get(f'/plants/{self.plant_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="plant-position-save" class="hidden"', html)
        self.assertIn("positionSaveButton.classList.remove('hidden')", html)
        self.assertIn("positionSaveButton.classList.add('hidden')", html)

    def test_plant_map_image_keeps_native_dimensions_for_overlay_alignment(self):
        response = self.client.get(f'/plants/{self.plant_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('#plant-map-image{position:absolute;left:0;top:0;display:block;max-width:none;', html)



if __name__ == '__main__':
    unittest.main()
