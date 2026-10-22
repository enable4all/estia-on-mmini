"""Documents: the app's side of the documents folder ([documents]): a PDF the user sends saved and read into the
index, /docs and /docs send, the hourly look and the note on new PDFs. Built by App, which keeps the delegates the tests
call; every other part of the app is read through `self.app`."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from local_assistant import prompts
from local_assistant.config import quiet_hours_of
from local_assistant.documents import (
    LOOK_ON_REQUEST_SECONDS,
    LOOK_SECONDS,
    LOOKED_KEY,
    MAX_BYTES,
    DocumentError,
    Documents,
    not_estia_kb,
    saved_note,
    shown_folder,
)
from local_assistant.initiative import in_quiet_hours
from local_assistant.signal_io import NotAllowedError, SignalError
from local_assistant.store import Attachment, Message
from local_assistant.voice_ready import QUIET_HOURS

if TYPE_CHECKING:
    from local_assistant.app import App

log = logging.getLogger("local_assistant")  # the app's own logger: its lines keep their name in bridge.log

# she checks every minute whether a look is due (every look_minutes) and whether a held note may go
DOCUMENTS_TICK_SECONDS = 60.0


class DocumentsHost:
    """The app's side of the documents folder."""

    def __init__(self, app: App, *, first_seconds: float) -> None:
        self.app = app
        # app.py's DOCUMENTS_FIRST_SECONDS: the first check comes this long after the start
        self.first_seconds = first_seconds
        # the documents' note and look wait like the watchers' alerts: the initiative's quiet hours, else 22:00-08:00
        self.quiet = quiet_hours_of(app.cfg.initiative_quiet_hours) or QUIET_HOURS

    async def save_documents(self, message: Message, attachments: list[Attachment]) -> tuple[str, bool]:
        """Save the PDFs the allowed user sent into the documents folder and read them into the index; signal-cli's
        copies go (as for photos). The message keeps where they were saved, the words sent with them below. Code's
        note (saved where, or why not) and whether one was saved."""
        notes: list[str] = []
        paths: list[str] = []
        documents = self.app.documents
        when = datetime.fromtimestamp((message.signal_ts or 0) / 1000 or self.app._clock(), self.app.cfg.timezone)
        for attachment in attachments[: prompts.DOCUMENTS_PER_MESSAGE]:
            try:
                if documents is None:
                    raise DocumentError("not_shared")
                if max(attachment.size, self.app._file_size(attachment.signal_id)) > MAX_BYTES:
                    raise DocumentError("too_big")  # never fetched
                data = await self.app.signal.get_attachment(message.peer_aci, attachment.signal_id)
                file = await documents.save(data, attachment.filename, when)
            except DocumentError as exc:
                log.warning("message %d: a PDF not saved: %s", message.id, exc.code)
                if exc.code == "not_shared" and documents is not None and not_estia_kb(documents.folder):
                    notes.append(prompts.DOCUMENT_NOT_KB.format(folder=shown_folder(documents.folder)))  # as /docs
                    continue
                notes.append(prompts.DOCUMENT_NOT_SAVED.get(exc.code, prompts.DOCUMENT_NOT_SAVED["failed"]))
                continue
            except (SignalError, NotAllowedError) as exc:
                log.warning("message %d: a PDF not fetched: %s", message.id, type(exc).__name__)
                notes.append(prompts.DOCUMENT_NOT_SAVED["failed"])
                continue
            paths.append(file.path)
            notes.append(saved_note(file))
        if len(attachments) > prompts.DOCUMENTS_PER_MESSAGE:
            notes.append(prompts.DOCUMENTS_LIMIT.format(count=prompts.DOCUMENTS_PER_MESSAGE))
        self.app._drop_attachments(message.id)  # saved or not: signal-cli's copies go
        words = message.text.partition("\n")[2]
        where = ", ".join(paths) or prompts.DOCUMENT_NONE_SAVED
        self.app.store.set_message_text(
            message.id, f"{prompts.DOCUMENT_MARK} {where}" + (f"\n{words}" if words else "")
        )
        return "\n".join(notes), bool(paths)

    async def docs_command(self, args: str, message: Message) -> str:
        """/docs: a look first (a PDF put there a minute ago is listed), then the list, newest first; the list names
        them, so no note follows for these. /docs send N: that PDF back to the account that asked."""
        documents = self.app.documents
        if documents is None:
            return prompts.DOCS_OFF
        words = args.split()
        if not words:
            look = await documents.look(budget=LOOK_ON_REQUEST_SECONDS)
            if look.problem:
                if look.problem == "refused":
                    return prompts.DOCS_REFUSED
                if not_estia_kb(documents.folder):  # her config's folder, not the share, is the fix (live, 15 Oct)
                    return prompts.DOCS_NOT_KB.format(folder=shown_folder(documents.folder))
                return prompts.DOCS_CLOSED
            documents.mark_announced([file.id for file in documents.files() if not file.announced])
            return documents.listing()
        if len(words) == 2 and words[0].lower() == "send" and words[1].lstrip("#").isdecimal():
            return await self.send_document(documents, int(words[1].lstrip("#")), message)
        return prompts.DOCS_USAGE

    async def send_document(self, documents: Documents, number: int, message: Message) -> str:
        file = documents.find(f"#{number}")
        if file is None:
            return prompts.DOCS_NOT_FOUND.format(number=number)
        try:
            data = documents.read_file(file)
        except DocumentError as exc:
            return prompts.DOCS_NOT_SENT.format(why=prompts.DOCS_NOT_SENT_WHY.get(exc.code, exc.code))
        try:
            await self.app.signal.send_document(message.peer_aci, data, Path(file.path).name)
        except (SignalError, NotAllowedError) as exc:
            log.warning("/docs send: not sent: %s", type(exc).__name__)
            return prompts.DOCS_NOT_SENT.format(why=prompts.DOCS_NOT_SENT_WHY["failed"])
        log.info("/docs send: document %d sent, %d bytes", file.id, len(data))
        return prompts.DOCS_SENT.format(number=file.id, name=file.path)

    async def documents_forever(self) -> None:
        documents = self.app.documents
        assert documents is not None
        await asyncio.sleep(self.first_seconds)
        await documents.warm()  # Vision's model prepared before the first scan (a deploy is a new build)
        while True:
            try:
                await self.documents_tick()
            except Exception:
                log.exception("documents: the look failed; tried again later")
            await asyncio.sleep(DOCUMENTS_TICK_SECONDS)

    async def documents_tick(self) -> None:
        """The hourly look at the folder (outside quiet hours; it starts never during a call, an answer or the lock,
        reads LOOK_SECONDS at most and stops when a call or the lock begins: the rest a minute later), then the note
        on new PDFs: held in quiet hours, skipped under /quiet. Gemma is used in neither."""
        documents = self.app.documents
        if documents is None or self.app.guard.locked or not self.app._no_call.is_set():
            return
        now = self.app._clock()
        quiet = in_quiet_hours(datetime.fromtimestamp(now, self.app.cfg.timezone), self.quiet)
        try:
            looked = float(self.app.store.get_state(LOOKED_KEY) or 0)
        except ValueError:
            looked = 0.0
        if not quiet and not self.app._processing and now - looked >= self.app.cfg.documents_look_minutes * 60:
            await documents.look(
                budget=LOOK_SECONDS, stop=lambda: self.app.guard.locked or not self.app._no_call.is_set()
            )
        text, found = documents.announcement()
        if not found:
            return
        if self.app.initiative is not None and self.app.initiative.quiet:  # /quiet: skipped, not held
            documents.mark_announced(found)
            log.info("documents: the note on %d new document(s) skipped: /quiet", len(found))
            return
        if quiet or self.app.guard.locked:
            return  # held until the quiet hours end
        documents.mark_announced(found)
        conversation = self.app.store.current_conversation()
        for aci in sorted(self.app.cfg.allowed_acis):
            out_id = self.app.store.add_outgoing(conversation.id, aci, "note", text, None, private=True)  # PDF names
            await self.app._send(out_id)
        log.info("documents: the note on %d new document(s) sent", len(found))
