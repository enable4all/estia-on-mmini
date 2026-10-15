"""The evening note (owner, D4, 15 Oct): code's three fixed questions at the user's time, off unless the user's typed
/evening on (or [initiative] evening). It goes through the initiative's gate where it applies (quiet hours, /quiet,
/lock, busy, practice; at most once a day), never needs /initiative on and never counts toward its caps; it goes only
to the one account that turned it on, is a note (never in Gemma's history), and is never followed up after a silence.
Off, her request is byte for byte as before (fake clock, never wall time; the bridge on a fake signal-cli)."""

import hashlib
import json
import random
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant.app import App
from local_assistant.config import ConfigError, parse_config
from local_assistant.context import ContextBuilder
from local_assistant.initiative import ANNOUNCED_KEY, EVENING_KEY, Initiative, Settings, Writer
from local_assistant.profile import Profile
from local_assistant.signal_text import plain as on_phone
from local_assistant.store import SCHEMA_VERSION, Store
from local_assistant.voice import COMMANDS
from tests.conftest import ME, TZ
from tests.fakes.fake_gemma import FakeGemma
from tests.test_app import T0, deliver, sent, start, stop, until
from tests.test_config import BASE
from tests.test_initiative import Clock, Sender, at, followup, outcomes, said, written

SECOND = "22222222-3333-4444-8555-666666666666"  # a second allowed account (a household)
NINE = 21 * 60  # [initiative] evening = "21:00"
FOOTER = "(evening note · /evening off to stop)"
NOTE = f"{prompts.EVENING_NOTE}\n{FOOTER}"  # as the phone gets it


class Holds:
    """What the gate reads from the bridge: an answer or a call (busy), a /babble session, /lock."""

    def __init__(self) -> None:
        self.busy = False
        self.practising = False
        self.locked = False


def make(
    store: Store,
    settings: Settings,
    when: str,
    *,
    holds: Holds | None = None,
    acis: frozenset[str] = frozenset({ME}),
    gemma: FakeGemma | None = None,
) -> tuple[Initiative, Sender, Clock]:
    held = holds or Holds()
    clock = Clock(at(when))
    store.set_state(ANNOUNCED_KEY, f"{clock.now - 60 * 86_400:.0f}")  # an install from before: no announcement
    sender = Sender(store)
    initiative = Initiative(
        store,
        settings,
        Writer(gemma or FakeGemma(), name="Estia", style=lambda: "STYLE (built in)"),
        sender,
        tz=TZ,
        acis=acis,
        busy=lambda: held.busy,
        practising=lambda: held.practising,
        locked=lambda: held.locked,
        clock=clock,
        rng=random.Random(7),
    )
    return initiative, sender, clock


def evening(**changes: Any) -> Settings:
    """[initiative] evening = "21:00"; the rest as the config leaves it (/initiative off)."""
    return Settings(evening=NINE, **changes)


async def ticks(initiative: Initiative, clock: Clock, first: str, last: str, *, every: int = 10) -> None:
    """One tick every `every` minutes from `first` to `last` (both included)."""
    clock.now = at(first)
    while clock.now <= at(last):
        await initiative.tick()
        clock.now += every * 60


def state(store: Store) -> list[tuple[str, str]]:
    return [(row[0], row[1]) for row in store._conn.execute("SELECT key, value FROM state ORDER BY key")]


def outgoing(store: Store) -> list[str]:
    return [row[0] for row in store._conn.execute("SELECT text FROM messages WHERE direction = 'out' ORDER BY id")]


# the words, the config


def test_the_note_is_codes_three_fixed_questions_with_its_footer() -> None:
    assert prompts.EVENING_NOTE == "Evening. What went well today? What is still open? One thing for tomorrow?"
    assert prompts.EVENING_NOTE.count("?") == 3
    assert prompts.initiative_footer("evening") == FOOTER
    assert prompts.INITIATIVE_KINDS["evening"] == "evening note"


