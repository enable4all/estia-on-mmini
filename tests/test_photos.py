import base64
import struct
import zlib
from pathlib import Path

import pytest

from local_assistant.photos import PhotoError, data_uri, prepare, sniff

HEIC = Path("/System/Library/Desktop Pictures/iMac Blue.heic")


def png(width: int, height: int, rgb: tuple[int, int, int] = (20, 60, 200)) -> bytes:
    """A plain one-colour PNG, built by hand (no image library needed)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def jpeg_size(data: bytes) -> tuple[int, int]:
    """(width, height) from a JPEG's start-of-frame marker."""
    index = 2
    while index < len(data):
        marker, length = data[index + 1], struct.unpack(">H", data[index + 2 : index + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        index += 2 + length
    raise AssertionError("no frame in JPEG")


@pytest.mark.parametrize(
    ("head", "kind"),
    [
        (b"\xff\xd8\xff\xe0rest", "jpeg"),
        (b"\x89PNG\r\n\x1a\nrest", "png"),
        (b"GIF89a rest", "gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00", "heic"),
        (b"\x00\x00\x00\x18ftypmif1\x00\x00", "heic"),
        (b"%PDF-1.7", None),
        (b"hello", None),
        (b"", None),
    ],
)
def test_sniff_decides_by_the_first_bytes(head: bytes, kind: str | None) -> None:
    assert sniff(head) == kind


async def test_prepare_makes_a_jpeg_with_the_longest_side_limited(tmp_path: Path) -> None:
    jpeg = await prepare(png(3000, 40), tmp_path, max_side=1536)
    assert sniff(jpeg) == "jpeg"
    assert jpeg_size(jpeg) == (1536, 20)
    assert list(tmp_path.iterdir()) == []  # no files left behind


async def test_prepare_keeps_small_photos_small(tmp_path: Path) -> None:
    jpeg = await prepare(png(64, 48), tmp_path)
    assert jpeg_size(jpeg) == (64, 48)


@pytest.mark.skipif(not HEIC.exists(), reason="macOS wallpaper not present")
async def test_prepare_reads_heic(tmp_path: Path) -> None:
    data = HEIC.read_bytes()
    assert sniff(data) == "heic"
    jpeg = await prepare(data, tmp_path, max_side=512)
    assert max(jpeg_size(jpeg)) == 512


async def test_prepare_refuses_what_is_not_an_image(tmp_path: Path) -> None:
    with pytest.raises(PhotoError, match="not a photo"):
        await prepare(b"%PDF-1.7 a document", tmp_path)
    with pytest.raises(PhotoError, match="could not read"):
        await prepare(b"\xff\xd8\xff\xe0 broken jpeg", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_data_uri() -> None:
    assert data_uri(b"\xff\xd8\xff") == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff").decode()


async def test_prepare_refuses_too_many_pixels(tmp_path: Path) -> None:
    with pytest.raises(PhotoError, match="too large"):
        await prepare(png(100, 100), tmp_path, max_pixels=5_000)
    assert list(tmp_path.iterdir()) == []
