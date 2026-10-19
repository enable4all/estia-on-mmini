import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from local_assistant import app as app_module
from local_assistant import prompts
from local_assistant.app import ALIVE_KEY, CALL_MAX_TOKENS, WELCOME_KEY, App, model_pin, signal_client
from local_assistant.calendar_graph import OutlookReader
from local_assistant.commands import TRUST_DAYS
from local_assistant.config import DEFAULT_NAME, CalendarSource, Config, DeviceSpec, MailAccount
from local_assistant.fetcher import Fetcher, FetchError, Page
from local_assistant.gemma import Completion, GemmaUnavailableError, ModelEntry, ModelPausedError, ModelPin, OllamaGemma
from local_assistant.health import HealthCheck
from local_assistant.language import CODES
from local_assistant.mail_auth import CALENDAR_SCOPES, MailAuthError, TokenStore
from local_assistant.morning_digest import MorningDigest
from local_assistant.part_status import PartStatus, Record, mail_part
from local_assistant.part_status import lines as part_lines
from local_assistant.part_status import read as read_part_status
from local_assistant.profiler import UNANNOUNCED_KEY
from local_assistant.signal_io import ReceiveProblem, SignalClient
from local_assistant.signal_text import plain as on_phone
from local_assistant.spotify import Spotify
from local_assistant.store import Store
from local_assistant.tools import TOOL_SCHEMAS
from local_assistant.watchers import Problem
from local_assistant.web_search import WebResult
from tests.conftest import ME, STRANGER
from tests.conftest import TZ as TZ_APP
from tests.fakes.fake_gemma import FakeGemma, text, tool
from tests.fakes.fake_voice import FakeVoiceService
from tests.fakes.fake_web import FakeWeb
from tests.test_devices import SPECS as DEVICE_SPECS
from tests.test_devices import Home
from tests.test_photos import png
from tests.test_voice import M4A, closed_port_url

NAME = DEFAULT_NAME  # her name in these tests (a fresh install's)
# a fresh install's welcome: reminders and the messages she starts are on without their sections
WELCOME = on_phone(prompts.welcome(name=NAME, reminders=True))  # as the phone shows it; her own messages: opt-in

FAKE = Path(__file__).parent / "fakes" / "fake_signal_cli.py"
# Recent timestamps: messages sent before the bridge started and older than 10 minutes count as
# missed during downtime and get the offline note instead of an answer.
T0 = int(time.time() * 1000)


@dataclass
class Harness:
    app: App
    gemma: FakeGemma
    folder: Path
    task: asyncio.Task[None]


async def until(condition: Callable[[], bool], limit: float = 5.0) -> None:
    async with asyncio.timeout(limit):
        while not condition():
            await asyncio.sleep(0.02)


async def start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gemma: FakeGemma,
    prepare: Callable[[Store], None] | None = None,
    fetcher: Fetcher | None = None,
    health: HealthCheck | None = None,
    clock: Callable[[], float] = time.time,
    welcome: bool = False,
    model_pin: ModelPin | None = None,
    security_level: Callable[[], str | None] | None = None,
    warm_up: bool | None = None,
    **settings: Any,
) -> Harness:
    """The bridge on a fake signal-cli. Its store has had the welcome (a fresh install's first message) unless
    `welcome`; starting again on the same tmp_path is a restart. `warm_up`: a call's model warm-up (App's; with a
    scripted Gemma none unless a test asks)."""
    folder = tmp_path / "signal"
    (folder / "attachments").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FAKE_SIGNAL_DIR", str(folder))
    options: dict[str, Any] = {
        "allowed_acis": frozenset({ME}),
        "busy_note_seconds": 5.0,
        "typing_refresh_seconds": 0.05,
        "signal_attachments_dir": folder / "attachments",
        "youtube_helper": (str(tmp_path / "no-youtube-helper"),),  # never the real one: it would ask www.youtube.com
    }
    options.update(settings)
    cfg = Config(bot_number="+306900000000", data_dir=tmp_path / "data", log_dir=tmp_path / "logs", **options)
    store = Store.open(cfg.db_path)
    if not welcome:
        store.set_state(WELCOME_KEY, "sent")
    if prepare is not None:
        prepare(store)
    signal = SignalClient([sys.executable, str(FAKE)], cfg.allowed_acis, request_timeout=5, backoff_start=0.05)
    app = App(
        cfg,
        store,
        signal,
        gemma,
        fetcher or Fetcher(),
        retry_delay=0.01,
        health=health,
        clock=clock,
        model_pin=model_pin,
        security_level=security_level,
        warm_up=warm_up,
    )
    task = asyncio.create_task(app.run())
    await until(lambda: signal.ready)
    return Harness(app, gemma, folder, task)


async def stop(harness: Harness) -> None:
    harness.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await harness.task
    await harness.app.signal.stop()
    harness.app.store.close()


def deliver(folder: Path, message: str | None, ts: int, source: str = ME, device: int | None = 1, **data: Any) -> None:
    """A message as signal-cli delivers it; `device`: the sender's device that sent it (1: their phone)."""
    envelope: dict[str, Any] = {"sourceUuid": source, "sourceName": "Alex", "sourceDevice": device, "timestamp": ts}
    envelope["dataMessage"] = {"timestamp": ts, "message": message, **data}
    with (folder / "inbox.jsonl").open("a") as handle:
        handle.write(json.dumps(envelope) + "\n")


def requests(folder: Path) -> list[dict[str, Any]]:
    path = folder / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def put(folder: Path, attachment_id: str, data: bytes) -> Path:
    """A file in signal-cli's attachments folder, as signal-cli leaves it after receiving a message."""
    path = folder / "attachments" / attachment_id
    path.write_bytes(data)
    return path


def photo(attachment_id: str, data: bytes, content_type: str = "image/png") -> dict[str, Any]:
    return {"contentType": content_type, "id": attachment_id, "size": len(data)}


def sent(folder: Path) -> list[str]:
    """The texts sent (voice notes have no text)."""
    return [r["params"]["message"] for r in requests(folder) if r["method"] == "send" and "message" in r["params"]]


def styles(folder: Path) -> list[list[str]]:
    """The text styles of each text sent, in order ([] for a text without them)."""
    return [
        r["params"].get("textStyle", []) for r in requests(folder) if r["method"] == "send" and "message" in r["params"]
    ]


def voice(attachment_id: str, data: bytes, content_type: str = "audio/aac") -> dict[str, Any]:
    return {"contentType": content_type, "id": attachment_id, "size": len(data)}


def voice_notes(folder: Path) -> list[dict[str, Any]]:
    return [r["params"] for r in requests(folder) if r["method"] == "send" and "attachments" in r["params"]]


async def test_answers_a_text_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi Alex!")))
    try:
        deliver(harness.folder, "hello Gemma", T0)
        await until(lambda: sent(harness.folder) == ["Hi Alex!"])
        methods = {r["method"]: r["params"] for r in requests(harness.folder)}
        assert methods["send"]["recipient"] == [ME]
        assert methods["sendReceipt"] == {"recipient": ME, "targetTimestamp": [T0], "type": "read"}
        assert any(r["method"] == "sendTyping" and r["params"]["stop"] for r in requests(harness.folder))
        await until(lambda: harness.app.store.message(2).status == "sent")
        assert harness.app.store.message(1).status == "answered"
    finally:
        await stop(harness)


async def test_markup_goes_out_as_text_styles_and_stays_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signal shows Markdown as typed: at send time it becomes Signal's text styles; the stored text keeps it, so her
    history (and the search) holds what she wrote."""
    answer = "# Plan\n- **Gym** at 7\n- ~~Bob~~ Anna"
    harness = await start(tmp_path, monkeypatch, FakeGemma(text(answer), text("ok")))
    try:
        deliver(harness.folder, "plan my day", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == ["Plan\n• Gym at 7\n• Bob Anna"]
        assert styles(harness.folder) == [["0:4:BOLD", "7:3:BOLD", "18:3:STRIKETHROUGH"]]
        assert harness.app.store.message(2).text == answer
        deliver(harness.folder, "thanks", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert answer in [m["content"] for m in harness.gemma.calls[1]["messages"]]
        assert styles(harness.folder)[1] == []  # plain text: no textStyle at all
    finally:
        await stop(harness)


async def test_a_converter_failure_sends_the_text_as_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The send path's rule: when the styles fail, the plain text goes out (never a message left pending)."""

    def broken(markup: str) -> Any:
        raise ValueError("converter bug")

    monkeypatch.setattr(app_module, "styled", broken)
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("**Hi** Alex!")))
    try:
        deliver(harness.folder, "hello", T0)
        await until(lambda: sent(harness.folder) == ["**Hi** Alex!"])
        assert styles(harness.folder) == [[]]
        await until(lambda: harness.app.store.message(2).status == "sent")
    finally:
        await stop(harness)


async def test_a_resend_after_a_restart_converts_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recovery rebuilds the text from the stored one (markup and footer), so it goes out exactly as the first try
    would have."""
    now = time.time()

    def prepare(store: Store) -> None:
        conv = store.current_conversation()
        asked = store.add_incoming(conv.id, ME, int((now - 60) * 1000), "text", "what's on?")
        assert asked is not None
        store.set_status(asked, "answered")
        out_id = store.add_outgoing(conv.id, ME, "text", "**Today**: the *dentist* at 09:30", asked)
        store.set_status(out_id, "failed")
        store.set_state(ALIVE_KEY, str(now - 30))

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=prepare)
    try:
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == ["Today: the dentist at 09:30"]
        assert styles(harness.folder) == [["0:5:BOLD", "11:7:ITALIC"]]
        assert harness.app.store.message(2).text == "**Today**: the *dentist* at 09:30"
    finally:
        await stop(harness)


async def test_strangers_get_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("ok")))
    try:
        deliver(harness.folder, "hi bot", T0, source=STRANGER)
        deliver(harness.folder, "hello", T0 + 1)
        await until(lambda: sent(harness.folder) == ["ok"])
        recipients = [json.dumps(r["params"]) for r in requests(harness.folder)]
        assert not any(STRANGER in params for params in recipients)
        assert harness.app.store.count_messages() == 2  # my message and the reply
    finally:
        await stop(harness)


async def test_duplicate_delivery_is_answered_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("once")))
    try:
        deliver(harness.folder, "hello", T0)
        deliver(harness.folder, "hello", T0)
        await until(lambda: sent(harness.folder) == ["once"])
        await asyncio.sleep(0.3)
        assert sent(harness.folder) == ["once"] and len(harness.gemma.calls) == 1
    finally:
        await stop(harness)


async def test_commands_and_voice_skip_gemma(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        voice = put(harness.folder, "v1.aac", b"voice bytes")
        deliver(harness.folder, "/help", T0)
        deliver(harness.folder, None, T0 + 1, attachments=[{"contentType": "audio/aac", "id": "v1.aac", "size": 11}])
        shown = on_phone(prompts.help_text(harness.app.capability_lines()))
        await until(lambda: sent(harness.folder) == [shown, prompts.TEXT_ONLY])
        assert harness.gemma.calls == []
        assert not voice.exists()  # unsupported files are deleted at once
    finally:
        await stop(harness)


async def test_gemma_failure_sends_the_unavailable_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(GemmaUnavailableError("down")))
    try:
        deliver(harness.folder, "hello", T0)
        await until(lambda: sent(harness.folder) == [prompts.UNAVAILABLE.format(name=NAME)])
        assert harness.app.store.message(1).status == "failed"
    finally:
        await stop(harness)


async def test_busy_note_when_gemma_takes_long(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pending: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    harness = await start(tmp_path, monkeypatch, FakeGemma(pending), busy_note_seconds=0.2)
    try:
        deliver(harness.folder, "hello", T0)
        await until(lambda: sent(harness.folder) == [prompts.BUSY.format(name=NAME)])
        pending.set_result(text("Here you go."))
        await until(lambda: sent(harness.folder) == [prompts.BUSY.format(name=NAME), "Here you go."])
    finally:
        await stop(harness)


async def test_two_quick_messages_are_answered_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("first answer"), text("second answer")))
    try:
        deliver(harness.folder, "first question", T0)
        deliver(harness.folder, "second question", T0 + 1)
        await until(lambda: sent(harness.folder) == ["first answer", "second answer"])
        second_input = [m["content"] for m in harness.gemma.calls[1]["messages"][1:]]
        assert [c.split("] ")[-1] for c in second_input] == ["first question", "first answer", "second question"]
    finally:
        await stop(harness)


async def test_untrusted_identity_marks_the_reply_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-4")
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("hi")))
    try:
        deliver(harness.folder, "hello", T0)
        await until(lambda: harness.app.store.count_messages() == 2 and harness.app.store.message(2).status == "failed")
        assert not harness.task.done()
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "safety number changed" in logged  # the log names the fix, never the bot's number or the user's ACI
        assert harness.app.cfg.bot_number not in logged and ME not in logged
        assert harness.app.part_status.security().identity_changed is not None  # doctor and /status show it
    finally:
        await stop(harness)


async def test_recovery_after_a_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    ids: dict[str, int] = {}

    def prepare(store: Store) -> None:
        conv = store.current_conversation()
        stale = store.add_incoming(conv.id, ME, int((now - 3_600) * 1000), "text", "old question")
        recent = store.add_incoming(conv.id, ME, int((now - 60) * 1000), "text", "recent question")
        assert stale is not None and recent is not None
        ids.update(stale=stale, recent=recent)
        store.set_state(ALIVE_KEY, str(now - 3_700))

    harness = await start(tmp_path, monkeypatch, FakeGemma(text("answer to the recent one")), prepare=prepare)
    try:
        await until(lambda: len(sent(harness.folder)) == 2)
        note, answer = sent(harness.folder)
        assert note.startswith("I was offline ") and note.endswith("ask again if you still need something.")
        assert answer == "answer to the recent one"
        assert harness.app.store.message(ids["stale"]).status == "failed"
    finally:
        await stop(harness)


async def test_any_failure_still_gets_a_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(RuntimeError("unexpected")))
    try:
        deliver(harness.folder, "hello", T0)
        await until(lambda: sent(harness.folder) == [prompts.CONFUSED])
        assert harness.app.store.message(1).status == "failed"
    finally:
        await stop(harness)


async def test_a_message_after_new_joins_the_new_conversation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pending: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    harness = await start(tmp_path, monkeypatch, FakeGemma(pending, text("cooking answer")))
    try:
        deliver(harness.folder, "tell me about Lisbon", T0)
        await until(lambda: len(harness.gemma.calls) == 1)
        assert harness.app.profiler.due() is False and harness.app._is_busy()
        deliver(harness.folder, "/new", T0 + 1)
        deliver(harness.folder, "let's talk about cooking", T0 + 2)
        await until(lambda: harness.app.store.count_messages() == 3)
        pending.set_result(text("Lisbon answer"))
        await until(lambda: len(sent(harness.folder)) == 3)
        assert sent(harness.folder)[2] == "cooking answer"
        seen = [m["content"] for m in harness.gemma.calls[1]["messages"][1:]]
        assert not any("Lisbon" in content for content in seen)
        assert not harness.app._is_busy()
    finally:
        await stop(harness)


async def test_old_messages_after_downtime_get_one_offline_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()

    def prepare(store: Store) -> None:
        store.set_state(ALIVE_KEY, str(now - 7_200))

    harness = await start(tmp_path, monkeypatch, FakeGemma(text("fresh answer")), prepare=prepare)
    try:
        deliver(harness.folder, "are you there?", int((now - 3_600) * 1000))
        deliver(harness.folder, "hello??", int((now - 3_500) * 1000))
        deliver(harness.folder, "ok, new question", int(now * 1000))
        await until(lambda: len(sent(harness.folder)) == 2)
        note, answer = sent(harness.folder)
        assert note.startswith("I was offline ") and answer == "fresh answer"
        assert len(harness.gemma.calls) == 1
        messages = [harness.app.store.message(i) for i in range(1, 6)]  # 3 in, the note and the answer out
        statuses = {m.text: m.status for m in messages if m.direction == "in"}
        assert statuses["are you there?"] == statuses["hello??"] == "failed"  # the note may be stored between them
    finally:
        await stop(harness)


async def test_profile_changes_come_as_a_separate_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(tool("remember", fact="The user likes tea."), text("Noted: you like tea."), text("Sure."))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "remember that I like tea", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == [
            "Noted: you like tea.",
            "Profile changes\nSaved #1: The user likes tea. (/forget 1 undoes it)",
        ]
        deliver(harness.folder, "and now?", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 3)
        history = " ".join(m["content"] for m in harness.gemma.calls[-1]["messages"][1:])
        assert "Profile changes" not in history  # Gemma never sees the notes, so it cannot imitate them
    finally:
        await stop(harness)


async def test_background_learning_is_mentioned_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def prepare(store: Store) -> None:
        store.set_state(UNANNOUNCED_KEY, "2")

    harness = await start(tmp_path, monkeypatch, FakeGemma(text("hi"), text("again")), prepare=prepare)
    try:
        deliver(harness.folder, "hello", T0)
        deliver(harness.folder, "hello again", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 3)
        assert sent(harness.folder) == ["hi", prompts.DIGEST.format(count=2, things="things"), "again"]
    finally:
        await stop(harness)


async def test_a_slow_fetch_is_cut_off(tmp_path: Path) -> None:
    class SlowFetcher(Fetcher):
        def fetch(self, url: str) -> Page:
            time.sleep(1.0)
            raise AssertionError("should have been cut off")

    cfg = Config(bot_number="+306900000000", allowed_acis=frozenset({ME}), data_dir=tmp_path / "data")
    store = Store.open(cfg.db_path)
    try:
        app = App(cfg, store, SignalClient(["true"], cfg.allowed_acis), FakeGemma(), SlowFetcher(), fetch_timeout=0.2)
        with pytest.raises(FetchError, match="too slow"):
            await app._fetch("https://example.org/slow")
    finally:
        store.close()


async def test_learn_command_lists_the_saved_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class PageFetcher(Fetcher):
        def fetch(self, url: str) -> Page:
            text = "Name: Alex\nLocation: Amsterdam, NL"
            return Page(url=url, final_url=url, title="Alex | LinkedIn", text=text, content_type="text/html")

    facts = json.dumps({"facts": ["The user lives in Amsterdam, NL."]})
    harness = await start(tmp_path, monkeypatch, FakeGemma(text(facts)), fetcher=PageFetcher())
    try:
        deliver(harness.folder, "/learn https://www.linkedin.com/in/alex", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder)[0] == (
            "Learned from Alex | LinkedIn:\nSaved #1: The user lives in Amsterdam, NL.\nUndo one with /forget N."
        )
        assert "Amsterdam" in harness.app.context.snapshot()  # Gemma knows it from the next message on
    finally:
        await stop(harness)


async def test_a_photo_is_answered_with_the_image_then_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("It is blue.")))
    try:
        image = png(40, 30)
        file = put(harness.folder, "p1.png", image)
        deliver(harness.folder, "what colour is this?", T0, attachments=[photo("p1.png", image)])
        await until(lambda: sent(harness.folder) == ["It is blue."])
        user = harness.gemma.calls[0]["messages"][-1]
        assert user["content"][0]["text"].endswith("[photo] what colour is this?")
        assert user["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,/9j/")
        assert harness.app.store.message(1).text == "[photo] what colour is this?"
        assert not file.exists()
        assert harness.app.store.attachments(1) == []  # all marked deleted
        assert list((tmp_path / "data" / "photos").iterdir()) == []
    finally:
        await stop(harness)


async def test_a_photo_without_caption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("A blue square.")))
    try:
        image = png(8, 8)
        put(harness.folder, "p1.png", image)
        deliver(harness.folder, None, T0, attachments=[photo("p1.png", image)])
        await until(lambda: sent(harness.folder) == ["A blue square."])
        assert harness.app.store.message(1).text == "[photo] (no caption)"
    finally:
        await stop(harness)


async def test_an_unreadable_photo_gets_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        broken = b"\xff\xd8\xff\xe0 not really a jpeg"
        file = put(harness.folder, "b.jpg", broken)
        deliver(harness.folder, "look", T0, attachments=[photo("b.jpg", broken, "image/jpeg")])
        await until(lambda: sent(harness.folder) == [prompts.PHOTO_FAILED])
        assert harness.gemma.calls == []
        assert not file.exists()
        assert harness.app.store.message(1).status == "answered"
    finally:
        await stop(harness)


async def test_a_too_big_photo_is_not_downloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        file = put(harness.folder, "huge.jpg", b"\xff\xd8\xff")
        deliver(
            harness.folder,
            "look",
            T0,
            attachments=[{"contentType": "image/jpeg", "id": "huge.jpg", "size": 30_000_000}],
        )
        await until(lambda: sent(harness.folder) == [prompts.PHOTO_FAILED])
        assert not any(r["method"] == "getAttachment" for r in requests(harness.folder))
        assert not file.exists()
    finally:
        await stop(harness)


async def test_at_most_four_photos_and_a_note_for_the_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Five blue squares, I saw four.")))
    try:
        image = png(8, 8)
        files = [put(harness.folder, f"p{n}.png", image) for n in range(5)]
        deliver(harness.folder, "how many?", T0, attachments=[photo(f"p{n}.png", image) for n in range(5)])
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == ["Five blue squares, I saw four.", prompts.PHOTO_LIMIT.format(count=4)]
        images = [part for part in harness.gemma.calls[0]["messages"][-1]["content"] if part["type"] == "image_url"]
        assert len(images) == 4
        assert not any(file.exists() for file in files)
    finally:
        await stop(harness)


async def test_one_unreadable_photo_of_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("One blue square.")))
    try:
        image, broken = png(8, 8), b"\xff\xd8\xff\xe0 broken"
        put(harness.folder, "good.png", image)
        put(harness.folder, "bad.jpg", broken)
        attachments = [photo("good.png", image), photo("bad.jpg", broken, "image/jpeg")]
        deliver(harness.folder, "what are these?", T0, attachments=attachments)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == ["One blue square.", prompts.PHOTO_SOME_FAILED.format(failed=1)]
    finally:
        await stop(harness)


async def test_a_strangers_photo_is_deleted_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("ok")))
    try:
        image = png(8, 8)
        file = put(harness.folder, "s.png", image)
        deliver(harness.folder, "hi bot", T0, source=STRANGER, attachments=[photo("s.png", image)])
        deliver(harness.folder, "hello", T0 + 1)
        await until(lambda: sent(harness.folder) == ["ok"])
        assert not file.exists()
        assert not any(r["method"] == "getAttachment" for r in requests(harness.folder))
    finally:
        await stop(harness)


class FakeHealth:
    name = "mini"

    def __init__(self) -> None:
        self.problems: list[Problem] = []

    async def check(self) -> list[Problem]:
        return list(self.problems)

    async def report(self) -> str:
        return "Mini status at 8 Oct 11:50\n- Extra check: ok\nProblems: none"


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


async def test_health_alerts_reach_the_user_as_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    health, clock = FakeHealth(), Clock()
    clock.now = datetime(2026, 10, 8, 11, 40, tzinfo=TZ_APP).timestamp()  # in the day: quiet hours would hold it
    harness = await start(tmp_path, monkeypatch, FakeGemma(), health=health, clock=clock, host_label="the study Mac")
    try:
        assert "You can check the study Mac's health" in harness.app.context.system_prompt(
            harness.app.store.current_conversation()
        )
        health.problems = [Problem("extra", "Extra check", "not answering")]
        clock.now += 4 * 60  # first check after 2 minutes, confirmed 5 minutes later
        await harness.app.scheduler.tick()
        clock.now += 5 * 60
        await harness.app.scheduler.tick()
        await until(lambda: len(sent(harness.folder)) == 1)
        [alert] = sent(harness.folder)
        assert alert.startswith("⚠ Problem on the study Mac:\n• Extra check: not answering (since ")  # "- ": "•"
        out = harness.app.store.message(1)
        assert (out.direction, out.kind, out.peer_aci) == ("out", "note", ME)
    finally:
        await stop(harness)


async def test_health_alerts_wait_for_the_morning_and_security_notes_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quiet hours (22:00-08:00 by default): a host problem is told at 08:00; a new linked device at once."""
    devices = [{"id": 1, "createdTimestamp": 1}, {"id": 2, "createdTimestamp": 2}]
    monkeypatch.setenv("FAKE_SIGNAL_DEVICES", json.dumps(devices))
    health, clock = FakeHealth(), Clock()
    clock.now = datetime(2026, 10, 9, 3, 0, tzinfo=TZ_APP).timestamp()
    harness = await start(tmp_path, monkeypatch, FakeGemma(), health=health, clock=clock)
    try:
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == [prompts.LINKED_ALERT.format(count=2)]  # security: at once, also at 03:00
        health.problems = [Problem("extra", "Extra check", "not answering")]
        clock.now += 4 * 60
        await harness.app.scheduler.tick()
        clock.now += 5 * 60
        await harness.app.scheduler.tick()
        await asyncio.sleep(0.2)
        assert len(sent(harness.folder)) == 1  # the host problem waits
        clock.now = datetime(2026, 10, 9, 8, 0, tzinfo=TZ_APP).timestamp()
        await harness.app.scheduler.tick()
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder)[1] == "⚠ Problem on the Mac:\n• Extra check: not answering (since 03:04)"
    finally:
        await stop(harness)


