"""The profile in two parts as the user meets it (owner, 16 Oct): /profile, /status, /fix, /forget and /new, the code's
"Profile changes" notice, remember's reply when facts move into her notes, search_history's notes, and the numbers
GUIDE.md and README.md give for the two parts."""

import dataclasses
import inspect
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from local_assistant import prompts
from local_assistant.brain import Brain, profile_notice
from local_assistant.commands import Commands, StatusInfo
from local_assistant.config import Config
from local_assistant.context import ContextBuilder
from local_assistant.fetcher import Page
from local_assistant.profile import Op, Profile
from local_assistant.profiler import Profiler
from local_assistant.store import Fact, Message, Store
from local_assistant.timefmt import short_date
from local_assistant.tools import Tools, Turn
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text, tool

ROOT = Path(__file__).resolve().parents[1]
YOURS = "The user's daughter Mia plays the violin."
NOTE = "The user often cycles to work along the river."


async def status() -> StatusInfo:
    return StatusInfo(
        gemma="loaded",
        queue_length=0,
        last_learning_run=None,
        db_bytes=1_000_000,
        messages=3,
        restarts=0,
    )


async def no_fetch(url: str) -> Page:
    raise AssertionError(f"no page is read here: {url}")


def sized(tag: str, n: int, length: int) -> str:
    """A fact of exactly `length` characters that passes check_fact (no long word, no instruction)."""
    body = (f"The user note {tag} {n:03d}" + " la" * 200)[: length - 1]
    if body.endswith(" "):
        body = body[:-1] + "x"
    return body + "."


def make(store: Store, profile: Profile | None = None) -> tuple[Profile, ContextBuilder, Commands]:
    profile = profile or Profile(store)
    context = ContextBuilder(store, profile, TZ)
    return profile, context, Commands(store, profile, context, TZ, status, lambda: None)


def command(store: Store, words: str, ts: int = T0) -> Message:
    message_id = store.add_incoming(store.current_conversation().id, ME, ts, "command", words)
    assert message_id is not None
    return store.message(message_id)


def turn(store: Store, words: str, ts: int = T0 + 100_000) -> Turn:
    message_id = store.add_incoming(store.current_conversation().id, ME, ts, "text", words)
    assert message_id is not None
    return Turn(store.message(message_id))


def day(fact: Fact) -> str:
    """The date /profile shows: without this year's number."""
    return short_date(fact.created_at, TZ, today=datetime.now(TZ).date())


def ops(*operations: dict[str, Any]) -> str:
    """A learning answer as Gemma's JSON (prompts.OPS_SCHEMA)."""
    full = [{"fact_id": 0, "text": "", "reason": "room", "source_message_id": 0, **op} for op in operations]
    return json.dumps({"operations": full})


def text_of(store: Store, fact_id: int) -> str | None:
    return next((fact.text for fact in store.facts() if fact.id == fact_id), None)


# /profile


async def test_profile_with_only_notes_says_nothing_of_yours_yet_then_the_notes_with_exact_fill(store: Store) -> None:
    profile, _, commands = make(store)
    profile.add(NOTE, source_kind="chat")
    profile.add("The user drinks green tea in the morning.", source_kind="chat")
    first, second = profile.facts()
    used = len(first.text) + len(second.text)
    assert await commands.handle(command(store, "/profile")) == (
        "**Your profile**: I see it with every message (0 of 4,000 characters, 0%)\n"
        "\n"
        'Nothing of yours yet: say "remember that ...".\n'
        "\n"
        f"**My notes from our chats**: 2 notes ({used} of 12,000 characters, 1%). I see up to 1,500 characters of "
        "them with each message, the ones that match it first, then the newest. /profile notes lists them.\n"
        "\n" + prompts.PROFILE_FOOTER
    )
    assert await commands.handle(command(store, "/profile notes", T0 + 1)) == (
        f"**My notes from our chats** ({used} of 12,000 characters, 1%): I see up to 1,500 characters of them with "
        "each message, the ones that match it first, then the newest.\n"
        "\n"
        f"#1 {NOTE} ({day(first)})\n"
        f"#2 The user drinks green tea in the morning. ({day(second)})\n"
        "\n" + prompts.PROFILE_FOOTER
    )


