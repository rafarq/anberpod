from __future__ import annotations

from pathlib import Path

from anberpod.adapters.itunes import ITunesCatalogClient
from anberpod.app import Application
from anberpod.config import DataPaths
from anberpod.domain.models import DiscoveryResult, InputAction, InputEvent, Podcast
from anberpod.ui.state import Route, VirtualKeyboard


class Online:
    def is_online(self) -> bool:
        return True


def press(keyboard: VirtualKeyboard, action: InputAction, count: int = 1) -> None:
    for _ in range(count):
        keyboard.handle(InputEvent(action))


def test_virtual_keyboard_can_enter_search_using_dpad_a_b() -> None:
    keyboard = VirtualKeyboard()
    press(keyboard, InputAction.ACCEPT)  # a
    press(keyboard, InputAction.RIGHT)
    press(keyboard, InputAction.ACCEPT)  # b
    press(keyboard, InputAction.BACK)  # delete b
    press(keyboard, InputAction.DOWN)
    press(keyboard, InputAction.RIGHT)
    press(keyboard, InputAction.ACCEPT)  # i
    press(keyboard, InputAction.DOWN, 5)
    press(keyboard, InputAction.ACCEPT)  # search

    assert keyboard.query == "ai"
    assert keyboard.take_submission() == "ai"
    assert keyboard.take_submission() is None


def test_discovery_and_search_results_are_consumable_by_headless_ui(tmp_path: Path) -> None:
    app = Application.open(DataPaths.create(tmp_path / "data"), Online())
    found = Podcast(
        "catalog:42",
        "https://feeds.example.test/show.xml",
        "A Show",
        author="A Person",
        catalog_id=42,
    )

    app.show_categories(DiscoveryResult(("Arts", "Technology"), cached=True, stale=True,
                                        warning_code="catalog_unavailable"))
    explore = app.screen()
    assert explore.route is Route.EXPLORE
    assert explore.items == ("Arts", "Technology")
    assert explore.status == "Saved categories - catalog_unavailable"

    app.show_search_results("a show", DiscoveryResult((found,)))
    results = app.screen()
    assert results.route is Route.SEARCH_RESULTS
    assert results.items == ("A Show  -  A Person",)

    app.handle(InputEvent(InputAction.ACCEPT))
    detail = app.screen()
    assert detail.route is Route.PODCAST
    assert detail.title == "A Show"
    assert detail.items[:2] == ("Update now", "Subscribe")

    app.handle(InputEvent(InputAction.DOWN))
    app.handle(InputEvent(InputAction.ACCEPT))
    assert app.repositories.podcasts.is_subscribed("catalog:42") is True
    assert app.screen().items[1] == "Unsubscribe"


def test_search_route_exposes_keyboard_and_pending_submission(tmp_path: Path) -> None:
    app = Application.open(DataPaths.create(tmp_path / "data"), Online())
    app.state.show(Route.SEARCH)

    initial = app.screen()
    assert initial.title == "Search"
    assert initial.items[0] == "Query: _"
    app.handle(InputEvent(InputAction.ACCEPT))
    app.handle(InputEvent(InputAction.RIGHT))
    app.handle(InputEvent(InputAction.ACCEPT))
    app.handle(InputEvent(InputAction.DOWN, repeated=True))

    assert app.screen().items[0] == "Query: ab_"
    assert app.pending_search_query is None


def test_missing_podcast_index_credentials_falls_back_to_itunes_by_default(tmp_path: Path) -> None:
    """Regression: without a configured Podcast Index key, Explore/Search
    must still work via the keyless iTunes Search API default, not show a
    permanent 'configure credentials' dead end."""
    app = Application.open(DataPaths.create(tmp_path / "data"), Online())
    saved = Podcast("saved", "https://feeds.example.test/saved.xml", "Saved locally")
    app.repositories.podcasts.save(saved)

    assert isinstance(app.discovery.catalog, ITunesCatalogClient)

    preserved = app.repositories.podcasts.get("saved")
    assert preserved is not None
    assert (preserved.title, preserved.feed_url) == (saved.title, saved.feed_url)


