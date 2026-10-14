# Third-party notices

The code in this repository is MIT licensed (see `LICENSE`), except `tools/call-tunnel/`, which is AGPL-3.0-only (see
`tools/call-tunnel/LICENSE-NOTICE.md`).

Nothing below is vendored (copied into this repository) except the call-tunnel patch and the copied code listed at the end.
Components marked "installed" are fetched or built on your machine by the setup steps, from their own sources.

## Components

| Component | Licence | How it is used |
|---|---|---|
| Bridge (`src/`) | MIT | This project's own code. Python standard library only at runtime. |
| ruff, mypy, pytest, pytest-asyncio, uv_build | MIT / Apache-2.0 / MIT | Development tools only; not shipped. |
| python-kasa 0.11.0.1 | GPL-3.0-or-later | Installed only in `tools/tapo`'s own uv environment; not vendored. The bridge runs `tools/tapo/helper.py` as a separate process, and that helper imports python-kasa. The helper's own code stays MIT. If you distribute the helper together with python-kasa, python-kasa's GPL-3.0 terms apply to that combined distribution. |
| aiohttp and the other `tools/tapo` dependencies | Apache-2.0 / BSD-3-Clause / MIT / MIT-0 / PSF-2.0 | Installed in `tools/tapo`'s environment. |
| icalendar 7.3.0 | BSD-2-Clause | Installed in `tools/ical`'s environment; imported by the calendar helper. |
| recurring-ical-events 3.8.2, x-wr-timezone 2.0.1 | LGPL-3.0-or-later | Installed in `tools/ical`'s environment; imported, unmodified. |
| python-dateutil, six, tzdata, click | Apache-2.0 / BSD-3-Clause, MIT, Apache-2.0, BSD-3-Clause | Installed in `tools/ical`'s environment (dependencies). |
| WhisperKit 1.1.1 | MIT (its NOTICES list swift-transformers, Apache-2.0) | Swift package used by `tools/voice`. |
| FluidAudio 0.15.8 | Apache-2.0 (with its own third-party licence file) | Swift package used by `tools/voice`. |
| swift-argument-parser 1.8.2 | Apache-2.0 | Swift package used by `tools/voice`. |
| Whisper large-v3 turbo (CoreML build, `argmaxinc/whisperkit-coreml`, pinned commit) | MIT | Downloaded at runtime by the voice service, pinned to one commit and checked against its sha256 values (in `tools/voice/Sources/VoiceCore/ModelPin.swift`); not in this repository (about 1.6 GB). Check the model card. |
| Whisper large-v3 tokenizer files (`openai/whisper-large-v3`: `tokenizer.json`, `tokenizer_config.json`, `config.json`, pinned commit) | Apache-2.0 | Downloaded at runtime by the voice service with the model, pinned and checked the same way; WhisperKit reads its tokenizer from them. |
| Kokoro-82M (CoreML), its voices and the Misaki lexicon | See the model cards (Apache-2.0 / MIT as far as known) | Downloaded at runtime by the voice service. Confirm the licence on the model cards before you redistribute the models. |
| Supertonic-3 (Supertone Inc.), the CoreML build `FluidInference/supertonic-3-coreml` and its voice styles | OpenRAIL-M (the BigScience Open RAIL-M License of 18 August 2022), with its use-based restrictions (Attachment A) | Downloaded at runtime by the voice service (through FluidAudio) on the first use of a language other than English; not in this repository. The licence is the one on the `Supertone/supertonic-3` model card and in its `LICENSE` file ("Copyright (c) 2026 Supertone Inc."); the FluidInference build's card says it inherits it. FluidAudio's own docs say Apache-2.0 for these weights; the upstream card and licence file are what is listed here. Its sample code is MIT. Read the licence and its restrictions before you use or redistribute the model. |
| Silero VAD v6.2.1 (snakers4/silero-vad) via FluidAudio, the CoreML build `FluidInference/silero-vad-coreml` (`silero-vad-unified-256ms-v6.2.1`, pinned commit) | MIT | Downloaded at runtime by the voice service (through FluidAudio's `VadManager`), pinned to one commit and checked against its sha256 values (both in `tools/voice/Sources/VoiceCore/ModelPin.swift`); not in this repository (about 1 MB). Tells speech from noise on calls (`[calls] barge`). |
| Gemma 4 (via Ollama) | Google's Gemma terms of use | Runtime only. Ollama pulls the model; it is not in this repository. Read the model card and the terms before you use or redistribute it. |
| Ollama | MIT | Runtime only; installed separately. |
| Shippori Mincho B1 (The Shippori Mincho Project Authors) | SIL Open Font License 1.1 | Its letters were converted to outlines for the wordmark in `docs/brand/`; the font itself is not in this repository. |
| Manrope ExtraBold (The Manrope Project Authors) | SIL Open Font License 1.1 | The "e" and the full stop of Estia's mark (`docs/brand/estia-mark.svg`, enable4all's "e." mark, also in the avatar and the logo) are outlines from it, as enable4all's brand notes say; the font itself is not in this repository. |
| AgentJev (<https://github.com/malevrigns/agent-jev>) and its Qwen3-0.6B base model | Apache-2.0 (code); see the model cards for the weights | Optional mail pre-filter. Runs as a separate local service you install yourself; the bridge only sends it HTTP requests on 127.0.0.1 (`[mail] jev_url`). Not vendored. |
| signal-cli 0.14.x | GPL-3.0-or-later | Installed separately (Homebrew, or the JVM build from `tools/call-tunnel`). Run by the bridge as a child process, talking JSON-RPC over stdin/stdout. Not vendored. |
| signal-call-tunnel (built on RingRTC) | AGPL-3.0-only | signal-call-tunnel is modified by `tools/call-tunnel/local.patch`. Built on your machine; binaries are not distributed. See `tools/call-tunnel/LICENSE-NOTICE.md`. |
| OpenJDK | GPL-2.0 with the Classpath Exception | Runtime only, for the JVM build of signal-cli; installed separately. |

Online services used when you turn their features on (Signal, Microsoft Graph, Google Calendar iCal and Places/Routes,
Spotify Web API, Brave Search, Ollama web search) are services, not software in this repository. Their own terms apply.

## Copied or adapted code

- `tools/voice/Sources/VoiceCore/NumberNormalizer.swift` is adapted from OpenWhisperer by the same author, who
  licenses it here under this repository's MIT licence. `HTTP.swift` and `main.swift` in the same folder were written
  for this project.
- The threat-scanning patterns in `src/local_assistant/projects.py` are adapted from Hermes Agent
  (<https://github.com/NousResearch/hermes-agent>, `prompt_builder`), MIT licence, Copyright (c) 2025 Nous Research.
  The MIT permission notice is in the root `LICENSE` text; it applies to that adapted part as well.
