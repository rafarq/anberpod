from __future__ import annotations

from pathlib import Path

from PIL import Image

from anberpod.domain.models import PlaybackState
from anberpod.ui.renderer import Renderer
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
