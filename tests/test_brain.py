from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.brain import NOTHING_SAVED, Brain
from local_assistant.context import ContextBuilder
from local_assistant.fetcher import Page
from local_assistant.gemma import Completion, GemmaUnavailableError, ToolCall
from local_assistant.profile import Profile
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
    assert call["messages"][-1] == {"role": "user", "content": "[Thu 8 Oct 09:32] hello Gemma"}


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
