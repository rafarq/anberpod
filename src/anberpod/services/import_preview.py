from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from anberpod.adapters.sqlite import Repositories
from anberpod.domain.errors import AnberPodError
from anberpod.domain.models import Podcast
from anberpod.domain.ports import AtomicFilePort, FeedPreviewReader
from anberpod.services.feeds import FeedPreview, FeedService


class ImportStatus(str, Enum):
    OK = "OK"
    DUPLICATE = "DUPLICATE"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ImportPreview:
    line_number: int
    url: str
    status: ImportStatus
    podcast: Podcast | None = None
    error_code: str | None = None
    insecure_http: bool = False
    feed_preview: FeedPreview | None = None


def _validated_url(raw: str) -> tuple[str | None, str | None]:
    if len(raw) > 2048:
        return None, "url_too_long"
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None, "invalid_url"
    if parts.scheme.lower() not in {"http", "https"}:
        return None, "unsupported_scheme"
    if parts.username is not None or parts.password is not None:
        return None, "url_credentials"
    if not parts.hostname:
        return None, "missing_host"
    host = parts.hostname.lower()
    if port and not ((parts.scheme.lower() == "https" and port == 443) or (parts.scheme.lower() == "http" and port == 80)):
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, "")), None


def preview_import_file(source: Path, reader: FeedPreviewReader) -> list[ImportPreview]:
    results: list[ImportPreview] = []
    seen: set[str] = set()
    candidates = [
        (line_number, line.strip())
        for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1)
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for line_number, raw in candidates[:100]:
        url, error = _validated_url(raw)
        if error or url is None:
            results.append(ImportPreview(line_number, raw, ImportStatus.ERROR, error_code=error))
            continue
        if url in seen:
            results.append(ImportPreview(line_number, url, ImportStatus.DUPLICATE))
            continue
        seen.add(url)
        try:
            podcast = reader.preview(url)
        except (ValueError, OSError):
            results.append(ImportPreview(line_number, url, ImportStatus.ERROR, error_code="invalid_feed"))
        else:
            results.append(ImportPreview(
                line_number, url, ImportStatus.OK, podcast=podcast, insecure_http=url.startswith("http://")
            ))
    return results


class RssFileImporter:
    """Explicit, two-step SD-card import: preview first, subscribe by selection."""

    def __init__(self, feeds: FeedService, repositories: Repositories, files: AtomicFilePort) -> None:
        self.feeds = feeds
        self.repositories = repositories
        self.files = files

    def preview_file(self, source: Path) -> list[ImportPreview]:
        results: list[ImportPreview] = []
        seen: set[str] = set()
        candidates = [
            (line_number, line.strip())
            for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1)
            if line.strip() and not line.lstrip().startswith("#")
        ]
        for line_number, raw in candidates[:100]:
            url, error = _validated_url(raw)
            if error or url is None:
                results.append(ImportPreview(line_number, raw, ImportStatus.ERROR, error_code=error))
                continue
            existing = self.repositories.podcasts.get_by_feed_url(url)
            if url in seen or existing is not None:
                results.append(ImportPreview(
                    line_number, url, ImportStatus.DUPLICATE, podcast=existing,
                    insecure_http=url.startswith("http://"),
                ))
                continue
            seen.add(url)
            try:
                preview = self.feeds.preview(url)
            except (AnberPodError, ValueError, OSError) as exc:
                results.append(ImportPreview(
                    line_number, url, ImportStatus.ERROR,
                    error_code=getattr(exc, "code", "invalid_feed"),
                    insecure_http=url.startswith("http://"),
                ))
            else:
                results.append(ImportPreview(
                    line_number, url, ImportStatus.OK, podcast=preview.parsed.podcast,
                    insecure_http=url.startswith("http://"), feed_preview=preview,
                ))
        payload = "".join(self._result_line(item) for item in results).encode("utf-8")
        self.files.write_atomic("imports/rss_urls.result.txt", payload)
        return results

    def subscribe(self, item: ImportPreview) -> None:
        if item.status is not ImportStatus.OK or item.feed_preview is None:
            raise ValueError("only a successful preview can be subscribed")
        self.feeds.subscribe(item.feed_preview)

    @staticmethod
    def _result_line(item: ImportPreview) -> str:
        status = item.status.value if item.status is not ImportStatus.ERROR else f"ERROR:{item.error_code}"
        return f"{item.line_number}\t{status}\t{item.url}\n"
