from typing import Any

import pytest

from local_assistant.gate import AttachmentRef, Kind, classify, sender_aci
from tests.conftest import ME, STRANGER, T0

ALLOWED = frozenset({ME})


def envelope(source: str = ME, data: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "source": source,
        "sourceNumber": None,
        "sourceUuid": source,
        "sourceName": "Alex",
        "sourceDevice": 1,
        "timestamp": T0,
    }
    if data is not None:
        env["dataMessage"] = {"timestamp": T0, "expiresInSeconds": 0, "viewOnce": False, **data}
    env.update(extra)
    return env


def test_text_from_the_user() -> None:
    decision = classify(envelope(data={"message": "  hello Gemma \n"}), ALLOWED)
    assert (decision.kind, decision.text) == (Kind.TEXT, "hello Gemma")


def test_command() -> None:
    decision = classify(envelope(data={"message": "/profile"}), ALLOWED)
    assert (decision.kind, decision.text) == (Kind.COMMAND, "/profile")


def test_uppercase_aci_still_matches() -> None:
    assert classify(envelope(source=ME.upper(), data={"message": "hi"}), ALLOWED).kind is Kind.TEXT
    assert sender_aci(envelope(source=ME.upper())) == ME


def test_stranger_with_a_message() -> None:
    assert classify(envelope(source=STRANGER, data={"message": "hi"}), ALLOWED).kind is Kind.STRANGER


def test_stranger_without_message_is_just_ignored() -> None:
    env = envelope(source=STRANGER, receiptMessage={"isRead": True})
    assert classify(env, ALLOWED).kind is Kind.IGNORE


def test_missing_sender_is_a_stranger() -> None:
    env = envelope(data={"message": "hi"})
    del env["sourceUuid"]
    assert classify(env, ALLOWED).kind is Kind.STRANGER


@pytest.mark.parametrize(
    "data",
    [
        {"message": "look", "attachments": [{"contentType": "application/zip", "id": "doc.zip", "size": 9}]},
        {"attachments": [{"contentType": "audio/aac"}]},
        {"sticker": {"packId": "x", "stickerId": 1}},
        {"contacts": [{"name": {"given": "A"}}]},
    ],
)
def test_unsupported_content(data: dict[str, Any]) -> None:
    assert classify(envelope(data=data), ALLOWED).kind is Kind.UNSUPPORTED


@pytest.mark.parametrize(
    "data",
    [
        {"reaction": {"emoji": "👍", "targetSentTimestamp": 1}},
        {"remoteDelete": {"timestamp": 1}},
        {"message": "in a group", "groupInfo": {"groupId": "g"}},
        {"message": "story reply", "storyContext": {"authorNumber": "x"}},
        {"isExpirationUpdate": True},
        {"message": None},
        {"message": "   "},
    ],
)
def test_ignored_data_messages(data: dict[str, Any]) -> None:
    assert classify(envelope(data=data), ALLOWED).kind is Kind.IGNORE


@pytest.mark.parametrize(
    "extra",
    [
        {"receiptMessage": {"isRead": True}},
        {"typingMessage": {"action": "STARTED"}},
        {"editMessage": {"targetSentTimestamp": 1, "dataMessage": {"message": "edited"}}},
        {"syncMessage": {}},
    ],
)
def test_non_message_envelopes_are_ignored(extra: dict[str, Any]) -> None:
    assert classify(envelope(**extra), ALLOWED).kind is Kind.IGNORE


JPEG = {"contentType": "image/jpeg", "filename": "IMG_1.jpg", "id": "abc123.jpg", "size": 1234}
HEIC = {"contentType": "image/heic", "id": "def456.heic", "size": 2_000_000}
VOICE = {"contentType": "audio/aac", "id": "voice1.aac", "size": 5000}


def test_photo_with_caption() -> None:
    decision = classify(envelope(data={"message": " what is this? ", "attachments": [JPEG]}), ALLOWED)
    assert decision.kind is Kind.PHOTO
    assert decision.text == "what is this?"
    assert decision.photos == (AttachmentRef("abc123.jpg", "image/jpeg", 1234),)
    assert decision.discard == ()


def test_photo_without_caption_and_heic() -> None:
    decision = classify(envelope(data={"attachments": [HEIC]}), ALLOWED)
    assert (decision.kind, decision.text) == (Kind.PHOTO, "")
    assert decision.photos == (AttachmentRef("def456.heic", "image/heic", 2_000_000),)


