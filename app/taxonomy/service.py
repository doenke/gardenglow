from dataclasses import dataclass, field
from typing import Mapping

from . import registry
from . import resolvers  # noqa: F401 - import triggers static resolver registration
from .resolvers.base import ResolverResult
from .resolvers.http import full_debug_enabled, get_full_debug_external_requests


@dataclass(frozen=True)
class TaxonomySuggestion:
    scientific_name: str
    matches: Mapping[str, str] = field(default_factory=dict)
    unavailable_catalogs: list[str] = field(default_factory=list)
    resolver_results: list[ResolverResult] = field(default_factory=list)

    @property
    def confidence(self):
        return 0.9 if self.matches else 0.0

    @property
    def note(self):
        return 'IDs werden katalogspezifisch ermittelt. Ohne Resolver gibt es keinen Vorschlag.'

    @property
    def external_calls(self):
        return [call for result in self.resolver_results for call in result.external_calls]

    def to_response(self, *, trace_id, duration_ms):
        debug = {
            'trace_id': trace_id,
            'duration_ms': duration_ms,
            'external_calls': [call.to_dict() for call in self.external_calls],
        }
        if full_debug_enabled():
            debug['full_debug_enabled'] = True
            debug['external_web_requests'] = get_full_debug_external_requests()

        return {
            'ok': True,
            'scientific_name': self.scientific_name,
            'matches': dict(self.matches),
            'unavailable_catalogs': list(self.unavailable_catalogs),
            'confidence': self.confidence,
            'note': self.note,
            'debug': debug,
        }


def resolve_for_catalog(catalog, scientific_name):
    taxonomy_resolver = registry.get_resolver_for_catalog(catalog)
    if not taxonomy_resolver:
        return ResolverResult(taxonomy_id=None, error='unavailable')

    resolver_config = taxonomy_resolver.build_config(catalog)
    return taxonomy_resolver.resolve(scientific_name, resolver_config)


def suggest_for_catalog(scientific_name, catalog):
    return suggest_for_all_enabled(scientific_name, [catalog])


def suggest_for_all_enabled(scientific_name, catalogs):
    matches: dict[str, str] = {}
    unavailable: list[str] = []
    resolver_results: list[ResolverResult] = []

    for catalog in catalogs:
        result = resolve_for_catalog(catalog, scientific_name)
        resolver_results.append(result)
        if result.unavailable:
            unavailable.append(catalog.key)
            continue
        if result.taxonomy_id:
            matches[catalog.key] = result.taxonomy_id

    return TaxonomySuggestion(
        scientific_name=scientific_name,
        matches=matches,
        unavailable_catalogs=unavailable,
        resolver_results=resolver_results,
    )
