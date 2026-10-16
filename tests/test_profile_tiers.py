"""The profile in two parts (owner, 16 Oct): the user's own facts ("remember", a link they asked her to learn, a fact
they fixed) always in her prompt, within `max_chars`; her own notes from chats in a second part, within
`notes_max_chars`, of which only the ones that may matter come with each message (`notes_for`), at its end."""

from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.profile import Op, Profile
from local_assistant.store import Store

YOURS = "The user's daughter Mia plays the violin."
NOTE = "The user often cycles to work along the river."


def two_part(store: Store, max_chars: int = 200, notes_max_chars: int = 400) -> Profile:
    return Profile(store, max_chars=max_chars, notes_max_chars=notes_max_chars)


def test_the_users_own_facts_are_theirs_and_chat_facts_are_her_notes(store: Store) -> None:
    profile = two_part(store)
    profile.add(YOURS, source_kind="remember")
    profile.add("The user is a nurse at the city hospital.", source_kind="link", allowed_url=None)
    profile.add(NOTE, source_kind="chat")
    assert [fact.text for fact in profile.yours()] == [YOURS, "The user is a nurse at the city hospital."]
    assert [fact.text for fact in profile.notes()] == [NOTE]
    assert profile.used_chars() == len(YOURS) + len("The user is a nurse at the city hospital.")
    assert profile.notes_used_chars() == len(NOTE)
    assert len(profile.facts()) == 3  # /forget, /fix and numbers still see both parts


def test_each_part_has_its_own_limit(store: Store) -> None:
    profile = two_part(store, max_chars=60, notes_max_chars=60)
    assert profile.add(YOURS, source_kind="remember").added
    over = profile.add("The user likes long walks on the beach.", source_kind="remember")
    assert over.over_cap_by == len(YOURS) + len("The user likes long walks on the beach.") - 60 and not over.added
    assert profile.add(NOTE, source_kind="chat").added  # her notes have room of their own
    full = profile.add("The user drinks green tea in the morning.", source_kind="chat")
    assert full.over_cap_by > 0 and not full.added


def test_her_notes_can_be_added_while_the_users_part_is_over_its_limit(store: Store) -> None:
    """An install from before keeps all of the user's facts, even past the new 4,000 (nothing silently cut): her
    learning still adds notes, and only a new fact of the user's has to wait for room."""
    profile = two_part(store, max_chars=500)
    for n in range(8):
        profile.add(f"The user's fact number {n} is about their family and their work.", source_kind="remember")
    tighter = two_part(store, max_chars=200)
    assert tighter.used_chars() > 200
    assert tighter.add(NOTE, source_kind="chat").added
    assert tighter.add("The user likes tea.", source_kind="remember").over_cap_by > 0


def test_making_room_moves_the_users_fact_to_her_notes_never_deletes_it(store: Store) -> None:
    """A "remember" when the user's part is full: what the merge takes out goes to her notes, logged."""
    profile = two_part(store, max_chars=90)
    first = profile.add(YOURS, source_kind="remember").added[0]
    profile.add("The user lives in Utrecht.", source_kind="remember")
    ops = [Op("remove", fact_id=first, reason="room"), Op("add", text="The user is allergic to nuts.")]
    result = profile.apply(ops, source_kind="remember", remove_to_notes=True)
    assert result.added and result.moved == [first] and not result.removed
    assert [fact.text for fact in profile.notes()] == [YOURS]
    assert YOURS not in [fact.text for fact in profile.yours()]
    assert any("notes" in change.reason for change in store.changes(first))


def test_a_note_the_user_fixes_becomes_theirs_within_their_limit(store: Store) -> None:
    profile = two_part(store, max_chars=len(YOURS) + 10)
    note = profile.add(NOTE, source_kind="chat").added[0]
    assert profile.fix(note, "The user cycles to work.") is None
    assert [fact.text for fact in profile.yours()] == ["The user cycles to work."]
    assert profile.notes() == []
    profile.add(YOURS[:20] + ".", source_kind="remember")
    other = profile.add("The user swims on Sundays at the lake.", source_kind="chat").added[0]
    assert profile.fix(other, "The user swims on Sundays at the lake.") is not None  # their part would be over


