# Changelog

Every release of Estia, newest first. The form is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). From 0.9.4 on, a release's page on GitHub holds its
section here, word for word, under the tagline and above what is in 0.9 (`.github/release-page.md`). The pages of 0.9.1
to 0.9.3 were written on GitHub and say more (security, requirements, install, tests); 0.9.0 has no release page.

## [Unreleased]

## [0.9.4] - 2026-10-19

### Added

- **Transposing chords** (an extra, off until set up: `[chords]` in the config): chords transposed or moved for a
  capo, a chord's notes and the likely key of given chords, worked out on the Mac and given in a note from the code,
  not from the AI. On texts and calls. Names as written (A to G, do re mi, German and Dutch names), written back the
  way they came. A song's chords from a web search are worked out the same way, with their Sources message; after a
  call, one message from the code holds the exact chords and the page they came from. A chord that cannot be read is
  named and never guessed. A sentence of hers with a chord or a capo fret that neither the code worked out nor a
  source holds is left out.
  Known gaps, in GUIDE.md: a capo question that names two keys (the recording's and the singer's) may get the
  recording's key as the key to sound in; a fret in words ("the third fret") is not checked.
- **Standby on calls:** after 90 seconds of silence, a check-in that names her ("Take your time, I'm here. Say Estia
  when you need me."), then only a turn with her name is answered. Other talk, a TV or kitchen noise is heard on the
  Mac and dropped: never answered, never kept. Her name ends standby. In standby, "I'm still here." every ten
  minutes. Known limits: a word that sounds like her name ends standby too; in Japanese, Korean and Russian the
  check-in asks to call her by name; not yet tested on a real call.
- **`/profile notes`:** her notes from chats, by heading, with short dates ("16 Oct"; the year only when it is not
  this year); last, the facts of yours moved there to make room.

### Changed

- **Her goodbye ends the call:** her own goodbye ("Bye!", "Talk soon!"), closing wish ("Enjoy your evening!") or offer
  to wait ("I'll be here when you're ready.") ends the call once the caller has been quiet for 3 seconds, whatever the
  caller said before; anything said in those 3 seconds keeps the call open and is answered. Not on a practice call.
  Known limit: her closing wishes in the languages other than English and Dutch are untested by a native speaker.
- **The default style, hearth, is calmer and shorter:** calm and plain, no gushing. The answer first, in one to four
  sentences unless more is asked for; no cheerleading and no recap of what was just said; concrete suggestions over
  hedging. In a brainstorm, she builds on the idea, adds one new angle, then asks one question. A goodbye gets one
  short line, no summary, also on a call. An install with its own soul.md is unchanged.
- **`/profile`:** your facts, with how full your part is in characters and percent, then one line on her notes: how
  many, how full, the 1,500 characters a message carries, and `/profile notes`. `/status` gives the same percent.
- **Silence never ends a call:** the goodbye after ten minutes of silence is gone. A call ends at its length limit (an
  hour by default) or when either side hangs up. `idle_hangup_seconds` is no longer read; a config that sets it keeps
  working.

### Fixed

- **A call ends when the caller asks to end it or leaves:** a request to end the call ("let's end the call", "we can
  stop here", "let's call it a day") or leaving with a reason ("I have something to do", "something came up") ends it
  once her answer is spoken, in each of the 11 languages. Before, such a call stayed open. "Wait" or "one more thing"
  keeps it open, in every call language.
- **Chord charts read aloud right,** on calls and in voice notes: chords with ";", " / ", "…", "‖" or "⏸" between
  them, "C... G..." and chords with only spaces between them are said as a list, in each of the 11 languages; "N.C."
  is "no chord", "x2" "twice" and "%" "repeat" among chords; C-7, Cm(maj7), C7(b9), Cm7(b5) and C(no3) are said in
  full. In tests, 126 of 127 chord readings right, against 106 before. Known limits, in GUIDE.md.
- **Stage directions never spoken:** "(pause)", "(laughs)" or "*smiles*" in her answers is never said on a call or in
  a voice note, and never sent in a text. Bold words, code and a title in brackets ("(Applause) by Lady Gaga") stay.
  Known limit: a capitalised one inside a sentence ("Also (Pause) weiter") is still said.
- **Her notes that come with a message** are matched by shared words, small English words such as "is" or "to" left
  out. Before, such a word alone could bring a note along.
- **A text typed during a practice call is a practice turn,** also when the call ends the moment the text arrives:
  it is answered as practice, and the recap reads it. Before, now and then the recap came first, and the text was
  answered after practice as an ordinary message.
- **The test suite no longer depends on the Mac it runs on** for the shell's locale, where uv was installed, an
  installed Ollama app or Tailscale, and the git version. Before, a few tests failed on such a Mac.

## [0.9.3] - 2026-10-18

### Added

- **A turn that sounds finished ends sooner:** after a short pause (0.6 seconds), Smart Turn, a small model on the
  Mac, hears the last 8 seconds and judges only whether the sentence sounds finished. Sure, the turn ends there;
  fairly sure, after 1.5 seconds; unsure, after the full pause, so there is still time to think. A turn that ends like
  it goes on ("and…", "but", a comma) or with a goodbye always waits the full pause. In tests on 2,640 clips in the 11
  languages, a finished sentence ended about 1.6 seconds sooner. `early_end = false` in `[calls]` turns it off.
- **The other ten languages read aloud as people say them:** times, dates, money, numbers, units and chord names in
  each language's own words: Spanish, Italian, Portuguese and French with do-re-mi chord names ("Fa#m y Sib" as "fa
  sostenido menor y si bemol"), Dutch and German with German letter names ("Hm und C#m" as "H-Moll und Cis-Moll"),
  Polish and Russian in the case the sentence asks for, Japanese and Korean with times, big numbers, phone numbers,
  temperatures and chords in words. Every reading: GUIDE.md, "Read aloud in the other ten languages".
- **Doctor and `/status`:** a "Turn detector" line beside the speech detector's, and a "Model prompt" line.

### Changed

- **Noise in a pause no longer keeps a turn open:** typing, dishes or traffic that goes on count as a pause, judged
  by the speech detector (Silero) on the Mac. In tests a turn ended 1.9 to 4.7 seconds sooner in such noise, and no
  speech was taken for noise, whispers included. A short noise right after the last word and other voices still keep
  it open. `pause = "quiet"` in `[calls]` brings back the old rule.
- **The first answer on a call comes sooner:** while a call rings, the model loads and reads her instructions and the
  conversation so far. On a test call with the model unloaded, the first answer came 11 seconds after the question,
  against 32 seconds before.
- **The full pause is 2.3 seconds** (was 2.8): still time to think. A config that sets `end_of_turn_seconds` under
  `[calls]` keeps its value.
- **Proposals opened by the project:** a feature proposal is opened, and now also updated, as an issue by GitHub
  Actions, so it names the project rather than a person's account.

### Fixed

- **Empty answers:** `local-worker` gets Google's own prompt for Gemma 4 12B and larger (`RENDERER gemma4-large` in
  its Modelfile). Before, Ollama gave it the small models' prompt, and now and then a hidden thought took the whole
  answer: a silence of about ten seconds before a second try. Every request also sends Google's `top_p` of 0.95. The
  second try's answer is checked too, and on a call she says a short acknowledgement while it runs. Each empty answer
  is logged, without words. A `local-worker` made before 0.9.3 keeps the small prompt until it is made again: doctor
  shows "! Model prompt" with the steps (`setup_assistant.md`, 2.2).
- **Single chord letters said as chords:** "E", "D" alone in a run of chords are "E major", "D major", in English.

## [0.9.2] - 2026-10-16

### Added

- **English read aloud as people say it:** on calls and in spoken answers, numbers, money, dates, units, phone
  numbers, mail addresses and chord names are said in words: "€12.99" is "twelve euros and ninety nine cents", "Fri
  16 Oct" "Friday the sixteenth of October", "C G7 Am F" "C, G seven, A minor, F". Before, "2nd" came out "twond"
  and "E7" "Eseven". Written answers show them as they are; the other ten languages are spoken as written. Every
  reading: GUIDE.md, "Read aloud in English".

### Changed

- **Ten short acknowledgements on calls, never on two requests in a row:** once a request of five words or more is
  taken in, one of ten lines such as "Got it.", "On it." or "Sure thing.", none with "okay". A request right after an
  acknowledged one gets none, and a line is never one of the last two said. Before, one of four lines came on every
  such request. The other languages keep their own lines.
- **"Take your time, I'm here." once, then at most every ten minutes:** the first 90 seconds of silence get the line,
  as before; a later silence within ten minutes of it gets none.

### Fixed

- **English hours with am/pm said as times of the day:** "7pm" is "seven in the evening", "12 PM" "noon", "3-5pm"
  "three to five in the afternoon". Before, "7pm" came out "sevenpm".

## [0.9.1] - 2026-10-16

### Fixed

- **English times said as times of the day:** "13:00" is "one in the afternoon", "7:30 PM" "seven thirty in the
  evening", "14:00–15:00" "two to three in the afternoon"; noon and midnight by name. Before, a time came out digit by
  digit. The other ten languages read times as before.
- **The Mac's memory in her health report:** "Memory: 32 GB, pressure normal (23% free), swap (disk space used as
  memory) 2.9 of 4.0 GB used". Before, the line named only swap, and she read it as the Mac's memory.
