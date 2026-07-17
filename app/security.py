"""Network-boundary authentication for the HTTP application."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
import os
import secrets
from typing import Mapping, TypeAlias

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


IPAddress: TypeAlias = IPv4Address | IPv6Address
IPNetwork: TypeAlias = IPv4Network | IPv6Network

_LOCAL_NETWORKS: tuple[IPNetwork, ...] = (
    ip_network('127.0.0.0/8'),
    ip_network('10.0.0.0/8'),
    ip_network('172.16.0.0/12'),
    ip_network('192.168.0.0/16'),
    ip_network('169.254.0.0/16'),
    ip_network('::1/128'),
    ip_network('fc00::/7'),
    ip_network('fe80::/10'),
)

_TEST_CLIENT_HOSTS = frozenset({'testclient', 'localhost'})
_PUBLIC_DASHBOARD_PATHS = frozenset(
    {
        '/dashboard',
        '/dashboard/',
        '/dashboard/app.js',
        '/dashboard/styles.css',
        '/dashboard/china.json',
        '/dashboard/china-cities.json',
    }
)


class SecurityConfigurationError(ValueError):
    """Raised when a security environment variable would weaken the boundary."""


def _normalized_ip(value: str) -> IPAddress:
    """Parse a socket/header address and normalize IPv4-mapped IPv6 values."""
    raw = str(value or '').strip()
    if raw.startswith('[') and raw.endswith(']'):
        raw = raw[1:-1]
    if '%' in raw:
        raw = raw.split('%', 1)[0]
    parsed = ip_address(raw)
    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def _network_contains(network: IPNetwork, address: IPAddress) -> bool:
    return network.version == address.version and address in network


def is_lan_client_host(host: str) -> bool:
    """Return whether an ASGI peer belongs to one of the explicit exempt ranges."""
    normalized = str(host or '').strip().lower()
    if normalized in _TEST_CLIENT_HOSTS:
        # Starlette's in-process TestClient uses a non-IP socket sentinel.
        return True
    try:
        address = _normalized_ip(normalized)
    except ValueError:
        return False
    return any(_network_contains(network, address) for network in _LOCAL_NETWORKS)


def _parse_trusted_proxies(raw_value: str) -> tuple[IPNetwork, ...]:
    raw_value = str(raw_value or '').strip()
    if not raw_value:
        return ()
    entries = [entry.strip() for entry in raw_value.split(',')]
    if any(not entry for entry in entries):
        raise SecurityConfigurationError(
            'GOODJOB_TRUSTED_PROXIES must be a comma-separated list of IP addresses or CIDRs'
        )

    networks: list[IPNetwork] = []
    for entry in entries:
        if '*' in entry:
            raise SecurityConfigurationError('GOODJOB_TRUSTED_PROXIES does not allow wildcards')
        try:
            network = ip_network(entry, strict=False)
        except ValueError as error:
            raise SecurityConfigurationError(
                f'Invalid GOODJOB_TRUSTED_PROXIES entry: {entry}'
            ) from error
        if network.prefixlen == 0:
            raise SecurityConfigurationError('GOODJOB_TRUSTED_PROXIES does not allow all-address CIDRs')
        if network not in networks:
            networks.append(network)
    return tuple(networks)


def _parse_shared_token(raw_value: str | None) -> str | None:
    if raw_value is None or not raw_value.strip():
        return None
    token = raw_value.strip()
    if not 32 <= len(token) <= 256:
        raise SecurityConfigurationError('GOODJOB_SHARED_TOKEN must contain 32 to 256 characters')
    if any(character in token for character in ('\r', '\n')):
        raise SecurityConfigurationError('GOODJOB_SHARED_TOKEN must not contain line breaks')
    return token


@dataclass(frozen=True)
class AccessContext:
    """Effective client identity after applying the configured proxy boundary."""

    authorized: bool
    client_host: str
    scheme: str
    auth_method: str
    via_trusted_proxy: bool


@dataclass(frozen=True)
class AccessDecision:
    context: AccessContext
    status_code: int = 200
    detail: str = ''


@dataclass(frozen=True)
class SecurityPolicy:
    """Immutable authentication policy loaded once while constructing the app."""

    shared_token: str | None
    trusted_proxies: tuple[IPNetwork, ...] = ()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> 'SecurityPolicy':
        values = os.environ if environ is None else environ
        return cls(
            shared_token=_parse_shared_token(values.get('GOODJOB_SHARED_TOKEN')),
            trusted_proxies=_parse_trusted_proxies(values.get('GOODJOB_TRUSTED_PROXIES', '')),
        )

    def _is_trusted_proxy(self, address: IPAddress) -> bool:
        return any(_network_contains(network, address) for network in self.trusted_proxies)

    def _effective_client(self, scope: Scope, headers: Headers) -> tuple[str, str, bool, str | None]:
        socket_client = scope.get('client')
        peer_host = str(socket_client[0]) if socket_client else ''
        native_scheme = str(scope.get('scheme') or 'http').lower()
        forwarded_for_values = headers.getlist('x-forwarded-for')
        forwarded_proto_values = headers.getlist('x-forwarded-proto')
        has_forwarding_headers = bool(forwarded_for_values or forwarded_proto_values)
        try:
            peer_ip = _normalized_ip(peer_host)
        except ValueError:
            if has_forwarding_headers and is_lan_client_host(peer_host):
                return (
                    peer_host,
                    native_scheme,
                    False,
                    'Forwarded headers require a configured trusted proxy',
                )
            return peer_host, native_scheme, False, None

        if not self._is_trusted_proxy(peer_ip):
            if has_forwarding_headers and is_lan_client_host(str(peer_ip)):
                return (
                    str(peer_ip),
                    native_scheme,
                    False,
                    'Forwarded headers require a configured trusted proxy',
                )
            return str(peer_ip), native_scheme, False, None

        if not forwarded_for_values or not forwarded_proto_values:
            return (
                str(peer_ip),
                native_scheme,
                True,
                'Trusted proxy requests require X-Forwarded-For and X-Forwarded-Proto headers',
            )
        if len(forwarded_for_values) != 1:
            return str(peer_ip), native_scheme, True, 'Invalid X-Forwarded-For header'
        if len(forwarded_proto_values) != 1:
            return str(peer_ip), native_scheme, True, 'Invalid X-Forwarded-Proto header'

        forwarded_for = forwarded_for_values[0]
        forwarded_proto = forwarded_proto_values[0]

        effective_ip = peer_ip
        forwarded_entries = [entry.strip() for entry in forwarded_for.split(',')]
        if not forwarded_entries or len(forwarded_entries) > 32 or any(not entry for entry in forwarded_entries):
            return str(peer_ip), native_scheme, True, 'Invalid X-Forwarded-For header'
        try:
            chain = [_normalized_ip(entry) for entry in forwarded_entries]
        except ValueError:
            return str(peer_ip), native_scheme, True, 'Invalid X-Forwarded-For header'
        chain.append(peer_ip)
        while len(chain) > 1 and self._is_trusted_proxy(chain[-1]):
            chain.pop()
        effective_ip = chain[-1]

        proto_entries = [entry.strip().lower() for entry in forwarded_proto.split(',')]
        # Requiring the trusted edge to overwrite this header prevents a client-supplied
        # first value from being mistaken for the original transport protocol.
        if len(proto_entries) != 1 or proto_entries[0] not in {'http', 'https'}:
            return str(effective_ip), native_scheme, True, 'Invalid X-Forwarded-Proto header'
        effective_scheme = proto_entries[0]
        return str(effective_ip), effective_scheme, True, None

    def authorize(self, scope: Scope) -> AccessDecision:
        headers = Headers(scope=scope)
        client_host, scheme, via_proxy, proxy_error = self._effective_client(scope, headers)
        base_context = AccessContext(
            authorized=False,
            client_host=client_host,
            scheme=scheme,
            auth_method='',
            via_trusted_proxy=via_proxy,
        )
        if proxy_error:
            return AccessDecision(base_context, 400, proxy_error)
        if is_lan_client_host(client_host):
            return AccessDecision(
                AccessContext(True, client_host, scheme, 'local-network', via_proxy)
            )
        if scheme != 'https':
            return AccessDecision(base_context, 426, 'Public access requires HTTPS')
        if self.shared_token is None:
            return AccessDecision(base_context, 503, 'GOODJOB_SHARED_TOKEN is not configured')

        authorization = headers.get('authorization', '')
        auth_scheme, separator, credential = authorization.partition(' ')
        supplied_token = credential.strip() if separator and auth_scheme.lower() == 'bearer' else ''
        supplied_bytes = supplied_token.encode('utf-8')
        configured_bytes = self.shared_token.encode('utf-8')
        if not supplied_token or not secrets.compare_digest(supplied_bytes, configured_bytes):
            return AccessDecision(base_context, 401, 'Invalid or missing bearer token')
        return AccessDecision(
            AccessContext(True, client_host, scheme, 'bearer', via_proxy)
        )


def is_public_dashboard_request(scope: Scope) -> bool:
    """Allow only the known, read-only Dashboard shell assets without auth."""
    return (
        str(scope.get('method') or '').upper() in {'GET', 'HEAD'}
        and str(scope.get('path') or '') in _PUBLIC_DASHBOARD_PATHS
    )


class HybridAuthMiddleware:
    """Protect every HTTP route except the read-only Dashboard static shell."""

    def __init__(self, app: ASGIApp, policy: SecurityPolicy):
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or is_public_dashboard_request(scope):
            await self.app(scope, receive, send)
            return

        decision = self.policy.authorize(scope)
        if not decision.context.authorized:
            headers = {'WWW-Authenticate': 'Bearer'} if decision.status_code == 401 else None
            response = JSONResponse(
                {'detail': decision.detail},
                status_code=decision.status_code,
                headers=headers,
            )
            await response(scope, receive, send)
            return

        state = scope.setdefault('state', {})
        state['goodjob_access'] = decision.context
        state['goodjob_authorized'] = True
        await self.app(scope, receive, send)
