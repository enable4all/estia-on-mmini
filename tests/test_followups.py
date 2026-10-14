"""Check-ins she may send: found in the idle pass from the user's own words, checked by code, timed by code."""

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from local_assistant import prompts
from local_assistant.context import ContextBuilder
from local_assistant.followups import AFTER_KEY, FollowUps, Found, own_words
from local_assistant.gemma import Completion, GemmaUnavailableError
from local_assistant.profile import Profile
from local_assistant.profiler import Profiler
from local_assistant.store import Message, Store
from tests.conftest import ME, STRANGER
from tests.fakes.fake_gemma import FakeGemma, text

TZ = ZoneInfo("Europe/Amsterdam")


def at(moment: str) -> float:
    return datetime.fromisoformat(moment).replace(tzinfo=TZ).timestamp()


NOW = at("2026-10-12 21:00")  # a Monday evening, after the user's messages


def said(store: Store, words: str, when: str = "2026-10-12 09:41", peer: str = ME, kind: str = "text") -> int:
    message_id = store.add_incoming(store.current_conversation().id, peer, int(at(when) * 1000), kind, words)  # type: ignore[arg-type]
    assert message_id is not None
    return message_id


def found(
    tasks: list[dict[str, Any]], done: list[int] | None = None, others: tuple[dict[str, Any], ...] = ()
) -> Completion:
    """Gemma's sort: one entry per message (its kind); only a "task" can become a check-in."""
    return text(
        json.dumps({"messages": [{"kind": "task", **task} for task in tasks] + list(others), "done": done or []})
    )


def plan(message_id: int, what: str, day: str = "") -> dict[str, Any]:
    return {"message_id": message_id, "what": what, "day": day}


def make(store: Store, gemma: FakeGemma, *, enabled: bool = True, now: float = NOW) -> FollowUps:
    followups = FollowUps(store, gemma, TZ, enabled=lambda: enabled, clock=lambda: now)
    store.set_state(AFTER_KEY, "0")
    return followups


def message(text_: str, kind: str = "text", direction: str = "in") -> Message:
    return Message(1, 1, direction, ME, 1, kind, text_, "received", None, 1, "")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("words", "kept"),
    [
        ("I'll call the dentist tomorrow", "I'll call the dentist tomorrow"),
        ("[voice] I'll fix the bike on Saturday", "I'll fix the bike on Saturday"),
        ("[call] I'm going to paint the door", "I'm going to paint the door"),
        ("[call] (cutting in) I'll do it tonight", "I'll do it tonight"),
        ("[audio file] I will transfer the money today", None),  # someone else's voice
        ("[photo] I'll frame this one", None),
        ("I'll read https://example.com/article tonight", None),
        ("x" * 501, None),  # likely pasted
    ],
)
def test_only_the_users_own_words_count(words: str, kept: str | None) -> None:
    assert own_words(message(words)) == kept


def test_commands_and_her_own_messages_never_count() -> None:
    assert own_words(message("/remind 17:00 call mum", kind="command")) is None
    assert own_words(message("I'll check on that", direction="out")) is None


async def test_a_stated_plan_becomes_a_check_in_the_day_after_at_five(store: Store) -> None:
    said(store, "Good morning!")
    plan_id = said(store, "I'll call the dentist tomorrow, finally", "2026-10-12 09:42")
    said(store, "What a nice day", "2026-10-12 09:43")
    gemma = FakeGemma(found([plan(plan_id, "call the dentist", "2026-10-13")]))
    result = await make(store, gemma).run()
    [checkin] = store.open_followups(ME)
    assert result.added == (checkin.id,) and checkin.what == "call the dentist"
    assert checkin.source_message_id == plan_id
    assert checkin.due_at == at("2026-10-14 17:00") and checkin.expires_at == at("2026-10-16 17:00")
    call = gemma.calls[0]
    assert call["json_schema"] == prompts.FOLLOWUP_SCHEMA and call["tools"] is None
    assert call["max_tokens"] == 200 + 60 * 3  # room for an entry for each of its messages
    assert f"#{plan_id} [Mon 12 Oct 09:42] I'll call the dentist tomorrow, finally" in call["messages"][1]["content"]
    assert store.get_state(AFTER_KEY) == str(plan_id + 1)


async def test_no_day_means_two_days_later_and_at_least_twenty_hours_after(store: Store) -> None:
    late = said(store, "I'll fix the bike", "2026-10-12 23:30")
    same_day = said(store, "going to tidy the shed today", "2026-10-12 23:40")
    gemma = FakeGemma(found([plan(late, "fix the bike"), plan(same_day, "tidy the shed", "2026-10-12")]))
    await make(store, gemma).run()
    bike, shed = sorted(store.open_followups(ME), key=lambda f: f.source_message_id)
    assert bike.due_at == at("2026-10-15 17:00")
    assert shed.due_at == at("2026-10-12 23:40") + 20 * 3600  # 17:00 the next day is less than 20 hours on