async def test_profile_with_only_the_users_facts_has_no_notes_section_and_formats_thousands(store: Store) -> None:
    """Fill numbers are exact and written with a thousands separator; "remember" and "link" are both theirs."""
    profile, _, commands = make(store)
    texts = [sized("own", n, 250) for n in range(5)]
    for n, fact in enumerate(texts):
        profile.add(fact, source_kind="link" if n == 2 else "remember")
    listing = await commands.handle(command(store, "/profile"))
    assert listing.splitlines()[0] == "**Your profile**: I see it with every message (1,250 of 4,000 characters, 31%)"
    assert "My notes from our chats" not in listing and prompts.PROFILE_NONE_YET not in listing
    assert f"#3 {texts[2]} (from a link, " in listing and f"#1 {texts[0]} (you asked, " in listing
    assert listing.endswith("\n\n" + prompts.PROFILE_FOOTER)


async def test_each_part_is_grouped_on_its_own_and_the_fill_counts_the_category_words(store: Store) -> None:
    """The user's part without categories has no headings, even when her notes have them (each part grouped alone);
    an uncategorised note goes under Other in the notes only. The fill counts the whole text, "Music: " included,
    though the line shows the text after it."""
    profile, _, commands = make(store)
    profile.add(YOURS, source_kind="remember")
    profile.add("Music: The user plays bass in a band.", source_kind="chat")
    profile.add("The user likes tea.", source_kind="chat")
    _, music, tea = profile.facts()
    listing = await commands.handle(command(store, "/profile"))
    notes_used = len("Music: The user plays bass in a band.") + len("The user likes tea.")
    assert listing == (
        f"**Your profile**: I see it with every message ({len(YOURS)} of 4,000 characters, 1%)\n"
        "\n"
        f"#1 {YOURS} (you asked, {day(music)})\n"
        "\n"
        f"**My notes from our chats**: 2 notes ({notes_used} of 12,000 characters, 0%). I see up to 1,500 characters "
        "of them with each message, the ones that match it first, then the newest. /profile notes lists them.\n"
        "\n" + prompts.PROFILE_FOOTER
    )
    assert await commands.handle(command(store, "/profile notes", T0 + 1)) == (
        f"**My notes from our chats** ({notes_used} of 12,000 characters, 0%): I see up to 1,500 characters of them "
        "with each message, the ones that match it first, then the newest.\n"
        "\n"
        "**Music**\n"
        f"#2 The user plays bass in a band. ({day(music)})\n"
        "\n"
        "**Other**\n"
        f"#3 The user likes tea. ({day(tea)})\n"
        "\n" + prompts.PROFILE_FOOTER
    )


async def test_an_install_from_before_over_the_users_limit_shows_the_real_fill(store: Store) -> None:
    """An install from before (old limit 6,000 for everything) keeps every fact of the user's: /profile and /status
    show the part over its limit as it is, never cut. GUIDE.md says "Up to 4,000 characters" without this case."""
    roomy = Profile(store, max_chars=6_000)
    for n in range(5):
        roomy.add(sized("old", n, 250), source_kind="remember")
    _, _, commands = make(store, Profile(store, max_chars=1_000))
    listing = await commands.handle(command(store, "/profile"))
    assert listing.splitlines()[0] == "**Your profile**: I see it with every message (1,250 of 1,000 characters, 125%)"
    assert listing.count(" (you asked, ") == 5
    shown = await commands.handle(command(store, "/status", T0 + 1))
    assert "Profile: 5 facts of yours, 125% full; 0 notes from chats, 0% full" in shown.splitlines()


