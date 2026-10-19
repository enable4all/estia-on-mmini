"""The [chords] extra in the bridge: its config, the tool, code's note, the reply check and calls. The chord
arithmetic itself is theory.py's (tests/test_theory.py)."""

import asyncio
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from local_assistant import chords as chords_module
from local_assistant import prompts
from local_assistant.app import ALIVE_KEY, App
from local_assistant.brain import INSTANT_TOOLS, Brain
from local_assistant.chords import CHORDS_SCHEMA, ChordTool
from local_assistant.config import Config, ConfigError, parse_config
from local_assistant.context import ContextBuilder
from local_assistant.fetcher import Fetcher, Page
from local_assistant.profile import Profile
from local_assistant.signal_io import SignalClient
from local_assistant.store import Store
from local_assistant.tools import Tools, Turn
from local_assistant.web_search import WebResult, WebResults
from tests.agent_docs import agent_docs
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text, tool
from tests.fakes.fake_web import FakeWeb
from tests.test_app import T0 as NOW  # recent: older messages count as missed during downtime
from tests.test_app import call_settings, deliver, sent, start, stop, until

BASE = {"bot_number": "+306900000000", "allowed_acis": [ME]}


def test_chords_is_an_extra_off_without_its_section() -> None:
    assert not parse_config(dict(BASE)).chords_enabled
    assert parse_config({**BASE, "chords": {}}).chords_enabled
    with pytest.raises(ConfigError, match=r"\[chords\] must be a table"):
        parse_config({**BASE, "chords": "on"})


# The request's parts with [chords] off, at 2acb640 (before the extra): its rules, a call's rules, the tools, /help.
# A text in every request once cost call_goodbye 30 points (docs/agents/prompts-evals.md, Gotchas), so without the
# extra nothing changes.
BEFORE_CHORDS = {
    "plain": {
        "rules": "882740a167731b5133cb87dc24866a5e6151254b8092938e3eae0b762e7f8824",
        "call_rules": "2d8183f9141a8922dfaf18a9b57fe1a2a058e9ae58bb168e5a7ef9f73f438bb9",
        "tools": "9fa8044d05b59d5d2c1b75b2d63ae36dc244e03ec7f996e8dbc3cb473d50ea39",
        "help": "2ff862f1c392f91a9a336338b2da1117a17086ef989bc46846de01ca0af1fd7d",
    },
    "web_calls": {
        "rules": "e4fdec3656a51413c0f19900e5d6138def0faf6795257500f7564bbd7864a814",
        "call_rules": "fd5ea40a6d7baa4f676bf5f7a75405add6ee142c41121c8b98e3be6eff76e3d3",
        "tools": "cfeb41ac8a696bde3310c5c7cd93b1790e3a929b4402df0d9533898db05f393a",
        "help": "47fa63d552a9a0fd1fe8c205a03cd2db1d1ae0f2f21e51c453bb1162bc455ef4",
    },
}
SETUPS: dict[str, dict[str, Any]] = {
    "plain": {},
    "web_calls": {"web_enabled": True, "calls_enabled": True, "voice_url": "http://127.0.0.1:8150"},
}


def bridge(tmp_path: Path, **settings: Any) -> App:
    """The app built (never run) on a config like a fresh install's, with `settings`."""
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        youtube_helper=(str(tmp_path / "none"),),
        **settings,
    )
    store = Store.open(cfg.db_path)
    signal = SignalClient([sys.executable, "-c", "pass"], cfg.allowed_acis)
    return App(cfg, store, signal, FakeGemma(), Fetcher(), security_level=lambda: "hardened")


def request_parts(app: App) -> dict[str, str]:
    rules = app._rules("en")
    parts = {
        "rules": rules,
        "call_rules": prompts.without_capabilities(rules),
        "tools": json.dumps(app.tools.schemas),
        "help": "\n".join(app.capability_lines()),
    }
    return {name: hashlib.sha256(value.encode()).hexdigest() for name, value in parts.items()}


@pytest.mark.parametrize("setup", SETUPS)
def test_without_chords_the_request_is_byte_for_byte_the_one_from_before(tmp_path: Path, setup: str) -> None:
    app = bridge(tmp_path, **SETUPS[setup])
    try:
        assert request_parts(app) == BEFORE_CHORDS[setup]
    finally:
        app.store.close()


# ---- the tool (design 3.5, 3.6) ----

SPOOKY = WebResult(
    "Spooky chords by Dusty Springfield | GuitarTuna",
    "https://guitartuna.com/chords/spooky-dusty-springfield",
    "guitartuna.com",
    "",
    "Spooky chords by Dusty Springfield (Official Version). Key: Am. Chords: Am7 · D6 · Ebdim7 · Em7. "
    "Am7 In the cool of the evening D6 Am7 D6 when everything is getting kind of groovy.",
)
OTHER_PAGE = WebResult(
    "Spooky lyrics", "https://lyrics.example/spooky", "lyrics.example", "", "In the cool of the evening."
)


async def no_fetch(url: str) -> Page:
    raise AssertionError(url)


def chord_tools(store: Store, *, chords: bool = True, web: FakeWeb | None = None, calls: bool = False) -> Tools:
    return Tools(
        store,
        Profile(store),
        TZ,
        no_fetch,
        web=web,
        calls=calls,
        chords=ChordTool() if chords else None,
    )


def said(store: Store, words: str, ts: int = T0 + 100_000) -> Turn:
    conversation = store.current_conversation()
    message_id = store.add_incoming(conversation.id, ME, ts, "text", words)
    assert message_id is not None
    return Turn(store.message(message_id))


def args(**values: Any) -> str:
    return json.dumps(values)


def test_the_tool_is_offered_only_with_chords_and_its_description_is_short(store: Store) -> None:
    on = [schema["function"]["name"] for schema in chord_tools(store).schemas]
    off = [schema["function"]["name"] for schema in chord_tools(store, chords=False).schemas]
    assert "chords" in on and "chords" not in off
    function = CHORDS_SCHEMA["function"]
    assert function["name"] == "chords"  # never music_…: that prefix is Spotify's, which refuses calls
    assert function["description"] == (
        "Chord arithmetic on given chords: transpose; capo, the fret for a key and shapes (to_key B-Dur, shapes G); "
        "a chord's notes; the key of given chords. Names exactly as the user or the site wrote them, never "
        "translated (H-Dur, H7, LAm, SIb). The bridge gives the chords itself."
    )
    properties = function["parameters"]["properties"]
    assert list(properties) == ["action", "chords", "from_key", "semitones", "to_key", "fret", "shapes"]
    assert properties["action"]["enum"] == ["transpose", "capo", "notes", "key"]
    assert "Verse: Am F C G" in properties["chords"]["description"]
    # eval of 18 Oct: the key to sound in had no place of its own ("the song's key" or "the chords"), so a capo
    # call got chords "G" with shapes "G"; it is a capo's target, as to_key is a move's
    assert "capo" not in properties["chords"]["description"] + properties["from_key"]["description"]
    assert "capo: the key to sound in" in properties["to_key"]["description"]
    for words in ("YouTube", "Spotify", "guitartuna", "Ultimate"):  # no site or brand name (prompts-evals.md, Gotchas)
        assert words not in json.dumps(CHORDS_SCHEMA)
    assert "chords" in INSTANT_TOOLS  # no "let me check" on a call


def test_the_tool_is_offered_on_calls_too(store: Store) -> None:
    tools = chord_tools(store, calls=True)
    assert "chords" in [schema["function"]["name"] for schema in tools.schemas]


async def test_a_transpose_gives_the_fixed_lines_and_keeps_the_result(store: Store) -> None:
    tools = chord_tools(store)
    turn = said(store, "Transpose Am F C G up 2")
    result = await tools.call(turn, "chords", args(action="transpose", chords="Am F C G", semitones=2))
    lines = (
        "Up 2 (a whole tone): from Am (A minor, likely; C major also possible) to Bm (B minor)\n"
        "Bm G D A\n"
        "Read as: Am F C G"
    )
    assert result.startswith(lines + "\n")
    assert "never write the chords out" in result  # what she does with it
    [kept] = turn.chords
    assert (kept.action, kept.lines, kept.given, kept.chords) == (
        "transpose",
        lines,
        ("Am", "F", "C", "G"),
        ("Bm", "G", "D", "A"),
    )
    assert kept.ok and kept.moved


@pytest.mark.parametrize("semitones", [4, "4", "+4", 4.0])
async def test_semitones_as_a_number_or_its_digits(store: Store, semitones: object) -> None:
    turn = said(store, "Verse: Am F C G E7, 4 semitones higher")
    await chord_tools(store).call(
        turn, "chords", args(action="transpose", chords="Verse: Am F C G E7", semitones=semitones)
    )
    assert turn.chords[0].lines.split("\n")[1] == "Verse: C#m A E B G#7"


@pytest.mark.parametrize("semitones", ["99999999999999999999", "four", 4.5, True, 0, 12])
async def test_a_step_that_is_no_small_whole_number_is_refused(store: Store, semitones: object) -> None:
    turn = said(store, "transpose Am F up")
    result = await chord_tools(store).call(turn, "chords", args(action="transpose", chords="Am F", semitones=semitones))
    assert result == "Error: semitones from -11 to 11, not 0"
    assert not turn.chords[0].ok and turn.chords[0].error == "semitones"


async def test_notes_key_and_capo_give_their_lines(store: Store) -> None:
    tools = chord_tools(store)
    turn = said(store, "What notes are in G seven, and the key of Am F C G?")
    await tools.call(turn, "chords", args(action="notes", chords="G7"))
    await tools.call(turn, "chords", args(action="key", chords="Am F C G"))
    assert turn.chords[0].lines == "G7: G B D F\nRead as: G7"
    assert turn.chords[1].lines == (
        "Likely A minor: i VI III VII. C major is also possible: the same chords.\nRead as: Am F C G"
    )
    capo = said(store, "Welcher Bund für B-Dur mit G-Griffen?", T0 + 200_000)
    await tools.call(capo, "chords", args(action="capo", from_key="B-Dur", shapes="G"))
    assert capo.chords[0].lines == "Capo 3 with G shapes: sounds in B♭ major.\nRead as: B-Dur (B♭ major); G (G major)"
    sheet = said(store, "what does this capo sheet sound like?", T0 + 300_000)
    await tools.call(sheet, "chords", args(action="capo", chords="Capo 3\nG Em C D"))
    assert sheet.chords[0].lines.startswith("Capo 3 with G Em C D: sounds Bb Gm Eb F (B♭ major).")


