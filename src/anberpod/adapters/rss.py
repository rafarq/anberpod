from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from anberpod.adapters.sqlite import derive_source_key
from anberpod.domain.errors import FeedHttpError, FeedParseError
from anberpod.domain.models import (
    Episode,
    FeedFetchResult,
    FeedValidators,
    ParsedFeed,
    Podcast,
    RequestPolicy,
)
from anberpod.domain.ports import HttpTransport


RSS_POLICY = RequestPolicy(max_bytes=5 * 1024 * 1024)
_FORBIDDEN_XML = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local(child.tag) == name]


def _child(element: ET.Element, *names: str) -> ET.Element | None:
    wanted = set(names)
    return next((child for child in element if _local(child.tag) in wanted), None)


def _text(element: ET.Element, *names: str) -> str | None:
    child = _child(element, *names)
    value = "".join(child.itertext()).strip() if child is not None else ""
    return value or None


def _attr(element: ET.Element | None, name: str) -> str | None:
    value = element.attrib.get(name, "").strip() if element is not None else ""
    return value or None


def _safe_http_url(raw: str | None, base: str) -> str | None:
    if not raw:
        return None
    value = urljoin(base, raw.strip())
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None or parts.fragment:
        return None
    return value


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _duration(value: str | None) -> int | None:
    if not value:
        return None
    pieces = value.strip().split(":")
    try:
        if len(pieces) == 3:
            seconds = int(pieces[0]) * 3600 + int(pieces[1]) * 60 + float(pieces[2])
        elif len(pieces) == 2:
            seconds = int(pieces[0]) * 60 + float(pieces[1])
        else:
            seconds = float(pieces[0])
    except (ValueError, IndexError):
        return None
    return round(seconds * 1000) if seconds >= 0 else None


def _nonnegative_int(value: str | None) -> int | None:
    try:
        number = int(value) if value is not None else None
    except ValueError:
        return None
    return number if number is not None and number >= 0 else None


def _bounded_tree(body: bytes) -> ET.Element:
    if _FORBIDDEN_XML.search(body):
        raise FeedParseError("DTD and entity declarations are forbidden")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise FeedParseError("malformed XML") from exc
    elements = list(root.iter())
    if len(elements) > 10_000:
        raise FeedParseError("feed has too many XML elements")
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > 64:
            raise FeedParseError("feed XML is too deeply nested")
        stack.extend((child, depth + 1) for child in node)
    return root


class DirectFeedReader:
    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    def fetch(self, url: str, validators: FeedValidators) -> FeedFetchResult:
        headers = {"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
        if validators.etag:
            headers["If-None-Match"] = validators.etag
        if validators.last_modified:
            headers["If-Modified-Since"] = validators.last_modified
        response = self.transport.request(RSS_POLICY, url, headers)
        if response.status == 304:
            return FeedFetchResult(url, None, validators, not_modified=True)
        if response.status != 200:
            raise FeedHttpError(f"feed returned HTTP {response.status}", code=f"http_{response.status}")
        etag = next((v for k, v in response.headers.items() if k.lower() == "etag"), None)
        modified = next((v for k, v in response.headers.items() if k.lower() == "last-modified"), None)
        return FeedFetchResult(url, response.body, FeedValidators(etag, modified))

    def parse(self, body: bytes, source_url: str) -> ParsedFeed:
        root = _bounded_tree(body)
        kind = _local(root.tag)
        if kind == "rss":
            channel = _child(root, "channel")
            if channel is None:
                raise FeedParseError("RSS channel is missing")
            return self._rss(channel, source_url)
        if kind == "feed":
            return self._atom(root, source_url)
        raise FeedParseError("XML is not RSS 2.0 or Atom")

    def preview(self, url: str) -> Podcast:
        fetched = self.fetch(url, FeedValidators())
        if fetched.body is None:
            raise FeedParseError("preview unexpectedly returned not modified")
        return self.parse(fetched.body, fetched.source_url).podcast

    def _rss(self, channel: ET.Element, source_url: str) -> ParsedFeed:
        title = _text(channel, "title")
        if not title:
            raise FeedParseError("feed title is required")
        podcast_id = str(uuid.uuid5(uuid.NAMESPACE_URL, source_url))
        image_node = _child(channel, "image")
        image = _safe_http_url(_attr(image_node, "href"), source_url)
        if image is None and image_node is not None:
            image = _safe_http_url(_text(image_node, "url"), source_url)
        episodes = tuple(filter(None, (self._rss_episode(item, podcast_id, source_url) for item in _children(channel, "item"))))
        if not episodes:
            raise FeedParseError("feed has no playable episodes")
        podcast = Podcast(podcast_id, source_url, title, author=_text(channel, "author"),
                          description=_text(channel, "description"), image_url=image,
                          language=_text(channel, "language"))
        return ParsedFeed(podcast, episodes)

    def _rss_episode(self, item: ET.Element, podcast_id: str, source_url: str) -> Episode | None:
        enclosure = _child(item, "enclosure")
        media_url = _safe_http_url(_attr(enclosure, "url"), source_url)
        title = _text(item, "title")
        if not media_url or not title:
            return None
        guid = _text(item, "guid")
        published = _date(_text(item, "pubdate", "published", "date"))
        key = derive_source_key(guid, media_url, title, published)
        episode_id = str(uuid.uuid5(uuid.UUID(podcast_id), key))
        image = _safe_http_url(_attr(_child(item, "image"), "href"), source_url)
        return Episode(episode_id, podcast_id, key, media_url, title, guid=guid,
                       description=_text(item, "description", "summary"), published_at=published,
                       duration_ms=_duration(_text(item, "duration")),
                       media_length_bytes=_nonnegative_int(_attr(enclosure, "length")),
                       media_type=_attr(enclosure, "type"), image_url=image)

    def _atom(self, feed: ET.Element, source_url: str) -> ParsedFeed:
        title = _text(feed, "title")
        if not title:
            raise FeedParseError("feed title is required")
        podcast_id = str(uuid.uuid5(uuid.NAMESPACE_URL, source_url))
        author_node = _child(feed, "author")
        author = _text(author_node, "name") if author_node is not None else None
        image = _safe_http_url(_text(feed, "logo", "icon"), source_url)
        episodes = tuple(filter(None, (self._atom_episode(item, podcast_id, source_url) for item in _children(feed, "entry"))))
        if not episodes:
            raise FeedParseError("feed has no playable episodes")
        return ParsedFeed(Podcast(podcast_id, source_url, title, author=author,
                                  description=_text(feed, "subtitle"), image_url=image), episodes)

    def _atom_episode(self, entry: ET.Element, podcast_id: str, source_url: str) -> Episode | None:
        enclosure = next((node for node in _children(entry, "link") if node.attrib.get("rel", "alternate") == "enclosure"), None)
        media_url = _safe_http_url(_attr(enclosure, "href"), source_url)
        title = _text(entry, "title")
        if not media_url or not title:
            return None
        guid = _text(entry, "id")
        published = _date(_text(entry, "published", "updated"))
        key = derive_source_key(guid, media_url, title, published)
        episode_id = str(uuid.uuid5(uuid.UUID(podcast_id), key))
        return Episode(episode_id, podcast_id, key, media_url, title, guid=guid,
                       description=_text(entry, "summary", "content"), published_at=published,
                       media_length_bytes=_nonnegative_int(_attr(enclosure, "length")),
                       media_type=_attr(enclosure, "type"))
