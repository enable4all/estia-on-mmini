"""Health of the Mac this runs on: fixed checks without a shell or admin rights.

`check()` is the watcher the scheduler runs every few hours (it returns only problems);
`report()` is the `mini_status` tool (every check as one line, for Gemma to summarise).
Besides the built-in checks (Ollama, disk, memory, uptime, log errors, voice) the config can add
`[health] extra_checks`: a local HTTP service or a launchd service per entry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from local_assistant.config import Config, ExtraCheck
from local_assistant.store import Store
from local_assistant.watchers import Problem

log = logging.getLogger(__name__)

LAUNCHCTL = "/bin/launchctl"
SYSCTL = "/usr/sbin/sysctl"
DATA_VOLUME = "/System/Volumes/Data"
COMMAND_TIMEOUT = 8.0
HTTP_TIMEOUT = 5.0
_HEALTHY = frozenset({"ready", "ok", "healthy", "up", "running", "pass"})  # a JSON "status" that means fine
_PRESSURE = {1: "normal", 2: "warning", 4: "critical"}
_RUNS = re.compile(r"^\s*runs = (\d+)\s*$", re.MULTILINE)
_EXIT = re.compile(r"^\s*last exit code = (-?\d+)\s*$", re.MULTILINE)
_STATE = re.compile(r"^\s*state = (.+?)\s*$", re.MULTILINE)
_SWAP = re.compile(r"total = ([\d.]+)M\s+used = ([\d.]+)M")
_BOOT = re.compile(r"sec = (\d+)")

Run = Callable[[list[str]], Awaitable[str]]
HttpGet = Callable[[str], Awaitable[str]]


class HealthCheck(Protocol):
    """What the app needs: a watcher for the scheduler and a report for `mini_status`."""

    name: str

    async def check(self) -> list[Problem]: ...

    async def report(self) -> str: ...


class CommandError(Exception):
    """A fixed command exited with an error."""


async def run_command(argv: list[str]) -> str:
    """Run a fixed command (no shell) and return its output; raise on a failure or a timeout."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), COMMAND_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise CommandError(f"exit {proc.returncode}")
    return out.decode("utf-8", "replace")


async def http_get(url: str) -> str:
    """GET a local URL without proxies; raise on any failure."""

    def fetch() -> str:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=HTTP_TIMEOUT) as response:
            body: bytes = response.read(1_000_000)
        return body.decode("utf-8", "replace")

    return await asyncio.to_thread(fetch)


async def _no_lines() -> list[str]:
    return []


def data_volume_usage() -> tuple[int, int]:
    usage = shutil.disk_usage(DATA_VOLUME)
    return usage.total, usage.free


@dataclass(frozen=True)
class _Row:
    label: str
    text: str
    problem: Problem | None = None


def _reported_status(body: str) -> str | None:
    """The "status" of a JSON health answer, if the answer has one."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    status = data.get("status") if isinstance(data, dict) else None
    return status if isinstance(status, str) else None


def _failed(exc: BaseException) -> str:
    return f"check failed ({exc})" if isinstance(exc, CommandError) else f"check failed ({type(exc).__name__})"


def _number(output: object) -> int | None:
    """A sysctl's whole-number answer, or None (failed, or not a number)."""
    text = output.strip() if isinstance(output, str) else ""
    return int(text) if text.isdecimal() else None


def _problem_row(key: str, label: str, detail: str) -> _Row:
    return _Row(label, f"PROBLEM: {detail}", Problem(key, label, detail))


