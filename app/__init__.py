from flask import Flask
from sqlalchemy import inspect
from werkzeug.middleware.proxy_fix import ProxyFix
from .models import db, LightNeed, InfluxIntegrationConfig, Sensor, User, SENSOR_TYPE_SOIL_MOISTURE, SENSOR_TYPE_TEMPERATURE, SENSOR_TYPE_RAINFALL
from .auth import DEFAULT_LOCAL_USER_NAME, DEFAULT_LOCAL_USER_SUB, DEFAULT_MAX_AVATAR_SIZE_BYTES, OIDC_ENV_VARS, auth_bp, oauth, oidc_configured_from_env
from .views import main_bp
from .map_data import parse_stored_points
from .services import influx_service
import os


_WEAK_SECRET_KEY_VALUES = {
    'changeme',
    'change-me',
    'change_me',
    'default',
    'dev',
    'development',
    'insecure',
    'placeholder',
    'replace-me',
    'replace_me',
    'secret',
    'test',
    'dev-secret-change-me',
}


def _validate_secret_key(secret_key):
    if not secret_key or not secret_key.strip():
        raise RuntimeError(
            'Konfigurationsfehler: SECRET_KEY ist nicht gesetzt. '
            'Setze eine zufällige, ausreichend lange SECRET_KEY-Umgebungsvariable.'
        )

    normalized = secret_key.strip().lower()
    if normalized in _WEAK_SECRET_KEY_VALUES:
        raise RuntimeError(
            'Konfigurationsfehler: SECRET_KEY ist zu schwach oder ein Platzhalter. '
            'Nutze einen zufälligen, nicht erratbaren Wert (mindestens 32 Zeichen).'
        )

    if len(secret_key) < 32:
        raise RuntimeError(
            'Konfigurationsfehler: SECRET_KEY ist zu kurz. '
            'Nutze mindestens 32 Zeichen mit hoher Entropie.'
        )


def _validate_oidc_config():
    values = {name: os.getenv(name, '').strip() for name in OIDC_ENV_VARS}

    oidc_enabled = oidc_configured_from_env()
    if not oidc_enabled:
        return

    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            'Konfigurationsfehler: OIDC ist teilweise konfiguriert, aber folgende Variablen fehlen: '
            f"{', '.join(missing)}"
        )


def create_app():
    app = Flask(__name__, static_folder='static', template_folder='templates')

    secret_key = os.getenv('SECRET_KEY')
    _validate_secret_key(secret_key)
    _validate_oidc_config()

    app.config['SECRET_KEY'] = secret_key
    app.config['OIDC_ENABLED'] = oidc_configured_from_env()
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///garden.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', '/data/uploads')
    app.config['MAX_ATTACHMENT_SIZE_BYTES'] = max(
        0,
        int(os.getenv('MAX_ATTACHMENT_SIZE_BYTES', str(15 * 1024 * 1024))),
    )
    app.config['MAX_CONTENT_LENGTH'] = app.config['MAX_ATTACHMENT_SIZE_BYTES']
    app.config['AVATAR_FOLDER'] = os.getenv('AVATAR_FOLDER', '/data/avatars')
    app.config['MAX_AVATAR_SIZE_BYTES'] = max(
        0,
        int(os.getenv('MAX_AVATAR_SIZE_BYTES', str(DEFAULT_MAX_AVATAR_SIZE_BYTES))),
    )
    app.config['MAP_FOLDER'] = os.getenv('MAP_FOLDER', '/data/maps')
    app.config['BACKUP_FOLDER'] = os.getenv('BACKUP_FOLDER', '/data/backups')
    app.config['APP_VERSION'] = os.getenv('APP_VERSION', '').strip()
    app.config['GIT_COMMIT'] = os.getenv('GIT_COMMIT', '').strip()
    app.config['WIDGET_API_KEY'] = os.getenv('WIDGET_API_KEY', '').strip()
    app.config['STATS_UPLOAD_CACHE_TTL_SECONDS'] = max(0, int(os.getenv('STATS_UPLOAD_CACHE_TTL_SECONDS', '60')))
    app.config['HEADER_LOGO_URL'] = os.getenv('HEADER_LOGO_URL', '').strip()
    app.config['COMMON_NAME_LOOKUP_LANG'] = os.getenv('COMMON_NAME_LOOKUP_LANG', 'de').strip().lower() or 'de'
    app.config['DEBUG_MODE'] = (os.getenv('DEBUG_MODE', 'false') or '').strip().lower() in {'1', 'true', 'yes', 'on', 'y'}
    app.config['INFLUX_URL'] = os.getenv('INFLUX_URL', '').strip()
    app.config['INFLUX_TOKEN'] = os.getenv('INFLUX_TOKEN', '').strip()
    app.config['INFLUX_ORG'] = os.getenv('INFLUX_ORG', '').strip()
    app.config['INFLUX_BUCKET'] = os.getenv('INFLUX_BUCKET', '').strip()
    app.config['INFLUX_TIMEOUT_SECONDS'] = max(0.1, float(os.getenv('INFLUX_TIMEOUT_SECONDS', '5')))

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

    db.init_app(app)
    oauth.init_app(app)

    app.jinja_env.filters['stored_points'] = parse_stored_points

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()
        _ensure_sensor_schema()
        _migrate_legacy_weather_config_to_sensors()
        _seed_light_needs()

        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(app.config['AVATAR_FOLDER'], exist_ok=True)
        os.makedirs(app.config['MAP_FOLDER'], exist_ok=True)

    return app