- **GUIDE.md:** the `/lock` troubleshooting row gives the same 10 minutes as the rest of the guide.

## [0.9.0] - 2026-10-16

The first public release.

### Added

- **Core, works once setup is done:** chat, memory in two parts (yours in every message, notes from your chats when
  they matter), reminders, language practice in eight languages (`/babble`), saved lists, the Mac's health, photos
  (deleted after the first answer), YouTube summaries from captions, and an emergency stop (`/lock`).
- **Opt-in, off until turned on:** messages she starts (at most one a day, never between 22:00 and 08:00) and a
  three-question evening note written by the code.
- **Extras, each off until set up:** calls that can be interrupted, wake-up calls, voice notes transcribed on the Mac,
  calendars (read-only), Microsoft 365 mail (read-only), the 09:15 morning digest, Spotify, Kasa and Philips Hue
  lights, web search, chords, Google places and routes, projects on the Mac, and answers from your PDFs that name the
  document and the page.
- **Two built-in styles:** hearth or playful, picked at setup. Her name is the user's choice; her Signal picture is
  her name's first letter with Estia's orange full stop.

### Security

- Gemma, Google's open model, runs on the Mac through Ollama's official build, checked before it is installed (its
  fingerprint, every program's signature and Apple's notarization). A cloud model is refused. No cloud AI service, no
  account with us, no telemetry.
- Reached only through Signal. No web app and no open port: the bridge listens on nothing.
- Mail and calendars are read, never changed. Each light and plug is `normal`, `ask first` or `never off`; a device
  change after outside text waits for your yes.
- She runs as her own hidden macOS user, `_estia`. Only that account reads the keys.
- Known gaps, in plain words: SECURITY.md, "Known accepted gaps". The biggest: prompt injection is reduced by
  code-level rules, not eliminated; signal-cli is an unofficial Signal client; on 16 or 24 GB the smaller model,
  Gemma 4 12B, failed some of her safety tests.

[Unreleased]: https://github.com/enable4all/estia-on-mmini/compare/v0.9.4...HEAD
[0.9.4]: https://github.com/enable4all/estia-on-mmini/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/enable4all/estia-on-mmini/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/enable4all/estia-on-mmini/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/enable4all/estia-on-mmini/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/enable4all/estia-on-mmini/releases/tag/v0.9.0