async def test_mini_status_tool_uses_the_health_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(tool("mini_status"), text("All fine on the Mini."))
    harness = await start(tmp_path, monkeypatch, gemma, health=FakeHealth())
    try:
        deliver(harness.folder, "how's the Mini?", T0)
        await until(lambda: sent(harness.folder) == ["All fine on the Mini."])
        assert (
            gemma.calls[1]["messages"][-1]["content"] == "Mini status at 8 Oct 11:50\n- Extra check: ok\nProblems: none"
        )
    finally:
        await stop(harness)


async def test_a_photo_missed_during_downtime_is_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()

    def prepare(store: Store) -> None:
        store.set_state(ALIVE_KEY, str(now - 7_200))

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=prepare)
    try:
        image = png(8, 8)
        file = put(harness.folder, "late.png", image)
        deliver(harness.folder, "look at this", int((now - 3_600) * 1000), attachments=[photo("late.png", image)])
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder)[0].startswith("I was offline ")
        assert not file.exists()
        assert harness.app.store.attachments(1) == []
    finally:
        await stop(harness)


async def test_recovery_deletes_the_photos_of_stale_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    file = tmp_path / "signal" / "attachments" / "old.png"

    def prepare(store: Store) -> None:
        conv = store.current_conversation()
        stale = store.add_incoming(conv.id, ME, int((now - 3_600) * 1000), "text", "[photo] old")
        assert stale is not None
        store.add_attachment(stale, "old.png", "image/png", 10)
        file.write_bytes(b"old photo")
        store.set_state(ALIVE_KEY, str(now - 3_700))

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=prepare)
    try:
        await until(lambda: len(sent(harness.folder)) == 1)
        assert not file.exists()
        assert harness.app.store.attachments(1) == []
    finally:
        await stop(harness)


async def test_a_long_text_reaches_gemma_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Got your CV.")))
    try:
        long_text = "I worked at Acme as a product manager. " * 80  # about 3,100 characters
        file = put(harness.folder, "long1.txt", long_text.encode())
        attachment = {"contentType": "text/x-signal-plain", "id": "long1.txt", "size": len(long_text)}
        deliver(harness.folder, long_text, T0, attachments=[attachment])
        await until(lambda: sent(harness.folder) == ["Got your CV."])
        assert harness.gemma.calls[0]["messages"][-1]["content"].endswith(long_text.strip())
        assert not file.exists()
    finally:
        await stop(harness)


async def test_the_sweep_deletes_old_files_nothing_waits_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # signal-cli also downloads link-preview images, quote thumbnails, avatars and edited messages' files,
    # which no photo message refers to: they are swept once they are 10 minutes old.
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        store, old = harness.app.store, time.time() - 3_600
        preview = put(harness.folder, "preview1.jpg", b"link preview")
        sidecar = put(harness.folder, "waiting.png.preview", b"thumbnail")
        waiting = put(harness.folder, "waiting.png", b"photo still queued")
        fresh = put(harness.folder, "fresh.jpg", b"just downloaded")
        leftover = tmp_path / "data" / "photos" / "crash-leftover.jpg"
        leftover.parent.mkdir(parents=True, exist_ok=True)
        leftover.write_bytes(b"temp file of a killed sips run")
        for path in (preview, sidecar, waiting, leftover):
            os.utime(path, (old, old))
        conv = store.current_conversation()
        queued = store.add_incoming(conv.id, ME, T0, "text", "[photo] (no caption)")
        assert queued is not None
        store.add_attachment(queued, "waiting.png", "image/png", 18)
        harness.app.sweep_files()
        assert not preview.exists() and not leftover.exists()
        assert waiting.exists() and sidecar.exists() and fresh.exists()
    finally:
        await stop(harness)


async def test_a_follow_up_within_5_minutes_sees_the_photo_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    gemma = FakeGemma(text("A blue square."), text("It was blue."), text("I can't see it anymore."))
    harness = await start(tmp_path, monkeypatch, gemma, clock=clock)
    try:
        image = png(8, 8)
        put(harness.folder, "p1.png", image)
        deliver(harness.folder, "what is this?", T0, attachments=[photo("p1.png", image)])
        await until(lambda: len(sent(harness.folder)) == 1)
        clock.now += 4 * 60
        deliver(harness.folder, "what colour was it?", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        clock.now += 2 * 60  # 6 minutes after the photo
        deliver(harness.folder, "and the corners?", T0 + 2)
        await until(lambda: len(sent(harness.folder)) == 3)

        def first_user(call: int) -> Any:
            return next(m for m in gemma.calls[call]["messages"] if m["role"] == "user")["content"]

        assert first_user(1)[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert first_user(2).endswith("[photo, no longer visible] what is this?")
    finally:
        await stop(harness)


async def test_the_real_file_size_is_checked_before_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), photo_max_bytes=100)
    try:
        image = png(200, 200)  # about 600 bytes, more than the limit
        assert len(image) > 100
        put(harness.folder, "big.png", image)
        claimed = {"contentType": "image/png", "id": "big.png", "size": 50}  # the envelope understates it
        deliver(harness.folder, "look", T0, attachments=[claimed])
        await until(lambda: sent(harness.folder) == [prompts.PHOTO_FAILED])
        assert not any(r["method"] == "getAttachment" for r in requests(harness.folder))
    finally:
        await stop(harness)


async def test_gemma_is_told_about_a_file_it_cannot_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("A blue square; I can't open the PDF.")))
    try:
        image = png(8, 8)
        put(harness.folder, "p1.png", image)
        put(harness.folder, "doc.pdf", b"%PDF-1.7")
        pdf = {"contentType": "application/pdf", "id": "doc.pdf", "size": 8}
        deliver(harness.folder, "summarise these", T0, attachments=[photo("p1.png", image), pdf])
        await until(lambda: len(sent(harness.folder)) == 1)
        assert harness.app.store.message(1).text == "[photo] summarise these (+1 other file I can't open)"
    finally:
        await stop(harness)


async def test_a_photo_message_is_stored_whole_or_not_at_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:

        def broken(*_args: Any, **_kwargs: Any) -> int:
            raise RuntimeError("disk full")

        monkeypatch.setattr(harness.app.store, "add_attachment", broken)
        image = png(8, 8)
        put(harness.folder, "p1.png", image)
        data: dict[str, Any] = {"timestamp": T0, "message": "look", "attachments": [photo("p1.png", image)]}
        envelope: dict[str, Any] = {"sourceUuid": ME, "sourceDevice": 1, "timestamp": T0, "dataMessage": data}
        with pytest.raises(RuntimeError):
            await harness.app.handle_envelope(envelope)
        assert harness.app.store.count_messages() == 0
    finally:
        await stop(harness)


async def test_mail_starts_and_asks_for_the_sign_ins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = (MailAccount("family", "info@example.com"), MailAccount("work", "contact@example.app"))
    harness = await start(
        tmp_path,
        monkeypatch,
        FakeGemma(),
        mail_tenant_id="11111111-2222-4333-8444-555555555555",
        mail_client_id="66666666-7777-4888-9999-000000000000",
        mail_accounts=accounts,
    )
    try:
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == [
            "Mailbox family needs a new sign-in: run scripts/mail-login.sh info@example.com on the Mac.",
            "Mailbox work needs a new sign-in: run scripts/mail-login.sh contact@example.app on the Mac.",
        ]
        assert harness.app.mail_sync is not None
        assert harness.app.mail_sync.status_line(TZ_APP) == "Mail: family needs sign-in · work needs sign-in"
    finally:
        await stop(harness)


MAIL_TENANT = "11111111-2222-4333-8444-555555555555"
MAIL_CLIENT = "66666666-7777-4888-9999-000000000000"


def test_mail_login_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        f'bot_number = "+306900000000"\nallowed_acis = ["{ME}"]\n'
        f'[mail]\ntenant_id = "{MAIL_TENANT}"\nclient_id = "{MAIL_CLIENT}"\n'
        '[[mail.accounts]]\nname = "family"\naddress = "info@example.com"\n'
    )
    path.chmod(0o600)
    calls: list[tuple[str, Path]] = []

    async def fake_login(cfg: Config, address: str, store: TokenStore, **_: Any) -> str:
        calls.append((address, store.path))
        return address

    monkeypatch.setattr(app_module, "device_login", fake_login)
    app_module.main(["--config", str(path), "--mail-login", "info@example.com"])
    assert calls == [
        ("info@example.com", Path.home() / "Library" / "Application Support" / "local-assistant" / "mail-tokens.json")
    ]
    assert "signed in: info@example.com" in capsys.readouterr().out
    options: list[bool] = []

    async def mail_only_login(cfg: Config, address: str, store: TokenStore, *, mail_only: bool = False) -> str:
        options.append(mail_only)
        return address

    monkeypatch.setattr(app_module, "device_login", mail_only_login)
    app_module.main(["--config", str(path), "--mail-login", "info@example.com", "--mail-only"])
    app_module.main(["--config", str(path), "--mail-login", "info@example.com"])
    assert options == [True, False]
    with pytest.raises(SystemExit) as wrong:
        app_module.main(["--config", str(path), "--mail-only"])
    assert wrong.value.code == 2
    assert "--mail-only" in capsys.readouterr().err

    async def failing_login(cfg: Config, address: str, store: TokenStore, **_: Any) -> str:
        raise MailAuthError("signed in as someone.else@example.com, expected info@example.com")

    monkeypatch.setattr(app_module, "device_login", failing_login)
    with pytest.raises(SystemExit) as exc:
        app_module.main(["--config", str(path), "--mail-login", "info@example.com"])
    assert exc.value.code == 1
    assert "signed in as someone.else" in capsys.readouterr().err


FAKE_ICAL = Path(__file__).parent / "fakes" / "fake_ical_helper.py"


def calendar_settings(tmp_path: Path, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """[calendar] on, with the fake calendar helper and its calendar file."""
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"calendar": "Family", "events": events or []}))
    return {"calendar_enabled": True, "calendar_helper": (sys.executable, str(FAKE_ICAL), str(path))}


async def test_a_calendar_only_digest_goes_out_at_its_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    clock.now = datetime(2026, 10, 9, 9, 15, tzinfo=TZ_APP).timestamp()
    dentist = {
        "title": "Dentist",
        "place": "",
        "start": "2026-10-09T09:30:00+02:00",
        "end": "2026-10-09T10:00:00+02:00",
        "all_day": False,
        "zone": "Europe/Amsterdam",
        "answer": "",
        "uid": "dentist@example.com",
    }
    settings = calendar_settings(tmp_path, [dentist])
    harness = await start(tmp_path, monkeypatch, FakeGemma(), health=FakeHealth(), clock=clock, **settings)
    try:
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == ["Fri 9 Oct\n\nToday\n• 09:30–10:00 Dentist\n\nTomorrow: nothing"]
        assert styles(harness.folder) == [["0:9:BOLD", "11:5:BOLD", "40:8:BOLD"]]  # the date and the headings
    finally:
        await stop(harness)


def test_the_morning_digest_needs_mail_or_the_calendar(tmp_path: Path) -> None:
    def morning(**settings: Any) -> MorningDigest | None:
        cfg = Config(
            bot_number="+306900000000",
            allowed_acis=frozenset({ME}),
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            **settings,
        )
        store = Store.open(cfg.db_path)
        try:
            signal = SignalClient([sys.executable, str(FAKE)], cfg.allowed_acis)
            return App(cfg, store, signal, FakeGemma(), Fetcher()).morning_digest
        finally:
            store.close()

    mail = {
        "mail_tenant_id": MAIL_TENANT,
        "mail_client_id": MAIL_CLIENT,
        "mail_accounts": (MailAccount("family", "info@example.com"),),
    }
    calendar = calendar_settings(tmp_path)
    assert morning() is None
    assert morning(**calendar, calendar_digest=False) is None
    calendar_only = morning(**calendar)
    assert calendar_only is not None and calendar_only._mail is None and calendar_only._calendar is not None
    mail_only = morning(**mail, **calendar, calendar_digest=False)
    assert mail_only is not None and mail_only._mail is not None and mail_only._calendar is None
    both = morning(**mail, **calendar)
    assert both is not None and both._mail is not None and both._calendar is not None
    assert both._calendar_seconds == 35  # 5 s more than the helper's own limit: the helper is never cut off first


async def test_the_calendar_gets_its_tools_rules_and_status_line(tmp_path: Path) -> None:
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        **calendar_settings(tmp_path),
    )
    store = Store.open(cfg.db_path)
    try:
        app = App(cfg, store, SignalClient([sys.executable, str(FAKE)], cfg.allowed_acis), FakeGemma(), Fetcher())
        assert "calendar_agenda" in [schema["function"]["name"] for schema in app.tools.schemas]
        assert app.context.system_prompt(store.current_conversation()).startswith(
            prompts.rules(
                name=NAME,
                calendar=True,
                reminders=True,
                practice_last=prompts.PRACTICE_NEVER,
                capabilities=app.capability_lines(),
            )
        )
        assert await app._status_lines() == ["Calendar: ok (0 events in the next 7 days)"]
    finally:
        store.close()


def test_an_outlook_calendar_is_read_through_its_own_graph(tmp_path: Path) -> None:
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        mail_tenant_id=MAIL_TENANT,
        mail_client_id=MAIL_CLIENT,
        mail_accounts=(MailAccount("family", "info@example.com"),),
        calendar_sources=(CalendarSource("calendar", "Family"), CalendarSource("work", mailbox="family")),
        **calendar_settings(tmp_path),
    )
    store = Store.open(cfg.db_path)
    try:
        app = App(cfg, store, SignalClient([sys.executable, str(FAKE)], cfg.allowed_acis), FakeGemma(), Fetcher())
        assert app.calendar is not None and app.calendar._feed.several
        readers = dict(app.calendar._feed._readers)
        outlook = readers[cfg.calendars[1]]
        assert isinstance(outlook, OutlookReader) and outlook._status is app.part_status
        assert app.mail_sync is not None and outlook._graph is not app.mail_sync._graph  # never mail's Graph
        assert outlook._graph._scope == CALENDAR_SCOPES
        labels = [line.label for line in part_lines(cfg, {}, 0)]
        assert labels == ["Mail (family)", "Calendar", "Calendar (work)", "YouTube"]
    finally:
        store.close()


async def test_status_tells_what_each_part_last_did(tmp_path: Path) -> None:
    now = datetime(2026, 10, 8, 11, 40, tzinfo=TZ_APP).timestamp()
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        **calendar_settings(tmp_path),
    )
    store = Store.open(cfg.db_path)
    try:
        app = App(
            cfg,
            store,
            SignalClient([sys.executable, str(FAKE)], cfg.allowed_acis),
            FakeGemma(),
            Fetcher(),
            clock=lambda: now,
        )
        untested = "YouTube: set up, not tested yet"  # always on with read_link
        assert (await app.status()).parts == ("Calendar: set up, not tested yet", untested)
        await app._status_lines()  # reads the calendar for real
        assert (await app.status()).parts == ("Calendar: working, last read 11:40", untested)
        assert read_part_status(cfg.data_dir / "status.json") == {"calendar": Record(ok=now)}  # doctor reads this
    finally:
        store.close()


def test_every_part_records_into_the_one_status_file(tmp_path: Path) -> None:
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        voice_url="http://127.0.0.1:8150",
        mail_tenant_id=MAIL_TENANT,
        mail_client_id=MAIL_CLIENT,
        mail_accounts=(MailAccount("family", "info@example.com"),),
        tapo_devices=DEVICE_SPECS,
        web_enabled=True,
        maps_enabled=True,
        spotify_client_id="0123456789abcdef0123456789abcdef",
        **calendar_settings(tmp_path),
    )
    store = Store.open(cfg.db_path)
    try:
        app = App(cfg, store, SignalClient([sys.executable, str(FAKE)], cfg.allowed_acis), FakeGemma(), Fetcher())
        recorder = app.part_status
        assert recorder.path == cfg.data_dir / "status.json"
        assert app.mail_sync is not None and app.mail_sync._status is recorder
        assert app.calendar is not None and app.calendar._feed._status is recorder
        assert app.voice is not None and app.voice._status is recorder
        assert app.devices is not None and app.devices._status is recorder
        assert app.web is not None and app.web._status is recorder
        assert app.maps is not None and app.maps._status is recorder
        assert app.music is not None and isinstance(app.music._spotify, Spotify)
        assert app.music._spotify._status is recorder
    finally:
        store.close()


@pytest.mark.parametrize("maps_on", [True, False], ids=["maps on", "maps off"])
def test_at_start_no_row_keeps_the_home_address(tmp_path: Path, maps_on: bool) -> None:
    """An earlier version kept the home address in the state table: the start removes it, maps on or off."""
    home = "Home Street 1, 1000 Town, NL"
    keys = tmp_path / "web.env"
    keys.write_text("GOOGLE_MAPS_API_KEY=g-key\n")
    keys.chmod(0o600)
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        maps_enabled=maps_on,
        maps_home=home if maps_on else "",
        web_keys_path=keys,
    )
    store = Store.open(cfg.db_path)
    try:
        store.set_state("maps_home_area", f"52.37,4.89|{home}")
        store.set_state("maps_home_failed_2026-10-10", home)
        App(cfg, store, SignalClient([sys.executable, str(FAKE)], cfg.allowed_acis), FakeGemma(), Fetcher())
        values = [str(row[0]) for row in store._conn.execute("SELECT value FROM state")]
        assert not any("Home Street" in value for value in values)
        assert (store.get_state("maps_home_area") is not None) is maps_on  # the centre stays for the same address
    finally:
        store.close()


async def test_a_voice_note_is_heard_answered_and_spoken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeVoiceService() as service:
        harness = await start(tmp_path, monkeypatch, FakeGemma(text("Done, the lamp is off.")), voice_url=service.url)
        try:
            file = put(harness.folder, "v1.m4a", M4A)
            deliver(harness.folder, None, T0, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: len(voice_notes(harness.folder)) == 1)
            sends = [r["params"] for r in requests(harness.folder) if r["method"] == "send"]
            assert [params.get("message") for params in sends] == ["Done, the lamp is off.", None]  # text first
            note = voice_notes(harness.folder)[0]
            assert (note["recipient"], note["voiceNote"]) == ([ME], True)
            spoken = Path(note["attachments"][0])
            copy = harness.folder / "outbox" / spoken.name
            # the fake signal-cli logs the request before it copies the file, and the bridge deletes it after the answer
            await until(lambda: copy.exists() and copy.stat().st_size > 0 and not spoken.exists())
            assert copy.read_bytes()[4:8] == b"ftyp"
            assert harness.app.store.message(1).text == "[voice] Turn off the lamp."
            assert str(harness.gemma.calls[0]["messages"][-1]["content"]).endswith("[voice] Turn off the lamp.")
            assert service.requests[0][3] == M4A
            assert json.loads(service.requests[1][3])["input"] == "Done, the lamp is off."
            assert not file.exists() and harness.app.store.attachments(1) == []
        finally:
            await stop(harness)


async def test_voice_modes_and_the_echo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeVoiceService() as service:
        gemma = FakeGemma(text("I'm fine, thanks for asking."), text("The lamp is off now."))
        harness = await start(tmp_path, monkeypatch, gemma, voice_url=service.url)
        try:
            folder = harness.folder
            deliver(folder, "/voice echo on", T0)
            deliver(folder, "/voice on", T0 + 1)
            deliver(folder, "how are you?", T0 + 2)  # typed, but voice is on: spoken too
            await until(lambda: len(voice_notes(folder)) == 1)
            deliver(folder, "/voice off", T0 + 3)
            put(folder, "v2.m4a", M4A)
            deliver(folder, None, T0 + 4, attachments=[voice("v2.m4a", M4A)])  # spoken, but voice is off
            await until(lambda: len(sent(folder)) == 6)
            assert sent(folder) == [
                "Transcript echo: on (I show what I heard first).",
                "Voice replies: on (every answer).",
                "I'm fine, thanks for asking.",
                "Voice replies: off.",
                '🎙️ "Turn off the lamp."',
                "The lamp is off now.",
            ]
            await asyncio.sleep(0.3)
            assert len(voice_notes(folder)) == 1
        finally:
            await stop(harness)


async def test_voice_notes_that_cannot_be_used_get_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeVoiceService() as service:
        service.transcript = ""  # what the service answers for silence
        harness = await start(tmp_path, monkeypatch, FakeGemma(), voice_url=service.url)
        try:
            folder = harness.folder
            files = [put(folder, "short.m4a", M4A[:500]), put(folder, "silent.m4a", M4A)]
            files.append(put(folder, "doc.bin", b"%PDF-1.7" + bytes(2000)))
            deliver(folder, None, T0, attachments=[voice("short.m4a", M4A[:500])])
            deliver(folder, None, T0 + 1, attachments=[voice("silent.m4a", M4A)])
            deliver(folder, None, T0 + 2, attachments=[{"id": "doc.bin", "size": 2008}])  # no type: the bytes decide
            await until(lambda: len(sent(folder)) == 3)
            assert sent(folder) == [prompts.VOICE_UNCLEAR, prompts.VOICE_UNCLEAR, prompts.TEXT_ONLY_VOICE]
            assert harness.gemma.calls == []
            assert service.paths() == ["/v1/audio/transcriptions"]  # only the silent one reached the service
            assert not any(file.exists() for file in files)
        finally:
            await stop(harness)


