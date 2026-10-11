# Set up Estia with your AI

This file is written for an AI coding assistant, such as Claude Code or Codex, that a person has asked to install Estia on their Mac. If you are that person: open this repository in your assistant and say "set me up". Your assistant will walk you through it and stop whenever only you can act.

---

## For the AI: your job

You are helping one person install Estia, a private AI thinking partner, on their own Mac. She is called Estia unless they give her another name (`name` in the config, 1 to 20 letters); use the name they chose from then on.

- **Estia is reached only through Signal.** There is no web app, no open port and no cloud AI.
- **The model runs on the Mac.** Mail and the calendar are read-only.
- **Only the owner's Signal account gets answers.**

Your job is to keep it that way while you help.

**Done when:** `scripts/doctor.sh` shows no ✗ and her welcome message has arrived on the person's phone. They reply to it to see her answer.

## The rules (security comes first)

1. **Never ask for, accept or repeat a secret in the chat.** That covers API keys, passwords, tokens, the Signal captcha link, the SMS code, the registration-lock PIN and the calendar's secret address. Every one of them goes into a hidden prompt in the person's own Terminal. Ask them to run the command themselves, with the `!` prefix in Claude Code, for example `! scripts/set-web-keys.sh`. If they paste a secret into the chat anyway, tell them to rotate it.
2. **Never print or read secret files:** `~/.config/local-assistant/config.toml` (except to check that it exists), `*.env`, `*tokens*.json`. Use `scripts/doctor.sh`: it prints only ✓/✗ and lengths.
3. **Never run `sudo` yourself.** It is needed twice, to install Homebrew and the always-on service. Explain why, then let the person run it.
4. **Never weaken a safety rule** (AGENTS.md, "Rules (safety)"): no extra allowed accounts, no listening ports, no `--no-verify`, no world-readable files.
5. **Explain each step in one plain sentence before you run it.** Say what it does and what leaves the Mac.

## The steps

Start with `scripts/setup.sh --dry-run`. It prints the steps and commands without doing anything, so you can explain the plan. On a fresh clone (no `.venv` yet) it prints the tool commands and a one-line summary of the rest; the table below has the details.

| Step | Who | What |
|---|---|---|
| 1. Homebrew and the tools | person, if Homebrew is missing (sudo); otherwise you | `scripts/setup.sh` installs Homebrew (with their OK), then `signal-cli`, `ollama` and `uv`, and starts Ollama |
| 2. The model | you | `setup.sh` picks the model for the Mac's memory: 16 or 24 GB → `gemma4:12b`, 32 GB or more → `gemma4:26b` (the tested one). The download is large; it keeps the Mac awake and can resume |
| 3. Her name and Signal number | **person** | `setup.sh` first asks for her name: Enter keeps Estia, or they pick a suggestion or type one (a typed name asks for a female or male voice). Then it asks for the number. A captcha link and an SMS code follow, both typed into hidden prompts and never printed. Prepare them: a number that no Signal app uses (a prepaid SIM is fine), and any device to solve the captcha at https://signalcaptchas.org/registration/generate.html |
| 4. Who may talk to her | **person** | They send a one-time word that `setup.sh` shows them to the new number from their own Signal; the account that sent it is the only one that gets answers (a profile name could be copied; the word cannot) |
| 5. Config and personality | you, through `setup.sh` | Writes `~/.config/local-assistant/config.toml` (mode 600). No `soul.md`: she has her built-in style until the person writes their own (copy `soul.example.md`, mode 600) |
| 6. Always on | **person** (sudo) | `setup.sh` asks; `scripts/install-daemon.sh` installs the service, which runs as them, not root |
| 7. Extras | the person chooses; keys are **person** only | `setup.sh` lists them, each with what leaves the Mac. Each extra has its own script with hidden prompts (setup_assistant.md, section 3) |
| 8. Check | you | `scripts/doctor.sh`; fix every ✗ with the command it prints |
| 9. Welcome | **person** | On her first start she sends them a welcome on Signal: what is set up, something to try for each, and what stays on the Mac. They reply to it |

Keep the person's choices few. The core (steps 1–6) is required. Extras can wait: `scripts/setup.sh --extras` shows them again any time.

## If something fails

- **`doctor.sh` says what to do.** Every ✗ line ends with the command that fixes it. ○ means a part is set up but not used yet: that is fine.
- **A ✗ "failing since …" line is the part's last real use.** After a fix that saves a sign-in, key or address it shows ○. After any other fix (a device back on, a service running again), have the person use the part once (ask her something that needs it), then run `doctor.sh` again. Do not run the same fix twice.
- **The model download stopped:** run `scripts/setup.sh` again. It skips finished steps and resumes the download.
- **Signal registration failed:** check that the number is written with `+` and the country code. A new captcha link is needed for each try. A mistyped SMS code can be typed again at once.
- **The one-time word did not arrive:** `setup.sh` lists the usual reasons and looks again when the person presses Enter. The most common one: it went to their own number or Note to Self instead of her new number.
- **No welcome, or no reply from her:**
  - `scripts/check.sh` shows whether the service and signal-cli run.
  - `~/Library/Logs/local-assistant/bridge.log` holds metadata only. A line `ignored a message from <ACI>` means the message came from an account that isn't allowed.
  - A welcome that could not be sent is tried again at the next start. It goes only to a fresh install: one that already had messages never gets it.
- **Anything else:** read `setup_assistant.md`. It is the full guide, and section 8 explains the security model.
