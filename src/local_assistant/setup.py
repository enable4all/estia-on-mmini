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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from local_assistant import doctor, harden, hostsec, ollama_build, ownuser, prompts, share
from local_assistant.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_NAME,
    DEFAULT_STYLE,
    PROJECT_LEVELS,
    STYLES,
    Config,
    ConfigError,
    check_name,
    load_config,
    parse_config,
)
from local_assistant.hostsec import SUDO, estia_root
from local_assistant.language import CODES, ENGLISH, find

REPO = Path(__file__).resolve().parents[2]
SHARE_FAILED = 3  # scripts/migrate-to-own-user.sh: she was moved and only the projects share after it failed
OLLAMA = ollama_build.BASE  # Ollama's official build, on this Mac only (the Ollama step keeps or installs it)
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
    digest: str  # the build's manifest digest (Ollama's list shows its first 12 digits as the ID): checked after a pull
    num_ctx: int  # the context window the local-worker alias gets
    note: str
    ollama: str = ollama_build.MIN_TEXT  # the oldest Ollama it works with: JSON-schema output on the MLX engine
    tested: bool = True  # passed her safety tests; False: setup says to use it with caution and asks first


# Gemma 4's builds for Apple silicon (MLX), by their explicit tags (owner, 15 Oct). Never the bare gemma4:26b or
# gemma4:12b: since Ollama 0.40.0 they are lists, and what they download depends on the Ollama version (on 0.40.x the
# untested 26b-nvfp4 build).
GEMMA_26B = "gemma4:26b-mlx"
GEMMA_26B_DIGEST = "f0fc7e0ae4947d382989b1f57db3d098a86b87c5b6e9523b9561ba81a2a64879"  # 18.3 GB, the live model
GEMMA_12B = "gemma4:12b-mlx"
GEMMA_12B_DIGEST = "ded7a27350032202d9e9b2a6071e8aa89959ab156771b5228f30863c741c4970"  # 7.7 GB


def choose_model(memory_gb: float) -> ModelChoice | None:
    """The best model for this Mac's memory, or None below 16 GB. Gemma 4 26B (a mixture of experts, 4B active) is
    the tested one and fast on Apple silicon, but its 18.3 GB need that much of the GPU's share; 12B fits next to macOS
    and the voice models on 16 and 24 GB, but failed some of her safety tests (15 Oct, run twice; SECURITY.md, known
    gaps): setup says to use it with caution and asks first (`Wizard.model`; owner, 19 Oct: never what failed). Both
    need Ollama 0.33.1 or later."""
    if memory_gb < 15:
        return None
    if memory_gb < 30:
        note = "a 7.7 GB download, about 10 minutes at 100 Mbit/s"
        return ModelChoice(GEMMA_12B, GEMMA_12B_DIGEST, 16_384 if memory_gb < 20 else 32_768, note, tested=False)
    context = 32_768 if memory_gb < 44 else 65_536
    note = "an 18.3 GB download, about 25 minutes at 100 Mbit/s; the tested model"
    return ModelChoice(GEMMA_26B, GEMMA_26B_DIGEST, context, note)


def memory_gb() -> float:
    """This Mac's memory in GB (GiB, as macOS counts it)."""
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30


def modelfile(choice: ModelChoice) -> str:
    """The alias local-worker: the chosen model with a context window large enough for a long chat, and for Gemma 4
    of 12B or more Google's prompt for that size (ollama_build.LARGE_PROMPT): Ollama tells the sizes apart by the
    model's name, which local-worker does not have (empty answers, 17 Oct)."""
    return alias_modelfile(choice.tag, choice.num_ctx)


def alias_modelfile(tag: str, num_ctx: int | None) -> str:
    """local-worker's Modelfile: FROM the tag, its context window (None: no line, Ollama's own applies), and the
    large prompt for a Gemma 4 tag of 12B or more."""
    lines = [f"FROM {tag}", *([f"PARAMETER num_ctx {num_ctx}"] if num_ctx is not None else [])]
    if ollama_build.large_gemma4(tag):
        lines.append(f"RENDERER {ollama_build.LARGE_PROMPT}")
    return "\n".join(lines) + "\n"


def context_of(parameters: str) -> int | None:
    """The num_ctx in /api/show's parameters text ("num_ctx    32768" lines); None when it sets none."""
    for line in parameters.splitlines():
        key, _, value = line.strip().partition(" ")
        if key == "num_ctx" and value.strip().isdigit():
            return int(value.strip())
    return None


# What an alias may add to its model's size in Ollama's list: its parameters and settings, a few hundred bytes (setup's
# num_ctx line: 16). Weights, an adapter or a draft model add megabytes.
ALIAS_EXTRA_BYTES = 64 * 1024


def _settings(parameters: str) -> list[str]:
    """/api/show's parameters text as sorted lines, spaces evened out and num_ctx left out: what an alias holds besides
    its context."""
    lines = (" ".join(line.split()) for line in parameters.splitlines())
    return sorted(line for line in lines if line and line.split(" ", 1)[0] != "num_ctx")