async def test_too_long_voice_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeVoiceService() as service:
        service.status["/v1/audio/transcriptions"] = 413  # over 3 minutes
        harness = await start(tmp_path, monkeypatch, FakeGemma(), voice_url=service.url, voice_max_audio_bytes=5_000)
        try:
            folder = harness.folder
            put(folder, "long.m4a", M4A)
            huge = put(folder, "huge.m4a", M4A)
            deliver(folder, None, T0, attachments=[voice("long.m4a", M4A)])
            deliver(folder, None, T0 + 1, attachments=[{"contentType": "audio/aac", "id": "huge.m4a", "size": 9_000}])
            await until(lambda: len(sent(folder)) == 2)
            assert sent(folder) == [prompts.VOICE_TOO_LONG, prompts.VOICE_TOO_LONG]
            fetched = [r["params"]["id"] for r in requests(folder) if r["method"] == "getAttachment"]
            assert fetched == ["long.m4a"]  # the huge one was never downloaded
            assert not huge.exists()
        finally:
            await stop(harness)


async def test_voice_service_not_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), voice_url=closed_port_url())
    try:
        file = put(harness.folder, "v1.m4a", M4A)
        deliver(harness.folder, None, T0, attachments=[voice("v1.m4a", M4A)])
        await until(lambda: sent(harness.folder) == [prompts.VOICE_DOWN])
        assert harness.gemma.calls == []
        assert harness.app.store.message(1).status == "failed"
        assert not file.exists()
    finally:
        await stop(harness)


async def test_when_speech_fails_the_text_answer_stands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeVoiceService() as service:
        service.status["/v1/audio/speech"] = 500
        harness = await start(tmp_path, monkeypatch, FakeGemma(text("The lamp is off.")), voice_url=service.url)
        try:
            put(harness.folder, "v1.m4a", M4A)
            deliver(harness.folder, None, T0, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: "/v1/audio/speech" in service.paths())
            await asyncio.sleep(0.3)
            assert sent(harness.folder) == ["The lamp is off."] and voice_notes(harness.folder) == []
            assert list((tmp_path / "data" / "voice").glob("*")) == []
        finally:
            await stop(harness)


async def test_the_sweep_also_clears_old_spoken_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        leftover = tmp_path / "data" / "voice" / "crash-leftover.m4a"
        leftover.parent.mkdir(parents=True)
        leftover.write_bytes(b"spoken answer of a killed run")
        os.utime(leftover, (time.time() - 3_600, time.time() - 3_600))
        assert harness.app.sweep_files() == 1
        assert not leftover.exists()
    finally:
        await stop(harness)


async def devices_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gemma: FakeGemma) -> tuple[Harness, Home]:
    home = Home(tmp_path, monkeypatch)
    return await start(tmp_path, monkeypatch, gemma, tapo_devices=DEVICE_SPECS, tapo_helper=home.helper), home


async def test_a_light_is_switched_and_the_note_says_what_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(tool("device_set", device="living room lamp", power="off"), text("The lamp is off."))
    harness, home = await devices_app(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "turn off the living room lamp", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == ["The lamp is off.", "Devices: living room lamp off"]
        assert home.sets() == [["set", "192.168.1.20", "--power", "off"]]
        assert [s["function"]["name"] for s in gemma.calls[0]["tools"]][-2:] == ["device_status", "device_set"]
    finally:
        await stop(harness)


async def test_a_switch_claimed_with_no_device_change_gets_codes_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Done! I've turned off the living room lamp."), text("Ok."))
    harness, home = await devices_app(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "turn off the living room lamp", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == ["Done! I've turned off the living room lamp.", "Nothing was switched."]
        assert home.sets() == []
        deliver(harness.folder, "hm", T0 + 60_000)
        await until(lambda: len(gemma.calls) == 2)
        history = json.dumps(gemma.calls[1]["messages"])
        assert "Nothing was switched" not in history  # a note: never in her history
    finally:
        await stop(harness)


async def test_on_a_call_a_switch_claimed_with_no_change_is_said_by_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("I've switched off the living room lamp."))
    home = Home(tmp_path, monkeypatch)
    harness = await start(
        tmp_path, monkeypatch, gemma, tapo_devices=DEVICE_SPECS, tapo_helper=home.helper, **call_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        said = await app.call_turn(ME, "turn off the living room lamp", lambda: None)
        assert (said.text, said.code_text) == ("I've switched off the living room lamp.", "Nothing was switched.")
        await until(lambda: "Nothing was switched." in sent(harness.folder))  # and the note as a text
        assert home.sets() == []
    finally:
        await stop(harness)


async def test_an_ask_first_device_changes_only_after_the_users_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(
        tool("device_set", device="heater", power="off"),
        text("The heater needs your OK first: reply yes."),
        tool("device_set", device="heater", power="off"),
        text("Reply yes to switch the heater off."),
        text("Sunny, 24 degrees."),
    )
    harness, home = await devices_app(tmp_path, monkeypatch, gemma)
    try:
        folder = harness.folder
        deliver(folder, "switch the heater off", T0)
        await until(lambda: len(sent(folder)) == 2)
        assert sent(folder) == [
            "The heater needs your OK first: reply yes.",
            "Devices: reply yes within 2 minutes to switch heater off",  # the bridge's own words
        ]
        assert home.sets() == []
        deliver(folder, "yes", T0 + 1)
        await until(lambda: len(sent(folder)) == 3)
        assert sent(folder)[2] == "Devices: heater off"  # done by the bridge itself, not by Gemma
        assert home.sets() == [["set", "192.168.1.30", "--power", "off"]] and len(gemma.calls) == 2
        deliver(folder, "switch the heater off", T0 + 2)
        await until(lambda: len(sent(folder)) == 5)
        deliver(folder, "what's the weather?", T0 + 3)  # anything but yes cancels, and is answered as usual
        await until(lambda: len(sent(folder)) == 6)
        assert sent(folder)[5] == "Sunny, 24 degrees." and len(home.sets()) == 1
    finally:
        await stop(harness)


async def test_in_another_language_an_ask_first_device_gets_only_codes_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dutch Gemma may claim "Ik heb de heater uitgezet" while the heater waits for a yes (a real-model eval found it):
    the user gets code's question instead, and the history keeps that, never the claim."""
    claim = "Ik heb de heater uitgezet, maar ik heb eerst je bevestiging nodig."
    gemma = FakeGemma(tool("device_set", device="heater", power="off"), text(claim))
    home = Home(tmp_path, monkeypatch)
    harness = await start(
        tmp_path, monkeypatch, gemma, language="nl", tapo_devices=DEVICE_SPECS, tapo_helper=home.helper
    )
    try:
        deliver(harness.folder, "zet de verwarming uit", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == [
            "Shall I switch heater off?",
            "Devices: reply yes within 2 minutes to switch heater off",
        ]
        assert harness.app.store.message(2).text == "Shall I switch heater off?"  # the history: what was sent
        assert home.sets() == []
    finally:
        await stop(harness)


async def test_mini_status_has_the_devices_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness, home = await devices_app(tmp_path, monkeypatch, FakeGemma())
    try:
        home.devices["192.168.1.31"] = {"error": "unreachable"}
        home.save()
        assert await harness.app._status_lines() == ["Devices: 3 of 4 answer (fridge not)"]
    finally:
        await stop(harness)


async def test_a_switch_is_reported_even_when_gemma_fails_afterwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(tool("device_set", device="living room lamp", power="off"), GemmaUnavailableError("down"))
    harness, _ = await devices_app(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "turn off the living room lamp", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == [prompts.UNAVAILABLE.format(name=NAME), "Devices: living room lamp off"]
    finally:
        await stop(harness)


async def test_a_voice_note_heard_before_a_restart_is_answered_from_its_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # transcribed, then the bridge restarted before answering: the audio must not be taken for a photo
    now = time.time()

    def prepare(store: Store) -> None:
        conv = store.current_conversation()
        heard = store.add_incoming(conv.id, ME, int((now - 60) * 1000), "text", "[voice] Turn off the lamp.")
        assert heard is not None
        store.add_attachment(heard, "v1.m4a", "audio/aac", len(M4A))

    with FakeVoiceService() as service:
        harness = await start(
            tmp_path, monkeypatch, FakeGemma(text("The lamp is off.")), prepare=prepare, voice_url=service.url
        )
        try:
            put(harness.folder, "v1.m4a", M4A)
            await until(lambda: "The lamp is off." in sent(harness.folder))
            assert prompts.PHOTO_FAILED not in sent(harness.folder)
            assert service.paths().count("/v1/audio/transcriptions") == 0  # not heard twice
        finally:
            await stop(harness)


async def test_the_audio_is_deleted_as_soon_as_it_is_heard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeVoiceService() as service:
        harness = await start(tmp_path, monkeypatch, FakeGemma(GemmaUnavailableError("down")), voice_url=service.url)
        try:
            file = put(harness.folder, "v1.m4a", M4A)
            deliver(harness.folder, None, T0, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: sent(harness.folder) == [prompts.UNAVAILABLE.format(name=NAME)])
            assert harness.app.store.message(1).text == "[voice] Turn off the lamp."
            assert not file.exists() and harness.app.store.attachments(1) == []
        finally:
            await stop(harness)


async def test_a_pending_yes_is_dropped_by_any_other_message_or_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(
        tool("device_set", device="heater", power="off"),
        text("Reply yes to switch the heater off."),
        text("Yes to what?"),
        tool("device_set", device="heater", power="off"),
        GemmaUnavailableError("down"),
        text("Yes to what?"),
    )
    harness, home = await devices_app(tmp_path, monkeypatch, gemma)
    try:
        folder = harness.folder
        deliver(folder, "switch the heater off", T0)
        await until(lambda: len(sent(folder)) == 2)  # the answer and the code's note saying what yes does
        deliver(folder, None, T0 + 1, sticker={"packId": "x", "stickerId": 1})  # unsupported: cancels
        deliver(folder, "yes", T0 + 2)
        await until(lambda: "Yes to what?" in sent(folder))
        deliver(folder, "switch the heater off", T0 + 3)  # Gemma fails after asking for the yes: cancels
        await until(lambda: prompts.UNAVAILABLE.format(name=NAME) in sent(folder))
        deliver(folder, "yes", T0 + 4)
        await until(lambda: sent(folder).count("Yes to what?") == 2)
        assert home.sets() == []
    finally:
        await stop(harness)


def call_settings(tmp_path: Path) -> dict[str, Any]:
    short = Path(tempfile.mkdtemp(prefix="c", dir="/tmp"))  # socket paths are at most 104 bytes
    return {"voice_url": "http://127.0.0.1:9", "calls_enabled": True, "calls_socket_dir": short}


async def test_the_speech_rule_reaches_the_calls_and_status_shows_its_detector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        service.vad, service.turn = "ready", "ready"
        settings = call_settings(tmp_path) | {"voice_url": service.url, "calls_barge": "speech"}
        harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings)
        try:
            assert harness.app.calls is not None and harness.app.calls.settings.barge == "speech"
            lines = await harness.app._status_lines()
            assert "Calls: ready · speech detector: ready · turn detector: ready" in lines
        finally:
            await stop(harness)


async def test_after_a_missed_call_the_voice_ready_note_goes_out_as_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call rang while her voice loaded: the 'not ready' note says she texts, and code's note follows once the voice
    is ready (a note: never in Gemma's history)."""
    with FakeVoiceService() as service:
        service.health = {"stt": "loading", "tts": "ready"}
        harness = await start(
            tmp_path, monkeypatch, FakeGemma(), **(call_settings(tmp_path) | {"voice_url": service.url})
        )
        try:
            app = harness.app
            assert app.calls is not None and app.voice_ready is not None
            await app.calls._on_event({"state": "RINGING_INCOMING", "callId": 5, "uuid": ME})
            await until(lambda: prompts.CALL_NOT_READY in sent(harness.folder))
            await app.voice_ready.check()
            assert prompts.VOICE_READY not in sent(harness.folder)  # still loading
            service.health = {"stt": "ready", "tts": "ready"}
            await app.voice_ready.check()
            await until(lambda: prompts.VOICE_READY in sent(harness.folder))
            rows = app.store._conn.execute("SELECT id FROM messages WHERE text = ?", (prompts.VOICE_READY,)).fetchall()
            notes = [app.store.message(row[0]) for row in rows]
            assert [(m.direction, m.kind, m.peer_aci) for m in notes] == [("out", "note", ME)]
        finally:
            await stop(harness)


async def test_a_call_that_connects_needs_no_voice_ready_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The call manager reports a call once connected (never one that only rang); call_started is too early."""
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **call_settings(tmp_path))
    try:
        app = harness.app
        assert app.voice_ready is not None and app.calls is not None
        assert app.calls._reached == app.voice_ready.reached
        reached: list[str] = []
        monkeypatch.setattr(app.voice_ready, "reached", reached.append)
        app.call_started(ME)
        app.call_ended()
        assert reached == []
    finally:
        await stop(harness)


async def test_the_voice_ready_note_keeps_quiet_hours_when_the_initiative_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[initiative] quiet_hours = "" lets her own messages come at any hour; the long-load note still never wakes."""
    settings = call_settings(tmp_path) | {"initiative_quiet_hours": ""}
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings)
    try:
        assert harness.app.voice_ready is not None
        assert harness.app.voice_ready._quiet_hours == (22 * 60, 8 * 60)
    finally:
        await stop(harness)


async def test_without_calls_there_is_no_voice_ready_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        assert harness.app.voice_ready is None
    finally:
        await stop(harness)


@pytest.mark.parametrize(
    ("chosen", "settings"),
    [
        ({}, (True, 0.6, "speech")),  # the owner's defaults, 17 Oct
        ({"calls_early_end": False, "calls_early_end_pause_seconds": 0.9, "calls_pause": "loudness"}, (False, 0.9, "loudness")),
    ],
)  # fmt: skip
async def test_the_early_end_settings_reach_the_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chosen: dict[str, Any], settings: tuple[bool, float, str]
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **call_settings(tmp_path) | chosen)
    try:
        calls = harness.app.calls
        assert calls is not None
        assert (calls.settings.early_end, calls.settings.early_end_pause_s, calls.settings.pause) == settings
    finally:
        await stop(harness)


async def test_with_loudness_status_shows_no_speech_detector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # interrupt = "any sound", pause = "quiet", early_end = false
    settings = call_settings(tmp_path) | {
        "calls_barge": "loudness",
        "calls_pause": "loudness",
        "calls_early_end": False,
    }
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings)
    try:
        assert harness.app.calls is not None and harness.app.calls.settings.barge == "loudness"
        assert "Calls: ready" in await harness.app._status_lines()  # as before: no detector line, no request
    finally:
        await stop(harness)


async def test_with_calls_texts_wait_during_a_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi.")), **call_settings(tmp_path))
    try:
        app = harness.app
        assert "Calls:" in app.context.system_prompt(app.store.current_conversation())
        app.call_started(ME)
        assert app._is_busy()  # the profiler and the mail sorter keep away from Gemma
        deliver(harness.folder, "hello", T0)
        await asyncio.sleep(0.5)
        assert harness.gemma.calls == []  # stored and waiting
        app.call_ended()
        await until(lambda: sent(harness.folder) == ["Hi."])
        assert not app._is_busy()
        lines = await app._status_lines()  # the defaults: the line names both detectors' states
        assert "Calls: ready · speech detector: not ready · turn detector: not ready" in lines
    finally:
        await stop(harness)


async def test_a_call_turn_is_stored_and_answered_with_the_same_brain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(tool("search_history", query="dentist"), text("Two meetings tomorrow."))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        app = harness.app
        checked: list[str] = []
        turn = await app.call_turn(ME, "what's on tomorrow", lambda: checked.append("x"))
        assert turn.text == "Two meetings tomorrow." and turn.reply_id is not None and not turn.asks_yes
        assert checked == ["x"]  # "let me check" while the tool ran
        assert gemma.calls[0]["max_tokens"] == CALL_MAX_TOKENS
        assert gemma.calls[0]["messages"][-1]["content"].endswith("[call] what's on tomorrow")
        conversation = app.store.current_conversation()
        window = app.store.window_messages(conversation.id, 0)
        assert [m.text for m in window] == ["[call] what's on tomorrow", "Two meetings tomorrow."]
        app.call_interrupted(turn.reply_id, "Two …")
        assert app.store.message(turn.reply_id).text == "Two …"  # history holds what the user heard
        assert app.store.unanswered_incoming() == [] and app.store.unsent_outgoing() == []
        assert sent(harness.folder) == []  # nothing goes out as a text
    finally:
        await stop(harness)


async def test_a_spoken_yes_switches_the_waiting_device_in_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(
        tool("device_set", device="heater", power="off"),
        text("The heater needs your yes. Say yes."),
        tool("device_set", device="heater", power="on"),
        text("Say yes to switch it on."),
    )
    home = Home(tmp_path, monkeypatch)
    harness = await start(
        tmp_path, monkeypatch, gemma, tapo_devices=DEVICE_SPECS, tapo_helper=home.helper, **call_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        asked = await app.call_turn(ME, "switch the heater off", lambda: None)
        assert asked.asks_yes and home.sets() == []
        done = await app.call_turn(ME, "Yes.", lambda: None)
        assert (done.text, done.code_text) == ("", "Done: heater off.") and done.reply_id is None  # code's words
        assert home.sets() == [["set", "192.168.1.30", "--power", "off"]] and len(gemma.calls) == 2  # code, not Gemma
        await until(lambda: "Devices: heater off" in sent(harness.folder))  # and the usual note
        again = await app.call_turn(ME, "switch the heater back on", lambda: None)
        assert again.asks_yes
        app.call_ended()  # hanging up drops a question still waiting
        assert app.devices is not None and not app.devices.pending
    finally:
        await stop(harness)


async def test_on_a_call_the_device_question_is_spoken_by_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The caller hears what their yes will do in code's words; Gemma's last question may be about something else."""
    gemma = FakeGemma(
        tool("device_set", device="heater", power="off"), text("Sunny, 18 degrees. Want the weekend too?")
    )
    home = Home(tmp_path, monkeypatch)
    harness = await start(
        tmp_path, monkeypatch, gemma, tapo_devices=DEVICE_SPECS, tapo_helper=home.helper, **call_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        asked = await app.call_turn(ME, "what's the weather? and the heater can go off", lambda: None)
        assert asked.asks_yes and asked.code_text == "Shall I switch heater off?"
        assert asked.text == "Sunny, 18 degrees. Want the weekend too?"
        assert asked.reply_id is not None
        assert app.store.message(asked.reply_id).text == f"{asked.text} {asked.code_text}"  # stored as heard
        assert home.sets() == []
    finally:
        await stop(harness)


async def test_on_a_call_in_another_language_only_codes_device_question_is_said_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gemma in another language may claim the device is already off (a real-model eval found it in Dutch): the caller
    hears only code's question, and the history keeps only what was said, so Gemma never learns the claim from it."""
    claim = "Ik heb de heater uitgezet, maar je moet nog even 'ja' zeggen."
    gemma = FakeGemma(tool("device_set", device="heater", power="off"), text(claim))
    home = Home(tmp_path, monkeypatch)
    harness = await start(
        tmp_path,
        monkeypatch,
        gemma,
        language="nl",
        tapo_devices=DEVICE_SPECS,
        tapo_helper=home.helper,
        **call_settings(tmp_path),
    )
    try:
        app = harness.app
        app.call_started(ME)
        asked = await app.call_turn(ME, "doe de verwarming uit", lambda: None)
        assert asked.asks_yes and (asked.text, asked.code_text) == ("", "Shall I switch heater off?")
        assert asked.reply_id is not None
        assert app.store.message(asked.reply_id).text == "Shall I switch heater off?"
        assert home.sets() == []  # nothing switched before the yes
    finally:
        await stop(harness)


# a call's glance: at its start code gives her today's calendar and mail digest as numbers and times only, never a
# title, name or subject (that text would make the whole call outside text); she reads the details only on a yes


def calendar_event(title: str, start: str, end: str, place: str = "") -> dict[str, Any]:
    """An event as the calendar helper gives it (start and end as ISO times in Amsterdam)."""
    return {
        "title": title,
        "place": place,
        "start": start,
        "end": end,
        "all_day": False,
        "zone": "Europe/Amsterdam",
        "answer": "",
        "uid": f"{title.lower().replace(' ', '-')}@example.com",
    }


GLANCE_DAY = [
    calendar_event("Breakfast with Sam", "2026-10-09T08:00:00+02:00", "2026-10-09T09:00:00+02:00"),
    calendar_event("Dentist", "2026-10-09T14:00:00+02:00", "2026-10-09T15:00:00+02:00", place="Prinsengracht 12"),
    calendar_event("Call with Anna", "2026-10-09T16:00:00+02:00", "2026-10-09T16:30:00+02:00"),
    calendar_event("Dinner", "2026-10-09T19:00:00+02:00", "2026-10-09T21:30:00+02:00"),
]
GLANCE_LINES = (
    "Calendar today: 3 events left, next 14:00–15:00 (in 50 min), last ends 21:30.\n"
    "Mail digest: 2 to answer (1 urgent), 1 action."
)


def glance_mail(store: Store) -> None:
    """Two mails to answer (one urgent) and a bill, by people and about things the call must never name."""
    from tests.test_mail_digest import add as add_mail

    add_mail(store, "g1", "reply", "Asks about November shipping.", "2026-10-08T07:00:00+00:00", sender="Bob Lee")
    add_mail(store, "g2", "urgent_reply", "Contract needs a signature today.", "2026-10-09T06:00:00+00:00")
    add_mail(store, "g3", "action", "Invoice to pay by Friday.", "2026-10-09T07:00:00+00:00", sender="Acme Billing")


def glance_settings(tmp_path: Path, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    mail = {
        "mail_tenant_id": MAIL_TENANT,
        "mail_client_id": MAIL_CLIENT,
        "mail_accounts": (MailAccount("family", "info@example.com"),),
    }
    return calendar_settings(tmp_path, GLANCE_DAY if events is None else events) | mail | call_settings(tmp_path)


def glance_clock() -> Clock:
    clock = Clock()
    clock.now = datetime(2026, 10, 9, 13, 10, tzinfo=TZ_APP).timestamp()  # a Friday
    return clock


async def test_a_call_starts_knowing_todays_numbers_and_times_never_a_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Hi! Three things today, the next at two. Want the rundown?"), text("Sure."))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **glance_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        first = await app.call_turn(ME, "hi, it's me", lambda: None)
        assert first.text == "Hi! Three things today, the next at two. Want the rundown?"
        await app.call_turn(ME, "not now, tell me later", lambda: None)
        system = [call["messages"][0]["content"] for call in gemma.calls]
        glance = prompts.call_glance(GLANCE_LINES.split("\n"), at="13:10", calendar=True, mail=True)
        assert system[0].endswith(f"\n\n{glance}") and system[1] == system[0]  # the same for the whole call
        # a call's rules leave out the list of what she can do (spoken answers, never a list)
        assert system[0] == f"{app.context.system_prompt(app.store.current_conversation(), call=True)}\n\n{glance}"
        for words in ("Sam", "Dentist", "Prinsengracht", "Anna", "Dinner", "Bob", "shipping", "Contract", "Acme"):
            assert words not in glance, words
        assert app.store.tool_calls() == []  # numbers only: no calendar or mail tool ran, nothing outside was read
    finally:
        await stop(harness)


async def test_a_call_glance_counts_a_mailbox_not_read_never_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status.json says the family mailbox fails since Monday (a sign-in): the glance adds a count and a time."""

    def prepare(store: Store) -> None:
        glance_mail(store)
        monday = datetime(2026, 10, 5, 9, 10, tzinfo=TZ_APP).timestamp()
        moments = iter((monday, monday + 300))
        status = PartStatus(tmp_path / "data" / "status.json", clock=lambda: next(moments))
        status.ok(mail_part("family"))
        status.failed(mail_part("family"), "sign_in")

    gemma = FakeGemma(text("Hi!"))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=prepare, clock=glance_clock(), **glance_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "hi, it's me", lambda: None)
        system = gemma.calls[0]["messages"][0]["content"]
        assert "Mail digest: 2 to answer (1 urgent), 1 action.\n1 mailbox not read since Mon 09:10.\n" in system
        assert "family" not in system.rpartition("AT THE START OF THIS CALL")[2]
    finally:
        await stop(harness)


OFFER = "Hi! Three things today, the next at two. Want the rundown?"


async def test_a_yes_to_her_offer_reads_the_day_for_her(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Her answer given with the glance offered the rundown and the user says yes: code reads today's calendar and the
    mail digest itself and gives them to her as her own tool calls (live evals: she called no tool after the yes, 7 of
    7); that answer read outside text exactly as if she had called them, and the glance stays numbers only."""
    gemma = FakeGemma(text(OFFER), text("At two the dentist, then a call with Anna. Two mails want a reply."))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **glance_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "hi, it's me", lambda: None)
        said: list[str] = []
        turn = await app.call_turn(ME, "Yes please.", lambda: said.append("let me check"))
        assert turn.text == "At two the dentist, then a call with Anna. Two mails want a reply."
        assert said == ["let me check"]
        messages = gemma.calls[1]["messages"]
        asked, calendar, mail = messages[-3], messages[-2]["content"], messages[-1]["content"]
        assert [call["function"]["name"] for call in asked["tool_calls"]] == ["calendar_agenda", "mail_digest"]
        assert json.loads(asked["tool_calls"][0]["function"]["arguments"]) == {"start": "today", "days": 1}
        assert messages[-4]["role"] == "user" and messages[-4]["content"].endswith("[call] Yes please.")
        assert "Dentist" in calendar and "Contract needs a signature today." in mail
        glance = prompts.call_glance(GLANCE_LINES.split("\n"), at="13:10", calendar=True, mail=True)
        assert messages[0]["content"].endswith(glance)  # the glance itself stays numbers only
        assert [call.name for call in app.store.tool_calls()] == ["calendar_agenda", "mail_digest"]
        later = app.store.add_call_turn(app.store.current_conversation().id, ME, "[call] thanks")
        assert app.context.after_outside(app.store.current_conversation(), later)  # outside text, as ever
    finally:
        await stop(harness)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Hi! Busy day today.", "yes"),  # she offered nothing
        (OFFER, "not now, tell me later"),  # no yes
        (OFFER, "yes, and what's the weather?"),  # not a whole yes: she reads with her tools as ever
    ],
)
async def test_without_her_offer_and_a_clear_yes_code_reads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first: str, second: str
) -> None:
    gemma = FakeGemma(text(first), text("Sure."))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **glance_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "hi, it's me", lambda: None)
        await app.call_turn(ME, second, lambda: None)
        assert app.store.tool_calls() == [] and "tool_calls" not in gemma.calls[1]["messages"][-1]
    finally:
        await stop(harness)


