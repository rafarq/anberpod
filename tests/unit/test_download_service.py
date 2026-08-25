from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pytest

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.adapters.sqlite import Repositories
from anberpod.domain.errors import DownloadError, HttpPolicyError
from anberpod.domain.models import DownloadResponse, DownloadState, Episode, Podcast
from anberpod.services.downloads import DownloadService


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FixedClock:
    def now_utc(self) -> datetime:
        return NOW


@dataclass
class ResponseTransport:
    responses: list[DownloadResponse]

    def __post_init__(self) -> None:
        self.requests: list[tuple[str, Mapping[str, str], int]] = []

    def request(self, url: str, headers: Mapping[str, str], max_bytes: int) -> DownloadResponse:
        self.requests.append((url, headers, max_bytes))
        return self.responses.pop(0)


class AcceptingProbe:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.paths: list[Path] = []

    def validate(self, path: Path) -> bool:
        self.paths.append(path)
        return self.valid


def setup_service(
    tmp_path: Path,
    responses: list[DownloadResponse],
    *,
    max_bytes: int = 32,
    free_bytes: int = 10_000,
    probe: AcceptingProbe | None = None,
) -> tuple[DownloadService, Repositories, ResponseTransport, AtomicFiles]:
    root = tmp_path / "data"
    root.mkdir()
    repos = Repositories.open(root / "db.sqlite3")
    repos.podcasts.save(Podcast("pod", "https://example.test/feed", "Show", created_at=NOW, updated_at=NOW))
    repos.episodes.upsert(Episode(
        "ep", "pod", "guid:ep", "https://cdn.example.test/ep.mp3", "Episode",
        media_length_bytes=8, media_type="audio/mpeg", created_at=NOW, updated_at=NOW,
    ))
    transport = ResponseTransport(responses)
    files = AtomicFiles(root)
    service = DownloadService(
        repos.downloads,
        repos.episodes,
        files,
        transport,
        probe or AcceptingProbe(),
        FixedClock(),
        max_bytes=max_bytes,
        free_bytes=lambda: free_bytes,
        space_margin_bytes=4,
    )
    return service, repos, transport, files


def response(status: int, body: bytes, **headers: str) -> DownloadResponse:
    return DownloadResponse(status, headers, (body,))


def test_download_only_becomes_complete_after_fsync_probe_and_atomic_rename(tmp_path: Path) -> None:
    probe = AcceptingProbe()
    service, repos, _, files = setup_service(
        tmp_path,
        [response(200, b"12345678", **{"Content-Length": "8", "ETag": '"v1"'})],
        probe=probe,
    )

    service.queue(repos.episodes.get("ep"))
    completed = service.run("ep")

    assert completed.state is DownloadState.COMPLETE
    assert completed.bytes_received == completed.bytes_total == 8
    assert completed.relative_path == "downloads/ep.mp3"
    assert completed.temp_relative_path is None
    assert files.read_bytes("downloads/ep.mp3") == b"12345678"
    assert not files.exists("download-parts/ep.part")
    assert [path.name for path in probe.paths] == ["ep.part"]
    assert repos.downloads.get("ep") == completed


def test_download_enforces_stream_limit_when_content_length_lies(tmp_path: Path) -> None:
    service, repos, _, files = setup_service(
        tmp_path,
        [response(200, b"x" * 33, **{"Content-Length": "8"})],
        max_bytes=32,
    )
    service.queue(repos.episodes.get("ep"))

    with pytest.raises(DownloadError) as caught:
        service.run("ep")

    assert caught.value.code == "download_too_large"
    failed = repos.downloads.get("ep")
    assert failed is not None and failed.state is DownloadState.FAILED
    assert failed.error_code == "download_too_large"
    assert failed.bytes_received == 0
    assert not files.exists("downloads/ep.mp3")


def test_range_resume_requires_coherent_206_otherwise_restarts(tmp_path: Path) -> None:
    service, repos, transport, files = setup_service(
        tmp_path,
        [
            response(206, b"BAD", **{"Content-Range": "bytes 2-4/8", "Content-Length": "3"}),
            response(200, b"12345678", **{"Content-Length": "8", "ETag": '"v2"'}),
        ],
    )
    service.queue(repos.episodes.get("ep"))
    files.write_part("download-parts/ep.part", (b"1234",), append=False, max_bytes=32)
    queued = repos.downloads.get("ep")
    assert queued is not None
    repos.downloads.save(queued.__class__(
        episode_id="ep", state=DownloadState.FAILED, temp_relative_path="download-parts/ep.part",
        bytes_received=4, bytes_total=8, etag='"v1"', error_code="interrupted",
        created_at=NOW, updated_at=NOW,
    ))

    completed = service.run("ep")

    assert transport.requests[0][1]["Range"] == "bytes=4-"
    assert transport.requests[0][1]["If-Range"] == '"v1"'
    assert "Range" not in transport.requests[1][1]
    assert completed.state is DownloadState.COMPLETE
    assert files.read_bytes("downloads/ep.mp3") == b"12345678"


