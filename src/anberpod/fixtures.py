from __future__ import annotations

from datetime import datetime, timezone

from anberpod.adapters.sqlite import Repositories
from anberpod.domain.models import Download, DownloadState, Episode, Playback, Podcast


_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def seed_demo_library(repositories: Repositories) -> None:
    podcasts = (
        Podcast("demo-science", "https://example.invalid/science.xml", "Saved Science", author="AnberPod Fixture", created_at=_NOW, updated_at=_NOW),
        Podcast("demo-history", "https://example.invalid/history.xml", "History Hour", author="AnberPod Fixture", created_at=_NOW, updated_at=_NOW),
    )
    episodes = (
        Episode("demo-ep-1", "demo-science", "guid:demo-1", "https://example.invalid/one.mp3", "How Stars Begin", created_at=_NOW, updated_at=_NOW),
        Episode("demo-ep-2", "demo-history", "guid:demo-2", "https://example.invalid/two.mp3", "The First Libraries", created_at=_NOW, updated_at=_NOW),
    )
    for podcast in podcasts:
        repositories.podcasts.save(podcast)
        repositories.podcasts.subscribe(podcast.id, _NOW)
    for episode in episodes:
        repositories.episodes.upsert(episode)
    repositories.playback.save(Playback("demo-ep-1", 185_000, 1_800_000, updated_at=_NOW))
    repositories.downloads.save(Download(
        "demo-ep-1", DownloadState.QUEUED, bytes_received=0, bytes_total=42_000_000,
        created_at=_NOW, updated_at=_NOW,
    ))
    repositories.downloads.save(Download(
        "demo-ep-2", DownloadState.FAILED, temp_relative_path="downloads/demo-ep-2.part",
        bytes_received=3_200_000, bytes_total=35_000_000, error_code="interrupted",
        created_at=_NOW, updated_at=_NOW,
    ))
