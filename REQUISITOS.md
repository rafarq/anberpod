# AnberPod — Confirmed Requirements

## Product
Native podcast player for the Anbernic RG35XX H with MuOS, installed from the `APPS` menu. It must be able to discover podcasts, subscribe to them, listen to episodes, and keep each episode's playback progress locally on the device only.

## MVP Scope

1. **Home**: access to Explore, Search, Subscriptions, Downloads, and Settings.
2. **Discovery**: categories, text search, and results sourced from Podcast Index.
3. **Direct RSS**: add an RSS feed URL as an additional source, validate it, and fetch its metadata and episodes.
4. **Subscriptions**: local subscribe/unsubscribe; per-podcast episode lists and on-demand refresh.
5. **Playback**: HTTPS streaming; play/pause/stop; seek forward/backward; save position with resume.
6. **Offline downloads**: manual per-episode download with state and size; prefer the local file when available; manual deletion. No automatic deletion or automatic downloads.
7. **Local persistence**: subscriptions, playback positions, downloads, and caches live on the SD card. App updates must never overwrite this data.

## Interaction and hardware

- Hardware: Anbernic RG35XX H.
- Firmware: MuOS.
- Logical screen resolution: 640×480.
- Physical-button navigation: D-pad, A to accept/play, B to go back, MENU to exit.
- Must work without an on-console keyboard. Entering an RSS URL can be done via a documented import file on the SD card.

## External sources

- Catalog and categories: Podcast Index, using credentials from a local file outside the repository or user configuration.
- Direct RSS: public feeds supplied by the user.
- HTTP(S) with TLS verification, time/size limits, XML validation, and atomic caching.

## Technical constraints

- Python 3.10, PySDL2, and Pillow compatible with MuOS.
- Must not depend on any media player bundled with the firmware.
- Bundle a configurable static ARM64 `ffmpeg`, or document where the binary must be placed; decode to PCM played through ALSA/aplay.
- The app must be able to start offline while showing valid local data.
- The repository contains no credentials, private podcasts, or downloads.

## Out of initial scope

- User accounts, cross-device sync, and analytics.
- Personalized recommendations.
- Automatic downloads, queues, and variable-speed playback.

## Acceptance criteria

- A user can browse categories, search, open a podcast, and subscribe.
- Can import an RSS feed, subscribe, and see its episodes.
- Can play a remote episode, stop, and resume from the saved position.
- Can manually download an episode, turn off the network, and play it from the SD card.
- Can delete a download without losing the subscription or history.
- The app runs from `Roms/APPS`, logs startup and errors, and never overwrites local state on update.