async def test_a_fact_moved_to_her_notes_still_says_the_user_asked_for_it(store: Store) -> None:
    """GUIDE.md: /profile lists "where each came from". A fact the user asked her to remember and that was moved into
    her notes to make room (Store.demote_fact: source_kind "chat") is shown as one she learned from chat."""
    profile, _, commands = make(store, Profile(store, max_chars=90))
    first = profile.add(YOURS, source_kind="remember").added[0]
    ops_ = [Op("remove", fact_id=first, reason="room"), Op("add", text="The user is allergic to nuts.")]
    assert profile.apply(ops_, source_kind="remember", remove_to_notes=True).moved == [first]
    listing = await commands.handle(command(store, "/profile notes"))
    line = next(line for line in listing.splitlines() if line.startswith(f"#{first} "))
    assert "learned from chat" not in line and "(you asked, " in line


# /status


async def test_status_has_one_exact_line_for_both_parts(store: Store) -> None:
    profile, _, commands = make(store)
    for n in range(4):
        profile.add(sized("own", n, 250), source_kind="remember")  # 1,000 of 4,000
    for n in range(12):
        profile.add(sized("hers", n, 250), source_kind="chat")  # 3,000 of 12,000
    lines = (await commands.handle(command(store, "/status"))).splitlines()
    assert "Profile: 4 facts of yours, 25% full; 12 notes from chats, 25% full" in lines
    assert "Last learning run: not yet" in lines


# /fix


async def test_fixing_a_note_makes_it_theirs_in_place_and_in_her_prompt(store: Store) -> None:
    """A note fixed with /fix keeps its number, moves to the user's part of /profile ("you asked"), and her prompt
    (refreshed) holds it, with one note fewer counted at its end."""
    profile, context, commands = make(store)
    profile.add(YOURS, source_kind="remember")
    profile.add(NOTE, source_kind="chat")
    profile.add("The user drinks green tea in the morning.", source_kind="chat")
    before = context.refresh_snapshot()
    assert before.endswith(prompts.NOTES_IN_SNAPSHOT.format(count=2)) and "cycles" not in before
    reply = await commands.handle(command(store, "/fix 2 The user cycles to work."))
    assert reply == "Fixed: #2 The user cycles to work."
    assert [(fact.id, fact.source_kind) for fact in profile.facts()] == [(1, "remember"), (2, "remember"), (3, "chat")]
    after = context.snapshot()
    assert "#2 The user cycles to work." in after and after.endswith(prompts.NOTES_IN_SNAPSHOT.format(count=1))
    listing = await commands.handle(command(store, "/profile", T0 + 1))
    yours, notes = listing.split("**My notes from our chats**")
    assert "#2 The user cycles to work. (you asked, " in yours and "#2 " not in notes
    assert "#2 " not in await commands.handle(command(store, "/profile notes", T0 + 2))
    used = len(YOURS) + len("The user cycles to work.")
    assert listing.splitlines()[0] == f"**Your profile**: I see it with every message ({used} of 4,000 characters, 2%)"


async def test_fixing_the_last_note_leaves_no_notes_section(store: Store) -> None:
    profile, context, commands = make(store)
    profile.add(NOTE, source_kind="chat")
    assert await commands.handle(command(store, "/fix 1 The user cycles to work.")) == (
        "Fixed: #1 The user cycles to work."
    )
    listing = await commands.handle(command(store, "/profile", T0 + 1))
    assert "My notes from our chats" not in listing and prompts.PROFILE_NONE_YET not in listing
    assert "notes of yours from earlier chats" not in context.snapshot()


