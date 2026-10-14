import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from local_assistant.signal_io import (
    NotAllowedError,
    SignalClient,
    SignalError,
    UntrustedIdentityError,
    call_env,
    signal_cli_command,
)
from tests.conftest import ME, STRANGER, T0

FAKE = Path(__file__).parent / "fakes" / "fake_signal_cli.py"


@pytest.fixture
def fake_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "signal"
    folder.mkdir()
    monkeypatch.setenv("FAKE_SIGNAL_DIR", str(folder))
    return folder


def make_client(command: list[str] | None = None, **kwargs: Any) -> SignalClient:
    settings: dict[str, Any] = {"request_timeout": 5.0, "backoff_start": 0.05, "backoff_max": 0.2}
    settings.update(kwargs)
    return SignalClient(command or [sys.executable, str(FAKE)], frozenset({ME}), **settings)


def deliver(folder: Path, envelope: dict[str, Any]) -> None:
    with (folder / "inbox.jsonl").open("a") as handle:
        handle.write(json.dumps(envelope) + "\n")


def requests(folder: Path) -> list[dict[str, Any]]:
    path = folder / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def until(condition: Callable[[], bool], limit: float = 5.0) -> None:
    async with asyncio.timeout(limit):
        while not condition():
            await asyncio.sleep(0.02)


def test_command_forces_ipv4_and_manual_receive() -> None:
    command = signal_cli_command("/opt/homebrew/bin/signal-cli", "+306900000000")
    assert command[:2] == ["/opt/homebrew/bin/signal-cli", "-Djava.net.preferIPv4Stack=true"]
    assert command[command.index("-a") + 1] == "+306900000000"
    assert command[command.index("jsonRpc") + 1 : command.index("jsonRpc") + 3] == ["--receive-mode", "manual"]
    assert "--scrub-log" in command
    assert "--ignore-attachments" not in command  # photos are fetched with getAttachment


async def test_subscribes_then_delivers_envelopes(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0, "dataMessage": {"message": "hi"}})
        stream = client.envelopes()
        envelope = await asyncio.wait_for(anext(stream), 5)
        assert envelope["dataMessage"]["message"] == "hi"
        assert requests(fake_dir)[0]["method"] == "subscribeReceive"
    finally:
        await client.stop()


