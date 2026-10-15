"""Her read-only access to your projects folder: the exact chmod argv (a recording runner and canned ACLs, since her
user does not exist in tests), and one real run on a temporary folder with the current user."""

import json
import os
import pwd
import shlex
import subprocess
import tomllib
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


# the documents folder: she lists and reads it, adds files to the folder itself (never a folder, never below it), never
# deletes or changes yours; an inherited entry gives you full control of what she adds

ADD = "user:_estia allow add_file"
OWNER = (
    "user:alex allow list,search,add_file,add_subdirectory,delete_child,delete,readattr,writeattr,readextattr,"
    "writeextattr,readsecurity,writesecurity,chown,file_inherit,directory_inherit"
)
ADD_ENTRY = entry("_estia", "allow", "write")
OWNER_ENTRY = entry(
    "alex",
    "allow,file_inherit,directory_inherit",
    "read,write,execute,delete,append,delete_child,readattr,writeattr,readextattr,writeextattr,readsecurity,"
    "writesecurity,chown",
)


@pytest.fixture
def documents(tmp_path: Path) -> Path:
    """A home with ~/Estia-KB: a PDF, a year folder with a PDF, and a link to your private folder."""
    home = tmp_path / "home"
    folder = home / "Estia-KB"
    (folder / "2026").mkdir(parents=True)
    (folder / "lease.pdf").write_bytes(b"%PDF-1.7 lease\n")
    (folder / "2026" / "letter.pdf").write_bytes(b"%PDF-1.7 letter\n")
    private = home / ".ssh"
    private.mkdir()
    (private / "id_ed25519").write_text("not a real key\n")
    (folder / "keys").symlink_to(private)
    return home


def batch_paths(argvs: Sequence[tuple[str, ...]], entry_text: str) -> list[str]:
    return sorted(path for argv in argvs if argv[:4] == (CHMOD, "-h", "+a", entry_text) for path in argv[4:])


def test_the_documents_share_lets_her_list_read_and_add_and_gives_you_full_control_of_what_she_adds(
    documents: Path,
) -> None:
    home, run, said = documents, Recorder(), list[str]()
    folder = home / "Estia-KB"
    assert share.share_documents(folder, home, owner="alex", run=run, acl=no_acl, say=said.append) == 0
    folders = sorted(map(str, (folder, folder / "2026")))
    files = sorted(map(str, (folder / "lease.pdf", folder / "2026" / "letter.pdf")))
    assert batch_paths(run.asked, TREE) == sorted(folders + files)  # read: every folder and file, never a link
    assert batch_paths(run.asked, ADD) == [str(folder)]  # files added to the folder itself only: no folder, no year
    assert batch_paths(run.asked, OWNER) == [str(folder)]  # inherited by what she adds: yours to open, move or delete
    assert run.asked[-1] == (CHMOD, "+a", SEARCH, str(home))  # your home last, passing through only: the door opens
    assert [argv for argv in run.asked if SEARCH in argv] == [run.asked[-1]]  # when all is shared; no other hop
    assert all("-R" not in argv for argv in run.asked)
    assert (folder / ".estia-share").is_file()  # a new share: her index starts fresh
    text = "\n".join(said)
    assert "never delete or change" in text and "--revoke" in text
    config = "\n".join(line.strip() for line in said if line.startswith("  "))  # pasted as printed: valid TOML
    assert tomllib.loads(config) == {"documents": {"folder": str(folder)}}, said  # review 15 Oct, 17
    assert "/usr/bin/sudo /usr/local/estia/bin/estia config" in text


def test_the_share_makes_the_documents_folder_when_it_is_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    folder = home / "Estia-KB"
    said: list[str] = []
    dry: list[str] = []
    assert share.share_documents(folder, home, owner="alex", run=Recorder(), acl=no_acl, say=dry.append, dry=True) == 0
    assert not folder.exists()  # a dry run makes nothing
    assert f"would make: {folder}" in "\n".join(dry)
    assert share.share_documents(folder, home, owner="alex", run=Recorder(), acl=no_acl, say=said.append) == 0
    assert folder.is_dir() and (folder.stat().st_mode & 0o777) == 0o700  # yours alone; hers by the ACL
    assert sorted(home.iterdir()) == [folder]  # directly in your home: no folder on the way
    assert any("made" in line and str(folder) in line for line in said)


