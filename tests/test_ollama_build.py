"""Ollama's official build (owner, 15 Oct: "ollama we keep use only the official build"): what setup keeps, what it
installs and checks first, and what it refuses with the fix. Nothing on the Mac is read or run: the tools are fakes,
the download a tarball made here, the files under the test's own folder."""

import hashlib
import os
import plistlib
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from local_assistant import ollama_build, prompts
from local_assistant.hostsec import CODESIGN, LAUNCHCTL, SUDO, TAR, Host
from local_assistant.ollama_build import (
    DAEMON,
    LABEL,
    LOG,
    MIN_VERSION,
    RELEASE,
    REQUIREMENT,
    SPCTL,
    TEAM_ID,
    TEE,
    XGRAMMAR,
    Found,
    Install,
    Job,
    Listener,
    Release,
    Tools,
    daemon_plist,
    missing_libraries,
    non_mlx_source,
    ollama_jobs,
    parse_version,
    problem,
    signature_problem,
)

MACHO = b"\xcf\xfa\xed\xfe" + b"\0" * 60  # a 64-bit Mach-O's magic number, then nothing
NOTARIZED = "ollama: accepted\nsource=Notarized Developer ID\norigin=Developer ID Application: Example (3MU9H2V9Y9)\n"
PROGRAMS = (
    "ollama",
    "llama-server",
    "llama-quantize",
    "libggml-base.dylib",
    *XGRAMMAR,
    "mlx_metal_v4/libmlx.dylib",
    "mlx_metal_v4/mlx.metallib",
)


def official_tree(folder: Path, *, leave_out: Sequence[str] = ()) -> Path:
    """The official tarball's layout: the programs at the top, the MLX runtimes in mlx_metal_v3 and mlx_metal_v4."""
    for name in PROGRAMS:
        if name in leave_out:
            continue
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"metallib" if name.endswith(".metallib") else MACHO)
    (folder / "LICENSE").write_text("not a program: never asked about its signature\n", encoding="utf-8")
    return folder


def tarball(tmp_path: Path, **kwargs: Sequence[str]) -> Path:
    tree = official_tree(tmp_path / "release-tree", **kwargs)
    out = tmp_path / "release" / "ollama-darwin.tgz"
    out.parent.mkdir()
    subprocess.run(
        ["/usr/bin/tar", "-czf", str(out), "-C", str(tree), *sorted(p.name for p in tree.iterdir())], check=True
    )
    return out


def release_of(path: Path, version: str = "0.40.4") -> Release:
    return Release(version, hashlib.sha256(path.read_bytes()).hexdigest())


LOCAL = ("127.0.0.1.11434",)


class FakeTools(Tools):
    """The Mac for the Ollama step: Ollama's answers in turn, the listening program in turn, canned codesign and spctl
    output; curl copies the test's tarball, tar is the real one (files of this test only), sudo is recorded and never
    run."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        versions: Sequence[str | None] = (None,),
        program: str | Sequence[str] = "",
        addresses: tuple[str, ...] = LOCAL,
    ) -> None:
        super().__init__(Path("/"), say=lambda _line: None)
        self.tmp_path = tmp_path
        self.root = tmp_path / "root"  # /Applications, /opt/homebrew and /Library/LaunchDaemons under the test
        self.versions = list(versions)
        self.programs = [program] if isinstance(program, str) else list(program)
        self.addresses = addresses
        self.uid: int | None = os.getuid()  # the account that runs the listening program: yours, unless a test says
        self.others: tuple[tuple[str, int | None], ...] = ()  # another program or account on the port too
        self.tarball: Path | None = None
        self.teams: dict[str, str] = {}  # a file's name -> its TeamIdentifier, when not Ollama's
        self.unsigned: set[str] = set()
        self.forged: set[str] = set()  # Ollama's team ID, ad-hoc signed: not Apple's Developer ID certificate
        self.notarized = NOTARIZED
        self.changes: list[tuple[str, ...]] = []
        self.inputs: list[bytes] = []
        self.reads: list[tuple[str, ...]] = []
        self.fail: set[str] = set()  # the changes (joined argv, prefix) that fail

    @property
    def live(self) -> bool:
        return True

    def resolve(self, program: str) -> str:
        """Links resolved in this fake Mac, as if its tree were "/": a system path and every link's target, also an
        absolute one, in its tree (never the Mac the tests run on); a path of the test's own (a home, a folder of
        its own) where it is."""
        if program.startswith(str(self.tmp_path) + "/"):
            return os.path.realpath(program)
        todo = list(Path(program).relative_to("/").parts)
        done: list[str] = []
        links = 0
        while todo:
            part = todo.pop(0)
            if part == "..":
                done = done[:-1]
            elif part != ".":
                here = self.root.joinpath(*done, part)
                if not os.path.islink(here):
                    done.append(part)
                    continue
                links += 1
                assert links <= 40, f"a loop of links at {program}"
                target = Path(os.readlink(here))
                if target.is_absolute():
                    done, target = [], target.relative_to("/")
                todo = [*target.parts, *todo]
        return "/" + "/".join(done)

    def version(self) -> str | None:
        return self.versions.pop(0) if len(self.versions) > 1 else self.versions[0]

    def listener(self, port: int = ollama_build.PORT) -> Listener:
        program = self.programs.pop(0) if len(self.programs) > 1 else self.programs[0]
        if not program:
            return Listener("")
        return Listener(program, self.addresses, self.uid, self.others)

    def read(self, argv: Sequence[str]) -> tuple[int, str]:
        self.reads.append(tuple(argv))
        name = Path(argv[-1]).name
        if list(argv[:5]) == [CODESIGN, "--verify", "--strict", "-R", REQUIREMENT]:
            if name in self.unsigned:
                return 1, "invalid signature"
            mismatch = name in self.forged or self.teams.get(name, TEAM_ID) != TEAM_ID
            return (
                (3, "test-requirement: code failed to satisfy specified code requirement(s)") if mismatch else (0, "")
            )
        if list(argv[:2]) == [CODESIGN, "-dv"]:
            signature = "adhoc" if name in self.forged else "size=9066"
            team = self.teams.get(name, TEAM_ID)
            return (
                0,
                f"Executable={argv[-1]}\nIdentifier=ai.ollama.ollama\nSignature={signature}\nTeamIdentifier={team}\n",
            )
        if argv[0] == SPCTL:
            return (0 if "accepted" in self.notarized else 3), self.notarized
        raise AssertionError(f"unexpected read: {argv}")

    def change(self, argv: Sequence[str], *, input: bytes | None = None) -> int:
        self.changes.append(tuple(argv))
        if input is not None:
            self.inputs.append(input)
        joined = " ".join(argv)
        if any(joined.startswith(prefix) for prefix in self.fail):
            return 1
        if argv[0] == ollama_build.CURL:
            assert self.tarball is not None
            shutil.copyfile(self.tarball, argv[argv.index("-o") + 1])
        elif argv[0] == TAR:
            return subprocess.run(list(argv), check=False).returncode
        elif argv[0] != SUDO:
            raise AssertionError(f"unexpected change: {argv}")
        return 0

    def sudo(self) -> list[tuple[str, ...]]:
        return [change for change in self.changes if change[0] == SUDO]


def install(
    tools: FakeTools, home: Path, *, dry_run: bool = False, release: Release = RELEASE
) -> tuple[Install, list[str]]:
    said: list[str] = []
    step = Install(
        tools,
        home,
        dry_run=dry_run,
        say=said.append,
        release=release,
        user="alex",
        group="staff",
        profile=home / ".zprofile",
        clock=iter(range(0, 10_000)).__next__,
        sleep=lambda _seconds: None,
    )
    return step, said


def job_file(folder: Path, label: str, program: str) -> Path:
    """A launchd job file, as launchd reads it: its label and the program it runs."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{label}.plist"
    path.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": [program, "serve"], "KeepAlive": True}))
    return path


def private_log(tools: FakeTools) -> Path:
    """Ollama's log as setup leaves it: your own, 0600, one name (so no step makes it again)."""
    log = tools.root / LOG.relative_to("/")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"")
    log.chmod(0o600)
    return log


@pytest.fixture
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    return folder


# ------------------------------------------------------------------ what setup keeps and what it refuses


def test_the_version_is_read_as_three_numbers() -> None:
    assert parse_version("0.40.4") == (0, 40, 4) and parse_version("0.40.4-rc1") == (0, 40, 4)
    assert parse_version("") is None and parse_version("dev") is None
    assert MIN_VERSION == (0, 33, 1)  # the MLX engine's JSON-schema output arrived in v0.33.1


def test_the_pins_are_the_owners_tested_release() -> None:
    assert RELEASE.version == "0.40.4" and TEAM_ID == "3MU9H2V9Y9"
    assert RELEASE.sha256 == "a4ceae4b2aaf18df37ebcbfb56ba3a37a87838631112d66c2be07268693c8d58"
    assert RELEASE.url == "https://github.com/ollama/ollama/releases/download/v0.40.4/ollama-darwin.tgz"
    assert XGRAMMAR == ("mlx_metal_v3/libollama_xgrammar.dylib", "mlx_metal_v4/libollama_xgrammar.dylib")


def test_an_official_ollama_with_both_libraries_is_kept(tmp_path: Path) -> None:
    program = official_tree(tmp_path / "v0.40.4") / "ollama"
    assert problem(Found(str(program), "0.40.4", signature="", addresses=LOCAL)) is None
    assert problem(Found(str(program), "0.33.1", signature="")) is None  # the oldest with JSON-schema output on MLX
    assert problem(Found(str(program), "0.40.4", addresses=("::1.11434",))) is None  # this Mac only, over IPv6