@pytest.mark.parametrize("url", ["http://cdn.example.test/ep.mp3", "https://u:p@cdn.example.test/ep.mp3"])
def test_download_rejects_non_https_or_credentialed_episode_url(tmp_path: Path, url: str) -> None:
    service, repos, transport, _ = setup_service(tmp_path, [])
    original = repos.episodes.get("ep")
    assert original is not None
    unsafe = Episode(
        original.id, original.podcast_id, original.source_key, url, original.title,
        media_length_bytes=original.media_length_bytes, media_type=original.media_type,
    )

    with pytest.raises(DownloadError) as caught:
        service.queue(unsafe)

    assert caught.value.code == "unsafe_media_url"
    assert transport.requests == []


def test_download_rejects_insufficient_space_known_and_unknown_length(tmp_path: Path) -> None:
    service, repos, _, _ = setup_service(tmp_path, [], max_bytes=32, free_bytes=11)
    known = repos.episodes.get("ep")
    assert known is not None
    with pytest.raises(DownloadError) as known_error:
        service.queue(known)
    assert known_error.value.code == "insufficient_space"

    unknown = Episode("other", "pod", "guid:other", "https://cdn.example.test/other.ogg", "Other")
    with pytest.raises(DownloadError) as unknown_error:
        service.queue(unknown)
    assert unknown_error.value.code == "insufficient_space"


def test_download_persists_transport_policy_error_and_partial_size(tmp_path: Path) -> None:
    class FailingTransport:
        def request(self, url: str, headers: Mapping[str, str], max_bytes: int) -> DownloadResponse:
            raise HttpPolicyError("redirected to HTTP", code="https_downgrade")

    service, repos, _, files = setup_service(tmp_path, [])
    service.transport = FailingTransport()
    service.queue(repos.episodes.get("ep"))
    files.write_part("download-parts/ep.part", (b"part",), append=False, max_bytes=32)

    with pytest.raises(DownloadError) as caught:
        service.run("ep")

    assert caught.value.code == "https_downgrade"
    failed = repos.downloads.get("ep")
    assert failed is not None
    assert failed.state is DownloadState.FAILED
    assert failed.bytes_received == 4
    assert failed.error_code == "https_downgrade"


def test_download_rejects_encoded_body_even_if_server_ignores_identity_request(tmp_path: Path) -> None:
    service, repos, _, files = setup_service(
        tmp_path,
        [response(200, b"compressed", **{"Content-Encoding": "gzip", "Content-Length": "10"})],
    )
    service.queue(repos.episodes.get("ep"))

    with pytest.raises(DownloadError) as caught:
        service.run("ep")

    assert caught.value.code == "unsupported_download_encoding"
    assert not files.exists("downloads/ep.mp3")


def test_download_persists_probe_io_failure_without_promoting_partial(tmp_path: Path) -> None:
    class BrokenProbe:
        def validate(self, path: Path) -> bool:
            raise OSError("probe executable unavailable")

    service, repos, _, files = setup_service(
        tmp_path,
        [response(200, b"12345678", **{"Content-Length": "8"})],
    )
    service.probe = BrokenProbe()
    service.queue(repos.episodes.get("ep"))

    with pytest.raises(DownloadError) as caught:
        service.run("ep")

    assert caught.value.code == "download_io"
    failed = repos.downloads.get("ep")
    assert failed is not None
    assert failed.state is DownloadState.FAILED
    assert failed.bytes_received == 8
    assert failed.error_code == "download_io"
    assert files.exists("download-parts/ep.part")
    assert not files.exists("downloads/ep.mp3")


def test_interrupted_first_response_persists_validator_for_range_retry(tmp_path: Path) -> None:
    def interrupted_body():  # type: ignore[no-untyped-def]
        yield b"1234"
        raise OSError("connection dropped")

    service, repos, transport, files = setup_service(
        tmp_path,
        [
            DownloadResponse(200, {"Content-Length": "8", "ETag": '"v1"'}, interrupted_body()),
            response(206, b"5678", **{"Content-Range": "bytes 4-7/8", "Content-Length": "4", "ETag": '"v1"'}),
        ],
    )
    service.queue(repos.episodes.get("ep"))

    with pytest.raises(DownloadError):
        service.run("ep")

    interrupted = repos.downloads.get("ep")
    assert interrupted is not None
    assert interrupted.bytes_received == 4
    assert interrupted.bytes_total == 8
    assert interrupted.etag == '"v1"'

    completed = service.run("ep")

    assert transport.requests[1][1]["Range"] == "bytes=4-"
    assert transport.requests[1][1]["If-Range"] == '"v1"'
    assert completed.state is DownloadState.COMPLETE
    assert files.read_bytes("downloads/ep.mp3") == b"12345678"
