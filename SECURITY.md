# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately with GitHub's private vulnerability reporting: on the repository's **Security** tab, choose
**Report a vulnerability**. Direct link:
<https://github.com/enable4all/estia-on-mmini/security/advisories/new>.

Useful details: the commit you tested, the steps to reproduce, and what an attacker could do. Please leave real
phone numbers, account IDs, mail, calendar entries and keys out of the report; a made-up example is enough.

The maintainer will acknowledge a report as soon as practical.

## Supported versions

Only the `main` branch is supported. Fixes land there.

## Scope

In scope, for this project's code:

- the bridge (`src/local_assistant/`), including its configuration handling and slash commands;
- the voice service (`tools/voice/`);
- the device, calendar and call helpers (`tools/tapo/`, `tools/ical/`, and the build and patch in `tools/call-tunnel/`);
- the scripts and the launchd templates in `scripts/` and `launchd/`.

Out of scope, report them upstream:

- signal-cli, RingRTC and the upstream signal-call-tunnel (see `THIRD_PARTY_NOTICES.md`);
- Ollama and the Gemma model;
- the Signal, Microsoft, Google, Spotify, Brave and TP-Link services and apps.

## What the project tries to protect

The full threat model is in [setup_assistant.md, section 8](setup_assistant.md#8-security). In short:

- Only the configured Signal accounts get answers. Other senders are ignored, never answered or rejected.
- The model has no shell. Its project tools read only git-tracked documents, never secret-like file names, and never
  through symlinks.
- Mail access is read-only (`Mail.Read`), and mail bodies are not stored.
- Calendar access is read-only: Google calendars through their iCal address, Outlook calendars with `Calendars.Read`
  only (its own token request, never mail's token).
- A Google calendar's secret iCal address is kept in a mode 0600 file, read only by the calendar helper.
- Lights and plugs are reached on the home network only. A device change requested after outside text (a web page, mail,
  a calendar entry, a project file or a photo) waits for the user's yes.
- Text from outside sources is treated as data, never as instructions.
- Reminders and wake-up calls come only from the user's own words: setting or cancelling one is refused in code in an
  answer that read outside text, and for a shared audio file or a photo. She places a call only to an allowed account and
  only for a reminder the user set; the confirmation is written by code.
- Messages she starts herself are opt-in (off until the user types `/initiative on` or the config turns them on) and
  decided by code (triggers, quiet hours, caps) before the model is asked; the model then sees only the user's own
  words, profile facts and the moment (never mail, calendar, web or pages), and code checks its text before anything is
  sent. Each is sent at most once, only to allowed accounts; `/quiet` pauses them and `/initiative off` turns them off.
- Keys live in mode 0600 files or the login keychain. Logs hold metadata, never message text, and so does
  `status.json` (each part's last use for doctor and `/status`: times and fixed reasons only).
- A privacy check (`scripts/privacy_scan.py`) keeps private files, keys and personal values out of the repo. It runs in
  `scripts/verify.sh` and, if you turn it on, before every commit.

## Known accepted gaps

- Signal's own delivery receipts go to any sender, so a stranger can tell that the number is registered.
- signal-cli starts the call helper for every incoming call before the bridge decides. The helper uses relay-only ICE,
  so callers do not learn the Mac's addresses.
- The call helper is built on an old WebRTC revision (7444f).
- Prompt injection is reduced by code-level rules, not eliminated. A model can still be talked into a wrong answer.
  Device changes and profile changes have their own checks in code.
- One user per Mac is assumed. Ollama (127.0.0.1:11434) and the voice service (127.0.0.1:8150) are trusted local
  services; on a Mac shared with other accounts, another user could start a look-alike service on those ports first.
- A voice note forwarded inside Signal keeps its voice-note flag, so it counts as your own words.
- The speech models (WhisperKit, Kokoro) are downloaded from Hugging Face without a pinned revision or hash.
- The JVM signal-cli for calls is verified against the maintainer's GPG keys as GitHub lists them, not a pinned
  fingerprint.
- Health checks (`[health] extra_checks`) follow redirects; list only services you run.
- A link quoted inside a mail or a page can reach you in an answer (links are removed only from web and map answers):
  check a link's address before you open it, as with any message.
- Log rotation (`/etc/newsyslog.d/local-assistant.conf`) runs as root over a folder your user owns.
- A reminder is delivered at least once: after a crash in the middle of sending, its note is listed again marked "(may be
  a repeat)". A reminder or a wake-up call is not a dependable alarm (one Mac, signal-cli's experimental `startCall`).
- A reminder's text (the user's words) is kept in the database like any message, until it is done or cancelled and 30
  days have passed.
- The checks of a message she starts are English word lists: in another language they rely on her rules and the Gemma
  test set. A check-in keeps the words of the plan (at most 120 characters) until 30 days after it closes.
