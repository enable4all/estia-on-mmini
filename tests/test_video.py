"""YouTube videos: the strict link check, the helper run as a child with fixed arguments, the parts of a long video and
the last video kept in memory for follow-up questions. The helper is faked (tests/fakes/fake_youtube_helper.py)."""

import asyncio
import itertools
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant.part_status import PartStatus
from local_assistant.video import (
    CAP_CHARS,
    CAP_SECONDS,
    KEEP_SECONDS,
    LINE_CHARS,
    PART_CHARS,
    PART_TOKENS,
    CaptionHelper,
    Captions,
    KeptVideos,
    VideoError,
    VideoReader,
    caption_lines,
    first_link,
    length_words,
    reading_note,
    split,
    stamp,
    video_id,
    without_copies,
)
from tests.conftest import ME, STRANGER
from tests.fakes.fake_gemma import FakeGemma, text

FAKE = Path(__file__).parent / "fakes" / "fake_youtube_helper.py"
VIDEO = "AbCdEfGhI_-"
LINK = f"https://youtu.be/{VIDEO}"


# the strict link check: only the 11-character ID of a video link on YouTube's own hosts


@pytest.mark.parametrize(
    "link",
    [
        f"https://www.youtube.com/watch?v={VIDEO}",
        f"https://youtube.com/watch?v={VIDEO}&t=42s",
        f"http://www.youtube.com/watch?feature=share&v={VIDEO}",
        f"https://m.youtube.com/watch?v={VIDEO}",
        f"https://music.youtube.com/watch?v={VIDEO}&list=RDAMVM",
        f"https://youtu.be/{VIDEO}",
        f"https://youtu.be/{VIDEO}?si=abc123&t=10",
        f"https://www.youtube.com/shorts/{VIDEO}",
        f"https://m.youtube.com/shorts/{VIDEO}/",
        f"https://www.youtube.com/live/{VIDEO}?si=x",
        f"https://www.youtube.com/embed/{VIDEO}",
        f"www.youtube.com/watch?v={VIDEO}",
        f"youtu.be/{VIDEO}",
        f"HTTPS://WWW.YOUTUBE.COM/watch?v={VIDEO}",
        f"https://www.youtube.com/watch?v={VIDEO}#t=30",
        f" https://youtu.be/{VIDEO}. ",
        f"https://youtu.be/{VIDEO})",
    ],
)
def test_a_video_link_gives_its_id(link: str) -> None:
    assert video_id(link) == VIDEO


@pytest.mark.parametrize(
    "link",
    [
        "https://www.youtube.com/watch?v=short",
        f"https://www.youtube.com/watch?v={VIDEO}x",
        f"https://www.youtube.com/watch?v={VIDEO}&v=ZyXwVuTsR_-",
        "https://www.youtube.com/watch",
        f"https://www.youtube.com/watch?vv={VIDEO}",
        f"https://www.youtube.com/watch/?v={VIDEO}",
        f"https://www.youtube.com/watch?v={VIDEO};rm",
        f"https://www.youtube.com/watch?v={VIDEO}%26x=1",
        "https://www.youtube.com/watch?v=AbCdEfGhI%5F-",
        "https://www.youtube.com/channel/UCabcdefghijklmnop",
        "https://www.youtube.com/@someone",
        "https://www.youtube.com/playlist?list=PL0123456789",
        f"https://youtube.com.evil.example/watch?v={VIDEO}",
        f"https://evil.example/watch?v={VIDEO}",
        f"https://www.youtube.com@evil.example/watch?v={VIDEO}",
        f"https://user@www.youtube.com/watch?v={VIDEO}",  # privacy-ok: a made-up login in an address
        f"https://www.youtube.com:8080/watch?v={VIDEO}",
        f"ftp://www.youtube.com/watch?v={VIDEO}",
        f"javascript:alert(1)//youtu.be/{VIDEO}",
        f"https://youtu.be/{VIDEO}/extra",
        f"https://music.youtube.com/shorts/{VIDEO}",
        f"https://www.youtube-nocookie.com/embed/{VIDEO}",
        f"https://www.youtube.com/shorts/{VIDEO}/more",
        f"https://www.youtube.com/watch?v={VIDEO}\nhttps://evil.example",
        f"https://www.youtube.com/watch?v={VIDEO} https://evil.example",
        "",
        "not a link",
    ],
)
def test_anything_else_is_not_a_video_link(link: str) -> None:
    assert video_id(link) is None