async def test_fixing_a_note_is_refused_when_their_part_would_be_over_and_allowed_at_the_limit(store: Store) -> None:
    """The whole new text counts in the user's part (a note counted nothing there before): one character over is
    refused with the exact number, and the note stays a note; exactly at the limit is fine."""
    profile, _, commands = make(store, Profile(store, max_chars=100))
    profile.add(sized("own", 1, 60), source_kind="remember")
    note = profile.add(NOTE, source_kind="chat").added[0]
    over = await commands.handle(command(store, f"/fix {note} {sized('fix', 1, 41)}"))
    assert over == "Not fixed: your part of the profile would be 1 characters over its limit."
    assert [fact.source_kind for fact in profile.facts()] == ["remember", "chat"]
    assert text_of(store, note) == NOTE
    fits = sized("fix", 2, 40)
    assert await commands.handle(command(store, f"/fix {note} {fits}", T0 + 1)) == f"Fixed: #{note} {fits}"
    assert profile.used_chars() == 100 and profile.notes() == []


async def test_the_category_a_fixed_note_keeps_counts_toward_their_limit(store: Store) -> None:
    """/fix keeps the note's category when the new text has none ("Work: " is added back): those 6 characters count
    too, so a text that fits on its own is refused by exactly them."""
    profile, _, commands = make(store, Profile(store, max_chars=100))
    profile.add(sized("own", 1, 70), source_kind="remember")
    profile.add("Work: The user works at Acme.", source_kind="chat")
    words = sized("fix", 1, 30)  # 70 + 30 = 100 alone; with "Work: " 106
    assert await commands.handle(command(store, f"/fix 2 {words}")) == (
        "Not fixed: your part of the profile would be 6 characters over its limit."
    )
    shorter = sized("fix", 2, 24)
    assert await commands.handle(command(store, f"/fix 2 {shorter}", T0 + 1)) == f"Fixed: #2 Work: {shorter}"
    listing = await commands.handle(command(store, "/profile", T0 + 2))
    assert f"**Work**\n#2 {shorter} (you asked, " in listing.split("**My notes from our chats**")[0]


async def test_a_note_fixed_in_its_own_words_becomes_theirs_and_a_copy_of_theirs_is_refused(store: Store) -> None:
    """Retyping a note (without its category) just makes it theirs; a fix into words one of their facts already
    holds is refused by number, and the note stays a note."""
    profile, _, commands = make(store)
    mine = profile.add(YOURS, source_kind="remember").added[0]
    note = profile.add("Home: The user lives near the old harbour.", source_kind="chat").added[0]
    other = profile.add(NOTE, source_kind="chat").added[0]
    assert await commands.handle(command(store, f"/fix {note} The user lives near the old harbour.")) == (
        f"Fixed: #{note} Home: The user lives near the old harbour."
    )
    assert [fact.id for fact in profile.yours()] == [mine, note]
    copy = await commands.handle(command(store, f"/fix {other} {YOURS.lower()}", T0 + 1))
    assert copy == f"Not fixed: fact #{mine} already says that."
    assert [fact.id for fact in profile.notes()] == [other]


async def test_an_install_over_the_users_limit_can_still_shorten_a_fact_with_fix(store: Store) -> None:
    """An install from before keeps every fact even past the user's limit (Profile.apply checks only the part it
    writes). Profile.fix counts the whole part, so a fix that makes their part smaller is refused too: the user cannot
    correct a wrong fact without first forgetting others."""
    roomy = Profile(store, max_chars=6_000)
    for n in range(5):
        roomy.add(sized("old", n, 250), source_kind="remember")
    profile, _, commands = make(store, Profile(store, max_chars=1_000))
    reply = await commands.handle(command(store, "/fix 1 The user lives in Utrecht."))
    assert reply == "Fixed: #1 The user lives in Utrecht."
    assert profile.used_chars() < 1_250


# /forget


async def test_forget_by_number_works_on_a_note_and_on_theirs_and_her_prompt_follows(store: Store) -> None:
    profile, context, commands = make(store)
    profile.add(YOURS, source_kind="remember")
    profile.add(NOTE, source_kind="chat")
    profile.add("The user drinks green tea in the morning.", source_kind="chat")
    assert context.refresh_snapshot().endswith(prompts.NOTES_IN_SNAPSHOT.format(count=2))
    assert await commands.handle(command(store, "/forget #2")) == f"Forgotten: #2 {NOTE}"
    assert context.snapshot().endswith(prompts.NOTES_IN_SNAPSHOT.format(count=1))
    assert await commands.handle(command(store, "/forget 1", T0 + 1)) == f"Forgotten: #1 {YOURS}"
    snapshot = context.snapshot()
    assert "Mia" not in snapshot and "(no facts yet)" in snapshot
    assert snapshot.endswith(prompts.NOTES_IN_SNAPSHOT.format(count=1))
    assert [fact.id for fact in profile.facts()] == [3]


