"""Stand-in for `signal-cli jsonRpc` in tests: the same JSON lines on stdin/stdout.

Environment:
  FAKE_SIGNAL_DIR          folder with inbox.jsonl (envelopes to deliver; tests append to it),
                           requests.jsonl (every request this fake received) and attachments/<id>
                           (files getAttachment returns as base64); outbox/ gets a copy of every file sent
  FAKE_SIGNAL_SEND_ERROR   JSON-RPC error code returned for every send, e.g. -4 (untrusted identity)
  FAKE_SIGNAL_SEND_ERROR_FOR  with FAKE_SIGNAL_SEND_ERROR: only sends to this recipient fail
  FAKE_SIGNAL_CRASH_ONCE   exit after delivering the first envelope, once per folder
  FAKE_SIGNAL_NO_CALLS     answer subscribeCallEvents with an error (a signal-cli without calls)
  FAKE_SIGNAL_AVATAR_ERROR answer an updateProfile with an avatar with an error
  FAKE_SIGNAL_CALL_END     after startCall (call id 42, its ringing event before the reply, as signal-cli 0.14.9
                           does): end the call with this reason (e.g. ring_timeout); unset, it rings until hung up
  FAKE_SIGNAL_DEVICES      JSON list listDevices answers with (default: this device only, id 1)
  FAKE_SIGNAL_CAPTCHA      register needs this captcha token (unset: none needed)
  FAKE_SIGNAL_CODE         the SMS code verify takes (default 111111)
  FAKE_SIGNAL_PIN          verify needs this registration-lock PIN too (unset: none)

A send's textStyle is checked as signal-cli 0.14.9 checks it ("start:length:STYLE"; an error fails the send).

register and verify work as in signal-cli 0.14.9: only in multi-account mode (started without -a), with the number in
an "account" param.

An inbox line with a top-level "method" (e.g. a callEvent) is written as it is: a notification, in order. A line with
a top-level "exception" is a message signal-cli could not open (0.14.9's shape: {"exception": {"message", "type"},
"envelope": {...}}). An envelope from an account without "sourceDevice" gets device 1, as signal-cli always names it.
launch.json records argv and the call-related environment of the latest start; launches.jsonl every start's argv.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any


def registration(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """signal-cli 0.14.9's register and verify: multi-account mode only, the number in the "account" param."""
    if "-a" in sys.argv[1:]:
        return {"error": {"code": -32601, "message": "Method not implemented"}}
    if not params.get("account"):
        return {"error": {"code": -32602, "message": "Method requires account parameter"}}
    if method == "register":
        wanted = os.environ.get("FAKE_SIGNAL_CAPTCHA")
        if wanted and params.get("captcha") != wanted:
            return {"error": {"code": -1, "message": "Captcha required for verification, use --captcha CAPTCHA"}}
        return {"result": {}}
    if params.get("verificationCode") != os.environ.get("FAKE_SIGNAL_CODE", "111111"):
        return {"error": {"code": -1, "message": "Verify error: Invalid verification code"}}
    pin = os.environ.get("FAKE_SIGNAL_PIN")
    if pin and params.get("pin") != pin:
        return {"error": {"code": -1, "message": "Verification failed! This number is locked with a pin."}}
    return {"result": {}}


STYLES = ("BOLD", "ITALIC", "SPOILER", "STRIKETHROUGH", "MONOSPACE")


def text_style_error(value: Any) -> str | None:
    """signal-cli 0.14.9's check of a send's textStyle (one string or a list): each "start:length:STYLE", STYLE one of
    its five names in upper case; one bad entry fails the whole send. It never compares a range with the text."""
    for entry in value if isinstance(value, list) else [value]:
        match = re.fullmatch(r"(\d+):(\d+):(.+)", str(entry))
        if match is None:
            return f"Invalid textStyle syntax ({entry}) expected 'start:length:STYLE'"
        if match.group(3) not in STYLES:
            return f"Invalid style: {match.group(3)}"
    return None