@pytest.mark.parametrize("cellar", ["/opt/homebrew/Cellar", "/usr/local/Cellar"])
def test_homebrews_ollama_is_named_with_the_fix(cellar: str) -> None:
    wrong = problem(Found(f"{cellar}/ollama/0.40.4/bin/ollama", "0.40.4"))
    assert wrong is not None
    text, fix = wrong
    assert "Homebrew's Ollama" in text and "JSON-schema" in text
    assert fix.startswith("brew services stop ollama, then brew uninstall ollama") and "scripts/setup.sh" in fix


def test_the_ollama_app_is_named_with_the_fix(tmp_path: Path) -> None:
    """The app holds the same signed files, but updates itself, starts only at a login on the Mac's screen, and stops
    any other `ollama serve` when its own cannot start: never next to the daemon."""
    resources = official_tree(tmp_path / "Applications/Ollama.app/Contents/Resources")
    wrong = problem(Found(str(resources / "ollama"), "0.40.4", signature=""))
    assert wrong is not None
    text, fix = wrong
    assert "the Ollama app answers" in text and "updates itself" in text
    assert f"mv {tmp_path / 'Applications/Ollama.app'} ~/.Trash/" in fix
    assert "osascript -e 'quit app \"Ollama\"'" in fix  # over SSH there is no menu to quit it from


@pytest.mark.parametrize(
    ("found", "why"),
    [
        (Found("", "0.40.4"), "could not be found"),
        (Found("{tree}/ollama", "0.40.4", signature="signed by ABCDE12345, not Ollama's"), "signed by ABCDE12345"),
        (Found("{tree}/ollama", "0.33.0", signature=""), "0.33.0 is older than 0.33.1"),
        (Found("{bare}/ollama", "0.40.4", signature=""), "no mlx_metal_v4/libollama_xgrammar.dylib"),
        (Found("{tree}/ollama", "0.40.4", addresses=("*.11434",)), "it listens for other machines too (*.11434)"),
        (
            Found("{tree}/ollama", "0.40.4", addresses=("127.0.0.1.11434", "192.168.1.20.11434")),
            "it listens for other machines too (192.168.1.20.11434)",
        ),
    ],
)
def test_any_other_ollama_stops_setup_with_why(tmp_path: Path, found: Found, why: str) -> None:
    tree = official_tree(tmp_path / "tree")
    bare = official_tree(tmp_path / "bare", leave_out=[XGRAMMAR[1]])
    program = found.program.format(tree=tree, bare=bare)
    wrong = problem(Found(program, found.version, signature=found.signature, addresses=found.addresses))
    assert wrong is not None and why in wrong[0], wrong
    assert "scripts/setup.sh" in wrong[1] or "lsof" in wrong[1]


def test_an_ollama_open_to_other_machines_names_the_setting_that_closes_it(tmp_path: Path) -> None:
    """Ollama has no login: on every network, any machine there could pull models or replace local-worker."""
    program = official_tree(tmp_path / "tree") / "ollama"
    wrong = problem(Found(str(program), "0.40.4", signature="", addresses=("*.11434",)))
    assert wrong is not None and "OLLAMA_HOST=127.0.0.1:11434" in wrong[0]


# ------------------------------------------------------------------ the signature: Apple's Developer ID, Ollama's team


def test_the_requirement_asks_for_apples_developer_id_chain_with_ollamas_team() -> None:
    assert REQUIREMENT.startswith("=anchor apple generic and ")
    assert "certificate 1[field.1.2.840.113635.100.6.2.6] exists" in REQUIREMENT  # Apple's Developer ID authority
    assert "certificate leaf[field.1.2.840.113635.100.6.1.13] exists" in REQUIREMENT  # a Developer ID Application
    assert REQUIREMENT.endswith(f'certificate leaf[subject.OU] = "{TEAM_ID}"')


def test_a_team_id_signed_ad_hoc_is_refused_by_the_real_codesign(tmp_path: Path) -> None:
    """Anyone can sign a program ad hoc with Ollama's team ID: `codesign --verify --strict` passes it and `-dv` names
    the team. Only the requirement (Apple's chain) refuses it. Offline, no keychain: the real codesign on a copy."""
    forged = tmp_path / "ollama"
    shutil.copyfile("/usr/bin/true", forged)
    signed = subprocess.run(
        [CODESIGN, "-f", "-s", "-", "--team-identifier", TEAM_ID, str(forged)], capture_output=True, check=False
    )
    assert signed.returncode == 0, signed.stderr
    plain = subprocess.run([CODESIGN, "--verify", "--strict", str(forged)], capture_output=True, check=False)
    assert plain.returncode == 0  # the old check: passed
    why = signature_problem(Tools(Path("/")).read, str(forged))
    assert why == prompts.OLLAMA_WHY_NOT_APPLE
    assert signature_problem(Tools(Path("/")).read, "/usr/bin/true").startswith("signed by no developer ID")


def test_a_forged_program_that_answers_stops_setup(tmp_path: Path, home: Path) -> None:
    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(program))
    tools.forged.add("ollama")
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="not one setup can keep") as stopped:
        step.run(32_768)
    assert "not Apple's Developer ID certificate" in str(stopped.value) and not tools.changes


def test_a_kept_ollamas_libraries_are_signature_checked_too(tmp_path: Path, home: Path) -> None:
    """The program alone is not enough: a library next to it, signed by anyone, would run inside it."""
    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(program))
    tools.forged.add("libollama_xgrammar.dylib")
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="not one setup can keep") as stopped:
        step.run(32_768)
    assert "mlx_metal_v3/libollama_xgrammar.dylib: its signature names Ollama's team ID" in str(stopped.value)
    checked = [read[-1] for read in tools.reads if read[:5] == (CODESIGN, "--verify", "--strict", "-R", REQUIREMENT)]
    assert str(program.parent / XGRAMMAR[1]) in checked and str(program.parent / "llama-server") in checked


def test_the_libraries_are_looked_for_next_to_the_program(tmp_path: Path) -> None:
    tree = official_tree(tmp_path / "tree", leave_out=[XGRAMMAR[0]])
    assert missing_libraries(str(tree / "ollama")) == ([XGRAMMAR[0]], False)
    assert missing_libraries(str(official_tree(tmp_path / "all") / "ollama")) == ([], False)


def test_doctor_and_setup_know_one_layout_the_libraries_as_files_next_to_the_program(
    tmp_path: Path, home: Path
) -> None:
    """Ollama's tarball puts both libraries in the program's own folder: doctor (missing_libraries) and setup
    (check_tree) look only there, so they judge one Ollama alike. A lib/ollama folder beside or above it does not
    count, nor does a folder named like a library."""
    tree = tmp_path / "opt"
    official_tree(tree / "bin", leave_out=XGRAMMAR)
    for library in XGRAMMAR:
        (tree / "lib/ollama" / library).parent.mkdir(parents=True, exist_ok=True)
        (tree / "lib/ollama" / library).write_bytes(MACHO)
        (tree / "bin/lib/ollama" / library).parent.mkdir(parents=True, exist_ok=True)
        (tree / "bin/lib/ollama" / library).write_bytes(MACHO)
    assert missing_libraries(str(tree / "bin/ollama")) == (list(XGRAMMAR), False)
    folder = official_tree(tmp_path / "named", leave_out=[XGRAMMAR[0]])
    (folder / XGRAMMAR[0]).mkdir(parents=True)
    assert missing_libraries(str(folder / "ollama")) == ([XGRAMMAR[0]], False)
    step, _said = install(FakeTools(tmp_path), home)
    for where, missing in ((tree / "bin", XGRAMMAR), (folder, XGRAMMAR[:1])):
        why = step.check_tree(where)
        assert [library for library in XGRAMMAR if f"no {library}" in why] == list(missing), why


def test_a_library_folder_that_cannot_be_read_is_not_called_missing(tmp_path: Path) -> None:
    """Doctor runs as her: your ~/opt may be closed to her account. Unread is never "missing"."""
    tree = official_tree(tmp_path / "tree")
    (tree / "mlx_metal_v4").chmod(0o000)
    try:
        assert missing_libraries(str(tree / "ollama")) == ([], True)
    finally:
        (tree / "mlx_metal_v4").chmod(0o755)


# ------------------------------------------------------------------ the install


def test_nothing_answering_means_the_pinned_release_downloaded_checked_installed_and_always_on(
    tmp_path: Path, home: Path
) -> None:
    tgz = tarball(tmp_path)
    release = release_of(tgz)
    folder = home / "opt/ollama/v0.40.4"
    tools = FakeTools(tmp_path, versions=[None, None, "0.40.4"], program=str(folder / "ollama"))
    tools.tarball = tgz
    step, said = install(tools, home, release=release)
    assert step.run(32_768) == str(home / "opt/ollama/current/ollama")
    assert (folder / "ollama").read_bytes() == MACHO and (folder / XGRAMMAR[1]).is_file()
    assert os.readlink(home / "opt/ollama/current") == str(folder)
    assert sorted(p.name for p in (home / "opt/ollama").iterdir()) == ["current", "v0.40.4"]  # the download is gone
    curl = tools.changes[0]
    assert curl[0] == "/usr/bin/curl" and curl[-1] == release.url and "=https" in curl
    # every program, library and metallib: Apple's Developer ID chain with Ollama's team; the LICENSE is no program
    for name in PROGRAMS:
        assert any(
            read[:5] == (CODESIGN, "--verify", "--strict", "-R", REQUIREMENT) and read[-1].endswith(name)
            for read in tools.reads
        ), name
    assert not any(read[-1].endswith("LICENSE") for read in tools.reads)
    assert any(read[0] == SPCTL and read[-1].endswith("/ollama") for read in tools.reads)
    assert tools.sudo() == [
        (SUDO, "/usr/bin/install", "-d", "-m", "755", "-o", "root", "-g", "wheel", "/var/log/estia"),
        # the log yours and private before launchd opens it: no other account reads it, you empty it without sudo
        (SUDO, "/usr/bin/install", "-m", "600", "-o", "alex", "-g", "staff", "/dev/null", str(LOG)),
        (SUDO, TEE, str(DAEMON)),
        (SUDO, "/bin/chmod", "644", str(DAEMON)),
        (SUDO, "/usr/sbin/chown", "root:wheel", str(DAEMON)),
        (SUDO, LAUNCHCTL, "bootstrap", "system", str(DAEMON)),
    ]
    assert tools.inputs == [daemon_plist(user="alex", group="staff", home=home, num_ctx=32_768)]
    assert (home / ".zprofile").read_text(encoding="utf-8").count("opt/ollama/current") == 1
    text = "\n".join(said)
    assert "✓ Ollama: 0.40.4 checked and installed" in text and "✓ Ollama: 0.40.4 answers, always on" in text


