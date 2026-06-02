from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DatabaseCatalogConfig:
    key: str
    label: str
    record_url_template: str
    search_url_template: str | None = None
    icon_url: str | None = None
    enabled: bool = True


_DEFAULT_DATABASE_CATALOGS = (
    DatabaseCatalogConfig(
        key='wfo',
        label='WFO',
        record_url_template='https://www.worldfloraonline.org/taxon/{id}',
        search_url_template='https://www.worldfloraonline.org/search?query={q}',
        icon_url='https://www.worldfloraonline.org/css/images/svgs/favicon.svg',
    ),
    DatabaseCatalogConfig(
        key='powo_ipni',
        label='POWO/IPNI-LSID',
        record_url_template='https://powo.science.kew.org/taxon/{id}',
        search_url_template='https://powo.science.kew.org/results?q={q}',
        icon_url='https://powo.science.kew.org/img/powo-favicon.ico',
    ),
    DatabaseCatalogConfig(
        key='gbif',
        label='GBIF',
        record_url_template='https://www.gbif.org/species/{id}',
        search_url_template='https://www.gbif.org/species/search?q={q}',
        icon_url='https://www.gbif.org/favicon.ico',
    ),
    DatabaseCatalogConfig(
        key='floraweb',
        label='FloraWeb',
        record_url_template='https://www.floraweb.de/taxon/{id}',
        search_url_template='https://www.floraweb.de/php/taxoquery.php?taxname={q}',
        icon_url='https://www.floraweb.de/favicon.ico',
    ),
    DatabaseCatalogConfig(
        key='naturadb',
        label='NaturaDB',
        record_url_template='https://www.naturadb.de/pflanzen/{id}',
        search_url_template='https://www.naturadb.de/suche?q={q}',
        icon_url='https://www.naturadb.de/favicon.ico',
    ),
    DatabaseCatalogConfig(
        key='wikipedia_de',
        label='Deutsche Wikipedia',
        record_url_template='https://de.wikipedia.org/wiki/{id}',
        search_url_template='https://de.wikipedia.org/w/index.php?search={q}',
        icon_url='https://de.wikipedia.org/favicon.ico',
    ),
    DatabaseCatalogConfig(
        key='mein_schoener_garten',
        label='Mein schöner Garten',
        record_url_template='https://www.mein-schoener-garten.de/pflanzen/{id}',
        search_url_template='https://www.mein-schoener-garten.de/suche?search_api_fulltext={q}',
        icon_url='https://www.mein-schoener-garten.de/favicon.ico',
    ),
)


def iter_database_catalogs() -> Iterable[DatabaseCatalogConfig]:
    return iter(_DEFAULT_DATABASE_CATALOGS)


def get_database_catalogs() -> list[DatabaseCatalogConfig]:
    return list(_DEFAULT_DATABASE_CATALOGS)


def get_database_catalog_by_key(catalog_key: str | None) -> DatabaseCatalogConfig | None:
    for catalog in _DEFAULT_DATABASE_CATALOGS:
        if catalog.key == catalog_key:
            return catalog
    return None
