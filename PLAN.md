# MVP Plan: AnberPod for RG35XX H / MuOS

## 1. Goal, boundaries, and definition of done

Build, in phases, a native podcast application that runs from `Roms/APPS` on an Anbernic RG35XX H with MuOS, with a PySDL2 interface at 640×480, exclusive control via physical buttons, and durable state on the SD card. The MVP allows browsing and searching Podcast Index, importing public RSS feeds, subscribing locally, updating on demand, playing over HTTPS, resuming, downloading, and playing without a network connection.

The MVP is only considered done once the automated host tests and the hardware validation checklist in section 13 both pass. Each phase must leave behind a runnable, verifiable increment; no later phase should be required to test an earlier one.

Explicitly out of scope for the MVP: accounts, synchronization, analytics or telemetry, personalized recommendations, private or authenticated feeds, automatic downloads, automatic deletion, queues, variable playback speed, an on-console keyboard, and any media player bundled with the firmware. The repository and published artifacts will not contain credentials, private feeds, or downloaded episodes.

## 2. Scope and architecture decisions

- A single Python 3.10 process renders the interface and coordinates services. Blocking work for networking, downloading, feed parsing, and process control runs outside the SDL thread and delivers events to a bounded queue.
- SQLite is the local source of truth for the seen catalog, subscriptions, episodes, progress, and download state. Large files live outside SQLite. The database uses foreign keys, transactions, WAL, and incremental migrations.
- The data directory is independent of the versioned code. The launcher passes an absolute `ANBERPOD_DATA_DIR` path; upgrading a version never copies, clears, or replaces that directory.
- Podcast Index is used exclusively for categories, search, and discovery metadata. A subscription retains the canonical feed URL and is refreshed by reading RSS on demand, so it remains useful without the catalog.
- Remote and local audio is decoded with a static ARM64 `ffmpeg` binary at a configurable path, into signed little-endian 16-bit PCM at 48 kHz, two channels. The PCM is delivered to ALSA via `aplay`; mpv, VLC, or any other firmware player is never invoked.
- Local playback takes priority when a download marked `complete` exists and its size and file match. Otherwise the remote HTTPS URL is used. A `.part` file is never played.
- The first version supports public RSS 2.0 and Atom, with common podcast namespaces (iTunes and the Podcast Namespace) only in the fields the product needs. HTML, OPML, and authenticated feeds are out of scope.
- Valid local state is always shown first. Lack of network connectivity produces a non-modal notice and does not prevent entering Subscriptions, Downloads, Settings, or playing already-complete files.

## 3. Planned repository layout

The future implementation will follow this structure; this document does not yet create any of these modules:

```text
anberpod/
├── pyproject.toml
├── requirements.lock
├── requirements-dev.lock
├── src/anberpod/
│   ├── __main__.py                 # composition and startup
│   ├── app.py                      # application loop and coordination
│   ├── domain/
│   │   ├── models.py               # entities and enums, no SDL/network/SQLite
│   │   ├── ports.py                # mockable Protocols
│   │   └── errors.py               # typed errors for UI/logging
│   ├── services/
│   │   ├── discovery.py            # Podcast Index use cases
│   │   ├── feeds.py                # import/refresh/subscribe
│   │   ├── downloads.py            # download state machine
│   │   └── playback.py             # local/remote selection and progress
│   ├── adapters/
│   │   ├── podcast_index.py
│   │   ├── http.py                 # HTTPS transport and shared policy
│   │   ├── rss.py                  # restricted XML parsing
│   │   ├── sqlite.py               # repositories and migrations
│   │   ├── filesystem.py           # writes, fsync, and atomic replace
│   │   ├── ffmpeg_aplay.py
│   │   └── sdl_input.py
│   ├── ui/
│   │   ├── state.py                # routes, focus, and view-models
│   │   ├── screens.py              # 640×480 screens
│   │   ├── widgets.py              # lists, dialogs, and virtual keyboard
│   │   ├── renderer.py             # PySDL2/Pillow
│   │   └── assets/                 # redistributable fonts and images
│   └── migrations/
│       ├── 001_initial.sql
│       └── ...
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── fixtures/                   # minimal RSS/XML/audio, all public/synthetic
│   └── hardware/                   # script/manual and diagnostics collector
├── packaging/muos/
│   ├── AnberPod.sh
│   ├── README-INSTALL.txt
│   └── config.example.toml
├── scripts/
│   ├── build_bundle.sh
│   ├── check_bundle.sh
│   └── deploy_sd.sh
└── docs/
    ├── RSS-IMPORT.md
    └── HARDWARE-VALIDATION.md
```

