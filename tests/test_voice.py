import array
import asyncio
import json
import math
import re
import socket
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from local_assistant import prompts, voice
from local_assistant.part_status import PartStatus, Record
from local_assistant.voice import (
    COMMANDS,
    VAD_TIMEOUT,
    Transcript,
    VoiceClient,
    VoiceError,
    VoiceTooLongError,
    VoiceUnavailableError,
    VoiceUnreadableError,
    detector_state,
    peer_is_ours,
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


def test_spoken_text_never_says_markup_or_a_struck_word() -> None:
    """The same reading as the text styles (signal_text): single *italics* and ~~struck~~ words too; a word crossed
    out is not meant, so it is not said."""
    answer = "**Today**: the *dentist* at 09:30 *(Prinsengracht 12)*, then dinner with ~~Bob~~ Anna."
    assert spoken_text(answer) == "Today: the dentist at 09:30 (Prinsengracht 12), then dinner with Anna."
    assert spoken_text("# Plan\n- *Gym* at 7\n- 2*3 is 6") == "Plan. Gym at 7. 2*3 is 6."


def test_spoken_text_joins_wrapped_lines_and_drops_links_and_paths() -> None:
    answer = "The notes are in ~/dev/notes/todo.md\nand online (https://example.com/x), both current."
    assert spoken_text(answer) == "The notes are in and online, both current."


def test_spoken_text_says_a_slash_command_instead_of_dropping_it_as_a_path() -> None:
    assert spoken_text("Use /reminders to see them.") == "Use slash reminders to see them."
    assert spoken_text("Type /help for the list, or /remind 8:00 call mum.", min_chars=1) == (
        "Type slash help for the list, or slash remind 8:00 call mum."
    )
    assert (
        spoken_text("Start with `/babble nl`, then /babble off.")
        == "Start with slash babble nl, then slash babble off."
    )
    # a path is still left out, also one that starts like a command
    assert spoken_text("It is in /reminders/old.txt and ~/new and /usr/bin/sudo, all three.") == (
        "It is in and and, all three."
    )


def test_every_command_in_help_is_spoken_as_a_command() -> None:
    named = set(re.findall(r"(?<![\w/])/([a-z]+)\b", prompts.HELP))
    assert named and named <= set(COMMANDS), named - set(COMMANDS)


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


def hers(port: int, local: int, seconds: float) -> bool | None:
    return True


async def test_a_slow_speech_detector_is_given_up_on_quickly_as_a_timeout() -> None:
    with FakeVoiceService() as service:
        service.vad, service.vad_delay = "ready", 0.8
        started = time.monotonic()
        assert (
            await VoiceClient(service.url, voice="af_heart", speed=1.1, peer=hers).vad(CHUNK, stream="s1") == "timeout"
        )
        assert time.monotonic() - started < 0.6  # a call never waits for the detector


async def test_the_check_of_whose_program_it_is_counts_within_the_detectors_own_time() -> None:
    def slow_check(port: int, local: int, seconds: float) -> bool | None:
        time.sleep(0.25)
        return True

    with FakeVoiceService() as service:
        service.vad, service.vad_delay = "ready", 0.2
        started = time.monotonic()
        client = VoiceClient(service.url, voice="af_heart", speed=1.1, peer=slow_check)
        assert await client.vad(CHUNK, stream="s1") == "timeout"  # 0.25 checking + 0.2 answering: over 0.3
        assert time.monotonic() - started < 0.5


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


# the voice port: in her own user, a program of the owner's could hold 127.0.0.1:8150 whenever her voice service is not
# bound (a deploy, a restart, the start of the Mac). Audio and text go only over a connection whose other end a program
# of her own account holds: lsof, as the account that runs it, lists only that account's processes, so a connection it
# does not show taken is not hers. The connection itself is checked, not only who listens, so a program that took it and
# then left the port to her service still gets nothing


def port_of(url: str) -> int:
    return int(url.rsplit(":", 1)[1])


def test_a_connection_a_program_of_this_account_took_is_ours() -> None:
    with FakeVoiceService() as service, socket.create_connection(("127.0.0.1", port_of(service.url))) as connection:
        assert peer_is_ours(port_of(service.url), connection.getsockname()[1], 1.0) is True


def test_a_listener_of_ours_that_has_not_taken_the_connection_is_not_enough() -> None:
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen(1)  # never accepted: no program holds the other end
        port = listening.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as connection:
            started = time.monotonic()
            assert peer_is_ours(port, connection.getsockname()[1], 0.3) is False
            assert time.monotonic() - started < 1.5  # it waits for the taking about the time it was given, no longer


def lsof_runs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    runs: list[list[str]] = []
    run = subprocess.run

    def counted(args: list[str], **kwargs: Any) -> Any:
        runs.append(args)
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counted)
    return runs


