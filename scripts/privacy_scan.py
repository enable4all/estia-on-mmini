#!/usr/bin/env python3
"""Privacy check: refuse private files and private-looking values before they reach git.

Usage: privacy_scan.py [--staged] [--config PATH | --values PATH]
       privacy_scan.py --hash-values CONFIG

Scans every git-tracked file (default), or the staged version of added and changed files (--staged, for the pre-commit
hook). Prints one line per finding, `<path>:<line>: <rule>` (a file name: `<path>: <rule>`), then a count line. It
never prints the matched text or a config value. Exit 0 when clean, 1 on a finding, 2 on a usage or git error.

- File names that are always refused, whatever the content: databases, logs, audio, env and token files, the owner's
  notes and config, keys.
- Per line: private keys, API keys, secret values (quoted, and the key files' own `KEY=value` lines: web.env,
  calendar.env, tapo.env, hue.env), e-mail addresses (also written %40, as in a calendar's address), phone numbers,
  UUIDs (Signal account IDs), home paths, a Google calendar's secret address (`…/private-<hex>/…`), private network
  addresses (outside the docs' own 192.168.1.0/24 and 10.0.0.0/24), and the owner's own config values as whole words
  (named by key; setup keys such as paths and labels, and values that also appear in config.example.toml, are generic).
- In her own user her config is hers (/var/estia, 0700). She gives salted hashes of its private values, never the
  values (`--hash-values`, run as her: the scan's note says how), into ~/.config/local-assistant/privacy-values.json,
  which every scan then reads; the values are matched there as runs of whole words.
- A line with the marker `privacy-ok` skips the content rules: give the reason (`# privacy-ok: fake test number`).
- `.privacy-scan-skip` at the repo root: glob patterns, one per line (# comments), of paths not scanned at all, for a
  private fork that tracks private notes. A pattern without `/` matches the file name at any depth; `dir/` matches all
  under it.

Standard library only (runs with `python3 -I`). Ideas from AURA's privacy scan (Apache-2.0); no code copied.
"""

import argparse
import fnmatch
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MARKER = "privacy-ok"
SKIP_FILE = ".privacy-scan-skip"
EXAMPLE_CONFIG = "config.example.toml"
VALUES_FILE = Path(".config") / "local-assistant" / "privacy-values.json"  # in your home: her values, hashed
HER_CONFIG = "/var/estia/.config/local-assistant/config.toml"
HASH_COMMAND = (  # private from the start (umask, the old file removed first): the salt sits beside the hashes
    f"umask 077 && mkdir -p ~/.config/local-assistant && /bin/rm -f ~/{VALUES_FILE} && "
    "/usr/bin/sudo -u _estia /usr/local/estia/current/.venv/bin/python -I "
    f"/usr/local/estia/current/scripts/privacy_scan.py --hash-values {HER_CONFIG} > ~/{VALUES_FILE}"
)
WORD = re.compile(r"\w+")
MAX_BYTES = 2 * 1024 * 1024  # bigger files: names only
BINARY_PROBE = 8192  # a NUL byte in the first 8 KB: binary, names only

# (rule, lower-case file-name patterns): refused whatever the content; no marker allows them
NAME_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("database file", ("*.db", "*.sqlite", "*.sqlite3", "*.db-wal", "*.db-shm")),
    ("log file", ("*.log",)),
    ("audio file", ("*.wav", "*.m4a", "*.mp3", "*.ogg", "*.opus", "*.aac", "*.flac", "*.caf", "*.aiff")),
    ("env file", (".env", "*.env", ".env.*")),
    ("token file", ("*tokens*.json",)),
    ("private notes", ("soul.md", "about-me.md")),
    ("owner config", ("config.toml", "owner-config-additions.toml")),
    ("key file", ("*.pem", "*.key", "*.p12", "id_rsa", "id_ed25519")),
)

PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
API_KEY = re.compile(
    r"\bsk-[A-Za-z0-9_-]{20,}"  # OpenAI-style
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}"  # GitHub tokens
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"  # AWS access key IDs
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"  # Slack
    r"|\bAIza[0-9A-Za-z_-]{35}"  # Google
)
SECRET_VALUE = re.compile(
    r"(?:api_?key|secret|token|password|passwd)\w*[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_-]{16,}[\"']", re.IGNORECASE
)
# a key file's line as written (web.env, calendar.env, tapo.env, hue.env: unquoted), also pasted into a doc or a comment;
# any other *_API_KEY, *_PASSWORD, *_SECRET or *_TOKEN only as an env line (no space around "=": not a constant in
# code); a placeholder (<your key>, $VAR, …) is no value
ENV_SECRET = re.compile(
    r"^[\s#>*-]*(?:export\s+)?"
    r"(?:(?:BRAVE_API_KEY|OLLAMA_API_KEY|GOOGLE_MAPS_API_KEY|TAPO_PASSWORD|HUE_KEY|HUE_FINGERPRINT"
    r"|CALDAV_PASSWORD\w*|CALENDAR_ICS_URL\w*)\s*=\s*|[A-Z][A-Z0-9_]*_(?:API_KEY|PASSWORD|SECRET|TOKEN)=)"
    r"[^\s\"'$<{%.…]\S{7,}"
)
EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+?)(?:@|%40)([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"
    r"(?![A-Za-z0-9-])"
)
# a Google calendar's secret iCal address: its private-<hex> part is the password
GOOGLE_ICAL = re.compile(r"calendar\.google\.com/calendar/ical/[^/\s]+/private-([0-9a-f]{8,})", re.IGNORECASE)
FAKE_HEX = ("0123456789abcdef" * 4, "fedcba9876543210" * 4)  # patterned fakes, read from the start
IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w/]|\.\d)")
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
FAKE_NETWORKS = tuple(ipaddress.ip_network(net) for net in ("192.168.1.0/24", "10.0.0.0/24"))  # the docs' home network
PHONE = re.compile(r"(?<![\w+])\+(\d{10,15})(?!\d)")  # E.164: + and 10-15 digits, no spaces
UUID = re.compile(r"\b([0-9a-f]{8})-([0-9a-f]{4})-([0-9a-f]{4})-([0-9a-f]{4})-([0-9a-f]{12})\b", re.IGNORECASE)
HOME_PATH = re.compile(r"/Users/(<[^>/\s]*>?|\$[{(]?\w*|[A-Za-z0-9._-]+)")  # <you>, $USER or a name

# Addresses at reserved names: example.com/.org/.net and their subdomains, example.* and the reserved test TLDs
RESERVED_TLDS = frozenset({"example", "test", "invalid"})
EXAMPLE_TLDS = frozenset({"com", "org", "net"})
NOREPLY_LOCALS = frozenset({"noreply", "no-reply"})
NOREPLY_DOMAIN = "users.noreply.github.com"
# Project addresses that must stay public, each with its reason. None today: security reports go through GitHub's
# private vulnerability reporting (SECURITY.md), and no licence or pyproject names an address.
PUBLIC_EMAILS: frozenset[str] = frozenset()
FAKE_PHONE_PREFIX = "3069000000"  # the project's fake range: + this prefix and two digits
# Public, well-known UUIDs that may appear (each with its reason). None today; patterned fakes pass by rule.
PUBLIC_UUIDS: frozenset[str] = frozenset()
PLACEHOLDER_USERS = frozenset({"alex", "you", "example", "shared"})  # plus <you>-style and $USER-style names
# Owner config keys whose values describe the setup, not the owner: programs and paths, addresses on this Mac (config.py
# accepts only local ones), times, labels (her name among them) and fixed choices. The docs share such values (a mailbox
# called "personal", signal-cli's Homebrew path, the name the owner gave her), so they are not checked. Every other key is private, also one added later: the bot
# number, ACIs, mail addresses and IDs, device names and addresses, home, speakers, other services.
SETUP_KEYS = frozenset(
    {
        "model",
        "name",
        "ollama_base_url",
        "signal_cli",
        "digest_time",
        "host_label",
        "health.watch_logs",
        "projects.root",
        "projects.default",
        "mail.jev_url",
        "mail.digest_time",
        "mail.accounts.name",
        "voice.url",
        "voice.voice",
        "tapo.devices.kind",
        "tapo.devices.rule",
        "tapo.devices.brand",
        "calls.signal_cli",
        "calls.java_home",
        "calls.tunnel",
        "calls.socket_dir",
    }
)
# Tables keyed by the owner's own names (speaker = address): a name counts as a value, and a finding shows "*" for it
NAMED_TABLES = frozenset({"spotify.bose"})


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int  # 0: the file name
    rule: str

    def __str__(self) -> str:
        return f"{self.path}: {self.rule}" if self.line == 0 else f"{self.path}:{self.line}: {self.rule}"


