# Estia

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/estia-hero-dark.png">
  <img src="docs/brand/estia-hero-light.png" alt="A stone garden seen from above, its gravel raked in rings around a Mac mini" width="100%">
</picture>

**A private AI thinking partner living at home, on your own Mac.** Estia (Εστία, the hearth) runs on one Mac at home and is reached only through Signal: encrypted texts, voice notes and calls. Once set up, she reads your mail and calendars, switches Kasa lights and plugs, and plays music on Spotify. Name her what you like. Ours is called Tiffany. Estia started as Tiffany, our own thinking partner, before she was opened up for every home.

Website: [estia.fun](https://estia.fun)

- **Local.** Gemma, Google's open AI model, runs on the Mac through the Ollama app; a cloud model, which Ollama runs on its own servers, is refused. No cloud AI service, no account with us, no telemetry. Chats, memory and profile stay on the Mac.
- **Read-only mail and calendars.** She reads mail and calendars. She never sends, moves, deletes or marks mail, and never changes a calendar event.
- **Lights and plugs follow a rule set per device.** Each device is `normal`, `ask first` or `never off` (switching it off is refused). A device change after outside text waits for your yes.
- **Only Signal.** Reached through Signal on your phone or Signal Desktop on your laptop. No web app and no open port: the bridge listens on nothing, so nothing on the network can connect to her. Messages travel end-to-end encrypted through Signal. Only the Signal accounts in the config get an answer. Other senders are ignored, never rejected. Calls go through Signal's relay servers, so your phone never learns the Mac's internet (IP) address. To keep your phone's address from the Mac as well, turn on "Always relay calls" in Signal on your phone.
- **Keys stay put.** Keys and sign-ins never appear in chat, logs or the code repository. Keys, passwords, the Signal captcha link, the SMS code and the PIN go into hidden prompts in your own Terminal: never on screen, and never on a program's command line, where other programs on the Mac can read them. Only her own hidden macOS account, `_estia`, can read the keys: a program you run needs your Mac password to read them, as long as no admin rule skips that password (the security step checks).

**Get started:** a Mac with Apple silicon and 16 GB of memory or more, plus a spare phone number for her Signal account.

```sh
git clone https://github.com/enable4all/estia-on-mmini.git ~/estia && cd ~/estia
scripts/setup.sh
```

Or open the folder in an AI coding assistant (Claude Code, Codex, ...) and say **"set me up"**. It follows [SETUP-WITH-YOUR-AI.md](SETUP-WITH-YOUR-AI.md) and stops for the few steps only you can do: the Signal SMS code, passwords and keys. `scripts/doctor.sh` checks the result.

With calls set up, calls work once her voice is ready (up to about 15 minutes after an install, a move or a macOS update); she texts you then. In quiet hours (22:00 to 08:00 by default) that text waits until they end, unless a call rang meanwhile.

Not affiliated with Signal, Spotify, Google, Microsoft, Bose, TP-Link or OpenClaw.

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
| Reminders | "Remind me at 17:00 to call the dentist", `/remind 20m pizza` | A text at that time: once, every day, every weekday or every week. A reminder is set only when a note from the code, not from the AI, says so. `/reminders` lists and cancels them. |
| Wake-up calls | "Call me at 7:00 to wake me up" | A text, then a Signal call at that time (once calls are set up), and one more try two minutes later if the call is not answered |
| Messages she starts | `/initiative on` (off until you do) | Now and then: a check-in, a calendar change or an early start, a Sunday look back, a spontaneous note. At most one a day, never between 22:00 and 08:00. `/quiet` pauses them, `/initiative off` stops them. |
| Language practice | `/babble nl` | A short conversation in Dutch (or one of eight other languages) at level A2 or B1, then a recap of at most three mistakes as a text |
| Music | "Play some jazz" | Spotify on your speakers (once set up) |
| Saved lists | "What should we play next?" | Ideas from a list you saved (songs, books, recipes) |
| Chords | "What are the chords of Spooky?" | Chords, key and tempo from a chord site, with a Sources message |
| Lights and plugs | "Turn off the living room lamp" | Kasa devices on the home network, never through the TP-Link cloud |
| The Mac | "How is the Mac doing?" | A few lines on its health. Real problems are reported without being asked. |
| Photos | Send a photo with a question | An answer about the photo. The photo is deleted after the first answer. |
| Web search | "Who won the match last night?" | An answer that names the site and how recent it is, plus a Sources message |
| Places and routes | "A good Italian place near me?" · "How long to the station by bike?" | Up to five places with rating, price and today's hours, or the time, distance and lines, with Google Maps links (once set up) |
| Projects | "What's next on <project>?" | Open to-do items and recent changes from a project on the Mac. An extra, off by default. |
| Memory | "Remember that …", `/profile`, `/fix`, `/forget` | Saves what you tell her. Facts can be listed, corrected or removed. |

### Your day: calendar, mail and the digest

The morning digest comes every day at 09:15 (`digest_time` in the config): today's calendar first, then tomorrow in one line, then the mail that needs a reply. Declined invitations are left out. On quiet days she says so. If the Mac was off at 09:15, the digest comes when the bridge starts, if that is before noon.

One calendar or several can be read, each with a short name: Google calendars through their secret calendar link, and the Outlook calendars of the Microsoft 365 mailboxes she already reads. Outlook calendars are read with Microsoft's read-only calendar permission, `Calendars.Read`. With several calendars, each event carries its calendar's name ("Family: Football", "Work: 1:1 with Sam"). An invitation in two calendars is shown once. A calendar that can't be read is named with the reason, and the others are shown as usual. Events are read live and never cached. No calendar can be changed. Examples: what's on a day, whether a time is free, when something is ("When is the dentist?").

A Google calendar's secret link is the one Google Calendar shows as "Secret address in iCal format". It works like a password, and it is kept in a file that only the calendar helper (a small program of hers) reads. A public link also works, but anyone can see a public calendar, so never make a private calendar public for her. Outlook calendars need `Calendars.Read` in the mailbox's sign-in. An administrator grants it once in Microsoft Entra, Microsoft's admin site, or the mailbox signs in again. Mail keeps working either way. Only the mailbox's own default calendar is read, not calendars others share with you.

Mail comes from the Exchange (Microsoft 365) mailboxes listed in the config. She reads mail only and never sends, moves, deletes or marks it. The headers (sender, recipients, subject, dates, and marks such as read, attachment or important) sync every five minutes. A mail's text is fetched only when needed (when you ask, or once to sort it) and is never stored. Headers, a label and a one-line summary are kept for 30 days. A mail counts as answered once you reply in the same conversation. Mail you archive in Outlook drops off her list.

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

Ollama's web search runs first, and Brave Search is the backup. Brave is capped at 2,000 searches a month by default (`brave_monthly_cap`; set it to your Brave plan's limit). An answer runs at most two searches. The answer names the site. For news, scores and prices, it also says how recent the source is. Links come in a separate Sources message, not in the answer. Search queries go only to Ollama and Brave. Mail, calendar and project text never go into a query: an answer that read them searches nothing. Results are never stored or learned from.

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

