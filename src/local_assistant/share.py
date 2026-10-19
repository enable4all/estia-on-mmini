"""Her read-only access to your projects folder, set by you without sudo: `scripts/share-projects.sh [--revoke]`; and
her access to your documents folder, ~/Estia-KB: `scripts/share-documents.sh [--revoke]`.

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

The documents folder (always ~/Estia-KB, directly in your home, made 0700 when missing) gets the same read-only entry
on every folder and file of yours in it, and two more on the folder itself: `user:_estia allow add_file` (she adds
files there, never a folder and never anything below it; nothing inherits it, so she never writes, renames or deletes
your files) and `user:<you> allow <every right>,file_inherit,directory_inherit` (what she adds is yours to open, move
or delete in Finder). A folder that holds a git checkout (`.git`) is refused: her account would add files to code you
run. A small file, `.estia-share`, holds the share's random token: the revoke deletes it first, so a new share has a
new token and her index of the documents starts fresh. Your home is its only way in (macOS guards Documents, Desktop
and Downloads: live, 14 Oct, even your own SSH session could not add her entry to ~/Documents, "Operation not
permitted"). Both shares pass through your home: a revoke keeps it open while the other share is in place. macOS's
`chmod -a` takes the named rights (and flags) out of every entry of that user, so the revoke removes her entries only,
never yours.

Her account may add files to that folder, and macOS lets any account make a hard link, a second name, to a file it
cannot read: your ~/.zsh_history as `invoice.pdf` there. An ACL entry belongs to the file, not to the name, so sharing
the link would open your file. The documents share therefore never names a file with a second name (`st_nlink` > 1),
never goes into a folder that is not yours (its names are another account's to change), and refuses a token file with
a second name. Each path it names stays yours meanwhile: in your folders she may add names, never rename or remove one.
For the same reason the projects folder is never ~/Estia-KB or a folder in it.

Passing through your home by name, her account may open anything directly in it that lets every account in (the macOS
default for a new file or folder: ~/.zshrc, ~/.config), and what such a folder holds. Both shares name those entries,
with the command that closes them (`chmod -h o-rwx`), and in a terminal ask once whether to run it (Enter runs it).
"""

from __future__ import annotations

import argparse
import ctypes
import functools
import json
import os
import pwd
import secrets
import shlex
import stat
import subprocess
import sys
import tomllib
from collections.abc import Callable, Collection, Iterator, Sequence
from pathlib import Path

from local_assistant.config import DOCUMENTS_FOLDER
from local_assistant.documents import SHARE_TOKEN
from local_assistant.hostsec import CHMOD, tail
from local_assistant.ownuser import HER_CONFIG, ROLE_USER, SHARE, lookup_uid, owner_path
from local_assistant.prompts import HER_CONFIG_EDIT

TREE_RIGHTS = "list,search,readattr,readextattr,readsecurity,read,file_inherit,directory_inherit"
PASS = "search"
# what an entry holds as the ACL API writes it (list is read, search is execute)
FILE_RIGHTS = frozenset({"read", "readattr", "readextattr", "readsecurity"})
FOLDER_RIGHTS = frozenset({"read", "execute", "readattr", "readextattr", "readsecurity"})
INHERIT = frozenset({"file_inherit", "directory_inherit"})
PASS_RIGHTS = frozenset({"execute"})
# the documents folder itself: adding files there (never a folder; inherited by nothing), and your full control of what
# she adds (inherited by every new file and folder)
ADD_RIGHTS = "add_file"
OWNER_RIGHTS = (
    "list,search,add_file,add_subdirectory,delete_child,delete,readattr,writeattr,readextattr,writeextattr,"
    "readsecurity,writesecurity,chown,file_inherit,directory_inherit"
)
ADD_HELD = frozenset({"write"})  # add_file, as the ACL API writes it for a folder
OWNER_HELD = frozenset(
    {
        "read",
        "write",
        "execute",
        "append",
        "delete",
        "delete_child",
        "readattr",
        "writeattr",
        "readextattr",
        "writeextattr",
        "readsecurity",
        "writesecurity",
        "chown",
    }
)
DOCUMENTS = Path(DOCUMENTS_FOLDER)  # always this folder, directly in your home, whatever her name (never in Documents)
DOCUMENTS_SCRIPT = "scripts/share-documents.sh"
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