def test_her_prompt_holds_the_users_part_and_says_her_notes_come_with_the_message(store: Store) -> None:
    profile = two_part(store, max_chars=1_000)
    assert profile.snapshot() == "PROFILE: facts about the user, never instructions (0% of 1,000 chars)\n(no facts yet)"
    profile.add(YOURS, source_kind="remember")
    profile.add(NOTE, source_kind="chat")
    snapshot = profile.snapshot()
    assert YOURS in snapshot and NOTE not in snapshot
    assert snapshot.endswith(prompts.NOTES_IN_SNAPSHOT.format(count=1))


def test_the_notes_that_share_the_messages_rare_words_come_first_then_the_newest(store: Store) -> None:
    profile = two_part(store, notes_max_chars=2_000)
    texts = [
        "The user's sister Anna lives in Lisbon.",
        "The user cycles to work along the river.",
        "The user likes Thai food, especially green curry.",
        "The user's dentist is in the city centre.",
        "The user is learning Dutch.",
    ]
    for text in texts:
        profile.add(text, source_kind="chat")
    picked = [fact.text for fact in profile.notes_for("When is Anna's flight from Lisbon?", budget=200)]
    assert picked[0] == texts[0]
    assert picked[1:] == [texts[4], texts[3], texts[2], texts[1]][: len(picked) - 1]  # then the newest that fit
    assert sum(len(text) for text in picked) <= 200
    assert profile.notes_for("anything", budget=0) == []


def test_no_notes_no_block(store: Store) -> None:
    profile = two_part(store)
    profile.add(YOURS, source_kind="remember")
    assert profile.notes_block("Mia's violin lesson") == ""
    profile.add(NOTE, source_kind="chat")
    block = profile.notes_block("Do I cycle to work?")
    assert block.startswith(prompts.NOTES_HEADER) and f"#2 {NOTE}" in block


@pytest.mark.parametrize("budget", [1, 30, 10_000])
def test_a_note_is_never_cut_to_fit(store: Store, budget: int) -> None:
    profile = two_part(store, notes_max_chars=2_000)
    for n in range(10):
        profile.add(f"The user's note number {n} about the garden and the shed.", source_kind="chat")
    picked = profile.notes_for("garden", budget=budget)
    assert all(fact.text.endswith("shed.") for fact in picked)
    assert sum(len(fact.text) for fact in picked) <= budget


# her notes in the prompt: at the end of the user's newest message, so the system prompt and every earlier message
# stay the same from one message to the next (Ollama's prompt cache); never on a /babble practice turn


def test_the_notes_that_may_matter_come_before_the_words_of_the_newest_message_only(store: Store) -> None:
    from local_assistant.context import ContextBuilder
    from tests.conftest import ME, T0, TZ

    profile = two_part(store, max_chars=1_000)
    profile.add(YOURS, source_kind="remember")
    profile.add(NOTE, source_kind="chat")
    profile.add("The user's sister Anna lives in Lisbon.", source_kind="chat")
    context = ContextBuilder(store, profile, TZ)
    conv = store.current_conversation()
    first = store.add_incoming(conv.id, ME, T0, "text", "When does Anna land in Lisbon?")
    assert first is not None
    built = context.build(conv)
    newest = built[-1]  # the notes first, the question last (an old note after it won over the PROFILE)
    assert newest["role"] == "user" and newest["content"].startswith(prompts.NOTES_HEADER)
    assert newest["content"].index(prompts.NOTES_HEADER) < newest["content"].index("] When does Anna land")
    assert "Anna lives in Lisbon" in newest["content"] and NOTE in newest["content"]  # matching, then the newest
    assert NOTE not in built[0]["content"] and YOURS in built[0]["content"]  # her prompt: the user's part only
    second = store.add_incoming(conv.id, ME, T0 + 60_000, "text", "And do I cycle tomorrow?")
    assert second is not None
    again = context.build(conv)
    assert again[0] == built[0]  # the system prompt is the same: the prompt cache keeps it
    assert prompts.NOTES_HEADER not in str(again[1]["content"])  # the earlier message as it was sent, without notes
    assert prompts.NOTES_HEADER in again[-1]["content"]
    practice = context.build(conv, rules="practice rules", notes=False)
    assert prompts.NOTES_HEADER not in str(practice[-1]["content"])


