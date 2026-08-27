"""iTunes Search API adapter: a keyless PodcastCatalog implementation.

Apple's iTunes Search API (`https://itunes.apple.com/search`) requires no
registration, no API key, and no signed request headers, unlike Podcast
Index. It is used as the default discovery source so a freshly installed
AnberPod can browse and search podcasts with zero configuration; a user who
configures their own Podcast Index credentials in `data/config/config.toml`
gets that richer catalog instead (see `Application.open`).

Apple's Podcasts category list is a small, stable, Apple-documented set
(https://podcasters.apple.com/support/1691-apple-podcasts-categories) with
no public "list categories" API endpoint, so it is hardcoded here rather
than fetched. Selecting a category re-uses the same text search against
that category's name, which is a reasonable approximation without needing
Apple's separate (undocumented, unstable) genre-ID RSS feeds.
"""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit

from anberpod import __version__
from anberpod.domain.errors import CatalogDataError, CatalogUnavailableError
from anberpod.domain.models import Podcast, RequestPolicy
from anberpod.domain.ports import HttpTransport


API_ROOT = "https://itunes.apple.com"
CATALOG_POLICY = RequestPolicy(max_bytes=2 * 1024 * 1024)
MAX_SEARCH_RESULTS = 100

# Apple's top-level Apple Podcasts categories, in the order Apple documents
# them. Stable and rarely changed; see the module docstring for why this is
# hardcoded rather than fetched from an API.
CATEGORIES: tuple[str, ...] = (
    "Arts", "Business", "Comedy", "Education", "Fiction", "Government",
    "History", "Health & Fitness", "Kids & Family", "Leisure", "Music",
    "News", "Religion & Spirituality", "Science", "Society & Culture",
    "Sports", "Technology", "True Crime", "TV & Film",
)


class ITunesCatalogClient:
    """Keyless PodcastCatalog backed by the public iTunes Search API."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        api_root: str = API_ROOT,
        user_agent: str = f"AnberPod/{__version__}",
    ) -> None:
        self.transport = transport
        self.api_root = api_root.rstrip("/")
        self.user_agent = user_agent

    def categories(self) -> list[str]:
        return list(CATEGORIES)

    def search(self, query: str, limit: int) -> list[Podcast]:
        clean_query = query.strip()
        if not clean_query or len(clean_query) > 200:
            raise ValueError("search query must contain 1 to 200 characters")
        if not 1 <= limit <= MAX_SEARCH_RESULTS:
            raise ValueError("search limit must be between 1 and 100")
        # Ask iTunes for a generous margin over results that will actually
        # validate (missing feedUrl, non-podcast entries) so a real search
        # doesn't come back short just because of filtering.
        requested = min(200, limit * 3)
        payload = self._get({"term": clean_query, "media": "podcast", "limit": str(requested)})
        podcasts: list[Podcast] = []
        for item in _list_field(payload, "results"):
            podcast = _podcast(item)
            if podcast is not None:
                podcasts.append(podcast)
            if len(podcasts) >= limit:
                break
        return podcasts

    def podcast(self, feed_id: int) -> Podcast | None:
        if not isinstance(feed_id, int) or isinstance(feed_id, bool) or feed_id <= 0:
            raise ValueError("feed_id must be a positive integer")
        payload = self._get({"id": str(feed_id), "entity": "podcast"}, path="lookup")
        results = _list_field(payload, "results")
        return _podcast(results[0]) if results else None

    def _get(self, parameters: Mapping[str, str], *, path: str = "search") -> Any:
        url = f"{self.api_root}/{path}?{urlencode(parameters)}"
        response = self.transport.request(CATALOG_POLICY, url, {"User-Agent": self.user_agent})
        if response.status >= 500:
            raise CatalogUnavailableError("iTunes Search API is temporarily unavailable")
        if response.status != 200:
            raise CatalogUnavailableError(f"iTunes Search API returned HTTP {response.status}")
        try:
            return json.loads(response.body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CatalogDataError("iTunes Search API returned invalid JSON") from exc


def _list_field(payload: Any, field: str) -> list[Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
        raise CatalogDataError("iTunes Search API response has an invalid shape")
    return payload[field]


def _podcast(value: Any) -> Podcast | None:
    """Map one iTunes search result to a Podcast, or ``None`` to skip it.

    Unlike Podcast Index (a trusted, request-signed source), this consumes
    unauthenticated public API output, so malformed/incomplete individual
    entries are silently skipped rather than raising and discarding an
    entire otherwise-valid result page.
    """
    if not isinstance(value, dict):
        return None
    if value.get("kind") != "podcast":
        return None
    identifier = value.get("collectionId", value.get("trackId"))
    feed_url = value.get("feedUrl")
    title = value.get("collectionName") or value.get("trackName")
    if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0:
        return None
    if not isinstance(feed_url, str) or not feed_url or len(feed_url) > 2048:
        return None
    if urlsplit(feed_url).scheme.lower() != "https":
        return None
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        return None
    author = value.get("artistName")
    image_url = value.get("artworkUrl600") or value.get("artworkUrl100")
    return Podcast(
        id=f"catalog:{identifier}",
        feed_url=feed_url,
        title=title.strip(),
        author=author.strip() if isinstance(author, str) and author.strip() else None,
        image_url=image_url if isinstance(image_url, str) and urlsplit(image_url).scheme.lower() == "https" else None,
        catalog_id=identifier,
    )
