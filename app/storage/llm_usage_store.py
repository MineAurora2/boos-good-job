"""Persist and aggregate daily LLM upstream usage in SQLite."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3


# Asia/Shanghai is a fixed UTC+08:00 zone (no DST), so keep the service
# timezone self-contained instead of requiring a platform tzdata package.
SERVICE_TIMEZONE = timezone(timedelta(hours=8), 'Asia/Shanghai')


_COUNTER_FIELDS = (
    ('upstream_calls', 'upstreamCalls'),
    ('successful_calls', 'successfulCalls'),
    ('failed_calls', 'failedCalls'),
    ('usage_reported_calls', 'usageReportedCalls'),
    ('input_tokens', 'inputTokens'),
    ('output_tokens', 'outputTokens'),
    ('total_tokens', 'totalTokens'),
)


class LLMUsageStore:
    """Store one aggregate row per day, provider, model, and purpose."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout = 30000')
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute('PRAGMA journal_mode = WAL')
            connection.execute('PRAGMA synchronous = FULL')
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_daily_usage (
                    usage_date TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    provider_name TEXT NOT NULL DEFAULT '',
                    api_base TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    upstream_calls INTEGER NOT NULL DEFAULT 0 CHECK(upstream_calls >= 0),
                    successful_calls INTEGER NOT NULL DEFAULT 0 CHECK(successful_calls >= 0),
                    failed_calls INTEGER NOT NULL DEFAULT 0 CHECK(failed_calls >= 0),
                    usage_reported_calls INTEGER NOT NULL DEFAULT 0 CHECK(usage_reported_calls >= 0),
                    input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
                    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
                    total_tokens INTEGER NOT NULL DEFAULT 0 CHECK(total_tokens >= 0),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (usage_date, provider_id, model, purpose)
                )
                """
            )

    @staticmethod
    def _token_count(value: int, field: str) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f'{field} must be a non-negative integer')
        return value

    @staticmethod
    def _dimension(value: object, field: str) -> str:
        text = '' if value is None else str(value).strip()
        if not text:
            raise ValueError(f'{field} must be non-empty')
        return text

    @staticmethod
    def _occurred_at(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(SERVICE_TIMEZONE)
        if not isinstance(value, datetime):
            raise ValueError('occurred_at must be a datetime')
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=SERVICE_TIMEZONE)
        return value

    def record_attempt(
        self,
        *,
        provider_id: str,
        provider_name: str,
        api_base: str,
        model: str,
        purpose: str,
        success: bool,
        usage_reported: bool,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        occurred_at: datetime | None = None,
    ) -> None:
        """Atomically add one real upstream attempt to its aggregate row."""
        if type(success) is not bool or type(usage_reported) is not bool:
            raise ValueError('success and usage_reported must be booleans')
        provider_id = self._dimension(provider_id, 'provider_id')
        model = self._dimension(model, 'model')
        purpose = self._dimension(purpose, 'purpose')
        input_tokens = self._token_count(input_tokens, 'input_tokens')
        output_tokens = self._token_count(output_tokens, 'output_tokens')
        total_tokens = self._token_count(total_tokens, 'total_tokens')
        if not usage_reported:
            input_tokens = output_tokens = total_tokens = 0
        occurred = self._occurred_at(occurred_at)
        usage_date = occurred.astimezone(SERVICE_TIMEZONE).date().isoformat()
        updated_at = occurred.astimezone(timezone.utc).isoformat(timespec='microseconds')

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO llm_daily_usage (
                    usage_date, provider_id, provider_name, api_base, model, purpose,
                    upstream_calls, successful_calls, failed_calls, usage_reported_calls,
                    input_tokens, output_tokens, total_tokens, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(usage_date, provider_id, model, purpose) DO UPDATE SET
                    provider_name = CASE
                        WHEN excluded.updated_at >= llm_daily_usage.updated_at
                        THEN excluded.provider_name ELSE llm_daily_usage.provider_name END,
                    api_base = CASE
                        WHEN excluded.updated_at >= llm_daily_usage.updated_at
                        THEN excluded.api_base ELSE llm_daily_usage.api_base END,
                    upstream_calls = llm_daily_usage.upstream_calls + 1,
                    successful_calls = llm_daily_usage.successful_calls + excluded.successful_calls,
                    failed_calls = llm_daily_usage.failed_calls + excluded.failed_calls,
                    usage_reported_calls = (
                        llm_daily_usage.usage_reported_calls + excluded.usage_reported_calls
                    ),
                    input_tokens = llm_daily_usage.input_tokens + excluded.input_tokens,
                    output_tokens = llm_daily_usage.output_tokens + excluded.output_tokens,
                    total_tokens = llm_daily_usage.total_tokens + excluded.total_tokens,
                    updated_at = MAX(llm_daily_usage.updated_at, excluded.updated_at)
                """,
                (
                    usage_date,
                    provider_id,
                    str(provider_name or '').strip(),
                    str(api_base or '').strip(),
                    model,
                    purpose,
                    int(success),
                    int(not success),
                    int(usage_reported),
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    updated_at,
                ),
            )

    @staticmethod
    def _empty_counters() -> dict[str, int]:
        return {field: 0 for field, _ in _COUNTER_FIELDS}

    @staticmethod
    def _add_counters(target: dict, source: sqlite3.Row) -> None:
        for field, _ in _COUNTER_FIELDS:
            target[field] += int(source[field])

    @staticmethod
    def _public_counters(source: dict) -> dict:
        calls = int(source['upstream_calls'])
        result = {
            public: int(source[field])
            for field, public in _COUNTER_FIELDS
        }
        result['successRate'] = (
            round(int(source['successful_calls']) * 100 / calls, 1)
            if calls
            else None
        )
        return result

    def query(self, days: int, *, today: date | None = None) -> dict:
        """Return daily, provider, and purpose aggregates for an inclusive range."""
        if type(days) is not int or days <= 0:
            raise ValueError('days must be a positive integer')
        if today is None:
            today = datetime.now(SERVICE_TIMEZONE).date()
        if not isinstance(today, date):
            raise ValueError('today must be a date')
        start = today - timedelta(days=days - 1)

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM llm_daily_usage
                WHERE usage_date BETWEEN ? AND ?
                ORDER BY usage_date, updated_at
                """,
                (start.isoformat(), today.isoformat()),
            ).fetchall()

        summary = self._empty_counters()
        daily = {
            (start + timedelta(days=offset)).isoformat(): self._empty_counters()
            for offset in range(days)
        }
        providers: dict[tuple[str, str], dict] = {}
        purposes: dict[str, dict] = {}

        for row in rows:
            self._add_counters(summary, row)
            self._add_counters(daily[row['usage_date']], row)

            provider_key = (row['provider_id'], row['model'])
            provider = providers.setdefault(provider_key, {
                **self._empty_counters(),
                'provider_id': row['provider_id'],
                'name': row['provider_name'],
                'api_base': row['api_base'],
                'model': row['model'],
                'updated_at': row['updated_at'],
            })
            self._add_counters(provider, row)
            if row['updated_at'] >= provider['updated_at']:
                provider['name'] = row['provider_name']
                provider['api_base'] = row['api_base']
                provider['updated_at'] = row['updated_at']

            purpose = purposes.setdefault(row['purpose'], {
                **self._empty_counters(),
                'purpose': row['purpose'],
            })
            self._add_counters(purpose, row)

        daily_output = [
            {'date': usage_date, **self._public_counters(counters)}
            for usage_date, counters in daily.items()
        ]
        provider_output = [
            {
                'providerId': provider['provider_id'],
                'name': provider['name'],
                'apiBase': provider['api_base'],
                'model': provider['model'],
                **self._public_counters(provider),
            }
            for provider in providers.values()
        ]
        provider_output.sort(
            key=lambda item: (
                -item['totalTokens'],
                -item['upstreamCalls'],
                item['providerId'],
                item['model'],
            )
        )
        purpose_output = [
            {'purpose': purpose['purpose'], **self._public_counters(purpose)}
            for purpose in purposes.values()
        ]
        purpose_output.sort(
            key=lambda item: (-item['totalTokens'], -item['upstreamCalls'], item['purpose'])
        )

        return {
            'generatedAt': datetime.now(SERVICE_TIMEZONE).isoformat(timespec='seconds'),
            'range': {
                'days': days,
                'startDate': start.isoformat(),
                'endDate': today.isoformat(),
            },
            'summary': self._public_counters(summary),
            'daily': daily_output,
            'providers': provider_output,
            'purposes': purpose_output,
        }
