"""signal-cli as a supervised child process, spoken to over JSON-RPC on stdin/stdout.

Only this module talks to signal-cli, and it refuses to send anything to an account
outside the allowlist, so a bug or a prompt injection cannot message third parties.
signal-cli's own log lines (stderr) go into the bridge log.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

UNTRUSTED_KEY_ERROR = -4  # signal-cli SignalJsonRpcCommandHandler.UNTRUSTED_KEY_ERROR


class SignalError(Exception):
    """signal-cli reported an error, did not answer, or is not running."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class UntrustedIdentityError(SignalError):
    """The recipient's safety number changed and has not been confirmed (signal-cli trust)."""


# One JSON line can carry a whole photo as base64 (20 MB → 26.7 MB), see getAttachment.
READ_LIMIT = 32 * 1024 * 1024


class NotAllowedError(Exception):
    """Refused: the recipient is not on the allowlist."""


IPV4 = "-Djava.net.preferIPv4Stack=true"


def signal_cli_command(signal_cli: str, bot_number: str, *, jvm: bool = False) -> list[str]:
    """IPv4 only: chat.signal.org also answers over IPv6, but Signal's captcha is checked against an IPv4 address, so
    registering over IPv6 fails with 403 (signal-cli issue 1788); the flag keeps one network path for every command.
    Manual receive (subscribe once ready); photos are fetched with getAttachment, so attachments are not ignored
    (strangers' files are deleted on receipt by the app).
    The JVM build's launcher would hand a -D argument to signal-cli itself: it gets IPv4 from JAVA_OPTS."""
    return [
        signal_cli,
        *([] if jvm else [IPV4]),
        "--scrub-log",
        "-a",
        bot_number,
        "jsonRpc",
        "--receive-mode",
        "manual",
        "--ignore-stories",
        "--ignore-avatars",
        "--ignore-stickers",
    ]


def call_env(*, java_home: Path | None, tunnel: Path, socket_dir: Path) -> dict[str, str]:
    """What the JVM signal-cli and the call helper it starts per call need, on top of the bridge's environment:
    Java (none given: the launcher looks for `java` itself), IPv4, our signal-call-tunnel build in pipe mode
    (audio over a Unix socket in a private folder)."""
    return {
        **({"JAVA_HOME": str(java_home)} if java_home is not None else {}),
        "JAVA_OPTS": IPV4,
        "SIGNAL_CALL_TUNNEL_BIN": str(tunnel),
        "SIGNAL_CALL_TUNNEL_AUDIO_MODE": "pipe",
        "SIGNAL_CALL_TUNNEL_SOCKET_DIR": str(socket_dir),
    }


# Put on the call-event stream when signal-cli stops: its calls (and their helpers) are gone.
RESTART_ENDS_CALLS: dict[str, Any] = {"state": "ENDED", "reason": "signal_cli_restarted"}


