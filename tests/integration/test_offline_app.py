from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from anberpod.app import Application
from anberpod.config import DataPaths
from anberpod.domain.models import Download, DownloadState, Episode, InputAction, InputEvent, Podcast
from anberpod.ui.state import Route


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class Offline:
    def is_online(self) -> bool:
        return False


def test_startup_offline_renders_valid_local_library(tmp_path: Path) -> None:
    paths = DataPaths.create(tmp_path / "data")
    app = Application.open(paths, Offline())
    app.repositories.podcasts.save(Podcast(
        "pod", "https://example.test/feed", "Saved Science", created_at=NOW, updated_at=NOW
    ))
    app.repositories.podcasts.subscribe("pod", NOW)
    app.repositories.episodes.upsert(Episode(
        "ep", "pod", "guid:ep", "https://example.test/ep.mp3", "Offline Episode",
        created_at=NOW, updated_at=NOW,
    ))
    app.repositories.downloads.save(Download(
        "ep", DownloadState.QUEUED, bytes_total=1234, created_at=NOW, updated_at=NOW
    ))

    subscriptions = app.screen(Route.SUBSCRIPTIONS)
    downloads = app.screen(Route.DOWNLOADS)

    assert subscriptions.items == ("Saved Science",)
    assert downloads.items == ("Offline Episode  -  Queued  0.0 / 1.2 KiB",)
    assert downloads.status == "Offline - complete downloads remain playable"
    assert subscriptions.status == "Offline - showing saved local data"


def test_menu_logs_clean_shutdown_without_network_or_platform_workers(tmp_path: Path) -> None:
    paths = DataPaths.create(tmp_path / "data")
    app = Application.open(paths, Offline())

    app.handle(InputEvent(InputAction.MENU))

    assert app.state.exit_requested is True
    entries = [json.loads(line) for line in (paths.logs / "anberpod.log").read_text(encoding="utf-8").splitlines()]
    assert [entry["event"] for entry in entries] == ["startup", "shutdown"]
