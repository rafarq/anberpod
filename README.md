# AnberPod

AnberPod currently implements phases 0–4 of `PLAN.md`: the executable offline
foundation, transactional SQLite library, bounded public HTTP(S), hardened RSS
parsing, explicit subscriptions and refresh, Podcast Index discovery/cache,
downloads, physical navigation state, deterministic 640×480 rendering, an
update-safe MuOS layout, and playback orchestration.

Playback uses a mockable controller and an isolated
`ffmpeg` → raw S16LE/48 kHz/stereo PCM → `aplay` adapter. It resumes saved
positions, prefers a size-verified complete local download, supports
pause/resume, stop and fixed −15/+15 second seeking, and writes periodic
progress checkpoints at most once per 10 seconds of playing time.

Run the host diagnostic and create review screens with Python 3.11:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
PYTHONPATH=src .venv/bin/python -m anberpod \
  --data-dir "$PWD/.local-data" --render-dir screenshots --demo
```

To enable discovery, copy `packaging/muos/config.example.toml` to
`data/config/config.toml`, fill in `[podcast_index]`, and restrict the file to
the owning user (`chmod 600`) where supported. Credentials are never stored in
SQLite, URLs, cache payloads, or logs.

The runtime remains compatible with Python 3.10. Persistent state is always
under the absolute `ANBERPOD_DATA_DIR`, outside replaceable release code. Tests
use synthetic network responses and fake playback processes: host tests never
run `ffmpeg`, invoke `aplay`, access an audio device, or require Internet.

## Required ARM64 ffmpeg placement

This repository intentionally does not redistribute an ffmpeg executable.
Before installing on the RG35XX H, supply a licensed static **AArch64/ARM64**
build with HTTPS/TLS and the podcast codecs required by `PLAN.md`, retain its
license and provenance, and place it exactly here:

```text
Roms/APPS/AnberPod/runtime/bin/ffmpeg
```

Set mode `0755`. The MuOS launcher exports that absolute path as
`ANBERPOD_FFMPEG`; set that variable before launch only to select another
bundled path. `ANBERPOD_APLAY` may select the system `aplay` command or its
absolute system path. AnberPod never falls back to a firmware media player.

Verify the binary on the console itself, not on the development host:

```sh
cd /actual/muos/path/Roms/APPS/AnberPod
test -x runtime/bin/ffmpeg
file runtime/bin/ffmpeg
./runtime/bin/ffmpeg -hide_banner -version
./runtime/bin/ffmpeg -hide_banner -protocols
command -v aplay
aplay -l
```

## Mandatory on-device audible playback test

Host tests cannot prove that the MuOS ALSA device, ARM64 binary, codecs, TLS, or
physical buttons work. This exact test remains mandatory on a real RG35XX H
running the target MuOS release:

1. From MuOS **APPS**, start AnberPod with headphones or the speaker at a safe
   audible volume and networking enabled.
2. Open a podcast containing a known-audible HTTPS episode at least 60 seconds
   long, highlight the episode row, and press **A**. Confirm speech/music is
   audible from the expected output and Player says `Source: Streaming`.
3. After at least 12 seconds, press **A**. Confirm silence and that the displayed
   position does not advance for 10 seconds. Press **A** and confirm audio
   resumes.
4. Press **RIGHT** once and confirm the position advances exactly 15 seconds and
   later content is audible. Press **LEFT** once and confirm it moves back
   exactly 15 seconds. Press **B**; confirm silence, then use
   `pgrep -f 'ffmpeg|aplay'` to confirm no AnberPod children remain.
5. Start the same episode, listen for at least 12 seconds, then press **MENU**.
   Relaunch and press **A** on that episode. Confirm audible playback resumes
   within 10 seconds of the pre-exit position.
6. With a complete, size-matching download of that episode present, disable all
   networking, relaunch, and press **A** on it. Confirm Player says
   `Source: Downloaded` and the same audio is audible. A `.part`, missing, or
   wrong-size file must never be selected as local media.

Record the device model, MuOS version, ALSA device, ffmpeg SHA-256/version,
episode URL/license, and each pass/fail result. Until this passes, audible
playback is not verified or release-ready.

## Remaining MVP scope

Remaining work includes the bundled ARM64 Python/ffmpeg/CA runtime and
validation of SDL key codes, display, ALSA, and long-running behavior on a
physical RG35XX H. The ffmpeg binary itself is deliberately absent from this
tree. The command-line entry point remains a headless diagnostic/review path;
it does not attempt audio. Playback is wired into `Application` and episode
rows enter Player, ready for the eventual SDL runtime loop.
