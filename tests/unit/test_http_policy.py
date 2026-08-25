from __future__ import annotations

import gzip

import pytest

from anberpod.adapters.http import AdapterResponse, PolicyHttpTransport
from anberpod.domain.errors import HttpPolicyError
from anberpod.domain.models import RequestPolicy


class FakeAdapter:
    def __init__(self, responses: list[AdapterResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, RequestPolicy, tuple[str, ...]]] = []

    def open(self, url, headers, policy, approved_addresses):  # type: ignore[no-untyped-def]
        self.calls.append((url, policy, approved_addresses))
        return self.responses.pop(0)


def public_resolver(host: str, port: int) -> tuple[str, ...]:
    assert host
    assert port in {80, 443, 8443}
    return ("93.184.216.34",)


def test_http_policy_revalidates_redirects_and_passes_timeouts_to_adapter() -> None:
    adapter = FakeAdapter([
        AdapterResponse(302, {"Location": "https://cdn.example.test/feed"}, [b""]),
        AdapterResponse(200, {"Content-Type": "application/rss+xml"}, [b"abc", b"def"]),
    ])
    policy = RequestPolicy(connect_timeout_seconds=1, read_timeout_seconds=2, total_timeout_seconds=3, max_bytes=6)

    response = PolicyHttpTransport(adapter, public_resolver).request(policy, "https://example.test/feed", {})

    assert response.body == b"abcdef"
    assert [call[0] for call in adapter.calls] == [
        "https://example.test/feed", "https://cdn.example.test/feed"
    ]
    assert adapter.calls[0][1] == policy
    assert adapter.calls[0][2] == ("93.184.216.34",)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("ftp://example.test/feed", "unsupported_scheme"),
        ("https://user:pass@example.test/feed", "url_credentials"),
        ("https:///feed", "missing_host"),
        ("https://127.0.0.1/feed", "non_public_address"),
    ],
)
def test_http_policy_rejects_unsafe_targets_before_adapter(url: str, code: str) -> None:
    adapter = FakeAdapter([])

    with pytest.raises(HttpPolicyError) as caught:
        PolicyHttpTransport(adapter, public_resolver).request(RequestPolicy(), url, {})

    assert caught.value.code == code
    assert adapter.calls == []


def test_http_policy_rejects_https_downgrade_redirect_limit_and_oversized_stream() -> None:
    downgrade = FakeAdapter([AdapterResponse(302, {"Location": "http://example.test/feed"}, [])])
    with pytest.raises(HttpPolicyError, match="downgrade"):
        PolicyHttpTransport(downgrade, public_resolver).request(RequestPolicy(), "https://example.test/feed", {})

    looping = FakeAdapter([AdapterResponse(302, {"Location": "/again"}, []) for _ in range(6)])
    with pytest.raises(HttpPolicyError) as caught:
        PolicyHttpTransport(looping, public_resolver, max_redirects=5).request(
            RequestPolicy(), "https://example.test/feed", {}
        )
    assert caught.value.code == "redirect_limit"

    oversized = FakeAdapter([AdapterResponse(200, {}, [b"1234", b"5678"])])
    with pytest.raises(HttpPolicyError) as caught:
        PolicyHttpTransport(oversized, public_resolver).request(
            RequestPolicy(max_bytes=7), "http://example.test/feed", {}
        )
    assert caught.value.code == "body_too_large"


def test_http_policy_rejects_private_address_returned_by_validation_hook() -> None:
    adapter = FakeAdapter([])

    with pytest.raises(HttpPolicyError) as caught:
        PolicyHttpTransport(adapter, lambda host, port: ("10.0.0.8",)).request(
            RequestPolicy(), "https://example.test/feed", {}
        )

    assert caught.value.code == "non_public_address"


def test_http_policy_limits_decompressed_body_and_rejects_unknown_encoding() -> None:
    compressed = gzip.compress(b"x" * 100)
    adapter = FakeAdapter([AdapterResponse(200, {"Content-Encoding": "gzip"}, [compressed])])

    with pytest.raises(HttpPolicyError) as caught:
        PolicyHttpTransport(adapter, public_resolver).request(
            RequestPolicy(max_bytes=50), "https://example.test/feed", {}
        )
    assert caught.value.code == "body_too_large"

    unknown = FakeAdapter([AdapterResponse(200, {"Content-Encoding": "br"}, [b"small"])])
    with pytest.raises(HttpPolicyError) as caught:
        PolicyHttpTransport(unknown, public_resolver).request(
            RequestPolicy(max_bytes=50), "https://example.test/feed", {}
        )
    assert caught.value.code == "unsupported_encoding"
