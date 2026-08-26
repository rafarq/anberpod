from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image, ImageDraw

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.adapters.sqlite import Repositories
from anberpod.config import DataPaths
from anberpod.domain.models import Download, DownloadState, Episode, Playback, Podcast


_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
DEMO_COVER_URL = "https://fixtures.example.invalid/anberpod-demo-cover.png"


def seed_demo_library(repositories: Repositories, paths: DataPaths | None = None) -> None:
    podcasts = (
        Podcast("demo-science", "https://example.invalid/science.xml", "Saved Science", author="AnberPod Fixture",
                image_url=DEMO_COVER_URL, created_at=_NOW, updated_at=_NOW),
        Podcast("demo-history", "https://example.invalid/history.xml", "History Hour", author="AnberPod Fixture", created_at=_NOW, updated_at=_NOW),
    )
    episodes = (
        Episode("demo-ep-1", "demo-science", "guid:demo-1", "https://example.invalid/one.mp3", "How Stars Begin", created_at=_NOW, updated_at=_NOW),
        Episode("demo-ep-2", "demo-history", "guid:demo-2", "https://example.invalid/two.mp3", "The First Libraries", created_at=_NOW, updated_at=_NOW),
    )
    for podcast in podcasts:
        repositories.podcasts.save(podcast)
        repositories.podcasts.subscribe(podcast.id, _NOW)
    for episode in episodes:
        repositories.episodes.upsert(episode)
    repositories.playback.save(Playback("demo-ep-1", 185_000, 1_800_000, updated_at=_NOW))
    repositories.downloads.save(Download(
        "demo-ep-1", DownloadState.QUEUED, bytes_received=0, bytes_total=42_000_000,
        created_at=_NOW, updated_at=_NOW,
    ))
    repositories.downloads.save(Download(
        "demo-ep-2", DownloadState.FAILED, temp_relative_path="downloads/demo-ep-2.part",
        bytes_received=3_200_000, bytes_total=35_000_000, error_code="interrupted",
        created_at=_NOW, updated_at=_NOW,
    ))
    if paths is not None:
        _write_demo_cover(paths)


def _write_demo_cover(paths: DataPaths) -> None:
    """Create a deterministic offline cover without depending on cwd or network."""
    image = Image.new("RGB", (336, 336), "#20153d")
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 40, 296, 296), fill="#6f35d5", outline="#36c2b4", width=12)
    draw.rounded_rectangle((139, 93, 197, 207), radius=29, fill="#f6f8ff")
    draw.arc((112, 137, 224, 249), 0, 180, fill="#36c2b4", width=12)
    draw.line((168, 246, 168, 276), fill="#f6f8ff", width=12)
    draw.line((133, 276, 203, 276), fill="#f6f8ff", width=12)
    # Geometric "AP" mark avoids host-font differences in review fixtures.
    draw.line((128, 322, 140, 292, 152, 322), fill="#f6f8ff", width=7)
    draw.line((133, 309, 147, 309), fill="#f6f8ff", width=6)
    draw.line((166, 322, 166, 292, 183, 292), fill="#f6f8ff", width=7)
    draw.arc((169, 292, 197, 312), 270, 90, fill="#f6f8ff", width=7)
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    digest = hashlib.sha256(DEMO_COVER_URL.encode("utf-8")).hexdigest()
    AtomicFiles(paths.root).write_atomic(f"cache/artwork/{digest}.png", output.getvalue())
