"""The profile's two parts at their limits (owner, 16 Oct): `Profile.apply` checks only the part a batch writes (the
user's part for "remember" and "link", her notes for "chat"), making room for a "remember" moves the user's facts into
her notes (`remove_to_notes`), and the learning pass, `consolidate_for` and the remember tool build on that math."""

import json
import logging
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant.brain import profile_notice
from local_assistant.context import ContextBuilder
from local_assistant.gemma import Completion
from local_assistant.profile import ApplyResult, Op, Profile
from local_assistant.profiler import LAST_LEARNED_KEY, Profiler
from local_assistant.store import SourceKind, Store
from local_assistant.tools import Tools, Turn
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text
from tests.test_tools import FakeFetch

MIA = "The user's daughter Mia plays the violin."  # 41 characters
CITY = "The user lives in Amsterdam, NL."  # 32
NUTS = "The user is allergic to nuts."  # 29
DAUGHTERS = "The user has two daughters."  # 27
PAGE = "https://example.org/alex"


def sized(tag: str, n: int) -> str:
    """A fact of exactly n characters that every check lets through (no long word, no "you")."""
    body = f"Fact {tag} about the user" + " la" * 120
    fact = body[: n - 1] + "."
    assert len(fact) == n
    return fact


def two_part(store: Store, max_chars: int = 100, notes_max_chars: int = 100) -> Profile:
    return Profile(store, max_chars=max_chars, notes_max_chars=notes_max_chars)


def over_from_before(store: Store, limit: int, facts: list[str]) -> Profile:
    """An install from before: the user's facts were saved under a larger limit, so their part is over `limit`."""
    roomy = two_part(store, max_chars=10_000, notes_max_chars=10_000)
    for fact in facts:
        assert roomy.add(fact, source_kind="remember").added
    tight = two_part(store, max_chars=limit, notes_max_chars=10_000)
    assert tight.used_chars() > limit
    return tight


def ops(*operations: dict[str, Any]) -> Completion:
    return text(json.dumps({"operations": list(operations)}))


def op(action: str, fact_id: int = 0, text_: str = "", source_message_id: int = 0) -> dict[str, Any]:
    return {
        "action": action,
        "fact_id": fact_id,
        "text": text_,
        "reason": "test",
        "source_message_id": source_message_id,
    }


def profiler_for(store: Store, profile: Profile, gemma: FakeGemma) -> Profiler:
    return Profiler(store, profile, ContextBuilder(store, profile, TZ), gemma, TZ, poll_seconds=0.01)


def user(store: Store, text_: str, ts: int = T0) -> int:
    message_id = store.add_incoming(store.current_conversation().id, ME, ts, "text", text_)
    assert message_id is not None
    return message_id


def turn(store: Store, text_: str, ts: int = T0 + 100_000) -> Turn:
    return Turn(store.message(user(store, text_, ts)))


# exact boundaries: a part exactly at its limit fits, one character more is over by one and writes nothing


@pytest.mark.parametrize("kind", ["remember", "link"])
def test_the_users_part_fits_exactly_at_its_limit_and_one_more_is_over_by_one(store: Store, kind: SourceKind) -> None:
    profile = two_part(store, max_chars=100, notes_max_chars=10)
    profile.add(sized("n", 90), source_kind="chat")  # her notes far over their own limit: not this part's business
    assert profile.add(sized("a", 60), source_kind=kind).added
    over = profile.add(sized("b", 41), source_kind=kind)
    assert over.over_cap_by == 1 and not over.added and profile.used_chars() == 60
    assert profile.add(sized("c", 40), source_kind=kind).added
    assert profile.used_chars() == 100 == profile.max_chars


def test_her_notes_fit_exactly_at_their_limit_and_one_more_is_over_by_one(store: Store) -> None:
    profile = over_from_before(store, 50, [sized("u1", 90)])  # the user's part over its limit: not this part's
    notes = two_part(store, max_chars=50, notes_max_chars=100)
    assert notes.add(sized("a", 60), source_kind="chat").added
    over = notes.add(sized("b", 41), source_kind="chat")
    assert over.over_cap_by == 1 and not over.added
    assert notes.add(sized("c", 40), source_kind="chat").added
    assert notes.notes_used_chars() == 100 and profile.used_chars() == 90


