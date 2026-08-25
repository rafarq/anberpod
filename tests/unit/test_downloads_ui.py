from __future__ import annotations

from pathlib import Path

from PIL import Image

from anberpod.domain.models import DownloadState
from anberpod.ui.renderer import Renderer
from anberpod.ui.state import DownloadItemViewModel, DownloadsViewModel, Route


def test_downloads_view_model_formats_state_size_and_error_deterministically() -> None:
    model = DownloadsViewModel((
        DownloadItemViewModel("one", "Ready episode", DownloadState.COMPLETE, 8_388_608, 8_388_608),
        DownloadItemViewModel("two", "Retry episode", DownloadState.FAILED, 524_288, 2_097_152, "interrupted"),
        DownloadItemViewModel("three", "Waiting episode", DownloadState.QUEUED, 0, None),
    ))

    screen = model.screen(focus=1, offline=True)

    assert screen.route is Route.DOWNLOADS
    assert screen.title == "Downloads"
    assert screen.items == (
        "Ready episode  -  Complete  8.0 / 8.0 MiB",
        "Retry episode  -  Failed  0.5 / 2.0 MiB  [interrupted]",
        "Waiting episode  -  Queued  0 B",
    )
    assert screen.focus == 1
    assert screen.status == "Offline - complete downloads remain playable"
    assert screen.footer == "D-Pad Navigate   A Open/Actions   B Back   MENU Exit"


def test_downloads_headless_render_is_deterministic_640x480(tmp_path: Path) -> None:
    screen = DownloadsViewModel((
        DownloadItemViewModel("one", "Ready episode", DownloadState.COMPLETE, 8_388_608, 8_388_608),
        DownloadItemViewModel("two", "Retry episode", DownloadState.FAILED, 524_288, 2_097_152, "interrupted"),
    )).screen(focus=0, offline=True)
    first = tmp_path / "downloads-first.png"
    second = tmp_path / "downloads-second.png"

    Renderer().save(screen, first)
    Renderer().save(screen, second)

    with Image.open(first) as image:
        assert image.size == (640, 480)
        assert image.mode == "RGB"
    assert first.read_bytes() == second.read_bytes()