async def test_the_rundown_is_read_once_a_call_and_only_right_after_her_offer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text(OFFER), text("Okay. Anything else?"), text("Sure."), text(OFFER), text("Bye."))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **glance_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "hi", lambda: None)
        await app.call_turn(ME, "hmm, let me think", lambda: None)  # the offer passed
        await app.call_turn(ME, "yes", lambda: None)  # a yes to "Anything else?": not to the offer
        assert app.store.tool_calls() == []
        app.call_ended()
        app.call_started(ME)  # a new call offers again
        await app.call_turn(ME, "hi again", lambda: None)
        await app.call_turn(ME, "yes", lambda: None)
        assert [call.name for call in app.store.tool_calls()] == ["calendar_agenda", "mail_digest"]
    finally:
        await stop(harness)


async def test_her_offer_may_come_later_in_the_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(text("Hi! Good to hear you."), text(OFFER), text("At two the dentist."))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **glance_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "hi", lambda: None)
        await app.call_turn(ME, "how busy is my day?", lambda: None)
        await app.call_turn(ME, "ja", lambda: None)  # English call: another language's yes is none
        assert app.store.tool_calls() == []
    finally:
        await stop(harness)
    gemma = FakeGemma(text("Hi! Good to hear you."), text(OFFER), text("At two the dentist."))
    harness = await start(
        tmp_path / "b", monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **glance_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "hi", lambda: None)
        await app.call_turn(ME, "how busy is my day?", lambda: None)
        await app.call_turn(ME, "okay", lambda: None)
        assert [call.name for call in app.store.tool_calls()] == ["calendar_agenda", "mail_digest"]
    finally:
        await stop(harness)


async def test_a_call_without_the_glance_reads_nothing_on_a_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Hi! Want to hear a joke?"), text("Why did the tomato blush?"))
    harness = await start(tmp_path, monkeypatch, gemma, clock=glance_clock(), **call_settings(tmp_path))
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "hi", lambda: None)
        await app.call_turn(ME, "yes", lambda: None)
        assert app.store.tool_calls() == []
    finally:
        await stop(harness)


async def test_with_the_glance_a_call_turn_still_sets_a_reminder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The glance is code's numbers, not outside text: what the user says on the call still counts as their own."""
    gemma = FakeGemma(tool("set_reminder", at="17:00", text="call the dentist"), text("Done."))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **glance_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        turn = await app.call_turn(ME, "remind me at five to call the dentist", lambda: None)
        assert "AT THE START OF THIS CALL" in gemma.calls[0]["messages"][0]["content"]
        assert turn.code_text.startswith("Reminder set")
        assert [r.text for r in app.store.open_reminders()] == ["call the dentist"]
    finally:
        await stop(harness)


async def test_a_practice_call_and_a_setup_without_calendar_or_mail_get_no_glance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(
        tmp_path, monkeypatch, FakeGemma(text("Hi.")), clock=glance_clock(), **call_settings(tmp_path)
    )
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "hi", lambda: None)
        system = harness.gemma.calls[0]["messages"][0]["content"]
        assert "AT THE START OF THIS CALL" not in system  # nothing to count: her input as before
        assert system == app.context.system_prompt(app.store.current_conversation(), call=True)  # no list either
        app.call_ended()
    finally:
        await stop(harness)
    harness = await start(tmp_path / "practice", monkeypatch, FakeGemma(), **glance_settings(tmp_path))
    try:
        harness.app.call_started(ME, practice=True)
        assert harness.app._glance is None  # practice reads neither the calendar nor the mail
        harness.app.call_ended()
    finally:
        await stop(harness)


async def test_a_slow_calendar_never_holds_up_the_first_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mail counts are a quick local read, taken when the call starts: the first answer has them even when the
    calendar is slow; the calendar's line joins from the turn it is ready."""
    monkeypatch.setattr(app_module, "GLANCE_WAIT_SECONDS", 0.2)
    gemma = FakeGemma(text("Hi."), text("Sure."))
    settings = glance_settings(tmp_path)
    calendar = Path(settings["calendar_helper"][2])
    calendar.write_text(json.dumps({"calendar": "Family", "events": GLANCE_DAY, "sleep": 1.5}))
    harness = await start(tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **settings)
    try:
        app = harness.app
        app.call_started(ME)
        started = time.monotonic()
        await app.call_turn(ME, "hi", lambda: None)
        assert time.monotonic() - started < 1.2  # waited at most a moment for the calendar
        first = gemma.calls[0]["messages"][0]["content"]
        assert first.endswith(
            prompts.call_glance(
                ["Mail digest: 2 to answer (1 urgent), 1 action."], at="13:10", calendar=True, mail=True
            )
        )
        assert "Calendar today" not in first
        await until(lambda: app._glance is not None and app._glance.calendar_done(), limit=5)
        await app.call_turn(ME, "what's up?", lambda: None)
        glance = prompts.call_glance(GLANCE_LINES.split("\n"), at="13:10", calendar=True, mail=True)
        assert gemma.calls[1]["messages"][0]["content"].endswith(glance)  # from the turn it is ready
        app.call_ended()
        assert app._glance is None  # the next call counts again
    finally:
        await stop(harness)


# the model's warm-up when a call starts (owner, 17 Oct): Gemma loads and reads the first turn's request up to the
# caller's words, so that turn finds the prefix in Ollama's cache (live, 17 Oct: a cold load and a miss over 16,855
# tokens cost the first answer 19.5 s)


def warm_lines(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [
        (r.levelno, r.getMessage()) for r in caplog.records if "warm" in r.getMessage() and "calls:" in r.getMessage()
    ]


def same_options(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Two requests ask Gemma the same way, apart from their input and length."""
    skip = ("messages", "max_tokens")
    return {k: v for k, v in first.items() if k not in skip} == {k: v for k, v in second.items() if k not in skip}


async def test_a_calls_warm_up_is_its_first_turns_request_up_to_the_callers_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The proof that Ollama keeps the prefix: the warm-up's input is the first turn's without its new message (the
    same builder, the same order), with the same tools and options, built after the same waits (the glance's
    calendar), asking for one token."""
    caplog.set_level(logging.INFO, logger="local_assistant")
    gemma = FakeGemma(text("Hi!"), Completion("", prompt_tokens=16_855), text("Three things today. Want the rundown?"))
    harness = await start(
        tmp_path,
        monkeypatch,
        gemma,
        prepare=glance_mail,
        clock=glance_clock(),
        warm_up=True,
        **glance_settings(tmp_path),
    )
    try:
        app, folder = harness.app, harness.folder
        deliver(folder, "hello", T0)
        await until(lambda: "Hi!" in sent(folder))  # (the mail's sign-in note may come too)
        app.call_started(ME)
        await app.call_warm_up(ME)
        await app.call_turn(ME, "what's on today?", lambda: None)
        _, warm, first = gemma.calls
        assert warm["messages"] == first["messages"][:-1]
        assert [m["role"] for m in first["messages"]] == ["system", "user", "assistant", "user"]
        assert first["messages"][-1]["content"].endswith("[call] what's on today?")
        glance = prompts.call_glance(GLANCE_LINES.split("\n"), at="13:10", calendar=True, mail=True)
        assert warm["messages"][0]["content"].endswith(glance)  # the calendar was read in time: its line is there
        assert warm["tools"] == first["tools"] and warm["tools"] and same_options(warm, first)
        assert (warm["max_tokens"], first["max_tokens"]) == (1, CALL_MAX_TOKENS)
        [(level, line)] = warm_lines(caplog)
        assert level == logging.INFO and re.fullmatch(
            r"calls: model warmed up in \d+\.\d s \(prompt 16,855 tokens\)", line
        )
        app.call_ended()
    finally:
        await stop(harness)


async def test_a_practice_calls_warm_up_is_its_practice_request_up_to_the_callers_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(Completion(""), text("Leuk! Waar werk je?"))
    harness = await start(tmp_path, monkeypatch, gemma, warm_up=True, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble nl", T0)
        await until(lambda: len(sent(folder)) == 1)
        session = app.babble.running(ME)
        assert session is not None
        app.call_started(ME, practice=True)
        await app.call_warm_up(ME, practice=True)
        await app.call_turn(ME, "Ik woon in Utrecht.", lambda: None, practice=True)
        warm, first = gemma.calls
        assert warm["messages"] == first["messages"][:-1] and same_options(warm, first)
        conversation = app.store.conversation(session.conversation_id)
        rules = app.babble.turn(spoken=True).rules
        assert warm["messages"] == [
            {"role": "system", "content": app.context.system_prompt(conversation, rules, call=True)}
        ]  # the practice rules, in the practice conversation
        assert warm["tools"] is None and warm["max_tokens"] == 1
        app.call_ended()
    finally:
        await stop(harness)


async def test_only_ollama_itself_gets_a_warm_up_never_a_scripted_gemma(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant")
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **call_settings(tmp_path))
    try:
        harness.app.call_started(ME)
        await harness.app.call_warm_up(ME)
        assert harness.gemma.calls == [] and warm_lines(caplog) == []  # the tests' scripts keep their order
        harness.app.call_ended()
    finally:
        await stop(harness)
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "ollama" / "data",
        log_dir=tmp_path / "ollama" / "logs",
        **call_settings(tmp_path),
    )
    store = Store.open(cfg.db_path)
    signal = SignalClient([sys.executable, str(FAKE)], cfg.allowed_acis)
    app = App(cfg, store, signal, OllamaGemma(closed_port_url(), cfg.model, unreachable_seconds=0), Fetcher())
    try:
        app.call_started(ME)
        await app.call_warm_up(ME)  # Ollama itself is asked (nothing listens on this port)
        assert warm_lines(caplog) == [(logging.WARNING, "calls: model warm-up failed (GemmaUnavailableError)")]
        app.call_ended()
    finally:
        store.close()


async def test_a_failed_or_slow_warm_up_is_one_warning_with_its_kind_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant")
    monkeypatch.setattr(app_module, "WARM_UP_SECONDS", 0.2)
    never: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    gemma = FakeGemma(GemmaUnavailableError("Ollama unreachable for 120 s (a private detail)"), never)
    harness = await start(tmp_path, monkeypatch, gemma, warm_up=True, **call_settings(tmp_path))
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_warm_up(ME)
        await app.call_warm_up(ME)  # the next one never answers: cut off at WARM_UP_SECONDS
        assert warm_lines(caplog) == [
            (logging.WARNING, "calls: model warm-up failed (GemmaUnavailableError)"),
            (logging.WARNING, "calls: model warm-up failed (timeout)"),
        ]
        assert "private detail" not in caplog.text
        app.call_ended()
    finally:
        await stop(harness)


async def test_a_warm_up_still_reading_never_holds_up_the_first_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Ollama itself runs one request at a time: the turn's waits behind the warm-up there, then finds its prefix
    read; the bridge never holds the turn back for it."""
    caplog.set_level(logging.INFO, logger="local_assistant")
    reading: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    gemma = FakeGemma(reading, text("Hi."))
    harness = await start(tmp_path, monkeypatch, gemma, warm_up=True, **call_settings(tmp_path))
    try:
        app = harness.app
        app.call_started(ME)
        warming = asyncio.create_task(app.call_warm_up(ME))
        await until(lambda: len(gemma.calls) == 1)
        turn = await app.call_turn(ME, "hi", lambda: None)
        assert turn.text == "Hi." and not warming.done()
        reading.set_result(Completion("", prompt_tokens=900))
        await warming
        [(_, line)] = warm_lines(caplog)
        assert line.startswith("calls: model warmed up in ") and line.endswith(" s (prompt 900 tokens)")
        app.call_ended()
    finally:
        await stop(harness)


async def test_a_warm_up_waits_for_a_text_being_answered_and_reads_nothing_once_the_call_ended_or_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(Completion(""))
    harness = await start(tmp_path, monkeypatch, gemma, warm_up=True, **call_settings(tmp_path))
    try:
        app, folder = harness.app, harness.folder
        app.call_started(ME)
        app._worker_idle.clear()  # a text is being answered: its reply belongs in the turn's history
        warming = asyncio.create_task(app.call_warm_up(ME))
        await asyncio.sleep(0.1)
        assert gemma.calls == []
        app._worker_idle.set()
        await warming
        assert len(gemma.calls) == 1
        app.call_ended()
        await app.call_warm_up(ME)  # the call ended before it read anything
        app.call_started(ME)
        deliver(folder, "/lock", T0)
        await until(lambda: app.guard.locked)
        await app.call_warm_up(ME)  # /lock: Gemma is not asked
        assert len(gemma.calls) == 1
        app.call_ended()
    finally:
        await stop(harness)


async def test_an_ended_calls_warm_up_never_reads_during_the_next_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review, 17 Oct: a text is being answered; call A rings and its warm-up waits for that text; A hangs up and call B
    rings, with its own warm-up. Once the text is done, only B's reaches Gemma: A's checks its own call, not just that
    some call is on."""
    gemma = FakeGemma(Completion(""), Completion(""))
    harness = await start(tmp_path, monkeypatch, gemma, warm_up=True, **call_settings(tmp_path))
    try:
        app = harness.app
        app._worker_idle.clear()  # a text is being answered
        app.call_started(ME)
        first = asyncio.create_task(app.call_warm_up(ME))
        await asyncio.sleep(0.05)
        app.call_ended()
        app.call_started(ME)
        second = asyncio.create_task(app.call_warm_up(ME))
        await asyncio.sleep(0.05)
        app._worker_idle.set()
        await asyncio.gather(first, second)
        assert len(gemma.calls) == 1
        app.call_ended()
    finally:
        await stop(harness)


async def test_the_warm_up_waits_for_the_calendar_at_most_its_own_limit_and_leaves_the_first_turn_its_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A calendar slower than WARM_UP_GLANCE_SECONDS: the warm-up goes without its line after that long, and the first
    turn still has its own wait (GLANCE_WAIT_SECONDS, from the turn). Here the calendar is not read by then either, so
    both go without it and the prefix matches; one read between the two still changes the glance (a known gap)."""
    monkeypatch.setattr(app_module, "WARM_UP_GLANCE_SECONDS", 0.3)
    monkeypatch.setattr(app_module, "GLANCE_WAIT_SECONDS", 0.3)
    gemma = FakeGemma(Completion(""), text("Hi."))
    settings = glance_settings(tmp_path)
    calendar = Path(settings["calendar_helper"][2])
    calendar.write_text(json.dumps({"calendar": "Family", "events": GLANCE_DAY, "sleep": 4.0}))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), warm_up=True, **settings
    )
    try:
        app = harness.app
        app.call_started(ME)
        started = time.monotonic()
        await app.call_warm_up(ME)
        assert 0.25 <= time.monotonic() - started < 2.5  # its own limit, never the calendar's 4 s
        started = time.monotonic()
        await app.call_turn(ME, "hi", lambda: None)
        assert time.monotonic() - started >= 0.25  # the turn's own wait, as before the warm-up
        warm, first = gemma.calls
        assert warm["messages"] == first["messages"][:-1]
        assert "Calendar today" not in warm["messages"][0]["content"]
        app.call_ended()
    finally:
        await stop(harness)


async def test_a_calendar_slower_than_a_turns_wait_still_reaches_the_warm_up_and_the_whole_prefix_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review, 17 Oct: the calendar is read live from the call's start and may take some seconds; the first turn comes
    some 20 s later and finds it read. The warm-up waits for it longer than a turn does (WARM_UP_GLANCE_SECONDS), so
    its glance is that turn's: else the system prompt differs at the glance, and Ollama reads the tools and the whole
    history again (6-8 s)."""
    monkeypatch.setattr(app_module, "GLANCE_WAIT_SECONDS", 0.2)
    gemma = FakeGemma(Completion(""), text("Hi."))
    settings = glance_settings(tmp_path)
    calendar = Path(settings["calendar_helper"][2])
    calendar.write_text(json.dumps({"calendar": "Family", "events": GLANCE_DAY, "sleep": 1.0}))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), warm_up=True, **settings
    )
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_warm_up(ME)
        await until(lambda: app._glance is not None and app._glance.calendar_done(), limit=5)  # the caller talks
        await app.call_turn(ME, "hi", lambda: None)
        warm, first = gemma.calls
        assert warm["messages"] == first["messages"][:-1]
        glance = prompts.call_glance(GLANCE_LINES.split("\n"), at="13:10", calendar=True, mail=True)
        assert warm["messages"][0]["content"].endswith(glance)
        app.call_ended()
    finally:
        await stop(harness)


async def test_a_calendar_that_fails_in_any_way_keeps_the_mail_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each part of the glance has its own try: an Outlook reader's unexpected error drops only the calendar's
    numbers, said as not read."""
    gemma = FakeGemma(text("Hi."))
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **glance_settings(tmp_path)
    )
    try:
        app = harness.app
        assert app.calendar is not None

        async def broken() -> str:
            raise RuntimeError("an Outlook reader failed")

        monkeypatch.setattr(app.calendar, "glance", broken)
        app.call_started(ME)
        await app.call_turn(ME, "hi", lambda: None)
        lines = ["Calendar today: not read just now.", "Mail digest: 2 to answer (1 urgent), 1 action."]
        glance = prompts.call_glance(lines, at="13:10", calendar=True, mail=True)
        assert gemma.calls[0]["messages"][0]["content"].endswith(glance)
        app.call_ended()
    finally:
        await stop(harness)


async def test_a_call_that_ends_while_its_first_answer_waits_for_the_glance_still_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end of the call drops the glance; the answer it was waiting for goes on without it (never cancelled)."""
    monkeypatch.setattr(app_module, "GLANCE_WAIT_SECONDS", 3.0)
    gemma = FakeGemma(text("Hi."))
    settings = glance_settings(tmp_path)
    calendar = Path(settings["calendar_helper"][2])
    calendar.write_text(json.dumps({"calendar": "Family", "events": GLANCE_DAY, "sleep": 2}))
    harness = await start(tmp_path, monkeypatch, gemma, prepare=glance_mail, clock=glance_clock(), **settings)
    try:
        app = harness.app
        app.call_started(ME)
        turn = asyncio.create_task(app.call_turn(ME, "hi", lambda: None))
        await asyncio.sleep(0.2)
        app.call_ended()
        answered = await asyncio.wait_for(turn, 5)
        assert answered.text == "Hi." and "AT THE START OF THIS CALL" not in gemma.calls[0]["messages"][0]["content"]
    finally:
        await stop(harness)


async def test_after_a_call_the_summary_is_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(text("- an idea about the garden\n- follow up: ask Anna"))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        await harness.app.call_summary(ME, ["User: the garden", "Estia: Nice idea."], 3)
        # the note is sent before call_summary returns (no waiting). Under heavy load the fake's answer can miss the
        # harness's 5 s request timeout; the bridge then sends again (at-least-once), so the same note may be there twice
        assert set(sent(harness.folder)) == {
            "📞 Call summary (3 min)\n• an idea about the garden\n• follow up: ask Anna"  # as the phone shows it
        }
        [call] = gemma.calls
        assert call["messages"][0] == {"role": "system", "content": prompts.CALL_SUMMARY_RULES.format(name=NAME)}
        assert call["messages"][1]["content"] == "User: the garden\nEstia: Nice idea."
        assert "tools" not in call or not call["tools"]
    finally:
        await stop(harness)


def test_with_calls_signal_cli_is_the_jvm_build_with_the_call_env(tmp_path: Path) -> None:
    folder = Path(tempfile.mkdtemp(prefix="c", dir="/tmp"))
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path,
        voice_url="http://127.0.0.1:9",
        calls_enabled=True,
        calls_socket_dir=folder / "calls",
        calls_signal_cli=Path("/v/signal-cli"),
        calls_tunnel=Path("/v/sct"),
    )
    client = signal_client(cfg)
    assert client._command[:5] == [
        "/v/signal-cli",
        "--scrub-log",
        "--config",
        str(Path.home() / ".local/share/signal-cli"),
        "--trust-new-identities",
    ]
    assert client._env is not None and f"-Duser.home={Path.home()}" in client._env["JAVA_OPTS"]
    assert client._env is not None and client._env["SIGNAL_CALL_TUNNEL_BIN"] == "/v/sct" and client._calls
    assert stat.S_IMODE((folder / "calls").stat().st_mode) == 0o700
    plain = signal_client(Config(bot_number="+306900000000", allowed_acis=frozenset({ME}), data_dir=tmp_path))
    assert plain._command[1] == "-Djava.net.preferIPv4Stack=true" and plain._env is None and not plain._calls


