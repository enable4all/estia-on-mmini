import json
from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.brain import NOTHING_SAVED, Brain
from local_assistant.context import ContextBuilder
from local_assistant.fetcher import Page
from local_assistant.gemma import Completion, GemmaUnavailableError, ToolCall
from local_assistant.profile import Profile
from local_assistant.reminders import Reminders
from local_assistant.store import Message, Store
from local_assistant.tools import TOOL_SCHEMAS, Tools
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text, tool
from tests.test_devices import Home


async def no_fetch(url: str) -> Page:
    raise AssertionError("no fetch expected")


def make(store: Store, gemma: FakeGemma, max_tool_calls: int = 5) -> tuple[Profile, ContextBuilder, Brain]:
    profile = Profile(store)
    context = ContextBuilder(store, profile, TZ)
    tools = Tools(store, profile, TZ, no_fetch)
    return profile, context, Brain(gemma, context, tools, max_tool_calls=max_tool_calls)


def incoming(store: Store, text_: str) -> Message:
    conv = store.current_conversation()
    message_id = store.add_incoming(conv.id, ME, T0, "text", text_)
    assert message_id is not None
    return store.message(message_id)


async def test_plain_answer(store: Store) -> None:
    gemma = FakeGemma(text("Hi there!"))
    _, _, brain = make(store, gemma)
    message = incoming(store, "hello Gemma")
    reply = await brain.answer(store.current_conversation(), message)
    assert (reply.text, reply.tool_calls) == ("Hi there!", 0)
    [call] = gemma.calls
    assert call["tools"] == TOOL_SCHEMAS and call["max_tokens"] == 600 and call["reasoning_effort"] == "none"
    assert call["messages"][-1] == {"role": "user", "content": "[Thu 8 Oct 2026 09:32] hello Gemma"}


async def test_tool_loop_runs_tools_then_answers(store: Store) -> None:
    gemma = FakeGemma(tool("remember", fact="The user likes tea."), text("Noted: you like tea."))
    profile, _, brain = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "remember that I like tea"))
    assert (reply.text, reply.tool_calls) == ("Noted: you like tea.", 1)
    assert [f.text for f in profile.facts()] == ["The user likes tea."]
    second = gemma.calls[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["function"]["name"] == "remember"
    assert second[-1] == {"role": "tool", "tool_call_id": "call_remember", "content": "Saved as fact #1."}


async def test_tool_limit_forces_an_answer(store: Store) -> None:
    searches = [tool("search_history", call_id=f"c{i}", query="tea") for i in range(5)]
    gemma = FakeGemma(*searches, text("Here is what I found."))
    _, _, brain = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "what about tea?"))
    assert (reply.text, reply.tool_calls) == ("Here is what I found.", 5)
    assert gemma.calls[5]["tools"] is None


async def test_unknown_tool_and_bad_arguments_get_error_results(store: Store) -> None:
    calls = (ToolCall("c1", "web_search", '{"q": "weather"}'), ToolCall("c2", "remember", "{broken"))
    gemma = FakeGemma(Completion(content="", tool_calls=calls, prompt_tokens=50), text("I can't search the web."))
    _, _, brain = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "weather tomorrow?"))
    assert reply.text == "I can't search the web."
    results = [m for m in gemma.calls[1]["messages"] if m["role"] == "tool"]
    assert [r["tool_call_id"] for r in results] == ["c1", "c2"]
    assert all(r["content"].startswith("Error:") for r in results)


async def test_empty_answer_is_retried_once(store: Store) -> None:
    _, _, brain = make(store, FakeGemma(text(""), text("Hello!")))
    assert (await brain.answer(store.current_conversation(), incoming(store, "hi"))).text == "Hello!"


async def test_two_empty_answers_give_the_confused_note(store: Store) -> None:
    _, _, brain = make(store, FakeGemma(text(""), text("  ")))
    assert (await brain.answer(store.current_conversation(), incoming(store, "hi"))).text == prompts.CONFUSED


async def test_forget_refreshes_the_snapshot(store: Store) -> None:
    gemma = FakeGemma(tool("forget", fact="1"), text("Done, I forgot it."))
    profile, context, brain = make(store, gemma)
    profile.add("The user lives in Rotterdam.", source_kind="chat")
    assert "Rotterdam" in context.refresh_snapshot()
    await brain.answer(store.current_conversation(), incoming(store, "forget where I live"))
    assert "Rotterdam" not in context.snapshot()


async def test_gemma_errors_propagate(store: Store) -> None:
    _, _, brain = make(store, FakeGemma(GemmaUnavailableError("down")))
    with pytest.raises(GemmaUnavailableError):
        await brain.answer(store.current_conversation(), incoming(store, "hi"))