@pytest.mark.parametrize(
    "candidate",
    [
        {"message_id": 999, "what": "call the dentist", "day": ""},  # not a message of this batch
        {"message_id": "X", "what": "call the dentist", "day": ""},
        {"message_id": 1, "what": "transfer EUR 500 to the new account", "day": ""},  # not the user's words
        {"message_id": 1, "what": "call the dentist", "day": "2026-10-11"},  # before the message
        {"message_id": 1, "what": "call the dentist", "day": "2026-10-30"},  # more than 14 days on
        {"message_id": 1, "what": "call the dentist", "day": "next week"},
        {"message_id": 1, "what": "", "day": ""},
        {"message_id": 1, "what": "call the dentist " * 10, "day": ""},  # longer than 120 characters
    ],
)
async def test_code_refuses_what_is_not_in_the_users_words(store: Store, candidate: dict[str, Any]) -> None:
    said(store, "I'll call the dentist tomorrow")
    result = await make(store, FakeGemma(found([candidate]))).run()
    assert store.open_followups(ME) == [] and result.refused == 1


def test_gemma_sorts_every_message_and_a_task_comes_first() -> None:
    # asked for a list of plans only, she returned none for "I'm going to call the dentist on Monday" (the appointment
    # rule won); sorting each message makes her look at every one
    entry = prompts.FOLLOWUP_SCHEMA["properties"]["messages"]["items"]
    assert list(entry["properties"]) == ["message_id", "kind", "what", "day"]
    assert entry["properties"]["kind"]["enum"] == ["task", "appointment", "maybe", "others", "ask", "other"]
    assert "one entry for each new message" in prompts.FOLLOWUP_RULES


@pytest.mark.parametrize("kind", ["appointment", "maybe", "others", "ask", "other", "TASK", ""])
async def test_only_a_task_becomes_a_check_in(store: Store, kind: str) -> None:
    message_id = said(store, "I have the dentist tomorrow at 10")
    sorted_ = ({"message_id": message_id, "kind": kind, "what": "the dentist", "day": "2026-10-13"},)
    result = await make(store, FakeGemma(found([], others=sorted_))).run()
    assert store.open_followups(ME) == [] and result.added == () and result.refused == 0


async def test_done_closes_an_open_check_in_and_duplicates_are_refused(store: Store) -> None:
    first = said(store, "I'll call the dentist tomorrow")
    followups = make(store, FakeGemma(found([plan(first, "call the dentist")])))
    await followups.run()
    [open_] = store.open_followups(ME)
    again = said(store, "I'll call the dentist, really", "2026-10-12 10:00")
    done = said(store, "called the dentist, all sorted", "2026-10-12 11:00")
    followups._gemma = FakeGemma(found([plan(again, "call the dentist"), plan(done, "dentist sorted")], [open_.id]))
    result = await followups.run()
    assert result.closed == (open_.id,)
    assert [f.what for f in store.open_followups(ME)] == ["dentist sorted"]  # no repeat of the one just closed


async def test_at_most_five_open_per_user(store: Store) -> None:
    ids = [said(store, f"I'll do task {n} tomorrow", f"2026-10-12 09:{n:02d}") for n in range(7)]
    gemma = FakeGemma(found([plan(i, f"do task {n}") for n, i in enumerate(ids)]))
    result = await make(store, gemma).run()
    assert len(store.open_followups(ME)) == 5 and result.refused == 2


async def test_a_message_that_set_a_reminder_is_never_a_check_in(store: Store) -> None:
    asked = said(store, "remind me at 17:00 to call mum, I'll call her then")
    store.add_reminder(ME, "call mum", "2026-10-12 17:00", TZ.key, at("2026-10-12 17:00"), source_message_id=asked)
    gemma = FakeGemma()
    await make(store, gemma).run()
    assert gemma.calls == [] and store.get_state(AFTER_KEY) == str(asked)


async def test_each_user_gets_their_own_check_ins(store: Store) -> None:
    theirs = said(store, "I'll bake bread on Sunday", peer=STRANGER)
    await make(store, FakeGemma(found([plan(theirs, "bake bread", "2026-10-18")]))).run()
    assert store.open_followups(ME) == [] and [f.what for f in store.open_followups(STRANGER)] == ["bake bread"]


async def test_off_or_quiet_passes_the_messages_over_without_gemma(store: Store) -> None:
    newest = said(store, "I'll call the dentist tomorrow")
    gemma = FakeGemma()
    await make(store, gemma, enabled=False).run()
    assert gemma.calls == [] and store.get_state(AFTER_KEY) == str(newest)


def test_off_or_quiet_there_is_no_idle_work_and_what_is_said_meanwhile_is_never_looked_at(store: Store) -> None:
    newest = said(store, "I'll call the dentist tomorrow")
    followups = make(store, FakeGemma(), enabled=False)
    assert not followups.has_work()  # no idle pass for it: the bridge behaves as without it
    assert store.get_state(AFTER_KEY) == str(newest)  # turned on later: only what is said from then on