def add_entry(user: str) -> str:
    return f"user:{user} allow {ADD_RIGHTS}"


def owner_entry(owner: str) -> str:
    return f"user:{owner} allow {OWNER_RIGHTS}"


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


def _in_estia_kb(root: Path, home: Path) -> bool:
    """`root` is your ~/Estia-KB or a folder in it (compared as files: on APFS ~/estia-kb is the same folder)."""
    try:
        kb = os.stat(home / DOCUMENTS)
    except OSError:
        return False
    here, top = Path(os.path.normpath(root)), Path(os.path.normpath(home))
    while here != top and here.is_relative_to(top):
        try:
            info = os.stat(here)
        except OSError:
            return False
        if (info.st_dev, info.st_ino) == (kb.st_dev, kb.st_ino):
            return True
        here = here.parent
    return False


# names macOS and common tools make directly in a home: where the home cannot be listed (her account, and so doctor
# run as her, passes through it by name only), these are looked up one by one
USUAL_NAMES = (
    ".aws",
    ".bash_history",
    ".bash_profile",
    ".bashrc",
    ".cache",
    ".cargo",
    ".claude",  # an AI's notes and settings (19 Oct: open, and doctor named nothing)
    ".claude.json",
    ".codex",
    ".config",
    ".docker",
    ".gemini",
    ".gitconfig",
    ".gnupg",
    ".kube",
    ".local",
    ".matplotlib",
    ".netrc",
    ".npm",
    ".npmrc",
    ".ollama",  # Ollama's models and key: Ollama runs as you, nothing of hers reads them (~/opt: OLLAMA_BUILD below)
    ".profile",
    ".python_history",
    ".rustup",
    ".ssh",
    ".swiftpm",
    ".viminfo",
    ".zprofile",
    ".zsh_history",
    ".zsh_sessions",
    ".zshenv",
    ".zshrc",
    "Desktop",
    "Documents",
    "Downloads",
    "Library",
    "Movies",
    "Music",
    "Pictures",
    "Sites",
    "dev",
    "estia",
)
# the same words in the share scripts and in doctor (finding 32)
# (review, 19 Oct: why closing matters, in plain words; the fix on a line of its own, `_say_open`)
OPEN_TEXT = (
    "every account may open {names} (the macOS default for a new file or folder), and her account passes through your "
    "home to the folder shared with her: her code never opens them, but a program that took over her account could"
)
OPEN_FIX = "Close what should stay yours (no admin rights needed): chmod -h o-rwx {paths}"
# a folder below your home on the way to the shared one: her entry lets her pass through it by name too (review 15 Oct)
OPEN_WAY_TEXT = (
    "every account may open {names}, and her account passes through it on its way: her code never opens them, but a "
    "program that took over her account could"
)
# Ollama's official build (ollama_build installs it there): doctor, run as her, reads its program and libraries through
# your home's ~/opt to name its version and signature, so that ~/opt keeps its pass-through and only its listing closes
# (review, 19 Oct); what else in it lets every account in is named like any other entry
OLLAMA_BUILD = Path("opt/ollama")
OPT_TEXT = (
    "every account may see what is in opt, which holds Ollama; her account still needs its way through it (the doctor "
    "checks Ollama as her)"
)
OPT_FIX = "Close its listing and keep her way through (no admin rights needed): chmod -h o-r {path}"
# closing them as you (owner, 19 Oct: "word it good, I do not fully understand it, go safe"): one question, only in a
# terminal and never in a dry run; what closing changes and never breaks first, then the question naming the command
# that runs (`_close`: chmod -h o-rwx, for ~/opt chmod -h o-r); Enter closes them. Other people's accounts on this Mac
# are in your group (staff), which keeps its access; hers is not (her own group, _estia), nor are the Mac's own
# accounts that programs run as (a guest file share's, the web server's _www)
CLOSE_WHAT = (
    "Closing them: her account and the Mac's own accounts that programs run as can no longer open them;\n"
    "  a guest file share or a web page served from them stops too; you keep full access, and people who log in\n"
    "  on this Mac keep what they can open today (every login is in your group, staff).\n"
    "  Nothing in them changes; her share and Ollama keep working."
)
CLOSE_ASK = "Close them now with chmod -h o-rwx (no Mac password)? Recommendation: yes."
CLOSE_ASK_OPT = (
    "Close them now with chmod -h o-rwx, and opt with chmod -h o-r (her way to Ollama stays; no Mac password)? "
    "Recommendation: yes."
)
# without a terminal (an installing AI's command tool) nothing is asked; a dry run says what a terminal is asked
CLOSE_NO_TERMINAL = (
    "Nothing is asked without a terminal: in your own Terminal window, close them with the chmod lines above,\n"
    "  or run {script} there, where it asks once."
)
CLOSE_DRY_RUN = '  (in a terminal it then asks "{ask} [Y/n]")'
# the move's end (scripts/migrate-to-own-user.sh, --after-move): a heading only over a folder to share
AFTER_MOVE = "Her projects folder, read-only for her (no admin rights needed):"
AFTER_MOVE_DRY_RUN = "Her projects folder, read-only for her, at the end of the move (no admin rights needed):"
CLOSE_ASK_OPT_ONLY = (
    "Close opt's listing now with chmod -h o-r (her way to Ollama stays; no Mac password)? Recommendation: yes."
)
CLOSED = "✓ Closed to her account: {names}."
NOT_CLOSED = "! Not closed: {names} ({why}). {fix}"