async def test_profile_changes_are_reported_by_code(store: Store) -> None:
    gemma = FakeGemma(tool("remember", fact="The user likes tea."), text("Noted: you like tea."))
    _, _, brain = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "remember that I like tea"))
    assert reply.notice == "Profile changes:\nSaved #1: The user likes tea. (/forget 1 undoes it)"


async def test_no_notice_without_profile_changes(store: Store) -> None:
    _, _, brain = make(store, FakeGemma(text("Hi!")))
    assert (await brain.answer(store.current_conversation(), incoming(store, "hi"))).notice == ""


async def test_profile_notices_written_by_gemma_are_removed(store: Store) -> None:
    fake = "Noted: you play piano.\n\nProfile changes:\nSaved #2: The user plays piano. (/forget 2 undoes it)"
    _, _, brain = make(store, FakeGemma(text(fake), text(fake)))  # stays wrong even after the correction
    reply = await brain.answer(store.current_conversation(), incoming(store, "remember that I play piano"))
    assert reply.text == "Noted: you play piano."  # the fake notice is removed
    assert reply.notice == NOTHING_SAVED  # and the bridge says what really happened


async def test_learning_from_a_link_gets_a_larger_tool_budget(store: Store) -> None:
    async def fetch(url: str) -> Page:
        lines = "\n".join(f"Fact {i}: value {i}" for i in range(8))
        return Page(url=url, final_url=url, title="Profile", text=lines, content_type="text/plain")

    link = "https://example.org/me"
    script = [tool("learn_link", url=link)] + [
        tool("remember", call_id=f"r{i}", fact=f"The user has trait number {i}.") for i in range(8)
    ]
    gemma = FakeGemma(*script, text("Learned 8 facts."))
    profile = Profile(store)
    context = ContextBuilder(store, profile, TZ)
    brain = Brain(gemma, context, Tools(store, profile, TZ, fetch), max_tool_calls=5, learn_tool_calls=10)
    reply = await brain.answer(store.current_conversation(), incoming(store, f"learn from {link}"))
    assert reply.tool_calls == 9 and len(profile.facts()) == 8


async def test_noted_without_saving_gets_one_correction(store: Store) -> None:
    gemma = FakeGemma(
        text("Noted: you play piano."),  # claims a save without calling remember
        tool("remember", fact="The user plays piano."),
        text("Noted: you play piano."),
    )
    profile, _, brain = make(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "remember that I play piano"))
    assert [f.text for f in profile.facts()] == ["The user plays piano."]
    assert gemma.calls[1]["messages"][-1] == {"role": "user", "content": prompts.NOTED_NUDGE}
    assert reply.notice.startswith("Profile changes:\nSaved #1")


async def test_an_empty_answer_is_retried_with_a_nudge(store: Store) -> None:
    gemma = FakeGemma(text(""), text("Hello!"))
    _, _, brain = make(store, gemma)
    assert (await brain.answer(store.current_conversation(), incoming(store, "hi"))).text == "Hello!"
    assert gemma.calls[1]["messages"][-1] == {"role": "user", "content": prompts.EMPTY_NUDGE}


async def test_a_photo_goes_with_the_current_message_through_the_tool_loop(store: Store) -> None:
    gemma = FakeGemma(tool("search_history", query="wallpaper"), text("It is a blue wallpaper."))
    _, _, brain = make(store, gemma)
    message = incoming(store, "[photo] what colour is this?")
    reply = await brain.answer(store.current_conversation(), message, images=["data:image/jpeg;base64,/9j/AAAA"])
    assert reply.text == "It is a blue wallpaper."
    for call in gemma.calls:  # the image stays in the request after the tool call
        user = [m for m in call["messages"] if m["role"] == "user"][-1]
        assert user["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/AAAA"}}


async def test_recent_photos_are_shown_with_their_own_message(store: Store) -> None:
    gemma = FakeGemma(text("It was blue."))
    _, _, brain = make(store, gemma)
    photo = incoming(store, "[photo] what is this?")
    follow_up = store.add_incoming(store.current_conversation().id, ME, T0 + 60_000, "text", "what colour was it?")
    assert follow_up is not None
    uri = "data:image/jpeg;base64,/9j/AAAA"
    await brain.answer(store.current_conversation(), store.message(follow_up), recent={photo.id: [uri]})
    users = [m for m in gemma.calls[0]["messages"] if m["role"] == "user"]
    assert users[0]["content"][1] == {"type": "image_url", "image_url": {"url": uri}}
    assert isinstance(users[-1]["content"], str)


async def test_turns_with_images_do_not_calibrate_the_token_estimate(store: Store) -> None:
    gemma = FakeGemma(text("A photo.", prompt_tokens=50_000), text("Hi.", prompt_tokens=50_000))
    _, context, brain = make(store, gemma)
    before = context.chars_per_token()
    message = incoming(store, "[photo] what is this?")
    await brain.answer(store.current_conversation(), message, ["data:image/jpeg;base64,/9j/AAAA"])
    assert context.chars_per_token() == before  # image tokens would distort the ratio
    await brain.answer(store.current_conversation(), message)
    assert context.chars_per_token() != before


