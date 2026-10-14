import array
import json
import math
import socket
import stat
import time
from pathlib import Path

import pytest

from local_assistant.part_status import PartStatus, Record
from local_assistant.voice import (
    Transcript,
    VoiceClient,
    VoiceError,
    VoiceTooLongError,
    VoiceUnavailableError,
    VoiceUnreadableError,
    detector_state,
    sentences,
    sniff,
    spoken_text,
    to_m4a,
)
from tests.fakes.fake_voice import FakeVoiceService, wav

M4A = b"\x00\x00\x00\x1cftypM4A " + bytes(2000)  # an iPhone voice note starts like this
ADTS = b"\xff\xf1\x50\x80" + bytes(2000)  # an Android one like this


def closed_port_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def test_the_first_bytes_decide_what_audio_it_is() -> None:
    assert sniff(M4A) == "m4a"
    assert sniff(ADTS) == "aac"
    assert sniff(b"\xff\xf9\x5c\x60" + bytes(10)) == "aac"
    assert sniff(b"OggS\x00\x02" + bytes(10)) == "ogg"
    assert sniff(b"RIFF\x24\x00\x00\x00WAVEfmt ") == "wav"
    assert sniff(b"ID3\x04\x00" + bytes(10)) == "mp3"
    assert sniff(b"\xff\xfb\x90\x44" + bytes(10)) == "mp3"
    assert sniff(b"%PDF-1.7") is None
    assert sniff(b"\x89PNG\r\n\x1a\n") is None
    assert sniff(b"") is None


async def test_transcribe_sends_the_bytes_and_returns_the_text() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="af_heart", speed=1.1)
        assert await client.transcribe(M4A) == Transcript("Turn off the lamp.", 2.4)
        method, path, headers, body = service.requests[0]
        assert (method, path, body) == ("POST", "/v1/audio/transcriptions", M4A)
        assert headers["content-type"] == "application/octet-stream"


@pytest.mark.parametrize(
    ("status", "error"),
    [(413, VoiceTooLongError), (415, VoiceUnreadableError), (422, VoiceUnreadableError), (503, VoiceUnavailableError)],
)
async def test_service_refusals_become_errors(status: int, error: type[VoiceError]) -> None:
    with FakeVoiceService() as service:
        service.status["/v1/audio/transcriptions"] = status
        with pytest.raises(error):
            await VoiceClient(service.url, voice="af_heart", speed=1.1).transcribe(M4A)


async def test_a_service_that_is_not_running_is_unavailable() -> None:
    client = VoiceClient(closed_port_url(), voice="af_heart", speed=1.1)
    with pytest.raises(VoiceUnavailableError):
        await client.transcribe(M4A)
    assert await client.health() is None


async def test_speak_sends_the_text_voice_and_speed() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="bf_emma", speed=1.2)
        assert await client.speak("The lamp is off.") == service.speech
        method, path, headers, body = service.requests[0]
        assert (method, path, headers["content-type"]) == ("POST", "/v1/audio/speech", "application/json")
        assert json.loads(body) == {"input": "The lamp is off.", "voice": "bf_emma", "speed": 1.2}
        assert await client.health() == {"stt": "ready", "tts": "ready"}


async def test_speech_that_fails_is_an_error() -> None:
    with FakeVoiceService() as service:
        service.status["/v1/audio/speech"] = 500
        with pytest.raises(VoiceError):
            await VoiceClient(service.url, voice="af_heart", speed=1.1).speak("Hello there.")


def test_spoken_text_flattens_markdown() -> None:
    answer = "## Today\n- Buy **milk**\n- Call [Anna](https://example.com/anna)\n\nThat's all for now 🙂"
    assert spoken_text(answer) == "Today. Buy milk. Call Anna. That's all for now."


def test_spoken_text_joins_wrapped_lines_and_drops_links_and_paths() -> None:
    answer = "The notes are in ~/dev/notes/todo.md\nand online (https://example.com/x), both current."
    assert spoken_text(answer) == "The notes are in and online, both current."


def test_spoken_text_reads_tables_and_inline_code() -> None:
    answer = "| Day | Plan |\n|---|---|\n| Mon | Gym |\n\nRun `uv sync` first."
    assert spoken_text(answer) == "Day; Plan. Mon; Gym. Run uv sync first."


def test_no_speech_for_code_answers_or_almost_nothing() -> None:
    assert spoken_text("Here:\n```bash\nls -la ~/dev\ncd local-assistant\ngit status\n```") == ""
    assert spoken_text("Ok 👍") == ""
    assert spoken_text("Okay, done.") == "Okay, done."


def test_long_answers_are_cut_at_a_sentence() -> None:
    answer = " ".join(f"This is sentence number {n} of a long answer." for n in range(60))
    spoken = spoken_text(answer, max_chars=200)
    assert len(spoken) <= 200
    assert spoken.endswith("of a long answer. The rest is in the text.")
    assert spoken.startswith("This is sentence number 0")


