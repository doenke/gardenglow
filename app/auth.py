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
DEFAULT_MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024
AVATAR_DOWNLOAD_CHUNK_SIZE = 64 * 1024
OIDC_ENV_VARS = (
    'OIDC_SERVER_METADATA_URL',
    'OIDC_CLIENT_ID',
    'OIDC_CLIENT_SECRET',
)
DEFAULT_LOCAL_USER_SUB = 'local-default-gardener'
DEFAULT_LOCAL_USER_NAME = 'Gärtner'


class AvatarDownloadTooLargeError(RuntimeError):
    pass


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
        _remove_avatar_file(user.avatar_filename)
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



def _avatar_extension_from_url(avatar_url):
    url_extension = os.path.splitext(urlparse(avatar_url).path)[1].lower()
    ext = (
        url_extension
        if url_extension in ALLOWED_AVATAR_EXTENSIONS
        else DEFAULT_AVATAR_EXTENSION
    )
    if url_extension and url_extension not in ALLOWED_AVATAR_EXTENSIONS:
        current_app.logger.warning(
            'Rejected avatar extension %s; using %s fallback',
            url_extension,
            DEFAULT_AVATAR_EXTENSION,
        )
    return ext


def _avatar_target_path(avatar_folder, filename):
    avatar_folder_abs = os.path.abspath(avatar_folder)
    target = os.path.abspath(os.path.join(avatar_folder_abs, filename))
    if os.path.commonpath([avatar_folder_abs, target]) != avatar_folder_abs:
        raise ValueError('Avatar target path escapes AVATAR_FOLDER')
    return target


def _remove_avatar_file(filename):
    if not filename:
        return

    try:
        target = _avatar_target_path(current_app.config['AVATAR_FOLDER'], filename)
    except (KeyError, ValueError):
        current_app.logger.warning('Skipped removal of invalid avatar filename %s', filename)
        return

    try:
        os.remove(target)
    except FileNotFoundError:
        return
    except OSError:
        current_app.logger.warning('Could not remove previous avatar file %s', filename)


def _stream_avatar_to_file(response, target, max_size_bytes):
    bytes_written = 0
    with open(target, 'wb') as f:
        for chunk in response.iter_content(chunk_size=AVATAR_DOWNLOAD_CHUNK_SIZE):
            if not chunk:
                continue
            bytes_written += len(chunk)
            if bytes_written > max_size_bytes:
                raise AvatarDownloadTooLargeError
            f.write(chunk)


def _download_avatar(user, avatar_url):
    if not avatar_url:
        return

    avatar_folder = current_app.config['AVATAR_FOLDER']
    os.makedirs(avatar_folder, exist_ok=True)

    previous_avatar_filename = user.avatar_filename
    ext = _avatar_extension_from_url(avatar_url)
    filename = f"avatar_{uuid4().hex}{ext}"
    target = _avatar_target_path(avatar_folder, filename)
    max_size_bytes = current_app.config['MAX_AVATAR_SIZE_BYTES']

    try:
        with requests.get(avatar_url, timeout=10, stream=True) as res:
            res.raise_for_status()
            content_type = res.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            if content_type and content_type not in ALLOWED_AVATAR_CONTENT_TYPES:
                current_app.logger.warning(
                    'Rejected avatar for %s due to invalid Content-Type: %s',
                    user.sub,
                    content_type,
                )
                return

            content_length = res.headers.get('Content-Length')
            if content_length and int(content_length) > max_size_bytes:
                current_app.logger.warning(
                    'Rejected avatar for %s because Content-Length exceeds %s bytes',
                    user.sub,
                    max_size_bytes,
                )
                return

            _stream_avatar_to_file(res, target, max_size_bytes)
        user.avatar_filename = filename
        if previous_avatar_filename and previous_avatar_filename != filename:
            _remove_avatar_file(previous_avatar_filename)
    except AvatarDownloadTooLargeError:
        current_app.logger.warning(
            'Rejected avatar for %s because it exceeds %s bytes',
            user.sub,
            max_size_bytes,
        )
        if os.path.exists(target):
            os.remove(target)
    except (OSError, ValueError, requests.RequestException):
        current_app.logger.warning('Could not download avatar for %s', user.sub)
        if os.path.exists(target):
            os.remove(target)

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
