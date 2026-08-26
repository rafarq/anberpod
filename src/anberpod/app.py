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
from anberpod.adapters.podcast_index import LocalCatalogCredentials, PodcastIndexClient
from anberpod.adapters.sqlite import Repositories
from anberpod.config import DataPaths
from anberpod.domain.errors import CatalogError, HttpPolicyError, MissingCatalogCredentialsError
from anberpod.domain.models import (
    DiscoveryResult,
    DownloadState,
    Episode,
    InputAction,
    InputEvent,
    PlaybackState,
    Podcast,
)
from anberpod.domain.ports import ArtworkCachePort, Clock, ConnectivityProbe, MonotonicClock, PlaybackEngine
from anberpod.services.artwork import ArtworkCache
from anberpod.logging import configure_logging
from anberpod.services.startup import recover_local_state
from anberpod.services.discovery import CatalogCache, DiscoveryService
from anberpod.services.import_preview import ImportPreview, ImportStatus
from anberpod.services.playback import PlaybackController, PlaybackSourceSelector
from anberpod.ui.state import (
    AppState,
    DownloadItemViewModel,
    DownloadsViewModel,
    PlayerViewModel,
    Route,
    ScreenModel,
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
            catalog = PodcastIndexClient(
                PolicyHttpTransport(UrllibHttpAdapter()),
                LocalCatalogCredentials(paths.config / "config.toml"),
                clock,
            )
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
        return cls(paths, repositories, connectivity, AppState(), logger, discovery, playback, artwork_cache)

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
                self.pending_search_query = submitted
            return
        if self.state.route is Route.SEARCH_RESULTS and event.action is InputAction.ACCEPT and not event.repeated:
            if self._search_results and self._search_results.items:
                self._open_catalog_podcast(self._search_results.items[self.state.focus])
            return
        if self.state.route is Route.PODCAST and event.action is InputAction.ACCEPT and not event.repeated:
            podcast_id = self._selected_podcast_id or ""
            if self.state.focus == 1 and self.repositories.podcasts.get(podcast_id) is not None:
                if self.repositories.podcasts.is_subscribed(podcast_id):
                    self.repositories.podcasts.unsubscribe(podcast_id)
                else:
                    self.repositories.podcasts.subscribe(podcast_id, datetime.now(timezone.utc))
            elif self.state.focus >= 2:
                episodes = self.repositories.episodes.list_for_podcast(podcast_id)
                episode_index = self.state.focus - 2
                if episode_index < len(episodes):
                    self.play_episode(episodes[episode_index])
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

    def screen(self, route: Route | None = None) -> ScreenModel:
        selected = route or self.state.route
        status = None if self.connectivity.is_online() else "Offline - showing saved local data"
        if selected is Route.HOME:
            items = ("Explore", "Search", "Subscriptions", "Downloads", "Settings")
            title = "Home"
        elif selected is Route.SUBSCRIPTIONS:
            items = tuple(podcast.title for podcast in self.repositories.podcasts.list_subscribed())
            title = "Subscriptions"
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
                focus=self.state.focus if selected is self.state.route else 0,
                offline=not self.connectivity.is_online(),
            )
            return downloads_screen
        elif selected is Route.SETTINGS:
            items = ("Import RSS file", f"Data: {self.paths.root}", f"Version {__version__}")
            title = "Settings"
        elif selected is Route.PODCAST:
            podcast = self.repositories.podcasts.get(self._selected_podcast_id or "")
            if podcast is None:
                items = ()
                title = "Podcast"
            else:
                action = "Unsubscribe" if self.repositories.podcasts.is_subscribed(podcast.id) else "Subscribe"
                episodes = self.repositories.episodes.list_for_podcast(podcast.id)
                items = ("Update now", action, *(episode.title for episode in episodes))
                title = podcast.title
        elif selected is Route.RSS_IMPORT:
            items = tuple(
                f"{item.status.value}  {item.podcast.title if item.podcast else item.error_code or item.url}"
                for item in self._import_results
            )
            title = "RSS import"
        elif selected is Route.PLAYER:
            if self._player is None:
                return ScreenModel(Route.PLAYER, "Now Playing", (), footer="B Back   MENU Exit")
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
            ).screen()
        elif selected is Route.EXPLORE:
            items = self._categories.items if self._categories is not None else ()
            title = "Explore"
            if self._categories is not None and self._categories.cached:
                status = f"Saved categories - {self._categories.warning_code or 'cached'}"
            if self._catalog_error_code == MissingCatalogCredentialsError.code:
                status = "Configure Podcast Index in data/config/config.toml"
        elif selected is Route.SEARCH:
            items = (f"Query: {self.keyboard.query}_", *self.keyboard.display_rows())
            title = "Search"
        else:
            results = self._search_results.items if self._search_results is not None else ()
            items = tuple(f"{item.title}  -  {item.author or 'Unknown author'}" for item in results)
            title = f"Results: {self._search_query}"
            if self._search_results is not None and self._search_results.cached:
                status = f"Saved results - {self._search_results.warning_code or 'cached'}"
            if self._catalog_error_code == MissingCatalogCredentialsError.code:
                status = "Configure Podcast Index in data/config/config.toml"
        focus = self.state.focus if selected is self.state.route else 0
        footer = (
            "Results saved - A Subscribe/Open   B Back   MENU Exit"
            if selected is Route.RSS_IMPORT
            else "D-Pad Move   A Type/Search   B Delete/Back   MENU Exit"
            if selected is Route.SEARCH
            else "D-Pad Navigate   A Select   B Back   MENU Exit"
        )
        return ScreenModel(selected, title, items, focus=focus, status=status, footer=footer)


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)


class SystemMonotonicClock:
    def seconds(self) -> float:
        return time.monotonic()
