"""The bridge with [documents]: a PDF from the allowed user saved into the folder and signal-cli's copy deleted, a
stranger's never; /docs and /docs send N; the hourly look and its note (held in quiet hours, skipped under /quiet);
the document tools offered. The folder is a temporary one; the helper the fake one."""

import base64
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from local_assistant import app as app_module
from local_assistant import prompts
from local_assistant.app import WAITING_COMMANDS
from local_assistant.documents import LOOK_SECONDS, LOOKED_KEY, DocumentError, Look, shown_folder
from local_assistant.initiative import QUIET_KEY
from local_assistant.signal_text import plain as on_phone
from tests.conftest import ME, STRANGER, TZ
from tests.fakes.fake_gemma import FakeGemma, text
from tests.test_app import T0, deliver, put, requests, sent, start, stop, until
from tests.test_documents import FAKE, pdf

LEASE = b"%PDF-1.7\nPets: one cat is allowed.\n"
SENT_ON = datetime.fromtimestamp(T0 / 1000, TZ)  # now: older messages get the offline note
SAVED_AS = f"{SENT_ON.date().isoformat()} Lease.pdf"  # in the folder itself, never in a folder of hers


def settings(tmp_path: Path) -> dict[str, Any]:
    folder = tmp_path / "Estia-KB"
    folder.mkdir(parents=True, exist_ok=True)
    return {
        "documents_enabled": True,
        "documents_folder": folder,
        "documents_helper": (sys.executable, str(FAKE), str(tmp_path / "helper.json")),
    }


def attachment(attachment_id: str, data: bytes, filename: str = "Lease.pdf") -> dict[str, Any]:
    return {"contentType": "application/pdf", "id": attachment_id, "size": len(data), "filename": filename}


def shown(folder: Path) -> list[str]:
    return [on_phone(message) for message in sent(folder)]


async def test_a_pdf_from_the_user_is_saved_indexed_and_signal_clis_copy_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        copy = put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, None, T0, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: shown(harness.folder) == [f"Saved in your Estia-KB folder: {SAVED_AS} (1 page)."])
        assert (tmp_path / "Estia-KB" / SAVED_AS).read_bytes() == LEASE
        assert not copy.exists() and harness.app.store.attachments(1) == []  # deleted, as for photos
        assert harness.gemma.calls == []  # no words with it: code's note only, no AI model
        assert harness.app.store.message(1).text == f"[document] {SAVED_AS}"
        assert [hit.path for hit in harness.app.store.search_doc_pages("cat")] == [SAVED_AS]
    finally:
        await stop(harness)


async def test_a_pdf_with_words_is_saved_then_answered_with_the_document_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(
        tmp_path, monkeypatch, FakeGemma(text("Yes: one cat is allowed (page 1).")), **settings(tmp_path)
    )
    try:
        put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, "may I keep a cat?", T0, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: len(sent(harness.folder)) == 2)
        assert shown(harness.folder) == [
            f"Saved in your Estia-KB folder: {SAVED_AS} (1 page).",
            "Yes: one cat is allowed (page 1).",
        ]
        request = harness.gemma.calls[0]
        assert request["messages"][-1]["content"].endswith(f"[document] {SAVED_AS}\nmay I keep a cat?")
        names = [tool["function"]["name"] for tool in request["tools"]]
        assert "search_documents" in names and "read_document" in names
        assert "Documents:\n" in request["messages"][0]["content"]
    finally:
        await stop(harness)


async def test_a_strangers_pdf_is_never_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        copy = put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, "here", T0, source=STRANGER, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: not copy.exists())
        assert list((tmp_path / "Estia-KB").iterdir()) == [] and sent(harness.folder) == []
    finally:
        await stop(harness)


async def test_with_documents_off_a_pdf_is_a_file_she_cannot_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        copy = put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, "look", T0, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: sent(harness.folder) == [prompts.TEXT_ONLY])
        assert not copy.exists()
    finally:
        await stop(harness)


async def test_a_file_that_only_claims_to_be_a_pdf_is_not_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        fake = b"<html>not a PDF</html>"
        copy = put(harness.folder, "a1.pdf", fake)
        deliver(harness.folder, None, T0, attachments=[attachment("a1.pdf", fake)])
        await until(lambda: shown(harness.folder) == [prompts.DOCUMENT_NOT_SAVED["not_pdf"]])
        assert not copy.exists() and list((tmp_path / "Estia-KB").iterdir()) == []
    finally:
        await stop(harness)


