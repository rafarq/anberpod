from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from anberpod.app import Application
from anberpod.config import DataPaths
from anberpod.domain.models import DiscoveryResult, Episode, FeedFetchResult, FeedValidators, ParsedFeed, Podcast
from anberpod.domain.models import InputAction, InputEvent
from anberpod.ui.state import Route


NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


class Online:
    def is_online(self) -> bool:
        return True


class FakeRssReader:
    """Serves one real RSS feed with episodes for a catalog-discovered podcast."""

    def __init__(self) -> None:
        self.fetch_calls: list[str] = []

    def fetch(self, url: str, validators: FeedValidators) -> FeedFetchResult:
        self.fetch_calls.append(url)
        return FeedFetchResult(url, b"<rss/>", FeedValidators("etag-1", "last-modified-1"))

    def parse(self, body: bytes, source_url: str) -> ParsedFeed:
        podcast = Podcast("catalog:99", source_url, "Discovered Show", author="Someone")
        episodes = (
            Episode("ep-1", "catalog:99", "guid:ep-1", "https://cdn.example.test/one.mp3", "Episode One"),
            Episode("ep-2", "catalog:99", "guid:ep-2", "https://cdn.example.test/two.mp3", "Episode Two"),
        )
        return ParsedFeed(podcast, episodes)


def test_subscribing_to_a_catalog_podcast_fetches_real_episodes(tmp_path: Path) -> None:
    """Regression: subscribing from Explore/Search must fetch RSS episodes,
    not just persist Podcast Index metadata with an empty episode list."""
    from anberpod.services.feeds import FeedService

    app = Application.open(DataPaths.create(tmp_path / "data"), Online())
    app.feeds = FeedService(FakeRssReader(), app.repositories, type("C", (), {"now_utc": lambda self: NOW})())

    catalog_podcast = Podcast("catalog:99", "https://feeds.example.test/discovered.xml", "Discovered Show")
    app._open_catalog_podcast(catalog_podcast)
    assert app.state.route is Route.PODCAST

    # Focus 1 is Subscribe on a freshly-opened, not-yet-subscribed podcast.
    app.state.focus = 1
    app.handle(InputEvent(InputAction.ACCEPT))

    assert app.repositories.podcasts.is_subscribed("catalog:99") is True
    episodes = app.repositories.episodes.list_for_podcast("catalog:99")
    assert [episode.title for episode in episodes] == ["Episode One", "Episode Two"]

    detail = app.screen()
    assert detail.items[2:] == ("Episode One", "Episode Two")


def test_update_now_action_refreshes_episode_list(tmp_path: Path) -> None:
    """Regression: the visible 'Update now' row must actually call FeedService,
    not be a dead menu entry."""
    from anberpod.services.feeds import FeedService

    app = Application.open(DataPaths.create(tmp_path / "data"), Online())
    reader = FakeRssReader()
    app.feeds = FeedService(reader, app.repositories, type("C", (), {"now_utc": lambda self: NOW})())

    app.repositories.podcasts.save(Podcast(
        "pod", "https://feeds.example.test/show.xml", "Saved Show", created_at=NOW, updated_at=NOW,
    ))
    app.repositories.podcasts.subscribe("pod", NOW)
    app.show_podcast("pod")
    assert app.repositories.episodes.list_for_podcast("pod") == []

    app.state.focus = 0  # "Update now"
    app.handle(InputEvent(InputAction.ACCEPT))

    assert reader.fetch_calls == ["https://feeds.example.test/show.xml"]
    episodes = app.repositories.episodes.list_for_podcast("pod")
    assert len(episodes) == 2
