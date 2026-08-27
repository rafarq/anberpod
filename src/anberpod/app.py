from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from anberpod import __version__
from anberpod.adapters.filesystem import AtomicFiles
from anberpod.adapters.ffmpeg_aplay import FfmpegAplayConfig, FfmpegAplayEngine
from anberpod.adapters.http import PolicyHttpTransport, UrllibHttpAdapter
from anberpod.adapters.itunes import ITunesCatalogClient
from anberpod.adapters.podcast_index import LocalCatalogCredentials, PodcastIndexClient
from anberpod.adapters.sqlite import Repositories
from anberpod.config import DataPaths
from anberpod.domain.errors import AnberPodError, CatalogError, HttpPolicyError, MissingCatalogCredentialsError
from anberpod.domain.models import (
    DiscoveryResult,
    DownloadState,
    Episode,
    InputAction,
    InputEvent,
    PlaybackState,
    Podcast,
)
from anberpod.domain.ports import ArtworkCachePort, Clock, ConnectivityProbe, MonotonicClock, PlaybackEngine, PodcastCatalog
from anberpod.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, normalize_language, resolve_system_language, t as translate
from anberpod.services.artwork import ArtworkCache
from anberpod.adapters.rss import DirectFeedReader
from anberpod.logging import configure_logging
from anberpod.services.startup import recover_local_state
from anberpod.services.discovery import CatalogCache, DiscoveryService
from anberpod.services.feeds import FeedService, UpdateStatus
from anberpod.services.import_preview import ImportPreview, ImportStatus
from anberpod.services.playback import PlaybackController, PlaybackSourceSelector
from anberpod.ui.state import (
    LANGUAGE_CODES,
    AppState,
    DownloadItemViewModel,
    DownloadsViewModel,
    PlayerViewModel,
    Route,
    ScreenModel,
    SettingsView,
    VirtualKeyboard,
)


