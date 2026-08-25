from __future__ import annotations

import json
from pathlib import Path

import pytest

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.domain.models import InputAction, InputEvent
from anberpod.domain.ports import (
    AtomicFilePort,
    Clock,
    ConnectivityProbe,
    CredentialProvider,
    DownloadRepository,
    DownloadRunner,
    EpisodeRepository,
    FeedReader,
    HttpTransport,
    InputSource,
    Logger,
    MonotonicClock,
    PlaybackEngine,
    PlaybackRepository,
    PodcastCatalog,
    PodcastRepository,
    SettingsRepository,
)
from anberpod.logging import configure_logging


class FakeInput:
    def poll(self) -> list[InputEvent]:
        return [InputEvent(InputAction.ACCEPT)]


def test_domain_ports_are_runtime_mockable_without_platform_dependencies() -> None:
    assert isinstance(FakeInput(), InputSource)
    assert FakeInput().poll() == [InputEvent(InputAction.ACCEPT)]


def test_all_planned_external_boundaries_are_declared_as_protocols() -> None:
    ports = (
        Clock, MonotonicClock, PodcastCatalog, HttpTransport, FeedReader,
        PodcastRepository, EpisodeRepository, PlaybackRepository, DownloadRepository,
        SettingsRepository, AtomicFilePort, DownloadRunner, PlaybackEngine,
        InputSource, ConnectivityProbe, CredentialProvider, Logger,
    )
    assert all(getattr(port, "_is_protocol", False) for port in ports)


def test_logs_redact_api_secret_authorization_and_query_values(tmp_path: Path) -> None:
    logger = configure_logging(tmp_path / "anberpod.log", max_bytes=4096, backups=1)
    logger.info(
        "request failed",
        extra={
            "event": "catalog_error",
            "url": "https://example.test/search?q=private+show&api_key=key123",
            "authorization": "Bearer top-secret",
            "api_secret": "top-secret",
        },
    )

    payload = json.loads((tmp_path / "anberpod.log").read_text(encoding="utf-8"))
    rendered = json.dumps(payload)
    assert payload["event"] == "catalog_error"
    assert payload["url"] == "https://example.test/search?q=%5BREDACTED%5D&api_key=%5BREDACTED%5D"
    assert "top-secret" not in rendered
    assert "private" not in rendered


def test_atomic_cache_interruption_keeps_previous_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = AtomicFiles(tmp_path)
    destination = tmp_path / "cache" / "item.json"
    destination.parent.mkdir()
    destination.write_bytes(b"old-valid")

    def interrupted(_source: Path, _destination: Path) -> None:
        raise OSError("power loss")

    monkeypatch.setattr("anberpod.adapters.filesystem.os.replace", interrupted)
    with pytest.raises(OSError, match="power loss"):
        files.write_atomic("cache/item.json", b"new-data")

    assert destination.read_bytes() == b"old-valid"
    assert list(destination.parent.glob("*.tmp")) == []