A call is answered after about two rings, with a greeting. She waits for about two seconds of quiet before she takes her turn, so a caller can think aloud. A caller can interrupt her at any time. Typing and other short noises are ignored, but a quick "yes" or "stop" counts. With `barge = "speech"` in `[calls]`, a speech detector on the Mac (Silero VAD) decides what stops her, so road noise rarely cuts her off; until it is ready, loudness decides. A whisper may not stop her; a radio or a passenger talking does. Her own voice from a loud speaker next to the microphone (a laptop on Signal Desktop) is told apart from the caller's and does not stop her: she carries on, as after a cough, and what the microphone heard of her own voice never reaches the model. Once she has taken in a request, she says a short "Got it." (not after a few words such as "how are you?"; `acknowledge = false` in `[calls]` turns it off). Her answer follows when it is ready. `fillers = true` adds a "let me think" line when it takes a while. If the caller hangs up before she answers, she stops and texts that it was not done. Silence: a check-in after 90 seconds and goodbye after ten minutes (defaults). In English, a goodbye or a wrap-up ("bye-bye", "talk to you later", "take care", "that's all") ends the call once her answer is spoken, unless she asks something. After a call with two or more turns, a short summary follows by text.

At the start of a call she knows today's numbers, never the titles: how many events are left, when the next one starts and the last one ends, and how many mails need a reply or an action. With the calendar or mail set up, she may offer the rundown once ("Three things today, the next at two. Want the rundown?"). Titles, places and senders are read only after your yes, as in a text. The numbers come from the code; she says them in the call's language.

Lights and plugs work on calls too. A device yes counts only once she has finished her question, so a yes said over the question is ignored.

**Calls wait for her voice.** Her voice takes up to about 15 minutes to load after an install, a move or a macOS update: the Mac's Neural Engine compiles it for her. Meanwhile a call is not answered, and a note says why: "I can't take calls yet: my voice is still loading (up to 15 minutes after an install, a move or a macOS update). I'll text you when it's ready." A second call within ten minutes gets no second note. Once her voice is ready, a code-written note follows: "My voice is ready: you can call me now." It goes at once to each allowed account whose call rang meanwhile, and to every allowed account when her voice was still loading two minutes after the bridge started. In quiet hours (22:00 to 08:00 by default, also when `[initiative] quiet_hours` is empty) it waits until they end, unless a call rang meanwhile. Each account gets it at most once per bridge start, except that every later "I'll text you" note is followed by one more. While `/lock` is on it waits for `/unlock`.

### Reminders and wake-up calls

Your own words set a reminder: "remind me tomorrow at 7 to call mum", "in 20 minutes, the pizza", "every weekday at 8, the pills". `/remind 17:00 call the dentist` works even when the model is busy.

**A reminder is set only when a note from the code, not from the AI, says so.** The note is a separate message: "Reminder set (#12): Tue 13 Oct 07:00 · …". It shows the exact time, so a misheard time in a voice note shows at once. The first note adds "Only this note means a reminder is set.", and the welcome and `/help` say it too. What she writes in her own answer does not count. If none of the reminders, wake-ups or calls in her answer was set, the sentences that mention one are left out, in any of the 11 languages. The rest of her answer stays, and a line from the code says why, in English: "No reminder was set: that time has passed." Her own words about a reminder are not the proof; the note is. A note may also say "No reminder was set", but only "Reminder set" counts. `/reminders` lists reminders with their times. `/reminders cancel N` removes one.

At the set time, a short text arrives: "Reminder: call the dentist". Reminders come at night too. Quiet hours and `/quiet` never pause them, because you set them. Repeating reminders keep their time across summer and winter time. If the Mac was off at that time, one note after the restart lists what was missed, marked late.

