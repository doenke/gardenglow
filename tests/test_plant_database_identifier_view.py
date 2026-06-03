import os
import tempfile
import unittest

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import Location, Plant, PlantDatabaseIdentifier, TimelineEntry, User, db


class PlantDatabaseIdentifierViewTest(unittest.TestCase):
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
            db.session.flush()
            self.plant = Plant(name='Brunnera', location_id=self.location.id, creator_id=self.user.id)
            self.plant.database_identifiers = [
                PlantDatabaseIdentifier(
                    catalog_key='wikipedia_de',
                    taxonomy_id='Gro%C3%9Fbl%C3%A4ttriges_Kaukasusvergissmeinnicht',
                ),
            ]
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

    def test_wikipedia_identifier_is_decoded_for_display(self):
        response = self.client.get(f'/plants/{self.plant_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('value="Großblättriges_Kaukasusvergissmeinnicht"', html)
        self.assertIn('title="Wikipedia (Großblättriges_Kaukasusvergissmeinnicht)"', html)
        self.assertNotIn('Gro%C3%9Fbl%C3%A4ttriges_Kaukasusvergissmeinnicht)', html)

    def test_wikipedia_identifier_is_saved_as_readable_slug(self):
        response = self.client.post(
            f'/plants/{self.plant_id}/masterdata',
            data={
                'name': 'Brunnera',
                'location_id': str(self.location_id),
                'database_id_wikipedia_de': 'Gro%C3%9Fbl%C3%A4ttriges Kaukasusvergissmeinnicht',
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            identifier = PlantDatabaseIdentifier.query.filter_by(
                plant_id=self.plant_id,
                catalog_key='wikipedia_de',
            ).one()
            self.assertEqual(identifier.taxonomy_id, 'Großblättriges_Kaukasusvergissmeinnicht')

    def test_database_identifier_update_does_not_create_timeline_entry(self):
        response = self.client.post(
            f'/plants/{self.plant_id}/masterdata',
            data={
                'name': 'Brunnera',
                'location_id': str(self.location_id),
                'database_id_wikipedia_de': 'Brunnera_macrophylla',
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            identifier = PlantDatabaseIdentifier.query.filter_by(
                plant_id=self.plant_id,
                catalog_key='wikipedia_de',
            ).one()
            self.assertEqual(identifier.taxonomy_id, 'Brunnera_macrophylla')
            timeline_entries = TimelineEntry.query.filter_by(scope_type='plant', scope_id=self.plant_id).all()
            self.assertEqual(timeline_entries, [])

    def test_database_links_follow_catalog_display_order(self):
        with self.app.app_context():
            PlantDatabaseIdentifier.query.filter_by(plant_id=self.plant_id).delete()
            plant = db.session.get(Plant, self.plant_id)
            plant.database_identifiers = [
                PlantDatabaseIdentifier(catalog_key='floraweb', taxonomy_id='6666'),
                PlantDatabaseIdentifier(catalog_key='gbif', taxonomy_id='1234'),
                PlantDatabaseIdentifier(catalog_key='naturadb', taxonomy_id='brunnera-macrophylla'),
                PlantDatabaseIdentifier(catalog_key='mein_schoener_garten', taxonomy_id='kaukasusvergissmeinnicht'),
                PlantDatabaseIdentifier(catalog_key='wikipedia_de', taxonomy_id='Brunnera_macrophylla'),
            ]
            db.session.commit()

        response = self.client.get(f'/plants/{self.plant_id}')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        wikipedia_index = html.index('title="Wikipedia (Brunnera_macrophylla)"')
        mein_schoener_garten_index = html.index('title="Mein schöner Garten (kaukasusvergissmeinnicht)"')
        naturadb_index = html.index('title="NaturaDB (brunnera-macrophylla)"')
        floraweb_index = html.index('title="FloraWeb (6666)"')
        gbif_index = html.index('title="GBIF (1234)"')

        self.assertLess(wikipedia_index, mein_schoener_garten_index)
        self.assertLess(mein_schoener_garten_index, naturadb_index)
        self.assertLess(naturadb_index, floraweb_index)
        self.assertLess(floraweb_index, gbif_index)


if __name__ == '__main__':
    unittest.main()