def main() -> None:
    folder = Path(os.environ["FAKE_SIGNAL_DIR"])
    inbox = folder / "inbox.jsonl"
    requests = folder / "requests.jsonl"
    offset_file = folder / "inbox.offset"
    crash_marker = folder / "crashed"
    send_error = os.environ.get("FAKE_SIGNAL_SEND_ERROR")
    fail_for = os.environ.get("FAKE_SIGNAL_SEND_ERROR_FOR")
    lock = threading.Lock()
    subscribed = threading.Event()
    keys = ("JAVA_HOME", "JAVA_OPTS", "SIGNAL_CALL_TUNNEL_BIN", "SIGNAL_CALL_TUNNEL_AUDIO_MODE")
    launch = {"argv": sys.argv[1:], "env": {k: os.environ[k] for k in keys if k in os.environ}}
    (folder / "launch.json").write_text(json.dumps(launch))
    with (folder / "launches.jsonl").open("a") as handle:
        handle.write(json.dumps(sys.argv[1:]) + "\n")

    def write(obj: dict[str, Any]) -> None:
        with lock:
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()

    def deliver() -> None:
        subscribed.wait()
        while True:
            done = int(offset_file.read_text()) if offset_file.exists() else 0
            raw = inbox.read_text() if inbox.exists() else ""
            lines = raw[: raw.rfind("\n") + 1].splitlines()  # complete lines only (tests may be mid-write)
            for index in range(done, len(lines)):
                item = json.loads(lines[index])
                if "method" in item:
                    write({"jsonrpc": "2.0", **item})
                elif "exception" in item:
                    result = {key: item[key] for key in ("exception", "envelope") if key in item}
                    params = {"subscription": 0, "result": {**result, "account": "+306900000000"}}
                    write({"jsonrpc": "2.0", "method": "receive", "params": params})
                else:
                    if item.get("sourceUuid") and "sourceDevice" not in item:
                        item["sourceDevice"] = 1
                    write(
                        {
                            "jsonrpc": "2.0",
                            "method": "receive",
                            "params": {"subscription": 0, "result": {"envelope": item}},
                        }
                    )
                offset_file.write_text(str(index + 1))
                if os.environ.get("FAKE_SIGNAL_CRASH_ONCE") and not crash_marker.exists():
                    crash_marker.write_text("1")
                    os._exit(3)
            time.sleep(0.02)

    threading.Thread(target=deliver, daemon=True).start()
    for raw in sys.stdin:
        request = json.loads(raw)
        with requests.open("a") as handle:
            handle.write(json.dumps(request) + "\n")
        method = request.get("method")
        request_id = request.get("id")
        if method == "subscribeReceive":
            write({"jsonrpc": "2.0", "result": 0, "id": request_id})
            subscribed.set()
        elif (
            method == "send"
            and "textStyle" in request.get("params", {})
            and (problem := text_style_error(request["params"]["textStyle"]))
        ):
            write({"jsonrpc": "2.0", "error": {"code": -1, "message": problem}, "id": request_id})
        elif method == "send":
            if send_error and (not fail_for or fail_for in request.get("params", {}).get("recipient", [])):
                write(
                    {"jsonrpc": "2.0", "error": {"code": int(send_error), "message": "fake failure"}, "id": request_id}
                )
            else:
                for name in request.get("params", {}).get("attachments", []):
                    if Path(name).is_file():
                        (folder / "outbox").mkdir(exist_ok=True)
                        shutil.copy(name, folder / "outbox" / Path(name).name)
                result = {"timestamp": int(time.time() * 1000), "results": [{"type": "SUCCESS"}]}
                write({"jsonrpc": "2.0", "result": result, "id": request_id})
        elif method == "getAttachment":
            path = folder / "attachments" / str(request.get("params", {}).get("id"))
            if path.is_file():
                data = base64.b64encode(path.read_bytes()).decode()
                write({"jsonrpc": "2.0", "result": {"data": data}, "id": request_id})
            else:
                write({"jsonrpc": "2.0", "error": {"code": -1, "message": "attachment not found"}, "id": request_id})
        elif method == "subscribeCallEvents" and not os.environ.get("FAKE_SIGNAL_NO_CALLS"):
            write({"jsonrpc": "2.0", "result": 1, "id": request_id})
        elif method == "startCall":
            recipient = (request.get("params", {}).get("recipient") or [""])[0]
            ringing = {"callId": 42, "state": "RINGING_OUTGOING", "uuid": recipient, "isOutgoing": True}
            write({"jsonrpc": "2.0", "method": "callEvent", "params": {"subscription": 1, "result": ringing}})
            write({"jsonrpc": "2.0", "result": {"callId": 42, "state": "RINGING_OUTGOING"}, "id": request_id})
            if os.environ.get("FAKE_SIGNAL_CALL_END"):
                ended = {
                    "callId": 42,
                    "state": "ENDED",
                    "reason": os.environ["FAKE_SIGNAL_CALL_END"],
                    "isOutgoing": True,
                }
                write({"jsonrpc": "2.0", "method": "callEvent", "params": {"subscription": 1, "result": ended}})
        elif method == "listDevices":
            now = int(time.time() * 1000)
            default = [{"id": 1, "name": None, "createdTimestamp": now - 86_400_000, "lastSeenTimestamp": now}]
            devices = json.loads(os.environ.get("FAKE_SIGNAL_DEVICES") or json.dumps(default))
            write({"jsonrpc": "2.0", "result": devices, "id": request_id})
        elif method == "acceptCall":
            call_id = request.get("params", {}).get("callId")
            result = {"callId": call_id, "state": "CONNECTING", "codec": "opus", "sampleRate": 48000}
            write({"jsonrpc": "2.0", "result": result, "id": request_id})
        elif method == "updateProfile" and os.environ.get("FAKE_SIGNAL_AVATAR_ERROR") and "avatar" in request["params"]:
            error = {"code": -1, "message": "fake failure"}
            write({"jsonrpc": "2.0", "error": error, "id": request_id})
        elif method in ("register", "verify"):
            write({"jsonrpc": "2.0", **registration(method, request.get("params") or {}), "id": request_id})
        elif method in ("sendTyping", "sendReceipt", "updateProfile", "hangupCall"):
            write({"jsonrpc": "2.0", "result": {}, "id": request_id})
        else:
            error = {"code": -32601, "message": f"unknown method {method}"}
            write({"jsonrpc": "2.0", "error": error, "id": request_id})


if __name__ == "__main__":
    main()