async def test_a_capo_sheet_naming_its_shapes_in_brackets_is_read(store: Store) -> None:
    """Deferred in the fix pass: "Capo 3 (G shapes)" was refused, both for capo and for transpose."""
    tools = chord_tools(store)
    sheet = said(store, "what does this capo sheet sound like?")
    await tools.call(sheet, "chords", args(action="capo", chords="Capo 3 (G shapes)\nG Em C D"))
    assert sheet.chords[0].lines == (
        "Capo 3 with G Em C D: sounds Bb Gm Eb F (B♭ major).\nRead as: G Em C D; the capo line names G shapes"
    )
    moved = said(store, "and up a whole tone?", T0 + 200_000)
    await tools.call(moved, "chords", args(action="transpose", chords="Capo 3 (G shapes)\nG Em C D", semitones=2))
    assert moved.chords[0].lines.split("\n")[1:3] == ["Capo 3 (A shapes)", "A F#m D E"]


@pytest.mark.parametrize(("written", "words"), [("fis klein", "F♯ minor"), ("ля минор", "A minor")])
async def test_capo_takes_a_key_in_words_as_written(store: Store, written: str, words: str) -> None:
    """Review C4: Gemma passes the key as written (the description says so); the tool never reads it as chords."""
    turn = said(store, f"Which capo for {written}?")
    await chord_tools(store).call(turn, "chords", args(action="capo", chords=written))
    [kept] = turn.chords
    assert kept.ok and kept.lines.startswith(f"For {words}: "), kept.lines


async def test_capo_with_do_re_mi_gives_its_lines(store: Store) -> None:
    """Review I3: the capo worked, but the names it gave back (letters) beside the user's (do re mi) were refused
    as mixed names, and the whole call failed."""
    tools = chord_tools(store)
    found: list[chords_module.Result] = []
    for number, (words, call) in enumerate(
        [
            ("Capo for SIb?", args(action="capo", chords="SIb")),
            ("SOL shapes at capo 3?", args(action="capo", shapes="SOL", fret=3)),
            ("Capo for La menor?", args(action="capo", chords="La menor")),
        ]
    ):
        turn = said(store, words, T0 + 100_000 * (number + 1))
        await tools.call(turn, "chords", call)
        found.extend(turn.chords)
    first, second, third = found
    assert first.ok and first.lines.startswith("For B♭ major: "), first.lines
    assert second.ok and second.lines.startswith("Capo 3 with SOL shapes: sounds in B♭ major."), second.lines
    assert third.ok and third.lines.startswith("For A minor: "), third.lines
    checked = chords_module.check("Capo 1 with A shapes, so it sounds in B♭ major.", results=[first], sources=[])
    assert checked.left_out == 0  # the shapes it named count for her words


@pytest.mark.parametrize(
    ("words", "key", "shapes", "line"),
    [
        (
            "I want it to sound in Bb, playing G shapes. Which fret?",
            "Bb",
            "G",
            "Capo 3 with G shapes: sounds in B♭ major.",
        ),
        ("Welcher Bund für B-Dur mit G-Griffen?", "B-Dur", "G", "Capo 3 with G shapes: sounds in B♭ major."),
        ("Welcher Bund für H-Dur mit G-Griffen?", "H-Dur", "G", "Capo 4 with G shapes: sounds in B major."),
        ("Quale capotasto per SIb con le forme di SOL?", "SIb", "SOL", "Capo 3 with SOL shapes: sounds in B♭ major."),
    ],
)
async def test_capo_takes_the_key_to_sound_in_as_to_key(
    store: Store, words: str, key: str, shapes: str, line: str
) -> None:
    """Eval of 18 Oct (chords_capo, chords_b_dur_capo): the key to sound in is a capo's target, as for a move."""
    turn = said(store, words)
    await chord_tools(store).call(turn, "chords", args(action="capo", to_key=key, shapes=shapes))
    [kept] = turn.chords
    assert kept.ok and kept.lines.split("\n")[0] == line, kept.lines


async def test_capo_still_takes_the_key_as_from_key_or_the_chords_played_beside_to_key(store: Store) -> None:
    tools = chord_tools(store)
    old = said(store, "Welcher Bund für B-Dur mit G-Griffen?")
    await tools.call(old, "chords", args(action="capo", from_key="B-Dur", shapes="G"))
    played = said(store, "I play G Em C D and want it in Bb major: which fret?", T0 + 200_000)
    await tools.call(played, "chords", args(action="capo", chords="G Em C D", to_key="Bb"))
    assert old.chords[0].lines.split("\n")[0] == "Capo 3 with G shapes: sounds in B♭ major."
    assert played.chords[0].lines.split("\n")[0] == "Capo 3 with G Em C D: sounds in B♭ major."


@pytest.mark.parametrize(
    ("words", "key", "shapes", "line"),
    [
        # review C1: the song's key in words is no key to sound in; her Bb (G up 3) stayed G, "no capo"
        (
            "This song is in G major but too low for me; I want it 3 semitones higher. I play G shapes. Capo?",
            "Bb",
            "G",
            "Capo 3 with G shapes: sounds in B♭ major.",
        ),
        (
            "The song is in A major, too low for me: 3 semitones higher, please. I play G shapes.",
            "C",
            "G",
            "Capo 5 with G shapes: sounds in C major.",
        ),
        ("Die Originalaufnahme ist in A-Dur. Welcher Bund für B-Dur mit G-Griffen?", "B-Dur", "G", "Capo 3 with G"),
    ],
)
async def test_a_capos_key_to_sound_in_stays_hers_beside_another_key_of_the_users(
    store: Store, words: str, key: str, shapes: str, line: str
) -> None:
    turn = said(store, words)
    await chord_tools(store).call(turn, "chords", args(action="capo", to_key=key, shapes=shapes))
    [kept] = turn.chords
    assert kept.ok and kept.lines.startswith(line), kept.lines
    assert "key from your message" not in kept.lines, kept.lines


@pytest.mark.parametrize(
    ("language", "words"),
    [
        ("fr", "Quel capo pour jouer en si bémol majeur avec des formes de Sol ?"),
        ("it", "Che capotasto per suonare in si bemolle maggiore con le forme di Sol?"),
        ("es", "¿Qué cejilla para sonar en si bemol mayor con formas de Sol?"),
    ],
)
async def test_a_romance_key_before_with_is_the_users_key(store: Store, language: str, words: str) -> None:
    """Review I5: a do-re-mi key followed by "avec", "con" or "com" was no key, so her "B" (translated, as she did
    with "Sol" and "SIb" live) stayed B and gave Capo 4."""
    tools = Tools(store, Profile(store), TZ, no_fetch, chords=ChordTool(language=lambda: language))
    turn = said(store, words)
    await tools.call(turn, "chords", args(action="capo", to_key="B", shapes="G"))
    [kept] = turn.chords
    assert kept.ok and kept.lines.split("\n")[0] == "Capo 3 with G shapes: sounds in B♭ major.", kept.lines


@pytest.mark.parametrize(
    ("words", "key", "shapes", "line", "written"),
    [
        ("Welcher Bund für H-Dur mit G-Griffen?", "B", "G", "Capo 4 with G shapes: sounds in B major.", "H-Dur"),
        (
            "Which capo for F sharp minor with Em shapes?",
            "F#",
            "Em",
            "Capo 2 with Em shapes: sounds in F♯ minor.",
            None,
        ),
        ("Welcher Bund für Fis-Dur mit E-Griffen?", "Gb", "E", "Capo 2 with E shapes: sounds in F♯ major.", "Fis-Dur"),
    ],
)
async def test_a_capos_key_that_misreads_the_users_key_words_takes_them(
    store: Store, words: str, key: str, shapes: str, line: str, written: str | None
) -> None:
    """L4 for a capo: her key is replaced only when it misreads one of the user's (the same letter or the same sound,
    another key: B for H-Dur, F# for F sharp minor, Gb for Fis-Dur), never by a key of another letter and sound."""
    turn = said(store, words)
    await chord_tools(store).call(turn, "chords", args(action="capo", to_key=key, shapes=shapes))
    [kept] = turn.chords
    assert kept.ok and kept.lines.split("\n")[0] == line, kept.lines
    assert "key from your message" in kept.lines and (written is None or f"({written})" in kept.lines), kept.lines


@pytest.mark.parametrize(
    ("words", "call", "written"),
    [
        # the eval's call (18 Oct): chords "G" with shapes "G" gave "Capo 0", five times over
        ("I want it to sound in Bb, playing G shapes. Which fret for the capo?", {"chords": "G", "shapes": "G"}, "Bb"),
        ("I want it to sound in Bb, playing G shapes. Which fret for the capo?", {"to_key": "G", "shapes": "G"}, "Bb"),
        ("Welcher Bund für B-Dur mit G-Griffen?", {"chords": "G", "shapes": "G"}, "B-Dur"),
    ],
)
async def test_a_capo_whose_key_is_the_shapes_key_beside_another_key_asks_never_picks(
    store: Store, call: dict[str, str], words: str, written: str
) -> None:
    """Review I3: the safety net picked the other key in the message, which may be a key the user never asked for
    ("The original recording is in A major"); it asks back instead, and she calls again with the key to sound in."""
    turn = said(store, words)
    result = await chord_tools(store).call(turn, "chords", args(action="capo", **call))
    assert result == (
        f"Error: the key to sound in and the shapes are both G, and the message names {written}: say which key "
        "should sound"
    )
    [kept] = turn.chords
    assert not kept.ok and kept.error == "capo_same", kept.lines


