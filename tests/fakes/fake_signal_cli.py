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

An inbox line with a top-level "method" (e.g. a callEvent) is written as it is: a notification, in order.
launch.json records argv and the call-related environment of the latest start.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any


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
                else:
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
        elif method == "acceptCall":
            call_id = request.get("params", {}).get("callId")
            result = {"callId": call_id, "state": "CONNECTING", "codec": "opus", "sampleRate": 48000}
            write({"jsonrpc": "2.0", "result": result, "id": request_id})
        elif method == "updateProfile" and os.environ.get("FAKE_SIGNAL_AVATAR_ERROR") and "avatar" in request["params"]:
            error = {"code": -1, "message": "fake failure"}
            write({"jsonrpc": "2.0", "error": error, "id": request_id})
        elif method in ("sendTyping", "sendReceipt", "updateProfile", "hangupCall"):
            write({"jsonrpc": "2.0", "result": {}, "id": request_id})
        else:
            error = {"code": -32601, "message": f"unknown method {method}"}
            write({"jsonrpc": "2.0", "error": error, "id": request_id})


if __name__ == "__main__":
    main()
