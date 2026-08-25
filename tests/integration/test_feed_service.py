from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from anberpod.adapters.sqlite import Repositories
from anberpod.domain.models import Episode, FeedFetchResult, FeedValidators, ParsedFeed, Playback, Podcast
from anberpod.services.feeds import FeedService, UpdateStatus


NOW = datetime(2026, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
LATER = datetime(2026, 4, 6, 6, 7, 8, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now_utc(self) -> datetime:
        return self.value


def parsed(title: str = "Imported Show", episode_title: str = "Episode One") -> ParsedFeed:
    podcast = Podcast("pod-1", "https://feeds.example.test/show", title)
    episode = Episode(
        "ep-1", "pod-1", "guid:one", "https://cdn.example.test/one.mp3", episode_title, guid="one"
    )
    return ParsedFeed(podcast, (episode,))


class FakeReader:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, FeedValidators]] = []
        self.results = [
            FeedFetchResult("https://feeds.example.test/show", b"first", FeedValidators('"v1"', "old")),
        ]
        self.parsed = [parsed()]

    def fetch(self, url: str, validators: FeedValidators) -> FeedFetchResult:
        self.fetches.append((url, validators))
        return self.results.pop(0)

    def parse(self, body: bytes, source_url: str) -> ParsedFeed:
        return self.parsed.pop(0)


def test_preview_then_subscribe_persists_feed_and_episodes_transactionally(tmp_path: Path) -> None:
    repos = Repositories.open(tmp_path / "state.sqlite3")
    reader = FakeReader()
    service = FeedService(reader, repos, FixedClock(NOW))

    preview = service.preview("https://feeds.example.test/show")
    service.subscribe(preview)

    saved = repos.podcasts.get("pod-1")
    assert saved is not None
    assert saved.etag == '"v1"'
    assert saved.last_checked_at == saved.last_success_at == NOW
    assert [item.id for item in repos.podcasts.list_subscribed()] == ["pod-1"]
    assert repos.episodes.list_for_podcast("pod-1")[0].title == "Episode One"


def test_manual_update_is_conditional_upserts_and_304_preserves_saved_data(tmp_path: Path) -> None:
    repos = Repositories.open(tmp_path / "state.sqlite3")
    reader = FakeReader()
    clock = FixedClock(NOW)
    service = FeedService(reader, repos, clock)
    service.subscribe(service.preview("https://feeds.example.test/show"))
    repos.playback.save(Playback("ep-1", 9000, updated_at=NOW))

    reader.results.extend([
        FeedFetchResult("https://feeds.example.test/show", b"second", FeedValidators('"v2"', "new")),
        FeedFetchResult("https://feeds.example.test/show", None, FeedValidators('"v2"', "new"), True),
    ])
    reader.parsed.append(parsed("Renamed Show", "Episode Renamed"))
    clock.value = LATER

    changed = service.update("pod-1")
    unchanged = service.update("pod-1")

    assert changed is UpdateStatus.UPDATED
    assert unchanged is UpdateStatus.NOT_MODIFIED
    assert reader.fetches[-2:] == [
        ("https://feeds.example.test/show", FeedValidators('"v1"', "old")),
        ("https://feeds.example.test/show", FeedValidators('"v2"', "new")),
    ]
    assert repos.podcasts.get("pod-1").title == "Renamed Show"  # type: ignore[union-attr]
    assert repos.episodes.list_for_podcast("pod-1")[0].title == "Episode Renamed"
    assert repos.playback.get("ep-1").position_ms == 9000  # type: ignore[union-attr]


def test_unsubscribe_and_resubscribe_preserve_episode_and_playback(tmp_path: Path) -> None:
    repos = Repositories.open(tmp_path / "state.sqlite3")
    reader = FakeReader()
    service = FeedService(reader, repos, FixedClock(NOW))
    preview = service.preview("https://feeds.example.test/show")
    service.subscribe(preview)
    repos.playback.save(Playback("ep-1", 12_000, updated_at=NOW))

    service.unsubscribe("pod-1")
    assert repos.podcasts.list_subscribed() == []
    assert repos.episodes.list_for_podcast("pod-1")[0].id == "ep-1"
    assert repos.playback.get("ep-1").position_ms == 12_000  # type: ignore[union-attr]

    service.subscribe(replace(preview, parsed=parsed("Imported Show", "Episode One")))
    assert repos.playback.get("ep-1").position_ms == 12_000  # type: ignore[union-attr]


def test_update_all_is_sequential_and_cooperatively_cancellable(tmp_path: Path) -> None:
    repos = Repositories.open(tmp_path / "state.sqlite3")
    for number in (1, 2):
        repos.podcasts.save(Podcast(
            f"pod-{number}", f"https://feeds.example.test/{number}", f"Show {number}",
            etag=f'"v{number}"', created_at=NOW, updated_at=NOW,
        ))
        repos.podcasts.subscribe(f"pod-{number}", NOW)
    reader = FakeReader()
    reader.results = [
        FeedFetchResult("https://feeds.example.test/1", None, FeedValidators('"v1"'), True),
        FeedFetchResult("https://feeds.example.test/2", None, FeedValidators('"v2"'), True),
    ]
    service = FeedService(reader, repos, FixedClock(LATER))
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    results = service.update_all(cancelled)

    assert results == [("pod-1", UpdateStatus.NOT_MODIFIED)]
    assert reader.fetches == [("https://feeds.example.test/1", FeedValidators('"v1"', None))]