def test_the_service_file_runs_it_as_you_on_this_mac_only(home: Path) -> None:
    plist = plistlib.loads(daemon_plist(user="alex", group="staff", home=home, num_ctx=16_384))
    assert plist["Label"] == LABEL == "com.local-assistant.ollama"
    assert (plist["UserName"], plist["GroupName"]) == ("alex", "staff")
    assert plist["ProgramArguments"] == [str(home / "opt/ollama/current/ollama"), "serve"]
    env = plist["EnvironmentVariables"]
    assert env["OLLAMA_HOST"] == "127.0.0.1:11434" and env["HOME"] == str(home)
    assert env["OLLAMA_MODELS"] == str(home / ".ollama/models") and env["OLLAMA_CONTEXT_LENGTH"] == "16384"
    assert (env["OLLAMA_MAX_LOADED_MODELS"], env["OLLAMA_NUM_PARALLEL"], env["OLLAMA_KEEP_ALIVE"]) == ("1", "1", "120m")
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True
    # launchd opens the log as root: in root's own folder, so no link of yours is ever followed there
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == str(LOG) == "/var/log/estia/ollama.log"


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ("fingerprint", "fingerprint is sha256:"),
        ("team", "signed by ABCDE12345, not Ollama's (3MU9H2V9Y9)"),
        ("unsigned", "signature does not check out"),
        ("notarization", "notarization"),
        ("xgrammar", "no mlx_metal_v3/libollama_xgrammar.dylib"),
    ],
)
def test_a_download_that_does_not_check_out_installs_nothing(tmp_path: Path, home: Path, change: str, why: str) -> None:
    tgz = tarball(tmp_path, leave_out=[XGRAMMAR[0]] if change == "xgrammar" else [])
    release = Release("0.40.4", "0" * 64) if change == "fingerprint" else release_of(tgz)
    tools = FakeTools(tmp_path)
    tools.tarball = tgz
    if change == "team":
        tools.teams["libmlx.dylib"] = "ABCDE12345"
    if change == "unsigned":
        tools.unsigned.add("llama-server")
    if change == "notarization":
        tools.notarized = "ollama: rejected\n"
    step, _said = install(tools, home, release=release)
    with pytest.raises(SystemExit, match="nothing was installed") as stopped:
        step.run(32_768)
    assert why in str(stopped.value)
    assert sorted(p.name for p in (home / "opt/ollama").iterdir()) == []  # no folder, no link, no download left
    assert not any(change[0] == SUDO for change in tools.changes) and not (home / ".zprofile").exists()


def test_a_failed_download_says_so_and_installs_nothing(tmp_path: Path, home: Path) -> None:
    tools = FakeTools(tmp_path)
    tools.fail.add("/usr/bin/curl")
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="the download did not finish"):
        step.run(32_768)
    assert list((home / "opt/ollama").iterdir()) == []


def test_a_folder_already_there_is_checked_and_kept_without_a_download(tmp_path: Path, home: Path) -> None:
    official_tree(home / "opt/ollama/v0.40.4")
    tools = FakeTools(tmp_path, versions=[None, "0.40.4"], program=str(home / "opt/ollama/v0.40.4/ollama"))
    step, said = install(tools, home)
    step.run(32_768)
    assert not any(change[0] == "/usr/bin/curl" for change in tools.changes)
    assert any("v0.40.4 is there and checks out" in line for line in said)


def test_a_folder_already_there_that_does_not_check_out_is_left_for_you(tmp_path: Path, home: Path) -> None:
    official_tree(home / "opt/ollama/v0.40.4", leave_out=[XGRAMMAR[1]])
    tools = FakeTools(tmp_path)
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="Move it aside") as stopped:
        step.run(32_768)
    assert f"mv {home / 'opt/ollama/v0.40.4'} {home / 'opt/ollama/v0.40.4'}.refused" in str(stopped.value)
    assert (home / "opt/ollama/v0.40.4/ollama").exists() and not tools.changes  # never deleted, nothing run


def test_an_official_ollama_that_answers_is_kept_and_nothing_is_installed(tmp_path: Path, home: Path) -> None:
    """The owner's Mac: Ollama's official build already runs (its own daemon): setup keeps it and goes on."""
    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    job_file(tmp_path / "root/Library/LaunchDaemons", "com.example.ollama", str(program))
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(program))
    step, said = install(tools, home)
    assert step.run(32_768) == str(program)
    assert not tools.changes and not (home / "opt").exists() and not (home / ".zprofile").exists()
    assert said == [f"✓ Ollama: its official build 0.40.4 answers ({program}); kept."]
    # its whole folder checked, not only the program: every library's signature, the program's notarization
    assert any(read[0] == SPCTL and read[-1] == str(program) for read in tools.reads)


def test_a_kept_ollama_that_nothing_starts_with_the_mac_is_named(tmp_path: Path, home: Path) -> None:
    """Started by hand, or by a login item: after a restart nothing answers until someone starts it again."""
    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(program))
    step, said = install(tools, home)
    assert step.run(32_768) == str(program)
    text = "\n".join(said)
    assert f"! Ollama: no background service starts it with the Mac ({program})" in text
    assert "it installs Ollama's official build as a background service that starts with the Mac" in text


def test_a_kept_ollama_of_another_version_is_named_untested(tmp_path: Path, home: Path) -> None:
    """Not setup's own service: setup keeps it, says its version is not the tested one, and changes nothing."""
    program = official_tree(tmp_path / "theirs/v0.40.0") / "ollama"
    job_file(tmp_path / "root/Library/LaunchDaemons", "com.example.ollama", str(program))
    tools = FakeTools(tmp_path, versions=["0.40.0"], program=str(program))
    step, said = install(tools, home)
    assert step.run(32_768) == str(program) and not tools.changes
    assert "! Ollama: 0.40.0 answers, not 0.40.4, the version she is tested with" in "\n".join(said)


def test_homebrews_ollama_installed_next_to_the_official_one_is_named(tmp_path: Path, home: Path) -> None:
    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    job_file(tmp_path / "root/Library/LaunchDaemons", "com.example.ollama", str(program))
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(program))
    (tools.root / "opt/homebrew/Cellar/ollama/0.40.4/bin").mkdir(parents=True)
    step, said = install(tools, home)
    step.run(32_768)
    text = "\n".join(said)
    assert "! Ollama: Homebrew's Ollama is installed too (/opt/homebrew/Cellar/ollama)" in text
    assert "brew services stop ollama, then brew uninstall ollama" in text


def test_homebrews_ollama_installed_with_nothing_answering_is_named_and_the_install_goes_on(
    tmp_path: Path, home: Path
) -> None:
    tgz = tarball(tmp_path)
    tools = FakeTools(tmp_path, versions=[None, None, "0.40.4"], program=str(home / "opt/ollama/v0.40.4/ollama"))
    tools.tarball = tgz
    (tools.root / "opt/homebrew/Cellar/ollama/0.40.4/bin").mkdir(parents=True)
    step, said = install(tools, home, release=release_of(tgz))
    assert step.run(32_768) == str(home / "opt/ollama/current/ollama")
    text = "\n".join(said)
    assert "! Ollama: Homebrew's Ollama is installed too (/opt/homebrew/Cellar/ollama)" in text
    assert "✓ Ollama: 0.40.4 answers, always on" in text
    assert (SUDO, LAUNCHCTL, "bootstrap", "system", str(DAEMON)) in tools.sudo()


def test_homebrews_ollama_answering_stops_setup_before_anything(tmp_path: Path, home: Path) -> None:
    tools = FakeTools(tmp_path, versions=["0.40.4"], program="/opt/homebrew/Cellar/ollama/0.40.4/bin/ollama")
    tools.teams["ollama"] = ""
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="Homebrew's Ollama answers") as stopped:
        step.run(32_768)
    assert "✗ Ollama:" in str(stopped.value) and "Fix: brew services stop ollama" in str(stopped.value)
    assert not tools.changes and not (home / "opt").exists()


def test_the_ollama_app_on_the_mac_stops_setup_before_the_service_is_installed(tmp_path: Path, home: Path) -> None:
    tools = FakeTools(tmp_path)
    (tools.root / "Applications/Ollama.app").mkdir(parents=True)
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="the Ollama app is installed") as stopped:
        step.run(32_768)
    assert "mv /Applications/Ollama.app ~/.Trash/" in str(stopped.value)
    assert not tools.changes


# ------------------------------------------------------------------ other background services that run Ollama


def test_the_jobs_that_run_ollama_are_read_without_following_links(tmp_path: Path) -> None:
    daemons = tmp_path / "root/Library/LaunchDaemons"
    agents = tmp_path / "home/Library/LaunchAgents"
    job_file(daemons, "com.local-llm.ollama", "/Users/example/opt/ollama/current/ollama")
    job_file(daemons, "com.example.other", "/usr/local/bin/other")
    job_file(agents, "homebrew.mxcl.ollama", "/opt/homebrew/opt/ollama/bin/ollama")
    (daemons / "broken.plist").write_text("not a plist", encoding="utf-8")
    (daemons / "linked.plist").symlink_to(agents / "homebrew.mxcl.ollama.plist")  # never followed
    (daemons / "program.plist").write_bytes(plistlib.dumps({"Label": "com.example.app", "Program": "/x/Ollama"}))
    assert ollama_jobs(tmp_path / "root", agents) == [
        Job(
            "com.local-llm.ollama",
            "/Users/example/opt/ollama/current/ollama",
            Path("/Library/LaunchDaemons/com.local-llm.ollama.plist"),
            True,
        ),
        Job("com.example.app", "/x/Ollama", Path("/Library/LaunchDaemons/program.plist"), True),
        Job(
            "homebrew.mxcl.ollama", "/opt/homebrew/opt/ollama/bin/ollama", agents / "homebrew.mxcl.ollama.plist", False
        ),
    ]
    assert ollama_jobs(tmp_path / "root") == ollama_jobs(tmp_path / "root", agents)[:2]  # without your agents


