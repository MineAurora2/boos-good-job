"""Deterministic delivery fingerprints and short-lived qualification tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import unicodedata


QUALIFICATION_TTL_SECONDS = 5 * 60
FILTER_VERSION = '2'
_BOUND_FIELDS = (
    'company',
    'title',
    'accountId',
    'workerId',
    'jobFingerprint',
    'configFingerprint',
    'mode',
)


def _normalized_text(value: object, *, casefold: bool = False) -> str:
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = ' '.join(text.split())
    return text.casefold() if casefold else text


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _text_hash(value: object) -> str:
    return hashlib.sha256(_normalized_text(value).encode('utf-8')).hexdigest()


def build_qualification_context(
    *,
    company: str,
    title: str,
    salary: str = '',
    detail: str = '',
    job_url: str = '',
    scoring_config: dict | None = None,
    resume: str = '',
    filter_prompt: str = '',
    llm_config: dict | None = None,
    filter_version: str = FILTER_VERSION,
    mode: str = 'delivery',
) -> dict[str, str]:
    """Build job, rule-config and AI-cache fingerprints without invoking an LLM."""
    job_payload = {
        'company': _normalized_text(company, casefold=True),
        'title': _normalized_text(title, casefold=True),
        'salary': _normalized_text(salary),
        'detail': _normalized_text(detail),
        'jobUrl': _normalized_text(job_url),
    }
    job_fingerprint = _fingerprint(job_payload)
    job_content_fingerprint = _fingerprint({
        key: value for key, value in job_payload.items() if key != 'jobUrl'
    })
    config_fingerprint = _fingerprint(scoring_config or {})
    ai_fingerprint = _fingerprint({
        'jobContentFingerprint': job_content_fingerprint,
        'resumeSummaryHash': _text_hash(resume),
        'filterPromptHash': _text_hash(filter_prompt),
        'llmRoute': llm_config or {},
        'filterVersion': str(filter_version),
    })
    qualification_fingerprint = _fingerprint({
        'jobFingerprint': job_fingerprint,
        'configFingerprint': config_fingerprint,
        'aiFingerprint': ai_fingerprint,
        'mode': str(mode),
    })
    return {
        'jobFingerprint': job_fingerprint,
        'configFingerprint': config_fingerprint,
        'aiFingerprint': ai_fingerprint,
        'qualificationFingerprint': qualification_fingerprint,
    }


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _b64decode(value: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    except (ValueError, TypeError) as error:
        raise ValueError('invalid_qualification_token') from error
    if not hmac.compare_digest(_b64encode(decoded), value):
        raise ValueError('invalid_qualification_token')
    return decoded


class QualificationTokenManager:
    """Issue and verify five-minute HMAC qualification tokens."""

    def __init__(self, secret: bytes | str, ttl_seconds: int = QUALIFICATION_TTL_SECONDS):
        if isinstance(secret, str):
            secret = secret.encode('utf-8')
        if not secret:
            raise ValueError('qualification_secret_required')
        self._secret = bytes(secret)
        self.ttl_seconds = max(1, int(ttl_seconds))

    def issue(self, claims: dict, *, now: int | float | None = None) -> tuple[str, int]:
        issued_at = int(time.time() if now is None else now)
        payload = {field: str(claims.get(field) or '') for field in _BOUND_FIELDS}
        if any(not payload[field] for field in _BOUND_FIELDS):
            raise ValueError('incomplete_qualification_claims')
        if payload['mode'] not in {'delivery', 'scan'}:
            raise ValueError('invalid_qualification_mode')
        for field in ('aiFingerprint', 'qualificationFingerprint'):
            if claims.get(field):
                payload[field] = str(claims[field])
        payload.update({'iat': issued_at, 'exp': issued_at + self.ttl_seconds, 'v': 2})
        encoded = _b64encode(_canonical_json(payload))
        signature = _b64encode(hmac.new(self._secret, encoded.encode('ascii'), hashlib.sha256).digest())
        return f'{encoded}.{signature}', payload['exp']

    def verify(
        self,
        token: str,
        *,
        expected: dict | None = None,
        now: int | float | None = None,
    ) -> dict:
        try:
            encoded, signature = str(token or '').split('.', 1)
            payload_bytes = _b64decode(encoded)
            supplied_signature = _b64decode(signature)
            payload = json.loads(payload_bytes.decode('utf-8'))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            if isinstance(error, ValueError) and str(error) == 'invalid_qualification_token':
                raise
            raise ValueError('invalid_qualification_token') from error
        expected_signature = hmac.new(self._secret, encoded.encode('ascii'), hashlib.sha256).digest()
        if not hmac.compare_digest(expected_signature, supplied_signature):
            raise ValueError('invalid_qualification_token')
        if not isinstance(payload, dict) or payload.get('v') != 2:
            raise ValueError('invalid_qualification_token')
        current = int(time.time() if now is None else now)
        try:
            expires_at = int(payload['exp'])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('invalid_qualification_token') from error
        if current >= expires_at:
            raise ValueError('qualification_expired')
        if expected:
            for field in _BOUND_FIELDS:
                if field in expected and not hmac.compare_digest(
                    str(payload.get(field) or '').encode('utf-8'),
                    str(expected.get(field) or '').encode('utf-8'),
                ):
                    raise ValueError(f'qualification_mismatch:{field}')
        return payload


def _process_secret() -> bytes:
    configured = os.environ.get('GOODJOB_QUALIFICATION_SECRET', '').strip()
    return configured.encode('utf-8') if configured else secrets.token_bytes(32)


QUALIFICATION_TOKENS = QualificationTokenManager(_process_secret())


__all__ = [
    'FILTER_VERSION',
    'QUALIFICATION_TOKENS',
    'QUALIFICATION_TTL_SECONDS',
    'QualificationTokenManager',
    'build_qualification_context',
]
