"""Calls: the app's side of a call (calls.Host): what a call reads at its start (the glance), each spoken turn
through the brain, the warm-up, the practice call, the notes, the chords message and the summary. Built by App, which
keeps one-line delegates for the names the tests call; every other part of the app is read through `self.app`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Collection
from typing import TYPE_CHECKING

from local_assistant import chords, prompts
from local_assistant.babble import Session
from local_assistant.calendar_tools import GLANCE_NOT_READ, CalendarTools
from local_assistant.calls import Turn, speakable, voice_ready, whole_sentences
from local_assistant.gemma import GemmaError, ModelPausedError
from local_assistant.language import ENGLISH, is_yes
from local_assistant.timefmt import local_hhmm

if TYPE_CHECKING:
    from local_assistant.app import App

log = logging.getLogger("local_assistant")  # the app's own logger: its lines keep their name in bridge.log

PRACTICE_DRAIN_S = 120.0  # after a practice call, texts typed during it are answered as practice for at most this
CALL_MAX_TOKENS = 300  # spoken answers stay short (a few sentences); a cut-off one ends at its last whole sentence

# a yes to her offer of the day's rundown on a call: code reads today's calendar and the mail digest itself (live
# evals, 13 Oct: after "yes please" she called no tool, 7 of 7), as her own tool calls would
RUNDOWN_READS = (("calendar_agenda", '{"start": "today", "days": 1}'), ("mail_digest", "{}"))


def asks(answer: str) -> bool:
    """Whether her answer asks something (a question mark, also the full-width one)."""
    return "?" in answer or "？" in answer


def rundown_reads(offer: str, said: str, language: str, *, calendar: bool, mail: bool) -> tuple[tuple[str, str], ...]:
    """What code reads for her when the user says yes to her offer of the day's rundown on a call: `offer` is her
    offer, the call's previous answer ("" when that answer was none: `CallGlance.offer`); it asks something ("Want the
    rundown?") and the user's reply is a whole yes (`is_yes`, in the call's language or English). Then today's
    calendar and the mail digest, each when it is set up; anything else reads nothing (she reads with her tools as
    ever)."""
    if not asks(offer) or not is_yes(said, language):
        return ()
    return tuple(read for read, on in zip(RUNDOWN_READS, (calendar, mail), strict=True) if on)


class CallGlance:
    """A call's glance: the mail digest's counts, read when the call starts (a quick local read), and the calendar's,
    read meanwhile; each line on its own, so a slow or failing calendar never holds back the mail counts. Her offer of
    the rundown: the first of her answers given with the glance that asks something (the glance's rule: offer it
    once); a yes right after it reads the day (`rundown_reads`), so once a call at most."""

    def __init__(self, at: str, mail: str, calendar: asyncio.Task[str] | None) -> None:
        self.at = at
        self.mail = mail
        self.calendar = calendar
        self.waited = False  # the first turn waited for the calendar once; later turns take it when it is ready
        self.offered = False  # she asked something with the glance: her offer, made once a call
        self.offer = ""  # that answer, until the next turn takes it

    def note(self, answer: str) -> None:
        """Her answer given with the glance: the first that asks something is her offer."""
        if not self.offered and asks(answer):
            self.offered, self.offer = True, answer

    def take_offer(self) -> str:
        """Her offer, for the turn right after it only ("" for any other turn)."""
        offer, self.offer = self.offer, ""
        return offer

    def calendar_done(self) -> bool:
        return self.calendar is None or self.calendar.done()

    def lines(self) -> list[str]:
        """The lines ready now: the calendar's (once read), then the mail digest's."""
        calendar = self.calendar
        read = calendar.result() if calendar is not None and calendar.done() and not calendar.cancelled() else ""
        return [line for line in (read, self.mail) if line]


class CallHost:
    """The app's side of a call: `calls.CallManager` asks it for each turn, the notes and the summary."""

    def __init__(self, app: App, *, glance_wait: float, warm_up_seconds: float, warm_up_glance: float) -> None:
        self.app = app
        # app.py's GLANCE_WAIT_SECONDS, WARM_UP_SECONDS and WARM_UP_GLANCE_SECONDS (what they say is there)
        self.glance_wait = glance_wait
        self.warm_up_seconds = warm_up_seconds
        self.warm_up_glance = warm_up_glance
        self.calls = 0  # calls started: a call's warm-up reads only while its own call is on
        # a call's glance: today's calendar and mail digest as numbers and times, read when an ordinary call starts
        self.glance: CallGlance | None = None
        # [chords]: a call's chords results with each turn's Sources note, for the message after the call; then the
        # last call's, which its summary is checked against
        self.chords_in_call: list[tuple[chords.Result, str]] = []
        self.chords_after: list[chords.Result] = []

    async def calls_forever(self) -> None:
        """The call loop never takes the bridge down (a TaskGroup task that raises would)."""
        assert self.app.calls is not None
        while True:
            try:
                await self.app.calls.run_forever()
            except Exception:
                log.exception("calls: the call loop failed; starting it again")
            await asyncio.sleep(5)

    def call_started(self, aci: str, *, practice: bool = False) -> None:
        """Texts wait until the call ends; idle learning, mail sorting and her own messages keep away from Gemma. An
        ordinary call starts reading today's calendar and mail digest as numbers and times (the glance); a practice
        call reads neither."""
        self.app._no_call.clear()
        self.calls += 1
        self.chords_in_call = []  # [chords]: this call's results, for the message after it
        self.app.profiler.note_activity()
        if self.app.initiative is not None:
            self.app.initiative.note_activity()
        if self.app.devices:
            self.app.devices.cancel()
        self.drop_glance()
        if not practice and (self.app.calendar is not None or self.app.mail_digest is not None):
            at = local_hhmm(self.app._clock(), self.app.cfg.timezone)
            calendar = None
            if self.app.calendar is not None:
                calendar = asyncio.create_task(self.calendar_glance(self.app.calendar), name="call-glance")
            self.glance = CallGlance(at, self.mail_glance(), calendar)

    def call_ended(self) -> None:
        self.drop_glance()  # the next call counts again
        if self.app.devices:
            self.app.devices.cancel()  # a question asked on the call is not answered by a later text
        self.app._no_call.set()
        self.app.profiler.note_activity()
        if self.app.reminder_scheduler is not None:
            self.app.reminder_scheduler.notify()  # reminders that waited for the call go out now

    async def call_turn(
        self,
        aci: str,
        text: str,
        on_tools: Callable[[], None],
        *,
        practice: bool = False,
        on_retry: Callable[[], None] | None = None,
    ) -> Turn:
        """One thing the user said on a call: stored like a message (answered at once), then a waiting device's
        yes (code) or Gemma with the same tools as text; device notes and profile notes go out as texts. A turn of a
        practice call (`practice`, the call's own flag: only a call the user made during practice) goes to practice;
        a call she placed for a reminder is an ordinary call, even during practice. `on_retry`: told once when
        Gemma's first try came back empty, before it is asked again (the call says an acknowledgement)."""
        self.app.profiler.note_activity()
        await self.app._worker_idle.wait()  # a text that was being answered when the call came in finishes first
        session = self.app.babble.running(aci) if practice else None
        if session is not None:  # a practice call: its own conversation, no tools, no device yes
            return await self.practice_call_turn(aci, text, session, on_retry)
        conversation_id = self.app.store.current_conversation().id
        said = f"{prompts.CALL_MARK} {text}"
        said_id = self.app.store.add_call_turn(conversation_id, aci, said)
        state = self.glance
        offer = state.take_offer() if state is not None else ""  # her offer counts only for the turn right after it
        if self.app.devices is not None and self.app.devices.pending:
            confirmed = await self.app.devices.confirm(said, aci)
            if confirmed is not None:
                await self.call_notes(aci, conversation_id, said_id, [confirmed])
                return Turn("", code_text=speakable(confirmed))  # code's words (English)
        message = self.app.store.message(said_id)
        conversation = self.app.context.enforce_limit(self.app.store.conversation(conversation_id))
        glance = await self.glance_now()
        reads = self.rundown(offer, said) if glance else ()
        try:
            reply = await self.app.brain.answer(
                conversation,
                message,
                max_tokens=CALL_MAX_TOKENS,
                on_tools=on_tools,
                glance=glance,
                reads=reads,
                on_retry=on_retry,
                chords_before=[result for result, _ in self.chords_in_call],
            )
        except GemmaError as exc:
            log.error("call turn: Gemma failed: %s", exc)
            await self.call_drop_waiting(aci)  # the user never heard a question; a device that switched is reported
            if isinstance(exc, ModelPausedError):
                return Turn("", code_text=self.app._paused_spoken())
            return Turn(prompts.call_lines(self.app.language, name=self.app.cfg.name).lost)
        # cut off at the token limit: never stop mid-sentence. Code's line why no reminder was set (her words about it
        # are left out of the reply) is said in English after hers and any question, and kept
        said_text = whole_sentences(reply.hers)
        code_line = speakable(reply.not_set_spoken) if reply.not_set else ""
        asks_yes = bool(self.app.devices and self.app.devices.pending)
        question = ""
        if (
            asks_yes and self.app.devices is not None
        ):  # the caller hears exactly what a yes will do (the note is a text)
            question = speakable(self.app.devices.question())
            if (
                self.app.language != ENGLISH
            ):  # Gemma may claim it is done already: only code's question is said and kept
                said_text = ""
        kept = " ".join(part for part in (said_text, question, code_line) if part)
        if glance and state is not None and state is self.glance:
            state.note(said_text)  # her offer of the rundown: a yes to it next turn reads the day
        reply_id = self.app.store.add_call_reply(conversation_id, aci, kept, said_id)
        device_note = self.app.devices.take_note() if self.app.devices else ""
        # [chords]: the chords note is not sent during the call: said now, texted in one message after it
        self.chords_in_call += [(result, reply.sources) for result in reply.chords_results]
        notes = [note for note in (reply.sources, device_note, reply.notice, reply.reminder, reply.device) if note]
        outside = {reply.sources} | ({reply.notice} if reply.read_outside else set())  # quoting outside text
        await self.call_notes(aci, conversation_id, said_id, notes, outside=outside)
        log.info("call turn %d answered: %d tool calls", said_id, reply.tool_calls)
        spoken = (reply.chords_spoken, reply.device_spoken, question, code_line, reply.reminder_spoken)
        code_text = " ".join(line for line in spoken if line)  # English
        return Turn(said_text, reply_id=reply_id, asks_yes=asks_yes, end_call=reply.end_call, code_text=code_text)

    async def call_warm_up(self, aci: str, *, practice: bool = False) -> None:
        """The model's warm-up for a call that starts (CallManager: while it rings, or once a call she placed is
        picked up): Gemma loads and reads the first turn's request up to the caller's words (Brain.warm_up), built
        after that turn's own waits (a text being answered finishes first; the glance's calendar at most
        WARM_UP_GLANCE_SECONDS, leaving the turn its own wait), so the turn finds its prefix in Ollama's cache. A practice
        call (the call's own flag, with practice still running) warms the practice request. Nothing without Ollama
        itself (`warm_up`), and nothing once its call ended (a later call's start too) or /lock came meanwhile. The read
        is cut off after WARM_UP_SECONDS. One log line, metadata only; it never raises."""
        if not self.app._warms_up:
            return
        call = self.calls  # the call it is for, taken before any wait
        try:
            await self.app._worker_idle.wait()  # as call_turn: that text's reply is in the turn's history
            session = self.app.babble.running(aci) if practice else None
            glance = await self.glance_now(warm_up=True) if session is None else ""
            if self.app._no_call.is_set() or self.calls != call or self.app.guard.locked:
                return  # its call ended (another may have started), or /lock: Gemma is not asked
            started = time.monotonic()
            if session is not None:
                conversation = self.app.store.conversation(session.conversation_id)
                warming = self.app.brain.warm_up(conversation, practice=self.app.babble.turn(spoken=True))
            else:
                warming = self.app.brain.warm_up(self.app.store.current_conversation(), glance=glance)
            completion = await asyncio.wait_for(warming, self.warm_up_seconds)
        except TimeoutError:
            log.warning("calls: model warm-up failed (timeout)")
            return
        except Exception as exc:  # the call goes on as without it: one line, the kind only
            log.warning("calls: model warm-up failed (%s)", type(exc).__name__)
            return
        tokens = f" (prompt {completion.prompt_tokens:,} tokens)" if completion.prompt_tokens else ""
        log.info("calls: model warmed up in %.1f s%s", time.monotonic() - started, tokens)

    async def calendar_glance(self, calendar: CalendarTools) -> str:
        """Today's calendar as numbers and times only, for her input on this call (never a title, place or calendar
        name: that text would make the whole call outside text); any failure is "not read"."""
        try:
            return await calendar.glance()
        except Exception:  # a glance is a courtesy: the call goes on without it
            log.exception("calls: the calendar's glance failed")
            return GLANCE_NOT_READ

    def mail_glance(self) -> str:
        """The mail digest as numbers only (never a sender or subject), a quick local read; "" when it fails."""
        if self.app.mail_digest is None:
            return ""
        try:
            return self.app.mail_digest.glance()
        except Exception:
            log.exception("calls: the mail digest's glance failed")
            return ""

    async def glance_now(self, *, warm_up: bool = False) -> str:
        """The call's glance as far as it is ready: the mail counts at once; the first turn waits for the calendar
        at most GLANCE_WAIT_SECONDS, later turns take it once read. The call's warm-up (`warm_up`) waits for it at
        most WARM_UP_GLANCE_SECONDS, so the first turn, some 20 s later, finds the glance the warm-up read, and leaves
        that turn its own wait. The call's end drops it."""
        glance = self.glance
        if glance is None:
            return ""
        if glance.calendar is not None and not glance.calendar.done() and not glance.waited:
            if not warm_up:
                glance.waited = True
            wait = self.warm_up_glance if warm_up else self.glance_wait
            # asyncio.wait neither cancels it nor raises when the call's end cancels it meanwhile
            await asyncio.wait({glance.calendar}, timeout=wait)
        if self.glance is not glance:  # the call ended meanwhile
            return ""
        return prompts.call_glance(
            glance.lines(), at=glance.at, calendar=self.app.calendar is not None, mail=self.app.mail_digest is not None
        )

    def rundown(self, offer: str, said: str) -> tuple[tuple[str, str], ...]:
        """The reads for a yes to her offer of the rundown, said right after it (`rundown_reads`)."""
        reads = rundown_reads(
            offer,
            said,
            self.app.language,
            calendar=self.app.calendar is not None,
            mail=self.app.mail_digest is not None,
        )
        if reads:
            log.info("call: a yes to the rundown; code reads %s", ", ".join(name for name, _ in reads))
        return reads

    def drop_glance(self) -> None:
        if self.glance is not None and self.glance.calendar is not None:
            self.glance.calendar.cancel()
        self.glance = None

    async def practice_call_turn(
        self, aci: str, text: str, practice: Session, on_retry: Callable[[], None] | None = None
    ) -> Turn:
        """What the user said on a practice call: in the practice conversation, answered with the practice rules (no
        tools, no "→" line). A claimed reminder still gets code's note and line."""
        conversation_id = practice.conversation_id
        said_id = self.app.store.add_call_turn(conversation_id, aci, f"{prompts.CALL_MARK} {text}")
        message = self.app.store.message(said_id)
        conversation = self.app.context.enforce_limit(self.app.store.conversation(conversation_id))
        try:
            reply = await self.app.brain.answer(
                conversation,
                message,
                max_tokens=CALL_MAX_TOKENS,
                practice=self.app.babble.turn(spoken=True),
                on_retry=on_retry,
            )
        except GemmaError as exc:
            log.error("practice call turn: Gemma failed: %s", exc)
            if isinstance(exc, ModelPausedError):  # as on an ordinary call: code's line (English), never the model's
                return Turn("", code_text=self.app._paused_spoken())
            return Turn(prompts.call_lines(practice.code, name=self.app.cfg.name).lost)
        said_text = whole_sentences(reply.text)
        reply_id = self.app.store.add_call_reply(conversation_id, aci, said_text, said_id)
        if reply.reminder:
            await self.call_notes(aci, conversation_id, said_id, [reply.reminder])
        log.info("practice call turn %d answered", said_id)
        return Turn(said_text, reply_id=reply_id, end_call=reply.end_call, code_text=reply.reminder_spoken)

    async def practice_call_ended(self, aci: str) -> None:
        """A practice call the user spoke in ended: practice ends and its recap goes out at once (no quiet-hours wait),
        instead of the call summary. Texts typed during the call wait in the queue: they are answered first, as
        practice turns (the recap reads them), for at most PRACTICE_DRAIN_S. A plain /babble resumes it."""
        if self.app.babble.running(aci) is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.app.queue.join(), PRACTICE_DRAIN_S)
        if not self.app._no_call.is_set():
            return  # the user called again meanwhile: that practice call's end ends practice
        ended = self.app.babble.end("call")
        if ended is not None:
            await self.app.babble.finish(ended)

    async def call_drop_waiting(self, aci: str) -> None:
        """Drop a device action waiting for a yes; what did switch in the meantime is still a note (as in text)."""
        if self.app.devices is None:
            return
        self.app.devices.cancel()
        note = self.app.devices.take_note()
        if note:
            conversation_id = self.app.store.current_conversation().id
            await self.call_notes(aci, conversation_id, None, [note])

    def call_interrupted(self, reply_id: int, heard: str) -> None:
        """The user talked over the answer: history keeps what they heard of it."""
        self.app.store.set_message_text(reply_id, heard)

    async def call_voice_ready(self) -> bool:
        """The voice service can take a call now (both engines ready)."""
        return self.app._call_voice is not None and voice_ready(await self.app._call_voice.health())

    async def call_note(self, aci: str, text: str, *, outside: bool = False) -> None:
        conversation_id = self.app.store.current_conversation().id
        await self.app._send(self.app.store.add_outgoing(conversation_id, aci, "note", text, None, outside=outside))

    async def call_chords(self, aci: str) -> None:
        """[chords]: after the call, one message from code: every chords result of the call in call order, its fixed
        lines word for word (the exact chords, sections as found), then its source: the web page that holds the
        chords, else the turn's Sources lines. Sent only when a transpose ran; stored as a note quoting outside text.
        The results wait in memory for this call only (a restart during the call loses them: a known gap)."""
        results, self.chords_in_call = self.chords_in_call, []
        self.chords_after = [result for result, _ in results if result.ok]  # the summary's check reads them
        if not any(result.moved for result, _ in results):
            return
        parts = []
        for result, sources in results:
            if not result.ok:
                continue
            if result.source is not None:
                source = f"\nSource: {result.source[0]}: {result.source[1]}"
            else:
                listed = [line for line in sources.splitlines()[1:] if line.strip()]
                source = "\nSources:\n" + "\n".join(listed) if listed else ""
            parts.append(result.lines + source)
        await self.call_note(aci, f"{prompts.CHORDS_AFTER_CALL}\n" + "\n\n".join(parts), outside=True)  # its source
        log.info("calls: the chords message after the call sent (%d results)", len(parts))

    async def call_summary(self, aci: str, lines: list[str], minutes: int) -> None:
        """A short summary of the call as a note (in her history as code's note quoting outside text: Gemma wrote it,
        and the call may have read some). After a call with a chords result, a
        summary sentence with another chord is left out: code's message holds the exact ones."""
        transcript = "\n".join(lines)[-12_000:]
        messages = [
            {"role": "system", "content": prompts.CALL_SUMMARY_RULES.format(name=self.app.cfg.name)},
            {"role": "user", "content": transcript},
        ]
        try:
            completion = await self.app.gemma.chat(messages, max_tokens=300)
        except GemmaError as exc:
            log.warning("call summary: Gemma failed: %s", exc)
            return
        summary = completion.content.strip()
        if summary and self.chords_after:
            checked = chords.check(summary, results=self.chords_after, sources=[], language=self.app.language)
            if checked.left_out:
                log.warning("call summary: %d sentences with chords not worked out left out", checked.left_out)
                summary = checked.text
        if summary:  # Gemma's words about a call that may have read outside text: marked so (code_notes)
            await self.call_note(aci, prompts.CALL_SUMMARY.format(minutes=minutes, summary=summary), outside=True)

    async def call_notes(
        self, aci: str, conversation_id: int, reply_to: int | None, notes: list[str], *, outside: Collection[str] = ()
    ) -> None:
        """Code's notes after a call turn; `outside`: the ones that quote outside text (code_notes)."""
        for note in notes:
            out_id = self.app.store.add_outgoing(conversation_id, aci, "note", note, reply_to, outside=note in outside)
            await self.app._send(out_id)
