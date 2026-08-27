from __future__ import annotations

import argparse
from pathlib import Path

from anberpod.adapters.connectivity import SystemConnectivityProbe
from anberpod.app import Application
from anberpod.config import DataPaths
from anberpod.domain.models import DiscoveryResult, InputAction, InputEvent, PlaybackState, Podcast
from anberpod.fixtures import DEMO_COVER_URL, seed_demo_library
from anberpod.services.import_preview import ImportPreview, ImportStatus
from anberpod.ui.renderer import Renderer
from anberpod.ui.state import PlayerViewModel, Route


class OfflineProbe:
    def is_online(self) -> bool:
        return False


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AnberPod local/offline foundation")
    parser.add_argument("--data-dir", type=Path, help="Absolute persistent data directory")
    parser.add_argument("--render-dir", type=Path, help="Write deterministic review PNGs and exit")
    parser.add_argument("--demo", action="store_true", help="Seed the synthetic offline review library")
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="Write one diagnostic PNG and exit instead of entering the SDL2 UI loop",
    )
    parser.add_argument(
        "--input-device",
        default="/dev/input/event1",
        help="Physical control device node (D-pad/buttons)",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    paths = DataPaths.create(args.data_dir) if args.data_dir else DataPaths.from_environment()
    # --render-dir/--demo produce deterministic offline review screenshots;
    # everything else (the real device UI loop, --diagnostic) uses a real,
    # non-blocking background connectivity probe instead of always reporting
    # offline.
    connectivity = OfflineProbe() if args.render_dir else SystemConnectivityProbe()
    app = Application.open(paths, connectivity)
    if args.demo:
        seed_demo_library(app.repositories, paths)
    renderer = Renderer(artwork_root=paths.cache / "artwork")
    if args.render_dir:
        routes = (Route.HOME, Route.SUBSCRIPTIONS, Route.DOWNLOADS, Route.SETTINGS)
        for route in routes:
            renderer.save(app.screen(route), args.render_dir / f"{route.value}.png", app.t)
        demo_result = Podcast(
            "catalog:9001", "https://feeds.example.test/discovery.xml", "Discovery Weekly",
            author="AnberPod Fixture", catalog_id=9001,
        )
        app.show_categories(DiscoveryResult(("Arts", "Education", "Science", "Technology"), cached=True))
        renderer.save(app.screen(), args.render_dir / "explore.png", app.t)
        app.state.show(Route.SEARCH)
        renderer.save(app.screen(), args.render_dir / "search.png", app.t)
        app.show_search_results("science", DiscoveryResult((demo_result,), cached=True))
        renderer.save(app.screen(), args.render_dir / "search_results.png", app.t)
        cover_path = app.artwork_cache.ensure_cached(DEMO_COVER_URL, online=False)
        renderer.save_player(PlayerViewModel(
            "demo-ep-1", "How Stars Begin", "Saved Science", PlaybackState.PAUSED,
            185_000, 1_800_000, local=True, artwork_path=cover_path,
        ), args.render_dir / "player.png", app.t)
        subscribed = app.repositories.podcasts.list_subscribed()
        if subscribed:
            app.show_podcast(subscribed[0].id)
            renderer.save(app.screen(), args.render_dir / "podcast.png", app.t)
            app.show_import_results([
                ImportPreview(1, subscribed[0].feed_url, ImportStatus.DUPLICATE, podcast=subscribed[0]),
                ImportPreview(2, "https://blocked.example/feed", ImportStatus.ERROR,
                              error_code="non_public_address"),
            ])
            renderer.save(app.screen(), args.render_dir / "rss_import.png", app.t)
        names = ", ".join(item.title for item in app.repositories.podcasts.list_subscribed()) or "empty library"
        print(f"Rendered 640x480 review screens with {names}")
    elif args.diagnostic:
        renderer.save(app.screen(Route.HOME), paths.cache / "diagnostic-home.png", app.t)
        print("AnberPod diagnostic render complete")
        if isinstance(connectivity, SystemConnectivityProbe):
            connectivity.shutdown()
    else:
        from anberpod.input.reader import InputReader
        from anberpod.ui.loop import run

        input_reader = InputReader(args.input_device)
        try:
            run(app, input_reader)
        finally:
            if isinstance(connectivity, SystemConnectivityProbe):
                connectivity.shutdown()
        return 0
    app.handle(InputEvent(InputAction.MENU))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
