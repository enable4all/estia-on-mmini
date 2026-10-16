"""The profile's two parts on data written before them (owner, 16 Oct): an install from before keeps every fact, its
chat facts become her notes and the user's stay theirs, even past the new 4,000; moves into her notes
(`Store.demote_fact`), the change log after them, /fix and /forget across both parts, a store opened again and the
snapshot after a start. Every test counts the facts before and after each step: no path loses one."""

import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant.commands import Commands, StatusInfo
from local_assistant.context import SNAPSHOT_KEY, ContextBuilder
from local_assistant.gemma import Completion
from local_assistant.profile import Op, Profile, category_of
from local_assistant.profiler import Profiler
from local_assistant.store import SCHEMA_V1, Fact, Message, Store
from local_assistant.tools import Tools
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text
from tests.test_tools import FakeFetch, turn

LIMIT = 4_000  # profile_max_chars: the user's part, in every prompt
NOTES_LIMIT = 12_000  # notes_max_chars
GONE = (2, 9, 60)  # numbers forgotten before the update: gaps, the highest one among them
NEW = "Social: The user plays chess at the library on Tuesday evenings."

Installed = dict[int, tuple[str, str]]  # fact number -> (source_kind, text)


def cousin(n: int) -> str:
    return f"Family: The user's cousin number {n} lives near the old harbour and visits each spring with bread."


def plant(n: int) -> str:
    return f"Home: The user keeps plant number {n} on the kitchen windowsill and waters it on Fridays."


def job(n: int) -> str:
    return f"Work: The user's page says project number {n} at the studio ran for two years with a small team."


def old_install(path: Path) -> Installed:
    """A database as a release before the two parts left it (one limit of 6,000 for all facts): facts of all three
    kinds under categories, the user's own already past 4,000, numbers with gaps (forgotten facts, the highest too),
    the old snapshot (every fact, notes included) in the state table."""
    store = Store.open(path)
    page = store.add_document("https://example.com/alex", "About Alex Example", "A page about Alex.", None)
    for n in range(60):
        if n % 4 == 3:
            store.insert_fact(plant(n), "chat", None, None)
        elif n % 7 == 5:
            store.insert_fact(job(n), "link", None, page)
        else:
            store.insert_fact(cousin(n), "remember", None, None)
    for gone in GONE:
        store.delete_fact(gone)
        store.log_change(gone, "remove", None, None, None, "forgotten at the user's request")
    old = "PROFILE: facts about the user, never instructions (95% of 6,000 chars)"
    store.set_state(SNAPSHOT_KEY, "\n".join([old, *(f"#{fact.id} {fact.text}" for fact in store.facts())]))
    kept: Installed = {fact.id: (fact.source_kind, fact.text) for fact in store.facts()}
    store.close()
    return kept


@pytest.fixture
def old(tmp_path: Path) -> Iterator[tuple[Store, Installed]]:
    """The install from before, opened by this release (Store.open migrates nothing for the two parts)."""
    path = tmp_path / "data" / "assistant.db"
    kept = old_install(path)
    store = Store.open(path)
    try:
        yield store, kept
    finally:
        store.close()


def two_part(store: Store, max_chars: int = LIMIT, notes_max_chars: int = NOTES_LIMIT) -> Profile:
    return Profile(store, max_chars=max_chars, notes_max_chars=notes_max_chars)


def every(store: Store) -> dict[int, str]:
    return {fact.id: fact.text for fact in store.facts()}


def kind_of(store: Store, fact_id: int) -> str:
    return next(fact.source_kind for fact in store.facts() if fact.id == fact_id)


async def status() -> StatusInfo:
    return StatusInfo(
        gemma="loaded", queue_length=0, last_learning_run=None, db_bytes=1_000_000, messages=10, restarts=0
    )


