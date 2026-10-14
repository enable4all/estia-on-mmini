"""Her read-only access to your projects folder, set by you without sudo: `scripts/share-projects.sh [--revoke]`.

In her own user (`_estia`) your home is closed to her. The share adds ACL entries, as you, the owner of the files:

- `user:_estia allow search` on your home (and on each folder between it and the projects folder): she passes through
  by name, never lists or reads anything there;
- `user:_estia allow list,search,readattr,readextattr,readsecurity,read,file_inherit,directory_inherit` on the
  projects folder and every folder and file in it, and inherited by what is made there later: read-only.

macOS's `chmod -R +a` sets the entry on the target of every symbolic link it meets in the tree (tested on macOS 27;
the man page says -R follows no link, which holds for modes, not for ACL entries). So the walk is this module's
(`os.walk`: no link followed, no link changed), only your own folders and files are named, and each batch goes to
`/bin/chmod -h +a`, which changes a link itself, never its target, if one is swapped in after the walk. An entry already
there, set before or inherited, is never added again (read with the ACL API, `acl_get_link_np`). The tree is shared
before your home opens to her, and the revoke closes your home first.
"""

from __future__ import annotations

import argparse
import ctypes
import functools
import json
import os
import pwd
import shlex
import stat
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from local_assistant.hostsec import CHMOD, tail
from local_assistant.ownuser import HER_CONFIG, ROLE_USER, SHARE, lookup_uid, owner_path

TREE_RIGHTS = "list,search,readattr,readextattr,readsecurity,read,file_inherit,directory_inherit"
PASS = "search"
# what an entry holds as the ACL API writes it (list is read, search is execute)
FILE_RIGHTS = frozenset({"read", "readattr", "readextattr", "readsecurity"})
FOLDER_RIGHTS = frozenset({"read", "execute", "readattr", "readextattr", "readsecurity"})
INHERIT = frozenset({"file_inherit", "directory_inherit"})
PASS_RIGHTS = frozenset({"execute"})
BATCH_PATHS = 500
BATCH_BYTES = 128 * 1024  # far below the argument limit (1 MB on macOS)
_LIBSYSTEM = "/usr/lib/libSystem.B.dylib"
_ACL_TYPE_EXTENDED = 0x00000100
SCRIPT = SHARE

Runner = Callable[[Sequence[str]], tuple[int, str]]
AclReader = Callable[[Path], list[str]]


def tree_entry(user: str) -> str:
    return f"user:{user} allow {TREE_RIGHTS}"


def pass_entry(user: str) -> str:
    return f"user:{user} allow {PASS}"


