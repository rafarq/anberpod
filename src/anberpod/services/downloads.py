from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, Protocol
from urllib.parse import urlsplit

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.domain.errors import DownloadError, HttpPolicyError
from anberpod.domain.models import Download, DownloadResponse, DownloadState, Episode, RequestPolicy
from anberpod.domain.ports import Clock, DownloadRepository, EpisodeRepository, HttpTransport


class DownloadTransport(Protocol):
    def request(self, url: str, headers: Mapping[str, str], max_bytes: int) -> DownloadResponse: ...


class MediaProbe(Protocol):
    def validate(self, path: Path) -> bool: ...


class BoundedHttpsDownloadTransport:
    """Download boundary that reuses the hardened redirect/DNS/TLS transport."""

    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    def request(self, url: str, headers: Mapping[str, str], max_bytes: int) -> DownloadResponse:
        if urlsplit(url).scheme.lower() != "https":
            raise HttpPolicyError("episode downloads require HTTPS", code="unsafe_media_url")
        response = self.transport.request(
            RequestPolicy(read_timeout_seconds=30.0, total_timeout_seconds=None, max_bytes=max_bytes),
            url,
            headers,
        )
        return DownloadResponse(response.status, response.headers, (response.body,))


_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
_SAFE_EXTENSIONS = {
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return next((value.strip() for key, value in headers.items() if key.lower() == name.lower()), None)


def _safe_https(url: str) -> bool:
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        return False
    return (
        parts.scheme.lower() == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and not parts.fragment
    )


class DownloadService:
    def __init__(
        self,
        downloads: DownloadRepository,
        episodes: EpisodeRepository,
        files: AtomicFiles,
        transport: DownloadTransport,
        probe: MediaProbe,
        clock: Clock,
        *,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
        free_bytes: Callable[[], int],
        space_margin_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.downloads = downloads
        self.episodes = episodes
        self.files = files
        self.transport = transport
        self.probe = probe
        self.clock = clock
        self.max_bytes = max_bytes
        self.free_bytes = free_bytes
        self.space_margin_bytes = space_margin_bytes

    def queue(self, episode: Episode | None) -> Download:
        if episode is None:
            raise DownloadError("episode does not exist", code="episode_missing")
        if not _safe_https(episode.media_url):
            raise DownloadError("episode URL must be safe HTTPS", code="unsafe_media_url")
        expected = episode.media_length_bytes
        if expected is not None and expected > self.max_bytes:
            raise DownloadError("episode exceeds configured limit", code="download_too_large")
        reserved = expected if expected is not None else self.max_bytes
        if self.free_bytes() < reserved + self.space_margin_bytes:
            raise DownloadError("not enough free space", code="insufficient_space")
        now = self.clock.now_utc()
        value = Download(
            episode.id,
            DownloadState.QUEUED,
            temp_relative_path=f"download-parts/{episode.id}.part",
            created_at=now,
            updated_at=now,
        )
        self.downloads.save(value)
        return value

    def run(self, episode_id: str) -> Download:
        episode = self.episodes.get(episode_id)
        current = self.downloads.get(episode_id)
        if episode is None or current is None:
            raise DownloadError("download job does not exist", code="download_missing")
        temp_path = current.temp_relative_path or f"download-parts/{episode_id}.part"
        partial_size = self.files.size(temp_path) if self.files.exists(temp_path) else 0
        headers: dict[str, str] = {"Accept-Encoding": "identity"}
        resuming = partial_size > 0 and bool(current.etag or current.last_modified)
        if resuming:
            headers["Range"] = f"bytes={partial_size}-"
            headers["If-Range"] = current.etag or current.last_modified or ""
        started = replace(
            current,
            state=DownloadState.DOWNLOADING,
            relative_path=None,
            temp_relative_path=temp_path,
            bytes_received=partial_size,
            error_code=None,
            updated_at=self.clock.now_utc(),
        )
        self.downloads.save(started)
        try:
            response = self.transport.request(episode.media_url, headers, self.max_bytes)
            self._validate_encoding(response.headers)
            append = False
            total: int | None = None
            if resuming and self._coherent_resume(response, partial_size):
                append = True
                total = int(_CONTENT_RANGE.fullmatch(_header(response.headers, "Content-Range") or "").group(3))  # type: ignore[union-attr]
            elif resuming:
                self.files.unlink(temp_path)
                response = self.transport.request(episode.media_url, {"Accept-Encoding": "identity"}, self.max_bytes)
                self._validate_encoding(response.headers)
            if not append:
                if response.status != 200:
                    raise DownloadError("unexpected download status", code="download_http_status")
                total = self._content_length(response.headers)
            if total is not None and total > self.max_bytes:
                raise DownloadError("episode exceeds configured limit", code="download_too_large")
            started = replace(
                started,
                bytes_total=total,
                etag=_header(response.headers, "ETag") or started.etag,
                last_modified=_header(response.headers, "Last-Modified") or started.last_modified,
            )
            self.downloads.save(started)
            try:
                received = self.files.write_part(
                    temp_path,
                    response.body_chunks,
                    append=append,
                    max_bytes=self.max_bytes,
                )
            except ValueError as exc:
                raise DownloadError("download exceeded configured limit", code="download_too_large") from exc
            if received == 0 or (total is not None and received != total):
                raise DownloadError("download size does not match response", code="download_size_mismatch")
            if not self.probe.validate(self.files.path(temp_path)):
                raise DownloadError("downloaded media failed validation", code="invalid_media")
            final_path = f"downloads/{episode_id}.{self._extension(episode)}"
            self.files.promote_part(temp_path, final_path)
            completed = Download(
                episode_id,
                DownloadState.COMPLETE,
                relative_path=final_path,
                bytes_received=received,
                bytes_total=total or received,
                etag=started.etag,
                last_modified=started.last_modified,
                created_at=current.created_at,
                updated_at=self.clock.now_utc(),
                completed_at=self.clock.now_utc(),
            )
            self.downloads.save(completed)
            return completed
        except (DownloadError, HttpPolicyError, OSError) as exc:
            if isinstance(exc, DownloadError):
                error = exc
            elif isinstance(exc, HttpPolicyError):
                error = DownloadError(str(exc), code=exc.code)
            else:
                error = DownloadError("download input/output failure", code="download_io")
            received = self.files.size(temp_path) if self.files.exists(temp_path) else 0
            failed = replace(
                started,
                state=DownloadState.FAILED,
                bytes_received=received,
                error_code=error.code,
                updated_at=self.clock.now_utc(),
            )
            self.downloads.save(failed)
            if error is exc:
                raise
            raise error from exc

    @staticmethod
    def _content_length(headers: Mapping[str, str]) -> int | None:
        raw = _header(headers, "Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError as exc:
            raise DownloadError("invalid Content-Length", code="invalid_download_headers") from exc
        if value < 0:
            raise DownloadError("invalid Content-Length", code="invalid_download_headers")
        return value

    @staticmethod
    def _validate_encoding(headers: Mapping[str, str]) -> None:
        encoding = (_header(headers, "Content-Encoding") or "identity").lower()
        if encoding not in {"", "identity"}:
            raise DownloadError("encoded episode bodies are unsupported", code="unsupported_download_encoding")

    @staticmethod
    def _coherent_resume(response: DownloadResponse, start: int) -> bool:
        if response.status != 206:
            return False
        match = _CONTENT_RANGE.fullmatch(_header(response.headers, "Content-Range") or "")
        if match is None:
            return False
        first, last, total = (int(value) for value in match.groups())
        length = DownloadService._content_length(response.headers)
        return first == start and first <= last < total and (length is None or length == last - first + 1)

    @staticmethod
    def _extension(episode: Episode) -> str:
        media_type = (episode.media_type or "").split(";", 1)[0].strip().lower()
        if media_type in _SAFE_EXTENSIONS:
            return _SAFE_EXTENSIONS[media_type]
        suffix = Path(urlsplit(episode.media_url).path).suffix.lower().lstrip(".")
        return suffix if suffix in {"mp3", "m4a", "ogg", "opus", "wav"} else "audio"


def delete_download(
    episode_id: str,
    downloads: DownloadRepository,
    files: AtomicFiles,
    *,
    in_use: Callable[[str], bool] | None = None,
    extra_temp_path: str | None = None,
) -> None:
    if in_use is not None and in_use(episode_id):
        raise DownloadError("download is currently in use", code="download_in_use")
    current = downloads.get(episode_id)
    if current is None:
        return
    paths = {current.relative_path, current.temp_relative_path, extra_temp_path}
    for relative_path in paths:
        if relative_path:
            files.unlink(relative_path)
    downloads.delete(episode_id)
