from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from anberpod.adapters.sqlite import Repositories
from anberpod.config import DataPaths
from anberpod.domain.models import Download, DownloadState, Episode, Podcast
from anberpod.services.startup import recover_local_state


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_interrupted_download_is_not_playable_and_can_retry(tmp_path: Path) -> None:
    paths = DataPaths.create(tmp_path / "data")
    repos = Repositories.open(paths.database)
    repos.podcasts.save(Podcast("pod", "https://example.test/feed", "Saved", created_at=NOW, updated_at=NOW))
    repos.episodes.upsert(Episode(
        "ep", "pod", "guid:ep", "https://example.test/ep.mp3", "Ep", created_at=NOW, updated_at=NOW
    ))
    part = paths.downloads / "ep.part"
    part.write_bytes(b"partial bytes")
    repos.downloads.save(Download(
        "ep", DownloadState.DOWNLOADING, temp_relative_path="downloads/ep.part",
        bytes_received=13, created_at=NOW, updated_at=NOW,
    ))

    report = recover_local_state(paths, repos)

    recovered = repos.downloads.get("ep")
    assert report.interrupted_downloads == 1
    assert recovered is not None
    assert recovered.state is DownloadState.FAILED
    assert recovered.error_code == "interrupted"
    assert recovered.temp_relative_path == "downloads/ep.part"
    assert recovered.relative_path is None
    assert part.exists()


def test_corrupt_cache_does_not_replace_last_valid_data(tmp_path: Path) -> None:
    paths = DataPaths.create(tmp_path / "data")
    repos = Repositories.open(paths.database)
    saved = Podcast("pod", "https://example.test/feed", "Saved locally", created_at=NOW, updated_at=NOW)
    repos.podcasts.save(saved)
    repos.podcasts.subscribe("pod", NOW)
    cache = paths.cache / "catalog.json"
    cache.write_bytes(b"{not-json")
    repos.database.connection.execute(
        "INSERT INTO catalog_cache VALUES (?, ?, ?, ?, ?, ?)",
        ("catalog", "cache/catalog.json", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", None, None),
    )
    repos.database.connection.commit()

    report = recover_local_state(paths, repos)

    assert report.discarded_cache_entries == 1
    assert repos.podcasts.list_subscribed() == [saved]
    assert not cache.exists()
    assert repos.database.connection.execute("SELECT * FROM catalog_cache").fetchall() == []


def test_startup_cleans_only_abandoned_partial_files(tmp_path: Path) -> None:
    paths = DataPaths.create(tmp_path / "data")
    repos = Repositories.open(paths.database)
    repos.podcasts.save(Podcast("pod", "https://example.test/feed", "Saved", created_at=NOW, updated_at=NOW))
    repos.episodes.upsert(Episode(
        "ep", "pod", "guid:ep", "https://example.test/ep.mp3", "Ep", created_at=NOW, updated_at=NOW
    ))
    referenced = paths.root / "download-parts" / "ep.part"
    abandoned = paths.root / "download-parts" / "orphan.part"
    referenced.parent.mkdir()
    referenced.write_bytes(b"retry me")
    abandoned.write_bytes(b"discard me")
    repos.downloads.save(Download(
        "ep", DownloadState.FAILED, temp_relative_path="download-parts/ep.part",
        bytes_received=8, error_code="interrupted", created_at=NOW, updated_at=NOW,
    ))

    report = recover_local_state(paths, repos)

    assert report.abandoned_partials_removed == 1
    assert referenced.exists()
    assert not abandoned.exists()