@pytest.mark.parametrize(
    ("language", "words", "shapes", "line"),
    [
        # review I3: the original's key in words was taken as the key to sound in: "Capo 2 ... A major"
        (
            "en",
            "The original recording is in A major, but I sing it in G major. I play G shapes.",
            "G",
            "G shapes with no capo: sounds in G major.",
        ),
        (
            "de",
            "Die Originalaufnahme ist in A-Dur, aber ich singe es in G-Dur. Ich spiele G-Griffe.",
            "G",
            "G shapes with no capo: sounds in G major.",
        ),
        # review I3: the shapes' own chords were read as keys ("a question about C and D")
        ("en", "I play G Em C D and want it to sound in G", "G Em C D", "G Em C D with no capo: sounds in G major."),
    ],
)
async def test_the_users_own_key_to_sound_in_and_the_shapes_chords_are_no_other_key(
    store: Store, language: str, words: str, shapes: str, line: str
) -> None:
    tools = Tools(store, Profile(store), TZ, no_fetch, chords=ChordTool(language=lambda: language))
    turn = said(store, words)
    await tools.call(turn, "chords", args(action="capo", to_key="G", shapes=shapes))
    [kept] = turn.chords
    assert kept.ok and kept.lines.split("\n")[0] == line, kept.lines


async def test_a_capo_whose_key_is_the_shapes_key_beside_two_other_keys_asks_which(store: Store) -> None:
    turn = said(store, "Should it sound in Bb or in C major? I play G shapes. Which fret?")
    result = await chord_tools(store).call(turn, "chords", args(action="capo", chords="G", shapes="G"))
    assert result == (
        "Error: the key to sound in and the shapes are both G, and the message names Bb and C major: say which key "
        "should sound"
    )
    assert "Capo 0" not in turn.chords[0].lines


@pytest.mark.parametrize(
    "words",
    [
        "Which fret for G shapes so it sounds in G?",
        "I play G shapes and the song has an Em in it. Which capo for G?",  # a capo keeps the mode: Em is no key here
    ],
)
async def test_shapes_in_the_key_to_sound_in_need_no_capo_and_never_say_capo_0(store: Store, words: str) -> None:
    turn = said(store, words)
    await chord_tools(store).call(turn, "chords", args(action="capo", to_key="G", shapes="G"))
    assert turn.chords[0].lines.split("\n")[0] == "G shapes with no capo: sounds in G major."


@pytest.mark.parametrize(
    ("call", "note"),
    [
        (
            {"chords": "Am F C G", "semitones": 2, "to_key": "Bm"},
            "Not worked out: a step and a key to move to, not both.",
        ),
        ({"chords": "Am F C G"}, "Not worked out: no step and no key to move to."),
        ({"chords": "Am F C G", "semitones": 0}, "Not worked out: a step of 1 to 11 half steps, up or down."),
        ({"chords": "G C", "to_key": "Am"}, "Not worked out: song key unclear: G or C major. Say the song's key."),
        ({"action": "sing"}, "Not worked out: not one of transpose, capo, notes or key."),
    ],
)
def test_codes_note_says_a_failed_call_in_plain_words(call: dict[str, Any], note: str) -> None:
    """Review, minor: the user's note named Gemma's fields ("semitones or to_key") and doubled a full stop."""
    assert ChordTool().run({"action": "transpose", **call}, "").lines == note


async def test_a_chord_it_cannot_read_is_named_and_kept_as_an_error(store: Store) -> None:
    turn = said(store, "transpose Hm7b up 2")
    result = await chord_tools(store).call(turn, "chords", args(action="transpose", chords="Hm7b Am", semitones=2))
    assert result == 'Error: not a chord: "Hm7b"'
    [kept] = turn.chords
    assert not kept.ok and kept.lines == 'Not worked out: "Hm7b" is not a chord.'
    bad = await chord_tools(store).call(turn, "chords", args(action="sing", chords="Am"))
    assert bad == "Error: action must be transpose, capo, notes or key"


async def test_a_key_in_words_in_the_users_message_wins_over_the_argument(store: Store) -> None:
    turn = said(store, "Spiel Am F C G in h-Moll")
    await chord_tools(store).call(turn, "chords", args(action="transpose", chords="Am F C G", to_key="Bb minor"))
    [kept] = turn.chords
    assert (
        kept.lines.split("\n")[0]
        == "Up 2 (a whole tone): from Am (A minor, likely; C major also possible) to Hm (B minor)"
    )
    assert kept.lines.split("\n")[1] == "Hm G D A"
    assert "key from your message: B minor (h-Moll)" in kept.lines
    agreeing = said(store, "Am F C G in B minor", T0 + 200_000)
    await chord_tools(store).call(agreeing, "chords", args(action="transpose", chords="Am F C G", to_key="Bm"))
    assert "key from your message" not in agreeing.chords[0].lines


@pytest.mark.parametrize(
    ("language", "words"), [("en", "Spiel Am F C G in h-Moll"), ("de", "Spiel Am F C G in B minor")]
)
async def test_her_key_agrees_only_as_the_move_reads_it(store: Store, language: str, words: str) -> None:
    """Review C5: "Bm" agreed with h-Moll read the English way, then the move read it the German way (B is B♭) and
    went to B♭ minor; her argument is now compared in the naming the move uses, so the user's B minor wins."""
    tools = Tools(store, Profile(store), TZ, no_fetch, chords=ChordTool(language=lambda: language))
    turn = said(store, words)
    await tools.call(turn, "chords", args(action="transpose", chords="Am F C G", to_key="Bm"))
    [kept] = turn.chords
    assert kept.lines.split("\n")[:2] == [
        "Up 2 (a whole tone): from Am (A minor, likely; C major also possible) to Hm (B minor)",
        "Hm G D A",
    ], kept.lines


async def test_her_key_is_kept_when_it_is_one_of_the_users_keys(store: Store) -> None:
    """Review I1: every key not after "from" counted as a target and the last one won, so a second key in the
    message overrode hers; now hers stays when it agrees with any, and only none agreeing gives the first."""
    tools = chord_tools(store)
    cases = [
        ("Move it to C minor; it is in G major now", {"chords": "G Em C D", "to_key": "Cm", "from_key": "G"}, "C"),
        ("Transpose D G A to E flat major, and also to F major", {"chords": "D G A", "to_key": "Eb"}, "Eb"),
        ("Transpose D G A to E flat major, and also to F major", {"chords": "D G A", "to_key": "F"}, "F"),
        ("Move it to C minor; it is in G major now", {"chords": "Am F C G", "to_key": "Dm"}, "Cm"),
    ]
    for number, (words, call, target) in enumerate(cases):
        turn = said(store, words, T0 + 100_000 * (number + 1))
        await tools.call(turn, "chords", args(action="transpose", **call))
        [kept] = turn.chords
        assert f" to {target} (" in kept.lines.split("\n")[0], (words, kept.lines)
        assert ("key from your message" in kept.lines) == (number == 3), kept.lines


@pytest.mark.parametrize(
    ("words", "call", "first"),
    [
        (
            "This song is in G major; put it in Bb: G C D",
            {"chords": "G C D", "to_key": "Bb"},
            "Up 3 (a minor third): from G (G major, likely) to Bb (B♭ major)",
        ),
        (
            "This song is in G major and too low: 3 semitones higher, please. G C D Em",
            {"chords": "G C D Em", "to_key": "Bb"},
            "Up 3 (a minor third): from G (G major, likely; E minor also possible) to Bb (B♭ major)",
        ),
        (
            "This song is in E minor; move it to Am: Em C G D",
            {"chords": "Em C G D", "to_key": "Am"},
            "Up 5 (a fourth): from Em (E minor, likely; G major also possible) to Am (A minor)",
        ),
    ],
)
async def test_a_moves_key_to_move_to_stays_hers_beside_the_songs_own_key_in_words(
    store: Store, words: str, call: dict[str, str], first: str
) -> None:
    """Review C1's twin in the move: the song's key in words ("is in G major") counted as the key to move to, so her
    Bb gave "No step (the same sounds)". The key the chords are in now is no target, unless hers misreads it."""
    turn = said(store, words)
    await chord_tools(store).call(turn, "chords", args(action="transpose", **call))
    [kept] = turn.chords
    assert kept.lines.split("\n")[0] == first, kept.lines
    assert "key from your message" not in kept.lines, kept.lines


async def test_a_misread_key_to_move_to_still_takes_the_users_words(store: Store) -> None:
    turn = said(store, "It is in A minor now. Transpose Am F C G to C minor")
    await chord_tools(store).call(turn, "chords", args(action="transpose", chords="Am F C G", to_key="C"))
    [kept] = turn.chords
    assert " to Cm (C minor)" in kept.lines.split("\n")[0], kept.lines
    assert "key from your message: C minor (C minor)" in kept.lines, kept.lines


async def test_the_pages_key_line_is_read_from_the_web_result_that_holds_the_chords(store: Store) -> None:
    tools = chord_tools(store, web=FakeWeb({"spooky": [OTHER_PAGE, SPOOKY]}))
    turn = said(store, "Spooky, but in G minor")
    await tools.call(turn, "web_search", args(query="spooky dusty springfield chords"))
    await tools.call(turn, "chords", args(action="transpose", chords="Am7 · D6 · Ebdim7 · Em7", to_key="Gm"))
    [kept] = turn.chords
    assert kept.lines.split("\n")[:2] == [
        "Down 2 (a whole tone): from Am (A minor) to Gm (G minor)",
        "Gm7 · C6 · Dbdim7 · Dm7",
    ]
    assert "the page's Key: Am" in kept.lines
    assert kept.source == ("guitartuna.com", "https://guitartuna.com/chords/spooky-dusty-springfield")


def test_the_page_is_found_by_its_chords_never_by_a_word() -> None:
    """Review, minor: page_of matched raw words, so the article "A" was a hit, and one hit picked the page and its
    "Key:" line; it now reads each page's chords as her words are read (design 3.7)."""
    lyrics = WebResult("A song about the sea", "https://lyrics.example/sea", "lyrics.example", "", "A wave. Key: F.")
    assert chords_module.page_of(("A", "D", "E"), [WebResults("Brave", (lyrics,))]) is None
    sheet = WebResult("Sea chords", "https://chords.example/sea", "chords.example", "", "Key: A. Chords: A D E A.")
    page = chords_module.page_of(("A", "D", "E"), [WebResults("Brave", (lyrics, sheet))])
    assert page is not None and page[1] == "https://chords.example/sea"


