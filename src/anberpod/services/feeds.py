from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable

from anberpod.adapters.sqlite import Repositories
from anberpod.domain.errors import FeedHttpError
from anberpod.domain.models import Episode, FeedValidators, ParsedFeed, Podcast
from anberpod.domain.ports import Clock, FeedReader


@dataclass(frozen=True)
class FeedPreview:
    parsed: ParsedFeed
    validators: FeedValidators


class UpdateStatus(str, Enum):
    UPDATED = "updated"
    NOT_MODIFIED = "not_modified"


class FeedService:
    def __init__(self, reader: FeedReader, repositories: Repositories, clock: Clock) -> None:
        self.reader = reader
        self.repositories = repositories
        self.clock = clock

    def preview(self, url: str) -> FeedPreview:
        fetched = self.reader.fetch(url, FeedValidators())
        if fetched.body is None:
            raise FeedHttpError("new feed returned no body", code="empty_feed")
        parsed = self.reader.parse(fetched.body, fetched.source_url)
        return FeedPreview(self._timestamp(parsed, fetched.validators), fetched.validators)

    def subscribe(self, preview: FeedPreview) -> None:
        self.repositories.persist_feed(preview.parsed, subscribe_at=self.clock.now_utc())

    def unsubscribe(self, podcast_id: str) -> None:
        self.repositories.podcasts.unsubscribe(podcast_id)

    def update(self, podcast_id: str) -> UpdateStatus:
        existing = self.repositories.podcasts.get(podcast_id)
        if existing is None:
            raise KeyError(podcast_id)
        validators = FeedValidators(existing.etag, existing.last_modified)
        fetched = self.reader.fetch(existing.feed_url, validators)
        now = self.clock.now_utc()
        if fetched.not_modified:
            self.repositories.podcasts.save(replace(existing, last_checked_at=now, updated_at=now))
            return UpdateStatus.NOT_MODIFIED
        if fetched.body is None:
            raise FeedHttpError("updated feed returned no body", code="empty_feed")
        incoming = self.reader.parse(fetched.body, existing.feed_url)
        remapped = ParsedFeed(
            replace(
                incoming.podcast,
                id=existing.id,
                feed_url=existing.feed_url,
                etag=fetched.validators.etag,
                last_modified=fetched.validators.last_modified,
                last_checked_at=now,
                last_success_at=now,
                created_at=existing.created_at,
                updated_at=now,
            ),
            tuple(replace(item, podcast_id=existing.id, created_at=item.created_at or now, updated_at=now)
                  for item in incoming.episodes),
        )
        self.repositories.persist_feed(remapped)
        return UpdateStatus.UPDATED

    def update_all(self, cancelled: Callable[[], bool] = lambda: False) -> list[tuple[str, UpdateStatus]]:
        results: list[tuple[str, UpdateStatus]] = []
        for podcast in self.repositories.podcasts.list_subscribed():
            if cancelled():
                break
            results.append((podcast.id, self.update(podcast.id)))
        return results

    def _timestamp(self, parsed: ParsedFeed, validators: FeedValidators) -> ParsedFeed:
        now = self.clock.now_utc()
        podcast: Podcast = replace(
            parsed.podcast,
            etag=validators.etag,
            last_modified=validators.last_modified,
            last_checked_at=now,
            last_success_at=now,
            created_at=parsed.podcast.created_at or now,
            updated_at=now,
        )
        episodes: tuple[Episode, ...] = tuple(
            replace(item, created_at=item.created_at or now, updated_at=now) for item in parsed.episodes
        )
        return ParsedFeed(podcast, episodes)
