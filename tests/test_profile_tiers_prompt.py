"""What Gemma sees of the profile's second part (owner, 16 Oct): her notes come only in the newest incoming message
(`ContextBuilder.build`; right before or right after its words, the message itself unchanged), never on a practice
turn, so the system prompt and the earlier messages stay byte for byte the same for Ollama's prompt cache; and how
`Profile.notes_for` / `notes_matching` pick them."""

from __future__ import annotations

import math
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant.brain import Brain
from local_assistant.context import _MESSAGE_OVERHEAD, DEFAULT_CHARS_PER_TOKEN, ContextBuilder
from local_assistant.fetcher import Page
from local_assistant.profile import COMMON_FROM_NOTES, Profile
from local_assistant.reminders import Reminders
from local_assistant.store import Conversation, Message, Store
from local_assistant.tools import Tools
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text

YOURS = "Family: The user's daughter Mia plays the violin."
GARDEN = "Home: The user grows tomatoes in the back garden."
ANNA = "Family: The user's sister Anna lives in Lisbon."
IMAGE = "data:image/jpeg;base64,AAAA"


def setup(store: Store, *, per_message: int = 1_500, **kwargs: Any) -> tuple[Profile, ContextBuilder]:
    profile = Profile(store, max_chars=1_000, notes_max_chars=20_000, notes_per_message=per_message)
    return profile, ContextBuilder(store, profile, TZ, **kwargs)


def say(store: Store, conv: Conversation, words: str, ts: int) -> Message:
    message_id = store.add_incoming(conv.id, ME, ts, "text", words)
    assert message_id is not None
    return store.message(message_id)


def answer(store: Store, conv: Conversation, words: str, to: Message, ts: int) -> Message:
    out = store.add_outgoing(conv.id, ME, "text", words, to.id)
    store.mark_sent(out, ts)
    return store.message(out)


def text_of(chat: dict[str, Any]) -> str:
    """A user message's text (its text part when it carries a photo)."""
    content = chat["content"]
    if isinstance(content, list):
        [part] = [part for part in content if part.get("type") == "text"]
        return str(part["text"])
    return str(content)


def without_block(chat: dict[str, Any], block: str) -> str:
    """The message's text without its notes block, which sits right before or right after its words."""
    content = text_of(chat)
    assert block and content.count(block) == 1
    if content.startswith(f"{block}\n\n"):
        return content[len(block) + 2 :]
    assert content.endswith(f"\n\n{block}"), content
    return content[: -len(block) - 2]


def has_notes(chat: dict[str, Any]) -> bool:
    content = chat["content"]
    if isinstance(content, list):
        return any(prompts.NOTES_HEADER in str(part.get("text", "")) for part in content)
    return prompts.NOTES_HEADER in str(content)


def picked(profile: Profile, words: str, budget: int | None = None) -> list[str]:
    return [note.text for note in profile.notes_for(words, budget=budget)]


async def no_fetch(url: str) -> Page:
    raise AssertionError("no fetch expected")


# where the notes go


def notes_of(profile: Profile, context: ContextBuilder, chat: dict[str, Any], message: Message) -> str:
    """The notes block in `chat` (the newest message as built), checked: the message's own text is byte for byte
    what it is without notes, and the block is the profile's for the message's words."""
    block = profile.notes_block(message.text)
    assert without_block(chat, block) == text_of(context.to_chat(message, newest=True))
    return block


def test_the_notes_go_to_the_newest_incoming_message_also_when_her_reply_comes_last(store: Store) -> None:
    profile, context = setup(store)
    profile.add(GARDEN, source_kind="chat")
    conv = store.current_conversation()
    asked = say(store, conv, "Should I water the garden today?", T0)
    answer(store, conv, "Yes, it is dry.", asked, T0 + 5_000)
    built = context.build(conv)
    assert [chat["role"] for chat in built] == ["system", "user", "assistant"]
    assert notes_of(profile, context, built[1], asked) == f"{prompts.NOTES_HEADER}\n#1 {GARDEN}"
    assert built[2] == {"role": "assistant", "content": "Yes, it is dry."}  # her reply exactly as she sent it


