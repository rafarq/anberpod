from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pytest

from anberpod.app import Application
from anberpod.config import DataPaths
from anberpod.domain.errors import HttpPolicyError
from anberpod.domain.models import (
    Download,
    DownloadResponse,
    DownloadState,
    Episode,
    InputAction,
    InputEvent,
    PlaybackEvent,
    PlaybackSource,
    Podcast,
)
from anberpod.services.downloads import DownloadTransport, MediaProbe
from anberpod.ui.state import PodcastView, Route


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class Offline:
    def is_online(self) -> bool:
        return False


class Online:
    def is_online(self) -> bool:
        return True


class FakeClock:
    def __init__(self) -> None:
        self.value = 10.0
        self.utc = NOW

    def seconds(self) -> float:
        return self.value

    def now_utc(self) -> datetime:
        return self.utc


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def play(self, source: PlaybackSource, start_seconds: float) -> None:
        self.calls.append(("play", source, start_seconds))

    def pause(self) -> None:
        self.calls.append(("pause",))

    def resume(self) -> None:
        self.calls.append(("resume",))

    def seek(self, position_seconds: float) -> None:
        self.calls.append(("seek", position_seconds))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def events(self) -> tuple[PlaybackEvent, ...]:
        return ()

    def shutdown(self) -> None:
        self.calls.append(("shutdown",))


class FakeDownloadTransport:
    def __init__(self, responses: list[DownloadResponse] | None = None) -> None:
        self.responses: list[DownloadResponse] = responses or []
        self.requests: list[tuple[str, Mapping[str, str], int]] = []

    def request(self, url: str, headers: Mapping[str, str], max_bytes: int) -> DownloadResponse:
        self.requests.append((url, headers, max_bytes))
        if not self.responses:
            raise OSError("no response queued in fake transport")
        return self.responses.pop(0)


class FakeProbe:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.validated_paths: list[Path] = []

    def validate(self, path: Path) -> bool:
        self.validated_paths.append(path)
        return self.valid


def make_response(status: int, body: bytes, **headers: str) -> DownloadResponse:
    return DownloadResponse(status, headers, (body,))


def create_app(
    tmp_path: Path,
    *,
    transport: DownloadTransport | None = None,
    probe: MediaProbe | None = None,
) -> tuple[Application, FakeEngine, Episode]:
    paths = DataPaths.create(tmp_path / "data")
    engine = FakeEngine()
    clock = FakeClock()
    app = Application.open(
        paths,
        Online(),
        playback_engine=engine,
        playback_monotonic=clock,
        playback_clock=clock,
        download_transport=transport or FakeDownloadTransport([
            make_response(200, b"AUDIO_DATA_12345678", **{"Content-Length": "19", "ETag": '"v1"'}),
        ]),
        download_probe=probe or FakeProbe(valid=True),
    )
    podcast = Podcast(
        "pod-1", "https://example.test/feed.xml", "Tech Talk", author="Host", created_at=NOW, updated_at=NOW
    )
    app.repositories.podcasts.save(podcast)
    app.repositories.podcasts.subscribe("pod-1", NOW)
    episode = Episode(
        "ep-1",
        "pod-1",
        "guid:ep-1",
        "https://cdn.example.test/ep1.mp3",
        "Deep Learning Intro",
        duration_ms=120_000,
        media_length_bytes=19,
        media_type="audio/mpeg",
        created_at=NOW,
        updated_at=NOW,
    )
    app.repositories.episodes.upsert(episode)
    return app, engine, episode


