from __future__ import annotations

from pathlib import Path

from PIL import Image

from anberpod.domain.models import InputAction, InputEvent
from anberpod.ui.renderer import Renderer
from anberpod.ui.state import AppState, Route, ScreenModel


def _pixel_present(image: Image.Image, color: tuple[int, int, int], box) -> bool:
    """Whether ``color`` appears anywhere inside ``box`` (used to detect a rendered banner)."""
    cropped = image.crop(box).convert("RGB")
    return any(pixel == color for pixel in cropped.getdata())


def test_focused_item_is_always_visible_in_a_long_list() -> None:
    """Regression: the renderer used to hard-clip to the first 9 items with
    no scrolling, so focus movement past that point was invisible (e.g.
    Podcast Index's 100+ Explore categories)."""
    renderer = Renderer()
    items = tuple(f"Category {i}" for i in range(120))

    early = ScreenModel(Route.EXPLORE, "Explore", items, focus=0, footer="footer")
    late = ScreenModel(Route.EXPLORE, "Explore", items, focus=110, footer="footer")

    early_frame = renderer.render(early)
    late_frame = renderer.render(late)

    # Different focus positions in a long list must render visibly
    # different frames (the visible window must have scrolled).
    assert list(early_frame.getdata()) != list(late_frame.getdata())


def test_offline_banner_only_renders_when_actually_offline() -> None:
    renderer = Renderer()
    screen = ScreenModel(Route.SUBSCRIPTIONS, "Subscriptions", (), footer="footer")

    online_frame = renderer.render(screen, offline=False)
    offline_frame = renderer.render(screen, offline=True)

    # The banner draws #8ca0bd text in the top-right corner strip; it must
    # only appear when the app is actually offline, not unconditionally.
    top_right_box = (500, 0, 640, 40)
    assert _pixel_present(online_frame, (140, 160, 189), top_right_box) is False
    assert _pixel_present(offline_frame, (140, 160, 189), top_right_box) is True


def test_navigation_focus_back_and_menu_work_without_keyboard() -> None:
    state = AppState()
    assert state.route is Route.HOME
    assert state.focus == 0

    state.handle(InputEvent(InputAction.RIGHT))
    state.handle(InputEvent(InputAction.RIGHT, repeated=True))
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


def test_home_navigation_follows_the_three_plus_two_card_grid() -> None:
    state = AppState()

    state.handle(InputEvent(InputAction.RIGHT))
    assert state.focus == 1
    state.handle(InputEvent(InputAction.RIGHT))
    assert state.focus == 2
    state.handle(InputEvent(InputAction.RIGHT))
    assert state.focus == 2

    state.handle(InputEvent(InputAction.DOWN))
    assert state.focus == 4
    state.handle(InputEvent(InputAction.LEFT))
    assert state.focus == 3
    state.handle(InputEvent(InputAction.UP))
    assert state.focus == 0

    state.handle(InputEvent(InputAction.ACCEPT))
    assert state.route is Route.EXPLORE
    state.handle(InputEvent(InputAction.BACK))
    assert (state.route, state.focus) == (Route.HOME, 0)


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


def test_home_renderer_draws_large_cards_without_using_the_list_layout() -> None:
    image = Renderer().render(ScreenModel(
        Route.HOME,
        "Home",
        ("Explore", "Search", "Subscriptions", "Downloads", "Settings"),
        focus=0,
    ))

    # Selected card has a bright, thick top-left outline; every card has a
    # distinct interior in the expected 3+2 geometry.
    assert image.getpixel((36, 116)) == (216, 184, 255)
    for point in ((110, 180), (315, 180), (520, 180), (215, 350), (421, 350)):
        assert image.getpixel(point) != (10, 16, 32)

    # A full-width strip between each icon and label remains untouched card fill.
    card_boxes = ((18, 116, 212, 268), (223, 116, 417, 268), (428, 116, 622, 268),
                  (120, 280, 314, 432), (326, 280, 520, 432))
    for index, (left, top, right, _bottom) in enumerate(card_boxes):
        expected_fill = (48, 38, 83) if index == 0 else (21, 31, 53)
        for y in range(top + 104, top + 112):
            assert all(image.getpixel((x, y)) == expected_fill for x in range(left + 10, right - 10))


def test_home_icon_loader_is_cached_and_has_a_vector_fallback(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import anberpod.ui.renderer as renderer_module

    renderer_module._load_icon.cache_clear()
    opened: list[Path] = []
    real_open = renderer_module.Image.open

    def tracking_open(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append(Path(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(renderer_module.Image, "open", tracking_open)
    renderer = Renderer()
    screen = ScreenModel(Route.HOME, "Home", ("Explore", "Search", "Subscriptions", "Downloads", "Settings"))
    renderer.render(screen)
    renderer.render(screen)
    assert len(opened) == 5
    assert all("src/anberpod/assets/icons" in path.as_posix() for path in opened)

    renderer_module._load_icon.cache_clear()
    monkeypatch.setitem(renderer_module.HOME_ICON_FILES, Route.EXPLORE, "missing.png")
    fallback = renderer.render(screen)
    # The missing Explore icon is replaced with a conspicuous vector question mark badge.
    assert fallback.getpixel((115, 174)) == (255, 255, 255)
