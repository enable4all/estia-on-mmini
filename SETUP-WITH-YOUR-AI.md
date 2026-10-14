# Set up Estia with your AI

This file is written for an AI coding assistant, such as Claude Code or Codex, that a person has asked to install Estia on their Mac. If you are that person: open this repository in your assistant and say "set me up". Your assistant will walk you through it and stop whenever only you can act.

---

## For the AI: your job

You are helping one person install Estia, a private AI thinking partner, on their own Mac. She is called Estia unless they give her another name (`name` in the config, 1 to 20 letters); use the name they chose from then on.

- **Estia is reached only through Signal.** There is no web app, no open port and no cloud AI.
- **The model runs on the Mac.** A cloud model, which Ollama runs on its own servers, is refused. Mail and the calendar are read-only.
- **Only the owner's Signal account gets answers.**

Your job is to keep it that way while you help.

**Done when:** `/usr/bin/sudo /usr/local/estia/bin/estia doctor`, run by the person, shows no ✗ and her welcome message has arrived on the person's phone. They reply to it to see her answer.

## The rules (security comes first)

1. **Never ask for, accept or repeat a secret in the chat.** That covers API keys, passwords, tokens, the Signal captcha link, the SMS code, the Registration Lock PIN and the calendar's secret address. Every one of them goes into a hidden prompt in the person's own Terminal: never on screen, and never on a program's command line (setup passes the captcha link, the SMS code and the PIN to signal-cli on its input). Ask them to run the command themselves in their own Terminal window, for example `scripts/set-web-keys.sh`: once she runs as `_estia` the key scripts need admin rights (sudo, which asks for the Mac password), and a Claude Code `!` command has no terminal for that password. If they paste a secret into the chat anyway, tell them to rotate it.
2. **Never print or read secret files:** `~/.config/local-assistant/config.toml` (except to check that it exists), `*.env`, `*tokens*.json`, and nothing under `/var/estia` (her home: only her own user can read it, and you must not try). Use the doctor: it prints only ✓/✗ and the lengths of keys, never their values.
3. **Never run `sudo` yourself.** It gives a command admin rights, after the Mac password. It is needed to install Homebrew, to move her into her own user (always on), for the security step, and for her commands (`/usr/bin/sudo /usr/local/estia/bin/estia …`). Explain each one before its password prompt, then have the person run it in their own Terminal window, not through the `!` prefix: sudo needs a terminal to ask for the password. Ask them to paste the doctor's output back to you (it holds no secrets).
4. **Name the password prompts before they come:** the move (one, plus the scripts it prints before it); the security step (one, plus one more after a rule that skipped the password is removed); every `/usr/bin/sudo /usr/local/estia/bin/estia …` or `… estia-deploy` once sudo's 5 minutes have passed, including setup's last check. Over a remote login (SSH), have them run the move inside `tmux`: a dropped connection then does not stop it (and if it does stop, her old setup comes back).
5. **Never weaken a safety rule** (AGENTS.md, "Rules (safety)"): no extra allowed accounts, no listening ports, no `--no-verify`, no world-readable files, no cloud model.
6. **Explain each step in one plain sentence before you run it.** Say what it does and what leaves the Mac, in words the person knows: "admin rights", not "sudo"; "only the Mac itself", not "127.0.0.1". Exact terms only in the commands they type.

**What you cannot do:** run sudo, or read her logs (they are hers, in `/var/estia`). When you need her log, ask the person to run `/usr/bin/sudo /usr/local/estia/bin/estia logs bridge -n 40` in their own Terminal window and share what it prints (only what happened and when, never message text). A Claude Code `!` command has no terminal for the password.

## The steps

Start with `scripts/setup.sh --dry-run`. It prints the steps and commands without doing anything, so you can explain the plan. On a fresh clone (no `.venv` yet) it prints the tool commands and a one-line summary of the rest; the table below has the details.