`requirements.lock` will pin versions compatible with Python 3.10, including PySDL2, Pillow, and a hardened XML parser; no dependency requiring compilation on the console will be accepted. Domain tests will not import SDL, open network connections, or play real audio.

## 4. Mockable contracts

Ports will be expressed as `typing.Protocol` and will use domain models, not objects from external libraries:

- `Clock.now_utc() -> datetime` and `MonotonicClock.seconds() -> float`: deterministic timestamps, cache expiration, and progress tracking.
- `PodcastCatalog.categories()`, `search(query, limit)`, and `podcast(feed_id)`: catalog decoupled from Podcast Index.
- `HttpTransport.request(RequestPolicy, url, headers) -> HttpResponse`: the single HTTP boundary; supports a fake transport with scripted response sequences and redirects.
- `FeedReader.fetch(url, validators) -> FeedFetchResult` and `parse(bytes, source_url) -> ParsedFeed`: HTTP validation kept separate from XML parsing.
- `PodcastRepository`, `EpisodeRepository`, `PlaybackRepository`, `DownloadRepository`, and `SettingsRepository`: transactional operations with in-memory doubles.
- `AtomicFiles.commit_temp(temp, destination)`, `exists`, `size`, `unlink`: a swappable filesystem abstraction; `unlink` only ever receives paths already resolved within the data directory.
- `DownloadRunner.start(job)`, `cancel(id)`, and `events()`: chunked downloading and progress events without coupling the UI to threads.
- `PlaybackEngine.play(source, start_seconds)`, `pause`, `resume`, `seek_relative`, `stop`, `events()`, and `shutdown`: the ffmpeg/aplay process, swappable for a deterministic engine.
- `InputSource.poll() -> list[InputEvent]`: SDL isolated from navigation.
- `ConnectivityProbe.is_online()`: a UI hint; never a substitute for real error handling.
- `CredentialProvider.podcast_index()`: reads secrets only from local configuration and allows a fake in tests.
- `Logger`: logs structured messages without secrets, sensitive query strings, or headers.

Use cases will receive these ports through their constructors. Contract tests will run the same battery of tests against both SQLite repositories and their doubles, to prevent the mock from having different semantics.

## 5. Local data, schema, and durability

Proposed stable path on the SD card:

```text
Roms/APPS/AnberPod/
├── current -> releases/<version>/       # or a copy selected by the installer
├── releases/<version>/                  # replaceable code
├── runtime/bin/ffmpeg                    # static ARM64, replaceable
└── data/                                 # never included in nor deleted by an update
    ├── db/anberpod.sqlite3
    ├── downloads/<episode_uuid>.<ext>
    ├── cache/images/
    ├── cache/http/
    ├── imports/rss_urls.txt
    ├── imports/rss_urls.result.txt
    ├── config/config.toml
    └── logs/anberpod.log
```

If MuOS or the SD card's filesystem does not support symbolic links, `current/` will be a replaceable directory; the launcher will still resolve `data/` as a sibling, never as a child. The installer creates missing data but aborts before touching existing data. Logs rotate locally with a documented maximum (by default, 3 files of 1 MiB each).

Exact initial schema, with UTC timestamps as RFC 3339 text and durations/positions as integer milliseconds:

