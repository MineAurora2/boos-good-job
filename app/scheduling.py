"""Pure schedule validation and local-time delivery window calculations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Any


SCHEDULE_MODES = frozenset({'daily', 'weekly', 'weekdays', 'date_range'})
DEFAULT_SCHEDULE = {
    'enabled': False,
    'mode': 'daily',
    'startTime': '',
    'durationMinutes': 0,
    'weekdays': [],
    'dateStart': '',
    'dateEnd': '',
}


@dataclass(frozen=True)
class ScheduleWindow:
    key: str
    start: datetime
    end: datetime


def _parse_time(value: Any) -> datetime_time | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, '%H:%M')
    except ValueError as error:
        raise ValueError('invalid_schedule_start_time') from error
    if parsed.strftime('%H:%M') != text:
        raise ValueError('invalid_schedule_start_time')
    return parsed.time()


def _parse_date(value: Any) -> date | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise ValueError('invalid_schedule_date_range') from error
    if parsed.isoformat() != text:
        raise ValueError('invalid_schedule_date_range')
    return parsed


def normalize_schedule(payload: dict | None) -> dict:
    """Validate and return a complete, JSON-safe schedule configuration."""
    if not isinstance(payload, dict):
        raise ValueError('invalid_schedule_payload')
    unsupported = set(payload) - set(DEFAULT_SCHEDULE)
    if unsupported:
        raise ValueError(f'unsupported_schedule_field:{sorted(unsupported)[0]}')

    candidate = {**deepcopy(DEFAULT_SCHEDULE), **payload}
    if not isinstance(candidate['enabled'], bool):
        raise ValueError('invalid_schedule_enabled')
    mode = str(candidate['mode'] or '').strip().lower()
    if mode not in SCHEDULE_MODES:
        raise ValueError('invalid_schedule_mode')

    start = _parse_time(candidate['startTime'])
    raw_duration = candidate['durationMinutes']
    if isinstance(raw_duration, bool):
        raise ValueError('invalid_schedule_duration')
    try:
        duration = int(raw_duration)
    except (TypeError, ValueError) as error:
        raise ValueError('invalid_schedule_duration') from error
    if isinstance(raw_duration, float) and not raw_duration.is_integer():
        raise ValueError('invalid_schedule_duration')

    raw_weekdays = candidate['weekdays']
    if not isinstance(raw_weekdays, list):
        raise ValueError('invalid_schedule_weekdays')
    weekdays = []
    for raw_day in raw_weekdays:
        if isinstance(raw_day, bool):
            raise ValueError('invalid_schedule_weekday')
        try:
            day = int(raw_day)
        except (TypeError, ValueError) as error:
            raise ValueError('invalid_schedule_weekday') from error
        if day != raw_day or not 0 <= day <= 6:
            raise ValueError('invalid_schedule_weekday')
        weekdays.append(day)
    weekdays = sorted(set(weekdays))

    date_start = _parse_date(candidate['dateStart'])
    date_end = _parse_date(candidate['dateEnd'])
    if (date_start is None) != (date_end is None) or (
        date_start is not None and date_end is not None and date_start > date_end
    ):
        raise ValueError('invalid_schedule_date_range')

    if candidate['enabled']:
        if start is None:
            raise ValueError('invalid_schedule_start_time')
        if not 1 <= duration <= 1440:
            raise ValueError('invalid_schedule_duration')
        if mode == 'weekly' and not weekdays:
            raise ValueError('missing_schedule_weekdays')
        if mode == 'date_range' and (date_start is None or date_end is None):
            raise ValueError('missing_schedule_date_range')
    elif duration < 0 or duration > 1440:
        raise ValueError('invalid_schedule_duration')

    if mode == 'weekdays':
        weekdays = [0, 1, 2, 3, 4]
    elif mode != 'weekly':
        weekdays = []
    if mode != 'date_range':
        date_start = None
        date_end = None

    return {
        'enabled': candidate['enabled'],
        'mode': mode,
        'startTime': start.strftime('%H:%M') if start else '',
        'durationMinutes': duration,
        'weekdays': weekdays,
        'dateStart': date_start.isoformat() if date_start else '',
        'dateEnd': date_end.isoformat() if date_end else '',
    }


def _valid_start_date(day: date, schedule: dict) -> bool:
    mode = schedule['mode']
    if mode == 'daily':
        return True
    if mode == 'weekdays':
        return day.weekday() < 5
    if mode == 'weekly':
        return day.weekday() in schedule['weekdays']
    return date.fromisoformat(schedule['dateStart']) <= day <= date.fromisoformat(schedule['dateEnd'])


def _window_start(day: date, schedule: dict) -> datetime:
    return datetime.combine(day, datetime.strptime(schedule['startTime'], '%H:%M').time())


def schedule_window(now: datetime, schedule: dict) -> ScheduleWindow | None:
    """Return the active window, considering a previous-day cross-midnight start."""
    if not schedule.get('enabled'):
        return None
    for day in (now.date(), now.date() - timedelta(days=1)):
        if not _valid_start_date(day, schedule):
            continue
        start = _window_start(day, schedule)
        end = start + timedelta(minutes=schedule['durationMinutes'])
        if start <= now < end:
            return ScheduleWindow(start.strftime('%Y-%m-%dT%H:%M'), start, end)
    return None


def next_schedule_start(now: datetime, schedule: dict) -> datetime | None:
    """Return the first configured start strictly after ``now``."""
    if not schedule.get('enabled'):
        return None
    mode = schedule['mode']
    if mode == 'date_range':
        first = date.fromisoformat(schedule['dateStart'])
        last = date.fromisoformat(schedule['dateEnd'])
        day = max(now.date(), first)
        while day <= last:
            start = _window_start(day, schedule)
            if start > now:
                return start
            day += timedelta(days=1)
        return None

    day = now.date()
    for _ in range(8):
        if _valid_start_date(day, schedule):
            start = _window_start(day, schedule)
            if start > now:
                return start
        day += timedelta(days=1)
    return None
