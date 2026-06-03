import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'x' * 40)

from app import create_app
from app.models import Location, Plant, User, db
from app.taxonomy.service import TaxonomySuggestion


class TaxonomySuggestViewTest(unittest.TestCase):
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
            )
            db.session.add(self.location)
            db.session.flush()
            self.plant = Plant(
                name='Phlox',
                scientific_name='Phlox paniculata',
                location_id=self.location.id,
                creator_id=self.user.id,
            )
            db.session.add(self.plant)
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


    def test_database_catalog_table_is_not_created(self):
        with self.app.app_context():
            table_names = set(db.inspect(db.engine).get_table_names())

        self.assertNotIn('database_catalog', table_names)

    def test_catalog_key_uses_single_enabled_catalog(self):
        captured = {}

        def fake_suggest(scientific_name, catalog):
            captured['scientific_name'] = scientific_name
            captured['catalog_key'] = catalog.key
            return TaxonomySuggestion(scientific_name=scientific_name, matches={catalog.key: '12345'})

        with patch('app.views.taxonomy_service.suggest_for_catalog', side_effect=fake_suggest) as suggest_for_catalog, \
             patch('app.views.taxonomy_service.suggest_for_all_enabled') as suggest_for_all_enabled:
            response = self.client.post(
                f'/plants/{self.plant_id}/taxonomy-ids-suggest',
                json={'scientific_name': 'Phlox paniculata', 'catalog_key': 'gbif'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['matches'], {'gbif': '12345'})
        self.assertEqual(captured, {'scientific_name': 'Phlox paniculata', 'catalog_key': 'gbif'})
        suggest_for_catalog.assert_called_once()
        suggest_for_all_enabled.assert_not_called()

    def test_missing_catalog_key_uses_all_enabled_catalogs(self):
        captured = {}

        def fake_suggest(scientific_name, catalogs):
            captured['scientific_name'] = scientific_name
            captured['catalog_keys'] = [catalog.key for catalog in catalogs]
            return TaxonomySuggestion(scientific_name=scientific_name, matches={'gbif': '12345', 'wfo': 'wfo-1'})

        with patch('app.views.taxonomy_service.suggest_for_all_enabled', side_effect=fake_suggest) as suggest_for_all_enabled, \
             patch('app.views.taxonomy_service.suggest_for_catalog') as suggest_for_catalog:
            response = self.client.post(
                f'/plants/{self.plant_id}/taxonomy-ids-suggest',
                json={'scientific_name': 'Phlox paniculata'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured, {
            'scientific_name': 'Phlox paniculata',
            'catalog_keys': ['wikipedia_de', 'mein_schoener_garten', 'naturadb', 'floraweb', 'gbif', 'wfo', 'powo_ipni'],
        })
        suggest_for_all_enabled.assert_called_once()
        suggest_for_catalog.assert_not_called()

    def test_unknown_catalog_key_returns_404(self):
        with patch('app.views.taxonomy_service.suggest_for_catalog') as suggest_for_catalog:
            response = self.client.post(
                f'/plants/{self.plant_id}/taxonomy-ids-suggest',
                json={'scientific_name': 'Phlox paniculata', 'catalog_key': 'missing'},
            )

        self.assertEqual(response.status_code, 404)
        self.assertIn('existiert nicht', response.get_json()['error'])
        suggest_for_catalog.assert_not_called()



if __name__ == '__main__':
    unittest.main()
