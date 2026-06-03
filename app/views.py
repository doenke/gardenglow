import os
import time
import re
import json
import subprocess
import zipfile
from io import BytesIO
from urllib.parse import quote, unquote

import requests
from functools import wraps
from datetime import datetime
from flask import Blueprint, current_app, g, render_template, request, redirect, url_for, session, jsonify, send_from_directory, flash, send_file
from .models import db, User, Location, Plant, PlantPhoto, PlantNote, GardenMap, TimelineEntry, LightNeed, SoilProperty, SoilMoistureSensor, PlantDatabaseIdentifier, plant_soil_property
from .map_data import MapPointValidationError, parse_stored_points, validate_calibration_points, validate_polygon_points
from .services.timeline_service import save_uploaded_attachment, set_single_title_entry, delete_timeline_entry, build_unique_upload_name
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
    {'name': 'OIDC_SERVER_METADATA_URL', 'config_key': None, 'default': ''},
    {'name': 'OIDC_CLIENT_ID', 'config_key': None, 'default': ''},
    {'name': 'OIDC_CLIENT_SECRET', 'config_key': None, 'default': '', 'sensitive': True},
    {'name': 'OIDC_LOGOUT_URL', 'config_key': None, 'default': ''},
]


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
        created_at=created_at or datetime.utcnow(),
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
    query = SoilMoistureSensor.query
    if sensor and sensor.id:
        query = query.filter(SoilMoistureSensor.id != sensor.id)
    existing_keys = {key for (key,) in query.with_entities(SoilMoistureSensor.key).all()}
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
    name = (form.get('name') or '').strip()
    if not name:
        return False, 'Bitte einen Sensornamen angeben.'

    map_x, map_x_valid = parse_sensor_map_coordinate('map_x')
    map_y, map_y_valid = parse_sensor_map_coordinate('map_y')
    if not map_x_valid or not map_y_valid:
        return False, 'Bitte gültige Koordinaten für map_x und map_y angeben.'

    locations = get_selected_sensor_locations(form)
    if not locations:
        return False, 'Bitte mindestens ein Beet auswählen.'

    entity_id = (form.get('homeassistant_entity_id') or '').strip() or None
    sensor.name = name
    sensor.homeassistant_entity_id = entity_id
    sensor.influx_measurement = (form.get('influx_measurement') or '').strip() or None
    sensor.influx_field = (form.get('influx_field') or '').strip() or None
    sensor.influx_tags = (form.get('influx_tags') or '').strip() or None
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
        event_at=datetime.utcnow(),
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
        event_at=event_at or datetime.utcnow(),
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
    user = User.query.get(uid) if uid else None
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
                    'mtime': datetime.utcfromtimestamp(os.path.getmtime(full_path)),
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
        'exported_at': datetime.utcnow().isoformat() + 'Z',
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
    )


@main_bp.route('/config')
@login_required
def config():
    user = current_user()
    garden_map = GardenMap.query.order_by(GardenMap.id.asc()).first()
    locations = Location.query.order_by(*location_sort_criteria()).all()
    return render_template(
        'config.html',
        user=user,
        garden_map=garden_map,
        locations=locations,
    )



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


def _safe_orphan_target(folder_path, filename):
    if not folder_path:
        return None
    folder_abs = os.path.abspath(folder_path)
    target = os.path.abspath(os.path.join(folder_abs, filename))
    if os.path.commonpath([folder_abs, target]) != folder_abs:
        return None
    return target


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
    export_time = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
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
    loc = Location.query.get_or_404(location_id)
    plants = Plant.query.filter_by(location_id=loc.id).all()
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


@main_bp.route('/locations/<int:location_id>/timeline/new', methods=['POST'])
@login_required
def new_location_timeline_entry(location_id):
    location = Location.query.get_or_404(location_id)
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
    location = Location.query.get_or_404(location_id)
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
    location = Location.query.get_or_404(location_id)
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
    event_at = datetime.utcnow()
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
    location = Location.query.get_or_404(location_id)
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
    sensors = SoilMoistureSensor.query.order_by(SoilMoistureSensor.name.asc(), SoilMoistureSensor.id.asc()).all()
    locations = Location.query.order_by(*location_sort_criteria()).all()
    selected_location = Location.query.get(selected_location_id) if selected_location_id else None
    return render_template(
        'sensors.html',
        sensors=sensors,
        locations=locations,
        selected_location=selected_location,
        selected_location_id=selected_location.id if selected_location else selected_location_id,
        user=current_user(),
    )