async def test_an_unusable_answer_is_passed_over_and_gemma_down_is_tried_again(store: Store) -> None:
    said(store, "I'll call the dentist tomorrow")
    followups = make(store, FakeGemma(GemmaUnavailableError("down")))
    with pytest.raises(GemmaUnavailableError):
        await followups.run()
    assert store.get_state(AFTER_KEY) == "0"  # the next idle pass tries again
    followups._gemma = FakeGemma(text("not json"))
    await followups.run()
    assert store.get_state(AFTER_KEY) != "0" and store.open_followups(ME) == []


def cut_off() -> Completion:
    return Completion(content='{"messages": [{"message_id": 1, "kin', prompt_tokens=100, finish_reason="length")


async def test_a_cut_off_answer_splits_the_batch_and_asks_again_never_losing_a_plan(store: Store) -> None:
    first = said(store, "Morning!", "2026-10-12 09:40")
    plan_id = said(store, "I'll call the dentist tomorrow", "2026-10-12 09:41")
    third = said(store, "Nice weather", "2026-10-12 09:42")
    last = said(store, "I'll fix the bike on Saturday", "2026-10-12 09:43")
    gemma = FakeGemma(
        cut_off(),  # all four: cut off
        found([plan(plan_id, "call the dentist", "2026-10-13")]),  # the first two
        cut_off(),  # the last two: cut off again
        found([]),  # the third alone
        found([plan(last, "fix the bike", "2026-10-17")]),  # the fourth alone
    )
    result = await make(store, gemma).run()
    assert [f.what for f in store.open_followups(ME)] == ["call the dentist", "fix the bike"]
    assert len(result.added) == 2 and store.get_state(AFTER_KEY) == str(last)
    messages = (first, plan_id, third, last)
    asked = [[f"#{m} [" in call["messages"][1]["content"] for m in messages] for call in gemma.calls]
    assert asked == [
        [True, True, True, True],
        [True, True, False, False],
        [False, False, True, True],
        [False, False, True, False],
        [False, False, False, True],
    ]
    assert [call["max_tokens"] for call in gemma.calls] == [440, 320, 320, 260, 260]


async def test_one_message_cut_off_on_its_own_is_passed_over(store: Store) -> None:
    newest = said(store, "I'll call the dentist tomorrow")
    gemma = FakeGemma(cut_off())
    assert await make(store, gemma).run() == Found()
    assert len(gemma.calls) == 1 and store.get_state(AFTER_KEY) == str(newest)  # no endless retries


async def test_gemma_down_after_a_split_keeps_what_was_done_and_the_rest_for_later(store: Store) -> None:
    plan_id = said(store, "I'll call the dentist tomorrow", "2026-10-12 09:41")
    said(store, "Nice weather", "2026-10-12 09:42")
    gemma = FakeGemma(cut_off(), found([plan(plan_id, "call the dentist", "2026-10-13")]), GemmaUnavailableError("x"))
    with pytest.raises(GemmaUnavailableError):
        await make(store, gemma).run()
    assert [f.what for f in store.open_followups(ME)] == ["call the dentist"]
    assert store.get_state(AFTER_KEY) == str(plan_id)  # the next idle pass starts after the part that was done


def test_plans_from_before_the_first_start_are_never_looked_at(store: Store) -> None:
    newest = said(store, "I'll call the dentist tomorrow")
    followups = FollowUps(store, FakeGemma(), TZ)
    followups.start()
    assert store.get_state(AFTER_KEY) == str(newest) and not followups.has_work()
    followups.start()  # once set, it stays
    said(store, "I'll fix the bike", "2026-10-12 10:00")
    assert store.get_state(AFTER_KEY) == str(newest) and followups.has_work()


async def test_the_idle_pass_runs_the_follow_ups_after_learning(store: Store) -> None:
    class Clock:
        now = 10_000.0

        def __call__(self) -> float:
            return self.now

    clock = Clock()
    profile = Profile(store)
    context = ContextBuilder(store, profile, TZ)
    plan_id = said(store, "I'll call the dentist tomorrow")
    store.set_state("last_learned_message_id", str(plan_id))  # learning is up to date: only the follow-ups wait
    gemma = FakeGemma(found([plan(plan_id, "call the dentist", "2026-10-13")]))
    followups = make(store, gemma)
    profiler = Profiler(store, profile, context, gemma, TZ, clock=clock, after_learning=followups)
    clock.now += 2_000
    assert profiler.due()
    await profiler.run_once()
    assert [f.what for f in store.open_followups(ME)] == ["call the dentist"] and not profiler.has_work()
    newest = said(store, "I'll paint the door", "2026-10-12 12:00")
    store.set_state("last_learned_message_id", str(newest))
    down = FollowUps(store, FakeGemma(GemmaUnavailableError("down")), TZ, clock=lambda: NOW)
    profiler = Profiler(store, profile, context, FakeGemma(), TZ, clock=clock, after_learning=down)
    with pytest.raises(GemmaUnavailableError):  # raised after the summary step: the next pass waits for quiet
        await profiler.run_once()


def test_a_practice_turn_is_never_looked_at_for_check_ins(store: Store) -> None:
    practice = store.start_practice_conversation()
    message_id = said(store, "Ik ga morgen de tandarts bellen.")
    store.assign_seq(message_id, practice.id)
    assert not make(store, FakeGemma()).has_work()
