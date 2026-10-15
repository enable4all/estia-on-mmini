"""Documents: the PDFs in the owner's ~/Estia-KB, and the ones the user sends on Signal ([documents]).

Her access to the folder is one set of ACL entries the owner gives without admin rights (`share.share_documents`,
`scripts/share-documents.sh`). The share writes a random token into `.estia-share` in the folder; the revoke deletes
it, so a new share has a new token and her index starts fresh. A folder she cannot list (revoked, moved) takes her
index with it at once.

The look (every `look_minutes`, and whenever the user asks about documents or types /docs) lists names, sizes and change
times only, never following a link; a PDF is read only when it is new or changed. The reading is tools/documents'
helper (Apple's PDFKit, and Vision for a page without text, on the Mac), run with fixed arguments, a clean environment
and a time limit, one document at a time; it prints one JSON line. Each page's text goes into her database's full-text
index (`store`, schema v10). Gemma is not used in any of this: it only answers from the index when the user asks.

The look never holds the folder for long: it takes the reading lock one document at a time, so a sent PDF is saved
between two; the hourly look reads `LOOK_SECONDS` at most and stops when a call begins, a look on request its 45 s,
and a look while another runs reads nothing (the answer comes from the index as it is). A share's first look announces
nothing, however many looks it takes (`FIRST_LOOK_KEY`, cleared only when one has read the whole folder).

A PDF the user sends is saved into the folder itself (never into a folder of hers: in a folder her account made, she
could rename or delete any file) under a clean name with the date, never over another file, and indexed at once; the
files she adds are the owner's to open, move or delete (the share's inherited entry). Code's note names new PDFs found
in the folder (`announcement`); a PDF she saved herself is never announced.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import os
import re
import stat
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.config import OWNER_HOME, is_estia_kb
from local_assistant.part_status import PartStatus
from local_assistant.store import DocFile, DocPage, Store

log = logging.getLogger(__name__)

SHARE_TOKEN = ".estia-share"  # the share's token file in the documents folder (the owner's file, readable by her)
PART = "documents"
MAX_BYTES = 25_000_000  # per document (owner): a larger one is never given to the helper
MAX_PAGES = 300  # per document (owner): the helper refuses more
MAX_SECONDS = 60  # per document (owner): the helper stops itself; the bridge stops it at [documents]' timeout
# the warm-up at the bridge's start: macOS prepares Vision's model on a new build's first scan (50-70 s, measured in
# tools/documents/README.md), so without it the first scanned document after a deploy would time out
WARM_SECONDS = 180
WARM_TIMEOUT = 200.0
PAGE_CHARS = 20_000  # a page's text kept at most (a full page of small print is about 5,000 characters)
READ_CHARS = 6_000  # read_document gives at most this much of a page
SEARCH_HITS = 6  # passages search_documents gives
LOOK_ON_REQUEST_SECONDS = 45.0  # a question's look reads new files this long at most; the rest at the next look
LOOK_SECONDS = 600.0  # the hourly look reads this long at most; the rest at the next tick, a minute later
SAVE_WAIT_SECONDS = 45.0  # a sent PDF waits this long at most for the document being read; else read at the next look
OUTPUT_LIMIT = 32 * 1024 * 1024  # the helper's answer (300 pages of text fit many times over)
PROBE_TIMEOUT = 10.0  # the helper run with no arguments, after a document that got no answer: does it answer at all?
MAX_FILES = 2_000  # PDFs one look lists; more are left out (logged)
MAX_DEPTH = 3  # folders of yours below the documents folder a look goes into
LISTED = 30  # documents /docs shows
ANNOUNCED_NAMES = 5  # names in the note; more are counted
NAME_CHARS = 80  # a saved file's name from its sent name, at most (before the date and .pdf)
SHARE_KEY = "documents:share"  # the share token her index belongs to
LOOKED_KEY = "documents:looked_at"  # when the folder was last looked at (epoch seconds); a look cut short leaves it
FIRST_LOOK_KEY = "documents:first_look"  # "1" until a new share's first look has read the whole folder
# why the folder cannot be listed: these mean closed (her index goes); any other error passes (the index stays)
CLOSED_ERRORS = frozenset({errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENOTDIR, errno.ELOOP})
PDF_START = b"%PDF-"
HELPER_ERRORS = frozenset({"not_pdf", "encrypted", "too_big", "too_many_pages", "timeout", "unreadable"})
_WHY = re.compile(r"[a-z_]{1,30}")
_TOKEN = re.compile(r"[0-9a-f]{16,64}")


def shown_folder(folder: Path) -> str:
    """The documents folder as code's list of what she can do names it: from the owner's home as "~/…" (the docs'
    words, and her own home is never the owner's), else written out."""
    text = str(folder)
    home = OWNER_HOME.match(text)
    return "~" + text[home.end() :] if home else text


class HelperError(Exception):
    """The helper could not read a document: one of its codes (HELPER_ERRORS), or "helper_missing", "helper_failed"."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DocumentError(Exception):
    """A sent PDF was not saved: "not_pdf", "too_big", "not_shared" (the folder cannot be written), "refused" (macOS
    itself refuses it: EPERM) or "failed"."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Reading:
    pages: tuple[DocPage, ...]
    skipped: tuple[tuple[int, str], ...]
    page_count: int


@dataclass(frozen=True)
class Listed:
    path: str  # in the folder, with "/" ("2026/lease.pdf")
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Look:
    """What one look did: the files new since the last look (never on a share's first look: `fresh`), how many were
    read, removed and left for the next look (`cut`: its time ran out or a call began), and why the folder could not
    be looked at ("not_shared", "refused" (macOS's own EPERM), "" when it could). `skipped`: nothing read or changed
    (another look was running, or the folder could not be listed just now): the index stays as it is."""

    found: tuple[DocFile, ...] = ()
    read: int = 0
    removed: int = 0
    left: int = 0
    problem: str = ""
    fresh: bool = False
    cut: bool = False
    skipped: bool = False


def not_estia_kb(folder: Path) -> bool:
    """A folder in an owner's home that is not their ~/Estia-KB, the only folder the share opens to her (live, 15 Oct:
    her config still named the first share's folder in Documents): her config is the fix, not the share. A folder
    outside a home (a test's, a checkout's) keeps the share's words."""
    return OWNER_HOME.match(str(folder)) is not None and not is_estia_kb(folder)


def helper_env() -> dict[str, str]:
    """A clean environment: no API keys, proxy settings or other secrets of the bridge's. HOME is also Foundation's
    home (CFFIXED_USER_HOME): her account's record keeps /var/empty (AGENTS.md, Gotchas), and with that home Vision's
    text recognition fails at once (live, 15 Oct: scanned pages and the warm-up failed; text pages still read)."""
    env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG") if key in os.environ}
    home = {"CFFIXED_USER_HOME": env["HOME"]} if "HOME" in env else {}
    return env | home | {"PATH": "/usr/bin:/bin"}


class Helper:
    """tools/documents' helper, run per document: `read <absolute path> --max-pages 300 --max-seconds 60`."""

    def __init__(self, command: Sequence[str], timeout: float, *, status: PartStatus | None = None) -> None:
        self._command = tuple(command)
        self._timeout = timeout
        self._status = status or PartStatus(None)

    async def warm(self) -> bool:
        """`warm --max-seconds 180`: Vision's model prepared before a document needs it. Whether it worked."""
        started = time.monotonic()
        try:
            data = await self._run(["warm", "--max-seconds", str(WARM_SECONDS)], limit=WARM_TIMEOUT)
        except HelperError as exc:
            log.warning("documents helper: warm-up failed: %s", exc.code)
            return False
        ok = data.get("ok") is True
        log.info(
            "documents helper: warm-up %s in %d ms", "done" if ok else "failed", (time.monotonic() - started) * 1000
        )
        return ok

    async def read(self, path: Path) -> Reading:
        """The document's pages; HelperError otherwise. A run that gives no answer (PDFKit crashed on the file, or its
        text is over OUTPUT_LIMIT) makes the file "unreadable" while the helper itself still answers (`_answers`):
        one bad PDF never stops the look. When the helper answers nothing at all, it is broken: "helper_failed"."""
        started = time.monotonic()
        argv = ["read", str(path), "--max-pages", str(MAX_PAGES), "--max-seconds", str(MAX_SECONDS)]
        try:
            data = await self._run(argv)
        except HelperError as exc:
            if exc.code != "helper_failed" or not await self._answers():
                raise
            log.info("documents helper: no answer for this document: unreadable")
            self._status.ok(PART)  # the helper works; the document does not
            raise HelperError("unreadable") from None
        if data.get("ok") is not True:
            error = data.get("error")
            code = error if isinstance(error, str) and error in HELPER_ERRORS else "unreadable"
            log.info("documents helper: %s in %d ms", code, (time.monotonic() - started) * 1000)
            self._status.ok(PART)  # the helper works; the document does not
            raise HelperError(code)
        reading = _reading(data)
        log.info(
            "documents helper: ok, %d of %d pages (%d by Vision), %d skipped in %d ms",
            len(reading.pages),
            reading.page_count,
            sum(page.ocr for page in reading.pages),
            len(reading.skipped),
            (time.monotonic() - started) * 1000,
        )
        self._status.ok(PART)
        return reading

    async def _answers(self) -> bool:
        """Whether the helper starts and prints its one line: run with no arguments, it answers "invalid" at once,
        reading no file and using neither PDFKit nor Vision."""
        try:
            await self._run([], limit=PROBE_TIMEOUT, record=False)
        except HelperError:
            return False
        return True

    async def _run(self, args: list[str], *, limit: float | None = None, record: bool = True) -> dict[str, Any]:
        """The helper's one JSON line (the bytes after the last line feed: a page may hold U+2028, U+2029 or U+0085,
        which JSON leaves as they are and Python's splitlines() would split at). HelperError "helper_missing" (it does
        not start), "timeout", or "helper_failed" (no answer: a crash, an empty or an oversized output); `record`:
        whether that failure is the part's state."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=helper_env(),
            )
        except OSError:  # not built yet (tools/documents)
            self._status.failed(PART, "helper_missing")
            raise HelperError("helper_missing") from None
        try:
            out = await asyncio.wait_for(_bounded(proc), limit or self._timeout)
        except TimeoutError:
            await _stop(proc)
            raise HelperError("timeout") from None
        except BaseException:  # an outer limit or a shutdown: no helper is left behind
            await asyncio.shield(_stop(proc))
            raise
        try:
            line = out.strip(b" \t\r\n").rsplit(b"\n", 1)[-1] if out is not None else b""
            data = json.loads(line.decode("utf-8", "replace")) if line else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            if record:
                self._status.failed(PART, "helper_failed")
            raise HelperError("helper_failed")
        return data


async def _bounded(proc: asyncio.subprocess.Process) -> bytes | None:
    """The helper's output, read to its end; None (and the helper stopped) past OUTPUT_LIMIT."""
    assert proc.stdout is not None
    chunks: list[bytes] = []
    size = 0
    while chunk := await proc.stdout.read(65_536):
        size += len(chunk)
        if size > OUTPUT_LIMIT:
            await _stop(proc)
            return None
        chunks.append(chunk)
    await proc.wait()
    return b"".join(chunks)


async def _stop(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


def _number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _reading(data: dict[str, Any]) -> Reading:
    """The helper's answer, checked: odd entries left out, each page's text capped, a reason only in its own words."""
    pages: list[DocPage] = []
    seen: set[int] = set()
    for item in data.get("pages") or []:
        number = _number(item.get("n")) if isinstance(item, dict) else None
        text = item.get("text") if isinstance(item, dict) else None
        if number is None or number in seen or not isinstance(text, str):
            continue
        seen.add(number)
        pages.append(DocPage(number, text[:PAGE_CHARS], item.get("ocr") is True))
    skipped: list[tuple[int, str]] = []
    for item in data.get("skipped") or []:
        number = _number(item.get("n")) if isinstance(item, dict) else None
        if number is not None:
            why = item.get("why")
            skipped.append((number, why if isinstance(why, str) and _WHY.fullmatch(why) else "unreadable"))
    count = _number(data.get("page_count")) or max([page.n for page in pages], default=0)
    return Reading(tuple(sorted(pages, key=lambda page: page.n)), tuple(sorted(skipped)), count)


def clean_name(sent: str, day: date) -> str:
    """The name a sent PDF is saved under: its own name's letters, digits, spaces and a few signs (no path, no
    hidden name, nothing a data link or a shell would read), at most NAME_CHARS, after the day it came."""
    base = unicodedata.normalize("NFC", sent).replace("\\", "/").rsplit("/", 1)[-1]
    stem = base[:-4] if base.lower().endswith(".pdf") else base
    stem = re.sub(r"[^\w .()\-]+|_", " ", stem)
    stem = " ".join(stem.split()).strip(" .-()")[:NAME_CHARS].strip(" .-")
    return f"{day.isoformat()} {stem or 'document'}.pdf"


class Documents:
    """The documents folder and her index of it: the look, the reading, saving a sent PDF, and code's texts."""

    def __init__(
        self,
        store: Store,
        folder: Path,
        helper: Helper,
        *,
        status: PartStatus | None = None,
        tz: ZoneInfo,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self.folder = folder
        self._helper = helper
        self._status = status or PartStatus(None)
        self._tz = tz
        self._clock = clock
        self._lock = asyncio.Lock()  # one document read at a time (taken per document: a save goes between two)
        self._looking = False  # a look is running: another one reads nothing
        self._closed: tuple[str, str] = ("", "")  # why the folder could not be listed last time: logged on a change
        self._retried: set[str] = set()  # documents read since this start (a timed-out one is read again once a start)

    async def warm(self) -> bool:
        """The helper's warm-up (at the bridge's start), one document at a time like any reading."""
        async with self._lock:
            return await self._helper.warm()

    # the look

    async def look(self, *, budget: float | None = None, stop: Callable[[], bool] | None = None) -> Look:
        """List the folder and read what is new or changed, one document at a time (a sent PDF is saved between two);
        `budget`: seconds the reading may take, `stop`: asked before each document (a call began); the rest is read
        at the next look. A look while another runs reads nothing: the answer comes from the index as it is."""
        if self._looking:
            log.info("documents: a look is running: this one reads nothing")
            return Look(skipped=True)
        self._looking = True
        try:
            return await self._look(budget, stop)
        finally:
            self._looking = False

    async def _look(self, budget: float | None, stop: Callable[[], bool] | None) -> Look:
        listed, problem, why = self._list()
        if problem or why:
            return self._cannot_list(problem, why)
        if self._closed != ("", ""):
            log.info("documents: the folder can be listed again")
            self._closed = ("", "")
        self._new_share()
        share = self._store.get_state(SHARE_KEY)
        fresh = self._first_look()
        known = {file.path: file for file in self._store.doc_files()}
        gone = [file.id for path, file in known.items() if path not in listed]
        self._store.remove_doc_files(gone)
        todo = [
            item
            for path, item in listed.items()
            if path not in known
            or (known[path].size, known[path].mtime_ns) != (item.size, item.mtime_ns)
            or known[path].error == "no_access"  # the share was run again since: tried every look
            or (known[path].error == "timeout" and path not in self._retried)  # once a start: after the warm-up
        ]
        todo.sort(key=lambda item: item.mtime_ns, reverse=True)  # the newest first, should the time run out
        started = time.monotonic()
        read = 0
        new: list[DocFile] = []
        for index, item in enumerate(todo):
            left = Look(tuple(new), read, len(gone), len(todo) - index, "", fresh, cut=True)
            spent = time.monotonic() - started
            if (stop is not None and stop()) or (budget is not None and spent >= budget):
                return self._looked(left)
            try:  # one document at a time: a sent PDF's save, or the warm-up, may hold the lock now
                await asyncio.wait_for(self._lock.acquire(), None if budget is None else budget - spent)
            except TimeoutError:
                return self._looked(left)
            try:
                self._retried.add(item.path)  # read in this run: a timeout is not tried again until the next start
                announced = self._first_look()  # a share begun since (a save saw it) is a first look too
                file = await self._index(item, announced=announced)
            except HelperError:  # the helper is missing or broken: nothing more can be read now
                return self._looked(Look(tuple(new), read, len(gone), len(todo) - index, "", fresh))
            finally:
                self._lock.release()
            read += 1
            if item.path not in known and not announced:
                new.append(file)
        self._status.ok(PART)
        if fresh and self._store.get_state(SHARE_KEY) == share:
            self._store.delete_state(FIRST_LOOK_KEY)  # the whole folder read: from now on a new PDF is new
        return self._looked(Look(tuple(new), read, len(gone), 0, "", fresh))

    def _looked(self, look: Look) -> Look:
        """A look done: its time kept, so the next hourly look is an hour away, unless it was cut short (the next
        tick, a minute later, goes on reading)."""
        if not look.cut:
            self._store.set_state(LOOKED_KEY, f"{self._clock():.0f}")
        log.info(
            "documents: looked: %d read, %d new, %d removed, %d left for the next look%s",
            look.read,
            len(look.found),
            look.removed,
            look.left,
            " (a fresh index)" if look.fresh else "",
        )
        return look

    def _cannot_list(self, problem: str, why: str) -> Look:
        """The folder could not be listed: closed (`problem`), her index goes; a passing error (`why` only: EMFILE,
        EIO), the index stays. Either waits for the next hourly look; logged when it changes, not every look."""
        if self._closed != (problem, why):
            log.warning("documents: the folder cannot be listed (%s)%s", why, "" if problem else ": the index stays")
            self._closed = (problem, why)
        if not problem:
            self._store.set_state(LOOKED_KEY, f"{self._clock():.0f}")
            return Look(skipped=True)
        if self._store.doc_files():
            dropped = self._store.clear_documents()
            log.warning("documents: her index of %d file(s) goes with the folder", dropped)
        if self._store.get_state(SHARE_KEY) != "":
            self._store.set_state(SHARE_KEY, "")
        self._status.failed(PART, "os_refused" if problem == "refused" else "not_shared")
        self._store.set_state(LOOKED_KEY, f"{self._clock():.0f}")
        return Look(problem=problem)

    def _list(self) -> tuple[dict[str, Listed], str, str]:
        """The folder's PDFs (name, size, change time; never through a link, never a hidden name), why it cannot be
        listed ("not_shared": missing, not a folder, a link, or closed to her; "refused": macOS itself refuses, EPERM,
        its privacy protection of Documents, Desktop and Downloads) and the error's code name ("" when listed). An error
        that passes (EMFILE, EIO) has a code name and no problem. One folder open at a time."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            top = os.open(self.folder, flags)
        except OSError as exc:
            return {}, _closed(exc), _why(exc)
        found: dict[str, Listed] = {}
        skipped = 0
        folders: list[tuple[Path, int]] = [(Path(), 0)]
        while folders:
            here, depth = folders.pop()
            try:
                fd = top if here == Path() else os.open(self.folder / here, flags)
            except OSError:  # gone, swapped for a link or closed since it was listed: left out
                continue
            try:
                with os.scandir(fd) as entries:
                    for entry in entries:
                        if entry.name.startswith("."):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if depth < MAX_DEPTH:
                                    folders.append((here / entry.name, depth + 1))
                                continue
                            if not entry.name.lower().endswith(".pdf") or not entry.is_file(follow_symlinks=False):
                                continue
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if len(found) >= MAX_FILES:
                            skipped += 1
                            continue
                        path = (here / entry.name).as_posix()
                        found[path] = Listed(path, info.st_size, info.st_mtime_ns)
            except OSError as exc:
                if here == Path():
                    return {}, _closed(exc), _why(exc)
            finally:
                os.close(fd)
        if skipped:
            log.warning("documents: %d PDF(s) over the %d a look reads were left out", skipped, MAX_FILES)
        return found, "", ""

    def _new_share(self) -> bool:
        """Whether the folder's share token differs from the one her index belongs to: then the index starts fresh
        (a revoke deleted the token, a new share wrote another), and its first look stays one until it has read the
        whole folder (`FIRST_LOOK_KEY`): nothing it finds is announced, however many looks that takes."""
        token = self._token()
        if self._store.get_state(SHARE_KEY) == token:
            return False
        dropped = self._store.clear_documents()
        self._store.set_state(SHARE_KEY, token)
        self._store.set_state(FIRST_LOOK_KEY, "1")
        log.info("documents: a new share: the index starts fresh (%d file(s) dropped)", dropped)
        return True

    def _first_look(self) -> bool:
        return self._store.get_state(FIRST_LOOK_KEY) == "1"

    def _token(self) -> str:
        """The share's token (hex), "" without one: read without following a link, a few bytes only."""
        try:
            fd = os.open(self.folder / SHARE_TOKEN, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return ""
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return ""
            text = os.read(fd, 100).decode("ascii", "replace").strip()
        finally:
            os.close(fd)
        return text if _TOKEN.fullmatch(text) else ""

    async def _index(self, item: Listed, *, announced: bool, source: str = "folder") -> DocFile:
        """Read one file into the index (a row with why when it cannot be read); HelperError when the helper itself
        is missing or broken (no row: the next look tries again)."""
        path = self.folder / item.path
        reading: Reading | None = None
        error = ""
        if item.size > MAX_BYTES:
            error = "too_big"
        elif not _openable(path):
            error = "no_access"
        else:
            try:
                reading = await self._helper.read(path)
            except HelperError as exc:
                if exc.code in ("helper_missing", "helper_failed"):
                    raise
                error = exc.code
        file_id = self._store.put_doc_file(
            item.path,
            item.size,
            item.mtime_ns,
            list(reading.pages) if reading else [],
            page_count=reading.page_count if reading else 0,
            skipped=reading.skipped if reading else (),
            error=error,
            source="signal" if source == "signal" else "folder",
            announced=announced,
        )
        found = self._store.doc_file(file_id)
        assert found is not None
        return found

    # a PDF the user sent

    async def save(self, data: bytes, sent_name: str, when: datetime) -> DocFile:
        """Save a PDF the allowed user sent into the folder itself under a clean name with the date (never over
        another file, never through a link, never into a folder: one her account made would be hers, and in it she
        could rename or delete any file), then read it into the index; DocumentError when it is not saved."""
        if len(data) > MAX_BYTES:
            raise DocumentError("too_big")
        if not data.startswith(PDF_START):
            raise DocumentError("not_pdf")
        name = clean_name(sent_name, when.astimezone(self._tz).date())
        self._new_share()  # the index belongs to this share before a file joins it (a first look stays one)
        item = self._write(name, data)
        file = await self._index_saved(item)
        log.info("documents: a sent PDF saved, %d bytes, %d pages", len(data), file.pages)
        return file

    async def _index_saved(self, item: Listed) -> DocFile:
        """Read a saved PDF into the index, once the document being read (or the warm-up) is done, within
        SAVE_WAIT_SECONDS; else, or without the helper, saved but read at the next look (never announced)."""
        try:
            await asyncio.wait_for(self._lock.acquire(), SAVE_WAIT_SECONDS)
        except TimeoutError:
            return self._read_later(item)
        try:
            return await self._index(item, announced=True, source="signal")
        except HelperError:
            return self._read_later(item)
        finally:
            self._lock.release()

    def _read_later(self, item: Listed) -> DocFile:
        file_id = self._store.put_doc_file(item.path, item.size, -1, [], page_count=0, source="signal", announced=True)
        found = self._store.doc_file(file_id)
        assert found is not None
        return found

    def _write(self, name: str, data: bytes) -> Listed:
        try:
            folder = os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            log.warning("documents: the folder cannot be opened to save a PDF (%s)", _why(exc))
            raise DocumentError(_not_saved(exc)) from None
        try:
            stem = name[:-4]
            for number in range(1, 100):
                candidate = name if number == 1 else f"{stem} ({number}).pdf"
                try:
                    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
                except FileExistsError:  # a file or a link of that name: never written over or through
                    continue
                except OSError as exc:
                    log.warning("documents: a PDF cannot be written (%s)", _why(exc))
                    raise DocumentError(_not_saved(exc)) from None
                try:
                    _write_all(fd, data)
                    info = os.fstat(fd)
                except OSError as exc:  # the disk is full, or it failed: no half PDF is left behind
                    log.warning("documents: a PDF could not be written whole (%s): removed", _why(exc))
                    _drop(fd, candidate, folder)
                    raise DocumentError("failed") from None
                finally:
                    os.close(fd)
                return Listed(candidate, info.st_size, info.st_mtime_ns)
            raise DocumentError("failed")
        finally:
            os.close(folder)

    def read_file(self, file: DocFile) -> bytes:
        """A document's bytes for /docs send N: each folder on the way and the file opened without following a link,
        a regular file of at most MAX_BYTES that starts as a PDF; DocumentError ("gone", "too_big", "not_pdf")."""
        flags = os.O_RDONLY | os.O_NOFOLLOW
        parts = Path(file.path).parts
        try:
            here = os.open(self.folder, flags | os.O_DIRECTORY)
        except OSError:
            raise DocumentError("gone") from None
        try:
            for part in parts[:-1]:
                inner = os.open(part, flags | os.O_DIRECTORY, dir_fd=here)
                os.close(here)
                here = inner
            fd = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=here)
        except OSError:
            raise DocumentError("gone") from None
        finally:
            os.close(here)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise DocumentError("gone")
            if info.st_size > MAX_BYTES:
                raise DocumentError("too_big")
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise DocumentError("too_big")
        if not data.startswith(PDF_START):
            raise DocumentError("not_pdf")
        return data

    # what the tools and /docs read

    def files(self) -> list[DocFile]:
        return self._store.doc_files()

    def find(self, document: str) -> DocFile | None:
        """A document by its number (#12), its path or name in the folder, or words of its name; None when no single
        one matches."""
        asked = " ".join(document.strip().strip("\"'").split())
        files = self.files()
        number = asked.lstrip("#")
        if number.isdecimal():
            return next((file for file in files if file.id == int(number)), None)
        folded = asked.casefold()
        for match in (
            lambda file: file.path.casefold() == folded,
            lambda file: Path(file.path).name.casefold() == folded,
            lambda file: Path(file.path).stem.casefold() == folded,
        ):
            found = [file for file in files if match(file)]
            if len(found) == 1:
                return found[0]
        words = [word for word in re.findall(r"\w+", folded) if word != "pdf"]
        if not words:
            return None
        found = [file for file in files if all(word in _words(file.path) for word in words)]
        return found[0] if len(found) == 1 else None

    def listing(self) -> str:
        """/docs: every document, newest first, with its number, pages or why it cannot be read."""
        files = self.files()
        if not files:
            return prompts.DOCS_NONE
        lines = [prompts.DOCS_HEADER]
        for file in files[:LISTED]:
            if file.error:
                about = prompts.DOCS_CANNOT.format(why=prompts.DOCUMENT_ERRORS.get(file.error, file.error))
            else:
                about = f"{file.pages} page{'' if file.pages == 1 else 's'}"
            lines.append(f"#{file.id} {file.path} · {about}")
        if len(files) > LISTED:
            lines.append(prompts.DOCS_MORE.format(count=len(files) - LISTED))
        lines.append(prompts.DOCS_SEND_HINT)
        return "\n".join(lines)

    def search_text(self, words: str) -> str:
        """search_documents' result: the best passages with their document and page (outside text)."""
        if not words.strip():
            raise ValueError("the search words are empty.")
        hits = self._store.search_doc_pages(words, limit=SEARCH_HITS)
        if not hits:
            count = len(self.files())
            return prompts.DOCUMENTS_NO_MATCH.format(words=" ".join(words.split())[:80], count=count)
        blocks = [prompts.DOCUMENTS_PASSAGES]
        for number, hit in enumerate(hits, 1):
            blocks.append(f'[{number}] "{hit.path}", page {hit.page}: {" ".join(hit.passage.split())}')
        return "\n".join(blocks)

    def read_text(self, document: str, page: int) -> str:
        """read_document's result: one page, capped (outside text)."""
        file = self.find(document)
        if file is None:
            names = ", ".join(f'"{Path(item.path).name}"' for item in self.files()[:8]) or "none yet"
            return prompts.DOCUMENT_NOT_FOUND.format(document=document.strip()[:80], names=names)
        if file.error:
            return prompts.DOCUMENT_UNREADABLE.format(
                name=file.path, why=prompts.DOCUMENT_ERRORS.get(file.error, file.error)
            )
        found = self._store.doc_page(file.id, page)
        if found is None:
            why = dict(file.skipped).get(page)
            if why is not None:
                return prompts.DOCUMENT_PAGE_SKIPPED.format(page=page, name=file.path, why=why)
            count = f"{file.pages} page{'' if file.pages == 1 else 's'}"
            return prompts.DOCUMENT_NO_PAGE.format(page=page, name=file.path, count=count)
        text = found.text[:READ_CHARS]
        cut = prompts.DOCUMENT_CUT if len(found.text) > READ_CHARS else ""
        how = prompts.DOCUMENT_BY_VISION if found.ocr else ""
        return prompts.DOCUMENT_PAGE.format(name=file.path, page=page, pages=file.pages, how=how, text=text) + cut

    # code's note about new documents

    def announcement(self) -> tuple[str, list[int]]:
        """The note naming the PDFs found in the folder since the last note ("" when none), and their numbers."""
        files = sorted(self._store.unannounced_doc_files(), key=lambda file: file.path.casefold())
        if not files:
            return "", []
        names = [Path(file.path).name for file in files]
        shown = ", ".join(names[:ANNOUNCED_NAMES])
        if len(names) > ANNOUNCED_NAMES:
            shown += f" and {len(names) - ANNOUNCED_NAMES} more"
        plural = "document" if len(files) == 1 else "documents"
        return prompts.DOCUMENTS_FOUND.format(count=len(files), documents=plural, names=shown), [f.id for f in files]

    def mark_announced(self, file_ids: list[int]) -> None:
        self._store.mark_doc_files_announced(file_ids)

    def unannounced(self) -> list[DocFile]:
        """The PDFs found in the folder that no note named yet (nor /docs, nor an answer)."""
        return self._store.unannounced_doc_files()


def saved_note(file: DocFile) -> str:
    """Code's note after a sent PDF was saved: where, and how many pages were read (or why none)."""
    if file.error:
        return prompts.DOCUMENT_SAVED_UNREAD.format(
            path=file.path, why=prompts.DOCUMENT_ERRORS.get(file.error, file.error)
        )
    if file.mtime_ns < 0:  # the helper was not there: read at the next look
        return prompts.DOCUMENT_SAVED.format(path=file.path, about=prompts.DOCUMENT_READ_LATER)
    about = f"{file.pages} page{'' if file.pages == 1 else 's'}"
    if file.skipped:
        about += prompts.DOCUMENT_SKIPPED_PAGES.format(count=len(file.skipped))
    return prompts.DOCUMENT_SAVED.format(path=file.path, about=about)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _drop(fd: int, name: str, folder: int) -> None:
    """A half-written PDF of hers: emptied, then removed. Her account may add files to the folder, never remove one
    (that right would reach yours too): in her own user an empty file may stay, which the next look names as no PDF."""
    with contextlib.suppress(OSError):
        os.ftruncate(fd, 0)
    try:
        os.unlink(name, dir_fd=folder)
    except OSError as exc:
        log.warning("documents: the half-written PDF could not be removed (%s): it is empty", _why(exc))


def _closed(exc: OSError) -> str:
    """Why the folder cannot be listed: "refused" (macOS's own EPERM), "not_shared" (closed to her, missing, a link),
    "" for an error that passes (EMFILE, EIO, …): her index stays."""
    if exc.errno == errno.EPERM:
        return "refused"
    return "not_shared" if exc.errno in CLOSED_ERRORS else ""


def _not_saved(exc: OSError) -> str:
    """Why a sent PDF cannot be written: as `_closed`, and "failed" for an error that passes."""
    return _closed(exc) or "failed"


def _openable(path: Path) -> bool:
    """She may open the file (a moved-in file keeps its own permissions until the share runs again)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    os.close(fd)
    return True


def _why(exc: OSError) -> str:
    """An error's code name for the log (EACCES, EPERM, ELOOP), never a path."""
    return errno.errorcode.get(exc.errno or 0, "unknown")


def _words(path: str) -> set[str]:
    return set(re.findall(r"\w+", Path(path).stem.casefold().replace("_", " ")))
