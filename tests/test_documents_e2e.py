"""Documents end to end, with the real helper: tools/documents' release build at the config's default path (the same
path in a checkout, where scripts/run.sh and scripts/verify.sh build it, and in a release, where estia-deploy does)
reads PDFs made here (text pages through PDFKit, a scanned page through Vision), and the bridge indexes and searches
them. The folder and the database are temporary ones. Skipped when the helper is not built, except under
scripts/verify.sh (DOCUMENTS_HELPER_REQUIRED). A new build's first scan waits for macOS to prepare Vision's model
(50-70 s, tools/documents/README.md): the warm-up at the start, as in the bridge, takes that wait."""

import json
import os
import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.config import CODE_ROOT, Config
from local_assistant.documents import Documents, Helper, helper_env
from local_assistant.store import Store
from tests.conftest import TZ
from tests.fakes.fake_gemma import FakeGemma, text, tool
from tests.test_app import T0, deliver, put, sent, start, stop, until
from tests.test_app_documents import attachment, shown

WHEN = datetime(2026, 10, 14, 9, 30, tzinfo=TZ)
COLD = 240.0  # seconds a first scan of a new build may wait for Vision's model (measured 50-70 s), with room
LEASE = [
    ["Lease for the flat at Example Street 1", "Rent is due on the first day of each month."],
    ["Pets: one cat is allowed, dogs are not.", "Le café du coin is downstairs."],
]
BOILER = ["Boiler manual", "Reset: hold the red button for ten seconds.", "Service every twelve months."]
POLICY = ["Home insurance policy", "The deductible is 250 euros per claim."]


def helper_command() -> tuple[str, ...]:
    """The config's default helper (no [documents] helper setting exists); built, or the test is skipped."""
    command = Config(bot_number="+306900000000", allowed_acis=frozenset()).documents_helper
    assert command == (str(CODE_ROOT / "tools/documents/.build/release/documents-helper"),)
    if not os.access(command[0], os.X_OK):
        if os.environ.get("DOCUMENTS_HELPER_REQUIRED"):
            pytest.fail("the documents helper is not at the config's default path after scripts/verify.sh built it")
        pytest.skip("the documents helper is not built: cd tools/documents && swift build -c release")
    return command


# PDFs written by hand: no producer, no dates, nothing but the pages