class Health:
    name = "mini"

    def __init__(
        self,
        store: Store,
        config: Config,
        *,
        signal_restarts: Callable[[], int] = lambda: 0,
        run: Run = run_command,
        http_get: HttpGet = http_get,
        disk: Callable[[], tuple[int, int]] = data_volume_usage,
        clock: Callable[[], float] = time.time,
        extra_lines: Callable[[], Awaitable[list[str]]] | None = None,
    ) -> None:
        self._extra_lines = extra_lines or _no_lines
        self._voice = config.voice_url
        self._host = config.host_label
        self._extra = config.health_extra_checks
        self._store = store
        self._tz = config.timezone
        self._ollama = config.ollama_base_url.rstrip("/").removesuffix("/v1")
        self._disk_min = config.disk_free_min_percent
        self._log_dirs = config.watched_log_dirs
        self._signal_restarts = signal_restarts
        self._run = run
        self._get = http_get
        self._disk = disk
        self._clock = clock

    async def check(self) -> list[Problem]:
        """The scheduled watcher: only the problems (log errors are counted for the next report)."""
        rows = await self._rows()
        return [row.problem for row in rows if row.problem is not None]

    async def report(self) -> str:
        """Every check as one line, for `mini_status`."""
        rows, extra = await asyncio.gather(self._rows(), self._safe_extra_lines())  # extra: mail, devices
        rows.append(self._log_row(reset=True))
        rows.append(_Row("Signal", f"{self._signal_restarts()} restarts since the bridge started"))
        problems = [row.problem for row in rows if row.problem is not None]
        lines = [f"Status of {self._host} at {self._when(self._clock())}"]
        lines += [f"- {row.label}: {row.text}" for row in rows]
        lines += [f"- {line}" for line in extra]
        if problems:
            lines.append(f"Problems: {len(problems)} ({', '.join(p.label for p in problems)})")
        else:
            lines.append("Problems: none")
        return "\n".join(lines)

    async def _safe_extra_lines(self) -> list[str]:
        try:
            return await self._extra_lines()
        except Exception as exc:  # one broken part must not take the whole report down
            log.warning("extra status lines failed: %s", type(exc).__name__)
            return [f"More status: check failed ({type(exc).__name__})"]

    async def _rows(self) -> list[_Row]:
        checks: list[tuple[str, str, Callable[[], Awaitable[_Row]]]] = [("ollama", "Gemma (Ollama)", self._ollama_row)]
        for extra in self._extra:
            row = self._http_row if extra.http else self._launchd_row
            checks.append((extra.key, extra.label, partial(row, extra)))
        checks += [("disk", "Disk", self._disk_row), ("memory", "Memory", self._memory_row)]
        if self._voice:
            checks.append(("voice", "Voice", self._voice_row))
        rows = list(
            await asyncio.gather(*(self._safe(key, label, fn) for key, label, fn in checks), self._uptime_row())
        )
        self._log_row(reset=False)  # keep counting between reports
        return rows

    async def _safe(self, key: str, label: str, fn: Callable[[], Awaitable[_Row]]) -> _Row:
        try:
            return await fn()
        except Exception as exc:
            log.warning("health check %s failed: %s", key, type(exc).__name__)
            return _problem_row(key, label, _failed(exc))

    async def _ollama_row(self) -> _Row:
        version_body, ps_body = await asyncio.gather(
            self._get(f"{self._ollama}/api/version"), self._get(f"{self._ollama}/api/ps"), return_exceptions=True
        )
        if isinstance(version_body, OSError):
            return _problem_row("ollama", "Gemma (Ollama)", "not answering")
        if isinstance(version_body, BaseException):
            raise version_body
        version = json.loads(version_body)["version"]
        try:
            if isinstance(ps_body, BaseException):
                raise ps_body
            models = json.loads(ps_body).get("models", [])
            names = [str(m.get("name", "?")).removesuffix(":latest") for m in models]
            loaded = f"loaded: {', '.join(names)}" if names else "no model loaded (loads on first use)"
        except (OSError, ValueError, AttributeError):
            loaded = "loaded: unknown"
        return _Row(f"Gemma (Ollama {version})", f"ok, {loaded}")

    async def _http_row(self, check: ExtraCheck) -> _Row:
        """A local service: it must answer, and a JSON "status" it reports must say it is ready."""
        try:
            body = await self._get(check.http)
        except OSError:
            return _problem_row(check.key, check.label, "not answering")
        status = _reported_status(body)
        if status is not None and status.casefold() not in _HEALTHY:
            return _problem_row(check.key, check.label, f"status {status}")
        return _Row(check.label, "ok")

    async def _voice_row(self) -> _Row:
        try:
            body = await self._get(f"{self._voice}/health")
        except OSError:
            return _problem_row("voice", "Voice", "not answering")
        state = json.loads(body)
        stt, tts = state.get("stt"), state.get("tts")
        failed = [part for part, value in (("listening", stt), ("speaking", tts)) if value == "failed"]
        if failed:
            return _problem_row("voice", "Voice", f"{' and '.join(failed)} failed to load")
        if stt == tts == "ready":
            return _Row("Voice", "ok (listening and speaking)")
        return _Row("Voice", "loading its models")

    async def _launchd_row(self, check: ExtraCheck) -> _Row:
        """A launchd service: it must exist, its last run must have succeeded and, with a limit, keep running."""
        output = await self._run([LAUNCHCTL, "print", check.launchd])
        if check.stuck_after_seconds:
            stuck_at = self._stuck_runs(check, output)
            if stuck_at is not None:
                return _problem_row(check.key, check.label, f"not running (runs stuck at {stuck_at})")
        exit_match = _EXIT.search(output)
        if exit_match is None:
            state = _STATE.search(output)
            return _Row(check.label, "ok (running)" if state and state.group(1) == "running" else "ok (not run yet)")
        code = int(exit_match.group(1))
        if code != 0:
            return _problem_row(check.key, check.label, f"last run failed (exit {code})")
        return _Row(check.label, "ok (last exit 0)")

    def _stuck_runs(self, check: ExtraCheck, output: str) -> int | None:
        """The runs counter when it has not moved for the check's limit, else None."""
        runs_match = _RUNS.search(output)
        if runs_match is None:
            raise ValueError("no runs counter")
        runs, now = int(runs_match.group(1)), self._clock()
        # When the counter last changed: the same answer whoever looks (watcher or mini_status) and how often.
        state_key = f"health:launchd:{check.key}"
        ref = self._load(state_key)
        if not ref or runs != int(ref.get("runs", -1)) or "changed_at" not in ref:  # also a reset after a reboot
            self._save(state_key, {"runs": runs, "changed_at": now})
        elif now - float(ref["changed_at"]) >= check.stuck_after_seconds:
            return runs
        return None

    async def _disk_row(self) -> _Row:
        total, free = self._disk()
        percent = free / total * 100
        if percent < self._disk_min:
            return _problem_row("disk", "Disk", f"{round(percent)}% free")
        return _Row("Disk", f"{round(percent)}% free ({free / 1e9:.0f} of {total / 1e9:.0f} GB)")

    async def _memory_row(self) -> _Row:
        """The Mac's memory, its pressure and free share, and swap said for what it is (owner, 16 Oct: "swap 2.9 of
        4.0 GB used" came back as "2.9 of 4 GB RAM"). A value sysctl cannot give is left out, never guessed."""
        pressure_out, swap_out, size_out, free_out = await asyncio.gather(
            self._run([SYSCTL, "-n", "kern.memorystatus_vm_pressure_level"]),
            self._run([SYSCTL, "-n", "vm.swapusage"]),
            self._run([SYSCTL, "-n", "hw.memsize"]),
            self._run([SYSCTL, "-n", "kern.memorystatus_level"]),  # the free share, as memory_pressure reports it
            return_exceptions=True,
        )
        if isinstance(pressure_out, BaseException):
            raise pressure_out
        level = int(pressure_out.strip())
        pressure = _PRESSURE.get(level, f"level {level}")
        if level == 4:
            return _problem_row("memory", "Memory", "pressure critical")
        try:
            match = _SWAP.search(swap_out) if isinstance(swap_out, str) else None
            swap = (
                f"swap (disk space used as memory) {float(match.group(2)) / 1024:.1f} of "
                f"{float(match.group(1)) / 1024:.1f} GB used"
                if match
                else "swap unknown"
            )
        except Exception:
            swap = "swap unknown"
        size = _number(size_out)
        free = _number(free_out)
        memory = f"{size / 1024**3:.0f} GB, " if size else ""  # Apple counts memory in GB of 1024^3: 32 GB
        share = f" ({free}% free)" if free is not None and 0 <= free <= 100 else ""
        return _Row("Memory", f"{memory}pressure {pressure}{share}, {swap}")

    async def _uptime_row(self) -> _Row:
        try:
            match = _BOOT.search(await self._run([SYSCTL, "-n", "kern.boottime"]))
            if match is None:
                raise ValueError("no boot time")
        except Exception as exc:
            return _Row("Uptime", _failed(exc))
        booted = int(match.group(1))
        seconds = int(self._clock()) - booted
        days, rest = divmod(seconds, 86_400)
        hours, minutes = rest // 3_600, rest % 3_600 // 60
        span = f"{days} d {hours} h" if days else f"{hours} h {minutes} min"
        return _Row("Uptime", f"{span} (since {self._when(booted)})")

    def _log_row(self, *, reset: bool) -> _Row:
        """Count new ERROR lines in the watched logs since the last report (complete lines only)."""
        now = self._clock()
        state = self._load("health:logs")
        first = not state
        offsets: dict[str, list[int]] = state.get("offsets", {})
        counts: dict[str, int] = state.get("counts", {})
        since = float(state.get("since", now))
        for path in sorted(p for folder in self._log_dirs if folder.is_dir() for p in folder.glob("*.log")):
            try:
                inode, size = path.stat().st_ino, path.stat().st_size
                old_inode, offset = offsets.get(str(path), [inode, 0])
                if first:
                    offset = size  # the first look only sets the reference
                elif old_inode != inode or size < offset:
                    offset = 0  # rotated or truncated
                found, offset = _count_errors(path, offset)
            except OSError:
                continue
            offsets[str(path)] = [inode, offset]
            if found:
                counts[path.name] = counts.get(path.name, 0) + found
        total = sum(counts.values())
        if total:
            detail = ", ".join(f"{name} {n}" for name, n in sorted(counts.items()))
            text = f"{total} since {self._when(since)} ({detail})"
        else:
            text = f"none since {self._when(since)}"
        if reset:
            counts, since = {}, now
        self._save("health:logs", {"since": since, "offsets": offsets, "counts": counts})
        return _Row("Log errors", text)

    def _when(self, epoch: float) -> str:
        moment = datetime.fromtimestamp(epoch, self._tz)
        return f"{moment.day} {moment:%b %H:%M}"

    def _load(self, key: str) -> dict[str, Any]:
        raw = self._store.get_state(key)
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, key: str, value: dict[str, Any]) -> None:
        self._store.set_state(key, json.dumps(value))


def _count_errors(path: Path, offset: int) -> tuple[int, int]:
    """ERROR lines after `offset`, and the offset after the last complete line."""
    found = 0
    with path.open("rb") as handle:
        handle.seek(offset)
        for line in handle:
            if not line.endswith(b"\n"):
                break  # still being written: count it next time
            offset += len(line)
            if b"ERROR" in line:
                found += 1
    return found, offset
