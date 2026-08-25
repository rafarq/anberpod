from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .state import PlayerViewModel, ScreenModel


WIDTH = 640
HEIGHT = 480


def _font(size: int):  # type: ignore[no-untyped-def]
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


class Renderer:
    def __init__(self) -> None:
        self.title_font = _font(30)
        self.item_font = _font(23)
        self.small_font = _font(16)

    def render(self, screen: ScreenModel) -> Image.Image:
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
