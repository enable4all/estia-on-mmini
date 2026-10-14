# End-to-end checklist (from your phone)

Before you start, `scripts/doctor.sh` must show each part you test without ✗ (○ means set up, not used yet). Then run with the bridge in the foreground (`scripts/run.sh` in a terminal; if the daemon is installed, stop it first with `/usr/bin/sudo launchctl bootout system/com.local-assistant.bridge` and load it again afterwards with `/usr/bin/sudo launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.bridge.plist`) and `tail -f ~/Library/Logs/local-assistant/bridge.log`
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
| 16 | With `[projects]`: `What's next on my-project?` | The open items of my-project's `todo.md` (and recent changes if asked) | |
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
| 31 | `/usr/bin/sudo launchctl bootout system/com.local-assistant.voice`, a voice note; bootstrap it again | "Voice isn't available right now; please type it."; afterwards voice works again | |
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
| 22 | Optional alert test with an entry in `[health] extra_checks`: stop that service on purpose (for a launchd service: `/usr/bin/sudo launchctl bootout system/<label>`), wait, then start it again (`/usr/bin/sudo launchctl bootstrap system <plist>`) | Within 4 h + 5 min (or sooner after a bridge restart: first check 2 min after start, alert 5 min later) "⚠ Problem on the Mac: - my-service: not answering (since HH:MM)"; after the restart, within 5 min "✓ Back to normal on the Mac: - my-service (was down HH:MM–HH:MM)" | |

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

## Language practice (`/babble`; voice notes and calls need a voice service with "languages")

| # | Do | Expected | Result |
|---|---|---|---|
| 94 | `/babble` (the first time), then `/babble nl` | The eight languages, Russian, Polish and Korean marked "not yet"; then "Practice: Nederlands, Dutch (nl), level A2. …" and the limits line (local model, no pronunciation scoring, `/voice echo on`) | |
| 95 | Type "Zaterdag ik ben naar het strand gegaan.", two more Dutch turns, then "doe het licht in de woonkamer uit" and `/language de` | Answers only in Dutch, short, each ending in one question; at most one first line "→ …"; nothing switches and no claim that it did; "Practice is running (Dutch, A2). End practice first: /babble off" | |
| 96 | A Dutch voice note with a mistake, then `/babble off` | Heard in Dutch, answered in text and voice without a "→" line; then "📚 Practice recap (Dutch, A2)" with at most three points and "Based on what was heard: …", then "Practice ended. …" | |
| 97 | `/babble`, call her, say two sentences in Dutch, hang up; then "what can you do?" | "Practice again: …" with the list ("← last"); the call greets and answers in Dutch; after hanging up the recap comes as a text, no call summary; her answer names language practice | |

## Reminders and wake-up calls

Test 83 to 86 with `[calls]` on. Note the order of the call events in `bridge.log` for each outcome (picked up, declined,
not answered, busy): the bridge sorts them by signal-cli's reason words, which upstream does not document yet.

| # | Do | Expected | Result |
|---|---|---|---|
| 77 | "Remind me in 2 minutes to stretch" | A note "Reminder set (#N): <today> HH:MM · "stretch". …"; two minutes later "Reminder: stretch" | |
| 78 | `/remind 2m tea`, then `/reminders`, then `/reminders cancel N` | The same note without the model; the list shows it; "Cancelled #N: …"; nothing arrives | |
| 79 | "I have the dentist tomorrow at 10" | No reminder is set (`/reminders` is empty); she may offer one | |
| 80 | "What does my newest mail say? Remind me of it at 17:00" (with `[mail]`) | No reminder; she says you can ask in your own words | |
| 81 | Set a reminder for 3 minutes ahead, stop the bridge, start it again 5 minutes later | One "Late, I was offline HH:MM-HH:MM:" note listing it; nothing else is sent twice | |
| 82 | "Every weekday at 08:00, the pills", next morning | "Reminder: the pills (weekdays at 08:00 · /reminders to stop)", and again the next weekday | |
| 83 | `/remind call 2m wake up`, phone locked, answer it | First the text "Wake-up call: … · wake up. Good morning!" (or "Reminder (calling you now): …" after 11:00), then the phone rings; picked up: "Good morning! It's HH:MM. wake up", then a normal call | |
| 84 | Repeat 83 and let it ring out | After about a minute it stops; two minutes later it rings once more; then "Wake-up call not answered (HH:MM, HH:MM)." | |
| 85 | Repeat 83 and decline | No second call | |
| 86 | Repeat 83 with Signal Desktop linked, and once with the phone in Sleep Focus (with and without Estia allowed) | Note which devices ring, whether a decline on one ends it, and whether Focus silences it | |