def test_no_listener_of_ours_is_not_ours_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = lsof_runs(monkeypatch)
    assert peer_is_ours(port_of(closed_port_url()), 50000, 5.0) is False
    assert len(runs) == 1  # nothing of ours listens: no waiting, no second look


def test_a_listener_that_has_not_taken_the_connection_yet_is_looked_at_again(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = lsof_runs(monkeypatch)
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen(1)
        port = listening.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as connection:
            assert peer_is_ours(port, connection.getsockname()[1], 0.5) is False
    assert len(runs) >= 2  # a connection waits for its accept: the taking is waited for


def test_a_check_that_cannot_run_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(voice, "LSOF", str(tmp_path / "no-lsof"))
    with FakeVoiceService() as service, socket.create_connection(("127.0.0.1", port_of(service.url))) as connection:
        assert peer_is_ours(port_of(service.url), connection.getsockname()[1], 1.0) is None


async def test_the_check_is_asked_about_this_very_connection_before_anything_is_sent() -> None:
    asked: list[tuple[int, int, float]] = []

    def checked(port: int, local: int, seconds: float) -> bool | None:
        asked.append((port, local, seconds))
        return peer_is_ours(port, local, seconds)

    with FakeVoiceService() as service:
        service.vad = "ready"
        client = VoiceClient(service.url, voice="af_heart", speed=1.1, peer=checked)
        assert await client.transcribe(M4A) == Transcript("Turn off the lamp.", 2.4)
        assert isinstance(await client.vad(CHUNK, stream="s1"), float)
    assert [port for port, _, _ in asked] == [port_of(service.url)] * 2
    assert all(local != port_of(service.url) for _, local, _ in asked)
    assert asked[1][2] <= VAD_TIMEOUT  # a call's detector check fits the detector's own time


@pytest.mark.parametrize(
    ("verdict", "said", "reason"), [(False, "not hers", "not_hers"), (None, "could not be checked", "no_answer")]
)
async def test_nothing_goes_to_a_program_that_is_not_hers_or_unchecked(
    caplog: pytest.LogCaptureFixture, verdict: bool | None, said: str, reason: str
) -> None:
    """Another program at her voice service's address gets nothing, and the part says so (`not_hers`, finding 41):
    doctor and /status name it, never "no answer"."""
    status = PartStatus(None)
    asked: list[int] = []

    def check(port: int, local: int, seconds: float) -> bool | None:
        asked.append(port)
        return verdict

    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="af_heart", speed=1.1, status=status, peer=check)
        with pytest.raises(VoiceUnavailableError):
            await client.transcribe(M4A)
        with pytest.raises(VoiceUnavailableError):
            await client.speak("Hello there.")
        assert await client.vad(CHUNK, stream="s1") == "http"
        assert await client.health() is None
        await asyncio.sleep(0.2)
        assert service.requests == []  # connected, then nothing sent: no audio, no text, no request
    assert asked == [port_of(service.url)] * 4
    assert status.snapshot()["voice"].reason == reason
    assert len([r for r in caplog.records if said in r.getMessage()]) == 1  # said once, not per request


async def test_her_own_service_again_is_said_once_and_used() -> None:
    verdicts = [False, True]

    def check(port: int, local: int, seconds: float) -> bool | None:
        return verdicts.pop(0)

    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="af_heart", speed=1.1, peer=check)
        with pytest.raises(VoiceUnavailableError):
            await client.transcribe(M4A)
        assert await client.transcribe(M4A) == Transcript("Turn off the lamp.", 2.4)
        assert len(service.requests) == 1


async def test_localhost_is_reached_at_127_0_0_1_where_her_service_listens() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url.replace("127.0.0.1", "localhost"), voice="af_heart", speed=1.1)
        assert await client.transcribe(M4A) == Transcript("Turn off the lamp.", 2.4)
