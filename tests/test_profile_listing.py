"""/profile and /profile notes (owner, 18 Oct: "on /profile things are not clear"; the proposal approved the same day,
"your suggestions are OK for the profile"): /profile shows the user's part and one line about her notes, /profile
notes lists the notes with the facts moved there to make room last, a date leaves out this year, and her notes are
matched on a message's words without English's small words ("is", "to", "on")."""

from datetime import date, datetime

import pytest

from local_assistant import prompts
from local_assistant.commands import Commands, StatusInfo
from local_assistant.context import ContextBuilder
from local_assistant.profile import Op, Profile
from local_assistant.store import Fact, Message, Store
from local_assistant.timefmt import short_date
from tests.conftest import ME, T0, TZ

YOURS = "The user's daughter Mia plays the violin."
NOTE = "The user often cycles to work along the river."
TEA = "The user drinks green tea in the morning."
PER_MESSAGE = "I see up to 1,500 characters of them with each message, the ones that match it first, then the newest."


async def status() -> StatusInfo:
    return StatusInfo(
        gemma="loaded", queue_length=0, last_learning_run=None, db_bytes=1_000_000, messages=3, restarts=0
    )


def make(store: Store, profile: Profile | None = None) -> tuple[Profile, Commands]:
    profile = profile or Profile(store)
    return profile, Commands(store, profile, ContextBuilder(store, profile, TZ), TZ, status, lambda: None)


def command(store: Store, words: str, ts: int = T0) -> Message:
    message_id = store.add_incoming(store.current_conversation().id, ME, ts, "command", words)
    assert message_id is not None
    return store.message(message_id)


def when(fact: Fact) -> str:
    return short_date(fact.created_at, TZ, today=datetime.now(TZ).date())


# the date


@pytest.mark.parametrize(
    ("stored", "today", "shown"),
    [
        ("2026-10-16T08:00:00+00:00", date(2026, 10, 18), "16 Oct"),
        ("2025-10-16T08:00:00+00:00", date(2026, 10, 18), "16 Oct 2025"),
        ("2025-12-31T23:30:00+00:00", date(2026, 1, 2), "1 Jan"),  # 00:30 on 1 January in Amsterdam: this year
        ("2026-01-01T00:30:00+00:00", date(2025, 12, 31), "1 Jan 2026"),  # a clock behind the fact: its year shown
    ],
)
def test_a_date_leaves_out_this_years_number(stored: str, today: date, shown: str) -> None:
    assert short_date(stored, TZ, today=today) == shown


# /profile


async def test_profile_shows_the_users_part_and_one_line_about_her_notes(store: Store) -> None:
    profile, commands = make(store)
    profile.add(YOURS, source_kind="remember")
    profile.add(NOTE, source_kind="chat")
    profile.add(TEA, source_kind="chat")
    mine, *_ = profile.facts()
    used = len(NOTE) + len(TEA)
    assert await commands.handle(command(store, "/profile")) == (
        f"**Your profile**: I see it with every message ({len(YOURS)} of 4,000 characters, 1%)\n"
        "\n"
        f"#1 {YOURS} (you asked, {when(mine)})\n"
        "\n"
        f"**My notes from our chats**: 2 notes ({used} of 12,000 characters, 1%). {PER_MESSAGE} /profile notes "
        "lists them.\n"
        "\n" + prompts.PROFILE_FOOTER
    )


async def test_one_note_is_one_note_and_the_limits_shown_are_the_profiles_own(store: Store) -> None:
    profile, commands = make(store, Profile(store, max_chars=1_000, notes_max_chars=2_000, notes_per_message=900))
    for n in range(5):
        profile.add(f"The user keeps a fact number {n:03d} of fifty characters x.", source_kind="remember")
    profile.add(NOTE, source_kind="chat")
    listing = await commands.handle(command(store, "/profile"))
    lines = listing.splitlines()
    used = profile.used_chars()
    assert (
        used == 275 and lines[0] == f"**Your profile**: I see it with every message ({used} of 1,000 characters, 28%)"
    )
    assert (
        f"**My notes from our chats**: 1 note ({len(NOTE)} of 2,000 characters, 2%). I see up to 900 characters of "
        "them with each message, the ones that match it first, then the newest. /profile notes lists them."
    ) in lines
    assert "#6 " not in listing  # the note itself is in /profile notes only


