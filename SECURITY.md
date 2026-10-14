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
- the device, calendar, YouTube and call helpers (`tools/tapo/`, `tools/ical/`, `tools/youtube/`, and the build and patch
  in `tools/call-tunnel/`);
- the scripts and the launchd templates in `scripts/` and `launchd/`.

Out of scope, report them upstream:

- signal-cli, RingRTC, the upstream signal-call-tunnel and youtube-transcript-api (see `THIRD_PARTY_NOTICES.md`);
- Ollama and the Gemma model;
- the Signal, Microsoft, Google, YouTube, Spotify, Brave and TP-Link services and apps.

## What the project tries to protect

The full threat model is in [setup_assistant.md, section 8](setup_assistant.md#8-security). In short:

- Only the configured Signal accounts get answers. Other senders are ignored, never answered or rejected.
- She runs as her own hidden user `_estia` (no password, no login, no remote login), from code in `/usr/local/estia`
  owned by root, the Mac's all-powerful admin account: her code and every program she runs (her signal-cli, Java for
  calls, the call helper) are root's copies, and the folders her programs are looked up in are root's only. Her data in
  `/var/estia` (0700) is out of reach of your user's programs and AI sessions as long as no admin rule skips the
  password; what she sends to Ollama is not (see the known gaps).
- Projects are an extra, off by default: without `[projects]` her user reads no folder of yours. With it, her user
  reads one folder of yours, the projects folder named in setup, read-only (`scripts/share-projects.sh`, as you, no
  admin rights); in your home she only passes through to that folder, by name, and never lists or reads anything else.
- A change to your copy of the code goes live only through an update, `/usr/bin/sudo /usr/local/estia/bin/estia-deploy`,
  which shows the list of changes and the files they touch and asks first. Root runs only `/usr/local/estia/tools`
  (their own Python, the update code, the service templates), which change only with `--update-tools`, after showing
  what changed in them; the rollback runs from there too.
- Only the devices you allowed get answers: your phone (Signal's device 1) from the start; a new linked device (Signal
  Desktop, a tablet) is held, not answered and not read, until your phone sends `/devices ok N`. A note says so. Signal
  gives a new linked device the number of a removed one: an allowed device silent for 14 days is held again, and
  `/devices forget N` removes one you unlinked at once.
- `/lock` from a device she answers stops every answer and call at once, until `/unlock` comes from the same device, 10
  minutes later at the earliest. Language practice stops too: a practice recap that falls due meanwhile is written and
  sent only after `/unlock`. The lock survives a restart and needs no admin rights.
- A changed safety number is never trusted by itself (`--trust-new-identities on-first-use`, pinned in code): messages
  from that number are dropped, and doctor and `/status` show it with the steps to compare, then trust. Doctor also
  counts the devices on her own Signal account (only the Mac's).
- The model always runs on the Mac. A cloud model, which Ollama runs on its own servers, is refused: by name (a tag
  ending in `-cloud` or `:cloud`) when the config is read, and by Ollama's own entry for her model (`remote_host` or
  `remote_model` set in `/api/tags`) at the start, every 5 minutes and before every chat. Once the check sees one, no
  message goes to it: answers pause, a note says why, and doctor shows ✗.
- With `model_digest` set, a model put in place of hers under the same name gets no message once the pin is checked: at
  the start, every 5 minutes and before every chat. Answers pause, a note says so, and doctor shows the digest. The
  pin cannot stop your own account, which runs Ollama.
- Whisper (speech to text) and the speech detector on calls load only files of one fixed version (one commit on
  Hugging Face), each checked against its fingerprint (sha256) at every start: a model with a changed or extra file
  is never loaded. Whisper's folder is then set aside (`<folder>.refused-<time>`, only the latest kept) and downloaded
  again at that version; changed again, listening stays off. Once downloaded, Whisper starts with no network.
- Setup's secrets: the Signal captcha link, the SMS code and a Registration Lock PIN are typed hidden and go to
  signal-cli on its input (JSON-RPC), never as command-line arguments, which every user of the Mac can read; the
  commands on screen show placeholders. Setup runs Homebrew with its analytics off (`HOMEBREW_NO_ANALYTICS=1`).