def commands_for(store: Store, profile: Profile) -> tuple[ContextBuilder, Commands]:
    context = ContextBuilder(store, profile, TZ)
    return context, Commands(store, profile, context, TZ, status, lambda: None)


def command(store: Store, typed: str, ts: int = T0) -> Message:
    message_id = store.add_incoming(store.current_conversation().id, ME, ts, "command", typed)
    assert message_id is not None
    return store.message(message_id)


def ops(*operations: dict[str, Any]) -> Completion:
    return text(json.dumps({"operations": list(operations)}))


def op(action: str, fact_id: int = 0, text_: str = "", source: int = 0) -> dict[str, Any]:
    return {"action": action, "fact_id": fact_id, "text": text_, "reason": "room", "source_message_id": source}


def enough_to_move(profile: Profile, fact: str) -> list[Fact]:
    """The user's oldest facts that free the room a "remember" of `fact` needs."""
    over = profile.used_chars() + len(fact) - profile.max_chars
    movers: list[Fact] = []
    for mine in profile.yours():
        if sum(len(moved.text) for moved in movers) >= over:
            break
        movers.append(mine)
    return movers


# an install from before: every fact kept, in its part


def test_an_install_from_before_keeps_every_fact_in_its_own_part(old: tuple[Store, Installed]) -> None:
    store, kept = old
    profile = two_part(store)
    assert {fact.id: (fact.source_kind, fact.text) for fact in profile.facts()} == kept
    assert [fact.id for fact in profile.facts()] == sorted(kept) and not set(GONE) & set(kept)
    theirs = sorted(fid for fid, (kind, _) in kept.items() if kind in ("remember", "link"))
    assert [fact.id for fact in profile.yours()] == theirs  # in number order, links among them
    assert [fact.id for fact in profile.notes()] == sorted(fid for fid, (kind, _) in kept.items() if kind == "chat")
    assert profile.used_chars() > LIMIT  # nothing cut to fit the new limit
    assert profile.used_chars() + profile.notes_used_chars() == sum(len(t) for _, t in kept.values())


def test_after_a_start_her_prompt_holds_every_fact_of_the_users_part_even_past_its_limit(
    old: tuple[Store, Installed],
) -> None:
    """The old release's snapshot (every fact, notes too) stays until the start refreshes it (App.run); then her
    prompt has every one of the user's facts under its category, no note's text, the true fill level past 100%, and
    the line that counts her notes."""
    store, kept = old
    profile = two_part(store)
    context = ContextBuilder(store, profile, TZ)
    assert plant(3) in context.snapshot()  # the old release's, as stored
    fresh = context.refresh_snapshot()
    assert context.snapshot() == fresh and plant(3) not in fresh
    for fid, (kind, fact_text) in kept.items():
        category = category_of(fact_text)
        assert category is not None
        if kind == "chat":
            assert category[1] not in fresh
        else:
            assert f"#{fid} {category[1]}" in fresh
    lines = fresh.splitlines()
    filled = re.search(r"\((\d+)% of 4,000 chars\)", lines[0])
    assert filled is not None and int(filled.group(1)) == round(100 * profile.used_chars() / LIMIT) > 100
    assert {"Family:", "Work:"} <= set(lines) and "Home:" not in lines  # the user's categories as headings
    assert lines[-1] == prompts.NOTES_IN_SNAPSHOT.format(count=len(profile.notes()))
    conversation = store.current_conversation()
    store.add_incoming(conversation.id, ME, T0, "text", "Which plant do I water on Fridays?")
    built = context.build(conversation)
    assert fresh in built[0]["content"] and plant(3) not in built[0]["content"]
    three = next(fid for fid, (_, fact_text) in kept.items() if fact_text == plant(3))
    assert f"#{three} {plant(3)}" in built[-1]["content"]  # the note comes with the message it matters for


