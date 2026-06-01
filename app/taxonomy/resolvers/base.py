import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional
from urllib.parse import unquote, urlencode

from ..url_templates import config_from_search_url_template


@dataclass
class ExternalCall:
    catalog: str
    url: str | None
    query: dict[str, str] = field(default_factory=dict)
    request_url: str | None = None
    full_debug: dict[str, Any] | None = None

    def __post_init__(self):
        self.query = {str(key): str(value) for key, value in dict(self.query or {}).items()}
        if self.request_url is None and self.url:
            self.request_url = f"{self.url}?{urlencode(self.query)}" if self.query else self.url

    def to_dict(self):
        payload = {
            'catalog': self.catalog,
            'url': self.url,
            'query': dict(self.query or {}),
            'request_url': self.request_url,
        }
        if self.full_debug is not None:
            payload['full_debug'] = self.full_debug
        return payload


@dataclass(frozen=True)
class ResolverRequest:
    catalog_key: str
    scientific_name: str
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ResolverResult:
    taxonomy_id: str | None
    confidence: float | None = None
    external_calls: list[ExternalCall] = field(default_factory=list)
    error: str | None = None

    @property
    def external_call(self) -> ExternalCall | None:
        return self.external_calls[0] if self.external_calls else None

    @property
    def unavailable(self) -> bool:
        return self.error == 'unavailable'


class TaxonomyResolver:
    key: str
    mode: str = None
    default_config_values: Mapping[str, Any] = {}

    def supports(self, catalog) -> bool:
        return getattr(catalog, 'key', None) == self.key

    def default_config(self) -> dict:
        defaults = deepcopy(self.default_config_values)
        defaults.setdefault('mode', self.mode or self.key)
        return defaults

    def build_config(self, catalog) -> dict:
        """Build resolver config from resolver defaults and catalog metadata.

        Common config keys shared by the current resolver implementations:

        * ``catalog_key``: catalog identifier used for logging/debug calls.
        * ``mode``: resolver mode used to select the lookup strategy.
        * ``search_url_template``: optional user-facing catalog search URL from
          the catalog configuration.
        * ``search_url``: optional request URL derived from the search template
          or resolver defaults, mainly used by HTML search resolvers.
        * ``query_param``: optional query parameter name for ``search_url``;
          HTML search resolvers default to ``q`` when it is absent.

        API resolvers should continue to validate and default their own
        catalog-specific settings locally instead of relying on a larger shared
        type hierarchy.
        """
        config = config_from_search_url_template(
            getattr(catalog, 'search_url_template', None),
            self.default_config(),
        )
        config['catalog_key'] = catalog.key
        return config

    def debug_call(self, scientific_name: str, config: dict) -> Optional[ExternalCall]:
        return self.external_call(ResolverRequest(config.get('catalog_key') or self.key, scientific_name, config))

    def resolve(self, scientific_name: str, config: dict) -> ResolverResult:
        catalog_key = config.get('catalog_key') or self.key
        request = ResolverRequest(catalog_key, scientific_name, config)
        call = self.external_call(request)
        return ResolverResult(
            taxonomy_id=self.suggest_id(request),
            external_calls=[call] if call else [],
        )

    def suggest_id(self, request: ResolverRequest) -> Optional[str]:
        raise NotImplementedError

    def external_call(self, request: ResolverRequest) -> Optional[ExternalCall]:
        return None


def fetch_response(call: ExternalCall, accept: str):
    from .http import fetch_response as _fetch_response

    return _fetch_response(call, accept)


def validate_common_config(config, required=()):
    """Return ``True`` when all requested common resolver config keys are set.

    The helper intentionally stays small while the resolver set is small: each
    resolver can opt in to the keys it needs and keep catalog-specific defaults
    or validation close to its own implementation.
    """
    config = config or {}
    for key in required or ():
        value = config.get(key)
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
    return True


def parse_json_response(response, logger=None):
    from .http import parse_json_response as _parse_json_response

    return _parse_json_response(response, logger)


def fetch_json(call: ExternalCall, accept: str = 'application/json'):
    from .http import fetch_json as _fetch_json

    return _fetch_json(call, accept)


def fetch_text(call: ExternalCall, accept: str = 'text/html,application/xhtml+xml'):
    from .http import fetch_text as _fetch_text

    return _fetch_text(call, accept)


def normalize_url_slug(raw_slug, allow_path=False):
    slug = unquote(raw_slug or '')
    slug = re.split(r'[?#]', slug, maxsplit=1)[0]
    if allow_path:
        slug = slug.replace('\\', '/')
    slug = slug.strip().strip('/').lower()

    segments = slug.split('/') if allow_path else [slug]
    normalized_segments = []
    for segment in segments:
        normalized_segment = re.sub(r'[^a-z0-9\-]+', '-', segment)
        normalized_segment = re.sub(r'-{2,}', '-', normalized_segment).strip('-')
        if normalized_segment:
            normalized_segments.append(normalized_segment)

    separator = '/' if allow_path else '-'
    return separator.join(normalized_segments) or None


def normalize_single_segment_slug(raw_slug):
    return normalize_url_slug(raw_slug, allow_path=False)


def normalize_scientific_name_for_lookup(scientific_name):
    value = re.sub(r'\s+', ' ', (scientific_name or '').strip())
    if not value:
        return None

    # remove cultivar designations and marketing names in quotes
    value = re.sub(r'"[^"]+"', '', value)
    value = re.sub(r"'[^']+'", '', value)
    value = re.sub(r'\s+', ' ', value).strip(' ,;:-')

    tokens = value.split()
    if len(tokens) < 2:
        return value or None

    def _is_species_token(token):
        return bool(re.fullmatch(r'[a-z][a-z\-]*', token))

    selected = [tokens[0]]
    for token in tokens[1:]:
        cleaned = token.strip(' ,;()')
        if not cleaned:
            continue
        if cleaned.lower() in {'x', '×'} or _is_species_token(cleaned):
            selected.append(cleaned)
            continue
        break

    if len(selected) < 2:
        return ' '.join(tokens[:2])
    return ' '.join(selected)
