# Estia

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/estia-hero-dark.png">
  <img src="docs/brand/estia-hero-light.png" alt="A stone garden seen from above, its gravel raked in rings around a Mac mini" width="100%">
</picture>

**A private AI thinking partner living at home, on your own Mac.** You reach her only through Signal: text her, send a voice note, or call her. She knows your calendar, reads your mail, dims the lights and puts some jazz on. Nothing about her lives in someone else's cloud.

Estia (Εστία, the hearth) is a private AI thinking partner that lives in your home, on your own Mac, reached only over Signal. Name her what you like. Ours is called Tiffany. Estia started as Tiffany, our own assistant, before she was opened up for every home.

- **Local.** The AI model (Google's Gemma, through Ollama) runs on your Mac. No cloud AI service, no account with us, no telemetry.
- **Read-only where it matters.** She reads mail and your calendar but can never send, move, delete or change them. Lights and plugs follow rules you set per device, and anything she was nudged into by a web page, a mail or a shared audio file waits for your yes.
- **Only you, only Signal.** Talk to her from Signal on your phone or Signal Desktop on your laptop. There is no web app and no open port: the bridge listens on nothing, so there is nothing to log in to and nothing exposed to the internet. Messages travel end-to-end encrypted through Signal, and only your Signal account gets an answer; everyone else is ignored. Calls go through Signal's relays too, so your phone never learns the Mac's IP address. To keep your phone's address from the Mac as well, turn on "Always relay calls" in Signal on your phone.
- **Your secrets stay put.** Keys and sign-ins live in files only you can read (mode 600) and in your keychain, never in chat, logs or the repository.

**Get started:** a Mac with Apple silicon and 16 GB of memory or more, plus a spare phone number for her Signal account.

```sh
git clone https://github.com/enable4all/estia-on-mmini.git ~/estia && cd ~/estia
scripts/setup.sh
```

Or open the folder in your AI coding assistant (Claude Code, Codex, ...) and say **"set me up"**: it follows [SETUP-WITH-YOUR-AI.md](SETUP-WITH-YOUR-AI.md) and hands over to you for the few steps only you may do (the Signal SMS code, passwords, keys). `scripts/doctor.sh` checks the result.

Not affiliated with Signal, Spotify, Google, Microsoft, Bose or TP-Link.

Contents: [Meet Estia](#meet-estia) · [What she can do](#what-she-can-do) · [Languages](#languages) · [A few conversations](#a-few-conversations) · [Safe and private](#safe-and-private-in-plain-words) · [What runs where](#what-runs-where) · [Words used here](#words-used-here) · [When something is off](#when-something-is-off) · [Slash commands](#slash-commands) · [Requirements](#requirements) · [Set it up](#set-it-up) · [Tests and checks](#tests-and-checks) · [Built with](#built-with) · [What's next](#whats-next) · [Good to know](#good-to-know) · [For developers](#for-developers) · [License](#license)

## Meet Estia

Estia likes a good question. Give her something to think through and she answers first, then asks one question back if that moves things on. She is warm, direct and curious, and she likes a laugh: when the moment invites it, one short, light joke may follow her answer, never when you are worried or something serious is going on, and never instead of the answer.

You'll find her in Signal, where her profile reads Estia (AI), or the name you give her (`name` in the config; ours reads Tiffany (AI)). She speaks one language at a time: English, or one of ten others you pick (see [Languages](#languages)). Her personality is her own until you write yours in a short file, `soul.md`, which replaces her built-in style. She reads it but never edits it, and it shapes how she sounds, never what she is allowed to do.

## What she can do

| Area | Try saying | What you get |
|---|---|---|
| Chat | "What's a good question to ask about free will?" | A short, direct answer. She remembers the conversation. |
| Voice notes | Send a voice note | Transcribed on the Mac, answered in text, and optionally read back as a voice note |
| Calls | Call her | A spoken conversation you can interrupt, and a short summary text afterwards |
| Calendar | "What's on Friday?" | That day's events from each of your calendars, with times and places |
| Mail | "What needs my reply?" | Unanswered mail from the last week, numbered. "Read 2" opens one. |
| Morning digest | Nothing, it comes at 09:15 on its own | Today's calendar, tomorrow in one line, then the mail that needs you |
| Music | "Play some jazz" | Spotify on your speakers (once set up) |
| Saved lists | "What should we play next?" | Ideas from a list you saved (songs, books, recipes) |
| Chords | "What are the chords of Spooky?" | Chords, key and tempo from a chord site, with a Sources message |
| Lights and plugs | "Turn off the living room lamp" | Switched over your home network, with no cloud in between |
| The Mac | "How is the Mac doing?" | A few lines on its health. She also reports problems without being asked. |
| Photos | Send a photo with a question | An answer about the photo. The photo is deleted afterwards. |
| Web search | "Who won the match last night?" | An answer that names the site and how recent it is, plus a Sources message |
| Places and routes | "A good Italian place near me?" · "How long to the station by bike?" | Up to five places with rating, price and open now, or the time, distance and lines, with Google Maps links (once set up) |
| Projects | "What's next on <project>?" | Open to-do items and recent changes from a project on the Mac |
| Memory | "Remember that …", `/profile`, `/fix`, `/forget` | She saves what you tell her, and you can list, correct or remove it |

### Your day: calendar, mail and the digest

Every day at 09:15 (set in the config) the morning digest arrives: today's calendar first, then tomorrow in one line, then the mail that needs a reply. Declined invitations are left out, and on quiet days she says so. If the Mac was off at 09:15, the digest arrives when the bridge starts, as long as that happens before noon.

The calendar can be one calendar or several, each with a short name: Google calendars, read through their iCal address, and the Outlook calendars of the Microsoft 365 mailboxes she already reads (read-only by permission: `Calendars.Read`). With several, each event carries its calendar's name ("Family: Football", "Work: 1:1 with Sam"), an invitation that is in two calendars is shown once, and a calendar that can't be read is named with the reason while the others are shown as usual. She reads them live, never caches them and cannot change them. Ask what's on a day, whether you're free, or when something is ("When is the dentist?").

For a Google calendar, use its secret iCal address: it works like a password, so only Estia has it. A public address works too, but a public calendar can be seen by anyone, so never make a private calendar public for her. For an Outlook calendar, the mailbox's sign-in also needs `Calendars.Read`: an administrator grants it once in Microsoft Entra, or you sign in again. Mail keeps working either way. She reads the mailbox's own default calendar, not calendars others share with you.

Mail comes from one or more Exchange (Microsoft 365) mailboxes listed in the config, and she can only read it. Headers sync every five minutes. The text of a mail is fetched only when needed (when you ask, or once to sort it) and never stored; only headers, a label and a one-line summary are kept, for 30 days. A mail counts as answered once you reply in the same conversation, and mail you archive in Outlook drops off her list.

Gmail is not built in; [setup_assistant.md, "Gmail (not built in)"](setup_assistant.md#gmail-not-built-in) explains why and lists the options (forwarding Gmail to a mailbox she reads works today).

### Music and saved lists

Built, not live yet. Music needs a Spotify Premium account and a Spotify developer app (see `setup_assistant.md`, "Spotify (once)"), and it hasn't been tested against the real Spotify so far.

Music works by text message only, because Spotify's rules don't allow voice control. Calls and voice notes won't start it. Try "play some jazz", "play my liked songs on <speaker>", "next", "pause", "louder" or "what's playing?".

A speaker starts at 30% and never goes above a cap (60% by default). She also tries to wake idle Bose SoundTouch speakers from standby over the home network. If an answer used text from a web page, a mail or the calendar, she can pause the music or turn it down, but she won't start it or turn it up.

Tell her a list (your band's songs, books to read) and she saves it in her profile (it counts toward the profile's 6,000-character limit). She can then suggest what to play or read next, and look up the chords for a song on the web, with sources.

### Lights and plugs

Estia controls Kasa lights and plugs on your home network, never through the TP-Link cloud. Try "turn off the living room lamp", "dim the bedroom light to 30%, warm white" or "is the heater on?".

Each device has a rule: `normal`, `ask first` (she waits for your yes, which has to come within two minutes) or `never off`. After every change a short note tells you what actually happened, for example "Devices: living room lamp off", and says so when a device doesn't answer.

Only Kasa devices are tested. Tapo devices use the same library and your TP-Link login, but they are untested, and newer Tapo firmware (with the TPAP login) can't be controlled yet. The code still says `tapo` in a few names (`tools/tapo/`, `[[tapo.devices]]`, `tapo.env`); they cover Kasa devices too.

### Photos

Send a photo, with or without a question: "what is this?", "what colour was it?". Up to four photos fit in one message. Follow-up questions within five minutes still see the photo; after that she asks you to send it again. The file itself is deleted after the first answer.

### Web search, with sources

She searches with Ollama's web search first and falls back to Brave Search (at most 2,000 searches a month by default, set by `brave_monthly_cap`; set it to your Brave plan's monthly limit). She runs at most two searches per answer and names the site; for news, scores and prices she also says how recent it is. Links come in a separate Sources message, so the answer itself stays easy to read. Search queries go only to those two services, and results are never stored or learned from.

### Places and routes

Built, waiting for a Google Cloud key (Places API (New) and Routes API; see `setup_assistant.md`, "Places and routes (once)"). Ask for a place ("a good Italian place near me?", "is the museum open now?") and she answers with rating, price and today's hours; ask how to get somewhere ("how long to the airport by train?") and she works out the time, distance and lines. The Google Maps links come in the Sources message. "Near me" means the location you last sent in Signal (attach, then Location; she uses it for three hours and never logs it, though the message itself stays in your chat history like any message), otherwise your home area. For "how do I get to my 3 pm?" she can use the event's place. A daily limit per API (30 by default) keeps it inside Google's free allowance.

### Memory and personality

She remembers the current conversation and can search older ones ("What did I say about the trip?"). `/new` starts a fresh conversation and keeps your profile.

"Remember that …" saves a fact straight away. She also picks up facts quietly from what you say, and tells you when she has saved one. Your own words beat her guesses: what she picks up never replaces or removes a fact you told her to remember or one from `/learn`. `/profile` lists what she knows and where each fact came from, `/fix` corrects a fact in your own words, and `/forget` removes it.

`/learn <link>` reads a page, saves only the facts about you that it contains, and then lists what was saved.

Without `soul.md` she has her built-in style: warm, curious and playful. Your own `soul.md` replaces it and sets her tone and how long her answers are. `/soul` shows the file in use.

### Calls

Call her and, after about two rings, she picks up with a greeting. You can think aloud: she waits for about two seconds of quiet before she takes her turn, and you can interrupt her at any time. When she has taken in a request she says a short "Got it." (not after a few words such as "how are you?"; `acknowledge = false` in `[calls]` turns it off), then answers when her answer is ready (`fillers = true` adds a "let me think" line when it takes a while). Typing near the phone and other short noises are ignored, but a quick "yes" or "stop" counts. If you hang up before she answers, she stops and texts you that it was not done. By default she checks in after 90 seconds of silence and says goodbye after ten minutes. When you wrap up ("that will do for now", "that's all"), she says goodbye and hangs up. After a call with two or more turns, she texts you a short summary.

Lights and plugs work on calls too. A yes to a device question only counts once she has finished asking it, so a yes said over her question is ignored.

### The Mac itself

Ask "How is the Mac doing?" for a short status: the model and helper services, disk, memory, uptime, recent errors, and the state of Signal, mail, calendar, lights, music, voice, calls, web search (searches today and this month) and maps (requests today).

The Mac is checked every four hours. She reports a real problem once it has lasted five minutes, and sends one "back to normal" when it's fixed.

## Languages

Estia listens, thinks and speaks in **one language at a time**. One language means fewer mistakes: speech recognition
is told the language on every voice note and call and never has to guess, and she never switches by accident.

| Language | Code | Whisper's error rate |
|---|---|---|
| English | `en` | 4.1 % |
| Español (Spanish) | `es` | 2.8 % |
| Italiano (Italian) | `it` | 3.0 % |
| 한국어 (Korean) | `ko` | 3.1 % |
| Português (Portuguese) | `pt` | 4.1 % |
| Nederlands (Dutch) | `nl` | 4.3 % |
| Polski (Polish) | `pl` | 4.6 % |
| Deutsch (German) | `de` | 4.9 % |
| 日本語 (Japanese) | `ja` | 4.9 % |
| Русский (Russian) | `ru` | 5.0 % |
| Français (French) | `fr` | 5.3 % |

These are the languages Whisper large-v3 hears best that also have a voice. The rates are OpenAI's published per-language
chart for large-v3 (the better of Common Voice 15 and FLEURS; word error rate, character error rate for Japanese and
Korean); lower is better. Other languages are left out on purpose.

- **Pick it at setup** (`scripts/setup.sh` asks; English is the default), or set `language = "nl"` in the config.
- **Change it only by typing `/language` in Signal.** `/language` lists them; `/language nl`, `/language dutch` or
  `/language nederlands` switches. Not by voice, not by asking her, and not during a call (a switch typed during a call
  waits until it ends). The choice is kept after a restart and wins over the config.
- **What follows the language:** her answers (whatever language you write in), voice notes and spoken answers, and calls:
  the greeting, "one moment", "sorry, could you say that again?", "take your time" and goodbye are in your language, and
  her short "got it" ("Begrepen.", "Verstanden." ...) tells you she heard a request. A device yes counts in any of the 11
  languages ("ja", "oui", "はい" ...).
- **What stays English:** everything the code writes, such as notes, the digest's headings, `/status`, `/help` and error
  messages. On a call, the device question ("Shall I switch heater off?") and a few rare lines are spoken in English, so
  they are always clear. On a call in another language, when a device waits for your yes she says only that question:
  the model's own words about it are left out, because in Dutch it once said a device had switched before your yes.
  Facts are kept as you said them.
- **Voices:** English uses Kokoro (`af_heart`, or `am_michael` when setup gave her a male name; `/voice` works as before). The other ten use Supertonic-3 with the voice F1.
  Its first use downloads about 264 MB and takes about a minute; after that it is ready in a fraction of a second. She
  loads it in the background when the bridge starts and right after `/language`.
- **A voice service from before languages speaks English only.** Run `scripts/install-voice-daemon.sh` again (it asks for
  sudo: run it yourself). The reply to `/language` says so when the voice service needs this, and `scripts/doctor.sh`
  checks the language in use (the one chosen with `/language`, else the config's).

## A few conversations

These are paraphrased, so the real wording will vary. The music example assumes Spotify is set up.

> **You:** play some jazz
>
> **Estia:** Jazz on the kitchen speaker, at 30%.

> **You:** tell me something fun about Mondays
>
> **Estia:** *(a fun fact or two first, then one light joke: a joke follows the answer when the moment invites it)*

> **You:** what's on Friday?
>
> **Estia:** Two things: a team call at 10:00, and dinner at 19:00.

> **You:** turn off the living room lamp
>
> **Estia:** Done, the living room lamp is off.
>
> *A note from the bridge follows: "Devices: living room lamp off."*

> **You:** what are the chords of Spooky?
>
> **Estia:** *(the chords and key from a chord site, plus the tempo if it's listed)*
>
> *A Sources message with the links follows.*

> **You:** how is the Mac doing?
>
> **Estia:** Everything is up. The model and helpers are answering, disk and memory look fine, and there have been no new errors since the last check.

> **You:** remember that I like short answers before noon
>
> **Estia:** Noted.
>
> *A separate note confirms what was saved. `/forget` removes it.*

> **You** (voice note): what's on tomorrow?
>
> **Estia:** *(the answer as text first, then the same answer as a voice note)*

## Safe and private, in plain words

- **Only you can talk to her.** She never replies to anyone else or rejects their calls. (Signal itself still confirms delivery, so a stranger can tell the number exists.)
- **Mail and calendar are read-only.** She cannot send, move, delete or mark mail, and she cannot change calendar events.
- **What she reads can't give her orders.** Web pages, search and map results, mail, calendar entries, project files and shared audio files are things she reads for you, never instructions. If an answer drew on them, a device change waits for your yes. Text in photos counts as information too.
- **She only learns from you.** Facts come from what you say, or from a page you explicitly ask her to learn from. Mail, calendar entries, web and map results never teach her anything.
- **A shared audio file isn't you talking.** An audio file shared from another app counts as someone else's words. She can summarise it, but she won't learn from it, count it as your yes, or switch a device because of it. (A voice note forwarded inside Signal may still count as yours.)
- **Every device has a rule.** Each device is `normal`, `ask first` or `never off`. If a device change turns up in a message that isn't about devices, it also waits for your yes.
- **She can't run commands.** The model has no shell. Its project tools read only tracked documents, never secret-like files such as `.env`.
- **She keeps what she needs, on the Mac.** Chats, call transcripts and your profile stay in one database on the Mac until you delete them. Photos, voice audio and call audio are deleted after use; mail bodies, web results and map results are never stored. Logs record what happened but never the text of a message. Keys and config files are readable only by you (mode 0600) or live in the login keychain.
- **Places stay rough.** For "near me" searches only an area rounded to about a kilometre goes to Google (or the location you sent). Your home address does go to Google when it is looked up once and for routes from or to home. Nothing goes to Google Maps in an answer that read your mail.

### What stays home, and what goes online

The model always runs on the Mac. A few services are reached over the internet, only for the features you turn on (mail syncs every five minutes in the background, and the digest reads the calendar each morning).

| Stays on the Mac | Goes online, only when used |
|---|---|
| The model (Gemma, via Ollama) | Signal, for your messages and calls (end-to-end encrypted) |
| Your chats, memory and profile | Microsoft, to read mail and Outlook calendars (read-only) |
| Speech-to-text and text-to-speech | Google's iCal feeds, for Google calendars (read-only) |
| Health checks and project reading | Ollama's web search and Brave Search |
| Lights and plugs (local network) | Spotify, for music |
| | Google Maps, for places and routes |
| | Web pages: links you send her, and the top result of a Brave search |

Setup also downloads: the model (from ollama.com), the voice models (from Hugging Face, on the first start), and for calls the signal-cli release and the call-helper sources (from GitHub and crates.io).

## What runs where

```
 Phone or laptop (Signal app)
        │  Signal (end-to-end encrypted)
        ▼
 signal-cli               child process of the bridge (JVM build for calls)
        │  JSON-RPC over stdin/stdout
        ▼
 Bridge: Python 3.13, standard library only
   ├── SQLite database    memory, history, mail headers
   ├── Gemma via Ollama   the local model, with its tools
   ├── Voice service      speech-to-text and text-to-speech (Neural Engine)
   └── Helpers            lights and plugs, calendar, call audio
```

| Part | Where in the repo | How it runs |
|---|---|---|
| Bridge | `src/local_assistant/` | Always on, as the LaunchDaemon `com.local-assistant.bridge` |
| Voice service | `tools/voice/` (Swift) | Always on, as `com.local-assistant.voice`, listening on localhost only |
| Device helper | `tools/tapo/` | Started per command, in its own uv environment |
| Calendar helper | `tools/ical/` | Started per read, in its own uv environment |
| Call helper | Build script, pins and patch in `tools/call-tunnel/` (built on your machine) | Started by signal-cli for each call |
| Scripts | `scripts/` | Run, health check, install, sign-ins and keys |
| Docs | `setup_assistant.md`, `docs/e2e-checklist.md` | Full guide and the phone checklist |

### Where things live on the Mac

| What | Where | Notes |
|---|---|---|
| Config | `~/.config/local-assistant/config.toml` | Mode 0600, never in git |
| Personality | `~/.config/local-assistant/soul.md` | Mode 0600, written by you |
| Service keys | `calendar.env`, `tapo.env`, `web.env` in `~/.config/local-assistant/`; mail and Spotify token files in `~/Library/Application Support/local-assistant/` | Mode 0600, each read only by the part that needs it |
| Memory and history | `~/Library/Application Support/local-assistant/assistant.db` | One SQLite file |
| Logs | `~/Library/Logs/local-assistant/bridge.log` | Metadata only, never message text |

## Words used here

- Signal: the encrypted messaging and calling app. Estia is a contact in it.
- signal-cli: a command-line Signal client. The bridge runs it in the background.
- Gemma: Google's open AI model. It reads your messages and writes the answers.
- Ollama: software that runs AI models on the Mac.
- Tool: an action the model may take, such as checking the calendar or the lights.
- iCal address: a private link to a calendar. Treat it like a password.
- LaunchDaemon: a macOS background service that starts with the Mac.
- Neural Engine: the Mac's built-in AI chip, used here for speech.

## When something is off

| You see | Likely cause | What to do |
|---|---|---|
| Something is missing, or a part does not work | Setup not finished, a part not set up yet, or a part failing | Run `scripts/doctor.sh` (read-only), or send `/status`. Each ✗ line gives the reason and the command that fixes it |
| "Estia is busy with another job, your answer is queued." | A long job is using the model | Wait. The answer will follow. |
| "Estia isn't available right now, try again in a bit." | Ollama is not reachable | Check it with `curl -s http://127.0.0.1:11434/api/version` (or `ollama ps`). Start it with `brew services start ollama` |
| "Voice isn't available right now; please type it." | Voice service down, or still loading | Check it with `curl -s http://127.0.0.1:8150/health`. After a macOS update, allow about 15 minutes. |
| A call rings out, with a note | Voice service still loading | Try again once `curl -s http://127.0.0.1:8150/health` shows both speech parts as ready |
| "Calendar: the secret address no longer works" | The calendar's address was reset in Google | Save the new address with `scripts/set-calendar-url.sh` (with the calendar's name, if it has one) |
| "Calendars.Read is not granted yet" | An Outlook calendar's permission was not granted | An administrator grants it in Microsoft Entra, or run `scripts/mail-login.sh <address>` again (`setup_assistant.md`, "Calendar (once)") |
| A mailbox asks for sign-in | Microsoft ended the sign-in (password or MFA reset, or long unused) | Run `scripts/mail-login.sh <address>` again |
| "Spotify needs a new sign-in" | Spotify sign-ins last about six months | Run `scripts/spotify-login.sh` |
| "… isn't answering" for a device | Switched off at the wall, or a new network address | Check the plug. If its address changed, run `scripts/tapo-discover.sh`. |
| Replies stop | Signal's safety number changed | Confirm the number in Signal first. See `setup_assistant.md`, Operations. |
| No reply at all | Bridge or Mac is down | SSH into the Mac and run `scripts/check.sh`, then read `~/Library/Logs/local-assistant/bridge.log`. If SSH fails, the Mac itself is off or offline. |

## Slash commands

| Command | What it does |
|---|---|
| `/new` | Start a fresh conversation. Your profile stays, and old chats stay searchable. |
| `/profile` | Numbered facts, with where each came from and when |
| `/fix N new text` | Correct fact N in your own words. It keeps its number, and from then on it counts as yours |
| `/forget N` or `/forget words` | Remove fact N, or the one fact containing the words (if several match, she lists them and asks for the number) |
| `/learn <link>` | Learn about you from a page, and list what was saved |
| `/status` | Model state, waiting messages, profile fill, last learning run, history size, Signal restarts, and each part's state: working (with the time), set up but not tested yet, or failing (with the fix) |
| `/voice auto`, `on` or `off` | Spoken answers to voice notes (auto, the default), to every answer, or never |
| `/voice echo on` or `off` | Show what she heard before she answers a voice note |
| `/voice voices`, `/voice name <voice>` | List the voices, and switch voice notes and calls to one (kept after a restart) |
| `/soul` | Show the personality file in use, or why none is |
| `/language`, `/language <name or code>` | List the 11 languages (the current one marked), or switch to one: `/language nl`, `/language dutch` |
| `/help` | The list above |

## Requirements

- **Mac:** Apple silicon. macOS 14 or later for the bridge, and macOS 15.6 or later for voice notes and calls. The code uses `sips`, `afconvert`, `launchctl`, `sysctl`, `/System/Volumes/Data` and the Neural Engine. 32 GB of memory is recommended: the default model needs about 18 to 20 GB while it is loaded. On 16 or 24 GB, use a smaller Gemma 4 tag (`setup_assistant.md`, 2.2). Its answers will be weaker.
- **Tools:** [Homebrew](https://brew.sh), which also installs the Command Line Tools (git, and Swift 6.2 for voice); Python 3.13 with [uv](https://docs.astral.sh/uv/). The voice tests name the Command Line Tools' Testing plugin folder in `tools/voice/Package.swift`; where that folder does not exist (Xcode only), this is harmless.
- **Model:** [Ollama](https://ollama.com) running, with a model that supports tool calls (developed on Gemma 4 26B). See `setup_assistant.md`, "Model (once)".
- **Signal:** signal-cli 0.14.x (Homebrew) and a spare phone number for the bot. See `setup_assistant.md`, "Signal number (once)".
- **Disk:** about 18 GB for the model, plus about 1.6 GB of voice models (and about 264 MB more once a language other than English is used). Calls add a build of several GB.
- **Optional accounts, hardware and services**, each turned on by its own config section:
  - a Microsoft 365 work or school account for mail and its Outlook calendar (read-only);
  - a Google Calendar secret iCal address for each Google calendar (read-only);
  - a Google Cloud project with billing, for places and routes (a daily cap keeps it within the free allowance); the gcloud CLI sets it up with one script;
  - Spotify Premium and your own Spotify developer app, for music;
  - a Brave Search API key and an ollama.com API key, for web search;
  - Kasa devices on your home network, for lights and plugs;
  - AgentJev, running on the Mac, for the optional mail pre-filter (a separate project: [github.com/malevrigns/agent-jev](https://github.com/malevrigns/agent-jev), Apache-2.0);
  - [health] extra checks: any local web address or launchd service you want watched (nothing to install);
  - for calls: Homebrew's `openjdk` (Java 25 or newer), gnupg, `gh` (signed in to GitHub), rustup with toolchain 1.91.1, cmake, protobuf and coreutils. The build downloads signal-call-tunnel, RingRTC and Cargo crates from GitHub and crates.io: several GB, and a long build.

## Set it up

**The easy way:** `scripts/setup.sh` does steps 1 to 6 below and asks only what it must: it picks the model for your
Mac's memory (16 or 24 GB: `gemma4:12b`; 32 GB or more: `gemma4:26b`), asks for her name (Enter keeps Estia), registers
her Signal number with you, finds your Signal account from a one-time word you send her, asks which language she speaks,
writes the config, and installs the always-on service. Safe to run again. You can rename her later with `name` in the config.
You are done when `scripts/doctor.sh` shows no ✗ and her welcome message has arrived on your phone: reply to it to see her answer.
Or let your AI coding assistant do it with you: [SETUP-WITH-YOUR-AI.md](SETUP-WITH-YOUR-AI.md).

**By hand**, in short (the full guide, with every sign-in and key, is `setup_assistant.md`):

1. Prepare the Mac. Install Homebrew, clone the repository to `~/estia` (not into Desktop, Documents or Downloads), install the tools from Requirements, start Ollama with `brew services start ollama`, and pull the model: `caffeinate -i ollama pull gemma4:26b` (about 18 GB; run it again if it stops). The model needs an Apple silicon Mac with plenty of memory. The commands are in `setup_assistant.md`, 2.1 and 2.2.
2. Give her a Signal number. The bot needs its own number, registered with signal-cli (`setup_assistant.md`, "Signal number (once)"). The bridge sets the bot's Signal profile name to her name and "(AI)", "Estia (AI)" by default, on its first start (and again after you rename her). Her profile picture is Estia's e., or your own PNG or JPEG with `avatar` in the config (`avatar = ""` for none).
3. Write the config. Copy `config.example.toml` to `~/.config/local-assistant/config.toml`, set it to mode 0600, and fill in the bot's number and your Signal account ID. Give her a name if you like: `name = "Estia"` is the default (ours says `name = "Tiffany"`; 1 to 20 letters, spaces or hyphens). The account ID is found by sending her a message and listing the senders (`setup_assistant.md`, 2.4). The bridge refuses a config file that other users can read. Never commit it.
4. Her personality (optional). Without `soul.md` she has her built-in style: warm, curious and playful. To give her yours, copy `soul.example.md` to `~/.config/local-assistant/soul.md` (mode 0600) and make it yours.
5. Check, then try it in the foreground. `scripts/doctor.sh` checks the Mac, the tools, the config, the model and the Signal number, and prints the fix for each ✗. Then run `scripts/run.sh`. On her first start she sends you a welcome message (what is set up, something to try, what stays on the Mac): reply to it from your phone. `scripts/check.sh` checks her health.
6. Make it always on. Stop the foreground bridge, then run `scripts/install-daemon.sh`. It fills in your user name and the repository path in the launchd template, checks the result, and installs it. It asks for your password once: that is sudo, which copies the service file to `/Library/LaunchDaemons` (and a log-rotation rule to `/etc/newsyslog.d`) and loads it, so the bridge starts at boot without a login. The bridge itself runs as your user. Then read "Keep it running" in `setup_assistant.md`, 2.8.
7. Add the other parts one at a time. Each part stays off until its section is in the config, and `scripts/doctor.sh` must show it without ✗ before you add the next (○ until its first use, then ✓). The sections and their helper scripts:

| Part | Config section | Script or step |
|---|---|---|
| Voice notes and calls | `[voice]` | `scripts/install-voice-daemon.sh` |
| Calendar and digest | `[calendar]`, and `[[calendar.sources]]` for several calendars | `scripts/set-calendar-url.sh [name]`; an Outlook calendar: `Calendars.Read` and `scripts/mail-login.sh <address>` |
| Mail | `[mail]` with `[[mail.accounts]]` | `scripts/mail-login.sh <address>` |
| Mail pre-filter (optional) | `[mail]` `jev_url` | AgentJev, run on the Mac; see `setup_assistant.md`, Mail |
| Lights and plugs | `[[tapo.devices]]` | `scripts/tapo-discover.sh` |
| Web search | `[web]` | `scripts/set-web-keys.sh` |
| Music | `[spotify]` | `scripts/spotify-login.sh` |
| Voice calls | `[calls]` (needs `[voice]`) | `tools/call-tunnel/` |
| Places and routes | `[maps]` (`home`, `daily_cap`, `region`) | `scripts/setup-google-maps.sh` (quick), or the key by hand: `setup_assistant.md` |
| Health extras | `[health]` `extra_checks` | `setup_assistant.md`, Health checks |

Keys and sign-ins go through these scripts, never through chat and never into the config file.

## Tests and checks

- `scripts/doctor.sh` checks the setup on the Mac, read-only: before the first start and after each optional part. For a part that is set up, it shows what the bridge's last real use of it did: ✓ working (with the time), ○ set up but not tested yet, or ✗ failing (with the fix). A file in place is not enough for ✓.
- Before every commit, `scripts/verify.sh` runs a privacy check (`scripts/privacy_scan.py`: no private files, keys, addresses, phone numbers, account IDs or home paths in the repo), then ruff (lint and format), mypy in strict mode and pytest. The tests run offline, with fakes for Signal, the voice service, the device helper and the model.
- Before every commit, `scripts/verify.sh` runs ruff (lint and format), mypy in strict mode and pytest. The tests run offline, with fakes for Signal, the voice service, the device helper and the model.
- The helpers have their own tests: `tools/voice` has Swift tests (`swift test`), and `tools/tapo` and `tools/ical` have pytest suites (`uv run pytest -q`).
- For the real model, `uv run python -m evals.gemma_suite` runs test cases against Gemma on your Mac. It uses the model in your config (`local-worker` when `model` is unset); `EVAL_MODEL=<tag>` runs it with another tag. To pass, every safety case must pass and at least 90% of the rest. The model doesn't give the same answer every time, so run the suite twice before trusting a prompt change. `EVAL_LANGUAGE=nl` runs the safety cases and three language cases with the rule line for that language. `EVAL_NAME=<name>` gives her another name (default Estia). Without `EVAL_SOUL` she has her built-in style, and two cases check it: a light joke when invited, none on a worry.
- For a phone walk-through, `docs/e2e-checklist.md` lists what to check from a real phone: texts, voice, calls, lights, mail, calendar, music, web search and languages.
- To check health without Signal, run `scripts/check.sh` on the Mac (for example over SSH). It covers the case where the bridge itself is down, since the bridge can't report its own outage. Other services can be watched with `[health] extra_checks`; none are watched by default.

## Built with

| Piece | What it does here |
|---|---|
| Gemma (Google's open model) | Answers, reads tool results, decides which tool to use |
| Ollama | Runs the model on the Mac, and is the first choice for web search |
| signal-cli | Signal, as a command-line client |
| WhisperKit | Speech-to-text on the Neural Engine (Whisper large-v3 turbo, told the language) |
| Kokoro | Text-to-speech in English, for voice notes and calls |
| Supertonic-3 (through FluidAudio) | Text-to-speech in the other ten languages |
| python-kasa | Talks to Kasa devices on the home network |
| Brave Search | Web search backup |
| Google Places API (New) and Routes API | Places and routes |
| Microsoft Graph | Read-only access to mail |
| Spotify Web API | Music control, by text |
| SQLite | One database file for memory, history and mail headers |
| AgentJev | Optional: a cheap yes/no check that marks obvious bulk mail as noise before Gemma. A separate service you run on the Mac (Apache-2.0) |

## What's next

- Places and routes: switch on with a Google Cloud key (`setup_assistant.md`, "Places and routes (once)").
- Terminal chat: talk to her from a terminal on the Mac.
- Agent bridge: next after terminal chat.
- Richer memory (a proposal, not built): profile sections with dates, `/undo`, a monthly review of what was learned, a small knowledge base from a folder of your notes, and searchable chat and call summaries. Live so far: the personality file, and profile facts sorted into categories (Family, Work, Music and so on).
- Spotify: a first live test against a real Spotify Premium account and app.
- Backups: the database and Signal's data aren't backed up yet.

## Good to know

- Everything depends on the Mac. If it's asleep or offline, so is Estia. Ollama starts when you log in, so a restart needs a login too: see `setup_assistant.md`, 2.8, for sleep, restarts and automatic login.
- Replies usually take a few seconds, at most about half a minute. The first one after a restart or a quiet spell is a little slower while the model loads.
- After a restart she skips messages older than ten minutes and tells you she was offline, so you can ask again.
- Voice notes must be in the current language (`/language`), up to three minutes long and 10 MB. Long spoken answers stop at the end of a sentence, and the rest is in the text.
- She runs on a local model and can get things wrong. Check anything important, especially web answers; that's what the Sources message is for.

## For developers

- Python 3.13, standard library only at runtime. Use uv, ruff (line length 120) and mypy --strict. Type hints everywhere.
- Test first: write a failing test before every change or bug fix. Prompt changes also go through the Gemma test set.
- New features: agree the design in an issue first. Design specs and plans are not kept in this repository.
- Conventional commits, with no attribution trailers.
- Secrets never go into the repo. They live in the config, the keychain or mode 0600 env files. Logs hold metadata only.
- Privacy check on every commit (optional): `git config core.hooksPath .githooks`. This replaces `.git/hooks`; if you already have a pre-commit hook, call `.githooks/pre-commit` from it instead. A reviewed exception gets `privacy-ok: <reason>` on its line.
- Where to start reading: `app.py` (wiring and queue), `brain.py` (the tool loop), `tools.py` (what the model may call), `prompts.py` (every text she writes) and `commands.py` (slash commands). `AGENTS.md` has the full layout and rules.
- Contributing: see [CONTRIBUTING.md](CONTRIBUTING.md). Report security problems privately, as described in [SECURITY.md](SECURITY.md).

## License

This project is MIT licensed (see [LICENSE](LICENSE)). The call helper in `tools/call-tunnel/` is AGPL-3.0-only, because the patch it contains modifies AGPL-3.0 code; see [its notice](tools/call-tunnel/LICENSE-NOTICE.md). Other third-party components and their licences are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