@pytest.mark.parametrize(
    ("initiative", "value"),
    [(None, ""), ({}, ""), ({"evening": ""}, ""), ({"evening": "21:00"}, "21:00"), ({"evening": " 20:30 "}, "20:30")],
)
def test_the_config_evening_left_out_is_off(initiative: dict[str, object] | None, value: str) -> None:
    cfg = parse_config(BASE | ({"initiative": initiative} if initiative is not None else {}))
    assert cfg.initiative_evening == value
    assert Settings.of(cfg).evening == (None if not value else int(value[:2]) * 60 + int(value[3:]))
    assert not cfg.initiative_enabled  # the evening note never turns the initiative on


@pytest.mark.parametrize("bad", ["9pm", "24:00", "21", 2100, True])
def test_a_bad_evening_time_is_a_config_error_naming_the_form(bad: object) -> None:
    with pytest.raises(ConfigError, match=r'initiative\.evening must look like "21:00"'):
        parse_config(BASE | {"initiative": {"evening": bad}})


# off by default


async def test_off_by_default_nothing_is_sent_and_no_state_is_written(store: Store) -> None:
    initiative, sender, clock = make(store, Settings(), "2026-10-14 20:00")
    before = state(store)
    await ticks(initiative, clock, "2026-10-14 20:00", "2026-10-15 23:30")
    assert sender.sent == [] and store.initiative_entries() == [] and state(store) == before
    assert initiative.evening() is None and initiative.evening_time() == ""
    assert initiative.set_evening(ME, "") == prompts.EVENING_STATE_OFF.format(time="21:00")
    assert state(store) == before  # /evening alone only tells


# on: once a day, at its time


async def test_on_it_comes_once_at_its_time_as_a_note_without_initiative_on(store: Store) -> None:
    initiative, sender, clock = make(store, evening(), "2026-10-14 20:59")
    assert not initiative.enabled and initiative.evening_time() == "21:00"
    await initiative.tick()
    assert sender.sent == []
    clock.now = at("2026-10-14 21:00")
    await initiative.tick()
    assert sender.sent == [(ME, NOTE)]
    [entry] = store.initiative_entries()
    assert (entry.kind, entry.outcome, entry.reason, entry.local_day) == ("evening", "sent", None, "2026-10-14")
    note = store.message(entry.message_id or 0)
    assert (note.kind, note.text, note.footer) == ("note", prompts.EVENING_NOTE, FOOTER)
    await ticks(initiative, clock, "2026-10-14 21:01", "2026-10-15 20:59", every=7)
    assert sender.sent == [(ME, NOTE)]  # at most once a day
    clock.now = at("2026-10-15 21:00")
    await initiative.tick()
    assert sender.sent == [(ME, NOTE), (ME, NOTE)]


async def test_a_late_time_ends_at_midnight_so_at_most_one_note_a_calendar_day(store: Store) -> None:
    """D4, at most once a day: a late time's two hours end at midnight at the latest (never "What went well today?"
    about yesterday, then today's own note the same day); the evening is then given up with the reason it waited for."""
    holds = Holds()
    holds.busy = True
    initiative, sender, clock = make(
        store, Settings(evening=23 * 60 + 30, quiet_hours=None), "2026-10-14 23:30", holds=holds
    )
    await ticks(initiative, clock, "2026-10-14 23:30", "2026-10-15 00:40")
    assert sender.sent == []
    assert (
        outcomes(store) == [("evening", "skipped", "busy")] and store.initiative_entries()[0].local_day == "2026-10-14"
    )
    holds.busy = False
    await ticks(initiative, clock, "2026-10-15 00:41", "2026-10-15 23:35")
    assert sender.sent == [(ME, NOTE)]  # the next evening's own, at its time
    assert [entry.local_day for entry in store.initiative_entries()] == ["2026-10-14", "2026-10-15"]
    assert store.initiative_entries()[1].created_at.startswith("2026-10-15T21:3")  # 23:31 local (UTC+2), a tick


# the gate where it applies: quiet hours, /quiet, /lock, busy, practice


async def test_quiet_hours_hold_it_and_after_two_hours_it_is_given_up(store: Store) -> None:
    initiative, sender, clock = make(store, evening(quiet_hours=(20 * 60 + 30, 7 * 60)), "2026-10-14 21:00")
    await ticks(initiative, clock, "2026-10-14 21:00", "2026-10-15 07:30")
    assert sender.sent == []
    assert outcomes(store) == [("evening", "skipped", "quiet_hours")]  # logged once, with its reason word