@pytest.mark.parametrize(
    ("arguments", "program"),
    [
        (["/usr/bin/env", "ollama", "serve"], "ollama"),
        (
            ["/usr/bin/env", "OLLAMA_HOST=127.0.0.1:11434", "/Users/example/opt/ollama/current/ollama", "serve"],
            "/Users/example/opt/ollama/current/ollama",
        ),
        (
            ["/bin/sh", "-c", "exec /Users/example/opt/ollama/current/ollama serve"],
            "/Users/example/opt/ollama/current/ollama",
        ),
        (["/bin/zsh", "-lc", "cd ~ && OLLAMA_KEEP_ALIVE=1h ollama serve >> ~/ollama.log 2>&1"], "ollama"),
        (["/bin/bash", "-c", '"$HOME/opt/ollama/current/ollama" serve'], "$HOME/opt/ollama/current/ollama"),
        (["/bin/sh", "-c", "exec ollama serve 'unbalanced"], "ollama"),
    ],
)
def test_a_job_that_starts_ollama_through_a_wrapper_is_found(
    tmp_path: Path, arguments: list[str], program: str
) -> None:
    """`/bin/sh -c "ollama serve"` or `/usr/bin/env ollama serve` runs Ollama as surely as its own path does."""
    daemons = tmp_path / "root/Library/LaunchDaemons"
    daemons.mkdir(parents=True)
    plist = {"Label": "com.example.wrapped", "ProgramArguments": arguments}
    (daemons / "com.example.wrapped.plist").write_bytes(plistlib.dumps(plist))
    job = Job("com.example.wrapped", program, Path("/Library/LaunchDaemons/com.example.wrapped.plist"), True, True)
    assert ollama_jobs(tmp_path / "root") == [job]
    assert ollama_build.job_kind(job, program) == "other"


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "/opt/homebrew/opt/python@3.13/bin/python3.13",
            "-I",
            "/Users/example/dev/local-llm/scripts/ollama-watchdog.py",
        ],
        ["/bin/sh", "-c", "/Users/example/opt/ollama/current/ollama pull gemma4:26b-mlx"],
        ["/usr/local/bin/backup", "--exclude", "/Users/example/.ollama"],
        ["/bin/sh", "-c", "echo ollama-serve"],
    ],
)
def test_a_job_that_only_names_ollama_is_not_one_that_runs_it(tmp_path: Path, arguments: list[str]) -> None:
    daemons = tmp_path / "root/Library/LaunchDaemons"
    daemons.mkdir(parents=True)
    (daemons / "com.example.other.plist").write_bytes(
        plistlib.dumps({"Label": "com.example.other", "ProgramArguments": arguments})
    )
    assert ollama_jobs(tmp_path / "root") == []


def test_a_wrapped_job_that_runs_homebrews_ollama_is_still_another_service(tmp_path: Path, home: Path) -> None:
    """Its path may be Homebrew's, but `brew services stop` does not remove someone's own wrapper: setup names the
    job with the command that starts it again, and stops while nothing answers."""
    daemons = tmp_path / "root/Library/LaunchDaemons"
    daemons.mkdir(parents=True)
    plist = {"Label": "com.example.wrapped", "ProgramArguments": ["/bin/sh", "-c", "/opt/homebrew/bin/ollama serve"]}
    (daemons / "com.example.wrapped.plist").write_bytes(plistlib.dumps(plist))
    tools = FakeTools(tmp_path)
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="another background service runs Ollama") as stopped:
        step.run(32_768)
    assert "/usr/bin/sudo /bin/launchctl kickstart -k system/com.example.wrapped" in str(stopped.value)
    assert not tools.changes and not (home / "opt").exists()


def test_another_service_that_runs_ollama_stops_setup_when_nothing_answers(tmp_path: Path, home: Path) -> None:
    """The owner's own LaunchDaemon, down for a moment: a second always-on service for the same program and port
    would fight it forever."""
    job_file(tmp_path / "root/Library/LaunchDaemons", "com.local-llm.ollama", str(home / "opt/ollama/current/ollama"))
    tools = FakeTools(tmp_path)
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="another background service runs Ollama") as stopped:
        step.run(32_768)
    text = str(stopped.value)
    assert "com.local-llm.ollama" in text and "/Library/LaunchDaemons/com.local-llm.ollama.plist" in text
    assert "/usr/bin/sudo /bin/launchctl kickstart -k system/com.local-llm.ollama" in text
    assert "/usr/bin/sudo /bin/launchctl bootstrap system /Library/LaunchDaemons/com.local-llm.ollama.plist" in text
    assert not tools.changes and not (home / "opt").exists()


def test_your_own_login_agent_that_runs_ollama_is_named_with_its_gui_restart(tmp_path: Path, home: Path) -> None:
    job_file(home / "Library/LaunchAgents", "com.example.ollama", "/usr/local/bin/ollama")
    tools = FakeTools(tmp_path)
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="another background service runs Ollama") as stopped:
        step.run(32_768)
    assert f"/bin/launchctl kickstart -k gui/{os.getuid()}/com.example.ollama" in str(stopped.value)
    assert "sudo /bin/launchctl kickstart -k gui" not in str(stopped.value) and not tools.changes


def recorded_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every path the step resolves or looks for, as it asks (os.path's realpath, lexists, exists, isfile, isdir,
    islink)."""
    seen: list[str] = []
    for name in ("realpath", "lexists", "exists", "isfile", "isdir", "islink"):
        real = getattr(os.path, name)

        def recording(path: str | Path, *args: object, _real: Callable[..., object] = real, **kwargs: object) -> object:
            seen.append(os.fspath(path))
            return _real(path, *args, **kwargs)

        monkeypatch.setattr(os.path, name, recording)
    return seen


def outside(paths: Sequence[str], tmp_path: Path) -> list[str]:
    """The paths that are not the test's own (its tmp_path, or a folder above it)."""
    mine = str(tmp_path.resolve())
    return [path for path in paths if not (path.startswith(mine) or mine.startswith(path.rstrip("/") + "/"))]