def test_the_first_youtube_link_in_a_message_is_the_users_own() -> None:
    """As link_in finds it: the form in the message, without the punctuation around it; only a checked video link."""
    other = "https://www.youtube.com/watch?v=ZyXwVuTsR_-&t=42s"
    assert first_link(f"Please send me the full transcript of this video: ({LINK}).") == LINK
    assert first_link(f"this one {other}, then {LINK}") == other
    assert first_link(f"https://example.com/watch?v={VIDEO} youtube.com {VIDEO} https://vimeo.com/76979871") is None
    assert first_link("https://www.youtube.com/playlist?list=PL0123456789") is None
    assert first_link("") is None


# the helper: fixed arguments, a clean environment, a time limit; only its codes are believed


class Helper:
    """The fake helper's answer in a file, and the runner that starts it."""

    def __init__(self, tmp_path: Path, answer: dict[str, Any] | None = None, timeout: float = 5.0) -> None:
        self.state = tmp_path / "youtube.json"
        self.status = PartStatus(None)
        self.answer(answer or captions_answer())
        self.runner = CaptionHelper((sys.executable, str(FAKE), str(self.state)), timeout, status=self.status)

    def answer(self, data: dict[str, Any]) -> None:
        self.state.write_text(json.dumps(data))

    def calls(self) -> list[list[str]]:
        path = Path(f"{self.state}.calls")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def captions_answer(**fields: Any) -> dict[str, Any]:
    segments = [[0.0, "Welcome to the lecture on bridges."], [65.5, "Steel beams carry the load."]]
    return {"ok": True, "language": "en", "generated": False, "length": 70.0, "cut": False, "segments": segments} | (
        fields
    )


async def test_only_the_checked_id_reaches_the_helper_with_fixed_arguments(tmp_path: Path) -> None:
    helper = Helper(tmp_path)
    captions = await helper.runner.captions(VIDEO)
    assert helper.calls() == [["captions", "--until", str(CAP_SECONDS), "--", VIDEO]]  # the ID last, after "--"
    assert captions == Captions(
        segments=((0.0, "Welcome to the lecture on bridges."), (65.5, "Steel beams carry the load.")),
        language="en",
        generated=False,
        length=70.0,
        cut=False,
    )
    assert helper.status.snapshot()["youtube"].ok is not None


async def test_an_id_that_starts_with_a_dash_is_read(tmp_path: Path) -> None:
    # about 1 in 64 YouTube IDs starts with "-": before "--" the helper's parser took it for an option ("invalid")
    helper = Helper(tmp_path)
    captions = await helper.runner.captions("-0Ao4t_fe0I")
    assert helper.calls()[0][-2:] == ["--", "-0Ao4t_fe0I"]
    assert captions.segments[0] == (0.0, "Welcome to the lecture on bridges.")
    assert helper.status.snapshot()["youtube"].failed is None


async def test_an_id_that_is_not_checked_never_starts_the_helper(tmp_path: Path) -> None:
    helper = Helper(tmp_path)
    with pytest.raises(VideoError) as refused:
        await helper.runner.captions(f"{VIDEO}; rm -rf ~")
    assert refused.value.code == "failed" and helper.calls() == []


