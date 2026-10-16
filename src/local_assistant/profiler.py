"""Idle-time learning (profile updates) and conversation summaries (spec section 6).

Runs only when the chat has been quiet (Hermes: on local models, background reviews wait for an
idle GPU). A new message cancels a running pass, which frees Ollama's single slot; nothing is
written until a pass finishes, so the aborted work is simply redone later.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.context import ContextBuilder
from local_assistant.fetcher import Page
from local_assistant.gemma import GemmaClient, GemmaError
from local_assistant.profile import ApplyResult, Op, Profile
from local_assistant.store import Message, SourceKind, Store
from local_assistant.timefmt import local_stamp, utc_now

log = logging.getLogger(__name__)

LAST_LEARNED_KEY = "last_learned_message_id"
LAST_RUN_KEY = "last_learning_run"
UNANNOUNCED_KEY = "unannounced_learned"  # facts learned in the background, announced with the next reply
FAILURES_KEY = "learn_failures"  # "<learned-up-to pointer>:<failed passes on the batch after it>"
_ACTIONS = ("add", "replace", "remove")


def _count(result: ApplyResult) -> int:
    return len(result.added) + len(result.replaced) + len(result.removed)


class LearnError(Exception):
    """Gemma's learning answer could not be used."""


class CutOffError(LearnError):
    """Gemma's learning answer was cut off at its token limit: asked again in halves (Profiler._learn_halves)."""


class IdleHook(Protocol):
    """More idle work after learning, in the same pass (followups.FollowUps: plans the user stated)."""

    def has_work(self) -> bool: ...

    async def run(self) -> object: ...


