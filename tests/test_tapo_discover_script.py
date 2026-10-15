"""scripts/tapo-discover.sh in a fake checkout: the device helper's Python is a stub (the TP-Link listing fails, the
Hue lights answer), the home a temporary one, and no own-user install (ESTIA_TEST_ROOT names a tree that never
exists, so no sudo is ever run)."""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NO_TREE = "/var/empty/estia-test-root"


def checkout(tmp_path: Path, answer: str, code: int = 1) -> Path:
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("tapo-discover.sh", "own-user.sh"):
        shutil.copy(ROOT / "scripts" / name, scripts / name)
    python = tmp_path / "checkout" / "tools" / "tapo" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/bin/bash\n"
        'case "$1" in\n'
        f"  helper.py) echo '{answer}'; exit {code} ;;\n"
        f'  -c) exec "{sys.executable}" "$@" ;;\n'
        '  -I) echo "Hue lights: hall light"; exit 0 ;;\n'
        "esac\n"
        "exit 9\n"
    )
    python.chmod(0o755)
    hue = tmp_path / "home" / ".config" / "local-assistant" / "hue.env"
    hue.parent.mkdir(parents=True)
    hue.write_text("HUE_BRIDGE=192.168.1.40\n")
    return scripts / "tapo-discover.sh"


def run(script: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(script)],
        env={"ESTIA_TEST_ROOT": NO_TREE, "HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_failed_tp_link_listing_still_lists_the_hue_lights_and_fails(tmp_path: Path) -> None:
    done = run(checkout(tmp_path, '{"ok": false, "error": "unreachable", "detail": "no answer in time"}'), tmp_path)
    assert "discovery failed: unreachable" in done.stderr
    assert "Hue lights: hall light" in done.stdout
    assert done.returncode != 0


def test_a_device_that_needs_the_login_is_listed_by_address_model_and_login(tmp_path: Path) -> None:
    """The listing sends no login: such a device's name is the one in the app."""
    answer = (
        '{"ok": true, "devices": [{"host": "192.168.1.20", "alias": "desk strip", "model": "KL430", "kind": "light"}, '
        '{"host": "192.168.1.21", "model": "KP125M", "login": "KLAP"}], '
        '"unsupported": [{"host": "192.168.1.22", "type": "SMART.TAPOPLUG", "encryption": "AES v1"}]}'
    )
    done = run(checkout(tmp_path, answer, code=0), tmp_path)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[1].split() == ["desk", "strip", "KL430", "light", "192.168.1.20"]
    assert "KP125M" in lines[2] and "192.168.1.21" in lines[2] and "(TP-Link login: KLAP)" in lines[2]
    assert "not controllable yet: SMART.TAPOPLUG at 192.168.1.22 (TP-Link login: AES v1)" in done.stdout