async def test_to_m4a_makes_a_private_aac_file(tmp_path: Path) -> None:
    path = await to_m4a(wav(0.5), tmp_path / "voice")
    data = path.read_bytes()
    assert data[4:8] == b"ftyp" and sniff(data) == "m4a"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "voice").stat().st_mode) == 0o700


async def test_to_m4a_failure_leaves_no_files(tmp_path: Path) -> None:
    with pytest.raises(VoiceError):
        await to_m4a(b"RIFF not really a wav", tmp_path / "voice")
    assert list((tmp_path / "voice").iterdir()) == []


def test_spoken_text_on_a_call_keeps_short_answers_and_has_no_rest_note() -> None:
    assert spoken_text("Done.") == ""  # a voice note that short is not worth sending
    assert spoken_text("Done.", min_chars=1) == "Done."
    long = " ".join(f"Sentence number {n} is here." for n in range(100))
    cut = spoken_text(long, max_chars=200, rest="")
    assert len(cut) <= 200 and cut.endswith("is here.") and "text" not in cut


def test_sentences_split_spoken_text_for_speaking_one_by_one() -> None:
    assert sentences("Hi there. It is 3.5 degrees! Shall I check? Okay") == [
        "Hi there.",
        "It is 3.5 degrees!",
        "Shall I check?",
        "Okay",
    ]
    assert sentences("") == []


def test_the_voice_can_be_changed_while_running() -> None:
    client = VoiceClient("http://127.0.0.1:1", voice="am_michael", speed=1.1)
    client.set_voice("bm_george")
    assert client.voice == "bm_george"


NOW = 1_791_452_400.0


def recorder(tmp_path: Path) -> PartStatus:
    return PartStatus(tmp_path / "status.json", clock=lambda: NOW)


async def test_transcripts_and_speech_record_the_voice_service(tmp_path: Path) -> None:
    status = recorder(tmp_path)
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="af_heart", speed=1.1, status=status)
        await client.transcribe(M4A)
        assert status.snapshot() == {"voice": Record(ok=NOW)}
        service.status["/v1/audio/speech"] = 500
        with pytest.raises(VoiceError):
            await client.speak("Hello there.")
        assert status.snapshot() == {"voice": Record(ok=NOW, failed=NOW, reason="refused", code=500)}
        del service.status["/v1/audio/speech"]
        await client.speak("Hello there.")
        assert status.snapshot()["voice"] == Record(ok=NOW)
        await client.health()  # a probe, not a real request
        assert status.snapshot()["voice"] == Record(ok=NOW)


@pytest.mark.parametrize(
    ("answer", "record"),
    [
        (503, Record(failed=NOW, reason="loading", code=503)),
        (413, Record(ok=NOW)),  # the service answered as it should: the audio was the problem
        (422, Record(ok=NOW)),
    ],
)
async def test_the_voice_service_answers_are_recorded(tmp_path: Path, answer: int, record: Record) -> None:
    status = recorder(tmp_path)
    with FakeVoiceService() as service:
        service.status["/v1/audio/transcriptions"] = answer
        with pytest.raises(VoiceError):
            await VoiceClient(service.url, voice="af_heart", speed=1.1, status=status).transcribe(M4A)
    assert status.snapshot() == {"voice": record}


async def test_a_voice_service_that_is_not_running_is_recorded(tmp_path: Path) -> None:
    status = recorder(tmp_path)
    with pytest.raises(VoiceUnavailableError):
        await VoiceClient(closed_port_url(), voice="af_heart", speed=1.1, status=status).speak("Hello there.")
    assert status.snapshot() == {"voice": Record(failed=NOW, reason="no_answer")}


async def test_english_requests_are_exactly_as_before_the_languages() -> None:
    # an older voice service (no languages) keeps working in English: no query, no field
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="af_heart", speed=1.1, language="en")
        await client.transcribe(M4A)
        await client.speak("Hi.")
        assert service.paths() == ["/v1/audio/transcriptions", "/v1/audio/speech"]
        assert service.requests[1][3] == json.dumps({"input": "Hi.", "voice": "af_heart", "speed": 1.1}).encode()
        assert service.request_languages == ["en", "en"]


async def test_another_language_goes_with_every_request() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="af_heart", speed=1.1, language="nl")
        assert client.language == "nl"
        await client.transcribe(M4A)
        await client.speak("Hoi.")
        await client.speak("Shall I switch heater off?", language="en")  # code's own words: English
        assert service.paths() == ["/v1/audio/transcriptions?language=nl", "/v1/audio/speech", "/v1/audio/speech"]
        assert json.loads(service.requests[1][3]) == {
            "input": "Hoi.",
            "voice": "af_heart",
            "speed": 1.1,
            "language": "nl",
        }
        assert "language" not in json.loads(service.requests[2][3])
        assert service.request_languages == ["nl", "nl", "en"]
        client.set_language("ja")
        await client.speak("はい。")
        assert service.request_languages[-1] == "ja"
        await client.transcribe(M4A, language="ko")  # a call keeps the language it started with
        assert service.paths()[-1] == "/v1/audio/transcriptions?language=ko"


