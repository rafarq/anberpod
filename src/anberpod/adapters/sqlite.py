from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from anberpod.domain.errors import MigrationError
from anberpod.domain.models import Download, DownloadState, Episode, Playback, Podcast


def _text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _required_text(value: datetime | None) -> str:
    return _text(value or datetime.now(timezone.utc)) or ""


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _statements(sql: str) -> list[str]:
    result: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                result.append(buffer)
            buffer = ""
    if buffer.strip():
        result.append(buffer)
    return result


class Database:
    def __init__(self, path: Path, migrations: Path | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.migrations = migrations or Path(__file__).parents[1] / "migrations"
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")

    def _backup(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.path.with_name(f"{self.path.name}.backup-{stamp}")
        with sqlite3.connect(backup_path) as target:
            self.connection.backup(target)
        backups = sorted(self.path.parent.glob(f"{self.path.name}.backup-*"))
        for old in backups[:-3]:
            old.unlink()
        return backup_path

    def migrate(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        self.connection.commit()
        applied = {row[0] for row in self.connection.execute("SELECT version FROM schema_migration")}
        for migration in sorted(self.migrations.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(migration.name[:3])
            if version in applied:
                continue
            if applied:
                self._backup()
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                for statement in _statements(migration.read_text(encoding="utf-8")):
                    self.connection.execute(statement)
                self.connection.execute(
                    "INSERT INTO schema_migration(version, applied_at) VALUES (?, ?)",
                    (version, _required_text(None)),
                )
                self.connection.commit()
            except sqlite3.Error as exc:
                self.connection.rollback()
                raise MigrationError(f"migration {version:03d} failed") from exc


def derive_source_key(guid: str | None, media_url: str, title: str, published_at: datetime | None) -> str:
    clean_guid = (guid or "").strip()
    if clean_guid:
        return f"guid:{clean_guid}"
    if media_url.strip():
        parts = urlsplit(media_url.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
        if port and not ((parts.scheme.lower() == "https" and port == 443) or (parts.scheme.lower() == "http" and port == 80)):
            host = f"{host}:{port}"
        normalized = urlunsplit((parts.scheme.lower(), host, parts.path or "/", urlencode(sorted(parse_qsl(parts.query))), ""))
        return f"url:{normalized}"
    seed = "\x00".join((title.strip(), _text(published_at) or "", media_url.strip()))
    return f"fallback:{hashlib.sha256(seed.encode('utf-8')).hexdigest()}"


class PodcastSqliteRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, value: Podcast, *, commit: bool = True) -> None:
        self.connection.execute(
            """INSERT INTO podcast VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET feed_url=excluded.feed_url, catalog_id=excluded.catalog_id,
            title=excluded.title, author=excluded.author, description=excluded.description,
            image_url=excluded.image_url, language=excluded.language, etag=excluded.etag,
            last_modified=excluded.last_modified, last_checked_at=excluded.last_checked_at,
            last_success_at=excluded.last_success_at, updated_at=excluded.updated_at""",
            (value.id, value.feed_url, value.catalog_id, value.title, value.author, value.description,
             value.image_url, value.language, value.etag, value.last_modified, _text(value.last_checked_at),
             _text(value.last_success_at), _required_text(value.created_at), _required_text(value.updated_at)),
        )
        if commit:
            self.connection.commit()

    def subscribe(self, podcast_id: str, when: datetime) -> None:
        self.connection.execute("INSERT OR IGNORE INTO subscription VALUES (?, ?)", (podcast_id, _required_text(when)))
        self.connection.commit()

    def unsubscribe(self, podcast_id: str) -> None:
        self.connection.execute("DELETE FROM subscription WHERE podcast_id=?", (podcast_id,))
        self.connection.commit()

    def _model(self, row: sqlite3.Row) -> Podcast:
        return Podcast(row["id"], row["feed_url"], row["title"], row["author"], row["description"],
                       row["image_url"], row["language"], row["catalog_id"], row["etag"], row["last_modified"],
                       _datetime(row["last_checked_at"]), _datetime(row["last_success_at"]),
                       _datetime(row["created_at"]), _datetime(row["updated_at"]))

    def get(self, podcast_id: str) -> Podcast | None:
        row = self.connection.execute("SELECT * FROM podcast WHERE id=?", (podcast_id,)).fetchone()
        return self._model(row) if row else None

    def get_by_feed_url(self, feed_url: str) -> Podcast | None:
        row = self.connection.execute("SELECT * FROM podcast WHERE feed_url=?", (feed_url,)).fetchone()
        return self._model(row) if row else None

    def is_subscribed(self, podcast_id: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM subscription WHERE podcast_id=?", (podcast_id,)
        ).fetchone() is not None

    def list_subscribed(self) -> list[Podcast]:
        rows = self.connection.execute(
            "SELECT podcast.* FROM podcast JOIN subscription ON podcast.id=subscription.podcast_id ORDER BY subscription.subscribed_at, podcast.id"
        ).fetchall()
        return [self._model(row) for row in rows]


class EpisodeSqliteRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert(self, value: Episode, *, commit: bool = True) -> Episode:
        self.connection.execute(
            """INSERT INTO episode VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(podcast_id, source_key) DO UPDATE SET guid=excluded.guid, media_url=excluded.media_url,
            title=excluded.title, description=excluded.description, published_at=excluded.published_at,
            duration_ms=excluded.duration_ms, media_length_bytes=excluded.media_length_bytes,
            media_type=excluded.media_type, image_url=excluded.image_url, updated_at=excluded.updated_at""",
            (value.id, value.podcast_id, value.source_key, value.guid, value.media_url, value.title,
             value.description, _text(value.published_at), value.duration_ms, value.media_length_bytes,
             value.media_type, value.image_url, _required_text(value.created_at), _required_text(value.updated_at)),
        )
        if commit:
            self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM episode WHERE podcast_id=? AND source_key=?", (value.podcast_id, value.source_key)
        ).fetchone()
        return self._model(row)

    def _model(self, row: sqlite3.Row) -> Episode:
        return Episode(row["id"], row["podcast_id"], row["source_key"], row["media_url"], row["title"],
                       row["guid"], row["description"], _datetime(row["published_at"]), row["duration_ms"],
                       row["media_length_bytes"], row["media_type"], row["image_url"],
                       _datetime(row["created_at"]), _datetime(row["updated_at"]))

    def list_for_podcast(self, podcast_id: str) -> list[Episode]:
        rows = self.connection.execute("SELECT * FROM episode WHERE podcast_id=? ORDER BY id", (podcast_id,)).fetchall()
        return [self._model(row) for row in rows]

    def get(self, episode_id: str) -> Episode | None:
        row = self.connection.execute("SELECT * FROM episode WHERE id=?", (episode_id,)).fetchone()
        return self._model(row) if row else None


class PlaybackSqliteRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, value: Playback) -> None:
        self.connection.execute(
            """INSERT INTO playback VALUES (?, ?, ?, ?, ?) ON CONFLICT(episode_id) DO UPDATE SET
            position_ms=excluded.position_ms, duration_ms=excluded.duration_ms,
            completed=excluded.completed, updated_at=excluded.updated_at""",
            (value.episode_id, value.position_ms, value.duration_ms, int(value.completed), _required_text(value.updated_at)),
        )
        self.connection.commit()

    def get(self, episode_id: str) -> Playback | None:
        row = self.connection.execute("SELECT * FROM playback WHERE episode_id=?", (episode_id,)).fetchone()
        return Playback(row["episode_id"], row["position_ms"], row["duration_ms"], bool(row["completed"]),
                        _datetime(row["updated_at"])) if row else None


class DownloadSqliteRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, value: Download) -> None:
        self.connection.execute(
            """INSERT INTO download VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET state=excluded.state, relative_path=excluded.relative_path,
            temp_relative_path=excluded.temp_relative_path, bytes_received=excluded.bytes_received,
            bytes_total=excluded.bytes_total, etag=excluded.etag, last_modified=excluded.last_modified,
            error_code=excluded.error_code, updated_at=excluded.updated_at, completed_at=excluded.completed_at""",
            (value.episode_id, value.state.value, value.relative_path, value.temp_relative_path,
             value.bytes_received, value.bytes_total, value.etag, value.last_modified, value.error_code,
             _required_text(value.created_at), _required_text(value.updated_at), _text(value.completed_at)),
        )
        self.connection.commit()

    def get(self, episode_id: str) -> Download | None:
        row = self.connection.execute("SELECT * FROM download WHERE episode_id=?", (episode_id,)).fetchone()
        return Download(row["episode_id"], DownloadState(row["state"]), row["relative_path"],
                        row["temp_relative_path"], row["bytes_received"], row["bytes_total"], row["etag"],
                        row["last_modified"], row["error_code"], _datetime(row["created_at"]),
                        _datetime(row["updated_at"]), _datetime(row["completed_at"])) if row else None

    def delete(self, episode_id: str) -> None:
        self.connection.execute("DELETE FROM download WHERE episode_id=?", (episode_id,))
        self.connection.commit()


class SettingsSqliteRepository:
    KNOWN_KEYS = {"theme", "download_limit_bytes", "last_screen"}

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        if key not in self.KNOWN_KEYS:
            raise ValueError("unknown or secret setting key")
        self.connection.execute("INSERT INTO setting VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.connection.commit()


@dataclass
class Repositories:
    database: Database
    podcasts: PodcastSqliteRepository
    episodes: EpisodeSqliteRepository
    playback: PlaybackSqliteRepository
    downloads: DownloadSqliteRepository
    settings: SettingsSqliteRepository

    def persist_feed(self, value, *, subscribe_at: datetime | None = None) -> None:  # type: ignore[no-untyped-def]
        connection = self.database.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            self.podcasts.save(value.podcast, commit=False)
            for episode in value.episodes:
                self.episodes.upsert(episode, commit=False)
            if subscribe_at is not None:
                connection.execute(
                    "INSERT OR IGNORE INTO subscription VALUES (?, ?)",
                    (value.podcast.id, _required_text(subscribe_at)),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @classmethod
    def open(cls, path: Path) -> "Repositories":
        database = Database(path)
        database.migrate()
        connection = database.connection
        connection.row_factory = sqlite3.Row
        return cls(database, PodcastSqliteRepository(connection), EpisodeSqliteRepository(connection),
                   PlaybackSqliteRepository(connection), DownloadSqliteRepository(connection),
                   SettingsSqliteRepository(connection))
