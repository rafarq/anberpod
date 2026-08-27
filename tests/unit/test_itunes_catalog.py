from __future__ import annotations

import json

from anberpod.adapters.itunes import CATEGORIES, ITunesCatalogClient
from anberpod.domain.errors import CatalogDataError, CatalogUnavailableError
from anberpod.domain.models import HttpResponse, RequestPolicy


class RecordingTransport:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[RequestPolicy, str, dict[str, str]]] = []

    def request(self, policy: RequestPolicy, url: str, headers: dict[str, str]) -> HttpResponse:
        self.requests.append((policy, url, dict(headers)))
        return self.responses.pop(0)


def response(payload: object, status: int = 200) -> HttpResponse:
    return HttpResponse(status, {}, json.dumps(payload).encode("utf-8"))


def test_categories_are_hardcoded_and_require_no_network_call() -> None:
    transport = RecordingTransport()
    client = ITunesCatalogClient(transport)

    categories = client.categories()

    assert categories == list(CATEGORIES)
    assert transport.requests == []


def test_search_requires_no_authentication_headers() -> None:
    payload = {
        "results": [
            {
                "kind": "podcast",
                "collectionId": 12345,
                "collectionName": "A Great Show",
                "artistName": "Jane Doe",
                "feedUrl": "https://feeds.example.test/show.xml",
                "artworkUrl600": "https://img.example.test/art.jpg",
            }
        ]
    }
    transport = RecordingTransport(response(payload))
    client = ITunesCatalogClient(transport)

    results = client.search("great show", limit=10)

    assert len(results) == 1
    assert results[0].id == "catalog:12345"
    assert results[0].feed_url == "https://feeds.example.test/show.xml"
    assert results[0].title == "A Great Show"
    assert results[0].author == "Jane Doe"

    _, url, headers = transport.requests[0]
    assert "itunes.apple.com" in url
    assert set(headers) == {"User-Agent"}  # no X-Auth-Key, no Authorization, no api key at all


def test_search_skips_malformed_entries_without_discarding_the_whole_page() -> None:
    payload = {
        "results": [
            {"kind": "podcast", "collectionId": 1, "collectionName": "Valid", "feedUrl": "https://feeds.example.test/a.xml"},
            {"kind": "podcast", "collectionId": 2, "collectionName": "Missing feed"},
            {"kind": "episode", "collectionId": 3, "collectionName": "Not a podcast"},
            {"kind": "podcast", "collectionId": 4, "collectionName": "Insecure", "feedUrl": "http://feeds.example.test/b.xml"},
        ]
    }
    transport = RecordingTransport(response(payload))
    client = ITunesCatalogClient(transport)

    results = client.search("anything", limit=10)

    assert [item.title for item in results] == ["Valid"]


def test_search_rejects_invalid_query_and_limit() -> None:
    client = ITunesCatalogClient(RecordingTransport())
    try:
        client.search("", limit=10)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        client.search("ok", limit=0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_server_error_raises_catalog_unavailable() -> None:
    transport = RecordingTransport(response({}, status=503))
    client = ITunesCatalogClient(transport)
    try:
        client.search("news", limit=5)
        raise AssertionError("expected CatalogUnavailableError")
    except CatalogUnavailableError:
        pass


def test_invalid_json_raises_catalog_data_error() -> None:
    transport = RecordingTransport(HttpResponse(200, {}, b"not json"))
    client = ITunesCatalogClient(transport)
    try:
        client.search("news", limit=5)
        raise AssertionError("expected CatalogDataError")
    except CatalogDataError:
        pass