async def test_docs_lists_and_docs_send_returns_the_pdf_to_the_account_that_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = settings(tmp_path)
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **options)
    try:
        pdf(options["documents_folder"] / "lease.pdf", "Pets: one cat is allowed.")  # put there a minute ago
        deliver(harness.folder, "/docs", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        listing = shown(harness.folder)[0]
        assert listing.startswith("Documents (newest first)\n") and "lease.pdf · 1 page" in listing
        number = harness.app.store.doc_files()[0].id
        deliver(harness.folder, f"/docs send {number}", T0 + 1)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert shown(harness.folder)[1] == f"Sent: #{number} lease.pdf."
        attached = [
            r["params"] for r in requests(harness.folder) if r["method"] == "send" and "attachments" in r["params"]
        ]
        data = (options["documents_folder"] / "lease.pdf").read_bytes()
        link = f"data:application/pdf;filename=lease.pdf;base64,{base64.b64encode(data).decode()}"
        assert attached == [{"recipient": [ME], "attachments": [link]}]
        deliver(harness.folder, "/docs send 999", T0 + 2)
        await until(lambda: len(sent(harness.folder)) == 3)
        assert shown(harness.folder)[2] == prompts.DOCS_NOT_FOUND.format(number=999)
    finally:
        await stop(harness)


async def test_docs_without_documents_says_how_they_are_turned_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma())
    try:
        deliver(harness.folder, "/docs", T0)
        await until(lambda: sent(harness.folder) == [prompts.DOCS_OFF])
    finally:
        await stop(harness)


def at(hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, 14, hour, minute, tzinfo=TZ).timestamp()


async def test_the_hourly_look_announces_new_pdfs_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = settings(tmp_path)
    now = [at(10)]
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: now[0], **options)
    try:
        folder = options["documents_folder"]
        pdf(folder / "lease.pdf", "Pets.")
        await harness.app.documents_tick()  # the first look: nothing is new
        pdf(folder / "boiler.pdf", "Reset.")
        now[0] = at(10, 30)
        await harness.app.documents_tick()  # not an hour yet: no look
        assert harness.app.store.doc_file_by_path("boiler.pdf") is None and sent(harness.folder) == []
        now[0] = at(11, 1)
        await harness.app.documents_tick()
        await until(lambda: shown(harness.folder) == ["I found 1 new document: boiler.pdf."])
        now[0] = at(12, 2)
        await harness.app.documents_tick()
        assert len(sent(harness.folder)) == 1  # once
    finally:
        await stop(harness)


async def test_a_note_found_in_quiet_hours_waits_for_eight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = settings(tmp_path)
    now = [at(10)]
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: now[0], **options)
    try:
        documents = harness.app.documents
        assert documents is not None
        await harness.app.documents_tick()
        now[0] = at(23, 30)
        pdf(options["documents_folder"] / "letter.pdf", "School.")
        harness.app.store.set_state(LOOKED_KEY, "0")
        await harness.app.documents_tick()  # quiet hours: no look at all
        assert harness.app.store.doc_file_by_path("letter.pdf") is None
        await documents.look()  # a question at 23:30 looked (search_documents)
        await harness.app.documents_tick()
        assert sent(harness.folder) == []  # held
        now[0] = at(8, 1) + 86_400
        await harness.app.documents_tick()
        await until(lambda: shown(harness.folder) == ["I found 1 new document: letter.pdf."])
    finally:
        await stop(harness)


async def test_under_quiet_the_note_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = settings(tmp_path)
    now = [at(10)]
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: now[0], **options)
    try:
        await harness.app.documents_tick()
        harness.app.store.set_state(QUIET_KEY, f"{at(9):.0f}")
        pdf(options["documents_folder"] / "letter.pdf", "School.")
        now[0] = at(11, 1)
        await harness.app.documents_tick()
        assert harness.app.store.doc_file_by_path("letter.pdf") is not None and sent(harness.folder) == []
        harness.app.store.set_state(QUIET_KEY, "")
        now[0] = at(12, 2)
        await harness.app.documents_tick()
        assert sent(harness.folder) == []  # skipped, not held
    finally:
        await stop(harness)


async def test_no_look_while_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = settings(tmp_path)
    now = [at(10)]
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: now[0], **options)
    try:
        pdf(options["documents_folder"] / "lease.pdf", "Pets.")
        harness.app.store.set_state("lock:on", "on")
        await harness.app.documents_tick()
        assert harness.app.store.get_state(LOOKED_KEY) is None
    finally:
        await stop(harness)