def open_to_others(home: Path, shared: Collection[Path] = (), names: Sequence[str] | None = None) -> list[Path]:
    """What directly in your home every account may open (a file others may read, a folder others may read or pass
    through; never a link), by name: with a share on, her account passes through your home and may open them too.
    The shared folders and ~/Public (macOS's own folder for every account) are left out. `names`: the ones to look
    up (default: the home's listing; none when it cannot be listed)."""
    skip = {os.path.normpath(path) for path in shared}
    if names is None:
        try:
            names = [entry.name for entry in os.scandir(home)]
        except OSError:
            return []
    found: list[Path] = []
    for name in sorted(names):
        path = home / name
        if name == "Public" or os.path.normpath(path) in skip:
            continue
        if _lets_others_in(path):
            found.append(path)
    return found


def _lets_others_in(path: Path) -> bool:
    """A file others may read or a folder others may read or pass through; never a link, or a name that is gone."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return False
    bits = stat.S_IROTH | stat.S_IXOTH if stat.S_ISDIR(info.st_mode) else stat.S_IROTH
    return bool(info.st_mode & bits)


def open_in_home(home: Path, shared: Collection[Path] = ()) -> tuple[list[Path], bool]:
    """`open_to_others`, and whether the whole home was read: where it cannot be listed (as her: she only passes
    through it by name), the usual names are looked up one by one (`USUAL_NAMES`)."""
    try:
        with os.scandir(home) as entries:
            names = [entry.name for entry in entries]
    except OSError:
        return open_to_others(home, shared, USUAL_NAMES), False
    return open_to_others(home, shared, names), True


def open_names(found: Sequence[Path]) -> str:
    """The names for `OPEN_TEXT`: the first 8, then how many more."""
    return ", ".join(path.name for path in found[:8]) + (f" and {len(found) - 8} more" if len(found) > 8 else "")


def _say_open(
    home: Path,
    shared: Collection[Path],
    say: Callable[[str], None],
    root: Path | None = None,
    *,
    ask: Callable[[str], bool] | None = None,
    run: Runner = run_tool,
    acl: AclReader = acl_lines,
    dry: bool = False,
    script: str = SCRIPT,
) -> None:
    """What every account may open in your home and, with `root` deeper than one level, in each folder on the way to
    it (her entry passes her through each by name): named, with the fix. The next folder on the way and the shared
    folders are left out; your home's ~/opt with Ollama's build in it keeps its pass-through (OLLAMA_BUILD). With
    `ask` (a terminal), what closing changes, then one question for all of them; a yes closes them, as you (`chmod -h
    o-rwx`, for that ~/opt `o-r`), and says what is closed and what is still open. A dry run asks nothing and shows
    the question; without a terminal, the line says where `script` asks it."""
    opt = home / OLLAMA_BUILD.parts[0] if (home / OLLAMA_BUILD).is_dir() and not os.path.islink(home / "opt") else None
    found = open_to_others(home, (*shared, *([opt] if opt is not None else [])))
    every: list[tuple[Path, str]] = [(path, "o-rwx") for path in found]
    if found:
        text = OPEN_TEXT.format(names=open_names(found))
        say(f"! In your home {text}.\n  {OPEN_FIX.format(paths=shlex.join(str(path) for path in found))}")
    if opt is not None:
        if _others_may_list(opt):
            say(f"! In your home {OPT_TEXT}.\n  {OPT_FIX.format(path=shlex.quote(str(opt)))}")
            every.append((opt, "o-r"))
        inside = open_to_others(opt, (home / OLLAMA_BUILD,))
        every += [(path, "o-rwx") for path in inside]
        if inside:
            text = OPEN_WAY_TEXT.format(names=open_names(inside))
            say(f"! In {opt} {text}.\n  {OPEN_FIX.format(paths=shlex.join(str(path) for path in inside))}")
    ways = _passes(root, home)[1:] if root is not None else []
    for index, way in enumerate(ways):
        onward = ways[index + 1] if index + 1 < len(ways) else Path(os.path.normpath(root or home))
        found = open_to_others(way, (*shared, onward))
        every += [(path, "o-rwx") for path in found]
        if found:
            text = OPEN_WAY_TEXT.format(names=open_names(found))
            say(f"! In {way} {text}.\n  {OPEN_FIX.format(paths=shlex.join(str(path) for path in found))}")
    if not every:
        return
    modes = {how for _path, how in every}
    question = CLOSE_ASK_OPT if modes == {"o-rwx", "o-r"} else CLOSE_ASK_OPT_ONLY if modes == {"o-r"} else CLOSE_ASK
    if dry:
        say(CLOSE_DRY_RUN.format(ask=question))
    elif ask is None:
        say(CLOSE_NO_TERMINAL.format(script=script))
    else:
        say(CLOSE_WHAT)
        if ask(question):
            _close(every, run, say, acl)


def _close(found: Sequence[tuple[Path, str]], run: Runner, say: Callable[[str], None], acl: AclReader) -> None:
    """`chmod -h <mode>` on what `_say_open` found (no link: `-h` changes a link itself, never its target), then each
    read again, its mode bits and its access rules (an ACL entry for every account or for her): what is closed is
    said, and what is still open named with its fix. A name gone meanwhile (chmod fails on it) is open to no one."""
    failed = ""
    for mode in ("o-rwx", "o-r"):
        for argv in _batches([CHMOD, "-h", mode], [path for path, how in found if how == mode]):
            code, out = run(argv)
            if code != 0 and not failed:
                failed = tail(out) or f"exit {code}"
    closed: list[Path] = []
    still: list[tuple[Path, str]] = []
    by_rule_only = True
    for path, mode in found:
        if not os.path.lexists(path):
            continue
        by_bits = _lets_others_in(path) if mode == "o-rwx" else _others_may_list(path)
        by_rule = _rule_lets_her_in(acl(path))
        if by_bits or by_rule:
            still.append((path, mode))
            by_rule_only = by_rule_only and not by_bits
        else:
            closed.append(path)
    if closed:
        say(CLOSED.format(names=open_names(closed)))
    if not still:
        return
    why = failed or ("an access rule lets her in: ls -le shows it" if by_rule_only else "still open")
    fixes = [OPEN_FIX.format(paths=shlex.join(str(path) for path, mode in still if mode == "o-rwx"))]
    fixes += [OPT_FIX.format(path=shlex.quote(str(path))) for path, mode in still if mode == "o-r"]
    if not any(mode == "o-rwx" for _path, mode in still):
        fixes = fixes[1:]
    say(NOT_CLOSED.format(names=open_names([path for path, _mode in still]), why=why, fix=" ".join(fixes)))


def _others_may_list(path: Path) -> bool:
    """A folder every account may list (its read bit for others); never a link, or a name that is gone."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and bool(info.st_mode & stat.S_IROTH)