async def test_the_helper_gets_a_clean_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:3128")
    helper = Helper(tmp_path)
    await helper.runner.captions(VIDEO)
    names = set(json.loads(Path(f"{helper.state}.env").read_text()))
    assert "OPENROUTER_API_KEY" not in names and "HTTPS_PROXY" not in names
    assert names <= {"HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "PATH", "__CF_USER_TEXT_ENCODING", "LC_CTYPE"}


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("no_captions", None),  # YouTube answered: the part works
        ("unavailable", None),
        ("age_restricted", None),
        ("blocked", "refused"),
        ("unreachable", "no_answer"),
        ("timeout", "no_answer"),
        ("refused", "bad_answer"),
        ("failed", "bad_answer"),
        ("invalid", "helper_failed"),
        ("something new", "bad_answer"),
    ],
)
async def test_each_helper_code_is_an_error_with_codes_own_words(tmp_path: Path, code: str, reason: str | None) -> None:
    helper = Helper(tmp_path, {"ok": False, "error": code, "detail": "YouTube's own text, never passed on"})
    with pytest.raises(VideoError) as failed:
        await helper.runner.captions(VIDEO)
    known = code if code in prompts.VIDEO_ERRORS else "failed"
    assert failed.value.code == known and str(failed.value) == prompts.VIDEO_ERRORS[known]
    record = helper.status.snapshot()["youtube"]
    if reason is None:
        assert record.ok is not None and record.failed is None
    else:
        assert record.reason == reason


async def test_no_json_is_a_failed_helper(tmp_path: Path) -> None:
    helper = Helper(tmp_path, {"raw": "Traceback (most recent call last): …"})
    with pytest.raises(VideoError) as failed:
        await helper.runner.captions(VIDEO)
    assert failed.value.code == "failed" and helper.status.snapshot()["youtube"].reason == "helper_failed"


async def test_a_missing_helper_says_how_to_set_it_up(tmp_path: Path) -> None:
    status = PartStatus(None)
    runner = CaptionHelper((str(tmp_path / "no-such-python"), "-I", "helper.py"), 5.0, status=status)
    with pytest.raises(VideoError) as failed:
        await runner.captions(VIDEO)
    # no command in her words: the step differs in her own user (a deploy builds it); doctor names the right one
    assert (
        failed.value.code == "helper_missing" and "doctor" in str(failed.value) and "cd tools" not in str(failed.value)
    )
    assert status.snapshot()["youtube"].reason == "helper_missing"