@dataclass
class Application:
    paths: DataPaths
    repositories: Repositories
    connectivity: ConnectivityProbe
    state: AppState
    logger: object
    discovery: DiscoveryService
    playback: PlaybackController
    artwork_cache: ArtworkCachePort
    feeds: FeedService
    language: str = DEFAULT_LANGUAGE
    _shutdown_logged: bool = False
    _selected_podcast_id: str | None = None
    _import_results: tuple[ImportPreview, ...] = ()
    _categories: DiscoveryResult[str] | None = None
    _search_results: DiscoveryResult[Podcast] | None = None
    _search_query: str = ""
    keyboard: VirtualKeyboard = field(default_factory=VirtualKeyboard)
    pending_search_query: str | None = None
    _catalog_error_code: str | None = None
    _player: PlayerViewModel | None = None

    def t(self, key: str, **kwargs: object) -> str:
        """Translate ``key`` for the app's current active language."""
        return translate(key, self.language, **kwargs)

    def set_language(self, language: str) -> None:
        """Set the active UI language and persist it (invalid codes fall back to English)."""
        self.language = normalize_language(language) or DEFAULT_LANGUAGE
        self.repositories.settings.set("language", self.language)

    def save_selected_language(self) -> None:
        """Persist the language currently highlighted in the Settings language picker.

        Called on ``A`` while on :attr:`Route.SETTINGS` in
        :attr:`SettingsView.LANGUAGE`; every screen reads ``app.language``/
        ``app.t`` fresh each frame, so the very next redraw reflects the new
        language with no extra signalling needed.
        """
        language = LANGUAGE_CODES[self.state.focus % len(LANGUAGE_CODES)]
        self.set_language(language)

    @classmethod
    def open(
        cls,
        paths: DataPaths,
        connectivity: ConnectivityProbe,
        discovery: DiscoveryService | None = None,
        *,
        playback_engine: PlaybackEngine | None = None,
        playback_monotonic: MonotonicClock | None = None,
        playback_clock: Clock | None = None,
        artwork_cache: ArtworkCachePort | None = None,
    ) -> "Application":
        logger = configure_logging(paths.logs / "anberpod.log")
        repositories = Repositories.open(paths.database)
        report = recover_local_state(paths, repositories)
        logger.info("AnberPod started", extra={
            "event": "startup",
            "offline": not connectivity.is_online(),
            "interrupted_downloads": report.interrupted_downloads,
            "discarded_cache_entries": report.discarded_cache_entries,
        })
        if discovery is None:
            clock = SystemClock()
            transport = PolicyHttpTransport(UrllibHttpAdapter())
            # Podcast Index gives richer metadata/categories, but requires a
            # free API key/secret the user must register for themselves.
            # The iTunes Search API needs no registration at all, so it is
            # the zero-configuration default: a fresh install can discover
            # podcasts immediately. Podcast Index is used instead only once
            # the user has actually configured credentials.
            index_credentials = LocalCatalogCredentials(paths.config / "config.toml")
            if index_credentials.podcast_index() is not None:
                catalog: PodcastCatalog = PodcastIndexClient(transport, index_credentials, clock)
            else:
                catalog = ITunesCatalogClient(transport)
            discovery = DiscoveryService(
                catalog,
                CatalogCache(paths, repositories.database.connection, AtomicFiles(paths.root)),
                clock,
                logger,
            )
        playback_clock = playback_clock or SystemClock()
        playback_monotonic = playback_monotonic or SystemMonotonicClock()
        if playback_engine is None:
            decoder_path = Path(os.environ.get(
                "ANBERPOD_FFMPEG",
                str(paths.root.parent / "runtime" / "bin" / "ffmpeg"),
            )).expanduser().resolve()
            playback_engine = FfmpegAplayEngine(FfmpegAplayConfig(
                decoder_path=decoder_path,
                aplay_path=os.environ.get("ANBERPOD_APLAY", "aplay"),
            ))
        playback = PlaybackController(
            repositories.playback,
            PlaybackSourceSelector(repositories.downloads, AtomicFiles(paths.root)),
            playback_engine,
            playback_monotonic,
            playback_clock,
        )
        artwork_cache = artwork_cache or ArtworkCache(
            AtomicFiles(paths.root),
            PolicyHttpTransport(UrllibHttpAdapter()),
        )
        feeds = FeedService(
            DirectFeedReader(PolicyHttpTransport(UrllibHttpAdapter())),
            repositories,
            SystemClock(),
        )
        # A valid persisted language always wins over system detection, per
        # the language-resolution rule mirrored from radio.app.state.
        language = normalize_language(repositories.settings.get("language")) or resolve_system_language()
        return cls(
            paths, repositories, connectivity, AppState(), logger, discovery, playback, artwork_cache, feeds,
            language=language,
        )

    def handle(self, event: InputEvent) -> None:
        if self.state.route is Route.PLAYER:
            if event.action is InputAction.MENU and not event.repeated:
                self.playback.shutdown()
            elif event.action is InputAction.BACK and not event.repeated:
                self.playback.stop()
                self.state.handle(event)
                return
            elif event.action is InputAction.ACCEPT and not event.repeated:
                if self.playback.state is PlaybackState.PLAYING:
                    self.playback.pause()
                elif self.playback.state is PlaybackState.PAUSED:
                    self.playback.resume()
                return
            elif event.action is InputAction.LEFT:
                self.playback.seek_backward()
                return
            elif event.action is InputAction.RIGHT:
                self.playback.seek_forward()
                return
        if self.state.route is Route.SEARCH and event.action is not InputAction.MENU:
            if event.action is InputAction.BACK and not self.keyboard.query:
                self.state.handle(event)
                return
            self.keyboard.handle(event)
            submitted = self.keyboard.take_submission()
            if submitted is not None:
                self.search_catalog(submitted)
            return
        if self.state.route is Route.EXPLORE and event.action is InputAction.ACCEPT and not event.repeated:
            if self._categories is not None and self._categories.items:
                category = self._categories.items[self.state.focus]
                self.search_catalog(category)
            return
        if self.state.route is Route.SUBSCRIPTIONS and event.action is InputAction.ACCEPT and not event.repeated:
            subscribed = self.repositories.podcasts.list_subscribed()
            if subscribed and self.state.focus < len(subscribed):
                self.show_podcast(subscribed[self.state.focus].id)
            return
        if self.state.route is Route.SEARCH_RESULTS and event.action is InputAction.ACCEPT and not event.repeated:
            if self._search_results and self._search_results.items:
                self._open_catalog_podcast(self._search_results.items[self.state.focus])
            return
        if self.state.route is Route.PODCAST and event.action is InputAction.ACCEPT and not event.repeated:
            podcast_id = self._selected_podcast_id or ""
            if self.state.focus == 0 and self.repositories.podcasts.get(podcast_id) is not None:
                self.update_podcast(podcast_id)
            elif self.state.focus == 1 and self.repositories.podcasts.get(podcast_id) is not None:
                if self.repositories.podcasts.is_subscribed(podcast_id):
                    self.repositories.podcasts.unsubscribe(podcast_id)
                else:
                    self.repositories.podcasts.subscribe(podcast_id, datetime.now(timezone.utc))
                    # A catalog subscription only carries Podcast Index
                    # metadata, not episodes; fetch the real RSS feed right
                    # away so episodes are immediately available to play or
                    # download instead of requiring a separate manual
                    # "Update now" the user has no reason to expect.
                    self.update_podcast(podcast_id)
            elif self.state.focus >= 2:
                episodes = self.repositories.episodes.list_for_podcast(podcast_id)
                episode_index = self.state.focus - 2
                if episode_index < len(episodes):
                    self.play_episode(episodes[episode_index])
            return
        if self.state.route is Route.SETTINGS and event.action is InputAction.ACCEPT and not event.repeated:
            if self.state.settings_view is SettingsView.MENU and self.state.focus == 1:
                self.state.settings_view = SettingsView.LANGUAGE
                self.state.focus = LANGUAGE_CODES.index(self.language) if self.language in LANGUAGE_CODES else 0
                self.state.set_item_count(len(LANGUAGE_CODES))
                return
            if self.state.settings_view is SettingsView.LANGUAGE:
                self.save_selected_language()
                self.state.settings_view = SettingsView.MENU
                self.state.focus = 1
                self.state.set_item_count(4)
                return
        if self.state.route is Route.HOME and event.action is InputAction.ACCEPT and not event.repeated:
            from anberpod.ui.state import HOME_ROUTES

            target = HOME_ROUTES[self.state.focus] if self.state.focus < len(HOME_ROUTES) else None
            self.state.handle(event)
            if target is Route.EXPLORE:
                self.refresh_categories()
            return
        self.state.set_item_count(len(self.screen().items))
        self.state.handle(event)
        if self.state.exit_requested and not self._shutdown_logged:
            self.logger.info("AnberPod stopped", extra={"event": "shutdown"})  # type: ignore[attr-defined]
            self._shutdown_logged = True

    def show_podcast(self, podcast_id: str) -> None:
        if self.repositories.podcasts.get(podcast_id) is None:
            raise KeyError(podcast_id)
        self._selected_podcast_id = podcast_id
        self.state.show(Route.PODCAST)

    def show_import_results(self, results: list[ImportPreview]) -> None:
        self._import_results = tuple(results)
        self.state.show(Route.RSS_IMPORT, len(results))

    def show_player(self, player: PlayerViewModel) -> None:
        self._player = player
        self.state.show(Route.PLAYER)

    def play_episode(self, episode: Episode, *, restart_completed: bool = False) -> None:
        self.playback.play(episode, restart_completed=restart_completed)
        podcast = self.repositories.podcasts.get(episode.podcast_id)
        artwork_path = self.artwork_cache.ensure_cached(
            podcast.image_url if podcast else None,
            online=self.connectivity.is_online(),
        )
        self._player = PlayerViewModel(
            episode.id,
            episode.title,
            podcast.title if podcast else "Unknown podcast",
            self.playback.state,
            self.playback.position_ms,
            self.playback.duration_ms,
            bool(self.playback.source and self.playback.source.local),
            artwork_path=artwork_path,
        )
        self.state.show(Route.PLAYER)

    def poll_playback(self) -> tuple[object, ...]:
        return self.playback.poll()

    def player_view(self) -> PlayerViewModel | None:
        """Current PlayerViewModel with live playback state, or ``None``."""
        if self._player is None:
            return None
        return PlayerViewModel(
            self._player.episode_id,
            self._player.episode_title,
            self._player.podcast_title,
            self.playback.state,
            self.playback.position_ms,
            self.playback.duration_ms,
            bool(self.playback.source and self.playback.source.local),
            self.playback.failure.code if self.playback.failure else None,
            self._player.artwork_path,
        )

    def show_categories(self, result: DiscoveryResult[str]) -> None:
        self._categories = result
        self._catalog_error_code = None
        self.state.show(Route.EXPLORE, len(result.items))

    def show_search_results(self, query: str, result: DiscoveryResult[Podcast]) -> None:
        self._search_query = query
        self._search_results = result
        self._catalog_error_code = None
        self.pending_search_query = None
        self.state.show(Route.SEARCH_RESULTS, len(result.items))

    def refresh_categories(self) -> None:
        try:
            self.show_categories(self.discovery.categories())
        except (CatalogError, HttpPolicyError, OSError, TimeoutError) as exc:
            self._catalog_error_code = getattr(exc, "code", "offline")
            self.state.show(Route.EXPLORE)

    def search_catalog(self, query: str, limit: int = 20) -> None:
        try:
            self.show_search_results(query, self.discovery.search(query, limit))
        except (CatalogError, HttpPolicyError, OSError, TimeoutError) as exc:
            self._catalog_error_code = getattr(exc, "code", "offline")
            self._search_query = query
            self.state.show(Route.SEARCH_RESULTS)

    def _open_catalog_podcast(self, podcast: Podcast) -> None:
        self.repositories.podcasts.save(podcast)
        self.show_podcast(podcast.id)

    def update_podcast(self, podcast_id: str) -> UpdateStatus | None:
        """Fetch the podcast's real RSS feed so its episode list is current.

        Returns ``None`` (rather than raising) on any network/parse failure
        so callers on the input-handling path never crash the UI; the
        existing local episode list, if any, is left untouched.
        """
        try:
            return self.feeds.update(podcast_id)
        except (AnberPodError, ValueError, OSError, TimeoutError):
            return None

    def screen(self, route: Route | None = None) -> ScreenModel:
        selected = route or self.state.route
        status = None if self.connectivity.is_online() else self.t("status_offline_library")
        if selected is Route.HOME:
            items = (
                self.t("home_item_explore"),
                self.t("home_item_search"),
                self.t("home_item_subscriptions"),
                self.t("home_item_downloads"),
                self.t("home_item_settings"),
            )
            title = self.t("home_title")
        elif selected is Route.SUBSCRIPTIONS:
            items = tuple(podcast.title for podcast in self.repositories.podcasts.list_subscribed())
            title = self.t("subscriptions_title")
        elif selected is Route.DOWNLOADS:
            rows = self.repositories.database.connection.execute(
                """SELECT episode.id, episode.title, download.state, download.bytes_received,
                download.bytes_total, download.error_code FROM download
                JOIN episode ON episode.id=download.episode_id ORDER BY download.created_at, episode.id"""
            ).fetchall()
            downloads_screen = DownloadsViewModel(tuple(
                DownloadItemViewModel(row[0], row[1], DownloadState(row[2]), row[3], row[4], row[5])
                for row in rows
            )).screen(
                self.t,
                focus=self.state.focus if selected is self.state.route else 0,
                offline=not self.connectivity.is_online(),
            )
            return downloads_screen
        elif selected is Route.SETTINGS:
            if self.state.settings_view is SettingsView.LANGUAGE and selected is self.state.route:
                items = tuple(SUPPORTED_LANGUAGES[code] for code in LANGUAGE_CODES)
                title = self.t("settings_language_title")
                focus = self.state.focus
                return ScreenModel(
                    selected, title, items, focus=focus, status=None, footer=self.t("footer_language_picker"),
                )
            items = (
                self.t("settings_menu_import_rss"),
                self.t("settings_menu_language"),
                self.t("settings_menu_data", path=self.paths.root),
                self.t("settings_menu_version", version=__version__),
            )
            title = self.t("settings_title")
        elif selected is Route.PODCAST:
            podcast = self.repositories.podcasts.get(self._selected_podcast_id or "")
            if podcast is None:
                items = ()
                title = self.t("podcast_title_fallback")
            else:
                action = (
                    self.t("podcast_action_unsubscribe")
                    if self.repositories.podcasts.is_subscribed(podcast.id)
                    else self.t("podcast_action_subscribe")
                )
                episodes = self.repositories.episodes.list_for_podcast(podcast.id)
                items = (self.t("podcast_update_now"), action, *(episode.title for episode in episodes))
                title = podcast.title
        elif selected is Route.RSS_IMPORT:
            items = tuple(
                f"{item.status.value}  {item.podcast.title if item.podcast else item.error_code or item.url}"
                for item in self._import_results
            )
            title = self.t("rss_import_title")
        elif selected is Route.PLAYER:
            if self._player is None:
                return ScreenModel(Route.PLAYER, self.t("player_title"), (), footer=self.t("footer_player_fallback"))
            return PlayerViewModel(
                self._player.episode_id,
                self._player.episode_title,
                self._player.podcast_title,
                self.playback.state,
                self.playback.position_ms,
                self.playback.duration_ms,
                bool(self.playback.source and self.playback.source.local),
                self.playback.failure.code if self.playback.failure else None,
                self._player.artwork_path,
            ).screen(self.t)
        elif selected is Route.EXPLORE:
            items = self._categories.items if self._categories is not None else ()
            title = self.t("explore_title")
            if self._categories is not None and self._categories.cached:
                status = self.t("explore_status_cached", warning=self._categories.warning_code or "cached")
            if self._catalog_error_code == MissingCatalogCredentialsError.code:
                status = self.t("explore_status_no_credentials")
        elif selected is Route.SEARCH:
            items = (self.t("search_query_prefix", query=self.keyboard.query), *self.keyboard.display_rows())
            title = self.t("search_title")
        else:
            results = self._search_results.items if self._search_results is not None else ()
            unknown_author = self.t("search_results_unknown_author")
            items = tuple(f"{item.title}  -  {item.author or unknown_author}" for item in results)
            title = self.t("search_results_title_prefix", query=self._search_query)
            if self._search_results is not None and self._search_results.cached:
                status = self.t("search_results_status_cached", warning=self._search_results.warning_code or "cached")
            if self._catalog_error_code == MissingCatalogCredentialsError.code:
                status = self.t("explore_status_no_credentials")
        focus = self.state.focus if selected is self.state.route else 0
        footer = (
            self.t("footer_rss_import")
            if selected is Route.RSS_IMPORT
            else self.t("footer_search")
            if selected is Route.SEARCH
            else self.t("footer_default")
        )
        return ScreenModel(selected, title, items, focus=focus, status=status, footer=footer)


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)


class SystemMonotonicClock:
    def seconds(self) -> float:
        return time.monotonic()