def test_a_photo_message_carries_its_notes_in_its_text_part_and_the_image_stays_as_it_was(store: Store) -> None:
    profile, context = setup(store)
    profile.add(GARDEN, source_kind="chat")
    conv = store.current_conversation()
    photo = say(store, conv, "[photo] what is wrong with my tomatoes?", T0)
    newest = context.build(conv, {photo.id: [IMAGE]})[-1]
    text_part, image_part = newest["content"]
    plain = context.to_chat(photo, [IMAGE], newest=True)["content"][0]["text"]
    assert text_part["type"] == "text" and without_block(newest, profile.notes_block(photo.text)) == plain
    assert image_part == {"type": "image_url", "image_url": {"url": IMAGE}}
    without = context.build(conv, {photo.id: [IMAGE]}, notes=False)[-1]
    assert without["content"] == [{"type": "text", "text": plain}, image_part] and not has_notes(without)


def test_a_call_turn_gets_its_notes_and_the_calls_system_prompt_stays_the_same(store: Store) -> None:
    profile, context = setup(store)
    profile.add(ANNA, source_kind="chat")
    conv = store.current_conversation()
    turn = say(store, conv, f"{prompts.CALL_MARK} is Anna still in Lisbon?", T0)
    built = context.build(conv, call=True, glance="CALL GLANCE: 2 events today")
    assert notes_of(profile, context, built[-1], turn).endswith(f"#1 {ANNA}")
    assert built[0] == context.build(conv, call=True, glance="CALL GLANCE: 2 events today", notes=False)[0]
    assert built[0]["content"].endswith("CALL GLANCE: 2 events today")  # the glance stays last, the notes elsewhere
    assert ANNA not in built[0]["content"]


def test_a_practice_turn_carries_no_notes_in_any_message(store: Store) -> None:
    profile, context = setup(store)
    profile.add(ANNA, source_kind="chat")
    conv = store.current_conversation()
    first = say(store, conv, "Anna woont in Lissabon.", T0)
    answer(store, conv, "Goed zo!", first, T0 + 5_000)
    last = say(store, conv, "Anna Lisbon Anna Lisbon", T0 + 60_000)
    practice = context.build(conv, rules="practice rules", notes=False)
    assert not any(has_notes(chat) for chat in practice)
    assert practice[-1] == context.to_chat(last, newest=True)
    assert practice[1:] == context.build(conv, notes=False)[1:]  # only the rules differ from a chat turn


def test_a_summary_stays_in_the_system_prompt_and_the_notes_in_the_newest_message(store: Store) -> None:
    profile, context = setup(store)
    profile.add(YOURS, source_kind="remember")
    profile.add(ANNA, source_kind="chat")
    conv = store.current_conversation()
    old = say(store, conv, "We talked about Lisbon.", T0)
    store.set_summary(conv.id, "They talked about a trip to Lisbon.", old.seq or 0)
    newest = say(store, conv, "When does Anna land in Lisbon?", T0 + 60_000)
    built = context.build(store.conversation(conv.id))
    assert built[0]["content"].endswith("EARLIER IN THIS CONVERSATION (summary)\nThey talked about a trip to Lisbon.")
    assert "#1 The user's daughter Mia plays the violin." in built[0]["content"]  # under its category
    assert "Anna lives" not in built[0]["content"]
    assert len(built) == 2 and notes_of(profile, context, built[1], newest).endswith(f"#2 {ANNA}")


# the prompt cache: what comes before the newest message stays byte for byte the same


def test_the_system_prompt_is_the_same_with_or_without_notes_and_after_new_notes(store: Store) -> None:
    """The snapshot (and its count of her notes) is frozen until /new, a summary, /forget, /fix or a restart: a note
    learned between two messages changes the notes block, never the system prompt."""
    profile, context = setup(store)
    profile.add(YOURS, source_kind="remember")
    profile.add(GARDEN, source_kind="chat")
    conv = store.current_conversation()
    say(store, conv, "How are my tomatoes doing?", T0)
    first = context.build(conv)
    assert first[0] == context.build(conv, notes=False)[0]
    assert first[0]["content"].endswith(prompts.NOTES_IN_SNAPSHOT.format(count=1))
    profile.add(ANNA, source_kind="chat")
    profile.add("Work: The user teaches maths at a secondary school.", source_kind="chat")
    say(store, conv, "And is Anna still in Lisbon?", T0 + 60_000)
    second = context.build(conv)
    assert second[0] == first[0]  # still "1 note": the count waits for the next snapshot
    assert ANNA in second[-1]["content"]
    assert context.refresh_snapshot().endswith(prompts.NOTES_IN_SNAPSHOT.format(count=3))


