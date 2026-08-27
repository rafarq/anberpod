from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Protocol

from anberpod import __version__
from anberpod.domain.errors import PlaybackError
from anberpod.domain.models import PlaybackEvent, PlaybackSource, PlaybackState


class ManagedProcessPort(Protocol):
    stdout: object | None

    @property
    def stderr_text(self) -> str: ...
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float) -> int: ...
    def kill(self) -> None: ...
    def suspend(self) -> None: ...
    def resume(self) -> None: ...


class ProcessFactory(Protocol):
    def start(
        self,
        args: list[str],
        *,
        stdin_from: object | None = None,
        pipe_stdout: bool = False,
    ) -> ManagedProcessPort: ...


class _BoundedStderr:
    def __init__(self, stream: BinaryIO, limit: int) -> None:
        self.stream = stream
        self.limit = max(0, limit)
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, name="anberpod-stderr", daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        try:
            while True:
                chunk = self.stream.read(4096)
                if not chunk:
                    return
                with self._lock:
                    self._buffer.extend(chunk)
                    if len(self._buffer) > self.limit:
                        del self._buffer[:len(self._buffer) - self.limit]
        except (OSError, ValueError):
            return

    def text(self) -> str:
        self._thread.join(timeout=0.1)
        with self._lock:
            return bytes(self._buffer).decode("utf-8", errors="replace")


class ManagedSubprocess:
    def __init__(self, process: subprocess.Popen[bytes], stderr_limit_bytes: int) -> None:
        self.process = process
        self.stdout = process.stdout
        if process.stderr is None:
            raise RuntimeError("managed subprocess requires stderr=PIPE")
        self._stderr = _BoundedStderr(process.stderr, stderr_limit_bytes)

    @property
    def stderr_text(self) -> str:
        return self._stderr.text()

    def poll(self) -> int | None:
        return self.process.poll()

    def terminate(self) -> None:
        os.killpg(self.process.pid, signal.SIGTERM)

    def wait(self, timeout: float) -> int:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError from exc

    def kill(self) -> None:
        os.killpg(self.process.pid, signal.SIGKILL)

    def suspend(self) -> None:
        os.killpg(self.process.pid, signal.SIGSTOP)

    def resume(self) -> None:
        os.killpg(self.process.pid, signal.SIGCONT)


class SubprocessProcessFactory:
    """The only playback boundary that imports and invokes real subprocesses."""

    def __init__(self, *, stderr_limit_bytes: int = 16 * 1024) -> None:
        self.stderr_limit_bytes = stderr_limit_bytes

    def start(
        self,
        args: list[str],
        *,
        stdin_from: object | None = None,
        pipe_stdout: bool = False,
    ) -> ManagedSubprocess:
        process = subprocess.Popen(
            args,
            stdin=stdin_from,
            stdout=subprocess.PIPE if pipe_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
            bufsize=0,
        )
        return ManagedSubprocess(process, self.stderr_limit_bytes)


DEFAULT_CA_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")


@dataclass(frozen=True)
class FfmpegAplayConfig:
    decoder_path: Path
    aplay_path: str = "aplay"
    sample_rate: int = 48_000
    channels: int = 2
    remote_timeout_seconds: int = 30
    stop_timeout_seconds: float = 2.0
    stderr_limit_bytes: int = 16 * 1024
    user_agent: str = f"AnberPod/{__version__}"
    ca_bundle_path: Path | str | None = DEFAULT_CA_BUNDLE