@pytest.mark.parametrize("hold", ["quiet", "locked", "busy", "practice"])
async def test_each_hold_waits_and_after_two_hours_that_evening_it_is_given_up(store: Store, hold: str) -> None:
    holds = Holds()
    late = (23 * 60 + 30, 7 * 60)  # quiet hours after its two hours: the hold alone keeps it
    initiative, sender, clock = make(store, evening(quiet_hours=late), "2026-10-14 20:00", holds=holds)
    if hold == "quiet":
        initiative.set_quiet(True)
    holds.locked, holds.busy, holds.practising = hold == "locked", hold == "busy", hold == "practice"
    await ticks(initiative, clock, "2026-10-14 20:00", "2026-10-14 22:59", every=1)
    assert sender.sent == [] and store.initiative_entries() == []  # it waits
    clock.now = at("2026-10-14 23:00")
    await initiative.tick()
    assert outcomes(store) == [("evening", "skipped", hold)]  # given up after two hours, never caught up
    if hold == "quiet":
        initiative.set_quiet(False)
    holds.locked = holds.busy = holds.practising = False
    await ticks(initiative, clock, "2026-10-14 23:01", "2026-10-15 21:01")
    assert sender.sent == [(ME, NOTE)]  # the next evening as ever


async def test_a_hold_lifted_within_its_two_hours_lets_it_go(store: Store) -> None:
    holds = Holds()
    holds.busy = True
    initiative, sender, clock = make(store, evening(), "2026-10-14 21:00", holds=holds)
    await ticks(initiative, clock, "2026-10-14 21:00", "2026-10-14 21:50")
    assert sender.sent == []
    holds.busy = False
    await ticks(initiative, clock, "2026-10-14 21:51", "2026-10-14 21:51")
    assert sender.sent == [(ME, NOTE)] and outcomes(store) == [("evening", "sent", None)]
    initiative.set_quiet(True)
    holds.locked = True
    await ticks(initiative, clock, "2026-10-15 20:50", "2026-10-15 21:20")
    assert len(sender.sent) == 1
    initiative.set_quiet(False)  # /quiet off: still locked
    await ticks(initiative, clock, "2026-10-15 21:21", "2026-10-15 21:40")
    assert len(sender.sent) == 1
    holds.locked = False  # /unlock
    await ticks(initiative, clock, "2026-10-15 21:41", "2026-10-15 21:41")
    assert sender.sent == [(ME, NOTE), (ME, NOTE)]


async def test_a_user_message_in_the_last_15_minutes_is_busy(store: Store) -> None:
    initiative, sender, clock = make(store, evening(), "2026-10-14 21:00")
    said(store, "On my way home", at("2026-10-14 20:50"))
    await ticks(initiative, clock, "2026-10-14 21:00", "2026-10-14 21:04", every=1)
    assert sender.sent == []
    clock.now = at("2026-10-14 21:05")
    await initiative.tick()
    assert sender.sent == [(ME, NOTE)]


# never followed up, never counted


async def test_never_followed_up_after_a_silence_and_the_same_words_each_evening(store: Store) -> None:
    settings = evening(enabled=True, improvise=False, lookback=False)  # her own messages on too: still nothing more
    initiative, sender, clock = make(store, settings, "2026-10-14 21:00")
    await ticks(initiative, clock, "2026-10-14 21:00", "2026-10-16 21:00", every=30)
    assert sender.sent == [(ME, NOTE)] * 3  # no "you didn't answer", no streak, no count: the same words
    assert outgoing(store) == [prompts.EVENING_NOTE] * 3
    said(store, "Good day. Taxes still open. Tomorrow: call Sam.", at("2026-10-16 21:20"))
    await ticks(initiative, clock, "2026-10-16 21:30", "2026-10-16 22:30", every=30)
    assert all(entry.replied_at is None for entry in store.initiative_entries())  # a reply is never counted
    assert "last 4 weeks: 0 sent · 0 answered within a day" in initiative.status_lines()[0]
    assert store.all_open_followups() == []


