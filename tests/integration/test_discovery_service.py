from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.adapters.sqlite import Repositories
from anberpod.config import DataPaths
from anberpod.domain.errors import CatalogDataError, CatalogUnavailableError
from anberpod.domain.models import Podcast
from anberpod.logging import configure_logging
from anberpod.services.discovery import CatalogCache, DiscoveryService


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self) -> None:
        self.value = NOW

    def now_utc(self) -> datetime:
        return self.value


class FakeCatalog:
    def __init__(self) -> None:
        self.category_values: list[object] = []
        self.search_values: list[object] = []

    def categories(self) -> list[str]:
        value = self.category_values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value  # type: ignore[return-value]

    def search(self, query: str, limit: int) -> list[Podcast]:
        value = self.search_values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value  # type: ignore[return-value]

    def podcast(self, feed_id: int) -> Podcast | None:
        return None


def service(tmp_path: Path, catalog: FakeCatalog, clock: MutableClock) -> tuple[DiscoveryService, Repositories, DataPaths]:
    paths = DataPaths.create(tmp_path / "data")
    repositories = Repositories.open(paths.database)
    cache = CatalogCache(paths, repositories.database.connection, AtomicFiles(paths.root))
    logger = configure_logging(paths.logs / "anberpod.log")
    return DiscoveryService(catalog, cache, clock, logger), repositories, paths


def podcast(identifier: int, title: str) -> Podcast:
    return Podcast(
        f"catalog:{identifier}",
        f"https://feeds.example.test/{identifier}.xml",
        title,
        catalog_id=identifier,
    )


def test_category_error_falls_back_to_validated_atomic_cache_for_offline_browsing(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.category_values = [["Arts", "Science"], CatalogUnavailableError("offline")]
    clock = MutableClock()
    discovery, repositories, paths = service(tmp_path, catalog, clock)

    fresh = discovery.categories()
    row_before = repositories.database.connection.execute(
        "SELECT payload_relative_path, fetched_at, expires_at FROM catalog_cache WHERE cache_key='categories'"
    ).fetchone()
    clock.value += timedelta(days=8)
    saved = discovery.categories()

    assert fresh.items == ("Arts", "Science") and fresh.cached is False
    assert saved.items == fresh.items
    assert saved.cached is True and saved.stale is True
    assert saved.warning_code == "catalog_unavailable"
    assert row_before is not None
    payload_path = paths.resolve_relative(row_before[0])
    assert payload_path.is_file()
    assert json.loads(payload_path.read_text(encoding="utf-8"))["kind"] == "categories"


def test_search_error_falls_back_only_to_matching_cached_query(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.search_values = [[podcast(7, "Space Cast")], OSError("network is down"), OSError("network is down")]
    clock = MutableClock()
    discovery, _repositories, _paths = service(tmp_path, catalog, clock)

    online = discovery.search("  Space  ", 10)
    cached = discovery.search("space", 10)

    assert online.items == (podcast(7, "Space Cast"),)
    assert cached.items == online.items
    assert cached.cached is True
    with pytest.raises(OSError, match="network is down"):
        discovery.search("different", 10)


def test_invalid_new_metadata_never_replaces_last_valid_cache(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.category_values = [["Arts"], ["Arts", "", "Science"]]
    clock = MutableClock()
    discovery, repositories, paths = service(tmp_path, catalog, clock)
    discovery.categories()
    before = repositories.database.connection.execute(
        "SELECT payload_relative_path FROM catalog_cache WHERE cache_key='categories'"
    ).fetchone()[0]
    before_bytes = paths.resolve_relative(before).read_bytes()

    with pytest.raises(CatalogDataError):
        discovery.categories()

    after = repositories.database.connection.execute(
        "SELECT payload_relative_path FROM catalog_cache WHERE cache_key='categories'"
    ).fetchone()[0]
    assert after == before
    assert paths.resolve_relative(after).read_bytes() == before_bytes


def test_corrupt_search_cache_is_rejected_instead_of_returned(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.search_values = [[podcast(7, "Space Cast")], CatalogUnavailableError("offline")]
    clock = MutableClock()
    discovery, repositories, paths = service(tmp_path, catalog, clock)
    discovery.search("space", 10)
    row = repositories.database.connection.execute(
        "SELECT payload_relative_path FROM catalog_cache WHERE cache_key LIKE 'search:%'"
    ).fetchone()
    paths.resolve_relative(row[0]).write_bytes(b'{"kind":"search","items":[{"id":true}]}')

    with pytest.raises(CatalogUnavailableError, match="offline"):
        discovery.search("space", 10)


def test_catalog_failure_logging_contains_no_secret_authorization_or_query(tmp_path: Path) -> None:
    catalog = FakeCatalog()
    catalog.search_values = [CatalogUnavailableError("safe failure")]
    clock = MutableClock()
    discovery, _repositories, paths = service(tmp_path, catalog, clock)

    with pytest.raises(CatalogUnavailableError):
        discovery.search("private search words", 10)

    rendered = (paths.logs / "anberpod.log").read_text(encoding="utf-8")
    assert "private search words" not in rendered
    assert "api_secret" not in rendered.lower()
    assert "authorization" not in rendered.lower()
    assert "catalog_unavailable" in rendered