def test_learning_on_an_install_from_before_adds_notes_and_never_touches_the_users_facts(
    old: tuple[Store, Installed],
) -> None:
    store, kept = old
    profile = two_part(store)
    first, second = profile.yours()[:2]
    a_note = profile.notes()[0]
    before = every(store)
    added = profile.apply(
        [Op("add", text="About: The user is learning to sail."), Op("remove", fact_id=first.id)], source_kind="chat"
    )
    assert len(added.added) == 1 and added.kept == [first.id]
    assert every(store) == {**before, added.added[0]: "About: The user is learning to sail."}
    merged = [Op("replace", fact_id=second.id, text=f"{second.text} Also a baker."), Op("remove", fact_id=a_note.id)]
    held = profile.apply(merged, source_kind="chat")  # a merge into the user's fact: its removes are held back
    assert held.kept == [second.id] and not held.removed and [op_.fact_id for op_, _ in held.rejected] == [a_note.id]
    assert every(store) == {**before, added.added[0]: "About: The user is learning to sail."}
    assert kind_of(store, first.id) == kept[first.id][0] and kind_of(store, a_note.id) == "chat"


# a "remember" in a full part: what makes room moves into her notes, never gone


async def test_a_remember_on_an_install_past_the_limit_moves_enough_into_her_notes_and_loses_nothing(
    old: tuple[Store, Installed],
) -> None:
    store, kept = old
    profile = two_part(store)
    gemma = FakeGemma()
    profiler = Profiler(store, profile, ContextBuilder(store, profile, TZ), gemma, TZ)
    tools = Tools(store, profile, TZ, FakeFetch())
    tools.set_consolidate(profiler.consolidate_for)
    movers = enough_to_move(profile, NEW)
    before = every(store)
    current = turn(store, "remember that I play chess at the library on Tuesdays")
    gemma.add(ops(*(op("remove", mover.id) for mover in movers), op("add", text_=NEW, source=current.message.id)))
    reply = await tools.call(current, "remember", json.dumps({"fact": NEW}))
    numbers = ", ".join(f"#{mover.id}" for mover in movers)
    assert reply.startswith("Saved as fact #61.") and prompts.MOVED_NOTE.format(numbers=numbers) in reply
    assert every(store) == {**before, 61: NEW}  # a new number past the highest ever used; every text kept
    assert [moved.id for moved in profile.notes() if moved.id in {m.id for m in movers}] == [m.id for m in movers]
    assert profile.used_chars() <= LIMIT
    assert current.moved == [(mover.id, mover.text) for mover in movers] and not current.removed
    shown = gemma.calls[0]["messages"][1]["content"]
    assert all(plant(n) not in shown for n in range(3, 60, 4))  # only the user's part was shown
    for mover in movers:
        last = store.changes(mover.id)[-1]
        assert (last.action, last.old_text, last.new_text, last.reason) == (
            "replace",
            mover.text,
            mover.text,
            prompts.MOVED_TO_NOTES,
        )
        # moved with its origin kept (schema v13): still the user's by origin, now in her notes
        assert kind_of(store, mover.id) == kept[mover.id][0] and kept[mover.id][1] == mover.text
        assert mover.id in {fact.id for fact in profile.notes()}


async def test_a_tidy_up_that_frees_too_little_writes_nothing(old: tuple[Store, Installed]) -> None:
    store, _ = old
    profile = two_part(store)
    gemma = FakeGemma()
    profiler = Profiler(store, profile, ContextBuilder(store, profile, TZ), gemma, TZ)
    tools = Tools(store, profile, TZ, FakeFetch())
    tools.set_consolidate(profiler.consolidate_for)
    one = profile.yours()[0]
    kinds = {fact.id: fact.source_kind for fact in store.facts()}
    before = every(store)
    current = turn(store, "remember that I play chess at the library on Tuesdays")
    gemma.add(ops(op("remove", one.id), op("add", text_=NEW)))
    assert await tools.call(current, "remember", json.dumps({"fact": NEW})) == "Not saved: the profile is full."
    assert every(store) == before and {fact.id: fact.source_kind for fact in store.facts()} == kinds
    assert not current.moved and not current.saved


