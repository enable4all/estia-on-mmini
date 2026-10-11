# Call helper: signal-call-tunnel (our build) and the JVM signal-cli

Voice calls need two things outside this repo, both built or installed as the normal user (never sudo):

1. **signal-call-tunnel**, the Rust helper signal-cli starts for each call (it wraps Signal's RingRTC).
   `tools/call-tunnel/build.sh` builds it from pinned sources plus `local.patch`:
   - upstream [visigoth/signal-call-tunnel](https://github.com/visigoth/signal-call-tunnel) PR #5 head
     `2f924268e1d3083eef70dd82718f1e7e760b13d1` (pipe audio: a Unix socket instead of root-installed BlackHole
     drivers), on upstream main `02c84e9`, RingRTC submodule `a86f8a6` (v2.65.1);
   - PR #2 head `69034124` (calls from linked devices): its change is carried in `local.patch`;
   - local changes: relay-only ICE (callers never learn the Mac's IP addresses), WebRTC audio processing off in
     pipe mode (the audio is synthetic), and a real-time clock thread (without it macOS stretches the 10 ms audio
     timer to ~90 ms in background processes and the call sounds garbled), with a clock stats log line.
2. **signal-cli, JVM build**, same version as Homebrew's (`install-signal-cli-jvm.sh`, signature checked).
   Homebrew's native build cannot place calls (missing reflection metadata for `TurnServerInfo`; also on master).
   Runs on Homebrew `openjdk` (keg-only); the bridge sets `JAVA_HOME` and `JAVA_OPTS` for it.

Licence: this folder (the pins, `build.sh`, `install-signal-cli-jvm.sh` and `local.patch`) is licensed AGPL-3.0-only,
because the tunnel and RingRTC it patches are AGPL-3.0. See [LICENSE-NOTICE.md](LICENSE-NOTICE.md) for the notice of
our changes, and the root `THIRD_PARTY_NOTICES.md`. Built binaries are not distributed. signal-cli is GPL-3.0 and is
installed, not vendored.

Security notes (reviews 2026-10-09): the tunnel uses an old WebRTC (7444f); signal-cli starts a tunnel for every
incoming offer, also from strangers, before the bridge can decide (accepted for now; relay-only ICE keeps the host's
addresses private). Re-check the patches before bumping RingRTC.
