from __future__ import annotations

import pytest

from anberpod.adapters.rss import DirectFeedReader
from anberpod.domain.errors import FeedParseError
from anberpod.domain.models import FeedValidators, HttpResponse, RequestPolicy


RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
 <channel><title>Safe Show</title><description>A public feed</description><language>en</language>
 <itunes:author>Alice</itunes:author><itunes:image href="https://img.example.test/show.jpg"/>
 <item><guid> ep-1 </guid><title>First</title><description>Notes</description>
 <pubDate>Fri, 02 Jan 2026 03:04:05 GMT</pubDate><itunes:duration>01:02:03</itunes:duration>
 <enclosure url="https://cdn.example.test/one.mp3" length="123" type="audio/mpeg"/></item>
 <item><title>No playable media</title></item></channel></rss>"""

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom">
 <title>Atom Show</title><subtitle>Atom notes</subtitle><author><name>Bob</name></author>
 <entry><id>tag:example.test,2026:2</id><title>Second</title>
 <updated>2026-02-03T04:05:06Z</updated>
 <link rel="enclosure" href="https://cdn.example.test/two.ogg" type="audio/ogg" length="42"/>
 </entry></feed>"""


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.calls = []

    def request(self, policy, url, headers):  # type: ignore[no-untyped-def]
        self.calls.append((policy, url, headers))
        return self.responses.pop(0)


def test_rss2_parser_extracts_podcast_and_only_playable_episodes() -> None:
    parsed = DirectFeedReader(FakeTransport([])).parse(RSS, "https://feeds.example.test/show")

    assert parsed.podcast.title == "Safe Show"
    assert parsed.podcast.author == "Alice"
    assert parsed.podcast.image_url == "https://img.example.test/show.jpg"
    assert len(parsed.episodes) == 1
    episode = parsed.episodes[0]
    assert episode.guid == "ep-1"
    assert episode.duration_ms == 3_723_000
    assert episode.media_length_bytes == 123
    assert episode.media_type == "audio/mpeg"
    assert episode.source_key == "guid:ep-1"


def test_atom_parser_extracts_enclosure_and_namespaced_metadata() -> None:
    parsed = DirectFeedReader(FakeTransport([])).parse(ATOM, "http://feeds.example.test/atom")

    assert parsed.podcast.title == "Atom Show"
    assert parsed.podcast.author == "Bob"
    assert parsed.episodes[0].title == "Second"
    assert parsed.episodes[0].published_at.isoformat() == "2026-02-03T04:05:06+00:00"  # type: ignore[union-attr]
    assert parsed.episodes[0].media_url.endswith("two.ogg")


@pytest.mark.parametrize(
    "body",
    [
        b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss><channel><title>&x;</title></channel></rss>',
        b'<!DOCTYPE rss SYSTEM "https://attacker.test/evil.dtd"><rss/>',
        b'<html><title>not a feed</title></html>',
        b'<rss><channel><title>no episodes</title><item><enclosure url="file:///secret"/></item></channel></rss>',
    ],
)
def test_parser_rejects_dtd_entities_nonfeeds_and_nonpublic_media(body: bytes) -> None:
    with pytest.raises(FeedParseError):
        DirectFeedReader(FakeTransport([])).parse(body, "https://feeds.example.test/show")


def test_fetch_sends_validators_has_rss_limit_and_handles_304() -> None:
    transport = FakeTransport([
        HttpResponse(200, {"ETag": '"v2"', "Last-Modified": "Fri, 02 Jan 2026 03:04:05 GMT"}, RSS),
        HttpResponse(304, {}, b""),
    ])
    reader = DirectFeedReader(transport)

    fetched = reader.fetch("https://feeds.example.test/show", FeedValidators('"v1"', "old"))
    unchanged = reader.fetch("https://feeds.example.test/show", fetched.validators)

    assert fetched.body == RSS
    assert fetched.validators.etag == '"v2"'
    assert unchanged.not_modified is True and unchanged.body is None
    policy, _, headers = transport.calls[0]
    assert policy == RequestPolicy(max_bytes=5 * 1024 * 1024)
    assert headers == {"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml", "If-None-Match": '"v1"', "If-Modified-Since": "old"}