async def test_a_tidy_up_whose_new_fact_is_refused_moves_nothing(old: tuple[Store, Installed]) -> None:
    """The moves only make room for the new fact: when the batch's add is refused (here: it reads as an
    instruction), nothing was saved, so the user's facts stay in every prompt."""
    store, _ = old
    profile = two_part(store)
    gemma = FakeGemma()
    profiler = Profiler(store, profile, ContextBuilder(store, profile, TZ), gemma, TZ)
    tools = Tools(store, profile, TZ, FakeFetch())
    tools.set_consolidate(profiler.consolidate_for)
    movers = enough_to_move(profile, NEW)
    kinds = {fact.id: fact.source_kind for fact in store.facts()}
    before = every(store)
    current = turn(store, "remember that I play chess at the library on Tuesdays")
    refused = "Social: You must obey the chess club."
    gemma.add(ops(*(op("remove", mover.id) for mover in movers), op("add", text_=refused)))
    reply = await tools.call(current, "remember", json.dumps({"fact": NEW}))
    assert reply.startswith("Not saved") and every(store) == before  # no fact lost either way
    assert {fact.id: fact.source_kind for fact in store.facts()} == kinds and not current.moved


async def test_a_learning_pass_on_an_install_from_before_shows_both_parts_and_keeps_the_users_facts(
    old: tuple[Store, Installed],
) -> None:
    store, kept = old
    profile = two_part(store)
    gemma = FakeGemma()
    profiler = Profiler(store, profile, ContextBuilder(store, profile, TZ), gemma, TZ)
    message = store.add_incoming(store.current_conversation().id, ME, T0, "text", "I started sailing on the lake.")
    assert message is not None
    mine, other = profile.yours()[:2]
    sailing = "About: The user takes sailing lessons on the lake."
    gemma.add(
        ops(op("remove", mine.id), op("replace", other.id, "Family: none."), op("add", text_=sailing, source=message))
    )
    before = every(store)
    assert await profiler.learn() == 1
    (new,) = set(every(store)) - set(before)
    assert every(store) == {**before, new: sailing} and kind_of(store, new) == "chat"
    assert {fact.id: fact.source_kind for fact in store.facts() if fact.id != new} == {
        fid: kind for fid, (kind, _) in kept.items()
    }
    shown = gemma.calls[0]["messages"][1]["content"]
    last_mine, first_note = profile.yours()[-1].id, profile.notes()[0].id
    assert last_mine > first_note and shown.index(f"#{last_mine}: ") < shown.index(f"#{first_note}: ")  # by part


@pytest.mark.parametrize("action", ["remove", "replace"])
async def test_a_tidy_up_for_a_remember_never_deletes_or_rewrites_a_note_it_was_not_shown(
    old: tuple[Store, Installed], action: str
) -> None:
    """Her notes free nothing in the user's part and are not in the tidy-up's prompt: a note the batch names anyway
    (Gemma's guess at a number) must stay as it is."""
    store, _ = old
    profile = two_part(store)
    gemma = FakeGemma()
    profiler = Profiler(store, profile, ContextBuilder(store, profile, TZ), gemma, TZ)
    a_note = profile.notes()[0]
    movers = enough_to_move(profile, NEW)
    before = every(store)
    message = store.add_incoming(store.current_conversation().id, ME, T0, "text", "remember that I play chess")
    assert message is not None
    on_note = op(action, a_note.id, "Home: The user has no plants." if action == "replace" else "")
    gemma.add(ops(*(op("remove", mover.id) for mover in movers), on_note, op("add", text_=NEW, source=message)))
    result = await profiler.consolidate_for(NEW, message, "remember", None)
    assert result.added
    assert every(store) == {**before, result.added[0]: NEW}  # the note too, unchanged


