from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.adapters.sqlite import Repositories
from anberpod.domain.errors import HttpPolicyError
from anberpod.domain.models import Episode, FeedFetchResult, FeedValidators, ParsedFeed, Podcast
from anberpod.services.feeds import FeedService
from anberpod.services.import_preview import ImportStatus, RssFileImporter


NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


class Clock:
    def now_utc(self) -> datetime:
        return NOW


class Reader:
    def __init__(self) -> None:
        self.last_url = ""

    def fetch(self, url, validators):  # type: ignore[no-untyped-def]
        self.last_url = url
        if "private" in url:
            raise HttpPolicyError("blocked", code="non_public_address")
        return FeedFetchResult(url, b"xml", FeedValidators('"tag"'))

    def parse(self, body, source_url):  # type: ignore[no-untyped-def]
        key = source_url.rsplit("/", 1)[-1]
        podcast = Podcast(f"pod-{key}", source_url, f"Show {key}")
        episode = Episode(f"ep-{key}", podcast.id, f"guid:{key}", f"https://cdn.example.test/{key}.mp3", f"Episode {key}")
        return ParsedFeed(podcast, (episode,))


def test_file_preview_writes_per_line_results_then_confirm_persists_selected_feed(tmp_path: Path) -> None:
    source = tmp_path / "imports" / "rss_urls.txt"
    source.parent.mkdir()
    source.write_text(
        "# public feeds\nhttps://feeds.example.test/one\n"
        "https://feeds.example.test/one\nhttps://private.example.test/feed\n",
        encoding="utf-8",
    )
    repos = Repositories.open(tmp_path / "db.sqlite3")
    importer = RssFileImporter(FeedService(Reader(), repos, Clock()), repos, AtomicFiles(tmp_path))

    previews = importer.preview_file(source)

    assert [item.status for item in previews] == [ImportStatus.OK, ImportStatus.DUPLICATE, ImportStatus.ERROR]
    assert previews[2].error_code == "non_public_address"
    assert source.exists()
    assert (tmp_path / "imports" / "rss_urls.result.txt").read_text(encoding="utf-8").splitlines() == [
        "2\tOK\thttps://feeds.example.test/one",
        "3\tDUPLICATE\thttps://feeds.example.test/one",
        "4\tERROR:non_public_address\thttps://private.example.test/feed",
    ]

    importer.subscribe(previews[0])

    assert [podcast.title for podcast in repos.podcasts.list_subscribed()] == ["Show one"]
    assert repos.episodes.list_for_podcast("pod-one")[0].title == "Episode one"


def test_existing_feed_is_duplicate_and_opens_without_network(tmp_path: Path) -> None:
    source = tmp_path / "imports" / "rss_urls.txt"
    source.parent.mkdir()
    source.write_text("https://feeds.example.test/one\n", encoding="utf-8")
    repos = Repositories.open(tmp_path / "db.sqlite3")
    reader = Reader()
    service = FeedService(reader, repos, Clock())
    first = service.preview("https://feeds.example.test/one")
    service.subscribe(first)
    reader.last_url = ""
    importer = RssFileImporter(service, repos, AtomicFiles(tmp_path))

    result = importer.preview_file(source)[0]

    assert result.status is ImportStatus.DUPLICATE
    assert result.podcast is not None and result.podcast.id == "pod-one"
    assert reader.last_url == ""
