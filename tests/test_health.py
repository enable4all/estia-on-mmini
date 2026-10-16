import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from local_assistant.config import Config, ExtraCheck
from local_assistant.health import LAUNCHCTL, SYSCTL, CommandError, Health
from local_assistant.store import Store
from local_assistant.watchers import Problem
from tests.conftest import ME, TZ

START = 1_791_452_400.0  # 2026-10-08 11:40 Europe/Amsterdam
OLLAMA = "http://127.0.0.1:11434"
HELPER_URL = "http://127.0.0.1:9100/health"
JOB_SERVICE = "system/com.example.backup"
JOB = (LAUNCHCTL, "print", JOB_SERVICE)
# the two kinds of extra check a user can add: a local HTTP service and a periodic launchd job
EXTRA = (
    ExtraCheck("Helper", http=HELPER_URL),
    ExtraCheck("Backup job", launchd=JOB_SERVICE, stuck_after_seconds=180),
)
HELPER = Problem("extra-helper", "Helper", "not answering")
PRESSURE = (SYSCTL, "-n", "kern.memorystatus_vm_pressure_level")
SWAP = (SYSCTL, "-n", "vm.swapusage")
MEMSIZE = (SYSCTL, "-n", "hw.memsize")
FREE = (SYSCTL, "-n", "kern.memorystatus_level")  # the free share, as memory_pressure's "memory free percentage"
BOOT = (SYSCTL, "-n", "kern.boottime")


