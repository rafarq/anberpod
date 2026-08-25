from __future__ import annotations

from urllib.parse import urlsplit

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.domain.errors import PlaybackError
from anberpod.domain.models import (
    DownloadState,
    Episode,
    Playback,
    PlaybackSource,
    PlaybackState,
)
from anberpod.domain.ports import (
    Clock,
    DownloadRepository,
    MonotonicClock,
    PlaybackEngine,
    PlaybackRepository,
)


class PlaybackSourceSelector:
    """Selects media only; it does not start ffmpeg or aplay."""

    def __init__(self, downloads: DownloadRepository, files: AtomicFiles) -> None:
        self.downloads = downloads
        self.files = files

    def select(self, episode: Episode) -> PlaybackSource:
        download = self.downloads.get(episode.id)
        if (
            download is not None
            and download.state is DownloadState.COMPLETE
            and download.relative_path is not None
            and not download.relative_path.endswith(".part")
            and download.bytes_received > 0
            and self.files.exists(download.relative_path)
            and self.files.size(download.relative_path) == download.bytes_received
        ):
            return PlaybackSource(str(self.files.path(download.relative_path)), local=True)
        return PlaybackSource(episode.media_url, local=False)


class PlaybackController:
    """Coordinates a mockable engine and durable, SD-friendly playback progress."""

    SEEK_SECONDS = 15

    def __init__(
        self,
        repository: PlaybackRepository,
        selector: PlaybackSourceSelector,
        engine: PlaybackEngine,
        monotonic: MonotonicClock,
        clock: Clock,
        *,
        checkpoint_seconds: float = 10.0,
    ) -> None:
        self.repository = repository
        self.selector = selector
        self.engine = engine
        self.monotonic = monotonic
        self.clock = clock
        self.checkpoint_seconds = checkpoint_seconds
        self.state = PlaybackState.IDLE
        self.failure: PlaybackError | None = None
        self.episode: Episode | None = None
        self.source: PlaybackSource | None = None
        self._position_ms = 0
        self._duration_ms: int | None = None
        self._position_anchor = monotonic.seconds()
        self._last_checkpoint = self._position_anchor

    @property
    def position_ms(self) -> int:
        if self.state is PlaybackState.PLAYING:
            elapsed = max(0.0, self.monotonic.seconds() - self._position_anchor)
            return self._clamp(self._position_ms + int(elapsed * 1000))
        return self._clamp(self._position_ms)

    @property
    def duration_ms(self) -> int | None:
        return self._duration_ms

    def play(self, episode: Episode, *, restart_completed: bool = False) -> None:
        if self.state in {PlaybackState.PLAYING, PlaybackState.PAUSED, PlaybackState.BUFFERING}:
            self.stop()
        stored = self.repository.get(episode.id)
        if stored and stored.completed and not restart_completed:
            failure = PlaybackError("completed episode requires restart confirmation", code="restart_confirmation")
            self.state = PlaybackState.ERROR
            self.failure = failure
            raise failure
        self.episode = episode
        self._duration_ms = episode.duration_ms if episode.duration_ms is not None else (
            stored.duration_ms if stored else None
        )
        self._position_ms = 0 if restart_completed else self._clamp(stored.position_ms if stored else 0)
        self.source = self.selector.select(episode)
        if not self.source.local:
            self._validate_remote(self.source.value)
        self.failure = None
        self.state = PlaybackState.BUFFERING
        try:
            self.engine.play(self.source, self._position_ms / 1000)
        except PlaybackError as exc:
            self.state = PlaybackState.ERROR
            self.failure = exc
            raise
        except (OSError, ValueError) as exc:
            failure = PlaybackError("could not start playback", code="playback_launch")
            self.state = PlaybackState.ERROR
            self.failure = failure
            raise failure from exc
        now = self.monotonic.seconds()
        self._position_anchor = now
        self._last_checkpoint = now
        self.state = PlaybackState.PLAYING

    def pause(self) -> None:
        self._require(PlaybackState.PLAYING)
        self._freeze_position()
        try:
            self.engine.pause()
        except (PlaybackError, OSError) as exc:
            self._engine_failure(exc)
        self.state = PlaybackState.PAUSED
        self._persist()

    def resume(self) -> None:
        self._require(PlaybackState.PAUSED)
        try:
            self.engine.resume()
        except (PlaybackError, OSError) as exc:
            self._engine_failure(exc)
        now = self.monotonic.seconds()
        self._position_anchor = now
        self._last_checkpoint = now
        self.state = PlaybackState.PLAYING

    def seek_forward(self) -> None:
        self._seek(self.SEEK_SECONDS)

    def seek_backward(self) -> None:
        self._seek(-self.SEEK_SECONDS)

    def stop(self) -> None:
        if self.state not in {PlaybackState.PLAYING, PlaybackState.PAUSED, PlaybackState.BUFFERING}:
            return
        self._freeze_position()
        try:
            self.engine.stop()
        except (PlaybackError, OSError) as exc:
            self._engine_failure(exc)
        self.state = PlaybackState.STOPPED
        self._persist()

    def checkpoint(self, *, force: bool = False) -> None:
        if self.episode is None:
            return
        now = self.monotonic.seconds()
        if not force and (
            self.state is not PlaybackState.PLAYING
            or now - self._last_checkpoint < self.checkpoint_seconds
        ):
            return
        self._persist()

    def poll(self) -> tuple[PlaybackError, ...]:
        failures: list[PlaybackError] = []
        for event in self.engine.events():
            if event.state is PlaybackState.ENDED:
                current_position = self.position_ms
                if self._duration_ms is not None:
                    self._position_ms = self._duration_ms
                elif event.position_ms > 0:
                    self._position_ms = event.position_ms
                else:
                    self._position_ms = current_position
                self.state = PlaybackState.ENDED
                self._persist(completed=True)
            elif event.state is PlaybackState.ERROR:
                self._freeze_position()
                failure = PlaybackError("playback pipeline failed", code=event.error_code or "playback_failed")
                self.failure = failure
                self.state = PlaybackState.ERROR
                self._persist()
                failures.append(failure)
        self.checkpoint()
        return tuple(failures)

    def shutdown(self) -> None:
        if self.state in {PlaybackState.PLAYING, PlaybackState.PAUSED, PlaybackState.BUFFERING}:
            self.stop()
        self.engine.shutdown()

    def _seek(self, seconds: int) -> None:
        if self.state not in {PlaybackState.PLAYING, PlaybackState.PAUSED}:
            self._require(PlaybackState.PLAYING)
        was_playing = self.state is PlaybackState.PLAYING
        self._freeze_position()
        self._position_ms = self._clamp(self._position_ms + seconds * 1000)
        try:
            self.engine.seek(self._position_ms / 1000)
        except (PlaybackError, OSError) as exc:
            self._engine_failure(exc)
        self._position_anchor = self.monotonic.seconds()
        self.state = PlaybackState.PLAYING if was_playing else PlaybackState.PAUSED
        self._persist()

    def _freeze_position(self) -> None:
        self._position_ms = self.position_ms
        self._position_anchor = self.monotonic.seconds()

    def _persist(self, *, completed: bool = False) -> None:
        if self.episode is None:
            return
        self.repository.save(Playback(
            self.episode.id,
            self.position_ms,
            self._duration_ms,
            completed=completed,
            updated_at=self.clock.now_utc(),
        ))
        self._last_checkpoint = self.monotonic.seconds()

    def _clamp(self, position_ms: int) -> int:
        value = max(0, position_ms)
        return min(value, self._duration_ms) if self._duration_ms is not None else value

    def _require(self, expected: PlaybackState) -> None:
        if self.state is not expected:
            raise PlaybackError(
                f"playback is {self.state.value}, expected {expected.value}",
                code="invalid_playback_state",
            )

    def _engine_failure(self, exc: BaseException) -> None:
        failure = exc if isinstance(exc, PlaybackError) else PlaybackError(
            "playback pipeline failed", code="playback_pipeline"
        )
        try:
            self.engine.stop()
        except (PlaybackError, OSError):
            pass
        self.state = PlaybackState.ERROR
        self.failure = failure
        raise failure from exc

    @staticmethod
    def _validate_remote(url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme.lower() != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
            raise PlaybackError("remote media URL is not safe for playback", code="unsafe_media_url")
