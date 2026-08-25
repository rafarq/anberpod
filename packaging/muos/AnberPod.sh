#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
APP_ROOT="$SCRIPT_DIR/AnberPod"
DATA_DIR="$APP_ROOT/data"
PYTHON_BIN=${ANBERPOD_PYTHON:-"$APP_ROOT/current/bin/python3"}
BOOT_LOG="$DATA_DIR/logs/launcher.log"

mkdir -p "$DATA_DIR/db" "$DATA_DIR/downloads" "$DATA_DIR/cache" "$DATA_DIR/imports" \
    "$DATA_DIR/config" "$DATA_DIR/logs"
chmod 700 "$DATA_DIR" "$DATA_DIR/db" "$DATA_DIR/downloads" "$DATA_DIR/cache" \
    "$DATA_DIR/imports" "$DATA_DIR/config" "$DATA_DIR/logs" 2>/dev/null || true

if [ ! -x "$PYTHON_BIN" ]; then
    printf '%s\n' "AnberPod: Python runtime is missing or not executable: $PYTHON_BIN" >>"$BOOT_LOG"
    exit 2
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    printf '%s\n' "AnberPod: Python 3.10 or newer is required" >>"$BOOT_LOG"
    exit 3
fi

export ANBERPOD_DATA_DIR="$DATA_DIR"
export ANBERPOD_FFMPEG=${ANBERPOD_FFMPEG:-"$APP_ROOT/runtime/bin/ffmpeg"}
export ANBERPOD_APLAY=${ANBERPOD_APLAY:-aplay}
export PYTHONPATH="$APP_ROOT/current/src:$APP_ROOT/current/vendor"
export SDL_VIDEODRIVER=${SDL_VIDEODRIVER:-kmsdrm}
export SDL_AUDIODRIVER=${SDL_AUDIODRIVER:-alsa}

exec "$PYTHON_BIN" -m anberpod "$@" >>"$BOOT_LOG" 2>&1
