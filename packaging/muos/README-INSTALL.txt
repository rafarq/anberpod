AnberPod phase 0-4 foundation
==============================

Copy AnberPod.sh beside the AnberPod directory under Roms/APPS. Code releases
live below AnberPod/releases and `current` selects one release. Persistent state
lives only in AnberPod/data and must not be copied from an update archive.

For Podcast Index discovery, copy config.example.toml to
AnberPod/data/config/config.toml, fill in api_key and api_secret under
[podcast_index], and run `chmod 600` on that user-owned file when supported.
The example and release contain no real credentials.

Place a licensed static AArch64 ffmpeg build at
AnberPod/runtime/bin/ffmpeg and set mode 0755. It must support HTTPS/TLS and the
required podcast codecs. The launcher exports this as ANBERPOD_FFMPEG;
ANBERPOD_APLAY selects the system ALSA aplay command. Host tests exercise only
fake processes. Complete the mandatory real-device audible test in README.md
before declaring playback verified.

Podcast Index discovery and explicit RSS feed requests use bounded public
HTTP(S). A device bundle must also provide Python 3.10+, Pillow and the
validated MuOS SDL/input profile.
