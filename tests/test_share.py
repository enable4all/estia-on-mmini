"""Her read-only access to your projects folder: the exact chmod argv (a recording runner and canned ACLs, since her
user does not exist in tests), and one real run on a temporary folder with the current user."""

import json
import os
import pwd
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from local_assistant import share
from local_assistant.hostsec import CHMOD

TREE = "user:_estia allow list,search,readattr,readextattr,readsecurity,read,file_inherit,directory_inherit"
SEARCH = "user:_estia allow search"


class Recorder:
    def __init__(self, code: int = 0, out: str = "") -> None:
        self.asked: list[tuple[str, ...]] = []
        self.code, self.out = code, out

    def __call__(self, argv: Sequence[str]) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        return self.code, self.out


def no_acl(path: Path) -> list[str]:
    return []


def entry(user: str, kind: str, rights: str) -> str:
    """One line as acl_to_text writes it."""
    uuid = "FFFFEEEE-DDDD-CCCC-BBBB-AAAA000001F3"  # privacy-ok: macOS's stand-in id of a user by number (499)
    return f"user:{uuid}:{user}:499:{kind}:{rights}"


DIR_ENTRY = entry("_estia", "allow,file_inherit,directory_inherit", "read,execute,readattr,readextattr,readsecurity")
FILE_ENTRY = entry("_estia", "allow", "read,execute,readattr,readextattr,readsecurity")
INHERITED = entry("_estia", "allow,inherited", "read,execute,readattr,readextattr,readsecurity")
PASS_ENTRY = entry("_estia", "allow", "execute")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A home with a projects folder: a repository with a file, a link to a folder outside the projects folder and a
    link to a file outside it."""
    home = tmp_path / "home"
    repo = home / "dev" / "alpha"
    (repo / "docs").mkdir(parents=True)
    (repo / "README.md").write_text("# alpha\n")
    (repo / "docs" / "notes.md").write_text("notes\n")
    private = home / ".ssh"
    private.mkdir()
    (private / "id_ed25519").write_text("not a real key\n")
    (repo / "keys").symlink_to(private)
    (repo / "docs" / "key.md").symlink_to(private / "id_ed25519")
    return home


def tree_paths(argvs: Sequence[tuple[str, ...]]) -> list[str]:
    return [path for argv in argvs if argv[:4] == (CHMOD, "-h", "+a", TREE) for path in argv[4:]]


def test_the_share_gives_her_the_tree_read_only_and_your_home_passing_through_only(home: Path) -> None:
    run = Recorder()
    said: list[str] = []
    assert share.share_projects(home / "dev", home, run=run, acl=no_acl, say=said.append) == 0
    dev = home / "dev"
    expected = sorted(
        str(p) for p in (dev, dev / "alpha", dev / "alpha/README.md", dev / "alpha/docs", dev / "alpha/docs/notes.md")
    )
    assert sorted(tree_paths(run.asked)) == expected  # every folder and file, no link and nothing a link leads to
    assert run.asked[-1] == (CHMOD, "+a", SEARCH, str(home))  # your home last: the door opens when all is shared
    assert len(run.asked) == 2
    assert all("-R" not in argv for argv in run.asked)  # macOS chmod -R +a sets the entry on each link's target
    assert any("read-only" in line and "_estia" in line for line in said)


def test_nothing_is_added_twice(home: Path) -> None:
    """An entry already there, set by an earlier share or inherited from the folder above, is never added again."""

    def given(path: Path) -> list[str]:
        if path == home:
            return [PASS_ENTRY]
        if path.is_dir():
            return [DIR_ENTRY]
        return [INHERITED] if path.name == "notes.md" else [FILE_ENTRY]

    run = Recorder()
    assert share.share_projects(home / "dev", home, run=run, acl=given, say=lambda line: None) == 0
    assert run.asked == []


def test_only_what_lacks_the_entry_is_changed(home: Path) -> None:
    new = home / "dev" / "alpha" / "new.md"
    new.write_text("moved in from elsewhere: its own permissions\n")

    def given(path: Path) -> list[str]:
        if path == new:
            return [entry("someone", "allow", "read")]  # another user's entry is not hers
        if path == home:
            return [PASS_ENTRY]
        return [DIR_ENTRY] if path.is_dir() else [FILE_ENTRY]

    run = Recorder()
    share.share_projects(home / "dev", home, run=run, acl=given, say=lambda line: None)
    assert run.asked == [(CHMOD, "-h", "+a", TREE, str(new))]


@pytest.mark.parametrize(
    ("lines", "has"),
    [
        ([entry("_estia", "allow,file_inherit,directory_inherit", "read,execute,readattr,readextattr")], False),
        ([entry("_estia", "allow", "read,execute,readattr,readextattr,readsecurity")], False),  # inherits nothing
        (
            [entry("_estia", "deny,file_inherit,directory_inherit", "read,execute,readattr,readextattr,readsecurity")],
            False,
        ),
        (
            [
                entry(
                    "_estia",
                    "allow,only_inherit,file_inherit,directory_inherit",
                    "read,execute,readattr,readextattr,readsecurity",
                )
            ],
            False,
        ),
        ([DIR_ENTRY], True),
        (
            [
                entry(
                    "_estia",
                    "allow,inherited,file_inherit,directory_inherit",
                    "read,execute,readattr,readextattr,readsecurity",
                )
            ],
            True,
        ),
    ],
)
def test_a_folder_counts_as_shared_only_with_her_full_inheriting_entry(lines: list[str], has: bool) -> None:
    assert share.granted(lines, "_estia", share.FOLDER_RIGHTS, share.INHERIT) is has


def test_a_nested_projects_folder_gets_passing_through_on_each_folder_above_it(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "work" / "dev").mkdir(parents=True)
    run = Recorder()
    share.share_projects(home / "work" / "dev", home, run=run, acl=no_acl, say=lambda line: None)
    assert run.asked[-2:] == [(CHMOD, "+a", SEARCH, str(home)), (CHMOD, "+a", SEARCH, str(home / "work"))]


@pytest.mark.parametrize(
    ("folder", "why"),
    [
        ("outside", "outside your home"),
        (".", "your home itself"),
        ("linked", "symbolic link"),
        ("via/dev", "symbolic link"),
        ("missing", "no folder"),
        ("notes.txt", "not a folder"),
    ],
)
def test_a_folder_outside_your_home_a_link_or_your_home_itself_is_refused(
    tmp_path: Path, folder: str, why: str
) -> None:
    home = tmp_path / "home"
    (home / "dev").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (home / "linked").symlink_to(home / "dev")
    (home / "via").symlink_to(tmp_path / "outside-home")
    (tmp_path / "outside-home" / "dev").mkdir(parents=True)
    (home / "notes.txt").write_text("x\n")
    target = tmp_path / "outside" if folder == "outside" else home / folder
    run = Recorder()
    said: list[str] = []
    assert share.share_projects(target, home, run=run, acl=no_acl, say=said.append) == 1
    assert run.asked == [] and why in "\n".join(said), said


def test_a_relative_folder_is_refused(home: Path) -> None:
    said: list[str] = []
    assert share.share_projects(Path("dev"), home, run=Recorder(), acl=no_acl, say=said.append) == 1
    assert "absolute" in said[0]


def test_files_that_are_not_yours_are_left_as_they_are(home: Path) -> None:
    run = Recorder()
    said: list[str] = []
    share.share_projects(home / "dev", home, run=run, acl=no_acl, say=said.append, uid=os.getuid() + 1)
    assert said[0].startswith("✗") and "not yours" in said[0] and run.asked == []


def test_long_lists_go_in_batches(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(share, "BATCH_PATHS", 2)
    run = Recorder()
    share.share_projects(home / "dev", home, run=run, acl=no_acl, say=lambda line: None)
    batches = [argv for argv in run.asked if argv[:4] == (CHMOD, "-h", "+a", TREE)]
    assert [len(argv) - 4 for argv in batches] == [2, 2, 1]


def test_a_failing_chmod_is_a_failure_with_its_reason(home: Path) -> None:
    run = Recorder(code=1, out="chmod: Failed to set ACL on file 'x': Operation not permitted\n")
    said: list[str] = []
    assert share.share_projects(home / "dev", home, run=run, acl=no_acl, say=said.append) == 1
    assert said[-1].startswith("✗") and "Operation not permitted" in said[-1]
    assert (CHMOD, "+a", SEARCH, str(home)) not in run.asked  # your home stays closed when the tree failed


def test_a_dry_run_prints_the_exact_commands_and_runs_none(home: Path) -> None:
    run = Recorder()
    said: list[str] = []
    assert share.share_projects(home / "dev", home, run=run, acl=no_acl, say=said.append, dry=True) == 0
    assert run.asked == []
    text = "\n".join(said)
    assert f"would run: {shlex.join([CHMOD, '+a', SEARCH, str(home)])}" in text
    assert f"would run: {shlex.join([CHMOD, '-h', '+a', TREE, str(home / 'dev')])} … and 4 more" in text


def test_the_revoke_takes_back_every_entry_and_closes_your_home_first(home: Path) -> None:
    shared = {home / "dev", home / "dev/alpha", home / "dev/alpha/README.md"}

    def given(path: Path) -> list[str]:
        if path == home:
            return [PASS_ENTRY]
        return ([DIR_ENTRY] if path.is_dir() else [INHERITED]) if path in shared else []

    run = Recorder()
    assert share.revoke_projects(home / "dev", home, run=run, acl=given, say=lambda line: None) == 0
    assert run.asked[0] == (CHMOD, "-a", SEARCH, str(home))
    assert run.asked[1][:4] == (CHMOD, "-h", "-a", TREE) and sorted(run.asked[1][4:]) == sorted(map(str, shared))
    assert len(run.asked) == 2


def test_a_revoke_with_nothing_shared_runs_nothing(home: Path) -> None:
    run = Recorder()
    assert share.revoke_projects(home / "dev", home, run=run, acl=no_acl, say=lambda line: None) == 0
    assert run.asked == []


def test_the_folder_on_record(tmp_path: Path) -> None:
    home = tmp_path / "home"
    marker = tmp_path / "migration.json"
    assert share.default_root(home, None) is None  # no config: projects are off, nothing to share
    config = home / ".config/local-assistant/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('bot_number = "+306900000000"\n')
    assert share.default_root(home, None) is None  # no [projects]: off
    config.write_text('bot_number = "+306900000000"\n[projects]\ndefault = "docs"\n')
    assert share.default_root(home, None) == home / "dev"  # on, without a root: ~/dev
    config.write_text('bot_number = "+306900000000"\n[projects]\nroot = "~/code"\n')
    assert share.default_root(home, marker) == home / "code"  # before the move: your config, ~ is your home
    marker.write_text(json.dumps({"projects_root": str(home / "work"), "rolled_back": False}))
    assert share.default_root(home, marker) == home / "work"  # after the move: what the move wrote into hers
    marker.write_text(json.dumps({"projects_root": None, "rolled_back": False}))
    assert share.default_root(home, marker) is None
    marker.write_text(json.dumps({"projects_root": str(home / "work"), "rolled_back": True}))
    assert share.default_root(home, marker) == home / "code"


def test_with_projects_off_nothing_is_shared_and_no_folder_suggested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Projects off (no [projects], or the move recorded no folder): no tool would read a shared folder, so the script
    never suggests naming one. With nothing known it names [projects] first."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    marker = tmp_path / "migration.json"
    assert share.main(["--dry-run"]) == 0
    unknown = capsys.readouterr().out
    assert unknown == (
        "No projects folder on record for her: with [projects] in her config, name its folder: "
        "scripts/share-projects.sh <folder>.\n"
    )
    config = home / ".config/local-assistant/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('bot_number = "+306900000000"\n')
    assert share.main(["--dry-run"]) == 0
    assert capsys.readouterr().out == "Projects are off (no [projects] in the config): nothing to share.\n"
    marker.write_text(json.dumps({"projects_root": None, "rolled_back": False}))
    config.write_text('bot_number = "+306900000000"\n[projects]\nroot = "~/code"\n')
    assert share.main(["--dry-run", "--marker", str(marker)]) == 0  # the move wrote no folder into hers: off
    assert capsys.readouterr().out == "Projects are off (no [projects] in the config): nothing to share.\n"


# one real run: the current user stands in for _estia (an entry for a user that does not exist cannot be set)


def test_a_real_share_and_revoke_on_a_temporary_folder(home: Path) -> None:
    me = pwd.getpwuid(os.getuid()).pw_name
    asked: list[tuple[str, ...]] = []

    def run(argv: Sequence[str]) -> tuple[int, str]:
        asked.append(tuple(argv))
        return share.run_tool(argv)

    dev, private = home / "dev", home / ".ssh"
    assert share.share_projects(dev, home, me, run=run, say=lambda line: None) == 0
    for path in (dev, dev / "alpha", dev / "alpha/README.md", dev / "alpha/docs/notes.md"):
        assert share.granted(share.acl_lines(path), me, share.FILE_RIGHTS), path
    assert share.granted(share.acl_lines(dev / "alpha"), me, share.FOLDER_RIGHTS, share.INHERIT)
    assert share.granted(share.acl_lines(home), me, share.PASS_RIGHTS)
    assert not share.granted(share.acl_lines(home), me, share.FILE_RIGHTS)  # passing through, never listing
    assert share.acl_lines(private) == [] and share.acl_lines(private / "id_ed25519") == []  # links never followed
    (dev / "alpha" / "later.md").write_text("made after the share\n")
    assert share.granted(share.acl_lines(dev / "alpha/later.md"), me, share.FILE_RIGHTS)  # inherited
    asked.clear()
    assert share.share_projects(dev, home, me, run=run, say=lambda line: None) == 0
    assert asked == []  # a second run adds nothing
    listing = subprocess.run(["/bin/ls", "-led", str(dev)], capture_output=True, text=True, check=True).stdout
    assert listing.count(f"user:{me} ") == 1
    assert share.revoke_projects(dev, home, me, run=run, say=lambda line: None) == 0
    for path in (home, dev, dev / "alpha", dev / "alpha/later.md", dev / "alpha/docs/notes.md"):
        assert share.acl_lines(path) == [], path
