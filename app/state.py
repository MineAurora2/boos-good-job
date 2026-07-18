"""Shared application resources initialized by the FastAPI lifespan."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading
import uuid

from fastapi import HTTPException
from starlette.requests import Request

from app import paths
from app.config import Config
from app.runtime import ACCOUNT_DAILY_LIMIT_MAX, ACCOUNT_DAILY_LIMIT_MIN, RUNTIME_MONITOR
from app.security import is_lan_client_host
from app.storage.delivery_store import DeliveryStore
from app.storage.io import append_jsonl


class ApplicationState:
    """Own project paths, process locks, and the configured delivery store."""

    def __init__(self, root: Path):
        self.root = root
        self.dashboard_dir = root / 'dashboard'
        self.decision_log_path = root / 'job_decisions.jsonl'
        self.action_log_path = root / 'job_actions.jsonl'
        self.greeted_log_path = root / 'greeted_jobs.jsonl'
        self.ai_filter_log_path = root / 'ai_filter_log.jsonl'
        self.decision_log_lock = threading.Lock()
        self.action_log_lock = threading.Lock()
        self.greeted_log_lock = threading.Lock()
        self.ai_filter_log_lock = threading.Lock()
        self._startup_lock = threading.Lock()
        self._delivery_store: DeliveryStore | None = None

    def startup(self) -> None:
        """Load configuration and initialize database resources once per path."""
        with self._startup_lock:
            Config.reload()
            database_path = self.root / Config.backend.get('delivery_db_path', 'delivery_state.db')
            daily_limit = min(
                ACCOUNT_DAILY_LIMIT_MAX,
                max(1, int(Config.backend.get('daily_greet_limit', 90))),
            )
            if self._delivery_store is None or self._delivery_store.db_path != database_path:
                self._delivery_store = DeliveryStore(database_path, daily_limit=daily_limit)
            else:
                self._delivery_store.daily_limit = daily_limit

            imported = self._delivery_store.import_legacy_once(
                self.greeted_log_path,
                self.action_log_path,
            )
            print(
                f'[启动] 投递协调数据库: {database_path}，'
                f'迁移旧记录 {imported} 条',
                flush=True,
            )

    @property
    def delivery_store(self) -> DeliveryStore:
        """Return the initialized store, lazily starting for direct function callers."""
        if self._delivery_store is None:
            self.startup()
        assert self._delivery_store is not None
        return self._delivery_store

    @property
    def delivery_db_path(self) -> Path:
        return self.delivery_store.db_path

    def record_job_decision(self, result: dict, raw_job: str, delay_ms: int) -> None:
        """Append one scoring decision and its rule deductions."""
        record = {
            'loggedAt': datetime.now().isoformat(timespec='seconds'),
            'title': result.get('title'),
            'detail': result.get('detail'),
            'matchedField': result.get('matched_field'),
            'keyword': result.get('keyword'),
            'score': result.get('score'),
            'stars': result.get('stars'),
            'deductedStars': result.get('deductedStars'),
            'deductions': result.get('deductions') or [],
            'reason': result.get('reason'),
            'delayMs': delay_ms,
            'rawJob': raw_job,
        }
        append_jsonl(self.decision_log_path, record, self.decision_log_lock)

    def record_job_action(self, action: dict) -> None:
        """Persist a browser action and update the in-memory runtime view."""
        record = {
            **action,
            'eventId': str(action.get('eventId') or uuid.uuid4()),
            'loggedAt': datetime.now().isoformat(timespec='seconds'),
        }
        append_jsonl(self.action_log_path, record, self.action_log_lock)
        RUNTIME_MONITOR.record_action(record)

    def record_ai_filter(
        self,
        title: str,
        salary: str,
        detail: str,
        keyword_score: int,
        ai_passed: bool,
        ai_reason: str,
    ) -> None:
        """Persist a bounded AI-filter audit record."""
        record = {
            'loggedAt': datetime.now().isoformat(timespec='seconds'),
            'title': title,
            'salary': salary,
            'detail': detail[:500] if detail else '',
            'keywordScore': keyword_score,
            'aiPassed': ai_passed,
            'aiReason': ai_reason,
        }
        append_jsonl(self.ai_filter_log_path, record, self.ai_filter_log_lock)

    def quota_status(self, account_id: str) -> dict:
        """Combine persisted usage with the control center's account override."""
        quota = self.delivery_store.quota_status(account_id)
        policy = RUNTIME_MONITOR.effective_control('', account_id).get('account') or {}
        raw_limit = policy.get('dailyLimit')
        configured_limit = int(raw_limit) if raw_limit is not None else quota['limit']
        configured_limit = min(
            ACCOUNT_DAILY_LIMIT_MAX,
            max(ACCOUNT_DAILY_LIMIT_MIN, configured_limit),
        )
        quota['limit'] = configured_limit
        quota['remaining'] = max(0, configured_limit - quota['count'])
        quota['reached'] = quota['count'] >= configured_limit
        return quota


def require_local_admin(request: Request) -> None:
    """Reuse the app boundary for management routes without a second denial."""
    if getattr(request.state, 'goodjob_authorized', False):
        return
    host = request.client.host if request.client else ''
    if not is_lan_client_host(host):
        raise HTTPException(status_code=403, detail='Management access requires an authorized client')


STATE = ApplicationState(paths.PROJECT_ROOT)