def _pdf(objects: Sequence[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%b\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def _stream(data: bytes, keys: bytes = b"") -> bytes:
    return b"<< %b /Length %d >>\nstream\n%b\nendstream" % (keys, len(data), data)


def text_pdf(pages: Sequence[Sequence[str]], size: tuple[int, int] = (612, 792), font: int = 12) -> bytes:
    """Pages of Helvetica text lines (Windows-1252: accents too), one line under the other."""
    count = len(pages)
    kids = b" ".join(b"%d 0 R" % (3 + 2 * index) for index in range(count))
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [%b] /Count %d >>" % (kids, count)]
    font_object = 3 + 2 * count
    for index, lines in enumerate(pages):
        shown_lines = b" ".join(
            b"(%b) Tj T*" % line.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
            for line in lines
        )
        content = b"BT /F1 %d Tf %d TL 72 %d Td %b ET" % (font, font * 13 // 10, size[1] - 72, shown_lines)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 %d 0 R >> >>"
            b" /Contents %d 0 R >>" % (size[0], size[1], font_object, 4 + 2 * index)
        )
        objects.append(_stream(content))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    return _pdf(objects)


def _jpeg_size(jpeg: bytes) -> tuple[int, int, int]:
    """(width, height, colour components) from a JPEG's frame header."""
    index = 2
    while index + 9 < len(jpeg):
        marker, length = jpeg[index + 1], int.from_bytes(jpeg[index + 2 : index + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            height = int.from_bytes(jpeg[index + 5 : index + 7], "big")
            return int.from_bytes(jpeg[index + 7 : index + 9], "big"), height, jpeg[index + 9]
        index += 2 + length
    raise ValueError("no JPEG frame header")


def scan_pdf(lines: Sequence[str], work: Path) -> bytes:
    """One page that is only a picture of text, like a scan: macOS's sips draws a large text page as a JPEG (one point
    a pixel), which fills a half-letter page at about 200 dots per inch. PDFKit finds no text in it: Vision reads it."""
    work.mkdir(parents=True, exist_ok=True)
    (work / "large.pdf").write_bytes(text_pdf([lines], size=(1700, 1100), font=48))
    subprocess.run(
        ["/usr/bin/sips", "-s", "format", "jpeg", str(work / "large.pdf"), "--out", str(work / "page.jpg")],
        check=True,
        capture_output=True,
    )
    jpeg = (work / "page.jpg").read_bytes()
    width, height, components = _jpeg_size(jpeg)
    colours = {1: b"/DeviceGray", 3: b"/DeviceRGB"}[components]
    image = b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %b /BitsPerComponent 8 /Filter /DCTDecode"
    return _pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 396] /Resources << /XObject << /Im1 4 0 R >> >>"
            b" /Contents 5 0 R >>",
            _stream(jpeg, image % (width, height, colours)),
            _stream(b"q 612 0 0 396 0 0 cm /Im1 Do Q"),
        ]
    )


async def test_the_real_helper_reads_text_and_scanned_pdfs_into_her_index_and_her_search_finds_them(
    store: Store, tmp_path: Path
) -> None:
    command = helper_command()
    folder = tmp_path / "Estia-KB"
    (folder / "2026").mkdir(parents=True)
    (folder / ".estia-share").write_text("0123456789abcdef0123456789abcdef\n")  # as scripts/share-documents.sh does
    (folder / "lease.pdf").write_bytes(text_pdf(LEASE))
    (folder / "2026" / "boiler manual.pdf").write_bytes(scan_pdf(BOILER, tmp_path / "scan"))
    documents = Documents(store, folder, Helper(command, 75.0), tz=TZ)
    assert await documents.warm()  # as the bridge does at its start, before a scan needs Vision's model

    look = await documents.look()
    assert (look.read, look.left, look.problem) == (2, 0, "")
    files = {file.path: file for file in documents.files()}
    assert (files["lease.pdf"].pages, files["lease.pdf"].error) == (2, "")
    assert (files["2026/boiler manual.pdf"].pages, files["2026/boiler manual.pdf"].error) == (1, "")

    pets = documents.search_text("cat")
    assert pets.startswith(prompts.DOCUMENTS_PASSAGES) and '[1] "lease.pdf", page 2: Pets: one cat is allowed' in pets
    assert "café" in documents.search_text("downstairs")  # PDFKit's text keeps the accent
    reset = documents.search_text("red button")
    assert '[1] "2026/boiler manual.pdf", page 1:' in reset and "hold the red button for ten seconds" in reset
    scanned = documents.read_text("boiler manual", 1)
    assert prompts.DOCUMENT_BY_VISION in scanned  # read by Vision: the page says so
    assert scanned.endswith("Boiler manual\nReset: hold the red button for ten seconds.\nService every twelve months.")
    assert prompts.DOCUMENT_BY_VISION not in documents.read_text("lease.pdf", 2)

    assert (await documents.look()).read == 0  # nothing changed: nothing is read again

    saved = await documents.save(text_pdf([POLICY]), "Home insurance.pdf", WHEN)  # a PDF the user sent
    assert (saved.path, saved.pages, saved.error) == ("2026-10-14 Home insurance.pdf", 1, "")
    assert '[1] "2026-10-14 Home insurance.pdf", page 1: Home insurance policy' in documents.search_text("deductible")


async def test_a_pdf_sent_on_signal_is_read_by_the_real_helper_and_her_search_tool_finds_its_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole bridge: the fake signal-cli delivers a PDF with a question; it is saved and read by the real helper
    (after the warm-up at the bridge's start), and search_documents gives Gemma the page."""
    helper_command()
    folder = tmp_path / "Estia-KB"
    folder.mkdir(parents=True)
    gemma = FakeGemma(tool("search_documents", words="deductible"), text("250 euros per claim (page 2)."))
    harness = await start(tmp_path, monkeypatch, gemma, documents_enabled=True, documents_folder=folder)
    try:
        policy = text_pdf([["Home insurance policy"], POLICY[1:]])
        put(harness.folder, "a1.pdf", policy)
        deliver(harness.folder, "what is the deductible?", T0, attachments=[attachment("a1.pdf", policy, "Policy.pdf")])
        await until(lambda: len(sent(harness.folder)) == 2, limit=COLD)
        day = datetime.fromtimestamp(T0 / 1000, TZ).date()
        saved_as = f"{day.isoformat()} Policy.pdf"
        assert shown(harness.folder) == [
            f"Saved in your Estia-KB folder: {saved_as} (2 pages).",
            "250 euros per claim (page 2).",
        ]
        assert (folder / saved_as).read_bytes() == policy
        results = [message["content"] for message in gemma.calls[1]["messages"] if message["role"] == "tool"]
        assert len(results) == 1 and f'[1] "{saved_as}", page 2: The deductible is 250 euros per claim.' in results[0]
    finally:
        await stop(harness)


# Vision on macOS 27 fails without Foundation's home; on macOS 26.6 in a test VM it read the page anyway (16 Oct)
NO_HOME_NEEDED = "Vision on this Mac reads without Foundation's home: its failure cannot be shown here"


def test_text_recognition_needs_foundations_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live, 15 Oct: as her, Foundation's home was her account record's /var/empty (helper_env passed HOME alone), and
    every Vision scan failed in under 0.3 s: scanned pages and the warm-up failed, text pages still read. The real
    helper fails with that home and works with helper_env's, whatever the bridge's own CFFIXED_USER_HOME says."""
    helper = helper_command()[0]

    def warm(env: dict[str, str]) -> object:
        done = subprocess.run(
            [helper, "warm", "--max-seconds", str(int(COLD))],
            env=env,
            capture_output=True,
            text=True,
            timeout=COLD + 30,
            check=False,
        )
        return json.loads(done.stdout.strip().splitlines()[-1]).get("ok")

    without_home = warm({"PATH": "/usr/bin:/bin", "HOME": "/var/empty", "CFFIXED_USER_HOME": "/var/empty"})
    monkeypatch.setenv("CFFIXED_USER_HOME", "/var/empty")
    assert warm(helper_env()) is True
    if without_home is not False:
        pytest.skip(NO_HOME_NEEDED)


def test_a_scanned_page_vision_fails_on_is_named_apart_from_an_unreadable_one(tmp_path: Path) -> None:
    """With /var/empty as Foundation's home Vision fails at once: the page is skipped as "ocr_failed" (read again
    after a start, and the part's warning), never as "unreadable" (a page that cannot be read)."""
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(scan_pdf(BOILER, tmp_path))
    done = subprocess.run(
        [helper_command()[0], "read", str(scan), "--max-pages", "300", "--max-seconds", "60"],
        env={"PATH": "/usr/bin:/bin", "HOME": "/var/empty", "CFFIXED_USER_HOME": "/var/empty"},
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    answer = json.loads(done.stdout.strip().splitlines()[-1])
    if answer["ok"] and answer["pages"]:
        pytest.skip(NO_HOME_NEEDED)
    assert (answer["ok"], answer["pages"], answer["skipped"]) == (True, [], [{"n": 1, "why": "ocr_failed"}])
