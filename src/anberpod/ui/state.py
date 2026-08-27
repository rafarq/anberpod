from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from anberpod.domain.models import DownloadState, InputAction, InputEvent, PlaybackState
from anberpod.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from anberpod.i18n import t as _translate

Translator = Callable[..., str]


def _default_translator(key: str, **kwargs: object) -> str:
    """English fallback translator used when a caller doesn't pass one.

    Keeps ``DownloadsViewModel.screen()``/``PlayerViewModel.screen()``
    callable without an ``Application`` (as the existing test suite and
    ``--render-dir``/``--diagnostic`` CLI helpers already do).
    """
    return _translate(key, DEFAULT_LANGUAGE, **kwargs)


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

# Stable display order for the settings language picker: the 15 supported
# locale codes, in the same order as anberpod.i18n.SUPPORTED_LANGUAGES
# (mirrors radio.app.state.LANGUAGE_CODES).
LANGUAGE_CODES = list(SUPPORTED_LANGUAGES)


class SettingsView(str, Enum):
    """Sub-view within :attr:`Route.SETTINGS`.

    Settings is a single ``Route`` with its own tiny nested navigation
    (menu -> language picker -> back to menu -> back to Home) so the
    top-level back stack only ever sees one ``Route.SETTINGS`` entry.
    """

    MENU = "menu"
    LANGUAGE = "language"


class PodcastView(str, Enum):
    """Sub-view within :attr:`Route.PODCAST`.

    Episodes list -> Episode actions (Play / Download / Delete) -> back to Episodes list.
    """

    EPISODES = "episodes"
    EPISODE_ACTIONS = "episode_actions"


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


_DOWNLOAD_STATE_KEYS = {
    DownloadState.QUEUED: "download_state_queued",
    DownloadState.DOWNLOADING: "download_state_downloading",
    DownloadState.COMPLETE: "download_state_complete",
    DownloadState.FAILED: "download_state_failed",
}

_PLAYBACK_STATE_KEYS = {
    PlaybackState.IDLE: "playback_state_idle",
    PlaybackState.BUFFERING: "playback_state_buffering",
    PlaybackState.PLAYING: "playback_state_playing",
    PlaybackState.PAUSED: "playback_state_paused",
    PlaybackState.STOPPED: "playback_state_stopped",
    PlaybackState.ENDED: "playback_state_ended",
    PlaybackState.ERROR: "playback_state_error",
}


@dataclass(frozen=True)
class DownloadItemViewModel:
    episode_id: str
    title: str
    state: DownloadState
    bytes_received: int
    bytes_total: int | None
    error_code: str | None = None

    def display(self, t: Translator = _default_translator) -> str:
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
        state_label = t(_DOWNLOAD_STATE_KEYS[self.state])
        return f"{self.title}  -  {state_label}  {size}{error}"


@dataclass(frozen=True)
class DownloadsViewModel:
    items: tuple[DownloadItemViewModel, ...]

    def screen(self, t: Translator = _default_translator, *, focus: int = 0, offline: bool = False) -> ScreenModel:
        status = t("downloads_status_offline") if offline else None
        return ScreenModel(
            Route.DOWNLOADS,
            t("downloads_title"),
            tuple(item.display(t) for item in self.items),
            focus=focus,
            status=status,
            footer=t("footer_downloads"),
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
    artwork_path: Path | None = None

    @property
    def progress(self) -> float:
        if not self.duration_ms:
            return 0.0
        return min(1.0, max(0.0, self.position_ms / self.duration_ms))

    def screen(self, t: Translator = _default_translator) -> ScreenModel:
        source_label = t("player_source_downloaded") if self.local else t("player_source_streaming")
        items = (
            self.episode_title,
            self.podcast_title,
            t(_PLAYBACK_STATE_KEYS[self.state]),
            f"{_display_time(self.position_ms)} / {_display_time(self.duration_ms)}",
            t("player_source_label", source=source_label),
            *((t("player_error_label", code=self.error_code),) if self.error_code else ()),
        )
        return ScreenModel(
            Route.PLAYER,
            t("player_title"),
            items,
            footer=t("footer_player"),
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
        self.settings_view: SettingsView = SettingsView.MENU
        self.podcast_view: PodcastView = PodcastView.EPISODES
        self._podcast_focus = 0

    def set_item_count(self, count: int) -> None:
        self._item_count = max(0, count)
        self.focus = min(self.focus, max(0, self._item_count - 1))

    def handle(self, event: InputEvent) -> None:
        if event.repeated and event.action in {InputAction.ACCEPT, InputAction.BACK, InputAction.MENU, InputAction.DELETE}:
            return
        if event.action is InputAction.MENU:
            self.exit_requested = True
            return
        if event.action is InputAction.BACK:
            if self.route is Route.SETTINGS and self.settings_view is not SettingsView.MENU:
                # Language picker back to the Settings menu, without popping
                # the outer back stack (Settings is still one screen).
                self.settings_view = SettingsView.MENU
                self.focus = 1
                self._item_count = 4
                return
            if self.route is Route.PODCAST and self.podcast_view is not PodcastView.EPISODES:
                self.podcast_view = PodcastView.EPISODES
                self.focus = self._podcast_focus
                return
            if self.route is not Route.HOME:
                self.route = Route.HOME
                self.focus = self._home_focus
                self._item_count = len(HOME_ROUTES)
                self.settings_view = SettingsView.MENU
                self.podcast_view = PodcastView.EPISODES
            return
        if self.route is Route.SETTINGS and self.settings_view is SettingsView.LANGUAGE:
            if event.action is InputAction.DOWN:
                self.focus = min(self.focus + 1, len(LANGUAGE_CODES) - 1)
            elif event.action is InputAction.UP:
                self.focus = max(self.focus - 1, 0)
            elif event.action is InputAction.LEFT:
                self.focus = max(self.focus - 1, 0)
            elif event.action is InputAction.RIGHT:
                self.focus = min(self.focus + 1, len(LANGUAGE_CODES) - 1)
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
                if self.route is Route.SETTINGS:
                    self.settings_view = SettingsView.MENU
                if self.route is Route.PODCAST:
                    self.podcast_view = PodcastView.EPISODES
        else:
            if event.action is InputAction.DOWN and self._item_count:
                self.focus = min(self.focus + 1, self._item_count - 1)
            elif event.action is InputAction.UP:
                self.focus = max(self.focus - 1, 0)

    def show(self, route: Route, item_count: int = 0) -> None:
        self.route = route
        self.focus = 0
        self._item_count = max(0, item_count)
        if route is Route.SETTINGS:
            self.settings_view = SettingsView.MENU
        if route is Route.PODCAST:
            self.podcast_view = PodcastView.EPISODES