async def test_after_a_web_search_the_tool_log_keeps_a_stub(store: Store) -> None:
    tools = chord_tools(store, web=FakeWeb({"spooky": [SPOOKY]}))
    turn = said(store, "Spooky in G minor")
    await tools.call(turn, "web_search", args(query="spooky chords"))
    await tools.call(turn, "chords", args(action="transpose", chords="Am7 D6", to_key="Gm"))
    row = store.tool_calls()[-1]
    assert row.name == "chords"
    assert "Am7" not in row.arguments and "Gm7" not in row.result  # a page's chords are never stored
    plain = said(store, "Am F C G up 2", T0 + 200_000)
    await tools.call(plain, "chords", args(action="transpose", chords="Am F C G", semitones=2))
    assert "Am F C G" in store.tool_calls()[-1].arguments  # the user's own chords: kept as any tool call


async def test_the_logs_hold_no_chords_keys_songs_or_sources(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    tools = chord_tools(store, web=FakeWeb({"spooky": [SPOOKY]}))
    turn = said(store, "Spooky in G minor")
    await tools.call(turn, "web_search", args(query="spooky chords"))
    await tools.call(turn, "chords", args(action="transpose", chords="Am7 · D6 · Ebdim7 · Em7", to_key="Gm"))
    await tools.call(turn, "chords", args(action="notes", chords="Hm7b"))
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "chords: transpose, 4 chords" in logged and "chords: notes, error not_a_chord" in logged
    for private in ("Am7", "Gm", "Spooky", "guitartuna", "Hm7b", "minor"):
        assert private not in logged, private


# ---- her rules and her list (design 3.5) ----


def test_the_chords_rules_come_only_with_chords_before_safety() -> None:
    assert prompts.rules(name="Estia", web=True) == prompts.rules(name="Estia", web=True, chords=False)
    on = prompts.rules(name="Estia", web=True, chords=True)
    assert f"\n\n{prompts.CHORDS_RULES}\n{prompts.CHORDS_WEB_RULE}\n\nSafety:\n" in on
    assert "always call chords; never work them out yourself" in on
    assert "say what was done, never write the chords out" in on
    assert "web_search, then chords with the page's chords and section names as found" in on
    without_web = prompts.rules(name="Estia", chords=True)  # no web_search to name
    assert prompts.CHORDS_RULES in without_web and "web_search" not in prompts.CHORDS_RULES
    assert prompts.CHORDS_WEB_RULE not in without_web
    assert "music" not in prompts.CHORDS_RULES.casefold()  # "music" is Spotify's (the evals read it so)


def test_her_list_names_chords_only_with_chords() -> None:
    off = prompts.capabilities(host="the Mini")
    on = prompts.capabilities(host="the Mini", chords=True)
    assert [line for line in on if line not in off] == [
        "• Chords: transposed, moved for a capo, a chord's notes and the key of given chords, worked out on the Mini "
        'and given in a note from the code. Example: "transpose Am F C G up 2"'
    ]
    assert "music" not in prompts.CAPABILITY["chords"].casefold()


def test_with_chords_the_app_offers_the_tool_and_says_so(tmp_path: Path) -> None:
    app = bridge(tmp_path, chords_enabled=True, calls_enabled=True, voice_url="http://127.0.0.1:8150")
    try:
        assert "chords" in [schema["function"]["name"] for schema in app.tools.schemas]
        rules = app._rules("en")
        assert prompts.CHORDS_RULES in rules
        call_rules = prompts.without_capabilities(rules)
        assert prompts.CHORDS_RULES in call_rules  # offered on calls too
        assert "Chords: transposed" in "\n".join(app.capability_lines())
        assert "Chords: transposed" not in call_rules  # never the list on a call
    finally:
        app.store.close()


# ---- the reader of chords in her words (design 3.7): each language's context rules, as her voice reads them ----

READ = [  # (language, her words, the chords read in them)
    ("en", "C G7 Am F", ["C", "G7", "Am", "F"]),
    ("en", "Capo 2 Am F C G", ["Am", "F", "C", "G"]),
    ("en", "Em7 - Cadd9 - G/B - D/F#", ["Em7", "Cadd9", "G/B", "D/F#"]),
    ("en", "Bb, Ebmaj7, F7sus4", ["Bb", "Ebmaj7", "F7sus4"]),
    ("en", "Verse: G D Em C", ["G", "D", "Em", "C"]),
    ("en", "The verse goes E, D, A, E, then G and C.", ["E", "D", "A", "E", "G", "C"]),
    ("en", "The key is E♭ major, with a B♭7 and an F♯m at the end.", ["E♭ major", "B♭7", "F♯m"]),
    ("en", "Up 2: B minor, G, D, A.", ["B minor", "G", "D", "A"]),  # review I9: a key in words joins a run
    ("en", "That is B-flat minor, then E-flat major.", ["B-flat minor", "E-flat major"]),
    ("en", "Play Bm/G/E/A here.", ["Bm", "G", "E", "A"]),  # three slashes or more: a list, not a slash chord
    ("en", "On the guitar it sounds in B flat.", ["B flat"]),
    ("en", "It goes Cmi7 to Fo7 and Bh7.", ["Cmi7", "Fo7", "Bh7"]),  # theory's spellings with a number count too
    ("es", "C, G, Am y F", ["C", "G", "Am", "F"]),
    ("es", "Fa#m y Sib", ["Fa#m", "Sib"]),
    ("es", "Am - F - C - G", ["Am", "F", "C", "G"]),
    ("es", "Los acordes son C, G7 y Am.", ["C", "G7", "Am"]),
    ("es", "Toca F#m7 y luego Am.", ["F#m7", "Am"]),
    ("es", "La canción está en la menor.", ["la menor"]),
    ("es", "Sube a si menor, Sol, Re, La.", ["si menor", "Sol", "Re", "La"]),
    ("fr", "Sib et Ré7", ["Sib", "Ré7"]),
    ("fr", "Am, Dm et E7", ["Am", "Dm", "E7"]),
    ("it", "Lam e Re7", ["Lam", "Re7"]),
    ("it", "Do, Sol, Lam e Fa", ["Do", "Sol", "Lam", "Fa"]),
    ("it", "Gli accordi: La, Mi e Sim.", ["La", "Mi", "Sim"]),
    ("pt", "F#m e Bb", ["F#m", "Bb"]),
    ("nl", "F#m en D7", ["F#m", "D7"]),
    ("nl", "Bb, Eb en Ab7", ["Bb", "Eb", "Ab7"]),
    ("nl", "Am, C en G", ["Am", "C", "G"]),
    ("de", "Hm und C#m", ["Hm", "C#m"]),
    ("de", "Fis7 und Es", ["Fis7", "Es"]),
    ("de", "Am, Dm und E7", ["Am", "Dm", "E7"]),
    ("de", "Die Akkorde sind Hm, Fis7, Es-Dur, B und C#m.", ["Hm", "Fis7", "Es-Dur", "B", "C#m"]),
    ("de", "Plan B und C#m", ["C#m"]),
    ("pl", "H7 i C#m", ["H7", "C#m"]),
    ("pl", "Am, Dm i E7", ["Am", "Dm", "E7"]),
    ("ru", "Am, H7 и Bb", ["Am", "H7", "Bb"]),
    ("ru", "C, F#m и B7", ["C", "F#m", "B7"]),
    ("ja", "F#m7、B♭", ["F#m7", "B♭"]),
    ("ja", "コードはAm、C#7です", ["Am", "C#7"]),
    ("ko", "Am, F#m7", ["Am", "F#m7"]),
    ("ko", "C, G, Am", ["C", "G", "Am"]),
]


@pytest.mark.parametrize(("language", "words", "expected"), READ)
def test_the_reader_finds_chords_as_her_voice_reads_them(language: str, words: str, expected: list[str]) -> None:
    assert [mention.text for mention in chords_module.read(words, language)] == expected


NEVER_FLAGGED = {  # everyday sentences that look like chords: never read as chords
    "en": [
        "Am I late?",
        "Dm me later.",
        "Run an A/B test.",
        "Get an A+.",
        "Choose A, B or C.",
        "Em, I mean, maybe.",
        "16 Gb free",
        "the bb gun",
        "Amin said hi",
        "Bar 12 Am",
        "I like C# and F# for code",
        "The CD and the DJ",
        "The Chorus begins",
        "Pick A, B or C.",
        "Plan B",
        "A good plan.",
        "The G7 summit was in June.",
        # everyday words that theory's parser reads but ChordSpeller never does (review C1): "Do" as D°, "Go" as G°,
        # "Ah" and "Eh" as half-diminished, "C++" and "Emma"
        "Do you know what time it is?",
        "Go to bed, it's late!",
        "Ah, I see.",
        "Eh, maybe.",
        "Do you want it in another key?",
        "I write C++ code.",
        "Emma loves her guitar.",
        "Emo songs, mostly.",
    ],
    "es": [
        "Si quieres, mi canción empieza en La.",
        "A, B y C",
        "La cumbre del G7 fue en junio.",
        "Mi Audi A7 es rojo.",
        "Programo en C# y F#.",
        "Hoy hace sol y la casa es bonita.",
        "Mi casa",
        "No tengo la menor idea.",  # review C2: "la menor" and "mi mayor" before a noun are no keys
        "La mayor parte del día.",
        "Mi mayor consejo: descansa.",
    ],
    "fr": ["Si tu veux.", "Je code en C#, si tu veux.", "Si tu veux, Do, Ré, Mi"],
    "it": [
        "E poi la canzone.",
        "La Sim del telefono",
        "Do una mano a Re Artù.",
        "La casa è grande",
        "Sei la minore delle sorelle.",
    ],
    "pt": ["Sol e mar.", "Em 2026, Sim.", "O filho do maior empresário."],
    "nl": ["Bes en Fis", "Ik programmeer in C#."],
    "de": ["Am Montag ist es kalt.", "Ab morgen", "Programmiere in C#.", "Hm, das weiß ich nicht.", "Plan B"],
    "pl": ["Plan B"],
    "ru": ["Пишу на C# и F#."],
    "ja": ["Aランク", "第1位、A案とB案"],
    "ko": ["A형", "A형, 6개월, 3원칙"],
}


@pytest.mark.parametrize(
    ("language", "words"), [(language, words) for language, rows in NEVER_FLAGGED.items() for words in rows]
)
def test_an_everyday_sentence_is_never_read_as_chords(language: str, words: str) -> None:
    assert chords_module.read(words, language) == []


CRAFTED = [  # review I2: many chords in many different sentences, the reader's worst case (it was 1.5 to 19 s)
    ("en", "".join(f"Em7 {number}. " for number in range(600))[:4_000]),
    ("en", "".join(f"Am {number}. Em7 {number}. " for number in range(600))[:4_000]),
    ("es", "".join(f"Fa7 {number}. Lam {number}. " for number in range(600))[:4_000]),
    ("de", "".join(f"Fis7 {number}. Dm {number}. " for number in range(600))[:4_000]),
    ("es", "Lam y Re, " * 400),
]


@pytest.mark.parametrize(("language", "words"), CRAFTED, ids=["en", "en-minor", "es", "de", "es-one-sentence"])
def test_the_reader_takes_time_in_line_with_the_text(language: str, words: str) -> None:
    """Review I2: each sentence is worked out once, so 4,000 crafted characters read in well under the 0.5 s
    budget (the check runs twice an answer, on the bridge's event loop: a call waits meanwhile)."""
    started = time.perf_counter()
    for lines in (False, True):
        chords_module.read(words, language, lines=lines)
    assert time.perf_counter() - started < 0.5


def test_the_check_takes_time_in_line_with_its_sources() -> None:
    page = "".join(f"Am {number}. Em7 {number}. " for number in range(200))[:1_200]
    answer = "".join(f"Bm {number}. F#m7 {number}. " for number in range(600))[:4_000]
    started = time.perf_counter()
    for language in ("en", "es"):
        chords_module.check(answer, results=[], sources=[page] * 5, language=language)
    assert time.perf_counter() - started < 0.5


PACKS = Path(__file__).resolve().parents[1] / "tools/voice/Sources/VoiceCore/Languages"


@pytest.mark.parametrize("language", ["de", "it", "es", "fr", "nl", "pt", "ru", "pl", "ja", "ko"])
def test_the_readers_music_words_are_the_voice_packs(language: str) -> None:
    """The reader's music words are the Swift packs' (Pack_<code>.swift), so her words are read as she says them."""
    source = (PACKS / f"Pack_{language}.swift").read_text(encoding="utf-8")
    style = source[source.index("var chords: ChordWords.Style") :]
    style = style[: style.index("\n    }\n")]
    found = re.search(r"music: \[([^\]]*)\]", style)
    words = re.findall(r'"([^"]*)"', found.group(1)) if found else []
    assert chords_module.MUSIC_WORDS.get(language, ()) == tuple(words)
    joins = re.findall(r'"([^"]*)"', re.search(r"joins: \[([^\]]*)\]", style).group(1))  # type: ignore[union-attr]
    assert chords_module.JOINS.get(language, ()) == tuple(joins)


# ---- the reply check (design 3.7): her chords must be code's, or a source's when the tool did not run ----


def moved(chords: str, semitones: int) -> chords_module.Result:
    result = ChordTool().run({"action": "transpose", "chords": chords, "semitones": semitones}, "")
    assert result.ok
    return result


def test_with_the_tool_her_chords_must_be_the_results() -> None:
    result = moved("Am F C G", 2)
    kept = chords_module.check("Done: up a whole tone. It is Bm G D A now.", results=[result], sources=[])
    assert (kept.text, kept.left_out) == ("Done: up a whole tone. It is Bm G D A now.", 0)
    wrong = chords_module.check("Done: up a whole tone. It is Bm G D A# now.", results=[result], sources=[])
    assert (wrong.text, wrong.left_out) == ("Done: up a whole tone.", 1)
    richer = chords_module.check("Try Bm7 G D A.", results=[result], sources=[])
    assert richer.left_out == 1  # by root and ending: the tool's result is Bm
    key = chords_module.check("That puts it in B minor.", results=[result], sources=[])
    assert key.left_out == 0  # a key in words counts by its root
    given = chords_module.check("From Am F C G to Bm G D A.", results=[result], sources=[])
    assert given.left_out == 0  # the chords the tool was given count too
    page = chords_module.check("The page also has Em7.", results=[result], sources=["Am F C G Em7"])
    assert page.left_out == 1  # a page's other chords do not count once the tool ran


def test_without_the_tool_her_chords_need_a_root_the_sources_have() -> None:
    sources = [SPOOKY.text]
    fine = chords_module.check("The chords are Am7, D6, Ebdim7 and Em7.", results=[], sources=sources)
    assert fine.left_out == 0 and fine.held
    richer = chords_module.check("Mostly Am and D.", results=[], sources=sources)
    assert richer.left_out == 0  # a richer or plainer ending passes: the root is the page's
    invented = chords_module.check("The chords are Am7 and D6. The bridge goes to G7.", results=[], sources=sources)
    assert (invented.text, invented.left_out) == ("The chords are Am7 and D6.", 1)
    nothing = chords_module.check("Try Am F C G.", results=[], sources=["How are you?"])
    assert not nothing.held and nothing.left_out == 0  # no source holds chords: no check


def test_a_list_of_chords_is_read_whole_and_left_out_whole() -> None:
    """Review C3: a list, Gemma's usual way to write chords, is read across its lines as one run; a run with another
    chord goes whole, never leaving part of a list that looks like the answer."""
    sources = ["Transpose Am F C G up 2"]
    for answer in (
        "Sure, up a whole tone:\n- Bm\n- G\n- E\n- A",
        "Sure, up a whole tone:\n* Bm\n* G\n* E\n* A",
        "Sure, up a whole tone:\n• Bm\n• G\n• E\n• A",
        "Sure, up a whole tone:\n1. Bm\n2. G\n3. E\n4. A",
        "Sure, up a whole tone:\n- **Bm**\n- **G**\n- **E**\n- **A**",
    ):
        found = chords_module.check(answer, results=[], sources=sources)
        assert (found.text, found.held) == ("Sure, up a whole tone:", True), answer
    bold = chords_module.check("Up a whole tone: **Bm** **G** **E** **A**. Enjoy!", results=[], sources=sources)
    assert (bold.text, bold.left_out) == ("Enjoy!", 1)  # one sentence, one run
    result = moved("Am F C G", 2)
    assert chords_module.check("Done:\n- Bm\n- G\n- D\n- A", results=[result], sources=[]).left_out == 0
    assert chords_module.check("Done:\n- Bm\n- G\n- E\n- A", results=[result], sources=[]).text == "Done:"


def test_chords_as_said_on_a_call_are_checked() -> None:
    """Review I9: "B minor, G, E, A" (as she says chords on a call), "B-flat minor" and "Bm/G/E/A" passed."""
    result = moved("Am F C G", 2)  # Bm G D A
    for wrong in (
        "Up 2: B minor, G, E, A.",
        "It is in B-flat minor now.",
        "It is Bm/G/E/A now.",
        "On the guitar it sounds in B flat.",
    ):
        assert chords_module.check(wrong, results=[result], sources=[]).left_out == 1, wrong
    for right in ("Up 2: B minor, G, D, A.", "It is Bm/G/D/A now.", "It is in B minor now."):
        assert chords_module.check(right, results=[result], sources=[]).left_out == 0, right


def capo_of(words: str, language: str = "en", **call: Any) -> chords_module.Result:
    result = ChordTool(language=lambda: language).run({"action": "capo", **call}, words)
    assert result.ok, result.lines
    return result


@pytest.mark.parametrize(
    ("language", "answer", "kept"),
    [
        # review C2: in all 4 runs code's note said Capo 3, her words "Capo im 4. Bund", twice "the tool is wrong"
        ("de", "Für B-Dur mit G-Griffen setzt du den Capo im 4. Bund.", ""),
        (
            "de",
            "Das Tool hat den 3. Bund vorgeschlagen, aber für B-Dur brauchst du den 4. Bund. Viel Spaß beim Spielen!",
            "Viel Spaß beim Spielen!",
        ),
        (
            "de",
            "Kapodaster in den 3. Bund, dann klingt es in B-Dur.",
            "Kapodaster in den 3. Bund, dann klingt es in B-Dur.",
        ),
        ("en", "Put the capo on fret 4 and play G shapes.", ""),
        ("en", "Put the capo on the 4th fret. Enjoy!", "Enjoy!"),
        (
            "en",
            "Capo 3 with G shapes. The capo raises it by 3 semitones.",
            "Capo 3 with G shapes. The capo raises it by 3 semitones.",
        ),
        ("en", "Put the capo on the 3rd fret.", "Put the capo on the 3rd fret."),
        ("fr", "Mets le capo à la 4e case.", ""),
        ("it", "Metti il capotasto al 4° tasto.", ""),
        ("es", "Pon la cejilla en el traste 4.", ""),
        ("ja", "カポを4フレットに付けてください。", ""),
    ],
)
def test_her_capo_fret_must_be_one_code_worked_out(language: str, answer: str, kept: str) -> None:
    """Review C2: the check read chords, never frets, so her own wrong fret reached the user beside code's note. A
    fret she names (a number after a fret word, or before it as an ordinal) must be one a result or a source names."""
    result = capo_of("Welcher Bund für B-Dur mit G-Griffen?", language, to_key="B-Dur", shapes="G")
    assert result.lines.startswith("Capo 3 with G shapes")
    found = chords_module.check(answer, results=[result], sources=["Which fret?"], language=language)
    assert found.text == kept and found.left_out == (0 if kept == answer else 1), found


def test_a_fret_the_user_named_or_no_capo_passes() -> None:
    result = capo_of("My capo is on 5 now. Which fret for Bb with G shapes?", to_key="Bb", shapes="G")
    answer = "You have it on 5 now; for B flat with G shapes, move the capo to fret 3."
    assert chords_module.check(answer, results=[result], sources=["My capo is on 5 now."]).left_out == 0
    none = capo_of("Which fret for G shapes so it sounds in G?", to_key="G", shapes="G")
    assert chords_module.check("No capo: capo 0, open G shapes.", results=[none], sources=[]).left_out == 0
    many = capo_of("Which capo for Bb?", to_key="Bb")  # each open shape's fret
    assert chords_module.check("A shapes at capo 1, or G shapes at capo 3.", results=[many], sources=[]).left_out == 0
    plain = moved("Am F C G", 2)  # no fret in code's result: frets are not read
    assert chords_module.check("Bm G D A, or capo 2 with the old shapes.", results=[plain], sources=[]).left_out == 0


def test_a_long_answer_is_checked_to_its_end() -> None:
    """Read whole (review C3), her answer is read past the 4,000 characters a source gets: a document answer runs
    to 2,000 tokens, and a wrong chord at its end still goes."""
    filler = "This line has no chords at all, only words. " * 120  # about 5,300 characters
    found = chords_module.check(f"{filler}Then it is Bm G D A#.", results=[moved("Am F C G", 2)], sources=[])
    assert found.left_out == 1 and found.text == filler.strip()


def test_a_line_is_its_own_sentence_in_her_words() -> None:
    """Read whole, her answer's lines still end a sentence: no music word or article rule reaches the next line."""
    result = moved("Em C G D", 1)  # Fm Db Ab Eb: no A, no E minor
    for answer in ("Done, moved up.\nA good plan: play it slowly.", "Here is the song\nEm, I mean, maybe ask me"):
        assert chords_module.check(answer, results=[result], sources=[]).left_out == 0, answer


def test_an_everyday_phrase_in_the_sources_is_no_key() -> None:
    """Review C2: "la menor idea" in the user's message held no key, so her "Mi mayor consejo" is never checked."""
    found = chords_module.check(
        "Mi mayor consejo: descansa.", results=[], sources=["No tengo la menor idea de qué hacer."], language="es"
    )
    assert (found.text, found.left_out, found.held) == ("Mi mayor consejo: descansa.", 0, False)


def test_the_same_chord_in_every_naming_and_alias() -> None:
    solfege = ChordTool().run({"action": "transpose", "chords": "LAm FA DO SOL", "semitones": 2}, "")
    assert chords_module.check("Ecco: SIm SOL RE LA.", results=[solfege], sources=[], language="it").left_out == 0
    assert chords_module.check("That is Bm G D A in letters.", results=[solfege], sources=[]).left_out == 0
    jazz = moved("Cmaj7 Am7 Dm7 G7", 2)
    assert chords_module.check("It goes DΔ7 Bm7 Em7 A7.", results=[jazz], sources=[]).left_out == 0
    assert chords_module.check("It goes Dmaj7 Bmin7 Em7 A7.", results=[jazz], sources=[]).left_out == 0
    german = ChordTool().run({"action": "transpose", "chords": "Hm G D A", "semitones": 1}, "", ())
    assert german.chords == ("Cm", "As", "Es", "B") or german.chords == ("Cm", "Ab", "Eb", "B")
    assert chords_module.check("Also Cm, Ab, Eb und B.", results=[german], sources=[], language="de").left_out == 0


def test_everyday_words_are_never_flagged() -> None:
    result = moved("Am F C G", 2)
    for language, rows in NEVER_FLAGGED.items():
        for words in rows:
            assert chords_module.check(words, results=[result], sources=[], language=language).left_out == 0, words


# ---- code's note and the check in an answer (brain, design 3.7) ----

TRANSPOSE: dict[str, Any] = {"action": "transpose", "chords": "Am F C G", "semitones": 2}
LINES = (
    "Up 2 (a whole tone): from Am (A minor, likely; C major also possible) to Bm (B minor)\nBm G D A\nRead as: Am F C G"
)


def chord_brain(store: Store, gemma: FakeGemma, *, chords: bool = True, web: FakeWeb | None = None) -> Brain:
    profile = Profile(store)
    tools = Tools(store, profile, TZ, no_fetch, web=web, calls=True, chords=ChordTool() if chords else None)
    return Brain(gemma, ContextBuilder(store, profile, TZ), tools)


async def ask(store: Store, brain: Brain, words: str, ts: int = T0) -> Any:
    conversation = store.current_conversation()
    message_id = store.add_incoming(conversation.id, ME, ts, "text", words)
    assert message_id is not None
    return await brain.answer(conversation, store.message(message_id))


async def test_the_tools_result_comes_in_codes_note(store: Store) -> None:
    gemma = FakeGemma(tool("chords", **TRANSPOSE), text("Done: up a whole tone."))
    reply = await ask(store, chord_brain(store, gemma), "Transpose Am F C G up 2")
    assert reply.text == "Done: up a whole tone."
    assert reply.chords == f"**Chords**\n{LINES}"
    assert reply.chords_spoken == "Up 2, a whole tone: Bm G D A. " + prompts.CHORDS_LATER


async def test_two_results_one_note_in_call_order_and_an_error_only_when_nothing_worked(store: Store) -> None:
    gemma = FakeGemma(
        tool("chords", call_id="a", action="transpose", chords="Hm7b Am", semitones=2),
        tool("chords", call_id="b", **TRANSPOSE),
        tool("chords", call_id="c", action="notes", chords="G7"),
        text("Both done."),
    )
    reply = await ask(store, chord_brain(store, gemma), "Transpose Am F C G up 2, and the notes of G7?")
    # the retried error is not in it: "Hm7b" was hers, not the user's
    assert reply.chords == f"**Chords**\n{LINES}\n\nG7: G B D F\nRead as: G7"
    failed = FakeGemma(
        tool("chords", call_id="a", action="transpose", chords="Hm7b", semitones=2),
        tool("chords", call_id="b", action="transpose", chords="Hm7b", semitones=2),
        text("I could not read Hm7b."),
    )
    error = await ask(store, chord_brain(store, failed), "Transpose Hm7b up 2", T0 + 1_000)
    assert error.chords == '**Chords**\nNot worked out: "Hm7b" is not a chord.'  # once


async def test_the_users_chord_that_could_not_be_read_stays_in_the_note_after_a_retry(store: Store) -> None:
    """Eval of 18 Oct (chords_unknown): "Hm7b" was refused, Gemma called again without it, and the note held only
    that result: the user never learnt that Hm7b could not be read."""
    gemma = FakeGemma(
        tool("chords", call_id="a", action="transpose", chords="Hm7b Am", semitones=2),
        tool("chords", call_id="b", action="transpose", chords="Am", semitones=2),
        text("Done, up a whole tone."),
    )
    reply = await ask(store, chord_brain(store, gemma), "Transpose Hm7b Am up 2")
    head, refused, blank, *moved = reply.chords.split("\n")
    assert (head, refused, blank) == ("**Chords**", 'Not worked out: "Hm7b" is not a chord.', "")
    assert moved[-1].startswith("Read as: ") and moved[-1].endswith('; "Hm7b" left out'), moved
    assert reply.chords_spoken.startswith('Not worked out: "Hm7b" is not a chord. Up 2, a whole tone: ')


async def test_a_chord_put_in_place_of_the_users_unread_one_is_refused_as_a_guess(store: Store) -> None:
    """Review I4: GUIDE.md and her rules say a chord that cannot be read is never guessed, yet her "Hm7" for the
    user's "Hm7b" was worked out and sent, and on a call said with no word that it was a guess. Code refuses it."""
    gemma = FakeGemma(
        tool("chords", call_id="a", action="transpose", chords="Hm7b Am", semitones=2),
        tool("chords", call_id="b", action="transpose", chords="Hm7 Am", semitones=2),  # the eval's run
        text("I could not read Hm7b: is it Hm7 or Hm7b5?"),
    )
    reply = await ask(store, chord_brain(store, gemma), "Transpose Hm7b Am up 2")
    second = gemma.calls[2]["messages"][-1]
    assert second["role"] == "tool" and second["content"] == (
        'Error: "Hm7b" could not be read, and a chord in its place is a guess: leave it out, or ask the user which '
        "chord it is"
    )
    assert reply.chords == '**Chords**\nNot worked out: "Hm7b" is not a chord.'  # the guess is no news
    assert reply.chords_spoken == 'Not worked out: "Hm7b" is not a chord.'
    assert reply.text == "I could not read Hm7b: is it Hm7 or Hm7b5?"


def test_every_chord_of_the_users_a_retry_leaves_out_is_named() -> None:
    """Review, minor 7: only the chord that could not be read was named; another chord of the user's that her retry
    dropped went unmentioned. A section label she dropped is no chord."""
    tool_ = ChordTool()
    message = "Transpose Verse: Hm7b Am F up 2"
    refused = tool_.run({"action": "transpose", "chords": "Verse: Hm7b Am F", "semitones": 2}, message)
    retry = tool_.run({"action": "transpose", "chords": "Am", "semitones": 2}, message, earlier=[refused])
    assert retry.ok and retry.lines.endswith('; "Hm7b" and "F" left out'), retry.lines
    kept = tool_.run({"action": "transpose", "chords": "Am F", "semitones": 2}, message, earlier=[refused])
    assert kept.ok and kept.lines.endswith('; "Hm7b" left out'), kept.lines


def test_the_users_unread_chord_is_a_whole_word_of_theirs_in_their_spelling() -> None:
    """Review, minor 6: a chord found anywhere in the user's text counted as theirs ("ama" inside "mama"), and the
    note quoted her spelling ("Sim7b"), not the user's ("SIm7b")."""
    tool_ = ChordTool(language=lambda: "it")
    fragment = tool_.run({"action": "transpose", "chords": "ama", "semitones": 2}, "mama mia, up 2")
    assert not fragment.ok and fragment.unread == ""
    spelled = tool_.run({"action": "transpose", "chords": "Sim7b LAm", "semitones": 2}, "Trasponi SIm7b LAm di un tono")
    assert spelled.unread == "SIm7b"
    assert spelled.lines == 'Not worked out: "SIm7b" is not a chord.'


def test_a_guess_is_refused_for_a_pages_chord_too_and_her_own_typo_is_no_guess() -> None:
    tool_ = ChordTool()
    page = [WebResults("Ollama", (WebResult("Song", "https://chords.example/s", "chords.example", "", "Am Fmaj7b C"),))]
    refused = tool_.run({"action": "transpose", "chords": "Am Fmaj7b C", "semitones": 2}, "the song up 2", page)
    assert refused.unread == "Fmaj7b"
    guessed = tool_.run(
        {"action": "transpose", "chords": "Am Fmaj7 C", "semitones": 2}, "the song up 2", page, earlier=[refused]
    )
    assert not guessed.ok and guessed.error == "guessed"
    typo = tool_.run({"action": "transpose", "chords": "Bm7b Am", "semitones": 2}, "Transpose Bm7 Am up 2")
    assert not typo.ok and typo.unread == ""  # hers: not in the message
    fixed = tool_.run(
        {"action": "transpose", "chords": "Bm7 Am", "semitones": 2}, "Transpose Bm7 Am up 2", earlier=[typo]
    )
    assert fixed.ok and "left out" not in fixed.lines


async def test_her_sentence_with_another_chord_is_left_out_after_the_tool(store: Store) -> None:
    gemma = FakeGemma(tool("chords", **TRANSPOSE), text("Done: up a whole tone. So it is Bm G D A#."))
    reply = await ask(store, chord_brain(store, gemma), "Transpose Am F C G up 2")
    assert reply.text == "Done: up a whole tone."
    assert reply.chords == f"**Chords**\n{LINES}"


async def test_all_her_sentences_left_out_after_the_tool_give_a_neutral_line(store: Store) -> None:
    """Review I5: "Sorry, I got confused" before code's correct note said the opposite of what happened."""
    gemma = FakeGemma(tool("chords", **TRANSPOSE), text("It is Bm G D A#."))
    reply = await ask(store, chord_brain(store, gemma), "Transpose Am F C G up 2")
    assert reply.text == prompts.CHORDS_DONE == "Done."
    assert reply.chords == f"**Chords**\n{LINES}"
    call = FakeGemma(tool("chords", **TRANSPOSE), text("It is Bm G D A#."))
    spoken = await ask(store, chord_brain(store, call), f"{prompts.CALL_MARK} transpose Am F C G up 2", T0 + 1_000)
    assert spoken.text == "Done." and spoken.chords_spoken.startswith("Up 2, a whole tone: Bm G D A.")
    failed = FakeGemma(tool("chords", action="transpose", chords="Hm7b", semitones=2), text(""), text(""))
    confused = await ask(store, chord_brain(store, failed), "Transpose Hm7b up 2", T0 + 2_000)
    assert confused.text == prompts.CONFUSED  # nothing worked: no "Done."


async def test_chords_worked_out_in_her_head_get_one_nudge(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    gemma = FakeGemma(text("Up 2 it is Bm G D A."), tool("chords", **TRANSPOSE), text("Done: up a whole tone."))
    with caplog.at_level("WARNING", logger="local_assistant.brain"):
        reply = await ask(store, chord_brain(store, gemma), "Transpose Am F C G up 2")
    assert "her chords not worked out by a chords call; asking Gemma once more" in caplog.text  # a plain log line
    assert gemma.calls[1]["messages"][-1] == {"role": "user", "content": prompts.CHORDS_NUDGE}
    assert reply.text == "Done: up a whole tone." and reply.chords == f"**Chords**\n{LINES}"


async def test_a_list_of_her_own_chords_gets_the_nudge(store: Store) -> None:
    """Review C3: the list she writes without the tool is checked like any other answer."""
    listed = text("Sure, up a whole tone:\n- Bm\n- G\n- E\n- A")
    gemma = FakeGemma(listed, tool("chords", **TRANSPOSE), text("Done: up a whole tone."))
    reply = await ask(store, chord_brain(store, gemma), "Transpose Am F C G up 2")
    assert gemma.calls[1]["messages"][-1] == {"role": "user", "content": prompts.CHORDS_NUDGE}
    assert reply.text == "Done: up a whole tone." and reply.chords == f"**Chords**\n{LINES}"


async def test_still_her_own_chords_after_the_nudge_are_left_out_with_codes_line(store: Store) -> None:
    gemma = FakeGemma(text("Sure. Up 2 it is Bm G D A."), text("Sure. It is Bm G D A."))
    reply = await ask(store, chord_brain(store, gemma), "Transpose Am F C G up 2")
    assert len(gemma.calls) == 2  # one nudge, never more
    assert reply.text == "Sure."
    assert reply.chords == prompts.CHORDS_LEFT_OUT


async def test_on_a_call_her_own_chords_are_left_out_at_once(store: Store) -> None:
    gemma = FakeGemma(text("Sure. Up 2 it is Bm G D A."))
    reply = await ask(store, chord_brain(store, gemma), f"{prompts.CALL_MARK} transpose Am F C G up 2")
    assert len(gemma.calls) == 1  # no nudge on a call: it would cost the caller a second wait
    assert reply.text == "Sure."
    assert reply.chords_spoken == prompts.CHORDS_LEFT_OUT


async def test_chords_from_the_page_pass_without_the_tool(store: Store) -> None:
    gemma = FakeGemma(
        tool("web_search", query="spooky dusty springfield chords"),
        text("Spooky goes Am7, D6, Ebdim7 and Em7."),
    )
    brain = chord_brain(store, gemma, web=FakeWeb({"spooky": [SPOOKY]}))
    reply = await ask(store, brain, "What are the chords of Spooky?")
    assert reply.text == "Spooky goes Am7, D6, Ebdim7 and Em7." and reply.chords == ""


async def test_the_turn_before_is_a_source(store: Store) -> None:
    first = FakeGemma(tool("chords", **TRANSPOSE), text("Done: up a whole tone."))
    brain = chord_brain(store, first)
    reply = await ask(store, brain, "Transpose Am F C G up 2")
    conversation = store.current_conversation()
    store.add_outgoing(conversation.id, ME, "text", reply.text, None)
    store.add_outgoing(conversation.id, ME, "note", reply.chords, None)
    first.add(text("In B minor, Bm is the tonic."))
    follow = await ask(store, brain, "which one is the tonic now?", T0 + 1_000)
    assert follow.text == "In B minor, Bm is the tonic."  # Bm was in code's note the turn before


async def test_an_everyday_exchange_is_never_checked_as_chords(store: Store) -> None:
    """Review C1: "Do" and "Go" are no chords, so no nudge and no left-out note; after a transpose too."""
    gemma = FakeGemma(text("Go to bed, it's late!"))
    reply = await ask(store, chord_brain(store, gemma), "Do you know what time it is?")
    assert reply.text == "Go to bed, it's late!" and reply.chords == "" and len(gemma.calls) == 1
    done = FakeGemma(tool("chords", **TRANSPOSE), text("Done: up a whole tone. Do you want it in another key?"))
    after = await ask(store, chord_brain(store, done), "Transpose Am F C G up 2", T0 + 1_000)
    assert after.text == "Done: up a whole tone. Do you want it in another key?"


async def test_without_chords_her_words_are_not_checked(store: Store) -> None:
    gemma = FakeGemma(text("Up 2 it is Bm G D A."))
    reply = await ask(store, chord_brain(store, gemma, chords=False), "Transpose Am F C G up 2")
    assert reply.text == "Up 2 it is Bm G D A." and reply.chords == "" and reply.chords_spoken == ""
    assert len(gemma.calls) == 1


# ---- the note in the app: after the Sources note, never in her history ----


async def test_on_texts_codes_note_follows_the_sources_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(
        tool("web_search", query="spooky dusty springfield chords"),
        tool("chords", action="transpose", chords="Am7 · D6 · Ebdim7 · Em7", to_key="Gm"),
        text("Done: Spooky down a whole tone, to G minor."),
    )
    harness = await start(
        tmp_path, monkeypatch, gemma, web_enabled=True, web_keys_path=tmp_path / "web.env", chords_enabled=True
    )
    try:
        app = harness.app
        app.tools._web = FakeWeb({"spooky": [SPOOKY]})
        deliver(harness.folder, "Spooky, but in G minor", NOW)
        await until(lambda: len(sent(harness.folder)) == 3)
        her, sources, note = sent(harness.folder)
        assert her == "Done: Spooky down a whole tone, to G minor."
        assert sources == "🔎 Sources\n• guitartuna.com: https://guitartuna.com/chords/spooky-dusty-springfield"
        assert note.startswith(
            "Chords\nDown 2 (a whole tone): from Am (A minor) to Gm (G minor)\nGm7 · C6 · Dbdim7 · Dm7"
        )
        history = json.dumps(app.context.build(app.store.current_conversation(), None))
        assert "Gm7" not in history  # code's note is never in her history (she would imitate it)
    finally:
        await stop(harness)


# ---- calls: said after her words, and one message from code after the call (design 3.7, E3) ----

NOS = WebResult("Spooky, the song", "https://news.example/spooky", "news.example", "", "A song from 1967.")


async def chord_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gemma: FakeGemma, **settings: Any) -> Any:
    harness = await start(
        tmp_path,
        monkeypatch,
        gemma,
        chords_enabled=True,
        web_enabled=True,
        web_keys_path=tmp_path / "web.env",
        **call_settings(tmp_path),
        **settings,
    )
    harness.app.tools._web = FakeWeb({"spooky": [SPOOKY], "news": [NOS]})
    # the start done (recovery ran): a note stored before recovery is sent twice, once by it (core.md, Gotchas)
    await until(lambda: harness.app.store.get_state(ALIVE_KEY) is not None)
    return harness


async def test_on_a_call_the_result_is_said_after_her_words_and_texted_after_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(
        tool("web_search", query="spooky dusty springfield chords"),
        tool("chords", action="transpose", chords="Am7 · D6 · Ebdim7 · Em7", to_key="Gm"),
        text("Done: down a whole tone."),
    )
    harness = await chord_call(tmp_path, monkeypatch, gemma)
    try:
        app = harness.app
        app.call_started(ME)
        said = await app.call_turn(ME, "Spooky, but in G minor", lambda: None)
        assert said.text == "Done: down a whole tone."
        assert said.code_text == "Down 2, a whole tone: Gm7 C6 Dbdim7 Dm7. " + prompts.CHORDS_LATER
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder)[0].startswith("🔎 Sources")  # the Sources note during the call, as today
        app.call_ended()
        await app.call_chords(ME)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder)[1] == (
            "Chords from the call\n"
            "Down 2 (a whole tone): from Am (A minor) to Gm (G minor)\n"
            "Gm7 · C6 · Dbdim7 · Dm7\n"
            "Read as: Am7 D6 Ebdim7 Em7; the page's Key: Am\n"
            "Source: guitartuna.com: https://guitartuna.com/chords/spooky-dusty-springfield"
        )
        history = json.dumps(app.context.build(app.store.current_conversation(), None))
        assert "Gm7 C6" not in history and "Chords from the call" not in history
    finally:
        await stop(harness)