async def test_at_the_start_the_helper_warms_up_then_she_looks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "DOCUMENTS_FIRST_SECONDS", 0.0)
    options = settings(tmp_path)
    pdf(options["documents_folder"] / "lease.pdf", "Pets.")
    # at 10:00: the hourly look waits in quiet hours (22:00-08:00), whatever time the tests run at
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: at(10), **options)
    try:
        record = tmp_path / "helper.json.calls"
        await until(lambda: record.exists() and len(record.read_text().splitlines()) == 2)
        assert [line.split(",")[0] for line in record.read_text().splitlines()] == ['["warm"', '["read"']
    finally:
        await stop(harness)


def test_docs_waits_for_the_end_of_a_call_and_help_names_it() -> None:
    assert "/docs" in WAITING_COMMANDS  # she sends nothing during a call
    assert "/docs - " in prompts.HELP and "/docs send N" in prompts.HELP


async def test_a_pdf_saved_before_a_restart_is_not_saved_again_nor_given_to_gemma(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def saved_then_stopped(store: Any) -> None:
        conversation = store.current_conversation()
        store.add_incoming(conversation.id, ME, T0, "text", f"[document] {SAVED_AS}")

    harness = await start(tmp_path, monkeypatch, FakeGemma(), prepare=saved_then_stopped, **settings(tmp_path))
    try:
        await until(lambda: shown(harness.folder) == [prompts.DOCUMENT_SAVED_BEFORE.format(path=SAVED_AS)])
        assert harness.gemma.calls == []
    finally:
        await stop(harness)


async def test_the_hourly_look_has_a_budget_and_stops_when_a_call_begins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), clock=lambda: at(10), **settings(tmp_path))  # not quiet
    try:
        documents = harness.app.documents
        assert documents is not None
        asked: list[dict[str, Any]] = []

        async def look(**kwargs: Any) -> Look:
            asked.append(kwargs)
            return Look()

        monkeypatch.setattr(documents, "look", look)
        await harness.app.documents_tick()
        assert asked[0]["budget"] == LOOK_SECONDS and not asked[0]["stop"]()
        harness.app._no_call.clear()  # a call began while she reads
        try:
            assert asked[0]["stop"]()
        finally:
            harness.app._no_call.set()
    finally:
        await stop(harness)


async def test_macos_refusing_the_folder_gets_its_own_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        documents = harness.app.documents
        assert documents is not None

        async def refused(**kwargs: Any) -> Look:
            return Look(problem="refused")

        async def not_saved(*args: Any) -> None:
            raise DocumentError("refused")

        monkeypatch.setattr(documents, "look", refused)
        monkeypatch.setattr(documents, "save", not_saved)
        deliver(harness.folder, "/docs", T0)
        await until(lambda: shown(harness.folder) == [on_phone(prompts.DOCS_REFUSED)])
        put(harness.folder, "a1.pdf", LEASE)
        deliver(harness.folder, None, T0 + 1, attachments=[attachment("a1.pdf", LEASE)])
        await until(lambda: len(sent(harness.folder)) == 2)
        assert shown(harness.folder)[1] == on_phone(prompts.DOCUMENT_NOT_SAVED["refused"])
        for text in (prompts.DOCS_REFUSED, prompts.DOCUMENT_NOT_SAVED["refused"]):
            assert "share-documents.sh" not in text  # sharing the same folder again would change nothing
            assert "Documents, Desktop and Downloads" in text and "~/Estia-KB" in text  # the fix (live, 14 Oct)
    finally:
        await stop(harness)


async def test_her_list_and_help_name_documents_with_their_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documents merge: with [documents], code's list of what she can do (her rules, /help) has a Documents line
    with the folder; without it, none (test_app: the list follows the config)."""
    harness = await start(tmp_path, monkeypatch, FakeGemma(), **settings(tmp_path))
    try:
        app = harness.app
        lines = app.capability_lines()
        line = next(line for line in lines if line.startswith("• Documents: "))
        folder = shown_folder(tmp_path / "Estia-KB")  # "~/…" when pytest's temp folder is in a home
        assert f"On or off (now: on, folder {folder})" in line
        assert line in app.context.system_prompt(app.store.current_conversation())
        deliver(harness.folder, "/help", T0)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert shown(harness.folder) == [on_phone(prompts.help_text(lines))]
        assert "/docs send N" in shown(harness.folder)[0]
    finally:
        await stop(harness)