async def test_profile_with_only_notes_says_nothing_of_yours_yet(store: Store) -> None:
    profile, commands = make(store)
    profile.add(NOTE, source_kind="chat")
    assert await commands.handle(command(store, "/profile")) == (
        "**Your profile**: I see it with every message (0 of 4,000 characters, 0%)\n"
        "\n" + prompts.PROFILE_NONE_YET + "\n"
        "\n"
        f"**My notes from our chats**: 1 note ({len(NOTE)} of 12,000 characters, 0%). {PER_MESSAGE} /profile notes "
        "lists them.\n"
        "\n" + prompts.PROFILE_FOOTER
    )


async def test_profile_without_notes_has_no_notes_line(store: Store) -> None:
    profile, commands = make(store)
    profile.add("Work: The user works as a product designer.", source_kind="link")
    profile.add("About: The user's name is Alex Example.", source_kind="remember")
    work, name = profile.facts()
    assert await commands.handle(command(store, "/profile")) == (
        "**Your profile**: I see it with every message (82 of 4,000 characters, 2%)\n"
        "\n"
        "**About**\n"
        f"#2 The user's name is Alex Example. (you asked, {when(name)})\n"
        "\n"
        "**Work**\n"
        f"#1 The user works as a product designer. (from a link, {when(work)})\n"
        "\n" + prompts.PROFILE_FOOTER
    )


async def test_an_empty_profile_says_so_for_both_commands(store: Store) -> None:
    _, commands = make(store)
    assert (await commands.handle(command(store, "/profile"))).startswith("Your profile is empty")
    assert await commands.handle(command(store, "/profile notes", T0 + 1)) == prompts.PROFILE_NO_NOTES


async def test_an_install_over_the_users_limit_shows_the_real_fill(store: Store) -> None:
    roomy = Profile(store, max_chars=6_000)
    for n in range(5):
        roomy.add(f"The user keeps an old fact number {n:03d} that is {'long ' * 40}enough.", source_kind="remember")
    _, commands = make(store, Profile(store, max_chars=1_000))
    used = Profile(store).used_chars()
    first = (await commands.handle(command(store, "/profile"))).splitlines()[0]
    assert first == (
        f"**Your profile**: I see it with every message ({used:,} of 1,000 characters, {round(used / 10)}%)"
    )


# /profile notes


async def test_profile_notes_lists_her_notes_by_heading_then_the_moved_facts_last(store: Store) -> None:
    """Her own notes show only their date (all are learned from chat); a fact of the user's moved there to make room
    keeps where it came from, in its own last group, with its whole text (its category word included)."""
    profile, commands = make(store, Profile(store, max_chars=90))
    aunt = profile.add("Family: The user's aunt Ruth visits every May.", source_kind="remember").added[0]
    ops = [Op("remove", fact_id=aunt, reason="room"), Op("add", text=YOURS)]
    assert profile.apply(ops, source_kind="remember", remove_to_notes=True).moved == [aunt]
    profile.add("Music: The user plays bass in a band.", source_kind="chat")
    profile.add(TEA, source_kind="chat")
    facts = {fact.id: fact for fact in profile.facts()}
    music, tea = (fact.id for fact in profile.notes() if fact.source_kind == "chat")
    used = profile.notes_used_chars()
    assert await commands.handle(command(store, "/profile notes")) == (
        f"**My notes from our chats** ({used} of 12,000 characters, 1%): {PER_MESSAGE}\n"
        "\n"
        "**Music**\n"
        f"#{music} The user plays bass in a band. ({when(facts[music])})\n"
        "\n"
        "**Other**\n"
        f"#{tea} {TEA} ({when(facts[tea])})\n"
        "\n"
        "**Moved here from your profile to make room**\n"
        f"#{aunt} Family: The user's aunt Ruth visits every May. (you asked, {when(facts[aunt])})\n"
        "\n" + prompts.PROFILE_FOOTER
    )
    listing = await commands.handle(command(store, "/profile", T0 + 1))
    assert f"#{aunt} " not in listing and "Moved here" not in listing
    assert f"**My notes from our chats**: 3 notes ({used} of 12,000 characters, 1%)." in listing


