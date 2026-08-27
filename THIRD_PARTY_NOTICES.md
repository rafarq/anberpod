# Third-party notices

This project does not vendor or commit any third-party binaries in this
repository. The notice below describes obligations that apply to the
*release/install package* built from this repository, which does bundle a
third-party binary supplied by the person building/distributing the release.

## FFmpeg (`Roms/APPS/AnberPod/runtime/bin/ffmpeg`)

A release package built with `scripts/build_bundle.sh` and installed on the
device includes a static AArch64 build of FFmpeg (see `README.md`), used as
the app's isolated audio-decoding engine because the device firmware's own
media players are unusable (`PLAN.md`).

- FFmpeg is licensed under the **GNU Lesser General Public License version
  2.1 or later (LGPL-2.1+)**, or the **GNU General Public License version 2
  or later (GPL-2.0+)** if built with GPL-only components enabled.
- Whichever license applies to the specific build used must be identified
  and included, verbatim, with the release package (e.g. alongside the
  binary or in a `LICENSES/` directory of the release, not necessarily this
  source repository).
- Under LGPL/GPL, anyone who receives the release package must also be
  able to obtain the **complete corresponding source code** for the exact
  FFmpeg build shipped (the specific version, patches, and build
  configuration used) — either bundled with the release or via a written
  offer valid for the required retention period.
- The specific FFmpeg version, build source/commit, and build
  configuration used for the shipped `ffmpeg` binary should be recorded
  alongside the release artifact so the source-availability obligation
  above can be met.
- If the shipped build was compiled with any GPL-licensed
  components/codecs, the whole binary is subject to GPL-2.0+ terms rather
  than LGPL-2.1+; confirm the actual build configuration before
  distributing.

No FFmpeg source or binary is vendored in this git repository; this file
exists so the obligations above aren't missed when a release package is
assembled and distributed.