class SignalClient:
    def __init__(
        self,
        command: Sequence[str],
        allowed: frozenset[str],
        *,
        env: Mapping[str, str] | None = None,
        calls: bool = False,
        request_timeout: float = 60.0,
        backoff_start: float = 1.0,
        backoff_max: float = 60.0,
        stable_after: float = 60.0,
        read_limit: int = READ_LIMIT,
    ) -> None:
        self._command = list(command)
        self._env = dict(env) if env else None
        self._calls = calls
        self._read_limit = read_limit
        self._allowed = frozenset(aci.lower() for aci in allowed)
        self._request_timeout = request_timeout
        self._backoff_start = backoff_start
        self._backoff_max = backoff_max
        self._stable_after = stable_after
        self._envelopes: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._call_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._next_id = 0
        self._proc: asyncio.subprocess.Process | None = None
        self._ready = asyncio.Event()
        self._supervisor: asyncio.Task[None] | None = None
        self._stopping = False
        self.restarts = 0

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    async def start(self) -> None:
        """Start supervising signal-cli and wait until it is subscribed (TimeoutError if it never is)."""
        self._supervisor = asyncio.create_task(self._supervise(), name="signal-cli")
        await asyncio.wait_for(self._ready.wait(), self._request_timeout)

    async def stop(self) -> None:
        self._stopping = True
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
        await self._terminate()

    async def envelopes(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self._envelopes.get()

    async def call_events(self) -> AsyncIterator[dict[str, Any]]:
        """signal-cli's callEvent results (callId, state, uuid, …), plus RESTART_ENDS_CALLS after each restart."""
        while True:
            yield await self._call_events.get()

    async def accept_call(self, aci: str, call_id: int) -> dict[str, Any]:
        """Answer the ringing call `call_id` from `aci` (the ACI its ringing event named)."""
        self._check_allowed(aci)
        result = await self.request("acceptCall", {"callId": call_id})
        return result if isinstance(result, dict) else {}

    async def hangup_call(self, aci: str, call_id: int) -> None:
        self._check_allowed(aci)
        await self.request("hangupCall", {"callId": call_id})

    async def send(self, aci: str, text: str) -> int:
        self._check_allowed(aci)
        result = await self.request("send", {"recipient": [aci], "message": text})
        timestamp = result.get("timestamp") if isinstance(result, dict) else None
        if not isinstance(timestamp, int):
            raise SignalError("send returned no timestamp")
        return timestamp

    async def send_voice_note(self, aci: str, path: Path) -> int:
        """Send an audio file as a Signal voice note (signal-cli reads the file while sending)."""
        self._check_allowed(aci)
        result = await self.request("send", {"recipient": [aci], "attachments": [str(path)], "voiceNote": True})
        timestamp = result.get("timestamp") if isinstance(result, dict) else None
        if not isinstance(timestamp, int):
            raise SignalError("send returned no timestamp")
        return timestamp

    async def update_profile(self, name: str) -> None:
        """The bot's Signal profile name (shown to the user)."""
        await self.request("updateProfile", {"givenName": name, "familyName": ""})

    async def update_avatar(self, path: Path) -> None:
        """The bot's Signal profile picture: signal-cli reads the file (a full path); fields not sent, such as the
        name, stay as they are."""
        await self.request("updateProfile", {"avatar": str(path)})

    async def typing(self, aci: str, *, stop: bool = False) -> None:
        self._check_allowed(aci)
        await self.request("sendTyping", {"recipient": [aci], "stop": stop})

    async def receipt(self, aci: str, timestamp: int) -> None:
        self._check_allowed(aci)
        await self.request("sendReceipt", {"recipient": aci, "targetTimestamp": [timestamp], "type": "read"})

    async def get_attachment(self, aci: str, attachment_id: str) -> bytes:
        """The bytes of an attachment the allowed user sent (signal-cli returns base64)."""
        self._check_allowed(aci)
        result = await self.request("getAttachment", {"id": attachment_id, "recipient": aci})
        data = result.get("data") if isinstance(result, dict) else result
        if not isinstance(data, str):
            raise SignalError("getAttachment returned no data")
        try:
            return base64.b64decode(data, validate=True)
        except binascii.Error as exc:
            raise SignalError("getAttachment returned invalid base64") from exc

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        try:
            await asyncio.wait_for(self._ready.wait(), self._request_timeout)
        except TimeoutError as exc:
            raise SignalError("signal-cli is not running") from exc
        return await self._call(method, params or {})

    def _check_allowed(self, aci: str) -> None:
        if aci.lower() not in self._allowed:
            raise NotAllowedError(f"recipient {aci} is not on the allowlist")

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise SignalError("signal-cli is not running")
        self._next_id += 1
        request_id = str(self._next_id)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        line = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            proc.stdin.write(line.encode() + b"\n")
            await proc.stdin.drain()
            return await asyncio.wait_for(future, self._request_timeout)
        except TimeoutError as exc:
            raise SignalError(f"signal-cli did not answer {method}") from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise SignalError("signal-cli went away") from exc
        finally:
            self._pending.pop(request_id, None)

    async def _supervise(self) -> None:
        delay = self._backoff_start
        while not self._stopping:
            started = time.monotonic()
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except SignalError as exc:
                log.warning("signal-cli: %s", exc)
            except Exception:
                log.exception("signal-cli failed")
            self._ready.clear()
            self._fail_pending(SignalError("signal-cli exited"))
            if self._calls:
                self._call_events.put_nowait(dict(RESTART_ENDS_CALLS))
            if self._stopping:
                break
            if time.monotonic() - started >= self._stable_after:
                delay = self._backoff_start
            self.restarts += 1
            log.warning("signal-cli exited; restart %d in %.1f s", self.restarts, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._backoff_max)

    async def _run_once(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=self._read_limit,
            env={**os.environ, **self._env} if self._env else None,
        )
        reader = asyncio.create_task(self._read(self._proc))
        # When signal-cli's output ends, fail waiting requests at once (e.g. subscribeReceive of a
        # process that exits immediately) instead of letting them run into the request timeout.
        reader.add_done_callback(lambda _task: self._fail_pending(SignalError("signal-cli exited")))
        stderr = asyncio.create_task(self._drain_stderr(self._proc))
        try:
            if self._calls:  # first: signal-cli drops call offers while nobody listens for call events
                try:
                    await self._call("subscribeCallEvents", {})
                except SignalError as exc:
                    log.warning("signal-cli: no call events (%s); calls are off until it restarts", exc)
            await self._call("subscribeReceive", {})
            self._ready.set()
            log.info("signal-cli ready (pid %s)", self._proc.pid)
            await reader
        finally:
            reader.cancel()
            stderr.cancel()
            await self._terminate()

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stdout is None:
            return
        while True:
            try:
                line = await proc.stdout.readline()
            except ValueError:  # a line over the limit: dropped (its request times out), signal-cli keeps running
                log.warning("signal-cli: a line over %d bytes was dropped", self._read_limit)
                continue
            if not line:
                break
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                log.warning("signal-cli: unreadable line (%d bytes)", len(line))
                continue
            if isinstance(obj, dict):
                self._dispatch(obj)

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stderr is None:
            return
        while line := await proc.stderr.readline():
            log.info("signal-cli: %s", line.decode(errors="replace").rstrip()[:500])

    def _dispatch(self, obj: dict[str, Any]) -> None:
        if "id" in obj and obj.get("method") is None:
            future = self._pending.get(str(obj["id"]))
            if future is None or future.done():
                return
            error = obj.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                code = code if isinstance(code, int) else None
                message = str(error.get("message", "signal-cli error"))
                error_type = UntrustedIdentityError if code == UNTRUSTED_KEY_ERROR else SignalError
                future.set_exception(error_type(message, code))
            else:
                future.set_result(obj.get("result"))
            return
        if obj.get("method") == "callEvent":
            params = obj.get("params")
            event = params.get("result", params) if isinstance(params, dict) else None
            if isinstance(event, dict):
                self._call_events.put_nowait(event)
            return
        if obj.get("method") == "receive":
            params = obj.get("params")
            container = params.get("result", params) if isinstance(params, dict) else None
            envelope = container.get("envelope") if isinstance(container, dict) else None
            if isinstance(envelope, dict):
                self._envelopes.put_nowait(envelope)

    def _fail_pending(self, error: SignalError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _terminate(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except TimeoutError:
            proc.kill()
            await proc.wait()
