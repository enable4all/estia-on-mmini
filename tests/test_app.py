import asyncio
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from local_assistant import app as app_module
from local_assistant import prompts
from local_assistant.app import ALIVE_KEY, CALL_MAX_TOKENS, WELCOME_KEY, App, signal_client
from local_assistant.calendar_graph import OutlookReader
from local_assistant.config import DEFAULT_NAME, CalendarSource, Config, DeviceSpec, MailAccount
from local_assistant.fetcher import Fetcher, FetchError, Page
from local_assistant.gemma import Completion, GemmaUnavailableError
from local_assistant.health import HealthCheck
from local_assistant.language import CODES
from local_assistant.mail_auth import CALENDAR_SCOPES, MailAuthError, TokenStore
from local_assistant.morning_digest import MorningDigest
from local_assistant.part_status import Record
from local_assistant.part_status import lines as part_lines
from local_assistant.part_status import read as read_part_status
from local_assistant.profiler import UNANNOUNCED_KEY
from local_assistant.signal_io import SignalClient
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
WELCOME = prompts.welcome(name=NAME, reminders=True)  # her own messages are opt-in: no line for them

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
    **settings: Any,
) -> Harness:
    """The bridge on a fake signal-cli. Its store has had the welcome (a fresh install's first message) unless
    `welcome`; starting again on the same tmp_path is a restart."""
    folder = tmp_path / "signal"
    (folder / "attachments").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FAKE_SIGNAL_DIR", str(folder))
    options: dict[str, Any] = {
        "allowed_acis": frozenset({ME}),
        "busy_note_seconds": 5.0,
        "typing_refresh_seconds": 0.05,
        "signal_attachments_dir": folder / "attachments",
    }
    options.update(settings)
    cfg = Config(bot_number="+306900000000", data_dir=tmp_path / "data", log_dir=tmp_path / "logs", **options)
    store = Store.open(cfg.db_path)
    if not welcome:
        store.set_state(WELCOME_KEY, "sent")
    if prepare is not None:
        prepare(store)
    signal = SignalClient([sys.executable, str(FAKE)], cfg.allowed_acis, request_timeout=5, backoff_start=0.05)
    app = App(cfg, store, signal, gemma, fetcher or Fetcher(), retry_delay=0.01, health=health, clock=clock)
    task = asyncio.create_task(app.run())
    await until(lambda: signal.ready)
    return Harness(app, gemma, folder, task)


async def stop(harness: Harness) -> None:
    harness.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await harness.task
    await harness.app.signal.stop()
    harness.app.store.close()


def deliver(folder: Path, message: str | None, ts: int, source: str = ME, **data: Any) -> None:
    envelope: dict[str, Any] = {"sourceUuid": source, "sourceName": "Alex", "timestamp": ts}
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
        await until(lambda: sent(harness.folder) == [prompts.HELP, prompts.TEXT_ONLY])
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
        assert [harness.app.store.message(i).status for i in (1, 2)] == ["failed", "failed"]
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
            "Profile changes:\nSaved #1: The user likes tea. (/forget 1 undoes it)",
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
        assert alert.startswith("⚠ Problem on the study Mac:\n- Extra check: not answering (since ")
        out = harness.app.store.message(1)
        assert (out.direction, out.kind, out.peer_aci) == ("out", "note", ME)
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

        def broken(*_args: Any) -> int:
            raise RuntimeError("disk full")

        monkeypatch.setattr(harness.app.store, "add_attachment", broken)
        image = png(8, 8)
        put(harness.folder, "p1.png", image)
        data: dict[str, Any] = {"timestamp": T0, "message": "look", "attachments": [photo("p1.png", image)]}
        envelope: dict[str, Any] = {"sourceUuid": ME, "timestamp": T0, "dataMessage": data}
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
        assert sent(harness.folder) == ["Fri 9 Oct\nCalendar today:\n- 09:30–10:00 Dentist\nTomorrow: nothing"]
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
            prompts.rules(name=NAME, calendar=True, reminders=True)
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
        assert [line.label for line in part_lines(cfg, {}, 0)] == ["Mail (family)", "Calendar", "Calendar (work)"]
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
        assert (await app.status()).parts == ("Calendar: set up, not tested yet",)
        await app._status_lines()  # reads the calendar for real
        assert (await app.status()).parts == ("Calendar: working, last read 11:40",)
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
        assert "Calls: ready" in await app._status_lines()
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


async def test_after_a_call_the_summary_is_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(text("- an idea about the garden\n- follow up: ask Anna"))
    harness = await start(tmp_path, monkeypatch, gemma, **call_settings(tmp_path))
    try:
        await harness.app.call_summary(ME, ["User: the garden", "Estia: Nice idea."], 3)
        # the note is sent before call_summary returns (no waiting). Under heavy load the fake's answer can miss the
        # harness's 5 s request timeout; the bridge then sends again (at-least-once), so the same note may be there twice
        assert set(sent(harness.folder)) == {
            "📞 Call summary (3 min):\n- an idea about the garden\n- follow up: ask Anna"
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
    assert client._command[:3] == ["/v/signal-cli", "--scrub-log", "-a"]
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
        assert sent(harness.folder) == ["Ajax won 2-1, says nos.nl.", "🔎 Sources:\n• nos.nl: https://nos.nl/1"]
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
        assert sent(harness.folder) == ["🔎 Sources:\n• nos.nl: https://nos.nl/1"]
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
        assert "/help lists the commands" not in seen and "Hi, I'm Estia" not in seen
    finally:
        await stop(harness)
    restarted = await start(tmp_path, monkeypatch, FakeGemma(), welcome=True)
    try:
        deliver(restarted.folder, "/help", T0 + 1)
        await until(lambda: sent(restarted.folder)[-1] == prompts.HELP)
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
        assert sent(harness.folder) == [prompts.HELP]
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
        assert sent(harness.folder) == [prompts.HELP]
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
        await until(lambda: sent(restarted.folder)[-1] == prompts.HELP)
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
    assert plain.startswith("Hi, I'm Estia") and plain.endswith("/help lists the commands.")
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
    tries = [line for line in every.splitlines() if line.startswith("- ")]
    assert len(tries) == 8 and all(":" in line for line in tries)
    assert "except what web search, places and music need to send" in every
    assert "I only read your mail and calendar, never change them" in every
    assert "Reminder set" not in every  # the contract comes with reminders only
    with_reminders = prompts.welcome(name=NAME, reminders=True, wake_calls=True)
    [line] = [line for line in with_reminders.splitlines() if line.startswith("- Reminders:")]
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
            assert ("nl", {"input": "De lamp is uit.", "voice": "af_heart", "speed": 1.1, "language": "nl"}) in (
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
            warm_up = ("nl", {"input": line, "voice": "af_heart", "speed": 1.1, "language": "nl"})
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


async def test_with_reminders_off_nothing_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(text("Hi.")), reminders_enabled=False)
    try:
        app = harness.app
        assert app.reminders is None and app.reminder_scheduler is None
        assert app.context.system_prompt(app.store.current_conversation()).startswith(prompts.rules(name=NAME))
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
    harness = await start(
        tmp_path, monkeypatch, gemma, prepare=planned, initiative_enabled=True, initiative_quiet_hours=""
    )
    try:
        assert harness.app.initiative is not None
        await harness.app.initiative.tick()
        footer = prompts.INITIATIVE_FOOTERS["followup"]
        await until(lambda: sent(harness.folder) == [f"Did you get hold of the dentist?\n{footer}"])
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
        welcome = harness.app._welcome_text()
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
        plain = prompts.welcome(name=NAME, reminders=True)
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