async def test_gemma_gets_the_tools_this_setup_has(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Ok."))
    profile = Profile(store)
    context = ContextBuilder(store, profile, TZ)
    tools = Tools(store, profile, TZ, no_fetch, devices=Home(tmp_path, monkeypatch).make())
    await Brain(gemma, context, tools).answer(store.current_conversation(), incoming(store, "lights?"))
    assert gemma.calls[0]["tools"] == tools.schemas and len(tools.schemas) == len(TOOL_SCHEMAS) + 2


async def test_a_call_answer_is_shorter_and_says_when_a_slow_tool_runs(store: Store) -> None:
    gemma = FakeGemma(
        tool("remember", fact="The user likes tea."),
        tool("search_history", query="tea"),
        text("Found it."),
    )
    _, _, brain = make(store, gemma)
    ran: list[str] = []
    reply = await brain.answer(
        store.current_conversation(), incoming(store, "[call] tea?"), max_tokens=220, on_tools=lambda: ran.append("x")
    )
    assert reply.text == "Found it."
    assert [call["max_tokens"] for call in gemma.calls] == [220, 220, 220]
    assert ran == ["x"]  # once, for the search (remember is instant, so no "let me check" for it)


async def test_a_call_answer_can_end_the_call(store: Store) -> None:
    gemma = FakeGemma(tool("end_call"), text("Bye, talk soon!"))
    profile = Profile(store)
    context = ContextBuilder(store, profile, TZ)
    brain = Brain(gemma, context, Tools(store, profile, TZ, no_fetch, calls=True))
    conversation = store.current_conversation()
    said = store.add_call_turn(conversation.id, ME, "[call] that's all for today")
    reply = await brain.answer(conversation, store.message(said))
    assert reply.text == "Bye, talk soon!" and reply.end_call


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("Sure thing. Have a great trip!  end_call", "Sure thing. Have a great trip!"),
        ("Bye for now! (end_call)", "Bye for now!"),
        ("Bye! `end_call()`", "Bye!"),
        ("Bye! [end_call]", "Bye!"),
    ],
)
async def test_end_call_written_as_text_ends_the_call_and_is_not_said(store: Store, written: str, spoken: str) -> None:
    # live 2026-10-10: Gemma wrote "end_call" into her goodbye instead of calling the tool
    profile = Profile(store)
    brain = Brain(
        FakeGemma(text(written)), ContextBuilder(store, profile, TZ), Tools(store, profile, TZ, no_fetch, calls=True)
    )
    conversation = store.current_conversation()
    said = store.add_call_turn(conversation.id, ME, "[call] okay, can you hang up?")
    reply = await brain.answer(conversation, store.message(said))
    assert reply.text == spoken and reply.end_call


async def test_end_call_named_inside_her_answer_is_left_out_but_ends_nothing(store: Store) -> None:
    # only an end_call written at the end of her answer stands for the tool; a mention in the middle is talk
    profile = Profile(store)
    tools = Tools(store, profile, TZ, no_fetch, calls=True)
    brain = Brain(
        FakeGemma(text("I use end_call when you say goodbye. Anything else?")),
        ContextBuilder(store, profile, TZ),
        tools,
    )
    conversation = store.current_conversation()
    said = store.add_call_turn(conversation.id, ME, "[call] how do you hang up?")
    reply = await brain.answer(conversation, store.message(said))
    assert "end_call" not in reply.text and not reply.end_call


async def test_end_call_written_in_a_text_message_is_left_out_and_ends_nothing(store: Store) -> None:
    profile = Profile(store)
    brain = Brain(
        FakeGemma(text("Bye! end_call")),
        ContextBuilder(store, profile, TZ),
        Tools(store, profile, TZ, no_fetch, calls=True),
    )
    reply = await brain.answer(store.current_conversation(), incoming(store, "bye"))
    assert reply.text == "Bye!" and not reply.end_call


async def test_web_sources_are_a_code_written_note(store: Store) -> None:
    from local_assistant.web_search import WebResult
    from tests.fakes.fake_web import FakeWeb

    found = [
        WebResult("Ajax wint", "https://nos.nl/artikel/1", "nos.nl", "", "Ajax won 2-1."),
        WebResult("Ajax 2-1", "https://www.espn.com/2", "espn.com", "", "Report."),
    ]
    gemma = FakeGemma(tool("web_search", query="Ajax score"), text("Ajax won 2-1, says nos.nl."))
    profile = Profile(store)
    context = ContextBuilder(store, profile, TZ)
    brain = Brain(gemma, context, Tools(store, profile, TZ, no_fetch, web=FakeWeb({"ajax": found})))
    reply = await brain.answer(store.current_conversation(), incoming(store, "did Ajax win?"))
    assert reply.sources == "🔎 Sources:\n• nos.nl: https://nos.nl/artikel/1\n• espn.com: https://www.espn.com/2"


