"""The owner's maintenance commands that run as her (`sudo /usr/local/estia/bin/estia signal …` and `… unlock`):
her own signal-cli with the bridge's flags, only the subcommands named, and the lock ended from the Mac."""

import dataclasses
from pathlib import Path

import pytest

from local_assistant import admin
from local_assistant.commands import LOCK_DEVICE_KEY, LOCK_KEY, LOCK_SINCE_KEY
from local_assistant.config import load_config
from local_assistant.store import Store

NUMBER = "+306900000000"
ACI = "11111111-2222-4333-8444-555555555555"


def config(tmp_path: Path, calls: bool = False) -> Path:
    path = tmp_path / "config.toml"
    text = (
        f'bot_number = "{NUMBER}"\nallowed_acis = ["{ACI}"]\nsignal_cli = "/usr/local/estia/current/vendor/'
        'signal-cli-native/bin/signal-cli"\n'
    )
    if calls:
        text += f'[voice]\n[calls]\nsignal_cli = "{tmp_path}/jvm/bin/signal-cli"\njava_home = "{tmp_path}/jdk"\n'
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_her_signal_cli_runs_with_the_bridges_flags(tmp_path: Path) -> None:
    cfg = load_config(config(tmp_path))
    argv, env = admin.signal_command(cfg, ["listIdentities", "-n", "+306900000001"])
    assert argv[0].endswith("signal-cli-native/bin/signal-cli")
    assert argv[1:6] == ["-Djava.net.preferIPv4Stack=true", "--trust-new-identities", "on-first-use", "-a", NUMBER]
    assert argv[6:] == ["listIdentities", "-n", "+306900000001"]
    assert env == {}


def test_with_calls_the_jvm_build_gets_java_and_ipv4_from_its_environment(tmp_path: Path) -> None:
    cfg = load_config(config(tmp_path, calls=True))
    argv, env = admin.signal_command(cfg, ["listDevices"])
    assert argv[0] == f"{tmp_path}/jvm/bin/signal-cli" and "-Djava.net.preferIPv4Stack=true" not in argv
    assert env["JAVA_HOME"] == f"{tmp_path}/jdk" and env["JAVA_OPTS"] == "-Djava.net.preferIPv4Stack=true"


@pytest.mark.parametrize(
    "words",
    [
        ["send", "-m", "hi", "+306900000001"],
        ["register"],
        ["listIdentities", "--config", "/tmp/x"],
        [],
        ["trust", "\x1b"],
    ],
)
def test_only_the_named_subcommands_run(tmp_path: Path, words: list[str]) -> None:
    cfg = load_config(config(tmp_path))
    with pytest.raises(ValueError):
        admin.signal_command(cfg, words)


def test_unlock_from_the_mac_clears_the_lock(tmp_path: Path) -> None:
    cfg = dataclasses.replace(load_config(config(tmp_path)), data_dir=tmp_path / "data")  # never the real one
    cfg.data_dir.mkdir(parents=True)
    store = Store.open(cfg.db_path)
    for key, value in ((LOCK_KEY, "on"), (LOCK_DEVICE_KEY, f"{ACI} 2"), (LOCK_SINCE_KEY, "1791000000")):
        store.set_state(key, value)
    store.close()
    assert admin.unlock(cfg) is True
    store = Store.open(cfg.db_path)
    assert store.get_state(LOCK_KEY) is None and store.get_state(LOCK_DEVICE_KEY) is None
    store.close()
    assert admin.unlock(cfg) is False  # not locked: nothing to do