async def test_the_message_after_the_call_holds_every_result_in_call_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(
        tool("chords", **TRANSPOSE),
        text("Done."),
        tool("chords", action="notes", chords="Bm"),
        text("B, D and F sharp."),
    )
    harness = await chord_call(tmp_path, monkeypatch, gemma)
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "transpose Am F C G up 2", lambda: None)
        notes = await app.call_turn(ME, "and the notes of B minor", lambda: None)
        assert notes.code_text == "Bm: the notes B, D and F sharp."  # no message promised: no move in this turn
        app.call_ended()
        await app.call_chords(ME)
        await until(lambda: len(sent(harness.folder)) == 1)
        assert sent(harness.folder)[0] == f"Chords from the call\n{LINES}\n\nBm: B D F#\nRead as: Bm"
    finally:
        await stop(harness)


async def test_no_message_after_a_call_without_a_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(
        tool("chords", action="transpose", chords="Hm7b", semitones=2),
        text("I could not read Hm7b."),
        tool("chords", action="notes", chords="G7"),
        text("G, B, D and F."),
    )
    harness = await chord_call(tmp_path, monkeypatch, gemma)
    try:
        app = harness.app
        app.call_started(ME)
        failed = await app.call_turn(ME, "transpose Hm7b up 2", lambda: None)
        assert failed.code_text == 'Not worked out: "Hm7b" is not a chord.'
        await app.call_turn(ME, "the notes of G7?", lambda: None)
        app.call_ended()
        await app.call_chords(ME)
        await asyncio.sleep(0.3)
        assert sent(harness.folder) == []  # an error and notes only: no transpose ran
    finally:
        await stop(harness)


