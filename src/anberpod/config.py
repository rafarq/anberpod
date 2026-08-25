from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataPaths:
    root: Path
    database: Path
    downloads: Path
    cache: Path
    imports: Path
    config: Path
    logs: Path

    @classmethod
    def create(cls, root: Path) -> "DataPaths":
        resolved = root.expanduser().resolve()
        resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
        directories = {
            name: resolved / name
            for name in ("db", "downloads", "cache", "imports", "config", "logs")
        }
        for directory in directories.values():
            directory.mkdir(mode=0o700, exist_ok=True)
        return cls(
            root=resolved,
            database=directories["db"] / "anberpod.sqlite3",
            downloads=directories["downloads"],
            cache=directories["cache"],
            imports=directories["imports"],
            config=directories["config"],
            logs=directories["logs"],
        )

    @classmethod
    def from_environment(cls) -> "DataPaths":
        value = os.environ.get("ANBERPOD_DATA_DIR")
        if not value:
            raise RuntimeError("ANBERPOD_DATA_DIR must be an absolute path")
        root = Path(value)
        if not root.is_absolute():
            raise RuntimeError("ANBERPOD_DATA_DIR must be an absolute path")
        return cls.create(root)

    def resolve_relative(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("path is outside data directory")
        return candidate