def run_tool(argv: Sequence[str]) -> tuple[int, str]:
    """A fixed argv (absolute tool, no shell): (exit code, its output)."""
    try:
        done = subprocess.run(
            list(argv), capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=600
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, type(exc).__name__
    return done.returncode, done.stdout + done.stderr


@functools.cache
def _libsystem() -> ctypes.CDLL:
    lib = ctypes.CDLL(_LIBSYSTEM, use_errno=True)
    lib.acl_get_link_np.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.acl_get_link_np.restype = ctypes.c_void_p
    lib.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
    lib.acl_to_text.restype = ctypes.c_void_p
    lib.acl_free.argtypes = [ctypes.c_void_p]
    lib.acl_free.restype = ctypes.c_int
    return lib


def acl_lines(path: Path) -> list[str]:
    """The ACL entries of `path` itself (a link's own, never its target's), as the ACL API writes them:
    `user:<uuid>:<name>:<id>:allow[,flags]:<rights>`. [] when it has none or it cannot be read."""
    lib = _libsystem()
    acl = lib.acl_get_link_np(os.fsencode(path), _ACL_TYPE_EXTENDED)
    if not acl:
        return []
    try:
        size = ctypes.c_ssize_t()
        text = lib.acl_to_text(acl, ctypes.byref(size))
        if not text:
            return []
        try:
            raw = ctypes.string_at(text, size.value).decode("utf-8", "replace")
        finally:
            lib.acl_free(text)
    finally:
        lib.acl_free(acl)
    return [line for line in raw.splitlines() if line and not line.startswith("!#")]


def _hers(line: str, user: str) -> tuple[set[str], set[str]] | None:
    """(flags, rights) of an allow entry for `user`; None for anything else."""
    parts = line.split(":")
    if len(parts) != 6 or parts[0] != "user" or parts[2] != user:
        return None
    kind, *flags = parts[4].split(",")
    if kind != "allow":
        return None
    return set(flags), set(parts[5].split(","))


def granted(lines: Sequence[str], user: str, rights: frozenset[str], flags: frozenset[str] = frozenset()) -> bool:
    """An allow entry for `user` (set or inherited, never only_inherit) holds `rights` and `flags`."""
    for line in lines:
        found = _hers(line, user)
        if found and "only_inherit" not in found[0] and rights <= found[1] and flags <= found[0]:
            return True
    return False


def _any_entry(lines: Sequence[str], user: str) -> bool:
    return any(_hers(line, user) is not None for line in lines)


def refusal(root: Path, home: Path, uid: int) -> str | None:
    """Why `root` may not be shared (None: it may): an absolute folder of yours inside your home, never your home
    itself, reached without a symbolic link."""
    if not root.is_absolute():
        return f"{root} is not an absolute path"
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        return f"no folder {root}"
    except OSError as exc:
        return f"{root} cannot be read ({exc.strerror})"
    if stat.S_ISLNK(info.st_mode):
        return f"{root} is a symbolic link: name the folder it leads to"
    if not stat.S_ISDIR(info.st_mode):
        return f"{root} is not a folder"
    plain, top = Path(os.path.normpath(root)), Path(os.path.normpath(home))
    if plain == top:
        return f"{root} is your home itself: name the projects folder in it"
    if not plain.is_relative_to(top):
        return f"{root} is outside your home {home}"
    if os.path.realpath(plain) != os.path.join(os.path.realpath(top), plain.relative_to(top)):
        return f"a folder on the way to {root} is a symbolic link"
    if info.st_uid != uid:
        return f"{root} is not yours"
    return None


def _passes(root: Path, home: Path) -> list[Path]:
    """Your home and each folder below it on the way to `root`: passing through only."""
    plain, top = Path(os.path.normpath(root)), Path(os.path.normpath(home))
    folders = [top]
    for part in plain.relative_to(top).parts[:-1]:
        folders.append(folders[-1] / part)
    return folders


def _walk(root: Path, uid: int, problems: list[str]) -> Iterator[tuple[Path, bool]]:
    """(path, is a folder) for the root and every folder and file in it that is yours; links are never followed or
    named, and anything else (another account's file, a socket) is left as it is."""

    def mine(path: Path) -> tuple[Path, bool] | None:
        try:
            info = os.lstat(path)
        except OSError:
            return None
        if info.st_uid != uid or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            return None
        return path, stat.S_ISDIR(info.st_mode)

    first = mine(root)
    if first is not None:
        yield first
    for here, dirs, files in os.walk(root, onerror=lambda exc: problems.append(f"{exc.filename}: {exc.strerror}")):
        for name in sorted([*dirs, *files]):
            found = mine(Path(here) / name)
            if found is not None:
                yield found


def _batches(head: list[str], paths: Sequence[Path]) -> Iterator[list[str]]:
    batch: list[str] = []
    size = 0
    for path in paths:
        text = str(path)
        if batch and (len(batch) >= BATCH_PATHS or size + len(text) > BATCH_BYTES):
            yield [*head, *batch]
            batch, size = [], 0
        batch.append(text)
        size += len(text) + 1
    if batch:
        yield [*head, *batch]


class _Steps:
    """The commands, run or (in a dry run) printed; the first failure is kept."""

    def __init__(self, run: Runner, say: Callable[[str], None], dry: bool, root: Path) -> None:
        self.run, self.say, self.dry, self.root = run, say, dry, root
        self.failed = ""

    def __call__(self, argv: list[str], fixed: int) -> bool:
        """`fixed`: how many words come before the paths (a long list is shown by its first path and a count)."""
        if self.dry:
            more = len(argv) - fixed - 1
            shown = shlex.join(argv[: fixed + 1])
            self.say(f"would run: {shown}" + (f" … and {more} more (in {self.root}; no link)" if more > 0 else ""))
            return True
        code, out = self.run(argv)
        if code != 0 and not self.failed:
            why = tail(out)
            self.failed = f"{shlex.join(argv[:fixed])} failed (exit {code})" + (f": {why}" if why else "")
        return code == 0


def share_projects(
    root: Path,
    home: Path,
    user: str = ROLE_USER,
    *,
    run: Runner = run_tool,
    acl: AclReader = acl_lines,
    say: Callable[[str], None] = print,
    dry: bool = False,
    uid: int | None = None,
) -> int:
    """Give `user` read-only access to `root` and passing through on your home, as you. 0: done (or nothing to do);
    1: refused or failed (the reason said)."""
    uid = os.getuid() if uid is None else uid
    why = refusal(root, home, uid)
    if why:
        say(f"✗ {why}. Nothing changed.")
        return 1
    problems: list[str] = []
    todo: list[Path] = []
    kept = 0
    for path, is_folder in _walk(root, uid, problems):
        lines = acl(path)
        has = granted(lines, user, FOLDER_RIGHTS, INHERIT) if is_folder else granted(lines, user, FILE_RIGHTS)
        if has:
            kept += 1
        else:
            todo.append(path)
    steps = _Steps(run, say, dry, root)
    for argv in _batches([CHMOD, "-h", "+a", tree_entry(user)], todo):
        steps(argv, 4)
    if steps.failed:
        say(f"✗ {steps.failed}. Your home stays closed to her; run {SCRIPT} again when it is fixed.")
        return 1
    for folder in _passes(root, home):
        if not granted(acl(folder), user, PASS_RIGHTS):
            steps([CHMOD, "+a", pass_entry(user), str(folder)], 3)
    if steps.failed:
        say(f"✗ {steps.failed}.")
        return 1
    for problem in problems[:3]:
        say(f"! not shared, it cannot be listed: {problem}")
    if dry:
        say(f"Nothing changed (a dry run): {len(todo)} folders and files to share, {kept} already shared.")
        return 0
    say(
        f"✓ {user} reads {root} read-only ({len(todo)} folders and files shared now, {kept} already); in your home "
        f"it only passes through, by name, to that folder. Take it back: {SCRIPT} --revoke"
    )
    return 0


def revoke_projects(
    root: Path,
    home: Path,
    user: str = ROLE_USER,
    *,
    run: Runner = run_tool,
    acl: AclReader = acl_lines,
    say: Callable[[str], None] = print,
    dry: bool = False,
    uid: int | None = None,
) -> int:
    """Take back what `share_projects` gave: your home closes first, then every entry of hers in the tree (set or
    inherited) goes."""
    uid = os.getuid() if uid is None else uid
    why = refusal(root, home, uid)
    if why:
        say(f"✗ {why}. Nothing changed.")
        return 1
    steps = _Steps(run, say, dry, root)
    for folder in _passes(root, home):
        if granted(acl(folder), user, PASS_RIGHTS):
            steps([CHMOD, "-a", pass_entry(user), str(folder)], 3)
    problems: list[str] = []
    todo = [path for path, _folder in _walk(root, uid, problems) if _any_entry(acl(path), user)]
    for argv in _batches([CHMOD, "-h", "-a", tree_entry(user)], todo):
        steps(argv, 4)
    if steps.failed:
        say(f"✗ {steps.failed}.")
        return 1
    if dry:
        say(f"Nothing changed (a dry run): {len(todo)} folders and files to take back.")
        return 0
    say(f"✓ {user} no longer reads {root} ({len(todo)} folders and files taken back); your home is closed to her.")
    return 0


def _recorded(marker: Path | None) -> tuple[bool, object]:
    """What the move recorded as her projects folder: (True, the value) when its record names one (None or "" when
    projects were off), (False, None) without a record, after a rollback or in an older record."""
    if marker is None:
        return False, None
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, None
    if isinstance(record, dict) and not record.get("rolled_back") and "projects_root" in record:
        return True, record["projects_root"]
    return False, None


def _your_projects(home: Path) -> tuple[bool, object]:
    """Your config's [projects]: (True, the table, or None when it has none), (False, None) when it does not load."""
    try:
        with (home / HER_CONFIG).open("rb") as handle:
            projects = tomllib.load(handle).get("projects")
    except (OSError, tomllib.TOMLDecodeError):
        return False, None
    return True, projects if isinstance(projects, dict) else None


def default_root(home: Path, marker: Path | None) -> Path | None:
    """The projects folder to share: what the move wrote into her config (the move's record, `marker`), else your
    config's `[projects] root` (~ is your home; ~/dev without one). None: the move recorded none, or projects are off
    (no [projects] in your config)."""
    recorded, value = _recorded(marker)
    if recorded:
        return Path(value) if isinstance(value, str) and value else None
    _, projects = _your_projects(home)
    if not isinstance(projects, dict):
        return None  # projects are off (or no config): nothing to share
    root = projects.get("root", "~/dev")
    return owner_path(root, home) if isinstance(root, str) else None


def projects_off(home: Path, marker: Path | None) -> bool:
    """Projects are known to be off: the move recorded no folder for her (it writes one only with [projects]), or your
    config loads and has no [projects]. Not known (no record, no config): False."""
    recorded, value = _recorded(marker)
    if recorded:
        return not value
    loaded, projects = _your_projects(home)
    return loaded and projects is None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=SCRIPT, description="Give her user read-only access to your projects folder (no sudo), or take it back."
    )
    parser.add_argument("folder", nargs="?", type=Path, help="the projects folder (default: the one on record)")
    parser.add_argument("--revoke", action="store_true", help="take her access back")
    parser.add_argument("--dry-run", action="store_true", help="print every command, change nothing")
    parser.add_argument("--marker", type=Path, help="the move's record (her projects folder)")
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        print(f"run it as yourself, without sudo: {SCRIPT}", file=sys.stderr)
        return 2
    home = Path(os.environ.get("HOME") or pwd.getpwuid(os.getuid()).pw_dir)
    root = Path(os.path.abspath(args.folder.expanduser())) if args.folder else default_root(home, args.marker)
    if root is None:
        if projects_off(home, args.marker):  # no tool would read a shared folder: none is suggested
            print("Projects are off (no [projects] in the config): nothing to share.")
        else:
            print(
                f"No projects folder on record for her: with [projects] in her config, name its folder: {SCRIPT} <folder>."
            )
        return 0
    if not args.dry_run and lookup_uid(ROLE_USER) is None:
        print(f"✗ her user {ROLE_USER} does not exist yet: the move into her own user makes it. Nothing changed.")
        return 1
    act = revoke_projects if args.revoke else share_projects
    return act(root, home, dry=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