| Step | Who | What |
|---|---|---|
| 1. Homebrew and the tools | person, if Homebrew is missing (sudo); otherwise you | `scripts/setup.sh` installs Homebrew (with their OK), then `signal-cli`, `ollama` and `uv`, and starts Ollama. Every brew command it runs has Homebrew's analytics (usage reports) off |
| 2. The model | you | `setup.sh` picks the model for the Mac's memory: 16 or 24 GB → `gemma4:12b`, 32 GB or more → `gemma4:26b` (the tested one). The download is large; it keeps the Mac awake and can resume |
| 3. Her name and Signal number | **person** | `setup.sh` first asks for her name: Enter keeps Estia, or they pick a suggestion or type one (a typed name asks for a female or male voice). Then it asks for the number. A captcha link and an SMS code follow, both typed into hidden prompts, never printed, and passed to signal-cli on its input, never on a command line. Prepare them: a number that no Signal app uses (a prepaid SIM is fine), and any device to solve the captcha at https://signalcaptchas.org/registration/generate.html. Then a Registration Lock PIN, typed hidden twice and passed the same way: tell them to store it in their password manager (without it and her data, the number is locked for 7 days) |
| 4. Who may talk to her | **person** | They send a one-time word that `setup.sh` shows them to the new number from their own Signal; the account that sent it is the only one that gets answers (a profile name could be copied; the word cannot) |
| 5. Config and personality | you, through `setup.sh` | Asks which language she speaks, then "Should she see projects on this Mac? Most people: no." Only a person who wants her to read their git projects says yes; then the folder and the level (summary, docs or code), never guessed from their folders. Writes `~/.config/local-assistant/config.toml` (mode 600). No `soul.md`: she has her built-in style until the person writes their own (copy `soul.example.md`, mode 600) |
| 6. Always on, in her own user | **person** (sudo, in their own Terminal) | `setup.sh` asks; `scripts/migrate-to-own-user.sh` builds her release while nothing stops, then creates her hidden user `_estia` (no password, no login), copies her data to `/var/estia` (only that user can read it), keeps the code and every program she runs in `/usr/local/estia` (owned by root), installs both services and starts her. She never runs as them or as root. Show `scripts/migrate-to-own-user.sh --dry-run` first. Within 7 days `/usr/bin/sudo /usr/local/estia/bin/estia rollback` goes back, with what she saved since. With voice, it ends with "voice: ready"; while her voice still loads, with "Calls work once her voice is ready (up to about 15 minutes); she texts you then." when calls are set up, else "voice: loading (up to about 15 minutes)". On success it deletes the releases she does not run and lists any earlier copies of hers. `/usr/bin/sudo /usr/local/estia/bin/estia remove-old`, run by the person, deletes them together with her old folders held for the rollback: no rollback after it. Do not suggest it within the 7 days unless they want the space back |
| 6b. Security step | **person** (sudo once) | `setup.sh` asks "Go through the security step now?" right after the move. The order that worked (below) runs it once her voice is ready: they may say no there and run `scripts/harden.sh` then. Over SSH, have them keep a second SSH session open. `scripts/harden.sh` shows SSH with keys only, rules that let admin commands skip the password, her file modes and her Registration Lock, and applies only what they say yes to; FileVault, automatic login, the screen lock and a few more are shown with their fix. It ends with the phone checklist. The firewall, stealth mode, python3 and ruby off the firewall's list and the keychain lock are the nuclear option, `scripts/harden.sh --nuclear`, never part of setup: it may stop other services on this Mac (it says how first). Offer it only to a person who asks for the strictest settings |
| 7. Extras | the person chooses; keys are **person** only | `setup.sh` lists them, each with what leaves the Mac. Each extra has its own script with hidden prompts (setup_assistant.md, section 3). Projects needs none: setup asks the folder and the level; in her own user it prints the lines for the person to add to her config, the share and the restart |
| 8. Check | **person** (sudo) | `/usr/bin/sudo /usr/local/estia/bin/estia doctor` (before her own user: `scripts/doctor.sh`, which you may run); fix every ✗ with the command it prints |
| 9. Welcome | **person** | On her first start she sends them a welcome on Signal: what is set up, something to try for each, and what stays on the Mac. They reply to it |

**The order that worked** (a Mac reached over SSH):

1. The dry run (`scripts/migrate-to-own-user.sh --dry-run`), then the move.
2. With calls set up, wait for her text "My voice is ready: you can call me now." (up to about 15 minutes: the Neural Engine compiles her voice again for her user), then a text and a call; without them, go on once `curl -s http://127.0.0.1:8150/health` shows `stt` and `tts` ready.
3. The person runs `scripts/harden.sh` in their own Terminal, with a second SSH session open.
4. Test a new SSH login from the laptop and a call; after `scripts/harden.sh --nuclear`, also the programs on the Mac the person opens from the laptop (the firewall).

Keep the person's choices few. The core (steps 1–6b) is required. Extras can wait: `scripts/setup.sh --extras` shows them again any time.

## Security (what to do and what to say)