def test_the_documents_share_adds_nothing_twice_and_keeps_its_token(documents: Path) -> None:
    folder = documents / "Estia-KB"

    def given(path: Path) -> list[str]:
        if path == documents:
            return [PASS_ENTRY]
        if path == folder:
            return [DIR_ENTRY, ADD_ENTRY, OWNER_ENTRY]
        return [DIR_ENTRY] if path.is_dir() else [INHERITED]

    (folder / ".estia-share").write_text("0123456789abcdef0123456789abcdef\n")
    run = Recorder()
    assert share.share_documents(folder, documents, owner="alex", run=run, acl=given, say=lambda s: None) == 0
    assert run.asked == []
    assert (folder / ".estia-share").read_text() == "0123456789abcdef0123456789abcdef\n"  # the same share


def test_a_dry_run_of_the_documents_share_changes_nothing(documents: Path) -> None:
    folder = documents / "Estia-KB"
    run, said = Recorder(), list[str]()
    assert share.share_documents(folder, documents, owner="alex", run=run, acl=no_acl, say=said.append, dry=True) == 0
    assert run.asked == [] and not (folder / ".estia-share").exists()
    text = "\n".join(said)
    assert f"would run: {shlex.join([CHMOD, '+a', SEARCH, str(documents)])}" in text
    assert f"would write: {folder / '.estia-share'}" in text


@pytest.mark.parametrize(("make", "why"), [("link", "symbolic link"), ("file", "not a folder")])
def test_a_documents_folder_that_is_a_link_or_a_file_is_refused(tmp_path: Path, make: str, why: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "elsewhere").mkdir()
    folder = home / "Estia-KB"
    if make == "link":
        folder.symlink_to(tmp_path / "elsewhere")
    else:
        folder.write_text("x\n")
    run, said = Recorder(), list[str]()
    assert share.share_documents(folder, home, owner="alex", run=run, acl=no_acl, say=said.append) == 1
    assert run.asked == [] and why in "\n".join(said)


def test_a_documents_folder_that_links_elsewhere_gets_no_token_there(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "elsewhere" / "Estia-KB").mkdir(parents=True)
    (home / "Estia-KB").symlink_to(tmp_path / "elsewhere" / "Estia-KB")
    run, said = Recorder(), list[str]()
    folder = home / "Estia-KB"
    assert share.share_documents(folder, home, owner="alex", run=run, acl=no_acl, say=said.append) == 1
    assert run.asked == [] and "symbolic link" in "\n".join(said)
    assert not (tmp_path / "elsewhere" / "Estia-KB" / ".estia-share").exists()


def test_a_documents_folder_that_holds_a_git_checkout_is_refused(documents: Path) -> None:
    """A git checkout is never the documents folder: her account would add files (a conftest.py) to code you run.
    Refused, dry run too."""
    folder = documents / "Estia-KB"
    (folder / ".git").mkdir()
    for dry in (True, False):
        run, said = Recorder(), list[str]()
        assert (
            share.share_documents(folder, documents, owner="alex", run=run, acl=no_acl, say=said.append, dry=dry) == 1
        )
        assert run.asked == [] and not (folder / ".estia-share").exists()
        assert f"✗ {folder} holds a git checkout (.git): her account would add files to your code" in said[-1]
        assert "Nothing changed" in said[-1]


