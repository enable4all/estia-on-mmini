"""scripts/install-voice-daemon.sh warns on macOS 26.4 and 26.5 (an Apple bug there can crash English speech; 26.6
fixes it), before anything else. `sw_vers` is a stub; no own-user install (ESTIA_TEST_ROOT names a tree that never
exists), so the script stops right after, and no sudo is ever run."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NO_TREE = "/var/empty/estia-test-root"


@pytest.mark.parametrize(("version", "warned"), [("26.4", True), ("26.5.1", True), ("26.6", False), ("27.0.1", False)])
def test_the_install_warns_on_macos_26_4_and_26_5(tmp_path: Path, version: str, warned: bool) -> None:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "sw_vers").write_text(f'#!/bin/sh\n[ "$1" = -productVersion ] && echo {version}\n')
    (stubs / "sw_vers").chmod(0o755)
    done = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts" / "install-voice-daemon.sh")],
        env={"ESTIA_TEST_ROOT": NO_TREE, "HOME": str(tmp_path), "PATH": f"{stubs}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1 and "her own user comes first" in done.stderr
    assert ("update to macOS 26.6 or later" in done.stderr) is warned