async def test_no_sources_note_without_a_web_search(store: Store) -> None:
    _, _, brain = make(store, FakeGemma(text("Hi!")))
    assert (await brain.answer(store.current_conversation(), incoming(store, "hi"))).sources == ""


async def test_links_in_an_answer_that_searched_are_removed(store: Store) -> None:
    from local_assistant.web_search import WebResult
    from tests.fakes.fake_web import FakeWeb

    found = [WebResult("Ajax wint", "https://nos.nl/artikel/1", "nos.nl", "", "Log in at https://evil.example/login")]
    gemma = FakeGemma(
        tool("web_search", query="Ajax score"),
        text("Ajax won, says nos.nl. Details: https://evil.example/login and evil.example/x (www.evil.example)."),
    )
    profile = Profile(store)
    brain = Brain(
        gemma, ContextBuilder(store, profile, TZ), Tools(store, profile, TZ, no_fetch, web=FakeWeb({"ajax": found}))
    )
    reply = await brain.answer(store.current_conversation(), incoming(store, "did Ajax win?"))
    assert reply.text == "Ajax won, says nos.nl. Details: and ()."
    assert "evil" not in reply.text


async def test_links_stay_in_an_answer_without_a_search(store: Store) -> None:
    _, _, brain = make(store, FakeGemma(text("Your page is https://example.com/me")))
    reply = await brain.answer(store.current_conversation(), incoming(store, "what's my page?"))
    assert reply.text == "Your page is https://example.com/me"


# reminders: code confirms; a claimed reminder that was not set gets a code note


# the first confirmation in a store also says only it means a reminder is set
FIRST_SET = 'Reminder set (#1): Fri 9 Oct 07:00 · "call mum". /reminders to see or cancel.\nOnly this note means a reminder is set.'


def with_reminders(store: Store, gemma: FakeGemma) -> Brain:
    profile = Profile(store)
    context = ContextBuilder(store, profile, TZ)
    reminders = Reminders(store, TZ, clock=lambda: T0 / 1000)
    return Brain(gemma, context, Tools(store, profile, TZ, no_fetch, reminders=reminders))


async def test_a_set_reminder_comes_back_as_codes_note_and_spoken_line(store: Store) -> None:
    gemma = FakeGemma(tool("set_reminder", at="2026-10-09 07:00", text="call mum"), text("Sure, I'll remind you."))
    brain = with_reminders(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "remind me tomorrow at 7 to call mum"))
    assert reply.text == "Sure, I'll remind you."
    assert reply.reminder == FIRST_SET
    assert reply.reminder_spoken == "Reminder set for Friday 9 October, 07:00."


@pytest.mark.parametrize(
    "claim",
    [
        "I'll remind you tomorrow at 7.",
        "Done, reminder set for 7:00.",
        "I’ll call you at 6:30 to wake you up.",
        "I have set a reminder for you.",
        "Your reminder has been scheduled.",
        "I will wake you at seven.",
        "I've noted the reminder for 17:00.",
        "Ik bel je morgen om 6:30.",  # in any of the 11 languages, whatever the language setting
        "Ich rufe dich morgen um 6:30 an.",
        "Je te rappellerai à 17 h.",
        "Напоминание установлено на 17:00.",
    ],
)
async def test_a_claimed_reminder_without_a_set_gets_the_guard_note(store: Store, claim: str) -> None:
    brain = with_reminders(store, FakeGemma(text(claim)))
    reply = await brain.answer(store.current_conversation(), incoming(store, "remind me at 7"))
    assert reply.text == claim and reply.reminder == prompts.REMINDER_GUARD


@pytest.mark.parametrize(
    "answer",
    [
        "I couldn't set a reminder: that time has passed.",
        "No reminder was set, sorry.",
        "Should I set a reminder for that?",
        "Call me back when you have a minute.",
        "Remind me what the dentist said?",
        # the guard's set is precise: never an ordinary sentence (the evals judge with the broad set)
        "Your call with Anna is set for 15:00 in the calendar.",
        "The call is scheduled for 15:00 according to your calendar.",
        "All set for 7!",
    ],
)
async def test_no_guard_note_without_a_claim(store: Store, answer: str) -> None:
    brain = with_reminders(store, FakeGemma(text(answer)))
    reply = await brain.answer(store.current_conversation(), incoming(store, "hm"))
    assert reply.reminder == ""


@pytest.mark.parametrize(
    ("fact", "answer"),
    [
        ("The user starts work at 9:00.", "Noted: you start work at 9:00."),
        ("The user likes a wake-up call at 7 on weekdays.", "Noted: you like a wake-up call at 7 on weekdays."),
    ],
)
async def test_the_noted_fact_after_a_remember_is_no_reminder_claim(store: Store, fact: str, answer: str) -> None:
    gemma = FakeGemma(tool("remember", fact=fact), text(answer))
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "remember that"))
    assert reply.notice.startswith("Profile changes:") and reply.reminder == ""


