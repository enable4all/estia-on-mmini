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
    assert sent == ["⚠ **Problem on the Mac:**\n- Extra check: not answering (since 11:42)"]
    clock.now += 300
    await scheduler.tick()  # still down: no repeat
    assert len(sent) == 1
    watcher.problems = []
    clock.now += 300
    await scheduler.tick()
    assert sent[1] == "✓ **Back to normal on the Mac:**\n- Extra check (was down 11:42–11:57)"
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
    assert sent == [
        "⚠ **Problem on the Mac:**\n- Extra check: not answering (since 11:42)\n- Disk: 7% free (since 11:42)"
    ]


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
    assert sent == ["⚠ **Problem on the Mac:**\n- Check mini: failed (RuntimeError) (since 11:42)"]


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
    assert sent2 == ["✓ **Back to normal on the Mac:**\n- Extra check (was down 11:42–11:54)"]


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


# quiet hours (22:00-08:00): a health alert and its "back to normal" wait until their end; a problem that came and went
# overnight is one line in the morning. Security notes never come through here: they go at once (app.py).

NIGHT = 1_791_496_800.0  # 2026-10-09 00:00 Europe/Amsterdam
QUIET = (22 * 60, 8 * 60)


def quiet_make(store: Store, clock: Clock) -> tuple[Scheduler, ScriptedWatcher, list[str]]:
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    watcher = ScriptedWatcher()
    scheduler = Scheduler(store, send, TZ, clock=clock, quiet_hours=QUIET)
    scheduler.add(watcher, every=4 * 3600, confirm_after=300, first_after=0)
    return scheduler, watcher, sent


async def test_a_problem_at_night_is_told_at_the_end_of_the_quiet_hours(store: Store) -> None:
    clock = Clock()
    clock.now = NIGHT + 3 * 3600  # 03:00
    scheduler, watcher, sent = quiet_make(store, clock)
    watcher.problems = [EXTRA_DOWN]
    await scheduler.tick()
    clock.now += 300
    await scheduler.tick()  # confirmed at 03:05, held
    assert sent == []
    clock.now = NIGHT + 8 * 3600  # 08:00
    await scheduler.tick()
    assert sent == ["⚠ **Problem on the Mac:**\n- Extra check: not answering (since 03:00)"]
    await scheduler.tick()
    assert len(sent) == 1  # told once
    watcher.problems = []
    clock.now += 3600
    await scheduler.tick()
    assert sent[1] == "✓ **Back to normal on the Mac:**\n- Extra check (was down 03:00–09:00)"


async def test_a_problem_that_cleared_overnight_is_one_line_in_the_morning(store: Store) -> None:
    clock = Clock()
    clock.now = NIGHT + 2 * 3600  # 02:00
    scheduler, watcher, sent = quiet_make(store, clock)
    watcher.problems = [EXTRA_DOWN]
    await scheduler.tick()
    clock.now += 600
    await scheduler.tick()  # confirmed, held
    watcher.problems = []
    clock.now += 1200
    await scheduler.tick()  # gone at 02:30: nothing at night
    assert sent == []
    clock.now = NIGHT + 8 * 3600 + 30
    await scheduler.tick()
    assert sent == ["✓ **Overnight on the Mac, already back to normal:**\n- Extra check: not answering (02:00–02:30)"]
    clock.now += 3600
    await scheduler.tick()
    assert len(sent) == 1


async def test_back_to_normal_at_night_waits_for_the_morning(store: Store) -> None:
    clock = Clock()
    clock.now = NIGHT - 3 * 3600  # 21:00: before the quiet hours, so told at once
    scheduler, watcher, sent = quiet_make(store, clock)
    watcher.problems = [EXTRA_DOWN]
    await scheduler.tick()
    clock.now += 300
    await scheduler.tick()
    assert sent == ["⚠ **Problem on the Mac:**\n- Extra check: not answering (since 21:00)"]
    watcher.problems = []
    clock.now = NIGHT + 3600  # 01:00
    await scheduler.tick()
    assert len(sent) == 1
    clock.now = NIGHT + 8 * 3600
    await scheduler.tick()
    assert sent[1] == "✓ **Back to normal on the Mac:**\n- Extra check (was down 21:00–01:00)"


async def test_held_alerts_survive_a_restart(store: Store) -> None:
    clock = Clock()
    clock.now = NIGHT + 3 * 3600
    scheduler, watcher, sent = quiet_make(store, clock)
    watcher.problems = [EXTRA_DOWN]
    await scheduler.tick()
    clock.now += 300
    await scheduler.tick()
    watcher.problems = []
    clock.now += 300
    await scheduler.tick()  # cleared overnight
    again, _, sent_again = quiet_make(store, clock)  # the bridge restarted
    clock.now = NIGHT + 8 * 3600
    await again.tick()
    assert sent == [] and sent_again == [
        "✓ **Overnight on the Mac, already back to normal:**\n- Extra check: not answering (03:00–03:10)"
    ]


async def test_a_morning_send_that_fails_is_tried_again_and_nothing_is_told_twice(store: Store) -> None:
    """Review of 14 Oct: what quiet hours held is cleared only once it went out, each kind on its own."""
    clock = Clock()
    clock.now = NIGHT - 3 * 3600  # 21:00
    sent: list[str] = []
    fail_once = ["Back to normal"]

    async def send(text: str) -> None:
        if fail_once and fail_once[0] in text:
            fail_once.clear()
            raise RuntimeError("signal down")
        sent.append(text)

    scheduler = Scheduler(store, send, TZ, clock=clock, quiet_hours=QUIET)
    watcher = ScriptedWatcher()
    scheduler.add(watcher, every=4 * 3600, confirm_after=300, first_after=0)
    watcher.problems = [EXTRA_DOWN]
    await scheduler.tick()
    clock.now += 300
    await scheduler.tick()  # 21:05: told at once
    watcher.problems = [Problem("disk", "Disk", "almost full"), Problem("memory", "Memory", "low")]
    clock.now = NIGHT + 3 * 3600  # 03:00: the extra check is back (in quiet hours), two new problems
    await scheduler.tick()
    clock.now += 300
    await scheduler.tick()  # 03:05: both alerts are held
    assert len(sent) == 1
    clock.now = NIGHT + 8 * 3600
    await scheduler.tick()  # the held alert goes; the back-to-normal message fails
    assert sent[1:] == ["⚠ **Problem on the Mac:**\n- Disk: almost full (since 03:00)\n- Memory: low (since 03:00)"]
    clock.now += 30
    await scheduler.tick()
    assert sent[2:] == ["✓ **Back to normal on the Mac:**\n- Extra check (was down 21:00–03:00)"]
    clock.now += 30
    await scheduler.tick()
    assert len(sent) == 3


async def test_without_quiet_hours_nothing_waits(store: Store) -> None:
    clock = Clock()
    clock.now = NIGHT + 3 * 3600
    scheduler, watcher, sent = make(store, clock)  # no quiet hours given
    watcher.problems = [EXTRA_DOWN]
    clock.now += 120
    await scheduler.tick()
    clock.now += 300
    await scheduler.tick()
    assert sent == ["⚠ **Problem on the Mac:**\n- Extra check: not answering (since 03:02)"]