def test_every_message_before_the_previous_newest_is_identical_between_two_builds(store: Store) -> None:
    profile, context = setup(store)
    for note in (GARDEN, ANNA):
        profile.add(note, source_kind="chat")
    conv = store.current_conversation()
    one = say(store, conv, "Anna's garden in Lisbon", T0)
    answer(store, conv, "Lovely.", one, T0 + 5_000)
    two = say(store, conv, "Tomatoes again", T0 + 60_000)
    first = context.build(conv)
    answer(store, conv, "They need sun.", two, T0 + 65_000)
    say(store, conv, "Anything else?", T0 + 120_000)
    second = context.build(conv)
    assert second[:3] == first[:3]  # system, the first message and her reply: the same bytes
    assert has_notes(first[3]) and not has_notes(second[3])  # the message before carries none any more
    assert second[3] == context.build(conv, notes=False)[3]
    assert [has_notes(chat) for chat in second] == [False] * (len(second) - 1) + [True]


def test_the_newest_message_after_the_emergency_trim_still_gets_its_notes(store: Store) -> None:
    profile, context = setup(store, window_tokens=60, emergency_tokens=120)
    profile.add(GARDEN, source_kind="chat")
    conv = store.current_conversation()
    ts = T0
    for n in range(8):
        asked = say(store, conv, f"Long message number {n} about nothing much at all, really nothing.", ts)
        answer(store, conv, f"Reply {n}, also about nothing at all, really nothing.", asked, ts + 1_000)
        ts += 60_000
    newest = say(store, conv, "Should I water the garden?", ts)
    trimmed = context.enforce_limit(conv)
    assert trimmed.summary_upto_seq > 0  # the oldest messages were dropped
    built = context.build(trimmed)
    assert 1 < len(built) < 18 and notes_of(profile, context, built[-1], newest).endswith(f"#1 {GARDEN}")
    assert sum(has_notes(chat) for chat in built) == 1


def test_notes_that_all_are_too_long_leave_the_message_exactly_as_it_was(store: Store) -> None:
    """No empty YOUR NOTES header: with no note that fits, the newest message is byte for byte the one without notes."""
    profile, context = setup(store, per_message=len(GARDEN) - 1)
    profile.add(GARDEN, source_kind="chat")
    conv = store.current_conversation()
    newest = say(store, conv, "Should I water the garden?", T0)
    assert profile.notes_block(newest.text) == ""
    assert context.build(conv) == context.build(conv, notes=False)


# token estimates: the window counts the stored messages; the ratio counts what Gemma got, notes included


