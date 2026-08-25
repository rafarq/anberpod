from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.adapters.sqlite import Repositories
from anberpod.domain.errors import DownloadError
from anberpod.domain.models import Download, DownloadState, Episode, Playback, Podcast
from anberpod.services.downloads import delete_download
from anberpod.services.playback import PlaybackSourceSelector


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def library(tmp_path: Path) -> tuple[Repositories, AtomicFiles, Episode]:
    root = tmp_path / "data"
    root.mkdir()
    repos = Repositories.open(root / "state.sqlite3")
    repos.podcasts.save(Podcast("pod", "https://example.test/feed", "Show", created_at=NOW, updated_at=NOW))
    repos.podcasts.subscribe("pod", NOW)
    episode = Episode(
        "ep", "pod", "guid:ep", "https://cdn.example.test/ep.mp3", "Episode",
        created_at=NOW, updated_at=NOW,
    )
    repos.episodes.upsert(episode)
    repos.playback.save(Playback("ep", 12_000, updated_at=NOW))
    return repos, AtomicFiles(root), episode


def test_delete_download_preserves_subscription_and_playback_and_removes_files(tmp_path: Path) -> None:
    repos, files, _ = library(tmp_path)
    files.write_part("download-parts/ep.part", (b"old",), append=False, max_bytes=20)
    files.write_atomic("downloads/ep.mp3", b"complete")
    repos.downloads.save(Download(
        "ep", DownloadState.COMPLETE, relative_path="downloads/ep.mp3",
        bytes_received=8, bytes_total=8, created_at=NOW, updated_at=NOW, completed_at=NOW,
    ))

    delete_download("ep", repos.downloads, files, extra_temp_path="download-parts/ep.part")

    assert repos.downloads.get("ep") is None
    assert not files.exists("downloads/ep.mp3")
    assert not files.exists("download-parts/ep.part")
    assert [podcast.id for podcast in repos.podcasts.list_subscribed()] == ["pod"]
    assert repos.playback.get("ep") == Playback("ep", 12_000, updated_at=NOW)


def test_delete_download_rejects_file_currently_in_use(tmp_path: Path) -> None:
    repos, files, _ = library(tmp_path)
    files.write_atomic("downloads/ep.mp3", b"complete")
    repos.downloads.save(Download(
        "ep", DownloadState.COMPLETE, relative_path="downloads/ep.mp3",
        bytes_received=8, bytes_total=8, created_at=NOW, updated_at=NOW, completed_at=NOW,
    ))

    with pytest.raises(DownloadError) as caught:
        delete_download("ep", repos.downloads, files, in_use=lambda episode_id: episode_id == "ep")

    assert caught.value.code == "download_in_use"
    assert files.exists("downloads/ep.mp3")
    assert repos.downloads.get("ep") is not None


def test_player_prefers_complete_local_file_over_remote_url(tmp_path: Path) -> None:
    repos, files, episode = library(tmp_path)
    files.write_atomic("downloads/ep.mp3", b"complete")
    repos.downloads.save(Download(
        "ep", DownloadState.COMPLETE, relative_path="downloads/ep.mp3",
        bytes_received=8, bytes_total=8, created_at=NOW, updated_at=NOW, completed_at=NOW,
    ))

    source = PlaybackSourceSelector(repos.downloads, files).select(episode)

    assert source.local is True
    assert source.value == str(files.path("downloads/ep.mp3"))


@pytest.mark.parametrize(
    ("download", "create_path"),
    [
        (Download("ep", DownloadState.FAILED, temp_relative_path="download-parts/ep.part"), "download-parts/ep.part"),
        (Download("ep", DownloadState.COMPLETE, relative_path="downloads/ep.mp3", bytes_received=8), None),
        (Download("ep", DownloadState.COMPLETE, relative_path="downloads/ep.mp3", bytes_received=9), "downloads/ep.mp3"),
    ],
)
def test_player_never_selects_part_missing_or_wrong_size_download(
    tmp_path: Path,
    download: Download,
    create_path: str | None,
) -> None:
    repos, files, episode = library(tmp_path)
    if create_path:
        if create_path.endswith(".part"):
            files.write_part(create_path, (b"complete",), append=False, max_bytes=20)
        else:
            files.write_atomic(create_path, b"complete")
    repos.downloads.save(download)

    source = PlaybackSourceSelector(repos.downloads, files).select(episode)

    assert source.local is False
    assert source.value == episode.media_url