async def test_a_helper_that_hangs_is_killed(tmp_path: Path) -> None:
    helper = Helper(tmp_path, captions_answer() | {"sleep": 30}, timeout=0.5)
    started = time.monotonic()
    with pytest.raises(VideoError) as failed:
        await helper.runner.captions(VIDEO)
    assert failed.value.code == "timeout" and time.monotonic() - started < 5
    pid = int(Path(f"{helper.state}.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert helper.status.snapshot()["youtube"].reason == "no_answer"


async def test_odd_segments_are_left_out_and_the_cap_holds(tmp_path: Path) -> None:
    segments: list[Any] = [
        [0.0, "Fine."],
        "not a segment",
        [-1, "before the start"],
        [1.0, 42],
        [2.0, "x" * 5_000],
        [CAP_SECONDS + 1.0, "after two hours"],
    ]
    helper = Helper(tmp_path, captions_answer(segments=segments, length=9_000.0))
    captions = await helper.runner.captions(VIDEO)
    assert [start for start, _ in captions.segments] == [0.0, 2.0]
    assert len(captions.segments[1][1]) == 1_000  # one segment's text, at most
    assert captions.cut is True and captions.length == 9_000.0


async def test_an_answer_without_words_has_no_captions(tmp_path: Path) -> None:
    helper = Helper(tmp_path, captions_answer(segments=[]))
    with pytest.raises(VideoError) as failed:
        await helper.runner.captions(VIDEO)
    assert failed.value.code == "no_captions"


async def test_logs_hold_codes_counts_and_times_never_the_id(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.video")
    helper = Helper(tmp_path)
    await helper.runner.captions(VIDEO)
    helper.answer({"ok": False, "error": "no_captions"})
    with pytest.raises(VideoError):
        await helper.runner.captions(VIDEO)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "61 chars" in logged and "no_captions" in logged and " ms" in logged
    assert VIDEO not in logged and "bridges" not in logged


# the parts: one time stamp about every 30 s, parts of about 15,000 characters


def speech(seconds: float, every: float = 5.0, words: str = "the beam carries the load") -> Captions:
    count = int(seconds / every)
    segments = tuple((n * every, f"{words} {n}") for n in range(count))
    return Captions(segments=segments, language="en", generated=False, length=seconds, cut=False)


@pytest.mark.parametrize(
    ("seconds", "shown"), [(0, "0:00"), (65.5, "1:05"), (599.9, "9:59"), (3_725, "1:02:05"), (7_200, "2:00:00")]
)
def test_stamps(seconds: float, shown: str) -> None:
    assert stamp(seconds) == shown


@pytest.mark.parametrize(
    ("seconds", "words"),
    [(30, "1 min"), (45 * 60, "45 min"), (100 * 60, "1 h 40 min"), (7_200, "2 h"), (3 * 3_600 + 600, "3 h 10 min")],
)
def test_lengths_in_words(seconds: float, words: str) -> None:
    assert length_words(seconds) == words


def test_lines_carry_a_stamp_about_every_30_seconds() -> None:
    lines = caption_lines(speech(120))
    assert [line.split("]")[0] for line in lines] == ["[0:00", "[0:30", "[1:00", "[1:30"]
    assert lines[0] == "[0:00] " + " ".join(f"the beam carries the load {n}" for n in range(6))


def test_a_short_video_is_one_part() -> None:
    parts = split(speech(600))
    assert len(parts) == 1 and parts[0].start == 0.0 and parts[0].text == "\n".join(caption_lines(speech(600)))


def test_a_long_video_is_split_into_even_parts_of_about_15000_characters() -> None:
    captions = speech(6_000)  # 1 h 40 min
    lines = caption_lines(captions)
    total = len("\n".join(lines))
    parts = split(captions)
    assert len(parts) == -(-total // PART_CHARS)
    assert "\n".join(part.text for part in parts) == "\n".join(lines)  # every line once, in order
    sizes = [len(part.text) for part in parts]
    assert max(sizes) <= PART_CHARS and max(sizes) - min(sizes) < 2_000
    assert parts[0].start == 0.0 and all(a.end <= b.start for a, b in itertools.pairwise(parts))


def test_dense_captions_make_short_lines_and_no_part_over_the_bound() -> None:
    # hand-written captions are the uploader's: 120 segments of 999 characters within the first 12 s
    dense = Captions(tuple((n * 0.1, "x" * 999) for n in range(120)), "en", False, 20.0, False)
    assert all(len(line) <= LINE_CHARS + len("[0:00] ") for line in caption_lines(dense))
    parts = split(dense)
    assert len(parts) > 1 and max(len(part.text) for part in parts) <= PART_CHARS
    assert "".join(part.text for part in parts).count("x") == 120 * 999  # nothing lost


def test_uneven_lines_never_make_a_part_over_the_bound() -> None:
    # lines just under the line cap fill each part short of its even share: the rest used to pile up in the last part
    captions = Captions(tuple((n * 31.0, "y" * 1_990) for n in range(60)), "en", False, 1_900.0, False)
    parts = split(captions)
    assert max(len(part.text) for part in parts) <= PART_CHARS
    assert "\n".join(part.text for part in parts) == "\n".join(caption_lines(captions))


def test_a_line_longer_than_a_part_is_cut_with_its_time() -> None:
    captions = Captions(((65.0, "z" * 900),), "en", False, 70.0, False)
    parts = split(captions, 400)
    assert len(parts) == 3 and all(part.text.startswith("[1:05] ") and len(part.text) <= 400 for part in parts)


async def test_the_bridge_keeps_at_most_its_own_character_cap(tmp_path: Path) -> None:
    segments = [[float(n), "w" * 999] for n in range(400)]  # 400,000 characters in under 7 minutes
    helper = Helper(tmp_path, captions_answer(segments=segments, length=400.0))
    captions = await helper.runner.captions(VIDEO)
    assert CAP_CHARS - 1_000 < captions.chars <= CAP_CHARS
    assert captions.cut and captions.size_cut


async def test_the_helpers_size_cut_is_believed(tmp_path: Path) -> None:
    helper = Helper(tmp_path, captions_answer(cut=True, cut_by="size", length=5_400.0))
    captions = await helper.runner.captions(VIDEO)
    assert captions.cut and captions.size_cut
    helper.answer(captions_answer(cut=True, cut_by="time", length=9_000.0))
    timed = await helper.runner.captions(VIDEO)
    assert timed.cut and not timed.size_cut


def test_the_helper_keeps_the_bridges_character_cap() -> None:
    source = (Path(__file__).parents[1] / "tools" / "youtube" / "helper.py").read_text(encoding="utf-8")
    found = re.search(r"^MAX_CHARS = ([\d_]+)", source, re.MULTILINE)
    assert found is not None and int(found.group(1).replace("_", "")) == CAP_CHARS


def test_a_size_cut_says_how_far_it_read_never_two_hours() -> None:
    size = Captions(((0.0, "a"), (4_200.0, "b")), "en", False, 5_400.0, True, size_cut=True)
    assert reading_note(size, 8) == "Reading the first 1 h 10 min of a 1 h 30 min video: about 1 minute."


async def test_a_size_cut_video_says_so_in_the_result() -> None:
    size = Captions(((0.0, "a"), (4_200.0, "b")), "en", False, 5_400.0, True, size_cut=True)
    result = await notes_of(size, FakeGemma(), [])
    assert prompts.VIDEO_CUT_SIZE.format(read="1 h 10 min", length="1 h 30 min") in result
    assert "2 hours" not in result


def test_the_reading_note_says_the_length_and_the_wait() -> None:
    assert reading_note(speech(6_000), 4) == "Reading a 1 h 40 min video: about 1 minute."
    assert reading_note(speech(1_500), 1) == "Reading a 25 min video: about 1 minute."
    long = Captions(segments=((0.0, "a"),), language="en", generated=False, length=11_400, cut=True)
    assert reading_note(long, 8) == "Reading the first 2 hours of a 3 h 10 min video: about 1 minute."
    assert reading_note(long, 14) == "Reading the first 2 hours of a 3 h 10 min video: about 2 minutes."


# her answer never writes the captions out: code leaves out the sentences that copy them


LECTURE = Captions(
    segments=(
        (0.0, "Welcome back. Today we look at how a suspension bridge carries its load."),
        (20.0, "We will go through the towers, the main cables, the deck and the anchorages."),
        (65.0, "The towers are tall columns that hold the main cables up high."),
        (210.0, "A main cable is not one thick rope: it is made of thousands of thin steel wires bundled together."),
        (300.0, "The cables hang in a curve, and they work in tension."),
        (625.0, "The steel is painted against rust, and the paint crew uses a colour called harbour grey."),
    ),
    language="en",
    generated=False,
    length=640.0,
    cut=False,
)


def test_a_summary_with_times_and_short_quotes_stays_as_it_is() -> None:
    summary = (
        'A lecture on suspension bridges. The towers (1:05) hold the cables up; a cable is "thousands of thin '
        'steel wires" (3:30).\n- The paint is harbour grey (10:25).'
    )
    assert without_copies(summary, LECTURE) == (summary, False)


def test_captions_written_out_are_left_out() -> None:
    answer = (
        "Here is the transcript.\n[0:00] Welcome back. Today we look at how a suspension bridge carries its load.\n"
        "[0:20] We will go through the towers, the main cables, the deck and the anchorages."
    )
    assert without_copies(answer, LECTURE) == ("Here is the transcript.", True)


def test_twelve_caption_words_in_a_row_stay_thirteen_go() -> None:
    twelve = 'He says "a main cable is not one thick rope: it is made of" wire. Then towers.'
    assert without_copies(twelve, LECTURE) == (twelve, False)
    thirteen = 'He says "A MAIN cable, is not one thick rope; it is made of thousands" here. Then towers.'
    assert without_copies(thirteen, LECTURE) == ("Then towers.", True)


def test_a_copy_cut_into_short_runs_goes_when_it_holds_much_of_the_captions() -> None:
    short = Captions(((0.0, "first you need an active starter you feed it the night before"),), "en", True, 9.0, False)
    pieces = "You need an active starter, you feed it. The night before!"  # runs of 6 or more
    assert without_copies(pieces, short) == ("", True)
    once = "Feed the starter the night before. Then you need an active starter."  # 3 and 4 words: fine
    assert without_copies(once, short) == (once, False)


def test_words_without_spaces_are_compared_by_character() -> None:
    japanese = Captions(((0.0, "今日は吊り橋がどのように荷重を支えるかを見ていきます。"),), "ja", False, 5.0, False)
    assert without_copies("吊り橋の話です。", japanese) == ("吊り橋の話です。", False)
    assert without_copies("要約です。今日は吊り橋がどのように荷重を支えるかを見ていきます。", japanese) == (
        "要約です。",
        True,
    )


# the last video, in memory only, per account, for 10 minutes


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_the_last_video_stays_for_ten_minutes_per_account_and_conversation() -> None:
    clock = Clock()
    kept = KeptVideos(clock=clock)
    video = kept.keep(ME, 7, VIDEO, LINK, speech(60))
    assert kept.get(ME.upper(), 7, VIDEO) == video
    assert kept.get(STRANGER, 7, VIDEO) is None  # another account
    assert kept.get(ME, 8, VIDEO) is None  # /new: another conversation, and it is gone
    assert kept.get(ME, 7, VIDEO) is None
    kept.keep(ME, 7, VIDEO, LINK, speech(60))
    assert kept.get(ME, 7, "ZyXwVuTsR_-") is None  # another video
    clock.now += KEEP_SECONDS + 1
    assert kept.get(ME, 7, VIDEO) is None
    assert not kept.held()


def test_a_new_video_replaces_the_last_one() -> None:
    kept = KeptVideos(clock=Clock())
    kept.keep(ME, 7, VIDEO, LINK, speech(60))
    kept.keep(ME, 7, "ZyXwVuTsR_-", "https://youtu.be/ZyXwVuTsR_-", speech(60))
    assert kept.get(ME, 7, VIDEO) is None and kept.get(ME, 7, "ZyXwVuTsR_-") is not None


def test_expiry_and_new_drop_the_captions_from_memory() -> None:
    clock = Clock()
    kept = KeptVideos(clock=clock)
    kept.keep(ME, 7, VIDEO, LINK, speech(60))
    kept.keep(STRANGER, 7, VIDEO, LINK, speech(60))
    clock.now += KEEP_SECONDS + 1
    kept.expire()
    assert not kept.held()
    kept.keep(ME, 7, VIDEO, LINK, speech(60))
    kept.clear()
    assert not kept.held()


async def test_the_captions_go_by_themselves_after_ten_minutes() -> None:
    clock = Clock()
    kept = KeptVideos(clock=clock, seconds=0.05)
    kept.keep(ME, 7, VIDEO, LINK, speech(60))
    clock.now += 1
    await asyncio.sleep(0.2)
    assert not kept.held()  # nobody asked again: the timer dropped them


# the answer's material: the captions themselves, or notes made part by part


class NoHelper:
    async def captions(self, video: str) -> Captions:
        raise AssertionError("the helper was run")


async def notes_of(captions: Captions, gemma: FakeGemma, notes: list[str], question: str = "summarise this") -> str:
    reader = VideoReader(NoHelper(), gemma)

    async def notify(note: str) -> None:
        notes.append(note)
        gemma.calls.append({"note": note})

    kept = KeptVideos(clock=Clock()).keep(ME, 7, VIDEO, LINK, captions)
    return await reader.material(kept, question, notify)


async def test_a_short_video_gives_its_captions_with_times_and_the_rules() -> None:
    gemma, notes = FakeGemma(), list[str]()
    result = await notes_of(speech(300), gemma, notes)
    assert result.startswith(prompts.VIDEO_MARK) and not gemma.calls and not notes
    assert "[0:00] the beam carries the load 0" in result and "[4:30]" in result
    assert LINK in result and prompts.VIDEO_GENERATED not in result and prompts.VIDEO_CUT not in result


async def test_auto_generated_captions_are_named() -> None:
    captions = speech(60)
    generated = Captions(captions.segments, "nl", True, captions.length, False)
    result = await notes_of(generated, FakeGemma(), [])
    assert prompts.VIDEO_GENERATED in result and "nl" in result


async def test_a_long_video_is_read_part_by_part_after_the_note() -> None:
    captions = speech(6_000)
    parts = split(captions)
    gemma = FakeGemma(*(text(f"- [{stamp(part.start)}] note {n}") for n, part in enumerate(parts, 1)))
    notes: list[str] = []
    result = await notes_of(captions, gemma, notes, question="what does he say about steel?")
    assert notes == [reading_note(captions, len(parts))]
    assert gemma.calls[0] == {"note": notes[0]}  # the note goes out before the first part is read
    asked = gemma.calls[1:]
    assert len(asked) == len(parts)
    for call, part in zip(asked, parts, strict=True):
        assert call["tools"] is None and call["max_tokens"] == PART_TOKENS
        system, user = call["messages"]
        assert system == {"role": "system", "content": prompts.VIDEO_PART_RULES}
        assert "what does he say about steel?" in user["content"] and part.text in user["content"]
        assert all(other.text not in user["content"] for other in parts if other is not part)
    assert result.startswith(prompts.VIDEO_MARK)
    assert [result.index(f"note {n}") for n in range(1, len(parts) + 1)] == sorted(
        result.index(f"note {n}") for n in range(1, len(parts) + 1)
    )
    assert "the beam carries the load 3" not in result  # the notes, never the captions
    assert f"(0:00–{stamp(parts[0].end)})" in result


async def test_a_part_with_no_notes_says_so() -> None:
    captions = speech(6_000)
    gemma = FakeGemma(*(text("") for _ in split(captions)))
    result = await notes_of(captions, gemma, [])
    assert prompts.VIDEO_NO_NOTES in result


async def test_a_cut_video_says_only_the_first_two_hours_were_read() -> None:
    captions = speech(60)
    cut = Captions(captions.segments, "en", False, 11_400, True)
    result = await notes_of(cut, FakeGemma(), [])
    assert prompts.VIDEO_CUT.format(length=length_words(11_400)) in result


async def test_the_reader_fetches_through_the_helper_and_keeps_the_video(tmp_path: Path) -> None:
    helper = Helper(tmp_path)
    reader = VideoReader(helper.runner, FakeGemma(), kept=KeptVideos(clock=Clock()))
    video = await reader.fetch(ME, 7, VIDEO, LINK)
    assert video.link == LINK and reader.kept(ME, 7, VIDEO) == video
    assert len(helper.calls()) == 1


def test_looking_at_the_last_video_never_drops_it() -> None:
    clock = Clock()
    kept = KeptVideos(clock=clock)
    video = kept.keep(ME, 7, VIDEO, LINK, speech(60))
    assert kept.last(ME, 8) is None and kept.last(ME, 7) == video  # another conversation (a practice one) only looks
    clock.now += KEEP_SECONDS + 1
    assert kept.last(ME, 7) is None