- The security step (setup's, and `scripts/harden.sh`) sets remote login (SSH) to keys only, removes rules that let
  admin commands skip your password, makes her files private and sets her Registration Lock, each on your yes. `scripts/harden.sh --nuclear`, the nuclear
  option, adds the firewall, stealth mode, python3 and ruby off the firewall's list and the keychain lock, after saying
  plainly that they may stop other services on this Mac (programs other machines reach stop answering them unless
  allowed, no answer to ping, more keychain prompts). Without it they are one neutral line in harden and doctor, never
  a warning; nothing turns off what a Mac already has.
- With `[privacy] keep_days` set, older messages, pages and tool calls are deleted each night and overwritten in the
  database file; the summary of the current conversation and the profile's facts stay until `/new` or `/forget`.
- The model has no shell. With Projects on, its project tools read only files git tracks, at the level chosen
  (summary, docs or code), never secret-like file names, binaries or files over 200 KB, and never through symlinks.
- Mail and project text never go to a search engine or Google Maps: an answer that read mail or the user's projects
  runs no web search and no Google Maps request (refused in code). An answer that read the calendar runs no web search;
  an event's place may go to Google Maps.
- Her home address stays in the private config: the database keeps only its area, rounded to about 1 km, and a tag of
  the address (HMAC-SHA256 keyed with the Google Maps key, which is never in the database), never the address, so the
  database alone cannot confirm a guessed address. A Directions link to or from home names that rounded area. Rows an
  earlier version wrote with the address, and the Directions links it sent with it, are rewritten or deleted at the
  next start, and deleted rows are overwritten in the file.
- Mail access is read-only (`Mail.Read`), and mail bodies are not stored.
- Calendar access is read-only: Google calendars through their iCal address, Outlook calendars with `Calendars.Read`
  only (its own token request, never mail's token).
- A Google calendar's secret iCal address is kept in a mode 0600 file, read only by the calendar helper.
- Lights and plugs are reached on the home network only. A device change requested after outside text (a web page,
  YouTube captions, mail, a calendar entry, a project file or a photo) waits for the user's yes.
- Text from outside sources is treated as data, never as instructions.
- YouTube: only a video link in the user's current message is read, and only its 11-character video ID reaches the
  YouTube helper, never the link; a video found by a web search is never read. The helper asks only
  https://www.youtube.com (every request and redirect checked, at most 3 redirects), with no proxy settings, no
  cookie file, no account and no key of its own (it uses the public key YouTube puts in the video's page); cookies
  YouTube sets last only for that one run. It stops itself after 20 s, and the bridge stops it at 30 s. YouTube sees
  the Mac's internet address, the video's ID and a user agent that names this helper (local-assistant youtube
  helper). The captions are outside text (no saved facts, no reminders, device changes wait for a yes, links
  defanged; only the user's own video link stays tappable, never one Gemma passed) and never stored: the tool log
  keeps the link, as the user's message does, and a stub in place of the captions, and the last video's captions stay
  in memory only, for 10 minutes. Her answer never holds the captions word for word: code leaves out each sentence
  that copies 13 or more of their words in a row, or shorter stretches of 6 or more that together make up more than
  30% of them, and adds the video's link. Nothing is transcribed. Logs hold outcome codes, character counts and
  times, never the video's ID or its title.
- Reminders and wake-up calls come only from the user's own words: setting or cancelling one is refused in code in an
  answer that read outside text, and for a shared audio file or a photo. She places a call only to an allowed account and
  only for a reminder the user set; the confirmation is written by code.
- Messages she starts herself are opt-in (off until the user types `/initiative on` or the config turns them on) and
  decided by code (triggers, quiet hours, caps) before the model is asked; the model then sees only the user's own
  words, profile facts and the moment (never mail, calendar, web or pages), and code checks its text before anything is
  sent: no link, on any ending, and no request for a code or a password, in all 11 languages. Each is sent at most
  once, only to allowed accounts; `/quiet` pauses them and `/initiative off` turns them off.
- Keys live in mode 0600 files or the login keychain. Logs hold only what happened and when, never message text, and so does
  `status.json` (each part's last use for doctor and `/status`: times and fixed reasons only).
- A privacy check (`scripts/privacy_scan.py`) keeps private files, keys and personal values out of the repo. It runs in
  `scripts/verify.sh` and, if you turn it on, before every commit.

## If the Mac is compromised

Assume everything she holds was copied: her Signal identity, the Microsoft sign-ins, the calendar addresses, the keys
and the TP-Link login. The same holds when a program of yours may have read her folders before the move into her own
user: a copy taken then still works. In this order:

1. Stop her: `/usr/bin/sudo launchctl bootout system/com.local-assistant.bridge`.
2. Re-register her number from a clean machine (setup_assistant.md, 2.3): this logs out every copy of her Signal
   identity. Then compare her safety number on your phone and mark it verified.
3. Microsoft: at mysignins.microsoft.com choose "Sign out everywhere" for each mailbox, then sign in again with
   `/usr/bin/sudo /usr/local/estia/bin/estia mail-login <address>`.
4. Google calendars: reset each calendar's secret iCal address (Google Calendar, Settings, Integrate calendar), then
   save the new one with `scripts/set-calendar-url.sh [name]`.
5. Keys: create new Brave, Ollama and Google Maps keys, delete the old ones in each console, then save them with
   `scripts/set-web-keys.sh`.
6. TP-Link: change the account's password (and turn on its two-step check), then save it with
   `scripts/set-tapo-credentials.sh`.
7. Registration Lock PIN: set a new one for her number with `scripts/harden.sh` (it stops her, sends signal-cli `setPin`
   through `jsonRpc` with the PIN on its input, and starts her), and keep it in your password manager, off the Mac.
8. Check your own phone: Signal, Linked devices; remove any device you do not know, then send her
   `/devices forget N` for it. Check hers: `/usr/bin/sudo /usr/local/estia/bin/estia signal listDevices` (only the Mac).

## Known accepted gaps

- signal-cli is an unofficial client, and Signal's terms list automated messaging and dialing as not allowed: Signal can
  disable her account. Keep the volume low (one household), and give her a separate number, never your own.
- Signal's own delivery receipts go to any sender, so a stranger can tell that the number is registered.
- signal-cli starts the call helper for every incoming call before the bridge decides. The helper connects only through Signal's
  relay servers, so callers do not learn the Mac's addresses.
- The call helper is built on an old version of WebRTC, the calling library (revision 7444f).
- Prompt injection is reduced by code-level rules, not eliminated. A model can still be talked into a wrong answer.
  Device changes and profile changes have their own checks in code.
- One user per Mac is assumed. Ollama and the voice service, reachable only from the Mac itself (127.0.0.1:11434 and
  127.0.0.1:8150), are trusted local services; on a Mac shared with other accounts, another user could start a
  look-alike service at the same address first.
- A voice note forwarded inside Signal keeps its voice-note flag, so it counts as your own words.
- The speaking models (Kokoro for English, Supertonic-3 for the other ten languages) come from Hugging Face as
  published: no fixed version, no check of their files. By FluidAudio 0.15.8's code they are fetched only when a file
  is missing; that was not tested with the network off.
- Health checks (`[health] extra_checks`) follow redirects; list only services you run.
- YouTube's terms forbid automated access and downloading its content, captions included, with no exception for
  personal use; the YouTube helper does both: accepted for personal use. YouTube blocks heavy use and cloud servers; two reads from a home
  connection worked, repeated use is untested. No proxy is used, so a block stops video links until YouTube lifts it
  (`/status` and doctor show it).
- The YouTube helper relies on an undocumented YouTube endpoint, through youtube-transcript-api 1.2.4 (pinned): it
  reads the video's page, then YouTube's internal player address with the key from that page, naming YouTube's
  Android app as its client, then the caption track. YouTube can change any of these without notice, or ask for a
  token that proves a request comes from its own app (reported for some caption tracks); reads then fail
  ("unreadable answer" in doctor and `/status`) until a newer pinned version. A change at YouTube or a reworded bot
  check can also look like an answer: every video then seems to have no captions, or to be private or removed, and
  `/status` and doctor still show YouTube working. If videos that clearly have captions get either answer, suspect
  YouTube, not the video. The helper also calls two internal steps of that version, to learn which captions YouTube
  shows by default: check them whenever the pin changes. In the EU, YouTube may first show its cookie consent form:
  shown on www.youtube.com, the library accepts it with a cookie for that one run; a redirect to consent.youtube.com
  is refused by the host check and the read fails ("unreadable answer"). Untested live.
- A link quoted inside outside text (a mail, a page, YouTube captions, the calendar, a photo, a shared audio file) reaches you only
  defanged (`example[.]com/login`) unless you sent it yourself, in language practice too; a bare domain with an
  uncommon ending stays as written.
  Check a link's address before you open it, as with any message.
- Calls name no device: a call from a new linked device rings like one from your phone (texts from it are held).
- The local AgentJev service (the optional mail pre-filter) is a separate project: its server does not check the Host
  and Origin headers of a request, so a web page in a browser on this Mac could reach it (DNS rebinding). Run it only
  reachable only from the Mac itself (127.0.0.1), and only while you use it.
- Ollama and AgentJev run as you: every prompt she sends (her history, profile, mail summaries, calendar events, call
  transcripts) and every mail header passes through a program your account can change, its logs or a look-alike on its
  port. Her own user keeps her files and keys from your programs, not what she sends there.
- Root, and any program that gets root, still reads everything. While an admin rule skips the password (`NOPASSWD`
  in `/etc/sudoers.d`), every program you run gets root without it: the security step removes such a rule. An admin's
  password, caught once, is root.
- The move and each update trust your copy of the code (the checkout) and Homebrew at that moment: what they copy is
  what she runs (the update shows the list of changes and the files first). The first move runs your copy's code as
  root, after your Mac password; after it root runs only `/usr/local/estia/tools`. `scripts/harden.sh` runs from your
  copy as you, and its admin sign-in lasts 5 minutes in that Terminal window: run it from a copy you trust.
- Her old folders are held for 7 days where only root reaches them (`/usr/bin/sudo /usr/local/estia/bin/estia remove-old` deletes
  them sooner, and ends the rollback). Earlier copies kept aside in `/var/estia/.before-<time>` (a retried move or a
  move after a rollback) hold older copies of her keys and Signal data, readable only by `_estia`: the move lists them,
  and the same `remove-old` deletes them. A copy a program of yours took before the move still works: if one may have, follow "If the Mac is
  compromised". A rehearsal (`--rehearse`) holds a copy of her keys in the folder you name: delete it afterwards.
- `_estia` has no password: the move sets `Password *` and deletes `AuthenticationAuthority` and `ShadowHashData`, doctor
  checks the record, and the SSH drop-in denies the account. The first real move ran on 13 Oct; that macOS 27's
  `sysadminctl -roleAccount` leaves nothing else to log in with is still untested beyond doctor's record check
  (docs/e2e-checklist.md, 113).
- launchd opens her two launchd log files (`launchd.log`, `voice.log`) as root in a folder she owns; that it never
  follows a link she puts there is untested (docs/e2e-checklist.md).
- A value typed on a `/usr/bin/sudo …` command line lands in sudo's log, which admins can read: `estia secret set` refuses one,
  but the log keeps it; change that key.
- A linked device's number is trusted until the device is silent for 14 days or `/devices forget N`: a device linked
  under a removed one's number within that time is answered. The Registration Lock lapses after 7 days with the Mac
  offline.
- With Projects on, her user reads every file in your projects folder (`[projects] root`, shared by
  `scripts/share-projects.sh`), also files git does not track, such as `.env.local`: her project tools open only
  tracked files at the level chosen, and she has no shell, so
  only code running as her could read the rest.
- Her git trusts your repositories with `-c safe.directory=*` on its own command line (never in a config file), after
  the check of each repository's local git config; `git status` leaves submodules alone. A program of yours that changes
  a repository's git config between that check and her git command could still make git run a program as her: the
  check narrows that race, never closes it.
- A reminder is delivered at least once: after a crash in the middle of sending, its note is listed again marked "(may be
  a repeat)". A reminder or a wake-up call is not a dependable alarm (one Mac, signal-cli's experimental `startCall`).
- A reminder's text (the user's words) is kept in the database like any message, until it is done or cancelled and 30
  days have passed.
- The checks of a message she starts: the words that ask for a code or a password, and reminder and call claims, are
  checked in all 11 languages; the other word lists (claims of an action, mail and calendar words, guilt) are English,
  and in another language those rely on her rules and the Gemma test set. A link is caught on any ending, but a
  one-letter name with a two-letter ending right before a full stop ("x.co.") reads as an abbreviation. A check-in keeps
  the words of the plan (at most 120 characters) until 30 days after it closes.
- The firewall, stealth mode and the keychain lock are set only by the nuclear option (`scripts/harden.sh --nuclear`):
  without the firewall, a program of yours that listens for other machines can be reached from your network.
- The cloud-model check trusts Ollama's own list, from a program that runs as you (above). When that list cannot be
  read, nothing pauses and the chat goes ahead (with Ollama down, it fails too); the name check at config load still
  holds. The check and the chat are two requests: a name switched in the moment between them is not caught.
- Copies of her data kept aside by the move (her old folders held for the rollback, and `/var/estia/.before-<time>`)
  hold the database as it was then, with the home address if an earlier version kept it (the home-area rows and its
  Directions links). Once the rollback is no longer needed, delete them with
  `/usr/bin/sudo /usr/local/estia/bin/estia remove-old`. A Directions link sent for an address you have since changed in
  the config is not found; `[privacy] keep_days` deletes old messages.
- Only setup's brew commands run with Homebrew's analytics off; brew commands you type keep your own Homebrew setting
  (`brew analytics off` turns it off for all of them).