async def test_a_claim_beside_the_noted_fact_still_gets_the_note(store: Store) -> None:
    gemma = FakeGemma(
        tool("remember", fact="The user starts work at 9:00."),
        text("Noted: you start work at 9:00. I'll call you at 8:00 tomorrow."),
    )
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "remember that"))
    assert reply.reminder == prompts.REMINDER_GUARD


@pytest.mark.parametrize(
    "answer",
    [
        "Your wake-up call is set for 7:00 tomorrow (#1).",
        "Yes, you're set: I'll call you at 7am tomorrow.",
        "Reminder #1 is set: I'll call you at 7 tomorrow.",
    ],
)
async def test_talk_about_reminders_that_exist_after_listing_them_gets_no_note(store: Store, answer: str) -> None:
    Reminders(store, TZ, calls=True, clock=lambda: T0 / 1000).set(ME, at="2026-10-09 07:00", text="wake up", call=True)
    gemma = FakeGemma(tool("list_reminders"), text(answer))
    brain = with_reminders(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "do I have a wake-up call tomorrow?"))
    assert reply.reminder == "" and reply.reminder_spoken == ""


@pytest.mark.parametrize(
    ("existing", "answer"),
    [
        (True, "You have a wake-up call at 7:00, and I've set a new reminder for 17:00 too."),
        (True, "Done, I'll remind you at 5pm as well."),
        (False, "You had none, so I've set a reminder for 17:00."),
    ],
)
async def test_a_new_claim_after_a_listing_still_gets_a_note(store: Store, existing: bool, answer: str) -> None:
    # no blanket pass for a listing: only talk that names a listed reminder's time or number restates it
    if existing:
        Reminders(store, TZ, clock=lambda: T0 / 1000).set(ME, at="2026-10-09 07:00", text="wake up")
    gemma = FakeGemma(tool("list_reminders"), text(answer))
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "and at 17:00?"))
    assert reply.reminder == prompts.REMINDER_GUARD_NEW == "No new reminder was set."
    assert reply.reminder_spoken == prompts.REMINDER_GUARD_NEW and store.open_reminders(ME)[existing:] == []


async def test_a_refused_set_and_a_claim_leaves_only_codes_line(store: Store) -> None:
    gemma = FakeGemma(tool("set_reminder", at="2026-10-07 07:00", text="old"), text("Reminder set!"))
    brain = with_reminders(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "remind me last week"))
    assert reply.text == "No reminder was set: that time has passed." and store.open_reminders(ME) == []
    assert reply.reminder == "" and reply.reminder_spoken == ""  # the answer itself says it: no second note


# when set_reminder stored nothing in an answer, in any language: her sentences that mention a reminder, waking or a
# call are left out, the others stay, and code's line why comes after them (only that line when nothing is left)

PAST = {"at": "2026-10-07 07:00", "text": "call mum"}
NOT_SET = "No reminder was set: that time has passed."


@pytest.mark.parametrize(
    ("arguments", "answer", "sent"),
    [
        # what the real model wrote in English after a refused set
        (PAST, "I've set a reminder for 17:00 to let you know about it.", NOT_SET),
        (PAST, "I've noted the reminder for 17:00.", NOT_SET),
        (PAST, "Got it! I've prepared a reminder for 17:00 so you won't miss it.", f"Got it!\n{NOT_SET}"),
        # and in Dutch
        (PAST, "Ik heb de herinnering om 17:00 uur voor je ingesteld.", NOT_SET),
        ({"at": "", "text": "mama bellen"}, "Je herinnering staat klaar.", "No reminder was set: no time was given."),
        (PAST, "Sorry, 7:00 has passed. Tomorrow instead?", f"Sorry, 7:00 has passed. Tomorrow instead?\n{NOT_SET}"),
    ],
)
async def test_a_set_reminder_that_stored_nothing_drops_her_reminder_talk_and_adds_codes_line(
    store: Store, arguments: dict[str, str], answer: str, sent: str
) -> None:
    gemma = FakeGemma(tool("set_reminder", **arguments), text(answer))
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "remind me"))
    assert reply.text == sent and reply.not_set == sent.splitlines()[-1] and store.open_reminders(ME) == []
    assert reply.reminder == "" and reply.reminder_spoken == ""


