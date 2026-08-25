from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from anberpod.app import Application
from anberpod.config import DataPaths
from anberpod.domain.models import Episode, Podcast
from anberpod.services.import_preview import ImportPreview, ImportStatus
from anberpod.ui.state import Route


NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


class Offline:
    def is_online(self) -> bool:
        return False


def test_headless_podcast_detail_and_import_result_models_are_minimal(tmp_path: Path) -> None:
    app = Application.open(DataPaths.create(tmp_path / "data"), Offline())
    app.repositories.podcasts.save(Podcast(
        "pod", "https://feeds.example.test/show", "Saved Show", author="Alice", created_at=NOW, updated_at=NOW
    ))
    app.repositories.podcasts.subscribe("pod", NOW)
    app.repositories.episodes.upsert(Episode(
        "ep", "pod", "guid:ep", "https://cdn.example.test/ep.mp3", "Newest Episode",
        created_at=NOW, updated_at=NOW,
    ))

    app.show_podcast("pod")
    detail = app.screen()
    assert detail.route is Route.PODCAST
    assert detail.title == "Saved Show"
    assert detail.items == ("Update now", "Unsubscribe", "Newest Episode")

    app.show_import_results([
        ImportPreview(1, "https://feeds.example.test/show", ImportStatus.DUPLICATE, podcast=app.repositories.podcasts.get("pod")),
        ImportPreview(2, "https://bad.example.test/feed", ImportStatus.ERROR, error_code="non_public_address"),
    ])
    imported = app.screen()
    assert imported.route is Route.RSS_IMPORT
    assert imported.items == ("DUPLICATE  Saved Show", "ERROR  non_public_address")
    assert "results saved" in imported.footer.lower()
