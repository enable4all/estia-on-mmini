"""The suite reads no part of the Mac it runs on that differs from one Mac to the next: the shell's locale, where uv
or Ollama was installed, the git version."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# the tests that read the environment a helper gets (a Python child that records its own os.environ)
HELPER_ENV_TESTS = (
    "tests/test_calendar_feed.py::test_the_helper_gets_fixed_arguments_and_a_clean_environment",
    "tests/test_devices.py::test_the_helper_gets_a_clean_environment",
    "tests/test_documents.py::test_the_helper_runs_with_fixed_arguments_and_a_clean_environment",
    "tests/test_video.py::test_the_helper_gets_a_clean_environment",
)


@pytest.mark.parametrize("lang", [None, "C"], ids=["LANG-unset", "LANG-C"])
def test_a_helpers_environment_is_the_same_whatever_the_shells_locale(lang: str | None) -> None:
    """With LANG unset or C, Python coerces its locale (PEP 538) and adds LC_CTYPE to its own environment, so a child
    helper's environment would differ from the one under a UTF-8 LANG. The suite pins the locale (conftest's
    stable_locale): the helper tests pass from a bare shell too."""
    env = {key: value for key, value in os.environ.items() if key not in ("LANG", "LC_ALL", "LC_CTYPE")}
    if lang is not None:
        env["LANG"] = lang
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--color=no", *HELPER_ENV_TESTS],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    assert f"{len(HELPER_ENV_TESTS)} passed" in done.stdout


# a system path written in the code: read through Host.path() (or the Ollama step's _system()), which puts it under a
# test's root; every other one is listed here with why a test never reads it on the Mac it runs on
LIVE_PATH = re.compile(r'"(/(?:Applications|Library|opt/homebrew|usr/local|var|etc)/[^"]*)"')
UNDER_ROOT = re.compile(r"\b(?:host|tools)\.(?:path|read)\(|\b_system\(")
ALLOWED = {
    ("config.py", "/etc/localtime"): "LOCALTIME: conftest's amsterdam_time points it at a link of its own",
    ("deploy.py", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"): "OWNER_PATH: a build's PATH",
    ("deploy.py", "/opt/homebrew/bin/uv"): "a dry run's stand-in when no uv is found: printed, never run",
    ("harden.py", "/etc/sudoers.d/{name}"): "an argument to sudo, which Host refuses off the live Mac",
    ("hostsec.py", "/opt/homebrew/bin/tailscale"): "TAILSCALE_TOOLS: looked for through host.path()",
    ("hostsec.py", "/usr/local/bin/tailscale"): "TAILSCALE_TOOLS: looked for through host.path()",
    ("hostsec.py", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"): "TAILSCALE_TOOLS: through host.path()",
    ("hostsec.py", "/etc/ssh/sshd_config.d/000-estia-keys-only.conf"): "SSH_DROP_IN_PATH: only in sudo's arguments",
    ("hostsec.py", "/var/folders/"): "_TEMPORARY: a prefix a program's path is compared with, nothing read",
    ("hostsec.py", "/etc/sudoers.d/{name}"): "an argument to sudo, which Host refuses off the live Mac",
    ("ollama_build.py", "/Library/LaunchDaemons"): "DAEMON, LAUNCH_DAEMONS: read under the root (_system, root /)",
    ("ollama_build.py", "/opt/homebrew/Cellar"): "CELLARS: looked at through _system()",
    ("ollama_build.py", "/usr/local/Cellar"): "CELLARS: looked at through _system()",
    ("ollama_build.py", "/Applications/Ollama.app"): "APP: looked at through _system(), shown as is",
    ("ownuser.py", "/var/estia"): "HOME: her home, read under the root (host.path, root /)",
    ("ownuser.py", "/usr/local/estia"): "CODE: read under the root (host.path, root /)",
    ("ownuser.py", "/Library/LaunchDaemons"): "DAEMONS: read under the root (host.path, root /)",
    ("ownuser.py", "/var/log/estia"): "STREAM_LOGS: made and read under the root (host.path)",
    ("ownuser.py", "/etc/newsyslog.d/local-assistant.conf"): "NEWSYSLOG: looked at and removed through host.path()",
}


def live_paths() -> set[tuple[str, str]]:
    """(module, path) for every system path in the code that is not read through the root on its own line."""
    found: set[tuple[str, str]] = set()
    package = ROOT / "src" / "local_assistant"
    for module in sorted(package.rglob("*.py")):  # the prompts package's files too
        name = module.relative_to(package).as_posix()
        for line in module.read_text(encoding="utf-8").splitlines():
            if not UNDER_ROOT.search(line):
                found.update((name, path) for path in LIVE_PATH.findall(line))
    return found


def test_no_live_paths_outside_host() -> None:
    """A new system path in the code is read through Host.path(), or added to ALLOWED with why no test reads it on
    the Mac it runs on."""
    found = live_paths()
    assert found - ALLOWED.keys() == set(), "read it through Host.path(), or allow it here with the reason"
    assert ALLOWED.keys() - found == set(), "no longer in the code: take it off ALLOWED"


def test_the_live_path_check_sees_a_path_and_lets_host_path_through() -> None:
    lines = [
        'TOOL = Path("/opt/homebrew/bin/thing")',
        'x = host.path("/var/estia")',
        'y = self._system(Path("/etc/a"))',
    ]
    found = [path for line in lines if not UNDER_ROOT.search(line) for path in LIVE_PATH.findall(line)]
    assert found == ["/opt/homebrew/bin/thing"]
