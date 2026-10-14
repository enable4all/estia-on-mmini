"""Stand-in for tools/voice's voice-service: the same endpoints on 127.0.0.1 (a random port), with the language
contract: `?language=<code>` on a transcription, a "language" field on speech, "en" without either, 400 for a code
outside the 11; the health lists "languages" only when a test sets them (an older service has none). The speech
detector (`POST /vad?stream=<id>`, 4096 samples of 16 kHz audio) answers with `speech_probability`; the health says
"vad" only when a test sets it (an older service has no detector)."""

from __future__ import annotations

import array
import io
import itertools
import json
import math
import threading
import time
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any

from local_assistant.language import CODES


def wav(seconds: float = 0.3, rate: int = 16_000) -> bytes:
    """A quiet mono 16-bit WAV (a soft square wave, so encoders have something to encode)."""
    frames = int(seconds * rate)
    samples = b"".join((800 if (n // 40) % 2 else -800).to_bytes(2, "little", signed=True) for n in range(frames))
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(samples)
    return out.getvalue()


VAD_CHUNK_BYTES = 8192  # 4096 samples of 16 kHz mono int16


def speech_probability(pcm16k: bytes) -> float:
    """A stand-in for Silero's speech probability, for the tests' sounds: their speech is tones (few zero crossings
    in its loud 10 ms blocks), their noise is random (many). Like Silero it ignores loudness above a whisper."""
    samples = array.array("h", pcm16k[: len(pcm16k) - len(pcm16k) % 2])
    blocks = [samples[start : start + 160] for start in range(0, len(samples) - 159, 160)]
    tonal = 0
    for block in blocks:
        level = math.sqrt(sum(x * x for x in block) / len(block))
        crossings = sum(1 for a, b in itertools.pairwise(block) if (a < 0) != (b < 0)) / len(block)
        tonal += level >= 100 and crossings < 0.15
    return 1.0 if blocks and tonal >= 0.3 * len(blocks) else 0.02


class FakeVoiceService:
    """Answers like voice-service; tests set `transcript`, force a status per path, and read `requests` and the
    language each one asked for (`request_languages`)."""

    def __init__(self) -> None:
        self.transcript = "Turn off the lamp."
        self.duration = 2.4
        self.speech = wav()
        self.health = {"stt": "ready", "tts": "ready"}
        self.languages: list[str] | None = None  # in the health once set (a service that can do languages)
        self.vad: str | None = None  # the detector's state in the health once set ("ready", "loading", ...)
        self.vad_delay = 0.0  # seconds before /vad answers (a slow service)
        self.vad_answer: bytes | None = None  # a /vad body to send instead of the probability
        self.status: dict[str, int] = {}  # path → status to answer with instead
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []  # method, path (with query), headers, body
        self.request_languages: list[str] = []  # per transcription or speech request
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # quiet tests
                pass

            def _answer(self, status: int, body: bytes, content_type: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _language(self, path: str, query: str, body: bytes) -> str:
                if path == "/v1/audio/speech":
                    try:
                        return str(json.loads(body).get("language", "en"))
                    except (ValueError, AttributeError):
                        return "en"
                return urllib.parse.parse_qs(query).get("language", ["en"])[0]

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                service.requests.append((self.command, self.path, headers, body))
                path, _, query = self.path.partition("?")
                language = self._language(path, query, body)
                if path.startswith("/v1/audio/"):
                    service.request_languages.append(language)
                forced = service.status.get(path)
                if forced is not None:
                    self._answer(forced, json.dumps({"error": "forced"}).encode())
                elif path == "/health":
                    listed = {"languages": service.languages} if service.languages is not None else {}
                    detector = {"vad": service.vad} if service.vad is not None else {}
                    self._answer(200, json.dumps(service.health | listed | detector).encode())
                elif path == "/vad":
                    self._vad(query, body)
                elif path.startswith("/v1/audio/") and language not in CODES:
                    self._answer(400, json.dumps({"error": f"unsupported language: {language}"}).encode())
                elif path == "/v1/audio/transcriptions":
                    answer = {"text": service.transcript, "duration": service.duration}
                    self._answer(200, json.dumps(answer).encode())
                elif path == "/v1/audio/speech":
                    self._answer(200, service.speech, "audio/wav")
                else:
                    self._answer(404, b'{"error":"not_found"}')

            def _vad(self, query: str, body: bytes) -> None:
                stream = urllib.parse.parse_qs(query).get("stream", [""])[0]
                if service.vad is None:
                    self._answer(404, b'{"error":"not_found"}')  # an older service
                elif service.vad != "ready":
                    self._answer(503, b'{"error":"loading"}')
                elif not stream or len(body) != VAD_CHUNK_BYTES:
                    self._answer(400, b'{"error":"bad_request"}')
                else:
                    time.sleep(service.vad_delay)
                    answer = service.vad_answer or json.dumps({"p": speech_probability(body)}).encode()
                    self._answer(200, answer)

            do_GET = _handle
            do_POST = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def paths(self) -> list[str]:
        return [path for _, path, _, _ in self.requests]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> FakeVoiceService:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.close()
