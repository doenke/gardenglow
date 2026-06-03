import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import Location, Plant, PlantDatabaseIdentifier, LightNeed, SoilProperty, TimelineEntry, User, db
from app.views import build_duplicate_plant_name


class PlantDuplicateViewTest(unittest.TestCase):
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
            self.sun = LightNeed.query.filter_by(key='full_sun').one()
            self.soil = SoilProperty(label='Humos')
            db.session.add(self.soil)
            db.session.flush()
            self.plant = Plant(
                name='Phlox',
                cultivar='Blue Paradise',
                scientific_name='Phlox paniculata',
                common_name='Flammenblume',
                source='Gärtnerei',
                bloom_start_month=7,
                bloom_end_month=9,
                flower_color='Blau',
                height_without_bloom_cm=40,
                height_with_bloom_cm=80,
                info='Duftet',
                map_x=12.5,
                map_y=75.5,
                location_id=self.location.id,
                creator_id=self.user.id,
            )
            self.plant.light_needs = [self.sun]
            self.plant.soil_properties = [self.soil]
            self.plant.database_identifiers = [PlantDatabaseIdentifier(catalog_key='gbif', taxonomy_id='12345')]
            db.session.add(self.plant)
            db.session.commit()
            self.user_id = self.user.id
            self.location_id = self.location.id
            self.plant_id = self.plant.id

        with self.client.session_transaction() as session:
            session['user_id'] = self.user_id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)

    def test_duplicate_plant_copies_masterdata_and_relationships(self):
        response = self.client.post(f'/plants/{self.plant_id}/duplicate')

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            duplicated = Plant.query.filter_by(name='Phlox (Kopie)').one()
            self.assertEqual(response.headers['Location'], f'/plants/{duplicated.id}')
            self.assertEqual(duplicated.location_id, self.location_id)
            self.assertEqual(duplicated.cultivar, 'Blue Paradise')
            self.assertEqual(duplicated.scientific_name, 'Phlox paniculata')
            self.assertEqual(duplicated.common_name, 'Flammenblume')
            self.assertEqual(duplicated.source, 'Gärtnerei')
            self.assertEqual(duplicated.bloom_start_month, 7)
            self.assertEqual(duplicated.bloom_end_month, 9)
            self.assertEqual(duplicated.flower_color, 'Blau')
            self.assertEqual(duplicated.height_without_bloom_cm, 40)
            self.assertEqual(duplicated.height_with_bloom_cm, 80)
            self.assertEqual(duplicated.info, 'Duftet')
            self.assertEqual(duplicated.map_x, 12.5)
            self.assertEqual(duplicated.map_y, 75.5)
            self.assertEqual([item.key for item in duplicated.light_needs], ['full_sun'])
            self.assertEqual([item.label for item in duplicated.soil_properties], ['Humos'])
            self.assertEqual([(item.catalog_key, item.taxonomy_id) for item in duplicated.database_identifiers], [('gbif', '12345')])
            event = TimelineEntry.query.filter_by(scope_type='plant', scope_id=duplicated.id).one()
            self.assertEqual(event.title, 'Pflanze dupliziert')

    def test_delete_plant_redirects_to_source_location(self):
        response = self.client.post(f'/plants/{self.plant_id}/delete')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], f'/locations/{self.location_id}')
        with self.app.app_context():
            trash = Location.query.filter_by(name='Papierkorb').one()
            plant = db.session.get(Plant, self.plant_id)
            self.assertEqual(plant.location_id, trash.id)
            event = TimelineEntry.query.filter_by(scope_type='plant', scope_id=self.plant_id, title='Ausgepflanzt').one()
            self.assertEqual(event.description, 'Pflanze wurde ausgepflanzt.')

    def test_build_duplicate_plant_name_increments_existing_copies(self):
        with self.app.app_context():
            db.session.add(Plant(name='Phlox (Kopie)', location_id=self.location_id, creator_id=self.user_id))
            db.session.add(Plant(name='Phlox (Kopie) 2', location_id=self.location_id, creator_id=self.user_id))
            db.session.commit()

            self.assertEqual(build_duplicate_plant_name('Phlox'), 'Phlox (Kopie) 3')


if __name__ == '__main__':
    unittest.main()