def test_a_caption_starting_with_a_slash_is_not_a_command() -> None:
    assert classify(envelope(data={"message": "/profile", "attachments": [JPEG]}), ALLOWED).kind is Kind.PHOTO


def test_other_files_next_to_a_photo_are_discarded() -> None:
    decision = classify(envelope(data={"attachments": [JPEG, VOICE]}), ALLOWED)
    assert decision.kind is Kind.PHOTO
    assert decision.discard == (AttachmentRef("voice1.aac", "audio/aac", 5000),)


def test_a_voice_note() -> None:
    decision = classify(envelope(data={"attachments": [VOICE]}), ALLOWED)
    assert decision.kind is Kind.VOICE
    assert decision.audio == (AttachmentRef("voice1.aac", "audio/aac", 5000),)
    assert decision.discard == ()


@pytest.mark.parametrize("item", [{"id": "x1", "size": 4000}, {"contentType": "application/octet-stream", "id": "x2"}])
def test_a_file_without_a_real_type_may_be_voice_the_bytes_decide(item: dict[str, Any]) -> None:
    assert classify(envelope(data={"attachments": [item]}), ALLOWED).kind is Kind.VOICE


def test_one_voice_note_per_message_other_files_are_discarded() -> None:
    pdf = {"contentType": "application/pdf", "id": "doc.pdf", "size": 100}
    second = {"contentType": "audio/mp4", "id": "voice2.m4a", "size": 100}
    decision = classify(envelope(data={"attachments": [pdf, VOICE, second]}), ALLOWED)
    assert decision.kind is Kind.VOICE
    assert decision.audio == (AttachmentRef("voice1.aac", "audio/aac", 5000),)
    assert decision.discard == (
        AttachmentRef("doc.pdf", "application/pdf", 100),
        AttachmentRef("voice2.m4a", "audio/mp4", 100),
    )


def test_a_strangers_photo_is_discarded() -> None:
    decision = classify(envelope(source=STRANGER, data={"message": "hi", "attachments": [JPEG]}), ALLOWED)
    assert decision.kind is Kind.STRANGER
    assert decision.photos == ()
    assert decision.discard == (AttachmentRef("abc123.jpg", "image/jpeg", 1234),)


def test_a_sticker_with_a_file_is_unsupported_and_discarded() -> None:
    decision = classify(envelope(data={"sticker": {"packId": "x"}, "attachments": [JPEG]}), ALLOWED)
    assert decision.kind is Kind.UNSUPPORTED
    assert decision.discard == (AttachmentRef("abc123.jpg", "image/jpeg", 1234),)


@pytest.mark.parametrize("bad_id", ["../evil.jpg", "a/b.jpg", ".hidden", "", None, 42, "x" * 300])
def test_attachments_without_a_safe_id_are_never_touched(bad_id: Any) -> None:
    decision = classify(envelope(data={"attachments": [{**JPEG, "id": bad_id}]}), ALLOWED)
    assert decision.kind is Kind.UNSUPPORTED
    assert (decision.photos, decision.discard) == ((), ())


def test_a_missing_size_counts_as_zero() -> None:
    decision = classify(envelope(data={"attachments": [{"contentType": "image/png", "id": "p.png"}]}), ALLOWED)
    assert decision.photos == (AttachmentRef("p.png", "image/png", 0),)


LONG_TEXT = {"contentType": "text/x-signal-plain", "id": "long1.txt", "size": 3_500}


def test_a_long_text_is_text_and_its_file_is_discarded() -> None:
    # signal-cli puts the whole long text in "message" but still lists the text file as an attachment
    decision = classify(envelope(data={"message": "word " * 700, "attachments": [LONG_TEXT]}), ALLOWED)
    assert (decision.kind, decision.text) == (Kind.TEXT, ("word " * 700).strip())
    assert decision.discard == (AttachmentRef("long1.txt", "text/x-signal-plain", 3_500),)


def test_a_photo_with_a_long_caption() -> None:
    decision = classify(envelope(data={"message": "caption " * 300, "attachments": [JPEG, LONG_TEXT]}), ALLOWED)
    assert decision.kind is Kind.PHOTO
    assert decision.photos == (AttachmentRef("abc123.jpg", "image/jpeg", 1234),)
    assert decision.discard == (AttachmentRef("long1.txt", "text/x-signal-plain", 3_500),)


def test_an_id_with_a_trailing_newline_is_not_safe() -> None:
    decision = classify(envelope(data={"attachments": [{**JPEG, "id": "abc123.jpg\n"}]}), ALLOWED)
    assert decision.photos == ()