class FfmpegAplayEngine:
    """Launches ffmpeg -> raw PCM -> aplay with deterministic process control."""

    def __init__(self, config: FfmpegAplayConfig, factory: ProcessFactory | None = None) -> None:
        self.config = config
        self.factory = factory or SubprocessProcessFactory(stderr_limit_bytes=config.stderr_limit_bytes)
        self._decoder: ManagedProcessPort | None = None
        self._aplay: ManagedProcessPort | None = None
        self._source: PlaybackSource | None = None
        self._paused = False
        self._terminal_reported = False
        self._last_diagnostic = ""

    @property
    def diagnostic_stderr(self) -> str:
        current = self._collect_stderr()
        return current[-self.config.stderr_limit_bytes:]

    def play(self, source: PlaybackSource, start_seconds: float) -> None:
        if self._decoder is not None or self._aplay is not None:
            self.stop()
        decoder = self.config.decoder_path
        if not decoder.is_file() or not os.access(decoder, os.X_OK):
            raise PlaybackError("bundled ffmpeg is missing or not executable", code="decoder_not_found")
        self._source = source
        self._paused = False
        self._terminal_reported = False
        self._launch(source, max(0.0, start_seconds))

    def _launch(self, source: PlaybackSource, start_seconds: float) -> None:
        decoder_args = self._decoder_args(source, start_seconds)
        try:
            self._decoder = self.factory.start(decoder_args, pipe_stdout=True)
        except FileNotFoundError as exc:
            raise PlaybackError("bundled ffmpeg could not be launched", code="decoder_not_found") from exc
        except OSError as exc:
            raise PlaybackError("bundled ffmpeg could not be launched", code="decoder_launch_failed") from exc
        try:
            self._aplay = self.factory.start(
                [
                    self.config.aplay_path,
                    "--quiet",
                    "--format=S16_LE",
                    f"--rate={self.config.sample_rate}",
                    f"--channels={self.config.channels}",
                ],
                stdin_from=self._decoder.stdout,
            )
        except FileNotFoundError as exc:
            self._stop_pipeline()
            raise PlaybackError("system aplay is unavailable", code="audio_output_not_found") from exc
        except OSError as exc:
            self._stop_pipeline()
            raise PlaybackError("system aplay could not be launched", code="audio_output_launch_failed") from exc

    def _decoder_args(self, source: PlaybackSource, start_seconds: float) -> list[str]:
        protocols = "file,pipe" if source.local else "http,https,tcp,tls,crypto,httpproxy,data"
        args = [
            str(self.config.decoder_path),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-protocol_whitelist",
            protocols,
        ]
        if not source.local:
            args.extend([
                "-user_agent", self.config.user_agent,
                "-rw_timeout", str(self.config.remote_timeout_seconds * 1_000_000),
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
            ])
            if self.config.ca_bundle_path is not None:
                try:
                    ca_path = Path(self.config.ca_bundle_path)
                    if ca_path.is_file() and os.access(ca_path, os.R_OK):
                        args.extend(["-ca_file", str(ca_path)])
                except OSError:
                    pass
        args.extend([
            "-ss", f"{start_seconds:.3f}",
            "-i", source.value,
            "-vn",
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ar", str(self.config.sample_rate),
            "-ac", str(self.config.channels),
            "pipe:1",
        ])
        return args

    def pause(self) -> None:
        self._require_pipeline()
        if self._paused:
            return
        assert self._decoder is not None and self._aplay is not None
        self._decoder.suspend()
        self._aplay.suspend()
        self._paused = True

    def resume(self) -> None:
        self._require_pipeline()
        if not self._paused:
            return
        assert self._decoder is not None and self._aplay is not None
        self._aplay.resume()
        self._decoder.resume()
        self._paused = False

    def seek(self, position_seconds: float) -> None:
        self._require_pipeline()
        assert self._source is not None
        paused = self._paused
        source = self._source
        self._stop_pipeline()
        self._terminal_reported = False
        self._launch(source, max(0.0, position_seconds))
        self._paused = False
        if paused:
            self.pause()

    def stop(self) -> None:
        self._stop_pipeline()
        self._paused = False

    def shutdown(self) -> None:
        self.stop()

    def events(self) -> Iterable[PlaybackEvent]:
        if self._terminal_reported or self._decoder is None or self._aplay is None:
            return ()
        decoder_code = self._decoder.poll()
        aplay_code = self._aplay.poll()
        if decoder_code is None and aplay_code is None:
            return ()
        if aplay_code not in (None, 0):
            code = "audio_output_failed"
            state = PlaybackState.ERROR
        elif decoder_code not in (None, 0):
            code = "decoder_failed"
            state = PlaybackState.ERROR
        elif decoder_code == 0 and aplay_code == 0:
            code = None
            state = PlaybackState.ENDED
        else:
            return ()
        self._last_diagnostic = self._collect_stderr()
        self._terminal_reported = True
        self._stop_pipeline()
        return (PlaybackEvent(state, error_code=code),)

    def _require_pipeline(self) -> None:
        if self._decoder is None or self._aplay is None:
            raise PlaybackError("playback pipeline is not running", code="invalid_playback_state")

    def _collect_stderr(self) -> str:
        chunks = [self._last_diagnostic]
        if self._decoder is not None:
            chunks.append(self._decoder.stderr_text)
        if self._aplay is not None:
            chunks.append(self._aplay.stderr_text)
        return "\n".join(part for part in chunks if part)[-self.config.stderr_limit_bytes:]

    def _stop_pipeline(self) -> None:
        processes = tuple(process for process in (self._decoder, self._aplay) if process is not None)
        if processes:
            self._last_diagnostic = self._collect_stderr()
        for process in processes:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                process.wait(self.config.stop_timeout_seconds)
            except (TimeoutError, OSError):
                try:
                    process.kill()
                    process.wait(self.config.stop_timeout_seconds)
                except (TimeoutError, OSError):
                    pass
        self._decoder = None
        self._aplay = None
