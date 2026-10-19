"""The chord arithmetic ([chords], theory.py): every row of the chords rule book (17 Oct 2026) as a table test, named
by its rule (S1 ... G3), with the input and the output as the book writes them (accidentals in ASCII, as typed), then
the design's own rows and the hard cases. No third-party code, table or test: the rows are the book's and ours."""

import ast
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from local_assistant import theory
from local_assistant.theory import ChordError, notes


def _notes(chord: str) -> str:
    return " ".join(notes(chord).notes)


def _error(call: object) -> str:
    assert callable(call)
    with pytest.raises(ChordError) as caught:
        call()
    return str(caught.value)


# ---- notes: S1 to S6 and N1 to N4 (the audit's 21 note rows, then the book's other note rows) ----

NOTE_ROWS = [
    ("S1_C9", "C9", "C E G Bb D"),
    ("S2_CDelta7", "CΔ7", "C E G B"),
    ("S2_C7M", "C7M", "C E G B"),
    ("S2_Bhalfdim", "Bø", "B D F A"),
    ("S3_C-7", "C-7", "C Eb G Bb"),
    ("S3_C7-9", "C7-9", "C E G Bb Db"),
    ("S3_C-9_is_Cm9", "C-9", "C Eb G Bb D"),
    ("S4_Eb7b9", "Eb7(b9)", "Eb G Bb Db Fb (sounds as E)"),
    ("S4_C7sus4", "C7sus4", "C F G Bb"),
    ("S4_Cadd9", "Cadd9", "C E G D"),
    ("N1_Cdim7", "Cdim7", "C Eb Gb Bbb (sounds as A)"),
    ("N1_D#7", "D#7", "D# F## (sounds as G) A# C#"),
    ("N2_D/F#", "D/F#", "F# D A"),
    ("N3_C11", "C11", "C G Bb D F"),
    ("N3_Cm11", "Cm11", "C Eb G Bb D F"),
    ("N4_C13", "C13", "C E G Bb D A"),
    ("N4_Cm13", "Cm13", "C Eb G Bb D F A"),
    ("N4_Cm6/9", "Cm6/9", "C Eb G A D"),
    ("S2_Cdim_triad", "Cdim", "C Eb Gb"),
    ("S2_Cdeg_triad", "C°", "C Eb Gb"),
    ("N2_Sol7", "Sol7", "Sol Si Re Fa"),
    ("N1_C9", "C9", "C E G Bb D"),
]


@pytest.mark.parametrize(("chord", "expected"), [row[1:] for row in NOTE_ROWS], ids=[row[0] for row in NOTE_ROWS])
def test_book_note_rows(chord: str, expected: str) -> None:
    assert _notes(chord) == expected


def test_S3_C7plus_is_not_read_by_notes() -> None:
    assert _error(lambda: notes("C7+")) == 'ending not in the table: "C7+"'


def test_S6_alt_has_no_fixed_notes() -> None:
    assert _error(lambda: notes("C7alt")) == 'ending not in the table: "C7alt"'


def test_S1_a_double_flat_root_is_refused_with_the_plain_name() -> None:
    assert _error(lambda: notes("Bbb7")) == 'not a chord: "Bbb7" (write A7)'
    assert _error(lambda: notes("Ebb9")) == 'not a chord: "Ebb9" (write D9)'
    assert _error(lambda: notes("C/Bbb")) == 'not a chord: "C/Bbb" (write C/A)'  # the bass too


def test_S1_lower_case_roots_are_refused() -> None:
    assert _error(lambda: notes("am")) == 'not a chord: "am"'


def test_S2_remarks_after_the_notes() -> None:
    assert notes("Cdim").remark == "(many chord books play Cdim as Cdim7)"
    assert notes("C°").remark == "(many chord books play Cdim as Cdim7)"
    assert notes("C11").remark == "(3rd usually left out)"
    assert notes("Cm11").remark is None
    assert notes("C9").remark is None


SPELLINGS = [  # S2: every common spelling is read; the notes are the ending's
    ("minor", ["Cm", "Cmi", "Cmin", "C-"], "C Eb G"),
    ("dim", ["Cdim", "C°", "Cº", "Co"], "C Eb Gb"),
    ("aug", ["Caug", "C+"], "C E G#"),
    ("maj7", ["Cmaj7", "Cma7", "CM7", "CΔ", "CΔ7", "C^7", "C7M"], "C E G B"),
    ("half-diminished", ["Cø", "Cø7", "Cm7b5", "C-7b5", "Ch7"], "C Eb Gb Bb"),
    ("dim7", ["Cdim7", "C°7", "Co7", "Cº7"], "C Eb Gb Bbb (sounds as A)"),
    ("minor-major 7th", ["CmMaj7", "Cm(maj7)", "CmΔ7", "C-Δ7"], "C Eb G B"),
    ("sus alone is sus4", ["Csus", "Csus4"], "C F G"),
]


@pytest.mark.parametrize(("kind", "spellings", "expected"), SPELLINGS, ids=[row[0] for row in SPELLINGS])
def test_S2_every_common_spelling_is_read(kind: str, spellings: list[str], expected: str) -> None:
    for chord in spellings:
        assert _notes(chord) == expected, chord


@pytest.mark.parametrize("word", ["Ah", "Eh", "Emma", "Emo", "Ch", "Ch9", "Chm", "Cmh", "Csusm", "Cmaddo"])
def test_S2_a_word_is_no_chord(word: str) -> None:
    """Review I4: an ending of letters alone is one of S2's ("m", "mi", "min", "M", "ma", "maj", "dim", "aug",
    "sus", "o", and "alt"), and "h" is half-diminished only as "h7"; "Ah" moved as "Bh", "Emma" as "F#mma", and
    notes("Emma") gave E minor's notes."""
    with pytest.raises(theory.ChordError, match="not a chord"):
        theory.transpose(f"{word} Am F", 2)
    with pytest.raises(theory.ChordError, match="not a chord"):
        theory.notes(word)
    with pytest.raises(theory.ChordError, match="not a chord"):
        theory.key(f"Am {word} F C G")


@pytest.mark.parametrize(
    "word", ["Cm", "Cmi", "Cmin", "CM", "Cma", "Cmaj", "CMaj", "Cdim", "Caug", "Csus", "Co", "Calt"]
)
def test_S2_the_books_endings_of_letters_still_read(word: str) -> None:
    assert theory.transpose(word, 2).chords == "D" + word[1:]


# the ending table (design 3.2), every ending from C, written out by hand from the standard chord symbols
TABLE_FROM_C = {
    "C": "C E G",
    "Cm": "C Eb G",
    "Cdim": "C Eb Gb",
    "Caug": "C E G#",
    "Csus4": "C F G",
    "Csus2": "C D G",
    "C5": "C G",
    "C6": "C E G A",
    "Cm6": "C Eb G A",
    "C6/9": "C E G A D",
    "C69": "C E G A D",
    "Cm6/9": "C Eb G A D",
    "Cm69": "C Eb G A D",
    "C7": "C E G Bb",
    "Cmaj7": "C E G B",
    "Cm7": "C Eb G Bb",
    "CmMaj7": "C Eb G B",
    "Cdim7": "C Eb Gb Bbb (sounds as A)",
    "Cm7b5": "C Eb Gb Bb",
    "C7sus4": "C F G Bb",
    "C7sus": "C F G Bb",
    "Caug7": "C E G# Bb",
    "C7#5": "C E G# Bb",
    "C+7": "C E G# Bb",
    "C7b5": "C E Gb Bb",
    "C7-5": "C E Gb Bb",
    "Cadd9": "C E G D",
    "Cadd2": "C E G D",
    "Cmadd9": "C Eb G D",
    "C9": "C E G Bb D",
    "Cmaj9": "C E G B D",
    "Cm9": "C Eb G Bb D",
    "C7b9": "C E G Bb Db",
    "C7#9": "C E G Bb D#",
    "C7+9": "C E G Bb D#",
    "C11": "C G Bb D F",
    "Cm11": "C Eb G Bb D F",
    "C7#11": "C E G Bb F#",
    "C13": "C E G Bb D A",
    "Cmaj13": "C E G B D A",
    "Cm13": "C Eb G Bb D F A",
}


@pytest.mark.parametrize(("chord", "expected"), list(TABLE_FROM_C.items()))
def test_the_ending_table_from_C(chord: str, expected: str) -> None:
    assert _notes(chord) == expected


# interval spelling, written out (design 6): a minor third from F# is A, from Eb is Gb; the 7th of a dim7 a double flat
INTERVALS = [
    ("F#m", "F# A C#"),
    ("Ebm", "Eb Gb Bb"),
    ("Gm7", "G Bb D F"),
    ("Bbmaj7", "Bb D F A"),
    ("Dbmaj7", "Db F Ab C"),
    ("Bdim7", "B D F Ab"),
    ("F#dim7", "F# A C Eb"),
    ("Ebdim7", "Eb Gb Bbb (sounds as A) Dbb (sounds as C)"),
    ("Gb7", "Gb Bb Db Fb (sounds as E)"),
    ("Db13", "Db F Ab Cb (sounds as B) Eb Bb"),
    ("G#7", "G# B# (sounds as C) D# F#"),
    ("Cb", "Cb (sounds as B) Eb Gb"),
    ("E#m", "E# (sounds as F) G# B# (sounds as C)"),
    ("Fb", "Fb (sounds as E) Ab Cb (sounds as B)"),
    ("B#", "B# (sounds as C) D## (sounds as E) F## (sounds as G)"),
    ("Cø7", "C Eb Gb Bb"),
    ("C6/9", "C E G A D"),
]


@pytest.mark.parametrize(("chord", "expected"), INTERVALS)
def test_notes_are_spelled_by_interval(chord: str, expected: str) -> None:
    assert _notes(chord) == expected


def test_N2_the_bass_comes_first_and_is_not_repeated() -> None:
    assert _notes("C7/Bb") == "Bb C E G"
    assert _notes("Am/G") == "G A C E"
    assert _notes("C/E") == "E C G"


def test_N2_notes_in_the_chords_own_naming() -> None:
    assert _notes("Sol7") == "Sol Si Re Fa"
    assert _notes("LAm") == "LA DO MI"
    assert _notes("Fa#m") == "Fa# La Do#"
    assert _notes("Sib") == "Sib Re Fa"
    assert _notes("Ré7") == "Ré Fa# La Do"
    assert _notes("H7") == "H D# F# A"  # H is a letter: German B
    assert _notes("Fis7") == "Fis Ais Cis E"
    assert _notes("Es") == "Es G Bes"  # Dutch: no H, no German word
    assert " ".join(notes("Es", language="de").notes) == "Es G B"  # German: B is B flat
    assert notes("H7").read_as == "H7 (B7, German H)"
    assert notes("Sol7").read_as == "Sol7 (G7)"
    assert notes("C-7").read_as == "C-7 (Cm7)"
    assert notes("G7").read_as == "G7"