async def test_not_counted_in_max_per_day_and_never_blocked_by_it(store: Store) -> None:
    gemma = FakeGemma(written("Did you get hold of the dentist?"), written("Did you get hold of the dentist?"))
    settings = evening(enabled=True, improvise=False, lookback=False)  # max_per_day = 1
    initiative, sender, clock = make(store, settings, "2026-10-14 17:30", gemma=gemma)
    followup(store, due=at("2026-10-14 17:00"))
    await initiative.tick()
    assert len(sender.sent) == 1  # the check-in: the day's one message
    clock.now = at("2026-10-14 21:00")
    await initiative.tick()
    assert sender.sent[-1] == (ME, NOTE)  # never blocked by max_per_day
    source = said(store, "I'll book the dentist on Thursday", at("2026-10-13 10:00"))
    store.add_followup(
        ME, "book the dentist", source, at("2026-10-15 21:00"), at("2026-10-17 12:00"), at="2026-10-13T08:00:00+00:00"
    )
    clock.now = at("2026-10-15 21:00")
    await initiative.tick()
    assert sender.sent[-1] == (ME, NOTE)  # the evening note first, then 15 minutes later ...
    clock.now = at("2026-10-15 21:15")
    await initiative.tick()
    assert sender.sent[-1][1].startswith("Did you get hold of the dentist?")  # ... the check-in: it never counted
    assert [kind for kind, _, _ in outcomes(store)] == ["followup", "evening", "evening", "followup"]


async def test_another_message_of_hers_waits_15_minutes_after_the_evening_note(store: Store) -> None:
    """Held busy all evening, a check-in due at 17:00 and the evening note at 21:00 are released together: the note
    goes first and the check-in waits as after a user message (busy), never two notes a minute apart."""
    gemma = FakeGemma(written("Did you get hold of the dentist?"))
    holds = Holds()
    holds.busy = True
    settings = evening(enabled=True, improvise=False, lookback=False)
    initiative, sender, clock = make(store, settings, "2026-10-14 17:00", holds=holds, gemma=gemma)
    followup(store, due=at("2026-10-14 17:00"), expires=at("2026-10-16 12:00"))
    await ticks(initiative, clock, "2026-10-14 17:00", "2026-10-14 21:29")
    assert sender.sent == []
    holds.busy = False
    await ticks(initiative, clock, "2026-10-14 21:30", "2026-10-14 21:44", every=1)
    assert sender.sent == [(ME, NOTE)]  # the evening note at 21:30; the check-in still waits
    clock.now = at("2026-10-14 21:45")
    await initiative.tick()
    assert sender.sent[-1][1].startswith("Did you get hold of the dentist?")  # 15 minutes after the note


# only to the account that turned it on


async def test_it_goes_only_to_the_account_that_turned_it_on(store: Store) -> None:
    both = frozenset({ME, SECOND})
    initiative, sender, clock = make(store, evening(), "2026-10-14 20:00", acis=both)
    assert initiative.evening() is None  # the config names no account: with two it goes to none
    assert initiative.set_evening(SECOND, "on") == prompts.EVENING_ON.format(time="21:00", start="today")
    assert initiative.set_evening(ME, "on") == prompts.EVENING_OTHER
    assert initiative.set_evening(ME, "off") == prompts.EVENING_OTHER
    assert initiative.set_evening(ME, "20:00") == prompts.EVENING_OTHER
    await ticks(initiative, clock, "2026-10-14 20:00", "2026-10-14 21:30")
    assert sender.sent == [(SECOND, NOTE)]
    assert initiative.set_evening(SECOND, "off") == prompts.EVENING_OFF_REPLY
    assert initiative.set_evening(ME, "21:15") == prompts.EVENING_ON.format(time="21:15", start="tomorrow")
    await ticks(initiative, clock, "2026-10-15 21:00", "2026-10-15 21:30")
    assert sender.sent == [(SECOND, NOTE), (ME, NOTE)]