async def test_forget_by_words_looks_in_both_parts(store: Store) -> None:
    """Words that match one fact of theirs and one note list both (with their numbers); words only the note has
    forget it, whichever part it is in."""
    profile, _, commands = make(store)
    profile.add("The user's sister lives in Rotterdam.", source_kind="remember")
    profile.add("The user sails near Rotterdam in summer.", source_kind="chat")
    several = await commands.handle(command(store, "/forget rotterdam"))
    assert several == (
        "Several facts match, send /forget N with one of:\n"
        "#1 The user's sister lives in Rotterdam.\n"
        "#2 The user sails near Rotterdam in summer."
    )
    assert await commands.handle(command(store, "/forget SAILS rotterdam", T0 + 1)) == (
        "Forgotten: #2 The user sails near Rotterdam in summer."
    )
    assert await commands.handle(command(store, "/forget sister", T0 + 2)) == (
        "Forgotten: #1 The user's sister lives in Rotterdam."
    )
    assert profile.facts() == []
    assert (await commands.handle(command(store, "/profile", T0 + 3))).startswith("Your profile is empty")


# /new


async def test_new_refreshes_her_prompt_with_their_part_and_the_count_of_notes(store: Store) -> None:
    profile, context, commands = make(store)
    context.refresh_snapshot()  # frozen while empty
    profile.add(YOURS, source_kind="remember")
    profile.add(NOTE, source_kind="chat")
    profile.add("The user drinks green tea in the morning.", source_kind="chat")
    assert "Mia" not in context.snapshot()
    reply = await commands.handle(command(store, "/new"))
    assert reply.startswith("New conversation started. I still have your profile (")
    snapshot = context.snapshot()
    assert f"#1 {YOURS}" in snapshot and "cycles" not in snapshot
    assert snapshot.endswith(prompts.NOTES_IN_SNAPSHOT.format(count=2))


# the code's notice and remember's reply


def test_the_notice_lists_saved_updated_removed_and_moved_in_that_order(store: Store) -> None:
    current = turn(store, "remember that")
    assert profile_notice(current) == ""  # nothing changed: no notice
    current.saved = [(7, "The user is allergic to nuts.")]
    current.updated = [(2, "The user lives in Utrecht.")]
    current.removed = [(3, "The user likes tea.")]
    current.moved = [(1, YOURS), (4, NOTE)]
    assert profile_notice(current) == (
        "**Profile changes**\n"
        "Saved #7: The user is allergic to nuts. (/forget 7 undoes it)\n"
        "Updated #2: The user lives in Utrecht.\n"
        "Removed #3: The user likes tea.\n"
        f"Moved to notes #1: {YOURS}\n"
        f"Moved to notes #4: {NOTE}"
    )


