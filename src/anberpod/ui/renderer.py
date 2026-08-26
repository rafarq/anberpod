from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .state import HOME_ROUTES, PlayerViewModel, Route, ScreenModel


WIDTH = 640
HEIGHT = 480
HOME_ICON_DIR = Path(__file__).resolve().parents[1] / "assets" / "icons"
HOME_ICON_FILES = {
    Route.EXPLORE: "explore.png",
    Route.SEARCH: "search.png",
    Route.SUBSCRIPTIONS: "subscriptions.png",
    Route.DOWNLOADS: "downloads.png",
    Route.SETTINGS: "settings.png",
}
HOME_CARD_BOXES = (
    (18, 116, 212, 268),
    (223, 116, 417, 268),
    (428, 116, 622, 268),
    (120, 280, 314, 432),
    (326, 280, 520, 432),
)


def _font(size: int):  # type: ignore[no-untyped-def]
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


@lru_cache(maxsize=len(HOME_ICON_FILES))
def _load_icon(filename: str) -> Image.Image | None:
    """Load and size a packaged Home icon once, independent of the cwd."""
    try:
        with Image.open(HOME_ICON_DIR / filename) as source:
            source.load()
            icon = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    icon.thumbnail((86, 86), Image.Resampling.LANCZOS)
    return icon


