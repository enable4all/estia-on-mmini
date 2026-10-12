# Estia

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/estia-hero-dark.png">
  <img src="docs/brand/estia-hero-light.png" alt="A stone garden seen from above, its gravel raked in rings around a Mac mini" width="100%">
</picture>

**A private AI thinking partner living at home, on your own Mac.** Estia (Εστία, the hearth) runs on one Mac at home and is reached only through Signal: encrypted texts, voice notes and calls. Once set up, she reads your mail and calendars, switches Kasa lights and plugs, and plays music on Spotify. Name her what you like. Ours is called Tiffany. Estia started as Tiffany, our own assistant, before she was opened up for every home.

- **Local.** Gemma, Google's open model, runs on the Mac through Ollama. No cloud AI service, no account with us, no telemetry. Chats, memory and profile stay on the Mac.
- **Read-only mail and calendars.** She reads mail and calendars. She never sends, moves, deletes or marks mail, and never changes a calendar event.
- **Lights and plugs follow a rule set per device.** Each device is `normal`, `ask first` or `never off` (switching it off is refused). A device change after outside text waits for your yes.
- **Only Signal.** Reached through Signal on your phone or Signal Desktop on your laptop. No web app and no open port: the bridge listens on nothing, so nothing on the network can connect to her. Messages travel end-to-end encrypted through Signal. Only the Signal accounts in the config get an answer. Other senders are ignored, never rejected. Calls go through Signal's relays, so your phone never learns the Mac's IP address. To keep your phone's address from the Mac as well, turn on "Always relay calls" in Signal on your phone.
- **Keys stay put.** Keys and sign-ins never appear in chat, logs or the repository. Only your Mac account can read them.

**Get started:** a Mac with Apple silicon and 16 GB of memory or more, plus a spare phone number for her Signal account.

```sh
git clone https://github.com/enable4all/estia-on-mmini.git ~/estia && cd ~/estia
scripts/setup.sh
```

Or open the folder in an AI coding assistant (Claude Code, Codex, ...) and say **"set me up"**. It follows [SETUP-WITH-YOUR-AI.md](SETUP-WITH-YOUR-AI.md) and stops for the few steps only you can do: the Signal SMS code, passwords and keys. `scripts/doctor.sh` checks the result.

Not affiliated with Signal, Spotify, Google, Microsoft, Bose or TP-Link.

