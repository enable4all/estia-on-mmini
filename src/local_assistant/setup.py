"""`scripts/setup.sh`: set her up step by step (by the config's name, default Estia), the parts she needs to work,
then the extras you pick.

Safe to run again: every step checks first and skips what is done. Keys and passwords never pass through here (each
extra has its own hidden-prompt script); the only codes typed here are Signal's one-time captcha link and SMS code
and a registration-lock PIN, read with a hidden prompt from the terminal itself (never from stdin, which may be an
AI's pipe) and given to signal-cli on stdin (never as an argument, which other users of the Mac can see). Nothing is
sent anywhere except what signal-cli and Ollama do for the step you are on. It runs only in the person's own terminal.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import termios
import time
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from local_assistant import doctor, harden, hostsec, ownuser, prompts
from local_assistant.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_NAME,
    PROJECT_LEVELS,
    Config,
    ConfigError,
    check_name,
    load_config,
    parse_config,
)
from local_assistant.hostsec import SUDO, estia_root
from local_assistant.language import CODES, ENGLISH, find

REPO = Path(__file__).resolve().parents[2]
OLLAMA = "http://127.0.0.1:11434"  # where setup.sh starts Ollama
IPV4 = "-Djava.net.preferIPv4Stack=true"  # registration goes through Java's HTTP client; hCaptcha is IPv4 only
PLACEHOLDER_NUMBER = "+300000000000"
PLACEHOLDER_ACI = "00000000-0000-0000-0000-000000000000"
_NUMBER = re.compile(r"^\+[0-9]{8,15}$")
_ACI = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TTY = "/dev/tty"  # the person's own terminal: every hidden answer is read from it, never from stdin
RECEIVE_SECONDS = 20  # how long one look for the one-time word waits
REGISTER_SECONDS = 120.0  # one register or verify request to Signal's servers
# the name question's suggestions (owner's picks), and the English (Kokoro) voice each brings; the other ten
# languages speak with Supertonic's voice F1 whatever the name (the voice service has one style for now)
FEMALE_NAMES = ("Tiffany", "Maya", "Melina")
MALE_NAMES = ("Socrates", "Perikles", "Plato")
FEMALE_VOICE = "af_heart"
MALE_VOICE = "am_michael"
_VOICE_LINE = re.compile(r'^#voice = "af_heart"', re.MULTILINE)
_DIGEST_LINE = re.compile(r'^#model_digest = "sha256:0{64}"', re.MULTILINE)
# the example's [projects] block, commented out (projects are an extra, off by default)
_PROJECTS_LINES = (
    re.compile(r"^#\[projects\]$", re.MULTILINE),
    re.compile(r'^#root = "~/dev"', re.MULTILINE),
    re.compile(r'^#default = "summary"', re.MULTILINE),
)


@dataclass(frozen=True)
class ModelChoice:
    tag: str
    num_ctx: int  # the context window the local-worker alias gets
    note: str


def choose_model(memory_gb: float) -> ModelChoice | None:
    """The best model for this Mac's memory, or None below 16 GB. Gemma 4 26B (a mixture of experts, 4B active) is
    the tested one and fast on Apple silicon, but needs about 18 GB of the GPU's share; 12B fits next to macOS and the
    voice models on 16 and 24 GB."""
    if memory_gb < 15:
        return None
    if memory_gb < 30:
        return ModelChoice("gemma4:12b", 16_384 if memory_gb < 20 else 32_768, "about 8 GB download")
    return ModelChoice("gemma4:26b", 32_768 if memory_gb < 44 else 65_536, "about 18 GB download; the tested model")


def modelfile(choice: ModelChoice) -> str:
    """The alias local-worker: the chosen model with a context window large enough for a long chat."""
    return f"FROM {choice.tag}\nPARAMETER num_ctx {choice.num_ctx}\n"


@dataclass(frozen=True)
class Sender:
    name: str
    aci: str
    text: str


def senders(lines: Iterable[str]) -> list[Sender]:
    """Who sent a message, from `signal-cli -o json receive` output (one JSON envelope per line), each person once."""
    found: dict[str, Sender] = {}
    for line in lines:
        try:
            envelope = json.loads(line).get("envelope", {})
        except (ValueError, AttributeError):
            continue
        data = envelope.get("dataMessage") if isinstance(envelope, dict) else None
        aci = str(envelope.get("sourceUuid") or "") if isinstance(envelope, dict) else ""
        if not isinstance(data, dict) or not isinstance(data.get("message"), str) or not _ACI.match(aci):
            continue
        name = str(envelope.get("sourceName") or envelope.get("sourceNumber") or "(no name)")
        found.setdefault(aci, Sender(name, aci, data["message"].strip()))
    return list(found.values())


def one_time_word(name: str, number: int) -> str:
    """The word the owner sends to show which account is theirs: her name and a number, like estia-4821."""
    return "-".join([*name.lower().replace("-", " ").split(), str(number)])


def owner_by_word(found: list[Sender], word: str) -> str | None:
    """The account that sent exactly the one-time word shown to the owner (a profile name can be copied)."""
    matches = {sender.aci for sender in found if sender.text.strip().lower() == word.lower()}
    return matches.pop() if len(matches) == 1 else None


def suggested(answer: str) -> tuple[str, str] | None:
    """A suggested name (any case, or 1 for the default) as it is written, with its voice; None for any other name."""
    if answer.strip() == "1":
        return DEFAULT_NAME, FEMALE_VOICE
    for names, voice in (((DEFAULT_NAME, *FEMALE_NAMES), FEMALE_VOICE), (MALE_NAMES, MALE_VOICE)):
        for name in names:
            if answer.strip().casefold() == name.casefold():
                return name, voice
    return None


def render_config(
    example: str,
    bot_number: str,
    aci: str,
    language: str = ENGLISH,
    *,
    name: str = DEFAULT_NAME,
    voice: str = FEMALE_VOICE,
    model_digest: str = "",
    projects: tuple[str, str] | None = None,
) -> str:
    """config.example.toml with the bot's number, the owner's account ID, the language, her name and her voice filled
    in, the model's pin when its digest is given, and [projects] with its folder and level when `projects` names them;
    everything else stays as the example has it (the extras commented out: the voice goes into the commented [voice]
    block's voice line, used once [voice] is turned on)."""
    if not _NUMBER.match(bot_number):
        raise ValueError("the bot's number must be + and the country code and digits, like +306900000000")
    if not _ACI.match(aci):
        raise ValueError("the account ID must be a UUID")
    if language not in CODES:
        raise ValueError(f"the language must be one of {', '.join(CODES)}")
    number_line = f'bot_number = "{PLACEHOLDER_NUMBER}"'
    aci_line = f'allowed_acis = ["{PLACEHOLDER_ACI}"]'
    if example.count(number_line) != 1 or example.count(aci_line) != 1:
        raise ValueError("config.example.toml no longer has its bot_number and allowed_acis placeholder lines")
    try:
        name = check_name(name)
    except ConfigError as exc:
        raise ValueError(str(exc)) from exc
    if voice not in (FEMALE_VOICE, MALE_VOICE):
        raise ValueError(f"the voice must be {FEMALE_VOICE} or {MALE_VOICE}")
    language_line = re.compile(r'^language = "en"', re.MULTILINE)
    if len(language_line.findall(example)) != 1:
        raise ValueError('config.example.toml no longer has its language = "en" line')
    name_line = re.compile(rf'^name = "{DEFAULT_NAME}"', re.MULTILINE)
    if len(name_line.findall(example)) != 1 or len(_VOICE_LINE.findall(example)) != 1:
        raise ValueError(f'config.example.toml no longer has its name = "{DEFAULT_NAME}" and #voice lines')
    text = example.replace(number_line, f'bot_number = "{bot_number}"').replace(aci_line, f'allowed_acis = ["{aci}"]')
    text = name_line.sub(f'name = "{name}"', language_line.sub(f'language = "{language}"', text))
    if model_digest:
        if not re.fullmatch(r"[0-9a-f]{64}", model_digest) or len(_DIGEST_LINE.findall(text)) != 1:
            raise ValueError(
                "the model's digest must be 64 hex digits, and the example must have its #model_digest line"
            )
        text = _DIGEST_LINE.sub(f'model_digest = "sha256:{model_digest}"', text)
    if projects is not None:
        if any(len(line.findall(text)) != 1 for line in _PROJECTS_LINES):
            raise ValueError("config.example.toml no longer has its commented [projects], root and default lines")
        text = with_projects(text, *projects)
    return _VOICE_LINE.sub(f'#voice = "{voice}"', text)


def with_projects(text: str, folder: str, level: str) -> str:
    """A config with [projects] on: its folder (as typed: ~ is the home of whoever runs her) and level. The example's
    commented block is filled in where it stands; a config without it gets the section at its end."""
    if level not in PROJECT_LEVELS:
        raise ValueError(f"the projects level must be one of {', '.join(PROJECT_LEVELS)}")
    root_line = f"root = {json.dumps(folder, ensure_ascii=False)}"
    if all(len(line.findall(text)) == 1 for line in _PROJECTS_LINES):
        header, root, default = _PROJECTS_LINES
        text = header.sub("[projects]", text)
        text = root.sub(lambda _: root_line, text)
        return default.sub(f'default = "{level}"', text)
    return f'{text.rstrip(chr(10))}\n\n[projects]\n{root_line}\ndefault = "{level}"\n'


def _config_problem(text: str) -> str:
    """Why a config's text would not load ("" when it loads)."""
    try:
        parse_config(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ConfigError) as exc:
        return str(exc)
    return ""


@dataclass(frozen=True)
class _Where:
    """The projects folder as typed (~ kept), as an absolute path, and the level."""

    typed: str
    folder: Path
    level: str


@dataclass(frozen=True)
class Extra:
    title: str
    leaves_the_mac: str
    how: str


EXTRAS: dict[str, Extra] = {  # new ones go last: the numbers of the others stay
    "voice": Extra(
        "Voice notes and spoken answers (speech stays on the Mac)",
        "nothing (the speech models are downloaded once)",
        "Take the # off [voice] and its lines in her config (the voice line has the voice that goes with her name),\n"
        "  then run scripts/install-voice-daemon.sh (setup_assistant.md, 3, Voice notes).",
    ),
    "calendar": Extra(
        "Calendars (Google, Outlook, iCloud, Fastmail or Nextcloud) in the morning digest and on request",
        "each calendar is read from its own service, read-only",
        "Google: scripts/set-calendar-url.sh; iCloud, Fastmail or Nextcloud: scripts/set-calendar-password.sh;\n"
        "  Outlook: its mailbox's sign-in (mail); then add [calendar] (setup_assistant.md, 3, Calendar).",
    ),
    "mail": Extra(
        "Read-only mail from Microsoft 365 mailboxes, sorted for you",
        "mail is read from Microsoft Graph; bodies are never stored",
        "Register an Entra app (Microsoft's admin site), add [mail], then scripts/mail-login.sh <address>\n"
        "  (a code typed at Microsoft's sign-in page) (setup_assistant.md, 3, Mail).",
    ),
    "lights": Extra(
        "Lights and plugs at home: Kasa, Tapo and Philips Hue",
        "nothing (they are switched over your home network)",
        "Kasa and Tapo: scripts/tapo-discover.sh (newer ones also scripts/set-tapo-credentials.sh, the TP-Link\n"
        "  login); Hue: scripts/hue-pair.sh (press the Hue Bridge's button when asked); then add [[tapo.devices]]\n"
        "  (setup_assistant.md, 3, Lights and plugs).",
    ),
    "web": Extra(
        "Web search with sources",
        "your search words, to Ollama's web search (Brave as backup)",
        "In her own user, type each key at a hidden prompt (also over SSH):\n"
        "    /usr/bin/sudo /usr/local/estia/bin/estia secret set web.env OLLAMA_API_KEY   (then BRAVE_API_KEY)\n"
        "  On the Mac's own screen the login keychain works too, typed hidden:\n"
        '    security add-generic-password -U -a "$USER" -s ollama -w   (-s brave-search for Brave\'s key)\n'
        "  then scripts/set-web-keys.sh copies them to her. Then add [web] (setup_assistant.md, 3, Web search).",
    ),
    "maps": Extra(
        "Places and routes",
        "the place or route you ask about; 'near me' is rounded to about 1 km",
        "Run scripts/setup-google-maps.sh (a Google Cloud project with billing; gcloud's own sign-in first),\n"
        "  then add [maps] (setup_assistant.md, 3, Places and routes).",
    ),
    "music": Extra(
        "Spotify by text message",
        "play and pause commands, to Spotify",
        "Create a Spotify app, add [spotify], then scripts/spotify-login.sh (setup_assistant.md, 3, Spotify).",
    ),
    "projects": Extra(
        "Your git projects in one folder of this Mac, read-only: summary, docs or code",
        "nothing (the files are read on the Mac)",
        "Nothing to install: setup asks the folder and the level now (setup_assistant.md, 3, Projects).",
    ),
    "documents": Extra(
        "Your PDFs in ~/Estia-KB and the ones you send her, read on the Mac",
        "nothing (the PDFs are read on the Mac)",
        "Run scripts/share-documents.sh (as you, no admin rights: it makes ~/Estia-KB and lets her account read it\n"
        '  and add files to it), then add [documents] with folder = "/Users/<you>/Estia-KB"\n'
        "  (setup_assistant.md, 3, Documents).",
    ),
    "calls": Extra(
        "Voice calls with her, and wake-up calls (needs voice)",
        "the calls, through Signal (end-to-end encrypted); the build downloads its code from GitHub and crates.io",
        "Needs voice first. Sign in to GitHub once (gh auth login: a GitHub account of yours), then run\n"
        "  tools/call-tunnel/install-signal-cli-jvm.sh and tools/call-tunnel/build.sh (several GB, a long build);\n"
        "  in her own user then /usr/bin/sudo /usr/local/estia/bin/estia-deploy (it copies the builds into her\n"
        "  release; [calls] before it stops her texts too); then add [calls] (setup_assistant.md, 3, Calls).",
    ),
}


def parse_extras(answer: str) -> tuple[list[str], list[str]]:
    """Picked extras (by name or number, in the menu's order; 'all' for every one) and the words that matched none."""
    names = list(EXTRAS)
    words = [w for w in re.split(r"[\s,]+", answer.strip().lower()) if w]
    if words == ["all"]:
        return names, []
    picked: list[str] = []
    unknown: list[str] = []
    for word in words:
        name = names[int(word) - 1] if word.isdigit() and 1 <= int(word) <= len(names) else word
        if name in EXTRAS:
            if name not in picked:
                picked.append(name)
        else:
            unknown.append(word)
    return sorted(picked, key=names.index), unknown


def has_terminal() -> bool:
    """Setup's questions and Signal's one-time codes need the person's own terminal: stdin is one, and it opens."""
    if not sys.stdin.isatty():
        return False
    try:
        os.close(os.open(TTY, os.O_RDWR | os.O_NOCTTY))
    except OSError:
        return False
    return True


def read_hidden(prompt: str, tty: str | None = None) -> str:
    """A hidden line of any length from the terminal itself, never from stdin (a pipe there may be an AI's). Its line
    mode is off while it reads: macOS keeps a typed line to 1,023 characters and drops the rest with the Enter, and
    Signal's captcha link is longer. Nothing is echoed; Backspace and Ctrl-U edit; Ctrl-C stops as usual. Ctrl-D on an
    empty line raises EOFError, no terminal OSError; the terminal's mode is always put back."""
    fd = os.open(tty or TTY, os.O_RDWR | os.O_NOCTTY)
    try:
        before = termios.tcgetattr(fd)
        quiet = termios.tcgetattr(fd)
        quiet[3] &= ~(termios.ICANON | termios.ECHO)
        quiet[6][termios.VMIN] = 1
        quiet[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSAFLUSH, quiet)
        try:
            os.write(fd, prompt.encode())
            return _hidden_line(fd)
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, before)
            os.write(fd, b"\n")
    finally:
        os.close(fd)


