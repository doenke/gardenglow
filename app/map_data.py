import json
import math


class MapPointValidationError(ValueError):
    """Raised when submitted map point JSON does not match the expected shape."""


MAX_POINT_COUNT = 500
PIXEL_MIN = 0
PIXEL_MAX = 1_000_000
LAT_MIN = -90
LAT_MAX = 90
LON_MIN = -180
LON_MAX = 180


def _load_points(payload, field_label):
    if payload is None or str(payload).strip() == '':
        return []
    try:
        points = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MapPointValidationError(f'{field_label} müssen gültiges JSON sein.') from exc
    if not isinstance(points, list):
        raise MapPointValidationError(f'{field_label} müssen eine JSON-Liste sein.')
    if len(points) > MAX_POINT_COUNT:
        raise MapPointValidationError(f'{field_label} dürfen höchstens {MAX_POINT_COUNT} Punkte enthalten.')
    return points


def _finite_number(value, field_name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MapPointValidationError(f'{field_name} muss eine Zahl sein.')
    number = float(value)
    if not math.isfinite(number):
        raise MapPointValidationError(f'{field_name} muss eine endliche Zahl sein.')
    return number


def _range_checked_number(value, field_name, minimum, maximum):
    number = _finite_number(value, field_name)
    if number < minimum or number > maximum:
        raise MapPointValidationError(f'{field_name} muss zwischen {minimum:g} und {maximum:g} liegen.')
    return number


def _compact_number(value):
    return int(value) if value.is_integer() else value


def _normalized_json(points):
    return json.dumps(points, ensure_ascii=False, separators=(',', ':'))


def _parse_point(point, allowed_keys, field_label, index):
    if not isinstance(point, dict):
        raise MapPointValidationError(f'{field_label}: Punkt {index} muss ein Objekt sein.')
    keys = set(point.keys())
    if keys != allowed_keys:
        expected = ', '.join(sorted(allowed_keys))
        raise MapPointValidationError(f'{field_label}: Punkt {index} darf nur die Felder {expected} enthalten.')


def validate_polygon_points(payload, field_label='Polygonpunkte'):
    """Validate and normalize x/y point lists used for boundaries and bed polygons."""
    points = _load_points(payload, field_label)
    normalized = []
    for index, point in enumerate(points, start=1):
        _parse_point(point, {'x', 'y'}, field_label, index)
        x = _range_checked_number(point['x'], f'{field_label}: Punkt {index} x', LAT_MIN, LAT_MAX)
        y = _range_checked_number(point['y'], f'{field_label}: Punkt {index} y', LON_MIN, LON_MAX)
        normalized.append({'x': _compact_number(x), 'y': _compact_number(y)})
    return _normalized_json(normalized)


def validate_calibration_points(payload):
    """Validate and normalize calibration points with image and geo coordinates."""
    points = _load_points(payload, 'Kalibrierungspunkte')
    if len(points) > 2:
        raise MapPointValidationError('Kalibrierungspunkte dürfen höchstens 2 Punkte enthalten.')
    normalized = []
    for index, point in enumerate(points, start=1):
        _parse_point(point, {'x', 'y', 'coord_x', 'coord_y'}, 'Kalibrierungspunkte', index)
        x = _range_checked_number(point['x'], f'Kalibrierungspunkt {index} x', PIXEL_MIN, PIXEL_MAX)
        y = _range_checked_number(point['y'], f'Kalibrierungspunkt {index} y', PIXEL_MIN, PIXEL_MAX)
        coord_x = _range_checked_number(point['coord_x'], f'Kalibrierungspunkt {index} coord_x', LAT_MIN, LAT_MAX)
        coord_y = _range_checked_number(point['coord_y'], f'Kalibrierungspunkt {index} coord_y', LON_MIN, LON_MAX)
        normalized.append({
            'x': _compact_number(x),
            'y': _compact_number(y),
            'coord_x': _compact_number(coord_x),
            'coord_y': _compact_number(coord_y),
        })
    return _normalized_json(normalized)


def parse_stored_points(payload):
    """Return persisted normalized point JSON as a list for tojson template rendering."""
    if payload is None or str(payload).strip() == '':
        return []
    try:
        points = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return []
    return points if isinstance(points, list) else []