def test_a_login_agent_reads_nothing_of_the_mac_the_tests_run_on(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job's program (/usr/local/bin/ollama) is resolved in the test's tree: on a Mac with the Ollama app, the
    live /usr/local/bin/ollama links into it, and this job was named as the app's."""
    job_file(home / "Library/LaunchAgents", "com.example.ollama", "/usr/local/bin/ollama")
    tools = FakeTools(tmp_path)
    step, _said = install(tools, home)
    seen = recorded_reads(monkeypatch)
    with pytest.raises(SystemExit, match="another background service runs Ollama"):
        step.run(32_768)
    assert seen and outside(seen, tmp_path) == []


def test_a_job_whose_program_links_into_the_ollama_app_is_named_as_the_apps(tmp_path: Path, home: Path) -> None:
    """The Ollama app puts its command at /usr/local/bin/ollama, a link into Ollama.app: in the test's tree too."""
    tools = FakeTools(tmp_path)
    program = tools.root / "Applications/Ollama.app/Contents/Resources/ollama"
    program.parent.mkdir(parents=True)
    program.write_bytes(MACHO)
    link = tools.root / "usr/local/bin/ollama"
    link.parent.mkdir(parents=True)
    link.symlink_to("../../../Applications/Ollama.app/Contents/Resources/ollama")
    job_file(home / "Library/LaunchAgents", "com.example.ollama", "/usr/local/bin/ollama")
    step, _said = install(tools, home)
    with pytest.raises(SystemExit) as stopped:
        step.run(32_768)
    plist = home / "Library/LaunchAgents/com.example.ollama.plist"
    assert prompts.OLLAMA_JOB_APP.format(plist=plist) in str(stopped.value)
    assert "Fix: " + prompts.OLLAMA_APP_FIX.format(path="/Applications/Ollama.app") in str(stopped.value)


def test_an_absolute_link_into_the_ollama_app_is_followed_in_the_tests_tree(tmp_path: Path, home: Path) -> None:
    """The real Ollama app links /usr/local/bin/ollama with an absolute target: in the fake Mac it points into the
    test's tree, never at the Mac the tests run on."""
    tools = FakeTools(tmp_path)
    program = tools.root / "Applications/Ollama.app/Contents/Resources/ollama"
    program.parent.mkdir(parents=True)
    program.write_bytes(MACHO)
    link = tools.root / "usr/local/bin/ollama"
    link.parent.mkdir(parents=True)
    link.symlink_to("/Applications/Ollama.app/Contents/Resources/ollama")
    assert tools.resolve("/usr/local/bin/ollama") == "/Applications/Ollama.app/Contents/Resources/ollama"
    job_file(home / "Library/LaunchAgents", "com.example.ollama", "/usr/local/bin/ollama")
    step, _said = install(tools, home)
    with pytest.raises(SystemExit) as stopped:
        step.run(32_768)
    plist = home / "Library/LaunchAgents/com.example.ollama.plist"
    assert prompts.OLLAMA_JOB_APP.format(plist=plist) in str(stopped.value)


class BlindTools(FakeTools):
    """Off the live Mac (a test's ESTIA_ROOT): setup's Ollama step only shows what it would do."""

    @property
    def live(self) -> bool:
        return False


def test_a_blind_run_never_reads_the_ollama_app_of_the_mac_it_runs_on(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the test tree's /Applications/Ollama.app counts: an app at the real path (here: a folder that exists
    outside the tree, standing in for it) is never looked at, and so never named."""
    app = tmp_path / "elsewhere/Ollama.app"
    (app / "Contents/Resources").mkdir(parents=True)
    monkeypatch.setattr(ollama_build, "APP", app)
    tools = BlindTools(tmp_path)
    tools.tarball = tarball(tmp_path)
    step, said = install(tools, home)
    seen = recorded_reads(monkeypatch)
    step.run(32_768)
    assert not any("Ollama app" in line for line in said)
    assert str(app) not in seen and outside(seen, tmp_path) == []
    (tools.root / app.relative_to("/")).mkdir(parents=True)  # the test tree's own app: named
    step, said = install(tools, home)
    step.run(32_768)
    assert any(prompts.OLLAMA_APP.format(state="is installed", path=app) in line for line in said)


@pytest.mark.parametrize("answering", [True, False], ids=["official-answers", "nothing-answers"])
def test_homebrews_service_file_stops_setup(tmp_path: Path, home: Path, answering: bool) -> None:
    """brew services' file starts Homebrew's Ollama at each login: it can take the port first, without JSON schema."""
    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    job_file(tmp_path / "root/Library/LaunchDaemons", "com.example.ollama", str(program))
    job_file(home / "Library/LaunchAgents", "homebrew.mxcl.ollama", "/opt/homebrew/opt/ollama/bin/ollama")
    tools = FakeTools(tmp_path, versions=["0.40.4" if answering else None], program=str(program) if answering else "")
    (tools.root / "opt/homebrew/Cellar/ollama/0.40.4/bin").mkdir(parents=True)
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="Homebrew's background service for Ollama") as stopped:
        step.run(32_768)
    assert str(home / "Library/LaunchAgents/homebrew.mxcl.ollama.plist") in str(stopped.value)
    assert "Fix: brew services stop ollama, then brew uninstall ollama" in str(stopped.value) and not tools.changes


# ------------------------------------------------------------------ the service file, its log, the folders it runs from


def test_a_service_file_already_in_place_is_started_again_not_rewritten(tmp_path: Path, home: Path) -> None:
    """Setup's own service is never "another one": its file in place, it is started again."""
    official_tree(home / "opt/ollama/v0.40.4")
    tools = FakeTools(tmp_path, versions=[None, "0.40.4"], program=str(home / "opt/ollama/v0.40.4/ollama"))
    daemon = tools.root / DAEMON.relative_to("/")
    daemon.parent.mkdir(parents=True)
    daemon.write_bytes(daemon_plist(user="alex", group="staff", home=home, num_ctx=32_768))
    private_log(tools)
    step, _said = install(tools, home)
    step.run(32_768)
    assert tools.sudo() == [(SUDO, LAUNCHCTL, "kickstart", "-k", f"system/{LABEL}")]


def test_a_service_that_is_not_loaded_is_loaded_again(tmp_path: Path, home: Path) -> None:
    """A booted-out service: kickstart says it cannot find it; bootstrap loads it again."""
    official_tree(home / "opt/ollama/v0.40.4")
    tools = FakeTools(tmp_path, versions=[None, "0.40.4"], program=str(home / "opt/ollama/v0.40.4/ollama"))
    daemon = tools.root / DAEMON.relative_to("/")
    daemon.parent.mkdir(parents=True)
    daemon.write_bytes(daemon_plist(user="alex", group="staff", home=home, num_ctx=32_768))
    private_log(tools)
    tools.fail.add(f"{SUDO} {LAUNCHCTL} kickstart")
    step, _said = install(tools, home)
    step.run(32_768)
    assert [change[1:3] for change in tools.sudo()] == [(LAUNCHCTL, "kickstart"), (LAUNCHCTL, "bootstrap")]


def test_a_changed_service_file_is_replaced_and_loaded_again(tmp_path: Path, home: Path) -> None:
    official_tree(home / "opt/ollama/v0.40.4")
    tools = FakeTools(tmp_path, versions=[None, "0.40.4"], program=str(home / "opt/ollama/v0.40.4/ollama"))
    daemon = tools.root / DAEMON.relative_to("/")
    daemon.parent.mkdir(parents=True)
    daemon.write_bytes(daemon_plist(user="alex", group="staff", home=home, num_ctx=16_384))
    step, _said = install(tools, home)
    step.run(32_768)
    sudo = [change[1:3] for change in tools.sudo()]
    assert sudo[-2:] == [(LAUNCHCTL, "bootout"), (LAUNCHCTL, "bootstrap")]


@pytest.mark.parametrize("state", ["roots", "linked", "shared"])
def test_a_log_that_is_not_your_private_file_is_made_again(tmp_path: Path, home: Path, state: str) -> None:
    """launchd made it as root (0644: every account, hers too, reads it), or it is a second name of another file."""
    official_tree(home / "opt/ollama/v0.40.4")
    tools = FakeTools(tmp_path, versions=[None, "0.40.4"], program=str(home / "opt/ollama/v0.40.4/ollama"))
    daemon = tools.root / DAEMON.relative_to("/")
    daemon.parent.mkdir(parents=True)
    daemon.write_bytes(daemon_plist(user="alex", group="staff", home=home, num_ctx=32_768))
    log = private_log(tools)
    if state == "roots":
        log.chmod(0o644)
    elif state == "linked":
        log.unlink()
        log.symlink_to(tmp_path / "elsewhere")
    else:
        os.link(log, tmp_path / "second-name")
    step, _said = install(tools, home)
    step.run(32_768)
    assert tools.sudo() == [
        (SUDO, "/usr/bin/install", "-d", "-m", "755", "-o", "root", "-g", "wheel", "/var/log/estia"),
        (SUDO, "/usr/bin/install", "-m", "600", "-o", "alex", "-g", "staff", "/dev/null", str(LOG)),
        (SUDO, LAUNCHCTL, "kickstart", "-k", f"system/{LABEL}"),
    ]


def test_a_private_log_over_10_mb_is_emptied_without_admin_rights(tmp_path: Path, home: Path) -> None:
    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    job_file(tmp_path / "root/Library/LaunchDaemons", "com.example.ollama", str(program))
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(program))
    log = private_log(tools)
    log.write_bytes(b"[GIN] 200 | POST /api/chat\n" * 400_000)  # about 11 MB
    step, said = install(tools, home)
    step.run(32_768)
    assert log.stat().st_size == 0 and "its log was over 10 MB and is now empty" in "\n".join(said)
    assert not tools.sudo()


def test_a_folder_others_can_change_on_the_way_to_the_program_stops_before_sudo(tmp_path: Path, home: Path) -> None:
    """The service runs ~/opt/ollama/current/ollama as you at every start: a folder another account can change on
    that way would let it plant the program."""
    official_tree(home / "opt/ollama/v0.40.4")
    (home / "opt").chmod(0o777)
    try:
        tools = FakeTools(tmp_path, versions=[None, "0.40.4"], program=str(home / "opt/ollama/v0.40.4/ollama"))
        step, _said = install(tools, home)
        with pytest.raises(SystemExit, match="other accounts can change it") as stopped:
            step.run(32_768)
        assert f"/bin/chmod go-w {home / 'opt'}" in str(stopped.value) and not tools.sudo()
    finally:
        (home / "opt").chmod(0o755)


def test_your_home_comes_from_the_user_database_not_from_home(monkeypatch: pytest.MonkeyPatch) -> None:
    import pwd

    monkeypatch.setenv("HOME", "/tmp/elsewhere")
    assert ollama_build.owner_home() == Path(pwd.getpwuid(os.getuid()).pw_dir)


def test_no_answer_after_the_service_started_says_where_to_look(tmp_path: Path, home: Path) -> None:
    official_tree(home / "opt/ollama/v0.40.4")
    tools = FakeTools(tmp_path, versions=[None])
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="no answer within 60 s") as stopped:
        step.run(32_768)
    assert f"/usr/bin/sudo /bin/launchctl print system/{LABEL}" in str(stopped.value) and str(LOG) in str(stopped.value)


# ------------------------------------------------------------------ an update of setup's own service, in place


def ours_at(tmp_path: Path, home: Path, version: str = "0.40.4") -> Path:
    """Setup's own install of `version`: its folder, `current` pointing at it, the service file and a private log."""
    folder = official_tree(home / f"opt/ollama/v{version}")
    (home / "opt/ollama/current").symlink_to(folder)
    daemon = tmp_path / "root" / DAEMON.relative_to("/")
    daemon.parent.mkdir(parents=True, exist_ok=True)
    daemon.write_bytes(daemon_plist(user="alex", group="staff", home=home, num_ctx=32_768))
    return folder


def test_a_new_pin_is_installed_in_place_while_the_old_one_runs(tmp_path: Path, home: Path) -> None:
    old = ours_at(tmp_path, home)
    tgz = tarball(tmp_path)
    new = home / "opt/ollama/v0.40.5"
    tools = FakeTools(tmp_path, versions=["0.40.4", "0.40.5"], program=[str(old / "ollama"), str(new / "ollama")])
    tools.tarball = tgz
    private_log(tools)
    step, said = install(tools, home, release=release_of(tgz, "0.40.5"))
    assert step.run(32_768) == str(home / "opt/ollama/current/ollama")
    assert os.readlink(home / "opt/ollama/current") == str(new) and (old / "ollama").is_file()  # the old one kept
    assert tools.sudo() == [(SUDO, LAUNCHCTL, "kickstart", "-k", f"system/{LABEL}")]  # one restart, no bootout
    text = "\n".join(said)
    assert "Ollama: 0.40.4 answers; the tested version is now 0.40.5" in text
    assert "✓ Ollama: 0.40.5 answers, always on" in text


