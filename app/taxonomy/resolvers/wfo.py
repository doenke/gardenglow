import re

from .base import ExternalCall, ResolverRequest, validate_common_config
from .html_search import HtmlSearchResolver, html_decode_candidates, search_page_html
from .http import fetch_json


WFO_ID_PATTERN = r'wfo-[A-Za-z0-9\-]+'
WFO_TAXON_PATTERNS = [
    rf'/taxon/({WFO_ID_PATTERN})',
    rf'worldfloraonline\.org/taxon/({WFO_ID_PATTERN})',
    rf'\\/taxon\\/({WFO_ID_PATTERN})',
    rf'worldfloraonline\\.org\\/taxon\\/({WFO_ID_PATTERN})',
    rf'%2[fF]taxon%2[fF]({WFO_ID_PATTERN})',
]

WFO_RESULT_TAXON_PATTERNS = [
    rf'<a\b(?=[^>]*\bclass=["\\\']?[^>"\\\']*\bresult\b)(?=[^>]*\bhref=["\\\']?(?:https?://(?:www\.)?worldfloraonline\.org)?/taxon/({WFO_ID_PATTERN}))[^>]*>',
    rf'<a\b(?=[^>]*\bhref=["\\\']?(?:https?://(?:www\.)?worldfloraonline\.org)?/taxon/({WFO_ID_PATTERN}))(?=[^>]*\bclass=["\\\']?[^>"\\\']*\bresult\b)[^>]*>',
]


def first_wfo_pattern_match(candidate_html, patterns):
    first_match = None
    for pattern in patterns:
        for match in re.finditer(pattern, candidate_html, flags=re.IGNORECASE):
            if first_match is None or match.start() < first_match.start():
                first_match = match
            break
    return first_match.group(1) if first_match else None


def extract_wfo_taxon_slug(page_html):
    candidates = html_decode_candidates(page_html)
    for candidate_html in candidates:
        result_slug = first_wfo_pattern_match(candidate_html, WFO_RESULT_TAXON_PATTERNS)
        if result_slug:
            return result_slug
    for candidate_html in candidates:
        fallback_slug = first_wfo_pattern_match(candidate_html, WFO_TAXON_PATTERNS)
        if fallback_slug:
            return fallback_slug
    return None


def _wfo_id_from_name_entry(entry):
    if not isinstance(entry, dict):
        return None
    for key in ('wfo_id', 'wfoId', 'id'):
        value = (entry.get(key) or '').strip()
        if re.fullmatch(WFO_ID_PATTERN, value, flags=re.IGNORECASE):
            return value
    return None


def extract_wfo_match_api_id(payload):
    if not isinstance(payload, dict):
        return None

    match_id = _wfo_id_from_name_entry(payload.get('match'))
    if match_id:
        return match_id

    candidates = payload.get('candidates')
    if not isinstance(candidates, list):
        return None

    candidate_ids = []
    for candidate in candidates:
        candidate_id = _wfo_id_from_name_entry(candidate)
        if candidate_id and candidate_id not in candidate_ids:
            candidate_ids.append(candidate_id)

    return candidate_ids[0] if len(candidate_ids) == 1 else None


class WfoResolver(HtmlSearchResolver):
    key = 'wfo'
    mode = 'wfo_match'
    default_config_values = {
        'mode': 'wfo_match',
        'match_url': 'https://list.worldfloraonline.org/matching_rest.php',
        'input_string_param': 'input_string',
        'accept_single_candidate': True,
        'search_url': 'https://www.worldfloraonline.org/search',
        'query_param': 'query',
        'allow_insecure_tls_fallback': True,
    }
    required_config_keys = ('match_url',)

    def external_call(self, request: ResolverRequest):
        if not validate_common_config(request.config, required=('match_url',)):
            if not validate_common_config(request.config, required=('search_url',)):
                return None
            query_param = request.config.get('query_param') or 'query'
            return ExternalCall(
                catalog=request.catalog_key,
                url=request.config.get('search_url'),
                query={query_param: request.scientific_name},
                allow_insecure_tls_fallback=bool(request.config.get('allow_insecure_tls_fallback')),
            )
        input_param = (request.config.get('input_string_param') or 'input_string').strip()
        query = {input_param: request.scientific_name}
        if request.config.get('accept_single_candidate', True):
            query['accept_single_candidate'] = 'true'
        return ExternalCall(
            catalog=request.catalog_key,
            url=request.config.get('match_url'),
            query=query,
            allow_insecure_tls_fallback=bool(request.config.get('allow_insecure_tls_fallback')),
        )

    def suggest_id(self, request):
        call = self.external_call(request)
        if call:
            match_payload = fetch_json(call)
            match_id = extract_wfo_match_api_id(match_payload)
            if match_id:
                return match_id

        page_html = search_page_html(request.scientific_name, request.config)
        if page_html is None:
            return None
        return extract_wfo_taxon_slug(page_html)
