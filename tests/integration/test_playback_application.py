from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from anberpod.app import Application
from anberpod.config import DataPaths
from anberpod.domain.models import Episode, InputAction, InputEvent, PlaybackEvent, PlaybackSource, PlaybackState, Podcast
from anberpod.ui.state import Route


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class Offline:
    def is_online(self) -> bool:
        return False


class FakeClock:
    def __init__(self) -> None:
        self.value = 10.0
        self.utc = NOW

    def seconds(self) -> float:
        return self.value

    def now_utc(self) -> datetime:
        return self.utc

    def advance(self, seconds: float) -> None:
        self.value += seconds
        self.utc += timedelta(seconds=seconds)


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


def open_app(tmp_path: Path) -> tuple[Application, FakeClock, FakeEngine, Episode]:
    clock = FakeClock()
    engine = FakeEngine()
    app = Application.open(
        DataPaths.create(tmp_path / "data"), Offline(),
        playback_engine=engine, playback_monotonic=clock, playback_clock=clock,
    )
    app.repositories.podcasts.save(Podcast("pod", "https://example.test/feed", "Saved Science"))
    episode = Episode(
        "ep", "pod", "guid:ep", "https://cdn.example.test/ep.mp3", "How Stars Begin",
        duration_ms=60_000,
    )
    app.repositories.episodes.upsert(episode)
    return app, clock, engine, episode


def test_player_route_maps_physical_controls_and_updates_explicit_view_model(tmp_path: Path) -> None:
    app, clock, engine, episode = open_app(tmp_path)
    app.play_episode(episode)

    assert app.screen().route is Route.PLAYER
    assert app.screen().items[:3] == ("How Stars Begin", "Saved Science", "Playing")

    clock.advance(2)
    app.handle(InputEvent(InputAction.ACCEPT))
    assert app.screen().items[2] == "Paused"
    app.handle(InputEvent(InputAction.RIGHT))
    assert ("seek", 17.0) in engine.calls
    app.handle(InputEvent(InputAction.LEFT))
    assert engine.calls[-1] == ("seek", 2.0)
    app.handle(InputEvent(InputAction.ACCEPT))
    assert app.screen().items[2] == "Playing"

    app.handle(InputEvent(InputAction.BACK))
    assert app.state.route is Route.HOME
    assert engine.calls[-1] == ("stop",)


def test_menu_persists_progress_and_shuts_down_playback_children(tmp_path: Path) -> None:
    app, clock, engine, episode = open_app(tmp_path)
    app.play_episode(episode)
    clock.advance(7)

    app.handle(InputEvent(InputAction.MENU))

    saved = app.repositories.playback.get("ep")
    assert saved is not None and saved.position_ms == 7_000
    assert engine.calls[-2:] == [("stop",), ("shutdown",)]
    assert app.state.exit_requested is True


def test_podcast_episode_row_opens_player_with_a(tmp_path: Path) -> None:
    app, _, engine, episode = open_app(tmp_path)
    app.show_podcast("pod")
    app.state.focus = 2

    app.handle(InputEvent(InputAction.ACCEPT))

    assert app.state.route is Route.PLAYER
    assert app.screen().items[0] == episode.title
    assert engine.calls[0][0] == "play"


def test_play_episode_resolves_podcast_cover_before_render_and_prefers_offline_cache(tmp_path: Path) -> None:
    class RecordingArtworkCache:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.calls: list[tuple[str | None, bool]] = []

        def ensure_cached(self, url: str | None, *, online: bool) -> Path | None:
            self.calls.append((url, online))
            return self.path

    paths = DataPaths.create(tmp_path / "data")
    clock = FakeClock()
    engine = FakeEngine()
    cached_cover = paths.cache / "artwork" / "cached.png"
    artwork = RecordingArtworkCache(cached_cover)
    app = Application.open(
        paths,
        Offline(),
        playback_engine=engine,
        playback_monotonic=clock,
        playback_clock=clock,
        artwork_cache=artwork,
    )
    app.repositories.podcasts.save(Podcast(
        "pod", "https://example.test/feed", "Saved Science",
        image_url="https://images.example.test/show.png?signature=private",
    ))
    episode = Episode("ep", "pod", "guid:ep", "https://cdn.example.test/ep.mp3", "Episode")

    app.play_episode(episode)

    assert artwork.calls == [("https://images.example.test/show.png?signature=private", False)]
    assert app._player is not None and app._player.artwork_path == cached_cover
