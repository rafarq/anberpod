from __future__ import annotations

from pathlib import Path

from PIL import Image

from anberpod.domain.models import InputAction, InputEvent
from anberpod.ui.renderer import Renderer
from anberpod.ui.state import AppState, Route, ScreenModel


def test_navigation_focus_back_and_menu_work_without_keyboard() -> None:
    state = AppState()
    assert state.route is Route.HOME
    assert state.focus == 0

    state.handle(InputEvent(InputAction.DOWN))
    state.handle(InputEvent(InputAction.DOWN, repeated=True))
    assert state.focus == 2
    state.handle(InputEvent(InputAction.ACCEPT))
    assert state.route is Route.SUBSCRIPTIONS
    state.handle(InputEvent(InputAction.BACK))
    assert state.route is Route.HOME
    assert state.focus == 2
    state.handle(InputEvent(InputAction.ACCEPT, repeated=True))
    assert state.route is Route.HOME
    state.handle(InputEvent(InputAction.MENU))
    assert state.exit_requested is True


def test_navigation_bounds_focus_on_local_list_screens() -> None:
    state = AppState()
    state.handle(InputEvent(InputAction.DOWN))
    state.handle(InputEvent(InputAction.DOWN))
    state.handle(InputEvent(InputAction.ACCEPT))
    state.set_item_count(2)

    state.handle(InputEvent(InputAction.DOWN))
    state.handle(InputEvent(InputAction.DOWN, repeated=True))
    assert state.focus == 1
    state.handle(InputEvent(InputAction.UP))
    assert state.focus == 0


def test_renderer_writes_deterministic_640x480_png_without_sdl(tmp_path: Path) -> None:
    renderer = Renderer()
    screen = ScreenModel(
        Route.SUBSCRIPTIONS,
        "Subscriptions",
        ("Local Science", "History Hour"),
        focus=1,
        status="Offline - showing saved library",
    )
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"

    renderer.save(screen, first)
    renderer.save(screen, second)

    with Image.open(first) as image:
        assert image.size == (640, 480)
        assert image.mode == "RGB"
        colors = image.getcolors(maxcolors=640 * 480)
        assert colors is not None and len(colors) > 4
    assert first.read_bytes() == second.read_bytes()
