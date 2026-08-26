from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .state import HOME_ROUTES, PlayerViewModel, Route, ScreenModel


WIDTH = 640
HEIGHT = 480
PLAYER_ARTWORK_BOX = (32, 132, 200, 300)
PLAYER_LAYOUT = {
    "artwork": PLAYER_ARTWORK_BOX,
    "title": (224, 132, 608, 192),
    "podcast": (224, 200, 608, 224),
    "state": (224, 232, 608, 262),
    "time": (32, 312, 300, 344),
    "progress": (32, 350, 608, 371),
    "source": (32, 382, 608, 404),
    "error": (24, 406, 616, 439),
    "controls": (0, 444, 640, 480),
}
_RESAMPLING = getattr(Image, "Resampling", Image)
_LANCZOS = getattr(_RESAMPLING, "LANCZOS", 1)
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
    icon.thumbnail((86, 86), _LANCZOS)
    return icon


class Renderer:
    def __init__(self, *, artwork_root: Path | None = None) -> None:
        self.title_font = _font(30)
        self.item_font = _font(23)
        self.small_font = _font(16)
        self.artwork_root = artwork_root.expanduser().resolve() if artwork_root is not None else None

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
        draw.text((26, 84), "Now Playing", font=self.item_font, fill="#78e0d4")
        self._draw_player_artwork(image, draw, player.artwork_path)
        title_lines = self._wrap_lines(draw, player.episode_title, self.item_font, 382, 2)
        for index, line in enumerate(title_lines):
            draw.text((224, 134 + index * 29), line, font=self.item_font, fill="#ffffff")
        podcast = self._ellipsize(draw, player.podcast_title, self.small_font, 382)
        draw.text((224, 202), podcast, font=self.small_font, fill="#9eb0c9")
        state_text = player.state.value.upper()
        state_width = draw.textbbox((0, 0), state_text, font=self.small_font)[2]
        draw.rounded_rectangle((224, 232, 244 + state_width, 260), radius=7, fill="#263d4b", outline="#36c2b4")
        draw.text((234, 238), state_text, font=self.small_font, fill="#78e0d4")
        screen = player.screen()
        draw.text((32, 316), screen.items[3], font=self.item_font, fill="#f6f8ff")
        draw.rounded_rectangle((32, 350, 608, 370), radius=10, fill="#26334b")
        progress_width = int(576 * player.progress)
        if progress_width:
            draw.rounded_rectangle((32, 350, 32 + progress_width, 370), radius=10, fill="#36c2b4")
        draw.text((32, 384), screen.items[4], font=self.small_font, fill="#c6d0df")
        if player.error_code:
            error = self._ellipsize(draw, f"Playback error: {player.error_code}", self.small_font, 562)
            draw.rounded_rectangle((24, 406, 616, 438), radius=6, fill="#4a2525")
            draw.text((38, 413), error, font=self.small_font, fill="#ffb0a8")
        draw.rectangle((0, 444, WIDTH, HEIGHT), fill="#111b30")
        draw.text((320, 462), screen.footer, font=self.small_font, fill="#9eb0c9", anchor="mm")
        return image

    def _draw_player_artwork(
        self,
        destination: Image.Image,
        draw: ImageDraw.ImageDraw,
        artwork_path: Path | None,
    ) -> None:
        left, top, right, bottom = PLAYER_ARTWORK_BOX
        cover = self._load_artwork(artwork_path)
        if cover is None:
            draw.rounded_rectangle((left, top, right - 1, bottom - 1), radius=18, fill="#6f35d5", outline="#36c2b4", width=3)
            draw.ellipse((72, 161, 160, 249), outline="#d8b8ff", width=5)
            draw.rounded_rectangle((103, 174, 129, 222), radius=13, fill="#f6f8ff")
            draw.arc((91, 188, 141, 239), 0, 180, fill="#36c2b4", width=5)
            draw.line((116, 237, 116, 253), fill="#f6f8ff", width=5)
            draw.line((99, 253, 133, 253), fill="#f6f8ff", width=5)
            draw.text((116, 277), "ANBERPOD", font=self.small_font, fill="#ffffff", anchor="mm")
            return
        mask = Image.new("L", (right - left, bottom - top), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1), radius=18, fill=255)
        destination.paste(cover, (left, top), mask)

    def _load_artwork(self, path: Path | None) -> Image.Image | None:
        if path is None or self.artwork_root is None:
            return None
        try:
            candidate = path.expanduser().resolve(strict=True)
            if candidate == self.artwork_root or self.artwork_root not in candidate.parents:
                return None
            with Image.open(candidate) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"}:
                    return None
                width, height = source.size
                if width < 1 or height < 1 or width > 8192 or height > 8192 or width * height > 16_000_000:
                    return None
                source.load()
                converted = source.convert("RGB")
            return ImageOps.fit(converted, (168, 168), method=_LANCZOS)
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
            return None

    @staticmethod
    def _ellipsize(draw, text, font, max_width):  # type: ignore[no-untyped-def]
        clean = " ".join(text.split()) or "Untitled"
        if draw.textbbox((0, 0), clean, font=font)[2] <= max_width:
            return clean
        while clean and draw.textbbox((0, 0), clean + "...", font=font)[2] > max_width:
            clean = clean[:-1]
        return clean.rstrip() + "..."

    @classmethod
    def _wrap_lines(cls, draw, text, font, max_width, max_lines):  # type: ignore[no-untyped-def]
        words = (" ".join(text.split()) or "Untitled").split(" ")
        lines: list[str] = []
        current = ""
        for word_index, word in enumerate(words):
            candidate = f"{current} {word}".strip()
            if not current or draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
                continue
            lines.append(current)
            current = word
            if len(lines) == max_lines - 1:
                remainder = " ".join((current, *words[word_index + 1:]))
                lines.append(cls._ellipsize(draw, remainder, font, max_width))
                return tuple(lines)
        if current:
            lines.append(cls._ellipsize(draw, current, font, max_width))
        return tuple(lines[:max_lines])

    def save_player(self, player: PlayerViewModel, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.render_player(player).save(path, format="PNG", optimize=False, compress_level=9)
