"""The Swift packages' test targets name the Command Line Tools' Testing macros folder (`-plugin-path`) only while
the Command Line Tools are the active developer folder (xcode-select): with Xcode active, its own compiler would
otherwise expand `@Test` with another version's macros. Read from `swift package dump-package`; skipped without swift.
The Xcode case is untested on a Mac without Xcode."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLT = "/Library/Developer/CommandLineTools"
FLAG = ["-plugin-path", f"{CLT}/usr/lib/swift/host/plugins/testing"]


def unsafe_flags(package: str) -> list[list[str]]:
    done = subprocess.run(
        ["swift", "package", "dump-package"],
        cwd=ROOT / "tools" / package,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    manifest = json.loads(done.stdout)
    return [
        setting["kind"]["unsafeFlags"]["_0"]
        for target in manifest["targets"]
        if target["type"] == "test"
        for setting in target.get("settings", [])
        if "unsafeFlags" in setting["kind"]
    ]


def active_developer_dir() -> str:
    done = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, check=False)
    return done.stdout.strip()


needs_swift = pytest.mark.skipif(shutil.which("swift") is None, reason="no swift")


@needs_swift
@pytest.mark.parametrize(("package", "targets"), [("voice", 2), ("documents", 1)])
def test_the_testing_plugin_folder_is_named_only_with_the_command_line_tools_active(package: str, targets: int) -> None:
    flags = unsafe_flags(package)
    if active_developer_dir().startswith(CLT):
        assert flags == [FLAG] * targets
    else:
        assert flags == []
