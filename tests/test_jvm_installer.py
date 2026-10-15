"""tools/call-tunnel/install-signal-cli-jvm.sh: the release is unpacked only when the maintainer's key and the
signature's signer both have the pinned fingerprint. Run with stub gh, gpg and brew on PATH, into a temporary folder."""

import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "call-tunnel" / "install-signal-cli-jvm.sh"
PINNED = "FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570"
OTHER = "0123456789ABCDEF0123456789ABCDEF01234567"
VERSION = "0.14.9"


def stub(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def release(path: Path) -> None:
    """A release tarball with a signal-cli launcher that prints its version."""
    launcher = f"#!/bin/sh\necho 'signal-cli {VERSION}'\n".encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(f"signal-cli-{VERSION}/bin/signal-cli")
        info.size, info.mode = len(launcher), 0o755
        archive.addfile(info, io.BytesIO(launcher))


def run(
    tmp_path: Path,
    *,
    key: str,
    signer: str,
    primary: str | None = None,
    signed_in: bool = True,
    java: bool = True,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    tarball = tmp_path / "release.tar.gz"
    release(tarball)
    stub(
        bin_dir,
        "gh",
        f'case "$1" in\n'
        f"  auth) exit {0 if signed_in else 1} ;;\n"
        f'  release) cp "{tarball}" "signal-cli-{VERSION}.tar.gz"; echo sig > "signal-cli-{VERSION}.tar.gz.asc" ;;\n'
        f"  api) echo '-----BEGIN PGP PUBLIC KEY BLOCK-----' ;;\n"
        f"esac",
    )
    validsig = f"{signer} 2026-01-01 1767225600 0 4 0 1 10 00" + (f" {primary}" if primary else "")
    stub(
        bin_dir,
        "gpg",
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        f'    --fingerprint) printf "pub:-:4096:1:AAAA:1:::-:::scESC::::::23::0:\\nfpr:::::::::{key}:\\n"; exit 0 ;;\n'
        f'    --verify) echo "[GNUPG:] GOODSIG AAAA AsamK"; echo "[GNUPG:] VALIDSIG {validsig}"; exit 0 ;;\n'
        "  esac\n"
        "done\n"
        "exit 0",
    )
    stub(bin_dir, "brew", f'echo "{tmp_path / "brew"}"')
    if java:
        (tmp_path / "brew" / "opt" / "openjdk" / "bin").mkdir(parents=True, exist_ok=True)
        stub(tmp_path / "brew" / "opt" / "openjdk" / "bin", "java", "exit 0")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "LOCAL_ASSISTANT_VENDOR": str(tmp_path / "vendor"),
        "TMPDIR": str(tmp_path),
    }
    return subprocess.run(["/bin/sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def unpacked(tmp_path: Path) -> Path:
    return tmp_path / "vendor" / f"signal-cli-{VERSION}-jvm" / f"signal-cli-{VERSION}" / "bin" / "signal-cli"


def test_the_pinned_fingerprint_is_in_the_script() -> None:
    assert f"FINGERPRINT={PINNED}" in SCRIPT.read_text()


@pytest.mark.parametrize("primary", [None, PINNED])
def test_the_pinned_key_and_signer_unpack_the_release(tmp_path: Path, primary: str | None) -> None:
    done = run(tmp_path, key=PINNED, signer=PINNED, primary=primary)
    assert done.returncode == 0, done.stderr
    assert unpacked(tmp_path).is_file()
    assert f"signal-cli {VERSION}" in done.stdout


def test_a_subkey_signature_counts_by_its_primary_key(tmp_path: Path) -> None:
    done = run(tmp_path, key=PINNED, signer=OTHER, primary=PINNED)
    assert done.returncode == 0, done.stderr
    assert unpacked(tmp_path).is_file()


@pytest.mark.parametrize(
    ("key", "signer", "primary"),
    [
        (OTHER, PINNED, None),  # GitHub served another key
        (PINNED, OTHER, None),  # signed by another key
        (PINNED, OTHER, OTHER),
    ],
)
def test_any_other_fingerprint_stops_before_unpacking(
    tmp_path: Path, key: str, signer: str, primary: str | None
) -> None:
    done = run(tmp_path, key=key, signer=signer, primary=primary)
    assert done.returncode == 1
    assert "fingerprint" in done.stderr
    assert not unpacked(tmp_path).exists()
    assert not unpacked(tmp_path).parents[2].exists()  # nothing half-made: a run again is not blocked (finding 13)


def test_a_missing_github_sign_in_is_named_before_anything_is_made(tmp_path: Path) -> None:
    """gh not signed in: said first, with the command, and nothing is left behind (findings 13, 60)."""
    done = run(tmp_path, key=PINNED, signer=PINNED, signed_in=False)
    assert done.returncode == 1 and "gh auth login" in done.stderr
    assert not (tmp_path / "vendor").exists()
    again = run(tmp_path, key=PINNED, signer=PINNED)
    assert again.returncode == 0, again.stderr
    assert unpacked(tmp_path).is_file()


def test_java_is_checked_before_the_download(tmp_path: Path) -> None:
    done = run(tmp_path, key=PINNED, signer=PINNED, java=False)
    assert done.returncode == 1 and "brew install openjdk" in done.stderr
    assert not (tmp_path / "vendor").exists()


def test_the_usage_stays_version_and_target_folder() -> None:
    text = SCRIPT.read_text()
    assert "Usage: tools/call-tunnel/install-signal-cli-jvm.sh [version] [target dir]" in text
    assert 'VENDOR="${LOCAL_ASSISTANT_VENDOR:-' in text
    assert os.access(SCRIPT, os.X_OK)


def test_the_builds_go_into_the_checkouts_vendor_folder_by_default() -> None:
    """Her own user's release copies the call helpers from the checkout's vendor/ (gitignored), the first place
    estia-deploy looks; her old data folder is held away from you after the move."""
    for script in (SCRIPT, SCRIPT.with_name("build.sh")):
        text = script.read_text()
        assert 'REPO=$(cd "$(dirname "$0")/../.." && pwd)' in text, script.name
        assert 'VENDOR="${LOCAL_ASSISTANT_VENDOR:-$REPO/vendor}"' in text, script.name
        assert "Application Support" not in text, script.name
