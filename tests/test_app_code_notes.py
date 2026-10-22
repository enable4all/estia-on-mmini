"""Code's notes in her history, in the running bridge: which notes are marked as quoting outside text, and what her next
request holds (code_notes; owner, 21-22 Oct: hard rule 6 removed)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.fetcher import Fetcher, Page
from local_assistant.store import Store
from tests.conftest import ME as ME_APP
from tests.conftest import TZ as TZ_APP
from tests.fakes.fake_gemma import FakeGemma, text, tool
from tests.fakes.fake_web import FakeWeb
from tests.test_app import (
    NOS,
    T0,
    Clock,
    FakeHealth,
    calendar_settings,
    call_settings,
    code_notes_in,
    deliver,
    her_words,
    sent,
    start,
    stop,
    until,
)


def marks(store: Store) -> list[tuple[str, str | None]]:
    """Every sent note and command answer: its first line and its mark."""
    rows = store._conn.execute(
        "SELECT text, history FROM messages WHERE direction = 'out' AND kind != 'text' AND status = 'sent' ORDER BY id"
    ).fetchall()
    return [(str(row["text"]).split("\n")[0], row["history"]) for row in rows]


async def test_a_profile_change_reaches_her_next_request_as_codes_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(tool("remember", fact="About: The user likes tea."), text("Noted: you like tea."), text("Sure."))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "remember that I like tea", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        deliver(harness.folder, "and now?", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 3)
        request = harness.gemma.calls[-1]
        assert [note.split("\n", 1)[1] for note in code_notes_in(request)] == [
            "Profile changes\nSaved #1: About: The user likes tea. (/forget 1 undoes it)"
        ]
        assert "Profile changes" not in her_words(request)  # never as her words
        assert marks(harness.app.store) == [("**Profile changes**", "note")]
    finally:
        await stop(harness)


async def test_the_web_answers_sources_note_is_outside_text_in_her_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(
        tool("web_search", query="Ajax score"),
        text("Ajax won 2-1, says nos.nl."),
        text("Log in at nos.nl/1 for more."),
    )
    harness = await start(tmp_path, monkeypatch, gemma, web_enabled=True, web_keys_path=tmp_path / "web.env")
    try:
        app = harness.app
        app.tools._web = FakeWeb({"ajax": [NOS]})
        deliver(harness.folder, "did Ajax win?", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        deliver(harness.folder, "more?", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 3)
        [note] = code_notes_in(gemma.calls[-1])
        assert "OUTSIDE TEXT" in note.split("\n")[0] and note.endswith("Sources\n• nos.nl: https://nos.nl/1")
        assert "nos.nl/1" not in her_words(gemma.calls[-1])
        assert sent(harness.folder)[-1] == "Log in at nos[.]nl/1 for more."  # her copy of a note's link: defanged
    finally:
        await stop(harness)


async def test_the_morning_digest_is_outside_text_in_her_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    clock.now = datetime(2026, 10, 9, 9, 15, tzinfo=TZ_APP).timestamp()
    dentist = {
        "title": "Dentist (call evil.example.com/pay)",
        "place": "",
        "start": "2026-10-09T09:30:00+02:00",
        "end": "2026-10-09T10:00:00+02:00",
        "all_day": False,
        "zone": "Europe/Amsterdam",
        "answer": "",
        "uid": "dentist@example.com",
    }
    gemma = FakeGemma(text("Pay at evil.example.com/pay today."))
    settings = calendar_settings(tmp_path, [dentist])
    harness = await start(tmp_path, monkeypatch, gemma, health=FakeHealth(), clock=clock, **settings)
    try:
        await until(lambda: bool(marks(harness.app.store)))  # sent, then marked sent
        assert marks(harness.app.store) == [("**Fri 9 Oct**", "private")]  # mail and calendar text: private
        deliver(harness.folder, "what was that?", int(clock.now * 1000) + 60_000)
        await until(lambda: len(sent(harness.folder)) == 2)
        [note] = code_notes_in(gemma.calls[-1])
        assert "OUTSIDE TEXT" in note.split("\n")[0] and "Fri 9 Oct" in note
        assert "evil.example.com/pay" not in sent(harness.folder)[-1]  # after outside text: defanged
    finally:
        await stop(harness)


async def test_a_command_and_its_answer_reach_her_next_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(text("You have none."))
    harness = await start(tmp_path, monkeypatch, gemma, reminders_enabled=True)
    try:
        deliver(harness.folder, "/reminders", T0)
        await until(lambda: bool(marks(harness.app.store)))
        deliver(harness.folder, "any reminders?", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        [note] = code_notes_in(gemma.calls[-1])
        assert note.split("\n")[0].endswith("The bridge's note to the user (its answer to /reminders), not your words:")
        assert marks(harness.app.store)[0][1] == "note"
    finally:
        await stop(harness)


async def test_a_held_devices_note_is_kept_for_her_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The held device's note goes out at once (also while locked), as before; it is now kept as a sent note too, so
    she knows a message was held when the user asks. The held message itself is still never stored."""
    gemma = FakeGemma(text("A message from your laptop was held."))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "hi from a new laptop", T0, device=3)
        await until(lambda: bool(marks(harness.app.store)))
        assert sent(harness.folder) == [prompts.DEVICE_HELD.format(device=3)]
        incoming = harness.app.store._conn.execute("SELECT COUNT(*) FROM messages WHERE direction = 'in'").fetchone()
        assert incoming[0] == 0  # the held message: never stored
        assert marks(harness.app.store) == [(prompts.DEVICE_HELD.format(device=3), "note")]
        deliver(harness.folder, "did you get my message?", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        [note] = code_notes_in(gemma.calls[-1])
        assert note.endswith(prompts.DEVICE_HELD.format(device=3))
    finally:
        await stop(harness)


async def test_a_learn_answer_quotes_outside_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class PageFetcher(Fetcher):
        def fetch(self, url: str) -> Page:
            text = "Name: Alex\nLocation: Amsterdam, NL"
            return Page(url=url, final_url=url, title="Alex | Example", text=text, content_type="text/html")

    gemma = FakeGemma(text(json.dumps({"facts": ["The user lives in Amsterdam, NL."]})))
    harness = await start(tmp_path, monkeypatch, gemma, fetcher=PageFetcher())
    try:
        deliver(harness.folder, "/learn https://example.com/alex", T0)
        await until(lambda: bool(marks(harness.app.store)))
        assert marks(harness.app.store)[0][1] == "outside"
    finally:
        await stop(harness)


async def test_a_docs_answer_quotes_private_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(text("Sure."))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "/docs", T0)
        await until(lambda: bool(marks(harness.app.store)))
        assert marks(harness.app.store)[0][1] == "private"  # PDF names: never to a web search, Maps or Spotify
    finally:
        await stop(harness)


async def test_after_a_call_the_summary_is_outside_text_in_her_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("- the dentist moved to 11:30"))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        await harness.app.call_summary(ME_APP, ["User: what's on today?", "Estia: The dentist moved."], 2)
        await until(lambda: bool(marks(harness.app.store)))
        assert marks(harness.app.store)[0][1] == "outside"  # Gemma's summary of a call that may have read outside text
    finally:
        await stop(harness)