| Table | Relevant columns and constraints |
|---|---|
| `schema_migration` | `version INTEGER PRIMARY KEY`, `applied_at TEXT NOT NULL` |
| `podcast` | `id TEXT PRIMARY KEY` (local UUID), `feed_url TEXT NOT NULL UNIQUE`, `catalog_id INTEGER NULL`, `title TEXT NOT NULL`, `author TEXT`, `description TEXT`, `image_url TEXT`, `language TEXT`, `etag TEXT`, `last_modified TEXT`, `last_checked_at TEXT`, `last_success_at TEXT`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL` |
| `subscription` | `podcast_id TEXT PRIMARY KEY REFERENCES podcast(id) ON DELETE CASCADE`, `subscribed_at TEXT NOT NULL` |
| `episode` | `id TEXT PRIMARY KEY` (stable local UUID), `podcast_id TEXT NOT NULL REFERENCES podcast(id) ON DELETE CASCADE`, `source_key TEXT NOT NULL`, `guid TEXT`, `media_url TEXT NOT NULL`, `title TEXT NOT NULL`, `description TEXT`, `published_at TEXT`, `duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0)`, `media_length_bytes INTEGER CHECK(media_length_bytes IS NULL OR media_length_bytes >= 0)`, `media_type TEXT`, `image_url TEXT`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL`, `UNIQUE(podcast_id, source_key)` |
| `playback` | `episode_id TEXT PRIMARY KEY REFERENCES episode(id) ON DELETE CASCADE`, `position_ms INTEGER NOT NULL DEFAULT 0 CHECK(position_ms >= 0)`, `duration_ms INTEGER`, `completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1))`, `updated_at TEXT NOT NULL` |
| `download` | `episode_id TEXT PRIMARY KEY REFERENCES episode(id) ON DELETE CASCADE`, `state TEXT NOT NULL CHECK(state IN ('queued','downloading','complete','failed'))`, `relative_path TEXT`, `temp_relative_path TEXT`, `bytes_received INTEGER NOT NULL DEFAULT 0 CHECK(bytes_received >= 0)`, `bytes_total INTEGER`, `etag TEXT`, `last_modified TEXT`, `error_code TEXT`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL`, `completed_at TEXT`, with checks requiring a final path only for `complete` |
| `catalog_cache` | `cache_key TEXT PRIMARY KEY`, `payload_relative_path TEXT NOT NULL`, `fetched_at TEXT NOT NULL`, `expires_at TEXT NOT NULL`, `etag TEXT`, `last_modified TEXT` |
| `setting` | `key TEXT PRIMARY KEY`, `value TEXT NOT NULL`, restricted by the repository to known, non-secret keys |

`source_key` is derived, in order, from a non-empty `guid`, the normalized enclosure URL, or, as a last resort, a hash of title+date+URL; this way an update performs an upsert instead of duplicating episodes. Unsubscribing removes only the `subscription` row: it does not remove the podcast, episodes, progress, or downloads. Manually deleting a download removes only its file and the `download` row, not the subscription, episode, or `playback`.

Each migration runs in a transaction and first creates a bounded backup of the database. If it fails, the app keeps the previous database, logs the error, and does not start in write mode. Caches and downloads are written to a temporary file in the same directory, flushed with `fsync`, validated, and published via atomic replace. On startup, `.part` files are treated as failed/resumable downloads; they are never presented as complete. The cache may be discarded if corrupted, but the application retains the last valid local record.

## 6. Safety rules for Podcast Index, HTTP, and RSS

### Podcast Index credentials

- `data/config/config.toml`, outside the repository and with `0600` permissions where the system allows it, holds `api_key` and `api_secret`; the package only ships empty example field names.
- For each request an `X-Auth-Date` is generated from `Clock`, and `Authorization = SHA1(api_key + api_secret + X-Auth-Date)` per the Podcast Index contract; `X-Auth-Key` and a stable `User-Agent` are also sent. The secret is never persisted in SQLite, logs, errors, fixtures, or URLs.
- If credentials are missing, Explore/Search explains how to configure them, while RSS, the local library, downloads, and playback remain available.
- An invalid clock, 401/403, 429, and 5xx are all typed errors. For 429, a bounded `Retry-After` is honored, with no automatic retry loop from the UI; potentially sensitive bodies are never logged.

### Common HTTP(S) policy

- Only `https` is used for Podcast Index, images, and remote media. For public RSS, both `https` and, for compatibility with existing feeds, `http` are accepted; the UI flags an HTTP feed as an unencrypted connection before confirming. A chain that started on HTTPS can never downgrade to HTTP. Scheme, port, host, and destination are re-validated after every redirect.
- Only the default ports 443/80 or an explicit port from the RSS URL are allowed; URLs with user/password, fragments, an empty host, or a literal local IP are blocked.
- To reduce SSRF risk from user-supplied RSS, loopback, link-local, multicast, unspecified, and private/reserved IPv4/IPv6 ranges — and any hostname resolving to them — are rejected before connecting and on every redirect. Redirects are capped at 5, and protection against DNS rebinding uses the addresses validated by the transport.
- TLS uses the bundled/documented CA store, verifies the certificate and hostname, and offers no "insecure" mode. A TLS or certificate failure never falls back to HTTP.
- Default timeouts: 10 s connect, 20 s read, and 60 s total for API/RSS/images. Audio downloads use a 10 s connect timeout, a 30 s inactivity timeout, and no short total timeout, but are cancelable.
- Maximums: 2 MiB per catalog JSON response, 5 MiB per RSS/XML document, 4 MiB per image, and a configurable per-episode download limit (2 GiB by default) plus a free-space check. The stream is cut off once the limit is exceeded, even if `Content-Length` is missing or wrong.
- Compressed responses are accepted only with a limit on decompressed bytes. Methods are restricted to GET and HEAD, parameters are encoded, and queries are never concatenated manually.
- JSON is validated for shape, types, and a maximum result count before being persisted. XML is processed with external entities, DTDs, entity expansion, and network access disabled; the parser operates on the already-bounded body. Feed and enclosure must pass semantic validation.
- ETag/Last-Modified are used for conditional requests. A 304 keeps the previous version. A new response only replaces the cache and data inside a transaction, and only after full validation.
- The cache has an explicit TTL per type. An expired response may be shown as "saved data" if the network fails, never as a fresh update.
- File names are derived from the local UUID, not from title or URL. All paths are resolved and verified under `data/`; paths coming from the feed are never followed.

## 7. RSS import and updates

`docs/RSS-IMPORT.md` will document a keyboard-free workflow: power off or eject the SD card (or access it by whatever means is available), add one HTTPS URL per line to `data/imports/rss_urls.txt`, reinsert the SD card, and start AnberPod. Blank lines and `#` comments are ignored; the file is capped at 100 lines and 2048 characters per URL. The file is read on demand from Settings > Import RSS, not silently at startup.

