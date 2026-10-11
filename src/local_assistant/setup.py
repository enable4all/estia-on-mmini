"""`scripts/setup.sh`: set her up step by step (by the config's name, default Estia), the parts she needs to work,
then the extras you pick.

Safe to run again: every step checks first and skips what is done. Keys and passwords never pass through here (each
extra has its own hidden-prompt script); the only codes typed here are Signal's one-time captcha link and SMS code,
read with a hidden prompt. Nothing is sent anywhere except what signal-cli and Ollama do for the step you are on.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from local_assistant import doctor, prompts
from local_assistant.config import DEFAULT_CONFIG_PATH, DEFAULT_NAME, Config, ConfigError, check_name, load_config
from local_assistant.language import CODES, ENGLISH, find

REPO = Path(__file__).resolve().parents[2]
OLLAMA = "http://127.0.0.1:11434"  # where setup.sh starts Ollama
IPV4 = "-Djava.net.preferIPv4Stack=true"  # registration goes through Java's HTTP client; hCaptcha is IPv4 only
PLACEHOLDER_NUMBER = "+300000000000"
PLACEHOLDER_ACI = "00000000-0000-0000-0000-000000000000"
_NUMBER = re.compile(r"^\+[0-9]{8,15}$")
_ACI = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RECEIVE_SECONDS = 20  # how long one look for the one-time word waits
# the name question's suggestions (owner's picks), and the English (Kokoro) voice each brings; the other ten
# languages speak with Supertonic's voice F1 whatever the name (the voice service has one style for now)
FEMALE_NAMES = ("Tiffany", "Maya", "Melina")
MALE_NAMES = ("Socrates", "Perikles", "Plato")
FEMALE_VOICE = "af_heart"
MALE_VOICE = "am_michael"
_VOICE_LINE = re.compile(r'^#voice = "af_heart"', re.MULTILINE)


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
) -> str:
    """config.example.toml with the bot's number, the owner's account ID, the language, her name and her voice filled
    in; everything else stays as the example has it (the extras commented out: the voice goes into the commented
    [voice] block's voice line, used once [voice] is turned on)."""
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
    return _VOICE_LINE.sub(f'#voice = "{voice}"', text)


@dataclass(frozen=True)
class Extra:
    title: str
    leaves_the_mac: str
    how: str


EXTRAS: dict[str, Extra] = {
    "voice": Extra(
        "Voice notes and spoken answers (speech stays on the Mac)",
        "nothing (the speech models are downloaded once)",
        "Take the # off [voice] and its lines in the config (the voice line has the voice that goes with her name),\n"
        "  then run scripts/install-voice-daemon.sh (setup_assistant.md, 3, Voice notes).",
    ),
    "calendar": Extra(
        "Your Google calendar in the morning digest and on request",
        "the calendar feed is fetched from Google",
        "Run scripts/set-calendar-url.sh, then add [calendar] (setup_assistant.md, 3, Calendar).",
    ),
    "mail": Extra(
        "Read-only mail from Microsoft 365 mailboxes, sorted for you",
        "mail is read from Microsoft Graph; bodies are never stored",
        "Register an Entra app, add [mail], then scripts/mail-login.sh <address> (setup_assistant.md, 3, Mail).",
    ),
    "lights": Extra(
        "Kasa lights and plugs at home",
        "nothing (they are switched over your home network)",
        "Run scripts/tapo-discover.sh, then add [[tapo.devices]] (setup_assistant.md, 3, Lights and plugs).",
    ),
    "web": Extra(
        "Web search with sources",
        "your search words, to Ollama's web search (Brave as backup)",
        "Save a key with scripts/set-web-keys.sh, then add [web] (setup_assistant.md, 3, Web search).",
    ),
    "maps": Extra(
        "Places and routes",
        "the place or route you ask about; 'near me' is rounded to about 1 km",
        "Run scripts/setup-google-maps.sh, then add [maps] (setup_assistant.md, 3, Places and routes).",
    ),
    "music": Extra(
        "Spotify by text message",
        "play and pause commands, to Spotify",
        "Create a Spotify app, add [spotify], then scripts/spotify-login.sh (setup_assistant.md, 3, Spotify).",
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


# ------------------------------------------------------------------ the steps (interactive; each checks first)


class Wizard:
    def __init__(self, config_path: Path, *, dry_run: bool, name: str = DEFAULT_NAME) -> None:
        self.config_path = config_path
        self.dry_run = dry_run
        self.name = name  # hers: the config's, once there is one, or the one chosen at the name question
        self.voice = FEMALE_VOICE  # the English voice that goes with the name chosen here
        self.signal_cli = shutil.which("signal-cli") or "signal-cli"

    def say(self, text: str) -> None:
        print(text, flush=True)

    def ask(self, question: str, default: str = "") -> str:
        try:
            answer = input(f"{question} ").strip()
        except EOFError:  # no terminal (a pipe, or Ctrl-D): take the default
            print()
            answer = ""
        return answer or default

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
        """A hidden answer (a code, a PIN); no terminal counts as q."""
        try:
            return getpass.getpass(prompt).strip()
        except EOFError:
            print()
            return "q"

    def run(
        self, command: list[str], *, shown: list[str] | None = None, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Run a fixed command (no shell), printed first (as `shown` when it holds a secret). In a dry run only print it."""
        self.say("  $ " + " ".join(command if shown is None else shown))
        if self.dry_run:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.run(command, text=True, check=False, **kwargs)  # type: ignore[call-overload,no-any-return]

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
            self.run(["ollama", "create", "local-worker", "-f", str(spec)])
        finally:
            if not self.dry_run:
                spec.unlink(missing_ok=True)
        done = "(dry run) would set" if self.dry_run else "✓ Model:"
        self.say(f"{done} local-worker = {choice.tag}, context {choice.num_ctx} tokens.")

    # 2. the Signal number
    def registered_numbers(self) -> list[str]:
        accounts = Path.home() / ".local/share/signal-cli/data/accounts.json"
        try:
            listed = json.loads(accounts.read_text(encoding="utf-8")).get("accounts", [])
        except (OSError, ValueError, AttributeError):
            return []
        return [str(a.get("number")) for a in listed if isinstance(a, dict) and a.get("number")]

    def signal_number(self) -> str:
        if self.dry_run:
            self.say(
                f"Signal number: asks for {self.name}'s number (+ and country code), then runs\n"
                f"  $ signal-cli {IPV4} -a <number> register   (and --captcha <link> if Signal asks; the link is typed hidden)\n"
                f"  $ signal-cli {IPV4} -a <number> verify <SMS code>   (the code is typed hidden)"
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
        base = [self.signal_cli, IPV4, "-a", number]
        first = self.run([*base, "register"], capture_output=True)
        if first.returncode != 0 and "captcha" in (first.stdout + first.stderr).lower():
            self.say(
                "Signal asks for a captcha (new numbers usually do):\n"
                "  1. On any device, open https://signalcaptchas.org/registration/generate.html and solve it.\n"
                "  2. Right-click (or long-press) 'Open Signal' and copy the link; it starts with signalcaptcha://\n"
                "  3. Paste it below (hidden). It is a one-time code; it is not stored."
            )
            captcha = getpass.getpass("captcha link: ").strip()
            first = self.run(
                [*base, "register", "--captcha", captcha],
                shown=[*base, "register", "--captcha", "<captcha link>"],
                capture_output=True,
            )
        if first.returncode != 0:
            raise SystemExit(f"Registration failed: {(first.stderr or first.stdout).strip()[:300]}")
        pin_prompt = "Registration-lock PIN, only if this number had one (hidden, Enter to skip): "
        while True:  # a mistyped code or PIN can be typed again; q stops
            code = self.hidden("The code Signal sent by SMS (hidden): ").replace("-", "")
            pin = "" if code.lower() == "q" else self.hidden(pin_prompt)
            if "q" in (code.lower(), pin.lower()):
                raise SystemExit("Verification failed: check the code and run setup again.")
            verify = self.run(  # shown without the code and the PIN: the terminal may be an AI's transcript
                [*base, "verify", code] + (["--pin", pin] if pin else []),
                shown=[*base, "verify", "<SMS code>"] + (["--pin", "<PIN>"] if pin else []),
                capture_output=True,
            )
            if verify.returncode == 0:
                break
            self.say(prompts.SETUP_CODE_FAILED.format(reason=(verify.stderr or verify.stdout).strip()[:300]))
        self.say(f"✓ Signal: {number} is {self.name}'s number.")
        return number

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
        receive = [self.signal_cli, IPV4, "-o", "json", "-a", number, "receive", "-t", str(RECEIVE_SECONDS)]
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

    def config(self, number: str, aci: str) -> None:
        """The config from the example. No soul.md: her built-in style is the default until the user writes one."""
        example = (REPO / "config.example.toml").read_text(encoding="utf-8")
        text = render_config(example, number, aci, self.language(), name=self.name, voice=self.voice)
        self._write_private(self.config_path, text)
        if self.dry_run:
            self.say(f"(dry run) would write config {self.config_path} (mode 600, only you can read it).")
        else:
            self.say(f"✓ Config: {self.config_path} (only you can read it).")
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

    # 5. always on
    def always_on(self) -> None:
        if Path("/Library/LaunchDaemons/com.local-assistant.bridge.plist").exists():
            self.say("✓ Always on: installed.")
            return
        self.say(
            f"Always on: {self.name} runs as a background service that starts when the Mac starts. Installing it needs\n"
            "your Mac password once (sudo copies the service file to /Library/LaunchDaemons). She runs as you, not root."
        )
        if self.dry_run:
            self.say(f"  $ {REPO / 'scripts/install-daemon.sh'}   (if you say yes)")
        elif self.yes("Install it now?"):
            self.run([str(REPO / "scripts/install-daemon.sh")])

    # 6. extras
    def extras(self) -> None:
        self.say("Extras (all optional, add any later with scripts/setup.sh --extras):")
        for i, (name, extra) in enumerate(EXTRAS.items(), 1):
            self.say(f"  {i}. {name:9} {extra.title}. Leaves the Mac: {extra.leaves_the_mac}.")
        if self.dry_run:
            self.say("  (asks which ones, then shows how to add each)")
            return
        picked, unknown = parse_extras(self.ask("Which ones? (names or numbers, Enter for none)"))
        if unknown:
            self.say(f"  (not an extra: {', '.join(unknown)})")
        for name in picked:
            self.say(f"• {name}: {EXTRAS[name].how}")

    def check(self) -> int:
        findings = doctor.run(self.config_path, doctor.Probe())
        self.say(doctor.render(findings))
        return doctor.exit_code(findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set the assistant up step by step (safe to run again).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="where the config goes")
    parser.add_argument("--extras", action="store_true", help="only show the extras and how to add them")
    parser.add_argument("--dry-run", action="store_true", help="print the steps and commands without running them")
    args = parser.parse_args(argv)
    wizard = Wizard(args.config, dry_run=args.dry_run)
    if args.extras:
        wizard.extras()
        return 0
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
    wizard.always_on()
    wizard.extras()
    wizard.say("\nChecking everything:")
    time.sleep(0.2)
    result = wizard.check()
    wizard.say(prompts.SETUP_DONE.format(name=wizard.name))
    return 0 if args.dry_run else result  # a dry run reports, it does not fail


if __name__ == "__main__":
    sys.exit(main())
