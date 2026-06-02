"""HTTP execution helpers for taxonomy resolver external calls."""

import os
import time

from flask import current_app, g, has_app_context
import requests

from .base import ExternalCall


USER_AGENT = 'garten-taxonomy-resolver/1.0'
REQUEST_TIMEOUT = 8
FULL_DEBUG_ENV_VAR = 'DEBUG_MODE'
_TRUE_VALUES = {'1', 'true', 'yes', 'on', 'y'}


def _env_flag(name, default='false'):
    return (os.getenv(name, default) or '').strip().lower() in _TRUE_VALUES


def full_debug_enabled():
    """Return whether full external-request debugging is enabled."""
    if has_app_context():
        return bool(current_app.config.get('DEBUG_MODE'))
    return _env_flag(FULL_DEBUG_ENV_VAR)


def get_full_debug_external_requests():
    """Return captured detailed external web requests for this Flask request."""
    if not has_app_context():
        return []
    return list(getattr(g, 'taxonomy_full_debug_external_requests', []))


def _append_full_debug_entry(entry):
    if not has_app_context():
        return
    if not hasattr(g, 'taxonomy_full_debug_external_requests'):
        g.taxonomy_full_debug_external_requests = []
    g.taxonomy_full_debug_external_requests.append(entry)


def _response_text(response):
    try:
        return response.text or ''
    except (LookupError, UnicodeError):
        return response.content.decode('utf-8', errors='replace') if response.content else ''


def _response_debug_payload(response):
    return {
        'status_code': response.status_code,
        'url': response.url,
        'headers': dict(response.headers),
        'content': _response_text(response),
    }


def _record_full_debug(call, *, headers, timeout, duration_ms, response=None, error=None, verify=True):
    if not full_debug_enabled():
        return

    entry = {
        'catalog': call.catalog,
        'url': call.url,
        'query': dict(call.query or {}),
        'request_url': call.request_url,
        'headers': dict(headers or {}),
        'timeout': timeout,
        'duration_ms': duration_ms,
        'verify_tls': verify,
    }
    if response is not None:
        entry['response'] = _response_debug_payload(response)
    if error is not None:
        entry['error'] = {
            'type': type(error).__name__,
            'message': str(error),
        }

    call.full_debug = entry
    _append_full_debug_entry(entry)


def _perform_get(call, request_headers, request_timeout, *, verify=True):
    kwargs = {
        'params': call.query,
        'headers': request_headers,
        'timeout': request_timeout,
    }
    if verify is False:
        kwargs['verify'] = False
    return requests.get(call.url, **kwargs)


def execute_external_call(call: ExternalCall, headers=None, timeout=None):
    """Execute an :class:`ExternalCall` and return the HTTP response.

    ``ExternalCall`` intentionally only describes the request.  This helper is
    the central place that turns that description into a network request, so
    individual resolvers can stay focused on building calls and parsing
    responses.
    """
    request_headers = {'User-Agent': USER_AGENT}
    if headers:
        request_headers.update(headers)

    request_timeout = REQUEST_TIMEOUT if timeout is None else timeout
    started_at = time.perf_counter()
    response = None
    request_failed = False
    verify = True
    try:
        try:
            response = _perform_get(call, request_headers, request_timeout)
        except requests.exceptions.SSLError as error:
            if not call.allow_insecure_tls_fallback:
                raise
            duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
            _record_full_debug(
                call,
                headers=request_headers,
                timeout=request_timeout,
                duration_ms=duration_ms,
                response=response,
                error=error,
                verify=True,
            )
            started_at = time.perf_counter()
            verify = False
            response = _perform_get(call, request_headers, request_timeout, verify=False)
        response.raise_for_status()
        return response
    except requests.RequestException as error:
        request_failed = True
        duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
        _record_full_debug(
            call,
            headers=request_headers,
            timeout=request_timeout,
            duration_ms=duration_ms,
            response=response,
            error=error,
            verify=verify,
        )
        raise
    finally:
        if response is not None and not request_failed:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
            _record_full_debug(
                call,
                headers=request_headers,
                timeout=request_timeout,
                duration_ms=duration_ms,
                response=response,
                verify=verify,
            )


def fetch_response(call: ExternalCall, accept: str):
    return execute_external_call(call, headers={'Accept': accept})


def parse_json_response(response, logger=None):
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        if logger:
            logger.warning('taxonomy resolver non-json response from %s (status=%s)', response.url, response.status_code)
        return None


def fetch_json(call: ExternalCall, accept: str = 'application/json'):
    try:
        response = execute_external_call(call, headers={'Accept': accept})
    except requests.RequestException:
        return None
    return parse_json_response(response)


def fetch_text(call: ExternalCall, accept: str = 'text/html,application/xhtml+xml'):
    try:
        response = execute_external_call(call, headers={'Accept': accept})
    except requests.RequestException:
        return None
    return response.text or ''