def _ensure_sensor_schema():
    """Keep the regular sensor schema compatible with existing databases."""
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())

    if Sensor.__tablename__ not in table_names:
        return

    sensor_columns = {column['name'] for column in inspector.get_columns(Sensor.__tablename__)}
    with db.engine.begin() as connection:
        if 'sensor_type' not in sensor_columns:
            connection.exec_driver_sql(
                f'ALTER TABLE {Sensor.__tablename__} ADD COLUMN sensor_type VARCHAR(32)'
            )
        connection.exec_driver_sql(
            f"UPDATE {Sensor.__tablename__} "
            f"SET sensor_type = '{SENSOR_TYPE_SOIL_MOISTURE}' "
            "WHERE sensor_type IS NULL OR sensor_type = ''"
        )

        if 'sensor_location' not in table_names:
            connection.exec_driver_sql(
                'CREATE TABLE sensor_location ('
                'sensor_id INTEGER NOT NULL, '
                'location_id INTEGER NOT NULL, '
                'PRIMARY KEY (sensor_id, location_id), '
                'FOREIGN KEY(sensor_id) REFERENCES sensor (id), '
                'FOREIGN KEY(location_id) REFERENCES location (id)'
                ')'
            )


def _seed_light_needs():
    """Ensure the default light need catalog values exist."""
    key_label_pairs = [
        ('full_sun', 'Sonnig'),
        ('part_shade', 'Halbschatten'),
        ('shade', 'Schatten'),
    ]
    existing = {row.key for row in LightNeed.query.all()}
    for key, label in key_label_pairs:
        if key not in existing:
            db.session.add(LightNeed(key=key, label=label))
    db.session.commit()


def _legacy_weather_sensor_creator_id():
    """Return a user id for startup migrations that create sensor rows."""
    user = User.query.order_by(User.id.asc()).first()
    if user:
        return user.id

    user = User(sub=DEFAULT_LOCAL_USER_SUB, name=DEFAULT_LOCAL_USER_NAME)
    db.session.add(user)
    db.session.flush()
    return user.id


def _legacy_weather_sensor_key(base_key):
    existing_keys = {
        key for (key,) in db.session.query(Sensor.key).filter(Sensor.key.like(f'{base_key}%')).all()
    }
    if base_key not in existing_keys:
        return base_key

    suffix = 2
    while f'{base_key}-{suffix}' in existing_keys:
        suffix += 1
    return f'{base_key}-{suffix}'


def _migrate_legacy_weather_config_to_sensors():
    """Move deprecated global weather fields into regular sensor rows once."""
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if InfluxIntegrationConfig.__tablename__ not in table_names or Sensor.__tablename__ not in table_names:
        return

    existing_columns = {column['name'] for column in inspector.get_columns(InfluxIntegrationConfig.__tablename__)}
    legacy_kinds = {
        'temperature': {
            'sensor_type': SENSOR_TYPE_TEMPERATURE,
            'name': 'Temperatur',
            'key': 'temperature',
        },
        'rainfall': {
            'sensor_type': SENSOR_TYPE_RAINFALL,
            'name': 'Regenmenge',
            'key': 'rainfall',
        },
    }
    required_columns = {
        f'{kind}_{suffix}'
        for kind in legacy_kinds
        for suffix in ('homeassistant_entity_id', 'influx_measurement', 'influx_field', 'influx_tags')
    }
    if not required_columns.intersection(existing_columns):
        return

    selectable_columns = ['id'] + [column for column in sorted(required_columns) if column in existing_columns]
    rows = db.session.execute(
        db.text(
            f"SELECT {', '.join(selectable_columns)} "
            f'FROM {InfluxIntegrationConfig.__tablename__} ORDER BY id ASC'
        )
    ).mappings().all()
    if not rows:
        return

    creator_id = None
    migrated = False
    for row in rows:
        for kind, definition in legacy_kinds.items():
            entity_id = (row.get(f'{kind}_homeassistant_entity_id') or '').strip()
            measurement = (row.get(f'{kind}_influx_measurement') or '').strip()
            field = (row.get(f'{kind}_influx_field') or '').strip()
            tags = (row.get(f'{kind}_influx_tags') or '').strip()
            if not any((entity_id, measurement, field, tags)):
                continue

            existing_query = Sensor.query.filter(Sensor.sensor_type == definition['sensor_type'])
            if entity_id:
                existing_query = existing_query.filter(Sensor.homeassistant_entity_id == entity_id)
            else:
                existing_query = existing_query.filter(Sensor.key == definition['key'])
            if existing_query.first():
                continue

            defaults = influx_service.homeassistant_entity_influx_defaults(entity_id) if entity_id else {}
            if creator_id is None:
                creator_id = _legacy_weather_sensor_creator_id()
            db.session.add(Sensor(
                name=definition['name'],
                key=_legacy_weather_sensor_key(definition['key']),
                sensor_type=definition['sensor_type'],
                homeassistant_entity_id=entity_id or None,
                influx_measurement=measurement or defaults.get('measurement') or None,
                influx_field=field or defaults.get('field') or None,
                influx_tags=tags or defaults.get('tags') or None,
                creator_id=creator_id,
                is_active=True,
            ))
            migrated = True

    if migrated:
        db.session.commit()
