import os
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Conflict
from werkzeug.utils import secure_filename


def build_unique_upload_name(filename):
    sanitized = secure_filename(filename)
    if not sanitized:
        return f"{uuid4().hex}_upload"
    return f"{uuid4().hex}_{sanitized}"


def _file_extension(filename):
    if not filename or '.' not in filename:
        return None
    return filename.rsplit('.', 1)[1].lower()


def _detected_image_extension(file):
    stream = file.stream
    position = stream.tell()
    header = stream.read(32)
    stream.seek(position)

    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if header.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if header.startswith((b'GIF87a', b'GIF89a')):
        return 'gif'
    if len(header) >= 12 and header.startswith(b'RIFF') and header[8:12] == b'WEBP':
        return 'webp'
    return None


def save_uploaded_attachment(
    file,
    upload_folder,
    allowed_exts,
    allowed_mime_types=None,
    max_size_bytes=None,
    require_image_content=False,
):
    if not file or not file.filename:
        return None, None

    if '.' not in file.filename:
        return None, 'extension_not_allowed'

    ext = _file_extension(file.filename)
    if ext not in allowed_exts:
        return None, 'extension_not_allowed'

    mimetype = (file.mimetype or '').split(';', 1)[0].strip().lower()
    if allowed_mime_types and mimetype not in allowed_mime_types:
        return None, 'mime_not_allowed'

    if require_image_content:
        detected_ext = _detected_image_extension(file)
        if not detected_ext:
            return None, 'image_content_not_allowed'
        matching_exts = {'jpg', 'jpeg'} if ext in {'jpg', 'jpeg'} else {ext}
        if detected_ext not in matching_exts:
            return None, 'image_content_not_allowed'

    if max_size_bytes is not None:
        file.stream.seek(0, os.SEEK_END)
        file_size = file.stream.tell()
        file.stream.seek(0)
        if file_size > max_size_bytes:
            return None, 'too_large'

    unique = build_unique_upload_name(file.filename)
    file.save(os.path.join(upload_folder, unique))
    return unique, None


def set_single_title_entry(model, owner_filter, entry_id_field, entry_id_value):
    entry = model.query.filter(
        *owner_filter,
        entry_id_field == entry_id_value,
    ).first_or_404()

    model.query.filter(*owner_filter).update({'is_title_entry': False}, synchronize_session=False)
    entry.is_title_entry = True

    try:
        model.query.session.flush()
    except IntegrityError as exc:
        model.query.session.rollback()
        raise Conflict('Für diesen Bereich existiert bereits ein Titelbeitrag.') from exc

    return entry


def delete_timeline_entry(entry, upload_folder, attachment_field_names):
    for field_name in attachment_field_names:
        filename = getattr(entry, field_name, None)
        if not filename:
            continue
        attachment_path = os.path.join(upload_folder, filename)
        if os.path.exists(attachment_path):
            os.remove(attachment_path)
