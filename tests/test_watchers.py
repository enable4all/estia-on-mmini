from local_assistant.store import Store
from local_assistant.watchers import Problem, Scheduler
from tests.conftest import TZ

START = 1_791_452_400.0  # 2026-10-08 11:40 Europe/Amsterdam


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


class ScriptedWatcher:
    name = "mini"

    def __init__(self) -> None:
        self.problems: list[Problem] = []
        self.calls = 0
        self.fail = False

    async def check(self) -> list[Problem]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return list(self.problems)


def make(store: Store, clock: Clock) -> tuple[Scheduler, ScriptedWatcher, list[str]]:
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    watcher = ScriptedWatcher()
    scheduler = Scheduler(store, send, TZ, clock=clock)
    scheduler.add(watcher, every=4 * 3600, confirm_after=300, first_after=120)
    return scheduler, watcher, sent


EXTRA_DOWN = Problem("extra", "Extra check", "not answering")


async def test_first_check_waits_after_start(store: Store) -> None:
    clock = Clock()
    scheduler, watcher, _ = make(store, clock)
    await scheduler.tick()
    assert watcher.calls == 0
    clock.now += 120
    await scheduler.tick()
    assert watcher.calls == 1


async def test_problem_is_confirmed_then_alerted_once_then_recovered_once(store: Store) -> None:
    clock = Clock()
    scheduler, watcher, sent = make(store, clock)
    watcher.problems = [EXTRA_DOWN]
    clock.now += 120
    await scheduler.tick()  # suspect: no message yet
    assert sent == []
    clock.now += 300
    await scheduler.tick()  # confirmed after 5 minutes
    assert sent == ["⚠ Problem on the Mac:\n- Extra check: not answering (since 11:42)"]
    clock.now += 300
    await scheduler.tick()  # still down: no repeat
    assert len(sent) == 1
    watcher.problems = []
    clock.now += 300
    await scheduler.tick()
    assert sent[1] == "✓ Back to normal on the Mac:\n- Extra check (was down 11:42–11:57)"
    assert watcher.calls == 4


async def test_a_passing_hiccup_sends_nothing(store: Store) -> None:
    clock = Clock()
    scheduler, watcher, sent = make(store, clock)
    watcher.problems = [EXTRA_DOWN]
    clock.now += 120
    await scheduler.tick()
    watcher.problems = []
    clock.now += 300
    await scheduler.tick()
    assert sent == []


async def test_several_problems_come_in_one_message(store: Store) -> None:
    clock = Clock()
    scheduler, watcher, sent = make(store, clock)
    watcher.problems = [EXTRA_DOWN, Problem("disk", "Disk", "7% free")]
    clock.now += 120
    await scheduler.tick()
    clock.now += 300
    await scheduler.tick()
    assert sent == ["⚠ Problem on the Mac:\n- Extra check: not answering (since 11:42)\n- Disk: 7% free (since 11:42)"]


async def test_rhythm_is_slow_when_fine_and_fast_while_a_problem_lasts(store: Store) -> None:
    clock = Clock()
    scheduler, watcher, _ = make(store, clock)
    clock.now += 120
    await scheduler.tick()  # fine → next in 4 h
    clock.now += 3_600
    await scheduler.tick()
    assert watcher.calls == 1
    clock.now += 3 * 3_600
    watcher.problems = [EXTRA_DOWN]
    await scheduler.tick()  # problem → next in 5 minutes
    clock.now += 300
    await scheduler.tick()
    assert watcher.calls == 3


async def test_a_failing_watcher_counts_as_a_problem(store: Store) -> None:
    clock = Clock()
    scheduler, watcher, sent = make(store, clock)
    watcher.fail = True
    clock.now += 120
    await scheduler.tick()
    clock.now += 300
    await scheduler.tick()
    assert sent == ["⚠ Problem on the Mac:\n- Check mini: failed (RuntimeError) (since 11:42)"]


async def test_alert_state_survives_a_restart(store: Store) -> None:
    clock = Clock()
    scheduler, watcher, sent = make(store, clock)
    watcher.problems = [EXTRA_DOWN]
    clock.now += 120
    await scheduler.tick()
    clock.now += 300
    await scheduler.tick()
    assert len(sent) == 1
    restarted, watcher2, sent2 = make(store, clock)  # the bridge restarts while the extra check is down
    watcher2.problems = [EXTRA_DOWN]
    clock.now += 120
    await restarted.tick()  # still down after the restart: no second alert
    assert sent2 == []
    watcher2.problems = []
    clock.now += 300
    await restarted.tick()
    assert sent2 == ["✓ Back to normal on the Mac:\n- Extra check (was down 11:42–11:54)"]


async def test_a_failing_send_does_not_stop_the_scheduler(store: Store) -> None:
    clock = Clock()
    attempts: list[str] = []

    async def broken_send(text: str) -> None:
        attempts.append(text)
        raise RuntimeError("signal down")

    watcher = ScriptedWatcher()
    scheduler = Scheduler(store, broken_send, TZ, clock=clock)
    scheduler.add(watcher, every=4 * 3600, confirm_after=300, first_after=120)
    watcher.problems = [EXTRA_DOWN]
    clock.now += 120
    await scheduler.tick()
    clock.now += 300
    await scheduler.tick()  # the alert send fails: logged, not raised
    assert len(attempts) == 1
    clock.now += 300
    await scheduler.tick()
    assert watcher.calls == 3  # still on the 5-minute rhythm
