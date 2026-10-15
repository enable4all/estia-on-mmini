"""Decide what to do with an incoming Signal envelope (signal-cli's JSON shape).

Only direct text, photo and voice messages and PDFs from allowed accounts are used. Everything else is
ignored, answered with a short note (files, stickers) or logged (strangers). Attachments that will
not be used (strangers', unsupported types) are listed for deletion at once.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from local_assistant.signal_io import device_number


class Kind(StrEnum):
    TEXT = "text"
    PHOTO = "photo"
    VOICE = "voice"
    DOCUMENT = "document"  # a PDF (by its type here; its first bytes decide when it is saved)
    COMMAND = "command"
    UNSUPPORTED = "unsupported"
    IGNORE = "ignore"
    STRANGER = "stranger"


@dataclass(frozen=True)
class AttachmentRef:
    id: str  # signal-cli's file name in its attachments folder
    content_type: str  # as claimed by the sender; the bytes decide (photos.sniff)
    size: int
    # Signal's voice-note flag: True recorded in Signal, False an audio file shared from elsewhere, None not given
    voice_note: bool | None = None
    filename: str = ""  # the name it was sent with (a PDF is saved under a clean form of it); "" when none


@dataclass(frozen=True)
class Decision:
    kind: Kind
    text: str = ""
    reason: str = ""
    photos: tuple[AttachmentRef, ...] = ()
    audio: tuple[AttachmentRef, ...] = ()  # one voice note; its bytes decide (voice.sniff)
    documents: tuple[AttachmentRef, ...] = ()  # PDFs; their first bytes decide (documents.PDF_START)
    discard: tuple[AttachmentRef, ...] = ()  # files to delete at once
    device: int | None = None  # the sender's device that sent it (1: their phone, 2 and up: linked); None: not named


IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/heic", "image/heif", "image/webp", "image/gif"})
PDF_TYPES = frozenset({"application/pdf", "application/x-pdf"})
LONG_TEXT = "text/x-signal-plain"
# a file without a real type may be a voice note too (OpenClaw #48614): the bytes decide later
UNTYPED = frozenset({"", "application/octet-stream"})
# A plain file name: the app deletes signal-cli's copy by this name, so no paths and no hidden names.
# signal-cli keeps the sender's extension, e.g. "<id>.jpeg (1)": spaces and brackets are fine, "/" never.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._ ()-]{0,199}")


_NON_DATA = ("editMessage", "storyMessage", "syncMessage", "callMessage", "receiptMessage", "typingMessage")
_IGNORED_DATA = (
    "reaction",
    "remoteDelete",
    "groupInfo",
    "storyContext",
    "pollVote",
    "pollTerminate",
    "pinMessage",
    "unpinMessage",
    "groupCallUpdate",
)
_UNSUPPORTED_DATA = ("sticker", "contacts", "pollCreate", "payment")


def sender_aci(envelope: Mapping[str, Any]) -> str | None:
    value = envelope.get("sourceUuid")
    return value.lower() if isinstance(value, str) and value else None


def _is_long_text(item: object) -> bool:
    return isinstance(item, Mapping) and str(item.get("contentType", "")).lower() == LONG_TEXT


def attachments(data: Mapping[str, Any]) -> tuple[AttachmentRef, ...]:
    """The message's attachments that have a safe file name (others are never touched)."""
    raw = data.get("attachments")
    if not isinstance(raw, list):
        return ()
    refs = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        ident = item.get("id")
        if not isinstance(ident, str) or not _SAFE_ID.fullmatch(ident):
            continue
        content_type = item.get("contentType")
        size = item.get("size")
        flag = item.get("isVoiceNote", item.get("voiceNote"))
        filename = item.get("filename")
        content_type = content_type.lower() if isinstance(content_type, str) else ""
        refs.append(
            AttachmentRef(
                ident,
                content_type,
                size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else 0,
                flag if isinstance(flag, bool) else None,
                # a PDF's own name (it is saved under a clean form of it); no other file's name is kept
                filename[:255] if isinstance(filename, str) and content_type in PDF_TYPES else "",
            )
        )
    return tuple(refs)


def source_device(envelope: Mapping[str, Any]) -> int | None:
    """The sender's device that sent the envelope (signal-cli's sourceDevice), or None when it names none."""
    return device_number(envelope.get("sourceDevice"))


def classify(envelope: Mapping[str, Any], allowed: frozenset[str]) -> Decision:
    """What to do with the envelope, and which of the sender's devices sent it."""
    return dataclasses.replace(_classify(envelope, allowed), device=source_device(envelope))


def _classify(envelope: Mapping[str, Any], allowed: frozenset[str]) -> Decision:
    aci = sender_aci(envelope)
    data = envelope.get("dataMessage")
    if aci is None or aci not in allowed:
        if isinstance(data, Mapping):
            return Decision(Kind.STRANGER, reason="sender not allowed", discard=attachments(data))
        return Decision(Kind.IGNORE, reason="sender not allowed, no message")
    if not isinstance(data, Mapping):
        present = [key for key in _NON_DATA if envelope.get(key) is not None]
        return Decision(Kind.IGNORE, reason=",".join(present) or "no message")
    refs = attachments(data)
    for key in _IGNORED_DATA:
        if data.get(key):
            return Decision(Kind.IGNORE, reason=key, discard=refs)
    if data.get("isExpirationUpdate") or data.get("isEndSession"):
        return Decision(Kind.IGNORE, reason="control message", discard=refs)
    for key in _UNSUPPORTED_DATA:
        if data.get(key):
            return Decision(Kind.UNSUPPORTED, reason=key, discard=refs)
    # signal-cli puts a long text (over ~2,000 characters) in "message" and still lists its text file
    long_texts = tuple(ref for ref in refs if ref.content_type == LONG_TEXT)
    files = data.get("attachments")
    if isinstance(files, list) and any(not _is_long_text(item) for item in files):
        photos = tuple(ref for ref in refs if ref.content_type in IMAGE_TYPES)
        others = tuple(ref for ref in refs if ref.content_type not in IMAGE_TYPES)
        caption = data.get("message")
        caption = caption.strip() if isinstance(caption, str) else ""  # the user's own words with the file
        if not photos:
            voice = next((ref for ref in refs if ref.content_type.startswith("audio/")), None)
            documents = tuple(ref for ref in refs if ref.content_type in PDF_TYPES)
            if voice is None and documents:  # a recorded voice note wins, as before; PDFs win over an untyped file
                return Decision(
                    Kind.DOCUMENT,
                    text=caption,
                    documents=documents,
                    discard=tuple(ref for ref in refs if ref not in documents),
                )
            voice = voice or next((ref for ref in refs if ref.content_type in UNTYPED), None)
            if voice is None:
                return Decision(Kind.UNSUPPORTED, reason="attachments", discard=refs)
            return Decision(
                Kind.VOICE, text=caption, audio=(voice,), discard=tuple(ref for ref in refs if ref != voice)
            )
        return Decision(Kind.PHOTO, text=caption, photos=photos, discard=others)
    text = data.get("message")
    if not isinstance(text, str) or not text.strip():
        return Decision(Kind.IGNORE, reason="no text", discard=refs)
    text = text.strip()
    if text.startswith("/"):
        return Decision(Kind.COMMAND, text=text, discard=long_texts)
    return Decision(Kind.TEXT, text=text, discard=long_texts)