def test_download_episode_via_real_input_events_completes_and_updates_ui(tmp_path: Path) -> None:
    fake_transport = FakeDownloadTransport([
        make_response(200, b"AUDIO_DATA_12345678", **{"Content-Length": "19", "ETag": '"v1"'}),
    ])
    fake_probe = FakeProbe(valid=True)
    app, engine, episode = create_app(tmp_path, transport=fake_transport, probe=fake_probe)

    # 1. Open podcast screen
    app.show_podcast("pod-1")
    screen = app.screen()
    assert screen.route is Route.PODCAST
    assert screen.title == "Tech Talk"
    assert screen.items == ("Update now", "Unsubscribe", "Deep Learning Intro")
    assert app.state.podcast_view is PodcastView.EPISODES

    # 2. Navigate down to the episode row (focus 2) and press ACCEPT (A button)
    app.handle(InputEvent(InputAction.DOWN))  # focus 1: Unsubscribe
    app.handle(InputEvent(InputAction.DOWN))  # focus 2: Deep Learning Intro
    assert app.state.focus == 2

    app.handle(InputEvent(InputAction.ACCEPT))  # Press A on episode

    # 3. Verify that Episode Actions screen opens
    assert app.state.podcast_view is PodcastView.EPISODE_ACTIONS
    screen = app.screen()
    assert screen.title == "Deep Learning Intro"
    assert screen.items == ("Play", "Download")
    assert app.state.focus == 0  # Initial focus on "Play"

    # 4. Move focus to "Download" (focus 1) and press ACCEPT
    app.handle(InputEvent(InputAction.DOWN))
    assert app.state.focus == 1

    app.handle(InputEvent(InputAction.ACCEPT))  # Press A on Download

    # 5. Verify download completed in SQLite repository
    download = app.repositories.downloads.get("ep-1")
    assert download is not None
    assert download.state is DownloadState.COMPLETE
    assert download.bytes_received == 19
    assert download.bytes_total == 19
    assert download.relative_path == "downloads/ep-1.mp3"

    # 6. Verify file exists on disk
    download_file = app.paths.root / "downloads" / "ep-1.mp3"
    assert download_file.is_file()
    assert download_file.read_bytes() == b"AUDIO_DATA_12345678"

    # 7. Verify probe was called
    assert len(fake_probe.validated_paths) == 1
    assert fake_probe.validated_paths[0].name == "ep-1.part"

    # 8. Verify UI updated to reflect completed download
    screen = app.screen()
    assert screen.items == ("Play", "Delete download")
    assert "Complete" in (screen.status or "")

    # 9. Select "Play" (focus 0) -> plays the downloaded local file
    app.handle(InputEvent(InputAction.UP))  # focus 0: Play
    assert app.state.focus == 0
    app.handle(InputEvent(InputAction.ACCEPT))

    assert app.state.route is Route.PLAYER
    assert app.playback.source is not None
    assert app.playback.source.local is True
    assert app.playback.source.value == str(download_file.resolve())
    assert engine.calls[-1][0] == "play"
    assert engine.calls[-1][1] == app.playback.source

    # 10. Stop player and go back to podcast actions
    app.handle(InputEvent(InputAction.BACK))  # Player -> Home
    assert app.state.route is Route.HOME

    app.show_podcast("pod-1")
    app.state.focus = 2
    app.handle(InputEvent(InputAction.ACCEPT))  # Open episode actions
    assert app.screen().items == ("Play", "Delete download")

    # 11. Delete download via ACCEPT on "Delete download" (focus 1)
    app.handle(InputEvent(InputAction.DOWN))  # focus 1: Delete download
    app.handle(InputEvent(InputAction.ACCEPT))

    assert not download_file.exists()
    assert app.repositories.downloads.get("ep-1") is None
    assert app.screen().items == ("Play", "Download")


def test_download_episode_error_handling_displays_typed_error_status(tmp_path: Path) -> None:
    class FailingTransport:
        def request(self, url: str, headers: Mapping[str, str], max_bytes: int) -> DownloadResponse:
            raise HttpPolicyError("redirected to HTTP", code="https_downgrade")

    app, _, _ = create_app(tmp_path, transport=FailingTransport())

    app.show_podcast("pod-1")
    app.state.focus = 2
    app.handle(InputEvent(InputAction.ACCEPT))  # Open episode actions

    assert app.screen().items == ("Play", "Download")
    app.handle(InputEvent(InputAction.DOWN))  # Focus "Download"
    app.handle(InputEvent(InputAction.ACCEPT))  # Press A

    # Download failed: state is FAILED, error_code recorded
    failed = app.repositories.downloads.get("ep-1")
    assert failed is not None
    assert failed.state is DownloadState.FAILED
    assert failed.error_code == "https_downgrade"

    # UI displays typed error status
    screen = app.screen()
    assert screen.status == "Download failed - https_downgrade"
    assert screen.items == ("Play", "Download")


def test_download_probe_failure_sets_failed_state_and_shows_error(tmp_path: Path) -> None:
    fake_transport = FakeDownloadTransport([
        make_response(200, b"CORRUPTED_DATA", **{"Content-Length": "14"}),
    ])
    fake_probe = FakeProbe(valid=False)  # Media validation fails
    app, _, _ = create_app(tmp_path, transport=fake_transport, probe=fake_probe)

    app.show_podcast("pod-1")
    app.state.focus = 2
    app.handle(InputEvent(InputAction.ACCEPT))  # Open episode actions
    app.handle(InputEvent(InputAction.DOWN))  # Focus "Download"
    app.handle(InputEvent(InputAction.ACCEPT))

    failed = app.repositories.downloads.get("ep-1")
    assert failed is not None
    assert failed.state is DownloadState.FAILED
    assert failed.error_code == "invalid_media"

    screen = app.screen()
    assert screen.status == "Download failed - invalid_media"


