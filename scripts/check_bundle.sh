#!/bin/sh
set -eu

ARCHIVE=${1:-}
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    printf '%s\n' "Usage: check_bundle.sh ARCHIVE" >&2
    exit 2
fi

LIST=$(tar -tzf "$ARCHIVE")
printf '%s\n' "$LIST" | grep -qx 'Roms/APPS/AnberPod.sh'
printf '%s\n' "$LIST" | grep -q '^Roms/APPS/AnberPod/releases/[^/]*/src/anberpod/__main__.py$'
printf '%s\n' "$LIST" | grep -qx 'Roms/APPS/AnberPod/data/'
if printf '%s\n' "$LIST" | grep -Eqi '(^|/)(data/.+|[^/]+\.(sqlite3|part|mp3|m4a|ogg))$'; then
    printf '%s\n' "Bundle contains persistent data or media" >&2
    exit 1
fi
if tar -xOzf "$ARCHIVE" Roms/APPS/AnberPod.sh | grep -Eqi '(api_secret|authorization|BEGIN .*PRIVATE KEY)'; then
    printf '%s\n' "Launcher contains secret material" >&2
    exit 1
fi
printf '%s\n' "Bundle layout is safe and inspectable"
