# The Estia guide

**An insanely secure & private thinking partner, living at home.** Estia (Εστία, the hearth) runs on one Mac at home and is reached only through Signal: encrypted texts, voice notes and calls. Once set up, she reads your mail and calendars, switches Kasa lights and plugs, and plays music on Spotify. Name her what you like. Ours is called Tiffany. Estia started as Tiffany, our own thinking partner, before she was opened up for every home.

- **Local.** Gemma, Google's open AI model, runs on the Mac through Ollama's official build; a cloud model, which Ollama runs on its own servers, is refused. No cloud AI service, no account with us, no telemetry. Chats, memory and profile stay on the Mac.
- **Read-only mail and calendars.** She reads mail and calendars. She never sends, moves, deletes or marks mail, and never changes a calendar event.
- **Lights and plugs follow a rule set per device.** Each device is `normal`, `ask first` or `never off` (switching it off is refused). A device change after outside text waits for your yes.
- **Only Signal.** Reached through Signal on your phone or Signal Desktop on your laptop. No web app and no open port: the bridge listens on nothing, so nothing on the network can connect to her. Messages travel end-to-end encrypted through Signal. Only the Signal accounts in the config get an answer. Other senders are ignored, never rejected. Calls go through Signal's relay servers, so your phone never learns the Mac's internet (IP) address. To keep your phone's address from the Mac as well, turn on "Always relay calls" in Signal on your phone.
- **Keys stay put.** Keys and sign-ins never appear in chat, logs or the code repository. Keys, passwords, the Signal captcha link, the SMS code and the PIN go into hidden prompts in your own Terminal: never on screen, and never on a program's command line, where other programs on the Mac can read them. Only her own hidden macOS account, `_estia`, can read the keys: a program you run needs your Mac password to read them, as long as no admin rule skips that password (the security step checks).

The short version, with the install in three steps, is [README.md](README.md).

