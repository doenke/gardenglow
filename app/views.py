import os
import time
import re
import shutil
import json
import math
import sqlite3
import subprocess
import zipfile
from io import BytesIO
from urllib.parse import quote, unquote, urljoin

import requests
from functools import wraps
from datetime import datetime, timezone, timedelta
from flask import Blueprint, abort, current_app, g, render_template, request, redirect, url_for, session, jsonify, send_from_directory, flash, send_file
from .models import db, utc_now, User, Location, Plant, PlantPhoto, PlantNote, GardenMap, TimelineEntry, LightNeed, SoilProperty, Sensor, PlantDatabaseIdentifier, InfluxIntegrationConfig, plant_soil_property, sensor_location, SENSOR_TYPE_LABELS, SENSOR_TYPE_SOIL_MOISTURE, SENSOR_TYPE_TEMPERATURE, SENSOR_TYPE_RAINFALL, SENSOR_TYPES
from sqlalchemy import or_
from .map_data import MapPointValidationError, parse_stored_points, validate_calibration_points, validate_polygon_points
from .services.timeline_service import save_uploaded_attachment, set_single_title_entry, delete_timeline_entry, build_unique_upload_name
from .services import influx_service
from .services.influx_service import FluxInfluxQueryAdapter, InfluxIntegrationConfig as InfluxServiceConfig, latest_sensor_value
from .auth import get_or_create_default_user, oidc_enabled
from .taxonomy import service as taxonomy_service
from .taxonomy.catalogs import get_database_catalog_by_key, get_database_catalogs
from .taxonomy.resolvers.base import ExternalCall, normalize_scientific_name_for_lookup
from .taxonomy.resolvers.http import execute_external_call, full_debug_enabled, get_full_debug_external_requests

main_bp = Blueprint('main', __name__)
ALLOWED = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'pdf'}
IMAGE_TYPES = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAP_IMAGE_ALLOWED = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAP_IMAGE_MIME_TYPES = {
    'image/png',
    'image/jpeg',
    'image/webp',
    'image/gif',
}
ALLOWED_ATTACHMENT_MIME_TYPES = {
    'image/png',
    'image/jpeg',
    'image/webp',
    'image/gif',
    'application/pdf',
}
TRASH_LOCATION_NAME = "Papierkorb"
EVENT_TYPE_MAP = {
    'planting': 'plant_event',
    'outplant': 'plant_event',
    'transplant': 'plant_event',
    'user_comment': 'user_event',
    'care_event': 'care_event',
    'measurement': 'measurement_event',
}

SYSTEM_EVENT_TEMPLATES = {
    'planting': {'title': 'Eingepflanzt', 'description': 'Pflanze wurde eingepflanzt.'},
    'transplant': {'title': 'Umgepflanzt', 'description': 'Pflanze wurde umgepflanzt.'},
    'outplant': {'title': 'Ausgepflanzt', 'description': 'Pflanze wurde ausgepflanzt.'},
}

PLANTING_STATE_TYPES = {
    'Eingepflanzt': 'planting',
    'Umgepflanzt': 'transplant',
    'Ausgepflanzt': 'outplant',
}

_upload_stats_cache = {
    'expires_at': 0.0,
    'upload_folder': None,
    'uploads': 0,
    'upload_size_bytes': 0,
}

LIGHT_NEED_OPTIONS = [
    {'key': 'full_sun', 'label': 'Sonnig', 'icon': '☀️'},
    {'key': 'part_shade', 'label': 'Halbschatten', 'icon': '⛅'},
    {'key': 'shade', 'label': 'Schatten', 'icon': '🌑'},
]
LIGHT_NEED_KEY_TO_LABEL = {item['key']: item['label'] for item in LIGHT_NEED_OPTIONS}
LIGHT_NEED_ICON_BY_KEY = {item['key']: item['icon'] for item in LIGHT_NEED_OPTIONS}
SOIL_MOISTURE_RANGE_OPTIONS = [
    {'key': '24h', 'label': '24 Stunden', 'delta': timedelta(hours=24)},
    {'key': '7d', 'label': '7 Tage', 'delta': timedelta(days=7)},
    {'key': '30d', 'label': '30 Tage', 'delta': timedelta(days=30)},
    {'key': '1y', 'label': '1 Jahr', 'delta': timedelta(days=365)},
]
SOIL_MOISTURE_RANGE_BY_KEY = {item['key']: item for item in SOIL_MOISTURE_RANGE_OPTIONS}
DEFAULT_SOIL_MOISTURE_RANGE = '7d'
WEATHER_SERIES_DEFINITIONS = {
    'temperature': {'label': 'Temperatur', 'unit': '°C'},
    'rainfall': {'label': 'Regenmenge', 'unit': 'mm'},
}

ENVIRONMENT_VARIABLES = [
    {'name': 'SECRET_KEY', 'config_key': 'SECRET_KEY', 'default': None, 'sensitive': True},
    {'name': 'DATABASE_URL', 'config_key': 'SQLALCHEMY_DATABASE_URI', 'default': 'sqlite:///garden.db'},
    {'name': 'UPLOAD_FOLDER', 'config_key': 'UPLOAD_FOLDER', 'default': '/data/uploads'},
    {'name': 'MAX_ATTACHMENT_SIZE_BYTES', 'config_key': 'MAX_ATTACHMENT_SIZE_BYTES', 'default': str(15 * 1024 * 1024)},
    {'name': 'AVATAR_FOLDER', 'config_key': 'AVATAR_FOLDER', 'default': '/data/avatars'},
    {'name': 'MAX_AVATAR_SIZE_BYTES', 'config_key': 'MAX_AVATAR_SIZE_BYTES', 'default': str(5 * 1024 * 1024)},
    {'name': 'MAP_FOLDER', 'config_key': 'MAP_FOLDER', 'default': '/data/maps'},
    {'name': 'BACKUP_FOLDER', 'config_key': 'BACKUP_FOLDER', 'default': '/data/backups'},
    {'name': 'APP_VERSION', 'config_key': 'APP_VERSION', 'default': ''},
    {'name': 'GIT_COMMIT', 'config_key': 'GIT_COMMIT', 'default': ''},
    {'name': 'WIDGET_API_KEY', 'config_key': 'WIDGET_API_KEY', 'default': '', 'sensitive': True},
    {'name': 'STATS_UPLOAD_CACHE_TTL_SECONDS', 'config_key': 'STATS_UPLOAD_CACHE_TTL_SECONDS', 'default': '60'},
    {'name': 'HEADER_LOGO_URL', 'config_key': 'HEADER_LOGO_URL', 'default': ''},
    {'name': 'COMMON_NAME_LOOKUP_LANG', 'config_key': 'COMMON_NAME_LOOKUP_LANG', 'default': 'de'},
    {'name': 'DEBUG_MODE', 'config_key': 'DEBUG_MODE', 'default': 'false'},
    {'name': 'INFLUX_URL', 'config_key': 'INFLUX_URL', 'default': ''},
    {'name': 'INFLUX_TOKEN', 'config_key': 'INFLUX_TOKEN', 'default': '', 'sensitive': True},
    {'name': 'INFLUX_ORG', 'config_key': 'INFLUX_ORG', 'default': ''},
    {'name': 'INFLUX_BUCKET', 'config_key': 'INFLUX_BUCKET', 'default': ''},
    {'name': 'INFLUX_TIMEOUT_SECONDS', 'config_key': 'INFLUX_TIMEOUT_SECONDS', 'default': '5'},
    {'name': 'OIDC_SERVER_METADATA_URL', 'config_key': None, 'default': ''},
    {'name': 'OIDC_CLIENT_ID', 'config_key': None, 'default': ''},
    {'name': 'OIDC_CLIENT_SECRET', 'config_key': None, 'default': '', 'sensitive': True},
    {'name': 'OIDC_LOGOUT_URL', 'config_key': None, 'default': ''},
]


def session_get_or_404(model, ident):
    instance = db.session.get(model, ident)
    if instance is None:
        abort(404)
    return instance


def _debuggable_external_get(catalog, url, params=None, timeout=6):
    return execute_external_call(
        ExternalCall(catalog=catalog, url=url, query=params or {}),
        timeout=timeout,
    )


def _debug_payload(trace_id, duration_ms=None):
    payload = {'trace_id': trace_id}
    if duration_ms is not None:
        payload['duration_ms'] = duration_ms
    if full_debug_enabled():
        payload['full_debug_enabled'] = True
        payload['external_web_requests'] = get_full_debug_external_requests()
    return payload