Each URL is normalized, passed through the SSRF policy, downloaded and parsed, and a preview is shown before subscribing. Per-line results (`OK`, `DUPLICATE`, or an error code with no credentials) are written atomically to `rss_urls.result.txt`; the source file is not deleted. A duplicate URL opens the existing podcast. A failure on one line does not roll back other valid imports.

"Update" exists on a podcast's detail screen and in Subscriptions to refresh all of them sequentially, with cancellation. It is on-demand only. There is no update timer, no implicit download, and no network activity when simply entering a screen.

## 8. Offline downloads

The "Download" action creates a `queued` row only after checking the HTTPS URL, the configured limit, and free space (known size plus a margin; if unknown, the configured maximum). A single worker moves it to `downloading`, writes `<uuid>.part` in chunks, enforces the actual size limit, and emits progress to the UI. v1 uses a single concurrent download to avoid memory, storage, and network pressure.

On completion, the worker flushes the file, checks that bytes were received, validates the container with the bundled `ffmpeg` without fully decoding it, and atomically renames it to `<uuid>.<safe-ext>`; only then is it marked `complete`. Errors, cancellation, insufficient space, invalid media, or a restart all preserve diagnostics and never create a false "complete" state. When the server and validators allow it, "Retry" resumes with `Range` and requires a coherent 206/`Content-Range` response; on any ambiguity, the `.part` file is restarted from scratch.