def test_a_move_and_a_replace_of_the_same_fact_in_one_batch_keep_the_new_text_in_her_notes(store: Store) -> None:
    for first in ("remove", "replace"):
        profile = two_part(store, max_chars=120)
        mine = profile.add(f"Family: The user's aunt visits in May ({first}).", source_kind="remember").added[0]
        other = profile.add(f"Home: The user lives on a quiet street ({first}).", source_kind="remember").added[0]
        shorter = f"Family: The user's aunt visits ({first})."
        remove, replace = Op("remove", fact_id=mine), Op("replace", fact_id=mine, text=shorter)
        batch = [remove, replace] if first == "remove" else [replace, remove]
        new = f"About: The user is left-handed ({first})."
        before = every(store)
        result = profile.apply([*batch, Op("add", text=new)], source_kind="remember", remove_to_notes=True)
        assert result.moved == [mine] and result.replaced == [mine] and len(result.added) == 1
        assert every(store) == {**before, mine: shorter, result.added[0]: new}
        assert kind_of(store, mine) == "remember" and kind_of(store, other) == "remember"  # the origin kept
        assert mine in {fact.id for fact in profile.notes()} and other in {fact.id for fact in profile.yours()}
        for fact in store.facts():
            store.delete_fact(fact.id)


def test_a_move_may_take_her_notes_past_their_limit_and_learning_then_waits_without_losing_a_fact(
    store: Store,
) -> None:
    """Each part checks only its own limit: a move into her notes is never refused for their room (the user is
    waiting), and her learning then has to tidy first; nothing is lost either way."""
    profile = two_part(store, max_chars=110, notes_max_chars=100)
    mine = profile.add("Family: The user's brother Sam lives in the mountains with two dogs.", source_kind="remember")
    profile.add("Home: The user waters the basil on Fridays and the fern on Mondays.", source_kind="chat")
    moved = profile.apply(
        [Op("remove", fact_id=mine.added[0]), Op("add", text=NEW)], source_kind="remember", remove_to_notes=True
    )
    assert moved.moved == mine.added and profile.notes_used_chars() > profile.notes_max_chars
    before = every(store)
    waits = profile.add("About: The user likes long walks.", source_kind="chat")
    assert waits.over_cap_by > 0 and not waits.added and every(store) == before


# Store.demote_fact, own_fact and a store opened again