def test_downloads_route_accept_plays_complete_and_retries_failed(tmp_path: Path) -> None:
    fake_transport = FakeDownloadTransport([
        make_response(200, b"RETRIED_AUDIO_DATA", **{"Content-Length": "18"}),
    ])
    app, engine, episode = create_app(tmp_path, transport=fake_transport)

    # Save a failed download and a complete download
    episode_2 = Episode(
        "ep-2", "pod-1", "guid:ep-2", "https://cdn.example.test/ep2.mp3", "Episode Two",
        duration_ms=60_000, media_length_bytes=18, media_type="audio/mpeg", created_at=NOW, updated_at=NOW,
    )
    app.repositories.episodes.upsert(episode_2)

    # Complete download for ep-1
    complete_file = app.paths.root / "downloads" / "ep-1.mp3"
    complete_file.parent.mkdir(parents=True, exist_ok=True)
    complete_file.write_bytes(b"EXISTING_COMPLETE_DATA")
    app.repositories.downloads.save(Download(
        "ep-1", DownloadState.COMPLETE, relative_path="downloads/ep-1.mp3",
        bytes_received=22, bytes_total=22, created_at=NOW, updated_at=NOW, completed_at=NOW,
    ))

    # Failed download for ep-2
    app.repositories.downloads.save(Download(
        "ep-2", DownloadState.FAILED, error_code="interrupted",
        bytes_received=0, bytes_total=18, created_at=NOW, updated_at=NOW,
    ))

    # Go to DOWNLOADS route
    app.state.show(Route.DOWNLOADS, 2)
    screen = app.screen()
    assert screen.route is Route.DOWNLOADS
    assert len(screen.items) == 2
    assert "Complete" in screen.items[0]
    assert "Failed" in screen.items[1]

    # Press A on focus 0 (Complete download) -> Plays episode
    app.state.focus = 0
    app.handle(InputEvent(InputAction.ACCEPT))
    assert app.state.route is Route.PLAYER
    assert app.playback.source is not None
    assert app.playback.source.local is True

    # Go back to DOWNLOADS route
    app.handle(InputEvent(InputAction.BACK))
    app.state.show(Route.DOWNLOADS, 2)

    # Press A on focus 1 (Failed download) -> Retries and completes download
    app.state.focus = 1
    app.handle(InputEvent(InputAction.ACCEPT))

    ep2_download = app.repositories.downloads.get("ep-2")
    assert ep2_download is not None
    assert ep2_download.state is DownloadState.COMPLETE
    assert (app.paths.root / "downloads" / "ep-2.mp3").is_file()


def test_episode_action_back_navigation_restores_podcast_focus(tmp_path: Path) -> None:
    app, _, _ = create_app(tmp_path)
    episode_2 = Episode(
        "ep-2", "pod-1", "guid:ep-2", "https://cdn.example.test/ep2.mp3", "Second Episode",
        duration_ms=60_000, created_at=NOW, updated_at=NOW,
    )
    app.repositories.episodes.upsert(episode_2)

    app.show_podcast("pod-1")
    assert len(app.screen().items) == 4  # Update, Unsubscribe, Ep1, Ep2

    # Focus second episode (focus = 3)
    app.state.focus = 3
    app.handle(InputEvent(InputAction.ACCEPT))  # Open EPISODE_ACTIONS

    assert app.state.podcast_view is PodcastView.EPISODE_ACTIONS
    assert app.screen().title == "Second Episode"
    assert app.state.focus == 0

    # Press BACK (B button) -> returns to EPISODES view with focus restored to 3
    app.handle(InputEvent(InputAction.BACK))

    assert app.state.podcast_view is PodcastView.EPISODES
    assert app.state.route is Route.PODCAST
    assert app.screen().title == "Tech Talk"
    assert app.state.focus == 3


def test_download_ui_translations_for_supported_languages(tmp_path: Path) -> None:
    app, _, _ = create_app(tmp_path)

    # Set language to Spanish
    app.set_language("es")
    app.show_podcast("pod-1")
    app.state.focus = 2
    app.handle(InputEvent(InputAction.ACCEPT))

    screen = app.screen()
    assert screen.items == ("Reproducir", "Descargar")

    # Set language to German
    app.set_language("de")
    screen = app.screen()
    assert screen.items == ("Abspielen", "Herunterladen")

    # Set language to Japanese
    app.set_language("ja")
    screen = app.screen()
    assert screen.items == ("再生", "ダウンロード")

    # Set language to Simplified Chinese
    app.set_language("zh-Hans")
    screen = app.screen()
    assert screen.items == ("播放", "下载")