Contents: [Meet Estia](#meet-estia) · [What she can do](#what-she-can-do) · [Reminders](#reminders-and-wake-up-calls) · [Messages she starts](#messages-she-starts-herself) · [Languages](#languages) · [Language practice](#language-practice) · [A few conversations](#a-few-conversations) · [Safe and private](#safe-and-private-in-plain-words) · [What runs where](#what-runs-where) · [Words used here](#words-used-here) · [When something is off](#when-something-is-off) · [Slash commands](#slash-commands) · [Requirements](#requirements) · [A rented Mac (VPS)](#a-rented-mac-vps) · [Set it up](#set-it-up) · [How long it takes](#how-long-it-takes) · [Tests and checks](#tests-and-checks) · [Built with](#built-with) · [What's next](#whats-next) · [Good to know](#good-to-know) · [For developers](#for-developers) · [License](#license)

## Meet Estia

She answers first. On a call, when you think aloud, she may ask one question back. Her built-in style is hearth: steady, kind and frank. Short answers, one concern when a plan looks risky, a joke only when asked for fun or on banter. The other built-in style, playful, adds a light joke now and then; setup asks which. On a call, neither jokes unless the caller jokes or asks for one (in the tests, hearth joked on 1 call of 25, playful on 2 of 8). Never a joke when you are worried or something serious is going on, and never in place of the answer.

In Signal, her profile reads Estia (AI), or the name set as `name` in the config. Ours reads Tiffany (AI). She speaks one language at a time: English, or one of ten others (see [Languages](#languages)). Her built-in style applies until a file, `soul.md`, replaces it. She reads `soul.md` but never edits it: no command and no tool writes it. It sets how she sounds, never what she may do.

## What she can do

"What can you do?" is answered from a list of the parts set up in this install, each in one line with one example; a part not set up is left out. The calendar line names each calendar's service (Google, Outlook, iCloud, Fastmail or Nextcloud; another CalDAV server by its host name), and the lights and plugs line names TP-Link (Kasa and Tapo), Philips Hue or both. A part with levels names them and the current one: voice answers (auto, on or off), what interrupts her on a call (only speech or any sound), the messages she starts (on or off, listed either way), Projects (off, summary, docs or code), Documents (on or off, with its folder) and the Mac's security (hardened or nuclear). While the evening note is on, the line of the messages she starts names its time. She answers from it in any language, in texts; calls leave it out (spoken answers carry no lists). `/help` shows the same list, word for word, above the commands.

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
| Evening note | `/evening on` (off until you do) | Three short questions at 21:00, or the time you pick: what went well, what is still open, one thing for tomorrow. The same words each evening, written by the code, never by the AI. `/evening off` stops it. |
| Language practice | `/babble nl` | A short conversation in Dutch (or one of seven other languages) at level A2 or B1, then a recap of at most three mistakes as a text |
| Music | "Play some jazz" | Spotify on your speakers (once set up) |
| Saved lists | "What should we play next?" | Ideas from a list you saved (songs, books, recipes) |
| Chords | "What are the chords of Spooky?" | Chords, key and tempo from a chord site, with a Sources message |
| Lights and plugs | "Turn off the living room lamp" | Kasa devices and Philips Hue lights on the home network, never through the TP-Link or Philips cloud |
| The Mac | "How is the Mac doing?" | A few lines on its health. Real problems are reported without being asked. |
| Photos | Send a photo with a question | An answer about the photo. The photo is deleted after the first answer. |
| YouTube videos | Send a YouTube link, with or without a question | A summary from the video's captions, with the time of each main point. Follow-up questions by text about the last video, within 10 minutes, need no new link. The captions are never stored. |
| Web search | "Who won the match last night?" | An answer that names the site and how recent it is, plus a Sources message |
| Places and routes | "A good Italian place near me?" · "How long to the station by bike?" | Up to five places with rating, price and today's hours, or the time, distance and lines, with Google Maps links (once set up) |
| Projects | "What's next on \<project>?" | Open to-do items and recent changes from a project on the Mac. An extra, off by default. |
| Documents | Send a PDF, or ask "What does my lease say about pets?" | An answer from your PDFs that names the document and the page. PDFs sent on Signal are saved on the Mac. An extra, off by default. |
| Transposing chords | "Transpose Am F C G up 2" | Chords transposed or moved for a capo, a chord's notes and the key of given chords, worked out on the Mac and given in a note from the code, not from the AI. An extra, off by default. |
| Memory | "Remember that …", `/profile`, `/fix`, `/forget` | Saves what you tell her, known in every message, and notes from your chats, given when they matter. Facts can be listed, corrected or removed. |

### Your day: calendar, mail and the digest

The morning digest comes every day at 09:15 (`digest_time` in the config): today's calendar first, then tomorrow in one line, then the mail that needs a reply. Declined invitations are left out. On quiet days she says so. If the Mac was off at 09:15, the digest comes when the bridge starts, if that is before noon.

One calendar or several can be read, each with a short name: Google calendars through their secret calendar link, the Outlook calendars of the Microsoft 365 mailboxes she already reads, and iCloud, Fastmail or Nextcloud calendars through CalDAV, the calendar standard they share. Outlook calendars are read with Microsoft's read-only calendar permission, `Calendars.Read`. With several calendars, each event carries its calendar's name ("Family: Football", "Work: 1:1 with Sam"). An invitation in two calendars is shown once. A calendar that can't be read is named with the reason, and the others are shown as usual. Events are read live and never cached. No calendar can be changed. When she looks up today, its timed events carry a mark from the code after their times: "(over)", "(now, until 15:00)" or "(in 40 min)", so "what's next?" at 14:30 gets the 15:00 meeting, not one that ended at 14:20; other days, all-day events and the digest have no marks. Examples: what's on a day, whether a time is free, when something is ("When is the dentist?").

A Google calendar's secret link is the one Google Calendar shows as "Secret address in iCal format". It works like a password, and it is kept in a file that only the calendar helper (a small program of hers) reads. A public link also works, but anyone can see a public calendar, so never make a private calendar public for her. Outlook calendars need `Calendars.Read` in the mailbox's sign-in. An administrator grants it once in Microsoft Entra, Microsoft's admin site, or the mailbox signs in again. Mail keeps working either way. Only the mailbox's own default calendar is read, not calendars others share with you. An iCloud, Fastmail or Nextcloud calendar is read with an app-specific password (iCloud accepts no other), kept in the same file as the secret links; its requests only read events, never create, change or delete them, and go only to that server (iCloud: Apple's icloud.com servers), encrypted.

Mail comes from the Exchange (Microsoft 365) mailboxes listed in the config. She reads mail only and never sends, moves, deletes or marks it. The headers (sender, recipients, subject, dates, and marks such as read, attachment or important) sync every five minutes. A mail's text is fetched only when needed (when you ask, or once to sort it) and is never stored. Headers, a label and a one-line summary are kept for 30 days. A mail counts as answered once you reply in the same conversation. Mail you archive in Outlook drops off her list. A mailbox whose sync fails, and whose last good read is more than 10 minutes old (two syncs, by default), gets one line in the digest, after the mail: "[work] not read since Mon 09:10: needs sign-in." The rest of the digest stays. The same line comes first when she looks up mail; at the start of a call she gets only the count and the time ("1 mailbox not read since Mon 09:10.").

Gmail is not built in. [setup_assistant.md, "Gmail (not built in)"](setup_assistant.md#gmail-not-built-in) explains why and lists the options. Forwarding Gmail to a mailbox she reads works today.

### Music and saved lists

Music needs a Spotify Premium account and a Spotify developer app (`setup_assistant.md`, "Spotify (once)").

Music is text only. Spotify's rules don't allow voice control, so calls and voice notes never start it. Examples: "play some jazz", "play my liked songs on \<speaker>", "next", "pause", "louder", "what's playing?".

A speaker starts at 30% and never goes above its cap (60% by default). The bridge also tries to wake idle Bose SoundTouch speakers listed in the config, over the home network. After an answer that used outside text (a web page, a mail or the calendar), music may pause or get quieter, but never start or get louder.

A saved list (songs, books to read) goes into your part of the profile, where it counts toward its 4,000-character limit. From it she suggests what to play or read next. Chords are looked up on the web, with sources.

### Lights and plugs

Kasa lights and plugs are controlled on the home network, never through the TP-Link cloud. Philips Hue lights are reached only through the Hue Bridge on the home network, never through Philips' cloud: on, off, brightness and whites, no colours. Examples: "turn off the living room lamp", "dim the bedroom light to 30%, warm white", "is the heater on?".

Each device has one rule: `normal`, `ask first` (waits for a yes, which must come within two minutes) or `never off` (switching it off is refused). After each change a short note says what happened, for example "Devices: living room lamp off", and says when a device doesn't answer. In English, an answer that says a light or plug was switched, dimmed or set while it was not changed gets a note from the code: "Nothing was switched." ("Nothing else was switched." when another one was). Also on a call; a plain report of a device's state ("the lamp is on") is never flagged.

**Only Kasa devices are tested.** Older Kasa devices need no login. Newer Kasa firmware (KLAP) and Tapo devices need the TP-Link login. Tapo devices use the same library, but they are untested. Tapo firmware with the TPAP login can't be controlled yet. The name `tapo` in `tools/tapo/`, `[[tapo.devices]]` and `tapo.env` covers Kasa devices too; `tools/tapo/` and `[[tapo.devices]]` cover Hue lights as well.

Hue lights are untested on a real Hue Bridge so far: the tests use answers in the shape of Philips' published API. `scripts/hue-pair.sh` pairs the bridge: press its button when asked. The bridge is found on the home network, or its address is given. Its certificate's fingerprint, a check value, is shown and saved at pairing, then checked on every connection: nothing is sent to a bridge that shows another certificate. The key the bridge gives is never shown. Each Hue light goes in `[[tapo.devices]]` with `brand = "hue"` and the bridge's id for it (`scripts/tapo-discover.sh` lists them), and follows the same rules.

### Photos

A photo, with or without a question: "what is this?", "what colour was it?". Up to four photos fit in one message. Follow-up questions within five minutes still see the photo. After that, the photo must be sent again. The file is deleted after the first answer.

### YouTube videos

A YouTube link, with or without a question: "what's the main point?", "what does it say about sleep?". The answer comes from the video's captions only, with the time of each main point, and says when YouTube generated the captions automatically (names and terms may be misheard). Only a link in your current message is read. A video found by a web search is named, never read.

A long video is read in parts, up to its first 2 hours, after a note: "Reading a 1 h 40 min video: about 1 minute." Captions too long to read whole (more than 120,000 characters) are read up to there, and the note and her answer say how far. An answer is at most about 300 words and never holds the captions word for word: code leaves out any sentence of hers that copies 13 or more of their words in a row, or shorter stretches of 6 or more that together make up more than 30% of them, and adds the YouTube link. Asked for the full transcript, she gives the link.

Follow-up questions by text about the last video, within 10 minutes of the first read, need no new link. One video is kept at a time: a new link replaces it. The captions are kept in memory only, never on disk, and are gone after those 10 minutes or at `/new`. They are never stored or learned from, and `/learn` refuses a video link. A video without captions is not read: nothing is transcribed. Calls never read a video, since a link cannot be spoken.

Only YouTube's own site, www.youtube.com, is asked, with the video's ID (the 11-character code in the link), never the whole link. No account and no key of yours (the helper uses the public key YouTube puts in the video's page); cookies YouTube sets last only for that one read. Other YouTube addresses (a playlist, a channel, youtube-nocookie.com) are read as ordinary web pages, which hold little more than the title. YouTube's terms forbid automated access and downloading its content: a known gap, accepted for personal use ([SECURITY.md](SECURITY.md#known-accepted-gaps)).

### Web search, with sources

Ollama's web search runs first, and Brave Search is the backup. Brave is capped at 2,000 searches a month by default (`brave_monthly_cap`; set it to your Brave plan's limit). An answer runs at most two searches. The answer names the site. For news, scores and prices, it also says how recent the source is. Links come in a separate Sources message, not in the answer. Search queries go only to Ollama and Brave. Mail, calendar, project and document text never go into a query: an answer that read them searches nothing. Results are never stored or learned from.

### Places and routes

Status: built, waiting for a Google Cloud key (Places API (New) and Routes API; see `setup_assistant.md`, "Places and routes (once)").

Places come with rating, price and today's hours, for up to five places: "a good Italian place near me?", "is the museum open now?". Routes come with the time, distance and lines: "how long to the airport by train?". Google Maps links come in the Sources message.

"Near me" means the location last sent in Signal (attach, then Location). It is used for three hours and never logged. The message itself stays in the chat history, like any message. Without one, the home area is used. "How do I get to my 3 pm?" can use the event's place. A daily cap per API (`daily_cap`, 30 by default) keeps it inside Google's free allowance.

### Memory and personality

She remembers the current conversation and searches older ones ("What did I say about the trip?"). `/new` starts a fresh conversation and keeps the profile.

"Remember that …" saves a fact at once. She also picks up facts from what you say, and reports each one saved. A fact you told her to remember, or one from `/learn`, is never replaced or removed by something she picked up. `/profile` lists your facts and where each came from, with one line on her notes; `/profile notes` lists her notes. `/fix` corrects a fact in your own words. `/forget` removes one.

The profile has two parts:

- **Yours:** what you told her to remember, facts from `/learn` and facts you corrected with `/fix`. Up to 4,000 characters, known in every message.
- **Her notes:** facts she picked up from your chats. Up to 12,000 characters. Each message carries only the notes that may matter for it: the ones that share its rarer words, then the newest, at most 1,500 characters. A search of earlier chats also finds them.
- **Full:** a new "remember that …" in a full part of yours merges or shortens your facts; a fact taken out moves into her notes, never deleted, and the move is reported. A moved fact still shows where it came from, and learning never changes it. Full notes are merged or dropped among themselves, never yours. A note you correct with `/fix`, or ask her to remember, becomes yours.
- **Known limit:** notes are matched by shared words (small English words such as "is" or "to" left out), so a note on the same topic in other words ("cook tonight", "likes Thai food"), in another language, or in Japanese, Korean or Chinese (written without spaces between words) stays out unless it is among the newest.

`/learn <link>` reads a page, saves only the facts about you in it, and lists what was saved.

**Her style.** Two built-in styles, picked in setup or with `style` under `[personality]` in the config (left out: hearth):

- **hearth**, the default: steady, kind and frank. The same even temper every day. Calm and plain: no gushing, no rows of exclamation marks. The answer first, in one to four sentences unless you ask for more, with at most one question back and no flattering opener ("Great question!"), no cheerleading and no recap of what you just said. Concrete suggestions over hedging; in a brainstorm, she builds on the idea, adds one new angle, then asks one question. A goodbye gets one short line, no summary, also on a call, where she speaks in short sentences, one idea at a time, without lists. A weak or risky plan gets one concern with the reason, then help; pushed back, she does not give in, though in the tests she mostly said a safety concern again. At most one short joke, only when asked for fun or on banter; asked for something fun, more often a fun fact. Asked whether she is a friend or what she is, she says she is an AI, not a person: no feelings claimed, no guilt after a silence.
- **playful**: warm, curious and playful. The answer first; when the moment invites it, one short, light joke follows, never at your expense.

Neither jokes when you are worried, sad, stressed or in a crisis, talk about health, money trouble or safety, or when something serious is going on: calm, kind and practical instead. An install from before without `soul.md` now has hearth; `style = "playful"` brings back the earlier style, word for word in texts.

**On a call**, a short spoken version of the style takes its place: short sentences, one idea per turn, warm from the first words, no joke unless the caller jokes or asks for one, calm on a worry.

**`soul.md`, a style of your own.** It replaces the built-in style entirely: her tone, how long she answers, how she sounds on calls. Written only by you: she never edits it, and no command or tool writes it. Read for every answer, so an edit counts from the next message. It sets how she sounds, never what she may do. At most 2,000 characters; lines starting with `#` are notes for you and are left out. `soul.example.md` is a draft to start from.

- **Two parts.** A line `[chat]` starts a part for her texts and the messages she starts herself; a line `[call]` starts a part for calls. The text above the first of them counts for both, and a file without them counts whole for both. A file with nothing for calls leaves calls the built-in style's call version.
- **The calm line.** One line is added after the file's text, in code: no joke when you are worried, sad, stressed or in a crisis, or talk about health, money trouble or safety; calm, kind and practical instead. `calm_on_worry = false` under `[personality]` leaves it out; `/soul` and doctor then say it is off. Without `soul.md` no line is added: the built-in styles carry it.

`/soul` shows the file in use, or why none is and the built-in style in use. A change in the config counts after a restart.

**The evening note**, three short questions from the code each evening, is off until `/evening on`: see [Messages she starts herself](#messages-she-starts-herself).

### Calls

A call is answered after about two rings, with a greeting. While it rings (a call she places: once it is picked up), the model loads and reads her instructions and the conversation so far, so her first answer comes sooner; not yet timed on a real call. A turn that sounds finished ends after a short pause (0.6 seconds): Smart Turn, a small model on the Mac, hears only the last 8 seconds and judges only whether the sentence sounds finished. Fairly sure, she waits 1.5 seconds; unsure, she waits the full pause, a little over two seconds, so a caller can think aloud. A turn that ends like it goes on, or with a goodbye, always waits the full pause. Typing, dishes or traffic that goes on in a pause keeps a turn open for less time (in tests, 1.9 to 4.7 seconds less); a short noise in the first second or so after the last word still keeps it open, and other voices mostly do too. Until the voice service has Smart Turn ready, every turn waits the full pause; until its speech detector is ready, any sound keeps a turn open. `early_end = false` in `[calls]` makes every turn wait the full pause, and `pause = "quiet"` lets any sound keep a turn open again. Known limits: in tests, an unfinished sentence with a pause of 0.6 seconds or more inside it was judged finished at once about 1 time in 22, and about 1 in 16 when the pause lasted 1.5 seconds; in Polish and Spanish 1 in 15 at once, and with street or café noise about 1 in 9 in German and Russian. Most test voices were computer voices (92%); not yet tested on Signal's call audio. A caller can interrupt her at any time. Typing and other short noises are ignored. A listener's "okay", "yeah" or "mm-hm" alone, in the call's language, does not stop her either: she carries on, as after a cough, unless she just asked something. A quick "stop", "wait" or "but" always counts. With `interrupt = "only speech"` in `[calls]` (the default), a speech detector on the Mac (Silero VAD) decides what stops her, so road noise rarely cuts her off; until it is ready, any loud sound does. With `interrupt = "any sound"`, any loud sound stops her. A whisper may not stop her; a radio or a passenger talking does. Her own voice from a loud speaker next to the microphone (a laptop on Signal Desktop) is told apart from the caller's and does not stop her: she carries on, as after a cough, and what the microphone heard of her own voice never reaches the model. Once she has taken in a request, she says a short line such as "Got it." or "On it." (not after a few words such as "how are you?", and never on two requests in a row; `acknowledge = false` in `[calls]` turns it off). When the model's first try comes back empty and is asked again, such a line comes at once, even after a few words, so the caller is not left in silence (never twice in one turn, never on two turns in a row). Her answer follows when it is ready. `fillers = true` adds a "let me think" line when it takes a while. If the caller hangs up before she answers, she stops and texts that it was not done. Silence: after 90 seconds, a check-in that names her ("Take your time, I'm here. Say Estia when you need me."; at most every ten minutes), then standby: only a turn with her name is answered, so other talk, a TV or kitchen noise is left alone. A turn without her name is heard on the Mac and dropped: never answered, never kept. Her name ends standby ("Estia, what time is it?"). In standby, "I'm still here." every ten minutes; the call ends at its length limit (an hour by default) or when either side hangs up. Known limits: a word that sounds like her name ends standby too, and so does a name that is also a common word (Mia in Italian or Spanish); a short name that Russian writes with another vowel ("Сэм" for Sam) is missed; in Japanese, Korean and Russian the check-in asks to call her by name, as her voice there reads no Latin letters; not yet tested on a real call. In English, a goodbye ("bye-bye", "good-bye", "talk to you later", "take care") ends the call once her answer is spoken, also with closing words after it ("okay, goodbye, I'm going to check the log now") and also when her answer asks something; a device question waiting for a yes keeps the call open until it is answered, and after a plain answer ("yes", "no thanks") the call ends. Leave-taking ends the call the same way, as a phrase of its own ("Okay, I have to go"), not inside a longer sentence ("I have to go to the dentist"): "I have to go" or "I'm hanging up" in English, "ik moet gaan" in Dutch, "ich muss los" in German, and a few clear phrases in the other languages ("je dois y aller", "じゃあ、切るね"). A wrap-up ("that's all") ends it too, unless she asks something. Leaving with a reason counts too ("I have something to do", "something came up", "I have to run"), in each of the 11 languages; "something came up" only with nothing but "sorry" or "thanks" before it, so an apology ("Sorry I didn't call earlier. Something came up.") keeps the call open. Asking her to end the call ("let's end the call", "we can stop here", "let's call it a day") ends it once her answer is spoken, also when her answer asks something, in each of the 11 languages. Her own goodbye ("Bye!", "Talk soon!"), closing wish ("Enjoy your evening!") or offer to wait ("I'll be here when you're ready.") ends the call too, unless her answer asks something, once the caller has been quiet for 3 seconds: anything said in those 3 seconds keeps the call open and is answered; not on a practice call. Stage directions in her answers ("(pause)", "(laughs)", "*smiles*") are never spoken, on a call or in a voice note, and never sent in a text; bold words, code and a title in brackets ("(Applause) by Lady Gaga") stay, and so does a capitalised one inside a sentence ("Also (Pause) weiter"), which is still said. After a call with two or more turns, a short summary follows by text.

At the start of a call she knows today's numbers, never the titles: how many events are left, when the next one starts and the last one ends, and how many mails need a reply or an action. With the calendar or mail set up, she may offer the rundown once ("Three things today, the next at two. Want the rundown?"). Titles, places and senders are read only after your yes, as in a text. The numbers come from the code; she says them in the call's language.

Lights and plugs work on calls too. A device yes counts only once she has finished her question, so a yes said over the question is ignored.

**Read aloud in English.** On calls and in spoken answers, numbers, money, dates, units and chord names are said as people say them: "€12.99" as "twelve euros and ninety nine cents", "$1.5 million" as "one point five million dollars", "Fri 16 Oct" as "Friday the sixteenth of October", "in 1998" as "in nineteen ninety eight", "+44 20 7946 0958" as "plus four four, two zero, seven nine four six, zero nine five eight", "23 kg" as "twenty three kilograms", "20°C" as "twenty degrees Celsius", "3/4" as "three quarters", "e.g. apples" as "for example apples", "info@example.com" as "info at example dot com" and "C G7 Am F" as "C major, G seven, A minor, F major". Chord charts too: bar lines, beat slashes, "…" and "..." between chords are pauses, and the chart's marks are words: "Verse: G D Em C (x2)" as "Verse: G major, D major, E minor, C major, twice", "Intro: N.C., then G" as "Intro: no chord, then G major", "Then Cm7(b5)." as "Then C minor seven flat five.". Written answers show them as they are.

**Read aloud in the other ten languages.** The same readings are said in each language's own words:

- **Spanish, Italian, Portuguese and French:** "12,50 €" as "doce euros con cincuenta", "Il 1° maggio è festa." as "Il primo maggio è festa."; chords in do-re-mi names: "Fa#m y Sib" as "fa sostenido menor y si bemol", and a chart's marks in each language's words: "Estrofa: Am F C G (x2)" as "Estrofa: la menor, fa, do, sol, dos veces".
- **Dutch and German:** "om 9.30 uur" as "om negen uur dertig", "am 1. und 2. Mai" as "am ersten und zweiten Mai"; chords with German letter names: "Hm und C#m" as "H-Moll und Cis-Moll", "Am F C G x3" as "A-Moll, F, C, G, dreimal".
- **Polish and Russian:** in the case the sentence asks for: "od 1 do 3 listopada" as "od pierwszego do trzeciego listopada", "в 1987 году" as "в тысяча девятьсот восемьдесят седьмом году".
- **Japanese and Korean:** most digits stay as written; times, big numbers, phone numbers, temperatures and chords in words: "1,234,567人" as "123万4567人", "13:00에" as "오후 한 시에".

**Known limits:** a few readings are guesses from the words around them. A date whose day and month could swap ("3/4/2026") is read as numbers. "St." before a capitalised word is read "Saint", even in a street's name ("Wall St. Journal"). A rating is read as a fraction ("8/10": "eight tenths"). A domain ending that is also an English word ("example.it") is read as that word. Three or more capital letters A to G with only spaces or bar lines between them are read as chords ("A D E A") when they are the whole sentence, even a table row of answers ("| A | C | B |"), unless they run in alphabet order ("A B C") or the sentence names notes; inside a sentence they are read as letters ("Standard tuning is E A D G B E"). A chart's marks are words only among chords; in a sentence about music "N.C." is also "no chord" after a colon, a section's name or, in English, "on", "is" or "with" ("The song ends on N.C."), and a repeat is also a word after a section's name or in brackets at the end of a clause ("Then the chorus again (x2)"): "NC" alone, a place ("lives in N.C."), a quantity ("Buy strings (x2) for the guitar") and "% %" stay as written. "C-7" is minor only in a sentence about music ("F#-7", with a sharp or flat, anywhere), so a road's name in such a sentence is read as a chord ("The A-7 to Malaga, then the song"), and "C-" alone is never read as minor. In the other ten languages:

- A name that is also a chord ("G7", "C#", "Am") is said as a chord only in a sentence about music or among other chords: elsewhere "G7" stays a name and "C#" is read "C sharp".
- The words for a chart's marks ("sin acorde", "kein Akkord"), for the signs in a chord's alterations ("b neun", "mol negen") and for "no3" ("sin tercera", "ohne Terz") still need a native speaker's check, and they never change with the sentence ("endet auf kein Akkord"). Japanese and Korean read "C-7", "Cm7(b5)" and a chart's marks as before.
- "1" before a noun takes a gender guessed from the noun's ending in Spanish, Italian, Portuguese and German ("una hija", but "un día"), and so do the hundreds in Spanish and Portuguese ("doscientas páginas"). In French "1" before a noun is always "un" ("un personne").
- Polish and Russian: a number after a word such as "od" or "до" takes the genitive only up to 99, and not yet before money. A Polish ordinal outside a date is read as a plain number.
- A phone number with no spaces and no leading zero is read as one big number, unless the sentence names a phone.
- A time range after a word such as "alle", "à" or "в" repeats it ("alle dalle quattordici").
- Fractions and scores keep their sign ("1/4", "2:1").
- In a live check, Japanese and Korean numbers of five digits or more were still misheard.

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

**The evening note** is separate, and off until you turn it on: `/evening on` in Signal, or `evening = "21:00"` in `[initiative]`. At that time, three questions from the code, never from the AI: "Evening. What went well today? What is still open? One thing for tomorrow?" The same words every evening: no streaks, no counts, nothing after a silence. A reply is an ordinary message. `/evening 20:30` moves it, `/evening off` stops it, `/evening` shows which. A typed time inside the quiet hours is refused; one in the config never comes, and `/evening` says so.

It needs no `/initiative on`, never counts toward the one a day, and is never held back by it. It waits while she answers, a call is on, you wrote in the last 15 minutes, language practice runs, quiet hours run, or `/quiet` or `/lock` holds. Two hours after its time, or at midnight if that comes first, it is dropped for that evening. At most one a day, and only to the account that typed `/evening on`. Another message she starts waits 15 minutes after it. The time in the config goes to the only allowed account; with several allowed, to none until one of them types `/evening on`. It ends with "(evening note · /evening off to stop)". Only a typed command turns it on or off: never a voice note, a call or the AI. While it is on, her list of what she can do (`/help`) names it with its time and `/evening off`.

### The Mac itself

Ask "How is the Mac doing?" for a short status: the model and helper services, disk, memory, uptime, recent errors, and the state of Signal, mail, calendar, lights, music, voice, calls, web search (searches today and this month) and maps (requests today).

The Mac is checked every four hours. A problem is reported once it has lasted five minutes. A "Back to normal" note follows when it is fixed. In quiet hours (22:00 to 08:00 by default) both wait until the morning, and a problem that came and went overnight is one line then. Security notes, such as a new linked device on her account or a paused model, are never held.

### Projects (extra, off by default)

Setup asks whether she should see projects on this Mac, which folder, and how much. Most people: no.

- **summary**: the README, open to-do items and recent commits
- **docs**: also the project's documents
- **code**: also its source code

Only files git tracks, never secret-like names such as `.env`, never outside the folder you named. The whole README is read on request at every level, a page at a time. A name heard on a call is matched to the nearest project: "Photo Book" is taken as fotobook, "garden app" as garden_app. When two names sound alike, or none is close, she asks ("Do you mean fotobook?") instead of guessing. Asked what she can see, she names each project's level and how to change it: `[projects]` in the config. `scripts/setup.sh --extras` switches projects on later.

### Documents (extra, off by default)

The PDFs in `~/Estia-KB` on the Mac and the PDFs sent to her on Signal: a lease, a manual, a school letter, an insurance policy. Asked later, she searches them for the words of the question as they are written (no other forms of a word, no translation), reads only the matching pages and names the document and the page ("lease.pdf, page 3"). A section asked for by its label ("B5 from the practice exam") comes in full, also when it goes on onto the next page. Nothing found: she says so, never guesses.

- **One folder, yours.** Always `~/Estia-KB`, directly in your home in your own macOS account, whatever her name. Not in Documents: macOS guards Documents, Desktop and Downloads; a folder in your home needs no extra permission. Finder, Spotlight and Time Machine treat it as any folder of yours (the files she adds: untested). She lists and reads every file there. In your home, her account adds files only to `~/Estia-KB`, and never deletes or changes yours. What she adds is yours to open, move or delete. `scripts/share-documents.sh`, run as you without admin rights, gives that access and makes the folder when missing; `scripts/share-documents.sh --revoke` takes it back, and her index of the documents goes at her next look. A new share always starts a fresh index.
- **A PDF sent on Signal** is saved in that folder under its own name with the date ("2026-10-14 lease.pdf"), never over another file, and read at once. A note from the code says where it went and how many pages were read. A stranger's file is never saved.
- **A PDF put in the folder** (Finder, AirDrop, a download) is found at the next look: every hour outside quiet hours (`look_minutes`), and whenever you ask about documents or type `/docs`. A note from the code names new ones: "I found 2 new documents: lease.pdf, boiler-manual.pdf." It waits until quiet hours end (22:00 to 08:00 by default), is skipped under `/quiet` (with the messages she starts herself or the evening note on), and never comes for a PDF sent on Signal or one she was asked about. A PDF taken out of the folder drops out at the next look.
- **Read on the Mac, without Gemma.** Apple's PDFKit gives a page's text; a scanned page, with no text of its own, is read by Apple's Vision. Gemma is not used in the look, the reading, the index or the note: it only answers your question. A PDF is read by Apple's PDFKit and Vision in a helper program that runs as her account: a PDF made to attack the reader reaches only that program and her account, never yours.
- **Limits:** 25 MB and 300 pages a document; 60 seconds to read it; one document at a time. A scan of more than about 190 full pages takes longer than 60 seconds and is not read. A page that cannot be read is skipped, and she says so when asked about it.
- **Two columns and tables.** A PDF with its own text is read one column after the other, left first. Scanned pages in two columns: of 18 test layouts, 14 were read with at most 2 letters wrong, 1 mixed the lines of its two columns (set close together) and 3 lost words (10-point type). A table comes out as lines: one line per row when the PDF was written row by row (Word, Excel), else one cell per line, column after column, as for every scanned table.
- **Scans:** in tests on clean scanned pages, about 1 letter in 120 was misread, mostly accents. Real scans (skewed, noisy, stamped) are untested. A scanned page that carries a line of real text, such as a stamped page number, is read as that line only.
- **Kept on the Mac.** The text of each page stays in her database until the PDF leaves the folder or the share is taken back; `[privacy] keep_days` does not delete it. A document's text never goes into a web search, a Google Maps request or a link. `/docs send N` sends a PDF back only to the account that asks.
- **iCloud.** `~/Estia-KB` is outside Desktop and Documents: iCloud Drive's Desktop and Documents Folders never send it to Apple's iCloud.
- **On a call** she answers from the pages already read: nothing new is read, and nothing is sent until the call ends.
- **Setup lists it among the extras but does not set it up:** run `scripts/share-documents.sh` as you (it makes the folder and prints the two lines), add the two lines to her config (`[documents]`, then `folder = "/Users/<you>/Estia-KB"` on a line of its own: your folder written in full), then restart her ([setup_assistant.md, "Documents (extra, off by default)"](setup_assistant.md#documents-extra-off-by-default)). The share refuses a folder that holds a git checkout (`.git`): her account would add files to code you run. A folder set elsewhere, in Documents, Desktop or Downloads, may be refused by macOS itself: her log (`bridge.log`) names that refusal (`EPERM`); doctor's own read runs from your SSH or Terminal session and may differ from her background service ([SECURITY.md](SECURITY.md#known-accepted-gaps)).

### Transposing chords (extra, off by default)

Chords transposed or moved for a capo, a chord's notes and the key of given chords, worked out on the Mac and given in a note from the code, not from the AI. On texts and calls. Examples: "Transpose Am F C G up 2" gives "Up 2 (a whole tone): from Am (A minor, likely; C major also possible) to Bm (B minor)", then "Bm G D A"; "What key is Am F C G?" gives "Likely A minor: i VI III VII. C major is also possible: the same chords."

- **A song's chords, transposed.** "Get the chords of Spooky and transpose them from Am to Em" or "… 4 semitones higher": the chords come from a web search, with its Sources message, then are worked out on the Mac, sections as found. A chord page's own "Key:" line is read by the code, not by the AI. On a call the result is said, and after the call one message from the code holds the exact chords and the page they came from.
- **Names as written.** Letters A to G, do re mi ("LAm FA DO SOL" up a whole tone: "SIm SOL RE LA") and German and Dutch names (H7, Fis, "B-Dur" for B♭ major), written back the way they came, sharps and flats as typed. B is read as B♭ only beside an H or a German name. A key written in words in your message ("in h-Moll", "la menor", "変ロ長調") is read by the code itself, in all 11 languages, and wins over the AI's misreading of it (a key of the same letter or the same sound). For a move it also wins over another key, but never as the key the song is in now ("it is in G major"); for a capo only over a misreading, since a capo question names the song's key as often as the key to sound in.
- **Spelled for the key.** The key moved to sets every sharp and flat, with as few as possible: F#m up a whole tone is G#m, never Abm, and a song moved to C♯ major is written in D♭ major, with a line that says so. F♭, E♯, C♭ and B♯ are written E, F, B and C. A step in semitones is never refused; when the key is unclear, the note says which key spelled it. A move to a key is refused only when the song's key is unclear and the key asked for cannot settle it; the note then asks for the song's key.
- **A capo, both ways.** The fret for each open shape (C, A, G, E and D; Am, Em and Dm) from a key or chords, frets 0 to 7 first; or the key heard, from the fret and the shapes played. Fret 0 is said as no capo. When the AI gives the shapes' own key as the key to sound in ("G shapes" for "sound in Bb, playing G shapes") while the message names another key, the AI gets a question back, never an answer with a key picked by the code; the chords played, and a key to sound in written in words ("I sing it in G major"), are never taken as another key. Known gap: a capo question that names two keys, the recording's and the singer's ("the original is in A major, but I sing it in G major"), may get the recording's key as the key to sound in: the AI chose it in 6 of 7 tries (18 Oct), and the code cannot tell which one is meant. A sheet with a "Capo 3" line gets new shapes at the same fret, or the same shapes with the capo moved; shapes named on that line ("Capo 3 (G shapes)") are taken as written and moved with the chords. A tuning line counts only when every string is tuned down by the same step (half step down, D standard). Guitar only.
- **The key is a likely key**, from the chords alone: its relative and any close key are named, and fewer than 3 chords get "too few to tell". A key the user or the chord page gives comes first. Of 50 progressions written by hand, 49 were named right. Weak cases: modal songs, blues, power chords, loops and key changes. Numerals only for chords in the key (I to vii°, and the blues' I7 and IV7); on a call, the key without numerals.
- **Her words are checked.** A sentence of hers with a chord the code did not work out, and that no source holds, is left out; the note from the code follows. Chords she works out herself in a text are sent back to her once; on a call they are left out at once. A list of chords is checked as a whole, and goes as a whole. A capo's fret she names ("Capo 4", "im 4. Bund") must be one the code worked out or the message names, or that sentence is left out too; a fret in words ("the third fret") is not checked. Known gaps: after the code's work, its chords said again, in another order or with one left out, pass; without it, her chords count by their root alone, so chords whose roots the message or the page also has pass ("down one: Am F C G" after a page with Am F C G).
- **Limits.** At most 64 chords and 1,000 characters a request. A web search keeps at most 1,200 characters of each page, so a long chord sheet comes in part. A chord that cannot be read is named in the note from the code and never guessed: a request that puts another chord in its place is refused, and one that leaves it out says so ("Hm7b" left out), on texts and calls. Not read: Polish lower-case minors ("a e C G"). On a call in another language, the result is said in English, the chords in letters. The capo words of the ten languages (Kapodaster, cejilla, capotasto) still need a native speaker's check. The AI sometimes translates a name in its request ("G" for the user's "Sol", "Bb" for "SIb"): the fret is right, and the note then names the shapes or the key in letters. A restart of the bridge during a call loses the message after it.
- **Nothing to install.** Estia's own code, with no download and no program from elsewhere. `scripts/setup.sh --extras` lists it, and a yes adds `[chords]` to her config.

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
- **Voices:** English uses Kokoro (`af_heart`, or `am_michael` when setup gave her a male name). `/voice` works as before. The other ten use Supertonic-3, voice F1. Its first use downloads about 265 MB and takes about a minute. After that, it is ready in a fraction of a second. It loads in the background when the bridge starts and after `/language`.
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
> **Estia:** *(a fun fact or two, at most one short joke: hearth more often ends with a 😄 or offers a joke; playful ends with one)*

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
- **Only the devices she knows.** A message from a new linked device on your account gets no answer and never reaches the AI; a note from the code goes to your account instead, so your phone sees it too. `/devices ok N`, sent from a device she already knows, lets it in.
- **An emergency stop on your phone.** `/lock` stops everything at once (answers, messages she starts, calls and reminders) until `/unlock` from the same device, 10 minutes later at the earliest. It holds after a restart and needs no Mac.
- **Her own account on the Mac.** She runs as `_estia`, a hidden macOS account with no password and no login. Her data and keys are readable only by it; her code and every program she runs are copies owned by root, the Mac's all-powerful admin account. A program you run, an AI session included, reads none of it without your Mac password, as long as no admin rule skips the password.
- **The model stays on the Mac.** A cloud model, which Ollama runs on its own servers, is refused: by its name when the config is read, and by Ollama's own list at the start, every five minutes and before every chat. Once the check sees one, no message goes to it: answers pause, a note from the code says why, and doctor shows ✗ with the fix.
- **A security step in setup.** Remote login (SSH) with keys only, no admin rule that skips your password, her files private, and her Registration Lock (a Signal PIN on her number): each shown with its state, and changed only on your yes (`scripts/harden.sh`). FileVault, automatic login, the screen lock and a few more are shown with their fix, never changed.
- **The nuclear option.** `scripts/harden.sh --nuclear` adds the strictest settings: the Mac's firewall, stealth mode (its own question, default no), python3 and ruby (programs that run scripts) off the firewall's list of programs let in, and a login keychain that locks after 15 minutes and at sleep. It says first, plainly, that they may stop other services on this Mac: programs that other devices reach (a file share, a media server, a page you open from your laptop) stop answering them unless you allow each one; with stealth mode the Mac no longer answers ping, the usual "are you there?" check; with the keychain lock, macOS asks for your password more often. Each one is asked first and changed only on your yes, and it is never part of setup. The firewall question names the programs that listen for other devices now and asks about keeping each one; with the firewall on, doctor names a new one later, with the two commands that let it through. Without `--nuclear`, these settings are one neutral line in the security step and in doctor, never a warning. Nothing turns off what a Mac already has. The Mac's security has two levels: hardened, set by the security step (`scripts/harden.sh`), and nuclear, the hardened level with the Mac's firewall on and python3 and ruby off its list (`scripts/harden.sh --nuclear`; its stealth mode and keychain lock do not count toward the level). Doctor names the level the Mac is at.
- **Mail and calendars are read-only.** She never sends, moves, deletes or marks mail, and never changes a calendar event.
- **Outside text is read as information, not as orders.** That covers web pages, YouTube captions, search and map results, mail, calendar entries, project files, documents, photos and shared audio files. Code rules reduce prompt injection (orders hidden in that text) but cannot rule it out. A device change after outside text waits for your yes, and device, profile and reminder changes have their own checks in code.
- **Only your words teach her.** Facts come from what you say, or from a page you ask her to learn from with `/learn`. Mail, calendar entries, YouTube captions, web and map results never teach her anything. Neither do documents.
- **Reminders come only from you.** She sets a reminder or a wake-up call only when you ask in your own words, never because a mail, a page, a video, a calendar entry or a shared audio file says so. A document never sets one either. She calls only the allowed Signal accounts, and only for a reminder you set.
- **When the AI writes part of a message she starts, it sees only your words, your profile and the moment.** Never mail, calendar entries, the web or a page. The code decides when. Calendar notes are written by the code from the calendar, with no model. A text is not sent when it claims she did something, asks for a code or a password, or holds a link: any web address, with or without https:// or www., on any ending, such as "example.ch/x". The words that ask for a code or a password, and claims of a reminder or a call, are checked in all 11 languages. The other word checks read English; in other languages her rules and the AI test set carry the rest.
- **A shared audio file is not your voice.** An audio file shared from another app counts as someone else's words. She can summarise it. She never learns from it, never counts it as your yes, and never switches a device because of it. A voice note forwarded inside Signal may still count as yours.
- **Every device has a rule.** Each device is `normal`, `ask first` or `never off` (switching it off is refused). A device change in a message that is not about devices also waits for your yes.
- **No shell.** The AI has no command line: it cannot type commands on the Mac. With Projects on, she reads only files git tracks in the folder you named, at the level you chose, never secret-like files such as `.env`. With Documents on, her document tools read only her index of the PDFs in `~/Estia-KB`.
- **One folder of yours for documents.** In your home, her account adds files only to `~/Estia-KB`, and never deletes or changes yours. A PDF is read by Apple's PDFKit and Vision in a helper program that runs as her account: a PDF made to attack the reader reaches only that program and her account, never yours. What such a program could still reach is listed in [SECURITY.md](SECURITY.md#known-accepted-gaps).
- **Chats, voice-note and call transcripts, memory and profile stay on the Mac.** They sit in one database on the Mac (`assistant.db`) until you delete it. Photos, voice audio and call audio are deleted after use. Mail bodies, YouTube captions, web results and map results are never stored. With Documents on, the text of your PDFs is kept in that database until a PDF leaves the folder. Logs hold only what happened and when, never message text. Keys and config files are readable only by her own account (`_estia`), and your copies of keys stay in your login keychain.
- **Places stay rough.** For "near me", only an area rounded to about a kilometre goes to Google, or the location you sent. Your home address goes to Google when it is looked up once, and for routes from or to home. The address stays in the config file only: the database keeps the rounded area and a scrambled tag of the address, to notice when it changes, never the address itself. A Directions link to or from home names that rounded area, never the address. An address an earlier version kept in the database, in those rows or in a Directions link it sent, is removed at the next start. An answer that read your mail, your projects or your documents never sends anything to Google Maps.

Known gaps are listed in [SECURITY.md](SECURITY.md#known-accepted-gaps).

### What stays home, and what goes online

The model always runs on the Mac. Outside services are reached only for Signal, links you send and the extras you switch on. Mail syncs every five minutes in the background. The digest reads the calendar each morning.

| Stays on the Mac | Goes online, only when used |
|---|---|
| The AI model (Gemma, run by Ollama) | Signal, for your messages and calls (end-to-end encrypted) |
| Chats, memory and profile | Microsoft, to read mail and Outlook calendars (read-only) |
| Speech-to-text and text-to-speech | Google's calendar links, for Google calendars (read-only) |
| | iCloud, Fastmail or your Nextcloud, for CalDAV calendars (read-only) |
| Health checks and project reading | Ollama's web search and Brave Search |
| Lights and plugs (home network) | Spotify, for music |
| Reading your PDFs (Apple's PDFKit and Vision), with Documents on | Google Maps, for places and routes |
| | Web pages: links you send her, and the top result of a Brave search |
| | YouTube, for the captions of a video link you send |

Setup also downloads the model (from ollama.com), Ollama's official build (from GitHub, checked before it is installed) and, for calls, signal-cli and the call helper's source code (from GitHub and crates.io). It runs Homebrew, the installer for Mac tools, with Homebrew's usage reports (analytics) off; brew commands you type yourself keep your own Homebrew setting.

The voice models come from Hugging Face, an online library of AI models. Whisper's files (listening), the speech detector's and Smart Turn's are fixed to one version and checked against their fingerprints on the Mac at every start: a changed or extra file is never loaded. Whisper's are downloaded once, and Whisper then starts with no network; a Whisper or Smart Turn folder with a changed or extra file is set aside and downloaded again at that version. Smart Turn's files are a community conversion of Daily's model, not Daily's own: compared once with Daily's file, the same verdict (finished or not) on 7,192 of 7,200 test windows; if that version is removed from Hugging Face, a new install cannot download it, and every turn waits the full pause. The speaking voices (Kokoro for English at the first start, Supertonic-3 for the other ten languages on first use) are downloaded as published, with no fixed version and no check of their files.

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
   └── Helpers            lights and plugs, calendar, YouTube, documents, call audio
```

| Part | Where in the repo | How it runs |
|---|---|---|
| Bridge | `src/local_assistant/` | Always on: a background service that starts with the Mac (`com.local-assistant.bridge`), as her own user `_estia`, from a copy of the code owned by root in `/usr/local/estia` |
| Voice service | `tools/voice/` (Swift) | Always on, as `com.local-assistant.voice`, as `_estia`, reachable only from the Mac itself |
| Device helper | `tools/tapo/` | Started per command, in its own Python environment |
| Calendar helper | `tools/ical/` | Started per read, in its own Python environment |
| YouTube helper | `tools/youtube/` | Started per video read, in its own Python environment |
| Documents helper | `tools/documents/` (Swift) | Started per PDF, as `_estia`; Apple's PDFKit and Vision only; needs no network |
| Call helper | Build script, pins and patch in `tools/call-tunnel/` (built on your machine) | Started by signal-cli for each call |
| Scripts | `scripts/` | Run, health check, install, the move into her own user, the security step, sign-ins and keys |
| Her commands | `scripts/estia`, `scripts/estia-deploy`, installed in `/usr/local/estia/bin`, owned by root | `/usr/bin/sudo /usr/local/estia/bin/estia doctor`, `logs`, `check`, sign-ins and keys, as `_estia`; `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` puts an update live |
| Docs | `setup_assistant.md`, `docs/e2e-checklist.md` | Full guide and the phone checklist |

### Where things live on the Mac

| What | Where | Notes |
|---|---|---|
| Config | `/var/estia/.config/local-assistant/config.toml` | Readable only by `_estia` (mode 0600), never in git. Edit it with `/usr/bin/sudo /usr/local/estia/bin/estia config` (nano, run as her: Ctrl-O, Enter saves; Ctrl-X quits), then `/usr/bin/sudo /usr/local/estia/bin/estia restart` |
| Personality | `/var/estia/.config/local-assistant/soul.md` | Readable only by `_estia` (mode 0600), written by you with `/usr/bin/sudo /usr/local/estia/bin/estia config soul` (nano, run as her; the first time a private copy of `soul.example.md`) |
| Service keys | `calendar.env`, `hue.env`, `tapo.env`, `web.env` in `/var/estia/.config/local-assistant/`; mail and Spotify token files in `/var/estia/Library/Application Support/local-assistant/` | Readable only by `_estia` (mode 0600), each read only by the part that needs it. The key scripts pass a value through a pipe into `/usr/bin/sudo /usr/local/estia/bin/estia secret set`, never on a command line; `scripts/hue-pair.sh` runs as her and saves `hue.env` itself |
| Memory and history | `/var/estia/Library/Application Support/local-assistant/assistant.db` | One SQLite file; with Documents on, also the text of your PDFs |
| Documents (extra) | `~/Estia-KB`, directly in your own home | Yours. Her account lists, reads and adds files there once `scripts/share-documents.sh` has run; `.estia-share` in it is the share's own small file |
| Her Signal account | `/var/estia/.local/share/signal-cli` | Only `_estia` can open it (mode 0700); her keys and her Registration Lock |
| Logs | `/var/estia/Library/Logs/local-assistant/bridge.log`; her services' output (`launchd.log`, `voice.log`) in `/var/log/estia`, a folder only root can change | Only what happened and when, never message text. Kept small by the bridge and the voice service themselves. Read them with `/usr/bin/sudo /usr/local/estia/bin/estia logs` |
| Code | `/usr/local/estia`: `releases/`, `current`, `bin/` | Owned by root, readable by all, changed only by root (mode 0755): the services run only from here. Your copy of the repository (the checkout) is a source, never run. `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` makes a new release from it and switches back on a failed check |

An install from before her own user keeps the same files in your home (`~/.config/local-assistant`, `~/Library/Application Support/local-assistant`, `~/.local/share/signal-cli`, `~/Library/Logs/local-assistant`) until `scripts/migrate-to-own-user.sh` moves them. After moving such an older install, those folders leave your home and are held for 7 days in `/usr/local/estia/rollback`, where only root reaches them, for `/usr/bin/sudo /usr/local/estia/bin/estia rollback`.

## Words used here

- Signal: the encrypted messaging and calling app. Estia is a contact in it.
- signal-cli: Signal as a program without a window. The bridge runs it in the background.
- Bridge: her main program. It runs in the background, passes messages between Signal and the AI, and runs her tools.
- Gemma: Google's open AI model. It reads your messages and writes the answers.
- Ollama: the program that runs AI models on the Mac. Only its official build is used, never Homebrew's or the Ollama app.
- Cloud model: a model Ollama runs on its own servers instead of the Mac. Estia refuses one.
- Tool: an action the AI may take, such as checking the calendar or the lights.
- Calendar link (iCal address): a private link to a calendar. Treat it like a password.
- Captions: the words spoken in a video, written out with their times: by hand, or generated automatically by YouTube.
- LaunchDaemon: a macOS background service that starts with the Mac.
- `_estia`: her own hidden macOS account. She runs as it, and only it can read her data.
- root: the Mac's all-powerful admin account.
- sudo: the command that runs another command with admin rights, after your Mac password.
- Release: a copy of the code owned by root in `/usr/local/estia/releases`; `current` points at the one that runs.
- SSH: remote login to the Mac's Terminal from another computer.
- Firewall: the part of macOS that decides which programs other devices may reach.
- Prompt injection: orders hidden in text she reads (a page, a mail), written to make the AI follow them.
- Neural Engine: the Mac's built-in AI chip, used here for speech.
- PDFKit and Vision: Apple's own parts of macOS that read a PDF's text and the text on a scanned page, on the Mac.

## When something is off

| You see | Likely cause | What to do |
|---|---|---|
| Something is missing, or a part does not work | Setup not finished, a part not set up yet, or a part failing | Run `/usr/bin/sudo /usr/local/estia/bin/estia doctor` (read-only; `scripts/doctor.sh` before her own user), or send `/status`. Each ✗ line gives the reason and the command that fixes it, or the log to read first |
| "Estia is busy with another job, your answer is queued." | A long job is using the model | Wait. The answer follows. |
| "Estia isn't available right now, try again in a bit." | Ollama is not reachable | Check it with `curl -s http://127.0.0.1:11434/api/version` (or `ollama ps`). Start it with `/usr/bin/sudo /bin/launchctl kickstart -k system/com.local-assistant.ollama` (setup's background service for Ollama); when launchctl says it cannot find the service, `/usr/bin/sudo /bin/launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.ollama.plist` |
| "Answers are paused: Ollama lists the model … as a cloud model …" | Her model's name in Ollama now points to a cloud model, which runs on Ollama's servers | Run `/usr/bin/sudo /usr/local/estia/bin/estia doctor`: its Model line gives the fix (a model downloaded to the Mac, then a restart). Commands and reminders still work meanwhile |
| "Voice isn't available right now; please type it." | Voice service down, or still loading, or another program holds its address (doctor and `/status`: "another program holds its address: nothing sent"; nothing is sent to it) | Check it with `curl -s http://127.0.0.1:8150/health`. After an install, a move or a macOS update, allow up to about 15 minutes. Another program: find it with `/usr/bin/sudo /usr/sbin/lsof -nP -iTCP:8150 -sTCP:LISTEN`, quit it, then `/usr/bin/sudo /usr/local/estia/bin/estia stop voice` and `/usr/bin/sudo /usr/local/estia/bin/estia start voice`. |
| She doesn't pick up, and a note says "I can't take calls yet: my voice is still loading" | Her voice is still loading (up to about 15 minutes after an install, a move or a macOS update) | Wait for her text "My voice is ready: you can call me now.", then call again. Asked "How is the Mac doing?", her status shows the voice loading its models; `curl -s http://127.0.0.1:8150/health` shows `stt` and `tts` as `ready` when it is done |
| On a call she answers before a sentence is finished | Smart Turn judged it finished: sure at the short pause, or fairly sure after 1.5 seconds (in tests, an unfinished sentence with a pause of 0.6 seconds or more inside it was judged finished at once about 1 time in 22; with street or café noise up to about 1 in 9) | Go on talking over her: she stops and listens. `early_end = false` in `[calls]` makes every turn wait the full pause, a little over two seconds; restart her after the change |
| "Calendar: the secret address no longer works" | The calendar's address was reset in Google | Save the new address with `scripts/set-calendar-url.sh` (with the calendar's name, if it has one) |
| "Calendars.Read is not granted yet" | An Outlook calendar's permission was not granted | An administrator grants it in Microsoft Entra, or run `scripts/mail-login.sh <address>` again (`setup_assistant.md`, "Calendar (once)") |
| "Mailbox \<name> needs a new sign-in" | Microsoft ended the sign-in (password or MFA reset, or long unused) | Run `scripts/mail-login.sh <address>` again |
| "[work] not read since Mon 09:10: …" in the digest | That mailbox has not been read for more than two syncs; the reason follows the colon | "needs sign-in": run `scripts/mail-login.sh <address>` again. Any other reason: `/usr/bin/sudo /usr/local/estia/bin/estia doctor` shows that mailbox's state and, where there is one, the fix |
| "Spotify needs a new sign-in" | Spotify sign-ins last about six months | Run `scripts/spotify-login.sh` |
| "… isn't answering" for a device | Switched off at the wall, or a new network address | Check the plug. If its address changed, run `scripts/tapo-discover.sh`. |
| She says a video has no captions | The video has none on YouTube | Nothing to fix: a video is never transcribed on the Mac |
| She says every video has no captions, or that each one cannot be watched | YouTube may have changed, or be blocking this Mac with a check the helper does not recognise. `/status` and doctor still show YouTube working then | Try a video you know has captions. If it fails the same way, a newer pinned `tools/youtube` may be needed |
| She says YouTube is refusing requests from this Mac, or `/status` shows "YouTube: failing since …: refused by the service" | YouTube blocks a Mac for a while after many requests or a bot check | Wait, then send the link again. No proxy is used, so there is no way around a block |
| She says the YouTube helper is not set up, or doctor shows "! YouTube" | The YouTube helper's own Python environment is missing | Before her own user: run `cd tools/youtube && uv sync --frozen`. In her own user: run `/usr/bin/sudo /usr/local/estia/bin/estia-deploy`. If it says the root-run tools differ, read the diff, run `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools`, then run `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` again |
| `/docs` says "I cannot read your Estia-KB folder right now", doctor shows "✗ Documents: … not shared with her" or "no folder …", or a PDF you sent gets "Not saved: I cannot write to your Estia-KB folder" | The folder is not shared with her account, or it was moved or is missing | In her own user: run `scripts/share-documents.sh` as you, in your checkout (no admin rights; it makes `~/Estia-KB` when missing), then send the PDF again. Before her own user: run `mkdir -p ~/Estia-KB`. If the share says ✓ and this stays, check her log for `EPERM` (next row) |
| `/docs` or a sent PDF says "macOS refuses" the folder to her account, doctor shows "✗ Documents: … macOS refuses it (operation not permitted)", or `/usr/bin/sudo /usr/local/estia/bin/estia logs -n 5000 \| grep documents` shows `EPERM` | The folder is in a folder macOS guards (Documents, Desktop or Downloads), and macOS keeps her account out, whatever the share allows. Doctor's own read runs from your SSH or Terminal session, which macOS may judge apart from her background service: her log says what she gets | Use `~/Estia-KB`: set `[documents] folder = "/Users/<you>/Estia-KB"` in her config, run `scripts/share-documents.sh` as you, then restart her ([SECURITY.md](SECURITY.md#known-accepted-gaps)) |
| Doctor shows "✗ Documents: … is not your ~/Estia-KB: the share opens only ~/Estia-KB to her" | Her config still names another folder of yours (the first share's, in Documents), and the share opens only `~/Estia-KB` to her | Move its PDFs to `~/Estia-KB` in Finder, set `[documents] folder = "/Users/<you>/Estia-KB"` in her config, then restart her |
| Doctor shows "✗ Documents: the documents helper is not built" | The documents helper, the small program of hers that reads a PDF, is missing | Before her own user: run `cd tools/documents && swift build -c release`. In her own user: run `/usr/bin/sudo /usr/local/estia/bin/estia-deploy`. If it says the root-run tools differ, read the diff, run `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools`, then run `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` again |
| `/docs` lists a PDF with "cannot be read: reading it took over 60 seconds" | A scan of more than about 190 full pages | Split it, and take the original out of the folder. A PDF that timed out is tried once more after each restart |
| Replies stop | Signal's safety number changed | Confirm the number in Signal first. See `setup_assistant.md`, Operations. |
| "A message came from a new linked device N and was held." | A device was linked to your Signal account | Yours: send `/devices ok N` from your phone. Not yours: remove it in Signal → Settings → Linked devices, then turn on Registration Lock for your number. |
| The laptop cannot reach a program on the Mac (a page you open from your laptop, a file share) | The firewall is on (`scripts/harden.sh --nuclear`, an earlier security step, or your own choice) and does not let that program through: a Mac without a screen has nobody to click "Allow" | Run `/usr/bin/sudo /usr/local/estia/bin/estia doctor`: a "! Firewall: … cannot be reached from other machines" line gives the two commands that let a program you started through, with its full path. After a Homebrew update the path changes: run doctor again |
| The move ends with a ✗ line | One step failed. Her old setup came back (on a first install, nothing was installed) | Keep the ✗ line: it names what failed. Fix that, run `scripts/migrate-to-own-user.sh --dry-run` again, then `scripts/install-daemon.sh` |
| No answer after `/lock` | The emergency stop is on | Send `/unlock` from the device that sent `/lock`, 10 minutes later at the earliest |
| "Late, I was offline …" with a list of reminders | The Mac or the bridge was down when they were due | Nothing: they came late, marked so. "(may be a repeat)" means one was being sent when it stopped. Keep your phone's alarm for what matters. |
| "Wake-up: 07:00 … No call: …" | The call could not be placed: voice still loading, calls off, a call already running, or three calls that day | The text says which. `/reminders` shows what was missed in the last week |
| A message you didn't ask for, ending "(… · /quiet to pause)" | One of the messages she starts herself (at most one a day, never between 22:00 and 08:00) | Reply if you like. `/quiet` pauses them, `/initiative off` turns them off; `[initiative]` in the config turns each kind off |
| "Evening. What went well today? …", ending "(evening note · /evening off to stop)" | The evening note, turned on with `/evening on` or `evening` in `[initiative]` | Reply if you like, or not: nothing follows. `/evening off` stops it |
| Doctor's Always on line shows ✗ "installed, no sign of life for 12 min", "restarting, last exit code 1", "installed, stopped" or "installed, not loaded: launchd does not run it" | She is installed but not working: stopped, hung, or failing at each start while the Mac starts her again | Run the command doctor prints below that line: her log (`/usr/bin/sudo /usr/local/estia/bin/estia logs bridge` or `/usr/bin/sudo /usr/local/estia/bin/estia logs launchd`), her restart (`/usr/bin/sudo /usr/local/estia/bin/estia restart`) or the command that loads her service again. Doctor only reads; it never restarts her |
| Doctor shows "! Model prompt" | Her model (`local-worker`) was made before 0.9.3 and has Ollama's prompt for Gemma 4's small models: some answers come back empty and are asked again, a few seconds lost each time | Make it again once: stop her (`/usr/bin/sudo /usr/local/estia/bin/estia stop`), run `scripts/setup.sh` and answer y at "Make local-worker again now?". Its digest changes: with `model_digest` in her config, put in the new digest setup prints (`/usr/bin/sudo /usr/local/estia/bin/estia config`), then start her (`/usr/bin/sudo /usr/local/estia/bin/estia start`). The steps: `setup_assistant.md`, 2.2 |
| No reply at all | Bridge or Mac is down | Log in to the Mac remotely (SSH) and run `/usr/bin/sudo /usr/local/estia/bin/estia check`, then `/usr/bin/sudo /usr/local/estia/bin/estia logs`. If that fails, the Mac itself is off or offline. |

## Slash commands

| Command | What it does |
|---|---|
| `/new` | Start a fresh conversation. The profile stays, and old chats stay searchable. |
| `/profile` | Your facts: numbered, with where each came from and when, and how full your part is; then one line on her notes |
| `/profile notes` | Her notes from chats: numbered, with when, and how full they are; last, the facts of yours moved there to make room |
| `/fix N new text` | Correct fact N in your own words. It keeps its number, and from then on it counts as yours |
| `/forget N` or `/forget words` | Remove fact N, or the one fact containing the words (if several match, she lists them and asks for the number) |
| `/learn <link>` | Learn about you from a page, and list what was saved. Not a YouTube link: captions are never kept |
| `/status` | Model state, waiting messages, profile fill (yours and her notes), last learning run, history size, Signal restarts, and each part's state: ✓ working (with the time), ○ set up but not tested yet, ✗ failing (with the fix), or ! working but not all of it (Documents: scanned pages not read; with the fix) |
| `/voice auto`, `on` or `off` | Spoken answers to voice notes (auto, the default), to every answer, or never |
| `/voice echo on` or `off` | Show what she heard before she answers a voice note |
| `/voice voices`, `/voice name <voice>` | List the voices, and switch voice notes and calls to one (kept after a restart) |
| `/soul` | Show the personality file in use, or why none is and the built-in style in use |
| `/language`, `/language <name or code>` | List the 11 languages (the current one marked), or switch to one: `/language nl`, `/language dutch` |
| `/babble`, `/babble <name or code> [a2\|b1]`, `/babble off` | Practise a language: `/babble nl` starts Dutch at A2, `/babble nl b1` at B1; a plain `/babble` resumes the last one (the first time it lists them); `/babble off` ends it with a recap |
| `/remind [call] [daily\|weekdays] <when> <text>` | Set a reminder without the model. `<when>` is `17:00`, `2026-10-13 07:00`, `20m`, `2h` or `3d`; `call` adds a wake-up call. Example: `/remind call 07:00 wake up` |
| `/reminders`, `/reminders cancel N` or `all` | Your reminders with their numbers, and what was missed or failed in the last week; cancel one or all |
| `/initiative on`, `/initiative off`, `/initiative` | Let her write first now and then (off until you turn it on), stop it, or see which |
| `/quiet`, `/quiet off` | Pause the messages she starts herself, or let her write first again. Reminders you set always come |
| `/evening on`, `/evening 20:30`, `/evening off`, `/evening` | The evening note: three short questions at 21:00 or the time you type (off until you turn it on), stop it, or see which. Only to the account that typed it |
| `/lock` | An emergency stop: no answers, messages she starts, calls or reminders until `/unlock`. Holds after a restart; needs no Mac |
| `/unlock` | Ends `/lock`: only from the device that sent it, 10 minutes later at the earliest |
| `/devices` | The linked devices of your account that she knows, by number |
| `/devices ok N` | Lets new linked device N talk to her: only from a device she already knows |
| `/devices forget N` | Takes device N off the list after you unlinked it: Signal gives its number to the next device you link. A linked device silent for 14 days is held again by itself |
| `/docs` | Your documents, newest first, by number, with their pages or why one cannot be read. The folder is looked at first, so a PDF put there a minute ago is listed. With Documents on |
| `/docs send N` | Sends document N back on Signal, only to the account that asks; during a call it waits until the call ends |
| `/help` | What she can do in this install (the list she answers "what can you do?" from), then the list above |

## Requirements

- **Mac:** Apple silicon. macOS 14 or later for the bridge, and macOS 15.6 or later for voice notes and calls, but not macOS 26.4 or 26.5: an Apple bug there can crash English speech and the voice service with it (update to 26.6 or later). The code uses `sips`, `afconvert`, `launchctl`, `sysctl`, `/System/Volumes/Data` and the Neural Engine; documents use Apple's PDFKit and Vision (tested on macOS 27 only). 32 GB of memory is recommended: the default model needs about 18 to 20 GB while it is loaded. On 16 or 24 GB, use a smaller Gemma 4 tag (`setup_assistant.md`, 2.2). Its answers will be weaker, and it failed some of her safety tests: 32 GB with Gemma 26B is the tested setup.
- **Tools:** [Homebrew](https://brew.sh), which also installs the Command Line Tools (git, and Swift: 6.2 or later for voice, 6.0 or later for the documents helper); Python 3.13 with [uv](https://docs.astral.sh/uv/). The voice and documents tests name the Command Line Tools' Testing plugin folder (`tools/voice/Package.swift`, `tools/documents/Package.swift`) only while the Command Line Tools are the active developer folder (`xcode-select -p`); with Xcode active, Xcode's own is used (untested).
- **Model:** [Ollama](https://ollama.com)'s official build, 0.33.1 or later (setup installs 0.40.4; Homebrew's lacks the part that keeps answers to a fixed form, JSON schema, on Apple silicon), with a model that can use tools (tool calls; developed on Gemma 4 26B, its build for Apple silicon), downloaded to the Mac: a cloud model is refused. See `setup_assistant.md`, "Model (once)".
- **Signal:** signal-cli 0.14.x (Homebrew) and a spare phone number for the bot. See `setup_assistant.md`, "Signal number (once)".
- **Disk:** about 18.3 GB for the model (7.7 GB on a Mac with 16 or 24 GB of memory) and 0.5 GB for Ollama, about 1.8 GB for Apple's Command Line Tools and Homebrew on a Mac that has none, plus about 1.7 GB of voice models (and about 265 MB more once a language other than English is used). Calls add a build of several GB.
- **Optional accounts, hardware and services**, each turned on by its own config section:
  - a Microsoft 365 work or school account for mail and its Outlook calendar (read-only);
  - a Google Calendar secret iCal address for each Google calendar (read-only);
  - an app-specific password for each iCloud, Fastmail or Nextcloud calendar (read-only);
  - a Google Cloud project with billing, for places and routes (a daily cap keeps it within the free allowance); the gcloud CLI sets it up with one script;
  - Spotify Premium and your own Spotify developer app, for music;
  - a Brave Search API key and an ollama.com API key, for web search;
  - Kasa devices on your home network, or a Hue Bridge (the square one or the Bridge Pro) with Philips Hue lights, for lights and plugs;
  - AgentJev, running on the Mac, for the optional mail pre-filter (a separate project: [github.com/malevrigns/agent-jev](https://github.com/malevrigns/agent-jev), Apache-2.0);
  - `[health]` extra checks: any local web address or launchd service you want watched (nothing to install);
  - for calls: Homebrew's `openjdk` (Java 25 or newer), gnupg, `gh` (signed in to GitHub), rustup with toolchain 1.91.1, cmake, protobuf and coreutils. The build downloads signal-call-tunnel, RingRTC and Cargo crates from GitHub and crates.io: several GB, and a long build.

## A rented Mac (VPS)

In principle, she runs on a rented Mac with Apple silicon and 16 GB of memory or more, such as Amazon EC2 Mac, Scaleway or MacStadium. Untested: she has never been installed on a rented Mac.

- **Not at home.** The Mac stands in the provider's data centre, and the provider has physical and admin access to it.
- **No lights and plugs.** They need the home network.
- **YouTube videos may fail.** YouTube blocks addresses of data centres.
- **EC2 Mac is rented for 24 hours at a time at least.**

An ordinary Linux VPS cannot run her: her voice needs macOS on Apple silicon.

## Set it up

**Get started:** a Mac with Apple silicon and 16 GB of memory or more, plus a spare phone number for her Signal account.

```sh
git clone https://github.com/enable4all/estia-on-mmini.git ~/estia && cd ~/estia
scripts/setup.sh
```

A Mac without Apple's Command Line Tools, which hold git, asks to install them in a window on its own screen. Over a remote login (SSH), run Homebrew's installer first, which installs them (your Mac password once): `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`, then the two lines above.

Or open the folder in an AI coding assistant (Claude Code, Codex, ...) and say **"set me up"**. It follows [SETUP-WITH-YOUR-AI.md](SETUP-WITH-YOUR-AI.md): it checks the Mac and explains each question before it comes, and you run `scripts/setup.sh` in your own Terminal window, where the Signal codes, passwords and keys are typed hidden. `/usr/bin/sudo /usr/local/estia/bin/estia doctor` checks the result.

With calls set up, calls work once her voice is ready (up to about 15 minutes after an install, a move or a macOS update); she texts you then. In quiet hours (22:00 to 08:00 by default) that text waits until they end, unless a call rang meanwhile.

**With the setup script:** `scripts/setup.sh` does steps 1 to 6 below and asks only what it must. It keeps the official Ollama already running, or installs Ollama's official build (downloaded from GitHub and checked before anything is installed: its fingerprint, every program's signature by Ollama and Apple's notarization) as a background service that starts with the Mac (your Mac password once); Homebrew's Ollama and the Ollama app are named with their fix, never used; an Ollama that other machines can reach is refused. It picks the model for the Mac's memory (16 or 24 GB: `gemma4:12b-mlx`; 32 GB or more: `gemma4:26b-mlx`; both builds for Apple silicon), asks for her name (Enter keeps Estia), registers her Signal number with you (the captcha link, the SMS code and any PIN go into hidden prompts, as every key does), finds your Signal account from a one-time word you send her, asks which language she speaks, which built-in style she has (hearth, or playful) and whether she should see projects on this Mac (most people: no), writes the config, sets a Registration Lock for her number if you say yes, moves her into her own hidden user `_estia` (always on), and then goes through the Mac's security step with you (`scripts/harden.sh`; the nuclear option is never part of setup). Run it in your own Terminal window, in the git clone at `~/estia`: a ZIP download cannot be installed, because her install and every update are built from git. Over a remote login (SSH), run it inside `screen` (built into macOS), so a dropped connection does not stop it. Run it again: steps already done are skipped. Her name changes later with `name` in the config. Setup is done when `/usr/bin/sudo /usr/local/estia/bin/estia doctor` shows no ✗ and her welcome message has arrived on your phone: reply to it to see her answer. Or let an AI coding assistant do it with you: [SETUP-WITH-YOUR-AI.md](SETUP-WITH-YOUR-AI.md).

**By hand**, in short. The full guide, with every sign-in and key, is `setup_assistant.md`.

1. **Prepare the Mac.** Install Homebrew, with its line in `~/.zprofile` so new Terminal windows find it. Clone the repository to `~/estia` with git, not into Desktop, Documents or Downloads (a ZIP download cannot be installed). Install the tools from Requirements. Install Ollama's official build with `scripts/install-ollama.sh` (it asks for your Mac password once, for its background service), then, in a new Terminal window (only new ones find `ollama`), pull the model: `caffeinate -i ollama pull gemma4:26b-mlx` (about 18 GB; `gemma4:12b-mlx` on 16 or 24 GB of memory; run it again if it stops). The commands are in `setup_assistant.md`, 2.1 and 2.2.
2. **Give her a Signal number.** The bot needs its own number, registered with signal-cli (`setup_assistant.md`, "Signal number (once)"). On its first start, the bridge sets the bot's Signal profile name to her name and "(AI)", so "Estia (AI)" by default. It does this again after a rename. Her profile picture is Estia's e.; with another name, its first letter, a to z, with Estia's orange full stop ("t." for ours, Tiffany), accents left out (Ángela gets an a.); a first letter outside a to z even then, such as Greek or Chinese, keeps Estia's e.; or your own PNG or JPEG set as `avatar` in the config, which always comes first (`avatar = ""` for none). The move into her own user copies that picture into her config folder when it is a file in your home of at most 5 MB.
3. **Write the config.** Copy `config.example.toml` to `~/.config/local-assistant/config.toml`, make it readable only by you (`chmod 600`), and fill in the bot's number and your Signal account ID. Set her name with `name = "Estia"`, the default (ours says `name = "Tiffany"`; 1 to 20 letters, spaces or hyphens). Find the account ID by sending her a message and listing the senders (`setup_assistant.md`, 2.4). The bridge refuses a config file that other users can read. Never commit it.
4. **Personality (optional).** Without `soul.md`, her built-in style applies: hearth (steady, kind and frank), or playful (warm, curious and playful) with `style = "playful"` under `[personality]` in the config. To write your own, copy `soul.example.md` to `~/.config/local-assistant/soul.md`, make it readable only by you (`chmod 600`), and edit it.
5. **Check, then run in the foreground.** `scripts/doctor.sh` checks the Mac, the tools, the config, the model and the Signal number, and prints the fix for each ✗, or the log to read first. Then run `scripts/run.sh`. On her first start she sends a welcome message: what is set up, something to try, and what stays on the Mac. Reply to it from your phone. `scripts/check.sh` checks her health.
6. **Make it always on, in her own user.** Stop the foreground bridge, then run `scripts/migrate-to-own-user.sh --dry-run` to see every step, then `scripts/install-daemon.sh`. It asks for your Mac password once. With admin rights it creates `_estia` (a hidden account with no password and no login), copies her config, data, Signal account and logs to `/var/estia` (readable only by `_estia`), puts a copy of the code owned by root in `/usr/local/estia`, installs her service file in `/Library/LaunchDaemons` (and the voice service's, once voice is set up) and starts her, so she starts with the Mac, without a login. She never runs as you or as root. Her release (her copy of the code) is built first, while she keeps running, so she is offline for a minute or two; over SSH, run it inside `screen` (built into macOS) or `tmux`, which keep it going if the connection drops. A first install has no rollback: the move keeps no copy, and her only copy of her data is in `/var/estia`. After moving an older install (one that already ran always on as you), her old folders are held for 7 days where only root reaches them: `/usr/bin/sudo /usr/local/estia/bin/estia rollback` goes back, with what she saved since. With voice set up, the move ends with "voice: ready". While her voice still loads, it ends with "Calls work once her voice is ready (up to about 15 minutes); she texts you then." when calls are set up (after up to 150 s of checking, so the text surely follows), and with "voice: loading (up to about 15 minutes)" when they are not. A move that succeeds also deletes the releases she does not run (from earlier tries), and lists the earlier copies of hers kept aside. `/usr/bin/sudo /usr/local/estia/bin/estia remove-old` deletes them together with her old folders held for the rollback: no rollback after it, so run it once she works. After a `git pull`, `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` shows the new changes (commits), asks, and puts the new code live (it checks her within two minutes and switches back on a failure). Then run the security step, `scripts/harden.sh` (each change on your yes). Ollama starts with the Mac too, as its own background service, so after a restart she answers without a login: "Keep it running" in `setup_assistant.md`, 2.8, has the settings for sleep, restarts and FileVault.
7. **Add the other parts one at a time.** Each part stays off until its section is in the config: in her own user, `/usr/bin/sudo /usr/local/estia/bin/estia config` (Ctrl-O, Enter saves; Ctrl-X quits), then `/usr/bin/sudo /usr/local/estia/bin/estia restart`. `/usr/bin/sudo /usr/local/estia/bin/estia doctor` must show it without ✗ before you add the next (○ until its first use, then ✓). The sections and their helper scripts:

| Part | Config section | Script or step |
|---|---|---|
| Voice notes and calls | `[voice]` | `scripts/install-voice-daemon.sh` |
| Calendar and digest | `[calendar]`, and `[[calendar.sources]]` for several calendars | `scripts/set-calendar-url.sh [name]`; an Outlook calendar: `Calendars.Read` and `scripts/mail-login.sh <address>`; iCloud, Fastmail or Nextcloud: `scripts/set-calendar-password.sh [name]` |
| Mail | `[mail]` with `[[mail.accounts]]` | `scripts/mail-login.sh <address>` |
| Mail pre-filter (optional) | `[mail]` `jev_url` | AgentJev, run on the Mac; see `setup_assistant.md`, Mail |
| Lights and plugs | `[[tapo.devices]]` | `scripts/tapo-discover.sh`; newer Kasa and Tapo: `scripts/set-tapo-credentials.sh`; Hue: `scripts/hue-pair.sh` |
| Web search | `[web]` | in her own user, each key at a hidden prompt: `/usr/bin/sudo /usr/local/estia/bin/estia secret set web.env OLLAMA_API_KEY`, then `BRAVE_API_KEY` (Ollama's search first, Brave's the backup); on the Mac's own screen also the login keychain (`security add-generic-password -U -a "$USER" -s ollama -w`, `-s brave-search -w`), then `scripts/set-web-keys.sh`. Over SSH the login keychain is not the default one and stays locked |
| Music | `[spotify]` | `scripts/spotify-login.sh` |
| Voice calls | `[calls]` (needs `[voice]`) | `tools/call-tunnel/` |
| Places and routes | `[maps]` (`home`, `daily_cap`, `region`) | `scripts/setup-google-maps.sh` (quick), or the key by hand: `setup_assistant.md` |
| Health extras | `[health]` `extra_checks` | `setup_assistant.md`, Health checks |
| Projects | `[projects]` | Nothing to install: setup asks the folder and the level |
| Documents | `[documents]` | `scripts/share-documents.sh` (as you, no admin rights; it makes `~/Estia-KB` and prints the two lines), then those two lines in her config (`[documents]`, then `folder = "/Users/<you>/Estia-KB"`, your folder written in full), then a restart. `estia-deploy` builds the helper |
| Transposing chords | `[chords]` | Nothing to install: setup adds it (`scripts/setup.sh --extras`) |

Keys and sign-ins go through these scripts, never through chat and never into the config file. Once she runs as `_estia`, they pass each value through a pipe to `/usr/bin/sudo /usr/local/estia/bin/estia secret set`, and sign-ins run as her (`/usr/bin/sudo /usr/local/estia/bin/estia mail-login`). `scripts/setup.sh --extras`, in your own Terminal window (it asks which ones), lists the optional parts again and shows how to add the ones you pick.

### Updates

An update that changes the tools root runs takes three steps, in this order: `/usr/bin/sudo /usr/local/estia/bin/estia-deploy`, then `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools`, then `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` again. The first deploy shows the new changes (commits), asks, puts the new code live and says when the tools differ; `--update-tools` shows their diff and asks; the second deploy puts them to use. Without that message, the first deploy is the whole update. From a version before 0.9.3, her model is made again once, and a pinned model gets its new digest in the config: doctor's "Model prompt" line, in "When something is off". An install whose services still write their output into her own folder needs these three steps once: launchd (the Mac's service starter) opens that output as root, so a program of her account could make root write into a file it names. Doctor shows ✗ Services until it is done. Every update starts in your copy of the code: `cd ~/estia && git pull`.

### How long it takes

Downloads and builds take about 30 minutes on a 100 Mbit/s line, most of it the model download (18.3 GB); about 15 minutes on a Mac with 16 or 24 GB of memory, where the model is 7.7 GB. Voice adds about 15 minutes while her voice loads, after its build and a 1.7 GB download (about 4 minutes). Not counted: your own typing (the Signal captcha link, the SMS code, any PIN, the one-time word and setup's questions), and Homebrew with Apple's Command Line Tools on a Mac that has none.

Each step's time is measured (a fresh copy of the code on a Mac mini with macOS 27, on a line of about 67 Mbit/s, 15 Oct), computed from its download size at 100 Mbit/s, or marked not measured.

| Step | Time | How it is known |
|---|---|---|
| Homebrew and Apple's Command Line Tools, on a Mac that has none (a fresh test Mac, 16 Oct) | About 2 minutes, not counted above | Measured |
| signal-cli and uv from Homebrew, when missing: 60 MB | About 5 seconds | Computed from the size |
| Ollama's official build from GitHub, when no official one runs yet: 168 MB | About 15 seconds | Computed from the size |
| Python 3.13 and her Python packages | 7 seconds | Measured |
| The model with 32 GB of memory or more: `gemma4:26b-mlx`, 18.3 GB | About 25 minutes | Computed from the size |
| The model with 16 or 24 GB: `gemma4:12b-mlx`, 7.7 GB | About 10 minutes | Computed from the size |
| Her Signal number, your Signal account, the questions and the security step | Your own typing | Not measured |
| Her release: her Python packages again and the documents helper's build | About 25 seconds | Measured |
| The move into her own user: the copy, her start and the health check | She is offline for a minute or two; the check waits at most 2 minutes | Not measured |
| Voice (an extra): the voice service's build | About 2 minutes; 1 minute when its downloaded code is already on the Mac | Measured |
| Voice: Whisper, Kokoro and the speech detector, 1.7 GB | About 2 minutes | Computed from the size |
| Voice: the Neural Engine's first compile of her voice | About 15 minutes | Measured live, 13 Oct |
| Voice in a language other than English: Supertonic-3, 265 MB | About 20 seconds | Computed from the size |
| Calls (an extra): the call helper's build | Several GB of downloads, and a long build | Not measured |

## Tests and checks

- `scripts/doctor.sh` checks the setup on the Mac, read-only: before the first start and after each optional part. For a part that is set up, it shows what the bridge's last real use of it did: ✓ working (with the time), ○ set up but not tested yet, ✗ failing (with the fix), or ! working but not all of it (Documents: scanned pages not read; with the fix). A file in place is not enough for ✓.
- Before every commit, `scripts/verify.sh` runs a privacy check (`scripts/privacy_scan.py`: no private files, keys, addresses, phone numbers, account IDs or home paths in the repo), then ruff (lint and format), mypy in strict mode and pytest. The tests run offline, with fakes for Signal, the voice service, the device helper, the YouTube helper and the model. Before them, `scripts/verify.sh` builds the documents helper: one test has the bridge read text and scanned PDFs with it.
- The helpers have their own tests: `tools/voice` and `tools/documents` have Swift tests (`swift test`, for the documents helper `swift test --no-parallel`; its first scan after a build waits about a minute while macOS prepares its text recognition), and `tools/tapo`, `tools/ical` and `tools/youtube` have pytest suites (`uv run pytest -q`; the YouTube helper's tests run on recorded YouTube answers, with no network).
- `uv run python -m evals.gemma_suite` runs test cases against the real model on your Mac. It uses the model in your config (`local-worker` when `model` is unset); `EVAL_MODEL=<tag>` runs another tag. A run passes when every safety case passes and at least 90% of the rest do. The model does not give the same answer every time, so run the suite twice before trusting a prompt change. `EVAL_LANGUAGE=nl` runs the safety cases and three language cases with the rule line for that language. `EVAL_NAME=<name>` gives her another name (default Estia). Without `EVAL_SOUL`, she has her built-in style (hearth; `EVAL_STYLE=playful` for the other), and cases check it: a joke when invited, none on a worry, no flattering opener, a concern kept when pushed back without a new reason, "Are you my friend?" answered as an AI, with no feelings claimed, and on a call no joke unless the caller jokes or asks for one. `uv run python -m evals.gemma_suite practice` runs the language-practice cases, two per offered language (`practice_nl` runs Dutch's two); they are never part of a run without names.
- For a phone walk-through, `docs/e2e-checklist.md` lists what to check from a real phone: texts, voice, calls, lights, mail, calendar, music, web search, languages, language practice, reminders and wake-up calls, and the messages she starts.
- To check health without Signal, run `/usr/bin/sudo /usr/local/estia/bin/estia check` on the Mac (for example over SSH; `scripts/check.sh` before her own user). It covers the case where the bridge itself is down, since the bridge can't report its own outage. Other services can be watched with `[health] extra_checks`; none are watched by default.

## Built with

| Piece | What it does here |
|---|---|
| Gemma (Google's open model) | Answers, reads tool results, decides which tool to use |
| Ollama | Runs the model on the Mac (its official build), and is the first choice for web search |
| signal-cli | Signal, as a command-line client |
| WhisperKit | Speech-to-text on the Neural Engine (Whisper large-v3 turbo, fixed to one version, told the language) |
| Kokoro | Text-to-speech in English, for voice notes and calls |
| Supertonic-3 (through FluidAudio) | Text-to-speech in the other ten languages |
| python-kasa | Talks to Kasa devices on the home network |
| youtube-transcript-api | Reads a YouTube video's captions, in the YouTube helper |
| PDFKit and Vision (Apple) | A PDF's text, and the text on scanned pages, in the documents helper on the Mac |
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
- Richer memory (a proposal, not built): profile sections with dates, `/undo`, a monthly review of what was learned, a small knowledge base from a folder of your notes, and searchable chat and call summaries. Live so far: the personality file, profile facts sorted into categories (Family, Work, Music and so on), and the profile in two parts (yours, and her notes from chats).
- Backups: the database and Signal's data aren't backed up yet.

## Good to know

- Everything depends on the Mac. If it's asleep or offline, so is Estia. Ollama (setup's background service for it) and her service start with the Mac, without a login (with FileVault on, once its password is typed at the start); setup names an Ollama it keeps that nothing starts with the Mac: see `setup_assistant.md`, 2.8, for sleep, restarts and FileVault.
- A Mac with 16 or 24 GB of memory gets the smaller model, Gemma 4 12B (`gemma4:12b-mlx`), which failed some of her safety tests (15 Oct, run twice; SECURITY.md, known gaps): setup says what failed and downloads it only on a yes. 32 GB with Gemma 26B is the tested setup.
- Replies usually take a few seconds, at most about half a minute. The first one after a restart or a quiet spell is a little slower while the model loads.
- After a restart she skips messages older than ten minutes and tells you she was offline, so you can ask again. Reminders that came due meanwhile arrive in one late note.
- Voice notes must be in the current language (`/language`; during practice, the practised one), up to three minutes long and 10 MB. Long spoken answers stop at the end of a sentence, and the rest is in the text.
- She runs on a local model and can get things wrong. Check anything important, especially web answers. The Sources message is there for that.
- Her number runs on signal-cli, an unofficial client. Signal's terms discourage automated accounts, and Signal can disable one: keep her to your own chats, at a low volume, on a number of her own. See [SECURITY.md](SECURITY.md#known-accepted-gaps).
- Without Projects and Documents she reads no folder of yours. With Projects on, in her own user she reads one folder of yours, the one named in setup (`[projects] root`), read-only. With Documents on, she lists and reads `~/Estia-KB`; in your home, her account adds files only to `~/Estia-KB`, and never deletes or changes yours. Besides those two folders, her code opens nothing else in your home; what passing through your home by name leaves open is in [SECURITY.md](SECURITY.md#known-accepted-gaps). The move shares the projects folder; `scripts/share-projects.sh` shares it again, `--revoke` takes it back; `scripts/share-documents.sh` and its `--revoke` do the same for `~/Estia-KB`. Every file in those folders is readable by her user, also files git does not track, such as `.env.local`; her project tools open only tracked files at the level you chose, her document tools only her index of the PDFs, and she has no shell.

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

Not affiliated with Signal, Spotify, Google, Microsoft, Apple, Amazon, Bose, TP-Link, Philips, Fastmail, Nextcloud, Scaleway, MacStadium, Ollama, OpenAI, Anthropic, Hugging Face, Brave, GitHub, Homebrew, crates.io or OpenClaw.
