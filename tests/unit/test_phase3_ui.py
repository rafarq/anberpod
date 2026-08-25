from __future__ import annotations

from pathlib import Path

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


def test_missing_catalog_credentials_leaves_local_features_available(tmp_path: Path) -> None:
    app = Application.open(DataPaths.create(tmp_path / "data"), Online())
    saved = Podcast("saved", "https://feeds.example.test/saved.xml", "Saved locally")
    app.repositories.podcasts.save(saved)

    app.refresh_categories()

    explore = app.screen()
    assert explore.route is Route.EXPLORE
    assert explore.items == ()
    assert explore.status == "Configure Podcast Index in data/config/config.toml"
    preserved = app.repositories.podcasts.get("saved")
    assert preserved is not None
    assert (preserved.title, preserved.feed_url) == (saved.title, saved.feed_url)