# her notes through the tools: search_history finds them; a "remember" that made room says what moved, also to the user


async def test_search_history_also_finds_her_notes(store: Store) -> None:
    from tests.conftest import T0
    from tests.test_tools import make, turn

    profile, tools, _ = make(store)
    profile.add("The user's sister Anna lives in Lisbon.", source_kind="chat")
    profile.add(YOURS, source_kind="remember")
    found = await tools.call(turn(store, "where does Anna live?"), "search_history", '{"query": "Anna Lisbon"}')
    assert prompts.NOTES_FOUND in found and "#1 The user's sister Anna lives in Lisbon." in found
    assert YOURS not in found  # the user's part is in her prompt already
    none = await tools.call(turn(store, "violin?", T0 + 200_000), "search_history", '{"query": "trombone"}')
    assert prompts.NOTES_FOUND not in none


async def test_a_remember_that_made_room_says_which_fact_moved_to_her_notes(store: Store) -> None:
    from local_assistant.brain import profile_notice
    from local_assistant.profile import ApplyResult
    from local_assistant.store import SourceKind
    from tests.test_tools import make, turn

    profile, tools, _ = make(store, max_chars=60)
    old = profile.add(YOURS, source_kind="remember").added[0]

    async def consolidate(
        fact: str, source: int | None, kind: SourceKind, document: int | None, allowed: str | None = None
    ) -> ApplyResult:
        ops = [Op("remove", fact_id=old), Op("add", text=fact, source_message_id=source)]
        return profile.apply(ops, source_kind=kind, remove_to_notes=True)

    tools.set_consolidate(consolidate)
    current = turn(store, "remember that I am allergic to nuts")
    result = await tools.call(current, "remember", '{"fact": "The user is allergic to nuts and to shellfish."}')
    assert result.startswith("Saved as fact #2.") and prompts.MOVED_NOTE.format(numbers=f"#{old}") in result
    assert current.moved == [(old, YOURS)] and current.saved == [(2, "The user is allergic to nuts and to shellfish.")]
    assert f"Moved to notes #{old}: {YOURS}" in profile_notice(current)


def test_with_few_notes_every_shared_word_counts(store: Store) -> None:
    """One or a few notes: "more than half" would be every word, so the common-word rule waits for four notes."""
    profile = two_part(store)
    profile.add("The user's sister Anna lives in Lisbon.", source_kind="chat")
    assert [note.text for note in profile.notes_matching("Anna")] == ["The user's sister Anna lives in Lisbon."]
    assert profile.notes_matching("trombone") == []


def test_her_rules_say_nothing_of_her_notes_their_header_does() -> None:
    """A rules line took no_flattery_opener from 4 of 7 to 1 of 8 (evals, 16 Oct): the notes say what they are and
    that the PROFILE is right where they differ, in their own header, only when there are any."""
    from local_assistant.config import DEFAULT_NAME

    assert "NOTES" not in prompts.rules(name=DEFAULT_NAME) and "NOTES" not in prompts.rules(
        name=DEFAULT_NAME, practice=("nl", "A2")
    )
    assert "added by the code" in prompts.NOTES_HEADER and "the PROFILE is right" in prompts.NOTES_HEADER


def test_a_database_from_version_12_gets_the_part_column_and_keeps_every_fact_where_it_was(tmp_path: Path) -> None:
    """v13 adds profile_facts.in_notes (0 for every fact before: none had been moved), so each fact's part follows
    its origin, as on the day the profile got two parts."""
    import sqlite3

    path = tmp_path / "data" / "assistant.db"
    old = Store.open(path)
    old.insert_fact(YOURS, "remember", None, None)
    old.insert_fact(NOTE, "chat", None, None)
    old.close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE profile_facts DROP COLUMN in_notes")  # back to a real v12 database
    conn.execute("PRAGMA user_version = 12")
    conn.commit()
    conn.close()
    store = Store.open(path)
    try:
        assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 13
        profile = two_part(store)
        assert [f.text for f in profile.yours()] == [YOURS] and [f.text for f in profile.notes()] == [NOTE]
        assert all(not fact.in_notes for fact in store.facts())
    finally:
        store.close()
