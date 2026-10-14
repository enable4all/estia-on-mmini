"""The owner's maintenance commands that run as her, through `/usr/bin/sudo /usr/local/estia/bin/estia` (which stops the bridge
first and starts it again after: one signal-cli per account, and the lock is read at the start):

    estia signal listIdentities|trust|listDevices|removeDevice [options]   her signal-cli, with the bridge's flags
    estia unlock                                                           ends /lock from the Mac

Only those subcommands: nothing that sends, registers or changes her profile. The command runs from / with her
config's signal-cli (the JVM build with Java and IPv4 in its environment when [calls] is on).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

from local_assistant.config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config
from local_assistant.signal_io import IPV4, TRUST_NEW_IDENTITIES
from local_assistant.store import Store

SUBCOMMANDS = frozenset({"listIdentities", "trust", "listDevices", "removeDevice"})
LOCK_KEYS = ("lock:on", "lock:device", "lock:since")  # commands.LOCK_KEY, LOCK_DEVICE_KEY, LOCK_SINCE_KEY
_WORD = re.compile(r"^[A-Za-z0-9+_.:= -]{1,200}$")


def signal_command(cfg: Config, words: list[str]) -> tuple[list[str], dict[str, str]]:
    """Her signal-cli for one of SUBCOMMANDS: (argv, extra environment). Anything else is a ValueError."""
    if not words or words[0] not in SUBCOMMANDS:
        raise ValueError(f"only {', '.join(sorted(SUBCOMMANDS))}")
    for word in words[1:]:
        if not _WORD.match(word) or word.startswith("--config") or word == "-c":
            raise ValueError("an option that is not allowed here")
    if cfg.calls_enabled:
        java = cfg.java_home
        env = {**({"JAVA_HOME": str(java)} if java is not None else {}), "JAVA_OPTS": IPV4}
        return [str(cfg.call_signal_cli), *TRUST_NEW_IDENTITIES, "-a", cfg.bot_number, *words], env
    return [cfg.signal_cli, IPV4, *TRUST_NEW_IDENTITIES, "-a", cfg.bot_number, *words], {}


def unlock(cfg: Config) -> bool:
    """Clear the lock (/lock) in her database: whether she was locked."""
    store = Store.open(cfg.db_path)
    try:
        locked = store.get_state(LOCK_KEYS[0]) == "on"
        for key in LOCK_KEYS:
            store.delete_state(key)
    finally:
        store.close()
    return locked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="admin", description="Her maintenance commands (run as her).")
    sub = parser.add_subparsers(dest="command", required=True)
    signal_cmd = sub.add_parser("signal")
    signal_cmd.add_argument("words", nargs=argparse.REMAINDER)
    sub.add_parser("unlock")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(DEFAULT_CONFIG_PATH)
    except ConfigError as exc:
        print(f"✗ config: {exc}", file=sys.stderr)
        return 1
    if args.command == "unlock":
        print("✓ Unlocked: she answers again." if unlock(cfg) else "She was not locked.")
        return 0
    try:
        command, extra = signal_command(cfg, list(args.words))
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    done = subprocess.run(command, cwd="/", env={**os.environ, **extra}, check=False)  # her environment (env -i)
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main())