async def test_with_one_account_the_configs_time_is_hers_and_the_state_wins(store: Store) -> None:
    initiative, sender, clock = make(store, evening(), "2026-10-14 20:00")
    assert initiative.set_evening(ME, "") == prompts.EVENING_STATE_ON.format(time="21:00")
    assert initiative.set_evening(ME, "on") == prompts.EVENING_ALREADY_ON.format(time="21:00")
    assert store.get_state(EVENING_KEY) is None  # nothing changed: nothing written
    assert initiative.set_evening(ME, "off") == prompts.EVENING_OFF_REPLY  # wins over the config
    assert initiative.set_evening(ME, "off") == prompts.EVENING_ALREADY_OFF
    await ticks(initiative, clock, "2026-10-14 20:00", "2026-10-14 23:30")
    assert sender.sent == [] and initiative.evening_time() == ""
    assert initiative.set_evening(ME, "on") == prompts.EVENING_ON.format(time="21:00", start="tomorrow")
    assert initiative.set_evening(ME, "9:30") == prompts.EVENING_ON.format(time="09:30", start="tomorrow")
    assert initiative.evening_time() == "09:30"


async def test_turned_on_after_its_time_it_starts_the_next_evening_as_its_reply_says(store: Store) -> None:
    initiative, sender, clock = make(store, Settings(), "2026-10-14 21:30")
    assert initiative.set_evening(ME, "21:00") == prompts.EVENING_ON.format(time="21:00", start="tomorrow")
    await ticks(initiative, clock, "2026-10-14 21:30", "2026-10-15 20:59")
    assert sender.sent == [] and store.initiative_entries() == []
    await ticks(initiative, clock, "2026-10-15 21:00", "2026-10-15 21:00")
    assert sender.sent == [(ME, NOTE)]
    assert initiative.set_evening(ME, "22:00") == prompts.EVENING_IN_QUIET_HOURS.format(
        time="22:00", hours="22:00-08:00"
    )
    assert initiative.set_evening(ME, "21:45") == prompts.EVENING_ON.format(time="21:45", start="today")
    await ticks(initiative, clock, "2026-10-15 21:01", "2026-10-15 23:59")
    assert len(sender.sent) == 1  # at most once a day, whatever the time becomes


@pytest.mark.parametrize("typed", ["soon", "25:00", "21:60", "on now", "21.00"])
async def test_evening_takes_on_off_or_a_time(store: Store, typed: str) -> None:
    initiative, _, _ = make(store, Settings(), "2026-10-14 20:00")
    assert initiative.set_evening(ME, typed) == prompts.EVENING_USAGE
    assert store.get_state(EVENING_KEY) is None


async def test_a_time_inside_the_quiet_hours_is_refused(store: Store) -> None:
    initiative, _, _ = make(store, Settings(), "2026-10-14 20:00")
    assert initiative.set_evening(ME, "23:00") == prompts.EVENING_IN_QUIET_HOURS.format(
        time="23:00", hours="22:00-08:00"
    )
    assert store.get_state(EVENING_KEY) is None
    assert initiative.set_evening(ME, "21:59").startswith("On: ")


async def test_turned_on_while_quiet_it_says_quiet_still_pauses_it(store: Store) -> None:
    initiative, _, _ = make(store, Settings(), "2026-10-14 20:00")
    initiative.set_quiet(True)
    reply = initiative.set_evening(ME, "on")
    assert reply == prompts.EVENING_ON.format(time="21:00", start="today") + prompts.EVENING_STILL_QUIET


async def test_quiet_off_names_the_evening_note_it_resumes(store: Store) -> None:
    initiative, _, _ = make(store, Settings(), "2026-10-14 20:00")
    initiative.set_evening(ME, "on")
    initiative.set_quiet(True)
    assert initiative.set_quiet(False) == prompts.QUIET_OFF.format(rules="the evening note at 21:00")
    both, _, _ = make(store, Settings(enabled=True), "2026-10-14 20:00")
    both.set_quiet(True)
    rules = f"{Settings(enabled=True).limits()}; the evening note at 21:00"
    assert both.set_quiet(False) == prompts.QUIET_OFF.format(rules=rules)


# at most once, never in her history


