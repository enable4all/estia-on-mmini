"""estia-deploy (deploy): a release built as the owner at its final path, frozen root-owned, switched atomically, health
checked, rolled back on failure, old releases pruned. A fake runner stands in for sudo, uv and launchd; git, tar and cp
run for real on a fake checkout in a temporary root."""

import json
import os
import pwd
import sqlite3
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from local_assistant import deploy, ownuser
from local_assistant.deploy import ENV, Builder, Deploy, DeployError
from local_assistant.hostsec import CHOWN, LAUNCHCTL, SUDO, Host

ROOT = Path(__file__).resolve().parent.parent
UV = "/fake/bin/uv"
ME = pwd.getpwuid(os.getuid()).pw_name


def checkout(tmp_path: Path) -> Path:
    """A git repo laid out like ours, with one commit."""
    repo = tmp_path / "checkout"
    for name, text in {
        "src/local_assistant/__init__.py": "",
        "pyproject.toml": "[project]\nname = 'x'\n",
        "scripts/estia": "#!/bin/bash\necho estia\n",
        "scripts/estia-deploy": "#!/bin/bash\necho deploy\n",
        "src/local_assistant/hostsec.py": "# hostsec\n",
        "src/local_assistant/ownuser.py": "# ownuser\n",
        "src/local_assistant/deploy.py": "# deploy\n",
        "tools/tapo/pyproject.toml": "",
        "tools/ical/pyproject.toml": "",
        "tools/youtube/pyproject.toml": "",
        "docs/brand/estia-avatar.png": "png",
    }.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (repo / "launchd").mkdir()
    for template in (ROOT / "launchd").glob("*.plist.in"):
        (repo / "launchd" / template.name).write_text(template.read_text())
    for argv in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=Alex Example", "-c", "user.email=alex@example.com", "commit", "-qm", "x"],
    ):
        subprocess.run(argv, cwd=repo, check=True)
    voice = repo / "tools/voice/.build/release"
    (voice / "FluidAudio_FluidAudio.bundle").mkdir(parents=True)
    (voice / "voice-service").write_text("binary")
    (voice / "FluidAudio_FluidAudio.bundle" / "Info.plist").write_text("x")
    return repo


class FakeMac(Host):
    """Runs git, tar, rm, mkdir and cp for real (after taking off the sudo/env prefix); fakes uv, the release's
    Pythons, launchd, ps and the database read; records every argv. A build step takes 30 s of the clock; a restart
    starts a new bridge process, which writes last_alive when it is healthy."""

    def __init__(
        self,
        root: Path,
        *,
        healthy: bool = True,
        clock: "Clock | None" = None,
        restarts: bool = True,
        fails: str = "",
    ) -> None:
        super().__init__(root, say=lambda text: None)
        self.asked: list[tuple[str, ...]] = []
        self.healthy = healthy
        self.alive = 0.0
        self.clock = clock
        self.pid = 4242
        self.restarts = restarts  # whether launchd really starts a new bridge process
        self.fails = fails  # a tool name whose command fails, with a message on stderr

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        argv = tuple(argv)
        self.asked.append(argv)
        inner = list(argv)
        if inner[0] == SUDO:
            inner = inner[inner.index("--") + 1 :]
        if inner and inner[0] == ENV:
            inner = inner[2:]
            while inner and "=" in inner[0] and not inner[0].startswith("/"):
                inner = inner[1:]
        tool = inner[0]
        if self.fails and Path(tool).name == self.fails:
            return 2, "line 1\nline 2\nerror: failed to fetch https://example.com/simple (network unreachable)\n"
        if self.clock is not None and tool == UV:
            self.clock.now += 30
        if tool == LAUNCHCTL and inner[1] in ("kickstart", "bootstrap") and self.restarts:
            self.pid += 1
            if self.healthy and self.clock is not None:
                self.alive = self.clock.now + 1  # the new bridge writes its sign of life
        if tool in ("/usr/bin/git", "/usr/bin/tar", "/bin/rm", "/bin/mkdir", "/bin/cp"):
            done = subprocess.run(inner, capture_output=True, text=True, check=False)
            return done.returncode, done.stdout + done.stderr
        if tool == UV and inner[1:3] == ["python", "install"]:
            interp = Path(inner[inner.index("--install-dir") + 1]) / "cpython-3.13.9-macos-aarch64-none/bin/python3.13"
            interp.parent.mkdir(parents=True)
            interp.write_text("#!/bin/sh\n")
            return 0, ""
        if tool == UV and "sync" in inner:
            project = Path(inner[inner.index("--directory") + 1])
            venv = project / ".venv" / "bin"
            venv.mkdir(parents=True)
            (venv / "python").symlink_to(inner[inner.index("--python") + 1])
            (project / ".venv" / "pyvenv.cfg").write_text(f"home = {Path(inner[inner.index('--python') + 1]).parent}\n")
            return 0, ""
        if tool.endswith(("/python", "/python3.13")):
            if "--read-alive" in inner:
                return (0, f"{self.alive:.0f}\n") if self.healthy else (1, "")
            if "sys.prefix" in " ".join(inner):
                return 0, str(Path(tool).parents[1]) + "\n"
            return 0, ""
        if tool == LAUNCHCTL and inner[1] == "print":
            return 0, f"\tstate = running\n\tpid = {self.pid}\n"
        if tool == "/bin/ps":
            return 0, f"  {os.getuid()} /x/signal-cli --trust-new-identities on-first-use -a +306900000000 jsonRpc\n"
        return 0, ""


