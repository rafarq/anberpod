from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw


ICON_DIR = Path(__file__).resolve().parents[2] / "src" / "anberpod" / "assets" / "icons"
ICON_NAMES = ("explore.png", "search.png", "subscriptions.png", "downloads.png", "settings.png")


@pytest.mark.parametrize("name", ICON_NAMES)
def test_home_icon_is_clean_inset_rgba_and_composites_on_checkerboard(name: str) -> None:
    with Image.open(ICON_DIR / name) as source:
        icon = source.copy()

    assert icon.mode == "RGBA"
    assert icon.size == (192, 192)
    alpha = icon.getchannel("A")
    assert alpha.getextrema() == (0, 255)
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((191, 191)) == 0
    assert alpha.getbbox() is not None
    left, top, right, bottom = alpha.getbbox()
    assert left >= 8 and top >= 8
    assert right <= 184 and bottom <= 184

    checker = Image.new("RGB", icon.size)
    draw = ImageDraw.Draw(checker)
    for y in range(0, 192, 12):
        for x in range(0, 192, 12):
            shade = "#d7d7d7" if (x // 12 + y // 12) % 2 == 0 else "#8f8f8f"
            draw.rectangle((x, y, x + 11, y + 11), fill=shade)
    composite = checker.copy()
    composite.paste(icon, mask=alpha)
    assert composite.tobytes() != checker.tobytes()
    # Purple artwork and glow survive the alpha cleanup.
    assert any(b > r and b > g for r, g, b, a in icon.get_flattened_data() if a >= 128)
