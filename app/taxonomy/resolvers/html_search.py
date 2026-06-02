import html
import re
from urllib.parse import unquote

from .base import ExternalCall, ResolverRequest, TaxonomyResolver, validate_common_config
from .http import fetch_text


def html_decode_candidates(page_html):
    candidates = []

    def _append_candidate(candidate):
        if candidate is None:
            return
        candidate = str(candidate)
        if candidate not in candidates:
            candidates.append(candidate)

    _append_candidate(page_html or '')
    for candidate in list(candidates):
        _append_candidate(html.unescape(candidate))
    for candidate in list(candidates):
        _append_candidate(unquote(candidate))
    for candidate in list(candidates):
        _append_candidate(candidate.replace('\\/', '/').replace('\\"', '"').replace("\\'", "'"))
    for candidate in list(candidates):
        _append_candidate(html.unescape(candidate))
        _append_candidate(unquote(candidate))
    return candidates


def extract_search_page_taxonomy_id(page_html, patterns):
    for candidate_html in html_decode_candidates(page_html):
        first_match = None
        for pattern in patterns:
            for match in re.finditer(pattern, candidate_html, flags=re.IGNORECASE):
                if first_match is None or match.start() < first_match.start():
                    first_match = match
                break
        if not first_match:
            continue
        taxonomy_id = (first_match.group(1) or '').strip().strip('/').strip()
        if taxonomy_id:
            return taxonomy_id
    return None


def search_page_taxonomy_id(scientific_name, config, patterns):
    page_html = search_page_html(scientific_name, config)
    if page_html is None:
        return None
    return extract_search_page_taxonomy_id(page_html, patterns)


def search_page_html(scientific_name, config):
    if not validate_common_config(config, required=('search_url',)):
        return None
    search_url = config.get('search_url').strip()
    query_param = (config.get('query_param') or 'q').strip()
    call = ExternalCall(
        catalog=config.get('catalog_key') or config.get('mode') or 'html_search',
        url=search_url,
        query={query_param: scientific_name},
        allow_insecure_tls_fallback=bool(config.get('allow_insecure_tls_fallback')),
    )
    return fetch_text(call)


class HtmlSearchResolver(TaxonomyResolver):
    key = None
    mode = None
    patterns = []
    required_config_keys = ('search_url',)

    def has_required_config(self, config):
        return validate_common_config(config, required=self.required_config_keys)

    def external_call(self, request: ResolverRequest):
        if not self.has_required_config(request.config):
            return None
        query_param = request.config.get('query_param') or 'q'
        return ExternalCall(
            catalog=request.catalog_key,
            url=request.config.get('search_url'),
            query={query_param: request.scientific_name},
            allow_insecure_tls_fallback=bool(request.config.get('allow_insecure_tls_fallback')),
        )

    def suggest_id(self, request: ResolverRequest):
        if not self.has_required_config(request.config):
            return None
        return search_page_taxonomy_id(request.scientific_name, request.config, self.patterns)