def test_accidentals_come_out_as_typed() -> None:
    assert _notes("D♯7") == "D♯ F𝄪 (sounds as G) A♯ C♯"
    assert _notes("C♭") == "C♭ (sounds as B) E♭ G♭"
    assert _notes("Cdim7") == "C Eb Gb Bbb (sounds as A)"


def test_every_table_ending_from_all_12_roots_keeps_its_pitch_classes() -> None:
    """Each ending's notes from every root: the C row's pitch classes moved by the root, each note one letter per
    interval number, and "(sounds as …)" after each double accidental and E#, B#, Cb, Fb (I7)."""
    letters = "CDEFGAB"
    natural = (0, 2, 4, 5, 7, 9, 11)

    def pitch(note: str) -> int:
        name = note.split(" ")[0]
        return (natural[letters.index(name[0])] + name.count("#") - name.count("b")) % 12

    for root in ("C", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B", "C#"):
        shift = pitch(root)
        for chord, from_c in TABLE_FROM_C.items():
            got = notes(root + chord[1:]).notes
            c_notes = _plain(from_c)
            assert [pitch(note) for note in got] == [(pitch(note) + shift) % 12 for note in c_notes], root + chord
            for note, c_note in zip(got, c_notes, strict=True):
                steps = (letters.index(note[0]) - letters.index(root[0])) % 7
                assert steps == letters.index(c_note[0]), root + chord
                name = note.split(" ")[0]
                odd = name.count("#") > 1 or name.count("b") > 1 or name in ("E#", "B#", "Cb", "Fb")
                assert ("(sounds as" in note) == odd, root + chord


def _plain(written: str) -> list[str]:
    """The notes of a written row without the "(sounds as …)" after them."""
    out: list[str] = []
    for word in written.split():
        if word.startswith("(") or word.endswith(")"):
            continue
        if word in ("sounds", "as"):
            continue
        out.append(word)
    return out


# ---- guards (G1 to G3) ----


def test_G1_too_many_chords_or_characters() -> None:
    assert _error(lambda: theory.read(" ".join(["C"] * 65))) == "too long: at most 64 chords and 1,000 characters"
    assert _error(lambda: theory.read("C " + "x" * 1_000)) == "too long: at most 64 chords and 1,000 characters"
    assert len(theory.read(" ".join(["C"] * 64)).chords) == 64  # 64 is fine


def test_G1_an_ending_has_at_most_12_characters() -> None:
    assert (
        _error(lambda: notes("Cmaj7(b9,#11,b13)"))
        == 'not a chord: "Cmaj7(b9,#11…" (an ending has at most 12 characters)'
    )


def test_G1_imports_only_the_standard_library() -> None:
    source = Path(theory.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


def _fast(call: object) -> None:
    started = time.perf_counter()
    _error(call)
    assert time.perf_counter() - started < 0.05


def test_G2_a_long_run_of_digits_is_never_a_number() -> None:
    _fast(lambda: notes("C/99999999999"))
    _fast(lambda: theory.read("Am C/99999999999 G"))
    assert _error(lambda: notes("C/99")) == 'not a chord: "C/99"'
    assert _error(lambda: theory.read("Am C/99")) == 'not a chord: "C/99"'


def test_G3_reading_time_grows_in_line_with_the_input() -> None:
    _fast(lambda: theory.read("(" * 1_000))
    _fast(lambda: theory.read("Cmaj7" * 200))
    _fast(lambda: theory.read(" ".join(["Cmaj7"] * 200)))
    _fast(lambda: notes("C" + "m" * 1_000))


def test_S5_polychords_and_digits_after_a_slash_are_refused() -> None:
    assert _error(lambda: notes("C/G7")) == 'not a chord: "C/G7"'
    assert _error(lambda: notes("C/9")) == 'not a chord: "C/9"'
    assert _notes("C6/9/E") == "E C G A D"  # 6/9 is no slash chord


def test_S6_beat_slashes_written_without_spaces_are_beat_marks() -> None:
    """Deferred in the fix pass: "C///" and "C/ G/" were refused as "not a chord". A slash followed only by slashes
    or nothing is a beat mark, never a bass note: kept as written, as the spaced grid marks are."""
    assert theory.transpose("| C/// | G/// |", 2).chords == "| D/// | A/// |"
    assert theory.transpose("C/ G/ Am/ F/", 2).chords == "D/ A/ Bm/ G/"
    assert theory.transpose("C /// G //", 2).chords == "D /// A //"  # a run of slashes standing alone
    assert theory.transpose("C/G/ Am//.", 2).chords == "D/A/ Bm//."  # a bass, then a beat; punctuation after
    moved = theory.transpose("C/// G/// Am/// F///", 2)
    assert (moved.given, moved.moved, moved.read_as) == (("C", "G", "Am", "F"), ("D", "A", "Bm", "G"), "C G Am F")
    assert moved.spoken == "Up 2, a whole tone: D A Bm G."  # beat marks are not said
    assert theory.likely("| C/// | G/// | Am/// | F/// |") == ("C",)
    assert _notes("C///") == "C E G"
    # as today: a slash before a note is the bass, digits after a slash are refused
    assert theory.transpose("C/G", 2).chords == "D/A"
    assert _error(lambda: theory.read("C/1000000")) == 'not a chord: "C/1000000"'
    _fast(lambda: theory.read("C/1000000///"))


def test_S5_a_lone_trailing_slash_is_never_a_bass_note() -> None:
    """Deferred in the fix pass: "C/" as one chord, as the shared parser reads a key, a capo's side or a chord in
    her words, was refused as "not a chord". A slash followed by nothing, or only by slashes, is a beat mark."""
    chord = theory.chord_of("C/")
    assert (chord.root, chord.ending, chord.bass, chord.reading) == (theory.Note(0, 0), "", None, "C/ (C)")
    assert theory.chord_of("Am7//").ending == "m7"
    assert theory.chord_of("C/G/").bass == theory.Note(4, 0)  # a bass, then a beat
    assert theory.parse_key("Am/").words == "A minor"
    assert theory.capo(shapes="G/", fret=3).summary == "Capo 3 with G shapes: sounds in B♭ major."
    assert theory.transpose("Am F C G", 2, from_key="Am/").from_key == "Am (A minor)"
    assert theory.transpose("(C/) (G/)", 2).chords == "(D/) (A/)"  # the beat kept inside the brackets
    # as today: a slash before a note is the bass, digits after a slash are refused
    assert theory.chord_of("C/G").bass == theory.Note(4, 0)
    assert _error(lambda: theory.chord_of("C/1000000")) == 'not a chord: "C/1000000"'
    _fast(lambda: theory.chord_of("C/1000000/"))


def test_a_token_that_starts_like_a_chord_but_does_not_parse_is_named() -> None:
    assert _error(lambda: notes("Hm7b")) == 'not a chord: "Hm7b"'
    assert _error(lambda: notes("Bridge")) == 'not a chord: "Bridge"'
    assert _error(lambda: notes("Cmm")) == 'not a chord: "Cmm"'


# ---- the key of given chords: F1 to F4, R1, R2 ----

# the audit's 29 key rows: the keys that score best (a tie lists each)
KEY_ROWS = [
    ("F1_C_G_Am_F", "C G Am F", ["C"]),
    ("F1_D_C_G", "D C G", ["D"]),
    ("F1_G_F_C", "G F C", ["G"]),
    ("F1_power_chords_F_minor", "F5 Bb5 Ab5 Db5", ["Fm"]),
    ("F2_Am_F_C_G", "Am F C G", ["Am"]),
    ("F2_tie_Fm_Db", "Fm Bbm Ab Db", ["Db", "Fm"]),
    ("F3_Bm_F#7", "Bm F#7 A E G D Em F#7", ["Bm"]),
    ("U2_tie_G_C", "G C Dm", ["C", "G"]),
    ("R1_blues_in_A", "A7 D7 A7 E7 D7 A7", ["A"]),
    ("F1_E7_A7_B7", "E7 A7 B7", ["E"]),
    ("F1_Am_Dm_E7_Am", "Am Dm E7 Am", ["Am"]),
    ("P3_Em_Am_B7_Em", "Em Am B7 Em", ["Em"]),
    ("P3_Am_G#dim7_Am", "Am G#°7 Am", ["Am"]),
    ("F1_bass_ignored", "C C/B C/Bb A7", ["C"]),
    ("P4_bass_line", "C C/B Am Am/G F", ["C"]),
    ("P2_G7_C7_D7", "G7 C7 D7", ["G"]),
    ("F4_Gb_Cb_Db", "Gb Cb Db", ["Gb"]),
    ("F4_F#_B_C#_D#m", "F# B C# D#m", ["F#"]),
    ("F4_C#m_A_E_B", "C#m A E B", ["C#m"]),
    ("F4_G#m_E_B_F#", "G#m E B F#", ["G#m"]),
    ("U4_C5_F5_G5", "C5 F5 G5", ["C", "Cm"]),
    ("U4_C5", "C5", ["C", "Cm"]),
    ("P5_G_F_Eb_Bb", "G F Eb Bb", ["G"]),
    ("P5_F_Db", "F Db", ["F"]),
    ("L1_Bb_F_C", "Bb F C", ["Bb"]),
    ("S3_Am7_-_D7", "Am7 - D7", ["Am"]),
    ("E2_C-7_F7", "C-7 F7", ["Cm"]),
    ("T3_Am7_D/F#", "Am7(#9,b13) D/F#", ["Am"]),
    ("S6_grid", "| C / / / | G / / / |", ["C"]),
]


@pytest.mark.parametrize(("chords", "expected"), [row[1:] for row in KEY_ROWS], ids=[row[0] for row in KEY_ROWS])
def test_book_key_rows(chords: str, expected: list[str]) -> None:
    assert sorted(theory.likely(chords)) == sorted(expected)


def test_F1_named_keys_and_the_ones_also_possible() -> None:
    assert theory.key("D C G").key == "D (D major)"
    assert theory.key("D C G").also == ("G (G major)",)
    assert theory.key("G F C").key == "G (G major)"
    assert theory.key("G F C").also == ("C (C major)",)
    assert theory.key("F5 Bb5 Ab5 Db5").key == "Fm (F minor)"


def test_F2_likely_its_relative_and_ties() -> None:
    found = theory.key("Am F C G")
    assert found.key == "Am (A minor)"
    assert found.also == ("C (C major)",)
    assert found.numerals == ("i", "VI", "III", "VII")
    assert found.summary == "Likely A minor: i VI III VII. C major is also possible: the same chords."
    tie = theory.key("Fm Bbm Ab Db")
    assert tie.key == "Fm (F minor) or Db (D♭ major)"
    assert tie.also == ()  # a tie leaves out the keys within 1 point
    assert tie.summary == "Likely F minor or D♭ major (a tie). In F minor: i iv III VI."
    too_few = theory.key("Am F")
    assert too_few.key is None
    assert too_few.summary == "Too few chords to tell the key: 3 or more name one."


def test_F3_and_F4_spelled_by_the_tonic_chord_as_written() -> None:
    assert theory.key("Bm F#7 A E G D Em F#7").key == "Bm (B minor)"
    assert theory.key("C#m A E B").summary.startswith("Likely C♯ minor")  # never "D♭m"
    sharp = theory.key("F# B C# D#m")
    assert sharp.key == "F# (F♯ major)"
    assert sharp.also == ("D#m (D♯ minor)",)  # its relative by letter
    assert theory.key("Gb Cb Db Gb").key == "Gb (G♭ major)"


NUMERAL_ROWS = [
    ("R1_in_G", "G Em C D7", ("I", "vi", "IV", "V7")),
    ("R1_Am_F_C_G", "Am F C G", ("i", "VI", "III", "VII")),
    ("R1_Am_Dm_E7_Am", "Am Dm E7 Am", ("i", "iv", "V7", "i")),
    ("R1_blues_in_A", "A7 D7 A7 E7", ("I7", "IV7", "I7", "V7")),
    ("R1_minor_sevenths", "Am7 Dm7 E7", ("i7", "iv7", "V7")),
    ("R2_outside_the_key", "G F C G", ("I", "outside the key", "IV", "I")),
]


@pytest.mark.parametrize(
    ("chords", "expected"), [row[1:] for row in NUMERAL_ROWS], ids=[row[0] for row in NUMERAL_ROWS]
)
def test_book_numeral_rows(chords: str, expected: tuple[str, ...]) -> None:
    assert theory.key(chords).numerals == expected


def test_R2_the_summary_lists_chords_outside_the_key() -> None:
    assert theory.key("G F C G").summary == "Likely G major: I, outside the key, IV, I."


def test_numerals_with_their_signs() -> None:
    assert theory.key("C Dm7 G7 Bm7b5 C").numerals == ("I", "ii7", "V7", "viiø7", "I")
    assert theory.key("Am G#dim Am").numerals == ("i", "vii°", "i")
    assert theory.key("Am G#°7 Am").numerals == ("i", "vii°7", "i")
    assert theory.key("C Fmaj7 G C").numerals == ("I", "IVmaj7", "V", "I")


def test_the_design_key_rows() -> None:
    assert theory.key("G F C G").key == "G (G major)"
    assert theory.key("Am E7 Am").summary == "Likely A minor: i V7 i."
    both = theory.key("Em G D C")
    assert both.key == "Em (E minor)"
    assert both.also == ("G (G major)",)
    alt = theory.key("Am C7alt G")  # C7alt counted as C7: never refused
    assert alt.key == "Am (A minor)"
    assert "C7alt (C7 for the key)" in alt.read_as
    assert _error(lambda: theory.key("Am C7+ G")) == 'ending not in the table: "C7+"'


def test_an_ending_outside_the_table_counts_by_root_and_third() -> None:
    assert theory.key("Am7(#9,b13) Dm9sus2 E7#5#9 Am").key == "Am (A minor)"
    assert theory.key("Cmaj7#11 F6 G13sus4 C").key == "C (C major)"


def test_the_key_of_names_and_do_re_mi() -> None:
    assert theory.key("LAm FA DO SOL").key == "LAm (A minor)"
    assert theory.key("Hm G D A").key == "Hm (B minor)"
    assert theory.key("Bes F Es Bes").key == "Bes (B♭ major)"


def test_three_chords_as_given_name_a_key() -> None:
    assert theory.key("Am F").key is None
    assert theory.key("Am E7 Am").key == "Am (A minor)"
    assert theory.key("Am F G").key == "Am (A minor)"


# ---- transposing: S, T, K, P, U, E, L (the audit's 63 rows: chords, semitones, to_key, from_key -> chords, key) ----

MOVE_ROWS: list[tuple[str, str, int | None, str | None, str | None, str, str]] = [
    ("S3_separator_dash", "Am7 - D7", 2, None, None, "Bm7 - E7", "Bm"),
    ("S6_label_nc_repeat", "[Chorus] N.C. Am F (x2)", 2, None, None, "[Chorus] N.C. Bm G (x2)", "Bm"),
    ("S5_slash_bass", "Am/G", 2, None, None, "Bm/A", "Bm"),
    ("S6_alt_moves", "C7alt", 1, None, None, "Db7alt", "Db"),
    ("T1_up_4", "Am F C G E7", 4, None, None, "C#m A E B G#7", "C#m"),
    ("T1_never_Abm", "F#m D A E", 2, None, None, "G#m E B F#", "G#m"),
    ("T2_F#m_up_4", "F#m", 4, None, None, "Bbm", "Bbm"),
    ("T3_ending_as_written", "Am7(#9,b13) D/F#", 2, None, None, "Bm7(#9,b13) E/G#", "Bm"),
    ("T3_down_2", "E B C#m A G#m", -2, None, None, "D A Bm G F#m", "D"),
    ("K1_Am_to_Em", "Am F C G E7", None, "Em", None, "Em C G D B7", "Em"),
    ("K1_closer_way", "A D E", None, "F", None, "F Bb C", "F"),
    ("K2_from_C_to_D", "Am F C G", None, "D", "C", "Bm G D A", "D"),
    ("K2_from_Am_to_Dm", "Am F C G", None, "Dm", "Am", "Dm Bb F C", "Dm"),
    ("K3_C#_written_Db", "C G Am F", None, "C#", "C", "Db Ab Bbm Gb", "Db"),
    ("K3_from_Am_to_Em", "C G Am F", None, "Em", "Am", "G D Em C", "Em"),
    ("P1_up_3", "G Em C D7", 3, None, None, "Bb Gm Eb F7", "Bb"),
    ("P1_Am_up_1", "Am", 1, None, None, "Bbm", "Bbm"),
    ("P2_fewer_respelled", "F Bb C", 1, None, None, "F# B C#", "F#"),
    ("P2_the_songs_side", "F", 1, None, None, "Gb", "Gb"),
    ("P2_D#m_with_VI", "Am F C G", 6, None, None, "D#m B F# C#", "D#m"),
    ("P2_Ebm_default", "Am Dm", 6, None, None, "Ebm Abm", "Ebm"),
    ("P3_V7_in_minor", "Am Dm E7 Am", None, "Em", None, "Em Am B7 Em", "Em"),
    ("P3_Am_E_up_1", "Am E", 1, None, None, "Bbm F", "Bbm"),
    ("P4_borrowed", "G F Eb Bb D7", 2, None, None, "A G F C E7", "A"),
    ("P4_passing_dim7", "C C#°7 Dm7 G7", 1, None, None, "Db D°7 Ebm7 Ab7", "Db"),
    ("P5_bass", "G/B", 1, None, None, "Ab/C", "Ab"),
    ("P5_Fb_Cb_respelled", "G F Eb Bb", 1, None, None, "Ab Gb E B", "Ab"),
    ("P5_F_Db", "F Db", 1, None, None, "F# D", "F#"),
    ("U2_from_G", "G C Dm", 6, None, "G", "Db Gb Abm", "Db"),
    ("U2_from_C", "G C Dm", 6, None, "C", "C# F# G#m", "F#"),
    ("U2_from_Fm", "Fm Bbm Ab Db", 1, None, "Fm", "F#m Bm A D", "F#m"),
    ("U2_from_Db", "Fm Bbm Ab Db", 1, None, "Db", "F#m Bm A D", "D"),
    ("U3_from_G_to_A", "G C Dm", None, "A", "G", "A D Em", "A"),
    ("U3_from_C_to_A", "G C Dm", None, "A", "C", "E A Bm", "A"),
    ("E1_up_2", "Am F C G", 2, None, None, "Bm G D A", "Bm"),
    ("E2_C-7", "C-7 F7", 2, None, None, "D-7 G7", "Dm"),
    ("L1_solfege", "LAm FA DO SOL", 2, None, None, "SIm SOL RE LA", "SIm"),
    ("L1_dutch_Bes", "Bes F C", 2, None, None, "C G D", "C"),
    ("C3_capo_6_heard", "E A B", 6, None, "E", "Bb Eb F", "Bb"),
    ("C4_new_shapes", "G Em C D", 2, None, None, "A F#m D E", "A"),
    ("S6_grid_marks", "| C / / / | G / / / |", 2, None, None, "| D / / / | A / / / |", "D"),
    ("T3_Gb_up_2", "Gb Cb Db", 2, None, None, "Ab Db Eb", "Ab"),
    ("F4_Gb_up_6", "Gb Cb Db", 6, None, None, "C F G", "C"),
    ("F4_F#_down_1", "F# B C# D#m", -1, None, None, "F Bb C Dm", "F"),
    ("P2_named_Gb_kept", "E A B", None, "Gb", "E", "Gb B Db", "Gb"),
    ("P2_named_F#_kept", "E A B", None, "F#", "E", "F# B C#", "F#"),
    ("P3_Em_up_1", "Em Am B7 Em", 1, None, None, "Fm Bbm C7 Fm", "Fm"),
    ("P3_Am_down_1", "Am Dm E7 Am", -1, None, None, "G#m C#m D#7 G#m", "G#m"),
    ("P3_Am_up_6", "Am Dm E7 Am", 6, None, None, "Ebm Abm Bb7 Ebm", "Ebm"),
    ("P3_dim7_lands_on_F##", "Am G#°7 Am", -1, None, None, "G#m G°7 G#m", "G#m"),
    ("R1_blues_up_1", "A7 D7 A7 E7 D7 A7", 1, None, None, "Bb7 Eb7 Bb7 F7 Eb7 Bb7", "Bb"),
    ("P2_G7_down_1", "G7 C7 D7", -1, None, None, "F#7 B7 C#7", "F#"),
    ("P4_bass_line_up_1", "C C/B Am Am/G F", 1, None, None, "Db Db/C Bbm Bbm/Ab Gb", "Db"),
    ("P4_bass_line_down_1", "C C/B C/Bb A7", -1, None, None, "B B/A# B/A G#7", "B"),
    ("P4_D/F#_up_1", "G D/F# Em", 1, None, None, "Ab Eb/G Fm", "Ab"),
    ("C4_from_G", "G Em C D", 2, None, "G", "A F#m D E", "A"),
    ("S3_C7plus_kept", "C7+", 2, None, None, "D7+", "D"),
    ("T3_back_to_the_tie", "Ab Db Eb", -2, None, None, "F# B C#", "F#"),
    ("T3_7_sharps_in", "A#m F# C# G#", 2, None, None, "Cm Ab Eb Bb", "Cm"),
    ("T3_7_sharps_back", "Cm Ab Eb Bb", -2, None, None, "Bbm Gb Db Ab", "Bbm"),
    ("C5_capo_2_to_Am", "Em C G D", 3, None, "Em", "Gm Eb Bb F", "Gm"),
    ("C1_capo_2_heard", "Em C G D", 2, None, "Em", "F#m D A E", "F#m"),
    ("C5_capo_3_heard", "G Em C D", 3, None, "G", "Bb Gm Eb F", "Bb"),
]


@pytest.mark.parametrize(
    ("chords", "semitones", "to_key", "from_key", "expected", "target"),
    [row[1:] for row in MOVE_ROWS],
    ids=[row[0] for row in MOVE_ROWS],
)
def test_book_transpose_rows(
    chords: str, semitones: int | None, to_key: str | None, from_key: str | None, expected: str, target: str
) -> None:
    moved = theory.transpose(chords, semitones, to_key, from_key)
    assert moved.chords == expected
    assert moved.to_key.startswith(target + " ("), moved.to_key


# U4: no chord with a 3rd, the major and minor of one tonic tied: both readings; when they differ, sharps up, flats down
U4_ROWS = [
    ("U4_C5_up_6", "C5", 6, "F#5"),
    ("U4_C5_down_6", "C5", -6, "F#5"),
    ("U4_C5_up_1", "C5", 1, "C#5"),
    ("U4_C5_down_4", "C5", -4, "Ab5"),
    ("U4_C5_up_8", "C5", 8, "G#5"),
    ("U4_C5_F5_G5_up_1", "C5 F5 G5", 1, "C#5 F#5 G#5"),
    ("U4_C5_F5_G5_down_1", "C5 F5 G5", -1, "B5 E5 F#5"),
]


@pytest.mark.parametrize(("chords", "semitones", "expected"), [r[1:] for r in U4_ROWS], ids=[r[0] for r in U4_ROWS])
def test_book_power_chord_rows(chords: str, semitones: int, expected: str) -> None:
    assert theory.transpose(chords, semitones).chords == expected


def test_U4_says_how_it_spelled_them() -> None:
    assert "key unclear (C major or minor): sharps going up" in theory.transpose("C5", 1).read_as
    assert "key unclear (C major or minor): flats going down" in theory.transpose("C5", -4).read_as
    assert "key unclear" not in theory.transpose("C5", 6).read_as  # both readings agree


def test_E1_the_step_and_both_keys_come_first() -> None:
    moved = theory.transpose("Am F C G", 2)
    assert moved.step == "Up 2 (a whole tone)"
    assert moved.from_key == "Am (A minor, likely; C major also possible)"
    assert moved.to_key == "Bm (B minor)"
    assert moved.read_as == "Am F C G"
    assert moved.capo is None


def test_T2_a_step_is_named_by_its_sound() -> None:
    names = {
        1: "a semitone",
        2: "a whole tone",
        3: "a minor third",
        4: "a major third",
        5: "a fourth",
        6: "a tritone",
        7: "a fifth",
        8: "a minor sixth",
        9: "a major sixth",
        10: "a minor seventh",
        11: "a major seventh",
    }
    for semitones, name in names.items():
        assert theory.transpose("C F G", semitones).step == f"Up {semitones} ({name})"
        assert theory.transpose("C F G", -semitones).step == f"Down {semitones} ({name})"
    moved = theory.transpose("F#m", 4)
    assert (moved.step, moved.from_key, moved.to_key) == (
        "Up 4 (a major third)",
        "F#m (F♯ minor, likely)",
        "Bbm (B♭ minor)",
    )


def test_T2_the_step_and_its_errors() -> None:
    for wrong in (0, 12, -12, 99):
        assert _error(lambda wrong=wrong: theory.transpose("Am F", wrong)) == "semitones from -11 to 11, not 0"
    assert _error(lambda: theory.transpose("Am F", 2, "Em")) == "semitones or to_key, not both"
    assert _error(lambda: theory.transpose("Am F")) == "transpose needs semitones or to_key"
    assert _error(lambda: theory.transpose("Am F", True)) == "semitones from -11 to 11, not 0"  # a yes is no number


def test_K1_the_closer_way_and_the_tritone_up() -> None:
    assert theory.transpose("Am F C G E7", to_key="Em", from_key="Am").step == "Down 5 (a fourth)"
    assert theory.transpose("A D E", to_key="F").step == "Down 4 (a major third)"
    moved = theory.transpose("C F G", to_key="Gb")  # a named key is kept: G♭, its C♭ written B
    assert (moved.chords, moved.step) == ("Gb B Db", "Up 6 (a tritone)")


def test_K2_the_mode_is_kept() -> None:
    picked = theory.transpose("Am F C G", to_key="D")  # no song key: the target's mode picks the relative reading
    assert picked.chords == "Bm G D A"
    assert "read as C major" in picked.read_as
    kept = theory.transpose("Am F C G", to_key="D", from_key="Am")  # the song's key of the other mode: kept minor
    assert kept.chords == "Dm Bb F C"
    assert kept.to_key == "Dm (D minor)"
    assert "the song is minor" in kept.read_as
    minor = theory.transpose("Am F C G", to_key="Dm")  # A minor or C major: the target's mode picks; never refused
    assert (minor.chords, minor.step) == ("Dm Bb F C", "Up 5 (a fourth)")
    assert minor.from_key == "Am (A minor, likely; C major also possible)"


def test_K3_seven_accidentals_become_the_key_that_sounds_the_same() -> None:
    moved = theory.transpose("C G Am F", to_key="C#")
    assert moved.chords == "Db Ab Bbm Gb"
    assert moved.to_key == "Db (D♭ major)"
    assert "C♯ written as D♭" in moved.read_as


def test_K3_a_given_key_whose_chord_is_missing_says_what_the_chords_look_like() -> None:
    moved = theory.transpose("G Em C D", 2, from_key="Bb")
    assert moved.chords == "A F#m D E"
    assert "the chords look like G major" in moved.read_as


def test_K3_the_pages_key_line_beats_the_estimate() -> None:
    page = "Key: Am. Chords: Am7 · D6 · Ebdim7 · Em7"
    moved = theory.transpose(page, to_key="Gm")
    assert moved.chords == "Key: Am. Chords: Gm7 · C6 · Dbdim7 · Dm7"
    assert (moved.step, moved.from_key, moved.to_key) == ("Down 2 (a whole tone)", "Am (A minor)", "Gm (G minor)")
    assert "the page's Key: Am" in moved.read_as
    estimated = theory.transpose("Am7 · D6 · Ebdim7 · Em7", to_key="Gm")  # without it the estimate says E minor
    assert estimated.chords == "Cm7 · F6 · Gbdim7 · Gm7"
    assert (estimated.step, estimated.from_key) == (
        "Up 3 (a minor third)",
        "Em (E minor, likely; A minor also possible)",
    )


def test_P2_P3_P5_respellings_are_said() -> None:
    assert "C♭ written B" in theory.transpose("E A B", to_key="Gb").read_as
    assert "F𝄪 written G" in theory.transpose("Am G#°7 Am", -1).read_as
    assert "F♭ written E, C♭ written B" in theory.transpose("G F Eb Bb", 1).read_as


def test_P5_E_sharp_and_B_sharp_are_written_F_and_C() -> None:
    sharp = theory.transpose("G C#°7 D", 4)
    assert sharp.chords == "B F°7 F#"
    assert "E♯ written F" in sharp.read_as
    b_sharp = theory.transpose("D A#°7 Bm", 2)
    assert b_sharp.chords == "E C°7 C#m"
    assert "B♯ written C" in b_sharp.read_as
    read = theory.transpose("Cb Fb Gb", 2)  # C♭, E♯, F♭ and B♯ are read
    assert read.chords == "Db Gb Ab"
    assert theory.transpose("E# A# B#", 1).chords == "F# B C#"


def test_U2_a_step_in_semitones_is_never_refused() -> None:
    unclear = theory.transpose("G C Dm", 6)
    assert unclear.chords == "Db Gb Abm"
    assert "key unclear (G or C major): spelled for D♭ major" in unclear.read_as
    alike = theory.transpose("Fm Bbm Ab Db", 1)
    assert alike.chords == "F#m Bm A D"
    assert "key unclear" not in alike.read_as


def test_U3_a_move_to_a_key_needs_the_songs_key() -> None:
    assert (
        _error(lambda: theory.transpose("G C Dm", to_key="A")) == "song key unclear: G or C major. Say the song's key."
    )
    assert theory.transpose("C G Am F", to_key="Em").chords == "G D Em C"  # the target's mode decides


def test_E2_read_as_names_the_words_passed_through() -> None:
    """Review I8: E2 names a token passed through; words from the chords argument went into code's note unsaid."""
    assert theory.transpose("Verse: Am F C G now", 2).read_as == "Am F C G; not chords: now"
    assert theory.key("Am F C G now").read_as == "Am F C G; not chords: now"
    assert theory.capo(shapes="G Em C D now", fret=2).read_as == "G Em C D; not chords: now"
    assert theory.capo("G Em C D now").read_as == "G Em C D; not chords: now"
    sheet = theory.transpose("Capo 2\nG Em C D now", 2).read_as
    assert sheet == "G Em C D; not chords: now; capo 2: shapes in G major, now A major"
    many = theory.transpose("Am one two three four five six seven one", 2).read_as
    assert many == "Am; not chords: one, two, three, four, five, six and 1 more"  # each once, at most six
    assert theory.transpose("Verse: Am F (x2)", 2).read_as == "Am F"  # labels and repeat marks are no words


def test_E2_read_as_names_each_reading_that_is_not_plain_letters() -> None:
    assert theory.transpose("C-7 F7", 2).read_as.startswith("C-7 (Cm7) F7")
    assert theory.transpose("LAm FA DO SOL", 2).to_key == "SIm (B minor)"
    assert "C7+ (the + kept as written)" in theory.transpose("C7+", 2).read_as
    assert _error(lambda: theory.transpose("Hm7b Am", 2)) == 'not a chord: "Hm7b"'


def test_the_design_transpose_rows() -> None:
    assert theory.transpose("Em C", to_key="Fm").chords == "Fm Db"
    assert theory.transpose("C", 6).chords == "F#"
    assert theory.transpose("Am H7 Em", 2).chords == "Hm C#7 F#m"
    intro = theory.transpose("Intro: Am7 · D6 | Ebdim7 - Em7 (x2)", to_key="Gm", from_key="Am")
    assert intro.chords == "Intro: Gm7 · C6 | Dbdim7 - Dm7 (x2)"
    assert _error(lambda: theory.transpose("Bridge Am F", 2)) == 'not a chord: "Bridge"'
    assert theory.transpose("C Ab G", to_key="G", from_key="C").chords == "G Eb D"  # P4: Ab in C, to G
    assert _error(lambda: theory.transpose("Am " * 333 + "Am", 2)) == "too long: at most 64 chords and 1,000 characters"


def test_the_owners_flows() -> None:
    by_key = theory.transpose("Verse: Am F C G E7", to_key="Em", from_key="Am")
    assert (by_key.chords, by_key.step) == ("Verse: Em C G D B7", "Down 5 (a fourth)")
    by_step = theory.transpose("Verse: Am F C G E7", 4)
    assert (by_step.chords, by_step.step) == ("Verse: C#m A E B G#7", "Up 4 (a major third)")
    sheet = "\n".join(
        [
            "[Intro]",
            "| Am / / / | F / / / |",
            "[Verse 1]",
            "Am F C G (x2)",
            "Chorus:",
            "F G Am - Am/G",
            "Bridge:",
            "N.C. Dm7 · E7 %",
        ]
    )
    moved = theory.transpose(sheet, 2)
    assert moved.chords == "\n".join(
        [
            "[Intro]",
            "| Bm / / / | G / / / |",
            "[Verse 1]",
            "Bm G D A (x2)",
            "Chorus:",
            "G A Bm - Bm/A",
            "Bridge:",
            "N.C. Em7 · F#7 %",
        ]
    )


def test_tuning_lines_pass_through_and_are_never_transposed() -> None:
    moved = theory.transpose("Tuning: Eb Ab Db Gb Bb Eb\nE A B", 2)
    assert moved.chords == "Tuning: Eb Ab Db Gb Bb Eb\nF# B C#"
    assert theory.transpose("Drop D\nD G A", 2).chords == "Drop D\nE A B"


def test_accidentals_come_out_as_typed_in_a_move() -> None:
    assert theory.transpose("A♭ D♭ E♭", 2).chords == "B♭ E♭ F"
    assert theory.transpose("A♭ D♭ C", 1).chords == "A D C♯"


ROOTS = ("C", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B", "C#")


def test_every_root_ending_and_step_moves_by_its_sound_and_comes_back() -> None:
    """Every root x every table ending x every step up and down (I7): the sound moves by the step, no double accidental
    or E#, B#, Cb, Fb comes out, and up then down gives the input back except T3's cases: a respelling said, an input
    in a key of 7 accidentals, a way back that lands on F# or Gb, D#m or Ebm."""
    letters = "CDEFGAB"
    natural = (0, 2, 4, 5, 7, 9, 11)

    def pitch(name: str) -> int:
        return (natural[letters.index(name[0])] + name.count("#") - name.count("b")) % 12

    endings = [chord[1:] for chord in TABLE_FROM_C]
    for root in ROOTS:
        for ending in endings:
            for semitones in [*range(1, 12), *range(-11, 0)]:
                chord = root + ending
                there = theory.transpose(chord, semitones)
                moved_root = there.chords[: 2 if there.chords[1:2] in ("#", "b") else 1]
                assert pitch(moved_root) == (pitch(root) + semitones) % 12, (chord, semitones)
                assert there.chords[len(moved_root) :] == ending, (chord, semitones)
                assert moved_root not in ("E#", "B#", "Cb", "Fb") and "##" not in there.chords, (chord, semitones)
                back = theory.transpose(there.chords, -semitones)
                if back.chords == chord:
                    continue
                # a lone chord is its own key, its 3rd the mode (U1): a diminished one's is minor
                minor = (ending.startswith("m") and not ending.startswith("maj")) or ending.startswith("dim")
                signatures = [theory.Note(letters.index(root[0]), root.count("#") - root.count("b")).fifths]
                fifths = [value - 3 for value in signatures] if minor else signatures
                if ending in ("5", "sus4", "sus2", "7sus4", "7sus", "sus", "11"):
                    fifths = [signatures[0], signatures[0] - 3]
                seven = any(abs(value) >= 7 for value in fifths)
                tie = back.chords[: 2 if back.chords[1:2] in ("#", "b") else 1] in ("F#", "Gb", "D#", "Eb")
                said = "written" in there.read_as or "written" in back.read_as
                assert seven or tie or said, (chord, semitones, there.chords, back.chords)


# ---- the capo: C1 to C5 ----

# the audit's 17 capo rows: (the key heard, the shape played, the fret)
CAPO_ROWS = [
    ("C1_G_shapes_fret_4", "B", "G", 4),
    ("C1_E_shape_fret_3", "G", "E", 3),
    ("C2_Bb_A", "Bb", "A", 1),
    ("C2_Bb_G", "Bb", "G", 3),
    ("C2_Bb_E", "Bb", "E", 6),
    ("C2_Bb_D_high", "Bb", "D", 8),
    ("C2_Bb_C_high", "Bb", "C", 10),
    ("C2_Bm_Am", "Bm", "Am", 2),
    ("C2_Bm_Em", "Bm", "Em", 7),
    ("C2_Bm_Dm_high", "Bm", "Dm", 9),
    ("C3_Bb_with_G_shapes", "Bb", "G", 3),
    ("C2_F#_E", "F#", "E", 2),
    ("C2_F#_D", "F#", "D", 4),
    ("C2_F#_C", "F#", "C", 6),
    ("C2_F#_A_high", "F#", "A", 9),
    ("C2_F#_G_high", "F#", "G", 11),
    ("C2_F#m_Em", "F#m", "Em", 2),
]


@pytest.mark.parametrize(("sounds", "shapes", "fret"), [r[1:] for r in CAPO_ROWS], ids=[r[0] for r in CAPO_ROWS])
def test_book_capo_rows(sounds: str, shapes: str, fret: int) -> None:
    assert theory.capo(sounds=sounds, shapes=shapes).frets == ((fret, shapes, fret >= 8),)
    assert theory.capo(shapes=shapes, fret=fret).sounds == theory.parse_key(sounds).words


def test_C1_what_sounds_is_the_shape_plus_the_fret() -> None:
    assert theory.capo(shapes="G", fret=4).sounds == "B major"
    assert theory.capo(shapes="E", fret=3).sounds == "G major"
    assert theory.capo(shapes="G", fret=4).summary == "Capo 4 with G shapes: sounds in B major."


def test_C2_each_shape_familys_fret_low_ones_first() -> None:
    assert theory.capo("Bb").frets == (
        (1, "A", False),
        (3, "G", False),
        (6, "E", False),
        (8, "D", True),
        (10, "C", True),
    )
    assert theory.capo("Bm").frets == ((2, "Am", False), (7, "Em", False), (9, "Dm", True))
    assert theory.capo("F#").frets == (
        (2, "E", False),
        (4, "D", False),
        (6, "C", False),
        (9, "A", True),
        (11, "G", True),
    )
    assert theory.capo("G").frets[0] == (0, "G", False)  # no capo first
    assert theory.capo("Bb").summary == (
        "For B♭ major: A shapes at capo 1, G shapes at capo 3, E shapes at capo 6; high up: D shapes at capo 8, "
        "C shapes at capo 10."
    )
    assert theory.capo("G").summary.startswith("For G major: G shapes with no capo, E shapes at capo 3")


def test_C2_for_chords_also_the_shapes_at_the_first_option() -> None:
    found = theory.capo("Ab Fm Db Eb")
    assert found.frets[0] == (1, "G", False)
    assert found.played == "G Em C D"
    assert found.sounds == "Ab Fm Db Eb (A♭ major)"


def test_C3_both_sides_the_shapes_with_the_fret_and_the_key_heard() -> None:
    heard = theory.capo(shapes="E A B", fret=6)
    assert heard.sounds == "Bb Eb F (B♭ major)"
    assert heard.played == "E A B"
    assert heard.summary == "Capo 6 with E A B: sounds Bb Eb F (B♭ major)."
    shapes = theory.capo(sounds="Bb Gm Eb F", fret=3)
    assert shapes.played == "G Em C D"
    assert shapes.summary == "For Bb Gm Eb F (B♭ major) at capo 3: G Em C D (G shapes)."
    assert theory.capo(sounds="Bb", fret=3).played == "G"


def test_C2_fret_0_is_no_capo_in_every_summary() -> None:
    """C2: 0 is no capo; the eval of 18 Oct showed "Capo 0 with G shapes" as an answer."""
    assert theory.capo("G", shapes="G").summary == "G shapes with no capo: sounds in G major."
    assert theory.capo(shapes="G", fret=0).summary == "G shapes with no capo: sounds in G major."
    assert theory.capo(shapes="G Em C D", fret=0).summary == "G Em C D with no capo: sounds G Em C D (G major)."
    assert theory.capo("Bb", fret=0).summary == "For B♭ major with no capo: Bb shapes."


@pytest.mark.parametrize(
    ("written", "words"),
    [
        ("La menor", "A minor"),
        ("la menor", "A minor"),
        ("Re menor", "D minor"),
        ("Si bemol mayor", "B♭ major"),
        ("Fis klein", "F♯ minor"),
        ("fis klein", "F♯ minor"),
        ("ля минор", "A minor"),
        ("B Dur", "B♭ major"),
    ],
)
def test_C1_a_capo_reads_a_key_in_words_in_every_language(written: str, words: str) -> None:
    """Review C4: a key with a space in any language's words is a key, never chords ("La menor" was A major)."""
    assert theory.capo(written).summary.startswith(f"For {words}: ")
    assert theory.capo(sounds=written, shapes="G").summary.endswith(f"sounds in {words}.")
    assert theory.capo(shapes=written, fret=2).read_as == f"{written} ({words})"


def test_C4_a_capo_line_means_the_sheet_shows_shapes() -> None:
    moved = theory.transpose("Capo 3\nG Em C D", 2)
    assert moved.chords == "Capo 3\nA F#m D E"
    assert moved.capo == "the same shapes at capo 5"
    assert moved.from_key == "Bb (B♭ major, likely, heard with capo 3)"
    assert moved.to_key == "C (C major)"
    assert "capo 3: shapes in G major, now A major" in moved.read_as
    assert theory.transpose("Capo 3\nG Em C D", 5).capo is None  # the capo at 8 would be high: shapes only
    assert theory.transpose("Capo 2\nG Em C D", -2).capo == "the same shapes with no capo"  # never "at capo 0"
    assert theory.capo(shapes="Capo 3\nG Em C D").sounds == "Bb Gm Eb F (B♭ major)"


def test_C4_a_capo_lines_shapes_in_brackets_name_the_shapes_key() -> None:
    """Deferred in the fix pass: "Capo 3 (G shapes)" was refused ("shapes)" is not a chord). The shapes in brackets
    name the shapes' key, so no estimate and no "likely"; a move writes the new shapes in the brackets."""
    moved = theory.transpose("Capo 3 (G shapes)\nG Em C D", 2)
    assert moved.chords == "Capo 3 (A shapes)\nA F#m D E"
    assert moved.capo == "the same shapes at capo 5"
    assert moved.from_key == "Bb (B♭ major, heard with capo 3)"
    assert "the capo line names G shapes" in moved.read_as
    assert "capo 3: shapes in G major, now A major" in moved.read_as
    assert moved.spoken == "Up 2, a whole tone: Capo 3, A shapes. A F#m D E."
    assert theory.transpose("Capo 3 (G shapes) G Em C D", 2).chords == "Capo 3 (A shapes) A F#m D E"  # one line
    to_key = theory.transpose("Capo 2 (Em shapes)\nG C D Em", to_key="Gm")  # the key heard is F♯ minor
    assert (to_key.chords, to_key.step) == ("Capo 2 (Fm shapes)\nAb Db Eb Fm", "Up 1 (a semitone)")
    # the bracket wins over the estimate (G major): E minor shapes, F♯ minor heard
    assert theory.capo(shapes="Capo 2 (Em shapes)\nG C D Em").sounds == "A D E F#m (F♯ minor)"
    assert theory.capo(shapes="Capo 2\nG C D Em").sounds == "A D E F#m (A major)"
    assert theory.capo(shapes="Capo 3 (G shape)\nG Em C D").summary.startswith("Capo 3 with G Em C D: sounds Bb")
    assert _error(lambda: theory.read("Capo 2 (open shapes)\nG C D")) == 'not a key: "open"'


def test_C4_only_a_tuning_that_lowers_every_string_moves_what_sounds() -> None:
    assert theory.capo(shapes="Half step down\nE", fret=0).sounds == "Eb (E♭ major)"
    assert theory.capo(shapes="E", fret=0).sounds == "E major"
    assert theory.capo(shapes="Tuning: Eb Ab Db Gb Bb Eb\nE A B", fret=0).sounds == "Eb Ab Bb (E♭ major)"
    assert theory.capo(shapes="D standard\nG C D", fret=2).sounds == "G C D (G major)"
    assert theory.capo(shapes="Drop D\nD G A", fret=0).sounds == "D G A (D major)"
    assert theory.capo(shapes="DADGAD\nD G A", fret=2).sounds == "E A B (E major)"


def test_C4_a_capo_line_with_on_is_a_capo_line() -> None:
    """Review, minor: "Capo on 2" was refused ("Capo" is not a chord); it is the fret as "Capo 2" is."""
    moved = theory.transpose("Capo on 2\nG Em C D", 2)
    assert (moved.chords, moved.capo) == ("Capo on 2\nA F#m D E", "the same shapes at capo 4")
    assert theory.capo(shapes="Capo on fret 2\nG Em C D").sounds == "A F#m D E (A major)"


def test_C4_a_tuning_line_without_a_capo_line_is_named() -> None:
    """Review, minor: without a "Capo N" line transpose moves the chords as written and keeps the tuning line, now
    said in "Read as:" (it was passed over without a word)."""
    moved = theory.transpose("Half step down\nG Em C D", 2)
    assert moved.chords == "Half step down\nA F#m D E"
    assert moved.read_as.endswith("; tuning line kept as written: Half step down")


def test_C5_a_key_is_matched_to_both_sides_of_a_capo_sheet() -> None:
    shapes_key = theory.capo(shapes="Capo 3\nKey: G\nG Em C D")
    assert shapes_key.sounds == "Bb Gm Eb F (B♭ major)"
    assert "the page's Key: G is the shapes' key" in shapes_key.read_as
    heard_key = theory.capo(shapes="Capo 3\nKey: Bb\nG Em C D")
    assert heard_key.sounds == "Bb Gm Eb F (B♭ major)"
    assert "the page's Key: Bb is the key heard" in heard_key.read_as
    neither = theory.capo(shapes="Capo 3\nKey: E\nG Em C D")
    assert "the chords look like G major" in neither.read_as
    to_heard = theory.transpose("Capo 2\nEm C G D", to_key="Am")  # a key to move to is the key heard
    assert to_heard.chords == "Capo 2\nGm Eb Bb F"
    assert to_heard.capo == "the same shapes at capo 5"
    assert (to_heard.step, to_heard.from_key) == ("Up 3 (a minor third)", "F#m (F♯ minor, likely, heard with capo 2)")
    given_heard = theory.transpose("Capo 3\nG Em C D", 2, from_key="Bb")  # the user's key: the key heard here
    assert given_heard.chords == "Capo 3\nA F#m D E"
    assert "the given key Bb is the key heard" in given_heard.read_as


def test_capo_errors() -> None:
    needs = "capo needs what sounds, or the shapes and the fret"
    assert _error(lambda: theory.capo()) == needs
    assert _error(lambda: theory.capo(fret=3)) == needs
    assert _error(lambda: theory.capo(shapes="G")) == needs
    assert _error(lambda: theory.capo("Bb", 3, "G")) == "capo takes two of: what sounds, the shapes, the fret"
    for fret in (-1, 12, 13, 99_999):
        assert _error(lambda fret=fret: theory.capo(shapes="G", fret=fret)) == "fret from 0 to 11"
    assert _error(lambda: theory.capo(shapes="Capo 13\nG C D")) == "fret from 0 to 11"
    assert _error(lambda: theory.capo("Bbb")) == 'not a key: "Bbb"'


def test_every_key_and_shape_family_adds_up() -> None:
    """Shape key + fret = sounding key, for every key and shape family (C1, C2)."""
    for pc in range(12):
        for minor in (False, True):
            name = theory.plain(pc, sharp=False)
            sounds = theory.KeyName(name, minor).symbol
            for fret, shape, high in theory.capo(sounds).frets:
                assert 0 <= fret <= 11 and high == (fret >= 8)
                back = theory.parse_key(theory.capo(shapes=shape, fret=fret).sounds)
                assert (back.tonic.pc, back.minor) == (pc, minor), (sounds, shape, fret)


# ---- names and key words in 11 languages: L1 to L5 ----

KEY_WORDS = [  # L4's table, each row's examples, plus English
    ("L4_de_B-Dur", "B-Dur", "B♭ major"),
    ("L4_de_h-Moll", "h-Moll", "B minor"),
    ("L4_de_Es-Dur", "Es-Dur", "E♭ major"),
    ("L4_de_fis-Moll", "fis-Moll", "F♯ minor"),
    ("L4_nl_Bes_groot", "Bes groot", "B♭ major"),
    ("L4_nl_fis_klein", "fis klein", "F♯ minor"),
    ("L4_nl_B_groot", "B groot", "B major"),
    ("L4_nl_a_mineur", "a mineur", "A minor"),
    ("L4_pl_B-dur", "B-dur", "B♭ major"),
    ("L4_pl_cis-moll", "cis-moll", "C♯ minor"),
    ("L4_ru_si_flat_major", "си-бемоль мажор", "B♭ major"),
    ("L4_ru_la_minor", "ля минор", "A minor"),
    ("L4_ru_fa_sharp_minor", "фа-диез минор", "F♯ minor"),
    ("L4_fr_si_bemol_majeur", "si bémol majeur", "B♭ major"),
    ("L4_fr_re_mineur", "ré mineur", "D minor"),
    ("L4_es_la_menor", "la menor", "A minor"),
    ("L4_es_fa_sostenido_menor", "fa sostenido menor", "F♯ minor"),
    ("L4_it_si_bemolle_maggiore", "si bemolle maggiore", "B♭ major"),
    ("L4_it_la_minore", "la minore", "A minor"),
    ("L4_pt_la_menor", "lá menor", "A minor"),
    ("L4_pt_si_bemol_maior", "si bemol maior", "B♭ major"),
    ("L4_ja_he_ro_chouchou", "変ロ長調", "B♭ major"),
    ("L4_ja_ei_he_tanchou", "嬰ヘ短調", "F♯ minor"),
    ("L4_ja_i_tanchou", "イ短調", "A minor"),
    ("L4_ko_naerim_na_jangjo", "내림나장조", "B♭ major"),
    ("L4_ko_ollim_ba_danjo", "올림바단조", "F♯ minor"),
    ("L4_ko_ga_danjo", "가단조", "A minor"),
    ("L4_en_B_flat_major", "B flat major", "B♭ major"),
    ("L4_en_F_sharp_minor", "F sharp minor", "F♯ minor"),
    ("L4_en_B_hyphen_flat_minor", "B-flat minor", "B♭ minor"),
    ("L4_en_symbol_Bbm", "Bbm", "B♭ minor"),
    ("L4_solfege_symbol", "SIm", "B minor"),
    ("L4_es_bare_Sol", "Sol", "G major"),
]


@pytest.mark.parametrize(("written", "words"), [row[1:] for row in KEY_WORDS], ids=[row[0] for row in KEY_WORDS])
def test_L4_key_words_from_a_fixed_table(written: str, words: str) -> None:
    assert theory.parse_key(written).words == words


MESSAGE_KEYS = [  # L4: read in the user's own message, with its role
    ("in h-Moll", [("B minor", "to")]),
    ("Transponiere das von B-Dur nach h-Moll", [("B♭ major", "from"), ("B minor", "to")]),
    ("Welcher Bund für B-Dur mit G-Griffen?", [("B♭ major", "to")]),
    ("Pon los acordes en la menor", [("A minor", "to")]),
    ("transpose it from A minor to E minor", [("A minor", "from"), ("E minor", "to")]),
    ("変ロ長調からイ短調に", [("B♭ major", "from"), ("A minor", "to")]),
    ("올림바단조로 바꿔줘", [("F♯ minor", "to")]),
    ("Transpose Am F C G up 2", []),
    ("La casa è grande", []),
    ("Move it to a minor key, like Em", []),  # "a minor" is the article, never a key
    ("make it a minor change, to E minor", [("E minor", "to")]),
    ("transpose it to B-flat minor", [("B♭ minor", "to")]),
    ("transpose it from c minor to e minor", [("C minor", "from"), ("E minor", "to")]),  # only "a" is the article
    ("go from a minor to e minor", [("E minor", "to")]),
    # review C2: do-re-mi words before a noun are everyday Spanish, Italian and Portuguese, never a key
    ("No tengo la menor idea.", []),
    ("La mayor parte del tiempo", []),
    ("Mi mayor consejo: descansa.", []),
    ("Sei la minore delle sorelle", []),
    ("O filho do maior empresário", []),
    ("Pásalo a la menor, por favor", [("A minor", "to")]),
    ("De la menor a mi menor", [("A minor", "from"), ("E minor", "to")]),
    ("Pon Am F C G en re mayor y luego en sol mayor", [("D major", "to"), ("G major", "to")]),
    ("Suonala in la minore per favore", [("A minor", "to")]),
    # review I5: "with" after the key dropped it in French, Italian, Spanish and Portuguese ("... avec des formes de Sol")
    ("Quel capo pour jouer en si bémol majeur avec des formes de Sol ?", [("B♭ major", "to")]),
    ("Che capotasto per suonare in si bemolle maggiore con le forme di Sol?", [("B♭ major", "to")]),
    ("¿Qué cejilla para sonar en si bemol mayor con formas de Sol?", [("B♭ major", "to")]),
    ("Que capotraste para soar em si bemol maior com formas de Sol?", [("B♭ major", "to")]),
]


@pytest.mark.parametrize(("message", "expected"), MESSAGE_KEYS)
def test_L4_keys_in_the_users_message(message: str, expected: list[tuple[str, str]]) -> None:
    assert [(found.key.words, found.role) for found in theory.keys_in(message)] == expected


def test_L4_a_key_word_moves_and_names_in_german() -> None:
    moved = theory.transpose("Am F C G", to_key="h-Moll", from_key="Am")
    assert moved.chords == "Hm G D A"
    assert moved.to_key == "Hm (B minor)"
    found = theory.capo(sounds="B-Dur", shapes="G")
    assert found.summary == "Capo 3 with G shapes: sounds in B♭ major."
    assert "B-Dur (B♭ major)" in found.read_as


def test_L2_a_result_in_german_names_says_so() -> None:
    """Review, minor: a German key word or an H sets German names for the result ("to B-moll": "Bm Gb Db Ab", the
    Bm being B♭ minor); "Read as:" now says so whenever the result writes a B or an H."""
    said = "German names: B is B♭, H is B"
    assert theory.transpose("Am F C G", to_key="B-moll").read_as.endswith(f"; {said}")
    assert theory.transpose("Am F C G", to_key="cis-moll").read_as.endswith(f"; {said}")  # C#m A E H
    assert said in theory.transpose("Hm G D A", 1).read_as  # Cm Ab Eb B
    assert said in theory.transpose("Hm G D A", 3).read_as  # Dm B F C: the B is B♭
    assert said not in theory.transpose("Hm G D A", 5).read_as  # Em C G D: no B or H in the result
    assert said not in theory.transpose("Am F C G", 2).read_as  # English names


def test_L2_B_and_H() -> None:
    reading = theory.transpose("B Es F H", 2).read_as
    assert reading.startswith("B (B♭, German B) Es (E♭) F H (B, German H)")
    assert [chord.root.words for chord in theory.read("B Es F H").chords] == ["B♭", "E♭", "F", "B"]
    assert [chord.root.words for chord in theory.read("B Fis Cis Gis").chords] == ["B", "F♯", "C♯", "G♯"]  # Dutch
    assert theory.transpose("B F C", 2, language="nl").read_as.startswith("B (English B)")
    assert theory.transpose("B F C", 2).read_as.startswith("B F C")  # an English conversation: nothing to say
    assert [chord.root.words for chord in theory.read("B F C", language="de").chords] == ["B♭", "F", "C"]
    assert [chord.root.words for chord in theory.read("B F C", message="in h-Moll").chords] == ["B♭", "F", "C"]


def test_L1_names_go_back_the_way_they_came() -> None:
    assert theory.transpose("LAm FA DO SOL", 2).chords == "SIm SOL RE LA"
    assert theory.transpose("Bes F C", 2).chords == "C G D"
    assert theory.transpose("Fis Cis Gis Dis", 1).chords == "G D A E"
    assert theory.transpose("Fis Cis Gis", -1).chords == "F C G"
    assert theory.transpose("Es As B", 2, language="de").chords == "F B C"
    assert theory.transpose("Sib Fa Do", 2).chords == "Do Sol Re"
    assert theory.transpose("Ré La Sol", 2).chords == "Mi Si La"
    assert theory.transpose("Dó Fá Sol", 2).chords == "Ré Sol Lá"


def test_L3_do_re_mi_in_title_case_or_capitals_never_lower_case() -> None:
    assert theory.notes("Ré7").read_as == "Ré7 (D7)"
    assert _error(lambda: theory.read("do re mi")) == 'not a chord: "do"'
    assert [chord.text for chord in theory.read("DO RE MI").chords] == ["DO", "RE", "MI"]


def test_L5_polish_lower_case_minors_are_refused() -> None:
    assert _error(lambda: theory.read("a e C G")) == 'not a chord: "a"'


# ---- the standard tables, written by hand from the tables themselves (design 6) ----

MAJOR_SCALES = {
    "C": "C D E F G A B",
    "G": "G A B C D E F#",
    "D": "D E F# G A B C#",
    "A": "A B C# D E F# G#",
    "E": "E F# G# A B C# D#",
    "B": "B C# D# E F# G# A#",
    "F#": "F# G# A# B C# D# E#",
    "C#": "C# D# E# F# G# A# B#",
    "F": "F G A Bb C D E",
    "Bb": "Bb C D Eb F G A",
    "Eb": "Eb F G Ab Bb C D",
    "Ab": "Ab Bb C Db Eb F G",
    "Db": "Db Eb F Gb Ab Bb C",
    "Gb": "Gb Ab Bb Cb Db Eb F",
    "Cb": "Cb Db Eb Fb Gb Ab Bb",
}
MINOR_SCALES = {  # the natural minor (P3)
    "Am": "A B C D E F G",
    "Em": "E F# G A B C D",
    "Bm": "B C# D E F# G A",
    "F#m": "F# G# A B C# D E",
    "C#m": "C# D# E F# G# A B",
    "G#m": "G# A# B C# D# E F#",
    "D#m": "D# E# F# G# A# B C#",
    "A#m": "A# B# C# D# E# F# G#",
    "Dm": "D E F G A Bb C",
    "Gm": "G A Bb C D Eb F",
    "Cm": "C D Eb F G Ab Bb",
    "Fm": "F G Ab Bb C Db Eb",
    "Bbm": "Bb C Db Eb F Gb Ab",
    "Ebm": "Eb F Gb Ab Bb Cb Db",
    "Abm": "Ab Bb Cb Db Eb Fb Gb",
}
SIGNATURES = {  # sharps +, flats -
    **dict(zip(["C", "G", "D", "A", "E", "B", "F#", "C#"], range(8), strict=True)),
    **dict(zip(["F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"], range(-1, -8, -1), strict=True)),
    **dict(zip(["Am", "Em", "Bm", "F#m", "C#m", "G#m", "D#m", "A#m"], range(8), strict=True)),
    **dict(zip(["Dm", "Gm", "Cm", "Fm", "Bbm", "Ebm", "Abm"], range(-1, -8, -1), strict=True)),
}
RELATIVES = dict(zip(MAJOR_SCALES, MINOR_SCALES, strict=True))


def _ascii(notes: tuple[theory.Note, ...]) -> str:
    return " ".join(theory.LETTERS[n.letter] + ("#" * n.alter if n.alter > 0 else "b" * -n.alter) for n in notes)


@pytest.mark.parametrize(("name", "scale"), [*MAJOR_SCALES.items(), *MINOR_SCALES.items()])
def test_the_30_keys_their_signatures_and_scales(name: str, scale: str) -> None:
    key = theory.parse_key(name)
    assert _ascii(key.scale) == scale
    assert key.accidentals == SIGNATURES[name]


@pytest.mark.parametrize(("major", "minor"), RELATIVES.items())
def test_the_15_relative_pairs(major: str, minor: str) -> None:
    assert theory.parse_key(major).relative.symbol == minor
    assert theory.parse_key(minor).relative.symbol == major


CIRCLE_UP = {  # up a fifth: the next key clockwise, keys that sound the same spelled by P1 and P2
    "C": "G", "G": "D", "D": "A", "A": "E", "E": "B", "B": "F#", "F#": "Db", "C#": "Ab", "F": "C", "Bb": "F",
    "Eb": "Bb", "Ab": "Eb", "Db": "Ab", "Gb": "Db", "Cb": "Gb",
    "Am": "Em", "Em": "Bm", "Bm": "F#m", "F#m": "C#m", "C#m": "G#m", "G#m": "D#m", "D#m": "Bbm", "A#m": "Fm",
    "Dm": "Am", "Gm": "Dm", "Cm": "Gm", "Fm": "Cm", "Bbm": "Fm", "Ebm": "Bbm", "Abm": "Ebm",
}  # fmt: skip
CIRCLE_DOWN = {
    "C": "F", "F": "Bb", "Bb": "Eb", "Eb": "Ab", "Ab": "Db", "Db": "Gb", "Gb": "B", "Cb": "E", "G": "C", "D": "G",
    "A": "D", "E": "A", "B": "E", "F#": "B", "C#": "F#",
    "Am": "Dm", "Dm": "Gm", "Gm": "Cm", "Cm": "Fm", "Fm": "Bbm", "Bbm": "Ebm", "Ebm": "G#m", "Abm": "C#m",
    "Em": "Am", "Bm": "Em", "F#m": "Bm", "C#m": "F#m", "G#m": "C#m", "D#m": "G#m", "A#m": "D#m",
}  # fmt: skip


def test_the_circle_of_fifths() -> None:
    for start, then in CIRCLE_UP.items():
        assert theory.transpose(start, 7).chords == then, start
    for start, then in CIRCLE_DOWN.items():
        assert theory.transpose(start, -7).chords == then, start


def test_the_seven_triads_and_their_numerals_in_each_of_the_30_keys() -> None:
    for name in MAJOR_SCALES:
        notes = _ascii(theory.parse_key(name).scale).split()
        chords = [notes[0], notes[1] + "m", notes[2] + "m", notes[3], notes[4], notes[5] + "m", notes[6] + "dim"]
        found = theory.key(" ".join([*chords, notes[0]]))
        assert found.key is not None and found.key.startswith(name + " ("), name
        assert found.numerals == ("I", "ii", "iii", "IV", "V", "vi", "vii°", "I"), name
        blues = theory.key(f"{notes[0]}7 {notes[3]}7 {notes[0]}7 {notes[4]}7")  # I7 and IV7 (R1)
        assert blues.numerals == ("I7", "IV7", "I7", "V7"), name
    for name in MINOR_SCALES:
        notes = _ascii(theory.parse_key(name).scale).split()
        chords = [notes[0] + "m", notes[1] + "dim", notes[2], notes[3] + "m", notes[4] + "m", notes[5], notes[6]]
        found = theory.key(" ".join([*chords, notes[0] + "m"]))
        assert found.key is not None and found.key.startswith(name + " ("), name
        assert found.numerals == ("i", "ii°", "III", "iv", "v", "VI", "VII", "i"), name
        five = theory.Note(theory.LETTERS.index(notes[4][0]), notes[4].count("#") - notes[4].count("b"))
        leading = five.moved(2, 4)  # V and vii°: the raised 7th, a major third above the 5th
        v = theory.LETTERS[five.letter] + ("#" * five.alter if five.alter > 0 else "b" * -five.alter)
        vii = theory.LETTERS[leading.letter] + ("#" * leading.alter if leading.alter > 0 else "b" * -leading.alter)
        tonic = notes[0] + "m"
        assert theory.key(f"{tonic} {v} {tonic}").numerals == ("i", "V", "i"), name
        assert theory.key(f"{tonic} {v}7 {tonic}").numerals == ("i", "V7", "i"), name
        if abs(leading.alter) < 2:  # in D#m, G#m and A#m it is a double sharp: no root has one (S1)
            assert theory.key(f"{tonic} {vii}dim {tonic}").numerals == ("i", "vii°", "i"), name


INTERVALS_FROM = {  # every interval of the ending table, written out
    "C": "C D Eb E F Gb G G# A Bbb Bb B Db D D# F F# A",
    "G": "G A Bb B C Db D D# E Fb F F# Ab A A# C C# E",
    "F#": "F# G# A A# B C C# C## D# Eb E E# G G# G## B B# D#",
    "Bb": "Bb C Db D Eb Fb F F# G Abb Ab A Cb C C# Eb E G",
    "Eb": "Eb F Gb G Ab Bbb Bb B C Dbb Db D Fb F F# Ab A C",
    "B": "B C# D D# E F F# F## G# Ab A A# C C# C## E E# G#",
    "Db": "Db Eb Fb F Gb Abb Ab A Bb Cbb Cb C Ebb Eb E Gb G Bb",
}
ORDER = ("1", "2", "b3", "3", "4", "b5", "5", "#5", "6", "bb7", "b7", "7", "b9", "9", "#9", "11", "#11", "13")


@pytest.mark.parametrize(("root", "written"), INTERVALS_FROM.items())
def test_interval_spelling_from_seven_roots(root: str, written: str) -> None:
    start = theory.parse_key(root).tonic
    assert _ascii(tuple(start.moved(*theory.INTERVALS[name]) for name in ORDER)) == written


# the key scorer on 50 progressions written by hand, each with the key its songs are known in (not the 30 cases the
# weights were tuned on): GUIDE.md states the result
KEY_SET = [
    ("C G Am F", "C"), ("G D Em C", "G"), ("D A Bm G", "D"), ("A E F#m D", "A"), ("E B C#m A", "E"),
    ("Em C G D", "Em"), ("C Am F G", "C"), ("G Em C D", "G"), ("D Bm G A", "D"), ("F Dm Bb C", "F"),
    ("Am G F E", "Am"), ("Em D C B7", "Em"), ("Dm C Bb A7", "Dm"), ("C F G C", "C"), ("G C D G", "G"),
    ("A D E A", "A"), ("E A B7 E", "E"), ("C Dm G C", "C"), ("Dm7 G7 Cmaj7", "C"), ("Em7 A7 Dmaj7", "D"),
    ("Am Dm E7 Am", "Am"), ("Em Am B7 Em", "Em"), ("Dm Gm A7 Dm", "Dm"), ("Gm Cm D7 Gm", "Gm"),
    ("Bm Em F#7 Bm", "Bm"), ("E7 A7 E7 B7 A7 E7", "E"), ("G7 C7 G7 D7 C7 G7", "G"), ("C F C G F C", "C"),
    ("D G D A G D", "D"), ("A D A E D A", "A"), ("D A Bm F#m G D G A", "D"), ("C G Am Em F C F G", "C"),
    ("Am C D F Am C E", "Am"), ("D C G", "D"), ("E D A E", "E"), ("Cm Ab Eb Bb", "Cm"), ("Fm Db Ab Eb", "Fm"),
    ("Bb F Gm Eb", "Bb"), ("Eb Bb Cm Ab", "Eb"), ("Ab Eb Fm Db", "Ab"), ("F C Dm Bb", "F"), ("C Em F G", "C"),
    ("G Bm C D", "G"), ("D F#m G A", "D"), ("Am F G Am", "Am"), ("E C#m A B", "E"), ("C C7 F Fm C", "C"),
    ("G B7 Em C", "G"), ("Dm Bb F C", "Dm"), ("A F#m D E", "A"),
]  # fmt: skip
KEY_SET_RIGHT = 49  # measured 17 Oct: "G B7 Em C" ties G with E minor (B7 is V of E minor)


def test_the_key_scorer_on_50_progressions_written_by_hand() -> None:
    assert len(KEY_SET) == 50
    right = [chords for chords, page in KEY_SET if theory.likely(chords) == (page,)]
    assert len(right) == KEY_SET_RIGHT, sorted({chords for chords, _ in KEY_SET} - set(right))


ROUND_TRIPS = [  # every naming system round-trips (I7): (text, language)
    ("LAm FA DO SOL", "en"),
    ("Lam Fa Do Sol", "es"),
    ("Dó Fá Sol Lám", "pt"),
    ("Ré La Sol", "fr"),
    ("Mi Si Do#m La", "it"),
    ("Fis Cis Gis Dis", "nl"),
    ("As Es Bes F", "nl"),
    ("Am H7 Em", "en"),
    ("Hm Fis7 G D", "de"),
]


@pytest.mark.parametrize(("text", "language"), ROUND_TRIPS)
def test_every_naming_system_round_trips(text: str, language: str) -> None:
    """Up then down gives the input back, the names as they came in, except T3's cases (a respelling said, a key of
    7 accidentals, a way back that lands on F# or Gb, D#m or Ebm)."""
    named = any(name in text for name in ("is", "es", "As ", "Es "))
    for semitones in [*range(1, 12), *range(-11, 0)]:
        there = theory.transpose(text, semitones, language=language)
        if named:  # German and Dutch names stay names, never mixed with # and b
            assert "#" not in there.chords and "b" not in there.chords.replace("Bes", ""), (text, there.chords)
        back = theory.transpose(there.chords, -semitones, language=language)
        if back.chords == text:
            continue
        # naturals alone carry no names, and a German B (B flat) no German mark: from such a text the way back is
        # read plainly (a known limit)
        names = ("is", "es", "As", "Es")
        lost_names = any(name in text for name in names) and not any(name in there.chords for name in names)
        lost_h = "H" in text and "H" not in there.chords and language != "de"
        if lost_names or lost_h:
            continue
        said = "written" in there.read_as + back.read_as
        tie = any(key in back.to_key + there.to_key for key in ("F♯", "G♭", "D♯", "E♭", "C♯", "C♭", "A♯", "A♭"))
        assert said or tie, (text, semitones, there.chords, back.chords)


# ---- what a call says (E3, design 3.7): labels said, separators and grid marks not, "(x2)" twice, plain letters ----

SPOKEN_MOVES = [
    (("Am F C G", 2), {}, "Up 2, a whole tone: Bm G D A."),
    (("Verse: Am F C G E7", None, "Em", "Am"), {}, "Down 5, a fourth: Verse: Em C G D B7."),
    (("[Chorus] N.C. Am F (x2)", 2), {}, "Up 2, a whole tone: Chorus: no chord Bm G twice."),
    (("| C / / / | G / / / |", 2), {}, "Up 2, a whole tone: D A."),
    (("LAm FA DO SOL", 2), {}, "Up 2, a whole tone: Bm G D A."),
    (("Intro: Am7 · D6\nVerse: Am F x3", 2), {}, "Up 2, a whole tone: Intro: Bm7 E6. Verse: Bm G three times."),
    (("B F C", 2), {"language": "de"}, "Up 2, a whole tone: C G D."),
    (("Am H7 Em", 2), {}, "Up 2, a whole tone: Bm C#7 F#m."),
    (("Capo 3\nG Em C D", 2), {}, "Up 2, a whole tone: Capo 3. A F#m D E."),
    (("Key: Am. Chords: Am7 · D6", None, "Gm"), {}, "Down 2, a whole tone: Chords: Gm7 C6."),
]


@pytest.mark.parametrize(("call", "options", "expected"), SPOKEN_MOVES)
def test_E3_a_move_as_said_on_a_call(call: tuple[Any, ...], options: dict[str, str], expected: str) -> None:
    assert theory.transpose(*call, language=options.get("language", "en")).spoken == expected


def test_E3_notes_key_and_capo_as_said_on_a_call() -> None:
    assert theory.notes("G7").spoken == "G7: the notes G, B, D and F."
    assert theory.notes("Cdim7").spoken == "Cdim7: the notes C, E flat, G flat and B double flat, which sounds as A."
    assert theory.notes("C11").spoken == "C11: the notes C, G, B flat, D and F, the 3rd usually left out."
    assert theory.notes("H7").spoken == "B7: the notes B, D sharp, F sharp and A."
    assert theory.notes("D/F#").spoken == "D/F#: the notes F sharp, D and A."
    assert theory.key("Am F C G").spoken == "Likely A minor. C major is also possible."  # the key only, no numerals
    assert theory.key("Fm Bbm Ab Db").spoken == "Likely F minor or D flat major."
    assert theory.key("Am F").spoken == "Too few chords to tell the key."
    assert theory.capo(sounds="B-Dur", shapes="G").spoken == "Capo 3 with G shapes: sounds in B flat major."
    assert theory.capo("Bb").spoken == (
        "For B flat major: A shapes at capo 1, G shapes at capo 3, E shapes at capo 6; high up: D shapes at capo 8, "
        "C shapes at capo 10."
    )