def _hidden_line(fd: int) -> str:
    """Bytes up to Enter (CR or LF), with Backspace (one character, all its UTF-8 bytes) and Ctrl-U applied."""
    line = bytearray()
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            raise EOFError
        for byte in chunk:
            if byte in (0x0A, 0x0D):
                return line.decode("utf-8", errors="replace").strip()
            if byte == 0x04 and not line:  # Ctrl-D on an empty line
                raise EOFError
            if byte in (0x08, 0x7F):
                while line and line.pop() & 0xC0 == 0x80:  # a continuation byte: the character goes on
                    pass
            elif byte == 0x15:  # Ctrl-U: the line so far
                line.clear()
            elif byte >= 0x20 or byte == 0x09:
                line.append(byte)


def registration_argv(signal_cli: str) -> list[str]:
    """signal-cli's JSON-RPC in multi-account mode (no -a): only there does 0.14.9 offer register and verify, so the
    captcha, the SMS code and a PIN go on stdin. It opens every account signal-cli keeps here; setup registers only
    when no config is set up yet, so no bridge holds one of them."""
    return [signal_cli, IPV4, *harden.TRUST, "jsonRpc", "--receive-mode", "manual"]


# ------------------------------------------------------------------ the steps (interactive; each checks first)


class Wizard:
    def __init__(self, config_path: Path, *, dry_run: bool, name: str = DEFAULT_NAME) -> None:
        self.config_path = config_path
        self.dry_run = dry_run
        self.name = name  # hers: the config's, once there is one, or the one chosen at the name question
        self.voice = FEMALE_VOICE  # the English voice that goes with the name chosen here
        self.signal_cli = shutil.which("signal-cli") or "signal-cli"
        self.clock: Callable[[], float] = time.monotonic  # the wait for her voice at the end
        self.sleep: Callable[[float], None] = time.sleep

    def say(self, text: str) -> None:
        print(text, flush=True)

    def typed(self, question: str) -> str | None:
        """The answer as typed; None without a terminal (a pipe, or Ctrl-D)."""
        try:
            return input(f"{question} ").strip()
        except EOFError:
            print()
            return None

    def ask(self, question: str, default: str = "") -> str:
        return self.typed(question) or default  # no terminal: take the default

    def yes(self, question: str) -> bool:
        return self.ask(f"{question} [y/N]").lower().startswith("y")

    def again(self, question: str) -> bool:
        """Enter (or anything but q): try again; q, or no terminal (Ctrl-D, a pipe): stop."""
        try:
            answer = input(f"{question} ").strip().lower()
        except EOFError:
            print()
            return False
        return answer != "q"

    def hidden(self, prompt: str) -> str:
        """A hidden answer (the captcha link, a code, a PIN), from the terminal only; Ctrl-D counts as q. Without a
        terminal setup stops and says where to run it: a code is never read from stdin."""
        try:
            return read_hidden(prompt)
        except EOFError:
            return "q"
        except OSError:
            raise SystemExit(prompts.SETUP_NO_TERMINAL) from None

    def run(
        self, command: list[str], *, shown: list[str] | None = None, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Run a fixed command (no shell), printed first (as `shown` when it holds a secret). In a dry run only print it."""
        self.say("  $ " + " ".join(command if shown is None else shown))
        if self.dry_run:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.run(command, text=True, check=False, **kwargs)  # type: ignore[call-overload,no-any-return]

    def registration(
        self, method: str, params: dict[str, object], shown: str, secrets: dict[str, str]
    ) -> tuple[bool, str]:
        """register or verify: one JSON-RPC request on signal-cli's stdin, printed first with `shown` (placeholders
        for the secrets). (done, why not); a secret signal-cli's error repeats shows as its placeholder."""
        argv = registration_argv(self.signal_cli)
        self.say("  $ " + " ".join(argv) + f"   ({shown} on stdin)")
        if self.dry_run:
            return True, ""
        return harden.json_rpc(argv, method, params, secrets=secrets, timeout=REGISTER_SECONDS)

    # 1. the model
    def installed_models(self) -> set[str]:
        """Ollama's model names, read-only (the doctor's GET), so a dry run sees what a real run sees; empty when
        Ollama does not answer."""
        try:
            listed = doctor.Probe().get_json(f"{OLLAMA}/api/tags").get("models", [])
            return {str(item.get("name", "")) for item in listed if isinstance(item, dict)}
        except (OSError, ValueError, AttributeError, TypeError):
            return set()

    def model(self) -> None:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        if self.installed_models() & {"local-worker", "local-worker:latest"}:
            self.say("✓ Model: local-worker is there.")
            return
        choice = choose_model(memory)
        if choice is None:
            self.say(f"✗ This Mac has {memory:.0f} GB of memory. {self.name} needs 16 GB or more.")
            raise SystemExit(1)
        self.say(f"Model: {choice.tag} for this Mac's {memory:.0f} GB ({choice.note}).")
        self.say("  The download can take a while; the Mac stays awake meanwhile. If it stops, run setup again.")
        if self.run(["caffeinate", "-i", "ollama", "pull", choice.tag]).returncode != 0:
            raise SystemExit("The download did not finish. Run scripts/setup.sh again to resume it.")
        spec = REPO / ".local-worker.Modelfile"
        if not self.dry_run:
            spec.write_text(modelfile(choice), encoding="utf-8")
        try:
            made = self.run(["ollama", "create", "local-worker", "-f", str(spec)]).returncode == 0
        finally:
            if not self.dry_run:
                spec.unlink(missing_ok=True)
        if not made:
            raise SystemExit(prompts.SETUP_MODEL_NOT_MADE)
        done = "(dry run) would set" if self.dry_run else "✓ Model:"
        self.say(f"{done} local-worker = {choice.tag}, context {choice.num_ctx} tokens.")

    # 2. the Signal number
    def registered_numbers(self) -> list[str]:
        """The numbers signal-cli finished registering here: listed in accounts.json, and its own account file says
        registered. signal-cli lists a number as soon as register starts, before the SMS code: a number whose code was
        never typed is registered again, never offered."""
        signal_data = Path.home() / ".local/share/signal-cli"
        try:
            listed = hostsec.signal_listed(signal_data, "")
        except (OSError, ValueError):
            return []
        found: list[str] = []
        for item in listed:
            account = hostsec.account_file(signal_data, item) if item.get("number") else None
            if account is not None and account.get("registered") is True:
                found.append(str(item["number"]))
        return found

    def signal_number(self) -> str:
        if self.dry_run:
            rpc = " ".join(registration_argv("signal-cli"))
            self.say(
                f"Signal number: asks for {self.name}'s number (+ and country code), then runs\n"
                f"  $ {rpc}   (register <number> on stdin; with <captcha link> if Signal asks, typed hidden)\n"
                f"  $ {rpc}   (verify <number> with <SMS code> on stdin; the code is typed hidden)\n"
                "Then Registration Lock, if you say yes: a PIN typed hidden twice, set with\n"
                f"  $ {' '.join(harden.set_pin_argv('signal-cli', '<number>'))}   (setPin with <PIN> on stdin)\n"
                f"  ({harden.LOCK_NOTE})"
            )
            return PLACEHOLDER_NUMBER
        numbers = self.registered_numbers()
        if len(numbers) == 1 and self.yes(f"Use the Signal number already set up here ({numbers[0]})?"):
            return numbers[0]
        self.say(
            f"{self.name} needs her own Signal number: a phone number you control that no Signal app uses (a cheap prepaid\n"
            "SIM is fine; the SIM only has to receive one SMS). Registering it here logs out any Signal app on it."
        )
        number = self.ask("The number, with + and the country code (like +306900000000):")
        if not _NUMBER.match(number):
            raise SystemExit("That does not look like +<country code><number>. Run setup again.")
        done, why = self.registration("register", {"account": number}, f"register {number}", {})
        if not done and "captcha" in why.lower():
            self.say(
                "Signal asks for a captcha (new numbers usually do):\n"
                "  1. On any device, open https://signalcaptchas.org/registration/generate.html and solve it.\n"
                "  2. Right-click (or long-press) 'Open Signal' and copy the link; it starts with signalcaptcha://\n"
                "  3. Paste it below (hidden). It is a one-time code; it is not stored."
            )
            captcha = self.hidden("captcha link: ")
            if captcha.lower() == "q":
                raise SystemExit("Registration stopped: run setup again (a new captcha link for each try).")
            token = captcha.removeprefix("signalcaptcha://")
            done, why = self.registration(
                "register",
                {"account": number, "captcha": token},
                f"register {number} with <captcha link>",
                {captcha: "<captcha link>", token: "<captcha link>"},
            )
        if not done:
            raise SystemExit(f"Registration failed: {why.strip()[:300]}")
        pin_prompt = "Registration-lock PIN, only if this number had one (hidden, Enter to skip): "
        while True:  # a mistyped code or PIN can be typed again; q stops
            code = self.hidden("The code Signal sent by SMS (hidden): ").replace("-", "")
            pin = "" if code.lower() == "q" else self.hidden(pin_prompt)
            if "q" in (code.lower(), pin.lower()):
                raise SystemExit("Verification failed: check the code and run setup again.")
            params: dict[str, object] = {"account": number, "verificationCode": code, **({"pin": pin} if pin else {})}
            done, why = self.registration(  # shown without the code and the PIN: the terminal may be an AI's transcript
                "verify",
                params,
                f"verify {number} with <SMS code>{' and <PIN>' if pin else ''}",
                {code: "<SMS code>", pin: "<PIN>"},
            )
            code = pin = ""
            if done:
                break
            self.say(prompts.SETUP_CODE_FAILED.format(reason=why.strip()[:300]))
        self.say(f"✓ Signal: {number} is {self.name}'s number.")
        self.registration_lock(number)
        return number

    def registration_lock(self, number: str) -> None:
        """Right after registering: a PIN typed hidden twice, set over signal-cli's JSON-RPC on stdin (never argv,
        never printed). Skipped, scripts/harden.sh offers it again."""
        self.say(harden.LOCK_NOTE)
        if not self.yes("Turn on Registration Lock now?"):
            self.say("  Later: scripts/harden.sh offers it again.")
            return
        shown = [*harden.set_pin_argv(self.signal_cli, number), "(setPin with <PIN> on stdin)"]
        while True:
            pin = self.hidden("Registration Lock PIN (hidden; q to skip): ")
            if pin.lower() == "q":
                self.say("  Skipped: scripts/harden.sh offers it again.")
                return
            problem = harden.pin_problem(pin)
            if problem:
                self.say(f"  The PIN needs {problem}.")
                continue
            if self.hidden("The same PIN again (hidden): ") != pin:
                self.say("  The two did not match; type it again.")
                continue
            self.say("  $ " + " ".join(shown))
            done, why = harden.set_pin(self.signal_cli, number, pin)
            pin = ""
            if done:
                self.say("✓ Registration Lock: on. Keep the PIN in your password manager.")
                return
            self.say(f"✗ Registration Lock: not set ({why}).")
            if not self.again("Press Enter to try again, or type q to leave it for scripts/harden.sh."):
                return

    # 3. who may talk to her: the owner's account ID, found from a message they send
    def owner(self, number: str) -> str:
        if self.dry_run:
            self.say(
                f"Your account: shows a one-time word (like {one_time_word(self.name, 4821)}) to send from your own "
                "Signal, then runs\n"
                f"  $ signal-cli {IPV4} -o json -a <number> receive -t 20   and keeps the account that sent the word\n"
                "  (not there yet: lists the usual reasons and looks again on Enter; q stops)"
            )
            return PLACEHOLDER_ACI
        word = one_time_word(self.name, secrets.randbelow(9000) + 1000)
        self.say(f"Now, from your own Signal on your phone, send exactly this word to {number}:  {word}")
        self.ask("Press Enter once it is sent.")
        receive = [
            self.signal_cli,
            IPV4,
            *harden.TRUST,
            "-o",
            "json",
            "-a",
            number,
            "receive",
            "-t",
            str(RECEIVE_SECONDS),
        ]
        while True:  # the same word on every look: the owner is whoever sent it
            aci = owner_by_word(senders(self.run(receive, capture_output=True).stdout.splitlines()), word)
            if aci is not None:
                self.say(f"✓ Only your account can talk to {self.name}. Messages from anyone else are ignored.")
                return aci
            self.say(
                prompts.SETUP_WORD_MISSING.format(word=word, number=number, seconds=RECEIVE_SECONDS, name=self.name)
            )
            if not self.again(prompts.SETUP_LOOK_AGAIN):
                raise SystemExit(f"'{word}' did not arrive (or came from more than one account). Run setup again.")

    # 1b. her name (asked before the Signal number, so the steps after it use it)
    def her_name(self) -> None:
        """One question: Enter keeps Estia; a suggestion brings its voice; any other name that fits the config's rule
        asks for a female or male voice. A dry run only shows the question."""
        self.say(
            prompts.SETUP_NAME.format(
                default=DEFAULT_NAME, female=" · ".join(FEMALE_NAMES), male=" · ".join(MALE_NAMES)
            )
        )
        if self.dry_run:
            self.say(prompts.SETUP_NAME_DRY_RUN.format(default=DEFAULT_NAME))
            return
        while True:
            answer = self.ask(prompts.SETUP_NAME_ASK.format(default=DEFAULT_NAME), DEFAULT_NAME)
            known = suggested(answer)
            if known is not None:
                self.name, self.voice = known
                break
            try:
                self.name = check_name(answer)
            except ConfigError as exc:
                self.say(prompts.SETUP_NAME_BAD.format(answer=answer, problem=exc))
                continue
            self.voice = self._voice()
            break
        kind = "male" if self.voice == MALE_VOICE else "female"
        self.say(prompts.SETUP_NAME_CHOSEN.format(name=self.name, kind=kind, voice=self.voice))

    def _voice(self) -> str:
        """Female (Enter) or male, for a name that is not a suggestion."""
        while True:
            answer = self.ask(prompts.SETUP_VOICE_ASK).lower()
            if answer in ("", "f", "female"):
                return FEMALE_VOICE
            if answer in ("m", "male"):
                return MALE_VOICE
            self.say(prompts.SETUP_VOICE_UNKNOWN)

    # 4. the language, the config and the personality
    def language(self) -> str:
        """One of the 11 (default English); a dry run only shows the list."""
        self.say(prompts.SETUP_LANGUAGE.format(name=self.name))
        for line in prompts.language_lines():
            self.say(f"  {line}")
        if self.dry_run:
            self.say(prompts.SETUP_LANGUAGE_DRY_RUN)
            return ENGLISH
        while True:
            answer = self.ask(prompts.SETUP_LANGUAGE_ASK)
            chosen = find(answer) if answer else find(ENGLISH)
            if chosen is not None:
                self.say(prompts.SETUP_LANGUAGE_CHOSEN.format(name=prompts.language_name(chosen.code)))
                return chosen.code
            self.say(prompts.SETUP_LANGUAGE_UNKNOWN.format(answer=answer))

    def model_digest(self) -> str:
        """The digest Ollama lists for local-worker now (read-only); "" when it lists none."""
        try:
            listed = doctor.Probe().get_json(f"{OLLAMA}/api/tags").get("models", [])
        except (OSError, ValueError, AttributeError, TypeError):
            return ""
        for item in listed if isinstance(listed, list) else []:
            if isinstance(item, dict) and item.get("name") in ("local-worker", "local-worker:latest"):
                digest = str(item.get("digest") or "").lower().removeprefix("sha256:")
                return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""
        return ""

    def projects(self) -> tuple[str, str] | None:
        """Projects, an extra: asked, never guessed from the Mac's folders (most people: no). Yes: the folder (as typed,
        ~ allowed; it must exist, and not be your home itself) and the level. A dry run only shows the question."""
        self.say(prompts.SETUP_PROJECTS.format(name=self.name))
        if self.dry_run:
            self.say(prompts.SETUP_PROJECTS_DRY_RUN)
            return None
        if not self.yes(prompts.SETUP_PROJECTS_ASK):
            return None
        where = self._projects_where()
        return None if where is None else (where.typed, where.level)

    def _projects_where(self) -> _Where | None:
        """The projects folder (it must exist, and not be your home itself; q or no terminal: none) and the level."""
        while True:
            given = self.typed(prompts.SETUP_PROJECTS_FOLDER)
            if given is None or given.lower() == "q":  # no terminal: the default folder may be missing, never a loop
                return None
            answer = given or "~/dev"
            folder = Path(os.path.abspath(Path(answer).expanduser()))
            if folder == Path.home():
                self.say(prompts.SETUP_PROJECTS_HOME)
            elif not folder.is_dir():
                self.say(prompts.SETUP_PROJECTS_NO_FOLDER.format(folder=folder))
            else:
                break
        while True:
            level = self.ask(prompts.SETUP_PROJECTS_LEVEL, "summary").lower()
            if level in PROJECT_LEVELS:
                break
            self.say(prompts.SETUP_PROJECTS_BAD_LEVEL.format(answer=level))
        typed = answer if answer.startswith("~") else str(folder)
        self.say(prompts.SETUP_PROJECTS_CHOSEN.format(folder=typed, level=level))
        return _Where(typed, folder, level)

    def projects_extra(self) -> None:
        """Projects picked from the extras: the folder and the level asked, then [projects] in the config (a copy of
        the old one kept). In her own user her config is hers alone: the lines to add and the commands, nothing
        written. A config that has [projects] already is left alone."""
        mine = self.own_user()
        text = ""
        if not mine:
            try:
                text = self.config_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self.say(prompts.SETUP_PROJECTS_NO_CONFIG.format(path=self.config_path))
                return
            if ownuser.projects_on(text):
                self.say(prompts.SETUP_PROJECTS_ON)
                return
            problem = _config_problem(text)
            if problem:  # the user's work: never changed, and nothing asked for it
                self.say(prompts.SETUP_PROJECTS_NOT_ADDED.format(path=self.config_path, why=problem))
                return
        self.say(prompts.SETUP_PROJECTS.format(name=self.name))
        where = self._projects_where()
        if where is None:
            return
        if mine:  # her ~ is /var/estia: her config names the folder by its absolute path
            home = Path.home()
            inside = where.folder != home and where.folder.is_relative_to(home)  # share-projects.sh shares only these
            share = prompts.SETUP_PROJECTS_SHARE if inside else prompts.SETUP_PROJECTS_OUTSIDE
            self.say(
                prompts.SETUP_PROJECTS_HERS.format(
                    root=json.dumps(str(where.folder), ensure_ascii=False),
                    level=where.level,
                    share=share.format(folder=where.folder),
                )
            )
            return
        new = with_projects(text, where.typed, where.level)
        problem = _config_problem(new)
        if problem:
            self.say(prompts.SETUP_PROJECTS_NOT_ADDED.format(path=self.config_path, why=problem))
            return
        self._write_private(self.config_path, new)
        self.say(prompts.SETUP_PROJECTS_ADDED.format(path=self.config_path))

    def config(self, number: str, aci: str) -> None:
        """The config from the example, with the model's pin when Ollama lists the alias setup just made, and
        [projects] when the user asks for it. No soul.md: her built-in style is the default until the user writes
        one."""
        example = (REPO / "config.example.toml").read_text(encoding="utf-8")
        digest = self.model_digest()
        language = self.language()
        projects = self.projects()
        text = render_config(
            example,
            number,
            aci,
            language,
            name=self.name,
            voice=self.voice,
            model_digest=digest,
            projects=projects,
        )
        self._write_private(self.config_path, text)
        if self.dry_run:
            self.say(f"(dry run) would write config {self.config_path} (mode 600, only you can read it).")
        else:
            self.say(f"✓ Config: {self.config_path} (only you can read it).")
        if digest:
            self.say(prompts.SETUP_PINNED)

    def personality(self) -> None:
        """Her style file, said at the end, once it is known where her files are: in her own user as her (her ~ is
        /var/estia; the move takes your folder), else in your config folder. No soul.md: her built-in style."""
        if self.own_user():
            self.say(
                prompts.SETUP_PERSONALITY_HERS.format(
                    name=self.name,
                    save=prompts.NANO_SAVE,
                )
            )
            return
        soul = self.config_path.parent / "soul.md"
        if soul.exists():
            self.say(f"✓ Personality: {soul} (yours; edit it any time).")
        else:
            self.say(prompts.SETUP_PERSONALITY.format(name=self.name, soul=soul))

    def _write_private(self, path: Path, text: str) -> None:
        """Write a file only the user can read; a file already there is copied first, never just replaced."""
        if path.exists():
            self._keep_copy(path)
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        path.chmod(0o600)

    def _keep_copy(self, path: Path) -> None:
        """Copy a file about to be replaced to <name>.bak-<time>, mode 600 (never over an earlier copy)."""
        copy = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        if self.dry_run:
            self.say(f"  (would keep a copy of {path} as {copy}, mode 600)")
            return
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(path.read_bytes())
        copy.chmod(0o600)
        self.say(f"Kept a copy of the old {path.name}: {copy} (only you can read it).")

    # 5. always on: in her own user
    def own_user(self) -> bool:
        """She runs as her own user: the bridge's service file says so (after a rollback it names you again)."""
        return ownuser.moved(estia_root())

    def always_on(self) -> bool:
        """Her own user: done already, the move (on a yes), or skipped. False only when the move failed: then setup
        stops, before the security step and the extras."""
        if self.own_user():
            self.say(f"✓ Always on: as her own user {ownuser.ROLE_USER}, from {ownuser.CURRENT}.")
            return True
        legacy = (estia_root() / ownuser.DAEMONS.relative_to("/") / f"{ownuser.BRIDGE}.plist").exists()
        move = str(REPO / "scripts" / "migrate-to-own-user.sh")
        if legacy:
            self.say(
                f"Always on: {self.name} runs as you today. Her own hidden user {ownuser.ROLE_USER} keeps her data and keys\n"
                f"away from every program you run: the move copies her data to {ownuser.HOME} (only that user can read it)\n"
                f"and her code to {ownuser.CODE} (owned by root). It needs your Mac password once; she never runs as you or as root."
            )
        else:
            self.say(
                f"Always on: {self.name} runs as a background service that starts when the Mac starts, as her own hidden\n"
                f"user {ownuser.ROLE_USER}: her data moves to {ownuser.HOME} (only that user can read it) and her code to\n"
                f"{ownuser.CODE} (owned by root). Installing it needs your Mac password once (sudo creates the user and\n"
                "copies the files and the two service files). She never runs as you or as root."
            )
        if self.dry_run:
            self.say(f"  $ {move}   (if you say yes; --dry-run first shows every step)")
            return True
        if os.environ.get("SSH_CONNECTION") and not (os.environ.get("STY") or os.environ.get("TMUX")):
            self.say(prompts.SETUP_SSH_SCREEN)  # a dropped connection would stop the move halfway
        if not self.yes("Install it now?" if not legacy else "Move her into her own user now?"):
            return True
        if self.run([move]).returncode != 0:
            self.say(prompts.SETUP_MOVE_FAILED.format(move=move))
            return False
        return True

    # 5b. the Mac's security around her, then the phone
    def security(self) -> None:
        harden_sh = str(REPO / "scripts" / "harden.sh")
        self.say(
            f"Security level {hostsec.HARDENED}: SSH with keys only, no passwordless sudo, her files private and her\n"
            "Registration Lock, each with one line on why; a change is made only on your yes, and sudo asks for your\n"
            "password once. FileVault and the other settings only you can change are shown with their fixes.\n"
            f"  {hostsec.NUCLEAR_LINE[0].upper()}{hostsec.NUCLEAR_LINE[1:]}; never part of setup."
        )
        if self.dry_run:
            self.say(f"  $ {harden_sh}   (if you say yes; {harden_sh} --dry-run shows every check)")
            for line in harden.PHONE_CHECKLIST:
                self.say(line)
            return
        if self.yes("Go through the security step now?"):
            self.run([harden_sh])  # it ends with the phone checklist
            return
        self.say(f"  Later: {harden_sh}")
        for line in harden.PHONE_CHECKLIST:
            self.say(line)

    # 6. extras
    def extras(self) -> None:
        self.say("Extras (all optional, add any later with scripts/setup.sh --extras):")
        for i, (name, extra) in enumerate(EXTRAS.items(), 1):
            self.say(f"  {i:>2}. {name:9} {extra.title}. Leaves the Mac: {extra.leaves_the_mac}.")
        if self.dry_run:
            self.say("  (asks which ones, then shows how to add each)")
            return
        picked, unknown = parse_extras(self.ask("Which ones? (names or numbers, Enter for none)"))
        if unknown:
            self.say(f"  (not an extra: {', '.join(unknown)})")
        for name in picked:
            self.say(f"• {name}: {EXTRAS[name].how}")
            if name == "projects":
                self.projects_extra()
        if any(name != "projects" for name in picked):  # where each section goes, and when she reads it
            if self.own_user():
                self.say(prompts.SETUP_EXTRAS_HERS.format(edit=prompts.HER_CONFIG_EDIT, save=prompts.NANO_SAVE))
            else:
                self.say(prompts.SETUP_EXTRAS_YOURS.format(path=self.config_path))

    def voice_health(self) -> dict[str, object] | None:
        """The voice service's health (127.0.0.1 only; never the running service under a test's ESTIA_ROOT)."""
        found = ownuser.voice_health(estia_root() == Path("/"))
        return dict(found) if found is not None else None

    def voice_end(self, *, calls: bool = False) -> None:
        """The last word on her voice when its service is installed: ready, or when calls work (a first install, a
        move or a macOS update compiles it again). A text is promised only with `calls` ([calls] in the config setup
        read; her own user's config is hers alone, so a re-run there promises none)."""
        plist = estia_root() / ownuser.DAEMONS.relative_to("/") / f"{ownuser.VOICE}.plist"
        if not self.dry_run and plist.exists():
            self.say(
                ownuser.voice_end(self.voice_health, calls=calls, clock=self.clock, sleep=self.sleep, say=self.say)
            )

    def check(self) -> int:
        if self.own_user():  # her config and data are hers alone: read through her wrapper, by its fixed path
            self.say("sudo asks for your Mac password: her doctor runs as her own user.")
            return self.run([SUDO, str(ownuser.BIN / "estia"), "doctor"]).returncode
        findings = doctor.run(self.config_path, doctor.Probe())
        self.say(doctor.render(findings))
        return doctor.exit_code(findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set the assistant up step by step (safe to run again).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="where the config goes")
    parser.add_argument(
        "--extras",
        action="store_true",
        help="show the extras and how to add them (projects: asks the folder and the level and adds [projects])",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the steps and commands without running them")
    args = parser.parse_args(argv)
    wizard = Wizard(args.config, dry_run=args.dry_run)
    if not args.dry_run and not args.extras and not has_terminal():  # an AI's command tool: it says where to run it
        wizard.say(prompts.SETUP_NO_TERMINAL)
        return 1
    if args.extras:
        wizard.extras()
        return 0
    if wizard.own_user():  # a re-run: never registers again, never rewrites her config
        wizard.say(
            f"Setup: she runs as her own user {ownuser.ROLE_USER}; her config and data are readable only by that user,\n"
            "so setup reads them through /usr/bin/sudo /usr/local/estia/bin/estia and never writes them.\n"
        )
        wizard.model()
        wizard.say(f"✓ Config, Signal number and your account: in her own user ({ownuser.HOME}).")
        wizard.always_on()
        wizard.security()
        wizard.extras()
        wizard.personality()
        wizard.say("\nChecking everything:")
        result = wizard.check()
        wizard.voice_end()
        wizard.say(prompts.SETUP_DONE.format(name=wizard.name))
        return 0 if args.dry_run else result
    cfg: Config | None = None
    problem: ConfigError | None = None
    if args.config.exists():  # a config with a mistake is the user's work: say what is wrong, never replace it
        try:
            cfg = load_config(args.config)
            wizard.name = cfg.name  # her name from here on (the banner, the steps, the last line)
        except ConfigError as exc:
            problem = exc
    wizard.say(f"{wizard.name} setup: the model, her Signal number, who may talk to her, the config, always on.\n")
    if problem is not None:
        wizard.say(f"✗ Config: {problem}")
        wizard.say(
            "  Setup stops here and changes nothing, so your config is not replaced. Fix it\n"
            "  (scripts/doctor.sh checks it), then run setup again."
        )
        return 1
    configured = cfg is not None and cfg.bot_number != PLACEHOLDER_NUMBER and PLACEHOLDER_ACI not in cfg.allowed_acis
    wizard.model()
    if configured:
        wizard.say(f"✓ Config: {args.config} is set up (bot number and your account).")
    else:
        wizard.her_name()
        number = wizard.signal_number()
        wizard.config(number, wizard.owner(number))
    if not wizard.always_on():
        return 1  # the move failed: its ✗ and the next step are on the screen
    wizard.security()
    wizard.extras()
    wizard.personality()
    wizard.say("\nChecking everything:")
    time.sleep(0.2)
    result = wizard.check()
    wizard.voice_end(calls=cfg is not None and cfg.calls_enabled)  # a config setup just wrote has no [calls]
    wizard.say(prompts.SETUP_DONE.format(name=wizard.name))
    return 0 if args.dry_run else result  # a dry run reports, it does not fail


if __name__ == "__main__":
    sys.exit(main())