class Renderer:
    def __init__(self) -> None:
        self.title_font = _font(30)
        self.item_font = _font(23)
        self.small_font = _font(16)

    def render(self, screen: ScreenModel) -> Image.Image:
        if screen.route is Route.HOME:
            return self._render_home(screen)

        image = Image.new("RGB", (WIDTH, HEIGHT), "#0a1020")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, WIDTH, 68), fill="#16233f")
        draw.rectangle((0, 68, 8, HEIGHT), fill="#36c2b4")
        draw.text((26, 16), "ANBERPOD", font=self.title_font, fill="#f6f8ff")
        draw.text((610, 22), "OFFLINE", font=self.small_font, fill="#8ca0bd", anchor="ra")
        draw.text((26, 88), screen.title, font=self.title_font, fill="#78e0d4")

        dense = len(screen.items) > 6
        y = 132 if dense else 145
        if not screen.items:
            draw.text((32, y), "Nothing here yet", font=self.item_font, fill="#aab7cc")
            draw.text((32, y + 38), "Local data will appear on this screen.", font=self.small_font, fill="#7587a3")
        item_font = self.small_font if dense else self.item_font
        step = 32 if dense else 50
        for index, item in enumerate(screen.items[:8]):
            selected = index == screen.focus
            if selected:
                bottom = y + (25 if dense else 34)
                draw.rounded_rectangle((24, y - 6, 616, bottom), radius=8, fill="#2b6170", outline="#78e0d4", width=2)
            prefix = ">" if selected else " "
            text = item if len(item) <= 46 else item[:43] + "..."
            draw.text((38, y), f"{prefix} {text}", font=item_font, fill="#ffffff" if selected else "#c6d0df")
            y += step

        if screen.status:
            draw.rounded_rectangle((24, 390, 616, 428), radius=6, fill="#332b18")
            draw.text((38, 400), screen.status, font=self.small_font, fill="#ffd27d")
        draw.rectangle((0, 444, WIDTH, HEIGHT), fill="#111b30")
        draw.text((320, 462), screen.footer, font=self.small_font, fill="#9eb0c9", anchor="mm")
        return image

    def _render_home(self, screen: ScreenModel) -> Image.Image:
        image = Image.new("RGB", (WIDTH, HEIGHT), "#0a1020")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, WIDTH, 68), fill="#16233f")
        draw.rectangle((0, 68, 8, HEIGHT), fill="#36c2b4")
        draw.text((26, 16), "ANBERPOD", font=self.title_font, fill="#f6f8ff")
        draw.text((610, 22), "OFFLINE", font=self.small_font, fill="#8ca0bd", anchor="ra")
        draw.text((26, 79), screen.title, font=self.item_font, fill="#78e0d4")

        for index, (route, box) in enumerate(zip(HOME_ROUTES, HOME_CARD_BOXES)):
            left, top, right, bottom = box
            selected = index == screen.focus
            if selected:
                draw.rounded_rectangle(
                    (left - 3, top - 3, right + 3, bottom + 3),
                    radius=17,
                    outline="#7152a3",
                    width=3,
                )
            draw.rounded_rectangle(
                box,
                radius=14,
                fill="#302653" if selected else "#151f35",
                outline="#d8b8ff" if selected else "#34435e",
                width=4 if selected else 2,
            )

            center_x = (left + right) // 2
            icon_center_y = top + 58
            icon = _load_icon(HOME_ICON_FILES[route])
            if icon is None:
                self._draw_icon_fallback(draw, center_x, icon_center_y)
            else:
                image.paste(
                    icon,
                    (center_x - icon.width // 2, icon_center_y - icon.height // 2),
                    icon,
                )

            label = screen.items[index] if index < len(screen.items) else route.value.title()
            draw.text(
                (center_x, bottom - 26),
                label,
                font=self.item_font,
                fill="#ffffff" if selected else "#dce3ee",
                anchor="mm",
            )

        draw.rectangle((0, 444, WIDTH, HEIGHT), fill="#111b30")
        draw.text((320, 462), screen.footer, font=self.small_font, fill="#9eb0c9", anchor="mm")
        return image

    def _draw_icon_fallback(self, draw: ImageDraw.ImageDraw, center_x: int, center_y: int) -> None:
        draw.ellipse(
            (center_x - 34, center_y - 34, center_x + 34, center_y + 34),
            fill="#6f35d5",
            outline="#e2caff",
            width=4,
        )
        draw.line((center_x, center_y - 17, center_x, center_y + 7), fill="#ffffff", width=7)
        draw.ellipse(
            (center_x - 4, center_y + 15, center_x + 4, center_y + 23),
            fill="#ffffff",
        )

    def save(self, screen: ScreenModel, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.render(screen).save(path, format="PNG", optimize=False, compress_level=9)

    def render_player(self, player: PlayerViewModel) -> Image.Image:
        image = Image.new("RGB", (WIDTH, HEIGHT), "#0a1020")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, WIDTH, 68), fill="#16233f")
        draw.rectangle((0, 68, 8, HEIGHT), fill="#36c2b4")
        draw.text((26, 16), "ANBERPOD", font=self.title_font, fill="#f6f8ff")
        draw.text((610, 22), player.state.value.upper(), font=self.small_font, fill="#78e0d4", anchor="ra")
        draw.text((26, 88), "Now Playing", font=self.title_font, fill="#78e0d4")
        title = player.episode_title if len(player.episode_title) <= 38 else player.episode_title[:35] + "..."
        podcast = player.podcast_title if len(player.podcast_title) <= 52 else player.podcast_title[:49] + "..."
        draw.text((32, 145), title, font=self.item_font, fill="#ffffff")
        draw.text((32, 183), podcast, font=self.small_font, fill="#9eb0c9")
        screen = player.screen()
        draw.text((32, 231), screen.items[3], font=self.item_font, fill="#f6f8ff")
        draw.rounded_rectangle((32, 278, 608, 300), radius=11, fill="#26334b")
        progress_width = int(576 * player.progress)
        if progress_width:
            draw.rounded_rectangle((32, 278, 32 + progress_width, 300), radius=11, fill="#36c2b4")
        draw.text((32, 326), screen.items[4], font=self.small_font, fill="#c6d0df")
        if player.error_code:
            draw.rounded_rectangle((24, 374, 616, 422), radius=6, fill="#4a2525")
            draw.text((38, 389), f"Playback error: {player.error_code}", font=self.small_font, fill="#ffb0a8")
        draw.rectangle((0, 444, WIDTH, HEIGHT), fill="#111b30")
        draw.text((320, 462), screen.footer, font=self.small_font, fill="#9eb0c9", anchor="mm")
        return image

    def save_player(self, player: PlayerViewModel, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.render_player(player).save(path, format="PNG", optimize=False, compress_level=9)