async def test_without_the_page_the_turns_sources_are_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemma = FakeGemma(tool("web_search", query="news about the song"), tool("chords", **TRANSPOSE), text("Done."))
    harness = await chord_call(tmp_path, monkeypatch, gemma)
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "Am F C G up 2", lambda: None)
        await until(lambda: len(sent(harness.folder)) == 1)
        app.call_ended()
        await app.call_chords(ME)
        await until(lambda: len(sent(harness.folder)) == 2)
        assert sent(harness.folder)[1].endswith(f"{LINES}\nSources:\n• news.example: https://news.example/spooky")
    finally:
        await stop(harness)


async def test_a_summary_line_with_another_chord_is_left_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemma = FakeGemma(
        tool("chords", **TRANSPOSE),
        text("Done."),
        text("Thanks."),
        text("We moved Am F C G up a whole tone. It is now Bm G D A#. Then we said goodbye."),
    )
    harness = await chord_call(tmp_path, monkeypatch, gemma)
    try:
        app = harness.app
        app.call_started(ME)
        await app.call_turn(ME, "transpose Am F C G up 2", lambda: None)
        await app.call_turn(ME, "thanks", lambda: None)
        app.call_ended()
        await app.call_chords(ME)
        await app.call_summary(ME, ["User: transpose", "Estia: Done."], 2)
        await until(lambda: len(sent(harness.folder)) == 2)
        summary = sent(harness.folder)[1]
        assert "We moved Am F C G up a whole tone." in summary and "Then we said goodbye." in summary
        assert "A#" not in summary
    finally:
        await stop(harness)