def _title_text_from_html(page_html):
    if not page_html:
        return None
    match = re.search(r'<title[^>]*>(.*?)</title>', page_html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    title = re.sub(r'\s+', ' ', match.group(1)).strip()
    title = re.sub(r'\s*[-|–—]\s*NaturaDB\s*$', '', title, flags=re.IGNORECASE).strip()
    return title or None


def _strip_edge_special_characters(value):
    value = (value or '').strip()
    start = 0
    end = len(value)
    while start < end and not value[start].isalnum():
        start += 1
    while end > start and not value[end - 1].isalnum():
        end -= 1
    return value[start:end].strip() or None


def _naturadb_common_name_from_slug(slug):
    slug = (slug or '').strip().strip('/')
    if not slug:
        return None, []
    catalog = get_database_catalog_by_key('naturadb')
    if not catalog:
        return None, []
    url = (catalog.record_url_template or '').replace('{id}', quote(slug, safe='/'))
    try:
        response = _debuggable_external_get('naturadb_common_name', url, timeout=6)
        response.raise_for_status()
    except requests.RequestException:
        return None, [url]
    title = _title_text_from_html(response.text or '')
    if not title:
        return None, [url]
    scientific = normalize_scientific_name_for_lookup(slug.replace('-', ' ')) or ''
    cleaned_title = _strip_edge_special_characters(title)
    name = cleaned_title or title
    if scientific:
        name = re.sub(rf'\s*\(?\b{re.escape(scientific)}\b\)?\s*$', '', name, flags=re.IGNORECASE)
    cleaned_name = _strip_edge_special_characters(name)
    return (cleaned_name or cleaned_title or title), [url]


def _guess_common_name_from_text(scientific_name, text):
    if not text:
        return None
    patterns = [
        r"(?:known as|called|also known as|auch genannt|deutsch(?:er|e)? name:?|trivialname:?|volksname:?)[\s:]+([^\.\,;\(\)]+)",
        r"(?:is a|ist eine?|ist ein)\s+[^\.]*?\(([^\)]+)\)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = match.group(1).strip().strip('\"\'')
        candidate = re.sub(r"\s+", ' ', candidate)
        if candidate and candidate.lower() != scientific_name.lower() and len(candidate) <= 120:
            return candidate
    return None



def _lookup_common_name_from_web(scientific_name, language_code='de', naturadb_id=None, wikipedia_id=None):
    query = (scientific_name or '').strip()
    wikipedia_id = (wikipedia_id or '').strip()
    naturadb_id = (naturadb_id or '').strip()
    if naturadb_id:
        common_name, sources = _naturadb_common_name_from_slug(naturadb_id)
        if common_name:
            return common_name, sources

    normalized_query = normalize_scientific_name_for_lookup(query)
    language = (language_code or 'de').strip().lower()
    if not query:
        return None, []

    if not re.fullmatch(r'[a-z]{2,10}', language):
        language = 'de'

    base_domain = f'https://{language}.wikipedia.org'
    search_url = f'{base_domain}/w/api.php'
    summary_url = f'{base_domain}/api/rest_v1/page/summary'

    sources = []
    common_name = None

    def _search(term):
        try:
            response = _debuggable_external_get(
                'wikipedia_common_name_search',
                search_url,
                params={
                    'action': 'query',
                    'list': 'search',
                    'srsearch': term,
                    'utf8': 1,
                    'format': 'json',
                },
                timeout=6,
            )
            response.raise_for_status()
            return response.json().get('query', {}).get('search', [])
        except requests.RequestException:
            return []

    if wikipedia_id:
        page_slug = quote(unquote(wikipedia_id).replace(' ', '_'), safe=':_()-,.%')
        sources.append(f'{base_domain}/wiki/{page_slug}')
        try:
            summary_response = _debuggable_external_get(
                'wikipedia_common_name_summary',
                f'{summary_url}/{page_slug}',
                timeout=6,
            )
            summary_response.raise_for_status()
            summary_data = summary_response.json()
            extract = (summary_data.get('extract') or '').strip()
            title = (summary_data.get('title') or '').strip()
        except requests.RequestException:
            extract = ''
            title = ''
        common_name = _guess_common_name_from_text(normalized_query or query, extract)
        normalized_title = normalize_scientific_name_for_lookup(title) or title
        if not common_name and title and normalized_title.lower() != (normalized_query or query).lower():
            common_name = title
        if common_name:
            return common_name, list(dict.fromkeys(sources))

    search_results = _search(query)
    if not search_results and normalized_query and normalized_query.lower() != query.lower():
        search_results = _search(normalized_query)

    for item in search_results[:3]:
        title = (item.get('title') or '').strip()
        if not title:
            continue
        page_slug = title.replace(' ', '_')
        sources.append(f'{base_domain}/wiki/{page_slug}')
        try:
            summary_response = _debuggable_external_get(
                'wikipedia_common_name_summary',
                f'{summary_url}/{page_slug}',
                timeout=6,
            )
            summary_response.raise_for_status()
            extract = (summary_response.json().get('extract') or '').strip()
        except requests.RequestException:
            continue

        common_name = _guess_common_name_from_text(normalized_query or query, extract)
        if common_name:
            break

    if not common_name and search_results:
        first_title = (search_results[0].get('title') or '').strip()
        normalized_title = normalize_scientific_name_for_lookup(first_title) or first_title
        if first_title and normalized_title.lower() != (normalized_query or query).lower():
            common_name = first_title

    return common_name, list(dict.fromkeys(sources))


def parse_light_need_keys(values):
    keys = [value.strip() for value in values if value and value.strip() in LIGHT_NEED_KEY_TO_LABEL]
    return keys


def format_light_needs(light_needs):
    return ', '.join(light_need.label for light_need in light_needs)


def get_catalog_configs():
    return get_database_catalogs()


def _database_catalog_order():
    return {catalog.key: index for index, catalog in enumerate(get_catalog_configs())}


def _normalize_database_identifier_for_catalog(catalog_key, identifier):
    value = (identifier or '').strip()
    if catalog_key == 'wikipedia_de':
        return unquote(value).replace(' ', '_')
    return value


def _display_database_identifier_for_catalog(catalog_key, identifier):
    value = (identifier or '').strip()
    if catalog_key == 'wikipedia_de':
        return unquote(value)
    return value


def _record_url_for_database_identifier(catalog, identifier):
    value = (identifier or '').strip()
    if not value:
        return ''
    if catalog.key == 'wikipedia_de':
        value = quote(unquote(value).replace(' ', '_'), safe=':_()-,.%')
    return (catalog.record_url_template or '').replace('{id}', value)


def _build_database_identifier_values(plant):
    values = {}
    for item in plant.database_identifiers:
        if not item.catalog:
            continue
        identifier = _display_database_identifier_for_catalog(item.catalog.key, item.taxonomy_id)
        if identifier:
            values[item.catalog.key] = identifier
    return values


def _build_database_links_for_plant(plant):
    links = []
    for item in plant.database_identifiers:
        if not item.catalog or not item.catalog.enabled:
            continue
        identifier = (item.taxonomy_id or '').strip()
        if not identifier:
            continue
        display_identifier = _display_database_identifier_for_catalog(item.catalog.key, identifier)
        url = _record_url_for_database_identifier(item.catalog, identifier)
        links.append({
            'catalog_key': item.catalog.key,
            'catalog_label': item.catalog.label,
            'identifier': identifier,
            'display_identifier': display_identifier,
            'url': url,
            'icon_url': (item.catalog.icon_url or '').strip(),
        })
    catalog_order = _database_catalog_order()
    return sorted(
        links,
        key=lambda link: (
            catalog_order.get(link['catalog_key'], len(catalog_order)),
            (link['catalog_label'] or '').lower(),
            link['display_identifier'].lower(),
        ),
    )


def _build_database_search_urls(catalogs, search_query):
    query = (search_query or '').strip()
    urls = {}
    if not query:
        return urls

    encoded_query = quote(query, safe='')
    for catalog in catalogs:
        template = (catalog.search_url_template or '').strip()
        if not template or '{q}' not in template:
            continue
        urls[catalog.key] = template.replace('{q}', encoded_query)
    return urls


def parse_soil_properties(raw_value):
    labels = []
    for value in (raw_value or '').split(','):
        cleaned = value.strip()
        if cleaned and cleaned.lower() not in {entry.lower() for entry in labels}:
            labels.append(cleaned)
    return labels


def get_or_create_soil_properties(labels):
    properties = []
    for label in labels:
        existing = SoilProperty.query.filter(db.func.lower(SoilProperty.label) == label.lower()).first()
        if existing:
            properties.append(existing)
            continue
        new_entry = SoilProperty(label=label)
        db.session.add(new_entry)
        db.session.flush()
        properties.append(new_entry)
    return properties


def get_top_soil_property_labels(excluded_soil_property_ids=None, limit=5):
    excluded_soil_property_ids = excluded_soil_property_ids or []
    top_soil_properties_query = (
        db.session.query(
            SoilProperty.label,
            db.func.count(plant_soil_property.c.plant_id).label('usage_count'),
        )
        .join(plant_soil_property, plant_soil_property.c.soil_property_id == SoilProperty.id)
    )
    if excluded_soil_property_ids:
        top_soil_properties_query = top_soil_properties_query.filter(~SoilProperty.id.in_(excluded_soil_property_ids))
    top_soil_properties = (
        top_soil_properties_query
        .group_by(SoilProperty.id, SoilProperty.label)
        .order_by(db.desc('usage_count'), SoilProperty.label.asc())
        .limit(limit)
        .all()
    )
    if len(top_soil_properties) < limit:
        existing_top_labels = {item.label for item in top_soil_properties}
        fallback_soil_properties = SoilProperty.query
        if excluded_soil_property_ids:
            fallback_soil_properties = fallback_soil_properties.filter(~SoilProperty.id.in_(excluded_soil_property_ids))
        if existing_top_labels:
            fallback_soil_properties = fallback_soil_properties.filter(~SoilProperty.label.in_(existing_top_labels))
        fallback_soil_properties = (
            fallback_soil_properties
            .order_by(SoilProperty.label.asc())
            .limit(limit - len(top_soil_properties))
            .all()
        )
        top_soil_properties += [(item.label, 0) for item in fallback_soil_properties]
    return [item[0] for item in top_soil_properties]


def timeline_entry_has_content(title=None, description=None, attachment_filename=None):
    return bool((title or '').strip() or (description or '').strip() or attachment_filename)


def attachment_kind_for_filename(filename):
    if not filename:
        return None
    ext = filename.rsplit('.', 1)[1].lower()
    return 'image' if ext in IMAGE_TYPES else 'pdf'


def create_timeline_entry(*, scope_type, scope_id, creator_id, created_at=None, event_at=None, event_type=None, title=None, description=None, attachment_filename=None, attachment_kind=None):
    entry = TimelineEntry(
        scope_type=scope_type,
        scope_id=scope_id,
        created_at=created_at or utc_now(),
        event_at=event_at,
        event_type=event_type,
        title=title,
        description=description,
        attachment_filename=attachment_filename,
        attachment_kind=attachment_kind,
        creator_id=creator_id,
    )
    db.session.add(entry)
    return entry


def location_sort_criteria():
    return (
        db.case((Location.name == TRASH_LOCATION_NAME, 1), else_=0).asc(),
        Location.name.asc(),
        Location.id.asc(),
    )




def get_flower_color_suggestions():
    colors = (
        db.session.query(Plant.flower_color)
        .filter(Plant.flower_color.isnot(None))
        .distinct()
        .order_by(Plant.flower_color.asc())
        .all()
    )
    return [color[0].strip() for color in colors if color[0] and color[0].strip()]


def get_source_suggestions(limit=30):
    sources = (
        db.session.query(Plant.source, db.func.count(Plant.id).label('usage_count'))
        .filter(Plant.source.isnot(None), Plant.source != '')
        .group_by(Plant.source)
        .order_by(db.desc('usage_count'), Plant.source.asc())
        .limit(limit)
        .all()
    )
    return [source for source, _ in sources]


def build_duplicate_plant_name(original_name):
    base_name = (original_name or 'Pflanze').strip() or 'Pflanze'
    copy_name = f"{base_name} (Kopie)"
    existing_names = {
        name for (name,) in db.session.query(Plant.name)
        .filter(Plant.name.like(f"{copy_name}%"))
        .all()
    }
    if copy_name not in existing_names:
        return copy_name

    counter = 2
    while f"{copy_name} {counter}" in existing_names:
        counter += 1
    return f"{copy_name} {counter}"


def slugify_sensor_key(value):
    base = re.sub(r'[^a-z0-9]+', '-', (value or '').strip().lower()).strip('-')
    return base[:96] or 'sensor'


def build_unique_sensor_key(sensor, preferred_value):
    base = slugify_sensor_key(preferred_value)
    candidate = base
    counter = 2
    query = Sensor.query
    if sensor and sensor.id:
        query = query.filter(Sensor.id != sensor.id)
    existing_keys = {key for (key,) in query.with_entities(Sensor.key).all()}
    while candidate in existing_keys:
        suffix = f'-{counter}'
        candidate = f'{base[:128 - len(suffix)]}{suffix}'
        counter += 1
    return candidate


def parse_sensor_map_coordinate(field_name):
    raw_value = (request.form.get(field_name) or '').strip()
    if not raw_value:
        return None, True
    try:
        return float(raw_value), True
    except ValueError:
        return None, False


def get_selected_sensor_locations(form):
    selected_ids = []
    for raw_value in form.getlist('location_ids'):
        try:
            selected_ids.append(int(raw_value))
        except (TypeError, ValueError):
            continue
    if not selected_ids:
        single_id = form.get('location_id', type=int)
        if single_id:
            selected_ids.append(single_id)
    if not selected_ids:
        return []
    return Location.query.filter(Location.id.in_(selected_ids)).order_by(*location_sort_criteria()).all()


def apply_sensor_form(sensor, form):
    name = (form.get('sensor_label') or form.get('name') or '').strip()
    if not name:
        return False, 'Bitte einen Sensornamen angeben.'

    map_x, map_x_valid = parse_sensor_map_coordinate('map_x')
    map_y, map_y_valid = parse_sensor_map_coordinate('map_y')
    if not map_x_valid or not map_y_valid:
        return False, 'Bitte gültige Koordinaten für map_x und map_y angeben.'

    # Eine leere Standortliste ist eine bewusste Auswahl: Der Sensor gilt
    # dadurch für alle produktiven Beete. Soll ein Sensor keinem produktiven
    # Beet zugeordnet sein, wird stattdessen der Papierkorb ausgewählt.
    locations = get_selected_sensor_locations(form)

    sensor_type = (form.get('sensor_type') or form.get('type') or '').strip()
    if not sensor_type:
        return False, 'Bitte einen Sensortyp auswählen.'
    if sensor_type not in SENSOR_TYPES:
        return False, 'Bitte einen gültigen Sensortyp auswählen.'

    entity_id = (form.get('homeassistant_entity_id') or '').strip() or None
    ha_influx_defaults = influx_service.homeassistant_entity_influx_defaults(entity_id) if entity_id else {}
    sensor.name = name
    sensor.sensor_type = sensor_type
    sensor.homeassistant_entity_id = entity_id
    sensor.influx_measurement = (form.get('influx_measurement') or '').strip() or (
        ha_influx_defaults.get('measurement') if ha_influx_defaults else None
    ) or None
    sensor.influx_field = (form.get('influx_field') or '').strip() or (
        ha_influx_defaults.get('field') if ha_influx_defaults else None
    ) or None
    sensor.influx_tags = (form.get('influx_tags') or '').strip() or (
        ha_influx_defaults.get('tags') if ha_influx_defaults else None
    ) or None
    sensor.map_x = map_x
    sensor.map_y = map_y
    sensor.key = build_unique_sensor_key(sensor, entity_id or name)
    sensor.locations = locations
    return True, None


def duplicate_plant_record(plant, creator_id):
    duplicated = Plant(
        location_id=plant.location_id,
        name=build_duplicate_plant_name(plant.name),
        cultivar=plant.cultivar,
        scientific_name=plant.scientific_name,
        common_name=plant.common_name,
        source=plant.source,
        bloom_start_month=plant.bloom_start_month,
        bloom_end_month=plant.bloom_end_month,
        flower_color=plant.flower_color,
        height_without_bloom_cm=plant.height_without_bloom_cm,
        height_with_bloom_cm=plant.height_with_bloom_cm,
        info=plant.info,
        map_x=plant.map_x,
        map_y=plant.map_y,
        creator_id=creator_id,
    )
    duplicated.light_needs = list(plant.light_needs)
    duplicated.soil_properties = list(plant.soil_properties)
    duplicated.database_identifiers = [
        PlantDatabaseIdentifier(
            catalog_key=identifier.catalog_key,
            taxonomy_id=identifier.taxonomy_id,
        )
        for identifier in plant.database_identifiers
    ]
    db.session.add(duplicated)
    db.session.flush()
    create_timeline_entry(
        scope_type='plant',
        scope_id=duplicated.id,
        event_type='data_event',
        event_at=utc_now(),
        title='Pflanze dupliziert',
        description=f'Dupliziert von {plant.name}.',
        creator_id=creator_id,
    )
    return duplicated

def create_system_event(plant_id, key, creator_id, event_at=None, description=None):
    tpl = SYSTEM_EVENT_TEMPLATES[key]
    create_timeline_entry(
        scope_type='plant',
        scope_id=plant_id,
        event_at=event_at or utc_now(),
        event_type='plant_event',
        title=tpl['title'],
        description=description if description is not None else tpl['description'],
        creator_id=creator_id,
    )

def current_user():
    cached_user_loaded = getattr(g, '_current_user_loaded', False)
    if cached_user_loaded:
        return g._current_user

    uid = session.get('user_id')
    user = db.session.get(User, uid) if uid else None
    if user is None and not oidc_enabled():
        user = get_or_create_default_user()
    g._current_user = user
    g._current_user_loaded = True
    return user

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return wrapped

def map_image_upload_error_message(upload_error):
    if upload_error == 'too_large':
        return 'Luftbild zu groß (max. 15 MB).'
    if upload_error in {'mime_not_allowed', 'image_content_not_allowed'}:
        return 'Luftbild-Dateityp nicht erlaubt. Bitte eine echte Bilddatei (PNG, JPG, WEBP oder GIF) hochladen.'
    if upload_error == 'extension_not_allowed':
        return 'Dateiendung nicht erlaubt. Bitte ein Luftbild als PNG, JPG, WEBP oder GIF hochladen.'
    return None


def widget_api_key_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        configured_key = (current_app.config.get('WIDGET_API_KEY') or '').strip()
        if not configured_key:
            return jsonify({'error': 'Widget API key is not configured'}), 503

        api_key = (request.headers.get('X-API-Key') or '').strip()
        if not api_key:
            auth_header = (request.headers.get('Authorization') or '').strip()
            if auth_header.lower().startswith('bearer '):
                api_key = auth_header[7:].strip()

        if api_key != configured_key:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return wrapped


def parse_bloom_months(form):
    bloom_start_month = form.get('bloom_start_month', type=int)
    bloom_end_month = form.get('bloom_end_month', type=int)
    if (bloom_start_month is None) != (bloom_end_month is None):
        return None, None, False
    return bloom_start_month, bloom_end_month, True

def get_or_create_garden_map():
    garden_map = GardenMap.query.order_by(GardenMap.id.asc()).first()
    if garden_map:
        return garden_map
    garden_map = GardenMap(calibration_points='[]', boundary_points='[]')
    db.session.add(garden_map)
    db.session.flush()
    return garden_map

def get_or_create_trash_location():
    trash_locations = Location.query.filter_by(name=TRASH_LOCATION_NAME).order_by(Location.id.asc()).all()
    if trash_locations:
        trash = trash_locations[0]
        for duplicate in trash_locations[1:]:
            Plant.query.filter_by(location_id=duplicate.id).update({'location_id': trash.id})
            db.session.delete(duplicate)
        db.session.flush()
        return trash
    trash = Location(
        name=TRASH_LOCATION_NAME,
        description="Automatisch erstellt. Gelöschte Pflanzen landen hier."
    )
    db.session.add(trash)
    db.session.flush()
    return trash


def _format_bytes(size_bytes):
    size = float(size_bytes or 0)
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == 'B':
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


def _safe_file_entries(folder):
    entries = []
    if not folder or not os.path.isdir(folder):
        return entries
    folder_abs = os.path.abspath(folder)
    for root, _, files in os.walk(folder_abs):
        for filename in files:
            full_path = os.path.abspath(os.path.join(root, filename))
            if not os.path.isfile(full_path):
                continue
            try:
                relative_path = os.path.relpath(full_path, folder_abs).replace(os.sep, '/')
                entries.append({
                    'filename': relative_path,
                    'path': full_path,
                    'size_bytes': os.path.getsize(full_path),
                    'mtime': datetime.fromtimestamp(os.path.getmtime(full_path), timezone.utc),
                })
            except OSError:
                continue
    entries.sort(key=lambda item: item['filename'].lower())
    return entries


def _upload_folder_definitions():
    return [
        {'key': 'uploads', 'label': 'Uploads', 'path': current_app.config.get('UPLOAD_FOLDER')},
        {'key': 'avatars', 'label': 'Avatare', 'path': current_app.config.get('AVATAR_FOLDER')},
        {'key': 'maps', 'label': 'Karten', 'path': current_app.config.get('MAP_FOLDER')},
    ]


def _referenced_upload_filenames():
    timeline_files = {
        filename for (filename,) in db.session.query(TimelineEntry.attachment_filename)
        .filter(TimelineEntry.attachment_filename.isnot(None))
        .all()
        if filename
    }
    legacy_photo_files = {
        filename for (filename,) in db.session.query(PlantPhoto.filename).all()
        if filename
    }
    avatar_files = {
        filename for (filename,) in db.session.query(User.avatar_filename)
        .filter(User.avatar_filename.isnot(None))
        .all()
        if filename
    }
    map_files = {
        filename for (filename,) in db.session.query(GardenMap.filename)
        .filter(GardenMap.filename.isnot(None))
        .all()
        if filename
    }
    return {
        'uploads': timeline_files | legacy_photo_files,
        'avatars': avatar_files,
        'maps': map_files,
    }


def _build_upload_folder_report():
    referenced = _referenced_upload_filenames()
    report = []
    for folder in _upload_folder_definitions():
        entries = _safe_file_entries(folder['path'])
        referenced_for_folder = referenced.get(folder['key'], set())
        orphan_entries = [entry for entry in entries if entry['filename'] not in referenced_for_folder]
        total_size = sum(entry['size_bytes'] for entry in entries)
        orphan_size = sum(entry['size_bytes'] for entry in orphan_entries)
        report.append({
            **folder,
            'exists': bool(folder['path'] and os.path.isdir(folder['path'])),
            'file_count': len(entries),
            'size_bytes': total_size,
            'size_human': _format_bytes(total_size),
            'orphan_count': len(orphan_entries),
            'orphan_size_bytes': orphan_size,
            'orphan_size_human': _format_bytes(orphan_size),
            'orphan_files': [
                {
                    **entry,
                    'size_human': _format_bytes(entry['size_bytes']),
                }
                for entry in orphan_entries
            ],
        })
    return report


def _resolve_folder_by_key(folder_key):
    for folder in _upload_folder_definitions():
        if folder['key'] == folder_key:
            return folder
    return None


def _is_orphan_upload_file(folder_key, filename):
    report = _build_upload_folder_report()
    for folder in report:
        if folder['key'] != folder_key:
            continue
        return any(entry['filename'] == filename for entry in folder['orphan_files'])
    return False


def _database_file_path():
    try:
        url = db.engine.url
    except Exception:
        return None
    if not str(url.drivername).startswith('sqlite'):
        return None
    database = url.database
    if not database or database == ':memory:':
        return None
    if os.path.isabs(database):
        return database
    return os.path.abspath(os.path.join(current_app.instance_path, database))


def _database_size_info():
    db_path = _database_file_path()
    if db_path and os.path.isfile(db_path):
        size = os.path.getsize(db_path)
        return {'size_bytes': size, 'size_human': _format_bytes(size), 'path': db_path, 'type': 'SQLite'}
    driver = getattr(db.engine.url, 'drivername', 'unbekannt')
    return {'size_bytes': None, 'size_human': 'Nicht verfügbar', 'path': None, 'type': driver}


def _backup_report(limit=5):
    backup_folder = current_app.config.get('BACKUP_FOLDER')
    entries = []
    if backup_folder and os.path.isdir(backup_folder):
        entries = _safe_file_entries(backup_folder)
        entries.sort(key=lambda item: item['mtime'], reverse=True)
    return {
        'path': backup_folder,
        'exists': bool(backup_folder and os.path.isdir(backup_folder)),
        'files': [{**entry, 'size_human': _format_bytes(entry['size_bytes'])} for entry in entries[:limit]],
    }


def _create_sqlite_backup():
    backup_folder = current_app.config.get('BACKUP_FOLDER')
    if not backup_folder:
        raise RuntimeError('Backup-Ordner ist nicht konfiguriert.')

    db_path = _database_file_path()
    if not db_path or not os.path.isfile(db_path):
        raise RuntimeError('Es ist keine lokale SQLite-Datenbank für ein Backup verfügbar.')

    os.makedirs(backup_folder, exist_ok=True)
    timestamp = utc_now().strftime('%Y%m%d-%H%M%S')
    base_filename = f'garden-backup-{timestamp}.sqlite'
    backup_path = os.path.join(backup_folder, base_filename)
    counter = 2
    while os.path.exists(backup_path):
        backup_path = os.path.join(backup_folder, f'garden-backup-{timestamp}-{counter}.sqlite')
        counter += 1

    source = None
    destination = None
    try:
        source = sqlite3.connect(db_path)
        destination = sqlite3.connect(backup_path)
        source.backup(destination)
        destination.close()
        source.close()
    except sqlite3.Error as exc:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass
        raise RuntimeError('Backup konnte nicht erstellt werden.') from exc

    return {
        'filename': os.path.basename(backup_path),
        'path': backup_path,
        'size_bytes': os.path.getsize(backup_path),
    }

def _safe_backup_target(filename):
    backup_folder = current_app.config.get('BACKUP_FOLDER')
    if not backup_folder or not filename:
        return None
    folder_abs = os.path.abspath(backup_folder)
    target = os.path.abspath(os.path.join(folder_abs, filename))
    try:
        if os.path.commonpath([folder_abs, target]) != folder_abs:
            return None
    except ValueError:
        return None
    return target


def _validate_sqlite_backup(backup_path):
    try:
        with sqlite3.connect(f'file:{quote(backup_path)}?mode=ro', uri=True) as connection:
            integrity = connection.execute('PRAGMA integrity_check').fetchone()
            if not integrity or integrity[0] != 'ok':
                return False
            expected_tables = {'user'}
            existing_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            return expected_tables.issubset(existing_tables)
    except sqlite3.Error:
        return False


def _restore_sqlite_backup(filename):
    backup_path = _safe_backup_target(filename)
    if not backup_path or not os.path.isfile(backup_path):
        raise RuntimeError('Backup-Datei wurde nicht gefunden.')
    if not _validate_sqlite_backup(backup_path):
        raise RuntimeError('Backup-Datei ist keine gültige GardenGlow-SQLite-Datenbank.')

    db_path = _database_file_path()
    if not db_path or not os.path.isfile(db_path):
        raise RuntimeError('Es ist keine lokale SQLite-Datenbank zum Wiederherstellen verfügbar.')

    db.session.remove()
    db.engine.dispose()
    shutil.copy2(backup_path, db_path)


def _delete_backup(filename):
    backup_path = _safe_backup_target(filename)
    if not backup_path or not os.path.isfile(backup_path):
        raise RuntimeError('Backup-Datei wurde nicht gefunden.')
    os.remove(backup_path)


def _git_commit():
    configured = (current_app.config.get('GIT_COMMIT') or '').strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=os.path.abspath(os.path.join(current_app.root_path, '..')),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() or 'Unbekannt'
    except (OSError, subprocess.SubprocessError):
        return 'Unbekannt'


def _app_version_info():
    return {
        'version': (current_app.config.get('APP_VERSION') or os.getenv('APP_VERSION') or 'Nicht gesetzt').strip(),
        'git_commit': _git_commit(),
    }


def _display_environment_value(definition, value):
    if definition.get('sensitive') and value:
        return '••••••••'
    if value is None:
        return ''
    return str(value)


def _environment_variable_report():
    variables = []
    for definition in ENVIRONMENT_VARIABLES:
        name = definition['name']
        raw_value = os.environ.get(name)
        is_set = raw_value is not None and raw_value != ''
        config_key = definition.get('config_key')
        if config_key:
            effective_value = current_app.config.get(config_key)
        else:
            effective_value = raw_value if raw_value is not None else definition.get('default')

        variables.append({
            'name': name,
            'value': _display_environment_value(definition, effective_value),
            'source': 'Gesetzt' if is_set else 'Default',
            'is_set': is_set,
            'sensitive': bool(definition.get('sensitive')),
        })
    return variables


def _serialize_export_value(value):
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


def _build_data_export_payload():
    payload = {
        'exported_at': utc_now().isoformat().replace('+00:00', 'Z'),
        'app': _app_version_info(),
        'tables': {},
    }
    for table in db.metadata.sorted_tables:
        rows = db.session.execute(db.select(table)).mappings().all()
        payload['tables'][table.name] = [
            {key: _serialize_export_value(value) for key, value in row.items()}
            for row in rows
        ]
    return payload

@main_bp.route('/healthz')
def healthz():
    try:
        db.session.execute(db.text('SELECT 1'))
        return jsonify({'status': 'ok'}), 200
    except Exception:
        return jsonify({'status': 'error'}), 500


@main_bp.route('/api/stats', methods=['GET'])
@widget_api_key_required
def api_stats():
    plant_count = db.session.query(db.func.count(Plant.id)).scalar() or 0
    bed_count = db.session.query(db.func.count(Location.id)).filter(Location.name != TRASH_LOCATION_NAME).scalar() or 0
    upload_folder = current_app.config.get('UPLOAD_FOLDER')
    cache_ttl = current_app.config.get('STATS_UPLOAD_CACHE_TTL_SECONDS', 60)
    now = time.monotonic()
    should_refresh = (
        _upload_stats_cache['upload_folder'] != upload_folder
        or now >= _upload_stats_cache['expires_at']
    )
    if should_refresh:
        upload_count = 0
        upload_total_size = 0
        if upload_folder and os.path.isdir(upload_folder):
            for root, _, files in os.walk(upload_folder):
                for filename in files:
                    full_path = os.path.join(root, filename)
                    if not os.path.isfile(full_path):
                        continue
                    upload_count += 1
                    upload_total_size += os.path.getsize(full_path)
        _upload_stats_cache['upload_folder'] = upload_folder
        _upload_stats_cache['uploads'] = upload_count
        _upload_stats_cache['upload_size_bytes'] = upload_total_size
        _upload_stats_cache['expires_at'] = now + max(0, cache_ttl)

    upload_count = _upload_stats_cache['uploads']
    upload_total_size = _upload_stats_cache['upload_size_bytes']

    database_size = 0
    db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if isinstance(db_uri, str) and db_uri.startswith('sqlite:///'):
        sqlite_path = db_uri.replace('sqlite:///', '', 1)
        if sqlite_path and os.path.isfile(sqlite_path):
            database_size = os.path.getsize(sqlite_path)

    return jsonify({
        'plants': plant_count,
        'beds': bed_count,
        'uploads': upload_count,
        'upload_size_bytes': upload_total_size,
        'database_size_bytes': database_size,
    }), 200

@main_bp.route('/manifest.webmanifest')
def manifest():
    return send_from_directory(current_app.static_folder, 'manifest.webmanifest', mimetype='application/manifest+json')

@main_bp.route('/sw.js')
def sw():
    return send_from_directory(current_app.static_folder, 'sw.js', mimetype='application/javascript')


@main_bp.route('/favicon.svg')
def favicon():
    return send_from_directory(current_app.static_folder, 'favicon.svg', mimetype='image/svg+xml')

@main_bp.route('/uploads/<path:filename>')
@login_required
def uploads(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

@main_bp.route('/avatars/<path:filename>')
@login_required
def avatars(filename):
    return send_from_directory(current_app.config['AVATAR_FOLDER'], filename)

@main_bp.route('/')
@login_required
def index():
    user = current_user()
    locations = Location.query.order_by(*location_sort_criteria()).all()
    garden_map = GardenMap.query.order_by(GardenMap.id.asc()).first()
    sensor_current_extremes = _load_sensor_current_extremes()
    location_plant_counts = {
        location_id: count
        for location_id, count in db.session.query(Plant.location_id, db.func.count(Plant.id)).group_by(Plant.location_id).all()
    }
    plants = (
        db.session.query(Plant, Location)
        .join(Location, Plant.location_id == Location.id)
        .filter(Location.name != TRASH_LOCATION_NAME)
        .order_by(Location.name.asc(), Plant.name.asc())
        .all()
    )
    return render_template(
        'index.html',
        user=user,
        locations=locations,
        location_plant_counts=location_plant_counts,
        garden_map=garden_map,
        plants=plants,
        sensor_current_extremes=sensor_current_extremes,
    )


def _first_influx_integration_config():
    return InfluxIntegrationConfig.query.order_by(InfluxIntegrationConfig.id.asc()).first()


def _ensure_influx_integration_config():
    influx_config = _first_influx_integration_config()
    if influx_config is None:
        influx_config = InfluxIntegrationConfig()
        db.session.add(influx_config)
    return influx_config


def _sensor_influx_config():
    stored_config = _first_influx_integration_config()
    if stored_config and any((
        stored_config.influx_url,
        stored_config.influx_token,
        stored_config.influx_org,
        stored_config.influx_bucket,
    )):
        return influx_service.InfluxIntegrationConfig(
            url=(stored_config.influx_url or '').strip(),
            token=(stored_config.influx_token or '').strip(),
            org=(stored_config.influx_org or '').strip(),
            bucket=(stored_config.influx_bucket or '').strip(),
            timeout_seconds=stored_config.timeout_seconds or influx_service.DEFAULT_TIMEOUT_SECONDS,
            verify_tls=stored_config.verify_tls,
        )
    return influx_service.InfluxIntegrationConfig.from_app_config()


def _selected_soil_moisture_range():
    selected_key = (request.args.get('moisture_range') or DEFAULT_SOIL_MOISTURE_RANGE).strip()
    if selected_key not in SOIL_MOISTURE_RANGE_BY_KEY:
        selected_key = DEFAULT_SOIL_MOISTURE_RANGE
    return selected_key, SOIL_MOISTURE_RANGE_BY_KEY[selected_key]['delta']


def _location_sensors(location_id, sensor_type=None):
    location = db.session.get(Location, location_id)
    if not location:
        return []

    query = Sensor.query.filter(Sensor.is_active.is_(True))
    if sensor_type:
        query = query.filter(Sensor.sensor_type == sensor_type)

    explicit_location_filter = Sensor.locations.any(Location.id == location.id)
    if location.name == TRASH_LOCATION_NAME:
        query = query.filter(explicit_location_filter)
    else:
        # Semantik der Standort-Auswahl bei Sensoren:
        # - explizit ausgewählte Beete gelten nur für diese Beete
        # - keine ausgewählten Beete gelten als globaler Sensor für alle
        #   produktiven Beete
        # - der Papierkorb modelliert ausdrücklich "kein produktives Beet"
        query = query.filter(or_(
            explicit_location_filter,
            ~Sensor.locations.any(),
        ))

    return query.order_by(Sensor.name.asc(), Sensor.id.asc()).all()


def _location_soil_moisture_sensors(location_id):
    return _location_sensors(location_id, SENSOR_TYPE_SOIL_MOISTURE)


def _empty_soil_moisture_series(sensors):
    return [{'sensor_id': sensor.id, 'name': sensor.name, 'points': []} for sensor in sensors]


def _weather_sensor_types():
    return {
        'temperature': SENSOR_TYPE_TEMPERATURE,
        'rainfall': SENSOR_TYPE_RAINFALL,
    }


def _empty_weather_sensor_series(sensors_by_kind=None):
    sensors_by_kind = sensors_by_kind or {}
    return {
        kind: {
            'kind': kind,
            'label': definition['label'],
            'unit': definition['unit'],
            'configured': bool(sensors_by_kind.get(kind)),
            'points': [],
            'series': [
                {'sensor_id': sensor.id, 'name': sensor.name, 'points': []}
                for sensor in sensors_by_kind.get(kind, [])
            ],
        }
        for kind, definition in WEATHER_SERIES_DEFINITIONS.items()
    }


def _location_weather_sensors(location_id):
    """Return weather sensors for a location, treating unassigned sensors as global."""
    trash_location = Location.query.filter_by(name=TRASH_LOCATION_NAME).order_by(Location.id.asc()).first()
    sensors_by_kind = {}
    for kind, sensor_type in _weather_sensor_types().items():
        sensors = (
            Sensor.query
            .outerjoin(sensor_location, Sensor.id == sensor_location.c.sensor_id)
            .filter(Sensor.is_active.is_(True))
            .filter(Sensor.sensor_type == sensor_type)
            .filter(db.or_(sensor_location.c.location_id.is_(None), sensor_location.c.location_id == location_id))
            .order_by(Sensor.name.asc(), Sensor.id.asc())
            .all()
        )
        if trash_location and trash_location.id != location_id:
            sensors = [
                sensor for sensor in sensors
                if not sensor.locations or any(location.id == location_id for location in sensor.locations)
            ]
        sensors_by_kind[kind] = sensors
    return sensors_by_kind


def _load_location_weather_sensor_series(location_id, lookback):
    sensors_by_kind = _location_weather_sensors(location_id)
    series = _empty_weather_sensor_series(sensors_by_kind)
    configured_sensors = [sensor for sensors in sensors_by_kind.values() for sensor in sensors]
    hints = []
    if not configured_sensors:
        return series, hints

    config = _sensor_influx_config()
    if not config.enabled:
        hints.append('InfluxDB ist nicht vollständig konfiguriert; Wetterdaten können nicht geladen werden.')
        return series, hints

    adapter = influx_service.get_sensor_time_series_adapter(config)
    stop = datetime.now(timezone.utc)
    start = stop - lookback
    errors = []
    for kind, sensors in sensors_by_kind.items():
        flat_points = []
        for sensor in sensors:
            sensor_points = []
            try:
                sensor_points = adapter.query_sensor(sensor, start, stop)
            except Exception as exc:  # pragma: no cover - concrete InfluxDB failures are integration-specific
                errors.append(f'{sensor.name}: {exc}')
            flat_points.extend(sensor_points)
            for sensor_series in series[kind]['series']:
                if sensor_series['sensor_id'] == sensor.id:
                    sensor_series['points'] = sensor_points
                    break
        series[kind]['points'] = sorted(flat_points, key=lambda point: point.get('time') or '')

    if errors:
        hints.append('InfluxDB-Fehler beim Laden der Wetterdaten: ' + '; '.join(errors))
    return series, hints


def _load_location_soil_moisture_series(location_id, lookback):
    sensors = _location_soil_moisture_sensors(location_id)
    series = _empty_soil_moisture_series(sensors)
    hints = []

    if not sensors:
        hints.append('Für dieses Beet sind keine Bodenfeuchte-Sensoren verknüpft.')
        return series, hints, sensors

    config = _sensor_influx_config()
    if not config.enabled:
        hints.append('InfluxDB ist nicht vollständig konfiguriert; es können keine Verlaufsdaten geladen werden.')
        return series, hints, sensors

    adapter = influx_service.get_sensor_time_series_adapter(config)
    stop = datetime.now(timezone.utc)
    start = stop - lookback
    errors = []
    for sensor, sensor_series in zip(sensors, series):
        try:
            sensor_series['points'] = adapter.query_sensor(sensor, start, stop)
        except Exception as exc:  # pragma: no cover - concrete InfluxDB failures are integration-specific
            errors.append(f'{sensor.name}: {exc}')

    if errors:
        hints.append('InfluxDB-Fehler beim Laden einzelner Sensoren: ' + '; '.join(errors))
    if not any(sensor_series['points'] for sensor_series in series):
        hints.append('Für den gewählten Zeitraum wurden keine Bodenfeuchte-Daten gefunden.')
    return series, hints, sensors

def _influx_service_config_from_db_or_app():
    influx_config = _first_influx_integration_config()
    if influx_config is None:
        return InfluxServiceConfig.from_app_config()
    return InfluxServiceConfig(
        url=(influx_config.influx_url or '').strip(),
        token=(influx_config.influx_token or '').strip(),
        org=(influx_config.influx_org or '').strip(),
        bucket=(influx_config.influx_bucket or '').strip(),
        timeout_seconds=influx_config.timeout_seconds,
        verify_tls=influx_config.verify_tls,
    )


def _numeric_sensor_value(raw_value):
    if isinstance(raw_value, bool):
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _format_soil_moisture_percent(value):
    if value is None:
        return None
    formatted = f'{value:.1f}'.rstrip('0').rstrip('.')
    return f'{formatted.replace(".", ",")} %'


def _serialize_soil_moisture_current_value(value, label, sensor_values):
    return {
        'value': value,
        'label': label,
        'sensor_values': [
            {
                'sensor_id': item['sensor'].id,
                'sensor_name': item['sensor'].name,
                'value': item['value'],
                'time': item['time'],
                'label': item['label'],
            }
            for item in sensor_values
        ],
    }


def _empty_sensor_current_value(label='Kein aktueller Messwert'):
    return {
        'value': None,
        'time': None,
        'label': label,
        'has_value': False,
    }


def _load_sensor_current_values(sensors):
    sensor_current_values = {
        sensor.id: _empty_sensor_current_value('Inaktiv' if not sensor.is_active else 'Kein aktueller Messwert')
        for sensor in sensors
    }
    active_sensors = [sensor for sensor in sensors if sensor.is_active]
    if not active_sensors:
        return sensor_current_values

    service_config = _influx_service_config_from_db_or_app()
    if not service_config.enabled:
        for sensor in active_sensors:
            sensor_current_values[sensor.id] = _empty_sensor_current_value('Keine InfluxDB-Konfiguration')
        return sensor_current_values

    adapter = influx_service.get_sensor_time_series_adapter(service_config)
    for sensor in active_sensors:
        datapoint = None
        value = None
        try:
            datapoint = latest_sensor_value(sensor, adapter=adapter)
            value = _numeric_sensor_value((datapoint or {}).get('value'))
        except Exception as exc:  # pragma: no cover - depends on external InfluxDB availability
            current_app.logger.info(
                'Aktueller Bodenfeuchtewert für Sensor %s konnte nicht geladen werden: %s',
                sensor.id,
                exc,
            )

        if value is not None:
            sensor_current_values[sensor.id] = {
                'value': value,
                'time': (datapoint or {}).get('time'),
                'label': _format_soil_moisture_percent(value),
                'has_value': True,
            }
        else:
            sensor_current_values[sensor.id] = _empty_sensor_current_value()
    return sensor_current_values


def _load_sensor_current_extremes():
    sensors = (
        Sensor.query
        .filter(Sensor.is_active.is_(True))
        .filter(Sensor.sensor_type == SENSOR_TYPE_SOIL_MOISTURE)
        .order_by(Sensor.name.asc(), Sensor.id.asc())
        .all()
    )
    sensor_current_values = _load_sensor_current_values(sensors)
    valid_values = [
        {
            'sensor': sensor,
            **sensor_current_values[sensor.id],
        }
        for sensor in sensors
        if sensor_current_values[sensor.id]['has_value']
    ]
    if not valid_values:
        return []
    if len(valid_values) == 1:
        return [{'kind': 'Aktuell', **valid_values[0]}]
    return [
        {'kind': 'Maximum', **max(valid_values, key=lambda item: (item['value'], item['sensor'].name, item['sensor'].id))},
        {'kind': 'Minimum', **min(valid_values, key=lambda item: (item['value'], item['sensor'].name, item['sensor'].id))},
    ]


def _soil_moisture_current_for_location(location):
    sensors = _location_soil_moisture_sensors(location.id)
    sensor_values = []
    if not sensors:
        return None, 'Kein Bodenfeuchtesensor verknüpft.', sensor_values

    service_config = _influx_service_config_from_db_or_app()
    if not service_config.enabled:
        return None, 'Bodenfeuchte nicht verfügbar: InfluxDB ist nicht vollständig konfiguriert.', [
            {'sensor': sensor, 'value': None, 'time': None, 'label': 'Keine InfluxDB-Konfiguration'}
            for sensor in sensors
        ]

    adapter = influx_service.get_sensor_time_series_adapter(service_config)
    valid_values = []
    has_query_error = False
    for sensor in sensors:
        datapoint = None
        value = None
        try:
            datapoint = latest_sensor_value(sensor, adapter=adapter)
            value = _numeric_sensor_value((datapoint or {}).get('value'))
        except Exception as exc:  # pragma: no cover - depends on external InfluxDB availability
            has_query_error = True
            current_app.logger.info(
                'Bodenfeuchtewert für Sensor %s konnte nicht geladen werden: %s',
                sensor.id,
                exc,
            )

        if value is not None:
            valid_values.append(value)
        sensor_values.append({
            'sensor': sensor,
            'value': value,
            'time': (datapoint or {}).get('time'),
            'label': _format_soil_moisture_percent(value) if value is not None else 'Kein aktueller Messwert',
        })

    if valid_values:
        current_value = sum(valid_values) / len(valid_values)
        return current_value, _format_soil_moisture_percent(current_value), sensor_values
    if has_query_error:
        return None, 'Bodenfeuchte zurzeit nicht abrufbar.', sensor_values
    return None, 'Keine aktuellen Bodenfeuchte-Messwerte.', sensor_values

def _form_bool(name):
    return (request.form.get(name) or '').strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


def _form_int(name, default, minimum=None, maximum=None):
    raw_value = (request.form.get(name) or '').strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        raise ValueError(f'{name} muss eine ganze Zahl sein.')
    if minimum is not None and value < minimum:
        raise ValueError(f'{name} muss mindestens {minimum} sein.')
    if maximum is not None and value > maximum:
        raise ValueError(f'{name} darf höchstens {maximum} sein.')
    return value


@main_bp.route('/config')
@login_required
def config():
    user = current_user()
    garden_map = GardenMap.query.order_by(GardenMap.id.asc()).first()
    locations = Location.query.order_by(*location_sort_criteria()).all()
    influx_config = _first_influx_integration_config()
    return render_template(
        'config.html',
        user=user,
        garden_map=garden_map,
        locations=locations,
        influx_config=influx_config,
    )


@main_bp.route('/config/influx', methods=['POST'])
@login_required
def save_influx_config():
    influx_config = _ensure_influx_integration_config()

    if 'timeout_seconds' in request.form:
        try:
            influx_config.timeout_seconds = _form_int('timeout_seconds', default=10, minimum=1, maximum=300)
        except ValueError as error:
            flash(str(error), 'error')
            return redirect(url_for('main.config', _anchor='influxdb-config'))
        influx_config.verify_tls = _form_bool('verify_tls')

    influx_config.influx_url = (request.form.get('influx_url') or '').strip()
    influx_config.influx_org = (request.form.get('influx_org') or '').strip()
    influx_config.influx_bucket = (request.form.get('influx_bucket') or '').strip()
    if 'homeassistant_url' in request.form:
        influx_config.homeassistant_url = (request.form.get('homeassistant_url') or '').strip()
    influx_config.updated_at = utc_now()

    influx_token = (request.form.get('influx_token') or '').strip()
    if influx_token:
        influx_config.influx_token = influx_token

    homeassistant_token = (request.form.get('homeassistant_token') or '').strip()
    if homeassistant_token:
        influx_config.homeassistant_token = homeassistant_token

    db.session.commit()
    flash('InfluxDB-Konfiguration wurde gespeichert.', 'success')
    return redirect(url_for('main.config', _anchor='influxdb-config'))


@main_bp.route('/config/homeassistant', methods=['POST'])
@login_required
def save_homeassistant_config():
    influx_config = _ensure_influx_integration_config()
    influx_config.homeassistant_url = (request.form.get('homeassistant_url') or '').strip()
    influx_config.updated_at = utc_now()

    homeassistant_token = (request.form.get('homeassistant_token') or '').strip()
    if homeassistant_token:
        influx_config.homeassistant_token = homeassistant_token

    db.session.commit()
    flash('Homeassistant-Konfiguration wurde gespeichert.', 'success')
    return redirect(url_for('main.config', _anchor='homeassistant-config'))



@main_bp.route('/config/connection-options', methods=['POST'])
@login_required
def save_connection_options():
    influx_config = _ensure_influx_integration_config()
    try:
        influx_config.timeout_seconds = _form_int('timeout_seconds', default=10, minimum=1, maximum=300)
    except ValueError as error:
        flash(str(error), 'error')
        return redirect(url_for('main.config', _anchor='connection-options'))

    influx_config.verify_tls = _form_bool('verify_tls')
    influx_config.updated_at = utc_now()
    db.session.commit()
    flash('Verbindungsoptionen wurden gespeichert.', 'success')
    return redirect(url_for('main.config', _anchor='connection-options'))


@main_bp.route('/config/influx/test', methods=['POST'])
@login_required
def test_influx_connection():
    influx_config = _first_influx_integration_config()
    if influx_config is None:
        flash('InfluxDB ist nicht konfiguriert.', 'error')
        return redirect(url_for('main.config'))

    service_config = InfluxServiceConfig(
        url=(influx_config.influx_url or '').strip(),
        token=(influx_config.influx_token or '').strip(),
        org=(influx_config.influx_org or '').strip(),
        bucket=(influx_config.influx_bucket or '').strip(),
        timeout_seconds=influx_config.timeout_seconds,
        verify_tls=influx_config.verify_tls,
    )
    health = FluxInfluxQueryAdapter(service_config).health()
    flash(health['message'], 'success' if health.get('ok') else 'error')
    return redirect(url_for('main.config'))


@main_bp.route('/config/homeassistant/test', methods=['POST'])
@login_required
def test_homeassistant_connection():
    influx_config = _first_influx_integration_config()
    if influx_config is None or not (influx_config.homeassistant_url or '').strip():
        flash('Homeassistant ist nicht konfiguriert.', 'error')
        return redirect(url_for('main.config'))

    headers = {}
    homeassistant_token = (influx_config.homeassistant_token or '').strip()
    if homeassistant_token:
        headers['Authorization'] = f'Bearer {homeassistant_token}'

    api_url = urljoin(influx_config.homeassistant_url.rstrip('/') + '/', 'api/')
    try:
        response = requests.get(
            api_url,
            headers=headers,
            timeout=influx_config.timeout_seconds,
            verify=influx_config.verify_tls,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        flash(f'Homeassistant-Verbindung fehlgeschlagen: {error}', 'error')
    else:
        flash('Homeassistant ist erreichbar.', 'success')
    return redirect(url_for('main.config'))


@main_bp.route('/admin')
@login_required
def admin():
    return render_template(
        'admin.html',
        user=current_user(),
        upload_folders=_build_upload_folder_report(),
        database=_database_size_info(),
        backups=_backup_report(),
        version_info=_app_version_info(),
        environment_variables=_environment_variable_report(),
    )


@main_bp.route('/admin/backup/create', methods=['POST'])
@login_required
def create_backup():
    try:
        backup = _create_sqlite_backup()
        flash(f'Backup „{backup["filename"]}“ wurde erstellt.', 'success')
    except RuntimeError as exc:
        flash(str(exc), 'error')
    except OSError:
        flash('Backup konnte nicht erstellt werden.', 'error')
    return redirect(url_for('main.admin'))


def _safe_orphan_target(folder_path, filename):
    if not folder_path:
        return None
    folder_abs = os.path.abspath(folder_path)
    target = os.path.abspath(os.path.join(folder_abs, filename))
    if os.path.commonpath([folder_abs, target]) != folder_abs:
        return None
    return target


@main_bp.route('/admin/backup/restore', methods=['POST'])
@login_required
def restore_backup():
    filename = (request.form.get('filename') or '').strip()
    try:
        _restore_sqlite_backup(filename)
        flash(f'Backup „{filename}“ wurde wiederhergestellt.', 'success')
    except RuntimeError as exc:
        flash(str(exc), 'error')
    except OSError:
        flash('Backup konnte nicht wiederhergestellt werden.', 'error')
    return redirect(url_for('main.admin'))


@main_bp.route('/admin/backup/delete', methods=['POST'])
@login_required
def delete_backup():
    filename = (request.form.get('filename') or '').strip()
    try:
        _delete_backup(filename)
        flash(f'Backup „{filename}“ wurde gelöscht.', 'success')
    except RuntimeError as exc:
        flash(str(exc), 'error')
    except OSError:
        flash('Backup konnte nicht gelöscht werden.', 'error')
    return redirect(url_for('main.admin'))


@main_bp.route('/admin/orphan-upload/delete', methods=['POST'])
@login_required
def delete_orphan_upload():
    folder_key = (request.form.get('folder_key') or '').strip()
    filename = (request.form.get('filename') or '').strip()
    folder = _resolve_folder_by_key(folder_key)
    if not folder or not filename:
        flash('Datei konnte nicht gelöscht werden: ungültige Anfrage.', 'error')
        return redirect(url_for('main.admin'))

    if not _is_orphan_upload_file(folder_key, filename):
        flash('Datei wurde nicht gelöscht, weil sie nicht als verwaist erkannt wurde.', 'warning')
        return redirect(url_for('main.admin'))

    target = _safe_orphan_target(folder['path'], filename)
    if not target:
        flash('Datei konnte nicht gelöscht werden: ungültiger Pfad.', 'error')
        return redirect(url_for('main.admin'))

    try:
        if os.path.isfile(target):
            os.remove(target)
            flash(f'Verwaiste Datei „{filename}“ wurde gelöscht.', 'success')
        else:
            flash('Datei existiert nicht mehr.', 'warning')
    except OSError:
        flash('Datei konnte nicht gelöscht werden.', 'error')
    return redirect(url_for('main.admin'))


@main_bp.route('/admin/orphan-uploads/delete-all', methods=['POST'])
@login_required
def delete_all_orphan_uploads():
    folder_key = (request.form.get('folder_key') or '').strip()
    folder = _resolve_folder_by_key(folder_key)
    if not folder:
        flash('Verwaiste Dateien konnten nicht gelöscht werden: ungültige Anfrage.', 'error')
        return redirect(url_for('main.admin'))

    report = _build_upload_folder_report()
    folder_report = next((item for item in report if item['key'] == folder_key), None)
    orphan_files = folder_report['orphan_files'] if folder_report else []
    deleted_count = 0
    failed_count = 0

    for orphan_file in orphan_files:
        filename = orphan_file['filename']
        target = _safe_orphan_target(folder['path'], filename)
        if not target:
            failed_count += 1
            continue
        try:
            if os.path.isfile(target):
                os.remove(target)
                deleted_count += 1
        except OSError:
            failed_count += 1

    if deleted_count and not failed_count:
        flash(f'{deleted_count} verwaiste Upload-Dateien in „{folder["label"]}“ wurden gelöscht.', 'success')
    elif deleted_count:
        flash(f'{deleted_count} verwaiste Upload-Dateien wurden gelöscht, {failed_count} konnten nicht gelöscht werden.', 'warning')
    elif failed_count:
        flash('Verwaiste Upload-Dateien konnten nicht gelöscht werden.', 'error')
    else:
        flash(f'Keine verwaisten Upload-Dateien in „{folder["label"]}“ gefunden.', 'info')
    return redirect(url_for('main.admin'))


@main_bp.route('/admin/export')
@login_required
def export_data():
    payload = _build_data_export_payload()
    export_time = utc_now().strftime('%Y%m%d-%H%M%S')
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            'garden-export.json',
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        db_path = _database_file_path()
        if db_path and os.path.isfile(db_path):
            archive.write(db_path, arcname=os.path.basename(db_path))
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'garden-export-{export_time}.zip',
    )

@main_bp.route('/locations/new', methods=['POST'])
@login_required
def new_location():
    loc = Location(name=request.form['name'], description=request.form.get('description'), color=request.form.get('color') or '#2f6d40')
    db.session.add(loc)
    db.session.commit()
    return redirect(url_for('main.index'))

@main_bp.route('/locations/<int:location_id>')
@login_required
def location_detail(location_id):
    loc = session_get_or_404(Location, location_id)
    plants = Plant.query.filter_by(location_id=loc.id).order_by(Plant.name.asc()).all()
    plant_ids = [plant.id for plant in plants]
    plant_title_images_by_id = {}
    if plant_ids:
        title_events = (
            TimelineEntry.query
            .filter(
                TimelineEntry.scope_type == 'plant',
                TimelineEntry.scope_id.in_(plant_ids),
                TimelineEntry.is_title_entry.is_(True),
                TimelineEntry.attachment_kind == 'image',
                TimelineEntry.attachment_filename.isnot(None),
            )
            .all()
        )
        plant_title_images_by_id = {
            (event.scope_id if hasattr(event, 'scope_id') else event.plant_id): event.attachment_filename
            for event in title_events
            if event.attachment_filename
        }
    timeline_entries = (
        TimelineEntry.query
        .filter_by(scope_type='location', scope_id=loc.id)
        .order_by(TimelineEntry.created_at.desc())
        .all()
    )
    location_plant_markers = [
        {'id': plant.id, 'name': plant.name, 'map_x': plant.map_x, 'map_y': plant.map_y}
        for plant in plants
    ]
    garden_map = GardenMap.query.order_by(GardenMap.id.asc()).first()
    other_locations = Location.query.filter(Location.id != loc.id).order_by(*location_sort_criteria()).all()
    soil_moisture_range_key, _soil_moisture_lookback = _selected_soil_moisture_range()
    soil_moisture_sensors = _location_soil_moisture_sensors(loc.id)
    weather_sensors_by_kind = _location_weather_sensors(loc.id)
    weather_series = _empty_weather_sensor_series(weather_sensors_by_kind)
    show_moisture_history = bool(soil_moisture_sensors) or any(item['configured'] for item in weather_series.values())
    soil_moisture_series = _empty_soil_moisture_series(soil_moisture_sensors)
    return render_template(
        'location.html',
        location=loc,
        plants=plants,
        plant_title_images_by_id=plant_title_images_by_id,
        timeline_entries=timeline_entries,
        location_plant_markers=location_plant_markers,
        user=current_user(),
        creators={u.id: u for u in User.query.all()},
        garden_map=garden_map,
        light_need_options=LIGHT_NEED_OPTIONS,
        flower_color_suggestions=get_flower_color_suggestions(),
        source_suggestions=get_source_suggestions(),
        top_soil_properties=get_top_soil_property_labels(),
        soil_property_suggestions=SoilProperty.query.order_by(SoilProperty.label.asc()).all(),
        soil_moisture_series=soil_moisture_series,
        weather_series=weather_series,
        soil_moisture_hints=['Bodenfeuchte-Daten werden im Hintergrund geladen.'] if show_moisture_history else [],
        soil_moisture_sensors=soil_moisture_sensors,
        show_moisture_history=show_moisture_history,
        soil_moisture_range_key=soil_moisture_range_key,
        soil_moisture_range_options=SOIL_MOISTURE_RANGE_OPTIONS,
        soil_moisture_current=None,
        soil_moisture_current_label=None,
        soil_moisture_sensor_values=[],
        other_location_polygons=[
            {
                'id': other_loc.id,
                'name': other_loc.name,
                'color': other_loc.color or '#2f6d40',
                'polygon_points': parse_stored_points(other_loc.polygon_points),
            }
            for other_loc in other_locations
        ],
    )


@main_bp.route('/locations/<int:location_id>/soil-moisture')
@login_required
def location_soil_moisture_data(location_id):
    loc = session_get_or_404(Location, location_id)
    soil_moisture_range_key, soil_moisture_lookback = _selected_soil_moisture_range()
    soil_moisture_series, soil_moisture_hints, soil_moisture_sensors = _load_location_soil_moisture_series(
        loc.id,
        soil_moisture_lookback,
    )
    weather_series, weather_hints = _load_location_weather_sensor_series(loc.id, soil_moisture_lookback)
    soil_moisture_hints.extend(weather_hints)
    soil_moisture_current, soil_moisture_current_label, soil_moisture_sensor_values = _soil_moisture_current_for_location(loc)

    return jsonify({
        'range_key': soil_moisture_range_key,
        'series': soil_moisture_series,
        'weather_series': weather_series,
        'hints': soil_moisture_hints,
        'sensors': [
            {'sensor_id': sensor.id, 'name': sensor.name}
            for sensor in soil_moisture_sensors
        ],
        'has_series_data': any(sensor_series['points'] for sensor_series in soil_moisture_series) or any(
            weather_item.get('points') for weather_item in weather_series.values()
        ),
        'current': _serialize_soil_moisture_current_value(
            soil_moisture_current,
            soil_moisture_current_label,
            soil_moisture_sensor_values,
        ),
    })


@main_bp.route('/locations/<int:location_id>/timeline/new', methods=['POST'])
@login_required
def new_location_timeline_entry(location_id):
    location = session_get_or_404(Location, location_id)
    title = (request.form.get('title') or '').strip()
    description = (request.form.get('description') or '').strip()
    attachment = request.files.get('attachment')

    unique, upload_error = save_uploaded_attachment(
        attachment,
        current_app.config['UPLOAD_FOLDER'],
        ALLOWED,
        ALLOWED_ATTACHMENT_MIME_TYPES,
        current_app.config.get('MAX_ATTACHMENT_SIZE_BYTES'),
    )
    if upload_error == 'too_large':
        flash('Datei zu groß (max. 15 MB).', 'error')
        return redirect(url_for('main.location_detail', location_id=location.id))
    if upload_error == 'mime_not_allowed':
        flash('Dateityp nicht erlaubt. Bitte Bild oder PDF hochladen.', 'error')
        return redirect(url_for('main.location_detail', location_id=location.id))
    if upload_error == 'extension_not_allowed':
        flash('Dateiendung nicht erlaubt. Bitte Bild oder PDF hochladen.', 'error')
        return redirect(url_for('main.location_detail', location_id=location.id))
    attachment_kind = attachment_kind_for_filename(unique)

    if not timeline_entry_has_content(title, description, unique):
        flash('Bitte Titel, Beschreibung oder Datei angeben.', 'warning')
        return redirect(url_for('main.location_detail', location_id=location.id))

    create_timeline_entry(
        scope_type='location',
        scope_id=location.id,
        title=title or None,
        description=description or None,
        attachment_filename=unique,
        attachment_kind=attachment_kind,
        creator_id=current_user().id,
    )
    db.session.commit()
    return redirect(url_for('main.location_detail', location_id=location.id))



@main_bp.route('/locations/<int:location_id>/timeline/<int:entry_id>/set-title', methods=['POST'])
@login_required
def set_location_timeline_title(location_id, entry_id):
    location = session_get_or_404(Location, location_id)
    set_single_title_entry(
        model=TimelineEntry,
        owner_filter=(TimelineEntry.scope_type == 'location', TimelineEntry.scope_id == location.id),
        entry_id_field=TimelineEntry.id,
        entry_id_value=entry_id,
    )
    db.session.commit()
    return redirect(url_for('main.location_detail', location_id=location.id))


@main_bp.route('/locations/<int:location_id>/timeline/<int:entry_id>/delete', methods=['POST'])
@login_required
def delete_location_timeline_entry(location_id, entry_id):
    location = session_get_or_404(Location, location_id)
    entry = TimelineEntry.query.filter_by(id=entry_id, scope_type='location', scope_id=location.id).first_or_404()
    delete_timeline_entry(entry, current_app.config['UPLOAD_FOLDER'], ('attachment_filename',))
    db.session.delete(entry)
    db.session.commit()
    return redirect(url_for('main.location_detail', location_id=location.id))

@main_bp.route('/locations/<int:location_id>/plants/new', methods=['POST'])
@login_required
def new_plant(location_id):
    light_need_keys = parse_light_need_keys(request.form.getlist('light_need'))
    selected_light_needs = LightNeed.query.filter(LightNeed.key.in_(light_need_keys)).order_by(LightNeed.id.asc()).all()
    bloom_start_month, bloom_end_month, bloom_months_valid = parse_bloom_months(request.form)
    if not bloom_months_valid:
        flash('Bitte beide Monate für die Blütezeit angeben oder beide leer lassen.', 'warning')
        return redirect(url_for('main.location_detail', location_id=location_id))

    name = (request.form.get('plant_name') or request.form.get('name') or '').strip()
    if not name:
        flash('Bitte einen Pflanzennamen angeben.', 'warning')
        return redirect(url_for('main.location_detail', location_id=location_id))

    p = Plant(
        location_id=location_id,
        name=name,
        cultivar=request.form.get('cultivar'),
        scientific_name=request.form.get('scientific_name'),
        common_name=request.form.get('common_name'),
        source=request.form.get('source'),
        bloom_start_month=bloom_start_month,
        bloom_end_month=bloom_end_month,
        flower_color=request.form.get('flower_color'),
        height_without_bloom_cm=request.form.get('height_without_bloom_cm', type=int),
        height_with_bloom_cm=request.form.get('height_with_bloom_cm', type=int),
        info=request.form.get('info'),
        creator_id=current_user().id
    )
    p.light_needs = selected_light_needs
    soil_labels = parse_soil_properties(request.form.get('soil_properties'))
    p.soil_properties = get_or_create_soil_properties(soil_labels)
    db.session.add(p)
    db.session.flush()
    upsert_plant_database_identifiers(p, request.form)
    event_at = utc_now()
    tpl = SYSTEM_EVENT_TEMPLATES['planting']
    create_timeline_entry(
        scope_type='plant',
        scope_id=p.id,
        event_at=event_at,
        event_type='plant_event',
        title=tpl['title'],
        description=tpl['description'],
        creator_id=current_user().id
    )
    db.session.commit()
    return redirect(url_for('main.plant_detail', plant_id=p.id, edit=1))

@main_bp.route('/locations/<int:location_id>/delete', methods=['POST'])
@login_required
def delete_location(location_id):
    location = session_get_or_404(Location, location_id)
    if location.name == TRASH_LOCATION_NAME:
        return redirect(url_for('main.index'))
    trash = get_or_create_trash_location()
    if location.id == trash.id:
        return redirect(url_for('main.index'))
    plants = Plant.query.filter_by(location_id=location.id).all()
    for plant in plants:
        plant.location_id = trash.id
    # Location nur entfernen, wenn keine abhängigen Timeline-Einträge existieren.
    has_timeline_entries = TimelineEntry.query.filter_by(scope_type='location', scope_id=location.id).first() is not None
    if not has_timeline_entries:
        db.session.delete(location)
    db.session.commit()
    return redirect(url_for('main.index'))


@main_bp.route('/sensors')
@login_required
def sensors():
    selected_location_id = request.args.get('location_id', type=int)
    sensors = Sensor.query.order_by(Sensor.name.asc(), Sensor.id.asc()).all()
    sensor_current_values = _load_sensor_current_values(sensors)
    locations = Location.query.order_by(*location_sort_criteria()).all()
    selected_location = db.session.get(Location, selected_location_id) if selected_location_id else None
    return render_template(
        'sensors.html',
        sensors=sensors,
        sensor_type_labels=SENSOR_TYPE_LABELS,
        sensor_types=SENSOR_TYPES,
        sensor_current_values=sensor_current_values,
        locations=locations,
        selected_location=selected_location,
        selected_location_id=selected_location.id if selected_location else selected_location_id,
        garden_map=GardenMap.query.order_by(GardenMap.id.asc()).first(),
        user=current_user(),
    )


@main_bp.route('/sensors/new', methods=['POST'])
@login_required
def new_sensor():
    sensor = Sensor(creator_id=current_user().id, key='pending')
    is_valid, error_message = apply_sensor_form(sensor, request.form)
    if not is_valid:
        flash(error_message, 'warning')
        return redirect(request.referrer or url_for('main.sensors'))
    db.session.add(sensor)
    db.session.commit()
    flash(f'Sensor „{sensor.name}“ wurde angelegt.', 'success')
    return redirect(url_for('main.sensor_detail', sensor_id=sensor.id))


@main_bp.route('/sensors/<int:sensor_id>')
@login_required
def sensor_detail(sensor_id):
    sensor = session_get_or_404(Sensor, sensor_id)
    locations = Location.query.order_by(*location_sort_criteria()).all()
    return render_template(
        'sensor.html',
        sensor=sensor,
        locations=locations,
        selected_location_ids={location.id for location in sensor.locations},
        sensor_type_labels=SENSOR_TYPE_LABELS,
        sensor_types=SENSOR_TYPES,
        garden_map=GardenMap.query.order_by(GardenMap.id.asc()).first(),
        user=current_user(),
    )


@main_bp.route('/sensors/<int:sensor_id>/edit', methods=['POST'])
@login_required
def edit_sensor(sensor_id):
    sensor = session_get_or_404(Sensor, sensor_id)
    is_valid, error_message = apply_sensor_form(sensor, request.form)
    if not is_valid:
        flash(error_message, 'warning')
        return redirect(url_for('main.sensor_detail', sensor_id=sensor.id))
    db.session.commit()
    flash(f'Sensor „{sensor.name}“ wurde gespeichert.', 'success')
    return redirect(url_for('main.sensor_detail', sensor_id=sensor.id))


@main_bp.route('/sensors/<int:sensor_id>/influx/test', methods=['POST'])
@login_required
def test_sensor_influx_value(sensor_id):
    sensor = session_get_or_404(Sensor, sensor_id)
    config = _sensor_influx_config()
    if not config.enabled:
        flash('InfluxDB ist nicht vollständig konfiguriert; der letzte Sensorwert kann nicht abgerufen werden.', 'warning')
        return redirect(url_for('main.sensor_detail', sensor_id=sensor.id))

    try:
        adapter = influx_service.get_sensor_time_series_adapter(config)
        datapoint = latest_sensor_value(sensor, adapter=adapter)
    except Exception as exc:  # pragma: no cover - concrete InfluxDB failures are integration-specific
        flash(f'InfluxDB-Test fehlgeschlagen: {exc}', 'error')
        return redirect(url_for('main.sensor_detail', sensor_id=sensor.id))

    if not datapoint:
        flash('InfluxDB-Test erfolgreich, aber im Suchzeitraum wurde kein Wert gefunden.', 'warning')
        return redirect(url_for('main.sensor_detail', sensor_id=sensor.id))

    value = datapoint.get('value')
    timestamp = datapoint.get('time') or 'Zeitpunkt unbekannt'
    flash(f'Letzter Influx-Wert: {value} ({timestamp})', 'success')
    return redirect(url_for('main.sensor_detail', sensor_id=sensor.id))


@main_bp.route('/sensors/<int:sensor_id>/delete', methods=['POST'])
@login_required
def delete_sensor(sensor_id):
    sensor = session_get_or_404(Sensor, sensor_id)
    db.session.delete(sensor)
    db.session.commit()
    flash('Sensor wurde gelöscht.', 'success')
    return redirect(url_for('main.sensors'))


@main_bp.route('/plants/<int:plant_id>')
@login_required
def plant_detail(plant_id):
    plant = session_get_or_404(Plant, plant_id)
    events = TimelineEntry.query.filter_by(scope_type='plant', scope_id=plant.id).order_by(TimelineEntry.event_at.desc(), TimelineEntry.created_at.desc()).all()
    photos = PlantPhoto.query.filter_by(plant_id=plant.id).order_by(PlantPhoto.uploaded_at.desc()).all()
    notes = PlantNote.query.filter_by(plant_id=plant.id).order_by(PlantNote.created_at.desc()).all()
    month_names = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez']
    last_plant_event = next((ev for ev in events if ev.event_type == 'plant_event' and ev.title in PLANTING_STATE_TYPES), None)
    is_planted = bool(last_plant_event and PLANTING_STATE_TYPES[last_plant_event.title] in {'planting', 'transplant'})
    location = db.session.get(Location, plant.location_id)
    garden_map = GardenMap.query.order_by(GardenMap.id.asc()).first()
    location_plants = Plant.query.filter_by(location_id=plant.location_id).order_by(Plant.name.asc()).all()
    location_plant_markers = [
        {'id': item.id, 'name': item.name, 'map_x': item.map_x, 'map_y': item.map_y}
        for item in location_plants
    ]
    title_event = next((event for event in events if event.is_title_entry), None)
    assigned_soil_property_ids = [soil_property.id for soil_property in plant.soil_properties]
    top_soil_properties = get_top_soil_property_labels(assigned_soil_property_ids)
    soil_property_suggestions = SoilProperty.query.order_by(SoilProperty.label.asc()).all()
    database_catalogs = get_catalog_configs()
    database_search_query = plant.scientific_name or plant.name
    return render_template(
        'plant.html',
        plant=plant,
        location=location,
        events=events,
        photos=photos,
        notes=notes,
        user=current_user(),
        locations=Location.query.order_by(*location_sort_criteria()).all(),
        creators={u.id: u for u in User.query.all()},
        today_date=utc_now().date().isoformat(),
        month_names=month_names,
        is_planted=is_planted,
        garden_map=garden_map,
        location_plant_markers=location_plant_markers,
        title_event=title_event,
        light_need_options=LIGHT_NEED_OPTIONS,
        light_need_icon_by_key=LIGHT_NEED_ICON_BY_KEY,
        top_soil_properties=top_soil_properties,
        soil_property_suggestions=soil_property_suggestions,
        flower_color_suggestions=get_flower_color_suggestions(),
        source_suggestions=get_source_suggestions(),
        database_links=_build_database_links_for_plant(plant),
        database_catalogs=database_catalogs,
        database_identifier_values=_build_database_identifier_values(plant),
        database_search_query=database_search_query,
        database_search_urls=_build_database_search_urls(database_catalogs, database_search_query),
        debug_enabled=full_debug_enabled(),
    )


@main_bp.route('/maps/<path:filename>')
@login_required
def maps(filename):
    return send_from_directory(current_app.config['MAP_FOLDER'], filename)


@main_bp.route('/map/upload', methods=['POST'])
@login_required
def upload_map():
    file = request.files.get('map_image')
    unique, upload_error = save_uploaded_attachment(
        file,
        current_app.config['MAP_FOLDER'],
        MAP_IMAGE_ALLOWED,
        MAP_IMAGE_MIME_TYPES,
        current_app.config.get('MAX_ATTACHMENT_SIZE_BYTES'),
        require_image_content=True,
    )
    if upload_error:
        flash(map_image_upload_error_message(upload_error), 'error')
        return redirect(request.referrer or url_for('main.config'))
    if not unique:
        flash('Bitte eine Luftbild-Datei auswählen.', 'warning')
        return redirect(request.referrer or url_for('main.config'))

    garden_map = get_or_create_garden_map()
    garden_map.filename = unique
    db.session.commit()
    flash('Luftbild wurde hochgeladen.', 'success')
    return redirect(request.referrer or url_for('main.index'))


@main_bp.route('/map/calibration', methods=['POST'])
@login_required
def save_calibration():
    try:
        payload = validate_calibration_points(request.form.get('calibration_points', '[]'))
    except MapPointValidationError as exc:
        return str(exc), 400
    garden_map = get_or_create_garden_map()
    garden_map.calibration_points = payload
    db.session.commit()
    return redirect(request.referrer or url_for('main.index'))


@main_bp.route('/map/boundary', methods=['POST'])
@login_required
def save_boundary():
    try:
        payload = validate_polygon_points(request.form.get('boundary_points', '[]'), 'Grundstücksgrenze')
    except MapPointValidationError as exc:
        return str(exc), 400
    garden_map = get_or_create_garden_map()
    garden_map.boundary_points = payload
    db.session.commit()
    return redirect(request.referrer or url_for('main.config'))


@main_bp.route('/locations/<int:location_id>/map', methods=['POST'])
@login_required
def save_location_map(location_id):
    loc = session_get_or_404(Location, location_id)
    try:
        polygon_points = validate_polygon_points(request.form.get('polygon_points', '[]'), 'Beet-Polygonpunkte')
    except MapPointValidationError as exc:
        return str(exc), 400
    loc.color = request.form.get('color') or '#2f6d40'
    loc.polygon_points = polygon_points
    db.session.commit()
    return redirect(url_for('main.location_detail', location_id=location_id))


@main_bp.route('/locations/<int:location_id>/color', methods=['POST'])
@login_required
def save_location_color(location_id):
    loc = session_get_or_404(Location, location_id)
    if loc.name == TRASH_LOCATION_NAME:
        return redirect(request.referrer or url_for('main.index'))
    loc.color = request.form.get('color') or '#2f6d40'
    db.session.commit()
    return redirect(request.referrer or url_for('main.index'))


@main_bp.route('/plants/<int:plant_id>/position', methods=['POST'])
@login_required
def save_plant_position(plant_id):
    plant = session_get_or_404(Plant, plant_id)
    is_json_request = request.is_json
    payload = request.get_json(silent=True) if is_json_request else None
    try:
        map_x_raw = (payload or {}).get('map_x') if is_json_request else request.form.get('map_x')
        map_y_raw = (payload or {}).get('map_y') if is_json_request else request.form.get('map_y')

        map_x = float(map_x_raw) if map_x_raw not in (None, '') else None
        map_y = float(map_y_raw) if map_y_raw not in (None, '') else None
    except (TypeError, ValueError):
        if is_json_request:
            return jsonify({'ok': False, 'error': 'Ungültige Koordinaten'}), 400
        return redirect(url_for('main.plant_detail', plant_id=plant_id))

    if map_x is not None and not -90 <= map_x <= 90:
        if is_json_request:
            return jsonify({'ok': False, 'error': 'Breitengrad muss zwischen -90 und 90 liegen'}), 400
        return redirect(url_for('main.plant_detail', plant_id=plant_id))
    if map_y is not None and not -180 <= map_y <= 180:
        if is_json_request:
            return jsonify({'ok': False, 'error': 'Längengrad muss zwischen -180 und 180 liegen'}), 400
        return redirect(url_for('main.plant_detail', plant_id=plant_id))

    plant.map_x = map_x
    plant.map_y = map_y
    db.session.commit()
    if is_json_request:
        return jsonify({'ok': True, 'map_x': plant.map_x, 'map_y': plant.map_y})
    return redirect(url_for('main.plant_detail', plant_id=plant_id))





@main_bp.route('/plants/<int:plant_id>/common-name-suggest', methods=['POST'])
@login_required
def suggest_common_name(plant_id):
    plant = session_get_or_404(Plant, plant_id)
    started_at = time.perf_counter()
    payload = request.get_json(silent=True) or {}
    name_value = (payload.get('name') or plant.name or '').strip()
    naturadb_id = (payload.get('naturadb_id') or '').strip()
    wikipedia_id = (payload.get('wikipedia_id') or '').strip()
    trace_id = f"magic-common-{plant_id}-{int(time.time() * 1000)}"
    if not name_value:
        current_app.logger.info('[%s] common-name lookup aborted: missing source name', trace_id)
        return jsonify({'ok': False, 'error': 'Bitte zuerst einen Namen eingeben.', 'debug': _debug_payload(trace_id)}), 400

    lookup_language = current_app.config.get('COMMON_NAME_LOOKUP_LANG', 'de')
    common_name, sources = _lookup_common_name_from_web(
        name_value,
        language_code=lookup_language,
        naturadb_id=naturadb_id,
        wikipedia_id=wikipedia_id,
    )
    if not common_name:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
        current_app.logger.info('[%s] common-name lookup failed for "%s" (%sms, sources=%s)', trace_id, name_value, duration_ms, len(sources or []))
        return jsonify({'ok': False, 'error': 'Kein Vorschlag gefunden.', 'debug': _debug_payload(trace_id, duration_ms)}), 404

    confidence = 0.88 if common_name.lower() != name_value.lower() else 0.55
    duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
    current_app.logger.info('[%s] common-name lookup success for "%s" -> "%s" (%sms, sources=%s)', trace_id, name_value, common_name, duration_ms, len(sources or []))
    return jsonify({'ok': True, 'common_name': common_name, 'confidence': confidence, 'sources': sources, 'language': lookup_language, 'debug': _debug_payload(trace_id, duration_ms)})


def upsert_plant_database_identifiers(plant, form):
    catalog_by_key = {catalog.key: catalog for catalog in get_catalog_configs()}
    desired_values = {
        catalog_key: _normalize_database_identifier_for_catalog(catalog_key, form.get(f'database_id_{catalog_key}'))
        for catalog_key in catalog_by_key.keys()
    }

    existing_by_key = {entry.catalog.key: entry for entry in plant.database_identifiers if entry.catalog}
    new_entries = []
    for catalog_key, catalog in catalog_by_key.items():
        desired = desired_values.get(catalog_key, '')
        existing_entry = existing_by_key.get(catalog_key)
        if not desired:
            continue
        if existing_entry and existing_entry.taxonomy_id == desired:
            new_entries.append(existing_entry)
            continue
        matched = PlantDatabaseIdentifier.query.filter_by(plant_id=plant.id, catalog_key=catalog.key).first()
        if matched:
            matched.taxonomy_id = desired
            new_entries.append(matched)
        else:
            created = PlantDatabaseIdentifier(plant_id=plant.id, catalog_key=catalog.key, taxonomy_id=desired)
            db.session.add(created)
            db.session.flush()
            new_entries.append(created)
    plant.database_identifiers = new_entries




@main_bp.route('/plants/<int:plant_id>/taxonomy-ids-suggest', methods=['POST'])
@login_required
def suggest_taxonomy_ids(plant_id):
    plant = session_get_or_404(Plant, plant_id)
    started_at = time.perf_counter()
    payload = request.get_json(silent=True) or {}
    scientific_name = (payload.get('scientific_name') or plant.scientific_name or plant.name or '').strip()
    trace_id = f"magic-taxonomy-{plant_id}-{int(time.time() * 1000)}"
    if not scientific_name:
        current_app.logger.info('[%s] taxonomy lookup aborted: missing scientific name', trace_id)
        return jsonify({'ok': False, 'error': 'Bitte zuerst einen wissenschaftlichen Namen eingeben.', 'debug': {'trace_id': trace_id}}), 400

    catalog_key = (payload.get('catalog_key') or '').strip()
    catalogs = get_catalog_configs()
    if catalog_key:
        catalog = next((item for item in catalogs if item.key == catalog_key), None)
        if catalog is None:
            return jsonify({
                'ok': False,
                'error': f'Der Katalog "{catalog_key}" existiert nicht.',
                'debug': {'trace_id': trace_id},
            }), 404
        if not catalog.enabled:
            return jsonify({
                'ok': False,
                'error': f'Der Katalog "{catalog.label}" ist deaktiviert.',
                'debug': {'trace_id': trace_id},
            }), 400
        suggestion = taxonomy_service.suggest_for_catalog(scientific_name, catalog)
    else:
        enabled_catalogs = [catalog for catalog in catalogs if catalog.enabled]
        suggestion = taxonomy_service.suggest_for_all_enabled(scientific_name, enabled_catalogs)
    duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
    current_app.logger.info(
        '[%s] taxonomy lookup for "%s" (%sms): %s hits, unavailable=%s',
        trace_id,
        scientific_name,
        duration_ms,
        len(suggestion.matches),
        ','.join(suggestion.unavailable_catalogs) or '-',
    )
    return jsonify(suggestion.to_response(trace_id=trace_id, duration_ms=duration_ms))

@main_bp.route('/plants/<int:plant_id>/masterdata', methods=['POST'])
@login_required
def update_masterdata(plant_id):
    plant = session_get_or_404(Plant, plant_id)

    field_labels = {
        'name': 'Name',
        'common_name': 'Bürgerlicher Name',
        'cultivar': 'Sorte/Kultivar',
        'scientific_name': 'Wissenschaftlicher Name',
        'source': 'Quelle',
        'light_need': 'Lichtbedarf',
        'bloom_start_month': 'Blütezeit von',
        'bloom_end_month': 'Blütezeit bis',
        'flower_color': 'Blütenfarbe',
        'height_without_bloom_cm': 'Höhe ohne Blüte (cm)',
        'height_with_bloom_cm': 'Höhe mit Blüte (cm)',
        'info': 'Info',
        'map_x': 'Breitengrad',
        'map_y': 'Längengrad',
    }

    bloom_start_month, bloom_end_month, bloom_months_valid = parse_bloom_months(request.form)
    if not bloom_months_valid:
        flash('Bitte beide Monate für die Blütezeit angeben oder beide leer lassen.', 'warning')
        return redirect(url_for('main.plant_detail', plant_id=plant.id))

    updates = {
        'name': request.form.get('name', '').strip(),
        'cultivar': request.form.get('cultivar', '').strip() or None,
        'scientific_name': request.form.get('scientific_name', '').strip() or None,
        'common_name': request.form.get('common_name', '').strip() or None,
        'source': request.form.get('source', '').strip() or None,
        'bloom_start_month': bloom_start_month,
        'bloom_end_month': bloom_end_month,
        'flower_color': request.form.get('flower_color', '').strip() or None,
        'height_without_bloom_cm': request.form.get('height_without_bloom_cm', type=int),
        'height_with_bloom_cm': request.form.get('height_with_bloom_cm', type=int),
        'info': request.form.get('info', '').strip() or None,
        'map_x': request.form.get('map_x', type=float),
        'map_y': request.form.get('map_y', type=float),
    }

    changes = []
    for field, new_value in updates.items():
        old_value = getattr(plant, field)
        if old_value != new_value:
            old_display = old_value if old_value not in (None, '') else '-'
            new_display = new_value if new_value not in (None, '') else '-'
            changes.append(f"{field_labels[field]}: {old_display} → {new_display}")
            setattr(plant, field, new_value)

    light_need_keys = parse_light_need_keys(request.form.getlist('light_need'))
    new_light_needs = LightNeed.query.filter(LightNeed.key.in_(light_need_keys)).order_by(LightNeed.id.asc()).all()
    old_light_need_display = format_light_needs(plant.light_needs) or '-'
    new_light_need_display = format_light_needs(new_light_needs) or '-'
    if old_light_need_display != new_light_need_display:
        changes.append(f"Lichtbedarf: {old_light_need_display} → {new_light_need_display}")
        plant.light_needs = new_light_needs

    new_soil_labels = parse_soil_properties(request.form.get('soil_properties'))
    new_soil_properties = get_or_create_soil_properties(new_soil_labels)
    old_soil_display = ', '.join(plant.soil_property_labels) or '-'
    new_soil_display = ', '.join(item.label for item in new_soil_properties) or '-'
    if old_soil_display != new_soil_display:
        changes.append(f"Bodeneigenschaften: {old_soil_display} → {new_soil_display}")
        plant.soil_properties = new_soil_properties

    upsert_plant_database_identifiers(plant, request.form)

    if changes:
        create_timeline_entry(
            scope_type='plant',
            scope_id=plant.id,
            event_type='data_event',
            event_at=utc_now(),
            title='Pflanzendaten geändert',
            description='\n'.join(changes),
            creator_id=current_user().id
        )

    db.session.commit()
    return redirect(url_for('main.plant_detail', plant_id=plant.id))


@main_bp.route('/plants/<int:plant_id>/duplicate', methods=['POST'])
@login_required
def duplicate_plant(plant_id):
    plant = session_get_or_404(Plant, plant_id)
    duplicated = duplicate_plant_record(plant, current_user().id)
    db.session.commit()
    flash(f'Pflanze „{plant.name}“ wurde dupliziert.', 'success')
    return redirect(url_for('main.plant_detail', plant_id=duplicated.id))

@main_bp.route('/plants/<int:plant_id>/delete', methods=['POST'])
@login_required
def delete_plant(plant_id):
    plant = session_get_or_404(Plant, plant_id)
    source_location_id = plant.location_id
    trash = get_or_create_trash_location()
    if plant.location_id != trash.id:
        create_system_event(plant.id, 'outplant', current_user().id)
    plant.location_id = trash.id
    db.session.commit()
    return redirect(url_for('main.location_detail', location_id=source_location_id))

@main_bp.route('/plants/<int:plant_id>/move', methods=['POST'])
@login_required
def move_plant(plant_id):
    plant = session_get_or_404(Plant, plant_id)
    target_location_id = request.form.get('location_id', type=int)
    target_location = session_get_or_404(Location, target_location_id)
    source_location = session_get_or_404(Location, plant.location_id)
    user_id = current_user().id
    trash = get_or_create_trash_location()

    if source_location.id != target_location.id:
        if source_location.id == trash.id and target_location.id != trash.id:
            create_system_event(plant.id, 'planting', user_id)
        elif target_location.id == trash.id and source_location.id != trash.id:
            create_system_event(plant.id, 'outplant', user_id)
        elif source_location.id != trash.id and target_location.id != trash.id:
            description = f"Umgepflanzt von Beet {source_location.name} nach Beet {target_location.name}"
            create_system_event(plant.id, 'transplant', user_id, description=description)

    plant.location_id = target_location.id
    db.session.commit()
    return redirect(url_for('main.plant_detail', plant_id=plant.id))

@main_bp.route('/plants/<int:plant_id>/events', methods=['POST'])
@login_required
def add_event(plant_id):
    event_type = 'user_event'
    event_at_raw = request.form.get('event_at')
    event_at = datetime.strptime(event_at_raw, '%Y-%m-%d').replace(tzinfo=timezone.utc) if event_at_raw else utc_now()
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()

    file = request.files.get('attachment')
    attachment_filename, upload_error = save_uploaded_attachment(
        file,
        current_app.config['UPLOAD_FOLDER'],
        ALLOWED,
        ALLOWED_ATTACHMENT_MIME_TYPES,
        current_app.config.get('MAX_ATTACHMENT_SIZE_BYTES'),
    )
    if upload_error == 'too_large':
        flash('Datei zu groß (max. 15 MB).', 'error')
        return redirect(url_for('main.plant_detail', plant_id=plant_id))
    if upload_error == 'mime_not_allowed':
        flash('Dateityp nicht erlaubt. Bitte Bild oder PDF hochladen.', 'error')
        return redirect(url_for('main.plant_detail', plant_id=plant_id))
    if upload_error == 'extension_not_allowed':
        flash('Dateiendung nicht erlaubt. Bitte Bild oder PDF hochladen.', 'error')
        return redirect(url_for('main.plant_detail', plant_id=plant_id))
    attachment_kind = attachment_kind_for_filename(attachment_filename)

    if not timeline_entry_has_content(title, description, attachment_filename):
        flash('Bitte Titel, Beschreibung oder Datei angeben.', 'warning')
        return redirect(url_for('main.plant_detail', plant_id=plant_id))

    create_timeline_entry(
        scope_type='plant',
        scope_id=plant_id,
        event_type=event_type,
        event_at=event_at,
        title=title or None,
        description=description or None,
        attachment_filename=attachment_filename,
        attachment_kind=attachment_kind,
        creator_id=current_user().id,
    )
    db.session.commit()
    return redirect(url_for('main.plant_detail', plant_id=plant_id))


@main_bp.route('/plants/<int:plant_id>/events/<int:event_id>/set-title', methods=['POST'])
@login_required
def set_plant_event_title(plant_id, event_id):
    plant = session_get_or_404(Plant, plant_id)
    set_single_title_entry(
        model=TimelineEntry,
        owner_filter=(TimelineEntry.scope_type == 'plant', TimelineEntry.scope_id == plant.id),
        entry_id_field=TimelineEntry.id,
        entry_id_value=event_id,
    )
    db.session.commit()
    return redirect(url_for('main.plant_detail', plant_id=plant.id))


@main_bp.route('/plants/<int:plant_id>/events/<int:event_id>/delete', methods=['POST'])
@login_required
def delete_event(plant_id, event_id):
    event = TimelineEntry.query.filter_by(id=event_id, scope_type='plant', scope_id=plant_id).first_or_404()
    delete_timeline_entry(event, current_app.config['UPLOAD_FOLDER'], ('attachment_filename',))
    db.session.delete(event)
    db.session.commit()
    return redirect(url_for('main.plant_detail', plant_id=plant_id))


@main_bp.route('/plants/<int:plant_id>/events/system/<string:event_key>', methods=['POST'])
@login_required
def add_system_event(plant_id, event_key):
    if event_key not in {'planting', 'outplant', 'care_event', 'measurement'}:
        return redirect(url_for('main.plant_detail', plant_id=plant_id))
    if event_key in {'care_event', 'measurement'}:
        titles = {'care_event': 'Pflege', 'measurement': 'Messen'}
        create_timeline_entry(scope_type='plant', scope_id=plant_id, event_type=EVENT_TYPE_MAP[event_key], event_at=utc_now(), title=titles[event_key], description=None, creator_id=current_user().id)
    else:
        create_system_event(plant_id, event_key, current_user().id)
    db.session.commit()
    return redirect(url_for('main.plant_detail', plant_id=plant_id))
