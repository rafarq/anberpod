from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from anberpod.adapters.sqlite import Database, Repositories, derive_source_key
from anberpod.domain.errors import MigrationError
from anberpod.domain.models import Download, DownloadState, Episode, Playback, Podcast


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def podcast() -> Podcast:
    return Podcast("pod-1", "https://example.test/feed", "Local Show", created_at=NOW, updated_at=NOW)


def episode() -> Episode:
    return Episode(
        "ep-1", "pod-1", "guid:episode-guid", "https://cdn.example.test/one.mp3", "One",
        guid="episode-guid", created_at=NOW, updated_at=NOW,
    )


def test_schema_migrates_empty_db_and_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "anberpod.sqlite3")
    database.migrate()
    first = database.connection.execute("SELECT version FROM schema_migration").fetchall()
    database.migrate()
    second = database.connection.execute("SELECT version FROM schema_migration").fetchall()

    tables = {
        row[0]
        for row in database.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert first == second == [(1,)]
    assert {"podcast", "subscription", "episode", "playback", "download", "catalog_cache", "setting"} <= tables
    assert database.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert database.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_failed_migration_rolls_back_and_preserves_backup(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_ok.sql").write_text("CREATE TABLE stable(value TEXT);", encoding="utf-8")
    database = Database(tmp_path / "state.sqlite3", migrations)
    database.migrate()
    database.connection.execute("INSERT INTO stable VALUES ('keep-me')")
    database.connection.commit()
    (migrations / "002_bad.sql").write_text(
        "CREATE TABLE should_rollback(value TEXT);\nTHIS IS NOT SQL;", encoding="utf-8"
    )

    with pytest.raises(MigrationError):
        database.migrate()

    assert database.connection.execute("SELECT value FROM stable").fetchone()[0] == "keep-me"
    assert database.connection.execute(
        "SELECT name FROM sqlite_master WHERE name='should_rollback'"
    ).fetchone() is None
    backups = list(tmp_path.glob("state.sqlite3.backup-*"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT value FROM stable").fetchone()[0] == "keep-me"


def test_unsubscribe_preserves_episodes_playback_and_downloads(tmp_path: Path) -> None:
    repos = Repositories.open(tmp_path / "state.sqlite3")
    repos.podcasts.save(podcast())
    repos.podcasts.subscribe("pod-1", NOW)
    repos.episodes.upsert(episode())
    repos.playback.save(Playback("ep-1", 25_000, 60_000, updated_at=NOW))
    repos.downloads.save(Download("ep-1", DownloadState.QUEUED, created_at=NOW, updated_at=NOW))

    repos.podcasts.unsubscribe("pod-1")

    assert repos.podcasts.list_subscribed() == []
    assert repos.episodes.list_for_podcast("pod-1") == [episode()]
    assert repos.playback.get("ep-1") is not None
    assert repos.downloads.get("ep-1") is not None


def test_delete_download_preserves_subscription_and_playback(tmp_path: Path) -> None:
    repos = Repositories.open(tmp_path / "state.sqlite3")
    repos.podcasts.save(podcast())
    repos.podcasts.subscribe("pod-1", NOW)
    repos.episodes.upsert(episode())
    repos.playback.save(Playback("ep-1", 10_000, updated_at=NOW))
    repos.downloads.save(Download("ep-1", DownloadState.QUEUED, created_at=NOW, updated_at=NOW))

    repos.downloads.delete("ep-1")

    assert [item.id for item in repos.podcasts.list_subscribed()] == ["pod-1"]
    assert repos.playback.get("ep-1").position_ms == 10_000  # type: ignore[union-attr]
    assert repos.downloads.get("ep-1") is None


def test_episode_upsert_uses_guid_url_then_fallback_key() -> None:
    assert derive_source_key(" abc ", "https://EXAMPLE.test:443/a.mp3?b=2&a=1", "T", NOW) == "guid:abc"
    assert derive_source_key(None, "https://EXAMPLE.test:443/a.mp3?b=2&a=1", "T", NOW) == (
        "url:https://example.test/a.mp3?a=1&b=2"
    )
    one = derive_source_key(None, "", "Same", NOW)
    two = derive_source_key(None, "", "Same", NOW)
    assert one == two
    assert one.startswith("fallback:")