Deletion requires confirmation, stops playback of that file (or is rejected while it's in use), removes only the file/temp file and the download row in a restart-tolerant operation, and preserves the subscription, metadata, and progress. There is no automatic cleanup based on age, space, or updates.

## 9. Playback and progress persistence

`PlaybackService` prefers a complete local file over HTTPS media. For remote playback, it re-validates the URL and launches the bundled `ffmpeg` with a whitelist of protocols and bounded reconnect/timeout options; for local playback, it passes a verified path under `data/downloads`. Arguments are passed as a list, never through a shell. `ffmpeg` writes PCM to stdout and `aplay` receives that PCM on stdin; stderr is captured within bounds for diagnostics, and neither process inherits secrets.

A session has the states `idle`, `buffering`, `playing`, `paused`, `stopped`, `ended`, and `error`. A plays/pauses; stop is an explicit action on the playback panel; left/right skip −15/+30 seconds, bounded between zero and the known duration. Seek restarts the pipeline in a controlled way using `-ss` at the requested position. Stop, error, and exit terminate both processes with a deadline, followed by controlled escalation, leaving no orphans.

Position is obtained from the monotonic clock and engine events, not from SDL frames. It is persisted in a transaction every 10 seconds during playback, and also on pause, seek, stop, receiving MENU, ending, or a normal exit. Writes are batched to avoid punishing the SD card. On finishing, `completed=1` is set; reopening a completed episode restarts from zero only after confirmation. A position greater than the duration, or a changing media length, is clamped. A power cut can lose at most the last 10-second interval, never corrupt confirmed state.

If streaming fails, the UI keeps the last confirmed position and offers a retry; it does not download automatically. Pause verifiably suspends the audio stream without advancing the counter. Switching between local and remote does not change the same episode's progress row.

## 10. Input, navigation, and the 640×480 interface

The UI uses a fixed logical resolution of 640×480 and scales while preserving aspect ratio. All important text is rendered with bundled fonts and Pillow/PySDL2, with high contrast, visible focus, ellipsis truncation, and list scrolling; no action requires hover, touch, or a keyboard.

Global control map:

| Control | Action |
|---|---|
| D-pad up/down | move focus or list item |
| D-pad left/right | change tab/value; in the player, −15/+30 s |
| A | confirm; on episode/player screens, play/pause depending on context |
| B | go back one screen; close a dialog without confirming |
| MENU | save progress, cleanly stop processes, and exit from any screen |

Key-down is normalized and accidental repeats of A/B/MENU are ignored; the D-pad allows repeat-with-delay. The concrete SDL codes are configured after being captured on hardware and kept in an isolated table, with a keyboard profile reserved for development only.

Screen flow:

```text
Home
├── Explore -> Categories -> Results -> Podcast -> Episodes -> Player
├── Search -> Virtual keyboard -> Results -> Podcast -> Episodes -> Player
├── Subscriptions -> Podcast -> Episodes -> Player
├── Downloads -> Episode/Player
└── Settings -> Import RSS / detected credentials / paths and version
```

Search uses a virtual keyboard operable with the D-pad, A (insert), and B (delete/back via explicit focus), plus an optional local, non-sensitive history; it never assumes a physical keyboard. Importing a URL does not use that keyboard: it uses the documented file instead. Each list restores focus and scroll position when returning to it. Destructive or unsubscribe actions show a confirmation. Indicators unambiguously distinguish subscribed, downloading, downloaded, in-progress, offline, loading, and error states. Network errors never replace local content or block navigation.

## 11. Launcher, package, and deployment

The artifact will be a tar/zip with this destination layout:

```text
Roms/APPS/
├── AnberPod.sh
└── AnberPod/
    ├── current/                    # Python application, dependencies, and assets
    ├── runtime/bin/ffmpeg          # static, executable ARM64 ELF
    ├── runtime/certs/cacert.pem    # versioned CA if MuOS doesn't provide a reliable one
    ├── data/                       # created only if missing; never packaged with content
    └── README-INSTALL.txt
```

`AnberPod.sh` will be POSIX `sh`, will resolve its own directory without depending on the working directory, will define absolute paths for Python/app/data/ffmpeg/CA, will configure SDL for the screen and ALSA using only values confirmed on MuOS, will create missing directories with restrictive permissions, and will redirect startup and errors to the rotated log. It verifies Python 3.10, the executables, and write access to data; on failure it writes a readable diagnostic and exits with a non-zero status.

The bundle will include Python bytecode/sources and pure or prebuilt wheels compatible with ARM64; it will not run `pip install` or access the network on the console. `ffmpeg` must be a static ARM64 build, with documented license and provenance, TLS/HTTPS support, and only the protocols/demuxers/decoders that are needed. `aplay` and the ALSA device are detected during install validation; if `aplay` is unavailable, installation fails with instructions, since it never silently falls back to a firmware player.

An update first publishes to `releases/<version>` or `current.new`, validates the content, and atomically switches the version selector when possible. It never includes `data/db`, `data/downloads`, `data/cache`, `data/imports`, `data/config`, or `data/logs`; nor does it ever run `rm` on `data`. A fingerprint of that data is computed before and after as part of the upgrade test. There must be a code rollback path that reuses the same schema when the migration is compatible; incompatible migrations require a copy and an explicit procedure.

## 12. Phases and exit gates

### Phase 0 — reproducible skeleton and contracts

Define the Python 3.10 package, pinned dependencies, models, ports, configuration, logging with redaction, data paths, and architecture tests. Create a diagnostic launcher that opens/closes SDL and logs startup, still without network or audio.

Gate: unit tests with no network; inspectable bundle; startup from a path containing spaces; MENU closes and leaves a log; scan confirms no secrets or media are present.

### Phase 1 — persistence and offline library

Implement the schema/migration, repositories, the Home/Subscriptions/Downloads/Settings screens with fixture data, physical navigation, and offline startup. Add file import, up to validation/preview only, using a fake transport.

Gate: idempotent migrations, recovery from corrupted cache/`.part` files, complete navigation without keyboard or network, and a simulated update that preserves data byte-for-byte.

### Phase 2 — direct RSS and subscriptions

Implement the hardened transport, the RSS/Atom parser, real importing, detail and episode screens, subscribe/unsubscribe, and conditional manual updates. Do not yet incorporate Podcast Index.

Gate: valid and hostile fixtures; limits/redirects/SSRF/TLS all covered; importing, subscribing, updating, and unsubscribing preserves episodes/progress.

### Phase 3 — Podcast Index and discovery

Implement the credential provider, signing, categories, search, caching, and the Explore/Search screens with the virtual keyboard. Open a result and subscribe via its feed.

Gate: deterministic signature test vectors; 401/429/5xx/offline handling; no secrets in logs; category, search, open, and subscribe criteria verified on host and device.

### Phase 4 — playback and resume

Integrate the pinned ffmpeg binary, `aplay`, the process engine, the playback panel, HTTPS streaming, seek, and periodic persistence. Test first with license-compatible synthetic HTTPS audio.

Gate: no firmware player is used; play/pause/stop/seek work; no orphaned processes; resume works after exit and restart with a maximum loss of 10 seconds; a remote error does not destroy the position.

### Phase 5 — downloads and offline playback

Implement the single worker, limits, `.part` files, safe resume, validation, atomic publishing, local priority, and isolated manual deletion.

Gate: network/space/restart interruption handling; a partial file is never played; turning off the network still plays the completed file; deleting preserves subscription and history; no automatic download or deletion behavior exists.

### Phase 6 — packaging and RG35XX H acceptance

Pin the MuOS SDL/input/ALSA profile, build the ARM64 bundle, document installation/import/credentials/licenses, and run the update and acceptance matrix on a real SD card.

Gate: all tests from section 13 pass; startup from `Roms/APPS`; useful logs; offline operation; and non-destructive updates across two consecutive cycles.

## 13. Exact tests and validation commands

The following names form the contract for the future repository. Commands are run from its root with Python 3.10; none of the unit/integration tests depend on the Internet.

### Host automation

```sh
python3.10 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy --strict src/anberpod
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/unit
.venv/bin/python -m pytest -q tests/integration
.venv/bin/python -m pytest -q tests/contract
.venv/bin/python -m pytest -q --cov=anberpod --cov-branch --cov-fail-under=85
```

Minimum required cases, with stable names:

- `test_schema_migrates_empty_db_and_is_idempotent`
- `test_failed_migration_rolls_back_and_preserves_backup`
- `test_unsubscribe_preserves_episodes_playback_and_downloads`
- `test_delete_download_preserves_subscription_and_playback`
- `test_episode_upsert_uses_guid_url_then_fallback_key`
- `test_startup_offline_renders_valid_local_library`
- `test_corrupt_cache_does_not_replace_last_valid_data`
- `test_atomic_cache_interruption_keeps_previous_file`
- `test_import_file_handles_comments_duplicates_and_per_line_errors`
- `test_import_rejects_url_credentials_and_overlong_url`
- `test_http_feed_warns_and_https_redirect_never_downgrades`
- `test_rss_parser_accepts_rss2_atom_and_common_namespaces`
- `test_rss_parser_rejects_dtd_entities_external_access_and_oversize_body`
- `test_http_rechecks_https_and_public_address_after_every_redirect`
- `test_http_rejects_private_loopback_linklocal_ipv4_and_ipv6`
- `test_http_enforces_timeouts_redirect_limit_and_decompressed_size`
- `test_tls_failure_never_falls_back_to_http`
- `test_conditional_get_304_preserves_cached_feed`
- `test_podcast_index_signature_matches_fixed_vector`
- `test_missing_catalog_credentials_leaves_local_features_available`
- `test_logs_redact_api_secret_authorization_and_query_values`
- `test_rate_limit_is_typed_and_does_not_busy_retry`
- `test_navigation_focus_back_and_menu_work_without_keyboard`
- `test_virtual_keyboard_can_enter_search_using_dpad_a_b`
- `test_menu_persists_progress_and_shuts_down_workers`
- `test_player_prefers_complete_local_file_over_remote_url`
- `test_player_never_selects_part_or_missing_download`
- `test_pause_does_not_advance_position_and_seek_is_bounded`
- `test_progress_saved_every_ten_seconds_pause_seek_stop_and_exit`
- `test_ffmpeg_and_aplay_arguments_are_lists_and_protocols_are_limited`
- `test_player_terminates_both_children_on_error_and_exit`
- `test_download_rejects_insufficient_space_known_and_unknown_length`
- `test_download_enforces_stream_limit_when_content_length_lies`
- `test_download_only_becomes_complete_after_fsync_probe_and_atomic_rename`
- `test_range_resume_requires_coherent_206_otherwise_restarts`
- `test_interrupted_download_is_not_playable_and_can_retry`
- `test_no_use_case_starts_automatic_download_or_deletion`

HTTP tests will use a local TLS server with a test CA and a controllable fake resolver; TLS verification will never be disabled. Process-adapter tests will use spy executables, not the host's audio. XML fixtures include truncated bodies, incorrect content types, redirects, bounded compression bombs, and malicious entities.

### Bundle and architecture

```sh
./scripts/build_bundle.sh --arch aarch64 --version 0.1.0
./scripts/check_bundle.sh dist/AnberPod-0.1.0-aarch64.tar.gz
tar -tf dist/AnberPod-0.1.0-aarch64.tar.gz
file build/stage/Roms/APPS/AnberPod/runtime/bin/ffmpeg
readelf -h build/stage/Roms/APPS/AnberPod/runtime/bin/ffmpeg
build/stage/Roms/APPS/AnberPod/runtime/bin/ffmpeg -hide_banner -protocols
build/stage/Roms/APPS/AnberPod/runtime/bin/ffmpeg -hide_banner -buildconf
rg -n --hidden -i '(api[_-]?secret|authorization|BEGIN .*PRIVATE KEY|\.mp3|\.m4a|\.ogg)' build/stage dist
```

`check_bundle.sh` must fail if the ELF is not AArch64, ffmpeg is not static per the documented method, HTTPS/TLS support or license info is missing, build machine absolute paths are present, the package contains secrets/media/DB/user data, the launcher is not executable, or a prohibited player shows up. The final `rg` check only allows empty example field names/documentation and explicitly listed test manifests; any unexpected match fails the job.

Exact preservation test for updates, run against a temporary directory (implemented by `tests/integration/test_upgrade_bundle.py`):

```sh
.venv/bin/python -m pytest -q tests/integration/test_upgrade_bundle.py
```

The test installs v0.1.0, creates configuration, a DB, cache, progress, a download, an import, and a sentinel log, computes SHA-256 hashes and metadata, installs v0.1.1, and checks that no file under `data/` was overwritten or deleted and that a migration only changed the DB in the expected way.

### Validation on RG35XX H with MuOS

First copy to a test SD card and run from the menu, not only via SSH. Record in `docs/HARDWARE-VALIDATION.md` the device model, MuOS version, filesystem, bundle hash, and the result of each step.

```sh
cd /mnt/mmc/Roms/APPS/AnberPod
./current/bin/python3 --version
file runtime/bin/ffmpeg
./runtime/bin/ffmpeg -hide_banner -version
command -v aplay
aplay -l
test -w data
tail -n 200 data/logs/anberpod.log
```

The `/mnt/mmc` path is a placeholder that will be replaced with the real path discovered on the device; the launcher never hardcodes it. Mandatory manual matrix:

1. Start from `APPS` with no network; confirm the startup log, Home screen, and local data.
2. Navigate every screen using only D-pad/A/B; MENU from any screen saves and exits with no orphaned `ffmpeg`, `aplay`, or Python process.
3. With local credentials, open categories, search using the virtual keyboard, open a podcast, subscribe, and unsubscribe without losing history.
4. Import from `rss_urls.txt`, review the result, subscribe, and update episodes on demand.
5. Play over HTTPS, pause, skip −15/+30, stop, restart the app, and verify resume within ±10 s.
6. Manually download, observe size/state, turn off the network, and play the completed local file.
7. Cut network and power during another download; restart and confirm the `.part` file is not played and that Retry works without a false "complete" state.
8. Delete a download and confirm the subscription, episode, and position remain present.
9. Fill the SD card up to the safety margin and confirm clean rejection, with no corruption or automatic deletion.
10. Install the next version over a populated library; compare the data manifest/hash and repeat the offline startup check.
11. Leave it playing and navigating for 60 minutes; confirm a responsive UI, no sustained audio degradation, bounded memory usage, a reasonable temperature, and rotated logs.

## 14. Risks that must be resolved before freezing the package

- Capture the real SDL codes for D-pad/A/B/MENU on actual hardware, the video driver, the ALSA device, and the presence/behavior of `aplay`; these are platform-specific facts that must not be guessed in code.
- Verify that the chosen static ffmpeg build for AArch64 has HTTPS, common podcast codecs (MP3, AAC/M4A, Opus/Vorbis), and a compatible redistributable license, while keeping a reasonable size.
- Measure decoding performance, write pressure, and battery life with both streaming and local file playback. If 48 kHz stereo is not stable, change the PCM format once, based on measurements, and update the contract/tests accordingly.
- Confirm update semantics and SD card paths for the target MuOS version. The non-negotiable invariant is that `data/` stays out of the replaceable content.
- Test the device's certificates and clock: an incorrect clock must produce an actionable diagnostic, never disable TLS or fake authentication.

## 15. Acceptance traceability

| Criterion | Phase | Primary evidence |
|---|---:|---|
| Categories, search, open, and subscribe | 3 | catalog/navigation tests + matrix 3 |
| Import RSS, subscribe, and view episodes | 2 | import/parser/repository tests + matrix 4 |
| Stream, stop, and resume | 4 | engine/progress tests + matrix 5 |
| Download, turn off network, and play from SD | 5 | priority/atomicity tests + matrix 6–7 |
| Delete a download without losing library/history | 5 | isolation tests + matrix 8 |
| Start from `Roms/APPS`, log, and preserve data on update | 0, 6 | `check_bundle`, upgrade test + matrices 1, 10 |

No out-of-scope features are added just to "complete" a phase. Any change to schema, network, playback, or deployment first requires updating its contracts, failure test, and corresponding hardware evidence.