def test_an_update_that_does_not_check_out_leaves_the_old_one_running(tmp_path: Path, home: Path) -> None:
    old = ours_at(tmp_path, home)
    tgz = tarball(tmp_path)
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(old / "ollama"))
    tools.tarball = tgz
    step, _said = install(tools, home, release=Release("0.40.5", "0" * 64))  # its fingerprint is not the pinned one
    with pytest.raises(SystemExit, match="nothing was installed"):
        step.run(32_768)
    assert os.readlink(home / "opt/ollama/current") == str(old) and not tools.sudo()
    assert not (home / "opt/ollama/v0.40.5").exists()


def test_an_update_that_does_not_answer_goes_back_to_the_old_one(tmp_path: Path, home: Path) -> None:
    old = ours_at(tmp_path, home)
    tgz = tarball(tmp_path)
    tools = FakeTools(tmp_path, versions=["0.40.4", None], program=str(old / "ollama"))
    tools.tarball = tgz
    private_log(tools)
    step, _said = install(tools, home, release=release_of(tgz, "0.40.5"))
    with pytest.raises(SystemExit) as stopped:
        step.run(32_768)
    assert os.readlink(home / "opt/ollama/current") == str(old)
    assert tools.sudo() == [(SUDO, LAUNCHCTL, "kickstart", "-k", f"system/{LABEL}")] * 2
    text = str(stopped.value)
    assert "no answer within 60 s" in text and f"{old} is in use again" in text


def test_an_update_with_a_folder_others_can_change_leaves_the_old_one_running(tmp_path: Path, home: Path) -> None:
    old = ours_at(tmp_path, home)
    tgz = tarball(tmp_path)
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(old / "ollama"))
    tools.tarball = tgz
    (home / "opt/ollama").chmod(0o775)  # its group can change it
    try:
        step, _said = install(tools, home, release=release_of(tgz, "0.40.5"))
        with pytest.raises(SystemExit, match="other accounts can change it"):
            step.run(32_768)
    finally:
        (home / "opt/ollama").chmod(0o755)
    assert os.readlink(home / "opt/ollama/current") == str(old) and not tools.sudo()


def test_setups_own_service_file_from_an_older_setup_is_written_again(tmp_path: Path, home: Path) -> None:
    """A later release changes the service file (a setting, the context length): setup's own service at the pin is
    brought up to date (the file from tee's input, then bootout, bootstrap and the wait), not only kept."""
    folder = ours_at(tmp_path, home)
    daemon = tmp_path / "root" / DAEMON.relative_to("/")
    daemon.write_bytes(daemon_plist(user="alex", group="staff", home=home, num_ctx=16_384))  # an older setup's
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(folder / "ollama"))
    private_log(tools)
    step, said = install(tools, home)
    assert step.run(32_768) == str(folder / "ollama")
    sudo = [change[1:3] for change in tools.sudo()]
    assert (TEE, str(DAEMON)) in sudo and sudo[-2:] == [(LAUNCHCTL, "bootout"), (LAUNCHCTL, "bootstrap")]
    assert tools.inputs == [daemon_plist(user="alex", group="staff", home=home, num_ctx=32_768)]
    text = "\n".join(said)
    assert f"its background service file ({DAEMON}) is not the one this version of setup writes" in text
    assert "✓ Ollama: 0.40.4 answers, always on" in text


def test_a_kept_ollama_that_is_not_setups_own_never_gets_setups_service_file(tmp_path: Path, home: Path) -> None:
    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    job_file(tmp_path / "root/Library/LaunchDaemons", "com.example.ollama", str(program))
    daemon = tmp_path / "root" / DAEMON.relative_to("/")
    daemon.write_bytes(daemon_plist(user="alex", group="staff", home=home, num_ctx=16_384))
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(program))
    step, _said = install(tools, home)
    step.run(32_768)
    assert not any(change[1] == TEE for change in tools.sudo())


def test_the_service_file_check_names_one_that_differs_from_what_setup_writes(tmp_path: Path) -> None:
    """Doctor's question (read-only): the file setup would write for the account the service runs as."""
    import grp
    import pwd

    me = pwd.getpwuid(os.getuid())
    group = grp.getgrgid(me.pw_gid).gr_name
    root = tmp_path / "root"
    assert ollama_build.service_file_changed(root, 32_768) is None  # no service of setup's
    daemon = root / DAEMON.relative_to("/")
    daemon.parent.mkdir(parents=True)
    daemon.write_bytes(daemon_plist(user=me.pw_name, group=group, home=Path(me.pw_dir), num_ctx=32_768))
    assert ollama_build.service_file_changed(root, 32_768) is False
    assert ollama_build.service_file_changed(root, 16_384) is True
    daemon.write_bytes(daemon.read_bytes().replace(b"120m", b"5m"))  # by hand, or an older setup
    assert ollama_build.service_file_changed(root, 32_768) is True


def test_setups_own_service_at_the_pin_gets_a_private_log_and_nothing_else(tmp_path: Path, home: Path) -> None:
    """An install from before: launchd made the log as root, readable by every account. Made yours and private now;
    Ollama writes to it from its next start."""
    folder = ours_at(tmp_path, home)
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(folder / "ollama"))
    private_log(tools).chmod(0o644)
    step, said = install(tools, home)
    assert step.run(32_768) == str(folder / "ollama")
    assert tools.sudo() == [(SUDO, "/usr/bin/install", "-m", "600", "-o", "alex", "-g", "staff", "/dev/null", str(LOG))]
    assert "made yours and private, used from its next start" in "\n".join(said)


# ------------------------------------------------------------------ a dry run, and off the live Mac


def test_a_dry_run_reads_but_changes_nothing(tmp_path: Path, home: Path) -> None:
    tools = FakeTools(tmp_path)
    step, said = install(tools, home, dry_run=True)
    assert step.run(32_768) == str(home / "opt/ollama/current/ollama")
    assert not tools.changes and not (home / "opt").exists() and not (home / ".zprofile").exists()
    text = "\n".join(said)
    assert (
        "$ /usr/bin/curl -q -fL --proto =https --proto-redir =https --retry 3 --connect-timeout 20 --speed-limit "
        "10240 --speed-time 60 --max-filesize 300000000 -o" in text
    )
    assert RELEASE.url in text
    assert f"$ {SUDO} {TEE} {DAEMON}   (the service file on its input)" in text
    assert f"$ {SUDO} {LAUNCHCTL} bootstrap system {DAEMON}" in text
    assert "would add" in text and "sudo asks for your Mac password" in text


@pytest.mark.parametrize("what", ["app", "homebrew-answers"])
def test_a_dry_run_shows_what_stops_setup_and_goes_on(tmp_path: Path, home: Path, what: str) -> None:
    """The AI's briefing run: the ✗ with its fix, then the rest as it would run after the fix."""
    if what == "app":
        tools = FakeTools(tmp_path)
        (tools.root / "Applications/Ollama.app").mkdir(parents=True)
    else:
        tools = FakeTools(tmp_path, versions=["0.40.4"], program="/opt/homebrew/Cellar/ollama/0.40.4/bin/ollama")
    step, said = install(tools, home, dry_run=True)
    assert step.run(32_768) == str(home / "opt/ollama/current/ollama")
    text = "\n".join(said)
    assert "✗ Ollama:" in text and "(setup stops here until this is fixed; the rest as it would run after)" in text
    assert f"$ {SUDO} {LAUNCHCTL} bootstrap system {DAEMON}" in text and not tools.changes


def test_off_the_live_mac_nothing_is_read_or_run(tmp_path: Path) -> None:
    """Under a test's root (ESTIA_ROOT): no Ollama answers, no program listens, every change is only printed."""
    said: list[str] = []
    tools = Tools(tmp_path, say=said.append)
    assert not tools.live and tools.version() is None and tools.listener() == Listener("")
    assert tools.read([CODESIGN, "-dv", "/bin/ls"]) == (127, "")
    assert tools.change([SUDO, LAUNCHCTL, "bootstrap", "system", str(DAEMON)]) == 0
    assert said == [f"would run: {SUDO} {LAUNCHCTL} bootstrap system {DAEMON}"]


# ------------------------------------------------------------------ the program on Ollama's port, and where it listens


class NetstatHost(Host):
    def __init__(self, out: str, programs: dict[str, str], lsof: str = "", uids: dict[str, str] | None = None) -> None:
        super().__init__(Path("/"))
        self.out = out
        self.lsof = lsof
        self.programs = programs
        self.uids = uids or {}  # pid -> `ps -o uid= -p <pid>`'s output
        self.asked: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        if argv[0] == "/usr/sbin/netstat":
            return 0, self.out
        if argv[0] == "/bin/ps" and argv[-1] in self.uids:
            return 0, self.uids[argv[-1]]
        return (0, self.lsof) if argv[0] == "/usr/sbin/lsof" else (1, "")

    def program_path(self, pid: str) -> str:
        return self.programs.get(pid, "")


NETSTAT = (
    "tcp4  0  0  127.0.0.1.53227  127.0.0.1.53305  ESTABLISHED  262  206  408256  146988  ollama:21196  00102\n"
    "tcp4  0  0  127.0.0.1.11434  *.*  LISTEN  0  0  131072  131072  ollama:68243  00100 00000006\n"
    "tcp4  0  0  127.0.0.1.8150   *.*  LISTEN  0  0  131072  131072  voice-servi:4242  00100 00000006\n"
)


def test_the_program_that_listens_on_ollamas_port_is_found_by_its_process() -> None:
    host = NetstatHost(NETSTAT, {"68243": "/opt/ollama/v0.40.4/ollama", "21196": "/elsewhere"})
    assert Tools(Path("/"), host=host).listener() == Listener("/opt/ollama/v0.40.4/ollama", ("127.0.0.1.11434",))
    assert host.asked[0] == ("/usr/sbin/netstat", "-anv", "-p", "tcp")