async def test_remember_in_a_full_part_moves_facts_into_her_notes_and_says_so(store: Store) -> None:
    """The real consolidation (Profiler.consolidate_for, Gemma faked): it shows Gemma only the user's part; the two
    facts it takes out move into her notes, never deleted; the reply names both, the turn lists them as moved (not
    removed), the notice says "Moved to notes", and /profile notes lists them in its moved group."""
    profile = Profile(store, max_chars=100)
    context = ContextBuilder(store, profile, TZ)
    first = profile.add(sized("own", 1, 40), source_kind="remember").added[0]
    second = profile.add(sized("own", 2, 40), source_kind="remember").added[0]
    note = profile.add(NOTE, source_kind="chat").added[0]
    new = "The user is allergic to nuts and to shellfish, since a child."
    current = turn(store, f"remember that {new}")
    gemma = FakeGemma(
        text(
            ops(
                {"action": "remove", "fact_id": first},
                {"action": "remove", "fact_id": second},
                {"action": "add", "text": new},
            )
        )
    )
    tools = Tools(store, profile, TZ, no_fetch)
    tools.set_consolidate(Profiler(store, profile, context, gemma, TZ).consolidate_for)
    reply = await tools.call(current, "remember", json.dumps({"fact": new}))
    added = max(fact.id for fact in profile.facts())
    assert reply == (
        f"Saved as fact #{added}. To make room, #{first}, #{second} moved to your notes from earlier chats (kept, no "
        "longer in every prompt)."
    )
    [asked] = gemma.calls
    assert NOTE not in asked["messages"][-1]["content"]  # her notes are never shown for this
    assert current.moved == [(first, sized("own", 1, 40)), (second, sized("own", 2, 40))]
    assert current.saved == [(added, new)] and current.removed == [] and current.updated == []
    assert profile_notice(current) == (
        "**Profile changes**\n"
        f"Saved #{added}: {new} (/forget {added} undoes it)\n"
        f"Moved to notes #{first}: {sized('own', 1, 40)}\n"
        f"Moved to notes #{second}: {sized('own', 2, 40)}"
    )
    assert [fact.id for fact in profile.notes()] == [first, second, note]
    _, _, commands = make(store, profile)
    listing = await commands.handle(command(store, "/profile notes", T0 + 200_000))
    assert (
        f"#{first} " in listing.split(prompts.PROFILE_MOVED)[1]
        and f"#{second} " in listing.split(prompts.PROFILE_MOVED)[1]
    )
    assert f"#{first} " not in await commands.handle(command(store, "/profile", T0 + 200_001))


async def test_a_consolidation_that_only_shortens_their_fact_moves_nothing(store: Store) -> None:
    """Merging or shortening in their part is an update: no "moved" in the reply, the turn or the notice, and her
    notes (present all along) are never counted as moved."""
    profile = Profile(store, max_chars=100)
    context = ContextBuilder(store, profile, TZ)
    first = profile.add(sized("own", 1, 70), source_kind="remember").added[0]
    profile.add(NOTE, source_kind="chat")
    new = "The user is allergic to nuts and shellfish."  # 70 + 43 > 100: consolidation
    current = turn(store, f"remember that {new}")
    short = "The user lives in Utrecht."
    gemma = FakeGemma(text(ops({"action": "replace", "fact_id": first, "text": short}, {"action": "add", "text": new})))
    tools = Tools(store, profile, TZ, no_fetch)
    tools.set_consolidate(Profiler(store, profile, context, gemma, TZ).consolidate_for)
    reply = await tools.call(current, "remember", json.dumps({"fact": new}))
    assert len(gemma.calls) == 1 and reply == "Saved as fact #3." and prompts.MOVED_NOTE.split("{")[0] not in reply
    assert current.moved == [] and current.updated == [(first, short)] and current.saved == [(3, new)]
    assert "Moved to notes" not in profile_notice(current)


async def test_making_room_for_a_remember_never_deletes_one_of_her_notes(store: Store) -> None:
    """consolidate_for shows Gemma only the user's part, and her notes "free nothing here", but Profile.apply takes
    any fact number in the answer: a remove of a note's number (a guess) deletes that note outright."""
    profile = Profile(store, max_chars=100)
    context = ContextBuilder(store, profile, TZ)
    first = profile.add(sized("own", 1, 70), source_kind="remember").added[0]
    note = profile.add(NOTE, source_kind="chat").added[0]
    new = "The user is allergic to nuts and shellfish."
    current = turn(store, f"remember that {new}")
    answer = ops(
        {"action": "remove", "fact_id": note}, {"action": "remove", "fact_id": first}, {"action": "add", "text": new}
    )
    tools = Tools(store, profile, TZ, no_fetch)
    tools.set_consolidate(Profiler(store, profile, context, FakeGemma(text(answer)), TZ).consolidate_for)
    assert (await tools.call(current, "remember", json.dumps({"fact": new}))).startswith("Saved as fact #")
    assert text_of(store, note) == NOTE