async def test_send_returns_signal_timestamp(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        timestamp = await client.send(ME, "Hello!")
        assert timestamp > 0
        send = next(r for r in requests(fake_dir) if r["method"] == "send")
        assert send["params"] == {"recipient": [ME], "message": "Hello!"}
    finally:
        await client.stop()


async def test_a_voice_note_is_an_attachment_with_the_voice_note_flag(fake_dir: Path, tmp_path: Path) -> None:
    note = tmp_path / "answer.m4a"
    note.write_bytes(b"\x00\x00\x00\x1cftypM4A " + bytes(100))
    client = make_client()
    await client.start()
    try:
        assert await client.send_voice_note(ME, note) > 0
        send = next(r for r in requests(fake_dir) if r["method"] == "send")
        assert send["params"] == {"recipient": [ME], "attachments": [str(note)], "voiceNote": True}
        with pytest.raises(NotAllowedError):
            await client.send_voice_note(STRANGER, note)
    finally:
        await client.stop()


async def test_refuses_to_send_to_anyone_not_allowed(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        with pytest.raises(NotAllowedError):
            await client.send(STRANGER, "leak")
        with pytest.raises(NotAllowedError):
            await client.typing(STRANGER)
        with pytest.raises(NotAllowedError):
            await client.receipt(STRANGER, T0)
        assert [r["method"] for r in requests(fake_dir)] == ["subscribeReceive"]
    finally:
        await client.stop()


async def test_typing_and_receipt_use_signal_cli_parameter_names(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        await client.typing(ME, stop=True)
        await client.receipt(ME, T0)
        by_method = {r["method"]: r["params"] for r in requests(fake_dir)}
        assert by_method["sendTyping"] == {"recipient": [ME], "stop": True}
        assert by_method["sendReceipt"] == {"recipient": ME, "targetTimestamp": [T0], "type": "read"}
    finally:
        await client.stop()


async def test_untrusted_identity_error_is_typed(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-4")
    client = make_client()
    await client.start()
    try:
        with pytest.raises(UntrustedIdentityError) as info:
            await client.send(ME, "Hello!")
        assert info.value.code == -4
    finally:
        await client.stop()


async def test_restarts_after_a_crash_and_keeps_delivering(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_CRASH_ONCE", "1")
    client = make_client()
    await client.start()
    try:
        stream = client.envelopes()
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0, "dataMessage": {"message": "one"}})
        first = await asyncio.wait_for(anext(stream), 5)
        await until(lambda: client.restarts >= 1 and client.ready)
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0 + 1, "dataMessage": {"message": "two"}})
        second = await asyncio.wait_for(anext(stream), 5)
        assert [first["dataMessage"]["message"], second["dataMessage"]["message"]] == ["one", "two"]
    finally:
        await client.stop()


async def test_failing_signal_cli_backs_off_and_start_times_out() -> None:
    client = make_client(command=[sys.executable, "-c", "raise SystemExit(1)"], request_timeout=0.6)
    with pytest.raises(TimeoutError):
        await client.start()
    try:
        assert 1 <= client.restarts <= 8  # waits 0.05, 0.1, 0.2, 0.2 ...: not a tight loop
    finally:
        await client.stop()


async def test_get_attachment_returns_the_file_bytes(fake_dir: Path) -> None:
    (fake_dir / "attachments").mkdir()
    (fake_dir / "attachments" / "abc123.jpg").write_bytes(b"\xff\xd8\xff photo bytes")
    client = make_client()
    await client.start()
    try:
        assert await client.get_attachment(ME, "abc123.jpg") == b"\xff\xd8\xff photo bytes"
        request = next(r for r in requests(fake_dir) if r["method"] == "getAttachment")
        assert request["params"] == {"id": "abc123.jpg", "recipient": ME}
        with pytest.raises(SignalError):
            await client.get_attachment(ME, "missing.jpg")
        with pytest.raises(NotAllowedError):
            await client.get_attachment(STRANGER, "abc123.jpg")
    finally:
        await client.stop()


async def test_a_20_mb_photo_fits_in_one_json_line(fake_dir: Path) -> None:
    (fake_dir / "attachments").mkdir()
    big = bytes(range(256)) * (20_000_000 // 256)  # about 26.7 MB as base64 in one line
    (fake_dir / "attachments" / "big.jpg").write_bytes(big)
    client = make_client()
    await client.start()
    try:
        assert await client.get_attachment(ME, "big.jpg") == big
        assert client.restarts == 0
    finally:
        await client.stop()


async def test_an_oversized_line_is_dropped_without_a_restart(fake_dir: Path) -> None:
    (fake_dir / "attachments").mkdir()
    (fake_dir / "attachments" / "huge.jpg").write_bytes(b"\xff" * 200_000)  # ~270 KB of base64 in one line
    (fake_dir / "attachments" / "small.jpg").write_bytes(b"\xff\xd8\xff small")
    client = make_client(read_limit=64 * 1024, request_timeout=1.0)
    await client.start()
    try:
        with pytest.raises(SignalError):
            await client.get_attachment(ME, "huge.jpg")  # its answer is dropped: the request times out
        assert await client.get_attachment(ME, "small.jpg") == b"\xff\xd8\xff small"
        assert client.restarts == 0
    finally:
        await client.stop()


def test_the_jvm_build_gets_no_java_argument() -> None:
    native = signal_cli_command("/x/signal-cli", "+306900000000")
    jvm = signal_cli_command("/x/signal-cli", "+306900000000", jvm=True)
    assert "-Djava.net.preferIPv4Stack=true" not in jvm  # the JVM launcher would pass it to signal-cli: JAVA_OPTS
    assert jvm == [native[0], *native[2:]]


def test_signal_cli_is_told_its_data_folder(tmp_path: Path) -> None:
    # live 13 Oct: under her own user macOS kept /var/empty as the account's home and Java (signal-cli) reads its home
    # from there, not from $HOME: the folder is given explicitly
    command = signal_cli_command("/x/signal-cli", "+306900000000", config_dir=tmp_path / "signal-cli")
    assert command[command.index("--config") + 1] == str(tmp_path / "signal-cli")
    assert command.index("--config") < command.index("-a")
    assert "--config" not in signal_cli_command("/x/signal-cli", "+306900000000")


def test_call_env_points_signal_cli_at_java_and_the_call_helper(tmp_path: Path) -> None:
    env = call_env(java_home=Path("/opt/jdk"), tunnel=Path("/t/sct"), socket_dir=tmp_path, home=Path("/var/estia"))
    assert env == {
        "JAVA_HOME": "/opt/jdk",
        "JAVA_OPTS": "-Djava.net.preferIPv4Stack=true -Duser.home=/var/estia",
        "SIGNAL_CALL_TUNNEL_BIN": "/t/sct",
        "SIGNAL_CALL_TUNNEL_AUDIO_MODE": "pipe",
        "SIGNAL_CALL_TUNNEL_SOCKET_DIR": str(tmp_path),
    }
    spaced = call_env(java_home=None, tunnel=Path("/t/sct"), socket_dir=tmp_path, home=Path("/tmp/a b"))
    assert spaced["JAVA_OPTS"] == "-Djava.net.preferIPv4Stack=true"  # JAVA_OPTS splits on spaces: such a home stays out


async def test_the_child_gets_the_extra_environment_on_top_of_the_bridges(fake_dir: Path) -> None:
    client = make_client(env={"JAVA_OPTS": "-Dx=1"})
    await client.start()  # FAKE_SIGNAL_DIR still comes from the bridge's own environment
    try:
        launch = json.loads((fake_dir / "launch.json").read_text())
        assert launch["env"]["JAVA_OPTS"] == "-Dx=1"
    finally:
        await client.stop()


async def test_with_calls_it_subscribes_to_call_events_before_receiving(fake_dir: Path) -> None:
    client = make_client(calls=True)
    await client.start()
    try:
        assert [r["method"] for r in requests(fake_dir)][:2] == ["subscribeCallEvents", "subscribeReceive"]
    finally:
        await client.stop()


async def test_without_calls_there_is_no_call_subscription(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        assert [r["method"] for r in requests(fake_dir)] == ["subscribeReceive"]
    finally:
        await client.stop()


async def test_a_failed_call_subscription_does_not_stop_texting(
    fake_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_NO_CALLS", "1")
    client = make_client(calls=True)
    await client.start()
    try:
        assert client.ready
    finally:
        await client.stop()


async def test_call_events_have_their_own_stream(fake_dir: Path) -> None:
    client = make_client(calls=True)
    await client.start()
    try:
        event = {"callId": -5, "state": "RINGING_INCOMING", "uuid": ME, "isOutgoing": False}
        deliver(fake_dir, {"method": "callEvent", "params": {"subscription": 1, "result": event}})
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0, "dataMessage": {"message": "hi"}})
        assert await asyncio.wait_for(anext(client.call_events()), 5) == event
        envelope = await asyncio.wait_for(anext(client.envelopes()), 5)
        assert envelope["dataMessage"]["message"] == "hi"  # an envelope after it still arrives
    finally:
        await client.stop()


async def test_a_signal_cli_restart_ends_any_call(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_CRASH_ONCE", "1")
    client = make_client(calls=True)
    await client.start()
    try:
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0, "dataMessage": {"message": "crash"}})
        ended = await asyncio.wait_for(anext(client.call_events()), 5)
        assert ended == {"state": "ENDED", "reason": "signal_cli_restarted"}
    finally:
        await client.stop()


async def test_a_call_is_placed_only_to_an_allowed_account(fake_dir: Path) -> None:
    client = make_client(calls=True)
    await client.start()
    try:
        result = await client.start_call(ME)
        assert result["callId"] == 42
        ringing = client._call_events.get_nowait()  # signal-cli sends the ringing event before startCall's reply
        assert (ringing["state"], ringing["isOutgoing"], ringing["uuid"]) == ("RINGING_OUTGOING", True, ME)
        with pytest.raises(NotAllowedError):
            await client.start_call(STRANGER)
        calls = [(r["method"], r["params"]) for r in requests(fake_dir) if r["method"] == "startCall"]
        assert calls == [("startCall", {"recipient": [ME]})]
    finally:
        await client.stop()


async def test_accept_and_hangup_only_for_allowed_callers(fake_dir: Path) -> None:
    client = make_client(calls=True)
    await client.start()
    try:
        result = await client.accept_call(ME, -5)
        assert result["state"] == "CONNECTING"
        await client.hangup_call(ME, -5)
        with pytest.raises(NotAllowedError):
            await client.accept_call(STRANGER, 6)
        with pytest.raises(NotAllowedError):
            await client.hangup_call(STRANGER, 6)
        calls = [(r["method"], r["params"]) for r in requests(fake_dir) if r["method"].endswith("Call")]
        assert calls == [("acceptCall", {"callId": -5}), ("hangupCall", {"callId": -5})]
    finally:
        await client.stop()


# hardening: the identity check pinned, receive exceptions kept, linked devices listed


@pytest.mark.parametrize("jvm", [False, True])
def test_new_identities_are_trusted_only_on_first_use(jvm: bool) -> None:
    """A changed safety number is never trusted by itself (a SIM swap of the user's number looks just like one): the
    flag is pinned in the command, not left to signal-cli's default."""
    command = signal_cli_command("/x/signal-cli", "+306900000000", jvm=jvm)
    assert command.count("--trust-new-identities") == 1
    assert command[command.index("--trust-new-identities") + 1] == "on-first-use"
    assert "always" not in command
    assert command.index("--trust-new-identities") < command.index("jsonRpc")  # a global option


UNTRUSTED: dict[str, Any] = {
    "exception": {"message": f"Untrusted identity: {ME}", "type": "UntrustedIdentityException"},
    "envelope": {
        "source": ME,
        "sourceNumber": None,
        "sourceUuid": ME,
        "sourceName": None,
        "sourceDevice": 1,
        "timestamp": T0,
        "serverReceivedTimestamp": T0 + 5,
        "serverDeliveredTimestamp": T0 + 9,
    },
}


async def test_a_receive_exception_is_kept_on_its_own_stream(fake_dir: Path) -> None:
    """signal-cli 0.14.9 puts an `exception` (message, type) next to the envelope of a message it could not open,
    such as one from a changed safety number: it reaches the bridge as a problem (type, sender, device; never the
    exception's message, which names the account), instead of being dropped."""
    client = make_client()
    await client.start()
    try:
        deliver(fake_dir, UNTRUSTED)
        deliver(fake_dir, {"exception": {"message": "Invalid message", "type": "InvalidMessageException"}})
        problems = client.problems()
        first = await asyncio.wait_for(anext(problems), 5)
        assert (first.kind, first.sender, first.device) == ("UntrustedIdentityException", ME, 1)
        assert first.untrusted_identity
        second = await asyncio.wait_for(anext(problems), 5)  # no envelope at all: still reported
        assert (second.kind, second.sender, second.device, second.untrusted_identity) == (
            "InvalidMessageException",
            None,
            None,
            False,
        )
        assert not hasattr(first, "message")  # the exception's text (it names the account) is not kept
    finally:
        await client.stop()


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("Untrusted\nidentity <script>", "Untrustedidentityscript"), ("", "unknown"), (None, "unknown"), (7, "unknown")],
)
def test_a_problems_type_is_kept_as_a_plain_word(kind: object, expected: str) -> None:
    client = make_client()
    client._dispatch({"method": "receive", "params": {"result": {"exception": {"type": kind}}}})
    assert client._problems.get_nowait().kind == expected