@main_bp.route('/sensors/new', methods=['POST'])
@login_required
def new_sensor():
    sensor = SoilMoistureSensor(creator_id=current_user().id, key='pending')
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
    sensor = SoilMoistureSensor.query.get_or_404(sensor_id)
    locations = Location.query.order_by(*location_sort_criteria()).all()
    return render_template(
        'sensor.html',
        sensor=sensor,
        locations=locations,
        selected_location_ids={location.id for location in sensor.locations},
        user=current_user(),
    )


@main_bp.route('/sensors/<int:sensor_id>/edit', methods=['POST'])
@login_required
def edit_sensor(sensor_id):
    sensor = SoilMoistureSensor.query.get_or_404(sensor_id)
    is_valid, error_message = apply_sensor_form(sensor, request.form)
    if not is_valid:
        flash(error_message, 'warning')
        return redirect(url_for('main.sensor_detail', sensor_id=sensor.id))
    db.session.commit()
    flash(f'Sensor „{sensor.name}“ wurde gespeichert.', 'success')
    return redirect(url_for('main.sensor_detail', sensor_id=sensor.id))


@main_bp.route('/sensors/<int:sensor_id>/delete', methods=['POST'])
@login_required
def delete_sensor(sensor_id):
    sensor = SoilMoistureSensor.query.get_or_404(sensor_id)
    db.session.delete(sensor)
    db.session.commit()
    flash('Sensor wurde gelöscht.', 'success')
    return redirect(url_for('main.sensors'))


@main_bp.route('/plants/<int:plant_id>')
@login_required
def plant_detail(plant_id):
    plant = Plant.query.get_or_404(plant_id)
    events = TimelineEntry.query.filter_by(scope_type='plant', scope_id=plant.id).order_by(TimelineEntry.event_at.desc(), TimelineEntry.created_at.desc()).all()
    photos = PlantPhoto.query.filter_by(plant_id=plant.id).order_by(PlantPhoto.uploaded_at.desc()).all()
    notes = PlantNote.query.filter_by(plant_id=plant.id).order_by(PlantNote.created_at.desc()).all()
    month_names = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez']
    last_plant_event = next((ev for ev in events if ev.event_type == 'plant_event' and ev.title in PLANTING_STATE_TYPES), None)
    is_planted = bool(last_plant_event and PLANTING_STATE_TYPES[last_plant_event.title] in {'planting', 'transplant'})
    location = Location.query.get(plant.location_id)
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
        today_date=datetime.utcnow().date().isoformat(),
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
    loc = Location.query.get_or_404(location_id)
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
    loc = Location.query.get_or_404(location_id)
    if loc.name == TRASH_LOCATION_NAME:
        return redirect(request.referrer or url_for('main.index'))
    loc.color = request.form.get('color') or '#2f6d40'
    db.session.commit()
    return redirect(request.referrer or url_for('main.index'))


@main_bp.route('/plants/<int:plant_id>/position', methods=['POST'])
@login_required
def save_plant_position(plant_id):
    plant = Plant.query.get_or_404(plant_id)
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
    plant = Plant.query.get_or_404(plant_id)
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
    plant = Plant.query.get_or_404(plant_id)
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
    plant = Plant.query.get_or_404(plant_id)

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
            event_at=datetime.utcnow(),
            title='Pflanzendaten geändert',
            description='\n'.join(changes),
            creator_id=current_user().id
        )

    db.session.commit()
    return redirect(url_for('main.plant_detail', plant_id=plant.id))


@main_bp.route('/plants/<int:plant_id>/duplicate', methods=['POST'])
@login_required
def duplicate_plant(plant_id):
    plant = Plant.query.get_or_404(plant_id)
    duplicated = duplicate_plant_record(plant, current_user().id)
    db.session.commit()
    flash(f'Pflanze „{plant.name}“ wurde dupliziert.', 'success')
    return redirect(url_for('main.plant_detail', plant_id=duplicated.id))

@main_bp.route('/plants/<int:plant_id>/delete', methods=['POST'])
@login_required
def delete_plant(plant_id):
    plant = Plant.query.get_or_404(plant_id)
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
    plant = Plant.query.get_or_404(plant_id)
    target_location_id = request.form.get('location_id', type=int)
    target_location = Location.query.get_or_404(target_location_id)
    source_location = Location.query.get_or_404(plant.location_id)
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
    event_at = datetime.strptime(event_at_raw, '%Y-%m-%d') if event_at_raw else datetime.utcnow()
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
    plant = Plant.query.get_or_404(plant_id)
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
        create_timeline_entry(scope_type='plant', scope_id=plant_id, event_type=EVENT_TYPE_MAP[event_key], event_at=datetime.utcnow(), title=titles[event_key], description=None, creator_id=current_user().id)
    else:
        create_system_event(plant_id, event_key, current_user().id)
    db.session.commit()
    return redirect(url_for('main.plant_detail', plant_id=plant_id))