class ScanError(Exception):
    """A git or usage problem: exit 2."""


def _git(root: Path | None, *args: str) -> bytes:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    except OSError as exc:
        raise ScanError(f"cannot run git: {exc.strerror}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ScanError(f"git {args[0]} failed: {detail[0] if detail else result.returncode}")
    return result.stdout


def _split_z(raw: bytes) -> list[str]:
    return list(dict.fromkeys(p for p in os.fsdecode(raw).split("\0") if p))


def name_rule(path: str) -> str | None:
    name = PurePosixPath(path).name.lower()
    for rule, patterns in NAME_RULES:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
            return rule
    return None


def load_skip_patterns(root: Path) -> list[str]:
    try:
        text = (root / SKIP_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise ScanError(f"cannot read {SKIP_FILE}") from exc
    patterns = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(line + "*" if line.endswith("/") else line)
    return patterns


def skipped(path: str, patterns: list[str]) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch.fnmatchcase(path, p) or ("/" not in p and fnmatch.fnmatchcase(name, p)) for p in patterns)


def _public_email(local: str, domain: str) -> bool:
    local, domain = local.lower(), domain.lower()
    labels = domain.split(".")
    return (
        labels[0] == "example"
        or labels[-1] in RESERVED_TLDS
        or (labels[-2] == "example" and labels[-1] in EXAMPLE_TLDS)
        or domain == NOREPLY_DOMAIN
        or local in NOREPLY_LOCALS
        or f"{local}@{domain}" in PUBLIC_EMAILS
    )


def _fake_phone(digits: str) -> bool:
    if digits.startswith(FAKE_PHONE_PREFIX) and len(digits) == len(FAKE_PHONE_PREFIX) + 2:
        return True
    return any(len(set(digits[cc:])) == 1 for cc in (1, 2, 3))  # one repeated digit after the country code


def _fake_uuid(match: re.Match[str]) -> bool:
    if match.group(0).lower() in PUBLIC_UUIDS:
        return True
    g1, g2, g3, g4, g5 = (g.lower() for g in match.groups())
    # every group one repeated hex digit, leaving out the version and variant nibbles
    return all(len(set(group)) == 1 for group in (g1, g2, g3[1:], g4[1:], g5))


def _placeholder_user(name: str) -> bool:
    return name.lower() in PLACEHOLDER_USERS or name.startswith(("<", "$"))


def _fake_hex(digits: str) -> bool:
    """A patterned fake: 0123456789abcdef or fedcba9876543210 from the start, or one repeated digit."""
    digits = digits.lower()
    return any(fake.startswith(digits) for fake in FAKE_HEX) or len(set(digits)) == 1


def _private_address(text: str) -> bool:
    """A private network address (RFC 1918) outside the docs' own fake ranges."""
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return any(address in net for net in PRIVATE_NETWORKS) and not any(address in net for net in FAKE_NETWORKS)


# the owner's values: a line in, the config keys whose values it holds out
Owner = Callable[[str], list[str]]


def _patterns(found: dict[str, str]) -> Owner:
    """Each value as a whole-word pattern."""
    patterns = [(key, _whole_word(value)) for value, key in found.items()]
    return lambda line: [key for key, pattern in patterns if pattern.search(line)]


def _digest(salt: str, words: list[str]) -> str:
    return hashlib.sha256("\0".join([salt, *words]).encode("utf-8")).hexdigest()


def _hashed(data: object) -> Owner:
    """Salted hashes of each value's words (`--hash-values`): a line's runs of whole words are hashed and looked up, so
    10.0.0.4 is not in 10.0.0.41 and a name not in a longer one. ValueError for a file of another shape."""
    if not isinstance(data, dict) or not isinstance(data.get("salt"), str) or not isinstance(data.get("values"), list):
        raise ValueError("not a values file")
    salt: str = data["salt"]
    tables: dict[int, dict[str, str]] = {}
    for entry in data["values"]:
        if not isinstance(entry, dict):
            raise ValueError("not a values file")
        key, words, digest = entry.get("key"), entry.get("words"), entry.get("hash")
        if not (isinstance(key, str) and isinstance(words, int) and words >= 1 and isinstance(digest, str)):
            raise ValueError("not a values file")
        tables.setdefault(words, {})[digest] = key

    def check(line: str) -> list[str]:
        words = WORD.findall(line)
        keys: list[str] = []
        for size, table in tables.items():
            for start in range(len(words) - size + 1):
                key = table.get(_digest(salt, words[start : start + size]))
                if key is not None and key not in keys:
                    keys.append(key)
        return keys

    return check


def hash_values(config: Path, example_text: str) -> dict[str, object]:
    """Her config's private values as salted hashes of their words, with their keys: never a value."""
    salt = secrets.token_hex(16)
    entries = []
    for value, key in private_values(config, example_text).items():
        words = WORD.findall(value)
        if words:
            entries.append({"key": key, "words": len(words), "hash": _digest(salt, words)})
    return {"salt": salt, "values": entries}


def line_rules(line: str, owner: Owner) -> Iterator[str]:
    """The content rules one line breaks, in a fixed order, each once."""
    if PRIVATE_KEY.search(line):
        yield "private key"
    if API_KEY.search(line):
        yield "api key"
    if SECRET_VALUE.search(line) or ENV_SECRET.search(line):
        yield "secret value"
    if any(not _public_email(m.group(1), m.group(2)) for m in EMAIL.finditer(line)):
        yield "email address"
    if any(not _fake_phone(m.group(1)) for m in PHONE.finditer(line)):
        yield "phone number"
    if any(not _fake_uuid(m) for m in UUID.finditer(line)):
        yield "uuid"
    if any(not _placeholder_user(m.group(1)) for m in HOME_PATH.finditer(line)):
        yield "home path"
    if any(not _fake_hex(m.group(1)) for m in GOOGLE_ICAL.finditer(line)):
        yield "calendar address"
    if any(_private_address(m.group(1)) for m in IPV4.finditer(line)):
        yield "private address"
    for key in owner(line):
        yield f"owner config value ({key})"


def scan_content(path: str, data: bytes, owner: Owner) -> Iterator[Finding]:
    if len(data) > MAX_BYTES or b"\0" in data[:BINARY_PROBE]:
        return
    for number, line in enumerate(data.decode("utf-8", "replace").split("\n"), start=1):
        if MARKER in line:
            continue
        for rule in line_rules(line, owner):
            yield Finding(path, number, rule)


def _whole_word(value: str) -> re.Pattern[str]:
    """The value, but not inside a longer word or number: 10.0.0.4 is not in 10.0.0.41, nor a name in a longer one."""
    head = r"(?<!\w)" if re.match(r"\w", value[0]) else ""
    tail = r"(?!\w)" if re.match(r"\w", value[-1]) else ""
    return re.compile(head + re.escape(value) + tail)


def private_values(config: Path, example_text: str) -> dict[str, str]:
    """{value: key path} for each private string of 4+ characters in the owner's config: not under a setup key and not
    in config.example.toml."""
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    found: dict[str, str] = {}

    def add(raw: str, keys: tuple[str, ...]) -> None:
        value, key = raw.strip(), ".".join(keys)
        if len(value) >= 4 and key not in SETUP_KEYS and value not in example_text:
            found.setdefault(value, key)

    def walk(node: object, keys: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            named = ".".join(keys) in NAMED_TABLES
            for key, child in node.items():
                if named:
                    add(str(key), (*keys, "*"))
                walk(child, (*keys, "*" if named else str(key)))
        elif isinstance(node, list):
            for child in node:
                walk(child, keys)
        elif isinstance(node, str):
            add(node, keys)

    walk(data, ())
    return found


def _example_text(root: Path) -> str:
    try:
        return (root / EXAMPLE_CONFIG).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _load_values(path: Path) -> Owner:
    """Her hashed values (ValueError or OSError when they cannot be read)."""
    return _hashed(json.loads(path.read_text(encoding="utf-8")))


def load_owner(config: Path | None, root: Path, values: Path | None = None) -> Owner:
    """The owner's private values: from `values` (hashed), the config, or by default your config, else her hashed
    values in your home (VALUES_FILE). Neither readable: the rule is skipped, with a note in her own user."""
    if values is not None:
        try:
            return _load_values(values)
        except (OSError, ValueError) as exc:
            raise ScanError(f"cannot read the values {values}") from exc
    path = config or Path.home() / ".config" / "local-assistant" / "config.toml"
    try:
        return _patterns(private_values(path, _example_text(root)))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        if config is not None:
            raise ScanError(f"cannot read the config {config}") from exc
    try:
        return _load_values(Path.home() / VALUES_FILE)
    except (OSError, ValueError):
        pass
    if (Path(os.environ.get("ESTIA_ROOT") or "/") / "var" / "estia").is_dir():  # her own user has it now
        print(
            "privacy scan: note: your own config values were not checked: her config is in /var/estia, readable only "
            "by _estia. Have her give their salted hashes (never the values), now and after each change to her "
            f"config: {HASH_COMMAND}. Or check against a copy you can read: --config <file>.",
            file=sys.stderr,
        )
    return lambda line: []


def _working_copy(root: Path, path: str) -> bytes | None:
    """A tracked file as it is on disk; a symlink's own text (never followed); None when it is gone."""
    full = root / path
    try:
        if full.is_symlink():
            return os.fsencode(os.readlink(full))
        if not full.is_file():
            return None
        if full.stat().st_size > MAX_BYTES:
            return b""  # names only
        return full.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ScanError(f"cannot read {path}") from exc


def scan(root: Path, staged: bool, config: Path | None, values: Path | None = None) -> tuple[list[Finding], int]:
    if staged:
        paths = _split_z(_git(root, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT"))
    else:
        paths = _split_z(_git(root, "ls-files", "-z"))
    patterns = load_skip_patterns(root)
    owner = load_owner(config, root, values)
    found: list[Finding] = []
    scanned = 0
    for path in paths:
        if skipped(path, patterns):
            continue
        data = _git(root, "cat-file", "blob", f":{path}") if staged else _working_copy(root, path)
        if data is None:
            continue
        scanned += 1
        rule = name_rule(path)
        if rule is not None:
            found.append(Finding(path, 0, rule))
        found.extend(scan_content(path, data, owner))
    return sorted(set(found)), scanned


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse private files and private-looking values in the repo.")
    parser.add_argument("--staged", action="store_true", help="scan the staged version of added and changed files")
    owner = parser.add_mutually_exclusive_group()
    owner.add_argument(
        "--config", type=Path, help="the owner's config (default: ~/.config/local-assistant/config.toml)"
    )
    owner.add_argument("--values", type=Path, help=f"her hashed values (default, without your config: ~/{VALUES_FILE})")
    owner.add_argument("--hash-values", type=Path, metavar="CONFIG", help="print a config's values, hashed; no scan")
    args = parser.parse_args(argv)
    if args.hash_values is not None:  # as her, from the release: no git, the example config beside this script
        try:
            hashed = hash_values(args.hash_values, _example_text(Path(__file__).resolve().parents[1]))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            print(f"privacy scan: cannot read the config {args.hash_values}", file=sys.stderr)
            return 2
        print(json.dumps(hashed))
        return 0
    try:
        root = Path(os.fsdecode(_git(None, "rev-parse", "--show-toplevel")).strip())
        found, scanned = scan(root, args.staged, args.config, args.values)
    except ScanError as exc:
        print(f"privacy scan: {exc}", file=sys.stderr)
        return 2
    for finding in found:
        print(finding)
    if found:
        print(
            f"privacy scan: {_plural(len(found), 'finding')}. Remove the value, or mark a reviewed line with "
            f'"{MARKER}: <reason>"; file names cannot be marked.'
        )
        return 1
    print(f"privacy scan: clean ({_plural(scanned, 'file')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
