from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from anberpod.packaging import install_release


def _manifest(root: Path) -> dict[str, tuple[str, int]]:
    return {
        str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_simulated_update_preserves_data_byte_for_byte(tmp_path: Path) -> None:
    install = tmp_path / "Roms" / "APPS" / "AnberPod"
    release_one = tmp_path / "release-one"
    release_two = tmp_path / "release-two"
    release_one.mkdir()
    release_two.mkdir()
    (release_one / "version.txt").write_text("0.1.0", encoding="utf-8")
    (release_two / "version.txt").write_text("0.1.1", encoding="utf-8")
    install_release(release_one, install, "0.1.0")
    for relative, payload in {
        "db/anberpod.sqlite3": b"database-sentinel",
        "downloads/episode.bin": b"download-sentinel",
        "cache/item": b"cache-sentinel",
        "imports/rss_urls.txt": b"https://example.test/feed\n",
        "config/config.toml": b"local-only=true\n",
        "logs/anberpod.log": b"log-sentinel\n",
    }.items():
        path = install / "data" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    before = _manifest(install / "data")

    install_release(release_two, install, "0.1.1")

    assert _manifest(install / "data") == before
    assert (install / "current" / "version.txt").read_text(encoding="utf-8") == "0.1.1"
    assert (install / "releases" / "0.1.0" / "version.txt").is_file()


def test_muos_launcher_works_from_path_with_spaces_and_other_cwd(tmp_path: Path) -> None:
    apps = tmp_path / "SD Card" / "Roms" / "APPS"
    install = apps / "AnberPod"
    release = tmp_path / "app-release"
    release.mkdir()
    shutil.copytree(Path.cwd() / "src", release / "src")
    install_release(release, install, "0.1.0")
    apps.mkdir(parents=True, exist_ok=True)
    launcher = apps / "AnberPod.sh"
    shutil.copy2(Path.cwd() / "packaging" / "muos" / "AnberPod.sh", launcher)
    launcher.chmod(0o755)
    environment = os.environ.copy()
    environment["ANBERPOD_PYTHON"] = sys.executable

    result = subprocess.run(
        [str(launcher), "--diagnostic"], cwd="/", env=environment, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert (install / "data" / "logs" / "anberpod.log").is_file()
    assert (install / "data" / "cache" / "diagnostic-home.png").is_file()
