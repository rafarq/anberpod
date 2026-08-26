from __future__ import annotations

from pathlib import Path

from PIL import Image

from anberpod.domain.models import PlaybackState
from anberpod.ui.renderer import PLAYER_LAYOUT, Renderer
from anberpod.ui.state import PlayerViewModel, Route


def test_player_view_model_exposes_state_progress_source_and_physical_controls() -> None:
    player = PlayerViewModel(
        episode_id="ep",
        episode_title="How Stars Begin",
        podcast_title="Saved Science",
        state=PlaybackState.PAUSED,
        position_ms=65_000,
        duration_ms=1_800_000,
        local=True,
        error_code="decoder_failed",
    )

    screen = player.screen()

    assert screen.route is Route.PLAYER
    assert screen.title == "Now Playing"
    assert screen.items == (
        "How Stars Begin",
        "Saved Science",
        "Paused",
        "01:05 / 30:00",
        "Source: Downloaded",
        "Error: decoder_failed",
    )
    assert screen.footer == "LEFT -15s   A Play/Pause   RIGHT +15s   B Stop"


def test_player_renderer_is_deterministic_640x480(tmp_path: Path) -> None:
    player = PlayerViewModel(
        episode_id="ep",
        episode_title="How Stars Begin",
        podcast_title="Saved Science",
        state=PlaybackState.PLAYING,
        position_ms=185_000,
        duration_ms=1_800_000,
        local=False,
    )
    renderer = Renderer()
    first = tmp_path / "player-1.png"
    second = tmp_path / "player-2.png"

    renderer.save_player(player, first)
    renderer.save_player(player, second)

    with Image.open(first) as image:
        assert image.size == (640, 480)
        assert image.mode == "RGB"
    assert first.read_bytes() == second.read_bytes()


def test_player_renders_168_square_cached_cover_and_branded_placeholder(tmp_path: Path) -> None:
    cover = tmp_path / "artwork" / "cover.png"
    cover.parent.mkdir()
    Image.new("RGB", (320, 180), "#d04a7a").save(cover)
    base = dict(
        episode_id="ep",
        episode_title="How Stars Begin",
        podcast_title="Saved Science",
        state=PlaybackState.PLAYING,
        position_ms=185_000,
        duration_ms=1_800_000,
        local=False,
    )
    renderer = Renderer(artwork_root=cover.parent)

    cached = renderer.render_player(PlayerViewModel(**base, artwork_path=cover))
    placeholder = renderer.render_player(PlayerViewModel(**base))

    # The 168x168 artwork occupies x=32..199, y=132..299; rounded corners expose the page.
    assert cached.getpixel((116, 216)) == (208, 74, 122)
    assert cached.getpixel((32, 132)) == (10, 16, 32)
    assert cached.getpixel((199, 299)) == (10, 16, 32)
    placeholder_colors = {color for _, color in placeholder.crop((32, 132, 200, 300)).getcolors(168 * 168) or []}
    assert (111, 53, 213) in placeholder_colors
    assert (54, 194, 180) in placeholder_colors


def test_player_corrupt_or_missing_cover_matches_placeholder(tmp_path: Path) -> None:
    corrupt = tmp_path / "artwork" / "cover.png"
    corrupt.parent.mkdir()
    corrupt.write_bytes(b"not an image")
    base = dict(
        episode_id="ep",
        episode_title="A very long episode title that still needs to remain readable without touching the artwork",
        podcast_title="A long saved podcast title",
        state=PlaybackState.PAUSED,
        position_ms=65_000,
        duration_ms=1_800_000,
        local=True,
    )
    renderer = Renderer(artwork_root=corrupt.parent)

    corrupt_render = renderer.render_player(PlayerViewModel(**base, artwork_path=corrupt))
    missing_render = renderer.render_player(PlayerViewModel(**base, artwork_path=corrupt.parent / "missing.png"))
    placeholder = renderer.render_player(PlayerViewModel(**base))

    box = (32, 132, 200, 300)
    assert corrupt_render.crop(box).tobytes() == placeholder.crop(box).tobytes()
    assert missing_render.crop(box).tobytes() == placeholder.crop(box).tobytes()


def test_player_layout_regions_are_in_bounds_and_do_not_overlap_at_640x480() -> None:
    boxes = tuple(PLAYER_LAYOUT.values())

    for left, top, right, bottom in boxes:
        assert 0 <= left < right <= 640
        assert 0 <= top < bottom <= 480
    for index, first in enumerate(boxes):
        for second in boxes[index + 1:]:
            overlap_width = min(first[2], second[2]) - max(first[0], second[0])
            overlap_height = min(first[3], second[3]) - max(first[1], second[1])
            assert overlap_width <= 0 or overlap_height <= 0