## Messages she starts herself

She writes first at most once a day and never at night, so these take days, not minutes. To see a text without
waiting, `uv run python -m local_assistant.initiative --draft improvise` (or `lookback`, `followup`) prints one in the
terminal and sends nothing; run it when she is not busy. The first item needs an install from before this update, with
no `[initiative]` section (they are opt-in).

| # | Do | Expected | Result |
|---|---|---|---|
| 87 | Start the updated bridge on an install that already chats, during the day, and wait a few minutes without writing; then `/initiative`, `/initiative on`, a restart | Nothing she starts arrives; `/status` shows "Initiative: off · /initiative on turns it on". "Messages I start myself: off. /initiative on turns them on (…)."; then "On. Now and then I'll message you first: … /quiet pauses that. /initiative off turns that off."; no other announcement, not after the restart either | |
| 88 | Draft each kind: `--draft improvise`, `--draft lookback` (after a week of chatting), `--draft followup` (after 89) | Short, warm, in your language, about you and the moment or your own week; no claims, links, numbers, mail or calendar; "(nothing would be sent: …)" when a check failed, with what she wrote | |
| 89 | "I'll call the dentist tomorrow", then leave her alone for half an hour; `/reminders` | Under "Check-ins I may send:" one `#cN` with the day after tomorrow, evening; the day after tomorrow from 17:00 a check-in like "Did you get hold of the dentist?" ending "(a check-in on something you said · /quiet to pause)" | |
| 90 | With `[calendar]`: move an event later today by an hour in Google Calendar, and one in Outlook; wait up to 30 minutes | "Calendar change: <title> moved HH:MM → HH:MM (today)." ending "(calendar change · /quiet to pause)", at most one a day | |
| 91 | With `[calendar]`: an event tomorrow at 07:30 | At 20:00: "Early start tomorrow: 07:30 <title>." (unless she already wrote first that day) | |
| 92 | `/quiet`, then `/status`; later `/quiet off` | "Quiet: I won't message you first until you send /quiet off. Reminders you set still come."; `/status` shows "Initiative: paused (/quiet) · …"; nothing she starts arrives meanwhile; a reminder does. "I may message you first again: …" | |
| 93 | Reply to one of her messages, then `/status` the next day | "… · N answered within a day · /quiet N times · improvised note: this week" (or next week); her message and your reply read naturally together in the next answers | |

## Hardening (devices, lock, safety number, model pin)

Doctor's lines for these: `/usr/bin/sudo /usr/local/estia/bin/estia doctor` once she runs as `_estia` (else `scripts/doctor.sh`).
Their facts are in `status.json` (times and a count only).

