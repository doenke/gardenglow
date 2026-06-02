import os
from uuid import uuid4
from urllib.parse import urlparse

import requests
from authlib.integrations.base_client.errors import MismatchingStateError
from authlib.integrations.flask_client import OAuth
from flask import Blueprint, current_app, redirect, url_for, session
from .models import db, User

oauth = OAuth()
auth_bp = Blueprint('auth', __name__)


ALLOWED_AVATAR_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
ALLOWED_AVATAR_CONTENT_TYPES = {
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/gif',
}
DEFAULT_AVATAR_EXTENSION = '.jpg'
OIDC_ENV_VARS = (
    'OIDC_SERVER_METADATA_URL',
    'OIDC_CLIENT_ID',
    'OIDC_CLIENT_SECRET',
)
DEFAULT_LOCAL_USER_SUB = 'local-default-gardener'
DEFAULT_LOCAL_USER_NAME = 'Gärtner'


def oidc_configured_from_env():
    return any(os.getenv(name, '').strip() for name in OIDC_ENV_VARS)


def oidc_enabled():
    return bool(current_app.config.get('OIDC_ENABLED'))


def get_or_create_default_user():
    user = User.query.filter_by(sub=DEFAULT_LOCAL_USER_SUB).first()
    if not user:
        user = User(sub=DEFAULT_LOCAL_USER_SUB)
        db.session.add(user)

    updated = False
    if user.name != DEFAULT_LOCAL_USER_NAME:
        user.name = DEFAULT_LOCAL_USER_NAME
        updated = True
    if user.email:
        user.email = None
        updated = True
    if user.avatar_url:
        user.avatar_url = None
        updated = True
    if user.avatar_filename:
        user.avatar_filename = None
        updated = True

    if updated or user.id is None:
        db.session.commit()

    session['user_id'] = user.id
    return user


def _register_oidc(oidc_is_enabled):
    if not oidc_is_enabled:
        return
    oauth.register(
        name='oidc',
        client_id=os.getenv('OIDC_CLIENT_ID'),
        client_secret=os.getenv('OIDC_CLIENT_SECRET'),
        server_metadata_url=os.getenv('OIDC_SERVER_METADATA_URL'),
        client_kwargs={'scope': 'openid profile email'},
    )

@auth_bp.record_once
def on_load(state):
    _register_oidc(bool(state.app.config.get('OIDC_ENABLED')))

@auth_bp.route('/login')
def login():
    if not oidc_enabled():
        get_or_create_default_user()
        return redirect(url_for('main.index'))

    redirect_uri = url_for('auth.auth_callback', _external=True)
    return oauth.oidc.authorize_redirect(redirect_uri)



def _download_avatar(user, avatar_url):
    if not avatar_url:
        return

    avatar_folder = current_app.config['AVATAR_FOLDER']
    os.makedirs(avatar_folder, exist_ok=True)

    url_extension = os.path.splitext(urlparse(avatar_url).path)[1].lower()
    ext = (
        url_extension
        if url_extension in ALLOWED_AVATAR_EXTENSIONS
        else DEFAULT_AVATAR_EXTENSION
    )
    if url_extension and url_extension not in ALLOWED_AVATAR_EXTENSIONS:
        current_app.logger.warning(
            'Rejected avatar extension %s for %s; using %s fallback',
            url_extension,
            user.sub,
            DEFAULT_AVATAR_EXTENSION,
        )

    filename = f"{user.sub}_{uuid4().hex}{ext}"
    target = os.path.join(avatar_folder, filename)

    try:
        res = requests.get(avatar_url, timeout=10)
        res.raise_for_status()
        content_type = res.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
        if content_type and content_type not in ALLOWED_AVATAR_CONTENT_TYPES:
            current_app.logger.warning(
                'Rejected avatar for %s due to invalid Content-Type: %s',
                user.sub,
                content_type,
            )
            return

        with open(target, 'wb') as f:
            f.write(res.content)
        user.avatar_filename = filename
    except requests.RequestException:
        current_app.logger.warning('Could not download avatar for %s', user.sub)

@auth_bp.route('/auth/callback')
def auth_callback():
    try:
        token = oauth.oidc.authorize_access_token()
    except MismatchingStateError:
        current_app.logger.warning('OIDC state mismatch; restarting login flow')
        session.clear()
        return redirect(url_for('auth.login'))
    userinfo = token.get('userinfo') or oauth.oidc.userinfo()
    sub = userinfo['sub']
    user = User.query.filter_by(sub=sub).first()
    if not user:
        user = User(sub=sub)
        db.session.add(user)
    user.name = userinfo.get('name')
    user.email = userinfo.get('email')
    user.avatar_url = userinfo.get('picture')
    _download_avatar(user, user.avatar_url)
    db.session.commit()
    session['user_id'] = user.id
    return redirect(url_for('main.index'))

@auth_bp.route('/logout')
def logout():
    session.clear()
    if not oidc_enabled():
        return redirect(url_for('main.index'))

    logout_url = os.getenv('OIDC_LOGOUT_URL')
    if logout_url:
        return redirect(logout_url)
    return redirect(url_for('main.index'))