async def test_the_health_says_which_languages_the_service_has() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="af_heart", speed=1.1)
        assert await client.health() == {"stt": "ready", "tts": "ready"}  # older than the languages
        service.languages = ["en", "nl"]
        assert await client.health() == {"stt": "ready", "tts": "ready", "languages": ("en", "nl")}


async def test_the_languages_of_the_installed_service() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="af_heart", speed=1.1)
        assert await client.languages() == ()  # answers, but older than the language feature
        service.languages = ["en", "nl"]
        assert await client.languages() == ("en", "nl")
    assert await VoiceClient("http://127.0.0.1:1", voice="af_heart", speed=1.1).languages() is None  # not answering


async def test_a_language_the_service_does_not_have_is_an_error() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="af_heart", speed=1.1, language="el")
        with pytest.raises(VoiceError, match="400"):
            await client.speak("Γεια.")


def test_japanese_is_spoken_sentence_by_sentence_too() -> None:
    # Japanese ends sentences with 。！？ and no space after them
    assert sentences("こんにちは。元気ですか？はい！") == ["こんにちは。", "元気ですか？", "はい！"]
    assert sentences("こんにちは。 元気？") == ["こんにちは。", "元気？"]
    assert spoken_text("こんにちは。元気ですか？", min_chars=1) == "こんにちは。元気ですか？"  # no "." added
    long = "今日は晴れです。" * 300
    cut = spoken_text(long, max_chars=200, rest="")
    assert len(cut) <= 200 and cut.endswith("晴れです。")


CHUNK = array.array("h", (int(3000 * math.sin(2 * math.pi * 220 * i / 16_000)) for i in range(4096))).tobytes()


async def test_the_speech_detector_gets_one_chunk_and_answers_a_probability() -> None:
    with FakeVoiceService() as service:
        service.vad = "ready"
        client = VoiceClient(service.url, voice="af_heart", speed=1.1)
        assert await client.vad(CHUNK, stream="0a1b2c") == 1.0
        assert await client.vad(bytes(8192), stream="0a1b2c") == 0.02
        method, path, headers, body = service.requests[0]
        assert (method, path, body) == ("POST", "/vad?stream=0a1b2c", CHUNK)
        assert headers["content-type"] == "application/octet-stream"


@pytest.mark.parametrize(
    ("vad", "answer", "chunk"),
    [
        (None, None, CHUNK),  # an older service: no /vad (404)
        ("loading", None, CHUNK),  # 503 until the model is ready
        ("ready", None, CHUNK[:100]),  # 400: not 4096 samples
        ("ready", b"not json", CHUNK),
        ("ready", b'{"p": "high"}', CHUNK),
        ("ready", b'{"p": 1.5}', CHUNK),
        ("ready", b'{"p": NaN}', CHUNK),
        ("ready", b"[1.0]", CHUNK),
    ],
    ids=["older-service", "loading", "short-chunk", "not-json", "not-a-number", "above-1", "nan", "a-list"],
)
async def test_a_speech_detector_that_cannot_answer_is_an_http_failure_never_an_error(
    tmp_path: Path, vad: str | None, answer: bytes | None, chunk: bytes
) -> None:
    status = recorder(tmp_path)
    with FakeVoiceService() as service:
        service.vad, service.vad_answer = vad, answer
        client = VoiceClient(service.url, voice="af_heart", speed=1.1, status=status)
        assert await client.vad(chunk, stream="s1") == "http"
    assert status.snapshot() == {}  # the detector is not the voice: a failure never marks the voice as failing


async def test_a_speech_detector_that_does_not_answer_is_an_http_failure() -> None:
    assert await VoiceClient(closed_port_url(), voice="af_heart", speed=1.1).vad(CHUNK, stream="s1") == "http"


async def test_a_slow_speech_detector_is_given_up_on_quickly_as_a_timeout() -> None:
    with FakeVoiceService() as service:
        service.vad, service.vad_delay = "ready", 0.8
        started = time.monotonic()
        assert await VoiceClient(service.url, voice="af_heart", speed=1.1).vad(CHUNK, stream="s1") == "timeout"
        assert time.monotonic() - started < 0.6  # a call never waits for the detector


@pytest.mark.parametrize(
    ("health", "state"),
    [
        ({"stt": "ready", "tts": "ready", "vad": "ready"}, "ready"),
        ({"stt": "ready", "tts": "ready", "vad": "loading"}, "loading"),
        ({"stt": "ready", "tts": "ready", "vad": "unavailable"}, "unavailable"),  # no model: its own fix (doctor)
        ({"stt": "ready", "tts": "ready"}, "old voice service"),  # built before the detector
        (None, "not ready"),  # no answer
        ({"stt": "ready", "tts": "ready", "vad": "something new"}, "not ready"),
    ],
)
def test_the_speech_detectors_state_from_the_health(health: dict[str, str] | None, state: str) -> None:
    assert detector_state(health) == state