class Profiler:
    def __init__(
        self,
        store: Store,
        profile: Profile,
        context: ContextBuilder,
        gemma: GemmaClient,
        tz: ZoneInfo,
        *,
        idle_seconds: float = 1_800,
        soon_seconds: float = 60,
        window_tokens: int = 12_000,
        summary_tokens: int = 500,
        batch_chars: int = 24_000,
        poll_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        busy: Callable[[], bool] = lambda: False,
        max_failures: int = 3,
        after_learning: IdleHook | None = None,
    ) -> None:
        self._store = store
        self._profile = profile
        self._context = context
        self._gemma = gemma
        self._tz = tz
        self._idle_seconds = idle_seconds
        self._soon_seconds = soon_seconds
        self._window_tokens = window_tokens
        self._summary_tokens = summary_tokens
        self._batch_chars = batch_chars
        self._poll = poll_seconds
        self._clock = clock
        self._busy = busy
        self._max_failures = max_failures
        self._after_learning = after_learning
        self._last_activity = clock()
        self._soon = False
        self._task: asyncio.Task[None] | None = None

    def note_activity(self) -> None:
        """A message came in or went out: postpone learning and abort a running pass."""
        self._last_activity = self._clock()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def request_soon(self) -> None:
        """After /new: learn from the closed conversation after a short quiet time."""
        self._soon = True
        self._last_activity = self._clock()

    def due(self) -> bool:
        if self._busy():  # the bridge is answering or has messages waiting: Gemma's slot is theirs
            return False
        wait = self._soon_seconds if self._soon else self._idle_seconds
        return self._clock() - self._last_activity >= wait and self.has_work()

    def has_work(self) -> bool:
        hook = self._after_learning is not None and self._after_learning.has_work()
        return bool(self._unlearned(limit=1)) or self._needs_summary() or hook

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(self._poll)
            if not self.due():
                continue
            self._task = asyncio.create_task(self.run_once(), name="learning-pass")
            try:
                await self._task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
                log.info("learning pass aborted by new activity; it runs again when the chat is quiet")
            except (LearnError, GemmaError) as exc:
                log.warning("learning pass failed (%s); retrying after the next quiet period", exc)
                self._last_activity = self._clock()
            except Exception:
                log.exception("learning pass crashed")
                self._last_activity = self._clock()
            finally:
                self._task = None
                self._soon = False  # one quick pass after /new, then the normal quiet-time rhythm

    async def run_once(self) -> None:
        """Learn, then the hook (the follow-ups), then summarise; a failed step does not block the next. A failure is
        raised at the end, so the next pass waits for the next quiet time."""
        error: LearnError | GemmaError | None = None
        changes = 0
        try:
            changes = await self.learn()
        except (LearnError, GemmaError) as exc:
            error = exc
        if self._after_learning is not None:
            try:
                await self._after_learning.run()
            except GemmaError as exc:
                error = error or exc
        summarized = await self.summarize_if_needed()
        if changes and not summarized and self._context.is_fresh(self._store.current_conversation()):
            self._context.refresh_snapshot()  # nothing cached yet in the new conversation
        if error is not None:
            raise error

    async def learn(self) -> int:
        """One pass over the user's unlearned messages; returns the number of profile changes."""
        after = int(self._store.get_state(LAST_LEARNED_KEY) or 0)
        fetched = self._store.user_messages_after(after, 200)
        if not fetched:
            return 0
        skip = self._profile.no_learn_ids()
        messages = [m for m in fetched if m.id not in skip]
        if not messages:  # only messages whose facts the user forgot
            with self._store.tx():
                self._advance(fetched)
            return 0
        failures = self._failures(after)
        if failures >= self._max_failures:
            log.warning("learning: message %d failed %d passes; skipped", messages[0].id, failures)
            with self._store.tx():
                self._advance(messages[:1])
            return 0
        batch = self._batch(messages, self._batch_chars >> failures)  # half the size after each failure
        skipped: list[int] = []  # the messages skipped in this pass, logged by count even when the pass then fails
        try:
            return await self._learn_halves(batch, skipped)
        except LearnError:  # a cut-off answer never comes here: the halves take it
            pointer = self._store.get_state(LAST_LEARNED_KEY) or "0"  # a half done before the failure moved it
            count = failures + 1 if pointer == str(after) else 1
            self._store.set_state(FAILURES_KEY, f"{pointer}:{count}")
            raise
        finally:
            if skipped:
                log.warning(
                    "learning: %d messages skipped: the answer for each alone was cut off at the token limit",
                    len(skipped),
                )

    async def _learn_halves(self, batch: list[Message], skipped: list[int], effort: str = "high") -> int:
        """Learn from `batch`. An answer cut off at its token limit while thinking (`effort` "high") is asked again
        once, whole, without thinking: the cut-off may be thinking that runs on (setup_local_model.md), which halving
        does not help. Cut off without thinking too, the answer itself is too long: it is asked again in two halves,
        without thinking, down to one message (as the follow-ups' extraction does). One message whose answer is still
        cut off alone without thinking is skipped (its id goes in `skipped`): the pointer moves past it, so no pass asks
        for it again. Each half moves the pointer once it is done, so a Gemma error later keeps only the rest for the
        next pass. At most 2n Gemma calls for n messages. Returns the profile changes."""
        try:
            return await self._learn_batch(batch, effort)
        except CutOffError:
            if effort != "none":
                log.warning("learning: the answer was cut off while thinking; asking again without thinking")
                return await self._learn_halves(batch, skipped, "none")
            if len(batch) == 1:
                with self._store.tx():
                    self._advance(batch)
                skipped.append(batch[0].id)
                return 0
        half = len(batch) // 2
        log.warning("learning: the answer was cut off; asking again for %d and %d messages", half, len(batch) - half)
        first = await self._learn_halves(batch[:half], skipped, "none")
        return first + await self._learn_halves(batch[half:], skipped, "none")

    async def _learn_batch(self, batch: list[Message], effort: str = "high") -> int:
        valid_ids = {message.id for message in batch}
        conversation: list[dict[str, Any]] = [
            {"role": "system", "content": prompts.PROFILER_RULES},
            {"role": "user", "content": self._learn_prompt(batch)},
        ]
        operations = await self._ask_ops(conversation, valid_ids, effort=effort)
        with self._store.tx():
            result = self._profile.apply(operations, source_kind="chat")
            if not result.over_cap_by:
                self._advance(batch, _count(result))
        if result.over_cap_by:
            conversation.append(
                {
                    "role": "user",
                    "content": prompts.OVER_CAP.format(limit=self._profile.notes_max_chars, over=result.over_cap_by),
                }
            )
            operations = await self._ask_ops(conversation, valid_ids, effort=effort)
            with self._store.tx():
                result = self._profile.apply(operations, source_kind="chat")
                self._advance(batch, _count(result))
            if result.over_cap_by:
                log.warning("profile full: a learning pass skipped changes worth %d characters", result.over_cap_by)
        for operation, reason in result.rejected:
            log.info("learning: %s rejected (%s)", operation.action, reason)
        changes = _count(result)
        log.info("learning pass: %d messages, %d changes", len(batch), changes)
        return changes

    async def summarize_if_needed(self) -> bool:
        """Summarise the older half of a long window (the last 4 messages always stay verbatim)."""
        conversation = self._store.current_conversation()
        messages = self._context.window(conversation)
        total = self._context.window_tokens_of(messages)
        if total <= self._window_tokens or len(messages) < 6:
            return False
        older: list[Message] = []
        counted = 0
        for message in messages[:-4]:
            older.append(message)
            counted += self._context.window_tokens_of([message])
            if counted >= total / 2:
                break
        name = self._context.name
        transcript = "\n".join(f"{'User' if m.direction == 'in' else name}: {m.text}" for m in older)
        rules = prompts.SUMMARY_RULES.format(name=name, words=int(self._summary_tokens * 0.6))
        request = [
            {"role": "system", "content": rules},
            {
                "role": "user",
                "content": f"PREVIOUS SUMMARY\n{conversation.summary or '(none)'}\n\nMESSAGES TO ADD\n{transcript}",
            },
        ]
        completion = await self._gemma.chat(request, max_tokens=self._summary_tokens, temperature=0)
        summary = completion.content.strip()
        if not summary:
            raise LearnError("empty summary")
        self._store.set_summary(conversation.id, summary, older[-1].seq or 0)
        self._context.refresh_snapshot()
        log.info("summarised %d messages of conversation %d", len(older), conversation.id)
        return True

    async def consolidate_for(
        self,
        fact: str,
        source_message_id: int | None,
        source_kind: SourceKind,
        source_document_id: int | None,
        allowed_url: str | None = None,
    ) -> ApplyResult:
        """remember() hit the user's part's limit: Gemma merges or shortens their facts so the new one fits (Hermes
        pattern); a fact it removes moves into her notes, never gone (owner, 16 Oct). Only the user's part is shown:
        her notes have their own limit and free nothing here.

        The user is waiting for this answer, so Gemma does not think first.
        """
        over = self._profile.used_chars() + len(fact.strip()) - self._profile.max_chars
        facts = "\n".join(f"#{f.id}: {f.text}" for f in self._profile.yours())
        prompt = prompts.CONSOLIDATE.format(
            limit=self._profile.max_chars,
            over=max(over, 1),
            fact=fact.strip(),
            source_message_id=source_message_id or 0,
        )
        conversation: list[dict[str, Any]] = [
            {"role": "system", "content": prompts.PROFILER_RULES},
            {"role": "user", "content": f"{prompt}\n\nTHE USER'S PART OF THE PROFILE (fact_id: text)\n{facts}"},
        ]
        valid = {source_message_id} if source_message_id is not None else set()
        operations = await self._ask_ops(conversation, valid, effort="none")
        operations = [
            dataclasses.replace(o, source_message_id=source_message_id) if o.action == "add" else o for o in operations
        ]
        return self._profile.apply(
            operations,
            source_kind=source_kind,
            source_document_id=source_document_id,
            allowed_url=allowed_url,  # a link fact keeps its page's own link through the strict check
            remove_to_notes=True,
        )

    async def learn_page(self, page: Page, source_message_id: int | None, document_id: int) -> ApplyResult:
        """A page the user asked to learn from: one structured Gemma call extracts the facts about the user,
        then the strict link checks apply. Deterministic where a chain of remember calls was not."""
        facts = "\n".join(f"#{f.id}: {f.text}" for f in self._profile.facts()) or "(empty)"
        content = (
            f"CURRENT PROFILE (fact_id: text)\n{facts}\n\nPAGE: {page.title}\nURL: {page.final_url}\n\n{page.text}"
        )
        completion = await self._gemma.chat(
            [{"role": "system", "content": prompts.PAGE_FACTS_RULES}, {"role": "user", "content": content}],
            max_tokens=1_500,
            reasoning_effort="none",
            temperature=0,
            json_schema=prompts.PAGE_FACTS_SCHEMA,
        )
        if completion.finish_reason == "length":
            raise LearnError("the page answer was cut off at the token limit")
        try:
            data = json.loads(completion.content)
        except json.JSONDecodeError as exc:
            raise LearnError("the page answer is not JSON") from exc
        raw = data.get("facts") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise LearnError("the page answer has no facts list")
        operations = [
            Op("add", text=item, reason="from a link the user asked to learn", source_message_id=source_message_id)
            for item in raw[:12]
            if isinstance(item, str)
        ]
        result = self._profile.apply(
            operations, source_kind="link", source_document_id=document_id, allowed_url=page.final_url
        )
        for _operation, reason in result.rejected:
            log.info("page learning: fact rejected (%s)", reason)
        return result

    def _unlearned(self, limit: int) -> list[Message]:
        after = int(self._store.get_state(LAST_LEARNED_KEY) or 0)
        return self._store.user_messages_after(after, limit)

    def _needs_summary(self) -> bool:
        conversation = self._store.current_conversation()
        return self._context.window_tokens_of(self._context.window(conversation)) > self._window_tokens

    def _failures(self, after: int) -> int:
        pointer, _, count = (self._store.get_state(FAILURES_KEY) or "").partition(":")
        return int(count) if pointer == str(after) and count.isdigit() else 0

    def _batch(self, messages: list[Message], max_chars: int) -> list[Message]:
        batch: list[Message] = []
        size = 0
        for message in messages:
            if batch and size + len(message.text) > max_chars:
                break
            batch.append(message)
            size += len(message.text)
        return batch

    def _advance(self, batch: list[Message], changes: int = 0) -> None:
        self._store.set_state(LAST_LEARNED_KEY, str(batch[-1].id))
        self._store.set_state(LAST_RUN_KEY, utc_now())
        self._store.set_state(FAILURES_KEY, "")
        if changes:
            unannounced = int(self._store.get_state(UNANNOUNCED_KEY) or 0)
            self._store.set_state(UNANNOUNCED_KEY, str(unannounced + changes))

    def _learn_prompt(self, batch: list[Message]) -> str:
        """Both parts, by number: the user's own facts (never changed by learning: `Profile.apply` keeps them) and
        her notes, which learning adds to, merges and tidies within their own limit."""
        yours = "\n".join(f"#{f.id}: {f.text}" for f in self._profile.yours()) or "(empty)"
        notes = "\n".join(f"#{f.id}: {f.text}" for f in self._profile.notes()) or "(empty)"
        lines = "\n".join(
            f"#{m.id} {local_stamp(m.signal_ts, self._tz) if m.signal_ts else ''} {m.text}" for m in batch
        )
        return (
            f"CURRENT PROFILE: THE USER'S OWN FACTS (fact_id: text; never replace or remove these)\n{yours}\n\n"
            f"CURRENT PROFILE: YOUR NOTES FROM EARLIER CHATS (fact_id: text)\n{notes}\n\n"
            f"NEW MESSAGES FROM THE USER (message id, local time, text)\n{lines}"
        )

    async def _ask_ops(self, conversation: list[dict[str, Any]], valid_ids: set[int], *, effort: str) -> list[Op]:
        completion = await self._gemma.chat(
            conversation,
            max_tokens=4_000,
            reasoning_effort=effort,
            temperature=0,
            json_schema=prompts.OPS_SCHEMA,
        )
        if completion.finish_reason == "length":
            raise CutOffError("the learning answer was cut off at the token limit")
        try:
            data = json.loads(completion.content)
        except json.JSONDecodeError as exc:
            raise LearnError("the learning answer is not JSON") from exc
        raw_operations = data.get("operations") if isinstance(data, dict) else None
        if not isinstance(raw_operations, list):
            raise LearnError("the learning answer has no operations list")
        conversation.append({"role": "assistant", "content": completion.content})
        operations: list[Op] = []
        for raw in raw_operations:
            if not isinstance(raw, dict) or raw.get("action") not in _ACTIONS:
                continue
            source = raw.get("source_message_id")
            fact_id = raw.get("fact_id")
            operations.append(
                Op(
                    action=raw["action"],
                    fact_id=fact_id if isinstance(fact_id, int) else 0,
                    text=str(raw.get("text") or ""),
                    reason=str(raw.get("reason") or "")[:200],
                    source_message_id=source if isinstance(source, int) and source in valid_ids else None,
                )
            )
        return operations
