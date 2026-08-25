from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from anberpod.config import DataPaths
from anberpod.domain.models import Download, DownloadState, Episode, Playback, Podcast


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_domain_models_are_immutable_and_validate_state() -> None:
    podcast = Podcast("pod-1", "https://example.test/feed", "Example", created_at=NOW, updated_at=NOW)
    episode = Episode("ep-1", podcast.id, "guid:g1", "https://example.test/e.mp3", "Episode", created_at=NOW, updated_at=NOW)
    playback = Playback(episode.id, position_ms=12_000, duration_ms=60_000)
    download = Download(episode.id, DownloadState.QUEUED, created_at=NOW, updated_at=NOW)

    assert download.state is DownloadState.QUEUED
    assert playback.completed is False
    with pytest.raises(FrozenInstanceError):
        podcast.title = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="position_ms"):
        Playback(episode.id, position_ms=-1)


def test_data_paths_create_private_layout_and_confine_resolved_paths(tmp_path: Path) -> None:
    paths = DataPaths.create(tmp_path / "persistent data")

    assert paths.database == paths.root / "db" / "anberpod.sqlite3"
    assert {"db", "downloads", "cache", "imports", "config", "logs"} <= {
        child.name for child in paths.root.iterdir()
    }
    assert paths.resolve_relative("downloads/ep-1.part") == paths.downloads / "ep-1.part"
    with pytest.raises(ValueError, match="outside data directory"):
        paths.resolve_relative("../current/app.py")