class Clock:
    def __init__(self, start: float = 1_791_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def her_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: os.getuid())


def deployment(
    root: Path,
    repo: Path,
    mac: FakeMac,
    clock: Clock,
    *,
    as_root: bool = True,
    keep: int = 3,
    health: bool = True,
    said: list[str] | None = None,
    confirm: bool = True,
    brew: Path | None = None,
    voice: bool = False,
) -> Deploy:
    builder = Builder(ME, root.parent / "owner-home", os.getuid())
    mac.clock = clock
    return Deploy(
        mac,
        builder,
        repo,
        uv=UV,
        keep=keep,
        voice=voice,
        clock=clock,
        sleep=clock.sleep,
        as_root=as_root,
        check_health=health,
        say=said.append if said is not None else lambda text: None,
        confirm=lambda question: confirm,
        brew=brew or root.parent / "no-homebrew",
    )


def daemons(root: Path) -> None:
    folder = root / "Library/LaunchDaemons"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "com.local-assistant.bridge.plist").write_text("x")


def bring_alive(mac: FakeMac, clock: Clock) -> None:
    mac.clock = clock  # the bridge writes last_alive after its restart (FakeMac does, on kickstart)


def test_a_deploy_builds_as_the_owner_freezes_and_switches(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    mac = FakeMac(root)
    bring_alive(mac, clock)
    assert deployment(root, repo, mac, clock).run() == 0
    code = root / "usr/local/estia"
    current = code / "current"
    assert current.is_symlink() and os.readlink(current).startswith("releases/")
    release = current.resolve()
    assert (release / "src/local_assistant/__init__.py").is_file() and (
        release / "docs/brand/estia-avatar.png"
    ).is_file()
    assert (release / "tools/voice/.build/release/voice-service").is_file()
    assert (release / "tools/voice/.build/release/FluidAudio_FluidAudio.bundle/Info.plist").is_file()
    assert (release / ".venv/bin/python").is_symlink() and (release / "tools/tapo/.venv").is_dir()
    youtube = release / "tools/youtube/.venv/bin/python"  # the YouTube helper: built, and checked that it imports
    assert youtube.is_symlink() and any(
        argv[-4:] == (str(youtube), "-I", "-c", "import youtube_transcript_api, requests") for argv in mac.asked
    )
    assert os.readlink(release / ".venv/bin/python").startswith(str(release / "python"))  # its own interpreter
    meta = json.loads((release / "release.json").read_text())
    assert meta["sha"] == release.name.rsplit("-", 1)[1] and meta["source"] == str(repo) and meta["voice"] is True
    assert (code / "bin/estia").read_text() == "#!/bin/bash\necho estia\n" and os.access(code / "bin/estia", os.X_OK)
    assert not list((code / "releases").glob(".*"))  # no staging left
    for path in release.rglob("*"):
        if not path.is_symlink():
            assert not path.stat().st_mode & 0o022, path  # nobody but the owner (root) may change it
    built = [argv for argv in mac.asked if argv[:2] == (SUDO, "-u")]
    assert built and all(argv[:5] == (SUDO, "-u", ME, "-H", "--") and argv[5:7] == (ENV, "-i") for argv in built)
    root_run = [argv for argv in mac.asked if argv[:2] != (SUDO, "-u")]
    for argv in root_run:  # root never runs anything from the checkout or the build
        assert not argv[0].startswith(str(repo)) and not argv[0].startswith(str(code / "releases"))
    assert any(argv[0] == CHOWN and "root:wheel" in argv for argv in root_run)
    # the installed service file ("x") was not what the tools render: written, then loaded again
    assert (LAUNCHCTL, "bootstrap", "system", "/Library/LaunchDaemons/com.local-assistant.bridge.plist") in mac.asked


def test_the_build_runs_without_sudo_for_a_rehearsal(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, as_root=False, health=False).run() == 0
    assert not any(argv[0] == SUDO for argv in mac.asked)
    assert (root / "usr/local/estia/current/src/local_assistant").is_dir()


def test_a_failed_health_check_switches_back(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    good = FakeMac(root)
    bring_alive(good, clock)
    assert deployment(root, repo, good, clock).run() == 0
    first = (root / "usr/local/estia/current").resolve()
    clock.now += 3600
    bad = FakeMac(root, healthy=False)
    said: list[str] = []
    second = deployment(root, repo, bad, clock)
    second.say = said.append
    assert second.run() == 1
    assert (root / "usr/local/estia/current").resolve() == first
    kicks = [argv for argv in bad.asked if argv[:2] == (LAUNCHCTL, "kickstart")]
    assert len(kicks) == 2  # the new release, then the old one again
    assert any("switched back" in line for line in said)


def test_old_releases_are_pruned(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    for _ in range(5):
        clock.now += 60
        assert deployment(root, repo, FakeMac(root), clock, keep=3, health=False).run() == 0
    releases = sorted(p.name for p in (root / "usr/local/estia/releases").iterdir())
    assert len(releases) == 3 and (root / "usr/local/estia/current").resolve().name == releases[-1]


def test_the_freeze_refuses_links_out_of_the_release(tmp_path: Path) -> None:
    staging = tmp_path / "rel"
    (staging / "src").mkdir(parents=True)
    (staging / "src" / "ok.py").write_text("x")
    (staging / "src" / "inside").symlink_to("ok.py")
    deploy.verify_build(staging, os.getuid())
    (staging / "src" / "out").symlink_to("/etc/hosts")
    with pytest.raises(DeployError, match="out"):
        deploy.verify_build(staging, os.getuid())
    (staging / "src" / "out").unlink()
    os.link(staging / "src" / "ok.py", staging / "src" / "hard.py")
    with pytest.raises(DeployError, match="link"):
        deploy.verify_build(staging, os.getuid())
    (staging / "src" / "hard.py").unlink()
    (staging / "src" / "ok.py").chmod(0o4755)
    with pytest.raises(DeployError, match="set-id"):
        deploy.verify_build(staging, os.getuid())
    (staging / "src" / "ok.py").chmod(0o644)
    with pytest.raises(DeployError, match="owner"):
        deploy.verify_build(staging, os.getuid() + 1)


def test_a_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    host = Host(root, dry_run=True)
    builder = Builder(ME, Path.home(), os.getuid())
    run = Deploy(
        host,
        builder,
        repo,
        uv=UV,
        clock=clock,
        sleep=clock.sleep,
        as_root=True,
        check_health=False,
        brew=tmp_path / "no-homebrew",
    )
    assert run.run() == 0
    assert not root.exists()
    out = capsys.readouterr().out
    assert "would run: /usr/bin/sudo -u" in out and "uv python install 3.13" in out and "would switch" in out


def test_no_checkout_and_no_release_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DeployError, match="checkout"):
        deploy.default_checkout(tmp_path / "usr/local/estia")


def test_the_default_checkout_is_the_current_release_source(tmp_path: Path) -> None:
    code = tmp_path / "usr/local/estia"
    (code / "releases/r1").mkdir(parents=True)
    (code / "releases/r1/release.json").write_text(json.dumps({"source": "/somewhere/estia"}))
    (code / "current").symlink_to("releases/r1")
    assert deploy.default_checkout(code) == Path("/somewhere/estia")


def test_last_alive_is_read_read_only(tmp_path: Path) -> None:
    db = tmp_path / "assistant.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO state VALUES ('last_alive', '1791000123')")
    assert deploy.read_alive(db) == "1791000123"
    assert deploy.read_alive(tmp_path / "none.db") is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["assistant.db"]  # no -wal or -shm made


def test_the_owner_is_the_sudo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.delenv("SUDO_USER", raising=False)
    with pytest.raises(DeployError, match="sudo"):
        deploy.builder()
    monkeypatch.setenv("SUDO_USER", ME)
    assert deploy.builder().name == ME


# ------------------------------------------------------------------ the review's fixes


def commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    for argv in (
        ["git", "add", "-A"],
        ["git", "-c", "user.name=Alex Example", "-c", "user.email=alex@example.com", "commit", "-qm", message],
    ):
        subprocess.run(argv, cwd=repo, check=True)


def test_the_health_check_needs_a_new_bridge_process(tmp_path: Path) -> None:
    """The old bridge keeps writing last_alive during the build: only a new process, alive after the restart, counts."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    first = (root / "usr/local/estia/current").resolve()
    clock.now += 3600
    stuck = FakeMac(root, restarts=False)  # launchd never started a new bridge: the old pid stays
    stuck.alive = clock.now + 10_000  # and the old one keeps writing its sign of life
    said: list[str] = []
    assert deployment(root, repo, stuck, clock, said=said).run() == 1
    assert (root / "usr/local/estia/current").resolve() == first
    assert any("did not restart" in line for line in said)


def test_last_alive_from_before_the_restart_does_not_count(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    mac = FakeMac(root, healthy=False)  # a new process, which never writes last_alive
    mac.alive = clock.now + 60  # written by the old bridge while the release was built
    said: list[str] = []
    assert deployment(root, repo, mac, clock, said=said).run() == 1
    assert any("last_alive" in line for line in said)


def test_the_freeze_refuses_a_chain_of_links_out_of_the_release(tmp_path: Path) -> None:
    """Each link's text stays inside, but the kernel resolves .. after the links before it: /etc/hosts."""
    staging = tmp_path / "rel"
    (staging / "sub").mkdir(parents=True)
    (staging / "sub" / "b").symlink_to("..")
    (staging / "b").symlink_to(".")
    (staging / "sub" / "evil.pth").symlink_to("b/b/b/b/../../../../../etc/hosts")
    with pytest.raises(DeployError, match=r"evil\.pth"):
        deploy.verify_build(staging, os.getuid())


def test_the_freeze_refuses_a_dangling_link(tmp_path: Path) -> None:
    staging = tmp_path / "rel"
    staging.mkdir()
    (staging / "gone").symlink_to("nowhere.py")
    with pytest.raises(DeployError, match="gone"):
        deploy.verify_build(staging, os.getuid())


def test_a_file_swapped_for_a_fifo_is_refused_without_hanging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Between the check and the open, a regular file can become a FIFO: the open must not wait for a writer."""
    import signal

    staging = tmp_path / "rel"
    staging.mkdir()
    os.mkfifo(staging / "pipe")
    real_stat = os.stat

    def looks_regular(path: object, *args: object, **kwargs: object) -> os.stat_result:
        found = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        if str(path) == "pipe":
            return os.stat_result((0o100644, *tuple(found)[1:]))
        return found

    def hung(signum: int, frame: object) -> None:
        raise TimeoutError("the open waited for a writer")

    monkeypatch.setattr(os, "stat", looks_regular)
    before = signal.signal(signal.SIGALRM, hung)
    signal.alarm(3)
    try:
        with pytest.raises(DeployError, match="pipe"):
            deploy.verify_build(staging, os.getuid())
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, before)


def test_the_release_keeps_bytecode_valid_after_the_freeze(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, health=False).run() == 0
    syncs = [argv for argv in mac.asked if "sync" in argv]
    assert len(syncs) == 4 and all("UV_COMPILE_BYTECODE=1" in argv for argv in syncs)
    assert any("compileall" in argv for argv in mac.asked)
    release = (root / "usr/local/estia/current").resolve()
    source = repo / "src/local_assistant/__init__.py"
    frozen = release / "src/local_assistant/__init__.py"
    assert frozen.stat().st_mtime_ns == source.stat().st_mtime_ns or frozen.stat().st_mtime == pytest.approx(
        source.stat().st_mtime, abs=1
    )  # git archive's times, kept by the freeze: a .pyc stays valid


def homebrew(tmp_path: Path) -> Path:
    """Homebrew's signal-cli (a link into its keg) and openjdk, as on the Mac."""
    brew = tmp_path / "homebrew"
    keg = brew / "Cellar/signal-cli/0.14.9"
    (keg / "bin").mkdir(parents=True)
    (keg / "bin/signal-cli").write_text("native")
    (keg / "bin/signal-cli").chmod(0o755)
    (brew / "bin").mkdir()
    (brew / "bin/signal-cli").symlink_to("../Cellar/signal-cli/0.14.9/bin/signal-cli")
    java = brew / "opt/openjdk/libexec/openjdk.jdk/Contents/Home/bin/java"
    java.parent.mkdir(parents=True)
    java.write_text("java")
    java.chmod(0o755)
    return brew


def jvm_vendor(repo: Path) -> None:
    launcher = repo / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")


def test_her_signal_cli_and_java_are_copied_into_the_release(tmp_path: Path) -> None:
    """Homebrew's folders are yours: what she runs is copied into the root-owned release."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    jvm_vendor(repo)
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, health=False, brew=homebrew(tmp_path)).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert (release / "vendor/signal-cli-native/bin/signal-cli").read_text() == "native"
    assert (release / "vendor/openjdk.jdk/Contents/Home/bin/java").read_text() == "java"
    assert (release / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli").is_file()
    meta = json.loads((release / "release.json").read_text())
    assert {"signal-cli-native", "openjdk.jdk", "signal-cli-0.14.9-jvm"} <= set(meta["vendor"])


def test_no_java_is_copied_without_the_jvm_signal_cli(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False, brew=homebrew(tmp_path)).run() == 0
    release = (root / "usr/local/estia/current").resolve()
    assert (release / "vendor/signal-cli-native/bin/signal-cli").is_file()
    assert not (release / "vendor/openjdk.jdk").exists()


def test_a_failing_build_step_says_why(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root, fails="uv"), clock, health=False, said=said).run() == 1
    assert any("network unreachable" in line for line in said)
    assert list((root / "usr/local/estia/releases").iterdir()) == []  # nothing left


# the root-run tools: never code from the release, updated only after their diff was shown


def test_the_first_deploy_installs_the_root_run_tools(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    code = root / "usr/local/estia"
    tools = code / "tools"
    assert (tools / "src/local_assistant/deploy.py").read_text() == "# deploy\n"
    assert (tools / "launchd/com.local-assistant.bridge.plist.in").is_file()
    assert (tools / "python3").is_symlink() and (tools / "python3").resolve().is_file()
    assert (tools / "python3").resolve().is_relative_to(tools.resolve())  # its own copy, not the release's
    assert (code / "bin/estia").read_text() == (tools / "scripts/estia").read_text() == "#!/bin/bash\necho estia\n"
    assert json.loads((tools / "tools.json").read_text())["release"] == (code / "current").resolve().name


def test_a_later_deploy_keeps_the_installed_tools_and_says_how_to_review_them(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, "scripts/estia", "#!/bin/bash\necho changed\n", "change the wrapper")
    clock.now += 60
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said).run() == 0
    code = root / "usr/local/estia"
    assert (code / "bin/estia").read_text() == "#!/bin/bash\necho estia\n"  # root runs only reviewed tools
    assert any("--update-tools" in line and "scripts/estia" in line for line in said)


@pytest.mark.parametrize("yes", [True, False])
def test_update_tools_shows_the_diff_and_asks(tmp_path: Path, yes: bool) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, "scripts/estia", "#!/bin/bash\necho changed\n", "change the wrapper")
    clock.now += 60
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    said: list[str] = []
    update = deployment(root, repo, FakeMac(root), clock, health=False, said=said, confirm=yes)
    assert update.update_tools() == (0 if yes else 1)
    assert "+echo changed" in said and "-echo estia" in said
    expected = "#!/bin/bash\necho changed\n" if yes else "#!/bin/bash\necho estia\n"
    assert (root / "usr/local/estia/bin/estia").read_text() == expected


def test_a_deploy_shows_the_new_commits_and_asks_first(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    live = (root / "usr/local/estia/current").resolve()
    commit(repo, "src/local_assistant/x.py", "x = 1\n", "feat: a new part")
    clock.now += 60
    said: list[str] = []
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, health=False, said=said, confirm=False).run() == 1
    text = "\n".join(said)
    assert "feat: a new part" in text and "src/local_assistant/x.py" in text
    assert (root / "usr/local/estia/current").resolve() == live
    assert not any(argv[0] == UV or UV in argv for argv in mac.asked)  # nothing built


def test_control_characters_in_commit_subjects_are_not_printed(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run() == 0
    commit(repo, "a.txt", "a", "fix\x1b[2K\x1b[1Ahidden")
    clock.now += 60
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, health=False, said=said, confirm=False).run() == 1
    assert not any("\x1b" in line for line in said)


def installed_plists(root: Path, *, voice: bool = False) -> Path:
    folder = root / "Library/LaunchDaemons"
    folder.mkdir(parents=True, exist_ok=True)
    for label in ("bridge", *(["voice"] if voice else [])):
        (folder / f"com.local-assistant.{label}.plist").write_text("old")
    return folder


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_each_deploy_renders_the_service_files_and_reloads_one_that_changed(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    folder = installed_plists(root, voice=True)
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock).run() == 0
    bridge = folder / "com.local-assistant.bridge.plist"
    assert b"<string>_estia</string>" in bridge.read_bytes()
    assert (LAUNCHCTL, "bootout", "system/com.local-assistant.bridge") in mac.asked
    assert (LAUNCHCTL, "bootstrap", "system", "/Library/LaunchDaemons/com.local-assistant.bridge.plist") in mac.asked
    clock.now += 60
    again = FakeMac(root)
    assert deployment(root, repo, again, clock).run() == 0
    assert (LAUNCHCTL, "kickstart", "-k", "system/com.local-assistant.bridge") in again.asked  # unchanged: a restart
    assert not any(argv[:2] == (LAUNCHCTL, "bootout") for argv in again.asked)
    assert not any(argv[-1] == "system/com.local-assistant.voice" for argv in again.asked)  # voice unchanged: left


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_new_voice_binary_restarts_the_voice_service(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    (repo / "tools/voice/.build/release/voice-service").write_text("binary 2")
    clock.now += 60
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock).run() == 0
    assert (LAUNCHCTL, "kickstart", "-k", "system/com.local-assistant.voice") in mac.asked


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_deploy_with_a_new_voice_binary_says_when_calls_work(tmp_path: Path) -> None:
    """The Neural Engine compiles a new voice again: the deploy ends with when calls work (the real state when ready);
    the same binary says nothing about the voice."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    said: list[str] = []
    first = deployment(root, repo, FakeMac(root), clock, said=said)
    first.voice_health = lambda: {"stt": "loading", "tts": "loading"}
    first.her_calls = lambda: True
    started = clock.now
    assert first.run() == 0
    assert said[-2:] == [ownuser.VOICE_WAITING, ownuser.VOICE_LOADING]
    assert clock.now - started >= ownuser.VOICE_WAIT_SECONDS  # still loading when the bridge counts a long load
    clock.now += 60
    said.clear()
    (repo / "tools/voice/.build/release/voice-service").write_text("binary 1b")
    no_calls = deployment(root, repo, FakeMac(root), clock, said=said)
    no_calls.voice_health = lambda: {"stt": "loading", "tts": "loading"}
    no_calls.her_calls = lambda: False  # no [calls]: no text follows, none is promised
    assert no_calls.run() == 0
    assert said[-1] == ownuser.VOICE_LOADING_NO_CALLS and not any("texts" in line for line in said)
    clock.now += 60
    said.clear()
    same = deployment(root, repo, FakeMac(root), clock, said=said)
    same.voice_health = lambda: {"stt": "loading", "tts": "loading"}
    assert same.run() == 0
    assert ownuser.VOICE_LOADING not in said and not any(line.startswith("voice:") for line in said)
    (repo / "tools/voice/.build/release/voice-service").write_text("binary 2")
    clock.now += 60
    said.clear()
    new = deployment(root, repo, FakeMac(root), clock, said=said)
    new.voice_health = lambda: {"stt": "ready", "tts": "ready"}
    assert new.run() == 0
    assert said[-1] == "voice: ready"


def test_her_calls_setting_is_read_as_her_and_says_on_only_with_calls(tmp_path: Path) -> None:
    """Root never reads her config: her release's Python reads it as her (like last_alive), on or off only."""
    config = tmp_path / "config.toml"
    config.write_text('[voice]\nurl = "http://127.0.0.1:8150"\n[calls]\n', encoding="utf-8")
    assert deploy.read_calls(config) is True
    config.write_text('[voice]\nurl = "http://127.0.0.1:8150"\n', encoding="utf-8")
    assert deploy.read_calls(config) is False
    assert deploy.read_calls(tmp_path / "missing.toml") is False
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    mac = FakeMac(root)
    run = deployment(root, repo, mac, clock)
    assert run.her_calls() is False  # a test root: sudo is never run
    assert any("--read-calls" in argv for argv in mac.asked)


def test_without_a_voice_service_a_deploy_says_nothing_about_it(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    daemons(root)
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, said=said).run() == 0
    assert ownuser.VOICE_LOADING not in said and not any(line.startswith("voice:") for line in said)


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_service_template_that_runs_as_root_stops_the_deploy(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    tools = root / "usr/local/estia/tools/launchd/com.local-assistant.bridge.plist.in"
    tools.write_text(tools.read_text().replace("<string>@RUN_USER@</string>", "<string>root</string>", 1))
    live = (root / "usr/local/estia/current").resolve()
    clock.now += 60
    said: list[str] = []
    assert deployment(root, repo, FakeMac(root), clock, said=said).run() == 1
    assert (root / "usr/local/estia/current").resolve() == live
    assert any("UserName" in line for line in said)


def test_the_move_builds_quietly_without_going_live(tmp_path: Path) -> None:
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    said: list[str] = []
    mac = FakeMac(root)
    assert deployment(root, repo, mac, clock, health=False, said=said).run(restart=False) == 0
    assert any(line.startswith("release ready") for line in said) and not any("live" in line for line in said)
    assert not any(argv[0] == LAUNCHCTL for argv in mac.asked)


def test_a_retried_move_takes_its_own_releases_tools(tmp_path: Path) -> None:
    """A failed move leaves the tools of its try (live 13 Oct: service templates without a key the new checks want).
    The move runs your checkout as root anyway: its release's tools replace them, no review needed."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    assert deployment(root, repo, FakeMac(root), clock, health=False).run(restart=False) == 0
    commit(repo, "scripts/estia", "#!/bin/bash\necho changed\n", "change the wrapper")
    clock.now += 60
    assert deployment(root, repo, FakeMac(root), clock, health=False).run(restart=False) == 0
    code = root / "usr/local/estia"
    assert (code / "bin/estia").read_text() == "#!/bin/bash\necho changed\n"
    assert json.loads((code / "tools/tools.json").read_text())["release"] == (code / "current").resolve().name


@pytest.mark.skipif(not Path("/usr/bin/plutil").exists(), reason="plutil is macOS only")
def test_a_dry_run_of_a_later_deploy_claims_no_changes_it_cannot_see(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing is built in a dry run: the tools and the voice service are compared with what the checkout holds."""
    root, repo, clock = tmp_path / "root", checkout(tmp_path), Clock()
    installed_plists(root, voice=True)
    assert deployment(root, repo, FakeMac(root), clock).run() == 0
    clock.now += 60
    host = Host(root, dry_run=True)
    run = Deploy(
        host,
        Builder(ME, tmp_path / "owner-home", os.getuid()),
        repo,
        uv=UV,
        clock=clock,
        sleep=clock.sleep,
        as_root=True,
        check_health=False,
        brew=tmp_path / "no-homebrew",
    )
    assert run.run() == 0
    out = capsys.readouterr().out
    assert "root-run tools differ" not in out
    assert "system/com.local-assistant.voice" not in out  # the same voice binary: not restarted
