# Set up Estia with your AI

This file is written for an AI coding assistant, such as Claude Code or Codex, that a person has asked to install Estia on their Mac. If you are that person: in Terminal, run `git clone https://github.com/enable4all/estia-on-mmini.git ~/estia` (on a Mac without Apple's Command Line Tools, git asks to install them in a window on the Mac's own screen: run Homebrew's installer first instead, section 1), then open your assistant in `~/estia` and say "set me up". Your assistant checks the Mac and explains each question before it comes; you run setup in your own Terminal window, where every code and password is typed hidden.

---

## For the AI: your job

You are helping one person install Estia, a private AI thinking partner, on their own Mac. She is called Estia unless they give her another name (`name` in the config, 1 to 20 letters, spaces or hyphens); use the name they chose from then on.

- **Estia is reached only through Signal.** There is no web app, no port reachable from other machines and no cloud AI: the bridge opens no port, and Ollama and her voice service listen only on the Mac itself.
- **The model runs on the Mac.** A cloud model, which Ollama runs on its own servers, is refused. Mail and the calendar are read-only.
- **Only the owner's Signal account gets answers.**

Your job is to keep it that way while you help. You check, explain and read; the person runs setup. `scripts/setup.sh` runs only in the person's own Terminal window: it reads every code from that terminal, and stops at once in a command tool like yours.

**You may run only these** (read-only or the person's own folders, never admin rights): the checks in 1, `scripts/setup.sh --dry-run`, `scripts/setup.sh --extras --dry-run`, `scripts/doctor.sh` (before the move), `scripts/harden.sh --dry-run`, `scripts/migrate-to-own-user.sh --dry-run`, `scripts/share-documents.sh --dry-run` and `scripts/share-documents.sh` (5, Documents), `curl -s http://127.0.0.1:8150/health`, `scripts/install-ollama.sh --dry-run` and `(cd tools/youtube && uv sync --frozen)`. The person runs everything else: every command that starts with `/usr/bin/sudo`, and every script that calls it (rule 3).

**Done when:** `/usr/bin/sudo /usr/local/estia/bin/estia doctor`, run by the person, shows no ✗ and her welcome message has arrived on the person's phone. They reply to it to see her answer.

## The rules (security comes first)

1. **Never ask for, accept or repeat a secret in the chat:** API keys, passwords, tokens, the Signal captcha link, the SMS code, the Registration Lock PIN, the calendar's secret address, app-specific passwords, the TP-Link login, and any sign-in code or address a script asks the person to type or paste (Microsoft's device code, gcloud's and gh's codes, the address Spotify's sign-in ends on). Each goes into a hidden prompt in the person's own Terminal, or their own browser: never on screen, never on a program's command line. A secret pasted into the chat anyway: tell them to replace it.
2. **Never print or read secret files:** `~/.config/local-assistant/config.toml` (except to check that it exists), `*.env`, `*tokens*.json`, and nothing under `/var/estia` (her home: only her own user can read it, and you must not try). Use the doctor: it prints only ✓/✗ and the lengths of keys, never their values.
3. **Never run `sudo` yourself.** It gives a command admin rights, after the Mac password: Homebrew's installer, the move into her own user, the security step and her commands (`/usr/bin/sudo /usr/local/estia/bin/estia …`) need it. So does `scripts/install-ollama.sh` (for Ollama's background service), and so do these scripts, which call it themselves once she runs as `_estia`: `scripts/install-daemon.sh`, `scripts/install-voice-daemon.sh`, `scripts/set-web-keys.sh`, `scripts/set-calendar-url.sh`, `scripts/set-calendar-password.sh`, `scripts/set-tapo-credentials.sh`, `scripts/mail-login.sh`, `scripts/spotify-login.sh`, `scripts/hue-pair.sh` and `scripts/tapo-discover.sh`. The person runs each in their own Terminal window (a Claude Code `!` command has no terminal for the password) and pastes the output back.
4. **Name the password prompts before they come** (the briefing, 3).
5. **Never weaken a safety rule** (AGENTS.md, "Rules (safety)"): no extra allowed accounts, no port reachable from other machines (the bridge opens no port), no `--no-verify`, no world-readable files, no cloud model.
6. **Explain each step in one plain sentence before it runs.** Say what it does and what leaves the Mac, in words the person knows: "admin rights", not "sudo"; "only the Mac itself", not "127.0.0.1". Exact terms only in the commands they type.

## 1. Check first

These checks are read-only: run them yourself, and tell the person what is missing before anything else.

| What | Needed | Check |
|---|---|---|
| Mac | Apple silicon (M1 or later); macOS 14 or later; 15.6 or later for voice and calls, but not 26.4 or 26.5 (update to 26.6 or later) | `uname -m` shows `arm64`; `sw_vers -productVersion` |
| Memory | 16 GB or more; 32 GB for the tested model | `sysctl -n hw.memsize` |
| Disk | about 25 GB free, or about 15 GB on 16 or 24 GB of memory: the model takes 18.3 GB (7.7 GB on 16 or 24 GB), Apple's Command Line Tools and Homebrew about 1.8 GB when missing, voice about 1.7 GB | `df -h ~` |
| Admin account | the person's account is an admin: its password is needed for Homebrew, the move and the security step | `id -Gn` lists `admin` |
| Command Line Tools | Apple's developer tools, which hold git | `xcode-select -p` prints a folder; missing: the person runs Homebrew's installer first, `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` (the Mac password once), which installs them without a window on the Mac's screen; then the clone |
| The code | a git clone at `~/estia`. Not a ZIP download (her install and every update are built from git; setup refuses a folder that is not a clone), and not in Desktop, Documents or Downloads | `git -C ~/estia rev-parse HEAD`; if it fails: `git clone https://github.com/enable4all/estia-on-mmini.git ~/estia` |
| Her number | a phone number no Signal app uses (a prepaid SIM is fine): it receives one SMS | ask |
| Their phone | Signal, with their own number | ask |
| Over SSH | setup runs inside `screen` (built into macOS) or `tmux`: a dropped connection then does not stop the move | `echo $SSH_CONNECTION` (empty: not over SSH) |

Your own shell may not find Homebrew's tools, and your command tool may keep nothing between commands: put `eval "$(/opt/homebrew/bin/brew shellenv)" &&` in front of each command that runs `brew` or `uv` (`eval "$(/opt/homebrew/bin/brew shellenv)" && (cd tools/youtube && uv sync --frozen)`). The scripts find Homebrew themselves; setup adds that line to new Terminal windows.

## 2. The plan, then the hand-over

1. **You:** run `scripts/setup.sh --dry-run`. It prints the steps and commands and changes nothing (on a fresh clone: the tool commands and a short summary of the rest). Explain the plan from it in plain words.
2. **You:** give the briefing (3) in one message, before the person starts.
3. **The person:** runs `cd ~/estia && scripts/setup.sh` in their own Terminal window and answers each question. Over SSH: `screen` first, then setup inside it; after a dropped connection, `screen -r` goes back to it. Safe to run again: steps already done are skipped, and a stopped download resumes.
4. **The person:** pastes back the end of the output, the check (`/usr/bin/sudo /usr/local/estia/bin/estia doctor`), which holds no secrets.
5. **You:** fix every ✗ with the command it prints; for a log it names, have the person run it and share what it says. A fix that starts with `/usr/bin/sudo`: the person runs it. Then 4.

## 3. The briefing: what setup asks, in order

- **"Install Homebrew now?"**, only when it is missing: the usual installer for Mac tools. The Mac password once; it may say "Press RETURN". Homebrew's usage reports (analytics) stay off.
- **Ollama:** an official Ollama that already runs is kept. Otherwise setup downloads Ollama's official build from GitHub (168 MB), checks it before anything is installed (its fingerprint, every program's signature by Ollama, Apple's notarization) and runs it as a background service that starts with the Mac: the Mac password once. Homebrew's Ollama or the Ollama app answering stops setup with the fix, and so does Homebrew's background service for Ollama or the app's login item, even when the official build answers (Homebrew's: `brew services stop ollama`, then `brew uninstall ollama`; the app: `osascript -e 'quit app "Ollama"'`, then move it to the Trash): the person runs it, then setup again. Homebrew's Ollama only installed, not running and not set up as a service, is a "!". Setup also stops for another background service that runs Ollama while nothing answers (its line names it and the command that starts it again), and for an Ollama that other machines can reach. An official Ollama of another version is kept with a "!"; setup's own is updated in place, the old one answering until the new one is checked. Run again once she is set up, setup shows each of these stops as a "!" with its fix and goes on with the other steps, except an Ollama that other machines can reach, that another account runs or shares its port with, or whose signatures do not check out: that stops every run.
- **The model download:** `gemma4:12b-mlx` (about 8 GB) on 16 or 24 GB of memory, `gemma4:26b-mlx` (about 18 GB, the tested one) on 32 GB or more; both builds for Apple silicon, each checked against the build Estia was tested with. On 16 or 24 GB, setup first says on a line of its own what the smaller model failed in her safety tests, then asks **"Go on with the smaller model?"**: only y downloads it; Enter or n stops setup with nothing downloaded. Tell the person before the download: Gemma 4 12B failed some of her safety tests (SECURITY.md, known gaps). 32 GB with Gemma 26B is the tested setup. Often an hour, more on a slow line; running setup again resumes it. On a re-run with `local-worker` made before 0.9.3, setup says it has the small models' prompt and asks **"Make local-worker again now?"**: y makes it again (the same model and context, only the prompt changes) and prints its new digest, which a pinned config needs (`model_digest`); Enter or n leaves it. Tell the person to stop her first.
- **Her name:** Enter keeps Estia; a suggestion or any name (a typed one asks for a female or male voice).
- **"Use the Signal number already set up here?"**, only for a number that finished registering on this Mac before.
- **Her number:** `+`, the country code and the digits. It logs out any Signal app that used it.
- **The captcha** (new numbers usually get one): on any device, open https://signalcaptchas.org/registration/generate.html, solve it, copy the **Open Signal** link (`signalcaptcha://…`) and paste it at `captcha link:`. Nothing shows: press Enter once. Each try needs a new link.
- **The SMS code**, then a Registration Lock PIN only if the number had one (Enter skips); both typed hidden.
- **"Turn on Registration Lock now?"**: yes. A PIN typed hidden twice, kept in their password manager (without it and her data, the number is locked for 7 days).
- **The one-time word** (like `estia-4821`): sent from their own Signal to her new number, then Enter. Only the account that sent it gets answers.
- **The language**, then **Her style**, at "Which style? (Enter keeps hearth)": how she sounds while there is no `soul.md`. hearth: steady, kind and frank; the answer first, one concern when a plan looks risky, a joke only when asked for fun or on banter. playful: warm, curious and playful; the answer first, now and then a light joke, never on a worry. On a call either one is short and spoken, with no joke unless the caller jokes first. The rules always win; `[personality] style` in her config changes it later, and a `soul.md` replaces either one. Run again after the move, setup asks it at the end and only prints the lines to add to her config.
- **"Should she see projects on this Mac? Most people: no."** On yes: the folder (not a symbolic link; one outside their home is never shared by the move: the person gives `_estia` read access to it), then the level.
- **"Install it now?"**: the move into her own hidden user `_estia`, so she starts with the Mac. The Mac password once. Her data then lives in `/var/estia`, readable only by her user.
- **"Go through the security step now?"**: yes on a new install. Each change only on their yes; the Mac password once more; over SSH, a second SSH session open.
- **The extras:** Enter for none (5 adds them later). **The last check:** the Mac password again once 5 minutes have passed.

## 4. After setup

- **A new install has her service, without voice:** voice and calls are extras (5); her voice service comes only with `scripts/install-voice-daemon.sh`.
- **A first install has no rollback: the move keeps no copy, and her only copy of her data is in `/var/estia`.** A rollback exists only after moving an older install (below).
- **Her welcome** on Signal says what is set up, something to try for each, and what stays on the Mac. The person replies to it; `/help` then lists what she can do in this install.
- **Keep it running.** Tell the person the three settings that decide whether she answers after a power cut or a restart (setup_assistant.md, 2.8):
  - **No sleep, and start after a power failure:** System Settings → Energy, or `/usr/bin/sudo /usr/bin/pmset -a sleep 0 autorestart 1`, run by the person. Doctor's Power line shows ! until both are set.
  - **Ollama starts with the Mac:** its background service starts at boot without a login, as hers does, so no automatic login is needed (automatic login lets anyone at the Mac into their account after a restart).
  - **FileVault:** with it on, someone types the password after each restart. For a planned restart, `/usr/bin/sudo /usr/bin/fdesetup authrestart` skips that prompt.

## 5. Adding an extra

You list them with `scripts/setup.sh --extras --dry-run`, each with what leaves the Mac; the person runs `scripts/setup.sh --extras` (it asks questions). Add one at a time; each needs a ✓ or ○ in the doctor before the next. Follow the part's numbered steps in setup_assistant.md, section 3: the order differs per part.

- **Most parts:** first its script or sign-in, run by the person; then the config lines; then her restart. Only the person can sign in: GitHub (`gh auth login`, for calls), Google Cloud (`gcloud auth login`, for places and routes), Microsoft (mail and Outlook calendars), Spotify, the Hue Bridge's button, the TP-Link login.
- **Mail, Outlook calendars and Spotify: the config lines first** (`[[mail.accounts]]`, `[spotify]` with the app's `client_id`), then the sign-in (`scripts/mail-login.sh <address>`, `scripts/spotify-login.sh`), then her restart: each sign-in refuses a mailbox or an app that is not in her config yet.
- **Calls:** `tools/call-tunnel/install-signal-cli-jvm.sh` and `tools/call-tunnel/build.sh` (no admin rights), then `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` (it copies the builds into her release), then `[calls]`, then her restart. `[calls]` before the deploy stops her texts too.
- **Web search:** over SSH, the person types each key at a hidden prompt: `/usr/bin/sudo /usr/local/estia/bin/estia secret set web.env OLLAMA_API_KEY`, then the same with `BRAVE_API_KEY` (Ollama's search runs first, Brave's is the backup). On the Mac's own screen, the login keychain works too: `security add-generic-password -U -a "$USER" -s ollama -w` and `-s brave-search -w`, then `scripts/set-web-keys.sh`. Over SSH that keychain is not the default one and stays locked: use the hidden prompt.

The config lines: **her config, edited as her by the person:** `/usr/bin/sudo /usr/local/estia/bin/estia config` (nano, run as her; Ctrl-O, Enter saves; Ctrl-X quits). Give them the exact lines to paste: you cannot read the file. Personal values go in as placeholders the person fills in nano (`home = "<your address>"`, a mailbox, a device's address, an Apple ID): never ask for them in the chat. **Her restart:** `/usr/bin/sudo /usr/local/estia/bin/estia restart`, then the doctor. A new part shows ○ until its first use.

Projects needs no script: setup asks the folder and the level, then prints the lines, the share and the restart. YouTube links are no extra: always on, with none of the person's keys or accounts; only the video's ID goes to YouTube.

**Documents** (the person's PDFs in `~/Estia-KB` and the ones they send her, read on the Mac): first `scripts/share-documents.sh`, with no admin rights (you may run it, after saying in one sentence that it lets her account read and add files in that one folder, never delete or change theirs, and that her account then passes through their home by name, so the files there that every account may open (the share names them) open to her too). It makes the folder and prints the two lines for her config, `[documents]` and `folder = "/Users/<them>/Estia-KB"`, each on its own line; then the config lines and her restart (above). The folder is directly in the person's home: macOS guards Documents, Desktop and Downloads; a folder in the person's home needs no extra permission.

## Security (what to do and what to say)

- **Run setup's security step with them** (`scripts/harden.sh`; `scripts/harden.sh --dry-run` first shows every check and command). Before each sudo prompt, say in one sentence what that sudo does: the move creates her own user and copies her data, the security step changes SSH or removes a rule that skipped the password (with `--nuclear` also the firewall), her commands run as her.
- **The Mac's security levels.** The Mac's security has two levels: hardened, set by the security step (`scripts/harden.sh`), and nuclear, the hardened level with the Mac's firewall on and python3 and ruby off its list (`scripts/harden.sh --nuclear`; its stealth mode and keychain lock do not count toward the level). Doctor names the level the Mac is at. The nuclear option is never part of setup: it may stop other services on this Mac (it says how first). Offer it only to a person who asks for the strictest settings.
- **Recommend the phone checklist** at the end of the security step: Signal's Linked devices, Registration Lock and screen lock on their phone, her safety number marked as verified, and for the device with her SIM a SIM PIN, a screen lock, hidden notifications and the carrier's port-out PIN.
- **Say plainly what stays a risk:** an unlocked phone or laptop with their Signal can talk to her (`/lock` from the phone stops her until `/unlock`), and root on the Mac reads everything.
- **Once she runs as `_estia`, AI sessions on this Mac, this one included, cannot read her data** or change her code or her programs without the person's password, as long as no admin rule skips that password (the security step removes one). Two limits stay; tell the person in these words: "a program of yours can catch your password with a fake `sudo` that your Terminal finds before the real one, so type `/usr/bin/sudo`; a fake one written into your shell's startup files runs even then, so `whence -va /usr/bin/sudo` must list only the file, never a shell function or an alias", and "a program of yours can change what your next update puts live; the update shows the list of changes and the files they touch, not the code itself". What she sends to Ollama, which runs as the person, passes through their account: tell them so. A copy a program made before the move still works.
- **Every update (`estia-deploy`) shows the list of changes and asks first:** have the person read the list before they say yes. A change to the tools root runs comes only with `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools`, after its diff.

## Moving an older install

An install that already runs always on as the person (an older version) moves into her own user the same way: `scripts/migrate-to-own-user.sh --dry-run` first, then `scripts/install-daemon.sh`. Two things differ: her voice service, when it runs, moves too; and within 7 days `/usr/bin/sudo /usr/local/estia/bin/estia rollback` goes back, with what she saved since. `/usr/bin/sudo /usr/local/estia/bin/estia remove-old` deletes her copies kept aside together with her old folders held for the rollback: no rollback after it. Do not suggest it within the 7 days unless they want the space back. setup_assistant.md, 2.7, has the details.

**The order that worked** (a Mac reached over SSH):

1. The dry run (`scripts/migrate-to-own-user.sh --dry-run`), then the move.
2. With calls set up, wait for her text "My voice is ready: you can call me now." (up to about 15 minutes: the Neural Engine compiles her voice again for her user), then a text and a call; with voice but without calls, go on once `curl -s http://127.0.0.1:8150/health` shows `stt` and `tts` ready; without voice, go on at once.
3. The person runs `scripts/harden.sh` in their own Terminal, with a second SSH session open.
4. Test a new SSH login from the laptop and a call; after `scripts/harden.sh --nuclear`, also the programs on the Mac the person opens from the laptop (the firewall).

## If something fails

- **The doctor says what to do** (`/usr/bin/sudo /usr/local/estia/bin/estia doctor`, run by the person; `scripts/doctor.sh` before her own user). Every ✗ line ends with the command that fixes it, or the log to read first. ○ means set up, not used yet: that is fine. A ✗ "failing since …" is the part's last real use: after a fix, have the person use the part once, then run the doctor again; never the same fix twice. For her log, the person runs `/usr/bin/sudo /usr/local/estia/bin/estia logs bridge -n 40` in their own Terminal window and shares what it prints (only what happened and when, never message text).
- **Setup stopped:** "run scripts/setup.sh in your own Terminal window" (it was started without a terminal: hand it to the person); "not a git clone" (1); "✗ Ollama:" (the line says why and the fix: Homebrew's Ollama, the Ollama app or their background service to remove, another service that runs Ollama to start again, or a setting to change, which the person does, then setup again; a download that did not check out installs nothing: setup again, and if it says the same, SECURITY.md says where to report it); a stopped download (setup again resumes it); "The move did not finish" (setup stops before the security step: ask for the ✗ line, fix that, the dry run again, then setup again; on a new install nothing was installed).
- **Signal registration failed:** the number needs `+` and the country code, and each try a new captcha link. **The one-time word did not arrive:** most often it went to their own number or Note to Self instead of her new number; setup looks again on Enter.
- **Doctor's Always on line shows ✗** ("installed, no sign of life for 12 min", "restarting, last exit code 1", "installed, stopped" or "installed, not loaded: launchd does not run it"): have the person run the command doctor prints below that line and share what the log says. Doctor only reads; it never restarts her. A ○ "installed; whether it runs could not be read": her log says more.
- **"! YouTube: the YouTube helper is not set up" or "the documents helper is not built":** before her own user, `cd tools/youtube && uv sync --frozen` (no admin rights); in her own user, the person's next `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` builds them, after `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools` when it says the root-run tools differ.
- **"✗ Documents":** "not shared with her", "no share token" or "no folder": `scripts/share-documents.sh`. "… is not your ~/Estia-KB: the share opens only ~/Estia-KB to her": the person sets `folder = "/Users/<them>/Estia-KB"` (5, the config lines), restarts her and moves any PDFs over in Finder. "…/.git is not yours (her account may have added it)" or "holds a git checkout": tell the person, and change nothing yourself. "macOS refuses it (operation not permitted)": the folder is in Documents, Desktop or Downloads; `~/Estia-KB` is the fix.
- **No reply, no welcome, no answer to a call:** `/usr/bin/sudo /usr/local/estia/bin/estia check` shows whether her service and signal-cli run; `ignored a message from <ACI>` in her log is an account that isn't allowed. Calls go unanswered while her voice loads (up to about 15 minutes after an install, a move or a macOS update); with calls set up she then texts "My voice is ready: you can call me now."
- **Anything else** (the firewall, a cloud model, a held device): the doctor's line has the fix. `setup_assistant.md` is the full guide, and section 8 explains the security model.