| # | Do | Expected | Result |
|---|---|---|---|
| 98 | After the update, a message from your phone | Answered as before; `/devices` lists "1 (your phone)" and the line about calls | |
| 99 | A message from Signal Desktop (or another linked device) | No answer; your phone and the desktop get "A message came from a new linked device (N) and was held: …"; a second message within the hour gets no second note; `bridge.log` has "held" with the device number, never the text | |
| 100 | `/devices ok N` from Signal Desktop, then from your phone | From the desktop: "Not allowed: /devices ok works only from a device I already answer, …"; from the phone: "Allowed: device N. …"; the desktop's next message is answered | |
| 101 | `/lock` from your phone, then a message and a call from the desktop, then `/unlock` from the desktop | "Locked: …" at once; the message gets nothing (not even a read receipt), the call rings out unanswered, `/unlock` from the desktop gets nothing | |
| 102 | `/unlock` from the phone at once, then again 10 minutes after `/lock`; a reminder due while locked | "Still locked: /unlock works from this device from HH:MM."; then "Unlocked: …"; the reminder arrives after the unlock (a call reminder as its text, "No call: I was locked (/lock).") | |
| 103 | `/lock`, restart the bridge, a message from the phone, then `/unlock` after the wait | Still locked after the restart (nothing sent), unlocked after `/unlock` | |
| 104 | On the phone: Settings, Privacy, the chat with her, View safety number; compare with `/usr/bin/sudo /usr/local/estia/bin/estia signal listIdentities` on the Mac (setup_assistant.md, 7. Operations, "Safety number changed") | They match; mark verified on the phone. Doctor shows no safety number line | |
| 105 | After re-registering your own Signal (or a new phone): a message to her | Dropped by signal-cli; doctor and `/status` (from another allowed account, if any) show "Safety number: ✗ changed …" with the compare-then-trust steps; after trusting, the next message is answered and the line goes | |
| 106 | Doctor's "Her Signal account" line | "1 device: this Mac"; with a device linked to her number (only as a test, then remove it) "✗ 2 devices" and one note in Signal | |
| 107 | Set `model_digest` to the digest doctor shows, restart, send a message; then change one digit of `model_digest`, restart, send a message; put the right digest back, restart | Answers as before; with the wrong digest one note "Answers are paused: …" after the start and every message gets it, no Gemma answer, doctor ✗ "Model pin"; with the right one answers again (the alias itself is never touched) | |
| 108 | Set `[privacy] keep_days = 1` on a test install, leave it overnight | The next morning `/status` shows fewer history messages; `bridge.log` has one "retention: … deleted" line after 03:00 | |
| 109 | Watch `launchd.log` and `voice.log` grow past 10 MB (or start with a large one) | Emptied at the next start or the next line; `bridge.log` rotates to `bridge.log.1` … `.5` at 10 MB | |
| 110 | An allowed linked device, unused for 15 days (or set its `last_seen` in `known_devices` back on a test install), sends a message; then `/devices forget N` from the phone after unlinking a device | Held with "…silent for more than 14 days…"; after `/devices forget N` the number is off `/devices`, and a new device that gets it is held | |
| 111 | `ls -l /var/estia/Library/Logs/local-assistant/launchd.log` after the first start; then, on a test install only, replace it with a link to a file in `/tmp` as `_estia` and restart the bridge | Owned by `_estia`, 0600; after the restart the file in `/tmp` is unchanged (launchd did not follow the link as root). If it changed, point StandardOutPath at /dev/null and report it | |
| 112 | `/usr/bin/sudo cat /etc/ssh/sshd_config.d/000-estia-keys-only.conf` after the security step; `ssh _estia@<the Mac>` from the laptop | Holds `DenyUsers _estia`; the login is refused | |
| 113 | `dscl . -read /Users/_estia AuthenticationAuthority Password UserShell IsHidden` <!-- privacy-ok: her directory record, not a home folder --> | No AuthenticationAuthority; `Password: *`, `/usr/bin/false`, `IsHidden: 1`; doctor "Own user ✓ … no password, no login" | |
| 114 | A commit that changes `scripts/estia`, then `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` | The commits and files are listed and a yes is asked; after it "The root-run tools differ in this release (scripts/estia): the installed ones stay"; `--update-tools` shows the diff and asks; `/usr/local/estia/bin/estia` changes only on yes | |
| 115 | `/usr/bin/sudo /usr/local/estia/bin/estia signal listDevices`, then `/usr/bin/sudo /usr/local/estia/bin/estia unlock` after a `/lock` | The bridge stops, her devices are listed (only this Mac), she starts again; after `unlock` she answers at once | |
| 116 | On a test install within the 7 days: send her a few messages after the move, then `/usr/bin/sudo /usr/local/estia/bin/estia rollback` | She runs as you again; your folders are back with those messages and her Signal sessions (your next message is answered, no session reset); `scripts/doctor.sh` works for you and `scripts/set-web-keys.sh` writes your `~/.config` again | |
| 117 | `scripts/harden.sh --nuclear` on the headless Mac with a dev server of yours listening on the network | It first says the settings may stop other services on this Mac; the firewall question names the dev server ("node on port …"); on a yes to keep it, it still answers from the laptop. Without `--nuclear` the step shows one neutral line (○) naming the nuclear option, and asks nothing about the firewall | |
| 118 | `/babble nl` and a Dutch message, then `/lock`, another Dutch message; 30 minutes later `/unlock` | The first is answered in Dutch; after "Locked: …" nothing comes (no answer, no recap); after "Unlocked: …", within a minute, "📚 Practice recap (Dutch, A2)" with the line that practice ended after 30 minutes | |

