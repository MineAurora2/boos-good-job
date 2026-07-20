"""浏览器工作器运行态、控制策略、事件流与诊断信息的线程安全注册中心。

客户端、事件、错误、审计和命令保存在当前进程内存中；安全开关、执行计划和账号策略
可选持久化到 JSON 文件。所有共享容器由同一个 ``Condition`` 保护，返回给调用方的
数据会深拷贝，避免外部修改内部状态。
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import secrets
import threading
import time

from app import paths
from app.protocol import CONTROL_PROTOCOL_VERSION, SCRIPT_API_VERSION
from app.scheduling import (
    DEFAULT_SCHEDULE,
    next_schedule_start,
    normalize_schedule,
    schedule_window,
)
from app.storage.io import atomic_write_text


DEFAULT_SAFETY = {
    'globalPaused': False,
    'scanOnly': False,
    'scanAiEnabled': False,
    'sendingDisabled': False,
    'openingDisabled': False,
    'resumeSendingDisabled': False,
    'stopOnDailyLimit': True,
    'stopOnServiceError': True,
}

DEFAULT_PLAN = {
    'dailyTarget': 60,
    'hourlyLimit': 0,
    'activeStart': '',
    'activeEnd': '',
    'breakStart': '',
    'breakEnd': '',
    'stopAtTarget': True,
    'maxConsecutiveFailures': 3,
    'minDelayMs': 0,
    'maxDelayMs': 0,
    'schedule': deepcopy(DEFAULT_SCHEDULE),
}

EVENT_SENDERS = frozenset({'system', 'delivery', 'claim', 'queue'})
EVENT_VERBOSITIES = frozenset({'detailed', 'normal', 'concise'})
EVENT_LEVELS = frozenset({'debug', 'info', 'warning', 'error', 'fatal', 'action'})
DESIRED_STATES = frozenset({'running', 'paused', 'stopped'})
EXECUTION_STATES = frozenset({
    'starting', 'running', 'pausing', 'paused', 'stopping', 'stopped', 'error',
})
CONTROL_ACK_STATUSES = frozenset({'applying', 'applied', 'failed'})
ACCOUNT_DAILY_LIMIT_MIN = 0
ACCOUNT_DAILY_LIMIT_MAX = 150


class RuntimeMonitor:
    """维护控制中心运行态和长轮询事件流的线程安全存储。

    ``client_ttl_seconds`` 决定客户端离线判定时间，事件和命令使用有界队列保存；
    ``state_path`` 为空时策略仅驻留内存，设置后策略变更会写入该 JSON 文件。
    """

    def __init__(
        self,
        *,
        client_ttl_seconds: int = 30,
        max_events: int = 1000,
        max_commands: int = 500,
        state_path: Path | str | None = None,
    ):
        """初始化内存队列、并发锁及可选持久化状态，不启动后台线程。"""
        self.client_ttl_seconds = client_ttl_seconds
        self._clients: dict[str, dict] = {}
        self._events = deque(maxlen=max_events)
        self._errors = deque(maxlen=500)
        self._audit = deque(maxlen=500)
        self._commands = deque(maxlen=max_commands)
        self._cursor = 0
        self._condition = threading.Condition()
        self._state_path = Path(state_path) if state_path else None
        self._safety = deepcopy(DEFAULT_SAFETY)
        self._plan = deepcopy(DEFAULT_PLAN)
        self._account_policies: dict[str, dict] = {}
        self._control_epoch = secrets.token_hex(16)
        self._control_revision = 0
        self._workers: dict[str, dict] = {}
        self._schedule_thread: threading.Thread | None = None
        self._schedule_stop = threading.Event()
        self._schedule_wakeup = threading.Event()
        self._schedule_tick_lock = threading.Lock()
        self._schedule_window_key: str | None = None
        self._schedule_owned_workers: set[str] = set()
        self._schedule_suppressed_workers: set[str] = set()
        self._schedule_last_action = ''
        self._schedule_last_error = ''
        self.started_at = datetime.now().isoformat(timespec='seconds')
        self.started_monotonic = time.monotonic()
        self._load_state()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec='seconds')

    @staticmethod
    def _clean_text(value, limit: int) -> str:
        return str(value or '').strip()[:limit]

    @classmethod
    def _normalize_choice(cls, value, allowed: frozenset[str], default: str) -> str:
        candidate = cls._clean_text(value, 40).lower()
        return candidate if candidate in allowed else default

    @classmethod
    def _normalize_sender(cls, value, default: str = 'system') -> str:
        return cls._normalize_choice(value, EVENT_SENDERS, default)

    @classmethod
    def _normalize_verbosity(cls, value, default: str = 'normal') -> str:
        return cls._normalize_choice(value, EVENT_VERBOSITIES, default)

    @classmethod
    def _normalize_level(cls, value, default: str = 'action') -> str:
        return cls._normalize_choice(value, EVENT_LEVELS, default)

    @classmethod
    def _payload_sender(cls, payload: dict, default: str = 'system') -> str:
        sender = payload.get('sender')
        if not cls._clean_text(sender, 40):
            sender = payload.get('source')
        return cls._normalize_sender(sender, default)

    @staticmethod
    def _infer_action_sender(action_name: str) -> str:
        action_name = str(action_name or '').strip().lower()
        if action_name.startswith('delivery_claim_'):
            return 'claim'
        if action_name == 'chat_open_requested' or any(
            marker in action_name for marker in ('queue', 'queued', 'wait')
        ):
            return 'queue'
        if action_name.startswith('job_') or any(marker in action_name for marker in ('sent', 'greet', 'resume')):
            return 'delivery'
        return 'system'

    def _load_state(self) -> None:
        """从 JSON 恢复允许持久化的字段；文件缺失、损坏时保留默认值。"""
        if not self._state_path or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        if isinstance(data.get('safety'), dict):
            self._safety.update({key: bool(value) for key, value in data['safety'].items() if key in DEFAULT_SAFETY})
        if isinstance(data.get('plan'), dict):
            loaded_plan = {key: value for key, value in data['plan'].items() if key in DEFAULT_PLAN}
            raw_schedule = loaded_plan.pop('schedule', None)
            self._plan.update(loaded_plan)
            if isinstance(raw_schedule, dict):
                try:
                    self._plan['schedule'] = normalize_schedule(raw_schedule)
                except ValueError:
                    self._plan['schedule'] = deepcopy(DEFAULT_SCHEDULE)
        if isinstance(data.get('accounts'), dict):
            for raw_account_id, raw_policy in data['accounts'].items():
                account_id = self._clean_text(raw_account_id, 120)
                if not account_id or not isinstance(raw_policy, dict):
                    continue
                policy = deepcopy(raw_policy)
                if 'dailyLimit' in policy:
                    try:
                        daily_limit = int(policy['dailyLimit'])
                    except (TypeError, ValueError):
                        policy.pop('dailyLimit', None)
                    else:
                        policy['dailyLimit'] = min(
                            ACCOUNT_DAILY_LIMIT_MAX,
                            max(ACCOUNT_DAILY_LIMIT_MIN, daily_limit),
                        )
                self._account_policies[account_id] = policy
        control_epoch = self._clean_text(data.get('controlEpoch'), 160)
        if control_epoch:
            self._control_epoch = control_epoch
        try:
            self._control_revision = max(
                0, int(data.get('revision', data.get('controlRevision')) or 0),
            )
        except (TypeError, ValueError):
            self._control_revision = 0
        workers = data.get('workers')
        if isinstance(workers, dict):
            for raw_worker_id, raw_worker in workers.items():
                worker_id = self._clean_text(raw_worker_id, 160)
                if not worker_id or not isinstance(raw_worker, dict):
                    continue
                desired_state = self._normalize_choice(
                    raw_worker.get('desiredState'), DESIRED_STATES, 'stopped',
                )
                try:
                    revision = max(0, int(raw_worker.get('revision') or 0))
                except (TypeError, ValueError):
                    revision = 0
                worker = {
                    'workerId': worker_id,
                    'accountId': self._clean_text(raw_worker.get('accountId'), 120),
                    'alias': self._clean_text(raw_worker.get('alias'), 120),
                    'role': self._clean_text(raw_worker.get('role'), 40),
                    'scriptVersion': self._clean_text(raw_worker.get('scriptVersion'), 80),
                    'scriptApiVersion': 0,
                    'registeredAt': self._clean_text(raw_worker.get('registeredAt'), 40) or self._now_iso(),
                    'updatedAt': self._clean_text(raw_worker.get('updatedAt'), 40) or self._now_iso(),
                    'desiredState': desired_state,
                    'revision': revision,
                    'operationId': self._clean_text(raw_worker.get('operationId'), 160) or None,
                    'controlAck': None,
                    'protocolVersion': 0,
                    'sessionId': '',
                    'sessionEpoch': 0,
                    'sequence': 0,
                }
                try:
                    worker['protocolVersion'] = max(0, int(raw_worker.get('protocolVersion') or 0))
                    worker['scriptApiVersion'] = max(0, int(raw_worker.get('scriptApiVersion') or 0))
                    worker['sessionEpoch'] = max(0, int(raw_worker.get('sessionEpoch') or 0))
                    worker['sequence'] = max(0, int(raw_worker.get('sequence') or 0))
                except (TypeError, ValueError):
                    worker['protocolVersion'] = 0
                    worker['scriptApiVersion'] = 0
                    worker['sessionEpoch'] = 0
                    worker['sequence'] = 0
                worker['sessionId'] = self._clean_text(raw_worker.get('sessionId'), 160)
                ack = raw_worker.get('controlAck')
                if isinstance(ack, dict):
                    ack_status = self._normalize_choice(ack.get('status'), CONTROL_ACK_STATUSES, '')
                    try:
                        ack_revision = max(0, int(ack.get('revision') or 0))
                    except (TypeError, ValueError):
                        ack_revision = -1
                    if ack_status and ack_revision >= 0:
                        worker['controlAck'] = {
                            'epoch': self._clean_text(ack.get('epoch'), 160),
                            'revision': ack_revision,
                            'operationId': self._clean_text(ack.get('operationId'), 160) or None,
                            'status': ack_status,
                            'executionState': self._normalize_choice(
                                ack.get('executionState'), EXECUTION_STATES, 'stopped',
                            ),
                            'message': self._clean_text(ack.get('message'), 1000),
                            'acknowledgedAt': self._clean_text(ack.get('acknowledgedAt'), 40) or self._now_iso(),
                        }
                self._workers[worker_id] = worker
        if self._workers:
            self._control_revision = max(
                self._control_revision,
                max(worker['revision'] for worker in self._workers.values()),
            )

    def _persist_state_locked(
        self,
        *,
        safety: dict | None = None,
        plan: dict | None = None,
        accounts: dict | None = None,
        control_epoch: str | None = None,
        control_revision: int | None = None,
        workers: dict | None = None,
    ) -> None:
        """通过同目录临时文件替换持久化策略；调用时必须已持有条件锁。"""
        if not self._state_path:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'safety': self._safety if safety is None else safety,
            'plan': self._plan if plan is None else plan,
            'accounts': self._account_policies if accounts is None else accounts,
            'controlEpoch': self._control_epoch if control_epoch is None else control_epoch,
            'revision': self._control_revision if control_revision is None else control_revision,
            'workers': self._workers if workers is None else workers,
            'updatedAt': self._now_iso(),
        }
        atomic_write_text(self._state_path, json.dumps(payload, ensure_ascii=False, indent=2))

    def publish(
        self,
        event_type: str,
        payload: dict,
        *,
        sender: str | None = None,
        verbosity: str | None = None,
        level: str | None = None,
    ) -> dict:
        """规范化事件元数据，在线程锁内追加事件并唤醒长轮询者。"""
        safe_payload = payload if isinstance(payload, dict) else {'value': payload}
        event_sender = (
            self._normalize_sender(sender)
            if self._clean_text(sender, 40)
            else self._payload_sender(safe_payload)
        )
        event_verbosity = self._normalize_verbosity(
            verbosity if self._clean_text(verbosity, 40) else safe_payload.get('verbosity'),
        )
        event_level = self._normalize_level(
            level if self._clean_text(level, 40) else safe_payload.get('level'),
        )
        with self._condition:
            self._cursor += 1
            event = {
                'id': self._cursor,
                'type': self._clean_text(event_type, 80),
                'loggedAt': self._now_iso(),
                'sender': event_sender,
                'verbosity': event_verbosity,
                'level': event_level,
                'payload': safe_payload,
            }
            self._events.append(event)
            self._condition.notify_all()
            return deepcopy(event)

    def record_error(self, payload: dict) -> dict:
        """记录一条经过长度限制的运行错误，同时发布 ``runtime_error`` 事件。"""
        sender = self._payload_sender(payload)
        verbosity = self._normalize_verbosity(payload.get('verbosity'))
        requested_level = self._normalize_level(payload.get('level'), 'error')
        level = 'fatal' if requested_level == 'fatal' else 'error'
        error = {
            'id': f"err-{secrets.token_hex(6)}",
            'workerId': self._clean_text(payload.get('workerId'), 160),
            'accountId': self._clean_text(payload.get('accountId'), 120),
            'type': self._clean_text(payload.get('type') or payload.get('code') or 'runtime_error', 80),
            'message': self._clean_text(payload.get('message') or payload.get('error'), 2000),
            'context': payload.get('context') if isinstance(payload.get('context'), dict) else {},
            'loggedAt': self._clean_text(payload.get('loggedAt'), 40) or self._now_iso(),
            'sender': sender,
            'verbosity': verbosity,
            'level': level,
            'resolved': False,
            'resolvedAt': '',
        }
        with self._condition:
            self._errors.append(error)
        self.publish('runtime_error', error)
        return deepcopy(error)

    def resolve_error(self, error_id: str, resolved: bool = True) -> dict:
        """切换内存错误的解决状态，并追加审计和事件；找不到 ID 时抛 ``ValueError``。"""
        with self._condition:
            for error in self._errors:
                if error['id'] == error_id:
                    error['resolved'] = bool(resolved)
                    error['resolvedAt'] = self._now_iso() if resolved else ''
                    result = deepcopy(error)
                    break
            else:
                raise ValueError('error_not_found')
        self.audit('error_resolved' if resolved else 'error_reopened', {'errorId': error_id})
        return result

    def audit(self, action: str, payload: dict | None = None, actor: str = 'dashboard') -> dict:
        """追加控制操作审计并发布事件；记录仅在本进程有界队列中保留。"""
        record = {
            'id': f"audit-{secrets.token_hex(6)}",
            'action': self._clean_text(action, 100),
            'actor': self._clean_text(actor, 120) or 'dashboard',
            'payload': payload if isinstance(payload, dict) else {},
            'loggedAt': self._now_iso(),
        }
        with self._condition:
            self._audit.append(record)
        self.publish('control_audit', record)
        return deepcopy(record)

    def enqueue_command(self, action: str, worker_id: str = '', payload: dict | None = None, actor: str = 'dashboard') -> dict:
        """创建面向指定工作器或全局的内存命令，并返回可公开的命令副本。

        部分全局快捷命令会立即更新并持久化安全开关；所有命令都会产生审计和事件。
        空动作会抛 ``ValueError``。
        """
        action = self._clean_text(action, 80)
        worker_id = self._clean_text(worker_id, 160)
        if not action:
            raise ValueError('missing_action')
        command = {
            'id': f"cmd-{int(time.time() * 1000):x}-{secrets.token_hex(4)}",
            'type': action,
            'payload': payload if isinstance(payload, dict) else {},
            'workerId': worker_id or None,
            'createdAt': self._now_iso(),
            'createdMonotonic': time.monotonic(),
            'status': 'pending',
            'deliveries': {},
        }
        with self._condition:
            self._commands.append(command)
        # 全局暂停、恢复和扫描模式需要立即生效，不等待工作器下次领取命令。
        if not worker_id and action in {'pause', 'pause_all'}:
            self.update_safety({'globalPaused': True}, actor=actor, audit=False)
        elif not worker_id and action in {'resume', 'resume_all'}:
            self.update_safety({'globalPaused': False}, actor=actor, audit=False)
        elif action == 'set_scan_only' and not worker_id:
            self.update_safety({'scanOnly': bool(command['payload'].get('enabled', True))}, actor=actor, audit=False)
        self.audit('command_created', {'commandId': command['id'], 'action': action, 'workerId': worker_id or None}, actor)
        self.publish('command_created', self._public_command(command))
        return self._public_command(command)

    @staticmethod
    def _public_command(command: dict) -> dict:
        return {
            key: deepcopy(value)
            for key, value in command.items()
            if key not in {'createdMonotonic'}
        }

    def _apply_command_results(self, worker_id: str, results: list) -> None:
        """合并工作器回传的命令结果，并为每个有效结果发布状态事件。"""
        for result in results[-50:]:
            if not isinstance(result, dict):
                continue
            command_id = self._clean_text(result.get('id'), 160)
            if not command_id:
                continue
            with self._condition:
                command = next((item for item in self._commands if item['id'] == command_id), None)
                if not command:
                    continue
                delivery = command['deliveries'].setdefault(worker_id, {})
                delivery.update({
                    'status': self._clean_text(result.get('status') or 'completed', 40),
                    'message': self._clean_text(result.get('message'), 1000),
                    'completedAt': self._clean_text(result.get('completedAt'), 40) or self._now_iso(),
                })
                statuses = {item.get('status') for item in command['deliveries'].values()}
                command['status'] = 'failed' if 'failed' in statuses else 'completed'
                public = self._public_command(command)
            self.publish('command_result', {'workerId': worker_id, 'command': public})

    def _commands_for_worker(self, worker_id: str) -> list[dict]:
        """领取一小时内适用于工作器的未完成命令，并在锁内标记为已送达。"""
        now = time.monotonic()
        result = []
        with self._condition:
            for command in reversed(self._commands):
                if now - command['createdMonotonic'] > 3600:
                    continue
                if command['workerId'] and command['workerId'] != worker_id:
                    continue
                delivery = command['deliveries'].get(worker_id)
                if delivery and delivery.get('status') in {'completed', 'failed', 'ignored'}:
                    continue
                if not delivery:
                    command['deliveries'][worker_id] = {'status': 'delivered', 'deliveredAt': self._now_iso()}
                    command['status'] = 'delivered'
                result.append({
                    'id': command['id'],
                    'type': command['type'],
                    'payload': deepcopy(command['payload']),
                    'createdAt': command['createdAt'],
                })
                if len(result) >= 20:
                    break
        result.reverse()
        return result

    @staticmethod
    def _optional_id(value, limit: int = 160) -> str | None:
        candidate = str(value or '').strip()[:limit]
        return candidate or None

    @staticmethod
    def _integer_field(value, field: str, *, minimum: int = 0) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f'invalid_{field}') from error
        if result < minimum:
            raise ValueError(f'invalid_{field}')
        return result

    @classmethod
    def _execution_state_from_payload(cls, payload: dict, control_ack: dict | None) -> str:
        candidates = [payload.get('executionState')]
        if control_ack:
            candidates.append(control_ack.get('executionState'))
        for value in candidates:
            candidate = cls._clean_text(value, 40).lower()
            if candidate in EXECUTION_STATES:
                return candidate

        # Heartbeats from pre-control-protocol clients remain observable while they upgrade.
        if not payload.get('protocolVersion'):
            legacy_state = cls._clean_text(payload.get('state'), 40).lower()
            if legacy_state in EXECUTION_STATES:
                return legacy_state
            if legacy_state in {'closed', 'exited'}:
                return 'stopped'
            return 'paused' if payload.get('paused') else 'running'
        raise ValueError('invalid_execution_state')

    def _sync_state(self, worker: dict, execution_state: str) -> str:
        ack = worker.get('controlAck')
        if not isinstance(ack, dict):
            return 'pending'
        if (
            ack.get('epoch') != self._control_epoch
            or ack.get('revision') != worker.get('revision')
            or ack.get('operationId') != worker.get('operationId')
        ):
            return 'pending'
        if ack.get('status') == 'failed':
            return 'failed'
        if ack.get('status') != 'applied':
            return 'applying'
        return 'synced' if execution_state == worker.get('desiredState') else 'applying'

    def _control_for_worker_locked(self, worker_id: str) -> dict:
        worker = self._workers[worker_id]
        account_id = worker.get('accountId') or ''
        account = {
            'accountId': account_id,
            **deepcopy(self._account_policies.get(account_id, {})),
        }
        safety = deepcopy(self._safety)
        plan = deepcopy(self._plan)
        failures = max(0, int((self._clients.get(worker_id) or {}).get('consecutiveFailures') or 0))
        return {
            'epoch': self._control_epoch,
            'revision': worker['revision'],
            'operationId': worker.get('operationId'),
            'desiredState': worker['desiredState'],
            'scriptApiVersion': int(worker.get('scriptApiVersion') or 0),
            'consecutiveFailures': failures,
            'safety': safety,
            'plan': plan,
            'account': account,
            'policy': {**safety, **plan, **account, 'consecutiveFailures': failures},
        }

    def _refresh_client_liveness_locked(self, worker_id: str) -> None:
        """Refresh an existing client's ephemeral liveness while holding the condition lock."""
        client = self._clients.get(worker_id)
        if client is not None:
            client['_seenMonotonic'] = time.monotonic()
            client['lastSeen'] = self._now_iso()

    def _prune_offline_workers_locked(self, now_monotonic: float) -> None:
        """Remove registrations whose browser connection is no longer live."""
        offline_worker_ids = [
            worker_id
            for worker_id in self._workers
            if (
                (client := self._clients.get(worker_id)) is None
                or now_monotonic - client['_seenMonotonic'] > self.client_ttl_seconds
                or client.get('state') in {'closed', 'exited'}
            )
        ]
        if not offline_worker_ids:
            return
        workers = {
            worker_id: worker
            for worker_id, worker in self._workers.items()
            if worker_id not in offline_worker_ids
        }
        self._persist_state_locked(workers=workers)
        self._workers = workers
        for worker_id in offline_worker_ids:
            self._clients.pop(worker_id, None)
            self._schedule_owned_workers.discard(worker_id)
            self._schedule_suppressed_workers.discard(worker_id)
        self._condition.notify_all()

    def desired_control(
        self,
        worker_id: str,
        *,
        protocol_version: int,
        session_id: str,
        session_epoch: int,
        after_epoch: str | None = None,
        after_revision: int | None = None,
        timeout_seconds: float = 0,
    ) -> dict:
        """Return or briefly wait for control while refreshing the valid client's liveness."""
        worker_id = self._clean_text(worker_id, 160)
        protocol_version = self._integer_field(protocol_version, 'protocol_version')
        session_id = self._clean_text(session_id, 160)
        session_epoch = self._integer_field(session_epoch, 'session_epoch')
        after_epoch = self._clean_text(after_epoch, 160) if after_epoch is not None else None
        if after_revision is not None:
            after_revision = self._integer_field(after_revision, 'after_revision')
        try:
            timeout_seconds = max(0.0, min(float(timeout_seconds), 20.0))
        except (TypeError, ValueError) as error:
            raise ValueError('invalid_timeout_seconds') from error
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while True:
                self._prune_offline_workers_locked(time.monotonic())
                if not worker_id or worker_id not in self._workers:
                    raise KeyError('worker_not_found')
                worker = self._workers[worker_id]
                if (
                    protocol_version < CONTROL_PROTOCOL_VERSION
                    or protocol_version != int(worker.get('protocolVersion') or 0)
                    or not session_id
                    or session_id != worker.get('sessionId')
                    or session_epoch != int(worker.get('sessionEpoch') or 0)
                ):
                    raise ValueError('stale_session')
                self._refresh_client_liveness_locked(worker_id)
                control = self._control_for_worker_locked(worker_id)
                unchanged = (
                    after_epoch is not None
                    and after_revision is not None
                    and control['epoch'] == after_epoch
                    and control['revision'] == after_revision
                )
                remaining = deadline - time.monotonic()
                if not unchanged or remaining <= 0:
                    return deepcopy(control)
                self._condition.wait(remaining)

    @staticmethod
    def _fresh_heartbeat(previous: dict | None, protocol_version: int, session_id: str, session_epoch: int, sequence: int) -> tuple[bool, str]:
        if not previous:
            return True, 'accepted'
        previous_protocol = int(previous.get('_protocolVersion', previous.get('protocolVersion')) or 0)
        if protocol_version < CONTROL_PROTOCOL_VERSION:
            return (False, 'stale_protocol') if previous_protocol >= CONTROL_PROTOCOL_VERSION else (True, 'accepted')
        if previous_protocol < CONTROL_PROTOCOL_VERSION:
            return True, 'accepted'
        previous_epoch = int(previous.get('_sessionEpoch', previous.get('sessionEpoch')) or 0)
        if session_epoch < previous_epoch:
            return False, 'stale_session'
        previous_session_id = previous.get('_sessionId', previous.get('sessionId'))
        if session_epoch == previous_epoch and session_id != previous_session_id:
            return False, 'stale_session'
        previous_sequence = int(previous.get('_sequence', previous.get('sequence')) or 0)
        if session_epoch == previous_epoch and sequence <= previous_sequence:
            return False, 'stale_sequence'
        return True, 'accepted'

    @staticmethod
    def _valid_running_handoff(
        previous: dict | None,
        worker: dict,
        handoff: dict | None,
        control_epoch: str,
    ) -> bool:
        """Accept an explicit handoff from the currently registered session."""
        if not isinstance(previous, dict) or not isinstance(handoff, dict):
            return False
        if handoff.get('desiredState') != 'running' or worker.get('desiredState') != 'running':
            return False
        if ('workerId' in handoff and handoff.get('workerId') != worker.get('workerId')):
            return False
        if ('accountId' in handoff and handoff.get('accountId') != worker.get('accountId')):
            return False
        if handoff.get('controlEpoch') != control_epoch:
            return False
        if not handoff.get('operationId') or handoff.get('operationId') != worker.get('operationId'):
            return False
        if isinstance(worker.get('controlAck'), dict) and worker['controlAck'].get('status') == 'failed':
            return False
        try:
            if int(handoff.get('revision')) != int(worker.get('revision') or 0):
                return False
            if int(handoff.get('sessionEpoch')) != int(
                previous.get('_sessionEpoch', previous.get('sessionEpoch')) or 0,
            ):
                return False
        except (TypeError, ValueError):
            return False
        previous_session_id = previous.get('_sessionId', previous.get('sessionId'))
        return bool(handoff.get('sessionId')) and handoff.get('sessionId') == previous_session_id

    def _heartbeat_response(self, worker_id: str, accepted: bool, reason: str) -> dict:
        with self._condition:
            control = (
                self._control_for_worker_locked(worker_id)
                if worker_id in self._workers
                else None
            )
        snapshot = self.snapshot()
        snapshot['commands'] = []
        snapshot['control'] = control
        snapshot['heartbeatAccepted'] = accepted
        if not accepted:
            snapshot['heartbeatReason'] = reason
        return snapshot

    def heartbeat(self, payload: dict) -> dict:
        """接收工作器心跳并返回当前客户端快照。

        输入中的日志、事件和错误会截断数量后写入内存事件流；客户端状态在条件锁内更新，
        并发心跳按最后一次写入为准。当前响应不会向浏览器下发可执行命令。
        """
        worker_id = self._clean_text(payload.get('workerId'), 160)
        if not worker_id:
            raise ValueError('missing_worker_id')
        raw_ack = payload.get('controlAck') if isinstance(payload.get('controlAck'), dict) else None
        control_handoff = payload.get('controlHandoff') if isinstance(payload.get('controlHandoff'), dict) else None
        try:
            protocol_version = int(payload.get('protocolVersion') or 0)
        except (TypeError, ValueError) as error:
            raise ValueError('invalid_protocol_version') from error
        if protocol_version < 0:
            raise ValueError('invalid_protocol_version')
        if protocol_version != CONTROL_PROTOCOL_VERSION:
            raise ValueError('unsupported_protocol_version')
        try:
            script_api_version = int(payload.get('scriptApiVersion') or 0)
        except (TypeError, ValueError) as error:
            raise ValueError('invalid_script_api_version') from error
        if script_api_version != SCRIPT_API_VERSION:
            raise ValueError('unsupported_script_api_version')
        if protocol_version >= CONTROL_PROTOCOL_VERSION:
            session_id = self._clean_text(payload.get('sessionId'), 160)
            if not session_id:
                raise ValueError('missing_session_id')
            session_epoch = self._integer_field(payload.get('sessionEpoch'), 'session_epoch')
            sequence = self._integer_field(payload.get('sequence'), 'sequence')
        else:
            session_id = ''
            session_epoch = 0
            sequence = 0
        execution_state = self._execution_state_from_payload(payload, raw_ack)
        now_monotonic = time.monotonic()
        logs = payload.get('logs') if isinstance(payload.get('logs'), list) else []
        events = payload.get('events') if isinstance(payload.get('events'), list) else []
        errors = payload.get('errors') if isinstance(payload.get('errors'), list) else []
        current_decision = payload.get('currentDecision') if isinstance(payload.get('currentDecision'), dict) else {}
        queue = payload.get('queue') if isinstance(payload.get('queue'), list) else []
        safe_client = {
            'workerId': worker_id,
            'accountId': self._clean_text(payload.get('accountId') or '未命名账号', 120),
            'alias': self._clean_text(payload.get('alias'), 120),
            'scriptVersion': self._clean_text(payload.get('scriptVersion') or 'unknown', 80),
            'scriptApiVersion': script_api_version,
            'role': self._clean_text(payload.get('role') or 'unknown', 40),
            'state': self._clean_text(payload.get('state') or 'online', 40),
            'executionState': execution_state,
            'phase': self._clean_text(payload.get('phase'), 120),
            'paused': execution_state in {'pausing', 'paused', 'stopping', 'stopped'},
            'keyword': self._clean_text(payload.get('keyword'), 120),
            'currentJob': self._clean_text(payload.get('currentJob'), 300),
            'currentJobUrl': self._clean_text(payload.get('currentJobUrl'), 1000),
            'currentDecision': deepcopy(current_decision),
            'queue': deepcopy(queue[-100:]),
            'path': self._clean_text(payload.get('path'), 500),
            'counters': deepcopy(payload.get('counters')) if isinstance(payload.get('counters'), dict) else {},
            'lastError': self._clean_text(payload.get('lastError'), 1000),
            'consecutiveFailures': max(0, int(payload.get('consecutiveFailures') or 0)),
            'lastSeen': self._now_iso(),
            '_seenMonotonic': now_monotonic,
            '_protocolVersion': protocol_version,
            '_sessionId': session_id,
            '_sessionEpoch': session_epoch,
            '_sequence': sequence,
        }
        with self._condition:
            previous = self._clients.get(worker_id)
            previous_session = previous or self._workers.get(worker_id)
            accepted, heartbeat_reason = self._fresh_heartbeat(
                previous_session, protocol_version, session_id, session_epoch, sequence,
            )
            if not accepted:
                if worker_id not in self._workers:
                    # This only applies to a corrupted in-memory state; a stale heartbeat may
                    # never create a new registration or replace a newer session.
                    raise ValueError('worker_not_registered')
                return self._heartbeat_response(worker_id, False, heartbeat_reason)
            if not safe_client['alias']:
                safe_client['alias'] = (previous or {}).get('alias') or self._account_policies.get(safe_client['accountId'], {}).get('alias', '')

            workers = deepcopy(self._workers)
            worker = workers.get(worker_id)
            workers_changed = worker is None
            control_revision = self._control_revision
            if worker is None:
                worker = {
                    'workerId': worker_id,
                    'accountId': safe_client['accountId'],
                    'alias': safe_client['alias'],
                    'role': safe_client['role'],
                    'scriptVersion': safe_client['scriptVersion'],
                    'scriptApiVersion': script_api_version,
                    'registeredAt': safe_client['lastSeen'],
                    'updatedAt': safe_client['lastSeen'],
                    'desiredState': 'stopped',
                    'revision': 0,
                    'operationId': None,
                    'controlAck': None,
                    'protocolVersion': protocol_version,
                    'sessionId': session_id,
                    'sessionEpoch': session_epoch,
                    'sequence': sequence,
                }
                workers[worker_id] = worker
            elif protocol_version >= CONTROL_PROTOCOL_VERSION and (
                worker.get('sessionId') != session_id
                or int(worker.get('sessionEpoch') or 0) != session_epoch
            ):
                if self._valid_running_handoff(
                    previous,
                    worker,
                    control_handoff,
                    self._control_epoch,
                ):
                    worker.update({
                        'controlAck': None,
                        'updatedAt': safe_client['lastSeen'],
                    })
                else:
                    control_revision += 1
                    worker.update({
                        'desiredState': 'stopped',
                        'revision': control_revision,
                        'operationId': self._new_control_operation(control_revision),
                        'controlAck': None,
                        'protocolVersion': protocol_version,
                        'sessionId': session_id,
                        'sessionEpoch': session_epoch,
                        'sequence': sequence,
                        'updatedAt': safe_client['lastSeen'],
                    })
                workers_changed = True
            for field in ('accountId', 'alias', 'role', 'scriptVersion', 'scriptApiVersion'):
                if worker.get(field) != safe_client[field]:
                    worker[field] = safe_client[field]
                    worker['updatedAt'] = safe_client['lastSeen']
                    workers_changed = True
            if protocol_version >= CONTROL_PROTOCOL_VERSION:
                session_values = {
                    'protocolVersion': protocol_version,
                    'scriptApiVersion': script_api_version,
                    'sessionId': session_id,
                    'sessionEpoch': session_epoch,
                    'sequence': sequence,
                }
                if any(worker.get(key) != value for key, value in session_values.items()):
                    worker.update(session_values)
                    workers_changed = True

            if raw_ack:
                try:
                    ack_revision = int(raw_ack.get('revision'))
                except (TypeError, ValueError):
                    ack_revision = -1
                ack_status = self._clean_text(raw_ack.get('status'), 40).lower()
                ack_operation_id = self._optional_id(raw_ack.get('operationId'))
                if (
                    self._clean_text(raw_ack.get('epoch'), 160) == self._control_epoch
                    and ack_revision == worker['revision']
                    and ack_operation_id == worker.get('operationId')
                    and ack_status in CONTROL_ACK_STATUSES
                ):
                    ack_execution_state = self._clean_text(raw_ack.get('executionState'), 40).lower()
                    if ack_execution_state not in EXECUTION_STATES:
                        ack_execution_state = execution_state
                    previous_ack = worker.get('controlAck')
                    terminal_ack = (
                        isinstance(previous_ack, dict)
                        and previous_ack.get('epoch') == self._control_epoch
                        and previous_ack.get('revision') == worker['revision']
                        and previous_ack.get('operationId') == worker.get('operationId')
                        and previous_ack.get('status') in {'applied', 'failed'}
                    )
                    if not terminal_ack or ack_status in {'applied', 'failed'}:
                        acknowledged_at = self._clean_text(raw_ack.get('acknowledgedAt'), 40)
                        if not acknowledged_at and isinstance(previous_ack, dict):
                            acknowledged_at = previous_ack.get('acknowledgedAt') or ''
                        normalized_ack = {
                            'epoch': self._control_epoch,
                            'revision': worker['revision'],
                            'operationId': worker.get('operationId'),
                            'status': ack_status,
                            'executionState': ack_execution_state,
                            'message': self._clean_text(raw_ack.get('message'), 1000),
                            'acknowledgedAt': acknowledged_at or safe_client['lastSeen'],
                        }
                        if normalized_ack != previous_ack:
                            worker['controlAck'] = normalized_ack
                            worker['updatedAt'] = safe_client['lastSeen']
                            workers_changed = True

            if workers_changed:
                self._persist_state_locked(
                    control_revision=control_revision,
                    workers=workers,
                )
                self._control_revision = control_revision
                self._workers = workers
                self._condition.notify_all()
            self._clients[worker_id] = safe_client
        if not previous:
            self.publish('client_connected', {key: value for key, value in safe_client.items() if not key.startswith('_')})
        elif safe_client['state'] != previous.get('state') or safe_client['phase'] != previous.get('phase'):
            self.publish('client_state_changed', {
                'workerId': worker_id,
                'accountId': safe_client['accountId'],
                'state': safe_client['state'],
                'phase': safe_client['phase'],
            })
        self._apply_command_results(worker_id, payload.get('commandResults') if isinstance(payload.get('commandResults'), list) else [])
        for log in logs[-50:]:
            if not isinstance(log, dict):
                continue
            log_payload = {
                'workerId': worker_id,
                'accountId': safe_client['accountId'],
                'role': safe_client['role'],
                'sender': self._payload_sender(log),
                'verbosity': self._normalize_verbosity(log.get('verbosity')),
                'level': self._normalize_level(log.get('level'), 'info'),
                'message': self._clean_text(log.get('message'), 2000),
                'loggedAt': self._clean_text(log.get('loggedAt'), 40) or safe_client['lastSeen'],
            }
            self.publish('script_log', log_payload)
            if log_payload['level'] in {'error', 'fatal'}:
                self.record_error({**log_payload, 'type': 'script_log'})
        for event in events[-50:]:
            if isinstance(event, dict):
                self.publish(self._clean_text(event.get('type') or 'script_event', 80), {
                    **event,
                    'workerId': worker_id,
                    'accountId': safe_client['accountId'],
                })
        for error in errors[-20:]:
            if isinstance(error, dict):
                self.record_error({**error, 'workerId': worker_id, 'accountId': safe_client['accountId']})
        if safe_client['lastError'] and safe_client['lastError'] != (previous or {}).get('lastError'):
            self.record_error({
                'workerId': worker_id,
                'accountId': safe_client['accountId'],
                'type': 'worker_last_error',
                'message': safe_client['lastError'],
            })
        return self._heartbeat_response(worker_id, True, heartbeat_reason)

    def record_action(self, action: dict) -> None:
        """把业务动作投影为监控事件；失败类动作还会生成一条运行错误。"""
        action_name = str(action.get('action') or '')
        normalized_action_name = action_name.lower()
        default_sender = self._infer_action_sender(action_name)
        sender = self._payload_sender(action, default_sender)
        verbosity = self._normalize_verbosity(action.get('verbosity'))
        default_level = (
            'error'
            if 'failed' in normalized_action_name or 'error' in normalized_action_name
            else 'action'
        )
        level = self._normalize_level(action.get('level'), default_level)
        payload = {
            key: action.get(key)
            for key in (
                'loggedAt', 'action', 'scene', 'title', 'company', 'salary', 'location',
                'city', 'industry', 'experience', 'education', 'score', 'stars', 'rawStars',
                'discarded', 'accountId', 'workerId', 'reason', 'hrActive',
                'hrActiveLevel', 'aiFilterEnabled', 'aiPassed', 'aiReason', 'greetingMode',
                'sender', 'verbosity', 'level',
            )
            if action.get(key) is not None
        }
        payload.update({'sender': sender, 'verbosity': verbosity, 'level': level})
        self.publish('job_action', payload)
        if 'failed' in normalized_action_name or 'error' in normalized_action_name:
            self.record_error({
                'workerId': action.get('workerId'),
                'accountId': action.get('accountId'),
                'type': action_name or 'job_action_failed',
                'message': action.get('reason') or action_name,
                'context': payload,
                'loggedAt': action.get('loggedAt'),
                'sender': sender,
                'verbosity': verbosity,
                'level': 'error',
            })

    def update_safety(self, patch: dict, *, actor: str = 'dashboard', audit: bool = True) -> dict:
        """校验并原子更新安全开关，持久化后返回完整开关副本。

        不支持的字段会抛 ``ValueError``；默认同时记录审计并发布变更事件。
        """
        if not isinstance(patch, dict):
            raise ValueError('invalid_safety_payload')
        unsupported = set(patch) - set(DEFAULT_SAFETY)
        if unsupported:
            raise ValueError(f'unsupported_safety_field:{sorted(unsupported)[0]}')
        changes = {key: bool(value) for key, value in patch.items()}
        with self._condition:
            candidate = {**self._safety, **changes}
            self._persist_state_locked(safety=candidate)
            self._safety = candidate
            result = deepcopy(self._safety)
        if audit:
            self.audit('safety_updated', {'changes': patch}, actor)
            self.publish('safety_updated', result)
        return result

    def update_plan(self, patch: dict, *, actor: str = 'dashboard') -> dict:
        """规范化并原子更新执行计划，持久化后发布审计和变更事件。"""
        if not isinstance(patch, dict):
            raise ValueError('invalid_plan_payload')
        integers = {'dailyTarget', 'hourlyLimit', 'maxConsecutiveFailures', 'minDelayMs', 'maxDelayMs'}
        booleans = {'stopAtTarget'}
        times = {'activeStart', 'activeEnd', 'breakStart', 'breakEnd'}
        normalized = {}
        for key, value in patch.items():
            if key not in DEFAULT_PLAN:
                raise ValueError(f'unsupported_plan_field:{key}')
            if key in integers:
                value = max(0, int(value))
            elif key in booleans:
                value = bool(value)
            elif key in times:
                value = self._clean_text(value, 5)
                if value and (len(value) != 5 or value[2] != ':'):
                    raise ValueError(f'invalid_time:{key}')
            elif key == 'schedule':
                value = normalize_schedule(value)
            normalized[key] = value
        with self._condition:
            candidate = {**deepcopy(self._plan), **normalized}
            if candidate['maxDelayMs'] and candidate['maxDelayMs'] < candidate['minDelayMs']:
                raise ValueError('max_delay_less_than_min_delay')
            self._persist_state_locked(plan=candidate)
            self._plan = candidate
            if not candidate['schedule']['enabled']:
                self._schedule_window_key = None
                self._schedule_owned_workers.clear()
                self._schedule_suppressed_workers.clear()
                self._schedule_last_action = 'disabled'
                self._schedule_last_error = ''
            result = deepcopy(self._plan)
        self._schedule_wakeup.set()
        self.audit('plan_updated', {'changes': patch}, actor)
        self.publish('plan_updated', result)
        return result

    def update_account(self, account_id: str, patch: dict, *, actor: str = 'dashboard') -> dict:
        """更新单个账号策略并持久化，返回该账号策略的深拷贝。

        账号为空或包含未支持字段时抛 ``ValueError``；成功后发布审计和变更事件。
        """
        account_id = self._clean_text(account_id, 120)
        if not account_id:
            raise ValueError('missing_account_id')
        allowed = {'alias', 'dailyLimit', 'dailyTarget', 'paused', 'keyword', 'notes'}
        if not isinstance(patch, dict) or any(key not in allowed for key in patch):
            raise ValueError('invalid_account_policy')
        normalized = {}
        for key, value in patch.items():
            if key == 'dailyLimit':
                if isinstance(value, bool):
                    raise ValueError('daily_limit_out_of_range')
                raw_value = value
                try:
                    value = int(value)
                except (TypeError, ValueError) as error:
                    raise ValueError('daily_limit_out_of_range') from error
                if isinstance(raw_value, float) and not raw_value.is_integer():
                    raise ValueError('daily_limit_out_of_range')
                if not ACCOUNT_DAILY_LIMIT_MIN <= value <= ACCOUNT_DAILY_LIMIT_MAX:
                    raise ValueError('daily_limit_out_of_range')
            elif key == 'dailyTarget':
                value = max(0, int(value))
            elif key == 'paused':
                value = bool(value)
            else:
                value = self._clean_text(value, 500 if key == 'notes' else 120)
            normalized[key] = value
        with self._condition:
            policy = {**self._account_policies.get(account_id, {}), **normalized}
            policy['accountId'] = account_id
            policy['updatedAt'] = self._now_iso()
            accounts = {**self._account_policies, account_id: policy}
            self._persist_state_locked(accounts=accounts)
            self._account_policies = accounts
            result = deepcopy(policy)
        self.audit('account_policy_updated', {'accountId': account_id, 'changes': patch}, actor)
        self.publish('account_policy_updated', result)
        return result

    @classmethod
    def _desired_state(cls, value) -> str:
        desired_state = cls._clean_text(value, 40).lower()
        if desired_state not in DESIRED_STATES:
            raise ValueError('invalid_desired_state')
        return desired_state

    def _new_control_operation(self, revision: int) -> str:
        return f"control-{revision:x}-{secrets.token_hex(8)}"

    def _record_manual_schedule_control_locked(
        self,
        worker_ids: set[str],
        desired_state: str,
        actor: str,
    ) -> None:
        if actor == 'scheduler' or not self._schedule_window_key:
            return
        if desired_state in {'paused', 'stopped'}:
            self._schedule_suppressed_workers.update(worker_ids)
        elif desired_state == 'running':
            self._schedule_suppressed_workers.difference_update(worker_ids)
            self._schedule_owned_workers.update(worker_ids)

    def set_global_desired_state(self, desired_state: str, *, actor: str = 'dashboard') -> dict:
        desired_state = self._desired_state(desired_state)
        updated_at = self._now_iso()
        with self._condition:
            self._prune_offline_workers_locked(time.monotonic())
            revision = self._control_revision + 1
            operation_id = self._new_control_operation(revision)
            workers = deepcopy(self._workers)
            for worker in workers.values():
                worker.update({
                    'desiredState': desired_state,
                    'revision': revision,
                    'operationId': operation_id,
                    'updatedAt': updated_at,
                })
            self._persist_state_locked(control_revision=revision, workers=workers)
            self._control_revision = revision
            self._workers = workers
            self._record_manual_schedule_control_locked(set(workers), desired_state, actor)
            self._condition.notify_all()
            result = {
                'operationId': operation_id,
                'revision': revision,
                'desiredState': desired_state,
                'targetCount': len(workers),
            }
        self.audit('global_desired_state_updated', result, actor)
        self.publish('desired_state_updated', {**result, 'scope': 'global'})
        return result

    def set_worker_desired_state(self, worker_id: str, desired_state: str, *, actor: str = 'dashboard') -> dict:
        worker_id = self._clean_text(worker_id, 160)
        if not worker_id:
            raise KeyError('worker_not_found')
        desired_state = self._desired_state(desired_state)
        updated_at = self._now_iso()
        with self._condition:
            self._prune_offline_workers_locked(time.monotonic())
            if worker_id not in self._workers:
                raise KeyError('worker_not_found')
            revision = self._control_revision + 1
            operation_id = self._new_control_operation(revision)
            workers = deepcopy(self._workers)
            workers[worker_id].update({
                'desiredState': desired_state,
                'revision': revision,
                'operationId': operation_id,
                'updatedAt': updated_at,
            })
            self._persist_state_locked(control_revision=revision, workers=workers)
            self._control_revision = revision
            self._workers = workers
            self._record_manual_schedule_control_locked({worker_id}, desired_state, actor)
            self._condition.notify_all()
            result = {
                'operationId': operation_id,
                'revision': revision,
                'desiredState': desired_state,
                'targetCount': 1,
            }
        self.audit('worker_desired_state_updated', {**result, 'workerId': worker_id}, actor)
        self.publish('desired_state_updated', {**result, 'scope': 'worker', 'workerId': worker_id})
        return result

    def _set_schedule_workers(self, worker_ids: set[str], desired_state: str) -> dict | None:
        updated_at = self._now_iso()
        with self._condition:
            targets = sorted(
                worker_id for worker_id in worker_ids
                if worker_id in self._workers
                and self._workers[worker_id]['desiredState'] != desired_state
            )
            if not targets:
                return None
            revision = self._control_revision + 1
            operation_id = self._new_control_operation(revision)
            workers = deepcopy(self._workers)
            for worker_id in targets:
                workers[worker_id].update({
                    'desiredState': desired_state,
                    'revision': revision,
                    'operationId': operation_id,
                    'updatedAt': updated_at,
                })
            self._persist_state_locked(control_revision=revision, workers=workers)
            self._control_revision = revision
            self._workers = workers
            self._condition.notify_all()
            result = {
                'operationId': operation_id,
                'revision': revision,
                'desiredState': desired_state,
                'targetCount': len(targets),
                'workerIds': targets,
            }
        self.audit('schedule_desired_state_updated', result, 'scheduler')
        self.publish('desired_state_updated', {**result, 'scope': 'schedule'})
        return result

    def schedule_status(self, now: datetime | None = None) -> dict:
        now = now or datetime.now()
        with self._condition:
            schedule = deepcopy(self._plan['schedule'])
            owned = len(self._schedule_owned_workers)
            suppressed = len(self._schedule_suppressed_workers)
            last_action = self._schedule_last_action
            last_error = self._schedule_last_error
            revision = self._control_revision
        window = schedule_window(now, schedule)
        next_start = next_schedule_start(now, schedule)
        if not schedule['enabled']:
            state = 'disabled'
        elif window and owned and suppressed >= owned:
            state = 'suppressed'
        elif window:
            state = 'running'
        elif next_start:
            state = 'waiting'
        else:
            state = 'finished'
        return {
            'enabled': schedule['enabled'],
            'state': state,
            'active': window is not None,
            'timezone': 'local',
            'currentStart': window.start.isoformat(timespec='seconds') if window else None,
            'currentEnd': window.end.isoformat(timespec='seconds') if window else None,
            'nextStart': next_start.isoformat(timespec='seconds') if next_start else None,
            'remainingSeconds': max(0, int((window.end - now).total_seconds())) if window else 0,
            'ownedCount': owned,
            'suppressedCount': suppressed,
            'lastAction': last_action,
            'lastError': last_error,
            'revision': revision,
        }

    def run_schedule_tick(self, now: datetime | None = None) -> dict:
        now = now or datetime.now()
        with self._schedule_tick_lock:
            with self._condition:
                self._prune_offline_workers_locked(time.monotonic())
                schedule = deepcopy(self._plan['schedule'])
                window = schedule_window(now, schedule)
                if not schedule['enabled']:
                    self._schedule_window_key = None
                    self._schedule_owned_workers.clear()
                    self._schedule_suppressed_workers.clear()
                    self._schedule_last_action = 'disabled'
                    self._schedule_last_error = ''
                    return self.schedule_status(now)
                previous_window_key = self._schedule_window_key
                window_started = window is not None and window.key != previous_window_key
                window_ended = window is None and previous_window_key is not None
                if window_started:
                    self._schedule_suppressed_workers.clear()
                if window:
                    eligible = set(self._workers) - self._schedule_suppressed_workers
                    pause_targets: set[str] = set()
                else:
                    eligible = set()
                    pause_targets = set(self._schedule_owned_workers)

            if window:
                operation = self._set_schedule_workers(eligible, 'running')
                with self._condition:
                    self._schedule_window_key = window.key
                    self._schedule_owned_workers.update(eligible)
                    self._schedule_last_action = 'window_started' if window_started else (
                        'workers_started' if operation else self._schedule_last_action
                    )
                    self._schedule_last_error = ''
                if window_started:
                    self.publish('schedule_window_started', {
                        'windowKey': window.key,
                        'start': window.start.isoformat(timespec='seconds'),
                        'end': window.end.isoformat(timespec='seconds'),
                        'ownedCount': len(eligible),
                    })
            else:
                operation = self._set_schedule_workers(pause_targets, 'paused')
                with self._condition:
                    if window_ended:
                        self._schedule_window_key = None
                        self._schedule_last_action = 'window_ended'
                    self._schedule_last_error = ''
                if window_ended:
                    self.publish('schedule_window_ended', {
                        'windowKey': previous_window_key,
                        'pausedCount': operation['targetCount'] if operation else 0,
                    })
            return self.schedule_status(now)

    def _scheduler_loop(self) -> None:
        while not self._schedule_stop.is_set():
            try:
                self.run_schedule_tick()
            except Exception as error:
                message = self._clean_text(error, 1000)
                with self._condition:
                    self._schedule_last_error = message
                self.record_error({
                    'type': 'schedule_tick_failed',
                    'message': message,
                    'sender': 'system',
                    'verbosity': 'concise',
                    'level': 'error',
                })
            self._schedule_wakeup.wait(1)
            self._schedule_wakeup.clear()

    def start_scheduler(self) -> bool:
        with self._condition:
            if self._schedule_thread and self._schedule_thread.is_alive():
                return False
            self._schedule_stop.clear()
            self._schedule_wakeup.clear()
            self._schedule_thread = threading.Thread(
                target=self._scheduler_loop,
                name='delivery-scheduler',
                daemon=True,
            )
            thread = self._schedule_thread
        thread.start()
        return True

    def stop_scheduler(self) -> bool:
        with self._condition:
            thread = self._schedule_thread
            if not thread:
                return False
            self._schedule_stop.set()
            self._schedule_wakeup.set()
        if thread is not threading.current_thread():
            thread.join(timeout=2)
        with self._condition:
            if self._schedule_thread is thread:
                self._schedule_thread = None
        return True

    def effective_control(self, worker_id: str, account_id: str) -> dict:
        """合并全局与账号策略，返回工作器当前应采用的只读控制快照。"""
        with self._condition:
            account = {
                'accountId': account_id,
                **deepcopy(self._account_policies.get(account_id, {})),
            }
            safety = deepcopy(self._safety)
            plan = deepcopy(self._plan)
            failures = max(0, int((self._clients.get(worker_id) or {}).get('consecutiveFailures') or 0))
            return {
                'safety': safety,
                'plan': plan,
                'account': account,
                'policy': {**safety, **plan, **account, 'consecutiveFailures': failures},
                'consecutiveFailures': failures,
                'shouldPause': bool(self._safety['globalPaused'] or account.get('paused')),
                'workerId': worker_id,
            }

    def snapshot(self) -> dict:
        """返回客户端在线状态快照；在线性依据单调时钟与 TTL 动态计算。"""
        now = time.monotonic()
        clients = []
        with self._condition:
            self._prune_offline_workers_locked(now)
            for worker_id, worker in self._workers.items():
                client = self._clients.get(worker_id)
                item = deepcopy(worker)
                if client:
                    item.update({
                        key: deepcopy(value)
                        for key, value in client.items()
                        if not key.startswith('_')
                    })
                    online = (
                        now - client['_seenMonotonic'] <= self.client_ttl_seconds
                        and item.get('state') not in {'closed', 'exited'}
                    )
                else:
                    online = False
                    item.update({
                        'state': 'offline',
                        'phase': '',
                        'paused': worker['desiredState'] != 'running',
                        'keyword': '',
                        'currentJob': '',
                        'currentJobUrl': '',
                        'currentDecision': {},
                        'queue': [],
                        'path': '',
                        'counters': {},
                        'lastError': '',
                        'consecutiveFailures': 0,
                        'lastSeen': '',
                    })
                execution_state = item.get('executionState')
                if execution_state not in EXECUTION_STATES:
                    ack = worker.get('controlAck')
                    execution_state = (
                        ack.get('executionState')
                        if isinstance(ack, dict) and ack.get('executionState') in EXECUTION_STATES
                        else 'stopped'
                    )
                item.update({
                    'online': online,
                    'executionState': execution_state,
                    'desiredState': worker['desiredState'],
                    'revision': worker['revision'],
                    'operationId': worker.get('operationId'),
                    'controlAck': deepcopy(worker.get('controlAck')),
                    'syncState': self._sync_state(worker, execution_state),
                    'control': self._control_for_worker_locked(worker_id),
                })
                clients.append(item)
            cursor = self._cursor
            control_epoch = self._control_epoch
            control_revision = self._control_revision
        clients.sort(key=lambda item: (not item['online'], item['accountId'], item['workerId']))
        connected_count = sum(1 for item in clients if item['online'])
        running_count = sum(
            1 for item in clients
            if item['online'] and item['executionState'] == 'running'
        )
        return {
            'serverStartedAt': self.started_at,
            'clientTtlSeconds': self.client_ttl_seconds,
            'cursor': cursor,
            'controlEpoch': control_epoch,
            'revision': control_revision,
            'registeredWorkerCount': len(clients),
            'activeClientCount': running_count,
            'connectedClientCount': connected_count,
            'runningClientCount': running_count,
            'clients': clients,
        }

    def control_state(self, command_limit: int = 100) -> dict:
        """汇总客户端、策略、命令、错误、审计和近期事件供控制台展示。"""
        snapshot = self.snapshot()
        with self._condition:
            commands = [self._public_command(item) for item in list(self._commands)[-max(1, min(command_limit, 300)):]]
            errors = [deepcopy(item) for item in list(self._errors)[-150:]]
            audit = [deepcopy(item) for item in list(self._audit)[-150:]]
            safety = deepcopy(self._safety)
            plan = deepcopy(self._plan)
            accounts = deepcopy(self._account_policies)
        return {
            **snapshot,
            'safety': safety,
            'plan': plan,
            'scheduleStatus': self.schedule_status(),
            'accounts': accounts,
            'commands': commands,
            'errors': errors,
            'unresolvedErrorCount': sum(1 for item in errors if not item['resolved']),
            'audit': audit,
            'events': self.recent_events(200),
        }

    def diagnostics(self) -> dict:
        """返回服务存活时间及各类运行态计数，不修改内部状态。"""
        state = self.control_state(300)
        command_counts = {}
        for command in state['commands']:
            command_counts[command['status']] = command_counts.get(command['status'], 0) + 1
        client_states = {}
        for client in state['clients']:
            client_states[client['state']] = client_states.get(client['state'], 0) + 1
        return {
            'healthy': True,
            'serverStartedAt': self.started_at,
            'uptimeSeconds': round(time.monotonic() - self.started_monotonic),
            'activeClientCount': state['activeClientCount'],
            'connectedClientCount': state['connectedClientCount'],
            'clientStates': client_states,
            'commandCounts': command_counts,
            'eventCount': len(state['events']),
            'unresolvedErrorCount': state['unresolvedErrorCount'],
            'safety': state['safety'],
            'plan': state['plan'],
        }

    def events_after(self, cursor: int, timeout: float = 15.0) -> list[dict]:
        """长轮询游标后的事件，最多等待 ``timeout`` 秒；超时返回空列表。"""
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._cursor <= cursor:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)
            return [deepcopy(event) for event in self._events if event['id'] > cursor]

    def recent_events(self, limit: int = 150) -> list[dict]:
        """返回最近 1 到 500 条事件的深拷贝，读取过程由条件锁保护。"""
        with self._condition:
            return [deepcopy(item) for item in list(self._events)[-max(1, min(limit, 500)):]]


RUNTIME_MONITOR = RuntimeMonitor(state_path=paths.CONTROL_CENTER_STATE_PATH)