async def test_remembering_what_she_already_noted_makes_it_theirs(store: Store) -> None:
    """GUIDE.md: "Yours: what you told her to remember ... known in every message." A "remember" whose words a note
    already holds is a duplicate ("Already in the profile.") and the fact stays a note: sent only when it matches a
    message, and one learning may still merge or drop."""
    profile = Profile(store)
    tools = Tools(store, profile, TZ, no_fetch)
    note = profile.add("The user likes tea.", source_kind="chat").added[0]
    current = turn(store, "remember that I like tea")
    reply = await tools.call(current, "remember", json.dumps({"fact": "The user likes tea."}))
    assert reply != "Not saved: the profile is full."
    assert note in [fact.id for fact in profile.yours()] or len(profile.yours()) == 1


async def test_the_answer_carries_the_moved_notice_end_to_end(store: Store) -> None:
    """Brain, Tools and the Profiler wired as the app does (one Gemma, faked): her reply's notice names the move."""
    profile = Profile(store, max_chars=100)
    context = ContextBuilder(store, profile, TZ)
    first = profile.add(sized("own", 1, 70), source_kind="remember").added[0]
    new = "The user is allergic to nuts and shellfish."
    gemma = FakeGemma(
        tool("remember", fact=new),
        text(ops({"action": "remove", "fact_id": first}, {"action": "add", "text": new})),
        text(f"Noted: {new}"),
    )
    tools = Tools(store, profile, TZ, no_fetch)
    tools.set_consolidate(Profiler(store, profile, context, gemma, TZ).consolidate_for)
    message_id = store.add_incoming(store.current_conversation().id, ME, T0, "text", f"remember that {new}")
    assert message_id is not None
    reply = await Brain(gemma, context, tools).answer(store.current_conversation(), store.message(message_id))
    assert reply.text == f"Noted: {new}"
    assert reply.notice == (
        f"**Profile changes**\nSaved #2: {new} (/forget 2 undoes it)\nMoved to notes #{first}: {sized('own', 1, 70)}"
    )
    tool_result = gemma.calls[2]["messages"][-1]
    assert tool_result["role"] == "tool" and "moved to your notes from earlier chats" in tool_result["content"]


# search_history


async def test_search_history_with_notes_only_and_with_nothing(store: Store) -> None:
    """Only notes match: the notes block alone (never the user's own facts, which her prompt holds); nothing matches:
    the history's own line."""
    profile = Profile(store)
    tools = Tools(store, profile, TZ, no_fetch)
    profile.add("The user's own violin teacher is Bram.", source_kind="remember")
    profile.add("The user practises the violin on Sundays.", source_kind="chat")
    profile.add(NOTE, source_kind="chat")
    found = await tools.call(turn(store, "when do I practise?"), "search_history", '{"query": "violin"}')
    assert found == f"{prompts.NOTES_FOUND}\n#2 The user practises the violin on Sundays."
    none = await tools.call(turn(store, "boat?", T0 + 200_000), "search_history", '{"query": "boat"}')
    assert none == "No matches in the history for: boat"