def _rule_lets_her_in(lines: Sequence[str]) -> bool:
    """An ACL allow entry (not only inherited by what is made in it) that lets every account or her account read,
    list or pass through: mode bits closed, she still opens it."""
    for line in lines:
        parts = line.split(":")
        if len(parts) < 6:
            continue
        name, flags, rights = parts[2], set(parts[4].split(",")), set(parts[5].split(","))
        allows = name in ("everyone", ROLE_USER) and "allow" in flags and "only_inherit" not in flags
        if allows and rights & {"read", "list", "search", "execute"}:
            return True
    return False


def terminal_ask() -> Callable[[str], bool] | None:
    """A yes from your terminal for the close question: Enter takes the safe answer, closing (owner, 19 Oct: [Y/n]);
    a no, anything else, the end of input or a Ctrl-C keeps everything as it is (the share is done by then). None
    unless both the question and the answer are a terminal's (output sent to a file would hide the question):
    nothing is asked."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None

    def ask(question: str) -> bool:
        try:  # the question on stdout, the end checked above (input() may write it to stderr)
            print(f"{question} [Y/n] ", end="", flush=True)
            answer = sys.stdin.readline()
        except KeyboardInterrupt:
            answer = ""
        if not answer.endswith("\n"):  # the end of input or a Ctrl-C: never a yes
            print()
            return False
        typed = answer.strip().lower()
        return typed.startswith("y") if typed else True

    return ask


def _passes(root: Path, home: Path) -> list[Path]:
    """Your home and each folder below it on the way to `root`: passing through only."""
    plain, top = Path(os.path.normpath(root)), Path(os.path.normpath(home))
    folders = [top]
    for part in plain.relative_to(top).parts[:-1]:
        folders.append(folders[-1] / part)
    return folders


def _walk(root: Path, uid: int, problems: list[str], linked: list[Path] | None = None) -> Iterator[tuple[Path, bool]]:
    """(path, is a folder) for the root and every folder and file in it that is yours; links are never followed or
    named, and anything else (another account's file, a socket) is left as it is. With `linked` (the documents share,
    where her account adds files): a file with a second name (a hard link) is never named but kept in `linked`, and a
    folder that is not yours is not gone into."""

    def mine(path: Path) -> tuple[Path, bool] | None:
        try:
            info = os.lstat(path)
        except OSError:
            return None
        if info.st_uid != uid or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            return None
        if linked is not None and stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            linked.append(path)
            return None
        return path, stat.S_ISDIR(info.st_mode)

    first = mine(root)
    if first is not None:
        yield first
    for here, dirs, files in os.walk(root, onerror=lambda exc: problems.append(f"{exc.filename}: {exc.strerror}")):
        found_dirs = {name: mine(Path(here) / name) for name in dirs}
        if linked is not None:  # another account's folder: its names are that account's to change
            dirs[:] = [name for name in dirs if found_dirs[name] is not None]
        for name in sorted([*dirs, *files]):
            found = found_dirs[name] if name in found_dirs else mine(Path(here) / name)
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
    ask: Callable[[str], bool] | None = None,
) -> int:
    """Give `user` read-only access to `root` and passing through on your home, as you. 0: done (or nothing to do);
    1: refused or failed (the reason said). `ask`: whether to close what every account may open (`_say_open`)."""
    uid = os.getuid() if uid is None else uid
    why = refusal(root, home, uid)
    if not why and _in_estia_kb(root, home):  # her account adds files there: a second name of a file of yours
        why = f"{root} is your ~/Estia-KB or a folder in it, where her account adds files: name another folder"
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
    else:
        say(
            f"✓ {user} reads {root} read-only ({len(todo)} folders and files shared now, {kept} already); in your home "
            f"it only passes through to that folder: her account cannot list your home. Take it back: {SCRIPT} --revoke"
        )
    _say_open(home, (root, home / DOCUMENTS), say, root, ask=ask, run=run, acl=acl, dry=dry)
    return 0


def _still_needed(keep: Path | None, home: Path, user: str, acl: AclReader) -> set[Path]:
    """The folders on the way to the other share (`keep`) while it is in place (an entry of hers on it): its revoke
    keeps them open."""
    if keep is None or not keep.is_absolute() or not os.path.lexists(keep):
        return set()
    plain, top = Path(os.path.normpath(keep)), Path(os.path.normpath(home))
    if plain == top or not plain.is_relative_to(top) or not _any_entry(acl(plain), user):
        return set()
    return set(_passes(plain, top))


def _close_the_way(root: Path, home: Path, user: str, acl: AclReader, steps: _Steps, keep: Path | None) -> None:
    needed = _still_needed(keep, home, user, acl)
    for folder in _passes(root, home):
        if folder not in needed and granted(acl(folder), user, PASS_RIGHTS):
            steps([CHMOD, "-a", pass_entry(user), str(folder)], 3)


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
    keep: Path | None = None,
) -> int:
    """Take back what `share_projects` gave: your home closes first (unless the other share, `keep`, still passes
    through it), then every entry of hers in the tree (set or inherited) goes."""
    uid = os.getuid() if uid is None else uid
    why = refusal(root, home, uid)
    if why:
        say(f"✗ {why}. Nothing changed.")
        return 1
    steps = _Steps(run, say, dry, root)
    _close_the_way(root, home, user, acl, steps, keep)
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


def _make_folders(folder: Path, home: Path, say: Callable[[str], None], dry: bool) -> str | None:
    """Make the folders missing between your home and `folder`, each yours alone (0700; hers only by the ACL), never
    through a link (`refusal` names one). Why it failed, else None."""
    plain, top = Path(os.path.normpath(folder)), Path(os.path.normpath(home))
    if not folder.is_absolute() or plain == top or not plain.is_relative_to(top):
        return None  # refusal says why
    here = top
    for part in plain.relative_to(top).parts:
        here = here / part
        try:
            if stat.S_ISLNK(os.lstat(here).st_mode):
                return None
            continue
        except FileNotFoundError:
            pass
        except OSError as exc:
            return f"{here} cannot be read ({exc.strerror})"
        if dry:
            say(f"would make: {here} (yours alone, 0700)")
            continue
        try:
            os.mkdir(here, 0o700)
            os.chmod(here, 0o700, follow_symlinks=False)  # the umask may have taken bits away: exactly 0700
        except OSError as exc:
            return f"{here} could not be made ({exc.strerror})"
        say(f"made {here} (yours alone, 0700)")
    return None


def _checkout(folder: Path, uid: int) -> str | None:
    """A git checkout is never the documents folder: her account would add files (a conftest.py) to code you run.
    A .git that is not yours is refused too, with its own reason: her account may add a file by that name."""
    git = folder / ".git"
    try:
        info = os.lstat(git)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"{git} cannot be read ({exc.strerror})"
    if info.st_uid != uid:
        return (
            f"{git} is not yours (her account may have added it): check it, delete it (rm {shlex.quote(str(git))}), "
            "then run the share again"
        )
    return f"{folder} holds a git checkout (.git): her account would add files to your code"


def _new_token(folder: Path, say: Callable[[str], None], dry: bool) -> str | None:
    """The share's token file: kept when there (the same share), written when not (a new share: her index starts
    fresh). Why it cannot be, else None."""
    path = folder / SHARE_TOKEN
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        info = None
    except OSError as exc:
        return f"{path} cannot be read ({exc.strerror})"
    if info is not None:  # a second name (a hard link) may name a file of yours elsewhere
        plain = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        return None if plain else f"{path} is not a plain file: remove it"
    if dry:
        say(f"would write: {path} (a new share: her index of the documents starts fresh)")
        return None
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(16) + "\n")
    except OSError as exc:
        return f"{path} could not be written ({exc.strerror})"
    return None


def share_documents(
    folder: Path,
    home: Path,
    user: str = ROLE_USER,
    *,
    owner: str | None = None,
    run: Runner = run_tool,
    acl: AclReader = acl_lines,
    say: Callable[[str], None] = print,
    dry: bool = False,
    uid: int | None = None,
    ask: Callable[[str], bool] | None = None,
) -> int:
    """Let `user` list and read `folder` (made when missing) and add files to it (never a folder, never below it), never
    delete or change yours, and give you (`owner`) full control of what she adds; then passing through on your home,
    as you. A file with a second name (a hard link) is never shared. 0: done (or nothing to do); 1: refused or failed
    (the reason said). `ask`: whether to close what every account may open (`_say_open`)."""
    uid = os.getuid() if uid is None else uid
    owner = owner or pwd.getpwuid(uid).pw_name
    made = _make_folders(folder, home, say, dry)
    if made:
        say(f"✗ {made}. Nothing shared.")
        return 1
    if dry and not os.path.lexists(folder):
        for way in _passes(folder, home):
            say(f"would run: {shlex.join([CHMOD, '+a', pass_entry(user), str(way)])}")
        say(f"Nothing changed (a dry run): {folder} is made and shared at the real run.")
        return 0
    why = refusal(folder, home, uid) or _checkout(folder, uid)
    if why:
        say(f"✗ {why}. Nothing changed.")
        return 1
    problems: list[str] = []
    linked: list[Path] = []
    reads: list[Path] = []
    adds: list[Path] = []
    yours: list[Path] = []
    kept = 0
    for path, is_folder in _walk(folder, uid, problems, linked):
        lines = acl(path)
        if path == folder:
            missing = [
                (reads, granted(lines, user, FOLDER_RIGHTS, INHERIT)),
                (adds, granted(lines, user, ADD_HELD)),
                (yours, granted(lines, owner, OWNER_HELD, INHERIT)),
            ]
        elif is_folder:
            missing = [(reads, granted(lines, user, FOLDER_RIGHTS, INHERIT))]
        else:
            missing = [(reads, granted(lines, user, FILE_RIGHTS))]
        for todo, has in missing:
            if not has:
                todo.append(path)
        kept += all(has for _todo, has in missing)
    steps = _Steps(run, say, dry, folder)
    for entry_text, todo in ((tree_entry(user), reads), (add_entry(user), adds), (owner_entry(owner), yours)):
        for argv in _batches([CHMOD, "-h", "+a", entry_text], todo):
            steps(argv, 4)
    if steps.failed:
        say(f"✗ {steps.failed}. Your home stays closed to her; run {DOCUMENTS_SCRIPT} again when it is fixed.")
        return 1
    token = _new_token(folder, say, dry)
    if token:
        say(f"✗ {token}. Your home stays closed to her.")
        return 1
    for way in _passes(folder, home):
        if not granted(acl(way), user, PASS_RIGHTS):
            steps([CHMOD, "+a", pass_entry(user), str(way)], 3)
    if steps.failed:
        say(f"✗ {steps.failed}.")
        return 1
    for problem in problems[:3]:
        say(f"! not shared, it cannot be listed: {problem}")
    for path in linked[:3]:
        say(f"! not shared, a hard link (a second name of a file that may be elsewhere): {path}")
    if len(linked) > 3:
        say(f"! not shared: {len(linked) - 3} more hard links")
    changed = len({*reads, *adds, *yours})
    if dry:
        say(f"Nothing changed (a dry run): {changed} folders and files to share, {kept} already shared.")
    else:
        say(
            f"✓ {user} lists and reads {folder} and adds files to it, and may never delete or change yours; "
            f"you keep full control of what she adds ({changed} folders and files shared now, {kept} already). In "
            f"your home she only passes through to that folder: her account cannot list your home. Take it back: "
            f"{DOCUMENTS_SCRIPT} --revoke"
        )
        # one line each, as TOML needs them: pasted as printed, her config still loads (review 15 Oct, 17)
        say(f"Her config, these two lines ({HER_CONFIG_EDIT}, then restart her):")
        say("  [documents]")
        say(f"  folder = {json.dumps(str(folder), ensure_ascii=False)}")
    _say_open(home, (folder,), say, ask=ask, run=run, acl=acl, dry=dry, script=DOCUMENTS_SCRIPT)
    return 0


def revoke_documents(
    folder: Path,
    home: Path,
    user: str = ROLE_USER,
    *,
    owner: str | None = None,
    run: Runner = run_tool,
    acl: AclReader = acl_lines,
    say: Callable[[str], None] = print,
    dry: bool = False,
    uid: int | None = None,
    keep: Path | None = None,
) -> int:
    """Take back what `share_documents` gave her: the way closes first (your home stays open while the other share,
    `keep`, passes through it), then the share's token goes (her index goes at her next look; a new share starts a
    fresh one, also when what follows fails), then her entries go from every folder and file of yours in it. Your own
    entry stays: what she added stays yours to open, move or delete. `owner` is not used: no entry of yours is
    changed."""
    uid = os.getuid() if uid is None else uid
    why = refusal(folder, home, uid)
    if why:
        say(f"✗ {why}. Nothing changed.")
        return 1
    steps = _Steps(run, say, dry, folder)
    _close_the_way(folder, home, user, acl, steps, keep)
    token = folder / SHARE_TOKEN
    ended = ""
    if dry:
        say(f"would remove: {token} (a new share starts a fresh index)")
    else:
        try:
            token.unlink(missing_ok=True)
        except OSError as exc:
            ended = f"{token} could not be removed ({exc.strerror})"
    problems: list[str] = []
    found = [(path, acl(path)) for path, _folder in _walk(folder, uid, problems)]
    reads = [path for path, lines in found if _any_entry(lines, user)]
    # macOS's chmod -a takes the named rights out of her entries: after the read entry, what is left of the add entry
    adds = [path for path, lines in found if _her_rights(lines, user) & {"write", "append"}]
    for entry_text, todo in ((tree_entry(user), reads), (add_entry(user), adds)):
        for argv in _batches([CHMOD, "-h", "-a", entry_text], todo):
            steps(argv, 4)
    if steps.failed or ended:
        say(f"✗ {steps.failed or ended}.")
        return 1
    if dry:
        say(f"Nothing changed (a dry run): {len(reads)} folders and files to take back.")
        return 0
    say(
        f"✓ {user} no longer reads or adds to {folder} ({len(reads)} folders and files taken back); her index of the "
        "documents goes at her next look, and a new share starts a fresh one."
    )
    return 0


def _her_rights(lines: Sequence[str], user: str) -> set[str]:
    rights: set[str] = set()
    for line in lines:
        found = _hers(line, user)
        if found is not None:
            rights |= found[1]
    return rights


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
        prog=SCRIPT,
        description="Give her user read-only access to your projects folder, or list, read and add access to your "
        "~/Estia-KB (--documents); no sudo. --revoke takes it back.",
    )
    parser.add_argument("folder", nargs="?", type=Path, help="the projects folder (default: the one on record)")
    parser.add_argument("--documents", action="store_true", help="your ~/Estia-KB (made when missing)")
    parser.add_argument("--revoke", action="store_true", help="take her access back")
    parser.add_argument("--dry-run", action="store_true", help="print every command, change nothing")
    parser.add_argument("--marker", type=Path, help="the move's record (her projects folder)")
    parser.add_argument("--after-move", action="store_true", help=argparse.SUPPRESS)  # the move's end: its heading
    args = parser.parse_args(argv)
    if args.after_move and (args.documents or args.revoke):
        parser.error("--after-move is the projects share at the move's end")
    if args.documents and args.folder is not None:
        parser.error("--documents takes no folder: it is always your ~/Estia-KB")
    if os.geteuid() == 0:
        print(f"run it as yourself, without sudo: {DOCUMENTS_SCRIPT if args.documents else SCRIPT}", file=sys.stderr)
        return 2
    home = Path(os.environ.get("HOME") or pwd.getpwuid(os.getuid()).pw_dir)
    if args.documents:
        return _documents_main(home, args.marker, revoke=args.revoke, dry=args.dry_run)
    root = Path(os.path.abspath(args.folder.expanduser())) if args.folder else default_root(home, args.marker)
    if root is None:
        if projects_off(home, args.marker):  # no tool would read a shared folder: none is suggested
            print("Projects are off (no [projects] in the config): nothing to share.")
        else:
            print(
                f"No projects folder on record for her: with [projects] in her config, name its folder: {SCRIPT} <folder>."
            )
        return 0
    if args.after_move:  # the heading only over a folder to share
        print(AFTER_MOVE_DRY_RUN if args.dry_run else AFTER_MOVE)
    if not args.dry_run and lookup_uid(ROLE_USER) is None:
        print(f"✗ her user {ROLE_USER} does not exist yet: the move into her own user makes it. Nothing changed.")
        return 1
    if args.revoke:  # your documents folder may still need your home open
        return revoke_projects(root, home, dry=args.dry_run, keep=home / DOCUMENTS)
    return share_projects(root, home, dry=args.dry_run, ask=terminal_ask())


def _documents_main(home: Path, marker: Path | None, *, revoke: bool, dry: bool) -> int:
    folder = home / DOCUMENTS
    if not dry and lookup_uid(ROLE_USER) is None:
        print(f"✗ her user {ROLE_USER} does not exist yet: the move into her own user makes it. Nothing changed.")
        return 1
    if revoke:  # your projects folder may still need your home open
        return revoke_documents(folder, home, dry=dry, keep=default_root(home, marker))
    return share_documents(folder, home, dry=dry, ask=terminal_ask())


if __name__ == "__main__":
    raise SystemExit(main())