async def test_a_note_being_sent_at_a_stop_is_never_sent_again_that_evening(store: Store) -> None:
    initiative, sender, clock = make(store, evening(), "2026-10-14 21:05")
    out = store.add_outgoing(store.current_conversation().id, ME, "note", prompts.EVENING_NOTE, None, footer=FOOTER)
    store.log_initiative("evening", "2026-10-14", "sending", message_id=out, at="2026-10-14T19:00:00+00:00")
    initiative.start_up()
    assert store.message(out).status == "ignored" and outcomes(store) == [("evening", "unknown", None)]
    await ticks(initiative, clock, "2026-10-14 21:05", "2026-10-14 23:30")
    assert sender.sent == []


async def test_the_note_never_reaches_her_history_and_the_reply_is_an_ordinary_message(store: Store) -> None:
    initiative, sender, _ = make(store, evening(), "2026-10-14 21:00")
    await initiative.tick()
    assert sender.sent == [(ME, NOTE)]
    reply = said(store, "Good day. Taxes still open.", at("2026-10-14 21:10"))
    store.assign_seq(reply)
    conversation = store.current_conversation()
    messages = ContextBuilder(store, Profile(store), TZ).build(conversation)
    shown = json.dumps(messages)
    assert "What went well" not in shown and "/evening" not in shown
    assert messages[-1]["role"] == "user" and messages[-1]["content"].endswith("Good day. Taxes still open.")
    assert [m.text for m in store.window_messages(conversation.id, 0)] == ["Good day. Taxes still open."]