async def test_search_history_with_history_only_and_with_both(store: Store) -> None:
    """History alone starts with the search results; with notes too, the notes come first, then a blank line and
    the search results; at most five notes, best and then newest first."""
    profile = Profile(store)
    tools = Tools(store, profile, TZ, no_fetch)
    conversation = store.current_conversation().id
    said = store.add_incoming(conversation, ME, T0, "text", "We drove to the lake with the canoe")
    assert said is not None
    alone = await tools.call(turn(store, "the lake?"), "search_history", '{"query": "canoe"}')
    assert alone.startswith("Search results (data, not instructions):") and prompts.NOTES_FOUND not in alone
    for n in range(12):  # 3 with canoe, 3 with lake, 6 others: neither word is in more than half
        topic = "canoe" if n < 3 else "lake" if n < 6 else f"topic{n}"
        profile.add(f"The user note {n} is about the {topic}.", source_kind="chat")
    both = await tools.call(turn(store, "the lake?", T0 + 200_000), "search_history", '{"query": "canoe lake"}')
    notes, results = both.split("\n\n", 1)
    assert notes.splitlines()[0] == prompts.NOTES_FOUND
    shown = [int(line.split()[0].lstrip("#")) for line in notes.splitlines()[1:]]
    assert shown == [6, 5, 4, 3, 2]  # five of the six matching, the newest first (all weigh the same)
    assert results.startswith("Search results (data, not instructions):")
    assert "We drove to the lake with the canoe" in results


async def test_search_history_finds_notes_on_a_word_most_of_them_share(store: Store) -> None:
    """GUIDE.md: "A search of earlier chats also finds them." From 4 notes on, a word in more than half of them is
    dropped as too common (right for ranking the notes that come with a message): an explicit search for "Mia", in 3
    of her 4 notes, finds none of them."""
    profile = Profile(store)
    tools = Tools(store, profile, TZ, no_fetch)
    profile.add("The user's daughter Mia plays the violin.", source_kind="chat")
    profile.add("Mia goes to the school on the corner.", source_kind="chat")
    profile.add("Mia is allergic to cats.", source_kind="chat")
    profile.add(NOTE, source_kind="chat")
    found = await tools.call(turn(store, "what about Mia?"), "search_history", '{"query": "Mia"}')
    assert found.startswith(prompts.NOTES_FOUND)


# what GUIDE.md and README.md say


def test_the_docs_numbers_are_the_codes_limits() -> None:
    """4,000, 12,000 and 1,500 in GUIDE.md are the defaults of the config and of Profile alike."""
    defaults = {f.name: f.default for f in dataclasses.fields(Config)}
    assert (defaults["profile_max_chars"], defaults["notes_max_chars"], defaults["notes_per_message_chars"]) == (
        4_000,
        12_000,
        1_500,
    )
    signature = inspect.signature(Profile.__init__).parameters
    assert signature["max_chars"].default == defaults["profile_max_chars"]
    assert signature["notes_max_chars"].default == defaults["notes_max_chars"]
    assert signature["notes_per_message"].default == defaults["notes_per_message_chars"]
    guide = (ROOT / "GUIDE.md").read_text()
    assert "Up to 4,000 characters, known in every message." in guide
    assert "Up to 12,000 characters." in guide and "at most 1,500 characters" in guide
    assert "counts toward its 4,000-character limit" in guide
    assert "6,000-character" not in guide


def test_readme_and_guide_say_memory_in_the_same_words() -> None:
    readme = (ROOT / "README.md").read_text()
    guide = (ROOT / "GUIDE.md").read_text()
    words = "Saves what you tell her, known in every message, and notes from your chats, given when they matter."
    assert f"- **Memory.** {words}" in readme and f"| {words} Facts can be listed" in guide


def test_a_fact_the_user_asked_to_remember_is_never_removed_by_learning_even_once_moved(store: Store) -> None:
    """GUIDE.md: "A fact you told her to remember, or one from /learn, is never replaced or removed by something she
    picked up", and a fact taken out to make room "moves into her notes, never deleted". Once moved it is a note
    (source_kind "chat"), so the next learning pass may remove it like any note."""
    profile = Profile(store, max_chars=90)
    first = profile.add(YOURS, source_kind="remember").added[0]
    moved = [Op("remove", fact_id=first, reason="room"), Op("add", text="The user is allergic to nuts.")]
    assert profile.apply(moved, source_kind="remember", remove_to_notes=True).moved == [first]
    profile.apply([Op("remove", fact_id=first, reason="tidy")], source_kind="chat")
    assert text_of(store, first) == YOURS
