from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import secrets
import threading
import time


DEFAULT_SAFETY = {
    'globalPaused': False,
    'scanOnly': False,
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
}


class RuntimeMonitor:
    """Thread-safe runtime registry and control-center state store."""

    def __init__(
        self,
        *,
        client_ttl_seconds: int = 120,
        max_events: int = 1000,
        max_commands: int = 500,
        state_path: Path | str | None = None,
    ):
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
        self.started_at = datetime.now().isoformat(timespec='seconds')
        self.started_monotonic = time.monotonic()
        self._load_state()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec='seconds')

    @staticmethod
    def _clean_text(value, limit: int) -> str:
        return str(value or '').strip()[:limit]

    def _load_state(self) -> None:
        if not self._state_path or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data.get('safety'), dict):
            self._safety.update({key: bool(value) for key, value in data['safety'].items() if key in DEFAULT_SAFETY})
        if isinstance(data.get('plan'), dict):
            self._plan.update({key: value for key, value in data['plan'].items() if key in DEFAULT_PLAN})
        if isinstance(data.get('accounts'), dict):
            self._account_policies = data['accounts']

    def _persist_state_locked(self) -> None:
        if not self._state_path:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'safety': self._safety,
            'plan': self._plan,
            'accounts': self._account_policies,
            'updatedAt': self._now_iso(),
        }
        temporary = self._state_path.with_suffix(self._state_path.suffix + '.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self._state_path)

    def publish(self, event_type: str, payload: dict) -> dict:
        with self._condition:
            self._cursor += 1
            event = {
                'id': self._cursor,
                'type': self._clean_text(event_type, 80),
                'loggedAt': self._now_iso(),
                'payload': payload if isinstance(payload, dict) else {'value': payload},
            }
            self._events.append(event)
            self._condition.notify_all()
            return deepcopy(event)

    def record_error(self, payload: dict) -> dict:
        error = {
            'id': f"err-{secrets.token_hex(6)}",
            'workerId': self._clean_text(payload.get('workerId'), 160),
            'accountId': self._clean_text(payload.get('accountId'), 120),
            'type': self._clean_text(payload.get('type') or payload.get('code') or 'runtime_error', 80),
            'message': self._clean_text(payload.get('message') or payload.get('error'), 2000),
            'context': payload.get('context') if isinstance(payload.get('context'), dict) else {},
            'loggedAt': self._clean_text(payload.get('loggedAt'), 40) or self._now_iso(),
            'resolved': False,
            'resolvedAt': '',
        }
        with self._condition:
            self._errors.append(error)
        self.publish('runtime_error', error)
        return deepcopy(error)

    def resolve_error(self, error_id: str, resolved: bool = True) -> dict:
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
        # Global pause/resume and safety shortcuts are effective immediately.
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

    def heartbeat(self, payload: dict) -> dict:
        worker_id = self._clean_text(payload.get('workerId'), 160)
        if not worker_id:
            raise ValueError('missing_worker_id')
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
            'role': self._clean_text(payload.get('role') or 'unknown', 40),
            'state': self._clean_text(payload.get('state') or 'online', 40),
            'phase': self._clean_text(payload.get('phase'), 120),
            'paused': bool(payload.get('paused')),
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
        }
        with self._condition:
            previous = self._clients.get(worker_id)
            if not safe_client['alias']:
                safe_client['alias'] = (previous or {}).get('alias') or self._account_policies.get(safe_client['accountId'], {}).get('alias', '')
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
                'level': self._clean_text(log.get('level') or 'info', 20),
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
        snapshot = self.snapshot()
        # Heartbeats are monitoring-only. Browser automation is controlled
        # locally and never receives executable commands from the backend.
        snapshot['commands'] = []
        return snapshot

    def record_action(self, action: dict) -> None:
        payload = {
            key: action.get(key)
            for key in (
                'loggedAt', 'action', 'scene', 'title', 'company', 'salary', 'location',
                'city', 'industry', 'experience', 'education', 'score', 'stars', 'rawStars',
                'discarded', 'accountId', 'workerId', 'reason',
            )
            if action.get(key) is not None
        }
        self.publish('job_action', payload)
        action_name = str(action.get('action') or '')
        if 'failed' in action_name or 'error' in action_name:
            self.record_error({
                'workerId': action.get('workerId'),
                'accountId': action.get('accountId'),
                'type': action_name or 'job_action_failed',
                'message': action.get('reason') or action_name,
                'context': payload,
                'loggedAt': action.get('loggedAt'),
            })

    def update_safety(self, patch: dict, *, actor: str = 'dashboard', audit: bool = True) -> dict:
        if not isinstance(patch, dict):
            raise ValueError('invalid_safety_payload')
        with self._condition:
            for key, value in patch.items():
                if key not in DEFAULT_SAFETY:
                    raise ValueError(f'unsupported_safety_field:{key}')
                self._safety[key] = bool(value)
            self._persist_state_locked()
            result = deepcopy(self._safety)
        if audit:
            self.audit('safety_updated', {'changes': patch}, actor)
            self.publish('safety_updated', result)
        return result

    def update_plan(self, patch: dict, *, actor: str = 'dashboard') -> dict:
        if not isinstance(patch, dict):
            raise ValueError('invalid_plan_payload')
        integers = {'dailyTarget', 'hourlyLimit', 'maxConsecutiveFailures', 'minDelayMs', 'maxDelayMs'}
        booleans = {'stopAtTarget'}
        times = {'activeStart', 'activeEnd', 'breakStart', 'breakEnd'}
        with self._condition:
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
                self._plan[key] = value
            if self._plan['maxDelayMs'] and self._plan['maxDelayMs'] < self._plan['minDelayMs']:
                raise ValueError('max_delay_less_than_min_delay')
            self._persist_state_locked()
            result = deepcopy(self._plan)
        self.audit('plan_updated', {'changes': patch}, actor)
        self.publish('plan_updated', result)
        return result

    def update_account(self, account_id: str, patch: dict, *, actor: str = 'dashboard') -> dict:
        account_id = self._clean_text(account_id, 120)
        if not account_id:
            raise ValueError('missing_account_id')
        allowed = {'alias', 'dailyLimit', 'dailyTarget', 'paused', 'keyword', 'notes'}
        if not isinstance(patch, dict) or any(key not in allowed for key in patch):
            raise ValueError('invalid_account_policy')
        with self._condition:
            policy = self._account_policies.setdefault(account_id, {})
            for key, value in patch.items():
                if key in {'dailyLimit', 'dailyTarget'}:
                    value = max(0, int(value))
                elif key == 'paused':
                    value = bool(value)
                else:
                    value = self._clean_text(value, 500 if key == 'notes' else 120)
                policy[key] = value
            policy['accountId'] = account_id
            policy['updatedAt'] = self._now_iso()
            self._persist_state_locked()
            result = deepcopy(policy)
        self.audit('account_policy_updated', {'accountId': account_id, 'changes': patch}, actor)
        self.publish('account_policy_updated', result)
        return result

    def effective_control(self, worker_id: str, account_id: str) -> dict:
        with self._condition:
            account = deepcopy(self._account_policies.get(account_id, {}))
            return {
                'safety': deepcopy(self._safety),
                'plan': deepcopy(self._plan),
                'account': account,
                'shouldPause': bool(self._safety['globalPaused'] or account.get('paused')),
                'workerId': worker_id,
            }

    def snapshot(self) -> dict:
        now = time.monotonic()
        clients = []
        with self._condition:
            for client in self._clients.values():
                item = {key: deepcopy(value) for key, value in client.items() if not key.startswith('_')}
                item['online'] = (
                    now - client['_seenMonotonic'] <= self.client_ttl_seconds
                    and item.get('state') not in {'stopped', 'closed', 'exited'}
                )
                clients.append(item)
            cursor = self._cursor
        clients.sort(key=lambda item: (not item['online'], item['accountId'], item['workerId']))
        return {
            'serverStartedAt': self.started_at,
            'clientTtlSeconds': self.client_ttl_seconds,
            'cursor': cursor,
            'activeClientCount': sum(1 for item in clients if item['online']),
            'connectedClientCount': len(clients),
            'clients': clients,
        }

    def control_state(self, command_limit: int = 100) -> dict:
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
            'accounts': accounts,
            'commands': commands,
            'errors': errors,
            'unresolvedErrorCount': sum(1 for item in errors if not item['resolved']),
            'audit': audit,
            'events': self.recent_events(200),
        }

    def diagnostics(self) -> dict:
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
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._cursor <= cursor:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)
            return [deepcopy(event) for event in self._events if event['id'] > cursor]

    def recent_events(self, limit: int = 150) -> list[dict]:
        with self._condition:
            return [deepcopy(item) for item in list(self._events)[-max(1, min(limit, 500)):]]


RUNTIME_MONITOR = RuntimeMonitor(state_path=Path(__file__).resolve().parent / 'control_center_state.json')
