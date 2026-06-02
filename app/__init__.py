from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from .models import db, LightNeed
from .auth import OIDC_ENV_VARS, auth_bp, oauth, oidc_configured_from_env
from .views import main_bp
from .map_data import parse_stored_points
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
    app.config['MAP_FOLDER'] = os.getenv('MAP_FOLDER', '/data/maps')
    app.config['WIDGET_API_KEY'] = os.getenv('WIDGET_API_KEY', '').strip()
    app.config['STATS_UPLOAD_CACHE_TTL_SECONDS'] = max(0, int(os.getenv('STATS_UPLOAD_CACHE_TTL_SECONDS', '60')))
    app.config['HEADER_LOGO_URL'] = os.getenv('HEADER_LOGO_URL', '').strip()
    app.config['COMMON_NAME_LOOKUP_LANG'] = os.getenv('COMMON_NAME_LOOKUP_LANG', 'de').strip().lower() or 'de'
    app.config['DEBUG_MODE'] = (os.getenv('DEBUG_MODE', 'false') or '').strip().lower() in {'1', 'true', 'yes', 'on', 'y'}

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

    db.init_app(app)
    oauth.init_app(app)

    app.jinja_env.filters['stored_points'] = parse_stored_points

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()
        _seed_light_needs()

        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(app.config['AVATAR_FOLDER'], exist_ok=True)
        os.makedirs(app.config['MAP_FOLDER'], exist_ok=True)

    return app


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