- **Run setup's security step with them** (`scripts/harden.sh`; `scripts/harden.sh --dry-run` first shows every check and command). Before each sudo prompt, say in one sentence what that sudo does: the move creates her own user and copies her data, the security step changes SSH or removes a rule that skipped the password (with `--nuclear` also the firewall), her commands run as her.
- **Never run `sudo` yourself**, and never ask for the password. The person types it in their own Terminal.
- **Never read or paste secrets**, and never try to read `/var/estia`.
- **Recommend the phone checklist** at the end of the security step: Signal's Linked devices, Registration Lock and screen lock on their phone, her safety number marked as verified, and for the device with her SIM a SIM PIN, a screen lock, hidden notifications and the carrier's port-out PIN.
- **Say plainly what stays a risk:** an unlocked phone or laptop with their Signal can talk to her (`/lock` from the phone stops her until `/unlock`), and root on the Mac reads everything.
- **Once she runs as `_estia`, AI sessions on this Mac, this one included, cannot read her data** or change her code or her programs without the person's password, as long as no admin rule skips that password (the security step removes one). Two limits stay; tell the person in these words: "a program of yours can catch your password with a fake `sudo` that your Terminal finds before the real one, so type `/usr/bin/sudo`", and "a program of yours can change what your next update puts live; the update shows the list of changes and the files they touch, not the code itself". What she sends to Ollama, which runs as the person, passes through their account: tell them so. A copy a program made before the move still works. Use `/usr/bin/sudo /usr/local/estia/bin/estia doctor`, run by the person, to see her state.
- **Every update (`estia-deploy`) shows the list of changes and asks first:** have the person read the list before they say yes. A change to the tools root runs comes only with `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools`, after its diff.

## If something fails

- **The doctor says what to do** (`/usr/bin/sudo /usr/local/estia/bin/estia doctor`, run by the person; `scripts/doctor.sh` before her own user). Every ✗ line ends with the command that fixes it. ○ means a part is set up but not used yet: that is fine.
- **A ✗ "failing since …" line is the part's last real use.** After a fix that saves a sign-in, key or address it shows ○. After any other fix (a device back on, a service running again), have the person use the part once (ask her something that needs it), then run the doctor again. Do not run the same fix twice.
- **The model download stopped:** run `scripts/setup.sh` again. It skips finished steps and resumes the download.
- **Signal registration failed:** check that the number is written with `+` and the country code. A new captcha link is needed for each try. A mistyped SMS code can be typed again at once.
- **The one-time word did not arrive:** `setup.sh` lists the usual reasons and looks again when the person presses Enter. The most common one: it went to their own number or Note to Self instead of her new number.
- **No welcome, or no reply from her:**
  - `/usr/bin/sudo /usr/local/estia/bin/estia check` (run by the person) shows whether the service and signal-cli run.
  - `/usr/bin/sudo /usr/local/estia/bin/estia logs` shows `bridge.log`, which holds only what happened and when, never message text. A line `ignored a message from <ACI>` means the message came from an account that isn't allowed.
  - A welcome that could not be sent is tried again at the next start. It goes only to a fresh install: one that already had messages never gets it.
- **She doesn't pick up:** her voice is still loading (up to about 15 minutes after an install, a move or a macOS update). Her note says so: "I can't take calls yet: my voice is still loading …". Asked "How is the Mac doing?", her status shows the voice loading its models. Have them call again after her text "My voice is ready: you can call me now."
- **The laptop cannot reach a program on the Mac (a page opened from the laptop, a file share):** the firewall is on (`scripts/harden.sh --nuclear`, an earlier security step, or the person's own choice) and does not let that program through. `/usr/bin/sudo /usr/local/estia/bin/estia doctor`, run by the person, names it ("! Firewall: … cannot be reached from other machines") with the two commands, each with the program's full path, after "if you started it and other machines should reach it:"; the person runs them only for a program they started. A program in a temporary folder gets no command. With "block all incoming connections" on, one line names every listener instead. After a Homebrew update the path changes: run doctor again.
- **"Answers are paused: Ollama lists the model … as a cloud model":** her model's name in Ollama points to a model on Ollama's servers. `/usr/bin/sudo /usr/local/estia/bin/estia doctor`, run by the person, gives the fix on its Model line: a model downloaded to the Mac, then a restart.
- **A new device is held** ("A message came from a new linked device N and was held."): theirs, `/devices ok N` from their phone; not theirs, remove it in Signal → Settings → Linked devices.
- **The move failed:** her old setup comes back by itself (on a first install, nothing is installed). Ask for the ✗ line (it names what failed), fix that, then run the dry run again before the move.
- **Anything else:** read `setup_assistant.md`. It is the full guide, and section 8 explains the security model.