def test_the_log_takes_the_evening_note_and_an_older_database_moves_up(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    old = Store.open(path)
    old.log_initiative("lookback", "2026-10-11", "sent", at="2026-10-11T17:00:00+00:00")
    old.close()
    conn = sqlite3.connect(path)  # the log as schema 10 had it: no evening note
    conn.executescript(
        """BEGIN;
        CREATE TABLE before (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('followup', 'calendar', 'early_start', 'lookback', 'improvise',
                'announce')),
            local_day TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('sending', 'sent', 'unknown', 'skipped', 'failed')),
            reason TEXT,
            message_id INTEGER REFERENCES messages (id),
            replied_at REAL,
            quieted_at REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO before SELECT * FROM initiative_log;
        DROP TABLE initiative_log;
        ALTER TABLE before RENAME TO initiative_log;
        CREATE INDEX initiative_log_day ON initiative_log (local_day);
        CREATE INDEX initiative_log_message ON initiative_log (message_id);
        PRAGMA user_version = 10;
        COMMIT;"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO initiative_log (kind, local_day, outcome, created_at, updated_at)"
            " VALUES ('evening', '2026-10-14', 'sent', 'x', 'x')"
        )
    conn.close()
    store = Store.open(path)
    assert SCHEMA_VERSION == 11 and store._conn.execute("PRAGMA user_version").fetchone()[0] == 11
    assert [(e.id, e.kind, e.outcome) for e in store.initiative_entries()] == [(1, "lookback", "sent")]
    store.log_initiative("evening", "2026-10-14", "sent", at="2026-10-14T19:00:00+00:00")
    assert [e.kind for e in store.initiative_entries()] == ["lookback", "evening"]
    indexes = {row[1] for row in store._conn.execute("PRAGMA index_list(initiative_log)")}
    assert {"initiative_log_day", "initiative_log_message"} <= indexes
    store.close()


# the bridge: typed only, /help, her list, her request byte for byte as before

# her request with the evening note off, at 4e882e0 (before it) with the review's changes since (15 Oct: the projects-
# off rule's "name the command in full" and hearth's friend line; 4e882e0's: chat b1940795…, call 3f41dfa0…,
# /initiative on bc353f30…): this harness's install (the defaults, hardened): the chat's and a call's system prompt and
# her tools, then the chat's with /initiative on
BEFORE_EVENING_SHA256 = {
    "chat": "7a8cd927a99cde34af9d08c0a7b0a7c89587118d84b6407b68ec21e91cb28693",
    "call": "e355cbf5c5a4f16fb85e766e631d1a5dc3852ae7ddbc3c51ebf5b322f77a8382",  # owner, 15 Oct: asking for a joke counts
    "tools": "42afa6769c213febcdab193788d983d0543a8158c28f83d3215c5176ee06b7ae",
}
BEFORE_EVENING_INITIATIVE_ON_SHA256 = "22bef8cf4f093e9f08942838b974e0dd064b0613977e6464518bc1f499e4b69c"
# the evening note inside the line of the messages she starts, never a line of its own: a line of its own took
# video_auto from 8 of 8 to 3 of 8, this from 8 of 8 to 7 of 8 (15 Oct, run in turn, a fresh prefix each run). It says
# "on" and names /evening off: "evening note at 21:00" read as off with the rest, and asked to stop it she claimed she
# had (evening_stop 0 of 4 before, 4 of 4 with it; video_auto 4 of 4 and video_summary 4 of 4 against 2 of 4)
LINE = (
    "• Messages I start: now and then a check-in or a short note. On or off (now: {now}; evening note: on, at {time} "
    "(/evening off stops it)). Example: {example}"
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def her_request(app: App) -> dict[str, str]:
    conversation = app.store.current_conversation()
    return {
        "chat": digest(app.context.system_prompt(conversation)),
        "call": digest(app.context.system_prompt(conversation, call=True)),
        "tools": digest(json.dumps(app.tools.schemas, sort_keys=True)),
    }


def test_the_list_names_it_with_its_time_only_when_on_and_help_lists_the_command() -> None:
    assert prompts.capabilities(evening="") == prompts.capabilities()
    for initiative, now, example in ((False, "off", "/initiative on"), (True, "on", "/quiet pauses them")):
        plain = prompts.capabilities(initiative=initiative)
        lines = prompts.capabilities(initiative=initiative, evening="20:30")
        line = LINE.format(now=now, time="20:30", example=example)
        assert line in lines and len(lines) == len(plain)  # in the same place, no line of its own
        unchanged = [other for other in plain if not other.startswith("• Messages I start: ")]
        assert [other for other in lines if other != line] == unchanged
    assert "/evening on - " in prompts.HELP and "evening" in COMMANDS


async def test_off_her_request_is_byte_for_byte_as_before_and_on_her_list_names_its_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), security_level=lambda: "hardened")
    try:
        app = harness.app
        assert app.initiative is not None
        await until(lambda: "(now: hardened)" in app.context.system_prompt(app.store.current_conversation()))
        assert her_request(app) == BEFORE_EVENING_SHA256
        deliver(harness.folder, "/quiet", T0)
        await until(lambda: sent(harness.folder) == [on_phone(prompts.QUIET_DISABLED)])  # nothing of hers is on yet
        deliver(harness.folder, "/evening 20:30", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder)[1].startswith("On: three short questions about the day, every day at 20:30")
        line = LINE.format(now="off", time="20:30", example="/initiative on")
        assert line in app.capability_lines() and line in app.context.system_prompt(app.store.current_conversation())
        assert {key: her_request(app)[key] for key in ("call", "tools")} == {
            key: BEFORE_EVENING_SHA256[key] for key in ("call", "tools")
        }  # a call's rules never carry the list; no tool for it
        assert not any("evening" in json.dumps(schema) for schema in app.tools.schemas)
        deliver(harness.folder, "/help", T0 + 2)
        await until(lambda: len(sent(harness.folder)) == 3)
        assert sent(harness.folder)[2] == on_phone(prompts.help_text(app.capability_lines()))
        deliver(harness.folder, "/quiet", T0 + 3)  # with only the evening note on, /quiet pauses it
        await until(lambda: len(sent(harness.folder)) == 4)
        assert sent(harness.folder)[3] == on_phone(prompts.QUIET_ON)
        deliver(harness.folder, "/quiet off", T0 + 4)
        await until(lambda: len(sent(harness.folder)) == 5)
        assert sent(harness.folder)[4] == on_phone(prompts.QUIET_OFF.format(rules="the evening note at 20:30"))
        deliver(harness.folder, "/evening off", T0 + 5)
        await until(lambda: len(sent(harness.folder)) == 6)
        assert her_request(app) == BEFORE_EVENING_SHA256  # off: byte for byte as before
        deliver(harness.folder, "/initiative on", T0 + 6)
        await until(lambda: len(sent(harness.folder)) == 7)
        assert her_request(app)["chat"] == BEFORE_EVENING_INITIATIVE_ON_SHA256
    finally:
        await stop(harness)
