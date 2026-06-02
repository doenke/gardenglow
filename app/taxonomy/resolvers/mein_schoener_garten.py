import re

from .base import (
    ExternalCall,
    fetch_json,
    normalize_scientific_name_for_lookup,
    normalize_url_slug,
)
from .html_search import HtmlSearchResolver, search_page_taxonomy_id

NEXT_DATA_SEARCH_URL = "https://www.mein-schoener-garten.de/_next/data/prod/suche.json"


def normalize_mein_schoener_garten_slug(raw_slug):
    return normalize_url_slug(raw_slug, allow_path=True)


def _normalized_name(value):
    normalized = normalize_scientific_name_for_lookup(value) or value or ""
    return re.sub(r"\s+", " ", normalized).strip().lower()


def _slug_from_plant_url(url):
    match = re.search(
        r'(?:https?://(?:www\.)?mein-schoener-garten\.de)?/pflanzen/([^"\'\s?#]+)',
        url or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return normalize_mein_schoener_garten_slug(match.group(1))


def _iter_node_plants(value):
    if isinstance(value, dict):
        if value.get("__typename") == "NodePlant" and value.get("url"):
            yield value
        for child in value.values():
            yield from _iter_node_plants(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_node_plants(child)


def _next_data_page_content(payload):
    if not isinstance(payload, dict):
        return None
    page_props = payload.get("pageProps")
    if not isinstance(page_props, dict):
        return None
    data = page_props.get("data")
    if not isinstance(data, dict):
        return None
    page = data.get("page")
    if not isinstance(page, dict):
        return None
    return page.get("content")


def extract_next_data_taxonomy_id(payload, scientific_name):
    requested_name = _normalized_name(scientific_name)
    first_slug = None
    content = _next_data_page_content(payload)

    for plant in _iter_node_plants(content):
        slug = _slug_from_plant_url(plant.get("url"))
        if not slug:
            continue
        if first_slug is None:
            first_slug = slug

        biological_name = _normalized_name(plant.get("biologicalName"))
        if requested_name and biological_name == requested_name:
            return slug

    return first_slug


class MeinSchoenerGartenResolver(HtmlSearchResolver):
    key = "mein_schoener_garten"
    mode = "mein_schoener_garten_search"
    default_config_values = {
        "mode": "mein_schoener_garten_search",
        "next_data_search_url": NEXT_DATA_SEARCH_URL,
        "search_url": "https://www.mein-schoener-garten.de/suche",
        "query_param": "search_api_fulltext",
        "facet_filter": "74355",
        "text_param": "text",
        "slug": "suche",
    }

    patterns = [
        r'https?://(?:www\.)?mein-schoener-garten\.de/pflanzen/([^"\'\s\?#]+)',
        r'/pflanzen/([^"\'\s\?#]+)',
        r"\/pflanzen\/([^\"\s\?#]+)",
        r"%2Fpflanzen%2F([^\s\?#]+)",
    ]

    def _next_data_call(self, request):
        search_url = (
            request.config.get("next_data_search_url") or NEXT_DATA_SEARCH_URL
        ).strip()
        query_param = (
            request.config.get("query_param") or "search_api_fulltext"
        ).strip()
        text_param = (request.config.get("text_param") or "text").strip()
        query = {
            query_param: request.scientific_name,
            text_param: request.scientific_name,
            "facetFilter": request.config.get("facet_filter") or "74355",
            "slug": request.config.get("slug") or "suche",
        }
        return ExternalCall(
            catalog=request.catalog_key,
            url=search_url,
            query=query,
            allow_insecure_tls_fallback=bool(
                request.config.get("allow_insecure_tls_fallback")
            ),
        )

    def external_call(self, request):
        return self._next_data_call(request)

    def suggest_id(self, request):
        payload = fetch_json(self._next_data_call(request))
        if payload:
            taxonomy_id = extract_next_data_taxonomy_id(
                payload, request.scientific_name
            )
            if taxonomy_id:
                return taxonomy_id

        raw_slug = search_page_taxonomy_id(
            request.scientific_name, request.config, self.patterns
        )
        if not raw_slug:
            return None
        return normalize_mein_schoener_garten_slug(raw_slug)


def mein_schoener_garten_taxonomy_id(scientific_name, config):
    from .base import ResolverRequest

    return MeinSchoenerGartenResolver().suggest_id(
        ResolverRequest("mein_schoener_garten", scientific_name, config)
    )
