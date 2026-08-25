from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.domain.errors import PlaybackError
from anberpod.domain.models import (
    Download,
    DownloadState,
    Episode,
    Playback,
    PlaybackEvent,
    PlaybackSource,
    PlaybackState,
)
from anberpod.services.playback import PlaybackController, PlaybackSourceSelector


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self) -> None:
        self.monotonic = 100.0
        self.utc = NOW

    def seconds(self) -> float:
        return self.monotonic

    def now_utc(self) -> datetime:
        return self.utc

    def advance(self, seconds: float) -> None:
        self.monotonic += seconds
        self.utc += timedelta(seconds=seconds)


class MemoryPlaybackRepository:
    def __init__(self, saved: Playback | None = None) -> None:
        self.value = saved
        self.saves: list[Playback] = []

    def get(self, episode_id: str) -> Playback | None:
        return self.value if self.value and self.value.episode_id == episode_id else None

    def save(self, playback: Playback) -> None:
        self.value = playback
        self.saves.append(playback)


class MemoryDownloads:
    def __init__(self, value: Download | None = None) -> None:
        self.value = value

    def get(self, episode_id: str) -> Download | None:
        return self.value if self.value and self.value.episode_id == episode_id else None


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.pending: list[PlaybackEvent] = []
        self.failure: PlaybackError | None = None

    def play(self, source: PlaybackSource, start_seconds: float) -> None:
        if self.failure:
            raise self.failure
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
        result = tuple(self.pending)
        self.pending.clear()
        return result

    def shutdown(self) -> None:
        self.calls.append(("shutdown",))


def episode() -> Episode:
    return Episode(
        "ep", "pod", "guid:ep", "https://cdn.example.test/episode.mp3", "Episode title",
        duration_ms=60_000,
    )


def controller(
    tmp_path: Path,
    *,
    playback: Playback | None = None,
    download: Download | None = None,
) -> tuple[PlaybackController, FakeClock, MemoryPlaybackRepository, FakeEngine, AtomicFiles]:
    files = AtomicFiles(tmp_path)
    clock = FakeClock()
    repository = MemoryPlaybackRepository(playback)
    engine = FakeEngine()
    selector = PlaybackSourceSelector(MemoryDownloads(download), files)
    return PlaybackController(repository, selector, engine, clock, clock), clock, repository, engine, files


def test_play_resumes_persisted_position_and_prefers_verified_complete_local_file(tmp_path: Path) -> None:
    download = Download(
        "ep", DownloadState.COMPLETE, relative_path="downloads/ep.mp3", bytes_received=8,
    )
    player, _, _, engine, files = controller(
        tmp_path, playback=Playback("ep", 23_000, 60_000, updated_at=NOW), download=download,
    )
    files.write_atomic("downloads/ep.mp3", b"complete")

    player.play(episode())

    assert player.state is PlaybackState.PLAYING
    assert player.position_ms == 23_000
    assert engine.calls == [("play", PlaybackSource(str(files.path("downloads/ep.mp3")), local=True), 23.0)]


def test_pause_resume_seek_and_stop_checkpoint_exact_positions(tmp_path: Path) -> None:
    player, clock, repository, engine, _ = controller(tmp_path, playback=Playback("ep", 5_000, 60_000))
    player.play(episode())
    clock.advance(4)

    player.pause()
    assert (player.state, player.position_ms) == (PlaybackState.PAUSED, 9_000)
    assert repository.saves[-1].position_ms == 9_000
    clock.advance(20)
    assert player.position_ms == 9_000

    player.resume()
    clock.advance(2)
    player.seek_forward()
    assert player.position_ms == 26_000
    assert engine.calls[-1] == ("seek", 26.0)
    assert repository.saves[-1].position_ms == 26_000

    player.seek_backward()
    assert player.position_ms == 11_000
    player.seek_backward()
    assert player.position_ms == 0

    clock.advance(3)
    player.stop()
    assert (player.state, player.position_ms) == (PlaybackState.STOPPED, 3_000)
    assert repository.saves[-1].position_ms == 3_000
    assert ("pause",) in engine.calls and ("resume",) in engine.calls and engine.calls[-1] == ("stop",)


def test_periodic_checkpoint_is_at_most_once_per_ten_seconds_of_playing_time(tmp_path: Path) -> None:
    player, clock, repository, _, _ = controller(tmp_path)
    player.play(episode())

    for _ in range(9):
        clock.advance(1)
        player.checkpoint()
    assert repository.saves == []

    clock.advance(1)
    player.checkpoint()
    assert [item.position_ms for item in repository.saves] == [10_000]

    for _ in range(9):
        clock.advance(1)
        player.checkpoint()
    assert len(repository.saves) == 1
    clock.advance(1)
    player.checkpoint()
    assert [item.position_ms for item in repository.saves] == [10_000, 20_000]


def test_engine_end_marks_complete_and_error_preserves_position_with_typed_failure(tmp_path: Path) -> None:
    player, clock, repository, engine, _ = controller(tmp_path)
    player.play(episode())
    clock.advance(7)
    engine.pending.append(PlaybackEvent(PlaybackState.ENDED, 60_000))

    assert player.poll() == ()
    assert player.state is PlaybackState.ENDED
    assert repository.saves[-1].completed is True
    assert repository.saves[-1].position_ms == 60_000

    failing, _, _, failing_engine, _ = controller(tmp_path / "failure")
    failing_engine.failure = PlaybackError("decoder missing", code="decoder_not_found")
    with pytest.raises(PlaybackError) as caught:
        failing.play(replace(episode(), id="other"))
    assert caught.value.code == "decoder_not_found"
    assert failing.state is PlaybackState.ERROR
    assert failing.failure is caught.value


def test_remote_source_must_be_https_without_embedded_credentials(tmp_path: Path) -> None:
    player, _, _, engine, _ = controller(tmp_path)

    with pytest.raises(PlaybackError) as caught:
        player.play(replace(episode(), media_url="http://user:secret@example.test/a.mp3"))

    assert caught.value.code == "unsafe_media_url"
    assert engine.calls == []


def test_paused_wall_time_does_not_trigger_early_periodic_sd_checkpoint(tmp_path: Path) -> None:
    player, clock, repository, _, _ = controller(tmp_path)
    player.play(episode())
    clock.advance(3)
    player.pause()
    assert len(repository.saves) == 1
    clock.advance(60)
    player.resume()
    clock.advance(9)
    player.checkpoint()
    assert len(repository.saves) == 1
    clock.advance(1)
    player.checkpoint()
    assert len(repository.saves) == 2


def test_control_failure_stops_pipeline_and_unknown_duration_end_keeps_position(tmp_path: Path) -> None:
    class PauseFailureEngine(FakeEngine):
        def pause(self) -> None:
            self.calls.append(("pause",))
            raise PlaybackError("signal failed", code="pause_failed")

    player, clock, _, _, _ = controller(tmp_path)
    broken = PauseFailureEngine()
    player.engine = broken
    player.play(episode())
    clock.advance(4)
    with pytest.raises(PlaybackError) as caught:
        player.pause()
    assert caught.value.code == "pause_failed"
    assert broken.calls[-1] == ("stop",)
    assert player.position_ms == 4_000

    unknown = replace(episode(), id="unknown", duration_ms=None)
    ended, ended_clock, repository, engine, _ = controller(tmp_path / "ended")
    ended.play(unknown)
    ended_clock.advance(7)
    engine.pending.append(PlaybackEvent(PlaybackState.ENDED))
    ended.poll()
    assert repository.saves[-1].position_ms == 7_000
    assert repository.saves[-1].completed is True
