from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from anberpod.domain.models import Podcast
from anberpod.services.import_preview import ImportStatus, preview_import_file


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


class FakeReader:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def preview(self, url: str) -> Podcast:
        self.urls.append(url)
        if "broken" in url:
            raise ValueError("synthetic invalid feed")
        return Podcast(f"preview-{len(self.urls)}", url, f"Preview {len(self.urls)}", created_at=NOW, updated_at=NOW)


def test_import_file_handles_comments_duplicates_and_per_line_errors(tmp_path: Path) -> None:
    source = tmp_path / "rss_urls.txt"
    source.write_text(
        "# copied from my desktop\n\nhttps://example.test/feed\n"
        "https://example.test/feed\nhttps://broken.example/feed\n",
        encoding="utf-8",
    )
    reader = FakeReader()

    results = preview_import_file(source, reader)

    assert [result.status for result in results] == [ImportStatus.OK, ImportStatus.DUPLICATE, ImportStatus.ERROR]
    assert results[0].podcast is not None
    assert results[2].error_code == "invalid_feed"
    assert reader.urls == ["https://example.test/feed", "https://broken.example/feed"]


def test_import_rejects_url_credentials_and_overlong_url(tmp_path: Path) -> None:
    overlong = "https://example.test/" + ("a" * 2049)
    source = tmp_path / "rss_urls.txt"
    source.write_text(f"https://user:pass@example.test/feed\n{overlong}\nftp://example.test/feed\n", encoding="utf-8")

    results = preview_import_file(source, FakeReader())

    assert [result.error_code for result in results] == ["url_credentials", "url_too_long", "unsupported_scheme"]


def test_http_feed_is_marked_insecure_before_preview(tmp_path: Path) -> None:
    source = tmp_path / "rss_urls.txt"
    source.write_text("http://example.test/feed\n", encoding="utf-8")

    result = preview_import_file(source, FakeReader())[0]

    assert result.status is ImportStatus.OK
    assert result.insecure_http is True
