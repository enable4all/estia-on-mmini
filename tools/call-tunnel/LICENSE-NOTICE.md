# Licence notice: tools/call-tunnel

The contents of this folder (the build script, the installer for the JVM build of signal-cli, the pinned upstream
revisions and `local.patch`) are licensed under the **GNU Affero General Public License, version 3 only (AGPL-3.0-only)**.
The rest of the repository is MIT licensed (see the root `LICENSE`).

## Why this folder is AGPL-3.0

`local.patch` modifies signal-call-tunnel, which builds on Signal's RingRTC library. Both are licensed AGPL-3.0. Under the AGPL,
a modified version that users interact with over a network must offer its source to those users. This folder is the
corresponding source of those changes.

The build script and the installer here contain no upstream code. They are AGPL-3.0 because they belong with the patch.

## Modified-work notice (AGPL-3.0, section 5a)

- Upstream works: signal-call-tunnel (its authors; see the upstream repository) and RingRTC (Signal Messenger, LLC).
  Both are AGPL-3.0.
- Modified: 2026-10-10, by the local-assistant project.
- Changes, as listed in `local.patch` (all in signal-call-tunnel): ICE candidates from linked devices keep their sender
  device id (the change of upstream PR #2); relay-only ICE, so callers never learn the home network's addresses;
  WebRTC audio processing off in pipe mode (the audio is synthetic); a real-time clock thread for the audio timer,
  with a clock statistics log line. Pipe audio itself comes from upstream PR #5, the pinned base.
- Pinned upstream revisions are listed in `README.md` in this folder.

## Other components

- signal-cli is GPL-3.0-or-later. It is installed on your machine and is not part of this repository.
- OpenJDK (used to run the JVM build of signal-cli) is GPL-2.0 with the Classpath Exception. It is installed separately.

Full licence text: `LICENSE` in this folder (also at <https://www.gnu.org/licenses/agpl-3.0.txt>).

Built binaries are not distributed by this project. If you build and run them, you are responsible for the AGPL-3.0
obligations that apply to the version you run and offer.
