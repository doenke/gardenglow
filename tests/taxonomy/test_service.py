import unittest
from unittest.mock import patch

from flask import Flask, g

from app.taxonomy.catalogs import DatabaseCatalogConfig
from app.taxonomy import registry
from app.taxonomy import service as taxonomy_service
from app.taxonomy.resolvers.base import ExternalCall, ResolverRequest, ResolverResult, TaxonomyResolver


class StubResolver(TaxonomyResolver):
    def __init__(self, key, taxonomy_id):
        self.key = key
        self.taxonomy_id = taxonomy_id
        self.mode = f'{key}_stub'

    def build_config(self, catalog):
        return {'catalog_key': catalog.key, 'mode': self.mode}

    def external_call(self, request):
        return ExternalCall(
            catalog=request.catalog_key,
            url=f'https://example.test/{request.catalog_key}',
            query={'q': request.scientific_name},
        )

    def resolve(self, scientific_name, config):
        request = ResolverRequest(config['catalog_key'], scientific_name, config)
        call = self.external_call(request)
        return ResolverResult(taxonomy_id=self.taxonomy_id, external_calls=[call])


class TaxonomyServiceTest(unittest.TestCase):

    def test_to_response_includes_captured_web_requests_only_when_full_debug_enabled(self):
        suggestion = taxonomy_service.TaxonomySuggestion(scientific_name='Phlox paniculata', matches={'gbif': '12345'})
        app = Flask(__name__)
        app.config['DEBUG_MODE'] = True

        with app.app_context():
            g.taxonomy_full_debug_external_requests = [
                {
                    'catalog': 'gbif',
                    'request_url': 'https://example.test/api?q=Phlox',
                    'response': {'status_code': 200, 'content': '{"usageKey":12345}'},
                }
            ]
            payload = suggestion.to_response(trace_id='trace-1', duration_ms=12.3)

        self.assertTrue(payload['debug']['full_debug_enabled'])
        self.assertEqual(
            payload['debug']['external_web_requests'][0]['response']['content'],
            '{"usageKey":12345}',
        )

    def test_suggest_for_all_enabled_aggregates_multiple_enabled_database_catalogs(self):
        catalogs = [
            DatabaseCatalogConfig(
                key='gbif',
                label='GBIF',
                enabled=True,
                record_url_template='https://gbif.example/{id}',
            ),
            DatabaseCatalogConfig(
                key='wfo',
                label='WFO',
                enabled=True,
                record_url_template='https://wfo.example/{id}',
            ),
        ]
        resolvers_by_key = {
            'gbif': StubResolver('gbif', '12345'),
            'wfo': StubResolver('wfo', 'wfo-4000029286'),
        }

        with patch.object(
            registry,
            'get_resolver_for_catalog',
            side_effect=lambda catalog: resolvers_by_key[catalog.key],
        ):
            suggestion = taxonomy_service.suggest_for_all_enabled('Phlox paniculata', catalogs)

        self.assertEqual(suggestion.matches, {'gbif': '12345', 'wfo': 'wfo-4000029286'})
        self.assertEqual(suggestion.unavailable_catalogs, [])
        self.assertEqual([call.catalog for call in suggestion.external_calls], ['gbif', 'wfo'])

    def test_suggest_for_catalog_resolves_only_one_catalog(self):
        catalog = DatabaseCatalogConfig(
            key='gbif',
            label='GBIF',
            enabled=True,
            record_url_template='https://gbif.example/{id}',
        )

        with patch.object(
            registry,
            'get_resolver_for_catalog',
            side_effect=lambda catalog: StubResolver(catalog.key, '12345'),
        ) as get_resolver_for_catalog:
            suggestion = taxonomy_service.suggest_for_catalog('Phlox paniculata', catalog)

        self.assertEqual(suggestion.matches, {'gbif': '12345'})
        self.assertEqual([call.catalog for call in suggestion.external_calls], ['gbif'])
        get_resolver_for_catalog.assert_called_once_with(catalog)

    def test_suggest_for_all_enabled_marks_catalogs_without_resolver_as_unavailable(self):
        catalog = DatabaseCatalogConfig(
            key='unknown_catalog',
            label='Unknown Catalog',
            enabled=True,
            record_url_template='https://unknown.example/{id}',
        )

        with patch.object(registry, 'get_resolver_for_catalog', return_value=None):
            suggestion = taxonomy_service.suggest_for_all_enabled('Phlox paniculata', [catalog])

        self.assertEqual(suggestion.matches, {})
        self.assertEqual(suggestion.unavailable_catalogs, ['unknown_catalog'])
        self.assertEqual(len(suggestion.resolver_results), 1)
        self.assertTrue(suggestion.resolver_results[0].unavailable)


if __name__ == '__main__':
    unittest.main()
