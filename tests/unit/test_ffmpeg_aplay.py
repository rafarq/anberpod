from __future__ import annotations

import io
from pathlib import Path

import pytest

from anberpod.adapters.ffmpeg_aplay import (
    DEFAULT_CA_BUNDLE,
    FfmpegAplayConfig,
    FfmpegAplayEngine,
    SubprocessProcessFactory,
)
from anberpod.domain.errors import PlaybackError
from anberpod.domain.models import PlaybackSource, PlaybackState


class FakeProcess:
    def __init__(self, name: str, *, returncode: int | None = None, stderr: str = "") -> None:
        self.name = name
        self.stdout = object()
        self.returncode = returncode
        self.stderr_text = stderr
        self.calls: list[tuple[object, ...]] = []

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.calls.append(("terminate",))

    def wait(self, timeout: float) -> int:
        self.calls.append(("wait", timeout))
        if self.returncode is None:
            raise TimeoutError
        return self.returncode

    def kill(self) -> None:
        self.calls.append(("kill",))
        self.returncode = -9

    def suspend(self) -> None:
        self.calls.append(("suspend",))

    def resume(self) -> None:
        self.calls.append(("resume",))


class FakeFactory:
    def __init__(self) -> None:
        self.starts: list[tuple[tuple[str, ...], object | None, bool]] = []
        self.processes: list[FakeProcess] = []
        self.fail_on: str | None = None

    def start(self, args: list[str], *, stdin_from: object | None = None, pipe_stdout: bool = False) -> FakeProcess:
        self.starts.append((tuple(args), stdin_from, pipe_stdout))
        if self.fail_on and Path(args[0]).name == self.fail_on:
            raise FileNotFoundError(args[0])
        process = FakeProcess(Path(args[0]).name)
        self.processes.append(process)
        return process


def config(tmp_path: Path, *, ca_bundle: Path | str | None = ...) -> FfmpegAplayConfig:
    decoder = tmp_path / "runtime" / "bin" / "ffmpeg"
    decoder.parent.mkdir(parents=True, exist_ok=True)
    decoder.write_bytes(b"synthetic-test-placeholder")
    decoder.chmod(0o755)
    if ca_bundle is ...:
        ca_file = tmp_path / "ca-certificates.crt"
        if not ca_file.exists():
            ca_file.write_text("synthetic-ca-bundle", encoding="utf-8")
        ca_bundle = ca_file
    return FfmpegAplayConfig(
        decoder_path=decoder,
        aplay_path="/usr/bin/aplay",
        stop_timeout_seconds=0.25,
        ca_bundle_path=ca_bundle,
    )


def test_engine_launches_static_decoder_and_pipes_s16le_pcm_to_aplay(tmp_path: Path) -> None:
    factory = FakeFactory()
    engine = FfmpegAplayEngine(config(tmp_path), factory)

    engine.play(PlaybackSource("https://cdn.example.test/ep.mp3", local=False), 12.5)

    ffmpeg_args, ffmpeg_stdin, decoder_pipe = factory.starts[0]
    aplay_args, aplay_stdin, aplay_pipe = factory.starts[1]
    assert ffmpeg_args[0].endswith("runtime/bin/ffmpeg")
    assert ("-ss", "12.500", "-i", "https://cdn.example.test/ep.mp3") == (
        ffmpeg_args[ffmpeg_args.index("-ss")],
        ffmpeg_args[ffmpeg_args.index("-ss") + 1],
        ffmpeg_args[ffmpeg_args.index("-i")],
        ffmpeg_args[ffmpeg_args.index("-i") + 1],
    )
    assert ffmpeg_args[-10:] == (
        "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2", "pipe:1"
    )
    assert ffmpeg_args[ffmpeg_args.index("-protocol_whitelist") + 1] == "http,https,tcp,tls,crypto,httpproxy,data"
    assert ("-user_agent", engine.config.user_agent) == (
        ffmpeg_args[ffmpeg_args.index("-user_agent")],
        ffmpeg_args[ffmpeg_args.index("-user_agent") + 1],
    )
    assert ("-ca_file", str(engine.config.ca_bundle_path)) == (
        ffmpeg_args[ffmpeg_args.index("-ca_file")],
        ffmpeg_args[ffmpeg_args.index("-ca_file") + 1],
    )
    assert not any("tls_verify" in arg for arg in ffmpeg_args)
    assert aplay_args == (
        "/usr/bin/aplay", "--quiet", "--format=S16_LE", "--rate=48000", "--channels=2"
    )
    assert ffmpeg_stdin is None and decoder_pipe is True
    assert aplay_stdin is factory.processes[0].stdout and aplay_pipe is False


