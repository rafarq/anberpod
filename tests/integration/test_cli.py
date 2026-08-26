from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image


def test_headless_cli_renders_review_screens_and_exits_cleanly(tmp_path: Path) -> None:
    render_dir = tmp_path / "review shots"
    data_dir = tmp_path / "persistent data"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path.cwd() / "src")

    result = subprocess.run(
        [sys.executable, "-m", "anberpod", "--data-dir", str(data_dir), "--render-dir", str(render_dir), "--demo"],
        cwd=Path.cwd(), env=environment, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in render_dir.glob("*.png")) == [
        "downloads.png", "explore.png", "home.png", "player.png", "podcast.png", "rss_import.png", "search.png",
        "search_results.png", "settings.png", "subscriptions.png"
    ]
    for path in render_dir.glob("*.png"):
        with Image.open(path) as image:
            assert image.size == (640, 480)
    cached_covers = list((data_dir / "cache" / "artwork").glob("*.png"))
    assert len(cached_covers) == 1
    with Image.open(render_dir / "player.png") as player:
        assert player.getpixel((116, 216)) == (246, 248, 255)
    assert "Saved Science" in result.stdout
    assert (data_dir / "logs" / "anberpod.log").is_file()