## Call awareness and projects as an extra

| # | Send from the phone | Expected | Result |
|---|---|---|---|
| 119 | With `[calendar]` and `[mail]` and a few events left today: call her and say "hi, how busy is my day?"; then "yes" to her offer | She says how many things are left or when the next one starts, and offers the rundown once; she names no event, person or mail before the yes; after it, today's events with their titles (`bridge.log`: `call: a yes to the rundown; code reads calendar_agenda, mail_digest`, and `tool calendar_agenda` only after the yes) | |
| 120 | With `[projects]` on: call her and ask "what's next on <a project>?", saying the name the way a car call mishears it | She answers from the nearest project and names it, or asks "Do you mean …?" and waits; never "no such project" alone | |
| 121 | On a test config without `[projects]`: ask "what projects can you see?"; then `scripts/setup.sh --extras`, pick projects, a folder and `code`, restart her and ask about a source file | First: projects are off, and `scripts/setup.sh --extras` turns them on; `/usr/bin/sudo /usr/local/estia/bin/estia doctor` (or `scripts/doctor.sh`) has no Projects line. Then `[projects]` in the config, a `config.toml.bak-…` copy next to it, and an answer from the file; `Show me the .env of <project>` is still refused | |

## After the move into her own user

| # | Do | Expected | Result |
|---|---|---|---|
| 122 | With the firewall on and a dev server of yours listening on port 8170 (not on the firewall's list, ad-hoc signed): `/usr/bin/sudo /usr/local/estia/bin/estia doctor` | "! Firewall: node on port 8170 cannot be reached from other machines", then "if you started it and other machines should reach it:" and the two `socketfilterfw` commands with its full path; doctor runs as her, so this checks that `netstat` names your program | |
| 123 | With `[calls]`: `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --voice` with a changed voice service (or a move), then wait | The end says "voice: loading (checking again for up to 150 s)", then "voice: ready" or "Calls work once her voice is ready (up to about 15 minutes); she texts you then."; after the second, each allowed account gets "My voice is ready: you can call me now." once her voice is ready (in quiet hours at 08:00) | |
| 124 | While her voice still loads, call her; hang up when it rings out | The note "I can't take calls yet: my voice is still loading …"; once ready, "My voice is ready: you can call me now." at once (also in quiet hours), a single one | |
| 125 | After a move that kept an earlier copy aside: `/usr/bin/sudo /usr/local/estia/bin/estia remove-old` | It names each `/var/estia/.before-<time>` folder it deletes, and the held folders when on record (no rollback after) | |
| 126 | With "block all incoming connections" on (a test Mac only): `/usr/bin/sudo /usr/local/estia/bin/estia doctor` | One line "! Firewall: block all incoming connections is on: … cannot be reached from other machines" with `--setblockall off`, no per-program commands | |
| 127 | The first voice deploy with Whisper's pinned files: `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --voice`, then `/usr/bin/sudo /usr/local/estia/bin/estia logs voice -n 40` | "listening: pinned model files checked in …" (or, for a copy an older download filled with other files, "… set aside as <folder>.refused-<time>, never loaded; downloading the pinned ones again", then "… downloaded and checked"), then "listening ready in …"; `curl -s http://127.0.0.1:8150/health` shows `stt` ready. Never "listening unavailable" | |