def test_a_replace_counts_its_new_length_both_when_it_grows_and_when_it_makes_room(store: Store) -> None:
    profile = two_part(store, max_chars=100)
    first = profile.add(sized("a", 50), source_kind="remember").added[0]
    profile.add(sized("b", 30), source_kind="remember")
    grow = profile.apply([Op("replace", fact_id=first, text=sized("a2", 71))], source_kind="remember")
    assert grow.over_cap_by == 1 and profile.used_chars() == 80  # 71 + 30: nothing written
    assert profile.apply([Op("replace", fact_id=first, text=sized("a3", 70))], source_kind="remember").replaced
    shrink_and_add = [Op("replace", fact_id=first, text=sized("a4", 20)), Op("add", text=sized("d", 50))]
    result = profile.apply(shrink_and_add, source_kind="remember")
    assert result.replaced == [first] and result.added and profile.used_chars() == 100


def test_a_chat_replace_of_a_note_while_the_users_part_is_over_counts_only_her_notes(store: Store) -> None:
    profile = over_from_before(store, 40, [MIA, CITY])
    note = profile.add(sized("n", 30), source_kind="chat").added[0]
    result = profile.apply([Op("replace", fact_id=note, text=sized("n2", 100))], source_kind="chat")
    assert result.replaced == [note] and profile.notes_used_chars() == 100
    assert [fact.text for fact in profile.yours()] == [MIA, CITY]  # still over their limit: kept, not cut
    assert profile.add(sized("m", 300), source_kind="chat").added  # her notes' own limit here is 10,000


# making room: adds and moves in one batch, all or nothing


def test_adds_and_moves_in_one_batch_fit_exactly_and_one_short_moves_nothing(store: Store) -> None:
    profile = two_part(store, max_chars=100, notes_max_chars=10)
    first = profile.add(sized("a", 50), source_kind="remember").added[0]
    second = profile.add(sized("b", 50), source_kind="remember").added[0]
    short = profile.apply(
        [Op("remove", fact_id=first), Op("add", text=sized("c", 51))], source_kind="remember", remove_to_notes=True
    )
    assert short.over_cap_by == 1 and not short.moved and not short.added
    assert [fact.id for fact in profile.yours()] == [first, second] and profile.notes() == []
    fits = profile.apply(
        [Op("remove", fact_id=first), Op("add", text=sized("c", 50))], source_kind="remember", remove_to_notes=True
    )
    assert fits.moved == [first] and fits.added and not fits.removed and fits.changed
    assert profile.used_chars() == 100
    assert profile.notes_used_chars() == 50 > profile.notes_max_chars  # a move may push her notes over: allowed
    assert [fact.id for fact in profile.notes()] == [first]
    assert [change.reason for change in store.changes(first)][-1] == prompts.MOVED_TO_NOTES


def test_moving_a_fact_whose_text_was_replaced_in_the_same_batch_moves_the_new_text(store: Store) -> None:
    profile = two_part(store, max_chars=80)
    first = profile.add(MIA, source_kind="remember").added[0]
    profile.add(CITY, source_kind="remember")
    batch = [
        Op("replace", fact_id=first, text="Mia plays the violin."),
        Op("remove", fact_id=first),
        Op("add", text=NUTS),
    ]
    result = profile.apply(batch, source_kind="remember", remove_to_notes=True)
    assert result.moved == [first] and result.added
    assert [(fact.id, fact.text) for fact in profile.notes()] == [(first, "Mia plays the violin.")]
    assert [fact.text for fact in profile.yours()] == [CITY, NUTS]
    assert all(change.old_text == MIA for change in store.changes(first)[1:])  # the log keeps where it came from


def test_a_move_and_a_duplicate_replace_of_the_same_fact_never_crash(store: Store) -> None:
    """remove_to_notes keeps a moved fact in `final`; a later replace of it with another fact's words deletes it from
    `final` as a merge, and the write loop then reads `final[fact_id]` for the move: KeyError."""
    profile = two_part(store, max_chars=80)
    first = profile.add(MIA, source_kind="remember").added[0]
    profile.add(CITY, source_kind="remember")
    batch = [Op("remove", fact_id=first), Op("replace", fact_id=first, text=CITY), Op("add", text=NUTS)]
    result = profile.apply(batch, source_kind="remember", remove_to_notes=True)
    assert result.added and first in result.moved + result.removed
    assert CITY in [fact.text for fact in profile.yours()]


# duplicates against the other part


