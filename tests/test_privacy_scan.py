"""The privacy check (scripts/privacy_scan.py) and its pre-commit hook: private files and private-looking values are
refused before they reach git, and the scan's own output never repeats what it found."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCAN = ROOT / "scripts" / "privacy_scan.py"
HOOK = ROOT / ".githooks" / "pre-commit"
VERIFY = ROOT / "scripts" / "verify.sh"
GIT_ID = ("-c", "user.name=Alex Example", "-c", "user.email=alex@example.com")

# Fake secrets are glued together from pieces, so this file passes the scan (and gitleaks) itself.
AT = "@"
AWS_KEY = "AK" + "IA" + "Q7" * 8
PRIVATE_EMAIL = "alex.smith" + AT + "gmail.com"


def clean_env(home: Path) -> dict[str, str]:
    """A temporary HOME (no real owner config, no global git config) and no GIT_* variables from a caller."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "XDG_CONFIG_HOME"}
    env["HOME"] = str(home)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["ESTIA_ROOT"] = str(home / "no-system")  # never the Mac's own /var/estia
    return env


class Repo:
    """A temporary git repository, with a temporary HOME next to it."""

    def __init__(self, base: Path) -> None:
        self.root = base / "repo"
        self.home = base / "home"
        self.root.mkdir()
        self.home.mkdir()
        self.env = clean_env(self.home)
        assert self.git("init", "-q", "-b", "main").returncode == 0

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *GIT_ID, *args], cwd=self.root, env=self.env, capture_output=True, text=True, check=False
        )

    def write(self, rel: str, content: str | bytes) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def add(self, rel: str, content: str | bytes) -> None:
        self.write(rel, content)
        result = self.git("add", "--", rel)
        assert result.returncode == 0, result.stderr

    def commit(self, message: str) -> subprocess.CompletedProcess[str]:
        return self.git("commit", "-q", "-m", message)

    def scan(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", str(SCAN), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )


def findings(result: subprocess.CompletedProcess[str]) -> list[str]:
    """The finding lines: everything but the final count line."""
    lines = result.stdout.splitlines()
    assert lines, result.stderr
    assert lines[-1].startswith("privacy scan: "), result.stdout
    return lines[:-1]


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def test_a_clean_repo_passes(repo: Repo) -> None:
    repo.add("README.md", "Hello from Alex Example, " + "alex" + AT + "example.com, +306900000000.\n")
    result = repo.scan()
    assert result.returncode == 0, result.stdout
    assert findings(result) == []
    assert result.stdout.splitlines()[-1] == "privacy scan: clean (1 file)"


REFUSED_NAMES = [
    ("memory.db", "database file"),
    ("data/notes.sqlite", "database file"),
    ("data/old.sqlite3", "database file"),
    ("memory.db-wal", "database file"),
    ("memory.db-shm", "database file"),
    ("logs/bridge.log", "log file"),
    *[
        (f"voice/note.{ext}", "audio file")
        for ext in ("wav", "m4a", "mp3", "ogg", "opus", "aac", "flac", "caf", "aiff")
    ],
    (".env", "env file"),
    ("web.env", "env file"),
    ("app/.env.local", "env file"),
    ("mail-tokens.json", "token file"),
    ("backup/spotify_tokens_old.json", "token file"),
    ("soul.md", "private notes"),
    ("notes/Soul.MD", "private notes"),
    ("notes/about-me.md", "private notes"),
    ("config.toml", "owner config"),
    ("old/config.toml", "owner config"),
    ("OWNER-CONFIG-ADDITIONS.toml", "owner config"),
    ("certs/cert.pem", "key file"),
    ("certs/server.key", "key file"),
    ("certs/store.p12", "key file"),
    ("id_rsa", "key file"),
    ("keys/id_ed25519", "key file"),
]


def test_private_file_names_are_refused_even_with_the_marker(repo: Repo) -> None:
    for name, _ in REFUSED_NAMES:
        repo.add(name, "nothing here  # privacy-ok: no marker can allow a file name\n")
    result = repo.scan()
    assert result.returncode == 1
    assert findings(result) == [f"{name}: {rule}" for name, rule in sorted(REFUSED_NAMES)]


def test_public_look_alike_names_pass(repo: Repo) -> None:
    for name in (
        "config.example.toml",
        "soul.example.md",
        "docs/about.md",
        "src/dbtools.py",
        "tokens.md",
        "env.example",
    ):
        repo.add(name, "fine\n")
    result = repo.scan()
    assert result.returncode == 0, result.stdout


LEAKS = [
    ("private key", "-----BEGIN " + "PRIVATE KEY-----", "BEGIN"),
    ("private key", "-----BEGIN RSA " + "PRIVATE KEY-----", "RSA"),
    ("private key", "-----BEGIN OPENSSH " + "PRIVATE KEY-----", "OPENSSH"),
    ("api key", "OPENAI=" + "sk-" + "proj-" + "Ab3dE" * 8, "Ab3dE"),
    ("api key", "token " + "gh" + "p_" + "aB3" * 12, "aB3"),
    ("api key", "gh" + "o_" + "Zx9" * 12, "Zx9"),
    ("api key", "github" + "_pat_" + "11AB" + "cD4" * 9, "cD4"),
    ("api key", "aws " + AWS_KEY, "Q7Q7"),
    ("api key", "xox" + "b-" + "1234567890-" + "aBcDeF" * 4, "aBcDeF"),
    ("api key", "maps " + "AI" + "za" + "Sy" + "k9" * 16 + "x", "k9k9"),
    ("secret value", 'api_key = "' + "a1B2" * 5 + '"', "a1B2"),
    ("secret value", "password: '" + "pw" * 9 + "'", "pwpw"),
    ("secret value", '"refresh_token": "' + "rT5" * 8 + '"', "rT5"),
    ("secret value", 'CLIENT_SECRET="' + "Cs_" * 6 + '"', "Cs_"),
    ("secret value", "BRAVE_API_KEY=" + "BSAq7" * 5, "BSAq7"),  # the env files' own lines, unquoted
    ("secret value", "export OLLAMA_API_KEY=" + "0f9e" * 8 + ".x" * 4, "0f9e"),
    ("secret value", "TAPO_PASSWORD=" + "Hunt" + "er2-made-up", "Hunter2"),
    ("secret value", "  HUE_KEY = " + "kY7" * 10, "kY7"),
    ("secret value", "CALDAV_PASSWORD_FAMILY=" + "abcd-efgh-" + "ijkl-mnop", "ijkl"),
    ("secret value", "# CALENDAR_ICS_URL_SCHOOL=" + "https://calendar.example/feed/" + "Zq" * 9, "ZqZq"),
    ("secret value", "MY_SERVICE_TOKEN=" + "tk_" + "Lm8" * 6, "Lm8"),
    ("email address", "write to " + PRIVATE_EMAIL, "alex.smith"),
    ("email address", "Bob" + AT + "Work-Mail.nl", "Work-Mail"),
    ("email address", "ical/" + "alex.smith" + "%40" + "gmail.com/public/basic.ics", "alex.smith"),  # encoded
    (
        "calendar address",
        "https://calendar.google.com/calendar/ical/x/" + "private-" + "9f3b2c71a0d4e58b" * 2 + "/basic.ics",
        "9f3b2c71",
    ),
    ("phone number", "call " + "+31" + "612345678", "612345678"),
    ("phone number", "tel:" + "+44" + "7700900123", "7700900123"),
    ("uuid", "aci = " + "0a1b2c3d" + "-4e5f-4a6b-8c7d-" + "9e0f1a2b3c4d", "0a1b2c3d"),
    ("uuid", "ACI " + "0A1B2C3D" + "-4E5F-4A6B-8C7D-" + "9E0F1A2B3C4D", "0A1B2C3D"),
    ("home path", "cd " + "/Users/" + "bob" + "/dev", "/bob"),
    ("home path", "'" + "/Users/" + "alexsmith" + "'", "alexsmith"),
    ("home path", "<string>" + "/Users/" + "bobby" + "</string>", "bobby"),
    ("private address", "ping " + "192.168" + ".178.23.", "178.23"),
    ("private address", 'host = "' + "172.20" + '.1.5"', "172.20"),
    ("private address", "lamp at " + "10.1" + ".2.3", "10.1.2"),
]


def test_content_rules_find_each_leak_without_repeating_it(repo: Repo) -> None:
    repo.add("notes.txt", "first line\n" + "\n".join(line for _, line, _ in LEAKS) + "\n")
    result = repo.scan()
    assert result.returncode == 1
    assert findings(result) == [f"notes.txt:{n}: {rule}" for n, (rule, _, _) in enumerate(LEAKS, start=2)]
    for _, _, secret in LEAKS:
        assert secret not in result.stdout + result.stderr


FINE_LINES = [
    "mail " + "info" + AT + "example.com and " + "anna" + AT + "Example.COM",
    "x" + AT + "mail.example.org, " + "news" + AT + "shop.example, " + "contact" + AT + "example.app",
    "info" + AT + "example.onmicrosoft.com",
    "user:secret" + AT + "site.test and " + "x" + AT + "host.invalid",
    "12345+alex-example" + AT + "users.noreply.github.com",
    "noreply" + AT + "bank.nl, " + "No-Reply" + AT + "service.com",
    "@pytest.mark.parametrize and page['@odata.nextLink']",
    "+306900000000 and +306900000042",
    "+300000000000 and +4411111111111",
    "call +31 6 1234 5678 or +31612, and an id 12345678901234567",
    "11111111-2222-4333-8444-555555555555",
    "00000000-0000-0000-0000-000000000000 and AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE",
    "/Users/alex/dev, /Users/you, /Users/example, /Users/Shared/x",
    "/Users/<you>/dev and /Users/$USER/dev and /Users/${USER} and /Users/",
    "<string>/Users/alex</string> and (/Users/you)",
    "-----BEGIN PUBLIC KEY----- -----BEGIN CERTIFICATE-----",
    "risk-assessment-for-the-whole-long-project-plan",
    'token = os.environ["TOKEN"]; password = "short"; api_key = "{key}"',
    "sha256:5981c011b306781ce561e18e14230a14524a3d8109b97553666c942c18f31a96",
    "BRAVE_API_KEY=<your key>",
    "TAPO_PASSWORD=${TAPO_PASSWORD}",
    "HUE_KEY=…",
    "# BRAVE_API_KEY=..., OLLAMA_API_KEY=..., GOOGLE_MAPS_API_KEY=...",
    '    keys.write_text("OLLAMA_API_KEY=secret-value-123")',
    'LOOKED_KEY = "documents:looked_at"',
    "CALENDAR_SAVED_AT_FAMILY=1760000000",
    "https://calendar.google.com/calendar/ical/alex%40example.com/private-0123456789abcdef/basic.ics",
    "https://calendar.google.com/calendar/ical/x/private-" + "0" * 32 + "/basic.ics",
    "192.168.1.20 and 10.0.0.4, the docs' home network; 127.0.0.1, 8.8.8.8 and 203.0.113.9 are not private ones",
    'networks ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"); version 1.10.20.3; 10.0.0.255',
]


def test_fake_and_public_values_pass(repo: Repo) -> None:
    repo.add("notes.txt", "\n".join(FINE_LINES) + "\n")
    result = repo.scan()
    assert result.returncode == 0, result.stdout


def test_the_marker_skips_one_reviewed_line(repo: Repo) -> None:
    phone = "+31" + "612345678"
    repo.add("notes.txt", f"{phone}  # privacy-ok: a documented example\n{phone}\n")
    result = repo.scan()
    assert result.returncode == 1
    assert findings(result) == ["notes.txt:2: phone number"]


def test_binary_files_are_not_read(repo: Repo) -> None:
    repo.add("logo.png", b"\x89PNG\r\n\x1a\n\0\0" + PRIVATE_EMAIL.encode())
    result = repo.scan()
    assert result.returncode == 0, result.stdout


def test_big_files_are_not_read_but_their_names_still_count(repo: Repo) -> None:
    big = ("x" * 99 + "\n") * 21_000 + PRIVATE_EMAIL + "\n"
    repo.add("big.txt", big)
    repo.add("big.log", big)
    result = repo.scan()
    assert result.returncode == 1
    assert findings(result) == ["big.log: log file"]


def test_symlinks_are_not_followed(repo: Repo, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text(PRIVATE_EMAIL + "\n", encoding="utf-8")
    (repo.root / "link.txt").symlink_to(outside)
    assert repo.git("add", "link.txt").returncode == 0
    result = repo.scan()
    assert result.returncode == 0, result.stdout


def test_the_skip_file_leaves_matching_paths_out(repo: Repo) -> None:
    repo.add(".privacy-scan-skip", "# private notes in a fork\nnotes/\nprivate-*.md\n\nabout-me.md\n")
    repo.add("notes/today.md", PRIVATE_EMAIL)
    repo.add("notes/deep/soul.md", "style")
    repo.add("private-plan.md", PRIVATE_EMAIL)
    repo.add("docs/about-me.md", "me")
    repo.add("public.md", PRIVATE_EMAIL)
    result = repo.scan()
    assert result.returncode == 1
    assert findings(result) == ["public.md:1: email address"]


OWNER_CONFIG = """\
model = "local-worker"
[owner]
name = "Zephyrine Q"
[signal]
contacts = ["Mirabel Hollow", "x"]
[[tapo.devices]]
name = "Brass Lamp Nine"
dim = true
level = 30
"""


def test_owner_config_values_are_found_by_key_and_never_shown(repo: Repo, tmp_path: Path) -> None:
    config = tmp_path / "owner.toml"
    config.write_text(OWNER_CONFIG, encoding="utf-8")
    repo.add("config.example.toml", 'model = "local-worker"\n')
    repo.add(
        "notes.txt",
        "Hi Zephyrine Q\nturn on Brass Lamp Nine\nask Mirabel Hollow\nlocal-worker is generic\nx true 30\n"
        "Zephyrine Q again  # privacy-ok: a reviewed line\n",
    )
    result = repo.scan("--config", str(config))
    assert result.returncode == 1
    assert findings(result) == [
        "notes.txt:1: owner config value (owner.name)",
        "notes.txt:2: owner config value (tapo.devices.name)",
        "notes.txt:3: owner config value (signal.contacts)",
    ]
    for value in ("Zephyrine", "Brass Lamp", "Mirabel"):
        assert value not in result.stdout + result.stderr


SETUP_AND_PRIVATE_CONFIG = """\
bot_number = "+306900000017"
name = "Marigold"
host_label = "the Mac mini"
signal_cli = "/opt/homebrew/bin/signal-cli"
digest_time = "08:30"
[health]
watch_logs = ["~/Library/Logs/local-assistant"]
[projects]
root = "~/dev"
[mail]
jev_url = "http://127.0.0.1:8199"
[[mail.accounts]]
name = "personal"
address = "zephyrine.q@corp.example"
[voice]
url = "http://127.0.0.1:8151"
voice = "bf_emma"
[[tapo.devices]]
name = "Brass Lamp Nine"
host = "10.0.0.4"
kind = "light"
rule = "ask first"
brand = "kasa"
[calls]
java_home = "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"
[maps]
home = "1 Example Street, Exampletown"
[spotify]
default_device = "Hallway Cube"
"""


def test_setup_values_in_the_owner_config_are_not_private(repo: Repo, tmp_path: Path) -> None:
    """Programs, paths, local addresses, times, labels (her name too) and choices are setup, not the owner: only the
    rest is flagged."""
    config = tmp_path / "owner.toml"
    config.write_text(SETUP_AND_PRIVATE_CONFIG, encoding="utf-8")
    setup = (
        "the Mac mini is fine\n"
        "Marigold says hi\n"
        "/opt/homebrew/bin/signal-cli -a <bot-number> receive\n"
        "the digest at 08:30\n"
        "logs in ~/Library/Logs/local-assistant\n"
        "projects in ~/dev\n"
        "Jev at http://127.0.0.1:8199\n"
        "the personal mailbox\n"
        "voice service at http://127.0.0.1:8151, voice bf_emma\n"
        "a light, rule ask first\n"
        "the kasa brand\n"
        "java in /opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home\n"
    )
    private = (
        "the bot is +306900000017\n"
        "mail from zephyrine.q" + AT + "corp.example\n"
        "turn on Brass Lamp Nine\n"
        "ping 10.0.0.4\n"
        "home is 1 Example Street, Exampletown\n"
        "play on Hallway Cube\n"
    )
    repo.add("notes.txt", setup + private)
    result = repo.scan("--config", str(config))
    assert result.returncode == 1
    first = len(setup.splitlines()) + 1
    keys = ["bot_number", "mail.accounts.address", "tapo.devices.name", "tapo.devices.host", "maps.home"]
    keys.append("spotify.default_device")
    assert findings(result) == [f"notes.txt:{n}: owner config value ({key})" for n, key in enumerate(keys, start=first)]


def test_owner_values_count_only_as_whole_words(repo: Repo, tmp_path: Path) -> None:
    config = tmp_path / "owner.toml"
    config.write_text('[[tapo.devices]]\nname = "Brass Lamp"\nhost = "10.0.0.4"\n', encoding="utf-8")
    repo.add(
        "notes.txt",
        "10.0.0.41 and 110.0.0.4 are other hosts\nBrass Lamps are many\nping 10.0.0.4.\n(Brass Lamp)\nhost=10.0.0.4\n",
    )
    result = repo.scan("--config", str(config))
    assert result.returncode == 1
    assert findings(result) == [
        "notes.txt:3: owner config value (tapo.devices.host)",
        "notes.txt:4: owner config value (tapo.devices.name)",
        "notes.txt:5: owner config value (tapo.devices.host)",
    ]


def test_speaker_names_are_private_and_never_shown_as_a_key(repo: Repo, tmp_path: Path) -> None:
    """[spotify.bose] maps the owner's speaker names to addresses: the names are values too, and the key path hides them."""
    config = tmp_path / "owner.toml"
    config.write_text('[spotify.bose]\n"Hallway Cube" = "10.0.0.41"\n', encoding="utf-8")
    repo.add("notes.txt", "wake 10.0.0.41\nplay on Hallway Cube\n")
    result = repo.scan("--config", str(config))
    assert result.returncode == 1
    assert findings(result) == [
        "notes.txt:1: owner config value (spotify.bose.*)",
        "notes.txt:2: owner config value (spotify.bose.*)",
    ]
    assert "Hallway" not in result.stdout + result.stderr


def test_the_default_config_is_the_owners_config_in_home(repo: Repo) -> None:
    config = repo.home / ".config" / "local-assistant" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('[owner]\nname = "Zephyrine Q"\n', encoding="utf-8")
    repo.add("notes.txt", "Hi Zephyrine Q\n")
    result = repo.scan()
    assert result.returncode == 1
    assert findings(result) == ["notes.txt:1: owner config value (owner.name)"]


def test_a_broken_default_config_is_skipped_silently(repo: Repo) -> None:
    config = repo.home / ".config" / "local-assistant" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("this is [not toml\n", encoding="utf-8")
    repo.add("notes.txt", "fine\n")
    result = repo.scan()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""


def test_an_explicit_config_that_cannot_be_read_is_an_error(repo: Repo, tmp_path: Path) -> None:
    repo.add("notes.txt", "fine\n")
    result = repo.scan("--config", str(tmp_path / "missing.toml"))
    assert result.returncode == 2


def test_staged_mode_scans_the_staged_version_of_changed_files_only(repo: Repo) -> None:
    repo.add("old.txt", PRIVATE_EMAIL + "\n")
    assert repo.commit("old").returncode == 0
    repo.add("new.txt", "fine\n" + PRIVATE_EMAIL + "\n")
    repo.write("new.txt", "fine\n")  # the working copy is clean again; the staged version is not
    repo.add("memory.db", "x")
    result = repo.scan("--staged")
    assert result.returncode == 1
    assert findings(result) == ["memory.db: database file", "new.txt:2: email address"]
    assert repo.scan().returncode == 1  # the whole-repo scan still sees old.txt
    assert "old.txt" not in result.stdout


def test_staged_mode_ignores_deletions_and_unstaged_changes(repo: Repo) -> None:
    repo.add("leak.txt", PRIVATE_EMAIL + "\n")
    repo.add("a.txt", "fine\n")
    assert repo.commit("first").returncode == 0
    assert repo.git("rm", "-q", "leak.txt").returncode == 0
    repo.write("a.txt", PRIVATE_EMAIL + "\n")
    result = repo.scan("--staged")
    assert result.returncode == 0, result.stdout
    assert result.stdout.splitlines()[-1] == "privacy scan: clean (0 files)"


def test_staged_mode_scans_a_symlink_that_became_a_file(repo: Repo) -> None:
    (repo.root / "data.txt").symlink_to("elsewhere.txt")
    assert repo.git("add", "data.txt").returncode == 0
    assert repo.commit("link").returncode == 0
    (repo.root / "data.txt").unlink()
    repo.add("data.txt", PRIVATE_EMAIL + "\n")
    result = repo.scan("--staged")
    assert result.returncode == 1
    assert findings(result) == ["data.txt:1: email address"]


def test_outside_a_git_repo_is_an_error(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    env = clean_env(tmp_path)
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-I", str(SCAN)], cwd=folder, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 2


def test_an_unknown_option_is_a_usage_error(repo: Repo) -> None:
    assert repo.scan("--everything").returncode == 2


def hook_repo(tmp_path: Path) -> Repo:
    """A temp repo using a copy of .githooks (core.hooksPath), with the scanner in place and python3 on PATH."""
    repo = Repo(tmp_path)
    shutil.copytree(HOOK.parent, repo.root / ".githooks")
    (repo.root / "scripts").mkdir()
    shutil.copy2(SCAN, repo.root / "scripts" / "privacy_scan.py")
    repo.env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"])
    assert repo.git("config", "core.hooksPath", ".githooks").returncode == 0
    return repo


def test_the_hook_is_an_executable_posix_sh_script() -> None:
    assert HOOK.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert os.access(HOOK, os.X_OK)
    assert subprocess.run(["sh", "-n", str(HOOK)], check=False).returncode == 0


def test_the_hook_stops_a_commit_with_a_fake_key(tmp_path: Path) -> None:
    repo = hook_repo(tmp_path)
    repo.add("settings.txt", "aws " + AWS_KEY + "\n")
    result = repo.git("commit", "-m", "add settings")
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "settings.txt:1: api key" in output
    assert AWS_KEY not in output
    assert repo.git("rev-parse", "--verify", "-q", "HEAD").returncode != 0  # nothing was committed


def test_the_hook_lets_a_clean_commit_through(tmp_path: Path) -> None:
    repo = hook_repo(tmp_path)
    repo.add("notes.txt", "hello from Alex Example, +306900000000\n")
    result = repo.git("commit", "-m", "add notes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(repo.git("log", "--oneline").stdout.splitlines()) == 1


def test_verify_runs_the_privacy_scan_first() -> None:
    lines = VERIFY.read_text(encoding="utf-8").splitlines()
    commands = [ln for ln in lines if ln.strip() and not ln.startswith(("#", "set ", "cd "))]
    assert commands[0] == "uv run python scripts/privacy_scan.py"


def test_this_repo_passes_its_own_scan(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(SCAN)],
        cwd=ROOT,
        env=clean_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_this_repo_passes_its_own_scan_with_a_realistic_owner_config(tmp_path: Path) -> None:
    """An owner config like a real one: setup values the docs share, private values (glued, so no file holds them)."""
    private = {
        "bot": "+30690" + "0000088",
        "aci": "7" * 8 + "-1234-4abc-8def-" + "0123456789ab",
        "address": "alex.q" + AT + "mail.example",
        "device": "Quiet" + " Owl Lamp",
        "host": "10.9.8" + ".77",
    }
    config = tmp_path / "owner.toml"
    config.write_text(
        f'bot_number = "{private["bot"]}"\n'
        f'allowed_acis = ["{private["aci"]}"]\n'
        'model = "local-worker"\n'
        'signal_cli = "/opt/homebrew/bin/signal-cli"\n'
        'host_label = "the Mac mini"\n'
        'name = "Tiffany"\n'  # the owner's own Estia: the README says her name
        "[health]\n"
        'watch_logs = ["~/Library/Logs/local-assistant"]\n'
        "[mail]\n"
        'jev_url = "http://127.0.0.1:8147"\n'
        "[[mail.accounts]]\n"
        'name = "personal"\n'
        f'address = "{private["address"]}"\n'
        "[voice]\n"
        "[[tapo.devices]]\n"
        f'name = "{private["device"]}"\n'
        f'host = "{private["host"]}"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-I", str(SCAN), "--config", str(config)],
        cwd=ROOT,
        env=clean_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_note_says_when_her_own_user_holds_the_config(repo: Repo, tmp_path: Path) -> None:
    """After the move into her own user your copy is gone: the owner-value rule is skipped, and the scan says so."""
    repo.add("notes.txt", "fine\n")
    assert repo.scan().stderr == ""  # no own user: silent, as before
    (tmp_path / "root" / "var" / "estia").mkdir(parents=True)
    repo.env["ESTIA_ROOT"] = str(tmp_path / "root")
    result = repo.scan()
    assert result.returncode == 0
    assert "your own config values were not checked" in result.stderr and "--config" in result.stderr


# after the move her config is hers (/var/estia, 0700): she gives salted hashes of its private values, never the values


def hashed(repo: Repo, config: Path, out: Path) -> Path:
    result = repo.scan("--hash-values", str(config))
    assert result.returncode == 0, result.stderr
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.stdout, encoding="utf-8")
    return out


def test_hashed_values_find_what_the_config_finds_without_holding_a_value(repo: Repo, tmp_path: Path) -> None:
    config = tmp_path / "owner.toml"
    config.write_text(SETUP_AND_PRIVATE_CONFIG, encoding="utf-8")
    values = hashed(repo, config, tmp_path / "values.json")
    text = values.read_text(encoding="utf-8")
    for private in ("306900000017", "zephyrine", "Brass Lamp", "10.0.0.4", "Example Street", "Hallway"):
        assert private.casefold() not in text.casefold()
    repo.add(
        "notes.txt",
        "the bot is +306900000017\nmail from zephyrine.q" + AT + "corp.example\nturn on Brass Lamp Nine\n"
        "ping 10.0.0.4.\nhome is 1 Example Street, Exampletown\nplay on Hallway Cube\n"
        "10.0.0.41 and Brass Lamps are others\nthe Mac mini, Marigold, the personal mailbox\n",
    )
    result = repo.scan("--values", str(values))
    keys = ["bot_number", "mail.accounts.address", "tapo.devices.name", "tapo.devices.host", "maps.home"]
    keys.append("spotify.default_device")
    assert findings(result) == [f"notes.txt:{n}: owner config value ({key})" for n, key in enumerate(keys, start=1)]


def test_her_hashed_values_are_read_by_default_once_she_holds_the_config(repo: Repo, tmp_path: Path) -> None:
    """In her own user: ~/.config/local-assistant/privacy-values.json, made as her, is checked by every scan."""
    config = tmp_path / "her-config.toml"
    config.write_text('[[tapo.devices]]\nname = "Brass Lamp Nine"\n', encoding="utf-8")
    hashed(repo, config, repo.home / ".config" / "local-assistant" / "privacy-values.json")
    (tmp_path / "root" / "var" / "estia").mkdir(parents=True)
    repo.env["ESTIA_ROOT"] = str(tmp_path / "root")
    repo.add("notes.txt", "turn on Brass Lamp Nine\n")
    result = repo.scan()
    assert findings(result) == ["notes.txt:1: owner config value (tapo.devices.name)"]
    assert "not checked" not in result.stderr


def test_the_note_says_how_she_gives_the_hashed_values(repo: Repo, tmp_path: Path) -> None:
    (tmp_path / "root" / "var" / "estia").mkdir(parents=True)
    repo.env["ESTIA_ROOT"] = str(tmp_path / "root")
    repo.add("notes.txt", "fine\n")
    note = repo.scan().stderr
    assert "--hash-values" in note and "privacy-values.json" in note and "/usr/bin/sudo -u _estia" in note
    # private from the start: the salt sits beside the hashes, and short values (numbers) fall to brute force
    assert "umask 077 && mkdir -p ~/.config/local-assistant && " in note  # review 15 Oct, 9