def test_demote_fact_keeps_number_text_and_page_and_a_reopened_store_keeps_both_parts(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    store = Store.open(path)
    page = store.add_document("https://example.com/alex", "About Alex Example", "A page about Alex.", None)
    linked = store.insert_fact(job(1), "link", None, page)
    mine = store.insert_fact(cousin(2), "remember", None, None)
    noted = store.insert_fact(plant(3), "chat", None, None)
    store.demote_fact(linked)
    store.demote_fact(noted)  # a note stays a note
    store.demote_fact(999)  # no such fact: nothing happens
    facts = [(fact.id, fact.text, fact.source_kind, fact.source_document_id, fact.in_notes) for fact in store.facts()]
    assert facts == [  # moved with its origin kept (schema v13): a page fact stays a page fact
        (linked, job(1), "link", page, True),
        (mine, cousin(2), "remember", None, False),
        (noted, plant(3), "chat", None, True),
    ]
    store.close()
    again = Store.open(path)
    try:
        assert [(f.id, f.text, f.source_kind, f.source_document_id, f.in_notes) for f in again.facts()] == facts
        profile = two_part(again)
        assert [f.id for f in profile.yours()] == [mine] and [f.id for f in profile.notes()] == [linked, noted]
        assert again.insert_fact(NEW, "remember", None, None) == noted + 1
    finally:
        again.close()


def test_own_fact_on_a_note_from_a_page_makes_it_the_users_and_drops_the_page(store: Store) -> None:
    page = store.add_document("https://example.com/alex", "About Alex Example", "A page about Alex.", None)
    linked = store.insert_fact(job(1), "link", None, page)
    store.demote_fact(linked)
    fix = command(store, "/fix 1 Work: The user ran a project at the studio.")
    store.own_fact(linked, "Work: The user ran a project at the studio.", fix.id)
    (fact,) = store.facts()
    assert (fact.id, fact.source_kind, fact.source_message_id, fact.source_document_id) == (
        linked,
        "remember",
        fix.id,
        None,
    )
    assert [f.id for f in two_part(store).yours()] == [linked]


def test_numbers_stay_in_order_and_are_never_reused_across_moves_fixes_and_a_reopen(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    kept = old_install(path)
    store = Store.open(path)
    roomy = two_part(store, max_chars=20_000)
    theirs = [fid for fid, (kind, _) in sorted(kept.items()) if kind != "chat"]
    notes = [fid for fid, (kind, _) in sorted(kept.items()) if kind == "chat"]
    store.demote_fact(theirs[3])  # a move (making room moves only alongside a fact it saves)
    assert roomy.fix(notes[0], "Home: The user keeps a fern by the window.") is None
    assert len(store.facts()) == len(kept)
    store.close()
    store = Store.open(path)
    try:
        profile = two_part(store)
        ids = [fact.id for fact in profile.facts()]
        assert ids == sorted(kept)
        yours, mine = [f.id for f in profile.yours()], [f.id for f in profile.notes()]
        assert yours == sorted(set(theirs) - {theirs[3]} | {notes[0]}) and mine == sorted(
            set(notes) - {notes[0]} | {theirs[3]}
        )
        assert sorted(yours + mine) == ids
        assert store.insert_fact(NEW, "remember", None, None) == max(GONE) + 1  # 60 was forgotten: never reused
    finally:
        store.close()


def test_an_install_from_version_1_keeps_each_facts_kind_and_so_its_part(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.executescript(f"BEGIN;\n{SCHEMA_V1}\nPRAGMA user_version = 1;\nCOMMIT;")
    now = "2026-09-01T10:00:00+00:00"
    for fid, kind, fact_text in ((1, "remember", cousin(1)), (4, "chat", plant(4)), (6, "link", job(6))):
        conn.execute(
            "INSERT INTO profile_facts (id, text, source_kind, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (fid, fact_text, kind, now, now),
        )
    conn.execute(
        "INSERT INTO profile_changes (fact_id, action, created_at, updated_at) VALUES (9, 'remove', ?, ?)", (now, now)
    )
    conn.commit()
    conn.close()
    store = Store.open(path)
    try:
        profile = two_part(store)
        assert [(f.id, f.text) for f in profile.yours()] == [(1, cousin(1)), (6, job(6))]
        assert [(f.id, f.text) for f in profile.notes()] == [(4, plant(4))]
        assert profile.add(NEW, source_kind="remember").added == [10]  # 9 is in the change log: never reused
    finally:
        store.close()


# /fix and /forget across both parts and on moved facts


async def test_fix_of_a_note_is_refused_while_the_users_part_is_past_its_limit(old: tuple[Store, Installed]) -> None:
    store, kept = old
    profile = two_part(store)
    context, commands = commands_for(store, profile)
    snapshot = context.refresh_snapshot()
    a_note = profile.notes()[0]
    before = every(store)
    reply = await commands.handle(command(store, f"/fix {a_note.id} Home: The user keeps a fern."))
    assert reply.startswith("Not fixed: your part of the profile would be")
    assert every(store) == before and kind_of(store, a_note.id) == "chat"
    assert context.snapshot() == snapshot and kept[a_note.id] == ("chat", a_note.text)


async def test_a_fix_that_shortens_the_users_own_fact_works_while_their_part_is_past_its_limit(
    old: tuple[Store, Installed],
) -> None:
    store, _ = old
    profile = two_part(store)
    _, commands = commands_for(store, profile)
    mine = profile.yours()[0]
    used = profile.used_chars()
    reply = await commands.handle(command(store, f"/fix {mine.id} The user's cousin lives near the harbour."))
    assert reply == prompts.FIX_DONE.format(number=mine.id, text="Family: The user's cousin lives near the harbour.")
    assert profile.used_chars() < used


async def test_fix_of_a_moved_fact_makes_it_the_users_again_with_its_number_and_category(store: Store) -> None:
    profile = two_part(store, max_chars=150)
    aunt = profile.add(
        "Family: The user's aunt Ruth visits every May from the coast.", source_kind="remember", reason="the user asked"
    )
    profile.add("Home: The user lives on a quiet street.", source_kind="remember")
    moved = profile.apply(
        [Op("remove", fact_id=aunt.added[0]), Op("add", text=NEW)], source_kind="remember", remove_to_notes=True
    )
    assert moved.moved == aunt.added
    fid = aunt.added[0]
    before = every(store)
    tight = two_part(store, max_chars=200)
    _, commands = commands_for(store, tight)
    reply = await commands.handle(command(store, f"/fix {fid} The user's aunt Ruth visits in May."))
    assert reply == prompts.FIX_DONE.format(number=fid, text="Family: The user's aunt Ruth visits in May.")
    assert every(store) == {**before, fid: "Family: The user's aunt Ruth visits in May."}
    assert kind_of(store, fid) == "remember"
    reasons = [change.reason for change in store.changes(fid)]
    assert reasons == ["the user asked", prompts.MOVED_TO_NOTES, "fixed by the user (/fix)"]


async def test_forget_works_in_both_parts_and_on_a_moved_fact_and_blanks_its_whole_log(store: Store) -> None:
    profile = two_part(store, max_chars=150)
    context, commands = commands_for(store, profile)
    aunt = profile.add("Family: The user's aunt Ruth visits every May from the coast.", source_kind="remember")
    street = profile.add("Home: The user lives on a quiet street.", source_kind="remember").added[0]
    fern = profile.add("Home: The user keeps a fern by the window.", source_kind="chat").added[0]
    profile.apply(
        [Op("remove", fact_id=aunt.added[0]), Op("add", text=NEW)], source_kind="remember", remove_to_notes=True
    )
    moved = aunt.added[0]
    count = len(store.facts())
    for n, fid in enumerate((moved, fern, street), start=1):
        gone = every(store)[fid]
        reply = await commands.handle(command(store, f"/forget {fid}", T0 + n))
        assert reply == f"Forgotten: #{fid} {gone}"
        assert len(store.facts()) == count - n and fid not in every(store)
        assert all(change.old_text is None and change.new_text is None for change in store.changes(fid))
        assert gone.split(": ", 1)[1] not in context.snapshot()
    assert [fact.text for fact in profile.facts()] == [NEW]
    assert prompts.NOTES_IN_SNAPSHOT.format(count=0) not in context.snapshot()


async def test_forget_by_words_sees_both_parts(store: Store) -> None:
    profile = two_part(store)
    _, commands = commands_for(store, profile)
    sister = profile.add("Family: The user's sister Anna lives in Lisbon.", source_kind="remember").added[0]
    coffee = profile.add("Social: The user meets Anna for coffee on Sundays.", source_kind="chat").added[0]
    before = every(store)
    reply = await commands.handle(command(store, "/forget anna"))
    assert reply.startswith("Several facts match") and f"#{sister} " in reply and f"#{coffee} " in reply
    assert every(store) == before


async def test_profile_and_status_show_an_install_past_the_limit_as_it_is(old: tuple[Store, Installed]) -> None:
    store, kept = old
    profile = two_part(store)
    _, commands = commands_for(store, profile)
    shown = await commands.handle(command(store, "/profile"))
    assert shown.startswith(prompts.PROFILE_HEADER.format(used=profile.used_chars(), limit=LIMIT))
    notes_header = prompts.PROFILE_NOTES_HEADER.format(used=profile.notes_used_chars(), limit=NOTES_LIMIT)
    yours_part, notes_part = shown.split(notes_header)
    for fid, (kind, _) in kept.items():
        assert f"#{fid} " in (notes_part if kind == "chat" else yours_part)
    line = next(
        row for row in (await commands.handle(command(store, "/status", T0 + 1))).splitlines() if "Profile:" in row
    )
    mine = round(100 * profile.used_chars() / LIMIT)
    assert mine > 100 and f"{len(profile.yours())} facts of yours, {mine}% full" in line
    assert f"{len(profile.notes())} notes from chats" in line


async def test_a_moved_fact_is_never_shown_as_learned_from_chat(store: Store) -> None:
    profile = two_part(store, max_chars=150)
    _, commands = commands_for(store, profile)
    aunt = profile.add("Family: The user's aunt Ruth visits every May from the coast.", source_kind="remember")
    profile.apply(
        [Op("remove", fact_id=aunt.added[0]), Op("add", text=NEW)], source_kind="remember", remove_to_notes=True
    )
    shown = await commands.handle(command(store, "/profile"))
    line = next(row for row in shown.splitlines() if row.startswith(f"#{aunt.added[0]} "))
    assert "learned from chat" not in line


# what the user asks to remember is theirs, also when she already noted it


def test_a_remember_of_what_she_has_as_a_note_makes_it_the_users(store: Store) -> None:
    profile = two_part(store)
    fact = "Family: The user's sister Anna lives in Lisbon."
    profile.add(fact, source_kind="chat")
    before = set(every(store).values())
    profile.add(fact, source_kind="remember")
    assert fact in [mine.text for mine in profile.yours()]
    assert before <= set(every(store).values())


# a chat batch that merges a note into another it also removes


def test_a_merge_into_a_note_the_batch_then_removes_keeps_its_text(store: Store) -> None:
    profile = two_part(store)
    short = profile.add("Home: The user has two cats.", source_kind="chat").added[0]
    whole = "Home: The user has two cats named Pip and Tom."
    full = profile.add(whole, source_kind="chat").added[0]
    profile.apply([Op("replace", fact_id=short, text=whole), Op("remove", fact_id=full)], source_kind="chat")
    assert whole in every(store).values()


def test_a_restart_after_a_move_shows_the_moved_fact_as_a_note_only(tmp_path: Path) -> None:
    """Between two starts the snapshot is frozen (the moved fact still in her prompt); after the restart it is in her
    notes only: counted in the snapshot's last line and sent with a message it matters for."""
    path = tmp_path / "data" / "assistant.db"
    store = Store.open(path)
    profile = two_part(store, max_chars=150)
    context = ContextBuilder(store, profile, TZ)
    aunt = profile.add("Family: The user's aunt Ruth visits every May from the coast.", source_kind="remember")
    profile.add("Home: The user lives on a quiet street.", source_kind="remember")
    frozen = context.refresh_snapshot()
    profile.apply(
        [Op("remove", fact_id=aunt.added[0]), Op("add", text=NEW)], source_kind="remember", remove_to_notes=True
    )
    assert context.snapshot() == frozen and "aunt Ruth" in frozen
    count = len(store.facts())
    store.close()
    store = Store.open(path)
    try:
        profile = two_part(store, max_chars=150)
        context = ContextBuilder(store, profile, TZ)
        assert context.snapshot() == frozen  # what the state table kept, until the start refreshes it
        fresh = context.refresh_snapshot()
        assert "aunt Ruth" not in fresh and "chess at the library" in fresh
        assert fresh.endswith(prompts.NOTES_IN_SNAPSHOT.format(count=1)) and len(store.facts()) == count
        assert f"#{aunt.added[0]} " in profile.notes_block("When does Ruth visit?")
    finally:
        store.close()
