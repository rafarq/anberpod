from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from anberpod.domain.models import DownloadState, InputAction, InputEvent, PlaybackState


class Route(str, Enum):
    HOME = "home"
    EXPLORE = "explore"
    SEARCH = "search"
    SEARCH_RESULTS = "search_results"
    SUBSCRIPTIONS = "subscriptions"
    DOWNLOADS = "downloads"
    SETTINGS = "settings"
    RSS_IMPORT = "rss_import"
    PODCAST = "podcast"
    PLAYER = "player"


HOME_ROUTES = (Route.EXPLORE, Route.SEARCH, Route.SUBSCRIPTIONS, Route.DOWNLOADS, Route.SETTINGS)


@dataclass(frozen=True)
class ScreenModel:
    route: Route
    title: str
    items: tuple[str, ...]
    focus: int = 0
    status: str | None = None
    footer: str = "D-Pad Navigate   A Select   B Back   MENU Exit"


def _display_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


@dataclass(frozen=True)
class DownloadItemViewModel:
    episode_id: str
    title: str
    state: DownloadState
    bytes_received: int
    bytes_total: int | None
    error_code: str | None = None

    def display(self) -> str:
        size = _display_bytes(self.bytes_received)
        if self.bytes_total is not None:
            if self.bytes_total >= 1024 * 1024:
                unit = 1024 * 1024
                size = f"{self.bytes_received / unit:.1f} / {self.bytes_total / unit:.1f} MiB"
            elif self.bytes_total >= 1024:
                unit = 1024
                size = f"{self.bytes_received / unit:.1f} / {self.bytes_total / unit:.1f} KiB"
            else:
                size = f"{self.bytes_received} / {self.bytes_total} B"
        error = f"  [{self.error_code}]" if self.error_code else ""
        return f"{self.title}  -  {self.state.value.title()}  {size}{error}"


@dataclass(frozen=True)
class DownloadsViewModel:
    items: tuple[DownloadItemViewModel, ...]

    def screen(self, *, focus: int = 0, offline: bool = False) -> ScreenModel:
        status = "Offline - complete downloads remain playable" if offline else None
        return ScreenModel(
            Route.DOWNLOADS,
            "Downloads",
            tuple(item.display() for item in self.items),
            focus=focus,
            status=status,
            footer="D-Pad Navigate   A Open/Actions   B Back   MENU Exit",
        )


def _display_time(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "--:--"
    seconds = max(0, milliseconds // 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class PlayerViewModel:
    episode_id: str
    episode_title: str
    podcast_title: str
    state: PlaybackState
    position_ms: int
    duration_ms: int | None
    local: bool
    error_code: str | None = None

    @property
    def progress(self) -> float:
        if not self.duration_ms:
            return 0.0
        return min(1.0, max(0.0, self.position_ms / self.duration_ms))

    def screen(self) -> ScreenModel:
        items = (
            self.episode_title,
            self.podcast_title,
            self.state.value.title(),
            f"{_display_time(self.position_ms)} / {_display_time(self.duration_ms)}",
            f"Source: {'Downloaded' if self.local else 'Streaming'}",
            *((f"Error: {self.error_code}",) if self.error_code else ()),
        )
        return ScreenModel(
            Route.PLAYER,
            "Now Playing",
            items,
            footer="LEFT -15s   A Play/Pause   RIGHT +15s   B Stop",
        )


class VirtualKeyboard:
    ROWS = (
        ("a", "b", "c", "d", "e", "f"),
        ("g", "h", "i", "j", "k", "l"),
        ("m", "n", "o", "p", "q", "r"),
        ("s", "t", "u", "v", "w", "x"),
        ("y", "z", "0", "1", "2", "3"),
        ("4", "5", "6", "7", "8", "9"),
        ("space", "delete", "search"),
    )

    def __init__(self) -> None:
        self.query = ""
        self.row = 0
        self.column = 0
        self._submission: str | None = None

    def handle(self, event: InputEvent) -> None:
        if event.repeated and event.action in {InputAction.ACCEPT, InputAction.BACK}:
            return
        if event.action is InputAction.LEFT:
            self.column = max(0, self.column - 1)
        elif event.action is InputAction.RIGHT:
            self.column = min(len(self.ROWS[self.row]) - 1, self.column + 1)
        elif event.action is InputAction.UP:
            self.row = max(0, self.row - 1)
            self.column = min(self.column, len(self.ROWS[self.row]) - 1)
        elif event.action is InputAction.DOWN:
            self.row = min(len(self.ROWS) - 1, self.row + 1)
            self.column = min(self.column, len(self.ROWS[self.row]) - 1)
        elif event.action is InputAction.BACK:
            self.query = self.query[:-1]
        elif event.action is InputAction.ACCEPT:
            key = self.ROWS[self.row][self.column]
            if key == "space" and self.query and len(self.query) < 200:
                self.query += " "
            elif key == "delete":
                self.query = self.query[:-1]
            elif key == "search":
                clean = " ".join(self.query.split())
                if clean:
                    self._submission = clean
            elif len(self.query) < 200:
                self.query += key

    def take_submission(self) -> str | None:
        value = self._submission
        self._submission = None
        return value

    def display_rows(self) -> tuple[str, ...]:
        rendered = []
        for row_index, row in enumerate(self.ROWS):
            rendered.append("  ".join(
                f"[{key.upper()}]" if (row_index, column) == (self.row, self.column) else key.upper()
                for column, key in enumerate(row)
            ))
        return tuple(rendered)


class AppState:
    def __init__(self) -> None:
        self.route = Route.HOME
        self.focus = 0
        self.exit_requested = False
        self._home_focus = 0
        self._item_count = len(HOME_ROUTES)

    def set_item_count(self, count: int) -> None:
        self._item_count = max(0, count)
        self.focus = min(self.focus, max(0, self._item_count - 1))

    def handle(self, event: InputEvent) -> None:
        if event.repeated and event.action in {InputAction.ACCEPT, InputAction.BACK, InputAction.MENU}:
            return
        if event.action is InputAction.MENU:
            self.exit_requested = True
            return
        if event.action is InputAction.BACK:
            if self.route is not Route.HOME:
                self.route = Route.HOME
                self.focus = self._home_focus
                self._item_count = len(HOME_ROUTES)
            return
        if self.route is Route.HOME:
            if event.action is InputAction.LEFT:
                self.focus = {1: 0, 2: 1, 4: 3}.get(self.focus, self.focus)
            elif event.action is InputAction.RIGHT:
                self.focus = {0: 1, 1: 2, 3: 4}.get(self.focus, self.focus)
            elif event.action is InputAction.DOWN:
                self.focus = {0: 3, 1: 3, 2: 4}.get(self.focus, self.focus)
            elif event.action is InputAction.UP:
                self.focus = {3: 0, 4: 2}.get(self.focus, self.focus)
            elif event.action is InputAction.ACCEPT:
                self._home_focus = self.focus
                self.route = HOME_ROUTES[self.focus]
                self.focus = 0
                self._item_count = 0
        else:
            if event.action is InputAction.DOWN and self._item_count:
                self.focus = min(self.focus + 1, self._item_count - 1)
            elif event.action is InputAction.UP:
                self.focus = max(self.focus - 1, 0)

    def show(self, route: Route, item_count: int = 0) -> None:
        self.route = route
        self.focus = 0
        self._item_count = max(0, item_count)