def test_with_calls_and_a_long_data_folder_the_sockets_go_to_the_short_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folder signal-cli is told about is the one the bridge makes (and the call manager cleans):
    cfg.call_socket_dir, also when it falls back to ~/.la-calls."""
    home = Path(tempfile.mkdtemp(prefix="h", dir="/tmp"))
    monkeypatch.setenv("HOME", str(home))
    long = tmp_path / ("x" * 70)
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=long,
        voice_url="http://127.0.0.1:9",
        calls_enabled=True,
        calls_signal_cli=Path("/v/signal-cli"),
        calls_tunnel=Path("/v/sct"),
    )
    assert cfg.call_socket_dir == home / ".la-calls"
    client = signal_client(cfg)
    assert client._env is not None and client._env["SIGNAL_CALL_TUNNEL_SOCKET_DIR"] == str(home / ".la-calls")
    assert stat.S_IMODE((home / ".la-calls").stat().st_mode) == 0o700
    assert not (long / "calls").exists()


async def test_a_device_switched_before_gemma_failed_on_a_call_is_still_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(tool("device_set", device="living room lamp", power="off"), GemmaUnavailableError("gone"))
    home = Home(tmp_path, monkeypatch)
    harness = await start(
        tmp_path, monkeypatch, gemma, tapo_devices=DEVICE_SPECS, tapo_helper=home.helper, **call_settings(tmp_path)
    )
    try:
        turn = await harness.app.call_turn(ME, "turn off the lamp", lambda: None)
        assert turn.text == prompts.CALL_LOST
        assert home.sets() == [["set", "192.168.1.20", "--power", "off"]]
        await until(lambda: "Devices: living room lamp off" in sent(harness.folder))  # what really changed
    finally:
        await stop(harness)


async def test_dropping_a_waiting_question_on_a_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(
        tool("device_set", device="heater", power="off"), text("Say yes to switch it off."), text("Yes to what?")
    )
    home = Home(tmp_path, monkeypatch)
    harness = await start(
        tmp_path, monkeypatch, gemma, tapo_devices=DEVICE_SPECS, tapo_helper=home.helper, **call_settings(tmp_path)
    )
    try:
        app = harness.app
        assert (await app.call_turn(ME, "switch the heater off", lambda: None)).asks_yes
        await app.call_drop_waiting(ME)
        assert app.devices is not None and not app.devices.pending
        assert (await app.call_turn(ME, "yes", lambda: None)).text  # no longer a yes to anything
        assert home.sets() == []
    finally:
        await stop(harness)


async def test_with_spotify_music_is_played_by_text_and_the_note_says_what_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fakes.fake_spotify import FakeSpotify
    from tests.test_music_tools import FakeTokens

    gemma = FakeGemma(tool("music_play", what="jazz"), text("Jazz is on."))
    harness = await start(
        tmp_path,
        monkeypatch,
        gemma,
        spotify_client_id="0123456789abcdef0123456789abcdef",
        spotify_default_device="Kitchen speaker",
    )
    try:
        app = harness.app
        assert app.music is not None
        spotify = FakeSpotify()
        app.music._spotify = spotify  # the real client is replaced by the pretend Spotify
        app.music._tokens = FakeTokens(authorized_at=time.time())
        assert "music_play" in app.context.system_prompt(app.store.current_conversation())
        deliver(harness.folder, "play some jazz", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder)[0] == "Jazz is on."
        assert sent(harness.folder)[1].startswith("Music: playing Jazz Classics on Kitchen speaker at 30 %")
        assert any(line.startswith("Music: Spotify signed in until") for line in await app._status_lines())
    finally:
        await stop(harness)


async def test_music_that_started_before_gemma_failed_is_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fakes.fake_spotify import FakeSpotify
    from tests.test_music_tools import FakeTokens

    gemma = FakeGemma(tool("music_play", what="jazz"), GemmaUnavailableError("gone"), text("Hello."))
    harness = await start(
        tmp_path,
        monkeypatch,
        gemma,
        spotify_client_id="0123456789abcdef0123456789abcdef",
        spotify_default_device="Kitchen speaker",
    )
    try:
        app = harness.app
        assert app.music is not None
        app.music._spotify = FakeSpotify()
        app.music._tokens = FakeTokens(authorized_at=time.time())
        deliver(harness.folder, "play some jazz", T0)
        await until(lambda: any(s.startswith("Music: playing") for s in sent(harness.folder)))
        deliver(harness.folder, "hi", T0 + 1)
        await until(lambda: "Hello." in sent(harness.folder))
        assert sum(s.startswith("Music:") for s in sent(harness.folder)) == 1  # not carried to the next answer
    finally:
        await stop(harness)


async def test_a_failing_music_reminder_does_not_stop_the_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_assistant.music_tools import MusicTools

    def broken(self: MusicTools) -> str | None:
        raise PermissionError("token file owned by root")

    monkeypatch.setattr(MusicTools, "reminder", broken)  # fails on the very first check, at start
    harness = await start(
        tmp_path, monkeypatch, FakeGemma(text("Hi.")), spotify_client_id="0123456789abcdef0123456789abcdef"
    )
    try:
        await asyncio.sleep(0.2)
        deliver(harness.folder, "hello", T0)
        await until(lambda: sent(harness.folder) == ["Hi."])
        assert not harness.task.done()
    finally:
        await stop(harness)


NOS = WebResult("Ajax wint", "https://nos.nl/1", "nos.nl", "", "Ajax won 2-1.")


async def test_with_web_search_the_links_come_in_a_code_written_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(tool("web_search", query="Ajax score"), text("Ajax won 2-1, says nos.nl."))
    harness = await start(tmp_path, monkeypatch, gemma, web_enabled=True, web_keys_path=tmp_path / "web.env")
    try:
        app = harness.app
        assert "web_search" in [schema["function"]["name"] for schema in app.tools.schemas]
        assert "Web search:" in app.context.system_prompt(app.store.current_conversation())
        assert await app._status_lines() == ["Web search: keys missing (scripts/set-web-keys.sh)"]
        app.tools._web = FakeWeb({"ajax": [NOS]})
        deliver(harness.folder, "did Ajax win?", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder) == ["Ajax won 2-1, says nos.nl.", "🔎 Sources\n• nos.nl: https://nos.nl/1"]
        history = app.context.build(app.store.current_conversation(), None)
        assert "Ajax won 2-1, says nos.nl." in json.dumps(history) and "nos.nl/1" not in json.dumps(history)
    finally:
        await stop(harness)


async def test_on_a_call_the_web_links_are_texted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(tool("web_search", query="Ajax score"), text("Ajax won, says nos.nl."))
    harness = await start(
        tmp_path, monkeypatch, gemma, web_enabled=True, web_keys_path=tmp_path / "web.env", **call_settings(tmp_path)
    )
    try:
        app = harness.app
        app.tools._web = FakeWeb({"ajax": [NOS]})
        turn = await app.call_turn(ME, "did Ajax win", lambda: None)
        assert turn.text == "Ajax won, says nos.nl."
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == ["🔎 Sources\n• nos.nl: https://nos.nl/1"]
    finally:
        await stop(harness)


async def test_an_audio_file_is_heard_as_outside_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # D4: audio not recorded in Signal (no voice-note flag) is someone else's voice: "[audio file] …", a text answer
    with FakeVoiceService() as service:
        harness = await start(
            tmp_path, monkeypatch, FakeGemma(text("It asks to switch off the lamp.")), voice_url=service.url
        )
        try:
            put(harness.folder, "a1.m4a", M4A)
            deliver(harness.folder, None, T0, attachments=[{**voice("a1.m4a", M4A), "isVoiceNote": False}])
            await until(lambda: len(sent(harness.folder)) == 1)
            assert harness.app.store.message(1).text == "[audio file] Turn off the lamp."
            assert str(harness.gemma.calls[0]["messages"][-1]["content"]).endswith("[audio file] Turn off the lamp.")
            assert voice_notes(harness.folder) == []  # a text answer: the user did not speak
        finally:
            await stop(harness)


async def test_words_typed_with_an_audio_file_stay_with_its_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        harness = await start(tmp_path, monkeypatch, FakeGemma(text("It is a lamp request.")), voice_url=service.url)
        try:
            put(harness.folder, "a1.m4a", M4A)
            files = [{**voice("a1.m4a", M4A), "isVoiceNote": False}]
            deliver(harness.folder, "who is this, should I reply?", T0, attachments=files)
            await until(lambda: len(sent(harness.folder)) == 1)
            assert harness.app.store.message(1).text == (
                "[audio file] Turn off the lamp.\n(the user's own words with it: who is this, should I reply?)"
            )
        finally:
            await stop(harness)


async def test_the_signal_profile_says_her_name_ai_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        await until(lambda: any(r["method"] == "updateProfile" for r in requests(harness.folder)))
        [update] = [r for r in requests(harness.folder) if r["method"] == "updateProfile"]
        assert update["params"] == {"givenName": "Estia (AI)", "familyName": ""}
        await until(lambda: harness.app.store.get_state("signal_profile_name") == "Estia (AI)")
    finally:
        await stop(harness)


async def test_a_new_name_renames_the_signal_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def named(store: Store) -> None:
        store.set_state("signal_profile_name", "Estia (AI)")

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=named, name="Juno")
    try:
        await until(lambda: harness.app.store.get_state("signal_profile_name") == "Juno (AI)")
        [update] = [r for r in requests(harness.folder) if r["method"] == "updateProfile"]
        assert update["params"] == {"givenName": "Juno (AI)", "familyName": ""}
    finally:
        await stop(harness)


async def test_the_signal_profile_is_not_sent_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def named(store: Store) -> None:
        store.set_state("signal_profile_name", "Tiffany (AI)")

    # the owner's install: named Tiffany before the name was a setting; with name = "Tiffany" nothing changes
    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=named, name="Tiffany")
    try:
        deliver(harness.folder, "/help", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert not any(r["method"] == "updateProfile" for r in requests(harness.folder))
    finally:
        await stop(harness)


def avatar_updates(folder: Path) -> list[dict[str, Any]]:
    """The profile-picture updates the bridge sent (the name goes in its own updateProfile)."""
    return [r["params"] for r in requests(folder) if r["method"] == "updateProfile" and "avatar" in r["params"]]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def test_the_signal_profile_picture_is_sent_once_with_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picture = tmp_path / "estia.png"
    picture.write_bytes(png(8, 8))
    harness = await start(tmp_path, monkeypatch, FakeGemma(), avatar=picture)
    try:
        await until(lambda: harness.app.store.get_state("signal_profile_avatar") == sha256(picture))
        assert avatar_updates(harness.folder) == [{"avatar": str(picture)}]
        names = [r["params"] for r in requests(harness.folder) if r["method"] == "updateProfile"]
        assert {"givenName": "Estia (AI)", "familyName": ""} in names  # the name as before, in its own update
    finally:
        await stop(harness)
    harness = await start(tmp_path, monkeypatch, FakeGemma(), avatar=picture)  # a restart: the same picture
    try:
        deliver(harness.folder, "/help", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert avatar_updates(harness.folder) == [{"avatar": str(picture)}]  # still only the first start's
    finally:
        await stop(harness)


async def test_a_changed_picture_is_sent_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    picture = tmp_path / "estia.png"
    picture.write_bytes(png(8, 8))
    old = sha256(picture)
    picture.write_bytes(png(16, 16))

    def sent_before(store: Store) -> None:
        store.set_state("signal_profile_avatar", old)

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=sent_before, avatar=picture)
    try:
        await until(lambda: harness.app.store.get_state("signal_profile_avatar") == sha256(picture))
        assert avatar_updates(harness.folder) == [{"avatar": str(picture)}]
    finally:
        await stop(harness)


async def test_no_picture_sends_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """avatar = "" in the config (or no repo picture): nothing is sent, and a picture set before stays."""
    harness = await start(tmp_path, monkeypatch, FakeGemma(), avatar=None)
    try:
        deliver(harness.folder, "/help", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert avatar_updates(harness.folder) == []
        assert harness.app.store.get_state("signal_profile_avatar") is None
    finally:
        await stop(harness)


async def test_a_picture_signal_refused_is_tried_at_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    picture = tmp_path / "private-folder" / "estia.png"
    picture.parent.mkdir()
    picture.write_bytes(png(8, 8))
    monkeypatch.setenv("FAKE_SIGNAL_AVATAR_ERROR", "1")
    harness = await start(tmp_path, monkeypatch, FakeGemma(), avatar=picture)
    try:
        await until(lambda: "Signal profile picture not set" in caplog.text)
        assert harness.app.store.get_state("signal_profile_avatar") is None
        assert "private-folder" not in caplog.text  # metadata only
    finally:
        await stop(harness)
    monkeypatch.delenv("FAKE_SIGNAL_AVATAR_ERROR")
    harness = await start(tmp_path, monkeypatch, FakeGemma(), avatar=picture)
    try:
        await until(lambda: harness.app.store.get_state("signal_profile_avatar") == sha256(picture))
        assert avatar_updates(harness.folder)[-1] == {"avatar": str(picture)}
    finally:
        await stop(harness)


@pytest.mark.parametrize(("kind", "problem"), [("missing", "no such file"), ("text", "not a PNG or JPEG file")])
async def test_a_picture_that_cannot_be_used_is_skipped_and_she_runs_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, kind: str, problem: str
) -> None:
    """The file is checked at the start, not when the config is loaded: moved, deleted or replaced by something else,
    it never stops the always-on bridge. Nothing is sent, Signal keeps the picture it has, the log says why."""
    picture = tmp_path / "private-folder" / "her.png"
    picture.parent.mkdir()
    if kind == "text":
        picture.write_text("hello", encoding="utf-8")

    def sent_before(store: Store) -> None:
        store.set_state("signal_profile_avatar", "the earlier picture")

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=sent_before, avatar=picture)
    try:
        deliver(harness.folder, "/help", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert avatar_updates(harness.folder) == []
        assert harness.app.store.get_state("signal_profile_avatar") == "the earlier picture"
        assert f"Signal profile picture not set: {problem}" in caplog.text
        assert "private-folder" not in caplog.text  # metadata only
    finally:
        await stop(harness)


async def test_a_location_pin_is_where_the_user_is_and_maps_are_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(
        tmp_path,
        monkeypatch,
        FakeGemma(text("Got it.")),
        maps_enabled=True,
        maps_home="Home Street 1",
        web_keys_path=tmp_path / "web.env",
    )
    try:
        app = harness.app
        assert app.maps is not None
        assert {"places_search", "route"} <= {schema["function"]["name"] for schema in app.tools.schemas}
        assert "Places and routes:" in app.context.system_prompt(app.store.current_conversation())
        assert await app._status_lines() == ["Maps: key missing (scripts/set-web-keys.sh)"]
        deliver(harness.folder, "Dam Square\nhttps://maps.google.com/maps?q=52.37312%2C4.89201", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert app.maps.here() == (52.37312, 4.89201)
    finally:
        await stop(harness)


async def test_a_chosen_voice_is_used_after_a_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def chosen(store: Store) -> None:
        store.set_state("voice_name", "bm_george")

    with FakeVoiceService() as service:
        harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=chosen, voice_url=service.url)
        try:
            assert harness.app.voice is not None and harness.app.voice.voice == "bm_george"
        finally:
            await stop(harness)


async def test_commands_are_answered_during_a_call_but_new_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # live test 2026-10-10: /voice name am_puck sent during a call stayed unanswered until the call ended
    with FakeVoiceService() as service:
        harness = await start(tmp_path, monkeypatch, FakeGemma(), voice_url=service.url)
        try:
            app = harness.app
            app.call_started(ME)
            deliver(harness.folder, "/voice name am_puck", T0)
            await until(lambda: len(sent(harness.folder)) == 1)
            assert sent(harness.folder)[0].startswith("Voice: am_puck")
            assert app.voice is not None and app.voice.voice == "am_puck"
            deliver(harness.folder, "/new", T0 + 1)
            await asyncio.sleep(0.3)
            assert len(sent(harness.folder)) == 1  # /new waits for the call to end
            app.call_ended()
            await until(lambda: len(sent(harness.folder)) == 2)
        finally:
            await stop(harness)


async def test_a_cut_off_call_answer_ends_at_its_last_whole_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # live test 2026-10-10: a long answer hit the token limit and she stopped mid-sentence ("after they found their")
    gemma = FakeGemma(text("They formed in 1983. Their sound mixed funk and punk. After they found their"))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        turn = await harness.app.call_turn(ME, "tell me about the band", lambda: None)
        assert turn.text == "They formed in 1983. Their sound mixed funk and punk."
        assert harness.app.store.message(turn.reply_id or 0).text == turn.text  # stored as said
        assert app_module.CALL_MAX_TOKENS == 300
    finally:
        await stop(harness)


async def test_a_fresh_install_says_welcome_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing in the store yet: one welcome written by code, kept as a sent note (out of Gemma's history), once."""
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi Alex!")), welcome=True)
    try:
        await until(lambda: sent(harness.folder) == [WELCOME])
        [request] = [r for r in requests(harness.folder) if r["method"] == "send"]
        assert request["params"]["recipient"] == [ME]
        await until(lambda: harness.app.store.get_state(WELCOME_KEY) is not None)  # stored once signal-cli answered
        note = harness.app.store.message(1)
        assert (note.direction, note.kind, note.status, note.peer_aci) == ("out", "note", "sent", ME)
        assert harness.app.store.get_state(WELCOME_KEY)
        deliver(harness.folder, "hello", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        seen = json.dumps(harness.gemma.calls[0]["messages"])
        assert prompts.WELCOME_HELP not in seen and "Hi, I'm Estia" not in seen
    finally:
        await stop(harness)
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), welcome=True)
    try:
        deliver(restarted.folder, "/help", T0 + 1)
        await until(lambda: sent(restarted.folder)[-1] == on_phone(prompts.help_text(restarted.app.capability_lines())))
        assert sent(restarted.folder).count(WELCOME) == 1
    finally:
        await stop(restarted)


async def test_the_welcome_flag_alone_means_no_second_welcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def welcomed(store: Store) -> None:
        store.set_state(WELCOME_KEY, "sent")

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=welcomed, welcome=True)
    try:
        deliver(harness.folder, "/help", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == [on_phone(prompts.help_text(harness.app.capability_lines()))]
    finally:
        await stop(harness)


async def test_the_language_changes_only_by_a_typed_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hallo!")))
    try:
        app = harness.app
        rule = "Answer only in Nederlands (Dutch)"
        assert app.language == "en" and rule not in app.context.system_prompt(app.store.current_conversation())
        deliver(harness.folder, "/language nl", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder)[0].startswith("Language: Nederlands (nl).")
        deliver(harness.folder, "hello", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        system = str(harness.gemma.calls[0]["messages"][0]["content"])
        assert rule in system and "Reply in the language the user writes in" not in system
        assert app.language == "nl" and app.store.get_state("language") == "nl"
    finally:
        await stop(harness)


@pytest.mark.parametrize(("stored", "language"), [("de", "de"), ("xx", "nl"), (None, "nl")])
async def test_the_language_chosen_by_command_wins_over_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored: str | None, language: str
) -> None:
    def chosen(store: Store) -> None:
        if stored is not None:
            store.set_state("language", stored)

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=chosen, language="nl")
    try:
        app = harness.app
        assert app.language == language
        name = {"de": "Deutsch (German)", "nl": "Nederlands (Dutch)"}[language]
        assert f"Answer only in {name}" in app.context.system_prompt(app.store.current_conversation())
        deliver(harness.folder, "/language", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert f"({language}) ← now" in sent(harness.folder)[0]
    finally:
        await stop(harness)


async def test_an_existing_install_never_gets_the_welcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An update reaches installs that already chat: any message in the store means no welcome, ever."""

    def chatted(store: Store) -> None:
        out_id = store.add_outgoing(store.current_conversation().id, ME, "text", "An answer from before.", None)
        store.mark_sent(out_id, T0 - 60_000)

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=chatted, welcome=True)
    try:
        deliver(harness.folder, "/help", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == [on_phone(prompts.help_text(harness.app.capability_lines()))]
        assert harness.app.store.get_state(WELCOME_KEY) is None
    finally:
        await stop(harness)


async def test_a_language_switch_waits_for_the_call_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **call_settings(tmp_path))
    try:
        app = harness.app
        app.call_started(ME)
        deliver(harness.folder, "/language fr", T0)
        await asyncio.sleep(0.3)
        assert sent(harness.folder) == [] and app.language == "en"  # never in the middle of a call
        app.call_ended()
        await until(lambda: len(sent(harness.folder)) == 1)
        assert app.language == "fr"
    finally:
        await stop(harness)


async def test_a_welcome_that_could_not_be_sent_is_tried_at_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-1")
    harness = await start(tmp_path, monkeypatch, FakeGemma(), welcome=True)
    try:
        await until(lambda: harness.app.store.get_state(ALIVE_KEY) is not None)  # the start is done
        assert sent(harness.folder) == [WELCOME] * 3  # three tries, like any message
        assert harness.app.store.get_state(WELCOME_KEY) is None
        assert harness.app.store.count_messages() == 0  # nothing left for recovery to send
    finally:
        await stop(harness)
    monkeypatch.delenv("FAKE_SIGNAL_SEND_ERROR")
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), welcome=True)
    try:
        await until(lambda: restarted.app.store.get_state(WELCOME_KEY) is not None)
        assert sent(restarted.folder) == [WELCOME] * 4
        assert restarted.app.store.message(1).status == "sent"
    finally:
        await stop(restarted)


PARTNER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"  # a second allowed account (a patterned fake)


def welcomed_accounts(folder: Path) -> list[str]:
    """Each try to send the welcome, by recipient, failed ones included."""
    tries = [r["params"] for r in requests(folder) if r["method"] == "send"]
    return [aci for params in tries if params.get("message") == WELCOME for aci in params["recipient"]]


async def test_a_welcome_one_account_missed_is_tried_again_at_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two allowed accounts: the one the welcome did not reach gets it at the next start, even though the other one
    got it and chatted meanwhile. Nobody gets it twice."""
    both = frozenset({ME, PARTNER})
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-1")
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR_FOR", PARTNER)
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi Alex!")), welcome=True, allowed_acis=both)
    try:
        await until(lambda: harness.app.store.get_state(ALIVE_KEY) is not None)  # the start is done
        deliver(harness.folder, "hello", T0)
        await until(lambda: "Hi Alex!" in sent(harness.folder))
        assert sorted(welcomed_accounts(harness.folder)) == sorted([ME, PARTNER, PARTNER, PARTNER])
        assert harness.app.store.get_state(WELCOME_KEY) is None
    finally:
        await stop(harness)
    monkeypatch.delenv("FAKE_SIGNAL_SEND_ERROR")
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), welcome=True, allowed_acis=both)
    try:
        await until(lambda: restarted.app.store.get_state(WELCOME_KEY) is not None)
        assert welcomed_accounts(restarted.folder).count(ME) == 1
        assert welcomed_accounts(restarted.folder)[-1] == PARTNER
        assert welcomed_accounts(restarted.folder).count(PARTNER) == 4  # three failed tries, then delivered
    finally:
        await stop(restarted)


async def test_an_account_allowed_later_gets_no_welcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The welcome is a fresh install's: it is owed to the accounts allowed at the first start only."""
    harness = await start(tmp_path, monkeypatch, FakeGemma(), welcome=True)
    try:
        await until(lambda: harness.app.store.get_state(WELCOME_KEY) is not None)
    finally:
        await stop(harness)
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), welcome=True, allowed_acis=frozenset({ME, PARTNER}))
    try:
        deliver(restarted.folder, "/help", T0)
        await until(lambda: sent(restarted.folder)[-1] == on_phone(prompts.help_text(restarted.app.capability_lines())))
        assert welcomed_accounts(restarted.folder) == [ME]
    finally:
        await stop(restarted)


