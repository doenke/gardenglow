"""Daily scheduler for irrigation prediction model training."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import logging
import threading
from typing import Any

from app.services import irrigation_prediction_service

DEFAULT_IRRIGATION_PREDICTION_TRAIN_CRON_TIME = '03:00'
_EXTENSION_KEY = 'irrigation_prediction_train_cron'


def parse_daily_time(value: Any, default: str = DEFAULT_IRRIGATION_PREDICTION_TRAIN_CRON_TIME) -> time:
    """Parse a daily HH:MM or HH:MM:SS schedule time."""
    raw_value = str(value or default).strip() or default
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            parsed = datetime.strptime(raw_value, fmt)
            return time(parsed.hour, parsed.minute, parsed.second)
        except ValueError:
            continue
    parsed_default = datetime.strptime(default, '%H:%M')
    return time(parsed_default.hour, parsed_default.minute)


def seconds_until_next_run(schedule_time: time, now: datetime | None = None) -> float:
    """Return seconds until the next daily run at schedule_time."""
    now = _as_local(now or datetime.now().astimezone())
    scheduled = now.replace(
        hour=schedule_time.hour,
        minute=schedule_time.minute,
        second=schedule_time.second,
        microsecond=0,
    )
    if scheduled <= now:
        scheduled += timedelta(days=1)
    return max(0.0, (scheduled - now).total_seconds())


def start_irrigation_prediction_train_cron(app) -> None:
    """Start one daemon thread that checks due irrigation models once per day."""
    if not app.config.get('IRRIGATION_PREDICTION_TRAIN_CRON_ENABLED', True):
        return
    if app.extensions.get(_EXTENSION_KEY):
        return

    stop_event = threading.Event()
    app.extensions[_EXTENSION_KEY] = stop_event
    thread = threading.Thread(
        target=_cron_loop,
        args=(app, stop_event),
        name='irrigation-prediction-train-cron',
        daemon=True,
    )
    thread.start()


def _cron_loop(app, stop_event: threading.Event) -> None:
    logger = logging.getLogger(__name__)
    while not stop_event.is_set():
        schedule_time = parse_daily_time(app.config.get('IRRIGATION_PREDICTION_TRAIN_CRON_TIME'))
        if stop_event.wait(seconds_until_next_run(schedule_time)):
            break
        try:
            with app.app_context():
                summary = irrigation_prediction_service.train_due_models()
            logger.info('Irrigation prediction cron finished: %s', summary)
        except Exception:  # pragma: no cover - defensive guard for background jobs
            logger.exception('Irrigation prediction cron failed')


def _as_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.astimezone()
    return value.astimezone()
