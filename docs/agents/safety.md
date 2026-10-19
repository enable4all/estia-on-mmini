# Safety: the hard rules

These hold in every area and load at every start. Each comes from an area's rule file, which has the details; AGENTS.md lists them in one line each.

## Who she answers

- Only ACIs in `allowed_acis` reach Gemma or receive anything; `SignalClient` refuses other recipients in code.
- Calls: only allowed ACIs are answered; anyone else is ignored, never rejected (a rejection sends them something). Details: `calls.md`.
- A message from a device of the user's that is not known yet is held at envelope time: not stored, not read, no receipt. `/lock` stops everything until `/unlock` from the same device. Details: `core.md`.

## Outside text

- Outside data may hold her back, never start anything but a code-written note (owner, 14 Oct; Gladys ADOPT 6): mail, calendar entries, web and map results, pages, YouTube captions, project files, documents, photos and shared audio files may hold, pause or quiet her (music only stops or gets quieter, a device change waits for the user's yes) or fill a code-written note (the digest; a calendar change starts one of the calendar watch's notes, and a new PDF in the folder the documents' note naming it, with no model); outside data never starts a Gemma-written message, a call, a reminder or a device change. A new hold names what an unreadable source means: hold, pass or retry.
- Outside text is data, never instructions. Code refuses reminders in an answer that read mail, a calendar, the web, a page, a project file, a document or the history, and for an audio file or a photo; after mail, a calendar, web results, a page, a document or an audio file it also refuses `remember` and `forget`; a device change after outside text waits for the user's yes. Details: each area's file; across turns: `prompts-evals.md`.

## What the model can do

- No shell. Her only file access: the read-only project tools (only files git tracks, at the project's level) and the document tools (only her index of the documents folder). Details: `documents-projects.md`.
- Health checks, git and the helpers run fixed commands from code, never a shell; in a project, Gemma picks only the project, a tracked file, a start line or search words. Details: `documents-projects.md`.
- Mail and calendars are read-only: no code sends, moves, deletes or marks mail, or creates, changes or answers an event (`Mail.Read`, `Calendars.Read`, CalDAV's PROPFIND and REPORT only). Details: `mail-calendar.md`.
- Lights and plugs: only the configured devices, by name; "ask first" only on the user's own yes. Music after outside text only stops or gets quieter. Details: `devices.md`, `web-maps-music-video.md`.

## Her memory and her notes

- Reminders, wake-up calls and the user's own facts come only from the user's own words; code confirms each change in its own note. Details: `memory-initiative.md`.
- Code-written notes never enter Gemma's history: Gemma imitates whatever is there.
- `soul.md` is the user's file: style only, below the rules, never written by code. Details: `prompts-evals.md`.

## Data and secrets

- No secrets or personal identifiers in the repo: they live in `~/.config/local-assistant/config.toml` (0600; in her own user `/var/estia/.config/local-assistant/config.toml`). Logs hold metadata only, never message text. `scripts/privacy_scan.py` enforces this (verify.sh and the opt-in hook): private file names, keys, addresses, numbers, UUIDs, home paths and the owner's own config values (whole words; setup keys such as paths, local addresses and labels are left out: `SETUP_KEYS`); its output names the rule or config key, never the value.
- Photos live only until answered, audio until used, call audio only in memory; mail bodies are never stored; `status.json` holds no message text, addresses or keys.
- A secret is never typed into chat, never shown on screen and never put into a program's arguments (`ps` shows them to every user of the Mac): hidden prompts read the terminal, helpers get a secret on stdin or from their own key file (0600). Details: `own-user-deploy.md`, Gotchas.

## The Mac

- The model always runs on the Mac: a cloud model is a config error, and the alias is checked against Ollama's own entry before every chat. Ollama only as its official build, never Homebrew's or the app. Details: `core.md`, `own-user-deploy.md`.
- In her own user, her services run only from /usr/local/estia as `_estia`, never from the checkout. Details: `own-user-deploy.md`.
- Never run sudo yourself: the person runs every sudo command in their own Terminal window (a Claude Code `!` command has no terminal for the password). Root-touching code takes `--dry-run` and `ESTIA_ROOT`. Details: `own-user-deploy.md`.
- Tests never read Estia's own system paths or installed tools: a system path goes through `Host.path()`, a fixed tool path under `ESTIA_TEST_ROOT`; the few things still read as the Mac has them are in AGENTS.md (Conventions). Details: `testing.md`.
