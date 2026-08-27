#!/bin/sh
# Launcher for the RG35XX H firmware menu.
#
# Expected install layout on the SD card:
#   /mnt/mmc/Roms/APPS/AnberPod.sh
#   /mnt/mmc/Roms/APPS/AnberPod/            (this script's sibling directory)
#
# FAT32 does not always preserve the executable bit on files copied via
# USB/card reader, so this script re-asserts execute permission on the
# bundled ffmpeg engine binary on every launch rather than assuming it
# survived the copy.
#
# PYSDL2_DLL_PATH=/usr/lib mirrors the known-good sibling Radio app on
# this same device: without it PySDL2 can fail to locate the system
# SDL2 shared library before the frame loop ever gets a chance to
# create a window. No SDL_VIDEODRIVER/SDL_AUDIODRIVER override is
# forced, matching what is actually confirmed working on this unit;
# SDL picks its own video driver, and audio never goes through SDL
# (ffmpeg -> aplay is a separate, isolated pipeline).
#
# All stdout/stderr is appended to data/logs/launcher.log so on-device
# crashes -- otherwise invisible, since the firmware menu gives no
# console -- are diagnosable after the fact. This script also brackets
# every run with its own timestamped launch/exit marker lines.

set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
APP_ROOT="$SCRIPT_DIR/AnberPod"
DATA_DIR="$APP_ROOT/data"
if [ -n "${ANBERPOD_PYTHON:-}" ]; then
    PYTHON_BIN="$ANBERPOD_PYTHON"
elif [ -x "$APP_ROOT/current/bin/python3" ]; then
    PYTHON_BIN="$APP_ROOT/current/bin/python3"
else
    PYTHON_BIN=$(command -v python3 || echo /usr/bin/python3)
fi
BOOT_LOG="$DATA_DIR/logs/launcher.log"
ENGINE="$APP_ROOT/runtime/bin/ffmpeg"

mkdir -p "$DATA_DIR/db" "$DATA_DIR/downloads" "$DATA_DIR/cache" "$DATA_DIR/imports" \
    "$DATA_DIR/config" "$DATA_DIR/logs"
chmod 700 "$DATA_DIR" "$DATA_DIR/db" "$DATA_DIR/downloads" "$DATA_DIR/cache" \
    "$DATA_DIR/imports" "$DATA_DIR/config" "$DATA_DIR/logs" 2>/dev/null || true

if [ -f "$ENGINE" ]; then
    chmod +x "$ENGINE" 2>/dev/null || true
fi

echo "===== AnberPod.sh launch marker: $(date -u +%Y-%m-%dT%H:%M:%SZ) =====" >>"$BOOT_LOG"

if [ ! -x "$PYTHON_BIN" ]; then
    printf '%s\n' "AnberPod: Python runtime is missing or not executable: $PYTHON_BIN" >>"$BOOT_LOG"
    exit 2
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    printf '%s\n' "AnberPod: Python 3.10 or newer is required" >>"$BOOT_LOG"
    exit 3
fi

export ANBERPOD_DATA_DIR="$DATA_DIR"
export ANBERPOD_FFMPEG=${ANBERPOD_FFMPEG:-"$ENGINE"}
export ANBERPOD_APLAY=${ANBERPOD_APLAY:-aplay}
export ANBERPOD_CA_BUNDLE=${ANBERPOD_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}
export PYTHONPATH="$APP_ROOT/current/src:$APP_ROOT/current/vendor"
export PYSDL2_DLL_PATH=/usr/lib

set +e
"$PYTHON_BIN" -m anberpod "$@" >>"$BOOT_LOG" 2>&1
STATUS=$?
set -e

echo "===== AnberPod.sh exit marker: $(date -u +%Y-%m-%dT%H:%M:%SZ) status=$STATUS =====" >>"$BOOT_LOG"

exit "$STATUS"
