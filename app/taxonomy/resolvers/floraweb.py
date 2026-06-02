from .html_search import HtmlSearchResolver


class FlorawebResolver(HtmlSearchResolver):
    key = 'floraweb'
    mode = 'floraweb_search'
    default_config_values = {
        'mode': 'floraweb_search',
        'search_url': 'https://www.floraweb.de/php/taxoquery.php',
        'query_param': 'taxname',
    }

    patterns = [
        r'[?&]name-use-id=([0-9]+)',
        r'[?&]taxon-id=([0-9]+)',
        r'/taxon/([A-Za-z0-9\-]+)',
        r'/pflanze/([A-Za-z0-9\-]+)',
        r'[?&]taxnr=([0-9]+)',
        r'/taxonomiedetail[s]?/[A-Za-z0-9\-]*([0-9]{3,})',
    ]


def floraweb_taxonomy_id(scientific_name, config):
    from .base import ResolverRequest

    return FlorawebResolver().suggest_id(ResolverRequest('floraweb', scientific_name, config))
