from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path


def install_release(source: Path, install_root: Path, version: str) -> Path:
    """Install immutable code and atomically select it without traversing data/."""
    source = source.resolve()
    if not source.is_dir() or not version or "/" in version or version in {".", ".."}:
        raise ValueError("invalid release source or version")
    install_root.mkdir(mode=0o755, parents=True, exist_ok=True)
    (install_root / "data").mkdir(mode=0o700, exist_ok=True)
    releases = install_root / "releases"
    releases.mkdir(mode=0o755, exist_ok=True)
    destination = releases / version
    if destination.exists():
        raise FileExistsError(f"release already installed: {version}")
    temporary_release = releases / f".{version}.new-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, temporary_release)
        os.replace(temporary_release, destination)
        temporary_link = install_root / f".current.new-{uuid.uuid4().hex}"
        temporary_link.symlink_to(Path("releases") / version, target_is_directory=True)
        os.replace(temporary_link, install_root / "current")
    finally:
        if temporary_release.exists():
            shutil.rmtree(temporary_release)
    return destination