def test_the_window_estimate_counts_stored_messages_and_calibration_counts_the_notes_sent(store: Store) -> None:
    profile, context = setup(store)
    conv = store.current_conversation()
    say(store, conv, "Should I water the garden?", T0)
    window = context.window(conv)
    before = context.window_tokens_of(window)
    profile.add(GARDEN, source_kind="chat")
    assert context.window_tokens_of(window) == before
    assert before == math.ceil(len(window[0].text) / DEFAULT_CHARS_PER_TOKEN) + _MESSAGE_OVERHEAD
    built = context.build(conv)
    chars = sum(len(str(chat["content"])) for chat in built)
    context.calibrate(built, prompt_tokens=chars // 4)
    expected = 0.7 * DEFAULT_CHARS_PER_TOKEN + 0.3 * max(1.5, min(5.0, chars / (chars // 4)))
    assert context.chars_per_token() == pytest.approx(round(expected, 3))
    assert context.estimate("x" * 7) == math.ceil(7 / context.chars_per_token())


# notes_for: the budget


@pytest.mark.parametrize("budget", [0, -1, -1_500])
def test_no_room_means_no_notes(store: Store, budget: int) -> None:
    profile, _ = setup(store, per_message=budget)
    profile.add(GARDEN, source_kind="chat")
    assert profile.notes_for("garden", budget=budget) == [] and profile.notes_block("garden") == ""
    assert picked(profile, "garden", budget=len(GARDEN)) == [GARDEN]  # a budget given wins over the profile's


def test_a_note_too_long_for_the_room_left_is_skipped_and_shorter_ones_still_come(store: Store) -> None:
    profile, _ = setup(store)
    short_old = "Other: The user likes figs."
    long_match = "Home: The user's garden has a greenhouse, a pond, an old shed and twelve apple trees by the wall."
    newest = "Music: The user plays the cello."
    for note in (short_old, long_match, newest):
        profile.add(note, source_kind="chat")
    budget = len(newest) + len(short_old)
    assert picked(profile, "garden greenhouse", budget=budget) == [newest, short_old]  # the match never cut
    assert picked(profile, "garden greenhouse", budget=len(long_match)) == [long_match]  # exactly the room: fits
    assert picked(profile, "garden greenhouse", budget=len(long_match) - 1) == [newest, short_old]


def test_with_many_notes_the_matches_come_first_then_the_newest_within_the_budget(store: Store) -> None:
    profile, _ = setup(store, per_message=1_500)
    for n in range(200):
        profile.add(f"Other: The user's note {n:03d} is about errand number {n:03d}.", source_kind="chat")
    profile.add(ANNA, source_kind="chat")
    for n in range(200, 260):
        profile.add(f"Other: The user's note {n:03d} is about errand number {n:03d}.", source_kind="chat")
    notes = profile.notes_for("Is Anna in Lisbon now? And errand 007?")
    texts = [note.text for note in notes]
    assert texts[:2] == [ANNA, "Other: The user's note 007 is about errand number 007."]  # 3 rare words, then 1
    rest = [note.id for note in notes[2:]]
    assert rest == sorted(rest, reverse=True) and rest[0] == max(fact.id for fact in profile.notes())
    assert len(set(texts)) == len(texts) and sum(map(len, texts)) <= 1_500
    room = 1_500 - sum(map(len, texts))
    assert room < min(len(note.text) for note in profile.notes() if note not in notes)  # nothing more would fit
    block = profile.notes_block("Is Anna in Lisbon now? And errand 007?")
    assert block.splitlines() == [prompts.NOTES_HEADER, *(f"#{note.id} {note.text}" for note in notes)]


# notes_matching: words, Unicode and the common-word rule


@pytest.mark.parametrize(
    ("note", "message"),
    [
        ("Family: Ο αδελφός του χρήστη, ο Νίκος, μένει στην Αθήνα.", "ΠΟΥ ΕΙΝΑΙ Ο ΝΙΚΟΣ;"),  # final sigma, no accents
        ("Family: Ο αδελφός του χρήστη, ο Νίκος, μένει στην Αθήνα.", "Πάμε Αθηνα;"),
        ("Social: The user meets friends at Café Zoë on Fridays.", "zoe cafe tonight?"),
        ("Home: The user's ĳsbaan pass is in the hallway.", "Waar is mijn ijsbaan pas?"),  # the Dutch ĳ ligature
        ("Work: The user's office is on the Hauptstraße.", "Parking near Hauptstrasse?"),
        ("Family: The user's sister Anna lives in Lisbon.", "Ａｎｎａ calls today"),  # fullwidth letters
        ("Family: The user's sister Anna lives in Lisbon.", "anna's birthday!!!"),  # punctuation, possessive
        ("Other: The user was born in 1984.", "what happened in 1984?"),  # numbers are words too
        ("Home: The user's e-mail backup runs weekly.", "my mail is slow"),
    ],
)
def test_a_note_is_found_whatever_the_case_accents_ligatures_or_punctuation(
    store: Store, note: str, message: str
) -> None:
    profile, _ = setup(store)
    profile.add(note, source_kind="chat")
    assert [fact.text for fact in profile.notes_matching(message)] == [note]


@pytest.mark.parametrize("message", ["e", "7?", "a b c d", "!!!", "", "🎻🎻", "_ _"])
def test_one_letter_words_digits_and_symbols_match_nothing(store: Store, message: str) -> None:
    profile, _ = setup(store)
    profile.add("Other: The user's PIN hint is e 7 a b c d.", source_kind="chat")
    assert profile.notes_matching(message) == []


@pytest.mark.parametrize(("count", "common"), [(3, False), (COMMON_FROM_NOTES, True), (5, True)])
def test_a_word_in_every_note_counts_until_there_are_four_notes(store: Store, count: int, common: bool) -> None:
    profile, _ = setup(store)
    for n in range(count):
        profile.add(f"Home: The user's garden bed {n} gets {'x' * (n + 2)} compost.", source_kind="chat")
    found = profile.notes_matching("garden")
    assert (found == []) is common
    if not common:
        assert [fact.id for fact in found] == sorted((fact.id for fact in profile.notes()), reverse=True)


@pytest.mark.parametrize(("count", "holding", "matches"), [(4, 2, True), (4, 3, False), (5, 2, True), (5, 3, False)])
def test_more_than_half_of_the_notes_makes_a_word_common(store: Store, count: int, holding: int, matches: bool) -> None:
    profile, _ = setup(store)
    for n in range(count):
        topic = "violin" if n < holding else f"topic{'q' * n}"
        profile.add(f"Music: The user's note {'z' * (n + 2)} mentions {topic}.", source_kind="chat")
    found = profile.notes_matching("violin")
    assert len(found) == (holding if matches else 0)


def test_ties_go_to_the_newest_and_a_rarer_or_second_shared_word_ranks_higher(store: Store) -> None:
    profile, _ = setup(store)
    oldest = profile.add("Home: The user keeps a kayak in the shed.", source_kind="chat").added[0]
    middle = profile.add("Home: The user's kayak paddle is broken.", source_kind="chat").added[0]
    newest = profile.add("Home: The user's bike is in the shed too.", source_kind="chat").added[0]
    profile.add("Music: The user sings in a choir.", source_kind="chat")
    profile.add("Work: The user commutes by train.", source_kind="chat")
    ids = [fact.id for fact in profile.notes_matching("kayak")]
    assert ids == [middle, oldest]  # the same weight: the newest first
    ids = [fact.id for fact in profile.notes_matching("kayak shed")]
    assert ids[0] == oldest and set(ids) == {oldest, middle, newest}  # both words beat one
    profile.add("Home: The user's kayak trailer is red.", source_kind="chat")
    ids = [fact.id for fact in profile.notes_matching("bike kayak")]
    assert ids[0] == newest  # "bike" is in 1 note, "kayak" in 3: the rarer word weighs more


# known bugs: each test passes once its bug is fixed


def test_the_notes_go_to_the_message_being_answered_after_a_call(store: Store) -> None:
    """Texts that come in during a call are answered after it (app: `_no_call.wait()`): the text was stored first
    (lower id, `defer_seq`), the call's turns got their seq at once, and the text gets its seq, last, only when it is
    answered. `build` picks the newest by id, so the last call turn (an earlier message, in the prompt cache) gets
    the notes and the year, and the message being answered gets neither."""
    profile, context = setup(store)
    profile.add(GARDEN, source_kind="chat")
    conv = store.current_conversation()
    held = store.add_incoming(conv.id, ME, T0, "text", "Is the garden shed locked?", defer_seq=True)
    assert held is not None
    turn = store.add_call_turn(conv.id, ME, f"{prompts.CALL_MARK} hello, a quick one about dinner")
    store.add_call_reply(conv.id, ME, "Sure, go ahead.", turn)
    store.assign_seq(held, conv.id)
    built = context.build(store.conversation(conv.id))
    assert built[-1]["role"] == "user" and "garden shed" in built[-1]["content"]  # the message answered, last
    assert not has_notes(built[-3])  # the call turn as it was sent
    assert has_notes(built[-1])


async def test_a_note_never_counts_as_the_user_asking_for_a_reminder(store: Store) -> None:
    """The user only said "hm"; her answer names a calendar entry, which the precise set passes
    (test_brain.test_no_guard_note_without_a_claim). A note of hers about waking up came with the message: code's
    "No reminder was set" note must not follow, the user never asked for one."""
    profile = Profile(store)
    context = ContextBuilder(store, profile, TZ)
    reminders = Reminders(store, TZ, clock=lambda: T0 / 1000)
    gemma = FakeGemma(text("Your call with Anna is set for 15:00 in the calendar."))
    brain = Brain(gemma, context, Tools(store, profile, TZ, no_fetch, reminders=reminders))
    profile.add("Home: The user wakes up at 6 on weekdays.", source_kind="chat")
    conv = store.current_conversation()
    reply = await brain.answer(conv, say(store, conv, "hm", T0))
    assert "wakes up at 6" in gemma.calls[0]["messages"][-1]["content"]  # the note came with the message
    assert reply.reminder == ""


@pytest.mark.parametrize(
    ("mark", "note"),
    [
        (prompts.CALL_MARK, "Work: The user has a call on Mondays."),
        (prompts.VOICE_MARK, "Work: The user's voice is hoarse a lot."),
        ("[photo]", "Home: The user frames every photo."),
    ],
)
def test_codes_mark_on_a_message_is_not_a_word_the_user_said(store: Store, mark: str, note: str) -> None:
    """Only one note fits: the message shares no word of the user's with any note, so the newest should come."""
    profile, _ = setup(store)
    profile.add(note, source_kind="chat")
    profile.add("Music: The user plays the cello.", source_kind="chat")
    for n in range(4):
        profile.add(f"Other: The user's errand {'k' * (n + 2)} is done.", source_kind="chat")
    newest = profile.notes()[-1].id
    assert all(len(fact.text) <= 40 for fact in profile.notes())
    notes = profile.notes_for(f"{mark} how was your weekend?", budget=40)
    assert [note.id for note in notes] == [newest]  # nothing shared but the mark: the newest note