class FakeDiscovery:
    def __init__(self) -> None:
        self.categories_called = False
        self.search_called_with: str | None = None

    def categories(self) -> DiscoveryResult:
        self.categories_called = True
        return DiscoveryResult(("Arts", "Technology"), cached=False)

    def search(self, query: str, limit: int) -> DiscoveryResult:
        self.search_called_with = query
        return DiscoveryResult((Podcast("catalog:1", "https://feeds.example.test/found.xml", "Found Show"),), cached=False)


def test_opening_explore_from_home_autoloads_categories(tmp_path: Path) -> None:
    """Regression: entering Explore must trigger a real catalog fetch, not just switch screens."""
    discovery = FakeDiscovery()
    app = Application.open(DataPaths.create(tmp_path / "data"), Online(), discovery=discovery)

    app.handle(InputEvent(InputAction.ACCEPT))  # Home focus 0 == Explore

    assert discovery.categories_called is True
    screen = app.screen()
    assert screen.route is Route.EXPLORE
    assert screen.items == ("Arts", "Technology")


def test_submitting_search_query_autoloads_results(tmp_path: Path) -> None:
    """Regression: submitting the on-screen keyboard must trigger a real search, not just stash the query."""
    discovery = FakeDiscovery()
    app = Application.open(DataPaths.create(tmp_path / "data"), Online(), discovery=discovery)
    app.state.show(Route.SEARCH)

    press(app.keyboard, InputAction.ACCEPT)  # 'a'
    for _ in range(6):
        press(app.keyboard, InputAction.DOWN)
    for _ in range(2):
        press(app.keyboard, InputAction.RIGHT)
    app.handle(InputEvent(InputAction.ACCEPT))  # 'search' key -> submit

    assert discovery.search_called_with == "a"
    screen = app.screen()
    assert screen.route is Route.SEARCH_RESULTS
    assert "Found Show" in screen.items[0]
    assert app.pending_search_query is None


def test_selecting_a_category_in_explore_searches_by_that_category(tmp_path: Path) -> None:
    """Regression: pressing A on a category in Explore did nothing at all --
    there was no handler wired for Route.EXPLORE + ACCEPT."""
    discovery = FakeDiscovery()
    app = Application.open(DataPaths.create(tmp_path / "data"), Online(), discovery=discovery)

    app.handle(InputEvent(InputAction.ACCEPT))  # Home -> Explore, autoloads categories
    assert app.screen().items == ("Arts", "Technology")

    app.handle(InputEvent(InputAction.RIGHT))  # move focus onto "Technology"
    app.state.focus = 1
    app.handle(InputEvent(InputAction.ACCEPT))  # select the category

    assert discovery.search_called_with == "Technology"
    screen = app.screen()
    assert screen.route is Route.SEARCH_RESULTS
    assert "Found Show" in screen.items[0]


def test_selecting_a_subscription_opens_its_episode_list(tmp_path: Path) -> None:
    """Regression: pressing A on a podcast in Subscriptions did nothing --
    there was no handler wired for Route.SUBSCRIPTIONS + ACCEPT, so
    subscribed episodes were never reachable from that screen."""
    from datetime import datetime, timezone

    from anberpod.domain.models import Episode

    app = Application.open(DataPaths.create(tmp_path / "data"), Online())
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    app.repositories.podcasts.save(Podcast(
        "pod-a", "https://feeds.example.test/a.xml", "Podcast A", created_at=now, updated_at=now,
    ))
    app.repositories.podcasts.save(Podcast(
        "pod-b", "https://feeds.example.test/b.xml", "Podcast B", created_at=now, updated_at=now,
    ))
    app.repositories.podcasts.subscribe("pod-a", now)
    app.repositories.podcasts.subscribe("pod-b", now)
    app.repositories.episodes.upsert(Episode(
        "ep-b1", "pod-b", "guid:b1", "https://cdn.example.test/b1.mp3", "B Episode One",
        created_at=now, updated_at=now,
    ))

    app.state.show(Route.SUBSCRIPTIONS, 2)
    listing = app.screen()
    assert listing.items == ("Podcast A", "Podcast B")

    app.state.focus = 1  # "Podcast B"
    app.handle(InputEvent(InputAction.ACCEPT))

    detail = app.screen()
    assert detail.route is Route.PODCAST
    assert detail.title == "Podcast B"
    assert "B Episode One" in detail.items