# ---- the docs (design 5): GUIDE.md's part and rows, README.md's bullet, AGENTS.md and its rule files ----

ROOT = Path(__file__).resolve().parents[1]
SAME = (  # the same fact in the same words everywhere
    "Chords transposed or moved for a capo, a chord's notes and the key of given chords, worked out on the Mac and "
    "given in a note from the code, not from the AI."
)


def test_the_guide_has_the_chords_part_and_its_rows() -> None:
    guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")
    part = guide[guide.index("### Transposing chords (extra, off by default)") : guide.index("## Languages")]
    assert guide.index("### Documents (extra, off by default)") < guide.index("### Transposing chords")
    assert SAME in part and "On texts and calls." in part
    for words in (
        "sections as found",
        "after the call one message from the code holds the exact chords and the page they came from",
        "B is read as B♭ only beside an H or a German name",
        "F♭, E♯, C♭ and B♯ are written E, F, B and C",
        "A step in semitones is never refused",
        "Of 50 progressions written by hand, 49 were named right.",
        "At most 64 chords and 1,000 characters a request.",
        "A web search keeps at most 1,200 characters of each page",
        "Nothing to install.",
        # GUIDE, 19 Oct (206d3d6): in her own user setup prints the line, before it setup adds it (setup.chords_extra)
        "`scripts/setup.sh --extras` lists it. In her own user her config is hers alone, so a yes prints the one line "
        "to add to it, `[chords]`, and the restart; before her own user, a yes adds the line itself.",
        # review, minor: the check's known gaps (design 3.7), in the part's own limits
        "after the code's work, its chords said again, in another order or with one left out, pass",
        "without it, her chords count by their root alone",
        "A list of chords is checked as a whole, and goes as a whole.",
        # eval of 18 Oct: a capo asked with the shapes' own key gave "Capo 0"
        "Fret 0 is said as no capo.",
        "the AI gets a question back, never an answer with a key picked by the code",
        # eval of 18 Oct: a refusal hidden by a later call with a changed chord; review I4: that chord was a guess
        "never guessed: a request that puts another chord in its place is refused",
        '("Hm7b" left out)',
    ):
        assert words in part, words
    assert (
        "| Transposing chords | `[chords]` | Nothing to install: `scripts/setup.sh --extras`, then add the line it prints, "
        "`[chords]`, to her config |" in guide
    )
    assert f'| Transposing chords | "Transpose Am F C G up 2" | {SAME} An extra, off by default. |' in guide
    for banned in ("exactly", "never in the model's head", "http://", "<"):  # the site's rules, and plain copy
        assert banned not in part, banned