def test_an_id_with_a_copy_suffix_is_a_photo() -> None:
    # signal-cli takes the extension from the sender's file name: "IMG.jpeg (1)" gives "<id>.jpeg (1)"
    decision = classify(envelope(data={"attachments": [{**JPEG, "id": "XYZ123.jpeg (1)"}]}), ALLOWED)
    assert decision.photos == (AttachmentRef("XYZ123.jpeg (1)", "image/jpeg", 1234),)


@pytest.mark.parametrize(
    ("flag", "voice_note"),
    [({"isVoiceNote": True}, True), ({"voiceNote": True}, True), ({"isVoiceNote": False}, False), ({}, None)],
)
def test_signal_says_whether_audio_was_recorded_as_a_voice_note(flag: dict[str, bool], voice_note: bool | None) -> None:
    # D4: an audio file shared from elsewhere is not the user's voice; without the flag nothing changes
    decision = classify(envelope(data={"attachments": [{**VOICE, **flag}]}), ALLOWED)
    assert decision.kind is Kind.VOICE and decision.audio[0].voice_note is voice_note


def test_words_typed_with_an_audio_file_are_kept() -> None:
    decision = classify(
        envelope(data={"attachments": [{**VOICE, "isVoiceNote": False}], "message": " who is this? "}), ALLOWED
    )
    assert decision.kind is Kind.VOICE and decision.text == "who is this?"


# hardening: which of the sender's devices sent it (1: their phone; linked devices 2 and up)


@pytest.mark.parametrize(
    ("value", "device"), [(1, 1), (2, 2), (17, 17), (0, None), (-1, None), ("2", None), (True, None), (None, None)]
)
def test_the_decision_names_the_sending_device(value: object, device: int | None) -> None:
    for env in (envelope(data={"message": "hi"}), envelope(data={"message": "/devices"}), envelope(source=STRANGER)):
        env["sourceDevice"] = value
        assert classify(env, ALLOWED).device == device


def test_without_a_device_field_the_device_is_unknown() -> None:
    env = envelope(data={"message": "hi"})
    del env["sourceDevice"]
    decision = classify(env, ALLOWED)
    assert (decision.kind, decision.device) == (Kind.TEXT, None)
    assert classify(envelope(data={"message": "hi"}, sourceDevice=3), ALLOWED).device == 3


def test_a_pdf_is_a_document_with_its_name_and_the_words_sent_with_it() -> None:
    pdf = {"contentType": "application/pdf", "id": "a1.pdf", "size": 9000, "filename": "Lease 2026.pdf"}
    other = {"contentType": "application/zip", "id": "a2.zip", "size": 10}
    decision = classify(envelope(data={"message": " what about pets? ", "attachments": [pdf, other]}), ALLOWED)
    assert (decision.kind, decision.text) == (Kind.DOCUMENT, "what about pets?")
    assert decision.documents == (AttachmentRef("a1.pdf", "application/pdf", 9000, filename="Lease 2026.pdf"),)
    assert [ref.id for ref in decision.discard] == ["a2.zip"]  # deleted at once, as next to a photo


def test_several_pdfs_and_the_old_pdf_type() -> None:
    files = [
        {"contentType": "application/pdf", "id": "a1.pdf", "size": 1, "filename": "one.pdf"},
        {"contentType": "application/x-pdf", "id": "a2.pdf", "size": 2},
        {"contentType": "application/pdf", "id": "a3.pdf", "size": 3, "filename": 7},
    ]
    decision = classify(envelope(data={"attachments": files}), ALLOWED)
    assert decision.kind is Kind.DOCUMENT and decision.text == ""
    assert [(ref.id, ref.filename) for ref in decision.documents] == [
        ("a1.pdf", "one.pdf"),
        ("a2.pdf", ""),
        ("a3.pdf", ""),
    ]


def test_a_pdf_next_to_a_photo_is_discarded_and_a_strangers_pdf_too() -> None:
    files = [
        {"contentType": "image/jpeg", "id": "p1.jpeg", "size": 1},
        {"contentType": "application/pdf", "id": "a1.pdf", "size": 2},
    ]
    photo = classify(envelope(data={"attachments": files}), ALLOWED)
    assert photo.kind is Kind.PHOTO and [ref.id for ref in photo.discard] == ["a1.pdf"]
    stranger = classify(envelope(source=STRANGER, data={"attachments": files[1:]}), ALLOWED)
    assert stranger.kind is Kind.STRANGER and [ref.id for ref in stranger.discard] == ["a1.pdf"]