@pytest.mark.parametrize(
    ("line", "address"),
    [
        ("tcp46  0  0  *.11434  *.*  LISTEN  0  0  131072  131072  ollama:68243  00100 00000006\n", "*.11434"),
        ("tcp4  0  0  *.11434  *.*  LISTEN  0  0  131072  131072  ollama:68243  00100 00000006\n", "*.11434"),
        ("tcp6  0  0  ::1.11434  *.*  LISTEN  0  0  131072  131072  ollama:68243  00100 00000006\n", "::1.11434"),
    ],
)
def test_where_it_listens_is_read_from_netstat(line: str, address: str) -> None:
    host = NetstatHost(line, {"68243": "/opt/ollama/v0.40.4/ollama"})
    assert Tools(Path("/"), host=host).listener().addresses == (address,)


def test_the_account_that_runs_the_listener_is_read_with_ps() -> None:
    """ps names the account of any user's process (fixed argv, read-only); a pid it cannot name has no account."""
    host = NetstatHost(NETSTAT, {"68243": "/opt/ollama/v0.40.4/ollama"}, uids={"68243": "  502\n"})
    listener = Tools(Path("/"), host=host).listener()
    assert listener == Listener("/opt/ollama/v0.40.4/ollama", ("127.0.0.1.11434",), 502)
    assert ("/bin/ps", "-o", "uid=", "-p", "68243") in host.asked
    host = NetstatHost(NETSTAT, {"68243": "/opt/ollama/v0.40.4/ollama"}, uids={"68243": "nobody\n"})
    assert Tools(Path("/"), host=host).listener().uid is None


def test_lsof_names_your_own_listener_when_netstat_lists_nothing() -> None:
    """netstat started from some Pythons lists no internet sockets: lsof (your own processes) names it then."""
    host = NetstatHost("", {"68243": "/opt/ollama/v0.40.4/ollama"}, lsof="p68243\nf3\nn*:11434\n")
    assert Tools(Path("/"), host=host).listener() == Listener("/opt/ollama/v0.40.4/ollama", ("*.11434",))
    assert host.asked[1] == ("/usr/sbin/lsof", "-nP", "-iTCP:11434", "-sTCP:LISTEN", "-Fpn")
    host = NetstatHost("", {"7": "/x/ollama"}, lsof="p7\nf3\nn127.0.0.1:11434\nf4\nn[::1]:11434\n")
    assert Tools(Path("/"), host=host).listener() == Listener("/x/ollama", ("127.0.0.1.11434", "::1.11434"))


MINE = "tcp4  0  0  127.0.0.1.11434  *.*  LISTEN  0  0  131072  131072  ollama:68243  00100 00000006\n"
THEIRS = "tcp6  0  0  ::1.11434  *.*  LISTEN  0  0  131072  131072  ollama:70001  00100 00000006\n"


@pytest.mark.parametrize("theirs_first", [False, True], ids=["yours-first", "theirs-first"])
def test_every_program_on_ollamas_port_is_read_not_only_the_first(tmp_path: Path, theirs_first: bool) -> None:
    """127.0.0.1 and ::1 are two addresses: your Ollama on one leaves the other free for another account's. With
    "localhost" in her config the bridge may reach that one, so each listener's program and account counts."""
    program = str(official_tree(tmp_path / "opt/ollama/v0.40.4") / "ollama")
    other = os.getuid() + 1
    host = NetstatHost(
        THEIRS + MINE if theirs_first else MINE + THEIRS,
        {"68243": program, "70001": program},
        uids={"68243": f"{os.getuid()}\n", "70001": f"{other}\n"},
    )
    heard = Tools(Path("/"), host=host).listener()
    assert set(heard.addresses) == {"127.0.0.1.11434", "::1.11434"}
    found = Found(heard.program, "0.40.4", "", heard.addresses, heard.uid, heard.others)
    wrong = problem(found, owner=os.getuid())
    assert wrong is not None and account(other) in wrong[0], wrong


def test_another_program_on_ollamas_port_or_one_whose_account_is_not_read_is_named(tmp_path: Path) -> None:
    program = str(official_tree(tmp_path / "opt/ollama/v0.40.4") / "ollama")
    host = NetstatHost(MINE + THEIRS, {"68243": program, "70001": "/tmp/x/serve"}, uids={"68243": f"{os.getuid()}"})
    heard = Tools(Path("/"), host=host).listener()
    assert heard.program == program and heard.uid == os.getuid()
    assert heard.others == (("/tmp/x/serve", None),)
    wrong = problem(Found(heard.program, "0.40.4", "", heard.addresses, heard.uid, heard.others), owner=os.getuid())
    assert wrong is not None and "/tmp/x/serve" in wrong[0] and "could not be read" in wrong[0]


def test_one_program_on_both_addresses_is_one_listener(tmp_path: Path) -> None:
    """tcp4 and tcp6 sockets of one process (or two processes of yours, one program) are no second listener."""
    program = str(official_tree(tmp_path / "opt/ollama/v0.40.4") / "ollama")
    host = NetstatHost(
        MINE + MINE.replace("tcp4", "tcp6").replace("127.0.0.1", "::1"),
        {"68243": program},
        uids={"68243": f"{os.getuid()}"},
    )
    heard = Tools(Path("/"), host=host).listener()
    assert heard == Listener(program, ("127.0.0.1.11434", "::1.11434"), os.getuid())
    assert (
        problem(Found(heard.program, "0.40.4", "", heard.addresses, heard.uid, heard.others), owner=os.getuid()) is None
    )


@pytest.mark.parametrize("uid", [os.getuid() + 1, 0, None], ids=["another-account", "root", "not-read"])
def test_an_official_ollama_that_another_account_runs_stops_setup(tmp_path: Path, home: Path, uid: int | None) -> None:
    """Signed and on this Mac only, but run by another account (or caught on the port while setup's own restarts):
    her prompts would go to a program that account controls. One whose account cannot be read is not kept either."""
    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    job_file(tmp_path / "root/Library/LaunchDaemons", "com.example.ollama", str(program))
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(program))
    tools.uid = uid
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="not one setup can keep") as stopped:
        step.run(32_768)
    if uid is None:
        assert "the account it runs as could not be read" in str(stopped.value)
    else:
        assert f"it runs as {account(uid)}, another account on this Mac" in str(stopped.value)
    assert not tools.changes


def account(uid: int) -> str:
    import pwd

    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid {uid}"


def test_problem_names_the_account_by_its_name() -> None:
    wrong = problem(Found("/x/ollama", "0.40.4", signature="", uid=os.getuid()), owner=os.getuid() + 1)
    assert wrong is not None and f"it runs as {account(os.getuid())}, another account on this Mac" in wrong[0]
    assert problem(Found("/x/ollama", "0.40.4", signature="", uid=7), owner=None) is not None  # the library: missing
    assert "another account" not in str(problem(Found("/x/ollama", "0.40.4", uid=None), owner=os.getuid()))


def test_an_official_ollama_open_to_other_machines_stops_setup(tmp_path: Path, home: Path) -> None:
    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    tools = FakeTools(tmp_path, versions=["0.40.4"], program=str(program), addresses=("*.11434",))
    step, _said = install(tools, home)
    with pytest.raises(SystemExit, match="it listens for other machines too"):
        step.run(32_768)
    assert not tools.changes


# ------------------------------------------------------------------ setup's Ollama step and scripts/install-ollama.sh