def _held(shown: Mapping[str, object]) -> tuple[object, ...]:
    """What a model holds, from its /api/show answer: its weights (architecture and parameter count from model_info,
    format and quantization from details; Ollama reads them from the weights' own files), its template, system prompt
    and messages, and its parameters but num_ctx."""
    info = shown.get("model_info")
    details = shown.get("details")
    info = info if isinstance(info, Mapping) else {}
    details = details if isinstance(details, Mapping) else {}
    return (
        info.get("general.architecture"),
        info.get("general.parameter_count"),
        details.get("format"),
        details.get("quantization_level"),
        str(shown.get("template") or ""),
        str(shown.get("system") or ""),
        repr(shown.get("messages") or []),
        _settings(str(shown.get("parameters") or "")),
    )


def made_from(
    alias: Mapping[str, object],
    alias_shown: Mapping[str, object],
    tag: Mapping[str, object],
    tag_shown: Mapping[str, object],
) -> bool:
    """Whether an alias is the tag with at most a context of its own, as setup made local-worker before 0.9.3 (FROM the
    tag, PARAMETER num_ctx): its digest is its own, and Ollama names no parent for an MLX build (its manifest has no
    model layer, so parent_model is ""), so it is traced by what it holds (`_held`, from each /api/show answer) and by
    its size in Ollama's list (`alias`, `tag`: their /api/tags entries), within ALIAS_EXTRA_BYTES of the tag's. False
    when any of it cannot be read: weights or settings a remake from the tag would drop are never guessed at."""
    alias_bytes, tag_bytes = _whole(alias.get("size")), _whole(tag.get("size"))
    if alias_bytes is None or tag_bytes is None or abs(alias_bytes - tag_bytes) > ALIAS_EXTRA_BYTES:
        return False
    held = _held(alias_shown)
    architecture, count = held[0], _whole(held[1])
    return isinstance(architecture, str) and bool(architecture) and bool(count) and held == _held(tag_shown)


def _whole(value: object) -> int | None:
    """A whole number from Ollama's JSON (never a bool); None for anything else."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _digest_of(entry: object) -> str:
    """A model's digest in its /api/tags entry, lowercase and without "sha256:" ("" when none)."""
    digest = entry.get("digest") if isinstance(entry, Mapping) else None
    return str(digest or "").lower().removeprefix("sha256:")


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
    style: str = DEFAULT_STYLE,
) -> str:
    """config.example.toml with the bot's number, the owner's account ID, the language, her name, her voice and her
    built-in style ([personality] style) filled in, the model's pin when its digest is given, and [projects] with its
    folder and level when `projects` names them;
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
    if style not in STYLES:
        raise ValueError(f"the style must be {' or '.join(STYLES)}")
    style_line = re.compile(rf'^style = "{DEFAULT_STYLE}"', re.MULTILINE)
    if len(style_line.findall(example)) != 1:
        raise ValueError(f'config.example.toml no longer has its style = "{DEFAULT_STYLE}" line')
    language_line = re.compile(r'^language = "en"', re.MULTILINE)
    if len(language_line.findall(example)) != 1:
        raise ValueError('config.example.toml no longer has its language = "en" line')
    name_line = re.compile(rf'^name = "{DEFAULT_NAME}"', re.MULTILINE)
    if len(name_line.findall(example)) != 1 or len(_VOICE_LINE.findall(example)) != 1:
        raise ValueError(f'config.example.toml no longer has its name = "{DEFAULT_NAME}" and #voice lines')
    text = example.replace(number_line, f'bot_number = "{bot_number}"').replace(aci_line, f'allowed_acis = ["{aci}"]')
    text = name_line.sub(f'name = "{name}"', language_line.sub(f'language = "{language}"', text))
    text = style_line.sub(f'style = "{style}"', text)
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


def with_chords(text: str) -> str:
    """A config with [chords] on: the example's commented section switched on where it stands; a config without it
    gets the section at its end."""
    if len(_CHORDS_LINE.findall(text)) == 1:
        return _CHORDS_LINE.sub("[chords]", text)
    return f"{text.rstrip(chr(10))}\n\n[chords]\n"


_CHORDS_LINE = re.compile(r"^# ?\[chords\]$", re.MULTILINE)  # the example's line, commented out


def _config_problem(text: str) -> str:
    """Why a config's text would not load ("" when it loads)."""
    try:
        parse_config(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ConfigError) as exc:
        return str(exc)
    return ""


def projects_refusal(folder: Path, home: Path) -> str | None:
    """Why setup does not take this projects folder, by the share's rules (`share.refusal`: the move shares it with
    her); None when it takes it. Inside your home: a folder of yours, reached without a symbolic link. Outside it:
    no symbolic link either, the folder or one on the way; the share leaves such a folder to you
    (`prompts.SETUP_PROJECTS_OUTSIDE`)."""
    plain, top = Path(os.path.normpath(folder)), Path(os.path.normpath(home))
    if plain.is_relative_to(top):
        return share.refusal(plain, top, os.getuid())
    if os.path.islink(plain):
        return prompts.SETUP_PROJECTS_LINK.format(folder=folder)
    if os.path.realpath(plain) != str(plain):
        return prompts.SETUP_PROJECTS_LINK_ON_THE_WAY.format(folder=folder)
    return None