With "call me", a text comes first, then a Signal call (needs `[calls]`): "Good morning! It's 7:00." An unanswered call is tried once more two minutes later. A declined call is not tried again. A message from you ("I'm up") cancels the second try. A call is never placed more than five minutes late. At most three calls a day. Without `[calls]`, "call me" brings the text, and a second one two minutes later unless you wrote. Wake-up calls use an experimental feature of signal-cli, `startCall` (see [SECURITY.md](SECURITY.md#known-accepted-gaps)).

Only your own words make a reminder. A mail, calendar entry, web page, file, photo or shared audio file never does. A calendar event is not a reminder unless you ask for one.

**For anything that matters, keep your phone's own alarm.** Estia runs on one Mac. A power cut, an update, a crash or a Signal problem means no reminder and no call. A Focus or Sleep mode on the phone can silence her call unless her contact is allowed. A wake-up call is an extra.

### Messages she starts herself

She writes first now and then, but only after you turn it on: `/initiative on` in Signal, or `[initiative] enabled = true` in the config. It is off until you do. `/initiative off` turns it off again. Four kinds, each on or off in the config:

- **A check-in on something you said.** "I'll call the dentist tomorrow" may bring "Did you get hold of the dentist?" the day after. `/reminders` lists check-ins under their own heading. `/reminders cancel cN` drops one.
- **A calendar change, or an early start.** A meeting in the next 24 hours that moved or disappeared. At 20:00, tomorrow's first event, if it starts before the digest. The code writes this note from a fresh read of the calendar.
- **A look back.** Sunday at 19:00: three short lines about the week, from your own messages only, and one question.
- **A spontaneous note.** About once a week, at a random time, from what she knows about you and the moment. Not in the first week. She may skip it when nothing good comes to mind. Two unanswered in a row pause it for four weeks.

At most one a day, never between 22:00 and 08:00, and never while she is answering, a call is on, or you wrote in the last 15 minutes. Each message ends with a line that says why it came, like "(weekly look back · /quiet to pause)". `/quiet` pauses all of them until `/quiet off`. Reminders you set still come.

The code finds every reason to write and checks every limit first. The AI writes only the words, in a separate request that never sees your mail, calendar, the web or the chat history. The code checks the text before anything is sent. `/status` counts replies within a day and uses of `/quiet`. How often she may write more is set by `improvise_per_week` in `[initiative]`. The code never raises it.

### The Mac itself

Ask "How is the Mac doing?" for a short status: the model and helper services, disk, memory, uptime, recent errors, and the state of Signal, mail, calendar, lights, music, voice, calls, web search (searches today and this month) and maps (requests today).

The Mac is checked every four hours. A problem is reported once it has lasted five minutes. A "Back to normal" note follows when it is fixed.

### Projects (extra, off by default)

Setup asks whether she should see projects on this Mac, which folder, and how much. Most people: no.

- **summary**: the README, open to-do items and recent commits
- **docs**: also the project's documents
- **code**: also its source code

Only files git tracks, never secret-like names such as `.env`, never outside the folder you named. The whole README is read on request at every level, a page at a time. A name heard on a call is matched to the nearest project: "Photo Book" is taken as fotobook, "garden app" as garden_app. When two names sound alike, or none is close, she asks ("Do you mean fotobook?") instead of guessing. Asked what she can see, she names each project's level and how to change it: `[projects]` in the config. `scripts/setup.sh --extras` switches projects on later.

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
- **A voice service from before languages speaks English only.** Run `scripts/install-voice-daemon.sh` again. It asks for your Mac password, so run it yourself, in your own Terminal. The reply to `/language` says when this is needed. `scripts/doctor.sh` checks the language in use: the one set with `/language`, else the config's.

## Language practice

`/babble nl` starts practice in Dutch: a short conversation at your level, and a recap of your mistakes when it ends.

- **Eight languages:** English, Dutch, German, Spanish, French, Portuguese, Japanese and Italian (`/babble` lists them). Russian, Polish and Korean are listed as not yet: in a test of the local model, too many of its Russian replies broke the practice rules, its own Polish had errors and its Korean corrections were less reliable.
- **Two levels:** every language starts at A2; `/babble nl b1` starts at B1. The level is set only at the start.
- **Her answers:** in the practised language, one to three short sentences ending in one question. A mistake is not named: the right form comes back in her answer. A typed message may get one first line, "→" and the corrected words; a voice note or a call never does. Voice notes and calls are heard and answered in the practised language; a question in another language can be typed.
- **The recap:** at most three mistakes, as a text: the words written, the corrected words, and one line why, in the language set with `/language`. The code checks each point first: the words must be the user's own, the change small and real (not only capitals or punctuation), a "not" never dropped, and the reason not empty and not "this is correct". With nothing to note: "No mistakes to note: well done."
- **Started only by typing `/babble`:** never by voice, a call or asking her. Ended by `/babble off`, the end of a practice call (the recap replaces the call summary), or 30 minutes without a practice message (the recap says so). In quiet hours (22:00 to 08:00 by default) that last recap waits until they end, or until the next message. A plain `/babble` resumes the last language and level.
- **During practice:** no lights, Spotify, mail, calendar, web search, new reminders or saved facts; ask again after `/babble off`. Reminders already set still arrive, and a call she places for one is an ordinary call. Her own messages (check-ins, the look back, the spontaneous note) never go out while practice runs. `/language` is refused until `/babble off`. `/lock` stops practice too: no answers, and a recap that falls due meanwhile comes after `/unlock`.
- **Kept apart:** practice messages are kept like other messages, in a conversation of their own. They are never learned from, never used for check-ins or the look back, and never found by the history search. No list of mistakes is kept, only the last language and level.
- **Limits:** corrections come from a local model and can be wrong. No pronunciation scoring. In Dutch, French, Russian and Japanese, speech recognition can write the right form for a mistake that was said, so after voice notes or a call in those languages the recap says it is based on what was heard. `/voice echo on` shows what was heard.

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
- **Only the devices she knows.** A message from a new linked device on your account gets no answer and never reaches the AI; a note from the code goes to your account instead, so your phone sees it too. `/devices ok N`, sent from a device she already knows, lets it in. Calls carry no device number, so a new linked device can still call.
- **An emergency stop on your phone.** `/lock` stops her answers and her calls until `/unlock` comes from the same device after a short wait. It holds after a restart and needs no Mac.
- **Her own account on the Mac.** She runs as `_estia`, a hidden macOS account with no password and no login. Her data and keys are readable only by it; her code and every program she runs are copies owned by root, the Mac's all-powerful admin account. A program you run, an AI session included, reads none of it without your Mac password, as long as no admin rule skips the password; a program of yours can catch your password with a fake `sudo` that your Terminal finds before the real one, so type `/usr/bin/sudo`. Her code changes only through an update (`estia-deploy`), and a program of yours can change what your next update puts live; the update shows the list of changes and the files they touch, not the code itself. What she sends to Ollama, which runs as you, passes through your account (SECURITY.md).
- **The model stays on the Mac.** A cloud model, which Ollama runs on its own servers, is refused: by its name when the config is read, and by Ollama's own list at the start, every five minutes and before every chat. Once the check sees one, no message goes to it: answers pause, a note from the code says why, and doctor shows ✗ with the fix.
- **A security step in setup.** Remote login (SSH) with keys only, no admin rule that skips your password, her files private, and her Registration Lock (a Signal PIN on her number): each shown with its state, and changed only on your yes (`scripts/harden.sh`). FileVault, automatic login, the screen lock and a few more are shown with their fix, never changed.
- **The nuclear option.** `scripts/harden.sh --nuclear` adds the strictest settings: the Mac's firewall, stealth mode (its own question, default no), python3 and ruby (programs that run scripts) off the firewall's list of programs let in, and a login keychain that locks after 15 minutes and at sleep. It says first, plainly, that they may stop other services on this Mac: programs that other devices reach (a file share, a media server, a page you open from your laptop) stop answering them unless you allow each one; with stealth mode the Mac no longer answers ping, the usual "are you there?" check; with the keychain lock, macOS asks for your password more often. Each one is asked first and changed only on your yes, and it is never part of setup. The firewall question names the programs that listen for other devices now and asks about keeping each one; with the firewall on, doctor names a new one later, with the two commands that let it through. Without `--nuclear`, these settings are one neutral line in the security step and in doctor, never a warning. Nothing turns off what a Mac already has.
- **Mail and calendars are read-only.** She never sends, moves, deletes or marks mail, and never changes a calendar event.
- **Outside text is read as information, not as orders.** That covers web pages, search and map results, mail, calendar entries, project files, photos and shared audio files. Code rules reduce prompt injection (orders hidden in that text) but cannot rule it out. A device change after outside text waits for your yes, and device, profile and reminder changes have their own checks in code.
- **Only your words teach her.** Facts come from what you say, or from a page you ask her to learn from with `/learn`. Mail, calendar entries, web and map results never teach her anything.
- **Reminders come only from you.** She sets a reminder or a wake-up call only when you ask in your own words, never because a mail, a page, a calendar entry or a shared audio file says so. She calls only the allowed Signal accounts, and only for a reminder you set.
- **When the AI writes part of a message she starts, it sees only your words, your profile and the moment.** Never mail, calendar entries, the web or a page. The code decides when. Calendar notes are written by the code from the calendar, with no model. A text is not sent when it claims she did something, asks for a code or a password, or holds a link: any web address, with or without https:// or www., on any ending, such as "example.ch/x". The words that ask for a code or a password, and claims of a reminder or a call, are checked in all 11 languages. The other word checks read English; in other languages her rules and the AI test set carry the rest.
- **A shared audio file is not your voice.** An audio file shared from another app counts as someone else's words. She can summarise it. She never learns from it, never counts it as your yes, and never switches a device because of it. A voice note forwarded inside Signal may still count as yours.
- **Every device has a rule.** Each device is `normal`, `ask first` or `never off` (switching it off is refused). A device change in a message that is not about devices also waits for your yes.
- **No shell.** The AI has no command line: it cannot type commands on the Mac. With Projects on, she reads only files git tracks in the folder you named, at the level you chose, never secret-like files such as `.env`.
- **Chats, voice-note and call transcripts, memory and profile stay on the Mac.** They sit in one database on the Mac (`assistant.db`) until you delete it. Photos, voice audio and call audio are deleted after use. Mail bodies, web results and map results are never stored. Logs hold only what happened and when, never message text. Keys and config files are readable only by her own account (`_estia`), and your copies of keys stay in your login keychain.
- **Places stay rough.** For "near me", only an area rounded to about a kilometre goes to Google, or the location you sent. Your home address goes to Google when it is looked up once, and for routes from or to home. The address stays in the config file only: the database keeps the rounded area and a scrambled tag of the address, to notice when it changes, never the address itself. A Directions link to or from home names that rounded area, never the address. An address an earlier version kept in the database, in those rows or in a Directions link it sent, is removed at the next start. An answer that read your mail or your projects never sends anything to Google Maps.

Known gaps are listed in [SECURITY.md](SECURITY.md#known-accepted-gaps).

### What stays home, and what goes online

The model always runs on the Mac. Outside services are reached only for Signal, links you send and the extras you switch on. Mail syncs every five minutes in the background. The digest reads the calendar each morning.

| Stays on the Mac | Goes online, only when used |
|---|---|
| The AI model (Gemma, run by the Ollama app) | Signal, for your messages and calls (end-to-end encrypted) |
| Chats, memory and profile | Microsoft, to read mail and Outlook calendars (read-only) |
| Speech-to-text and text-to-speech | Google's calendar links, for Google calendars (read-only) |
| Health checks and project reading | Ollama's web search and Brave Search |
| Lights and plugs (home network) | Spotify, for music |
| | Google Maps, for places and routes |
| | Web pages: links you send her, and the top result of a Brave search |

Setup also downloads the model (from ollama.com) and, for calls, signal-cli and the call helper's source code (from GitHub and crates.io). It runs Homebrew, the installer for Mac tools, with Homebrew's usage reports (analytics) off; brew commands you type yourself keep your own Homebrew setting.

The voice models come from Hugging Face, an online library of AI models. Whisper's files (listening) and the speech detector's are fixed to one version and checked against their fingerprints on the Mac at every start: a changed or extra file is never loaded. Whisper's are downloaded once, and Whisper then starts with no network; a Whisper folder with a changed or extra file is set aside and downloaded again at that version. The speaking voices (Kokoro for English at the first start, Supertonic-3 for the other ten languages on first use) are downloaded as published, with no fixed version and no check of their files.

## What runs where

```
 Phone or laptop (Signal app)
        │  Signal (end-to-end encrypted)
        ▼
 signal-cli               child process of the bridge (JVM build for calls)
        │  a pipe inside the Mac (no network port)
        ▼
 Bridge: her main program (Python, no add-ons)
   ├── Database (SQLite)  memory, history, mail headers
   ├── Gemma via Ollama   the AI model on the Mac, with its tools
   ├── Voice service      speech-to-text and text-to-speech (Neural Engine)
   └── Helpers            lights and plugs, calendar, call audio
```

| Part | Where in the repo | How it runs |
|---|---|---|
| Bridge | `src/local_assistant/` | Always on: a background service that starts with the Mac (`com.local-assistant.bridge`), as her own user `_estia`, from a copy of the code owned by root in `/usr/local/estia` |
| Voice service | `tools/voice/` (Swift) | Always on, as `com.local-assistant.voice`, as `_estia`, reachable only from the Mac itself |
| Device helper | `tools/tapo/` | Started per command, in its own Python environment |
| Calendar helper | `tools/ical/` | Started per read, in its own Python environment |
| Call helper | Build script, pins and patch in `tools/call-tunnel/` (built on your machine) | Started by signal-cli for each call |
| Scripts | `scripts/` | Run, health check, install, the move into her own user, the security step, sign-ins and keys |
| Her commands | `scripts/estia`, `scripts/estia-deploy`, installed in `/usr/local/estia/bin`, owned by root | `/usr/bin/sudo /usr/local/estia/bin/estia doctor`, `logs`, `check`, sign-ins and keys, as `_estia`; `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` puts an update live |
| Docs | `setup_assistant.md`, `docs/e2e-checklist.md` | Full guide and the phone checklist |

### Where things live on the Mac

| What | Where | Notes |
|---|---|---|
| Config | `/var/estia/.config/local-assistant/config.toml` | Readable only by `_estia` (mode 0600), never in git. Edit it with `/usr/bin/sudo -u _estia -e /var/estia/.config/local-assistant/config.toml` (your editor works on a copy), then `/usr/bin/sudo /usr/local/estia/bin/estia restart` |
| Personality | `/var/estia/.config/local-assistant/soul.md` | Readable only by `_estia` (mode 0600), written by you: make it with `/usr/bin/sudo -u _estia install -m 600 /usr/local/estia/current/soul.example.md /var/estia/.config/local-assistant/soul.md`, then edit it with `/usr/bin/sudo -u _estia -e` |
| Service keys | `calendar.env`, `tapo.env`, `web.env` in `/var/estia/.config/local-assistant/`; mail and Spotify token files in `/var/estia/Library/Application Support/local-assistant/` | Readable only by `_estia` (mode 0600), each read only by the part that needs it. The key scripts pass a value through a pipe into `/usr/bin/sudo /usr/local/estia/bin/estia secret set`, never on a command line |
| Memory and history | `/var/estia/Library/Application Support/local-assistant/assistant.db` | One SQLite file |
| Her Signal account | `/var/estia/.local/share/signal-cli` | Only `_estia` can open it (mode 0700); her keys and her Registration Lock |
| Logs | `/var/estia/Library/Logs/local-assistant/bridge.log` | Only what happened and when, never message text. Kept small by the bridge itself. Read them with `/usr/bin/sudo /usr/local/estia/bin/estia logs` |
| Code | `/usr/local/estia`: `releases/`, `current`, `bin/` | Owned by root, readable by all, changed only by root (mode 0755): the services run only from here. Your copy of the repository (the checkout) is a source, never run. `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` makes a new release from it and switches back on a failed check |

An install from before her own user keeps the same files in your home (`~/.config/local-assistant`, `~/Library/Application Support/local-assistant`, `~/.local/share/signal-cli`, `~/Library/Logs/local-assistant`) until `scripts/migrate-to-own-user.sh` moves them. After the move, those folders leave your home and are held for 7 days in `/usr/local/estia/rollback`, where only root reaches them, for `/usr/bin/sudo /usr/local/estia/bin/estia rollback`.

## Words used here

- Signal: the encrypted messaging and calling app. Estia is a contact in it.
- signal-cli: Signal as a program without a window. The bridge runs it in the background.
- Bridge: her main program. It runs in the background, passes messages between Signal and the AI, and runs her tools.
- Gemma: Google's open AI model. It reads your messages and writes the answers.
- Ollama: the app that runs AI models on the Mac.
- Cloud model: a model Ollama runs on its own servers instead of the Mac. Estia refuses one.
- Tool: an action the AI may take, such as checking the calendar or the lights.
- Calendar link (iCal address): a private link to a calendar. Treat it like a password.
- LaunchDaemon: a macOS background service that starts with the Mac.
- `_estia`: her own hidden macOS account. She runs as it, and only it can read her data.
- root: the Mac's all-powerful admin account.
- sudo: the command that runs another command with admin rights, after your Mac password. Type it as `/usr/bin/sudo`.
- Release: a copy of the code owned by root in `/usr/local/estia/releases`; `current` points at the one that runs.
- SSH: remote login to the Mac's Terminal from another computer.
- Firewall: the part of macOS that decides which programs other devices may reach.
- Prompt injection: orders hidden in text she reads (a page, a mail), written to make the AI follow them.
- Neural Engine: the Mac's built-in AI chip, used here for speech.

## When something is off

| You see | Likely cause | What to do |
|---|---|---|
| Something is missing, or a part does not work | Setup not finished, a part not set up yet, or a part failing | Run `/usr/bin/sudo /usr/local/estia/bin/estia doctor` (read-only; `scripts/doctor.sh` before her own user), or send `/status`. Each ✗ line gives the reason and the command that fixes it |
| "Estia is busy with another job, your answer is queued." | A long job is using the model | Wait. The answer follows. |
| "Estia isn't available right now, try again in a bit." | Ollama is not reachable | Check it with `curl -s http://127.0.0.1:11434/api/version` (or `ollama ps`). Start it with `brew services start ollama` |
| "Answers are paused: Ollama lists the model … as a cloud model …" | Her model's name in Ollama now points to a cloud model, which runs on Ollama's servers | Run `/usr/bin/sudo /usr/local/estia/bin/estia doctor`: its Model line gives the fix (a model downloaded to the Mac, then a restart). Commands and reminders still work meanwhile |
| "Voice isn't available right now; please type it." | Voice service down, or still loading | Check it with `curl -s http://127.0.0.1:8150/health`. After an install, a move or a macOS update, allow up to about 15 minutes. |
| She doesn't pick up, and a note says "I can't take calls yet: my voice is still loading" | Her voice is still loading (up to about 15 minutes after an install, a move or a macOS update) | Wait for her text "My voice is ready: you can call me now.", then call again. Asked "How is the Mac doing?", her status shows the voice loading its models; `curl -s http://127.0.0.1:8150/health` shows `stt` and `tts` as `ready` when it is done |
| "Calendar: the secret address no longer works" | The calendar's address was reset in Google | Save the new address with `scripts/set-calendar-url.sh` (with the calendar's name, if it has one) |
| "Calendars.Read is not granted yet" | An Outlook calendar's permission was not granted | An administrator grants it in Microsoft Entra, or run `scripts/mail-login.sh <address>` again (`setup_assistant.md`, "Calendar (once)") |
| "Mailbox <name> needs a new sign-in" | Microsoft ended the sign-in (password or MFA reset, or long unused) | Run `scripts/mail-login.sh <address>` again |
| "Spotify needs a new sign-in" | Spotify sign-ins last about six months | Run `scripts/spotify-login.sh` |
| "… isn't answering" for a device | Switched off at the wall, or a new network address | Check the plug. If its address changed, run `scripts/tapo-discover.sh`. |
| Replies stop | Signal's safety number changed | Confirm the number in Signal first. See `setup_assistant.md`, Operations. |
| "A message came from a new linked device N and was held." | A device was linked to your Signal account | Yours: send `/devices ok N` from your phone. Not yours: remove it in Signal → Settings → Linked devices, then turn on Registration Lock for your number. |
| The laptop cannot reach a program on the Mac (a page you open from your laptop, a file share) | The firewall is on (`scripts/harden.sh --nuclear`, an earlier security step, or your own choice) and does not let that program through: a Mac without a screen has nobody to click "Allow" | Run `/usr/bin/sudo /usr/local/estia/bin/estia doctor`: a "! Firewall: … cannot be reached from other machines" line gives the two commands that let a program you started through, with its full path. After a Homebrew update the path changes: run doctor again |
| The move ends with a ✗ line | One step failed. Her old setup came back (on a first install, nothing was installed) | Keep the ✗ line: it names what failed. Fix that, run `scripts/migrate-to-own-user.sh --dry-run` again, then `scripts/install-daemon.sh` |
| No answer after `/lock` | The emergency stop is on | Send `/unlock` from the device that sent `/lock`, after the wait it names |
| "Late, I was offline …" with a list of reminders | The Mac or the bridge was down when they were due | Nothing: they came late, marked so. "(may be a repeat)" means one was being sent when it stopped. Keep your phone's alarm for what matters. |
| "Wake-up: 07:00 … No call: …" | The call could not be placed: voice still loading, calls off, a call already running, or three calls that day | The text says which. `/reminders` shows what was missed in the last week |
| A message you didn't ask for, ending "(… · /quiet to pause)" | One of the messages she starts herself (at most one a day, never between 22:00 and 08:00) | Reply if you like. `/quiet` pauses them, `/initiative off` turns them off; `[initiative]` in the config turns each kind off |
| No reply at all | Bridge or Mac is down | Log in to the Mac remotely (SSH) and run `/usr/bin/sudo /usr/local/estia/bin/estia check`, then `/usr/bin/sudo /usr/local/estia/bin/estia logs`. If that fails, the Mac itself is off or offline. |

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
| `/babble`, `/babble <name or code> [a2\|b1]`, `/babble off` | Practise a language: `/babble nl` starts Dutch at A2, `/babble nl b1` at B1; a plain `/babble` resumes the last one (the first time it lists them); `/babble off` ends it with a recap |
| `/remind [call] [daily\|weekdays] <when> <text>` | Set a reminder without the model. `<when>` is `17:00`, `2026-10-13 07:00`, `20m`, `2h` or `3d`; `call` adds a wake-up call. Example: `/remind call 07:00 wake up` |
| `/reminders`, `/reminders cancel N` or `all` | Your reminders with their numbers, and what was missed or failed in the last week; cancel one or all |
| `/initiative on`, `/initiative off`, `/initiative` | Let her write first now and then (off until you turn it on), stop it, or see which |
| `/quiet`, `/quiet off` | Pause the messages she starts herself, or let her write first again. Reminders you set always come |
| `/lock` | An emergency stop: no answers and no calls until `/unlock`. Holds after a restart; needs no Mac |
| `/unlock` | Ends `/lock`: only from the device that sent it, after a short wait |
| `/devices` | The linked devices of your account that she knows, by number |
| `/devices ok N` | Lets new linked device N talk to her: only from a device she already knows |
| `/devices forget N` | Takes device N off the list after you unlinked it: Signal gives its number to the next device you link. A linked device silent for 14 days is held again by itself |
| `/help` | The list above |

## Requirements

- **Mac:** Apple silicon. macOS 14 or later for the bridge, and macOS 15.6 or later for voice notes and calls. The code uses `sips`, `afconvert`, `launchctl`, `sysctl`, `/System/Volumes/Data` and the Neural Engine. 32 GB of memory is recommended: the default model needs about 18 to 20 GB while it is loaded. On 16 or 24 GB, use a smaller Gemma 4 tag (`setup_assistant.md`, 2.2). Its answers will be weaker.
- **Tools:** [Homebrew](https://brew.sh), which also installs the Command Line Tools (git, and Swift 6.2 for voice); Python 3.13 with [uv](https://docs.astral.sh/uv/). The voice tests name the Command Line Tools' Testing plugin folder in `tools/voice/Package.swift`; where that folder does not exist (Xcode only), this is harmless.
- **Model:** [Ollama](https://ollama.com) running, with a model that can use tools (tool calls; developed on Gemma 4 26B), downloaded to the Mac: a cloud model is refused. See `setup_assistant.md`, "Model (once)".
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

**With the setup script:** `scripts/setup.sh` does steps 1 to 6 below and asks only what it must. It picks the model for the Mac's memory (16 or 24 GB: `gemma4:12b`; 32 GB or more: `gemma4:26b`), asks for her name (Enter keeps Estia), registers her Signal number with you (the captcha link, the SMS code and any PIN go into hidden prompts, as every key does), finds your Signal account from a one-time word you send her, asks which language she speaks and whether she should see projects on this Mac (most people: no), writes the config, sets a Registration Lock for her number if you say yes, moves her into her own hidden user `_estia` (always on), and then goes through the Mac's security step with you (`scripts/harden.sh`; the nuclear option is never part of setup). Run it again: steps already done are skipped. You can rename her later with `name` in the config. Setup is done when `/usr/bin/sudo /usr/local/estia/bin/estia doctor` shows no ✗ and her welcome message has arrived on your phone: reply to it to see her answer. Or let an AI coding assistant do it with you: [SETUP-WITH-YOUR-AI.md](SETUP-WITH-YOUR-AI.md).

**By hand**, in short. The full guide, with every sign-in and key, is `setup_assistant.md`.

1. **Prepare the Mac.** Install Homebrew. Clone the repository to `~/estia`, not into Desktop, Documents or Downloads. Install the tools from Requirements. Start Ollama with `brew services start ollama`, then pull the model: `caffeinate -i ollama pull gemma4:26b` (about 18 GB; run it again if it stops). The commands are in `setup_assistant.md`, 2.1 and 2.2.
2. **Give her a Signal number.** The bot needs its own number, registered with signal-cli (`setup_assistant.md`, "Signal number (once)"). On its first start, the bridge sets the bot's Signal profile name to her name and "(AI)", so "Estia (AI)" by default. It does this again after a rename. Her profile picture is Estia's e., or your own PNG or JPEG set as `avatar` in the config (`avatar = ""` for none). The move into her own user copies that picture into her config folder when it is a file in your home of at most 5 MB.
3. **Write the config.** Copy `config.example.toml` to `~/.config/local-assistant/config.toml`, make it readable only by you (`chmod 600`), and fill in the bot's number and your Signal account ID. Set her name with `name = "Estia"`, the default (ours says `name = "Tiffany"`; 1 to 20 letters, spaces or hyphens). Find the account ID by sending her a message and listing the senders (`setup_assistant.md`, 2.4). The bridge refuses a config file that other users can read. Never commit it.
4. **Personality (optional).** Without `soul.md`, her built-in style applies: warm, curious and playful. To write your own, copy `soul.example.md` to `~/.config/local-assistant/soul.md`, make it readable only by you (`chmod 600`), and edit it.
5. **Check, then run in the foreground.** `scripts/doctor.sh` checks the Mac, the tools, the config, the model and the Signal number, and prints the fix for each ✗. Then run `scripts/run.sh`. On her first start she sends a welcome message: what is set up, something to try, and what stays on the Mac. Reply to it from your phone. `scripts/check.sh` checks her health.
6. **Make it always on, in her own user.** Stop the foreground bridge, then run `scripts/migrate-to-own-user.sh --dry-run` to see every step, then `scripts/install-daemon.sh`. It asks for your Mac password once. With admin rights it creates `_estia` (a hidden account with no password and no login), copies her config, data, Signal account and logs to `/var/estia` (readable only by `_estia`), puts a copy of the code owned by root in `/usr/local/estia`, installs the service files in `/Library/LaunchDaemons` and starts her, so she starts with the Mac, without a login. She never runs as you or as root. Her release (her copy of the code) is built first, while she keeps running, so she is offline for a minute or two; over SSH, run it inside `tmux`, which keeps it going if the connection drops. Your old folders are held for 7 days where only root reaches them: `/usr/bin/sudo /usr/local/estia/bin/estia rollback` goes back, with what she saved since. With voice set up, the move ends with "voice: ready". While her voice still loads, it ends with "Calls work once her voice is ready (up to about 15 minutes); she texts you then." when calls are set up (after up to 150 s of checking, so the text surely follows), and with "voice: loading (up to about 15 minutes)" when they are not. A move that succeeds also deletes the releases she does not run (from earlier tries), and lists the earlier copies of hers kept aside. `/usr/bin/sudo /usr/local/estia/bin/estia remove-old` deletes them together with her old folders held for the rollback: no rollback after it, so run it once she works. After a `git pull`, `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` shows the new changes (commits), asks, and puts the new code live (it checks her within two minutes and switches back on a failure). Then run the security step, `scripts/harden.sh` (each change on your yes), and read "Keep it running" in `setup_assistant.md`, 2.8.
7. **Add the other parts one at a time.** Each part stays off until its section is in the config. `/usr/bin/sudo /usr/local/estia/bin/estia doctor` must show it without ✗ before you add the next (○ until its first use, then ✓). The sections and their helper scripts:

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
| Projects | `[projects]` | Nothing to install: setup asks the folder and the level |

Keys and sign-ins go through these scripts, never through chat and never into the config file. Once she runs as `_estia`, they pass each value through a pipe to `/usr/bin/sudo /usr/local/estia/bin/estia secret set`, and sign-ins run as her (`/usr/bin/sudo /usr/local/estia/bin/estia mail-login`). `scripts/setup.sh --extras` lists the optional parts again and shows how to add the ones you pick.

## Tests and checks

- `scripts/doctor.sh` checks the setup on the Mac, read-only: before the first start and after each optional part. For a part that is set up, it shows what the bridge's last real use of it did: ✓ working (with the time), ○ set up but not tested yet, or ✗ failing (with the fix). A file in place is not enough for ✓.
- Before every commit, `scripts/verify.sh` runs a privacy check (`scripts/privacy_scan.py`: no private files, keys, addresses, phone numbers, account IDs or home paths in the repo), then ruff (lint and format), mypy in strict mode and pytest. The tests run offline, with fakes for Signal, the voice service, the device helper and the model.
- The helpers have their own tests: `tools/voice` has Swift tests (`swift test`), and `tools/tapo` and `tools/ical` have pytest suites (`uv run pytest -q`).
- `uv run python -m evals.gemma_suite` runs test cases against the real model on your Mac. It uses the model in your config (`local-worker` when `model` is unset); `EVAL_MODEL=<tag>` runs another tag. A run passes when every safety case passes and at least 90% of the rest do. The model does not give the same answer every time, so run the suite twice before trusting a prompt change. `EVAL_LANGUAGE=nl` runs the safety cases and three language cases with the rule line for that language. `EVAL_NAME=<name>` gives her another name (default Estia). Without `EVAL_SOUL`, she has her built-in style, and two cases check it: a light joke when invited, none on a worry. `uv run python -m evals.gemma_suite practice` runs the language-practice cases, two per offered language (`practice_nl` runs Dutch's two); they are never part of a run without names.
- For a phone walk-through, `docs/e2e-checklist.md` lists what to check from a real phone: texts, voice, calls, lights, mail, calendar, music, web search, languages, language practice, reminders and wake-up calls, and the messages she starts.
- To check health without Signal, run `/usr/bin/sudo /usr/local/estia/bin/estia check` on the Mac (for example over SSH; `scripts/check.sh` before her own user). It covers the case where the bridge itself is down, since the bridge can't report its own outage. Other services can be watched with `[health] extra_checks`; none are watched by default.

## Built with

| Piece | What it does here |
|---|---|
| Gemma (Google's open model) | Answers, reads tool results, decides which tool to use |
| Ollama | Runs the model on the Mac, and is the first choice for web search |
| signal-cli | Signal, as a command-line client |
| WhisperKit | Speech-to-text on the Neural Engine (Whisper large-v3 turbo, fixed to one version, told the language) |
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
- Voice notes must be in the current language (`/language`; during practice, the practised one), up to three minutes long and 10 MB. Long spoken answers stop at the end of a sentence, and the rest is in the text.
- She runs on a local model and can get things wrong. Check anything important, especially web answers. The Sources message is there for that.
- Her number runs on signal-cli, an unofficial client. Signal's terms discourage automated accounts, and Signal can disable one: keep her to your own chats, at a low volume, on a number of her own. See [SECURITY.md](SECURITY.md#known-accepted-gaps).
- Without Projects she reads no folder of yours. With Projects on, in her own user she reads one folder of yours, the one named in setup (`[projects] root`), read-only, and nothing else in your home. The move shares it; `scripts/share-projects.sh` shares it again, `--revoke` takes it back. Every file in that folder is readable by her user, also files git does not track, such as `.env.local`; her project tools open only tracked files at the level you chose, and she has no shell.

## For developers

- Python 3.13, standard library only at runtime. Use uv, ruff (line length 120) and mypy --strict. Type hints everywhere.
- Test first: write a failing test before every change or bug fix. Prompt changes also go through the Gemma test set.
- New features: agree the design in an issue first. Design specs and plans are not kept in this repository.
- Conventional commits, with no attribution trailers.
- Secrets never go into the repo. They live in the config, the keychain or mode 0600 env files. Logs hold only what happened and when, never message text.
- Privacy check on every commit (optional): `git config core.hooksPath .githooks`. This replaces `.git/hooks`; if you already have a pre-commit hook, call `.githooks/pre-commit` from it instead. A reviewed exception gets `privacy-ok: <reason>` on its line.
- Where to start reading: `app.py` (wiring and queue), `brain.py` (the tool loop), `tools.py` (what the model may call), `prompts.py` (every text she writes) and `commands.py` (slash commands). `AGENTS.md` has the full layout and rules.
- Contributing: see [CONTRIBUTING.md](CONTRIBUTING.md). Report security problems privately, as described in [SECURITY.md](SECURITY.md).

## License

This project is MIT licensed (see [LICENSE](LICENSE)). The call helper in `tools/call-tunnel/` is AGPL-3.0-only, because the patch it contains modifies AGPL-3.0 code; see [its notice](tools/call-tunnel/LICENSE-NOTICE.md). Other third-party components and their licences are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