def test_homebrews_ollama_answering_stops_setup_before_the_model_is_pulled(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_assistant import setup

    tools = FakeTools(tmp_path, versions=["0.40.4"], program="/opt/homebrew/Cellar/ollama/0.40.4/bin/ollama")
    monkeypatch.setattr(ollama_build, "Tools", lambda say=print: tools)
    monkeypatch.setattr(ollama_build, "owner_home", lambda: home)
    monkeypatch.setattr(setup, "memory_gb", lambda: 32.0)
    monkeypatch.setattr(setup, "has_terminal", lambda: True)
    pulled: list[str] = []
    monkeypatch.setattr(setup.Wizard, "model", lambda self: pulled.append("model"))
    with pytest.raises(SystemExit, match="Homebrew's Ollama answers"):
        setup.main(["--config", str(tmp_path / "config.toml")])
    assert not pulled and not tools.changes


def rerun_steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every step after the Ollama step recorded, none run; Homebrew's Ollama answers."""
    from local_assistant import setup

    reached: list[str] = []

    def step(name: str, result: object) -> Callable[..., object]:
        def reach(self: setup.Wizard, *args: object, **kwargs: object) -> object:
            reached.append(name)
            return result

        return reach

    for name in ("always_on", "security", "extras", "personality", "voice_end"):
        monkeypatch.setattr(setup.Wizard, name, step(name, True))
    monkeypatch.setattr(setup.Wizard, "check", step("check", 0))
    monkeypatch.setattr(setup, "memory_gb", lambda: 32.0)
    monkeypatch.setattr(setup, "has_terminal", lambda: True)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    return reached


@pytest.mark.parametrize("where", ["configured", "her-own-user"])
def test_a_rerun_shows_an_ollama_refusal_as_a_warning_and_goes_on(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """She is set up already: an older Ollama setup in place (Homebrew's answering) no longer blocks the security
    step, the extras or the check. The model step does not pull with an Ollama setup refused."""
    from local_assistant import doctor, hostsec, setup

    tools = FakeTools(tmp_path, versions=["0.40.4"], program="/opt/homebrew/Cellar/ollama/0.40.4/bin/ollama")
    monkeypatch.setattr(ollama_build, "Tools", lambda say=print: tools)
    monkeypatch.setattr(ollama_build, "owner_home", lambda: home)
    monkeypatch.setattr(doctor.Probe, "get_json", lambda self, url: {"models": []})  # no local-worker yet
    reached = rerun_steps(monkeypatch)
    config = tmp_path / "config.toml"
    if where == "configured":
        config.write_text(
            'bot_number = "+306900000000"\nallowed_acis = ["11111111-2222-4333-8444-555555555555"]\n', encoding="utf-8"
        )
        config.chmod(0o600)
    else:
        plist = hostsec.estia_root() / "Library/LaunchDaemons/com.local-assistant.bridge.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(plistlib.dumps({"Label": "com.local-assistant.bridge", "UserName": "_estia"}))
    pulled: list[list[str]] = []
    monkeypatch.setattr(setup.Wizard, "run", lambda self, command, **k: pulled.append(command))
    assert setup.main(["--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "! Ollama: Homebrew's Ollama answers" in out and "✗ Ollama" not in out
    assert "Fix: brew services stop ollama" in out and "setup goes on with the other steps" in out
    assert "! Model: local-worker is not there yet" in out and not pulled
    assert reached[:3] == ["always_on", "security", "extras"] and "check" in reached
    assert not tools.changes


@pytest.mark.parametrize("where", ["configured", "her-own-user"])
@pytest.mark.parametrize(
    "why", ["another-account", "account-not-read", "other-machines", "unsigned", "second-listener", "not-found"]
)
def test_a_rerun_still_stops_at_an_ollama_that_someone_else_may_control(
    tmp_path: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    where: str,
    why: str,
) -> None:
    """Only Homebrew's, the app's and the like are a "!" in a re-run: an Ollama another account runs (or whose
    account cannot be read), one that shares its port, one other machines reach or one whose signature does not check
    out gets her prompts, so setup stops there whether she is set up or not, before the move and the model step."""
    from local_assistant import doctor, hostsec, setup

    program = official_tree(tmp_path / "theirs/v0.40.4") / "ollama"
    tools = FakeTools(
        tmp_path,
        versions=["0.40.4"],
        program="" if why == "not-found" else str(program),
        addresses=("*.11434",) if why == "other-machines" else LOCAL,
    )
    if why == "another-account":
        tools.uid = os.getuid() + 1
    elif why == "account-not-read":
        tools.uid = None
    elif why == "unsigned":
        tools.unsigned.add("ollama")
    elif why == "second-listener":
        tools.others = (("/tmp/elsewhere/serve", os.getuid() + 1),)
    monkeypatch.setattr(ollama_build, "Tools", lambda say=print: tools)
    monkeypatch.setattr(ollama_build, "owner_home", lambda: home)
    monkeypatch.setattr(doctor.Probe, "get_json", lambda self, url: {"models": [{"name": "local-worker:latest"}]})
    reached = rerun_steps(monkeypatch)
    pulled: list[str] = []
    monkeypatch.setattr(setup.Wizard, "model", lambda self: pulled.append("model"))
    config = tmp_path / "config.toml"
    if where == "configured":
        config.write_text(
            'bot_number = "+306900000000"\nallowed_acis = ["11111111-2222-4333-8444-555555555555"]\n', encoding="utf-8"
        )
        config.chmod(0o600)
    else:
        plist = hostsec.estia_root() / "Library/LaunchDaemons/com.local-assistant.bridge.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(plistlib.dumps({"Label": "com.local-assistant.bridge", "UserName": "_estia"}))
    with pytest.raises(SystemExit) as stopped:
        setup.main(["--config", str(config)])
    assert str(stopped.value).startswith("✗ Ollama:"), stopped.value
    assert "setup goes on with the other steps" not in capsys.readouterr().out
    assert not reached and not pulled and not tools.changes


def test_a_dry_run_of_setup_with_the_ollama_app_still_reaches_the_model_step(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import doctor, hostsec, setup

    (hostsec.estia_root() / "Applications/Ollama.app").mkdir(parents=True)
    monkeypatch.setattr(ollama_build, "owner_home", lambda: home)
    monkeypatch.setattr(setup, "memory_gb", lambda: 32.0)
    monkeypatch.setattr(doctor.Probe, "get_json", lambda self, url: {"models": []})
    wizard = setup.Wizard(tmp_path / "config.toml", dry_run=True)
    wizard.ollama()
    wizard.model()
    out = capsys.readouterr().out
    assert "✗ Ollama: the Ollama app is installed (/Applications/Ollama.app)" in out
    assert f"caffeinate -i {home}/opt/ollama/current/ollama pull gemma4:26b-mlx" in out


def wrapper(tmp_path: Path, *args: str, repo: Path | None = None) -> subprocess.CompletedProcess[str]:
    """scripts/install-ollama.sh as the person's AI would start it: no terminal (stdin /dev/null), off the live Mac."""
    script = (repo or Path(__file__).resolve().parents[1]) / "scripts/install-ollama.sh"
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        stdin=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ESTIA_ROOT": str(tmp_path / "root")},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_install_ollama_without_a_terminal_changes_nothing_and_says_where_to_run_it(tmp_path: Path) -> None:
    done = wrapper(tmp_path)
    assert done.returncode == 1, done.stderr
    assert "run it in your own Terminal window" in done.stderr and "--dry-run" in done.stderr
    assert sorted(path.name for path in tmp_path.iterdir()) == []  # no ~/opt, no ~/.zprofile


def test_install_ollama_dry_run_needs_no_terminal(tmp_path: Path) -> None:
    done = wrapper(tmp_path, "--dry-run")
    assert done.returncode == 0, done.stderr
    assert "Ollama: its official build 0.40.4" in done.stdout


def test_install_ollama_without_the_environment_says_how_to_make_it(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(Path(__file__).resolve().parents[1] / "scripts/install-ollama.sh", repo / "scripts")
    done = wrapper(tmp_path, repo=repo)
    assert done.returncode == 1 and f"no .venv in {repo}: run uv sync --frozen first" in done.stderr


# ------------------------------------------------------------------ the model build, for doctor


@pytest.mark.parametrize(
    ("model", "entry", "source"),
    [
        ("gemma4:26b-mlx", {}, None),
        ("gemma4:12b-mlx", {}, None),
        ("gemma4:26b", {}, "gemma4:26b"),  # a list: what it pulls depends on Ollama's version
        ("gemma4:26b-nvfp4", {}, "gemma4:26b-nvfp4"),
        ("local-worker", {"details": {"format": "safetensors", "family": "gemma4"}}, None),
        (
            "local-worker",
            {"details": {"format": "gguf", "family": "gemma4", "parent_model": "gemma4:26b"}},
            "gemma4:26b",
        ),
        (
            "local-worker",
            {"details": {"format": "gguf", "families": ["gemma4"]}},
            "a llama.cpp (GGUF) build of Gemma 4",
        ),
        ("local-worker", {"details": {"parent_model": "registry.ollama.ai/library/gemma4:12b"}}, "gemma4:12b"),
        ("local-worker", {"details": {"parent_model": "gemma4:12b-mlx"}}, None),
        ("local-worker", {"details": {"format": "gguf", "family": "qwen3"}}, None),  # not Gemma 4: not ours to say
        ("qwen3:30b", {}, None),
    ],
)
def test_a_gemma_4_model_that_is_not_an_mlx_build_is_named(
    model: str, entry: dict[str, object], source: str | None
) -> None:
    assert non_mlx_source(model, entry) == source


def test_one_definition_of_the_log_folder_and_the_model_tags() -> None:
    """The log's folder is ownuser's STREAM_LOGS itself (root's, where launchd opens her services' output too), and
    the model tags live only in setup (GEMMA_26B, GEMMA_12B): no second copy here to drift."""
    from local_assistant import ownuser

    assert ollama_build.LOG_DIR is ownuser.STREAM_LOGS
    assert not hasattr(ollama_build, "MLX_TAGS")


def test_the_words_name_the_fix_and_the_folder_once() -> None:
    assert "scripts/setup.sh" in prompts.OLLAMA_OTHER_FIX and "lsof" in prompts.OLLAMA_NOT_FOUND_FIX
    assert prompts.OLLAMA_NOT_FOUND_FIX.startswith("/usr/bin/sudo /usr/sbin/lsof")


def shown(renderer: str | None, size: str = "26.2B", fmt: str = "safetensors") -> dict[str, object]:
    """/api/show's answer, the parts doctor and setup read: the Modelfile Ollama writes back, and the model's size."""
    lines = ['# Modelfile generated by "ollama show"', "FROM local-worker:latest"]
    lines += [f"RENDERER {renderer}"] if renderer is not None else []
    lines += ["PARSER gemma4", "PARAMETER top_p 0.95"]
    return {"modelfile": "\n".join(lines), "details": {"format": fmt, "family": "gemma4", "parameter_size": size}}


@pytest.mark.parametrize(
    ("name", "answer", "listed", "small"),
    [
        ("local-worker", shown("gemma4"), None, True),  # the live alias before 0.9.3: no size in its name
        ("local-worker", shown("gemma4", "12.4B"), None, True),  # 12B's alias too
        ("local-worker", shown("gemma4-small"), None, True),
        ("local-worker", shown("gemma4-large"), None, False),  # setup's alias from 0.9.3 on
        ("gemma4:26b-mlx", shown("gemma4"), None, False),  # the tag itself: Ollama picks large by its name
        ("my-12b-gemma", shown("gemma4", "12.4B"), None, False),
        ("my-e4b", shown("gemma4", "12.4B"), None, True),  # Ollama reads e2b and e4b first
        ("local-worker", shown("gemma4", "8.0B"), None, False),  # a small model: Ollama's pick is right
        ("local-worker", shown("gemma4", "26B", "gguf"), {"details": {"parameter_size": "26B"}}, False),  # by type
        ("local-worker", shown(None), None, False),  # no renderer: a template model
        ("local-worker", shown("qwen3.5"), None, False),
        ("local-worker", shown("gemma4", ""), None, False),  # the size cannot be read: no claim
    ],
)
def test_a_large_gemma_4_with_the_small_models_prompt_is_found_from_its_modelfile(
    name: str, answer: dict[str, object], listed: dict[str, object] | None, small: bool
) -> None:
    assert ollama_build.gets_small_prompt(name, answer, listed) is small


def test_the_size_can_come_from_the_model_info() -> None:
    answer = shown("gemma4", "") | {"model_info": {"general.parameter_count": 25_805_000_000}}
    assert ollama_build.gets_small_prompt("local-worker", answer)
