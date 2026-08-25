from __future__ import annotations

import configparser
import hashlib
import json
from datetime import timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit

from anberpod import __version__
from anberpod.domain.errors import (
    CatalogAuthenticationError,
    CatalogClockError,
    CatalogDataError,
    CatalogRateLimitError,
    CatalogUnavailableError,
    MissingCatalogCredentialsError,
)
from anberpod.domain.models import CatalogCredentials, Podcast, RequestPolicy
from anberpod.domain.ports import Clock, CredentialProvider, HttpTransport


API_ROOT = "https://api.podcastindex.org/api/1.0"
CATALOG_POLICY = RequestPolicy(max_bytes=2 * 1024 * 1024)
MAX_CATEGORIES = 256
MAX_SEARCH_RESULTS = 100


class LocalCatalogCredentials:
    """Read Podcast Index keys from the persistent user data directory."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def podcast_index(self) -> CatalogCredentials | None:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                parser.read_file(stream)
        except (OSError, UnicodeError, configparser.Error):
            return None
        if not parser.has_section("podcast_index"):
            return None
        key = _unquote(parser.get("podcast_index", "api_key", fallback="")).strip()
        secret = _unquote(parser.get("podcast_index", "api_secret", fallback="")).strip()
        if not key or not secret:
            return None
        return CatalogCredentials(key, secret)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


class PodcastIndexClient:
    def __init__(
        self,
        transport: HttpTransport,
        credentials: CredentialProvider,
        clock: Clock,
        *,
        api_root: str = API_ROOT,
        user_agent: str = f"AnberPod/{__version__}",
    ) -> None:
        self.transport = transport
        self.credentials = credentials
        self.clock = clock
        self.api_root = api_root.rstrip("/")
        self.user_agent = user_agent

    def _headers(self) -> dict[str, str]:
        credentials = self.credentials.podcast_index()
        if credentials is None:
            raise MissingCatalogCredentialsError("Podcast Index credentials are not configured")
        now = self.clock.now_utc()
        if now.tzinfo is None or now.utcoffset() is None:
            raise CatalogClockError("Device clock must provide UTC time")
        timestamp = str(int(now.astimezone(timezone.utc).timestamp()))
        if int(timestamp) <= 0:
            raise CatalogClockError("Device clock is invalid")
        token = hashlib.sha1(
            f"{credentials.api_key}{credentials.api_secret}{timestamp}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        return {
            "Authorization": token,
            "User-Agent": self.user_agent,
            "X-Auth-Date": timestamp,
            "X-Auth-Key": credentials.api_key,
        }

    def _get(self, path: str, parameters: Mapping[str, str] | None = None) -> Any:
        url = f"{self.api_root}/{path.lstrip('/')}"
        if parameters:
            url = f"{url}?{urlencode(parameters)}"
        response = self.transport.request(CATALOG_POLICY, url, self._headers())
        if response.status in {401, 403}:
            raise CatalogAuthenticationError("Podcast Index rejected the configured credentials")
        if response.status == 429:
            retry = _header(response.headers, "retry-after")
            retry_seconds = int(retry) if retry and retry.isdigit() else None
            if retry_seconds is not None:
                retry_seconds = min(retry_seconds, 3600)
            raise CatalogRateLimitError(retry_seconds)
        if response.status >= 500:
            raise CatalogUnavailableError("Podcast Index is temporarily unavailable")
        if response.status != 200:
            raise CatalogUnavailableError(f"Podcast Index returned HTTP {response.status}")
        try:
            return json.loads(response.body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CatalogDataError("Podcast Index returned invalid JSON") from exc

    def categories(self) -> list[str]:
        payload = self._get("categories/list")
        feeds = _list_field(payload, "feeds", MAX_CATEGORIES)
        names: list[str] = []
        for item in feeds:
            if not isinstance(item, dict):
                raise CatalogDataError("Podcast Index category is invalid")
            name = item.get("name")
            identifier = item.get("id")
            if not _integer(identifier) or not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise CatalogDataError("Podcast Index category is invalid")
            names.append(name.strip())
        if len(set(names)) != len(names):
            raise CatalogDataError("Podcast Index categories contain duplicates")
        return sorted(names, key=str.casefold)

    def search(self, query: str, limit: int) -> list[Podcast]:
        clean_query = query.strip()
        if not clean_query or len(clean_query) > 200:
            raise ValueError("search query must contain 1 to 200 characters")
        if not 1 <= limit <= MAX_SEARCH_RESULTS:
            raise ValueError("search limit must be between 1 and 100")
        payload = self._get("search/byterm", {"q": clean_query, "max": str(limit)})
        return [_podcast(item) for item in _list_field(payload, "feeds", limit)]

    def podcast(self, feed_id: int) -> Podcast | None:
        if not _integer(feed_id) or feed_id <= 0:
            raise ValueError("feed_id must be a positive integer")
        feeds = _list_field(self._get("podcasts/byfeedid", {"id": str(feed_id)}), "feed", 1)
        return _podcast(feeds[0]) if feeds else None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return next((value for key, value in headers.items() if key.lower() == name), None)


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _list_field(payload: Any, field: str, maximum: int) -> list[Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
        raise CatalogDataError("Podcast Index response has an invalid shape")
    values = payload[field]
    if len(values) > maximum:
        raise CatalogDataError("Podcast Index response contains too many results")
    return values


def _optional_text(item: Mapping[str, Any], key: str, maximum: int) -> str | None:
    value = item.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise CatalogDataError(f"Podcast Index {key} is invalid")
    return value


def _optional_https(item: Mapping[str, Any], key: str) -> str | None:
    value = _optional_text(item, key, 2048)
    if value is not None and urlsplit(value).scheme.lower() != "https":
        raise CatalogDataError(f"Podcast Index {key} must use HTTPS")
    return value


def _podcast(value: Any) -> Podcast:
    if not isinstance(value, dict):
        raise CatalogDataError("Podcast Index feed is invalid")
    identifier = value.get("id")
    feed_url = value.get("url")
    title = value.get("title")
    if not _integer(identifier) or identifier <= 0:
        raise CatalogDataError("Podcast Index feed id is invalid")
    if not isinstance(feed_url, str) or not feed_url or len(feed_url) > 2048 or urlsplit(feed_url).scheme != "https":
        raise CatalogDataError("Podcast Index feed URL must use HTTPS")
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise CatalogDataError("Podcast Index feed title is invalid")
    return Podcast(
        id=f"catalog:{identifier}",
        feed_url=feed_url,
        title=title.strip(),
        author=_optional_text(value, "author", 300),
        description=_optional_text(value, "description", 10_000),
        image_url=_optional_https(value, "image"),
        language=_optional_text(value, "language", 32),
        catalog_id=identifier,
    )
