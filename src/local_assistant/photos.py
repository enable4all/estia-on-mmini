"""Photos for Gemma: decide the type from the first bytes, convert with macOS `sips`.

The type comes from the file itself, never from the content type the sender claims (Hermes Signal
adapter). `sips` is built into macOS and reads HEIC; the result is a JPEG with the longest side
limited, which Gemma gets as a data URI. Temporary files are always removed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import re
import uuid
from pathlib import Path

SIPS = "/usr/bin/sips"
SIPS_TIMEOUT = 30.0
MAX_PIXELS = 100_000_000
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
_SIZE = re.compile(r"pixel(?:Width|Height): (\d+)")
_EXTENSIONS = {"jpeg": "jpg", "png": "png", "gif": "gif", "webp": "webp", "heic": "heic"}


class PhotoError(Exception):
    """The photo cannot be used; the message says why (for the log, never the image data)."""


def sniff(data: bytes) -> str | None:
    """'jpeg', 'png', 'gif', 'webp' or 'heic' from the first bytes; None for anything else."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "heic"
    return None


async def prepare(
    data: bytes, workdir: Path, *, max_side: int = 1536, max_pixels: int = MAX_PIXELS, sips: str = SIPS
) -> bytes:
    """A JPEG of the photo with the longest side at most `max_side` pixels (smaller photos keep their size)."""
    kind = sniff(data)
    if kind is None:
        raise PhotoError("not a photo (unknown file type)")
    source, target = await asyncio.to_thread(_stage, workdir, kind, data)
    try:
        sizes = _SIZE.findall(await _sips([sips, "-g", "pixelWidth", "-g", "pixelHeight", str(source)]))
        if len(sizes) != 2:
            raise PhotoError("could not read the photo (no size)")
        width, height = int(sizes[0]), int(sizes[1])
        if width * height > max_pixels:  # a decompression bomb would eat memory next to the loaded model
            raise PhotoError(f"photo too large ({width}×{height} pixels)")
        resize = ["-Z", str(max_side)] if max(width, height) > max_side else []  # -Z also enlarges
        await _sips([sips, "-s", "format", "jpeg", *resize, str(source), "--out", str(target)])
        jpeg = await asyncio.to_thread(target.read_bytes)
        if sniff(jpeg) != "jpeg":
            raise PhotoError("could not read the photo (no JPEG out of sips)")
        return jpeg
    except FileNotFoundError as exc:
        raise PhotoError("could not read the photo (no output)") from exc
    finally:
        await asyncio.to_thread(_remove, source, target)


def _stage(workdir: Path, kind: str, data: bytes) -> tuple[Path, Path]:
    """Write the photo to a new private file; return it and the path for the JPEG."""
    workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = uuid.uuid4().hex
    source = workdir / f"{name}.{_EXTENSIONS[kind]}"
    fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return source, workdir / f"{name}-out.jpg"


def _remove(*paths: Path) -> None:
    for path in paths:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


async def _sips(argv: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), SIPS_TIMEOUT)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise PhotoError("could not read the photo (sips timed out)") from exc
    if proc.returncode != 0:
        raise PhotoError(f"could not read the photo (sips exit {proc.returncode})")
    return out.decode("utf-8", "replace")


def data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