def test_setup_assistant_has_the_chords_part() -> None:
    """Review, minor (disclosed by the builder): setup_assistant.md had no word on [chords]."""
    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    part = guide[guide.index("### Transposing chords (extra, off by default)") : guide.index("### Personality")]
    part = " ".join(part.split())  # its lines wrapped anywhere
    assert guide.index("### Documents (extra, off by default)") < guide.index("### Transposing chords")
    for words in (
        "Off without `[chords]`: no chords tool is offered, and her rules and requests are byte for byte as before.",
        "`scripts/setup.sh --extras` lists it",
        "/usr/bin/sudo /usr/local/estia/bin/estia config # add the line [chords]",
        "never chords, keys, song names or sources",
        "about 440 tokens",
        "showed no clear harm, but 40 tries cannot rule it out",
    ):
        assert words in part, words
    assert "`[chords]` (transposing chords) is an extra, off by default" in " ".join(guide.split())  # config's list


def test_the_readme_has_the_chords_bullet() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    bullet = (
        f"- **Transposing chords.** {SAME} An extra, off by default. Example: "
        '"Get the chords of Spooky and transpose them 4 semitones higher".'
    )
    assert bullet in readme
    assert readme.index("- **Documents.**") < readme.index(bullet)
    assert "- **Chords.** Chords, key and tempo from a chord site, with a Sources message." in readme  # stays
    assert len(readme.splitlines()) < 120  # the front page


def test_agents_names_the_modules_and_the_rule() -> None:
    agents = agent_docs()  # the chords' modules and rule are in docs/agents/web-maps-music-video.md (AGENTS.md diet)
    for words in ("`theory` ([chords]", "`chords` ([chords]", "Chords ([chords]", "tests/test_theory.py"):
        assert words in agents, words


def test_the_cost_in_every_request_is_measured_and_stated(tmp_path: Path) -> None:
    """Review I6: the design's estimate (265 tokens on texts, 225 on calls) was never measured. Measured here with web
    search and calls on (the owner's install), and stated in the agents' docs (the chords rule in
    docs/agents/web-maps-music-video.md, the A/B command in prompts-evals.md): a change to the tool or its rules
    shows here, and the call_goodbye gate (evals.gemma_suite --ab) decides whether the tool stays on calls."""
    setup = SETUPS["web_calls"]
    off, on = bridge(tmp_path / "off", **setup), bridge(tmp_path / "on", chords_enabled=True, **setup)
    try:
        tool = len(json.dumps(on.tools.schemas)) - len(json.dumps(off.tools.schemas))
        rules = len(on._rules("en")) - len(off._rules("en"))
        call_rules = len(prompts.without_capabilities(on._rules("en"))) - len(
            prompts.without_capabilities(off._rules("en"))
        )
    finally:
        off.store.close()
        on.store.close()
    assert (rules, call_rules, tool) == (616, 439, 1_127)
    agents = agent_docs()  # AGENTS.md and its rule files (the AGENTS.md diet)
    texts, calls = rules + tool, call_rules + tool
    assert f"{texts:,} characters on texts (about {round(texts / 4, -1):.0f} tokens)" in agents
    assert f"{calls:,} on calls (about {round(calls / 4, -1):.0f})" in agents
    assert "uv run python -m evals.gemma_suite --ab call_goodbye 100" in agents
    assert "missed hang-ups 5 without the tool and 8 with it (one-sided Fisher p = 0.27)" in agents
