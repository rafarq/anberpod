from __future__ import annotations

import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

from anberpod.domain.errors import HttpPolicyError
from anberpod.domain.models import HttpResponse, RequestPolicy


@dataclass(frozen=True)
class AdapterResponse:
    status: int
    headers: Mapping[str, str]
    body_chunks: Iterable[bytes]


class HttpAdapter(Protocol):
    def open(
        self,
        url: str,
        headers: Mapping[str, str],
        policy: RequestPolicy,
        approved_addresses: tuple[str, ...],
    ) -> AdapterResponse: ...


AddressResolver = Callable[[str, int], tuple[str, ...]]


def system_public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        addresses = tuple(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except socket.gaierror as exc:
        raise HttpPolicyError("host resolution failed", code="dns_failed") from exc
    return addresses


def _public(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def normalize_public_url(url: str, resolver: AddressResolver) -> tuple[str, tuple[str, ...]]:
    if len(url) > 2048:
        raise HttpPolicyError("URL is too long", code="url_too_long")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise HttpPolicyError("invalid URL", code="invalid_url") from exc
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise HttpPolicyError("unsupported URL scheme", code="unsupported_scheme")
    if parts.username is not None or parts.password is not None:
        raise HttpPolicyError("URL credentials are forbidden", code="url_credentials")
    if not parts.hostname:
        raise HttpPolicyError("URL host is required", code="missing_host")
    if parts.fragment:
        raise HttpPolicyError("URL fragments are forbidden", code="url_fragment")
    host = parts.hostname.lower()
    effective_port = port or (443 if scheme == "https" else 80)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        addresses = resolver(host, effective_port)
    else:
        addresses = (str(literal),)
    if not addresses or any(not _public(address) for address in addresses):
        raise HttpPolicyError("target address is not public", code="non_public_address")
    authority = host
    if ":" in host:
        authority = f"[{host}]"
    if port is not None and port != (443 if scheme == "https" else 80):
        authority = f"{authority}:{port}"
    normalized = urlunsplit((scheme, authority, parts.path or "/", parts.query, ""))
    return normalized, addresses


class PolicyHttpTransport:
    def __init__(
        self,
        adapter: HttpAdapter,
        resolver: AddressResolver = system_public_addresses,
        *,
        max_redirects: int = 5,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.adapter = adapter
        self.resolver = resolver
        self.max_redirects = max_redirects
        self.monotonic = monotonic

    def request(self, policy: RequestPolicy, url: str, headers: Mapping[str, str]) -> HttpResponse:
        started = self.monotonic()

        def check_deadline() -> None:
            if (
                policy.total_timeout_seconds is not None
                and self.monotonic() - started > policy.total_timeout_seconds
            ):
                raise HttpPolicyError("request exceeded total time limit", code="total_timeout")

        current, addresses = normalize_public_url(url, self.resolver)
        initial_scheme = urlsplit(current).scheme
        for redirects in range(self.max_redirects + 1):
            check_deadline()
            response = self.adapter.open(current, headers, policy, addresses)
            if response.status in {301, 302, 303, 307, 308}:
                location = next((value for key, value in response.headers.items() if key.lower() == "location"), None)
                if not location:
                    raise HttpPolicyError("redirect has no location", code="invalid_redirect")
                if redirects == self.max_redirects:
                    raise HttpPolicyError("redirect limit exceeded", code="redirect_limit")
                target, addresses = normalize_public_url(urljoin(current, location), self.resolver)
                if initial_scheme == "https" and urlsplit(target).scheme != "https":
                    raise HttpPolicyError("HTTPS redirect downgrade is forbidden", code="https_downgrade")
                current = target
                continue
            encoding = next(
                (value.strip().lower() for key, value in response.headers.items() if key.lower() == "content-encoding"),
                "identity",
            )
            if encoding not in {"", "identity", "gzip", "deflate"}:
                raise HttpPolicyError("unsupported content encoding", code="unsupported_encoding")
            decompressor = None
            if encoding == "gzip":
                decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            elif encoding == "deflate":
                decompressor = zlib.decompressobj()
            body = bytearray()
            try:
                for chunk in response.body_chunks:
                    check_deadline()
                    decoded = decompressor.decompress(chunk) if decompressor is not None else chunk
                    body.extend(decoded)
                    if len(body) > policy.max_bytes:
                        raise HttpPolicyError("response body exceeds limit", code="body_too_large")
                if decompressor is not None:
                    body.extend(decompressor.flush())
                check_deadline()
            except zlib.error as exc:
                raise HttpPolicyError("invalid compressed response", code="invalid_encoding") from exc
            if len(body) > policy.max_bytes:
                raise HttpPolicyError("response body exceeds limit", code="body_too_large")
            return HttpResponse(response.status, response.headers, bytes(body))
        raise AssertionError("unreachable")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class UrllibHttpAdapter:
    """Production byte-stream adapter; policy and DNS decisions remain injectable in tests."""

    def __init__(self, ca_file: str | None = None, chunk_size: int = 64 * 1024) -> None:
        context = ssl.create_default_context(cafile=ca_file)
        self.opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context))
        self.chunk_size = chunk_size

    def open(self, url, headers, policy, approved_addresses):  # type: ignore[no-untyped-def]
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            stream = self.opener.open(request, timeout=policy.connect_timeout_seconds)
        except urllib.error.HTTPError as exc:
            stream = exc
        peer = getattr(getattr(getattr(stream, "fp", None), "raw", None), "_sock", None)
        if peer is not None:
            peer_address = peer.getpeername()[0]
            if peer_address not in approved_addresses:
                stream.close()
                raise HttpPolicyError("connected address was not approved", code="dns_rebinding")
            peer.settimeout(policy.read_timeout_seconds)

        def chunks():  # type: ignore[no-untyped-def]
            with stream:
                while True:
                    chunk = stream.read(self.chunk_size)
                    if not chunk:
                        break
                    yield chunk

        return AdapterResponse(stream.status, dict(stream.headers.items()), chunks())