def test_local_playback_uses_absolute_file_and_seek_restarts_clean_pipeline(tmp_path: Path) -> None:
    factory = FakeFactory()
    engine = FfmpegAplayEngine(config(tmp_path), factory)
    media = (tmp_path / "downloads" / "ep.mp3").resolve()

    engine.play(PlaybackSource(str(media), local=True), 0)
    old_decoder, old_aplay = factory.processes
    engine.seek(15)

    assert factory.starts[2][0][factory.starts[2][0].index("-i") + 1] == str(media)
    assert "https" not in factory.starts[2][0][factory.starts[2][0].index("-protocol_whitelist") + 1]
    assert "-user_agent" not in factory.starts[2][0]
    assert "-ca_file" not in factory.starts[2][0]
    assert old_decoder.calls[0] == ("terminate",)
    assert old_aplay.calls[0] == ("terminate",)
    assert ("kill",) in old_decoder.calls and ("kill",) in old_aplay.calls


def test_pause_resume_signal_both_children_in_audio_safe_order(tmp_path: Path) -> None:
    factory = FakeFactory()
    engine = FfmpegAplayEngine(config(tmp_path), factory)
    engine.play(PlaybackSource("https://cdn.example.test/ep.mp3", local=False), 0)
    decoder, aplay = factory.processes

    engine.pause()
    engine.resume()

    assert decoder.calls[:2] == [("suspend",), ("resume",)]
    assert aplay.calls[:2] == [("suspend",), ("resume",)]


def test_launch_and_child_exit_are_typed_and_stderr_is_bounded(tmp_path: Path) -> None:
    factory = FakeFactory()
    factory.fail_on = "aplay"
    engine = FfmpegAplayEngine(config(tmp_path), factory)

    with pytest.raises(PlaybackError) as caught:
        engine.play(PlaybackSource("https://cdn.example.test/ep.mp3", local=False), 0)
    assert caught.value.code == "audio_output_not_found"
    assert factory.processes[0].calls[0] == ("terminate",)

    healthy_factory = FakeFactory()
    healthy = FfmpegAplayEngine(config(tmp_path), healthy_factory)
    healthy.play(PlaybackSource("https://cdn.example.test/ep.mp3", local=False), 0)
    decoder, aplay = healthy_factory.processes
    decoder.stderr_text = "x" * 100_000
    decoder.returncode = 1
    aplay.returncode = 0
    events = tuple(healthy.events())
    assert events[0].state is PlaybackState.ERROR
    assert events[0].error_code == "decoder_failed"
    assert len(healthy.diagnostic_stderr) <= healthy.config.stderr_limit_bytes


def test_events_prioritizes_aplay_failure_over_broken_pipe_decoder_failure(tmp_path: Path) -> None:
    factory = FakeFactory()
    engine = FfmpegAplayEngine(config(tmp_path), factory)
    engine.play(PlaybackSource("https://cdn.example.test/ep.mp3", local=False), 0)
    decoder, aplay = factory.processes

    # Both fail (e.g. aplay crashes with 1, decoder dies of broken pipe with 1)
    decoder.returncode = 1
    aplay.returncode = 1

    events = tuple(engine.events())
    assert len(events) == 1
    assert events[0].state is PlaybackState.ERROR
    assert events[0].error_code == "audio_output_failed"


def test_subprocess_factory_never_uses_shell_and_drains_stderr_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    class FakePopen:
        def __init__(self, args: list[str], **kwargs: object) -> None:
            calls.append((tuple(args), kwargs))
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO(b"decoder warning\n" * 2000)
            self.returncode = 0
            self.pid = 123

        def poll(self) -> int:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    monkeypatch.setattr("anberpod.adapters.ffmpeg_aplay.subprocess.Popen", FakePopen)
    process = SubprocessProcessFactory(stderr_limit_bytes=128).start(["ffmpeg", "-version"], pipe_stdout=True)
    process.wait(1)

    assert calls[0][1]["shell"] is False
    assert calls[0][1]["close_fds"] is True
    assert len(process.stderr_text.encode("utf-8")) <= 128