def test_a_duplicate_is_found_across_both_parts(store: Store) -> None:
    profile = two_part(store, max_chars=200, notes_max_chars=200)
    mine = profile.add(MIA, source_kind="remember").added[0]
    note = profile.add(CITY, source_kind="chat").added[0]
    assert profile.add("the user's daughter mia plays the violin", source_kind="chat").duplicates  # a note of theirs
    assert profile.add(MIA, source_kind="remember").duplicates
    merged = profile.apply([Op("replace", fact_id=note, text=MIA)], source_kind="chat")
    assert merged.removed == [note] and merged.duplicates == [MIA]  # the note merged into the user's own fact
    assert [(fact.id, fact.text) for fact in profile.facts()] == [(mine, MIA)]


async def test_a_remember_of_what_is_only_a_note_makes_it_the_users_own(store: Store) -> None:
    """The user asks her to remember what she had only noted: it must become theirs (in every prompt, never changed
    by learning), not stay a note that the next learning pass may remove."""
    profile = two_part(store, max_chars=200, notes_max_chars=200)
    note = profile.add(CITY, source_kind="chat").added[0]
    tools = Tools(store, profile, TZ, FakeFetch())
    current = turn(store, "remember that I live in Amsterdam")
    await tools.call(current, "remember", json.dumps({"fact": CITY}))
    own = [fact.id for fact in profile.yours() if fact.text == CITY]
    assert len(own) == 1 and CITY not in [fact.text for fact in profile.notes()]
    assert profile.apply([Op("remove", fact_id=own[0])], source_kind="chat").kept == own  # learning cannot take it
    assert note in own or note not in [fact.id for fact in profile.facts()]


# held-back removes and link facts


def test_held_back_removes_free_no_room_in_her_notes(store: Store) -> None:
    """A learning batch that merged into the user's own fact keeps its removes back, so they free nothing."""
    profile = two_part(store, max_chars=200, notes_max_chars=80)
    note = profile.add(sized("n", 40), source_kind="chat").added[0]
    mine = profile.add(MIA, source_kind="remember").added[0]
    batch = [
        Op("replace", fact_id=mine, text=MIA[:-1] + " and the piano."),
        Op("remove", fact_id=note),
        Op("add", text=sized("m", 41)),
    ]
    result = profile.apply(batch, source_kind="chat")
    assert result.kept == [mine] and result.over_cap_by == 1  # 40 kept back + 41 new
    assert [r for o, r in result.rejected if o.fact_id == note] == [
        "held back: its batch merged into the user's own fact"
    ]
    assert [fact.text for fact in profile.facts()] == [sized("n", 40), MIA]


def test_link_facts_are_checked_strictly_and_count_in_the_users_part(store: Store) -> None:
    profile = two_part(store, max_chars=100, notes_max_chars=10)
    addressed = profile.add("Your sister is called Anna.", source_kind="link", allowed_url=PAGE)
    foreign = profile.add("The user writes at https://example.net/blog", source_kind="link", allowed_url=PAGE)
    assert addressed.rejected[0][1] == "addresses the assistant, not a fact about the user"
    assert foreign.rejected[0][1] == "link not from the learned page"
    own = profile.add(f"The user's page is {PAGE}", source_kind="link", allowed_url=PAGE).added[0]
    assert [fact.id for fact in profile.yours()] == [own]
    room = profile.max_chars - profile.used_chars()
    page = [Op("add", text=sized("p1", room - 20)), Op("add", text=sized("p2", 21))]
    assert profile.apply(page, source_kind="link", allowed_url=PAGE).over_cap_by == 1  # one batch: nothing written
    assert len(profile.facts()) == 1
    kept = profile.apply([Op("replace", fact_id=own, text="The user blogs.")], source_kind="chat")
    assert kept.kept == [own] and profile.yours()[0].source_kind == "link"


# her notes over their limit after a move, and the next learning pass


def moved_over(store: Store, notes_max_chars: int) -> tuple[Profile, int, int, int]:
    """Her notes pushed over their limit by a move: note #1 (CITY), MIA moved from the user's part, NUTS theirs."""
    profile = two_part(store, max_chars=60, notes_max_chars=notes_max_chars)
    note = profile.add(CITY, source_kind="chat").added[0]
    mia = profile.add(MIA, source_kind="remember").added[0]
    moved = profile.apply(
        [Op("remove", fact_id=mia), Op("add", text=NUTS)], source_kind="remember", remove_to_notes=True
    )
    assert moved.moved == [mia] and profile.notes_used_chars() == 73 > notes_max_chars
    return profile, note, mia, moved.added[0]