def outside_home(folder: Path, home: Path) -> bool:
    """A projects folder the share does not share (it shares only folders in your home): opened to her by hand."""
    return not Path(os.path.normpath(folder)).is_relative_to(Path(os.path.normpath(home)))


@dataclass(frozen=True)
class _Where:
    """The projects folder as typed (~ kept), as an absolute path, and the level."""

    typed: str
    folder: Path
    level: str


@dataclass(frozen=True)
class Extra:
    title: str
    leaves_the_mac: str  # what leaves the Mac, and to which service by name
    how: str
    needs: str  # what it takes from you: an account, a sign-in, a key, a device


EXTRAS: dict[str, Extra] = {  # new ones go last: the numbers of the others stay
    "voice": Extra(
        "Voice notes and spoken answers (speech stays on the Mac)",
        "nothing (the speech models are downloaded once, from Hugging Face)",
        "Take the # off [voice] and its lines in her config (the voice line has the voice that goes with her name),\n"
        "  then run scripts/install-voice-daemon.sh (setup_assistant.md, 3, Voice notes).",
        "macOS 15.6 or later (not 26.4 or 26.5), about 1.7 GB of disk and your Mac password once",
    ),
    "calendar": Extra(
        "Calendars (Google, Outlook, iCloud, Fastmail or Nextcloud) in the morning digest and on request",
        "each calendar, read-only, from its own service: Google, Microsoft, iCloud, Fastmail or Nextcloud",
        "Google: scripts/set-calendar-url.sh; iCloud, Fastmail or Nextcloud: scripts/set-calendar-password.sh;\n"
        "  Outlook: its mailbox's sign-in (mail); then add [calendar] (setup_assistant.md, 3, Calendar).",
        "a Google calendar's secret address, an app-specific password (iCloud, Fastmail, Nextcloud) or the mail extra",
    ),
    "mail": Extra(
        "Read-only mail from Microsoft 365 mailboxes, sorted for you",
        "mail is read from Microsoft (Microsoft Graph); bodies are never stored",
        "Register an Entra app (Microsoft's admin site), add [mail], then scripts/mail-login.sh <address>\n"
        "  (a code typed at Microsoft's sign-in page) (setup_assistant.md, 3, Mail).",
        "a Microsoft 365 mailbox, an app registered in Microsoft's admin site and a Microsoft sign-in",
    ),
    "lights": Extra(
        "Lights and plugs at home: Kasa, Tapo and Philips Hue",
        "nothing (they are switched over your home network)",
        "Kasa and Tapo: scripts/tapo-discover.sh (newer ones also scripts/set-tapo-credentials.sh, the TP-Link\n"
        "  login); Hue: scripts/hue-pair.sh (press the Hue Bridge's button when asked); then add [[tapo.devices]]\n"
        "  (setup_assistant.md, 3, Lights and plugs).",
        "the devices on your home network; newer Kasa and Tapo: the TP-Link login; Hue: the Bridge's button",
    ),
    "web": Extra(
        "Web search with sources",
        "your search words, to Ollama's web search (Brave Search as backup)",
        "In her own user, type each key at a hidden prompt (also over SSH):\n"
        "    /usr/bin/sudo /usr/local/estia/bin/estia secret set web.env OLLAMA_API_KEY   (then BRAVE_API_KEY)\n"
        "  On the Mac's own screen the login keychain works too, typed hidden:\n"
        '    security add-generic-password -U -a "$USER" -s ollama -w   (-s brave-search for Brave\'s key)\n'
        "  then scripts/set-web-keys.sh copies them to her. Then add [web] (setup_assistant.md, 3, Web search).",
        "an API key from your account at ollama.com (and Brave Search's, for the backup)",
    ),
    "maps": Extra(
        "Places and routes",
        "the place or route you ask about, to Google; 'near me' is rounded to about 1 km",
        "Run scripts/setup-google-maps.sh (a Google Cloud project with billing; gcloud's own sign-in first),\n"
        "  then add [maps] (setup_assistant.md, 3, Places and routes).",
        "a Google Cloud project with billing, and a Google sign-in (gcloud)",
    ),
    "music": Extra(
        "Spotify by text message",
        "play and pause commands, to Spotify",
        "Create a Spotify app, add [spotify], then scripts/spotify-login.sh (setup_assistant.md, 3, Spotify).",
        "Spotify Premium, an app on Spotify's developer site and a Spotify sign-in",
    ),
    "projects": Extra(
        "Your git projects in one folder of this Mac, read-only: summary, docs or code",
        "nothing (the files are read on the Mac)",
        "Nothing to install: setup asks the folder and the level now (setup_assistant.md, 3, Projects).",
        "a folder of git projects in your home",
    ),
    "documents": Extra(
        "Your PDFs in ~/Estia-KB and the ones you send her, read on the Mac",
        "nothing (the PDFs are read on the Mac)",
        "Run scripts/share-documents.sh (as you, no admin rights: it makes ~/Estia-KB and lets her account read it\n"
        '  and add files to it), then add [documents] with folder = "/Users/<you>/Estia-KB"\n'
        "  (setup_assistant.md, 3, Documents).",
        "nothing: the share makes ~/Estia-KB",
    ),
    "calls": Extra(
        "Voice calls with her, and wake-up calls (needs voice)",
        "the calls, through Signal (end-to-end encrypted); the build's code comes from GitHub and crates.io",
        "Needs voice first. Sign in to GitHub once (gh auth login: a GitHub account of yours), then run\n"
        "  tools/call-tunnel/install-signal-cli-jvm.sh and tools/call-tunnel/build.sh (several GB, a long build);\n"
        "  in her own user then /usr/bin/sudo /usr/local/estia/bin/estia-deploy (it copies the builds into her\n"
        "  release; [calls] before it stops her texts too); then add [calls] (setup_assistant.md, 3, Calls).",
        "voice first, a GitHub account (its sign-in) and several GB of disk for a long build",
    ),
    "chords": Extra(
        "Chords transposed, moved for a capo, a chord's notes and the key of given chords, worked out on the Mac",
        "nothing (the chords are worked out on the Mac)",
        "Nothing to install: setup adds [chords] to your config now; in her own user it prints the line to add, as\n"
        "  her config is hers alone (GUIDE.md, Transposing chords).",
        "nothing",
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
        self.style = DEFAULT_STYLE  # her built-in style: the config's, once there is one, or the one chosen here
        self.style_asked = False  # the style question came already (the config step): the end asks it no more
        self.signal_cli = shutil.which("signal-cli") or "signal-cli"
        # the Ollama step names the one in use; your home from the user database, as the service file's
        self.ollama_program = str(ollama_build.owner_home() / "opt/ollama/current/ollama")
        # a first install stops at an Ollama the step refuses; a re-run where she is set up (main says so) shows it
        # as "!" and goes on, and the model step then pulls nothing
        self.first_install = True
        self.ollama_refused = False
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

    def terminal(self) -> bool:
        """The person's own terminal shows the question and answers it: only there may Enter mean yes."""
        return harden.in_terminal()

    def yes(self, question: str, *, default: bool = False) -> bool:
        """y or n. Enter takes the safe answer, `default` (owner, 19 Oct: [Y/n] or [y/N] says which), only from a
        terminal; the end of input (Ctrl-D, a pipe) is always a no, and Ctrl-C stops setup (`main`)."""
        enter = default and self.terminal()
        answer = self.typed(f"{question} {'[Y/n]' if enter else '[y/N]'}")
        if answer is None:
            return False
        return answer.lower().startswith("y") if answer else enter

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

    # 0. Ollama's official build (owner, 15 Oct), before the model it runs
    def ollama(self) -> None:
        """Keep the official Ollama that answers, or install its pinned build, checked, always on (ollama_build);
        anything else stops a first install with the fix the person runs. A re-run where she is set up already
        (configured, or in her own user) shows that as "!" with its fix and goes on with the other steps, except an
        Ollama that someone other than you may control (`ollama_build.endangers`): that stops every run. Off the live
        Mac (a test's ESTIA_ROOT) it is only printed, as in a dry run."""
        memory = memory_gb()
        choice = choose_model(memory)
        if choice is None:
            self.say(prompts.SETUP_TOO_SMALL.format(memory=memory, name=self.name))
            raise SystemExit(1)
        tools = ollama_build.Tools(say=self.say)
        home = ollama_build.owner_home()  # never $HOME: root's service file runs the program under it at every start
        step = ollama_build.Install(tools, home, dry_run=self.dry_run or not tools.live, say=self.say)
        try:
            self.ollama_program = step.run(choice.num_ctx)
        except ollama_build.Refusal as refused:
            if self.first_install or refused.fatal:  # someone other than you may control it: every run stops
                raise
            self.say(ollama_build.warned(str(refused)))
            self.say(prompts.SETUP_OLLAMA_GOES_ON)
            self.ollama_refused = True

    # 1. the model
    def listed_models(self) -> list[dict[str, object]]:
        """Ollama's model list, read-only (the doctor's GET), so a dry run sees what a real run sees; empty when
        Ollama does not answer."""
        try:
            listed = doctor.Probe().get_json(f"{OLLAMA}/api/tags").get("models", [])
        except (OSError, ValueError, AttributeError, TypeError):
            return []
        return [item for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []

    def model(self) -> None:
        memory = memory_gb()
        listed = self.listed_models()
        alias = next((item for item in listed if item.get("name") in ("local-worker", "local-worker:latest")), None)
        if alias is not None:
            shown = self.shown("local-worker")
            if shown is None or not ollama_build.gets_small_prompt("local-worker", shown, alias):
                self.say("✓ Model: local-worker is there.")
                return
            self.remake(alias, shown, listed)
            return
        if self.ollama_refused:  # never a download through an Ollama setup refused
            self.say(prompts.SETUP_MODEL_AFTER_OLLAMA)
            return
        choice = choose_model(memory)
        if choice is None:
            self.say(prompts.SETUP_TOO_SMALL.format(memory=memory, name=self.name))
            raise SystemExit(1)
        self.say(f"Model: {choice.tag} for this Mac's {memory:.0f} GB ({choice.note}).")
        if not choice.tested:  # owner, 15 Oct: still picked, but said plainly and never downloaded without a yes
            self.say(prompts.SETUP_SMALLER_MODEL)
            if self.dry_run:
                self.say(prompts.SETUP_SMALLER_MODEL_DRY_RUN.format(ask=prompts.SETUP_SMALLER_MODEL_ASK))
            elif not self.yes(prompts.SETUP_SMALLER_MODEL_ASK):
                raise SystemExit(prompts.SETUP_SMALLER_MODEL_NO)
        self.say(prompts.SETUP_DOWNLOAD)
        if self.run(["caffeinate", "-i", self.ollama_program, "pull", choice.tag]).returncode != 0:
            raise SystemExit("The download did not finish. Run scripts/setup.sh again to resume it.")
        got = "" if self.dry_run else self.listed_digest(choice.tag)
        if not self.dry_run and got != choice.digest:  # the tested build, by its digest: never one changed since
            raise SystemExit(
                prompts.SETUP_MODEL_OTHER_BUILD.format(
                    tag=choice.tag, got=got[:12] or "no digest", want=choice.digest[:12]
                )
            )
        spec = REPO / ".local-worker.Modelfile"
        if not self.dry_run:
            spec.write_text(modelfile(choice), encoding="utf-8")
        try:
            made = self.run([self.ollama_program, "create", "local-worker", "-f", str(spec)]).returncode == 0
        finally:
            if not self.dry_run:
                spec.unlink(missing_ok=True)
        if not made:
            raise SystemExit(prompts.SETUP_MODEL_NOT_MADE)
        done = "(dry run) would set" if self.dry_run else "✓ Model:"
        self.say(f"{done} local-worker = {choice.tag}, context {choice.num_ctx} tokens.")

    def shown(self, name: str) -> dict[str, object] | None:
        """Ollama's /api/show answer for a model (read-only: its Modelfile, parameters and size); None when Ollama
        does not answer."""
        try:
            answer = doctor.Probe().post_json(f"{OLLAMA}/api/show", {"model": name})
        except (OSError, ValueError):
            return None
        return answer if isinstance(answer, dict) else None

    def alias_base(
        self, alias: dict[str, object], shown: dict[str, object], listed: list[dict[str, object]]
    ) -> ModelChoice | None:
        """Which of setup's models local-worker is made from, only while that model is listed with its tested digest:
        the one whose digest it has (a copy, `ollama cp`), else the one it holds with at most a context of its own
        (`made_from`: what setup made before 0.9.3; that model's /api/show read, read-only). `shown`: the alias's own
        /api/show answer. None when it cannot be told."""
        choices = [choice for choice in (choose_model(32), choose_model(16)) if choice is not None]
        entries = {str(item.get("name")): item for item in listed}
        tested = [choice for choice in choices if _digest_of(entries.get(choice.tag)) == choice.digest]
        own = _digest_of(alias)
        copy = next((choice for choice in tested if own == choice.digest), None)
        if copy is not None:
            return copy
        for choice in tested:
            tag_shown = self.shown(choice.tag)
            if tag_shown is not None and made_from(alias, shown, entries[choice.tag], tag_shown):
                return choice
        return None

    def remake(self, alias: dict[str, object], shown: dict[str, object], listed: list[dict[str, object]]) -> None:
        """A re-run's alias with the small models' prompt (made before 0.9.3; empty answers, 17 Oct): said, and on a
        yes made again from the same model with its own context, so only the prompt changes. Its digest changes:
        the new one is printed with the pin's line. Never through an Ollama setup refused, never from a model setup
        cannot tell (the way by hand instead); a failed create leaves the old alias, and setup goes on."""
        self.say(prompts.SETUP_MODEL_SMALL_PROMPT)
        if self.ollama_refused:
            self.say(prompts.SETUP_MODEL_REMAKE_AFTER_OLLAMA)
            return
        base = self.alias_base(alias, shown, listed)
        if base is None:
            self.say(prompts.SETUP_MODEL_PROMPT_BY_HAND)
            return
        stop = prompts.SETUP_MODEL_STOP_HERS if self.own_user() else prompts.SETUP_MODEL_STOP_YOURS
        self.say(prompts.SETUP_MODEL_REMAKE.format(tag=base.tag, stop=stop))
        if self.dry_run:
            self.say(prompts.SETUP_MODEL_REMAKE_DRY_RUN.format(ask=prompts.SETUP_MODEL_REMAKE_ASK))
        elif not self.yes(prompts.SETUP_MODEL_REMAKE_ASK):
            self.say(prompts.SETUP_MODEL_REMAKE_LATER)
            return
        spec = REPO / ".local-worker.Modelfile"
        if not self.dry_run:
            spec.write_text(alias_modelfile(base.tag, context_of(str(shown.get("parameters") or ""))), encoding="utf-8")
        try:
            made = self.run([self.ollama_program, "create", "local-worker", "-f", str(spec)]).returncode == 0
        finally:
            if not self.dry_run:
                spec.unlink(missing_ok=True)
        if self.dry_run:
            return
        if not made:
            self.say(prompts.SETUP_MODEL_REMAKE_FAILED)
            return
        self.say(prompts.SETUP_MODEL_REMADE)
        digest = self.model_digest()
        if not digest:
            self.say(prompts.SETUP_MODEL_REMADE_UNLISTED)
            return
        hers = self.own_user()
        where = prompts.SETUP_MODEL_PIN_HERS if hers else prompts.SETUP_MODEL_PIN_YOURS.format(path=self.config_path)
        self.say(prompts.SETUP_MODEL_REMADE_PIN.format(digest=digest, where=where))

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
                f'Then "{prompts.SETUP_LOCK_ASK} [Y/n]": a PIN typed hidden twice, set with\n'
                f"  $ {' '.join(harden.set_pin_argv('signal-cli', '<number>'))}   (setPin with <PIN> on stdin)\n"
                f"  ({harden.LOCK_NOTE})"
            )
            return PLACEHOLDER_NUMBER
        numbers = self.registered_numbers()
        if len(numbers) == 1:  # a run that stopped after the SMS code: what yes and no do, then the question
            self.say(prompts.SETUP_KNOWN_NUMBER.format(number=numbers[0]))
            if self.yes(prompts.SETUP_KNOWN_NUMBER_ASK.format(number=numbers[0])):
                return numbers[0]
        self.say(prompts.SETUP_NUMBER.format(name=self.name))
        number = re.sub(r"[\s-]", "", self.ask(prompts.SETUP_NUMBER_ASK))  # spaces and dashes are fine (the prompt)
        if not _NUMBER.match(number):
            raise SystemExit(prompts.SETUP_NUMBER_BAD)
        done, why = self.registration("register", {"account": number}, f"register {number}", {})
        if not done and "captcha" in why.lower():
            self.say(prompts.SETUP_CAPTCHA)
            captcha = self.hidden(prompts.SETUP_CAPTCHA_ASK)
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
        self.say(prompts.SETUP_SMS_SENT.format(number=number))
        while True:  # a mistyped code or PIN can be typed again; q stops
            code = self.hidden(prompts.SETUP_SMS_ASK).replace("-", "")
            pin = "" if code.lower() == "q" else self.hidden(prompts.SETUP_OLD_PIN_ASK)
            if "q" in (code.lower(), pin.lower()):  # a stop, not a failed check
                raise SystemExit(prompts.SETUP_CODE_STOPPED)
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
        self.say(prompts.SETUP_LOCK_HOW)
        if not self.yes(prompts.SETUP_LOCK_ASK, default=True):
            self.say("  Later: scripts/harden.sh offers it again.")
            return
        shown = [*harden.set_pin_argv(self.signal_cli, number), "(setPin with <PIN> on stdin)"]
        while True:
            pin = self.hidden(prompts.SETUP_LOCK_PIN)
            if pin.lower() == "q":
                self.say("  Skipped: scripts/harden.sh offers it again.")
                return
            problem = harden.pin_problem(pin)
            if problem:
                self.say(f"  The PIN needs {problem}.")
                continue
            if self.hidden(prompts.SETUP_LOCK_AGAIN) != pin:
                self.say("  The two did not match; type it again.")
                continue
            self.say("  $ " + " ".join(shown))
            done, why = harden.set_pin(self.signal_cli, number, pin)
            pin = ""
            if done:
                self.say("✓ Registration Lock: on. Keep the PIN in your password manager.")
                return
            self.say(f"✗ Registration Lock: not set ({why}).")
            if not self.again(prompts.SETUP_LOCK_RETRY):
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
        self.say(prompts.SETUP_WORD.format(number=number, word=word, name=self.name))
        self.ask(prompts.SETUP_WORD_ASK.format(seconds=RECEIVE_SECONDS))
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
        """Female (Enter) or male, for a name that is not a suggestion: what the voice is for, said once."""
        self.say(prompts.SETUP_VOICE)
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

    def her_style(self) -> str:
        """Her built-in style while there is no soul.md: hearth (Enter) or playful, a name or its first letters; no
        terminal keeps hearth. A dry run only shows the question."""
        self.say(prompts.SETUP_STYLE.format(name=self.name))
        self.style_asked = True
        if self.dry_run:
            self.say(prompts.SETUP_STYLE_DRY_RUN)
            return self.style
        while True:
            answer = self.ask(prompts.SETUP_STYLE_ASK).lower()
            chosen = [name for name in STYLES if name.startswith(answer)] if answer else [DEFAULT_STYLE]
            if len(chosen) == 1:
                self.style = chosen[0]
                self.say(prompts.SETUP_STYLE_CHOSEN.format(style=self.style))
                return self.style
            self.say(prompts.SETUP_STYLE_UNKNOWN.format(answer=answer))

    def model_digest(self) -> str:
        """The digest Ollama lists for local-worker now (read-only); "" when it lists none."""
        return self.listed_digest("local-worker:latest")

    def listed_digest(self, name: str) -> str:
        """The digest Ollama lists for a model now (read-only; a name without a tag is :latest); "" when none."""
        names = {name, name.removesuffix(":latest")}
        try:
            listed = doctor.Probe().get_json(f"{OLLAMA}/api/tags").get("models", [])
        except (OSError, ValueError, AttributeError, TypeError):
            return ""
        for item in listed if isinstance(listed, list) else []:
            if isinstance(item, dict) and item.get("name") in names:
                digest = str(item.get("digest") or "").lower().removeprefix("sha256:")
                return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""
        return ""

    def projects(self) -> tuple[str, str] | None:
        """Projects, an extra: asked, never guessed from the Mac's folders (recommendation: no). Yes: the folder (as typed,
        ~ allowed; it must exist, and not be your home itself) and the level. A dry run only shows the question."""
        self.say(prompts.SETUP_PROJECTS.format(name=self.name))
        if self.dry_run:
            self.say(prompts.SETUP_PROJECTS_DRY_RUN)
            return None
        if not self.yes(prompts.SETUP_PROJECTS_ASK):
            return None
        where = self._projects_where()
        if where is None:
            return None
        if outside_home(where.folder, Path.home()):  # the move's share leaves it to you
            self.say(prompts.SETUP_PROJECTS_OUTSIDE.format(folder=where.folder))
        return where.typed, where.level

    def _projects_where(self) -> _Where | None:
        """The projects folder (it must exist, not be your home itself, and pass the share's rules,
        `projects_refusal`; q or no terminal: none) and the level."""
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
            elif (why := projects_refusal(folder, Path.home())) is not None:
                self.say(prompts.SETUP_PROJECTS_REFUSED.format(why=why[:1].upper() + why[1:]))
            else:
                break
        self.say(prompts.SETUP_PROJECTS_LEVELS)
        while True:
            level = self.ask(prompts.SETUP_PROJECTS_LEVEL, "summary").lower()
            if level in PROJECT_LEVELS:
                break
            self.say(prompts.SETUP_PROJECTS_BAD_LEVEL.format(answer=level))
        typed = answer if answer.startswith("~") else str(folder)
        self.say(prompts.SETUP_PROJECTS_CHOSEN.format(folder=typed, level=level))
        return _Where(typed, folder, level)

    def chords_extra(self) -> None:
        """Chords picked from the extras: [chords] added to the config (a copy of the old one kept), nothing asked.
        In her own user her config is hers alone: the line to add and the restart, nothing written. A config that
        has [chords] already, or does not load, is left alone."""
        if self.own_user():
            self.say(prompts.SETUP_CHORDS_HERS.format(edit=prompts.HER_CONFIG_EDIT, save=prompts.NANO_SAVE))
            return
        try:
            text = self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.say(prompts.SETUP_CHORDS_NO_CONFIG.format(path=self.config_path))
            return
        problem = _config_problem(text)
        if problem:  # the user's work: never changed
            self.say(prompts.SETUP_CHORDS_NOT_ADDED.format(path=self.config_path, why=problem))
            return
        if parse_config(tomllib.loads(text)).chords_enabled:
            self.say(prompts.SETUP_CHORDS_ON)
            return
        self._write_private(self.config_path, with_chords(text))
        self.say(prompts.SETUP_CHORDS_ADDED.format(path=self.config_path))

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
            outside = outside_home(where.folder, Path.home())  # share-projects.sh shares only folders in your home
            how = prompts.SETUP_PROJECTS_OUTSIDE if outside else prompts.SETUP_PROJECTS_SHARE
            self.say(
                prompts.SETUP_PROJECTS_HERS.format(
                    root=json.dumps(str(where.folder), ensure_ascii=False),
                    level=where.level,
                    share=how.format(folder=where.folder),
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
        style = self.her_style()
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
            style=style,
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
        /var/estia; the move takes your folder), else in your config folder. No soul.md: her built-in style. A re-run
        in her own user asks the style here (the config step did not run) and prints the lines to add as her."""
        if self.own_user():
            if not self.style_asked:
                self.her_style()
                self.say(
                    prompts.SETUP_STYLE_HERS_DRY_RUN
                    if self.dry_run
                    else prompts.SETUP_STYLE_HERS.format(
                        style=self.style, edit=prompts.HER_CONFIG_EDIT, save=prompts.NANO_SAVE
                    )
                )
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
            self.say(
                prompts.SETUP_PERSONALITY.format(
                    name=self.name, soul=soul, style=self.style, words=prompts.STYLE_WORDS[self.style]
                )
            )

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
        stops, before the security step and the extras. The move's script ends `SHARE_FAILED` (3) only when its last
        step, the projects share, fails after the move: she runs in her own user then, so setup says so, with the
        share's command, and goes on. Any other failure is the move's, even with her service file left in place (a
        restore that failed too)."""
        if self.own_user():
            self.say(f"✓ Always on: as her own user {ownuser.ROLE_USER}, from {ownuser.CURRENT}.")
            return True
        legacy = (estia_root() / ownuser.DAEMONS.relative_to("/") / f"{ownuser.BRIDGE}.plist").exists()
        move = str(REPO / "scripts" / "migrate-to-own-user.sh")
        self.say((prompts.SETUP_MOVE_OLDER if legacy else prompts.SETUP_MOVE).format(name=self.name))
        unkept = bool(os.environ.get("SSH_CONNECTION")) and not (os.environ.get("STY") or os.environ.get("TMUX"))
        if unkept:
            self.say(prompts.SETUP_SSH_SCREEN)  # a dropped connection would stop the move halfway
        # Enter moves her (the safe answer), except over SSH without screen, where the line above says no
        if unkept:
            ask = prompts.SETUP_MOVE_OLDER_ASK_SSH if legacy else prompts.SETUP_MOVE_ASK_SSH
        else:
            ask = prompts.SETUP_MOVE_OLDER_ASK if legacy else prompts.SETUP_MOVE_ASK
        if self.dry_run:
            self.say(f'  $ {move}   (asks "{ask} {"[y/N]" if unkept else "[Y/n]"}"; --dry-run first shows every step)')
            return True
        if not self.yes(ask, default=not unkept):
            return True
        code = self.run([move]).returncode
        if code != 0:
            if code != SHARE_FAILED or not self.own_user():
                self.say(prompts.SETUP_MOVE_FAILED.format(move=move))
                return False
            self.say(
                prompts.SETUP_SHARE_FAILED.format(
                    name=self.name, user=ownuser.ROLE_USER, share=REPO / "scripts" / "share-projects.sh"
                )
            )
        return True

    # 5b. the Mac's security around her, then the phone
    def security(self) -> None:
        harden_sh = str(REPO / "scripts" / "harden.sh")
        self.say(prompts.SETUP_SECURITY)
        self.say(prompts.SETUP_NUCLEAR)
        if self.dry_run:
            self.say(
                f'  $ {harden_sh}   (asks "{prompts.SETUP_SECURITY_ASK} [Y/n]"; {harden_sh} --dry-run shows every check)'
            )
            for line in harden.PHONE_CHECKLIST:
                self.say(line)
            return
        if self.yes(prompts.SETUP_SECURITY_ASK, default=True):
            self.run([harden_sh])  # it ends with the phone checklist
            return
        self.say(f"  Later: {harden_sh}")
        for line in harden.PHONE_CHECKLIST:
            self.say(line)

    # 6. extras
    def extras(self) -> None:
        self.say(prompts.SETUP_EXTRAS)
        for i, (name, extra) in enumerate(EXTRAS.items(), 1):  # each in three lines: what, what leaves, what it needs
            self.say(f"  {i:>2}. {name:9} {extra.title}.")
            self.say(f"{'':16}Leaves the Mac: {extra.leaves_the_mac}.")
            self.say(f"{'':16}Needs: {extra.needs}.")
        if self.dry_run:
            self.say("  (asks which ones, then shows how to add each)")
            return
        picked, unknown = parse_extras(self.ask(prompts.SETUP_EXTRAS_ASK))
        if unknown:
            self.say(f"  (not an extra: {', '.join(unknown)})")
        for name in picked:
            self.say(f"• {name}: {EXTRAS[name].how}")
            if name == "projects":
                self.projects_extra()
            elif name == "chords":
                self.chords_extra()
        if any(name not in ("projects", "chords") for name in picked):  # where each section goes, when she reads it
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
            self.say(prompts.SETUP_CHECK_HERS)
            return self.run([SUDO, str(ownuser.BIN / "estia"), "doctor"]).returncode
        findings = doctor.run(self.config_path, doctor.Probe())
        self.say(doctor.render(findings))
        return doctor.exit_code(findings)


def main(argv: list[str] | None = None) -> int:
    """Setup; Ctrl-C at any point ends it with one line on how to go on (130, as the shell reports Ctrl-C)."""
    try:
        return run_setup(argv)
    except KeyboardInterrupt:
        print(f"\n{prompts.SETUP_STOPPED}", flush=True)
        return 130


def run_setup(argv: list[str] | None = None) -> int:
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
        wizard.first_install = False
        wizard.ollama()
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
            wizard.style = cfg.personality_style
        except ConfigError as exc:
            problem = exc
    wizard.say(prompts.SETUP_OPENING.format(name=wizard.name))
    if problem is not None:
        wizard.say(f"✗ Config: {problem}")
        wizard.say(
            "  Setup stops here and changes nothing, so your config is not replaced. Fix it\n"
            "  (scripts/doctor.sh checks it), then run setup again."
        )
        return 1
    configured = cfg is not None and cfg.bot_number != PLACEHOLDER_NUMBER and PLACEHOLDER_ACI not in cfg.allowed_acis
    wizard.first_install = not configured
    wizard.ollama()
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
