from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from anberpod.adapters.sqlite import Repositories
from anberpod.config import DataPaths
from anberpod.domain.models import DownloadState


@dataclass(frozen=True)
class RecoveryReport:
    interrupted_downloads: int = 0
    discarded_cache_entries: int = 0
    abandoned_partials_removed: int = 0


def recover_local_state(paths: DataPaths, repos: Repositories) -> RecoveryReport:
    interrupted = 0
    rows = repos.database.connection.execute("SELECT episode_id FROM download WHERE state='downloading'").fetchall()
    for row in rows:
        download = repos.downloads.get(row[0])
        if download is None:
            continue
        repos.downloads.save(replace(
            download,
            state=DownloadState.FAILED,
            relative_path=None,
            error_code="interrupted",
            updated_at=datetime.now(timezone.utc),
        ))
        interrupted += 1

    discarded = 0
    cache_rows = repos.database.connection.execute(
        "SELECT cache_key, payload_relative_path FROM catalog_cache"
    ).fetchall()
    for cache_key, relative_path in cache_rows:
        path = paths.resolve_relative(relative_path)
        try:
            with path.open("rb") as stream:
                json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            repos.database.connection.execute("DELETE FROM catalog_cache WHERE cache_key=?", (cache_key,))
            discarded += 1
    repos.database.connection.commit()
    referenced = {
        row[0]
        for row in repos.database.connection.execute(
            "SELECT temp_relative_path FROM download WHERE temp_relative_path IS NOT NULL"
        ).fetchall()
    }
    removed = 0
    partial_root = paths.root / "download-parts"
    if partial_root.exists():
        for path in partial_root.glob("*.part"):
            relative_path = path.relative_to(paths.root).as_posix()
            if relative_path not in referenced:
                path.unlink(missing_ok=True)
                removed += 1
    return RecoveryReport(interrupted, discarded, removed)
