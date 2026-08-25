#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -P "$(dirname "$0")/.." && pwd)
ARCH=""
VERSION=""
OUTPUT=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --arch) ARCH=$2; shift 2 ;;
        --version) VERSION=$2; shift 2 ;;
        --output) OUTPUT=$2; shift 2 ;;
        *) printf '%s\n' "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
if [ -z "$ARCH" ] || [ -z "$VERSION" ]; then
    printf '%s\n' "Usage: build_bundle.sh --arch aarch64 --version VERSION [--output FILE]" >&2
    exit 2
fi
if [ "$ARCH" != "aarch64" ]; then
    printf '%s\n' "Phase 0/1 bundle supports the aarch64 target layout only" >&2
    exit 2
fi
if [ -z "$OUTPUT" ]; then
    OUTPUT="$ROOT/dist/AnberPod-$VERSION-$ARCH.tar.gz"
fi

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT HUP INT TERM
APP="$STAGE/Roms/APPS/AnberPod"
RELEASE="$APP/releases/$VERSION"
mkdir -p "$RELEASE" "$APP/runtime/bin" "$APP/runtime/certs" "$APP/data" "$(dirname "$OUTPUT")"
tar -C "$ROOT" --exclude='__pycache__' --exclude='*.pyc' -cf - src | tar -C "$RELEASE" -xf -
cp "$ROOT/pyproject.toml" "$ROOT/README.md" "$ROOT/requirements.lock" "$RELEASE/"
cp "$ROOT/packaging/muos/AnberPod.sh" "$STAGE/Roms/APPS/AnberPod.sh"
cp "$ROOT/packaging/muos/README-INSTALL.txt" "$APP/README-INSTALL.txt"
cp "$ROOT/packaging/muos/config.example.toml" "$APP/config.example.toml"
ln -s "releases/$VERSION" "$APP/current"
chmod 755 "$STAGE/Roms/APPS/AnberPod.sh"
tar --sort=name --mtime='UTC 2026-01-01' --owner=0 --group=0 --numeric-owner -C "$STAGE" -czf "$OUTPUT" Roms
printf '%s\n' "$OUTPUT"
