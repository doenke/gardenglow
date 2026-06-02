import unittest
from unittest.mock import patch

from app.taxonomy.catalogs import DatabaseCatalogConfig, get_database_catalog_by_key
from app.taxonomy import registry
from app.taxonomy import resolvers  # noqa: F401 - import triggers static resolver registration
from app.taxonomy.resolvers.base import ResolverRequest, validate_common_config
from app.taxonomy.resolvers.floraweb import FlorawebResolver
from app.taxonomy.resolvers.gbif import GbifResolver
from app.taxonomy.resolvers.mein_schoener_garten import (
    NEXT_DATA_SEARCH_URL,
    MeinSchoenerGartenResolver,
    extract_next_data_taxonomy_id,
)
from app.taxonomy.resolvers.naturadb import NaturaDbResolver
from app.taxonomy.resolvers.passthrough import SearchQueryPassthroughResolver
from app.taxonomy.resolvers.powo import PowoResolver
from app.taxonomy.resolvers.wfo import WfoResolver
from app.taxonomy.resolvers.wikipedia import GermanWikipediaResolver


class TaxonomyRegistryTest(unittest.TestCase):
    def test_known_catalog_keys_get_expected_resolver(self):
        expected_resolvers = {
            'gbif': GbifResolver,
            'powo_ipni': PowoResolver,
            'wfo': WfoResolver,
            'floraweb': FlorawebResolver,
            'naturadb': NaturaDbResolver,
            'mein_schoener_garten': MeinSchoenerGartenResolver,
            'wikipedia_de': GermanWikipediaResolver,
            'botanikus': SearchQueryPassthroughResolver,
        }

        for catalog_key, expected_resolver_class in expected_resolvers.items():
            with self.subTest(catalog_key=catalog_key):
                catalog = DatabaseCatalogConfig(
                    key=catalog_key,
                    label=catalog_key,
                    enabled=True,
                    record_url_template='https://example.test/{id}',
                )

                resolver = registry.get_resolver_for_catalog(catalog)

                self.assertIsInstance(resolver, expected_resolver_class)

    def test_default_floraweb_catalog_uses_taxonomie_record_url(self):
        catalog = get_database_catalog_by_key('floraweb')

        self.assertIsNotNone(catalog)
        self.assertEqual(
            catalog.record_url_template,
            'https://www.floraweb.de/php/taxonomie.php?taxon-id={id}',
        )