async def test_after_a_move_the_next_learning_pass_asks_once_with_her_notes_limit(store: Store) -> None:
    """A fact moved out of the user's part keeps its origin: learning may not remove it (the remove of NUTS is kept;
    a replace of MIA would hold the batch's removes back, as any merge into the user's own fact does), so the room
    comes from her own note."""
    profile, note, mia, nuts = moved_over(store, notes_max_chars=70)
    message = user(store, "I also have two daughters.")
    gemma = FakeGemma(
        ops(op("add", text_=DAUGHTERS, source_message_id=message)),
        ops(
            op("remove", fact_id=note),
            op("remove", fact_id=mia),  # the user's own, moved: kept
            op("remove", fact_id=nuts),  # the user's own: kept, whatever the answer says
            op("add", text_=DAUGHTERS, source_message_id=message),
        ),
    )
    assert await profiler_for(store, profile, gemma).learn() == 2
    assert gemma.calls[1]["messages"][-1]["content"] == prompts.OVER_CAP.format(limit=70, over=73 + 27 - 70)
    assert [fact.text for fact in profile.notes()] == [MIA, DAUGHTERS]
    assert [fact.text for fact in profile.yours()] == [NUTS]
    assert store.get_state(LAST_LEARNED_KEY) == str(message)


async def test_a_second_learning_answer_still_over_writes_nothing_and_moves_on(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    profile, _, _, _ = moved_over(store, notes_max_chars=50)
    message = user(store, "I also have two daughters.")
    add = ops(op("add", text_=DAUGHTERS, source_message_id=message))
    gemma = FakeGemma(add, add)
    before = [(fact.id, fact.text, fact.source_kind) for fact in profile.facts()]
    with caplog.at_level(logging.WARNING, logger="local_assistant.profiler"):
        assert await profiler_for(store, profile, gemma).learn() == 0
    assert [(fact.id, fact.text, fact.source_kind) for fact in profile.facts()] == before
    assert store.get_state(LAST_LEARNED_KEY) == str(message)
    assert "profile full: a learning pass skipped changes worth 50 characters" in caplog.text


def test_her_notes_over_their_limit_block_only_her_notes(store: Store) -> None:
    """After a move her notes may stay over their limit: a new note waits for room, the user's part does not."""
    profile, _, _, _ = moved_over(store, notes_max_chars=50)
    assert profile.add(DAUGHTERS, source_kind="chat").over_cap_by == 73 + 27 - 50
    assert profile.add("The user swims.", source_kind="remember").added  # 29 + 15 of 60
    assert profile.notes_used_chars() == 73


def test_a_learning_batch_that_only_frees_room_is_written_while_her_notes_are_over(store: Store) -> None:
    """Her notes over their limit after a move (allowed): a batch of removes can only bring them closer, yet apply
    refuses it whole because the part is still over, so tidying must free everything in one answer or nothing."""
    profile, note, _, _ = moved_over(store, notes_max_chars=40)
    result = profile.apply([Op("remove", fact_id=note)], source_kind="chat")
    assert result.removed == [note] and profile.notes_used_chars() == 41


# consolidate_for: only the user's part shown, a removed fact moved, never deleted


async def test_consolidate_for_shows_only_the_users_part_and_moves_instead_of_deleting(store: Store) -> None:
    profile = over_from_before(store, 70, [MIA, CITY])  # 73 of 70: over before the new fact
    note = profile.add(sized("n", 250), source_kind="chat").added[0]
    message = user(store, "remember I'm allergic to nuts")
    gemma = FakeGemma(ops(op("remove", fact_id=1), op("add", text_=NUTS)))
    result = await profiler_for(store, profile, gemma).consolidate_for(NUTS, message, "remember", None)
    prompt = gemma.calls[0]["messages"][1]["content"]
    assert prompts.CONSOLIDATE.format(limit=70, over=73 + 29 - 70, fact=NUTS, source_message_id=message) in prompt
    assert f"THE USER'S PART OF THE PROFILE (fact_id: text)\n#1: {MIA}\n#2: {CITY}" in prompt
    assert sized("n", 250) not in prompt and f"#{note}:" not in prompt
    assert gemma.calls[0]["reasoning_effort"] == "none"
    assert result.moved == [1] and not result.removed
    assert [fact.text for fact in profile.yours()] == [CITY, NUTS] and profile.yours()[1].source_message_id == message
    assert [fact.text for fact in profile.notes()] == [MIA, sized("n", 250)]


@pytest.mark.parametrize("action", ["remove", "replace"])
async def test_consolidation_never_touches_a_note_it_was_not_shown(store: Store, action: str) -> None:
    """consolidate_for shows only the user's part; a note's number in its answer (the numbers interleave) frees
    nothing there: a remove deletes the note for good (no move: not owned), a replace rewrites it unseen."""
    profile = two_part(store, max_chars=60)
    mine = profile.add(MIA, source_kind="remember").added[0]
    note = profile.add(CITY, source_kind="chat").added[0]
    message = user(store, "remember I'm allergic to nuts")
    note_op = op(action, fact_id=note, text_="The user lives in Utrecht." if action == "replace" else "")
    gemma = FakeGemma(ops(note_op, op("replace", fact_id=mine, text_="Mia plays the violin."), op("add", text_=NUTS)))
    await profiler_for(store, profile, gemma).consolidate_for(NUTS, message, "remember", None)
    assert [(fact.id, fact.text) for fact in profile.notes()] == [(note, CITY)]


@pytest.mark.parametrize("answer", ["no add", "add too long"])
async def test_consolidation_that_does_not_save_the_fact_moves_nothing(store: Store, answer: str) -> None:
    """Room is made for the new fact: when Gemma's batch leaves it out, or its shortened text is refused, the moves
    are for nothing, and the user hears "Not saved" with "Moved to notes" beside it."""
    profile = two_part(store, max_chars=60)
    mine = profile.add(MIA, source_kind="remember").added[0]
    message = user(store, "remember I'm allergic to nuts")
    batch = [op("remove", fact_id=mine)] + ([op("add", text_=NUTS + " " + "la " * 100)] if answer != "no add" else [])
    gemma = FakeGemma(ops(*batch))
    result = await profiler_for(store, profile, gemma).consolidate_for(NUTS, message, "remember", None)
    assert not result.added
    assert [fact.id for fact in profile.yours()] == [mine] and not result.moved


# the remember tool over the limit, end to end


async def test_remember_over_the_limit_moves_with_gemmas_answer_and_says_so(store: Store) -> None:
    profile = over_from_before(store, 60, [MIA, CITY])
    gemma = FakeGemma(ops(op("remove", fact_id=1), op("remove", fact_id=2), op("add", text_=NUTS)))
    tools = Tools(store, profile, TZ, FakeFetch(), profiler_for(store, profile, gemma).consolidate_for)
    current = turn(store, "remember that I am allergic to nuts")
    reply = await tools.call(current, "remember", json.dumps({"fact": NUTS}))
    assert reply == f"Saved as fact #3. {prompts.MOVED_NOTE.format(numbers='#1, #2')}"
    assert current.moved == [(1, MIA), (2, CITY)] and current.saved == [(3, NUTS)] and not current.removed
    notice = profile_notice(current)
    assert f"Moved to notes #1: {MIA}" in notice and f"Moved to notes #2: {CITY}" in notice
    assert [fact.text for fact in profile.yours()] == [NUTS] and len(profile.facts()) == 3


@pytest.mark.parametrize("outcome", ["still over", "gemma failed"])
async def test_remember_when_room_could_not_be_made_changes_nothing(store: Store, outcome: str) -> None:
    profile = two_part(store, max_chars=60)
    mine = profile.add(MIA, source_kind="remember").added[0]

    async def consolidate(
        fact: str, source: int | None, kind: SourceKind, document: int | None, allowed: str | None = None
    ) -> ApplyResult:
        if outcome == "gemma failed":
            raise RuntimeError("Gemma is down")
        batch = [Op("replace", fact_id=mine, text=MIA[:-1] + " every day."), Op("add", text=fact)]
        return profile.apply(batch, source_kind=kind, remove_to_notes=True)

    tools = Tools(store, profile, TZ, FakeFetch(), consolidate)
    current = turn(store, "remember that I am allergic to nuts")
    reply = await tools.call(current, "remember", json.dumps({"fact": NUTS}))
    expected = "the profile is full and could not be tidied up right now" if outcome == "gemma failed" else "full"
    assert reply.startswith("Not saved:") and expected in reply
    assert [(fact.id, fact.text) for fact in profile.facts()] == [(mine, MIA)]
    assert not (current.saved or current.updated or current.moved) and profile_notice(current) == ""


def moving_consolidate(profile: Profile, calls: list[str]) -> Any:
    """A consolidation that makes room the way Gemma would: the user's first fact moves, the new one is added."""

    async def consolidate(
        fact: str, source: int | None, kind: SourceKind, document: int | None, allowed: str | None = None
    ) -> ApplyResult:
        calls.append(fact)
        first = profile.yours()[0].id
        batch = [Op("remove", fact_id=first), Op("add", text=fact, source_message_id=source)]
        return profile.apply(batch, source_kind=kind, remove_to_notes=True)

    return consolidate


async def test_a_remember_refused_by_its_check_never_makes_room(store: Store) -> None:
    """An install from before over the limit: the first add is refused for its text, yet over_cap_by is set (the
    part was over already), so the tool asks for room and moves a fact for a fact that is never saved."""
    profile = over_from_before(store, 60, [MIA, CITY])
    calls: list[str] = []
    tools = Tools(store, profile, TZ, FakeFetch(), moving_consolidate(profile, calls))
    current = turn(store, "remember my account NL00TEST0123456789")
    reply = await tools.call(current, "remember", json.dumps({"fact": "The user's account is NL00TEST0123456789."}))
    assert reply == "Not saved: looks like a bank account number."
    assert [fact.text for fact in profile.yours()] == [MIA, CITY] and current.moved == []


async def test_a_remember_already_in_the_profile_never_makes_room(store: Store) -> None:
    profile = over_from_before(store, 60, [MIA, CITY])
    calls: list[str] = []
    tools = Tools(store, profile, TZ, FakeFetch(), moving_consolidate(profile, calls))
    current = turn(store, "remember that I live in Amsterdam")
    reply = await tools.call(current, "remember", json.dumps({"fact": CITY}))
    assert reply == "Already in the profile."
    assert [fact.text for fact in profile.yours()] == [MIA, CITY] and current.moved == []


async def test_a_link_fact_that_needs_room_keeps_the_pages_own_link(store: Store) -> None:
    """After learn_link, remember saves "link" facts checked strictly against the page's own link; consolidate_for
    re-checks Gemma's batch strictly without that link, so the same fact is refused, after a move."""
    profile = two_part(store, max_chars=60)
    profile.add(MIA, source_kind="remember")
    fact = f"The user's page is {PAGE}"
    gemma = FakeGemma(ops(op("remove", fact_id=1), op("add", text_=fact)))
    tools = Tools(store, profile, TZ, FakeFetch(), profiler_for(store, profile, gemma).consolidate_for)
    current = turn(store, f"Learn from my page: {PAGE}")
    await tools.call(current, "learn_link", json.dumps({"url": PAGE}))
    assert current.learned_url == PAGE
    reply = await tools.call(current, "remember", json.dumps({"fact": fact}))
    assert reply.startswith("Saved as fact #")
    assert [(fact_.text, fact_.source_kind) for fact_ in profile.yours()] == [(fact, "link")]


async def test_learning_a_page_into_a_full_part_says_it_is_full(store: Store) -> None:
    """learn_page writes the user's part: over its limit nothing is saved (one batch), and the result must say so
    rather than read as a page with nothing new (/learn says "Nothing new: I already know what it says.")."""
    profile = over_from_before(store, 60, [MIA, CITY])
    gemma = FakeGemma(text(json.dumps({"facts": ["The user works as a product manager."]})))
    tools = Tools(store, profile, TZ, FakeFetch())
    tools.set_learn_page(profiler_for(store, profile, gemma).learn_page)
    current = turn(store, f"Learn from my page: {PAGE}")
    reply = await tools.call(current, "learn_link", json.dumps({"url": PAGE}))
    assert len(profile.facts()) == 2  # nothing written: the part was over before the page
    assert "full" in reply


def test_a_fix_that_shortens_a_users_fact_is_allowed_while_their_part_is_over(store: Store) -> None:
    """An install from before keeps the user's facts past the limit; a /fix that makes their part smaller can only
    help, yet fix() refuses any text while the part stays over."""
    profile = over_from_before(store, 60, [MIA, CITY])
    assert profile.fix(1, "The user's daughter Mia plays violin.") is None
    assert profile.used_chars() == len("The user's daughter Mia plays violin.") + len(CITY)