async def test_an_account_no_longer_allowed_is_not_owed_the_welcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-1")
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR_FOR", PARTNER)
    harness = await start(tmp_path, monkeypatch, FakeGemma(), welcome=True, allowed_acis=frozenset({ME, PARTNER}))
    try:
        await until(lambda: harness.app.store.get_state(ALIVE_KEY) is not None)
        assert harness.app.store.get_state(WELCOME_KEY) is None
    finally:
        await stop(harness)
    monkeypatch.delenv("FAKE_SIGNAL_SEND_ERROR")
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), welcome=True)
    try:
        await until(lambda: restarted.app.store.get_state(WELCOME_KEY) is not None)  # done: nobody is owed it now
        assert welcomed_accounts(restarted.folder).count(PARTNER) == 3
    finally:
        await stop(restarted)


def test_the_welcome_names_only_the_parts_that_are_set_up() -> None:
    plain = prompts.welcome(name=NAME)
    assert plain.startswith("Hi, I'm Estia") and plain.endswith("/help lists what I can do here and the commands.")
    assert "Everything stays on this Mac." in plain and "Only you get answers from me." in plain
    for part in ("Mail", "Calendar", "Voice notes", "Calls", "Lights and plugs", "Web search", "Places", "Music"):
        assert part not in plain
    some = prompts.welcome(name=NAME, mail=True, device="desk lamp", web=True)
    assert "Mail:" in some and '"turn on desk lamp"' in some and "Web search:" in some
    assert "Calendar" not in some and "Voice notes" not in some and "Music" not in some
    assert "except what web search needs to send" in some
    assert "I only read your mail, never change it, and only you get answers from me." in some
    every = prompts.welcome(
        name=NAME, mail=True, calendar=True, voice=True, calls=True, device="desk lamp", web=True, maps=True, music=True
    )
    tries = [line for line in every.splitlines() if line.startswith("• ")]
    assert len(tries) == 8 and all(":" in line for line in tries)
    assert "\n\n**Set up and ready to try**\n• Mail: " in every  # a bold heading, one part per line
    assert "except what web search, places and music need to send" in every
    assert "I only read your mail and calendar, never change them" in every
    assert "Reminder set" not in every  # the contract comes with reminders only
    with_reminders = prompts.welcome(name=NAME, reminders=True, wake_calls=True)
    [line] = [line for line in with_reminders.splitlines() if line.startswith("• Reminders:")]
    assert line.endswith('(a reminder is set only when my "Reminder set" note says so)')


@pytest.mark.parametrize(
    ("settings", "part"),
    [
        ({"mail_accounts": (MailAccount("family", "info@example.com"),)}, {"mail": True}),
        ({"calendar_enabled": True}, {"calendar": True}),
        ({"voice_url": "http://127.0.0.1:9"}, {"voice": True}),
        ({"calls_enabled": True}, {"calls": True, "wake_calls": True}),
        ({"tapo_devices": (DeviceSpec("desk lamp", "192.168.1.20", "light", "normal"),)}, {"device": "desk lamp"}),
        ({"web_enabled": True}, {"web": True}),
        ({"maps_enabled": True}, {"maps": True}),
        ({"spotify_client_id": "0123456789abcdef0123456789abcdef"}, {"music": True}),
    ],
    ids=["mail", "calendar", "voice", "calls", "lights", "web", "maps", "music"],
)
def test_the_welcome_follows_the_config(tmp_path: Path, settings: dict[str, Any], part: dict[str, Any]) -> None:
    """Each part of the config turns on its own line in the welcome, and no other."""
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        web_keys_path=tmp_path / "web.env",
        **settings,
    )
    store = Store.open(cfg.db_path)
    try:
        app = App(cfg, store, SignalClient([sys.executable, str(FAKE)], cfg.allowed_acis), FakeGemma(), Fetcher())
        default: dict[str, Any] = {"reminders": True}  # her own messages are opt-in: no line for them
        assert app._welcome_text() == prompts.welcome(name=NAME, **default | part)
    finally:
        store.close()


def speech_requests(service: FakeVoiceService) -> list[tuple[str, dict[str, Any]]]:
    """(language, JSON) of each speech request."""
    audio = [(path, body) for _, path, _, body in service.requests if path.startswith("/v1/audio/")]
    return [
        (language, json.loads(body))
        for (path, body), language in zip(audio, service.request_languages, strict=True)
        if path == "/v1/audio/speech"
    ]


async def test_a_voice_note_is_heard_and_answered_in_the_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        service.languages = list(CODES)
        service.transcript = "Doe de lamp uit."
        harness = await start(
            tmp_path, monkeypatch, FakeGemma(text("De lamp is uit.")), voice_url=service.url, language="nl"
        )
        try:
            put(harness.folder, "v1.m4a", M4A)
            deliver(harness.folder, None, T0, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: len(voice_notes(harness.folder)) == 1)
            assert "/v1/audio/transcriptions?language=nl" in service.paths()
            assert ("nl", {"input": "De lamp is uit.", "voice": "af_heart", "speed": 1.05, "language": "nl"}) in (
                speech_requests(service)
            )
        finally:
            await stop(harness)


async def test_the_voice_warms_up_at_the_start_and_after_a_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        service.languages = list(CODES)
        harness = await start(tmp_path, monkeypatch, FakeGemma(), voice_url=service.url, language="nl")
        try:
            line = prompts.spoken("nl", name=NAME).now_speaking
            warm_up = ("nl", {"input": line, "voice": "af_heart", "speed": 1.05, "language": "nl"})
            await until(lambda: speech_requests(service) == [warm_up])
            deliver(harness.folder, "/language de", T0)
            await until(lambda: len(speech_requests(service)) == 2)
            assert speech_requests(service)[1][0] == "de" and harness.app.voice is not None
            assert harness.app.voice.language == "de"
            deliver(harness.folder, "/language en", T0 + 1)
            await until(lambda: len(sent(harness.folder)) == 2)
            await asyncio.sleep(0.2)
            assert len(speech_requests(service)) == 2  # English (Kokoro) needs no warm-up
        finally:
            await stop(harness)


async def test_no_warm_up_for_english_or_an_older_voice_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with FakeVoiceService() as service:  # no "languages" in its health: older than the language feature
        harness = await start(tmp_path, monkeypatch, FakeGemma(), voice_url=service.url, language="fr")
        try:
            await until(lambda: "older than the language feature" in caplog.text)
            assert speech_requests(service) == []
        finally:
            await stop(harness)


async def test_a_switch_tells_when_the_voice_service_is_too_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:  # no "languages" in its health: older than the language feature
        harness = await start(tmp_path, monkeypatch, FakeGemma(), voice_url=service.url)
        try:
            deliver(harness.folder, "/language nl", T0)
            await until(lambda: len(sent(harness.folder)) == 1)
            assert "need a newer voice service: run scripts/install-voice-daemon.sh" in sent(harness.folder)[0]
            service.languages = list(CODES)
            deliver(harness.folder, "/language de", T0 + 1)
            await until(lambda: len(sent(harness.folder)) == 2)
            assert sent(harness.folder)[1].endswith("Code-written notes stay in English.")
        finally:
            await stop(harness)


async def test_a_spoken_language_command_is_not_a_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with FakeVoiceService() as service:
        service.transcript = "/language nl"
        harness = await start(tmp_path, monkeypatch, FakeGemma(text("Type it, please.")), voice_url=service.url)
        try:
            put(harness.folder, "v1.m4a", M4A)
            deliver(harness.folder, None, T0, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: "Type it, please." in sent(harness.folder))
            assert harness.app.language == "en" and harness.app.store.get_state("language") is None
        finally:
            await stop(harness)


async def test_her_built_in_style_is_the_configs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[personality] style reaches her rules' style block: hearth when left out, playful when chosen."""
    left_out = await start(tmp_path / "hearth", monkeypatch, FakeGemma())
    try:
        prompt = left_out.app.context.system_prompt(left_out.app.store.current_conversation())
        assert prompts.HEARTH_STYLE in prompt and prompt.count("STYLE (built in") == 1
    finally:
        await stop(left_out)
    chosen = await start(tmp_path / "playful", monkeypatch, FakeGemma(), personality_style="playful")
    try:
        prompt = chosen.app.context.system_prompt(chosen.app.store.current_conversation())
        assert prompts.PLAYFUL_STYLE in prompt and prompts.HEARTH_STYLE not in prompt
    finally:
        await stop(chosen)


async def test_the_calm_on_worry_line_follows_the_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[personality] calm_on_worry: code's line after a usable soul.md, on when left out, off on the user's word."""
    soul = tmp_path / "soul.md"
    soul.write_text("Vibe: a pun in every answer.\n", encoding="utf-8")
    soul.chmod(0o600)
    left_out = await start(tmp_path / "on", monkeypatch, FakeGemma(), soul_path=soul)
    try:
        assert left_out.app.context.style().endswith("Vibe: a pun in every answer.\n\n" + prompts.SOUL_CALM)
    finally:
        await stop(left_out)
    off = await start(tmp_path / "off", monkeypatch, FakeGemma(), soul_path=soul, personality_calm_on_worry=False)
    try:
        assert off.app.context.style().endswith("Vibe: a pun in every answer.")
    finally:
        await stop(off)


async def test_the_owners_tiffany_is_tiffany_everywhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """name = "Tiffany" in the config: her rules, welcome, Signal profile, notes and calls (in Dutch too) say so."""
    gemma = FakeGemma(GemmaUnavailableError("gone"))
    settings = call_settings(tmp_path)
    harness = await start(tmp_path, monkeypatch, gemma, welcome=True, name="Tiffany", language="nl", **settings)
    try:
        app = harness.app
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder)[0].startswith("Hi, I'm Tiffany, your private thinking partner")
        assert app.context.system_prompt(app.store.current_conversation()).startswith("You are Tiffany, a private")
        [update] = [r for r in requests(harness.folder) if r["method"] == "updateProfile"]
        assert update["params"]["givenName"] == "Tiffany (AI)"
        deliver(harness.folder, "hello", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder)[1] == "Tiffany isn't available right now, try again in a bit."
        assert app.calls is not None and (app.calls.name, app.calls.language) == ("Tiffany", "nl")
        assert app.tools._name == app.context.name == "Tiffany"
    finally:
        await stop(harness)


async def test_a_call_follows_the_language(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(GemmaUnavailableError("gone"))
    harness = await start(tmp_path, monkeypatch, gemma, language="nl", **call_settings(tmp_path))
    try:
        app = harness.app
        assert app.calls is not None and app.calls.language == "nl"
        turn = await app.call_turn(ME, "hoe laat is het?", lambda: None)
        assert turn.text == prompts.spoken("nl", name=NAME).again  # said in Dutch, like the call's other lines
        deliver(harness.folder, "/language pl", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert app.calls.language == "pl" and app._call_voice is not None and app._call_voice.language == "pl"
    finally:
        await stop(harness)


# reminders and wake-up calls in the bridge


class MovableClock:
    """The bridge's clock, moved on by a test (reminders fire by it; asyncio still runs in real time)."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


async def test_a_reminder_from_the_command_fires_as_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = MovableClock()
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock)
    try:
        deliver(harness.folder, "/remind 20m take the pizza out", T0)
        await until(lambda: any(text.startswith("Reminder set (#1):") for text in sent(harness.folder)))
        clock.offset = 20 * 60
        assert harness.app.reminder_scheduler is not None
        harness.app.reminder_scheduler.notify()
        await until(lambda: "Reminder: take the pizza out" in sent(harness.folder))
        assert harness.gemma.calls == []  # code only
        notes = [m for m in harness.app.store.window_messages(harness.app.store.current_conversation().id, 0)]
        assert all("Reminder" not in m.text for m in notes)  # never in Gemma's history
    finally:
        await stop(harness)


async def test_gemma_sets_a_reminder_and_code_confirms_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    when = datetime.fromtimestamp(time.time() + 3 * 3600, TZ_APP).strftime("%Y-%m-%d %H:%M")
    gemma = FakeGemma(tool("set_reminder", at=when, text="call mum"), text("Sure, I'll remind you."))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        assert "set_reminder" in [schema["function"]["name"] for schema in harness.app.tools.schemas]
        deliver(harness.folder, "remind me in three hours to call mum", T0)
        await until(lambda: len(sent(harness.folder)) == 2)
        answer, note = sent(harness.folder)
        assert answer == "Sure, I'll remind you." and note.startswith("Reminder set (#1):") and '"call mum"' in note
    finally:
        await stop(harness)


async def test_a_claimed_reminder_that_was_not_set_gets_codes_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Done, I'll remind you at five.")))
    try:
        deliver(harness.folder, "remind me at five", T0)
        await until(lambda: sent(harness.folder) == ["Done, I'll remind you at five.", prompts.REMINDER_GUARD])
    finally:
        await stop(harness)


@pytest.mark.parametrize(
    ("answer", "kept"),
    [
        ("I've set a reminder for 17:00 to let you know about it.", ""),
        (
            "Your invoice from Acme is 120 EUR. I've noted the reminder for 17:00.",
            "Your invoice from Acme is 120 EUR.\n",
        ),
        ("Er staat een factuur van Acme in. Ik heb de herinnering ingesteld.", "Er staat een factuur van Acme in.\n"),
    ],
)
async def test_a_refused_reminder_leaves_her_other_words_and_codes_line_in_any_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, kept: str
) -> None:
    """In English as in any language: her sentences about the reminder never reach the user or the history; her
    others do, with code's line why after them, in the one message (no second note)."""
    gemma = FakeGemma(tool("set_reminder", at="2030-01-01 07:00", text="invoice"), text(answer))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        deliver(harness.folder, "remind me of the invoice", T0)
        await until(lambda: len(sent(harness.folder)) >= 1)
        await asyncio.sleep(0.2)
        line = "No reminder was set: that is more than 366 days ahead."
        assert sent(harness.folder) == [f"{kept}{line}"]
        assert harness.app.store.message(2).text == f"{kept}{line}"
    finally:
        await stop(harness)


async def test_in_another_language_a_refused_reminder_gets_only_codes_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dutch Gemma may say "Ik heb de herinnering ingesteld" after set_reminder refused: the user gets code's line
    instead, and the history keeps that, never the claim (as for a device question)."""
    claim = "Ik heb de herinnering om 17:00 voor je ingesteld."
    gemma = FakeGemma(tool("set_reminder", at="2030-01-01 07:00", text="mama bellen"), text(claim))
    harness = await start(tmp_path, monkeypatch, gemma, language="nl")
    try:
        deliver(harness.folder, "herinner me aan mama", T0)
        await until(lambda: len(sent(harness.folder)) >= 1)
        await asyncio.sleep(0.2)
        assert sent(harness.folder) == ["No reminder was set: that is more than 366 days ahead."]
        assert harness.app.store.message(2).text == "No reminder was set: that is more than 366 days ahead."
    finally:
        await stop(harness)


async def test_on_a_call_in_another_language_a_refused_reminder_is_said_in_english_by_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(tool("set_reminder", at="2030-01-01 07:00", text="koken"), text("Ik bel je om vijf uur!"))
    harness = await start(tmp_path, monkeypatch, gemma, language="nl", **call_settings(tmp_path))
    try:
        app = harness.app
        app.call_started(ME)
        turn = await app.call_turn(ME, "bel me om vijf uur", lambda: None)
        line = "No reminder was set: that is more than 366 days ahead."
        assert (turn.text, turn.code_text) == ("", line)  # said in English, Gemma's words left out
        assert turn.reply_id is not None and app.store.message(turn.reply_id).text == line  # and kept
        assert sent(harness.folder) == []  # no second note
    finally:
        await stop(harness)


@pytest.mark.parametrize("language", ["en", "nl"])
async def test_on_a_call_her_other_words_are_said_and_codes_line_after_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    answers = {"en": "It's sunny today. I'll call you at five!", "nl": "Het is zonnig vandaag. Ik bel je om vijf uur!"}
    gemma = FakeGemma(tool("set_reminder", at="2030-01-01 07:00", text="koken"), text(answers[language]))
    harness = await start(tmp_path, monkeypatch, gemma, language=language, **call_settings(tmp_path))
    try:
        app = harness.app
        app.call_started(ME)
        turn = await app.call_turn(ME, "call me at five", lambda: None)
        hers = answers[language].split(". ")[0] + "."
        line = "No reminder was set: that is more than 366 days ahead."
        assert (turn.text, turn.code_text) == (hers, line)  # hers in her voice, code's line in English
        assert turn.reply_id is not None and app.store.message(turn.reply_id).text == f"{hers} {line}"
    finally:
        await stop(harness)


async def test_on_a_call_codes_line_has_the_spoken_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(tool("set_reminder", at="17:00", text="x" * 201), text("Ik bel je om vijf uur!"))
    harness = await start(tmp_path, monkeypatch, gemma, language="nl", **call_settings(tmp_path))
    try:
        app = harness.app
        app.call_started(ME)
        turn = await app.call_turn(ME, "bel me om vijf uur", lambda: None)
        line = "No reminder was set: the text is too long."  # said and kept; the text form names 200 characters
        assert (turn.text, turn.code_text) == ("", line)
        assert turn.reply_id is not None and app.store.message(turn.reply_id).text == line
    finally:
        await stop(harness)


async def test_a_reminder_set_on_a_call_is_said_in_codes_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    when = datetime.fromtimestamp(time.time() + 3 * 3600, TZ_APP).strftime("%Y-%m-%d %H:%M")
    gemma = FakeGemma(tool("set_reminder", at=when, text="cook"), text("Sure."))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        turn = await harness.app.call_turn(ME, "remind me to cook", lambda: None)
        assert turn.text == "Sure." and turn.code_text.startswith("Reminder set for ")
        await until(lambda: any(text.startswith("Reminder set (#1):") for text in sent(harness.folder)))
    finally:
        await stop(harness)


async def test_a_wake_up_call_without_the_voice_service_is_a_text_that_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = MovableClock()
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock, **call_settings(tmp_path))
    try:
        deliver(harness.folder, "/remind call 1m wake up", T0)
        await until(lambda: any(text.startswith("Reminder set (#1):") for text in sent(harness.folder)))
        clock.offset = 60
        assert harness.app.reminder_scheduler is not None
        harness.app.reminder_scheduler.notify()
        await until(lambda: any("No call: the voice service is not ready." in text for text in sent(harness.folder)))
        assert not any(r["method"] == "startCall" for r in requests(harness.folder))
    finally:
        await stop(harness)


async def test_after_a_restart_a_note_that_may_have_gone_out_is_not_sent_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def half_sent(store: Store) -> None:
        due = time.time() - 5 * 60
        local = datetime.fromtimestamp(due, TZ_APP).strftime("%Y-%m-%d %H:%M")
        rid = store.add_reminder(ME, "call mum", local, TZ_APP.key, due)
        note = store.add_outgoing(store.current_conversation().id, ME, "note", "Reminder: call mum", None)
        store.fire_reminder(rid, note)  # the bridge stopped before signal-cli answered
        store.set_state(ALIVE_KEY, f"{time.time() - 600:.0f}")

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=half_sent)
    try:
        await until(lambda: any(text.startswith("Late, I was offline") for text in sent(harness.folder)))
        await asyncio.sleep(0.3)
        [late] = sent(harness.folder)
        assert late.endswith("call mum (may be a repeat)")  # recovery never sent the old note again
    finally:
        await stop(harness)


async def test_status_shows_the_reminders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        deliver(harness.folder, "/remind 2h take the pills", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        deliver(harness.folder, "/status", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert re.search(r"^Reminders: 1 open · next .* · 0 failed$", sent(harness.folder)[1], re.MULTILINE)
    finally:
        await stop(harness)


async def test_projects_are_an_extra_offered_only_with_their_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off without [projects]: no project tool, no project instruction, one line on how the owner turns them on."""
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        app = harness.app
        names = [schema["function"]["name"] for schema in app.tools.schemas]
        assert not [name for name in names if name.startswith("project_")]
        rules = app.context.system_prompt(app.store.current_conversation())
        assert "project_list" not in rules and prompts.PROJECTS_OFF_RULE in rules
    finally:
        await stop(harness)
    root = tmp_path / "work"
    harness = await start(
        tmp_path / "on", monkeypatch, FakeGemma(), projects_enabled=True, projects_root=root, host_label="the Mini"
    )
    try:
        app = harness.app
        found = [s["function"] for s in app.tools.schemas if s["function"]["name"].startswith("project_")]
        assert [f["name"] for f in found] == ["project_list", "project_status", "project_read", "project_search"]
        assert f"on the Mini (folders in {root})" in found[0]["description"]
        rules = app.context.system_prompt(app.store.current_conversation())
        assert "project_list" in rules and prompts.PROJECTS_OFF_RULE not in rules
    finally:
        await stop(harness)


async def test_with_reminders_off_nothing_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi.")), reminders_enabled=False)
    try:
        app = harness.app
        assert app.reminders is None and app.reminder_scheduler is None
        rules = prompts.rules(  # every rules text names practice; the list has no reminders
            name=NAME, practice_last=prompts.PRACTICE_NEVER, capabilities=app.capability_lines()
        )
        assert app.context.system_prompt(app.store.current_conversation()).startswith(rules)
        assert not any("Reminders" in line for line in app.capability_lines())
        deliver(harness.folder, "hello", T0)
        await until(lambda: sent(harness.folder) == ["Hi."])
        assert harness.gemma.calls[0]["tools"] == TOOL_SCHEMAS
        deliver(harness.folder, "/remind 2h x", T0 + 1)
        await until(lambda: sent(harness.folder)[-1] == prompts.REMINDERS_OFF)
    finally:
        await stop(harness)


# messages she starts herself


def announced(store: Store) -> None:
    store.set_state("initiative_announced", "1")


async def test_a_message_she_starts_goes_out_with_its_footer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The app's send adds the footer; her part is kept as her text, the footer apart."""
    now = time.time()

    def planned(store: Store) -> None:
        announced(store)
        source = store.add_incoming(store.current_conversation().id, ME, int((now - 3 * 86_400) * 1000), "text", "x")
        assert source is not None
        store.set_status(source, "answered")
        store.add_followup(ME, "call the dentist", source, now - 60, now + 86_400, at="2026-10-12T10:00:00+00:00")

    gemma = FakeGemma(text(json.dumps({"text": "Did you get hold of the dentist?"})))
    # the other kinds off: the Sunday look back, skipped after 21:00 and logged then, and an improvised note's random
    # slot follow the real clock (this test failed every Sunday evening from 21:00, 18 Oct)
    harness = await start(
        tmp_path,
        monkeypatch,
        gemma,
        prepare=planned,
        initiative_enabled=True,
        initiative_quiet_hours="",
        initiative_lookback=False,
        initiative_improvise=False,
    )
    try:
        assert harness.app.initiative is not None
        await harness.app.initiative.tick()
        footer = prompts.INITIATIVE_FOOTERS["followup"]
        await until(lambda: sent(harness.folder) == [f"Did you get hold of the dentist?\n{footer}"])
        start_at = len("Did you get hold of the dentist?\n")
        assert styles(harness.folder) == [[f"{start_at}:{len(footer)}:ITALIC"]]  # the footer in italics
        [entry] = harness.app.store.initiative_entries()
        message = harness.app.store.message(entry.message_id or 0)
        assert (message.kind, message.text, message.footer, message.status) == (
            "text",
            "Did you get hold of the dentist?",
            footer,
            "sent",
        )
    finally:
        await stop(harness)