class TaxonomyResolverConfigTest(unittest.TestCase):
    def test_html_resolver_build_config_uses_catalog_search_template_query_param(self):
        catalog = DatabaseCatalogConfig(
            key='floraweb',
            label='FloraWeb',
            enabled=True,
            record_url_template='https://example.test/{id}',
            search_url_template='https://www.floraweb.de/php/taxoquery.php?taxname={q}',
        )

        config = FlorawebResolver().build_config(catalog)

        self.assertEqual(config['catalog_key'], 'floraweb')
        self.assertEqual(config['mode'], 'floraweb_search')
        self.assertEqual(config['search_url'], 'https://www.floraweb.de/php/taxoquery.php')
        self.assertEqual(config['query_param'], 'taxname')
        self.assertEqual(config['search_url_template'], catalog.search_url_template)

    def test_api_resolver_build_config_keeps_defaults_and_search_template(self):
        catalog = DatabaseCatalogConfig(
            key='gbif',
            label='GBIF',
            enabled=True,
            record_url_template='https://example.test/{id}',
            search_url_template='https://www.gbif.org/species/search?q={q}',
        )

        config = GbifResolver().build_config(catalog)

        self.assertEqual(config['catalog_key'], 'gbif')
        self.assertEqual(config['mode'], 'gbif_species_match')
        self.assertEqual(config['prefer_statuses'], {'ACCEPTED'})
        self.assertEqual(config['kingdom'], 'Plantae')
        self.assertEqual(config['search_url_template'], catalog.search_url_template)

    def test_wikipedia_resolver_returns_readable_unicode_slug(self):
        resolver = GermanWikipediaResolver()
        request = ResolverRequest('wikipedia_de', 'Brunnera macrophylla', resolver.default_config_values)

        with patch('app.taxonomy.resolvers.wikipedia.fetch_json', return_value={
            'query': {'search': [{'title': 'Großblättriges Kaukasusvergissmeinnicht'}]},
        }):
            suggestion = resolver.suggest_id(request)

        self.assertEqual(suggestion, 'Großblättriges_Kaukasusvergissmeinnicht')

    def test_floraweb_resolver_extracts_name_use_id_from_search_results(self):
        resolver = FlorawebResolver()
        request = ResolverRequest(
            'floraweb',
            'Brunnera macrophylla',
            {
                'catalog_key': 'floraweb',
                'mode': 'floraweb_search',
                'search_url': 'https://www.floraweb.de/php/taxoquery.php',
                'query_param': 'taxname',
            },
        )
        page_html = '''
            <h2>Trefferliste</h2>
            <p>Ihre Suche nach <strong>'Brunnera macrophylla'</strong> ergab 1 Treffer.</p>
            <a href="/php/artenhome.php?name-use-id=6666" title="zum Pflanzensteckbrief">
                <span class="taxname-main">Brunnera macrophylla (Adams) I. M. Johnst.</span>
            </a>
            <a class="atlas" href="/webkarten/karte.html?taxon-id=6666" title="direkt zur Atlaskarte"></a>
        '''

        with patch('app.taxonomy.resolvers.html_search.fetch_text', return_value=page_html):
            suggestion = resolver.suggest_id(request)

        self.assertEqual(suggestion, '6666')

    def test_mein_schoener_garten_resolver_uses_next_data_search_endpoint(self):
        catalog = DatabaseCatalogConfig(
            key='mein_schoener_garten',
            label='Mein schöner Garten',
            enabled=True,
            record_url_template='https://www.mein-schoener-garten.de/pflanzen/{id}',
            search_url_template='https://www.mein-schoener-garten.de/suche?search_api_fulltext={q}',
        )
        resolver = MeinSchoenerGartenResolver()
        config = resolver.build_config(catalog)

        call = resolver.debug_call('Brunnera macrophylla', config)

        self.assertEqual(call.url, NEXT_DATA_SEARCH_URL)
        self.assertEqual(call.query['search_api_fulltext'], 'Brunnera macrophylla')
        self.assertEqual(call.query['text'], 'Brunnera macrophylla')
        self.assertEqual(call.query['facetFilter'], '74355')
        self.assertEqual(call.query['slug'], 'suche')

    def test_mein_schoener_garten_resolver_extracts_nested_slug_from_next_data(self):
        resolver = MeinSchoenerGartenResolver()
        request = ResolverRequest(
            'mein_schoener_garten',
            'Brunnera macrophylla',
            resolver.default_config(),
        )
        payload = {
            'pageProps': {
                'data': {
                    'page': {
                        'content': [
                            {
                                'data': {
                                    'items': [
                                        {
                                            '__typename': 'NodePlant',
                                            'biologicalName': 'Brunnera macrophylla',
                                            'url': (
                                                '/pflanzen/kaukasusvergissmeinnicht/'
                                                'kaukasusvergissmeinnicht'
                                            ),
                                        },
                                    ],
                                },
                            },
                        ],
                    },
                },
            },
        }

        with patch('app.taxonomy.resolvers.mein_schoener_garten.fetch_json', return_value=payload):
            suggestion = resolver.suggest_id(request)

        self.assertEqual(suggestion, 'kaukasusvergissmeinnicht/kaukasusvergissmeinnicht')

    def test_mein_schoener_garten_next_data_extractor_prefers_matching_biological_name(self):
        payload = {
            'items': [
                {
                    '__typename': 'NodePlant',
                    'biologicalName': 'Phlox paniculata',
                    'url': '/pflanzen/phlox/flammenblume',
                },
                {
                    '__typename': 'NodePlant',
                    'biologicalName': 'Brunnera macrophylla',
                    'url': (
                        '/pflanzen/kaukasusvergissmeinnicht/'
                        'kaukasusvergissmeinnicht'
                    ),
                },
            ],
        }

        suggestion = extract_next_data_taxonomy_id(payload, 'Brunnera macrophylla')

        self.assertEqual(suggestion, 'kaukasusvergissmeinnicht/kaukasusvergissmeinnicht')

    def test_common_config_validation_checks_required_keys(self):
        self.assertTrue(validate_common_config({'search_url': 'https://example.test/search'}, required=('search_url',)))
        self.assertFalse(validate_common_config({'search_url': '   '}, required=('search_url',)))
        self.assertFalse(validate_common_config({}, required=('search_url',)))

    def test_html_resolver_requires_search_url_for_external_call(self):
        resolver = FlorawebResolver()
        request = ResolverRequest('floraweb', 'Bellis perennis', {'query_param': 'taxname'})

        self.assertIsNone(resolver.external_call(request))

    def test_wikipedia_resolver_external_call_uses_api_with_search_query(self):
        catalog = DatabaseCatalogConfig(
            key='wikipedia_de',
            label='Deutsche Wikipedia',
            enabled=True,
            record_url_template='https://de.wikipedia.org/wiki/{id}',
            search_url_template='https://de.wikipedia.org/w/index.php?search={q}',
        )
        resolver = GermanWikipediaResolver()
        config = resolver.build_config(catalog)

        call = resolver.debug_call('Phlox paniculata', config)

        self.assertEqual(call.url, 'https://de.wikipedia.org/w/api.php')
        self.assertEqual(call.query['srsearch'], 'Phlox paniculata')
        self.assertEqual(call.query['list'], 'search')


if __name__ == '__main__':
    unittest.main()