@pytest.mark.parametrize("language", ["en", "nl"])
async def test_a_mixed_answer_keeps_her_other_content(store: Store, language: str) -> None:
    # whatever the language setting: the sentences are read in all 11 languages
    answers = {
        "en": "I found one invoice mail from Acme Billing for 120 EUR, due on 16 October.\n\n"
        "I've noted the reminder for 17:00.",
        "nl": "Er staat een factuur van Acme Billing in voor 120 euro, uiterlijk 16 oktober.\n\n"
        "Ik heb de herinnering voor 17:00 uur voor je ingesteld.",
    }
    gemma = FakeGemma(tool("set_reminder", **PAST), text(answers[language]))
    reply = await with_reminders(store, gemma).answer(
        store.current_conversation(), incoming(store, "check my mail and remind me")
    )
    summary = answers[language].split("\n\n")[0]
    assert reply.text == f"{summary}\n{NOT_SET}" and reply.hers == summary


async def test_a_noted_fact_stays_beside_codes_line(store: Store) -> None:
    gemma = FakeGemma(
        tool("remember", fact="The user starts work at 9:00."),
        tool("set_reminder", **PAST),
        text("Noted: you start work at 9:00. I'll remind you at 8:30."),
    )
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "remember that"))
    assert reply.text == f"Noted: you start work at 9:00.\n{NOT_SET}"


async def test_a_refused_set_then_a_set_one_gets_only_codes_confirmation(store: Store) -> None:
    gemma = FakeGemma(
        tool("set_reminder", at="2026-10-07 07:00", text="call mum"),
        tool("set_reminder", at="2026-10-09 07:00", text="call mum"),
        text("Done."),
    )
    brain = with_reminders(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "remind me tomorrow at 7 to call mum"))
    assert reply.reminder == FIRST_SET
    assert reply.reminder_spoken == "Reminder set for Friday 9 October, 07:00."


async def test_one_set_and_one_refused_in_an_answer_notes_the_refused_one(store: Store) -> None:
    both = (
        ToolCall("c1", "set_reminder", '{"at": "2026-10-09 07:00", "text": "call mum"}'),
        ToolCall("c2", "set_reminder", '{"at": "2026-10-07 07:00", "text": "water the plants"}'),
    )
    gemma = FakeGemma(Completion(content="", tool_calls=both, prompt_tokens=50), text("Both are set!"))
    brain = with_reminders(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "remind me of both"))
    assert [r.text for r in store.open_reminders(ME)] == ["call mum"]
    assert reply.reminder.splitlines() == [
        'Reminder set (#1): Fri 9 Oct 07:00 · "call mum". /reminders to see or cancel.',
        prompts.REMINDER_CONTRACT,
        'Not set: "water the plants" (that time has passed).',
    ]
    assert reply.reminder_spoken == (
        "Reminder set for Friday 9 October, 07:00. One reminder was not set: that time has passed."
    )


async def test_a_refused_one_after_a_set_one_with_the_same_text_is_still_noted(store: Store) -> None:
    # only a refused try that a later set one of the same text replaced stays quiet
    gemma = FakeGemma(
        tool("set_reminder", at="2026-10-09 07:00", text="call mum"),
        tool("set_reminder", at="2026-10-07 07:00", text="call mum"),
        text("Done."),
    )
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "twice"))
    assert reply.reminder.splitlines()[2:] == ['Not set: "call mum" (that time has passed).']


@pytest.mark.parametrize("arguments", ["{broken", '["17:00"]'])
async def test_a_set_reminder_with_unreadable_arguments_counts_as_tried(store: Store, arguments: str) -> None:
    # Brain marks every set_reminder before the tool runs: arguments that never reach the tool still get the note
    unreadable = ToolCall("c1", "set_reminder", arguments)
    gemma = FakeGemma(Completion(content="", tool_calls=(unreadable,), prompt_tokens=50), text("Okay."))
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "remind me"))
    assert reply.text == "Okay.\nNo reminder was set: the request could not be read." and store.open_reminders(ME) == []


async def test_unreadable_arguments_beside_a_set_one_get_their_own_note(store: Store) -> None:
    both = (
        ToolCall("c1", "set_reminder", '{"at": "2026-10-09 07:00", "text": "cook"}'),
        ToolCall("c2", "set_reminder", "{broken"),
    )
    gemma = FakeGemma(Completion(content="", tool_calls=both, prompt_tokens=50), text("Okay."))
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "remind me"))
    assert reply.reminder.splitlines()[2:] == ["Not set: one reminder (the request could not be read)."]


async def test_unreadable_arguments_then_a_stored_try_is_a_retry(store: Store) -> None:
    # a try with no text that could not be read, then one that stored a reminder: she tried again, no "Not set"
    unreadable = ToolCall("c1", "set_reminder", "{broken")
    gemma = FakeGemma(
        Completion(content="", tool_calls=(unreadable,), prompt_tokens=50),
        tool("set_reminder", at="2026-10-09 07:00", text="cook"),
        text("Done."),
    )
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "remind me"))
    assert reply.reminder.splitlines() == [
        'Reminder set (#1): Fri 9 Oct 07:00 · "cook". /reminders to see or cancel.',
        prompts.REMINDER_CONTRACT,
    ]
    assert reply.reminder_spoken == "Reminder set for Friday 9 October, 07:00."