def chatted(store: Store) -> None:
    out_id = store.add_outgoing(store.current_conversation().id, ME, "text", "An answer from before.", None)
    store.mark_sent(out_id, T0 - 60_000)


async def test_an_install_from_before_that_turns_them_on_in_the_config_gets_the_announcement_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(
        tmp_path, monkeypatch, FakeGemma(), prepare=chatted, initiative_enabled=True, initiative_quiet_hours=""
    )
    try:
        assert harness.app.initiative is not None
        await harness.app.initiative.tick()
        line = harness.app.initiative.settings.line()  # no quiet hours here: the test may run at night
        expected = prompts.announcement(reminders=True, wake_calls=False, initiative=line)
        await until(lambda: sent(harness.folder) == [expected])
        await harness.app.initiative.tick()
        await asyncio.sleep(0.2)
        assert sent(harness.folder) == [expected]
    finally:
        await stop(harness)


async def test_a_fresh_installs_welcome_tells_and_no_announcement_follows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(
        tmp_path, monkeypatch, FakeGemma(), welcome=True, initiative_enabled=True, initiative_quiet_hours=""
    )
    try:
        welcome = on_phone(harness.app._welcome_text())
        await until(lambda: sent(harness.folder) == [welcome])
        assert "Reminders:" in welcome and "/quiet pauses that." in welcome
        assert harness.app.initiative is not None
        await harness.app.initiative.tick()
        await asyncio.sleep(0.2)
        assert sent(harness.folder) == [welcome]
    finally:
        await stop(harness)


async def test_a_message_she_was_sending_at_a_crash_is_never_sent_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def half_sent(store: Store) -> None:
        announced(store)
        out = store.add_outgoing(store.current_conversation().id, ME, "text", "A small thought.", None, footer="(f)")
        store.log_initiative("improvise", "2026-10-12", "sending", message_id=out, at="2026-10-12T10:00:00+00:00")

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=half_sent)
    try:
        await until(lambda: harness.app.store.get_state(ALIVE_KEY) is not None)  # the start is done
        await asyncio.sleep(0.2)
        assert sent(harness.folder) == []
        [entry] = harness.app.store.initiative_entries()
        assert entry.outcome == "unknown" and harness.app.store.message(entry.message_id or 0).status == "ignored"
    finally:
        await stop(harness)


async def test_quiet_and_status_through_signal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=announced, initiative_enabled=True)
    try:
        deliver(harness.folder, "/quiet", T0)
        await until(lambda: sent(harness.folder) == [prompts.QUIET_ON])
        deliver(harness.folder, "/status", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert re.search(r"^Initiative: paused \(/quiet\) · last 4 weeks: 0 sent", sent(harness.folder)[1], re.M)
        assert harness.app.profiler._after_learning is harness.app.followups is not None
    finally:
        await stop(harness)


async def test_off_by_default_nothing_changes_until_the_user_types_initiative_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi.")), welcome=True, initiative_quiet_hours="")
    try:
        app = harness.app
        assert app.initiative is not None and not app.initiative.enabled
        plain = on_phone(prompts.welcome(name=NAME, reminders=True))
        await until(lambda: sent(harness.folder) == [plain])
        assert "/quiet" not in plain
        deliver(harness.folder, "I'll call the dentist tomorrow", T0)
        await until(lambda: sent(harness.folder)[-1] == "Hi.")
        assert app.followups is not None and not app.followups.has_work()  # no idle pass for it while off
        deliver(harness.folder, "/quiet", T0 + 1)
        await until(lambda: sent(harness.folder)[-1] == prompts.QUIET_DISABLED)
        deliver(harness.folder, "/status", T0 + 2)
        await until(lambda: len(sent(harness.folder)) == 4)
        assert prompts.INITIATIVE_OFF in sent(harness.folder)[3].splitlines()
        await app.initiative.tick()
        deliver(harness.folder, "/initiative on", T0 + 3)
        on = prompts.INITIATIVE_ON.format(line=app.initiative.settings.line())
        await until(lambda: sent(harness.folder)[-1] == on)
        assert app.initiative.enabled and app.store.get_state("initiative_announced") is not None
        await app.initiative.tick()
        await asyncio.sleep(0.2)
        assert len(sent(harness.folder)) == 5  # no announcement after it: the reply told
    finally:
        await stop(harness)


async def test_an_install_from_before_hears_nothing_new_while_they_are_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=chatted, initiative_quiet_hours="")
    try:
        assert harness.app.initiative is not None
        await harness.app.initiative.tick()
        await asyncio.sleep(0.2)
        assert sent(harness.folder) == [] and harness.app.store.get_state("initiative_announced") is None
    finally:
        await stop(harness)


# hardening: logs bounded in-process (no newsyslog), and the alive mark the deploy check reads


def test_the_alive_mark_keeps_its_name_and_its_minute(tmp_path: Path) -> None:
    """estia-deploy's health check reads state.last_alive (read-only) after a deploy: the key and its timing stay."""
    assert ALIVE_KEY == "last_alive" and app_module.ALIVE_SECONDS == 60


LOGGING_SCRIPT = """
import logging, sys
from pathlib import Path
from local_assistant.app import setup_logging
setup_logging(Path(sys.argv[1]), foreground=False, stream_cap=int(sys.argv[2]), bridge_cap=int(sys.argv[3]))
log = logging.getLogger("local_assistant")
for number in range(int(sys.argv[4])):
    log.info("line %d %s", number, "x" * 100)
sys.stderr.write("y" * 300 + "\\n")
print("z" * 300)
sys.stderr.flush()
sys.stdout.flush()
"""


def run_logging(tmp_path: Path, *, stream_cap: int, bridge_cap: int, lines: int) -> Path:
    logs = tmp_path / "logs"
    launchd = tmp_path / "launchd.log"
    if not launchd.exists():
        launchd.write_text("old\n" * 2_000)  # 8 KB from before, over the cap
    with launchd.open("a") as out:  # launchd opens StandardOutPath and StandardErrorPath for appending
        subprocess.run(
            [sys.executable, "-c", LOGGING_SCRIPT, str(logs), str(stream_cap), str(bridge_cap), str(lines)],
            stdout=out,
            stderr=out,
            check=True,
        )
    return logs


def test_bridge_log_rotates_in_process_and_stays_private(tmp_path: Path) -> None:
    logs = run_logging(tmp_path, stream_cap=10_000, bridge_cap=5_000, lines=300)
    names = sorted(path.name for path in logs.iterdir())
    assert names == ["bridge.log", *(f"bridge.log.{n}" for n in range(1, 6))]  # 5 old files at most
    assert all((logs / name).stat().st_size <= 5_200 for name in names)
    assert all(stat.S_IMODE((logs / name).stat().st_mode) == 0o600 for name in names)
    assert stat.S_IMODE(logs.stat().st_mode) == 0o700


def test_launchds_log_of_the_bridge_is_capped_at_the_start_and_on_write(tmp_path: Path) -> None:
    run_logging(tmp_path, stream_cap=1_000, bridge_cap=1_000_000, lines=1)
    launchd = tmp_path / "launchd.log"
    text = launchd.read_text()
    assert "old" not in text  # over the cap at the start: emptied
    assert launchd.stat().st_size <= 1_000 + 301  # each write empties it first when it is over the cap
    assert text.endswith("z" * 300 + "\n")


def test_the_real_caps(tmp_path: Path) -> None:
    assert app_module.BRIDGE_LOG_BYTES == 10_000_000 and app_module.BRIDGE_LOG_FILES == 5
    assert app_module.STREAM_LOG_BYTES == 10_000_000


# hardening: devices (held until allowed), the lock, the safety number, her account's devices, the model pin, retention


def held_note(device: int) -> str:
    return prompts.DEVICE_HELD.format(device=device)


async def test_a_message_from_a_new_linked_device_is_held_until_the_phone_allows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hello from Gemma.")))
    try:
        deliver(harness.folder, "hi from a new laptop", T0, device=3)
        await until(lambda: sent(harness.folder) == [held_note(3)])  # a code note to the account: the phone sees it
        deliver(harness.folder, "again", T0 + 1, device=3)
        await asyncio.sleep(0.3)
        assert sent(harness.folder) == [held_note(3)]  # one note an hour per device
        assert harness.gemma.calls == [] and harness.app.store.count_messages() == 0  # not read, not stored
        assert not [r for r in requests(harness.folder) if r["method"] == "sendReceipt"]  # no read receipt either
        deliver(harness.folder, "/devices ok 3", T0 + 2, device=1)
        await until(lambda: sent(harness.folder)[-1:] == [prompts.DEVICES_OK.format(device=3)])
        deliver(harness.folder, "and now?", T0 + 3, device=3)
        await until(lambda: sent(harness.folder)[-1:] == ["Hello from Gemma."])
        stored = harness.app.store.message(harness.app.store.newest_message_id() - 1)
        assert (stored.text, stored.source_device) == ("and now?", 3)
        assert ME not in caplog.text
    finally:
        await stop(harness)


async def test_a_linked_device_silent_too_long_is_held_again_and_one_that_talks_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signal gives a new linked device the number of a removed one: trust in a number ends after TRUST_DAYS of
    silence; every message from a device she answers starts the days again."""
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hello from Gemma.")))
    try:
        store = harness.app.store
        old = datetime.fromtimestamp(time.time() - (TRUST_DAYS - 1) * 86_400, UTC).isoformat(timespec="seconds")
        store.set_device(ME, 2, "allowed")
        store.touch_device(ME, 2, old)
        store.set_device(ME, 3, "allowed")
        store.touch_device(ME, 3, "2020-01-01T00:00:00+00:00")
        deliver(harness.folder, "still me", T0, device=2)
        await until(lambda: sent(harness.folder) == ["Hello from Gemma."])
        seen = store.device(ME, 2)
        assert seen is not None and seen.last_seen is not None and seen.last_seen > old
        deliver(harness.folder, "a new laptop with an old number", T0 + 1, device=3)
        silent = prompts.DEVICE_HELD_SILENT.format(device=3, days=TRUST_DAYS)
        await until(lambda: sent(harness.folder)[-1:] == [silent])
        assert store.device_source(ME, 3) == "held" and len(harness.gemma.calls) == 1
    finally:
        await stop(harness)


async def test_a_message_naming_no_device_is_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        deliver(harness.folder, "who am I", T0, device=None)
        await until(lambda: sent(harness.folder) == [prompts.DEVICE_HELD_UNNAMED])
        assert harness.app.store.count_messages() == 0
    finally:
        await stop(harness)


async def test_a_held_photo_is_deleted_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        image = put(harness.folder, "held.png", png(10, 10))
        deliver(harness.folder, "look", T0, device=2, attachments=[photo("held.png", png(10, 10))])
        await until(lambda: sent(harness.folder) == [held_note(2)])
        assert not image.exists()
    finally:
        await stop(harness)


class StepClock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


async def test_lock_stops_everything_until_unlock_from_the_same_device_after_the_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = StepClock()
    harness = await start(
        tmp_path,
        monkeypatch,
        FakeGemma(text("Back again.")),
        clock=clock,
        prepare=lambda store: store.set_device(ME, 2, "allowed"),
    )
    try:
        deliver(harness.folder, "/lock", T0, device=2)
        locked = prompts.LOCKED.format(device=2, minutes=10)
        await until(lambda: sent(harness.folder) == [locked])
        deliver(harness.folder, "hello?", T0 + 1, device=1)
        deliver(harness.folder, "/unlock", T0 + 2, device=1)  # another device: no answer at all
        deliver(harness.folder, "/status", T0 + 3, device=2)  # nothing else works while locked
        await asyncio.sleep(0.4)
        assert sent(harness.folder) == [locked] and harness.gemma.calls == []
        assert harness.app.store.count_messages() == 0  # what came in while locked is not read or kept
        await harness.app._alert("a watcher's alert")  # notes wait too: nothing goes out
        assert sent(harness.folder) == [locked]
        deliver(harness.folder, "/unlock", T0 + 4, device=2)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder)[-1].startswith("Still locked: /unlock works from this device from ")
        clock.now += 600
        deliver(harness.folder, "/unlock", T0 + 5, device=2)
        await until(lambda: sent(harness.folder)[-1:] == [prompts.UNLOCKED])
        deliver(harness.folder, "hello", int(clock.now * 1000), device=1)
        await until(lambda: sent(harness.folder)[-1:] == ["Back again."])
    finally:
        await stop(harness)


async def test_lock_holds_back_an_answer_already_on_its_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pending: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    harness = await start(tmp_path, monkeypatch, FakeGemma(pending))
    try:
        deliver(harness.folder, "a long question", T0)
        await until(lambda: len(harness.gemma.calls) == 1)
        deliver(harness.folder, "/lock", T0 + 1, device=1)
        await until(lambda: len(sent(harness.folder)) == 1)
        pending.set_result(text("The long answer."))
        await asyncio.sleep(0.3)
        assert sent(harness.folder) == [prompts.LOCKED.format(device=1, minutes=10)]
        statuses = [harness.app.store.message(i).status for i in range(1, harness.app.store.newest_message_id() + 1)]
        assert "ignored" in statuses and "pending" not in statuses and "failed" not in statuses  # never resent
    finally:
        await stop(harness)


async def test_the_lock_survives_a_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        deliver(harness.folder, "/lock", T0, device=1)
        await until(lambda: len(sent(harness.folder)) == 1)
    finally:
        await stop(harness)
    again = await start(tmp_path, monkeypatch, FakeGemma(), **call_settings(tmp_path))
    try:
        assert again.app.guard.locked
        deliver(again.folder, "hello", T0 + 1, device=1)
        await asyncio.sleep(0.3)
        assert len(sent(again.folder)) == 1 and again.gemma.calls == []
        assert again.app.calls is not None and await again.app.calls.can_place(ME) == "locked"
    finally:
        await stop(again)


async def test_a_reminder_due_while_locked_comes_after_the_unlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = StepClock()

    def remind(store: Store) -> None:
        local = datetime.fromtimestamp(clock.now + 120, TZ_APP).strftime("%Y-%m-%d %H:%M")
        store.add_reminder(ME, "stretch", local, "Europe/Amsterdam", clock.now + 120)

    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock, prepare=remind, timezone=TZ_APP)
    try:
        deliver(harness.folder, "/lock", T0, device=1)
        await until(lambda: len(sent(harness.folder)) == 1)
        clock.now += 300
        assert harness.app.reminder_scheduler is not None
        await harness.app.reminder_scheduler.tick()
        assert len(sent(harness.folder)) == 1
        clock.now += 600
        deliver(harness.folder, "/unlock", T0 + 1, device=1)
        await until(lambda: "Reminder: stretch" in sent(harness.folder))
    finally:
        await stop(harness)


UNTRUSTED_LINE: dict[str, Any] = {
    "exception": {"message": f"Untrusted identity: {ME}", "type": "UntrustedIdentityException"},
    "envelope": {"source": ME, "sourceUuid": ME, "sourceDevice": 1, "timestamp": 1, "serverReceivedTimestamp": 2},
}


async def test_a_changed_safety_number_is_recorded_and_shown_until_a_message_opens_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("hi")))
    status_file = tmp_path / "data" / "status.json"
    try:
        with (harness.folder / "inbox.jsonl").open("a") as inbox:
            inbox.write(json.dumps(UNTRUSTED_LINE) + "\n")
        await until(lambda: "identity_changed" in status_file.read_text() if status_file.exists() else False)
        assert ME not in status_file.read_text() and ME not in caplog.text
        assert "safety number changed" in caplog.text
        info = await harness.app.status()
        assert any(line.startswith("Safety number: ✗ changed") for line in info.security)
        deliver(harness.folder, "it's me again", T0)
        await until(lambda: sent(harness.folder) == ["hi"])
        assert "identity_changed" not in status_file.read_text()
    finally:
        await stop(harness)


async def test_her_accounts_devices_are_counted_and_more_than_this_mac_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    devices = [{"id": 1, "createdTimestamp": 1}, {"id": 2, "name": "Example laptop", "createdTimestamp": 2}]
    monkeypatch.setenv("FAKE_SIGNAL_DEVICES", json.dumps(devices))
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    status_file = tmp_path / "data" / "status.json"
    try:
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder) == [prompts.LINKED_ALERT.format(count=2)]
        saved = json.loads(status_file.read_text())["security"]
        assert saved["linked_devices"] == 2 and "Example laptop" not in status_file.read_text()
        assert await harness.app.check_linked_devices() == 2
        assert len(sent(harness.folder)) == 1  # told once, not on every check
        info = await harness.app.status()
        assert any(line.startswith("Her Signal account: ✗ 2 devices") for line in info.security)
    finally:
        await stop(harness)


async def test_only_this_mac_on_her_account_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        await until(lambda: harness.app.part_status.security().linked_devices == 1)
        assert sent(harness.folder) == []
    finally:
        await stop(harness)


class Digest:
    def __init__(self, value: str | None) -> None:
        self.value = value

    async def __call__(self) -> str | None:
        return self.value


async def test_a_model_that_does_not_match_its_pin_pauses_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pinned = "ab" * 32
    digest = Digest("cd" * 32)
    clock = StepClock()
    pin = ModelPin(digest, pinned)
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Answered again.")), clock=clock, model_pin=pin)
    paused = prompts.MODEL_PAUSED.format(model="local-worker", host="the Mac")
    try:
        await until(lambda: sent(harness.folder) == [paused])  # told at once, as a note
        deliver(harness.folder, "hello", T0)
        await until(lambda: sent(harness.folder) == [paused, paused])  # code's note, never Gemma's answer
        assert harness.gemma.calls == []
        saved = json.loads((tmp_path / "data" / "status.json").read_text())["security"]
        assert "model_paused" in saved
        digest.value = pinned
        clock.now += 61
        deliver(harness.folder, "hello again", T0 + 1)
        resumed = prompts.MODEL_RESUMED.format(model="local-worker")
        await until(lambda: {"Answered again.", resumed} <= set(sent(harness.folder)))
        assert "model_paused" not in json.loads((tmp_path / "data" / "status.json").read_text()).get("security", {})
    finally:
        await stop(harness)


class Entry:
    """Ollama's tags entry for the alias, as the pin reads it."""

    def __init__(self, remote: bool) -> None:
        self.remote = remote

    async def __call__(self) -> ModelEntry | None:
        return ModelEntry("ab" * 32, remote=self.remote)


async def test_a_cloud_model_under_the_alias_pauses_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The model always runs on the Mac: an alias Ollama marks as a cloud model gets no message, without a pinned
    digest too; code says so in a note, and again when it runs on the Mac."""
    entry = Entry(remote=True)
    clock = StepClock()
    pin = ModelPin(entry, "")
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Answered again.")), clock=clock, model_pin=pin)
    paused = prompts.MODEL_CLOUD.format(model="local-worker", host="the Mac", name="Estia")
    try:
        await until(lambda: sent(harness.folder) == [paused])
        deliver(harness.folder, "hello", T0)
        await until(lambda: sent(harness.folder) == [paused, paused])
        assert harness.gemma.calls == []
        saved = json.loads((tmp_path / "data" / "status.json").read_text())["security"]
        assert "model_cloud" in saved and "model_paused" not in saved
        entry.remote = False
        clock.now += 61
        deliver(harness.folder, "hello again", T0 + 1)
        back = prompts.MODEL_CLOUD_RESUMED.format(model="local-worker", host="the Mac")
        await until(lambda: {"Answered again.", back} <= set(sent(harness.folder)))
        assert "model_cloud" not in json.loads((tmp_path / "data" / "status.json").read_text()).get("security", {})
    finally:
        await stop(harness)


def test_the_bridge_checks_the_model_without_a_pinned_digest_too() -> None:
    """The cloud check needs no model_digest: the bridge always reads the alias's entry."""
    cfg = Config(bot_number="+306900000000", allowed_acis=frozenset({ME}))
    gemma = OllamaGemma(cfg.ollama_base_url, cfg.model)
    pin = model_pin(cfg, gemma)
    assert pin.expected == ""
    pinned = model_pin(dataclasses.replace(cfg, model_digest="ab" * 32), gemma)
    assert pinned.expected == "ab" * 32


async def test_history_older_than_keep_days_is_pruned_once_a_night_when_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = StepClock()
    night = datetime.fromtimestamp(clock.now, TZ_APP).replace(hour=3, minute=30)
    clock.now = night.timestamp()

    def old_message(store: Store) -> None:
        conversation = store.current_conversation()
        old = store.add_incoming(conversation.id, ME, 5, "text", "from long ago")
        store._conn.execute("UPDATE messages SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (old,))

    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=clock, prepare=old_message, keep_days=30)
    try:
        assert await harness.app.retention_pass() is True
        texts = [row[0] for row in harness.app.store._conn.execute("SELECT text FROM messages")]
        assert texts and "from long ago" not in texts  # the offline note about it (sent now) stays
        assert harness.app.store.get_state("privacy:pruned_at") == f"{clock.now:.0f}"
        assert await harness.app.retention_pass() is False  # once a night
        clock.now += 8 * 3600  # 11:30: outside the window
        assert await harness.app.retention_pass() is False
    finally:
        await stop(harness)


async def test_without_keep_days_nothing_is_pruned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        assert await harness.app.retention_pass() is False
    finally:
        await stop(harness)


async def test_another_users_message_does_not_clear_a_changed_safety_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("hi")), allowed_acis=frozenset({ME, STRANGER}))
    try:
        harness.app.on_problem(ReceiveProblem("UntrustedIdentityException", ME, 1))
        deliver(harness.folder, "from the other account", T0, source=STRANGER)
        await until(lambda: sent(harness.folder) == ["hi"])
        assert harness.app.part_status.security().identity_changed is not None
    finally:
        await stop(harness)


# /babble: language practice in its own conversation, no tools, voice in the practised language, the recap


def recap_answer(*items: tuple[str, str, str]) -> Completion:
    return text(json.dumps({"items": [{"said": s, "better": b, "why": w} for s, b, w in items]}))


async def test_a_practice_text_is_answered_without_tools_in_its_own_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Wat leuk! Waar woon je?"))
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        folder, app = harness.folder, harness.app
        chat = app.store.current_conversation()
        deliver(folder, "/babble nl", T0)
        await until(lambda: len(sent(folder)) == 1)
        assert sent(folder)[0].startswith("Practice: Nederlands, Dutch (nl), level A2\nAnswers in Dutch")
        deliver(folder, "Ik woon in Utrecht.", T0 + 1)
        await until(lambda: len(sent(folder)) == 2)
        assert sent(folder)[1] == "Wat leuk! Waar woon je?"
        [call] = gemma.calls
        assert call["tools"] is None
        practice_rules = prompts.rules(name=NAME, practice=("nl", "A2"), practice_last="Last practised: Dutch, A2.")
        assert call["messages"][0]["content"].startswith(practice_rules)
        session = app.babble.running(ME)
        assert session is not None
        said = [m for m in app.store.user_texts(session.conversation_id)]
        assert [m.text for m in said] == ["Ik woon in Utrecht."]
        assert app.store.current_conversation().id == chat.id
        assert "Ik woon in Utrecht." not in [m.text for m in app.store.window_messages(chat.id, 0)]
    finally:
        await stop(harness)