async def test_linked_devices_are_listed_as_ids_and_creation_times_only(
    fake_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    devices = [
        {"id": 1, "name": None, "createdTimestamp": 1_700_000_000_000, "lastSeenTimestamp": 1_760_000_000_000},
        {"id": 3, "name": "Example tablet", "createdTimestamp": 1_760_000_100_000, "lastSeenTimestamp": None},
    ]
    monkeypatch.setenv("FAKE_SIGNAL_DEVICES", json.dumps(devices))
    client = make_client()
    await client.start()
    try:
        listed = await client.list_devices()
        assert [(device.id, device.created) for device in listed] == [(1, 1_700_000_000_000), (3, 1_760_000_100_000)]
        assert "Example tablet" not in repr(listed)  # names are never kept
        assert requests(fake_dir)[-1]["method"] == "listDevices"
    finally:
        await client.stop()


async def test_odd_device_entries_are_left_out(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    devices: list[object] = [{"id": 1}, {"id": "2"}, {"id": True}, "x", {"id": 0}, {"id": 4, "createdTimestamp": "?"}]
    monkeypatch.setenv("FAKE_SIGNAL_DEVICES", json.dumps(devices))
    client = make_client()
    await client.start()
    try:
        assert [(device.id, device.created) for device in await client.list_devices()] == [(1, None), (4, None)]
    finally:
        await client.stop()


async def test_the_fake_names_the_sending_device_as_signal_cli_does(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0, "dataMessage": {"message": "hi"}})
        deliver(fake_dir, {"sourceUuid": ME, "sourceDevice": 2, "timestamp": T0 + 1, "dataMessage": {"message": "x"}})
        stream = client.envelopes()
        assert (await asyncio.wait_for(anext(stream), 5))["sourceDevice"] == 1
        assert (await asyncio.wait_for(anext(stream), 5))["sourceDevice"] == 2
    finally:
        await client.stop()
