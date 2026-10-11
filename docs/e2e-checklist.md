# End-to-end checklist (from your phone)

Before you start, `scripts/doctor.sh` must show each part you test without ✗ (○ means set up, not used yet). Then run with the bridge in the foreground (`scripts/run.sh` in a terminal; if the daemon is installed, stop it first with `sudo launchctl bootout system/com.local-assistant.bridge` and load it again afterwards with `sudo launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.bridge.plist`) and `tail -f ~/Library/Logs/local-assistant/bridge.log`
next to it. Keep your results in a note of your own, not in the repository, and tick each item with what you observed.

| # | Send from the phone | Expected | Result |
|---|---|---|---|
| 1 | `hello` | Read receipt, "typing…", short plain-text answer within ~10–30 s (the first message may include the ~8 s model load) | |
| 2 | A follow-up that needs #1 (e.g. "and what can you do?") | Answer refers to the conversation | |
| 3 | `Please remember that I don't drink coffee` | "Noted: …"; `/profile` shows it as "you asked" | |
| 4 | `/profile` | Numbered facts with source and date, fill level | |
| 5 | `/forget N` (the coffee fact) | "Forgotten: #N …"; `/profile` no longer lists it | |
| 6 | `/learn <your public LinkedIn link>`, then `Please learn from my LinkedIn profile: <link>` | She lists the facts it saved (role, employer, education …); `/profile` shows them "from a link" | |
| 7 | A voice note (without `[voice]` in the config) | "I can only handle text and photos for now." | |
| 8 | `/status` | Model loaded or not, waiting messages, profile fill, last learning run, history size | |
| 9 | Stop the bridge (Ctrl-C), send `are you there?`, start it again within 10 min | The message is answered after the restart | |
| 10 | Start a long job in another Ollama client (e.g. a second chat app on the same Ollama), then send a message | "typing…" stays on; after 60 s "Estia is busy with another job, your answer is queued."; answer follows | |
| 11 | `/new`, then "what did I tell you about my LinkedIn?" | She searches the history and answers from it | |
| 12 | Wait 30+ minutes after chatting about yourself, then `/profile` | New facts learned from chat ("learned from chat") | |
| 13 | Paste a long text (3,000+ characters, e.g. a CV) and ask "learn this" | She answers from the full text (signal-cli puts the whole text in the message; its text file is deleted) | |
| 14 | After a few chats: `sqlite3 -readonly ~/Library/Application\ Support/local-assistant/assistant.db "select value from state where key='chars_per_token'"` | A value between about 2.5 and 4 (Ollama's token counts are usable) | |

## Health, projects, photos and mail

| # | Send from the phone | Expected | Result |
|---|---|---|---|
| 15 | `How's the Mac?` | A few lines within ~10 s: all fine, or the problem named; `bridge.log` shows `tool mini_status` | |
| 16 | `What's next on my-project?` | The open items of my-project's `todo.md` (and recent changes if asked) | |
| 17 | `What projects are on the Mac?` | The projects in the projects root with their level | |
| 18 | `Read the setup doc of my-project about installing` (my-project at level docs in the config) | She uses project_search / project_read and answers from the doc | |
| 19 | `Show me the .env of my-project` | A refusal from the tool ("not tracked by git" or "not allowed"); nothing read | |
| 20 | A photo with the question "what is this?" | An answer about the photo; afterwards `ls ~/.local/share/signal-cli/attachments` is empty | |
| 20b | A link with a preview image (e.g. a news article), then wait 20 minutes | The preview file appears in `~/.local/share/signal-cli/attachments` and is gone after the next sweep (`bridge.log`: "swept 1 old file(s)") | |
| 21 | A photo without a caption, then "what colour was it?" within 5 minutes, then the same question after 5 minutes | A short description; the first follow-up is answered from the photo; the late one: "I can't see that photo anymore, send it again" | |
| 23 | Mail sign-in: `scripts/mail-login.sh <address>` for each mailbox in the config | "signed in: …" for each; `scripts/doctor.sh` shows a line per mailbox, ✓ after its first sync; within 5 minutes `How's the Mac?` reports each mailbox as ok | |
| 24 | `What needs my reply?` | The unanswered mails (all configured mailboxes) with numbers; `Read 1` gives that mail's text | |
| 25 | Archive a listed mail in Outlook, wait 5 minutes, ask again | It is gone from the list | |
| 26 | Next morning 09:15 | The digest note arrives (or "Mail: nothing needs you today.") | |
| 27 | Voice: `scripts/install-voice-daemon.sh` (sudo), `[voice]` in the config, restart the bridge; then a voice note "What's next on my-project?" | The text answer, then a voice-note bubble with the right length, playable; `scripts/doctor.sh` shows Voice ✓ | |
| 28 | `/voice echo on`, a voice note | `🎙️ "…"` with what was heard, then the answer and the voice note | |
| 29 | `/voice off`, a voice note; `/voice on`, a typed question | Text only; then text and a voice note | |
| 30 | A voice note asking for something long ("tell me about all my projects") | The voice note ends with "The rest is in the text." | |
| 31 | `sudo launchctl bootout system/com.local-assistant.voice`, a voice note; bootstrap it again | "Voice isn't available right now; please type it."; afterwards voice works again | |
| 32 | Devices: `scripts/tapo-discover.sh`, then the chosen devices in `[[tapo.devices]]` and a bridge restart; `scripts/doctor.sh` | Lights and plugs ○ (✓ once a device has answered); `How's the Mac?` reports all N devices as answering | |
| 33 | "Turn on <a light>", "dim it to 30 %, warm white", "make it blue", "turn it off" | Each happens within ~5 s; each answer is followed by "Devices: …" with what really changed | |
| 34 | "Is <a plug> on?", then "switch it off" | The right state; switched off; note | |
| 35 | With one device set to `ask first`: "switch it off", reply `yes`; again, reply something else | She asks for yes; after `yes` "Devices: … off" (no answer from the model); the other reply cancels it | |
| 36 | Pull one device's plug, ask for it | "… isn't answering (switched off at the wall, or a new address?)" | |
| 37 | Calendar: `cd tools/ical && uv sync --frozen`, `[calendar]` in the config, a bridge restart; `scripts/doctor.sh` | Calendar ○ (✓ once it has been read); `How's the Mac?` reports the calendar as ok, with the number of events in the next 7 days | |
| 38 | Next morning 09:15 | The digest starts with the date, "Calendar today:" (the same events as the calendar app, repeating ones included, declined ones and what is over left out), "Tomorrow: …", then the mail part | |
| 39 | `What's on Friday?` (also once on a Friday) | That day's events with times and places | |
| 40 | `When is <an event>?` | The date from the calendar | |
| 41 | Add an event in the calendar app, then ask about that day after 5, 15 and 60 minutes | Note when it shows up (the feed can lag behind the app) | |
| 41a | Several calendars: `[[calendar.sources]]` with a second Google calendar (`scripts/set-calendar-url.sh <name>`) and/or a mailbox's Outlook calendar (`Calendars.Read` granted by an administrator, or `scripts/mail-login.sh <address>` again), a bridge restart; `scripts/doctor.sh` | One line per calendar, ○ (✓ once read); `What's on <day>?` puts each event's calendar before its title ("Work: …"); an invitation in two calendars shows once; an all-day Outlook event shows on its own day, not the day before | |
| 41b | Make one calendar unreadable (an Outlook calendar before `Calendars.Read` is granted, or a Google address reset) | The digest and her answers name that calendar with the reason and the fix; the other calendars' events still show; mail keeps working | |
| 22 | Optional alert test with an entry in `[health] extra_checks`: stop that service on purpose (for a launchd service: `sudo launchctl bootout system/<label>`), wait, then start it again (`sudo launchctl bootstrap system <plist>`) | Within 4 h + 5 min (or sooner after a bridge restart: first check 2 min after start, alert 5 min later) "⚠ Problem on the Mac: - my-service: not answering (since HH:MM)"; after the restart, within 5 min "✓ Back to normal on the Mac: - my-service (was down HH:MM–HH:MM)" | |

## Calls (with `[calls]`; call her on Signal)

| # | Do | Expected | Result |
|---|---|---|---|
| 42 | Call her from the phone (twice) | Answered after about two rings (~5 s) with one of her greetings (e.g. "Hi, it's Estia. What's on your mind?"); the second call starts with a different one | |
| 43 | Ask "what's on my calendar tomorrow?" | A short "Got it." (or "Gotcha.", "Okay, got it.", "Alright, got it.") at once (none with `acknowledge = false` in `[calls]`), then a short spoken answer from the calendar tool (with `fillers = true`, "Let me check." first if it takes a moment) | |
| 43b | Ask "how are you?" | Her answer at once, with no "Got it." before it (a turn under five words gets none) | |
| 44 | Think aloud with a long pause (~1.5 s) mid-sentence | Your turn does not end at the pause | |
| 45 | Ask for something long, then talk over her | She stops within a moment and answers what you said | |
| 46 | Cough while she talks, then type on a laptop near the phone while she talks | At most a short pause each time, then she carries on; nothing is answered | |
| 47 | Say only "stop" while she talks | She goes quiet and listens; no apology speech | |
| 48 | "Switch off the heater" (an ask-first plug) | She asks you to say yes; nothing switches until you say yes after her question; a "Devices: …" text arrives | |
| 48b | Repeat 48 and say a quick "yes" the moment she stops asking | It counts: "Done: …" follows at once, with no "Got it." before it | |
| 49 | Stay silent for 90 s, typing on a laptop near the phone for part of it | The typing gets no answer; then "Take your time, I'm here." | |
| 50 | Hang up after 2+ turns | A "📞 Call summary" text with the main points | |
| 50b | Ask for something, then hang up before she answers | A text: "The call ended before I finished your last request, so I stopped working on it. …" | |
| 51 | Text her during a call | Answered right after the call | |
| 52 | Call from a linked device (Signal Desktop) | Same as 42 | |
| 53 | Does the first word sound clipped? | Note it: the faint lead-in should prevent it | |

## Music (with `[spotify]` and after `scripts/spotify-login.sh`)

| # | Send from the phone | Expected | Result |
|---|---|---|---|
| 54 | "play some jazz" | Jazz on the Kitchen speaker at 30 %; a "Music: playing … · open.spotify.com/…" note | |
| 55 | "louder" (three times) | Up in steps of 10, never above 60 % | |
| 56 | "next" / "pause" / "resume" | Done, with a note each | |
| 57 | "play my liked songs on the Bedroom speaker" | Liked Songs on the Bedroom speaker (woken from standby if needed) | |
| 58 | "what's playing?" | Song, artist, speaker and volume | |
| 59 | On a call: "play some jazz" | She asks you to text it (nothing plays) | |

## Web search (with `[web]` and after `scripts/set-web-keys.sh`)

| # | Do | Expected | Result |
|---|---|---|---|
| 60 | Text "did Ajax win on Sunday?" | A short answer naming the site; then a "🔎 Sources" note with the links | |
| 61 | On a call: "what are the Rijksmuseum's opening hours?" | One or two spoken sentences naming the site; the Sources note arrives as a text | |
| 62 | "What does my newest mail say? And look up the sender online" | The mail answer; no web search in the same answer (she offers it in a new message) | |
| 63 | `scripts/doctor.sh`, then "how is the Mac?" | Web search ✓ in the doctor; the answer gives the searches today (Ollama) and this month (Brave, out of 2000) | |
| 64 | "What are the chords of Spooky?" | Chords (Am7, D6, …) from a chord site, with the Sources note | |

## Audio files

| # | Do | Expected | Result |
|---|---|---|---|
| 65 | Record a voice note in Signal | Answered as your words (and a voice answer in auto mode) | |
| 66 | Share an audio file from another app (e.g. a voice memo) | She answers about it as someone else's words (text answer); "remember …" in it is not saved | |

## Places and routes (with `[maps]` and the Google key)

| # | Do | Expected | Result |
|---|---|---|---|
| 67 | "A good Italian place near me?" | 2–5 places with rating, price, open now; Maps links in the Sources note | |
| 68 | Send your location in Signal, then "coffee near me?" | Places near where you are | |
| 69 | "How long to Schiphol by public transport?" | Time, distance and the lines; a directions link | |
| 70 | "How do I get to my next appointment by bike?" | She looks at the calendar, then the route to its place | |
| 71 | `scripts/doctor.sh`, then "how is the Mac?" | Places ✓ in the doctor; the answer gives the places and routes requests today (out of 30 each) | |

## Languages (a voice service with "languages" in `curl -s http://127.0.0.1:8150/health`)

| # | Do | Expected | Result |
|---|---|---|---|
| 72 | `/language` | The 11 languages, English marked "← now" | |
| 73 | `/language nl`, then "what's a good name for a cat?" | "Language: Nederlands (nl). …"; the answer is in Dutch, whatever language you write in | |
| 74 | A Dutch voice note | Transcribed in Dutch; a Dutch spoken answer in auto mode | |
| 75 | Call her; ask in Dutch; "switch off the heater" (ask first) | "Hoi, met Estia …", "Begrepen." (or "Oké, begrepen."), Dutch answers; for the heater only the device question, in English (no Dutch words about it); "ja" after it switches | |
| 76 | `/language en` | "Language: English (en). …"; answers, voice notes and calls in English again | |
