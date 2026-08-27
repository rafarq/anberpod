from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Generic, Iterable, Mapping, TypeVar


T = TypeVar("T")


class DownloadState(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    FAILED = "failed"


class PlaybackState(str, Enum):
    IDLE = "idle"
    BUFFERING = "buffering"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    ENDED = "ended"
    ERROR = "error"


class InputAction(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    ACCEPT = "accept"
    BACK = "back"
    MENU = "menu"
    DELETE = "delete"


@dataclass(frozen=True)
class InputEvent:
    action: InputAction
    repeated: bool = False


@dataclass(frozen=True)
class RequestPolicy:
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 20.0
    total_timeout_seconds: float | None = 60.0
    max_bytes: int = 2 * 1024 * 1024


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class DownloadResponse:
    status: int
    headers: Mapping[str, str]
    body_chunks: Iterable[bytes]


@dataclass(frozen=True)
class FeedValidators:
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class FeedFetchResult:
    source_url: str
    body: bytes | None
    validators: FeedValidators
    not_modified: bool = False


@dataclass(frozen=True)
class ParsedFeed:
    podcast: Podcast
    episodes: tuple[Episode, ...]


@dataclass(frozen=True)
class CatalogCredentials:
    api_key: str
    api_secret: str


@dataclass(frozen=True)
class DiscoveryResult(Generic[T]):
    items: tuple[T, ...]
    cached: bool = False
    stale: bool = False
    warning_code: str | None = None


@dataclass(frozen=True)
class DownloadJob:
    episode_id: str
    url: str
    temp_relative_path: str


@dataclass(frozen=True)
class DownloadEvent:
    episode_id: str
    state: DownloadState
    bytes_received: int = 0


@dataclass(frozen=True)
class PlaybackSource:
    value: str
    local: bool


@dataclass(frozen=True)
class PlaybackEvent:
    state: PlaybackState
    position_ms: int = 0
    error_code: str | None = None


@dataclass(frozen=True)
class Podcast:
    id: str
    feed_url: str
    title: str
    author: str | None = None
    description: str | None = None
    image_url: str | None = None
    language: str | None = None
    catalog_id: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class Episode:
    id: str
    podcast_id: str
    source_key: str
    media_url: str
    title: str
    guid: str | None = None
    description: str | None = None
    published_at: datetime | None = None
    duration_ms: int | None = None
    media_length_bytes: int | None = None
    media_type: str | None = None
    image_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must not be negative")
        if self.media_length_bytes is not None and self.media_length_bytes < 0:
            raise ValueError("media_length_bytes must not be negative")


@dataclass(frozen=True)
class Playback:
    episode_id: str
    position_ms: int = 0
    duration_ms: int | None = None
    completed: bool = False
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.position_ms < 0:
            raise ValueError("position_ms must not be negative")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must not be negative")


@dataclass(frozen=True)
class Download:
    episode_id: str
    state: DownloadState
    relative_path: str | None = None
    temp_relative_path: str | None = None
    bytes_received: int = 0
    bytes_total: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.bytes_received < 0:
            raise ValueError("bytes_received must not be negative")
        if self.bytes_total is not None and self.bytes_total < 0:
            raise ValueError("bytes_total must not be negative")
        if self.state is DownloadState.COMPLETE and not self.relative_path:
            raise ValueError("complete download requires relative_path")