async def test_profile_notes_without_categories_has_no_headings_and_a_link_fact_says_so(store: Store) -> None:
    profile, commands = make(store, Profile(store, max_chars=60))
    page = profile.add("The user's band is called Example Lights.", source_kind="link").added[0]
    ops = [Op("remove", fact_id=page, reason="room"), Op("add", text="The user likes jazz.")]
    profile.apply(ops, source_kind="remember", remove_to_notes=True)
    profile.add(NOTE, source_kind="chat")
    note = profile.notes()[-1]
    shown = (await commands.handle(command(store, "/profile notes"))).split("\n\n")
    assert shown[1] == f"#{note.id} {NOTE} ({when(note)})"
    assert shown[2].splitlines()[1].startswith(f"#{page} The user's band is called Example Lights. (from a link, ")


async def test_profile_notes_with_only_moved_facts_shows_only_that_group(store: Store) -> None:
    profile, commands = make(store, Profile(store, max_chars=60))
    first = profile.add("The user's band is called Example Lights.", source_kind="remember").added[0]
    profile.apply(
        [Op("remove", fact_id=first, reason="room"), Op("add", text="The user likes jazz.")],
        source_kind="remember",
        remove_to_notes=True,
    )
    blocks = (await commands.handle(command(store, "/profile notes"))).split("\n\n")
    assert blocks[1].startswith("**Moved here from your profile to make room**\n") and len(blocks) == 3


async def test_profile_notes_with_none_of_her_notes_says_so(store: Store) -> None:
    profile, commands = make(store)
    profile.add(YOURS, source_kind="remember")
    assert await commands.handle(command(store, "/profile notes")) == prompts.PROFILE_NO_NOTES


@pytest.mark.parametrize("typed", ["/profile NOTES", "/profile  notes ", "/Profile notes"])
async def test_profile_notes_takes_any_case_and_spaces(store: Store, typed: str) -> None:
    profile, commands = make(store)
    profile.add(NOTE, source_kind="chat")
    assert (await commands.handle(command(store, typed))).startswith("**My notes from our chats** (")


async def test_profile_with_other_words_is_the_profile(store: Store) -> None:
    profile, commands = make(store)
    profile.add(YOURS, source_kind="remember")
    plain = await commands.handle(command(store, "/profile"))
    assert await commands.handle(command(store, "/profile please", T0 + 1)) == plain


# where /profile notes is named


def test_help_and_the_learning_digest_name_profile_notes() -> None:
    assert "/profile notes" in prompts.HELP
    assert "/profile notes shows them" in prompts.DIGEST.format(count=2, things="things")


# her notes for a message: English's small words never match


def notes_with(store: Store) -> Profile:
    profile = Profile(store)
    for note in [
        "Family: The user takes Mia to cello lessons on Wednesdays.",
        "Home: The user is renovating the kitchen in November.",
        "Social: The user meets friends for climbing on Sundays.",
        "Work: The user has a deadline on Friday for the app.",
        "Family: The user's son goes to school in Utrecht.",
        "Music: The user tunes the guitar a half step down.",
        "Home: The user has lights in the living room.",
        "Study: The user practises Dutch twice a week.",
        "Humour: The user likes dry jokes.",
    ]:
        profile.add(note, source_kind="chat")
    return profile


@pytest.mark.parametrize(
    ("message", "found"),
    [
        ("is it going to rain?", []),
        ("what should I do on Thursday?", []),
        ("how is the cello going for Mia?", ["Family: The user takes Mia to cello lessons on Wednesdays."]),
        (
            "my son's school starts at 8",
            ["Family: The user's son goes to school in Utrecht."],
        ),  # "son" is no small word
        ("Wat doe ik op zondag met de kinderen?", []),
    ],
)
def test_her_notes_match_a_message_without_small_words(store: Store, message: str, found: list[str]) -> None:
    assert [note.text for note in notes_with(store).notes_matching(message)] == found


def test_a_search_for_a_small_word_alone_finds_no_note(store: Store) -> None:
    assert notes_with(store).notes_matching("the", searched=True) == []