Contents: [Meet Estia](#meet-estia) · [What she can do](#what-she-can-do) · [Reminders](#reminders-and-wake-up-calls) · [Messages she starts](#messages-she-starts-herself) · [Languages](#languages) · [A few conversations](#a-few-conversations) · [Safe and private](#safe-and-private-in-plain-words) · [What runs where](#what-runs-where) · [Words used here](#words-used-here) · [When something is off](#when-something-is-off) · [Slash commands](#slash-commands) · [Requirements](#requirements) · [Set it up](#set-it-up) · [Tests and checks](#tests-and-checks) · [Built with](#built-with) · [What's next](#whats-next) · [Good to know](#good-to-know) · [For developers](#for-developers) · [License](#license)

## Meet Estia

She answers first. On a call, when you think aloud, she may ask one question back. Her built-in style is warm, curious and playful. One short, light joke may follow an answer when the moment invites it. Never when you are worried or something serious is going on, and never in place of the answer.

In Signal, her profile reads Estia (AI), or the name set as `name` in the config. Ours reads Tiffany (AI). She speaks one language at a time: English, or one of ten others (see [Languages](#languages)). Her built-in style applies until a file, `soul.md`, replaces it. She reads `soul.md` but never edits it. It sets how she sounds, never what she may do.

## What she can do

| Area | Example | What you get |
|---|---|---|
| Chat | "What's a good question to ask about free will?" | A short, direct answer. The conversation is remembered. |
| Voice notes | Send a voice note | Transcribed on the Mac, answered in text, and optionally read back as a voice note (`/voice`) |
| Calls | Call her | A spoken conversation that can be interrupted. A short summary text follows a call of two or more turns. |
| Calendar | "What's on Friday?" | That day's events from each calendar, with times and places |
| Mail | "What needs my reply?" | Unanswered mail from the last week, numbered. "Read 2" opens one. |
| Morning digest | Nothing: it comes at 09:15 on its own | Today's calendar, tomorrow in one line, then the mail that needs a reply |
| Reminders | "Remind me at 17:00 to call the dentist", `/remind 20m pizza` | A text at that time: once, every day, every weekday or every week. A reminder is set only when the code's note says so. `/reminders` lists and cancels them. |
| Wake-up calls | "Call me at 7:00 to wake me up" | A text, then a Signal call at that time (once calls are set up), and one more try two minutes later if the call is not answered |
| Messages she starts | `/initiative on` (off until you do) | Now and then: a check-in, a calendar change or an early start, a Sunday look back, a spontaneous note. At most one a day, never between 22:00 and 08:00. `/quiet` pauses them, `/initiative off` stops them. |
| Music | "Play some jazz" | Spotify on your speakers (once set up) |
| Saved lists | "What should we play next?" | Ideas from a list you saved (songs, books, recipes) |
| Chords | "What are the chords of Spooky?" | Chords, key and tempo from a chord site, with a Sources message |
| Lights and plugs | "Turn off the living room lamp" | Kasa devices on the home network, never through the TP-Link cloud |
| The Mac | "How is the Mac doing?" | A few lines on its health. Real problems are reported without being asked. |
| Photos | Send a photo with a question | An answer about the photo. The photo is deleted after the first answer. |
| Web search | "Who won the match last night?" | An answer that names the site and how recent it is, plus a Sources message |
| Places and routes | "A good Italian place near me?" · "How long to the station by bike?" | Up to five places with rating, price and today's hours, or the time, distance and lines, with Google Maps links (once set up) |
| Projects | "What's next on <project>?" | Open to-do items and recent changes from a project on the Mac |
| Memory | "Remember that …", `/profile`, `/fix`, `/forget` | Saves what you tell her. Facts can be listed, corrected or removed. |

### Your day: calendar, mail and the digest

The morning digest comes every day at 09:15 (`digest_time` in the config): today's calendar first, then tomorrow in one line, then the mail that needs a reply. Declined invitations are left out. On quiet days she says so. If the Mac was off at 09:15, the digest comes when the bridge starts, if that is before noon.

One calendar or several can be read, each with a short name: Google calendars through their iCal address, and the Outlook calendars of the Microsoft 365 mailboxes she already reads. Outlook calendars are read-only by permission (`Calendars.Read`). With several calendars, each event carries its calendar's name ("Family: Football", "Work: 1:1 with Sam"). An invitation in two calendars is shown once. A calendar that can't be read is named with the reason, and the others are shown as usual. Events are read live and never cached. No calendar can be changed. Examples: what's on a day, whether a time is free, when something is ("When is the dentist?").

Google calendars use their secret iCal address. It works like a password, and it is kept in a file that only the calendar helper reads. A public address also works, but anyone can see a public calendar, so never make a private calendar public for her. Outlook calendars need `Calendars.Read` in the mailbox's sign-in. An administrator grants it once in Microsoft Entra, or the mailbox signs in again. Mail keeps working either way. Only the mailbox's own default calendar is read, not calendars others share with you.

Mail comes from the Exchange (Microsoft 365) mailboxes listed in the config. She reads mail only and never sends, moves, deletes or marks it. Headers sync every five minutes. A mail's text is fetched only when needed (when you ask, or once to sort it) and is never stored. Headers, a label and a one-line summary are kept for 30 days. A mail counts as answered once you reply in the same conversation. Mail you archive in Outlook drops off her list.

Gmail is not built in. [setup_assistant.md, "Gmail (not built in)"](setup_assistant.md#gmail-not-built-in) explains why and lists the options. Forwarding Gmail to a mailbox she reads works today.

### Music and saved lists

Music needs a Spotify Premium account and a Spotify developer app (`setup_assistant.md`, "Spotify (once)").

Music is text only. Spotify's rules don't allow voice control, so calls and voice notes never start it. Examples: "play some jazz", "play my liked songs on <speaker>", "next", "pause", "louder", "what's playing?".

A speaker starts at 30% and never goes above its cap (60% by default). The bridge also tries to wake idle Bose SoundTouch speakers listed in the config, over the home network. After an answer that used outside text (a web page, a mail or the calendar), music may pause or get quieter, but never start or get louder.

A saved list (songs, books to read) goes into the profile, where it counts toward the profile's 6,000-character limit. From it she suggests what to play or read next. Chords are looked up on the web, with sources.

### Lights and plugs

Kasa lights and plugs are controlled on the home network, never through the TP-Link cloud. Examples: "turn off the living room lamp", "dim the bedroom light to 30%, warm white", "is the heater on?".

Each device has one rule: `normal`, `ask first` (waits for a yes, which must come within two minutes) or `never off` (switching it off is refused). After each change a short note says what happened, for example "Devices: living room lamp off", and says when a device doesn't answer.

**Only Kasa devices are tested.** Older Kasa devices need no login. Newer Kasa firmware (KLAP) and Tapo devices need the TP-Link login. Tapo devices use the same library, but they are untested. Tapo firmware with the TPAP login can't be controlled yet. The name `tapo` in `tools/tapo/`, `[[tapo.devices]]` and `tapo.env` covers Kasa devices too.

### Photos

A photo, with or without a question: "what is this?", "what colour was it?". Up to four photos fit in one message. Follow-up questions within five minutes still see the photo. After that, the photo must be sent again. The file is deleted after the first answer.

### Web search, with sources

Ollama's web search runs first, and Brave Search is the backup. Brave is capped at 2,000 searches a month by default (`brave_monthly_cap`; set it to your Brave plan's limit). An answer runs at most two searches. The answer names the site. For news, scores and prices, it also says how recent the source is. Links come in a separate Sources message, not in the answer. Search queries go only to Ollama and Brave. Results are never stored or learned from.

### Places and routes

Status: built, waiting for a Google Cloud key (Places API (New) and Routes API; see `setup_assistant.md`, "Places and routes (once)").

Places come with rating, price and today's hours, for up to five places: "a good Italian place near me?", "is the museum open now?". Routes come with the time, distance and lines: "how long to the airport by train?". Google Maps links come in the Sources message.

"Near me" means the location last sent in Signal (attach, then Location). It is used for three hours and never logged. The message itself stays in the chat history, like any message. Without one, the home area is used. "How do I get to my 3 pm?" can use the event's place. A daily cap per API (`daily_cap`, 30 by default) keeps it inside Google's free allowance.

### Memory and personality

She remembers the current conversation and searches older ones ("What did I say about the trip?"). `/new` starts a fresh conversation and keeps the profile.

"Remember that …" saves a fact at once. She also picks up facts from what you say, and reports each one saved. A fact you told her to remember, or one from `/learn`, is never replaced or removed by something she picked up. `/profile` lists the facts and where each came from. `/fix` corrects a fact in your own words. `/forget` removes one.

`/learn <link>` reads a page, saves only the facts about you in it, and lists what was saved.

Without `soul.md`, her built-in style applies: warm, curious and playful. A `soul.md` replaces it and sets her tone and how long her answers are. `/soul` shows the file in use, or why none is.

### Calls

A call is answered after about two rings, with a greeting. She waits for about two seconds of quiet before she takes her turn, so a caller can think aloud. A caller can interrupt her at any time. Typing and other short noises are ignored, but a quick "yes" or "stop" counts. Once she has taken in a request, she says a short "Got it." (not after a few words such as "how are you?"; `acknowledge = false` in `[calls]` turns it off). Her answer follows when it is ready. `fillers = true` adds a "let me think" line when it takes a while. If the caller hangs up before she answers, she stops and texts that it was not done. Silence: a check-in after 90 seconds and goodbye after ten minutes (defaults). In English, a goodbye or a wrap-up ("bye-bye", "talk to you later", "take care", "that's all") ends the call once her answer is spoken, unless she asks something. After a call with two or more turns, a short summary follows by text.

Lights and plugs work on calls too. A device yes counts only once she has finished her question, so a yes said over the question is ignored.

### Reminders and wake-up calls

Your own words set a reminder: "remind me tomorrow at 7 to call mum", "in 20 minutes, the pizza", "every weekday at 8, the pills". `/remind 17:00 call the dentist` works even when the model is busy.

**A reminder is set only when the code's note says so.** The note is a separate message: "Reminder set (#12): Tue 13 Oct 07:00 · …". It shows the exact time, so a misheard time in a voice note shows at once. The first note adds "Only this note means a reminder is set.", and the welcome and `/help` say it too. What she writes in her own answer does not count. If none of the reminders, wake-ups or calls in her answer was set, the sentences that mention one are left out, in any of the 11 languages. The rest of her answer stays, and the code's line says why, in English: "No reminder was set: that time has passed." Her own words about a reminder are not the proof; the note is. A note may also say "No reminder was set", but only "Reminder set" counts. `/reminders` lists reminders with their times. `/reminders cancel N` removes one.

At the set time, a short text arrives: "Reminder: call the dentist". Reminders come at night too. Quiet hours and `/quiet` never pause them, because you set them. Repeating reminders keep their time across summer and winter time. If the Mac was off at that time, one note after the restart lists what was missed, marked late.

With "call me", a text comes first, then a Signal call (needs `[calls]`): "Good morning! It's 7:00." An unanswered call is tried once more two minutes later. A declined call is not tried again. A message from you ("I'm up") cancels the second try. A call is never placed more than five minutes late. At most three calls a day. Without `[calls]`, "call me" brings the text, and a second one two minutes later unless you wrote. Wake-up calls use signal-cli's experimental `startCall` (see [SECURITY.md](SECURITY.md#known-accepted-gaps)).

Only your own words make a reminder. A mail, calendar entry, web page, file, photo or shared audio file never does. A calendar event is not a reminder unless you ask for one.

**For anything that matters, keep your phone's own alarm.** Estia runs on one Mac. A power cut, an update, a crash or a Signal problem means no reminder and no call. A Focus or Sleep mode on the phone can silence her call unless her contact is allowed. A wake-up call is an extra.

### Messages she starts herself

She writes first now and then, but only after you turn it on: `/initiative on` in Signal, or `[initiative] enabled = true` in the config. It is off until you do. `/initiative off` turns it off again. Four kinds, each on or off in the config:

- **A check-in on something you said.** "I'll call the dentist tomorrow" may bring "Did you get hold of the dentist?" the day after. `/reminders` lists check-ins under their own heading. `/reminders cancel cN` drops one.
- **A calendar change, or an early start.** A meeting in the next 24 hours that moved or disappeared. At 20:00, tomorrow's first event, if it starts before the digest. The code writes this note from a fresh read of the calendar.
- **A look back.** Sunday at 19:00: three short lines about the week, from your own messages only, and one question.
- **A spontaneous note.** About once a week, at a random time, from what she knows about you and the moment. Not in the first week. She may skip it when nothing good comes to mind. Two unanswered in a row pause it for four weeks.

At most one a day, never between 22:00 and 08:00, and never while she is answering, a call is on, or you wrote in the last 15 minutes. Each message ends with a line that says why it came, like "(weekly look back · /quiet to pause)". `/quiet` pauses all of them until `/quiet off`. Reminders you set still come.

The code finds every trigger and checks every limit first. Gemma writes only the words, in a call of its own. That call never sees your mail, calendar, the web or the chat history. The code checks the text before anything is sent. `/status` counts replies within a day and uses of `/quiet`. How often she may write more is set by `improvise_per_week` in `[initiative]`. The code never raises it.

### The Mac itself

Ask "How is the Mac doing?" for a short status: the model and helper services, disk, memory, uptime, recent errors, and the state of Signal, mail, calendar, lights, music, voice, calls, web search (searches today and this month) and maps (requests today).

The Mac is checked every four hours. A problem is reported once it has lasted five minutes. A "Back to normal" note follows when it is fixed.

## Languages

Estia listens, thinks and speaks in **one language at a time**. Speech recognition is told the language for every voice note and call, so it never has to guess. She never switches by accident.

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

These are the languages Whisper large-v3 hears best that also have a voice. The rates are OpenAI's published per-language chart for large-v3, the better of Common Voice 15 and FLEURS (word error rate; character error rate for Japanese and Korean). Lower is better. Other languages are left out on purpose.

- **Pick it at setup** (`scripts/setup.sh` asks; English is the default), or set `language = "nl"` in the config.
- **Changed only by typing `/language` in Signal.** `/language` lists the 11 languages. `/language nl`, `/language dutch` or `/language nederlands` switches. Not by voice, not by asking her, and not during a call: a switch typed during a call waits until it ends. The choice survives a restart and wins over the config.
- **What follows the language:** her answers (in whatever language you write), voice notes, spoken answers and calls. On a call, the greeting, "one moment", "sorry, could you say that again?", "take your time" and goodbye are in that language. Her short "got it" ("Begrepen.", "Verstanden." ...) confirms a request was heard. A device yes counts in any of the 11 languages ("ja", "oui", はい ...).
- **What stays English:** everything the code writes: notes, the digest's headings, `/status`, `/help` and error messages. On a call, the device question ("Shall I switch heater off?") and a few rare lines are spoken in English, so they are always clear. On a call in another language, a device waiting for a yes gets only that question. The model's own words about it are left out, because in Dutch it once said a device had switched before the yes. Facts are kept as you said them.
- **Voices:** English uses Kokoro (`af_heart`, or `am_michael` when setup gave her a male name). `/voice` works as before. The other ten use Supertonic-3, voice F1. Its first use downloads about 264 MB and takes about a minute. After that, it is ready in a fraction of a second. It loads in the background when the bridge starts and after `/language`.
- **A voice service from before languages speaks English only.** Run `scripts/install-voice-daemon.sh` again. It asks for sudo, so run it yourself. The reply to `/language` says when this is needed. `scripts/doctor.sh` checks the language in use: the one set with `/language`, else the config's.

## A few conversations

Paraphrased; real wording varies. The music example assumes Spotify is set up.

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

- **Only the Signal accounts in the config get an answer.** Other senders are ignored, never rejected. Signal itself still confirms delivery, so a stranger can tell the number exists.
- **Mail and calendars are read-only.** She never sends, moves, deletes or marks mail, and never changes a calendar event.
- **Outside text is read as information, not as orders.** That covers web pages, search and map results, mail, calendar entries, project files, photos and shared audio files. Code rules reduce prompt injection but cannot rule it out. A device change after outside text waits for your yes, and device, profile and reminder changes have their own checks in code.
- **Only your words teach her.** Facts come from what you say, or from a page you ask her to learn from with `/learn`. Mail, calendar entries, web and map results never teach her anything.
- **Reminders come only from you.** She sets a reminder or a wake-up call only when you ask in your own words, never because a mail, a page, a calendar entry or a shared audio file says so. She calls only the allowed Signal accounts, and only for a reminder you set.
- **Gemma's part of a message she starts sees only your words.** The code decides when. Gemma's text uses only your own words, your profile and the moment: never mail, calendar entries, the web or a page. Calendar notes are written by the code from the calendar, with no model. A text that claims she did something, asks for a code or a password, or holds a link is not sent. The word checks read English, except for reminder and call claims, which cover all 11 languages; in other languages her rules and the Gemma test set carry the rest.
- **A shared audio file is not your voice.** An audio file shared from another app counts as someone else's words. She can summarise it. She never learns from it, never counts it as your yes, and never switches a device because of it. A voice note forwarded inside Signal may still count as yours.
- **Every device has a rule.** Each device is `normal`, `ask first` or `never off` (switching it off is refused). A device change in a message that is not about devices also waits for your yes.
- **No shell.** Gemma has none. Its project tools read only git-tracked documents, never secret-like files such as `.env`.
- **Chats, voice-note and call transcripts, memory and profile stay on the Mac.** They sit in one database on the Mac (`assistant.db`) until you delete it. Photos, voice audio and call audio are deleted after use. Mail bodies, web results and map results are never stored. Logs hold metadata only, never message text. Keys and config files are readable only by your Mac account, or kept in the login keychain.
- **Places stay rough.** For "near me", only an area rounded to about a kilometre goes to Google, or the location you sent. Your home address goes to Google when it is looked up once, and for routes from or to home. An answer that read your mail never sends anything to Google Maps.

Known gaps are listed in [SECURITY.md](SECURITY.md#known-accepted-gaps).

### What stays home, and what goes online

The model always runs on the Mac. Services on the internet are reached only for features that are turned on. Mail syncs every five minutes in the background. The digest reads the calendar each morning.

| Stays on the Mac | Goes online, only when used |
|---|---|
| The model (Gemma, via Ollama) | Signal, for your messages and calls (end-to-end encrypted) |
| Chats, memory and profile | Microsoft, to read mail and Outlook calendars (read-only) |
| Speech-to-text and text-to-speech | Google's iCal feeds, for Google calendars (read-only) |
| Health checks and project reading | Ollama's web search and Brave Search |
| Lights and plugs (local network) | Spotify, for music |
| | Google Maps, for places and routes |
| | Web pages: links you send her, and the top result of a Brave search |

Setup also downloads the model (from ollama.com), the voice models (from Hugging Face, on the first start), and, for calls, the signal-cli release and the call-helper sources (from GitHub and crates.io).

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
| "Estia is busy with another job, your answer is queued." | A long job is using the model | Wait. The answer follows. |
| "Estia isn't available right now, try again in a bit." | Ollama is not reachable | Check it with `curl -s http://127.0.0.1:11434/api/version` (or `ollama ps`). Start it with `brew services start ollama` |
| "Voice isn't available right now; please type it." | Voice service down, or still loading | Check it with `curl -s http://127.0.0.1:8150/health`. After a macOS update, allow about 15 minutes. |
| A call rings out, with a note | Voice service still loading | Try again once `curl -s http://127.0.0.1:8150/health` shows both speech parts as ready |
| "Calendar: the secret address no longer works" | The calendar's address was reset in Google | Save the new address with `scripts/set-calendar-url.sh` (with the calendar's name, if it has one) |
| "Calendars.Read is not granted yet" | An Outlook calendar's permission was not granted | An administrator grants it in Microsoft Entra, or run `scripts/mail-login.sh <address>` again (`setup_assistant.md`, "Calendar (once)") |
| "Mailbox <name> needs a new sign-in" | Microsoft ended the sign-in (password or MFA reset, or long unused) | Run `scripts/mail-login.sh <address>` again |
| "Spotify needs a new sign-in" | Spotify sign-ins last about six months | Run `scripts/spotify-login.sh` |
| "… isn't answering" for a device | Switched off at the wall, or a new network address | Check the plug. If its address changed, run `scripts/tapo-discover.sh`. |
| Replies stop | Signal's safety number changed | Confirm the number in Signal first. See `setup_assistant.md`, Operations. |
| "Late, I was offline …" with a list of reminders | The Mac or the bridge was down when they were due | Nothing: they came late, marked so. "(may be a repeat)" means one was being sent when it stopped. Keep your phone's alarm for what matters. |
| "Wake-up: 07:00 … No call: …" | The call could not be placed: voice still loading, calls off, a call already running, or three calls that day | The text says which. `/reminders` shows what was missed in the last week |
| A message you didn't ask for, ending "(… · /quiet to pause)" | One of the messages she starts herself (at most one a day, never between 22:00 and 08:00) | Reply if you like. `/quiet` pauses them, `/initiative off` turns them off; `[initiative]` in the config turns each kind off |
| No reply at all | Bridge or Mac is down | SSH into the Mac and run `scripts/check.sh`, then read `~/Library/Logs/local-assistant/bridge.log`. If SSH fails, the Mac itself is off or offline. |

## Slash commands

| Command | What it does |
|---|---|
| `/new` | Start a fresh conversation. The profile stays, and old chats stay searchable. |
| `/profile` | Numbered facts, with where each came from and when |
| `/fix N new text` | Correct fact N in your own words. It keeps its number, and from then on it counts as yours |
| `/forget N` or `/forget words` | Remove fact N, or the one fact containing the words (if several match, she lists them and asks for the number) |
| `/learn <link>` | Learn about you from a page, and list what was saved |
| `/status` | Model state, waiting messages, profile fill, last learning run, history size, Signal restarts, and each part's state: ✓ working (with the time), ○ set up but not tested yet, or ✗ failing (with the fix) |
| `/voice auto`, `on` or `off` | Spoken answers to voice notes (auto, the default), to every answer, or never |
| `/voice echo on` or `off` | Show what she heard before she answers a voice note |
| `/voice voices`, `/voice name <voice>` | List the voices, and switch voice notes and calls to one (kept after a restart) |
| `/soul` | Show the personality file in use, or why none is |
| `/language`, `/language <name or code>` | List the 11 languages (the current one marked), or switch to one: `/language nl`, `/language dutch` |
| `/remind [call] [daily\|weekdays] <when> <text>` | Set a reminder without the model. `<when>` is `17:00`, `2026-10-13 07:00`, `20m`, `2h` or `3d`; `call` adds a wake-up call. Example: `/remind call 07:00 wake up` |
| `/reminders`, `/reminders cancel N` or `all` | Your reminders with their numbers, and what was missed or failed in the last week; cancel one or all |
| `/initiative on`, `/initiative off`, `/initiative` | Let her write first now and then (off until you turn it on), stop it, or see which |
| `/quiet`, `/quiet off` | Pause the messages she starts herself, or let her write first again. Reminders you set always come |
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
  - `[health]` extra checks: any local web address or launchd service you want watched (nothing to install);
  - for calls: Homebrew's `openjdk` (Java 25 or newer), gnupg, `gh` (signed in to GitHub), rustup with toolchain 1.91.1, cmake, protobuf and coreutils. The build downloads signal-call-tunnel, RingRTC and Cargo crates from GitHub and crates.io: several GB, and a long build.

## Set it up

**With the setup script:** `scripts/setup.sh` does steps 1 to 6 below and asks only what it must. It picks the model for the Mac's memory (16 or 24 GB: `gemma4:12b`; 32 GB or more: `gemma4:26b`), asks for her name (Enter keeps Estia), registers her Signal number with you, finds your Signal account from a one-time word you send her, asks which language she speaks, writes the config, and installs the always-on service. Run it again: steps already done are skipped. You can rename her later with `name` in the config. Setup is done when `scripts/doctor.sh` shows no ✗ and her welcome message has arrived on your phone: reply to it to see her answer. Or let an AI coding assistant do it with you: [SETUP-WITH-YOUR-AI.md](SETUP-WITH-YOUR-AI.md).

**By hand**, in short. The full guide, with every sign-in and key, is `setup_assistant.md`.

1. **Prepare the Mac.** Install Homebrew. Clone the repository to `~/estia`, not into Desktop, Documents or Downloads. Install the tools from Requirements. Start Ollama with `brew services start ollama`, then pull the model: `caffeinate -i ollama pull gemma4:26b` (about 18 GB; run it again if it stops). The commands are in `setup_assistant.md`, 2.1 and 2.2.
2. **Give her a Signal number.** The bot needs its own number, registered with signal-cli (`setup_assistant.md`, "Signal number (once)"). On its first start, the bridge sets the bot's Signal profile name to her name and "(AI)", so "Estia (AI)" by default. It does this again after a rename. Her profile picture is Estia's e., or your own PNG or JPEG set as `avatar` in the config (`avatar = ""` for none).
3. **Write the config.** Copy `config.example.toml` to `~/.config/local-assistant/config.toml`, set it to mode 0600, and fill in the bot's number and your Signal account ID. Set her name with `name = "Estia"`, the default (ours says `name = "Tiffany"`; 1 to 20 letters, spaces or hyphens). Find the account ID by sending her a message and listing the senders (`setup_assistant.md`, 2.4). The bridge refuses a config file that other users can read. Never commit it.
4. **Personality (optional).** Without `soul.md`, her built-in style applies: warm, curious and playful. To write your own, copy `soul.example.md` to `~/.config/local-assistant/soul.md`, set it to mode 0600, and edit it.
5. **Check, then run in the foreground.** `scripts/doctor.sh` checks the Mac, the tools, the config, the model and the Signal number, and prints the fix for each ✗. Then run `scripts/run.sh`. On her first start she sends a welcome message: what is set up, something to try, and what stays on the Mac. Reply to it from your phone. `scripts/check.sh` checks her health.
6. **Make it always on.** Stop the foreground bridge, then run `scripts/install-daemon.sh`. It fills in your user name and the repository path in the launchd template, checks the result, and installs it. It asks for your password once. That is sudo, which copies the service file to `/Library/LaunchDaemons` (and a log-rotation rule to `/etc/newsyslog.d`) and loads it, so the bridge starts at boot without a login. The bridge itself runs as your user. Then read "Keep it running" in `setup_assistant.md`, 2.8.
7. **Add the other parts one at a time.** Each part stays off until its section is in the config. `scripts/doctor.sh` must show it without ✗ before you add the next (○ until its first use, then ✓). The sections and their helper scripts:

| Part | Config section | Script or step |
|---|---|---|
| Voice notes and calls | `[voice]` | `scripts/install-voice-daemon.sh` |
| Calendar and digest | `[calendar]`, and `[[calendar.sources]]` for several calendars | `scripts/set-calendar-url.sh [name]`; an Outlook calendar: `Calendars.Read` and `scripts/mail-login.sh <address>` |
| Mail | `[mail]` with `[[mail.accounts]]` | `scripts/mail-login.sh <address>` |
| Mail pre-filter (optional) | `[mail]` `jev_url` | AgentJev, run on the Mac; see `setup_assistant.md`, Mail |
| Lights and plugs | `[[tapo.devices]]` | `scripts/tapo-discover.sh`; newer Kasa and Tapo: `scripts/set-tapo-credentials.sh` |
| Web search | `[web]` | `scripts/set-web-keys.sh` |
| Music | `[spotify]` | `scripts/spotify-login.sh` |
| Voice calls | `[calls]` (needs `[voice]`) | `tools/call-tunnel/` |
| Places and routes | `[maps]` (`home`, `daily_cap`, `region`) | `scripts/setup-google-maps.sh` (quick), or the key by hand: `setup_assistant.md` |
| Health extras | `[health]` `extra_checks` | `setup_assistant.md`, Health checks |

Keys and sign-ins go through these scripts, never through chat and never into the config file. `scripts/setup.sh --extras` lists the optional parts again and shows how to add the ones you pick.

## Tests and checks

- `scripts/doctor.sh` checks the setup on the Mac, read-only: before the first start and after each optional part. For a part that is set up, it shows what the bridge's last real use of it did: ✓ working (with the time), ○ set up but not tested yet, or ✗ failing (with the fix). A file in place is not enough for ✓.
- Before every commit, `scripts/verify.sh` runs a privacy check (`scripts/privacy_scan.py`: no private files, keys, addresses, phone numbers, account IDs or home paths in the repo), then ruff (lint and format), mypy in strict mode and pytest. The tests run offline, with fakes for Signal, the voice service, the device helper and the model.
- The helpers have their own tests: `tools/voice` has Swift tests (`swift test`), and `tools/tapo` and `tools/ical` have pytest suites (`uv run pytest -q`).
- `uv run python -m evals.gemma_suite` runs test cases against the real model on your Mac. It uses the model in your config (`local-worker` when `model` is unset); `EVAL_MODEL=<tag>` runs another tag. A run passes when every safety case passes and at least 90% of the rest do. The model does not give the same answer every time, so run the suite twice before trusting a prompt change. `EVAL_LANGUAGE=nl` runs the safety cases and three language cases with the rule line for that language. `EVAL_NAME=<name>` gives her another name (default Estia). Without `EVAL_SOUL`, she has her built-in style, and two cases check it: a light joke when invited, none on a worry.
- For a phone walk-through, `docs/e2e-checklist.md` lists what to check from a real phone: texts, voice, calls, lights, mail, calendar, music, web search, languages, reminders and wake-up calls, and the messages she starts.
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
- Backups: the database and Signal's data aren't backed up yet.

## Good to know

- Everything depends on the Mac. If it's asleep or offline, so is Estia. Ollama starts when you log in, so a restart needs a login too: see `setup_assistant.md`, 2.8, for sleep, restarts and automatic login.
- Replies usually take a few seconds, at most about half a minute. The first one after a restart or a quiet spell is a little slower while the model loads.
- After a restart she skips messages older than ten minutes and tells you she was offline, so you can ask again. Reminders that came due meanwhile arrive in one late note.
- Voice notes must be in the current language (`/language`), up to three minutes long and 10 MB. Long spoken answers stop at the end of a sentence, and the rest is in the text.
- She runs on a local model and can get things wrong. Check anything important, especially web answers. The Sources message is there for that.

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