async def test_babble_off_sends_the_recap_before_its_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(
        text("Leuk! Wat heb je gekocht?"),
        recap_answer(("een grote huis", "een groot huis", "'Huis' is a het-word: no -e after 'een'.")),
    )
    harness = await start(tmp_path, monkeypatch, gemma)
    try:
        folder, app = harness.folder, harness.app
        chat = app.store.current_conversation()
        deliver(folder, "/babble nl", T0)
        deliver(folder, "We hebben een grote huis gekocht.", T0 + 1)
        await until(lambda: len(sent(folder)) == 2)
        deliver(folder, "/babble off", T0 + 2)
        await until(lambda: len(sent(folder)) == 4)
        assert sent(folder)[2:] == [
            "📚 Practice recap (Dutch, A2)\n"
            "• \"een grote huis\" → \"een groot huis\": 'Huis' is a het-word: no -e after 'een'.",
            prompts.BABBLE_ENDED.format(english="Dutch", level="A2"),
        ]
        assert app.store.current_conversation().id == chat.id and app.babble.running() is None
        assert "Last practised: Dutch, A2." in app.context.system_prompt(chat)  # "what can you do" knows it
    finally:
        await stop(harness)


async def test_a_practice_voice_note_is_heard_and_answered_in_the_practised_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeVoiceService() as service:
        service.languages = list(CODES)
        service.transcript = "Ik woon in Utrecht."
        harness = await start(
            tmp_path, monkeypatch, FakeGemma(text("→ woon ik\nLeuk! Waar werk je?")), voice_url=service.url
        )
        try:
            folder = harness.folder
            deliver(folder, "/babble nl", T0)
            await until(lambda: len(sent(folder)) == 1)
            put(folder, "v1.m4a", M4A)
            deliver(folder, None, T0 + 1, attachments=[voice("v1.m4a", M4A)])
            await until(lambda: len(voice_notes(folder)) == 1)
            assert sent(folder)[1] == "Leuk! Waar werk je?"  # spoken: no arrow line
            heard = [path for _, path, _, _ in service.requests if path.startswith("/v1/audio/transcriptions")]
            assert heard and all("language=nl" in path for path in heard)
            spoken = [json.loads(body) for _, path, _, body in service.requests if path.startswith("/v1/audio/speech")]
            answer = [request for request in spoken if request["input"] == "Leuk! Waar werk je?"]
            assert answer and answer[0]["language"] == "nl"
        finally:
            await stop(harness)


def slow_sends(app: App, monkeypatch: pytest.MonkeyPatch, seconds: float = 0.3) -> None:
    """signal-cli's answer to each send comes `seconds` after the request is logged (a loaded Mac or CI runner): the
    text counts in sent() while the worker is still answering it."""
    real = app.signal.send

    async def slow(*args: Any, **kwargs: Any) -> Any:
        result = await real(*args, **kwargs)
        await asyncio.sleep(seconds)
        return result

    monkeypatch.setattr(app.signal, "send", slow)


def settled(app: App) -> bool:
    """No text being answered and none waiting: only then does babble.tick() act (it returns at once while busy)."""
    return app._worker_idle.is_set() and app.queue.empty()


async def test_half_an_hour_without_practice_sends_the_recap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [time.time()]
    gemma = FakeGemma(text("Leuk!"), recap_answer())
    # no quiet hours: a recap after an idle end is not held, whatever the time the tests run
    harness = await start(tmp_path, monkeypatch, gemma, clock=lambda: now[0], initiative_quiet_hours="")
    try:
        folder, app = harness.folder, harness.app
        slow_sends(app, monkeypatch)  # her answer counts in sent() while still being sent; a tick then does nothing
        deliver(folder, "/babble nl", T0)
        deliver(folder, "Ik woon in Utrecht.", T0 + 1)
        await until(lambda: len(sent(folder)) == 2 and settled(app))
        await app.babble.tick()
        assert len(sent(folder)) == 2
        now[0] += 31 * 60
        await app.babble.tick()
        await until(lambda: len(sent(folder)) == 3)
        assert sent(folder)[2] == (
            f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_NONE}\n{prompts.BABBLE_RECAP_IDLE}"
        )
        assert app.babble.running() is None and app.babble.held() is None
    finally:
        await stop(harness)


async def test_babble_typed_during_a_call_waits_for_its_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **call_settings(tmp_path))
    try:
        app = harness.app
        app.call_started(ME)
        deliver(harness.folder, "/babble nl", T0)
        await asyncio.sleep(0.3)
        assert sent(harness.folder) == [] and app.babble.running() is None
        app.call_ended()
        await until(lambda: len(sent(harness.folder)) == 1)
        assert app.babble.running(ME) is not None
    finally:
        await stop(harness)


async def test_a_call_turn_passes_the_retry_word_to_the_brain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty first try on a call: the call is told before the retry (it says an acknowledgement meanwhile)."""
    gemma = FakeGemma(text(""), text("Two meetings tomorrow."))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        told: list[int] = []
        turn = await harness.app.call_turn(
            ME, "what's on tomorrow", lambda: None, on_retry=lambda: told.append(len(gemma.calls))
        )
        assert turn.text == "Two meetings tomorrow." and told == [1]
        assert harness.app.brain._language() == harness.app.language  # the log's language code is hers
    finally:
        await stop(harness)


async def test_a_practice_call_turn_passes_the_retry_word_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(text(""), text("Leuk! Waar werk je?"))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble nl", T0)
        await until(lambda: len(sent(folder)) == 1)
        app.call_started(ME)
        told: list[int] = []
        turn = await app.call_turn(
            ME, "Ik woon in Utrecht.", lambda: None, practice=True, on_retry=lambda: told.append(len(gemma.calls))
        )
        assert turn.text == "Leuk! Waar werk je?" and told == [1]
        app.call_ended()
    finally:
        await stop(harness)


async def test_a_practice_call_turn_joins_practice_and_the_calls_end_sends_the_recap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(text("Leuk! Waar werk je?"), recap_answer())
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble nl", T0)
        await until(lambda: len(sent(folder)) == 1)
        session = app.babble.running(ME)
        assert session is not None and app.calls is not None and app.calls._practice(ME) == "nl"
        assert app.calls._practice(STRANGER) is None
        app.call_started(ME)
        turn = await app.call_turn(ME, "Ik woon in Utrecht.", lambda: None, practice=True)
        assert turn.text == "Leuk! Waar werk je?"
        assert gemma.calls[0]["tools"] is None and gemma.calls[0]["max_tokens"] == CALL_MAX_TOKENS
        assert [m.text for m in app.store.window_messages(session.conversation_id, 0)] == [
            "[call] Ik woon in Utrecht.",
            "Leuk! Waar werk je?",
        ]
        app.call_ended()
        await app.practice_call_ended(ME)
        await until(lambda: len(sent(folder)) == 2)
        assert sent(folder)[1] == (
            f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_NONE}\n{prompts.BABBLE_RECAP_HEARD}"
        )
        assert app.babble.running() is None and app.babble.held() is None
    finally:
        await stop(harness)


async def test_new_during_practice_keeps_practice_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble de b1", T0)
        await until(lambda: len(sent(folder)) == 1)
        before = app.store.current_conversation().id
        deliver(folder, "/new", T0 + 1)
        await until(lambda: len(sent(folder)) == 2)
        session = app.babble.running(ME)
        assert session is not None and (session.code, session.level) == ("de", "B1")
        assert app.store.current_conversation().id != before
    finally:
        await stop(harness)


async def test_a_turn_of_a_call_she_placed_during_practice_is_an_ordinary_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a reminder call during practice: the chat conversation, her tools, and practice keeps running
    gemma = FakeGemma(text("Okay."))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble nl", T0)
        await until(lambda: len(sent(folder)) == 1)
        session = app.babble.running(ME)
        assert session is not None
        app.call_started(ME)
        turn = await app.call_turn(ME, "remind me again in 10 minutes", lambda: None)
        assert turn.text == "Okay." and gemma.calls[0]["tools"]  # her tools are offered
        chat = app.store.current_conversation()
        assert "[call] remind me again in 10 minutes" in [m.text for m in app.store.window_messages(chat.id, 0)]
        assert app.store.user_texts(session.conversation_id) == []
        app.call_ended()
        assert app.babble.running(ME) == session
    finally:
        await stop(harness)


async def test_a_user_back_after_an_idle_end_at_night_gets_the_recap_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the recap held for the morning goes out the moment the user writes again: they know practice ended before
    # their message is answered in chat (with her tools, and learned from)
    amsterdam = ZoneInfo("Europe/Amsterdam")
    now = [datetime(2026, 10, 14, 21, 40, tzinfo=amsterdam).timestamp()]
    gemma = FakeGemma(text("Leuk! Waar werk je?"), recap_answer(), text("Het licht is uit."))
    harness = await start(tmp_path, monkeypatch, gemma, clock=lambda: now[0], timezone=amsterdam)
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble nl", int(now[0] * 1000))
        deliver(folder, "Ik woon in Utrecht.", int(now[0] * 1000) + 1)
        await until(lambda: len(sent(folder)) == 2 and settled(app))
        now[0] += 40 * 60  # 22:20: idle, in quiet hours
        await app.babble.tick()
        held = app.babble.held()
        assert held is not None and app.babble.running() is None and len(sent(folder)) == 2
        now[0] += 10 * 60
        deliver(folder, "Kun je het licht uitdoen?", int(now[0] * 1000))
        await until(lambda: len(sent(folder)) == 4)
        assert sent(folder)[2] == (
            f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_NONE}\n{prompts.BABBLE_RECAP_IDLE}"
        )
        assert sent(folder)[3] == "Het licht is uit."
        assert app.babble.held() is None and gemma.calls[2]["tools"]  # the answer: chat, with her tools
    finally:
        await stop(harness)


async def test_a_text_typed_during_a_practice_call_is_a_practice_turn_before_practice_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the queued text is answered as practice (no tools: a practice sentence never sets a wake-up call) and the recap
    # reads it; only then does practice end
    gemma = FakeGemma(text("Leuk! Hoe laat sta je meestal op?"), recap_answer())
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble nl", T0)
        await until(lambda: len(sent(folder)) == 1)
        session = app.babble.running(ME)
        assert session is not None
        app.call_started(ME)
        deliver(folder, "Ik wil morgen om 7 uur gewekt worden", T0 + 1)
        await until(lambda: bool(app.store.unanswered_incoming()))  # it waits for the call's end
        app.call_ended()
        await app.practice_call_ended(ME)
        await until(lambda: len(sent(folder)) == 3)
        assert sent(folder)[1] == "Leuk! Hoe laat sta je meestal op?"
        assert sent(folder)[2].startswith("📚 Practice recap (Dutch, A2)")
        assert gemma.calls[0]["tools"] is None
        assert "1. Ik wil morgen om 7 uur gewekt worden" in gemma.calls[1]["messages"][1]["content"]
        assert [m.text for m in app.store.user_texts(session.conversation_id)] == [
            "Ik wil morgen om 7 uur gewekt worden"
        ]
        assert app.babble.running() is None and app.store.open_reminders() == []
    finally:
        await stop(harness)


async def test_a_text_whose_read_receipt_is_still_on_its_way_when_the_practice_call_ends_is_a_practice_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored text is in the queue before anything is awaited: its read receipt (a round trip to signal-cli, and
    through it to Signal's servers) still on its way when the call ends never lets the recap go first and the text
    become an ordinary turn after practice (the test above failed 1 run in 6, 18 Oct)."""
    gemma = FakeGemma(text("Leuk! Hoe laat sta je meestal op?"), recap_answer())
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble nl", T0)
        await until(lambda: len(sent(folder)) == 1)
        assert app.babble.running(ME) is not None
        app.call_started(ME)
        receipt, on_its_way = app.signal.receipt, asyncio.Event()

        async def slow_receipt(aci: str, timestamp: int) -> None:
            await on_its_way.wait()
            await receipt(aci, timestamp)

        monkeypatch.setattr(app.signal, "receipt", slow_receipt)
        deliver(folder, "Ik wil morgen om 7 uur gewekt worden", T0 + 1)
        await until(lambda: bool(app.store.unanswered_incoming()))
        app.call_ended()
        await asyncio.wait_for(app.practice_call_ended(ME), 5)
        on_its_way.set()
        await until(lambda: len(sent(folder)) == 3)
        assert sent(folder)[1] == "Leuk! Hoe laat sta je meestal op?"
        assert sent(folder)[2].startswith("📚 Practice recap (Dutch, A2)")
        assert gemma.calls[0]["tools"] is None and app.store.open_reminders() == []
    finally:
        await stop(harness)


async def test_her_own_messages_wait_while_practice_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        folder, app = harness.folder, harness.app
        assert app.initiative is not None and not app.initiative._practising()
        deliver(folder, "/babble nl", T0)
        await until(lambda: len(sent(folder)) == 1)
        assert app.initiative._practising()
    finally:
        await stop(harness)


async def test_a_restart_during_practice_warms_up_the_practised_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def practising(store: Store) -> None:
        conversation = store.start_practice_conversation()
        session = {"code": "nl", "level": "A2", "aci": ME, "started": time.time(), "conversation": conversation.id}
        store.set_state("babble:session", json.dumps(session | {"ended": 0, "due": 0}))

    with FakeVoiceService() as service:
        service.languages = list(CODES)
        harness = await start(tmp_path, monkeypatch, FakeGemma(), practising, voice_url=service.url)
        try:
            assert harness.app.babble.running(ME) is not None
            line = prompts.spoken("nl", name=NAME).now_speaking
            await until(lambda: any(request["input"] == line for _, request in speech_requests(service)))
        finally:
            await stop(harness)


# /lock during practice: locked means no answers at all; a recap that falls due while locked waits until /unlock


async def test_lock_stops_practice_replies_and_its_recap_waits_until_unlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = StepClock()
    gemma = FakeGemma(text("Leuk! Waar woon je?"), recap_answer())
    harness = await start(tmp_path, monkeypatch, gemma, clock=clock, initiative_quiet_hours="")
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble nl", T0)
        deliver(folder, "Ik woon in Utrecht.", T0 + 1)
        await until(lambda: len(sent(folder)) == 2)
        deliver(folder, "/lock", T0 + 2)
        locked = prompts.LOCKED.format(device=1, minutes=10)
        await until(lambda: sent(folder)[-1:] == [locked])
        deliver(folder, "Ik werk in Amsterdam.", T0 + 3)  # a practice turn while locked: not read, no answer
        clock.now += 31 * 60  # past the unlock wait, and practice is idle
        await app.babble.tick()
        await asyncio.sleep(0.3)
        assert len(sent(folder)) == 3 and len(gemma.calls) == 1  # no reply, no recap written
        assert app.babble.running(ME) is not None
        deliver(folder, "/unlock", T0 + 4)
        await until(lambda: sent(folder)[-1:] == [prompts.UNLOCKED])
        await app.babble.tick()
        await until(lambda: len(sent(folder)) == 5)
        assert sent(folder)[4] == (
            f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_NONE}\n{prompts.BABBLE_RECAP_IDLE}"
        )
        assert "Ik werk in Amsterdam." not in gemma.calls[1]["messages"][-1]["content"]  # never read
        assert app.babble.session() is None
    finally:
        await stop(harness)


async def test_a_practice_call_ended_by_lock_keeps_its_recap_until_unlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = StepClock()
    gemma = FakeGemma(text("Leuk! Waar werk je?"), recap_answer())
    harness = await start(tmp_path, monkeypatch, gemma, clock=clock, **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble nl", T0)
        await until(lambda: len(sent(folder)) == 1)
        app.call_started(ME)
        await app.call_turn(ME, "Ik woon in Utrecht.", lambda: None, practice=True)
        deliver(folder, "/lock", T0 + 1)  # at envelope time, also during a call
        await until(lambda: len(sent(folder)) == 2)
        app.call_ended()  # the lock hung up the practice call
        await app.practice_call_ended(ME)
        assert app.babble.running() is None and app.babble.held() is not None  # ended, its recap held
        assert len(sent(folder)) == 2 and len(gemma.calls) == 1
        clock.now += 600
        deliver(folder, "/unlock", T0 + 2)
        await until(lambda: sent(folder)[-1:] == [prompts.UNLOCKED])
        await app.babble.tick()
        await until(lambda: len(sent(folder)) == 4)
        assert sent(folder)[3].startswith("📚 Practice recap (Dutch, A2)")
        assert app.babble.session() is None
    finally:
        await stop(harness)


async def test_a_practice_call_turn_while_the_model_is_paused_says_codes_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # as on an ordinary call: the model's pause is code's spoken line, never the "lost" line in the practised language
    harness = await start(tmp_path, monkeypatch, FakeGemma(ModelPausedError("paused")), **call_settings(tmp_path))
    try:
        folder, app = harness.folder, harness.app
        deliver(folder, "/babble nl", T0)
        await until(lambda: len(sent(folder)) == 1)
        app.call_started(ME)
        turn = await app.call_turn(ME, "Ik woon in Utrecht.", lambda: None, practice=True)
        assert (turn.text, turn.code_text) == ("", prompts.MODEL_PAUSED_SPOKEN)
    finally:
        await stop(harness)


# YouTube links: /learn refuses them; a long video's note comes before the answer (and no busy note); /new drops the
# kept captions

YOUTUBE = Path(__file__).parent / "fakes" / "fake_youtube_helper.py"
VIDEO_LINK = "https://youtu.be/AbCdEfGhI_-"


def youtube_helper(tmp_path: Path, seconds: float) -> tuple[str, ...]:
    """The fake YouTube helper with made-up captions of `seconds` (a segment every 5 s)."""
    state = tmp_path / "youtube.json"
    segments = [[n * 5.0, f"the beam carries the load {n}"] for n in range(int(seconds / 5))]
    answer = {"ok": True, "language": "en", "generated": False, "length": seconds, "cut": False, "segments": segments}
    state.write_text(json.dumps(answer))
    return (sys.executable, str(YOUTUBE), str(state))


async def test_the_harness_never_runs_the_real_youtube_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # a test that sends a video link with the default settings must never reach www.youtube.com
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        command = " ".join(harness.app.cfg.youtube_helper)
        assert "tools/youtube" not in command and not Path(harness.app.cfg.youtube_helper[0]).exists()
    finally:
        await stop(harness)


async def test_learn_refuses_a_video_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class NoFetcher(Fetcher):
        def fetch(self, url: str) -> Page:
            raise AssertionError("a video link reached the fetcher")

    harness = await start(tmp_path, monkeypatch, FakeGemma(), fetcher=NoFetcher())
    try:
        deliver(harness.folder, f"/learn {VIDEO_LINK}", T0)
        await until(lambda: sent(harness.folder) == [prompts.VIDEO_LEARN_COMMAND])
        assert harness.gemma.calls == []
    finally:
        await stop(harness)


async def test_a_long_videos_note_comes_first_and_no_busy_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slow: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    gemma = FakeGemma(tool("read_link", url=VIDEO_LINK), slow, text("- [20:00] notes"), text("- [40:00] notes"))
    gemma.add(text("A lecture on beams [0:00]."))
    harness = await start(
        tmp_path, monkeypatch, gemma, busy_note_seconds=0.3, youtube_helper=youtube_helper(tmp_path, 6_000)
    )
    try:
        deliver(harness.folder, f"summarise {VIDEO_LINK}", T0)
        note = "Reading a 1 h 40 min video: about 1 minute."
        await until(lambda: sent(harness.folder) == [note])
        await asyncio.sleep(0.5)  # past the busy note's time: the video's note replaced it
        slow.set_result(text("- [0:00] notes"))
        await until(lambda: sent(harness.folder) == [note, "A lecture on beams [0:00]."])
        assert len(gemma.calls) == 5  # the tool call, three parts, the answer
        logged = harness.app.store.tool_calls()[0]
        assert logged.result == prompts.VIDEO_STUB
        assert harness.app.videos.held() == 1  # kept for a follow-up
        deliver(harness.folder, "/new", T0 + 1)
        await until(lambda: harness.app.videos.held() == 0)  # /new: gone from memory
    finally:
        await stop(harness)


# owner, 14 Oct: "what can you do?" is answered from code's list of the parts on in this install; /help shows it word
# for word, and a level that changes (the Mac's security, /initiative) changes her rules


def her_rules(app: App) -> str:
    return app.context.system_prompt(app.store.current_conversation())


async def test_her_rules_and_help_carry_what_she_can_do_here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), security_level=lambda: "hardened")
    try:
        app = harness.app
        await until(lambda: "(now: hardened)" in her_rules(app))  # read in the background at the start
        lines = prompts.capabilities(reminders=True, security="hardened")  # this install: the defaults
        assert app.capability_lines() == lines
        assert "\n".join(lines) in her_rules(app)
        deliver(harness.folder, "/help", T0)
        await until(lambda: sent(harness.folder) == [on_phone(prompts.help_text(lines))])
        deliver(harness.folder, "/initiative on", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert "Messages I start: now and then a check-in or a short note. On or off (now: on)" in her_rules(app)
    finally:
        await stop(harness)


async def test_the_list_names_hue_lights_when_they_are_set_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hall = DeviceSpec("hall light", "", "light", "normal", brand="hue", light="11111111-1111-4111-8111-111111111111")
    harness = await start(tmp_path, monkeypatch, FakeGemma(), tapo_devices=(*DEVICE_SPECS, hall))
    try:
        devices = next(line for line in harness.app.capability_lines() if line.startswith("• Lights and plugs: "))
        assert devices.startswith("• Lights and plugs: TP-Link and Philips Hue, switched by name")
        assert "Hue lights: on, off, brightness and whites, no colours." in devices
    finally:
        await stop(harness)


async def test_the_list_names_tp_link_for_kasa_and_tapo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # brand "kasa" covers Kasa and Tapo: the line names their maker, so a Tapo plug never reads as not set up
    harness = await start(tmp_path, monkeypatch, FakeGemma(), tapo_devices=DEVICE_SPECS)
    try:
        devices = next(line for line in harness.app.capability_lines() if line.startswith("• Lights and plugs: "))
        assert devices.startswith("• Lights and plugs: TP-Link, switched by name, each by its own rule. Example:")
    finally:
        await stop(harness)


async def test_off_the_live_mac_the_security_level_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())  # the real reader, under the tests' own root
    try:
        await asyncio.sleep(0.2)
        assert "Levels: hardened or nuclear (now: not read yet)" in her_rules(harness.app)
    finally:
        await stop(harness)


async def test_the_list_follows_the_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = call_settings(tmp_path) | {"calls_barge": "loudness", "projects_enabled": True}
    harness = await start(tmp_path, monkeypatch, FakeGemma(), security_level=lambda: "nuclear", **settings)
    try:
        lines = harness.app.capability_lines()
        assert "• Calls: a spoken conversation on Signal. Interrupt: only speech or any sound (now: any sound)" in (
            line.split(". Example", 1)[0] for line in lines
        )
        assert any(line.startswith("• Reminders and wake-up calls: ") for line in lines)
        assert any("Levels: off, summary, docs or code (now: summary)" in line for line in lines)
        assert any(line.startswith("• Voice notes: ") for line in lines)  # calls need [voice]: on too
    finally:
        await stop(harness)


async def test_the_list_names_each_calendars_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    icloud = CalendarSource("family", caldav="https://caldav.icloud.com", user="alex@example.com", calendar="Family")
    sources = (CalendarSource("calendar"), icloud, replace(icloud, name="home", calendar="Home"))
    never = (str(tmp_path / "no-calendar-helper"),)  # never the real one: it would ask iCloud
    harness = await start(
        tmp_path, monkeypatch, FakeGemma(), calendar_enabled=True, calendar_sources=sources, calendar_helper=never
    )
    try:
        lines = harness.app.capability_lines()
        assert any(line.startswith("• Calendar: Google and iCloud, read only, never changed.") for line in lines)
    finally:
        await stop(harness)
