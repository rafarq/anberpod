from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable


class AtomicFiles:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _resolve(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("path is outside data directory")
        return path

    def write_atomic(self, relative_path: str, data: bytes) -> None:
        destination = self._resolve(relative_path)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, raw_temp = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        temp = Path(raw_temp)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, destination)
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp.unlink(missing_ok=True)

    def exists(self, relative_path: str) -> bool:
        return self._resolve(relative_path).is_file()

    def read_bytes(self, relative_path: str) -> bytes:
        return self._resolve(relative_path).read_bytes()

    def size(self, relative_path: str) -> int:
        return self._resolve(relative_path).stat().st_size

    def unlink(self, relative_path: str) -> None:
        self._resolve(relative_path).unlink(missing_ok=True)

    def write_part(
        self,
        relative_path: str,
        chunks: Iterable[bytes],
        *,
        append: bool,
        max_bytes: int,
    ) -> int:
        if not relative_path.endswith(".part"):
            raise ValueError("partial download path must end in .part")
        destination = self._resolve(relative_path)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        existing = destination.stat().st_size if append and destination.exists() else 0
        if existing > max_bytes:
            raise ValueError("partial download exceeds limit")
        total = existing
        with destination.open("ab" if append else "wb") as stream:
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise TypeError("download chunks must be bytes")
                if total + len(chunk) > max_bytes:
                    raise ValueError("download exceeds limit")
                stream.write(chunk)
                total += len(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        return total

    def promote_part(self, temp_relative_path: str, destination_relative_path: str) -> None:
        if not temp_relative_path.endswith(".part"):
            raise ValueError("source is not a partial download")
        source = self._resolve(temp_relative_path)
        destination = self._resolve(destination_relative_path)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.replace(source, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def path(self, relative_path: str) -> Path:
        return self._resolve(relative_path)