class FakeMini:
    """Answers the health checks like a healthy Mac on 2026-10-08, until a test breaks something."""

    def __init__(self) -> None:
        self.now = START
        self.http: dict[str, str | Exception] = {
            f"{OLLAMA}/api/version": '{"version":"0.40.0"}',
            f"{OLLAMA}/api/ps": '{"models":[{"name":"local-worker:latest","size":18523690724}]}',
            HELPER_URL: '{"status": "ready", "model": "tiny-1b"}',
        }
        self.commands: dict[tuple[str, ...], str | Exception] = {
            JOB: "\tstate = not running\n\truns = 1408\n\tlast exit code = 0\n\trun interval = 60 seconds\n",
            PRESSURE: "1\n",
            SWAP: "total = 4096.00M  used = 3013.88M  free = 1082.12M  (encrypted)\n",
            MEMSIZE: "34359738368\n",
            FREE: "23\n",
            BOOT: "{ sec = 1791378038, usec = 791046 } Wed Oct  7 15:00:38 2026\n",
        }
        self.total, self.free = 500_000_000_000, 400_000_000_000
        self.restarts = 0

    def clock(self) -> float:
        return self.now

    async def run(self, argv: list[str]) -> str:
        answer = self.commands[tuple(argv)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def get(self, url: str) -> str:
        answer = self.http[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def disk(self) -> tuple[int, int]:
        return self.total, self.free

    def job_runs(self, runs: int, exit_code: int = 0) -> None:
        self.commands[JOB] = f"\truns = {runs}\n\tlast exit code = {exit_code}\n"


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "ollama.log").write_text("time=… level=INFO msg=start\n")
    return folder


def config(logs: Path, **settings: Any) -> Config:
    merged: dict[str, Any] = {"health_extra_checks": EXTRA, "watched_log_dirs": (logs,), "timezone": TZ} | settings
    return Config(bot_number="+306900000000", allowed_acis=frozenset({ME}), **merged)


def make(store: Store, mini: FakeMini, logs: Path, **settings: Any) -> Health:
    cfg = config(logs, **settings)
    return Health(
        store,
        cfg,
        signal_restarts=lambda: mini.restarts,
        run=mini.run,
        http_get=mini.get,
        disk=mini.disk,
        clock=mini.clock,
    )


async def test_all_fine_report_and_no_problems(store: Store, logs: Path) -> None:
    mini = FakeMini()
    health = make(store, mini, logs)
    assert await health.check() == []
    mini.now += 600
    mini.job_runs(1418)  # it ran every minute meanwhile
    assert await health.report() == (
        "Status of the Mac at 8 Oct 11:50\n"
        "- Gemma (Ollama 0.40.0): ok, loaded: local-worker\n"
        "- Helper: ok\n"
        "- Backup job: ok (last exit 0)\n"
        "- Disk: 80% free (400 of 500 GB)\n"
        "- Memory: 32 GB, pressure normal (23% free), swap (disk space used as memory) 2.9 of 4.0 GB used\n"
        "- Uptime: 20 h 49 min (since 7 Oct 15:00)\n"
        "- Log errors: none since 8 Oct 11:40\n"
        "- Signal: 0 restarts since the bridge started\n"
        "Problems: none"
    )


async def test_ollama_down_is_a_problem(store: Store, logs: Path) -> None:
    mini = FakeMini()
    mini.http[f"{OLLAMA}/api/version"] = ConnectionRefusedError()
    mini.http[f"{OLLAMA}/api/ps"] = ConnectionRefusedError()
    health = make(store, mini, logs)
    assert await health.check() == [Problem("ollama", "Gemma (Ollama)", "not answering")]
    report = await health.report()
    assert "- Gemma (Ollama): PROBLEM: not answering\n" in report
    assert report.endswith("Problems: 1 (Gemma (Ollama))")


async def test_no_model_loaded_is_fine(store: Store, logs: Path) -> None:
    mini = FakeMini()
    mini.http[f"{OLLAMA}/api/ps"] = '{"models":[]}'
    health = make(store, mini, logs)
    assert await health.check() == []
    assert "- Gemma (Ollama 0.40.0): ok, no model loaded (loads on first use)\n" in await health.report()


async def test_no_extra_checks_means_only_the_built_in_ones(store: Store, logs: Path) -> None:
    mini = FakeMini()
    health = make(store, mini, logs, health_extra_checks=())
    assert await health.check() == []
    report = await health.report()
    assert "Helper" not in report and "Backup job" not in report and "- Disk: 80% free" in report


async def test_an_http_check_down_or_not_ready_is_a_problem(store: Store, logs: Path) -> None:
    mini = FakeMini()
    health = make(store, mini, logs)
    mini.http[HELPER_URL] = TimeoutError()
    assert await health.check() == [HELPER]
    mini.http[HELPER_URL] = ConnectionRefusedError()
    assert await health.check() == [HELPER]
    mini.http[HELPER_URL] = '{"status": "loading"}'
    assert await health.check() == [Problem("extra-helper", "Helper", "status loading")]
    assert "- Helper: PROBLEM: status loading\n" in await health.report()


@pytest.mark.parametrize("body", ['{"status": "ok"}', '{"status": "Ready"}', "pong", "", '{"uptime": 5}', "[1, 2]"])
async def test_an_http_check_that_answers_is_fine(store: Store, logs: Path, body: str) -> None:
    mini = FakeMini()
    mini.http[HELPER_URL] = body
    health = make(store, mini, logs)
    assert await health.check() == []
    assert "- Helper: ok\n" in await health.report()


async def test_a_job_that_stops_running_is_a_problem(store: Store, logs: Path) -> None:
    mini = FakeMini()
    health = make(store, mini, logs)
    assert await health.check() == []  # first look: reference only
    mini.now += 60
    assert await health.check() == []  # too soon to compare
    mini.now += 240
    mini.job_runs(1412)
    assert await health.check() == []  # runs went up: fine
    mini.now += 300
    assert await health.check() == [Problem("extra-backup-job", "Backup job", "not running (runs stuck at 1412)")]


async def test_a_job_counter_reset_after_a_reboot_is_fine(store: Store, logs: Path) -> None:
    mini = FakeMini()
    health = make(store, mini, logs)
    await health.check()
    mini.now += 300
    mini.job_runs(3)
    assert await health.check() == []
    mini.now += 300
    mini.job_runs(8)
    assert await health.check() == []


async def test_a_failing_job_run_or_missing_service(store: Store, logs: Path) -> None:
    mini = FakeMini()
    health = make(store, mini, logs)
    mini.job_runs(1408, exit_code=1)
    assert await health.check() == [Problem("extra-backup-job", "Backup job", "last run failed (exit 1)")]
    mini.commands[JOB] = CommandError("exit 113")
    assert await health.check() == [Problem("extra-backup-job", "Backup job", "check failed (exit 113)")]


async def test_a_job_that_never_ran_is_fine(store: Store, logs: Path) -> None:
    mini = FakeMini()
    mini.commands[JOB] = "\tstate = not running\n\truns = 0\n\tlast exit code = (never exited)\n"
    health = make(store, mini, logs)
    assert await health.check() == []
    assert "- Backup job: ok (not run yet)\n" in await health.report()


async def test_a_daemon_check_without_a_stuck_limit_ignores_the_run_counter(store: Store, logs: Path) -> None:
    mini = FakeMini()
    mini.commands[JOB] = "\tstate = running\n\truns = 1\n\tlast exit code = (never exited)\n"
    daemon = ExtraCheck("Backup job", launchd=JOB_SERVICE)  # a service that keeps running: runs never changes
    health = make(store, mini, logs, health_extra_checks=(daemon,))
    assert await health.check() == []
    mini.now += 86_400
    assert await health.check() == []
    assert "- Backup job: ok (running)\n" in await health.report()
    mini.commands[JOB] = "\tstate = waiting\n\tlast exit code = 78\n"  # no counter at all: still readable
    assert await health.check() == [Problem("extra-backup-job", "Backup job", "last run failed (exit 78)")]


async def test_low_disk_is_a_problem(store: Store, logs: Path) -> None:
    mini = FakeMini()
    mini.free = 35_000_000_000
    health = make(store, mini, logs)
    assert await health.check() == [Problem("disk", "Disk", "7% free")]


async def test_the_memory_line_names_the_macs_memory_and_what_swap_is(store: Store, logs: Path) -> None:
    """Owner, 16 Oct: "swap 2.9 of 4.0 GB used" came back from Gemma as "2.9 of 4 GB RAM". The line names the Mac's
    memory (hw.memsize, in GB of 1024^3 as Apple counts it: 32 GB) and its free share, and says what swap is; a value
    sysctl cannot give is left out, never guessed."""
    mini = FakeMini()
    health = make(store, mini, logs)
    assert (
        "- Memory: 32 GB, pressure normal (23% free), swap (disk space used as memory) 2.9 of 4.0 GB used\n"
        in await health.report()
    )
    mini.commands[MEMSIZE] = TimeoutError()
    mini.commands[FREE] = "not a number\n"
    assert "- Memory: pressure normal, swap (disk space used as memory) 2.9 of 4.0 GB used\n" in await health.report()


async def test_memory_pressure(store: Store, logs: Path) -> None:
    mini = FakeMini()
    health = make(store, mini, logs)
    mini.commands[PRESSURE] = "2\n"
    assert await health.check() == []
    assert (
        "- Memory: 32 GB, pressure warning (23% free), swap (disk space used as memory) 2.9 of 4.0 GB used\n"
        in await health.report()
    )
    mini.commands[PRESSURE] = "4\n"
    assert await health.check() == [Problem("memory", "Memory", "pressure critical")]
    mini.commands[PRESSURE] = TimeoutError()
    assert await health.check() == [Problem("memory", "Memory", "check failed (TimeoutError)")]


async def test_a_broken_check_does_not_stop_the_others(store: Store, logs: Path) -> None:
    mini = FakeMini()
    mini.commands[BOOT] = "garbage"
    mini.commands[JOB] = "no counter in this output"
    health = make(store, mini, logs)
    assert await health.check() == [Problem("extra-backup-job", "Backup job", "check failed (ValueError)")]
    report = await health.report()
    assert "- Uptime: check failed (ValueError)\n" in report
    assert "- Disk: 80% free (400 of 500 GB)\n" in report


async def test_log_errors_are_counted_since_the_previous_report(store: Store, logs: Path) -> None:
    mini = FakeMini()
    health = make(store, mini, logs)
    await health.check()  # the first look sets the reference: old errors are not counted
    log = logs / "ollama.log"
    with log.open("a") as handle:
        handle.write("time=… level=ERROR msg=one\ntime=… level=INFO msg=fine\n")
    mini.now += 3600
    await health.check()  # the scheduled check keeps counting without reporting
    with log.open("a") as handle:
        handle.write("time=… level=ERROR msg=two\ntime=… level=ERROR msg=half-writ")
    (logs / "bridge.log").write_text("2026-10-08 12:41:00,000 ERROR local_assistant: boom\n")
    mini.now += 600
    assert "- Log errors: 3 since 8 Oct 11:40 (bridge.log 1, ollama.log 2)\n" in await health.report()
    with log.open("a") as handle:
        handle.write("ten\n")  # completes the half-written ERROR line
    mini.now += 60
    assert "- Log errors: 1 since 8 Oct 12:50 (ollama.log 1)\n" in await health.report()


async def test_a_rotated_log_is_read_from_the_start(store: Store, logs: Path) -> None:
    mini = FakeMini()
    health = make(store, mini, logs)
    log = logs / "ollama.log"
    log.write_text("x" * 500 + "\n")
    await health.check()
    log.write_text("level=ERROR new file\n")
    mini.now += 60
    assert "- Log errors: 1 since 8 Oct 11:40 (ollama.log 1)\n" in await health.report()


async def test_signal_restarts_and_uptime_in_days(store: Store, logs: Path) -> None:
    mini = FakeMini()
    mini.restarts = 2
    mini.now += 3 * 86_400
    health = make(store, mini, logs)
    report = await health.report()
    assert "- Signal: 2 restarts since the bridge started\n" in report
    assert "- Uptime: 3 d 20 h (since 7 Oct 15:00)\n" in report


async def test_a_status_report_does_not_hide_a_stuck_job(store: Store, logs: Path) -> None:
    # The user asks "how's the Mac?" between the alert and the next 5-minute check: the next check must
    # still see the stuck job (it once compared against the report's fresh reference and said fine).
    stuck = [Problem("extra-backup-job", "Backup job", "not running (runs stuck at 1000)")]
    mini = FakeMini()
    mini.job_runs(1000)
    health = make(store, mini, logs)
    assert await health.check() == []
    mini.now += 300
    assert await health.check() == stuck
    mini.now += 200
    assert "- Backup job: PROBLEM: not running (runs stuck at 1000)\n" in await health.report()
    mini.now += 100
    assert await health.check() == stuck
    mini.now += 30
    assert "- Backup job: PROBLEM: not running (runs stuck at 1000)\n" in await health.report()


class SlowMini(FakeMini):
    async def run(self, argv: list[str]) -> str:
        await asyncio.sleep(0.2)
        return await super().run(argv)

    async def get(self, url: str) -> str:
        await asyncio.sleep(0.2)
        return await super().get(url)


async def test_the_report_runs_its_checks_in_parallel(store: Store, logs: Path) -> None:
    mini = SlowMini()
    health = make(store, mini, logs)
    started = time.monotonic()
    await health.report()
    assert time.monotonic() - started < 0.35  # every command and request takes 0.2 s


async def test_extra_lines_come_before_the_problems_and_run_alongside(store: Store, logs: Path) -> None:
    mini = SlowMini()
    cfg = config(logs)

    async def extra() -> list[str]:  # e.g. the devices, which take a moment to answer
        await asyncio.sleep(0.2)
        return ["Mail: family ok (11:40) · work needs sign-in", "Devices: all 4 answer"]

    health = Health(store, cfg, run=mini.run, http_get=mini.get, disk=mini.disk, clock=mini.clock, extra_lines=extra)
    started = time.monotonic()
    report = await health.report()
    assert time.monotonic() - started < 0.35
    assert "- Mail: family ok (11:40) · work needs sign-in\n- Devices: all 4 answer\nProblems: none" in report


async def test_the_voice_service_has_a_line_when_voice_is_on(store: Store, logs: Path) -> None:
    mini = FakeMini()
    cfg = config(logs, voice_url="http://127.0.0.1:8150")
    health = Health(store, cfg, run=mini.run, http_get=mini.get, disk=mini.disk, clock=mini.clock)
    mini.http["http://127.0.0.1:8150/health"] = '{"stt": "ready", "tts": "ready"}'
    assert await health.check() == []
    assert "- Voice: ok (listening and speaking)\n" in await health.report()
    mini.http["http://127.0.0.1:8150/health"] = '{"stt": "loading", "tts": "loading"}'
    assert await health.check() == []
    assert "- Voice: loading its models\n" in await health.report()
    mini.http["http://127.0.0.1:8150/health"] = '{"stt": "ready", "tts": "failed"}'
    assert await health.check() == [Problem("voice", "Voice", "speaking failed to load")]
    mini.http["http://127.0.0.1:8150/health"] = ConnectionRefusedError()
    assert await health.check() == [Problem("voice", "Voice", "not answering")]


async def test_no_voice_line_when_voice_is_off(store: Store, logs: Path) -> None:
    health = make(store, FakeMini(), logs)
    assert "Voice" not in await health.report()


async def test_a_failing_extra_line_does_not_take_the_report_down(store: Store, logs: Path) -> None:
    mini = FakeMini()
    cfg = config(logs)

    async def broken() -> list[str]:
        raise FileNotFoundError("tools/tapo/.venv/bin/python")

    health = Health(store, cfg, run=mini.run, http_get=mini.get, disk=mini.disk, clock=mini.clock, extra_lines=broken)
    report = await health.report()
    assert "- Helper: ok\n" in report and "- More status: check failed (FileNotFoundError)\n" in report


async def test_the_report_names_the_host(store: Store, logs: Path) -> None:
    health = make(store, FakeMini(), logs, host_label="the Mini")
    assert (await health.report()).startswith("Status of the Mini at 8 Oct 11:40\n")


async def test_every_configured_log_folder_is_read_and_no_other(store: Store, logs: Path, tmp_path: Path) -> None:
    more, unwatched = tmp_path / "more-logs", tmp_path / "unwatched-logs"
    more.mkdir()
    unwatched.mkdir()
    mini = FakeMini()
    health = make(store, mini, logs, watched_log_dirs=(logs, more))
    await health.check()
    (more / "tool.log").write_text("level=ERROR in the second folder\n")
    (unwatched / "other.log").write_text("level=ERROR nobody asked\n")
    (logs / "ollama.log").write_text("level=ERROR in the first\n")
    mini.now += 60
    assert "- Log errors: 2 since 8 Oct 11:40 (ollama.log 1, tool.log 1)\n" in await health.report()
