from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from anberpod.adapters.podcast_index import LocalCatalogCredentials, PodcastIndexClient
from anberpod.domain.errors import (
    CatalogAuthenticationError,
    CatalogDataError,
    CatalogRateLimitError,
    CatalogUnavailableError,
    MissingCatalogCredentialsError,
)
from anberpod.domain.models import CatalogCredentials, HttpResponse, RequestPolicy


NOW = datetime(2026, 8, 25, 12, 24, 5, tzinfo=timezone.utc)


class FixedClock:
    def now_utc(self) -> datetime:
        return NOW


class Credentials:
    def __init__(self, value: CatalogCredentials | None) -> None:
        self.value = value

    def podcast_index(self) -> CatalogCredentials | None:
        return self.value


class RecordingTransport:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[RequestPolicy, str, dict[str, str]]] = []

    def request(self, policy: RequestPolicy, url: str, headers: dict[str, str]) -> HttpResponse:
        self.requests.append((policy, url, dict(headers)))
        return self.responses.pop(0)


def response(payload: object, status: int = 200, headers: dict[str, str] | None = None) -> HttpResponse:
    return HttpResponse(status, headers or {}, json.dumps(payload).encode("utf-8"))


def client(transport: RecordingTransport, credentials: CatalogCredentials | None = None) -> PodcastIndexClient:
    return PodcastIndexClient(
        transport,
        Credentials(credentials or CatalogCredentials("test-key", "test-secret")),
        FixedClock(),
    )


def test_local_catalog_credentials_come_from_user_data_config(tmp_path: Path) -> None:
    config = tmp_path / "user-data" / "config" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[podcast_index]\napi_key = "local-key"\napi_secret = "local-secret"\n',
        encoding="utf-8",
    )

    loaded = LocalCatalogCredentials(config).podcast_index()

    assert loaded == CatalogCredentials("local-key", "local-secret")
    assert LocalCatalogCredentials(tmp_path / "missing.toml").podcast_index() is None


def test_podcast_index_signature_matches_fixed_vector() -> None:
    transport = RecordingTransport(response({"feeds": []}))

    assert client(transport).search("science & nature", 10) == []

    policy, url, headers = transport.requests[0]
    assert policy.max_bytes == 2 * 1024 * 1024
    assert url == "https://api.podcastindex.org/api/1.0/search/byterm?q=science+%26+nature&max=10"
    assert headers == {
        "Authorization": "7feabaa5d319877d105ab76b8576ac2b8f09b32a",
        "User-Agent": "AnberPod/0.1.0",
        "X-Auth-Date": "1787660645",
        "X-Auth-Key": "test-key",
    }


def test_client_validates_categories_and_search_results() -> None:
    transport = RecordingTransport(
        response({"feeds": [{"id": 2, "name": "Technology"}, {"id": 1, "name": "Arts"}]}),
        response({"feeds": [{
            "id": 42,
            "url": "https://feeds.example.test/show.xml",
            "title": "A Show",
            "author": "A Person",
            "description": "Synthetic result",
            "image": "https://images.example.test/show.png",
            "language": "en",
        }]}),
    )
    catalog = client(transport)

    assert catalog.categories() == ["Arts", "Technology"]
    podcasts = catalog.search("show", 5)
    assert len(podcasts) == 1
    assert podcasts[0].catalog_id == 42
    assert podcasts[0].feed_url == "https://feeds.example.test/show.xml"
    assert podcasts[0].title == "A Show"
    assert podcasts[0].id == "catalog:42"


@pytest.mark.parametrize(
    "payload",
    [
        {"feeds": "not-a-list"},
        {"feeds": [{"id": True, "url": "https://feeds.example.test/feed", "title": "Bad id"}]},
        {"feeds": [{"id": 1, "url": "http://feeds.example.test/feed", "title": "Insecure"}]},
        {"feeds": [{"id": 1, "url": "https://feeds.example.test/feed", "title": ""}]},
        {"feeds": [{"id": index, "url": f"https://feeds.example.test/{index}", "title": "Too many"}
                   for index in range(3)]},
    ],
)
def test_bad_search_data_is_rejected_without_partial_results(payload: object) -> None:
    catalog = client(RecordingTransport(response(payload)))

    with pytest.raises(CatalogDataError):
        catalog.search("bad", 2)


def test_catalog_status_errors_are_typed_and_rate_limit_does_not_retry() -> None:
    authentication = RecordingTransport(response({}, 401))
    with pytest.raises(CatalogAuthenticationError):
        client(authentication).categories()
    assert len(authentication.requests) == 1

    limited = RecordingTransport(response({}, 429, {"Retry-After": "120"}))
    with pytest.raises(CatalogRateLimitError) as caught:
        client(limited).categories()
    assert caught.value.retry_after_seconds == 120
    assert len(limited.requests) == 1

    unavailable = RecordingTransport(response({}, 503))
    with pytest.raises(CatalogUnavailableError):
        client(unavailable).categories()
    assert len(unavailable.requests) == 1


def test_missing_credentials_fail_before_transport_and_do_not_expose_values() -> None:
    transport = RecordingTransport()
    catalog = PodcastIndexClient(transport, Credentials(None), FixedClock())

    with pytest.raises(MissingCatalogCredentialsError) as caught:
        catalog.categories()

    assert str(caught.value) == "Podcast Index credentials are not configured"
    assert transport.requests == []
