from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, TypeVar

from anberpod.config import DataPaths
from anberpod.domain.errors import CatalogDataError, CatalogError, HttpPolicyError
from anberpod.domain.models import DiscoveryResult, Podcast
from anberpod.domain.ports import AtomicFilePort, Clock, Logger, PodcastCatalog


T = TypeVar("T")
CATEGORIES_TTL = timedelta(days=7)
SEARCH_TTL = timedelta(days=1)


@dataclass(frozen=True)
class CachedPayload:
    body: bytes
    stale: bool


class CatalogCache:
    """Publish immutable JSON payloads, then atomically point SQLite at them."""

    def __init__(self, paths: DataPaths, connection: sqlite3.Connection, files: AtomicFilePort) -> None:
        self.paths = paths
        self.connection = connection
        self.files = files

    def load(self, key: str, now: datetime) -> CachedPayload | None:
        row = self.connection.execute(
            "SELECT payload_relative_path, expires_at FROM catalog_cache WHERE cache_key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        try:
            body = self.files.read_bytes(row[0])
            expires_at = _datetime(row[1])
        except (OSError, UnicodeError, ValueError):
            return None
        return CachedPayload(body, now >= expires_at)

    def save(self, key: str, body: bytes, fetched_at: datetime, ttl: timedelta) -> None:
        digest = hashlib.sha256(body).hexdigest()
        relative_path = f"cache/catalog/{digest}.json"
        old_row = self.connection.execute(
            "SELECT payload_relative_path FROM catalog_cache WHERE cache_key=?", (key,)
        ).fetchone()
        old_path = old_row[0] if old_row else None
        self.files.write_atomic(relative_path, body)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """INSERT INTO catalog_cache(cache_key, payload_relative_path, fetched_at, expires_at, etag, last_modified)
                VALUES (?, ?, ?, ?, NULL, NULL) ON CONFLICT(cache_key) DO UPDATE SET
                payload_relative_path=excluded.payload_relative_path, fetched_at=excluded.fetched_at,
                expires_at=excluded.expires_at, etag=NULL, last_modified=NULL""",
                (key, relative_path, _text(fetched_at), _text(fetched_at + ttl)),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            if relative_path != old_path:
                self.files.unlink(relative_path)
            raise
        if old_path and old_path != relative_path and not self._is_referenced(old_path):
            self.files.unlink(old_path)

    def _is_referenced(self, path: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM catalog_cache WHERE payload_relative_path=? LIMIT 1", (path,)
        ).fetchone() is not None


class DiscoveryService:
    def __init__(self, catalog: PodcastCatalog, cache: CatalogCache, clock: Clock, logger: Logger) -> None:
        self.catalog = catalog
        self.cache = cache
        self.clock = clock
        self.logger = logger

    def categories(self) -> DiscoveryResult[str]:
        now = _utc(self.clock.now_utc())
        try:
            items = _valid_categories(self.catalog.categories())
        except CatalogDataError:
            raise
        except (CatalogError, HttpPolicyError, OSError, TimeoutError) as exc:
            return self._fallback("categories", now, exc, _decode_categories)
        body = _json_bytes({"version": 1, "kind": "categories", "items": list(items)})
        self.cache.save("categories", body, now, CATEGORIES_TTL)
        return DiscoveryResult(items)

    def search(self, query: str, limit: int = 20) -> DiscoveryResult[Podcast]:
        normalized = " ".join(query.split()).casefold()
        if not normalized or len(normalized) > 200:
            raise ValueError("search query must contain 1 to 200 characters")
        if not 1 <= limit <= 100:
            raise ValueError("search limit must be between 1 and 100")
        key = f"search:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}:{limit}"
        now = _utc(self.clock.now_utc())
        try:
            items = _valid_podcasts(self.catalog.search(normalized, limit), limit)
        except CatalogDataError:
            raise
        except (CatalogError, HttpPolicyError, OSError, TimeoutError) as exc:
            return self._fallback(key, now, exc, lambda body: _decode_search(body, normalized, limit))
        body = _json_bytes({
            "version": 1,
            "kind": "search",
            "query": normalized,
            "limit": limit,
            "items": [_podcast_dict(item) for item in items],
        })
        self.cache.save(key, body, now, SEARCH_TTL)
        return DiscoveryResult(items)

    def _fallback(
        self,
        key: str,
        now: datetime,
        error: Exception,
        decoder: Callable[[bytes], tuple[T, ...]],
    ) -> DiscoveryResult[T]:
        code = getattr(error, "code", "offline")
        cached = self.cache.load(key, now)
        if cached is not None:
            try:
                items = decoder(cached.body)
            except CatalogDataError:
                pass
            else:
                self.logger.info("Catalog request failed; using saved metadata", extra={
                    "event": "catalog_fallback", "error_code": code, "cache_key": key.split(":", 1)[0],
                })
                return DiscoveryResult(items, cached=True, stale=cached.stale, warning_code=code)
        self.logger.info("Catalog request failed without usable saved metadata", extra={
            "event": "catalog_error", "error_code": code, "cache_key": key.split(":", 1)[0],
        })
        raise error


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an aware datetime")
    return value.astimezone(timezone.utc)


def _text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _object(body: bytes, kind: str) -> Mapping[str, Any]:
    try:
        value = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogDataError("Saved catalog metadata is invalid") from exc
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("kind") != kind:
        raise CatalogDataError("Saved catalog metadata has an invalid shape")
    return value


def _valid_categories(values: object) -> tuple[str, ...]:
    if not isinstance(values, list) or len(values) > 256:
        raise CatalogDataError("Catalog categories are invalid")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            raise CatalogDataError("Catalog category is invalid")
        result.append(value.strip())
    if len(set(result)) != len(result):
        raise CatalogDataError("Catalog categories contain duplicates")
    return tuple(result)


def _decode_categories(body: bytes) -> tuple[str, ...]:
    value = _object(body, "categories")
    return _valid_categories(value.get("items"))


def _valid_podcasts(values: object, limit: int) -> tuple[Podcast, ...]:
    if not isinstance(values, list) or len(values) > limit:
        raise CatalogDataError("Catalog search results are invalid")
    result: list[Podcast] = []
    for value in values:
        if not isinstance(value, Podcast) or value.catalog_id is None or value.catalog_id <= 0:
            raise CatalogDataError("Catalog podcast is invalid")
        if not value.id == f"catalog:{value.catalog_id}" or not value.title.strip():
            raise CatalogDataError("Catalog podcast is invalid")
        if not value.feed_url.startswith("https://"):
            raise CatalogDataError("Catalog podcast feed URL must use HTTPS")
        result.append(value)
    return tuple(result)


def _podcast_dict(value: Podcast) -> dict[str, Any]:
    return {
        "id": value.id,
        "catalog_id": value.catalog_id,
        "feed_url": value.feed_url,
        "title": value.title,
        "author": value.author,
        "description": value.description,
        "image_url": value.image_url,
        "language": value.language,
    }


def _decode_search(body: bytes, query: str, limit: int) -> tuple[Podcast, ...]:
    value = _object(body, "search")
    if value.get("query") != query or value.get("limit") != limit or not isinstance(value.get("items"), list):
        raise CatalogDataError("Saved search metadata does not match the request")
    podcasts: list[Podcast] = []
    try:
        for item in value["items"]:
            if not isinstance(item, dict):
                raise CatalogDataError("Saved podcast is invalid")
            podcasts.append(Podcast(**item))
    except (TypeError, ValueError) as exc:
        raise CatalogDataError("Saved podcast is invalid") from exc
    return _valid_podcasts(podcasts, limit)