def test_a_git_name_that_is_not_yours_is_refused_with_its_own_reason(
    documents: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Her account may add a file named .git to the folder itself: still refused, but the reason never blames your
    code; it says whose the name is and what to do."""
    folder = documents / "Estia-KB"
    git = folder / ".git"
    git.write_text("gitdir: /elsewhere\n")
    real_lstat = os.lstat

    def lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        info = real_lstat(path)  # type: ignore[arg-type]
        if Path(str(path)) == git:  # added by another account (hers)
            fields = list(info)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(os, "lstat", lstat)  # the same module the share calls
    run, said = Recorder(), list[str]()
    assert share.share_documents(folder, documents, owner="alex", run=run, acl=no_acl, say=said.append) == 1
    assert run.asked == [] and not (folder / ".estia-share").exists()
    assert said[-1] == (
        f"✗ {git} is not yours (her account may have added it): check it, delete it (rm {shlex.quote(str(git))}), "
        "then run the share again. Nothing changed."
    )
    assert "your code" not in said[-1]


def test_the_documents_folder_is_your_estia_kb_directly_in_your_home() -> None:
    """Live, 14 Oct: macOS guards ~/Documents (and Desktop and Downloads): the owner's own SSH session could not add
    her entry to it ("Operation not permitted"). The folder is ~/Estia-KB (owner's decision), so the only way through
    is your home; its name is never the README's clone ~/estia, which a Mac's usual disk would match ignoring case."""
    assert Path("Estia-KB") == share.DOCUMENTS and share.DOCUMENTS.name.lower() != "estia"
    home = Path("/Users/alex")
    assert share._passes(home / share.DOCUMENTS, home) == [home]


def test_the_documents_revoke_closes_the_way_takes_her_entries_back_and_ends_the_share(documents: Path) -> None:
    folder = documents / "Estia-KB"
    (folder / ".estia-share").write_text("0123456789abcdef0123456789abcdef\n")

    def given(path: Path) -> list[str]:
        if path == documents:
            return [PASS_ENTRY]
        return [DIR_ENTRY, ADD_ENTRY, OWNER_ENTRY] if path.is_dir() else [INHERITED]

    run = Recorder()
    assert share.revoke_documents(folder, documents, owner="alex", run=run, acl=given, say=lambda s: None) == 0
    assert run.asked[0] == (CHMOD, "-a", SEARCH, str(documents))  # the way closes first
    assert [argv for argv in run.asked if SEARCH in argv] == [run.asked[0]]  # your home is the only way
    removed = sorted(path for argv in run.asked if argv[:4] == (CHMOD, "-h", "-a", TREE) for path in argv[4:])
    assert removed == sorted(
        map(str, (folder, folder / "2026", folder / "lease.pdf", folder / "2026" / "letter.pdf"))
    )  # the share's token went first
    assert batch_paths([(*argv[:2], "+a", *argv[3:]) for argv in run.asked if argv[2] == "-a"], ADD) == sorted(
        map(str, (folder, folder / "2026"))
    )
    assert not any(OWNER in argv for argv in run.asked)  # your full control of what she added stays
    assert not (folder / ".estia-share").exists()  # the next share starts a fresh index


def test_revoking_one_share_keeps_your_home_open_for_the_other(documents: Path) -> None:
    """Both shares pass through your home: revoking documents keeps it open while your projects are shared, and
    revoking projects keeps it open while documents are."""
    (documents / "dev" / "alpha").mkdir(parents=True)
    folder = documents / "Estia-KB"

    def given(path: Path) -> list[str]:
        if path == documents:
            return [PASS_ENTRY]
        return [DIR_ENTRY, ADD_ENTRY] if path.is_dir() else [INHERITED]

    run = Recorder()
    kept = documents / "dev"
    assert (
        share.revoke_documents(folder, documents, owner="alex", run=run, acl=given, say=lambda s: None, keep=kept) == 0
    )
    assert not any(SEARCH in argv for argv in run.asked)  # your home, the only way, stays open for projects
    run = Recorder()
    assert share.revoke_projects(documents / "dev", documents, run=run, acl=given, say=lambda s: None, keep=folder) == 0
    assert (CHMOD, "-a", SEARCH, str(documents)) not in run.asked


def test_the_documents_script_needs_no_folder_and_names_its_own(
    documents: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(documents))
    assert share.main(["--documents", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert str(documents / "Estia-KB") in out and "Nothing changed (a dry run)" in out
    with pytest.raises(SystemExit):
        share.main(["--documents", str(documents / "elsewhere")])  # always ~/Estia-KB: no folder of your own


def test_a_real_documents_share_and_revoke_on_a_temporary_folder(documents: Path) -> None:
    """The current user stands in for her and for you (an entry for a user that does not exist cannot be set); macOS
    merges entries of one user with the same flags, so only what she must hold is checked."""
    me = pwd.getpwuid(os.getuid()).pw_name
    folder, private = documents / "Estia-KB", documents / ".ssh"
    asked: list[tuple[str, ...]] = []

    def run(argv: Sequence[str]) -> tuple[int, str]:
        asked.append(tuple(argv))
        return share.run_tool(argv)

    assert share.share_documents(folder, documents, me, owner=me, run=run, say=lambda line: None) == 0
    for path in (folder, folder / "2026"):
        assert share.granted(share.acl_lines(path), me, share.FOLDER_RIGHTS, share.INHERIT), path
    assert share.granted(share.acl_lines(folder), me, share.ADD_HELD)
    assert share.granted(share.acl_lines(folder), me, share.OWNER_HELD, share.INHERIT)
    for path in (folder / "lease.pdf", folder / "2026" / "letter.pdf", folder / ".estia-share"):
        assert share.granted(share.acl_lines(path), me, share.FILE_RIGHTS), path
    assert share.granted(share.acl_lines(documents), me, share.PASS_RIGHTS)
    assert share.acl_lines(private) == []  # links never followed
    (folder / "added.pdf").write_bytes(b"%PDF-1.7 added\n")  # as she would add one
    added = share.acl_lines(folder / "added.pdf")
    assert share.granted(added, me, share.FILE_RIGHTS) and share.granted(added, me, frozenset({"delete", "write"}))
    (folder / "2027").mkdir()  # a folder you make: she reads it, adds nothing to it
    assert share.granted(share.acl_lines(folder / "2027"), me, share.FOLDER_RIGHTS, share.INHERIT)
    assert not share.granted(share.acl_lines(folder / "2026"), me, share.ADD_HELD)
    asked.clear()
    assert share.share_documents(folder, documents, me, owner=me, run=run, say=lambda line: None) == 0
    assert asked == []  # a second run adds nothing
    assert share.revoke_documents(folder, documents, me, owner=me, run=run, say=lambda line: None) == 0
    for path in (documents, folder, folder / "lease.pdf", folder / "2027"):
        assert not share.granted(share.acl_lines(path), me, frozenset({"read"})), path
        assert not share.granted(share.acl_lines(path), me, share.PASS_RIGHTS), path
    assert not (folder / ".estia-share").exists()


# a hard link: her account may add a file to the folder, and macOS lets it link a file of yours it cannot read (in
# your home, ~/.zsh_history) under a new name there. An ACL entry belongs to the file, not the name: a share that named
# the link would open your file. So a file with a second name is never shared, and neither is anything in a folder that
# is not yours (its names are another account's to change)


def your_secret(home: Path) -> Path:
    secret = home / ".zsh_history"
    secret.write_text("not a real history\n")
    secret.chmod(0o600)
    return secret


def test_a_hard_link_in_the_documents_folder_is_never_shared(documents: Path) -> None:
    folder = documents / "Estia-KB"
    os.link(your_secret(documents), folder / "invoice.pdf")
    run, said = Recorder(), list[str]()
    assert share.share_documents(folder, documents, owner="alex", run=run, acl=no_acl, say=said.append) == 0
    named = {path for argv in run.asked for path in argv[4:]}
    assert str(folder / "invoice.pdf") not in named and str(folder / "lease.pdf") in named
    assert any("hard link" in line and "invoice.pdf" in line for line in said)


def test_a_real_documents_share_leaves_your_file_behind_a_hard_link_closed(documents: Path) -> None:
    me = pwd.getpwuid(os.getuid()).pw_name
    folder = documents / "Estia-KB"
    secret = your_secret(documents)
    os.link(secret, folder / "invoice.pdf")
    assert share.share_documents(folder, documents, me, owner=me, say=lambda line: None) == 0
    assert share.acl_lines(secret) == []  # the file outside stays as it was: no entry of hers
    assert share.granted(share.acl_lines(folder / "lease.pdf"), me, share.FILE_RIGHTS)


def test_a_share_token_that_is_a_hard_link_is_refused(documents: Path) -> None:
    folder = documents / "Estia-KB"
    os.link(your_secret(documents), folder / ".estia-share")
    run, said = Recorder(), list[str]()
    assert share.share_documents(folder, documents, owner="alex", run=run, acl=no_acl, say=said.append) == 1
    assert not any(SEARCH in argv for argv in run.asked)  # your home stays closed to her
    assert str(folder / ".estia-share") not in {path for argv in run.asked for path in argv[4:]}


def test_the_documents_share_never_goes_into_a_folder_that_is_not_yours(
    documents: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = documents / "Estia-KB"
    theirs = folder / "theirs"
    pdf_inside = theirs / "inside.pdf"
    theirs.mkdir()
    pdf_inside.write_bytes(b"%PDF-1.7 inside\n")
    real_lstat = os.lstat

    def lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        info = real_lstat(path)  # type: ignore[arg-type]
        if Path(str(path)) == theirs:  # another account's folder: its names are that account's to change
            fields = list(info)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(os, "lstat", lstat)  # the same module the share calls
    run = Recorder()
    assert share.share_documents(folder, documents, owner="alex", run=run, acl=no_acl, say=lambda s: None) == 0
    named = {path for argv in run.asked for path in argv[4:]}
    assert str(theirs) not in named and str(pdf_inside) not in named
    assert str(folder / "2026" / "letter.pdf") in named


def test_a_documents_revoke_that_fails_partway_still_ends_the_share(documents: Path) -> None:
    """The share's token goes as soon as the way is closed: a new share then always starts a fresh index, also when
    taking her entries back failed."""
    folder = documents / "Estia-KB"
    (folder / ".estia-share").write_text("0123456789abcdef0123456789abcdef\n")

    def given(path: Path) -> list[str]:
        if path == documents:
            return [PASS_ENTRY]
        return [DIR_ENTRY, ADD_ENTRY, OWNER_ENTRY] if path.is_dir() else [INHERITED]

    said: list[str] = []
    failing = Recorder(code=1, out="chmod: Failed to set ACL")
    assert share.revoke_documents(folder, documents, owner="alex", run=failing, acl=given, say=said.append) == 1
    assert not (folder / ".estia-share").exists()
    assert any(line.startswith("✗") for line in said)


# the projects share never covers ~/Estia-KB, where her account adds files: a second name she made there (a hard link
# to your ~/.zsh_history) would get her read entry


@pytest.mark.parametrize("inside", ["Estia-KB", "Estia-KB/code", "estia-kb"])
def test_a_projects_folder_that_is_your_estia_kb_or_inside_it_is_refused(tmp_path: Path, inside: str) -> None:
    home = tmp_path / "home"
    (home / "Estia-KB" / "code").mkdir(parents=True)
    root = home / inside
    if not root.exists():  # a file system that tells case apart: no such folder, refused as missing
        pytest.skip("case-sensitive file system")
    run, said = Recorder(), list[str]()
    assert share.share_projects(root, home, run=run, acl=no_acl, say=said.append) == 1
    assert run.asked == [] and "Estia-KB" in said[0] and "Nothing changed" in said[0]


def test_a_projects_folder_beside_your_estia_kb_is_shared(home: Path) -> None:
    (home / "Estia-KB").mkdir()
    assert share.share_projects(home / "dev", home, run=Recorder(), acl=no_acl, say=lambda line: None) == 0


# what passing through your home leaves open: every entry directly in it that lets every account in, named by the share


def test_the_share_names_what_every_account_may_open_in_your_home(home: Path) -> None:
    home.chmod(0o750)
    (home / ".zshrc").write_text("export PATH=x\n")
    (home / ".zshrc").chmod(0o644)
    (home / ".config").mkdir()
    (home / ".config").chmod(0o755)
    (home / "Notes").mkdir()
    (home / "Notes").chmod(0o700)
    (home / ".ssh").chmod(0o700)
    (home / "secret.txt").write_text("x\n")
    (home / "secret.txt").chmod(0o600)
    (home / "Public").mkdir()
    (home / "Public").chmod(0o755)  # macOS's folder for sharing with every account: left as it is
    (home / "dev").chmod(0o755)  # the shared folder itself
    (home / "elsewhere").symlink_to(home / ".config")
    assert share.open_to_others(home, {home / "dev"}) == [home / ".config", home / ".zshrc"]
    said: list[str] = []
    assert share.share_projects(home / "dev", home, run=Recorder(), acl=no_acl, say=said.append) == 0
    text = "\n".join(said)
    assert ".config" in text and ".zshrc" in text and "secret.txt" not in text and "Notes" not in text
    assert f"chmod o-rwx {shlex.quote(str(home / '.config'))} {shlex.quote(str(home / '.zshrc'))}" in text


def test_the_share_names_what_every_account_may_open_in_each_folder_on_the_way(home: Path) -> None:
    """A projects folder deeper than one level: every folder on the way lets her pass by name, so what in it every
    account may open, she may too; the share names it with the same fix (the next folder on the way is left out:
    her entry opens it) (review 15 Oct, 3)."""
    work = home / "code" / "work"
    work.mkdir(parents=True)
    (home / "code").chmod(0o700)
    other = home / "code" / "other"
    other.mkdir()
    other.chmod(0o755)
    (home / "code" / "notes.txt").write_text("x\n")
    (home / "code" / "notes.txt").chmod(0o644)
    (home / "code" / "mine").mkdir()
    (home / "code" / "mine").chmod(0o700)
    said: list[str] = []
    assert share.share_projects(work, home, run=Recorder(), acl=no_acl, say=said.append) == 0
    line = next(line for line in said if str(home / "code") in line and line.startswith("!"))
    assert "other" in line and "notes.txt" in line and "mine" not in line and "work" not in line.split(":", 1)[0]
    assert f"chmod o-rwx {shlex.quote(str(home / 'code' / 'notes.txt'))} {shlex.quote(str(other))}" in line


def test_a_home_that_cannot_be_listed_is_read_by_the_usual_names(home: Path) -> None:
    """Her account passes through your home by name, never lists it: doctor, run as her, looks the usual names up one
    by one, and says that it read only those (finding 32)."""
    (home / ".zshrc").write_text("export PATH=x\n")
    (home / ".zshrc").chmod(0o644)
    (home / ".config").mkdir()
    (home / ".config").chmod(0o755)
    (home / "Notes").mkdir()
    (home / "Notes").chmod(0o755)  # not a usual name: unseen without the listing
    (home / ".gitconfig").write_text("[user]\n")
    (home / ".gitconfig").chmod(0o600)
    (home / ".ssh").chmod(0o700)
    assert share.open_in_home(home, {home / "dev"}) == ([home / ".config", home / ".zshrc", home / "Notes"], True)
    home.chmod(0o300)  # passing through by name only, as her account does
    try:
        assert share.open_in_home(home, {home / "dev"}) == ([home / ".config", home / ".zshrc"], False)
    finally:
        home.chmod(0o750)
    assert {".zshrc", ".config", ".gitconfig", ".ssh", ".bash_history"} <= set(share.USUAL_NAMES)


def test_the_documents_share_names_them_too(documents: Path) -> None:
    (documents / ".gitconfig").write_text("[user]\n")
    (documents / ".gitconfig").chmod(0o644)
    said: list[str] = []
    assert (
        share.share_documents(
            documents / "Estia-KB", documents, owner="alex", run=Recorder(), acl=no_acl, say=said.append
        )
        == 0
    )
    assert any(".gitconfig" in line and "chmod o-rwx" in line for line in said)
