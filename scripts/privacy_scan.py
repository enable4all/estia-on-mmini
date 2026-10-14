#!/usr/bin/env python3
"""Privacy check: refuse private files and private-looking values before they reach git.

Usage: privacy_scan.py [--staged] [--config PATH]

Scans every git-tracked file (default), or the staged version of added and changed files (--staged, for the pre-commit
hook). Prints one line per finding, `<path>:<line>: <rule>` (a file name: `<path>: <rule>`), then a count line. It
never prints the matched text or a config value. Exit 0 when clean, 1 on a finding, 2 on a usage or git error.

- File names that are always refused, whatever the content: databases, logs, audio, env and token files, the owner's
  notes and config, keys.
- Per line: private keys, API keys, e-mail addresses, phone numbers, UUIDs (Signal account IDs), home paths, and the
  owner's own config values as whole words (named by key; setup keys such as paths and labels, and values that also
  appear in config.example.toml, are generic).
- A line with the marker `privacy-ok` skips the content rules: give the reason (`# privacy-ok: fake test number`).
- `.privacy-scan-skip` at the repo root: glob patterns, one per line (# comments), of paths not scanned at all, for a
  private fork that tracks private notes. A pattern without `/` matches the file name at any depth; `dir/` matches all
  under it.

Standard library only (runs with `python3 -I`). Ideas from AURA's privacy scan (Apache-2.0); no code copied.
"""

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MARKER = "privacy-ok"
SKIP_FILE = ".privacy-scan-skip"
EXAMPLE_CONFIG = "config.example.toml"
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
EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![A-Za-z0-9-])"
)
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


def line_rules(line: str, owner: list[tuple[str, re.Pattern[str]]]) -> Iterator[str]:
    """The content rules one line breaks, in a fixed order, each once."""
    if PRIVATE_KEY.search(line):
        yield "private key"
    if API_KEY.search(line):
        yield "api key"
    if SECRET_VALUE.search(line):
        yield "secret value"
    if any(not _public_email(m.group(1), m.group(2)) for m in EMAIL.finditer(line)):
        yield "email address"
    if any(not _fake_phone(m.group(1)) for m in PHONE.finditer(line)):
        yield "phone number"
    if any(not _fake_uuid(m) for m in UUID.finditer(line)):
        yield "uuid"
    if any(not _placeholder_user(m.group(1)) for m in HOME_PATH.finditer(line)):
        yield "home path"
    for key, value in owner:
        if value.search(line):
            yield f"owner config value ({key})"


def scan_content(path: str, data: bytes, owner: list[tuple[str, re.Pattern[str]]]) -> Iterator[Finding]:
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


def owner_values(config: Path, example_text: str) -> list[tuple[str, re.Pattern[str]]]:
    """(key path, whole-word pattern) for each private string of 4+ characters in the owner's config: not under a
    setup key and not in config.example.toml."""
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
    return [(key, _whole_word(value)) for value, key in found.items()]


def load_owner(config: Path | None, root: Path) -> list[tuple[str, re.Pattern[str]]]:
    """The owner's private values. The default config may be missing or broken: then the rule is skipped silently."""
    try:
        example_text = (root / EXAMPLE_CONFIG).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        example_text = ""
    path = config or Path.home() / ".config" / "local-assistant" / "config.toml"
    try:
        return owner_values(path, example_text)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        if config is None:
            if (Path(os.environ.get("ESTIA_ROOT") or "/") / "var" / "estia").is_dir():  # her own user has it now
                print(
                    "privacy scan: note: your own config values were not checked: her config is in /var/estia, "
                    "readable only by _estia. --config <file> checks against a copy you can read.",
                    file=sys.stderr,
                )
            return []
        raise ScanError(f"cannot read the config {config}") from exc


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


def scan(root: Path, staged: bool, config: Path | None) -> tuple[list[Finding], int]:
    if staged:
        paths = _split_z(_git(root, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT"))
    else:
        paths = _split_z(_git(root, "ls-files", "-z"))
    patterns = load_skip_patterns(root)
    owner = load_owner(config, root)
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
    parser.add_argument(
        "--config", type=Path, help="the owner's config (default: ~/.config/local-assistant/config.toml)"
    )
    args = parser.parse_args(argv)
    try:
        root = Path(os.fsdecode(_git(None, "rev-parse", "--show-toplevel")).strip())
        found, scanned = scan(root, args.staged, args.config)
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
