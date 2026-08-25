from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path


def test_bundle_is_inspectable_and_contains_no_persistent_payload(tmp_path: Path) -> None:
    archive = tmp_path / "AnberPod-0.1.0-aarch64.tar.gz"

    build = subprocess.run(
        ["./scripts/build_bundle.sh", "--arch", "aarch64", "--version", "0.1.0", "--output", str(archive)],
        cwd=Path.cwd(), capture_output=True, text=True, check=False,
    )
    check = subprocess.run(
        ["./scripts/check_bundle.sh", str(archive)], cwd=Path.cwd(), capture_output=True, text=True, check=False,
    )

    assert build.returncode == 0, build.stderr
    assert check.returncode == 0, check.stderr
    with tarfile.open(archive, "r:gz") as bundle:
        names = bundle.getnames()
        assert "Roms/APPS/AnberPod.sh" in names
        assert "Roms/APPS/AnberPod/releases/0.1.0/src/anberpod/__main__.py" in names
        assert "Roms/APPS/AnberPod/data" in names
        assert not any(name.endswith((".sqlite3", ".part", ".mp3", ".m4a", ".ogg")) for name in names)
        assert not any("data/" in name for name in names)