async def test_unreadable_arguments_then_a_refused_try_then_a_stored_one(store: Store) -> None:
    # only the unreadable try without text counts as retried; a refused one with its own text keeps its note
    unreadable = ToolCall("c1", "set_reminder", "{broken")
    gemma = FakeGemma(
        Completion(content="", tool_calls=(unreadable,), prompt_tokens=50),
        tool("set_reminder", at="2026-10-07 07:00", text="water the plants"),
        tool("set_reminder", at="2026-10-09 07:00", text="cook"),
        text("Done."),
    )
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "remind me"))
    assert reply.reminder.splitlines()[2:] == ['Not set: "water the plants" (that time has passed).']


async def test_a_set_one_and_one_past_the_tool_limit_notes_the_late_one(store: Store) -> None:
    first = ToolCall("c0", "set_reminder", '{"at": "2026-10-09 07:00", "text": "cook"}')
    calls = tuple(ToolCall(f"c{n}", "list_reminders", "{}") for n in range(1, 5))
    late = ToolCall("c5", "set_reminder", '{"at": "2026-10-09 08:00", "text": "walk the dog"}')
    many = Completion(content="", tool_calls=(first, *calls, late), prompt_tokens=100, finish_reason="tool_calls")
    reply = await with_reminders(store, FakeGemma(many, text("Okay."))).answer(
        store.current_conversation(), incoming(store, "two reminders")
    )
    assert reply.reminder.splitlines()[2:] == ['Not set: "walk the dog" (too many steps in one answer).']


async def test_a_set_reminder_past_the_tool_limit_also_gets_the_guard(store: Store) -> None:
    calls = tuple(ToolCall(f"c{n}", "list_reminders", "{}") for n in range(5))
    late = ToolCall("c5", "set_reminder", '{"at": "2026-10-09 07:00", "text": "mama bellen"}')
    many = Completion(content="", tool_calls=(*calls, late), prompt_tokens=100, finish_reason="tool_calls")
    brain = with_reminders(store, FakeGemma(many, text("Ik heb de herinnering ingesteld.")))
    reply = await brain.answer(store.current_conversation(), incoming(store, "herinner me morgen om 7 uur"))
    assert store.open_reminders(ME) == [] and reply.text == "No reminder was set: too many steps in one answer."


async def test_a_claim_without_a_set_is_also_said_on_a_call(store: Store) -> None:
    brain = with_reminders(store, FakeGemma(text("I'll call you at 6:30.")))
    reply = await brain.answer(store.current_conversation(), incoming(store, "[call] wake me at 6:30"))
    assert reply.reminder == prompts.REMINDER_GUARD and reply.reminder_spoken == prompts.REMINDER_GUARD_SPOKEN


# a pure reminder answer whose set_reminder stored nothing is only code's words, in any language


@pytest.mark.parametrize(
    ("arguments", "line"),
    [
        ({"at": "2026-10-07 07:00", "text": "mama bellen"}, "No reminder was set: that time has passed."),
        (
            {"at": "", "text": "mama bellen"},
            "No reminder was set: no time was given.",
        ),
    ],
)
async def test_a_refused_set_is_answered_only_in_codes_words(
    store: Store, arguments: dict[str, str], line: str
) -> None:
    gemma = FakeGemma(tool("set_reminder", **arguments), text("Ik heb de herinnering voor je ingesteld!"))
    brain = with_reminders(store, gemma)
    reply = await brain.answer(store.current_conversation(), incoming(store, "herinner me om 17:00 aan mama"))
    assert (reply.text, reply.not_set, reply.hers) == (line, line, "") and store.open_reminders(ME) == []
    assert reply.reminder == "" and reply.reminder_spoken == ""  # the answer itself says it: no note, no second line


async def test_the_line_said_on_a_call_has_the_spoken_reason(store: Store) -> None:
    gemma = FakeGemma(tool("set_reminder", at="17:00", text="x" * 201), text("Ik heb de herinnering ingesteld!"))
    reply = await with_reminders(store, gemma).answer(
        store.current_conversation(), incoming(store, "herinner me om 17:00")
    )
    assert reply.text == reply.not_set == "No reminder was set: the text is longer than 200 characters."
    assert reply.not_set_spoken == "No reminder was set: the text is too long."


async def test_one_refused_beside_a_set_one_is_said_with_the_spoken_reason(store: Store) -> None:
    both = (
        ToolCall("c1", "set_reminder", '{"at": "2026-10-09 07:00", "text": "cook"}'),
        ToolCall("c2", "set_reminder", json.dumps({"at": "17:00", "text": "y" * 201})),
    )
    gemma = FakeGemma(Completion(content="", tool_calls=both, prompt_tokens=50), text("Okay."))
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "remind me"))
    assert reply.reminder.splitlines()[2].endswith('…" (the text is longer than 200 characters).')
    assert reply.reminder_spoken.endswith("One reminder was not set: the text is too long.")


