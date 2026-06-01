import unittest
from unittest.mock import patch

from app.views import _naturadb_common_name_from_slug, _strip_edge_special_characters


class CommonNameLookupTest(unittest.TestCase):
    def test_strip_edge_special_characters_keeps_inner_punctuation_and_umlauts(self):
        self.assertEqual(
            _strip_edge_special_characters('  „Ährige-Teufelskralle (blau) Sorte!“  '),
            'Ährige-Teufelskralle (blau) Sorte',
        )

    def test_naturadb_common_name_strips_edge_special_characters(self):
        class FakeResponse:
            text = '<html><title>» Roter Sonnenhut (Echinacea purpurea) « - NaturaDB</title></html>'

            def raise_for_status(self):
                return None

        with patch('app.views.requests.get', return_value=FakeResponse()) as get:
            common_name, sources = _naturadb_common_name_from_slug('echinacea-purpurea')

        self.assertEqual(common_name, 'Roter Sonnenhut')
        self.assertEqual(sources, ['https://www.naturadb.de/pflanzen/echinacea-purpurea'])
        get.assert_called_once_with(
            'https://www.naturadb.de/pflanzen/echinacea-purpurea',
            params={},
            headers={'User-Agent': 'garten-taxonomy-resolver/1.0'},
            timeout=6,
        )


if __name__ == '__main__':
    unittest.main()
