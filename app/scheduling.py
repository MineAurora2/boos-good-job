"""Pure schedule validation and local-time delivery window calculations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Any


SCHEDULE_MODES = frozenset({'daily', 'weekly', 'weekdays', 'date_range'})
MAX_INTERVALS = 12
DEFAULT_SCHEDULE = {
    'enabled': False,
    'mode': 'daily',
    'intervals': [],
    'weekdays': [],
    'dateStart': '',
    'dateEnd': '',
}


@dataclass(frozen=True)
class ScheduleWindow:
    key: str
    start: datetime
    end: datetime


def _parse_time_minutes(value: Any) -> int:
    """Parse an ``HH:MM`` string into minutes since midnight (0..1440)."""
    text = str(value or '').strip()
    if text == '24:00':
        return 1440
    try:
        parsed = datetime.strptime(text, '%H:%M')
    except (ValueError, TypeError) as error:
        raise ValueError('invalid_schedule_interval') from error
    if parsed.strftime('%H:%M') != text:
        raise ValueError('invalid_schedule_interval')
    return parsed.hour * 60 + parsed.minute


def _minutes_to_label(minutes: int) -> str:
    if minutes >= 1440:
        return '24:00'
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


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


def _migrate_legacy_intervals(payload: dict) -> list:
    """Convert a legacy ``startTime`` + ``durationMinutes`` schedule into one interval.

    The old single-window model allowed crossing midnight; when migrating we clamp the
    end to the same day (24:00) because the interval model does not cross midnight.
    """
    start_text = str(payload.get('startTime') or '').strip()
    if not start_text:
        return []
    try:
        start_minutes = _parse_time_minutes(start_text)
    except ValueError:
        return []
    raw_duration = payload.get('durationMinutes')
    if isinstance(raw_duration, bool) or not isinstance(raw_duration, (int, float)):
        return []
    duration = int(raw_duration)
    if duration <= 0:
        return []
    end_minutes = min(1440, start_minutes + duration)
    if end_minutes <= start_minutes:
        return []
    return [{'start': _minutes_to_label(start_minutes), 'end': _minutes_to_label(end_minutes)}]


def _normalize_intervals(raw_intervals: Any) -> list[dict]:
    """Validate the interval list: same-day ranges, sorted, non-overlapping."""
    if not isinstance(raw_intervals, list):
        raise ValueError('invalid_schedule_intervals')
    if len(raw_intervals) > MAX_INTERVALS:
        raise ValueError('invalid_schedule_intervals')
    parsed: list[tuple[int, int]] = []
    for item in raw_intervals:
        if not isinstance(item, dict):
            raise ValueError('invalid_schedule_interval')
        unsupported = set(item) - {'start', 'end'}
        if unsupported:
            raise ValueError('invalid_schedule_interval')
        start_minutes = _parse_time_minutes(item.get('start'))
        end_minutes = _parse_time_minutes(item.get('end'))
        if not 0 <= start_minutes < end_minutes <= 1440:
            raise ValueError('invalid_schedule_interval')
        parsed.append((start_minutes, end_minutes))

    parsed.sort()
    for index in range(1, len(parsed)):
        if parsed[index][0] < parsed[index - 1][1]:
            raise ValueError('overlapping_schedule_intervals')

    return [
        {'start': _minutes_to_label(start), 'end': _minutes_to_label(end)}
        for start, end in parsed
    ]


def normalize_schedule(payload: dict | None) -> dict:
    """Validate and return a complete, JSON-safe schedule configuration."""
    if not isinstance(payload, dict):
        raise ValueError('invalid_schedule_payload')
    # Accept legacy single-window fields transparently by migrating them before
    # rejecting unknown keys, so previously saved plans keep working after upgrade.
    payload = dict(payload)
    legacy_keys = {'startTime', 'durationMinutes'}
    has_legacy = bool(legacy_keys & set(payload))
    migrated_intervals = _migrate_legacy_intervals(payload) if has_legacy else []
    for key in legacy_keys:
        payload.pop(key, None)
    if migrated_intervals and 'intervals' not in payload:
        payload['intervals'] = migrated_intervals

    unsupported = set(payload) - set(DEFAULT_SCHEDULE)
    if unsupported:
        raise ValueError(f'unsupported_schedule_field:{sorted(unsupported)[0]}')

    candidate = {**deepcopy(DEFAULT_SCHEDULE), **payload}
    if not isinstance(candidate['enabled'], bool):
        raise ValueError('invalid_schedule_enabled')
    mode = str(candidate['mode'] or '').strip().lower()
    if mode not in SCHEDULE_MODES:
        raise ValueError('invalid_schedule_mode')

    intervals = _normalize_intervals(candidate['intervals'])

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
        if not intervals:
            raise ValueError('missing_schedule_intervals')
        if mode == 'weekly' and not weekdays:
            raise ValueError('missing_schedule_weekdays')
        if mode == 'date_range' and (date_start is None or date_end is None):
            raise ValueError('missing_schedule_date_range')

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
        'intervals': intervals,
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


def _interval_bounds(day: date, interval: dict) -> tuple[datetime, datetime]:
    start_minutes = _parse_time_minutes(interval['start'])
    end_minutes = _parse_time_minutes(interval['end'])
    start = datetime.combine(day, datetime_time()) + timedelta(minutes=start_minutes)
    end = datetime.combine(day, datetime_time()) + timedelta(minutes=end_minutes)
    return start, end


def schedule_window(now: datetime, schedule: dict) -> ScheduleWindow | None:
    """Return the active interval for today, if ``now`` falls inside one.

    Intervals never cross midnight, so only the current day needs to be checked.
    """
    if not schedule.get('enabled'):
        return None
    day = now.date()
    if not _valid_start_date(day, schedule):
        return None
    for interval in schedule['intervals']:
        start, end = _interval_bounds(day, interval)
        if start <= now < end:
            return ScheduleWindow(f'{day.isoformat()}T{interval["start"]}', start, end)
    return None


def next_schedule_start(now: datetime, schedule: dict) -> datetime | None:
    """Return the first configured interval start strictly after ``now``."""
    if not schedule.get('enabled') or not schedule['intervals']:
        return None
    mode = schedule['mode']
    if mode == 'date_range':
        first = date.fromisoformat(schedule['dateStart'])
        last = date.fromisoformat(schedule['dateEnd'])
        day = max(now.date(), first)
        while day <= last:
            start = _first_interval_start_after(now, day, schedule)
            if start is not None:
                return start
            day += timedelta(days=1)
        return None

    day = now.date()
    for _ in range(8):
        if _valid_start_date(day, schedule):
            start = _first_interval_start_after(now, day, schedule)
            if start is not None:
                return start
        day += timedelta(days=1)
    return None


def _first_interval_start_after(now: datetime, day: date, schedule: dict) -> datetime | None:
    for interval in schedule['intervals']:
        start, _ = _interval_bounds(day, interval)
        if start > now:
            return start
    return None