async def test_every_way_of_storing_nothing_is_codes_words(store: Store) -> None:
    unreadable = ToolCall("c1", "set_reminder", "{broken")
    past = ToolCall("c2", "set_reminder", '{"at": "2026-10-07 07:00", "text": "x"}')
    again = ToolCall("c3", "set_reminder", '{"at": "2026-10-06 07:00", "text": "y"}')
    gemma = FakeGemma(
        Completion(content="", tool_calls=(unreadable, past, again), prompt_tokens=50), text("Erinnerung gesetzt!")
    )
    reply = await with_reminders(store, gemma).answer(store.current_conversation(), incoming(store, "erinnere mich"))
    assert reply.text == reply.not_set and reply.text.splitlines() == [
        "No reminder was set: the request could not be read.",
        "No reminder was set: that time has passed.",  # one line per reason
    ]


async def test_a_cancel_note_still_comes_beside_codes_words(store: Store) -> None:
    reminders = Reminders(store, TZ, clock=lambda: T0 / 1000)
    reminders.set(ME, at="2026-10-09 07:00", text="wake up")
    gemma = FakeGemma(
        tool("cancel_reminder", number=1),
        tool("set_reminder", at="2026-10-07 08:00", text="opstaan"),
        text("Je wekker staat nu op 8 uur!"),
    )
    reply = await with_reminders(store, gemma).answer(
        store.current_conversation(), incoming(store, "verzet mijn wekker naar 8 uur")
    )
    assert reply.text == reply.not_set == "No reminder was set: that time has passed."
    assert reply.reminder == "Cancelled #1: Fri 9 Oct 07:00 · wake up"


async def test_in_another_language_a_set_one_keeps_her_text(store: Store) -> None:
    gemma = FakeGemma(tool("set_reminder", at="2026-10-09 07:00", text="mama bellen"), text("Komt goed!"))
    reply = await with_reminders(store, gemma).answer(
        store.current_conversation(), incoming(store, "herinner me morgen om 7 uur")
    )
    assert (reply.text, reply.not_set) == ("Komt goed!", "") and reply.reminder.startswith("Reminder set (#1)")


async def test_in_another_language_a_claim_without_any_set_keeps_her_text_and_gets_the_note(store: Store) -> None:
    reply = await with_reminders(store, FakeGemma(text("Ik bel je morgen om 6:30."))).answer(
        store.current_conversation(), incoming(store, "bel me morgen om 6:30")
    )
    assert (reply.text, reply.not_set) == ("Ik bel je morgen om 6:30.", "")
    assert reply.reminder == prompts.REMINDER_GUARD


async def test_the_guard_needs_reminders_on(store: Store) -> None:
    _, _, brain = make(store, FakeGemma(text("I'll remind you at 7.")))
    reply = await brain.answer(store.current_conversation(), incoming(store, "remind me at 7"))
    assert reply.reminder == ""


@pytest.mark.parametrize(
    "claim",
    [
        "Your call-reminder is set for 6:30.",  # the checker's misses: a word before "reminder"
        "Your wake-up call reminder is set for 6:30.",
        "He creado un recordatorio de llamar a mamá a las 17:00.",  # "de" + infinitive: what, not whose
        "J'ai créé un rappel d'appeler maman à 17h.",
        "Ho creato un promemoria di chiamare la mamma alle 17:00.",
        "Criei um lembrete de ligar para a mãe às 17:00.",
    ],
)
async def test_asked_for_a_reminder_any_claim_without_a_set_gets_the_note(store: Store, claim: str) -> None:
    """The user asked for a reminder and nothing was stored: the strict (broad) reading decides."""
    brain = with_reminders(store, FakeGemma(text(claim)))
    reply = await brain.answer(store.current_conversation(), incoming(store, "remind me at 6:30 please"))
    assert reply.reminder == prompts.REMINDER_GUARD


@pytest.mark.parametrize(
    "answer",
    [
        "The school's reminder is set for Friday.",
        "Er is een herinnering dat je afspraak bij de tandarts gepland is op dinsdag.",
        "The reminder for the team meeting is set in your calendar.",
        "Je wordt vrijdag door de school herinnerd aan de schoolreis.",
        "A reminder for the school trip was added to the newsletter.",
    ],
)
async def test_not_asked_for_a_reminder_no_guard_note(store: Store, answer: str) -> None:
    """The user asked about something else and she summarised outside text (here the history, which can hold learned
    pages; mail and calendar reads count the same): talk about others' reminders is never a claim of hers."""
    brain = with_reminders(store, FakeGemma(tool("search_history", query="school"), text(answer)))
    reply = await brain.answer(store.current_conversation(), incoming(store, "anything new in my mail?"))
    assert reply.reminder == ""