def test_default_ca_bundle_path_is_etc_ssl_certs_ca_certificates_crt() -> None:
    assert DEFAULT_CA_BUNDLE == Path("/etc/ssl/certs/ca-certificates.crt")
    conf = FfmpegAplayConfig(decoder_path=Path("/usr/bin/ffmpeg"))
    assert conf.ca_bundle_path == Path("/etc/ssl/certs/ca-certificates.crt")


def test_remote_playback_supplies_custom_ca_bundle_when_readable(tmp_path: Path) -> None:
    custom_ca = tmp_path / "custom_ca.pem"
    custom_ca.write_text("custom certificate bundle", encoding="utf-8")
    factory = FakeFactory()
    engine = FfmpegAplayEngine(config(tmp_path, ca_bundle=custom_ca), factory)

    engine.play(PlaybackSource("https://cdn.example.test/stream.mp3", local=False), 0)

    ffmpeg_args = factory.starts[0][0]
    assert "-ca_file" in ffmpeg_args
    assert ffmpeg_args[ffmpeg_args.index("-ca_file") + 1] == str(custom_ca)
    assert not any("tls_verify" in arg for arg in ffmpeg_args)


def test_remote_playback_omits_ca_file_when_ca_bundle_missing(tmp_path: Path) -> None:
    missing_ca = tmp_path / "nonexistent" / "ca.crt"
    factory = FakeFactory()
    engine = FfmpegAplayEngine(config(tmp_path, ca_bundle=missing_ca), factory)

    engine.play(PlaybackSource("https://cdn.example.test/stream.mp3", local=False), 0)

    ffmpeg_args = factory.starts[0][0]
    assert "-ca_file" not in ffmpeg_args
    assert not any("tls_verify" in arg for arg in ffmpeg_args)


def test_remote_playback_omits_ca_file_when_ca_bundle_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unreadable_ca = tmp_path / "unreadable_ca.crt"
    unreadable_ca.write_text("ca content", encoding="utf-8")
    factory = FakeFactory()
    engine = FfmpegAplayEngine(config(tmp_path, ca_bundle=unreadable_ca), factory)

    import os
    orig_access = os.access

    def fake_access(path: object, mode: int, *args: object, **kwargs: object) -> bool:
        if str(path) == str(unreadable_ca) and mode == os.R_OK:
            return False
        return orig_access(path, mode, *args, **kwargs)

    monkeypatch.setattr("os.access", fake_access)

    engine.play(PlaybackSource("https://cdn.example.test/stream.mp3", local=False), 0)

    ffmpeg_args = factory.starts[0][0]
    assert "-ca_file" not in ffmpeg_args
    assert not any("tls_verify" in arg for arg in ffmpeg_args)


def test_remote_playback_omits_ca_file_when_ca_bundle_is_none(tmp_path: Path) -> None:
    factory = FakeFactory()
    engine = FfmpegAplayEngine(config(tmp_path, ca_bundle=None), factory)

    engine.play(PlaybackSource("https://cdn.example.test/stream.mp3", local=False), 0)

    ffmpeg_args = factory.starts[0][0]
    assert "-ca_file" not in ffmpeg_args
    assert not any("tls_verify" in arg for arg in ffmpeg_args)


def test_local_playback_never_supplies_ca_file_even_with_readable_ca(tmp_path: Path) -> None:
    ca = tmp_path / "ca.crt"
    ca.write_text("ca bundle", encoding="utf-8")
    local_file = tmp_path / "media.mp3"
    local_file.write_bytes(b"audio data")
    factory = FakeFactory()
    engine = FfmpegAplayEngine(config(tmp_path, ca_bundle=ca), factory)

    engine.play(PlaybackSource(str(local_file), local=True), 0)

    ffmpeg_args = factory.starts[0][0]
    assert "-ca_file" not in ffmpeg_args


