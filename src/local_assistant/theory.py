"""Chord arithmetic on the Mac ([chords]): transpose, capo, a chord's notes and the key of given chords.

Four pure functions with a fixed input and output (`transpose`, `capo`, `notes`, `key`): text as written in, one frozen
result out. No state, no network, files or programs; standard library only. A chord, key or field that cannot be used
raises `ChordError` (its code and the token named), never a nearest match. The rule numbers (S1, P2, F1 ...) are the
chords rule book's (17 Oct 2026), the authority for every musical rule; the module is ours, built from the standard
tables (key signatures, the circle of fifths, scale degrees, interval spelling and the chord symbols).

Credit (rules read, nothing copied): OMT (Open Music Theory), Feezell (learnmusictheory.net), Harte (ISMIR 2005),
MusicXML 4.0, Dorico, iReal Pro, ChordPro (its transpose and grid rules), tonal (spelling by interval), Temperley,
Noland and Sandler, de Clercq and Temperley, and Richards.

Guards (G1 to G3): at most 64 chords, 1,000 characters and 12 characters per ending, the limit named in the error; no
long run of digits is ever turned into a number (digits after a slash are refused, 6/9 aside); the reading is a
hand-written scanner over whitespace-split tokens, and its few patterns have no repetition inside a repeated group, so
its time grows in line with the input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise

LETTERS = "CDEFGAB"
_NATURAL = (0, 2, 4, 5, 7, 9, 11)  # C to B
_FIFTHS = (0, 2, 4, -1, 1, 3, 5)  # each letter's place on the line of fifths (C = 0, F = -1, B = 5)
_ODD = frozenset({(3, -1), (2, 1), (0, -1), (6, 1)})  # F♭, E♯, C♭, B♯
MAX_CHORDS = 64
MAX_CHARS = 1_000
MAX_ENDING = 12
SHOWN = 12  # an error shows at most this many characters of the token
TOO_LONG = "too long: at most 64 chords and 1,000 characters"


class ChordError(ValueError):
    """Why a chord, key or field cannot be used, in plain words (the token named); `code` for the logs."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def shown(token: str) -> str:
    """A token as an error quotes it: at most SHOWN characters, then "…"."""
    return token if len(token) <= SHOWN else token[:SHOWN] + "…"


def _not_a_chord(token: str, why: str = "") -> ChordError:
    return ChordError("not_a_chord", f'not a chord: "{shown(token)}"' + (f" ({why})" if why else ""))


@dataclass(frozen=True)
class Note:
    """A spelled note: its letter (0 to 6 for C to B) and its sharps (+) or flats (-)."""

    letter: int
    alter: int = 0

    @property
    def pc(self) -> int:
        """Its sound, 0 to 11 (C = 0)."""
        return (_NATURAL[self.letter] + self.alter) % 12

    @property
    def fifths(self) -> int:
        """Its place on the line of fifths: a major key's signature (+ sharps, - flats)."""
        return _FIFTHS[self.letter] + 7 * self.alter

    @property
    def odd(self) -> bool:
        """A double accidental, or F♭, E♯, C♭, B♯ (P5: never a root or bass in a result; N1: kept in notes)."""
        return abs(self.alter) >= 2 or (self.letter, self.alter) in _ODD

    def moved(self, steps: int, semitones: int) -> Note:
        """The note `steps` letters and `semitones` half steps on (an interval), spelled by the letter."""
        letter = (self.letter + steps) % 7
        alter = (self.pc + semitones - _NATURAL[letter]) % 12
        return Note(letter, alter - 12 if alter > 6 else alter)

    @property
    def words(self) -> str:
        """In English words with signs: "B♭", "F♯", "B𝄫"."""
        return LETTERS[self.letter] + _signs(self.alter, unicode=True)


def plain(pc: int, sharp: bool) -> Note:
    """A sound spelled plainly: the natural note, else one sharp or one flat (`sharp`)."""
    for letter, natural in enumerate(_NATURAL):
        if natural == pc % 12:
            return Note(letter)
    for letter, natural in enumerate(_NATURAL):
        if (natural + (1 if sharp else -1)) % 12 == pc % 12:
            return Note(letter, 1 if sharp else -1)
    raise AssertionError(pc)  # every sound is one of them


def _signs(alter: int, *, unicode: bool) -> str:
    if alter == 0:
        return ""
    if unicode:
        single, double = ("♯", "𝄪") if alter > 0 else ("♭", "𝄫")
        return double * (abs(alter) // 2) + single * (abs(alter) % 2)
    return ("#" if alter > 0 else "b") * abs(alter)


# ---- naming systems (L1 to L3) ----

_SOLFEGE = ("Do", "Re", "Mi", "Fa", "Sol", "La", "Si")  # C to B
_ACCENTED = {"Dó": 0, "Ré": 1, "Fá": 3, "Lá": 5}  # Portuguese and French accents (Ré, Dó, Fá, Lá)
_ACCENT_OF = {letter: name for name, letter in _ACCENTED.items()}


@dataclass(frozen=True)
class Naming:
    """How a chord's notes are written: `system` letters (A to G), names (German and Dutch: Fis, Es, Bes) or solfege
    (do re mi, in title case or `caps`); `german`: B is B♭ and H is B (L2); `accents`: the solfège letters the song
    writes with an accent (Ré); `unicode`: ♯ and ♭ typed rather than # and b."""

    system: str = "letters"
    german: bool = False
    caps: bool = False
    accents: frozenset[int] = frozenset()
    unicode: bool = False

    def write(self, note: Note) -> str:
        """A note in this naming."""
        if self.system == "solfege":
            name = _ACCENT_OF[note.letter] if note.letter in self.accents else _SOLFEGE[note.letter]
            return (name.upper() if self.caps else name) + _signs(note.alter, unicode=self.unicode)
        if note.letter == 6 and (self.german or self.system == "names"):
            return _b_name(note.alter, german=self.german)
        letter = LETTERS[note.letter]
        if self.system == "names":
            if note.alter > 0:
                return letter + "is" * note.alter
            if note.alter < 0:
                if letter in "AE":
                    return letter + "s" + "es" * (-note.alter - 1)
                return letter + "es" * -note.alter
            return letter
        return letter + _signs(note.alter, unicode=self.unicode)

    @property
    def plain(self) -> bool:
        """Plain letters: A to G with # or b, B as B (no German B or H)."""
        return self.system == "letters" and not self.german


def _b_name(alter: int, *, german: bool) -> str:
    """B's names: German H, B (B♭), Heses, His; Dutch B, Bes, Beses, Bis."""
    if german:
        if alter == -1:
            return "B"
        return "H" + ("is" * alter if alter > 0 else "es" * -alter)
    return "B" + ("is" * alter if alter > 0 else "es" * -alter)


LETTERS_NAMING = Naming()


@dataclass(frozen=True)
class _Root:
    """A root (or bass) read at a token's start: its note, how many characters it took, its naming, and what needs
    the song to settle: a plain letter B (B♭ in a German song), an H, "Do" (D° in a song of letters), a double
    accidental (refused)."""

    note: Note
    length: int
    system: str
    caps: bool = False
    unicode: bool = False
    accent: bool = False
    is_b: bool = False
    is_h: bool = False
    double: bool = False


_ACCIDENTAL = {"#": 1, "♯": 1, "b": -1, "♭": -1}


def _roots(text: str) -> list[_Root]:
    """Every reading of a root at the start of `text`: letters (with H), German and Dutch names, do re mi."""
    found: list[_Root] = []
    first = text[:1]
    if first and first in "ABCDEFGH":
        letter = 6 if first == "H" else LETTERS.index(first)
        alter, size, unicode = 0, 1, False
        signs = text[1:3]
        if signs[:1] in _ACCIDENTAL:
            alter, size, unicode = _ACCIDENTAL[signs[0]], 2, signs[0] in "♯♭"
            double = signs[1:2] in _ACCIDENTAL
            if double:
                alter += _ACCIDENTAL[signs[1]]
                size = 3
            found.append(_Root(Note(letter, alter), size, "letters", unicode=unicode, double=double, is_h=first == "H"))
        else:
            found.append(_Root(Note(letter), 1, "letters", is_b=first == "B", is_h=first == "H"))
        named = _german_name(text)
        if named is not None:
            found.append(named)
    for name, letter in (*((s, i) for i, s in enumerate(_SOLFEGE)), *_ACCENTED.items()):
        for written, caps in ((name, False), (name.upper(), True)):
            if text.startswith(written):
                rest = text[len(written) :]
                alter, size, unicode, double = 0, len(written), False, False
                if rest[:1] in _ACCIDENTAL:
                    alter, size, unicode = _ACCIDENTAL[rest[0]], size + 1, rest[0] in "♯♭"
                    if rest[1:2] in _ACCIDENTAL:
                        alter, size, double = alter + _ACCIDENTAL[rest[1]], size + 1, True
                accent = name in _ACCENTED
                found.append(_Root(Note(letter, alter), size, "solfege", caps, unicode, accent, double=double))
    return found


def _german_name(text: str) -> _Root | None:
    """Cis, Fis, His, Es, As, Des, Bes, Eis (German and Dutch names, L2); doubles (Fisis, Eses) flagged."""
    first = text[0]
    letter = 6 if first == "H" else LETTERS.index(first)
    rest = text[1:]
    for suffix, alter in (("isis", 2), ("is", 1), ("eses", -2), ("es", -1)):
        if rest.startswith(suffix):
            return _Root(Note(letter, alter), 1 + len(suffix), "names", is_h=first == "H", double=abs(alter) == 2)
    if first in "AE":
        for suffix, alter in (("ses", -2), ("s", -1)):
            if rest.startswith(suffix):
                return _Root(Note(letter, alter), 1 + len(suffix), "names", double=alter == -2)
    return None


# ---- endings (S2 to S4, design 3.2) ----

_WORD_PIECES = ("omit", "Maj", "maj", "add", "alt", "aug", "dim", "min", "sus", "no", "ma", "mi", "m", "M", "h", "o")
_SIGN_PIECES = frozenset("°ºøΔ^+-")
_NUMBERS = frozenset({"2", "3", "4", "5", "6", "7", "9", "11", "13", "69"})
# S2's endings of letters alone; any other ("Ah", "Emma", "Emo", "Csusm") is a word, not a chord (review I4)
_LETTER_ENDINGS = frozenset({"m", "mi", "min", "M", "ma", "maj", "Maj", "dim", "aug", "sus", "alt", "o"})


def valid_ending(ending: str) -> bool:
    """Whether an ending is made of chord pieces (transpose and capo keep it as written, T3): words (m, maj, dim, sus,
    add, alt, no ...), the signs ° º ø Δ ^ + -, numbers of a chord (2 to 13), a sharp or flat only before a number
    (an alteration, sign first: b9, #11), parentheses and the commas inside them. "6/9" is one piece. At most
    MAX_ENDING characters, so trying the word pieces both ways ("madd9": m and add) stays small. An ending of letters
    alone is one of S2's (m, mi, min, M, ma, maj, dim, aug, sus, o, and alt), and h is half-diminished only as h7."""
    if ending.isascii() and ending.isalpha() and ending not in _LETTER_ENDINGS:  # ø, º and Δ are signs here
        return False
    return len(ending) <= MAX_ENDING and _pieces_from(ending, 0, 0, "")


def _pieces_from(ending: str, i: int, depth: int, last_word: str) -> bool:
    size = len(ending)
    if i >= size:
        return depth == 0
    char = ending[i]
    if ending.startswith("6/9", i):
        return _pieces_from(ending, i + 3, depth, "")
    if char.isdigit():
        j = i
        while j < size and ending[j].isdigit() and j - i < 3:
            j += 1
        return ending[i:j] in _NUMBERS and _pieces_from(ending, j, depth, "")
    if char in "#♯b♭":
        return i + 1 < size and ending[i + 1].isdigit() and _pieces_from(ending, i + 1, depth, "")
    if char in _SIGN_PIECES:
        return _pieces_from(ending, i + 1, depth, "")
    if char == "(":
        return not depth and _pieces_from(ending, i + 1, 1, "")
    if char == ")":
        return bool(depth) and ending[i - 1] != "(" and _pieces_from(ending, i + 1, 0, "")
    if char == ",":
        return bool(depth) and _pieces_from(ending, i + 1, depth, "")
    return any(
        _pieces_from(ending, i + len(word), depth, word)
        for word in _WORD_PIECES
        if word != last_word  # "Cmm", "Ahh": no chord repeats a word
        and ending.startswith(word, i)
        and (word != "h" or ending.startswith("7", i + 1))  # S2: h7 only
    )


# the ending table (design 3.2), written by us from the standard chord symbols: the intervals from the root
TABLE: dict[str, tuple[str, ...]] = {
    "": ("1", "3", "5"),
    "m": ("1", "b3", "5"),
    "dim": ("1", "b3", "b5"),
    "aug": ("1", "3", "#5"),
    "sus4": ("1", "4", "5"),
    "sus2": ("1", "2", "5"),
    "5": ("1", "5"),
    "6": ("1", "3", "5", "6"),
    "m6": ("1", "b3", "5", "6"),
    "6/9": ("1", "3", "5", "6", "9"),
    "m6/9": ("1", "b3", "5", "6", "9"),
    "7": ("1", "3", "5", "b7"),
    "maj7": ("1", "3", "5", "7"),
    "m7": ("1", "b3", "5", "b7"),
    "mMaj7": ("1", "b3", "5", "7"),
    "dim7": ("1", "b3", "b5", "bb7"),
    "m7b5": ("1", "b3", "b5", "b7"),
    "7sus4": ("1", "4", "5", "b7"),
    "aug7": ("1", "3", "#5", "b7"),
    "7b5": ("1", "3", "b5", "b7"),
    "add9": ("1", "3", "5", "9"),
    "madd9": ("1", "b3", "5", "9"),
    "9": ("1", "3", "5", "b7", "9"),
    "maj9": ("1", "3", "5", "7", "9"),
    "m9": ("1", "b3", "5", "b7", "9"),
    "7b9": ("1", "3", "5", "b7", "b9"),
    "7#9": ("1", "3", "5", "b7", "#9"),
    "11": ("1", "5", "b7", "9", "11"),  # the 3rd usually left out (N3)
    "m11": ("1", "b3", "5", "b7", "9", "11"),
    "7#11": ("1", "3", "5", "b7", "#11"),
    "13": ("1", "3", "5", "b7", "9", "13"),  # no 11 (N4)
    "maj13": ("1", "3", "5", "7", "9", "13"),
    "m13": ("1", "b3", "5", "b7", "9", "11", "13"),
}
# each interval as (letters on, semitones): every note is spelled by its interval from the root (N1)
INTERVALS: dict[str, tuple[int, int]] = {
    "1": (0, 0),
    "2": (1, 2),
    "b3": (2, 3),
    "3": (2, 4),
    "4": (3, 5),
    "b5": (4, 6),
    "5": (4, 7),
    "#5": (4, 8),
    "6": (5, 9),
    "bb7": (6, 9),
    "b7": (6, 10),
    "7": (6, 11),
    "b9": (1, 1),
    "9": (1, 2),
    "#9": (1, 3),
    "11": (3, 5),
    "#11": (3, 6),
    "13": (5, 9),
}
_SHOWN_ENDING = {"mMaj7": "m(maj7)"}
_DIM_TRIADS = frozenset({"dim", "°", "º", "o"})
UNREAD = "unread"  # canonical()'s answer for a "-" or "+" that is read nowhere (S3: C7+)


def canonical(ending: str) -> str | None:
    """The ending table's name for an ending as written (S2, S3): its aliases folded (mi, min and - for minor, ° for
    dim, ø and h7 for m7b5, Δ, ^, ma7, M7 and 7M for maj7 ...), "-" and "+" read by where they stand: right after the
    root minor and augmented, and that reading wins (C-9 is Cm9); before 5, 9, 11 or 13 flat and sharp (C7-9 is C7b9).
    UNREAD for a "-" or "+" anywhere else (C7+); None for an ending outside the table (C7alt)."""
    e = ending.replace("♯", "#").replace("♭", "b").replace("º", "°")
    if e.startswith("-"):
        e = "m" + e[1:]
    elif e.startswith("+"):
        e = "aug" + e[1:]
    e = re.sub(r"(?<=.)-(?=(?:5|9|11|13)(?!\d))", "b", e)
    e = re.sub(r"(?<=.)\+(?=(?:5|9|11|13)(?!\d))", "#", e)
    if "+" in e or "-" in e:
        return UNREAD
    e = e.replace("(", "").replace(")", "").replace(",", "")
    if e.startswith("min"):
        e = "m" + e[3:]
    elif e.startswith("mi"):
        e = "m" + e[2:]
    minor = e.startswith("m") and not e.startswith("maj") and not e.startswith("ma")
    body = e[1:] if minor else e
    if body in ("Δ", "^"):
        body = "maj7"
    body = re.sub(r"^(?:Maj|maj|ma|M|Δ|\^)(?=\d)", "maj", body)
    if body in ("Maj", "maj", "ma", "M"):
        body = ""
    if body == "7M":
        body = "maj7"
    if body.startswith("°") or (body.startswith("o") and not body.startswith("omit")):
        body = "dim" + body[1:]
    if body in ("ø", "ø7", "h7"):  # S2: h7, never a lone h ("Ah", "Eh")
        return "m7b5"
    if minor:
        if body.startswith("maj"):
            return "mMaj7" if body == "maj7" else None
        name = "m" + body
        name = {"m69": "m6/9", "madd2": "madd9", "m7-5": "m7b5"}.get(name, name)
        return name if name in TABLE else None
    name = {
        "sus": "sus4",
        "7sus": "7sus4",
        "69": "6/9",
        "add2": "add9",
        "7#5": "aug7",
        "aug7": "aug7",
    }.get(body, body)
    return name if name in TABLE else None


# ---- one chord (S1, S5) ----


@dataclass(frozen=True)
class Chord:
    """One chord as read: its root, its ending as written, its bass, and how it is written (`naming`; `text`: the chord
    as written)."""

    text: str
    root: Note
    ending: str
    bass: Note | None
    naming: Naming
    root_text: str
    bass_text: str = ""

    @property
    def table(self) -> str | None:
        """Its ending's name in the table (canonical); UNREAD or None when it has none."""
        return canonical(self.ending)

    @property
    def letters(self) -> str:
        """The chord in plain letters with signs and the table's name of its ending ("B♭m7", "F♯")."""
        name = self.table
        ending = self.ending if name in (None, UNREAD) else _SHOWN_ENDING.get(name, name)
        bass = f"/{self.bass.words}" if self.bass is not None else ""
        return self.root.words + ending + bass

    @property
    def ascii(self) -> str:
        """`letters` with # and b."""
        return self.letters.replace("♯", "#").replace("♭", "b").replace("𝄪", "##").replace("𝄫", "bb")

    @property
    def reading(self) -> str:
        """The chord as read, for "Read as:": as written, then what it was read as when that is not plain letters
        or the table's name of its ending ("H7 (B7, German H)", "Sol7 (G7)", "C-7 (Cm7)", "B (B♭, German B)")."""
        typed = self.text.replace("♯", "#").replace("♭", "b")
        why = [self.letters] if self.ascii != typed else []
        if self.naming.german:
            written = (self.root_text, self.bass_text)
            if any(text.startswith("H") for text in written):
                why.append("German H")
            if "B" in written:
                why.append("German B")
        return f"{self.text} ({', '.join(why)})" if why else self.text


@dataclass(frozen=True)
class _Song:
    """What the whole text settles for its chords: German B and H (L2), do re mi (L3) and the accents it writes."""

    german: bool = False
    solfege: bool = False
    accents: frozenset[int] = frozenset()
    unicode: bool = False  # ♯ or ♭ typed somewhere: a chord typed without a sign gets its signs as ♯ and ♭ too
    names: bool = False  # German or Dutch names (Fis, Es) and no # or b: a chord typed without a sign gets names too


def _parts(token: str, root: _Root) -> tuple[str, str] | None:
    """The ending and the bass text after a root, split at the bass's slash (6/9 is no slash, and a slash followed
    only by slashes or nothing is a beat mark, never a bass: "C/" is C); None when the ending is not made of chord
    pieces."""
    rest = token[root.length :].rstrip("/")
    cut = rest.find("/")
    while cut > 0 and rest[cut - 1] == "6" and rest[cut + 1 : cut + 2] == "9":
        cut = rest.find("/", cut + 1)
    ending, bass = (rest, "") if cut < 0 else (rest[:cut], rest[cut + 1 :])
    if cut >= 0 and not bass:
        return None
    if len(ending) > MAX_ENDING:
        raise _not_a_chord(token, f"an ending has at most {MAX_ENDING} characters")
    return (ending, bass) if valid_ending(ending) else None


def _fits(root: _Root, song: _Song) -> bool:
    """Whether a reading of a root fits the song: do re mi only in a song of do re mi (and "Do" is then never D°)."""
    return (root.system == "solfege") == song.solfege


def chord_of(token: str, song: _Song | None = None) -> Chord:
    """One chord read from its token (S1, S5), in the song's naming; ChordError names the token."""
    song = song or _Song()
    for root in _roots(token):
        if not _fits(root, song):
            continue
        split = _parts(token, root)
        if split is None:
            continue
        ending, bass_text = split
        bass: Note | None = None
        bass_root: _Root | None = None
        if bass_text:
            if bass_text[0].isdigit():
                raise _not_a_chord(token)  # digits after a slash (6/9 aside): never a number (G2)
            bass_root = next(
                (b for b in _roots(bass_text) if b.length == len(bass_text) and _fits(b, song)),
                None,
            )
            if bass_root is None:
                raise _not_a_chord(token)  # a polychord, or no note after the slash
            bass = _settled(bass_root, song)
        typed = [part for part in (root, bass_root) if part is not None and part.note.alter]
        system = "names" if song.names and root.system == "letters" and not typed else root.system
        naming = Naming(
            system,
            german=song.german,
            caps=root.caps,
            accents=song.accents,
            unicode=any(part.unicode for part in typed) if typed else song.unicode,  # as typed
        )
        if root.double or (bass_root is not None and bass_root.double):  # S1: refused, the plain name offered
            written = naming.write(plain(root.note.pc, root.note.alter > 0)) if root.double else token[: root.length]
            if bass is not None and bass_root is not None:
                bass_written = naming.write(plain(bass.pc, bass.alter > 0)) if bass_root.double else bass_text
                raise _not_a_chord(token, f"write {written}{ending}/{bass_written}")
            raise _not_a_chord(token, f"write {written}{ending}")
        return Chord(token, _settled(root, song), ending, bass, naming, token[: root.length], bass_text)
    raise _not_a_chord(token)


def _settled(root: _Root, song: _Song) -> Note:
    """A root as the song reads it: a plain B is B♭ in a German song (L2)."""
    if root.is_b and song.german:
        return Note(6, -1)
    return root.note


# ---- a whole text: chords and what passes through (S6, C4, K3) ----

_SEPARATORS = frozenset({",", "·", "|", "-", "–", "—", "→", "->", "||", "|:", ":|", "||:", ":||", "‖"})
_GRID = frozenset({"/", "%", "%%", "."})  # chord grids: the chord again, repeat one or two bars, an empty beat
_NO_CHORD = re.compile(r"(?:N\.?C\.?|n\.c\.)\Z")
_REPEAT = re.compile(r"\(?(?:[x×X]\s?\d{1,2}|\d{1,2}[x×X])\)?\Z")
_LABEL = re.compile(r"[\w'’.-]{1,30}:\Z")
_WORD = re.compile(r"[^\W\d_][\w'’-]{0,40}\Z")
_NUMBER = re.compile(r"\d{1,4}(?:bpm|BPM)?\Z")
_CAPO = re.compile(  # "Capo 3", "Capo: 3", "Capo 3rd fret", "Capo on 2", "Capo on fret 2", "Capo 3 (G shapes)"
    r"(\s*)(capo|Capo|CAPO)\s?:?\s?(?:on\s(?:fret\s)?)?(\d{1,2})(?:st|nd|rd|th)?(?:\s(?:fret|Fret))?"
    r"(?:\s?\(([^\s()]{1,12})[\s-](?:shapes?|Shapes?)\))?(?=[\s.,;:]|\Z)"
)
_SAID_BRACKET = re.compile(r"\s?\(([^()]*)\)")  # "Capo 3 (A shapes)" as said: "Capo 3, A shapes"
_TUNING = re.compile(
    r"\s*(?:tuning\s?:|drop\s[a-g]\b|dadgad\b|open\s[a-g]\b|(?:half|whole)[\s-]step\sdown\b|"
    r"[a-g][b♭#♯]?\sstandard\b|standard(?:\stuning)?\s*\Z)",
    re.IGNORECASE,
)
_STANDARD = (4, 9, 2, 7, 11, 4)  # E A D G B E, low to high


@dataclass(frozen=True)
class Piece:
    """A piece of the text as written: a chord (`chord`, with the punctuation around it), or what passes through
    (`kind`: space, mark, label, repeat, nc, word, key, capo, tuning)."""

    text: str
    kind: str
    chord: Chord | None = None
    before: str = ""
    after: str = ""


@dataclass(frozen=True)
class Reading:
    """A text of chords as read: its pieces in order, its chords, the naming it settled, a "Capo N" line's fret and
    the shapes it names in brackets ("Capo 3 (G shapes)": "G"), how far a tuning line tunes every string down, a
    "Key:" line's text, and the words passed through."""

    pieces: tuple[Piece, ...]
    chords: tuple[Chord, ...]
    song: _Song
    fret: int | None = None
    tuning: str = ""
    drop: int = 0
    key_text: str = ""
    words: tuple[str, ...] = ()
    language: str = "en"
    shapes: str = ""

    def written(self, chords: list[str], shapes: str = "") -> str:
        """The text with its chords replaced by `chords`, in order, and the capo line's shapes by `shapes`;
        everything else as written."""
        out: list[str] = []
        moved = iter(chords)
        for piece in self.pieces:
            if piece.chord is not None:
                out.append(piece.before + next(moved) + piece.after)
            else:
                out.append(_with_shapes(piece.text, shapes) if piece.kind == "capo" else piece.text)
        return "".join(out)


def _with_shapes(text: str, shapes: str) -> str:
    """A "Capo N" line with the shapes in its brackets replaced by `shapes` (none, or no brackets: as written)."""
    found = _CAPO.match(text)
    if not shapes or found is None or found.group(4) is None:
        return text
    return text[: found.start(4)] + shapes + text[found.end(4) :]


_GERMAN_WORD = re.compile(r"(?<![\w])[A-Ha-h](?:is|es|s)?[\s-]?(?:Dur|Moll|dur|moll)\b")


def _german(language: str, extra: str) -> bool:
    """A German song (L2): the conversation in German or Polish, or a German or Polish key word in the user's
    message or the key arguments (B-Dur, h-Moll, cis-moll). An H among the chords counts too (read)."""
    return language in ("de", "pl") or _GERMAN_WORD.search(extra) is not None


def read(text: str, *, language: str = "en", message: str = "") -> Reading:
    """A text of chords as written (sections, separators, N.C., repeat marks, grid marks, labels, a tuning line, a
    "Capo N" and a "Key:" line), read whole; ChordError for the first token that starts like a chord but is none, for
    lower-case roots, mixed names and too long a text (G1)."""
    if len(text) > MAX_CHARS:
        raise ChordError("too_long", TOO_LONG)
    raw = _pieces(text)
    tokens = [piece.text for piece in raw if piece.kind == "chord?"]
    if len(tokens) > MAX_CHORDS:
        raise ChordError("too_long", TOO_LONG)
    song = _settle(tokens, language, message)
    pieces: list[Piece] = []
    chords: list[Chord] = []
    for piece in raw:
        if piece.kind != "chord?":
            pieces.append(piece)
            continue
        chord = chord_of(piece.text, song)
        chords.append(chord)
        pieces.append(Piece(piece.text, "chord", chord, piece.before, piece.after))
    line = next((found for piece in raw if piece.kind == "capo" and (found := _CAPO.match(piece.text))), None)
    fret = int(line.group(3)) if line is not None else None
    if fret is not None and fret > 11:
        raise ChordError("fret", "fret from 0 to 11")
    shapes = (line.group(4) or "") if line is not None else ""
    if shapes:
        parse_key(shapes, song)  # the shapes in brackets name a key ("Capo 2 (open shapes)" is refused)
    tuning = next((piece.text.strip() for piece in raw if piece.kind == "tuning"), "")
    key_text = " ".join(piece.text for piece in raw if piece.kind == "key").rstrip(".,;")
    words = tuple(dict.fromkeys(piece.text for piece in raw if piece.kind == "word"))
    drop = tuning_drop(tuning)
    return Reading(tuple(pieces), tuple(chords), song, fret, tuning, drop, key_text, words, language, shapes)


def _settle(tokens: list[str], language: str, message: str) -> _Song:
    """The song's naming from all its chords: do re mi when one chord can only be do re mi (letters and do re mi
    together are refused), German B when an H or a German name or word says so (L2), the accents it writes."""
    solfege_only = letters_only = ""
    german = _german(language, message)
    accents: set[int] = set()
    named = signed = False
    for token in tokens:
        readings = [root for root in _roots(token) if _parts_or_none(token, root) is not None]
        systems = {root.system for root in readings}
        named = named or systems == {"names"}
        signed = signed or any(root.system == "letters" and root.note.alter for root in readings)
        if systems == {"solfege"} and not solfege_only:
            solfege_only = token
        if systems and "solfege" not in systems and not letters_only:
            letters_only = token
        german = german or any(root.is_h for root in readings) or _bass_h(token)
        accents |= {root.note.letter for root in readings if root.accent}
    if solfege_only and letters_only:
        raise ChordError("mixed", f'mixed names: "{shown(letters_only)}" and "{shown(solfege_only)}"')
    solfege = bool(solfege_only) or (not letters_only and any(t.startswith("Do") for t in tokens))
    unicode = any("♯" in token or "♭" in token for token in tokens)
    if language == "fr":
        accents.add(1)  # French writes Ré
    if accents - {1} or language == "pt":  # Dó, Fá or Lá: Portuguese, which accents all four; Ré alone: French
        accents = set(_ACCENTED.values())
    return _Song(german, solfege, frozenset(accents), unicode, named and not signed)


def _parts_or_none(token: str, root: _Root) -> tuple[str, str] | None:
    try:
        return _parts(token, root)
    except ChordError:
        return None


def _bass_h(token: str) -> bool:
    return "/H" in token


def _pieces(text: str) -> list[Piece]:
    """The text cut into pieces: whitespace, what passes through, and tokens to read as chords ("chord?")."""
    out: list[Piece] = []
    lines = text.split("\n")
    for number, line in enumerate(lines):
        if number:
            out.append(Piece("\n", "space"))
        if _TUNING.match(line):
            out.append(Piece(line, "tuning"))
            continue
        capo = _CAPO.match(line)
        if capo:
            if capo.group(1):
                out.append(Piece(capo.group(1), "space"))
            out.append(Piece(capo.group(0)[len(capo.group(1)) :], "capo"))
            line = line[capo.end() :]
        out.extend(_line_pieces(line))
    return out


def _line_pieces(line: str) -> list[Piece]:
    parts = re.split(r"(\s+)", line)
    out: list[Piece] = []
    i = 0
    key_left = 0  # tokens of a "Key:" line's value still to come
    while i < len(parts):
        token = parts[i]
        i += 1
        if not token:
            continue
        if token.isspace():
            out.append(Piece(token, "space"))
            continue
        if key_left:
            key_left -= 1
            out.append(Piece(token, "key"))
            if token[-1] in ".,;":
                key_left = 0
            continue
        if token.casefold() == "key:":
            out.append(Piece(token, "label"))
            key_left = _key_value(parts[i:])
            continue
        if token[0] in "[{" and not token.endswith("]}"[("[{".index(token[0]))]):
            closing = "]}"[("[{".index(token[0]))]
            j = i
            while j < len(parts) and not parts[j].endswith(closing) and j - i < 20:
                j += 1
            if j < len(parts) and parts[j].endswith(closing):
                out.append(Piece("".join(parts[i - 1 : j + 1]), "label"))
                i = j + 1
                continue
        out.append(_token_piece(token))
    return out


_KEY_WORD = re.compile(r"(?:flat|sharp|major|minor|maj|min)[.,;]?\Z", re.IGNORECASE)


def _key_value(rest: list[str]) -> int:
    """How many tokens after "Key:" are its value: a chord symbol or a key in one word (Am, B-Dur), or a letter with
    flat or sharp and major or minor after it (A minor, B flat major); a full stop or a comma ends it."""
    words = [token for token in rest if token and not token.isspace()][:3]
    count = 0
    for token in words:
        if count and not _KEY_WORD.match(token):
            break
        count += 1
        if token[-1] in ".,;":
            break
    return count


def _token_piece(token: str) -> Piece:
    """One token: a mark, N.C., a repeat mark, a label, a word or number passed through, or a chord to read. A slash
    followed only by slashes or nothing is a beat mark, never a bass note: "C///" and "C/" are C and their beats."""
    if token in _SEPARATORS or token in _GRID or not token.strip("/"):  # "///" standing alone: beat marks too
        return Piece(token, "mark")
    if _NO_CHORD.match(token):
        return Piece(token, "nc")
    if _REPEAT.match(token):
        return Piece(token, "repeat")
    if token[0] in "[(" and token[-1] in "])" and len(token) > 2:
        inner = token[1:-1]
        if _parses(inner) and not inner.endswith("/"):  # a chord in brackets ("[Am]"); "(C/)" keeps its beat below
            return Piece(inner, "chord?", before=token[0], after=token[-1])
        if _WORD.match(inner):  # a section label in brackets ("[Chorus]")
            return Piece(token, "label")
    if _LABEL.match(token):  # a section label ("Intro:", "Verse:")
        return Piece(token, "label")
    before = "(" if token.startswith("(") else ""
    core = token[len(before) :]
    after = ""
    while core and core[-1] in ".,;" and len(after) < 3:
        after = core[-1] + after
        core = core[:-1]
    if before and core.endswith(")") and core.count("(") < core.count(")"):
        core, after = core[:-1], ")" + after
    bare = core.rstrip("/")
    if bare and bare != core:  # the beat marks glued to a chord ("C///", "C/G/") stay as written after it
        core, after = bare, core[len(bare) :] + after
    if core and _starts_like_a_chord(core):
        return Piece(core, "chord?", before=before, after=after)
    if core and core[0] in "abcdefgh" and _parses_capitalised(core):
        raise _not_a_chord(token)  # lower-case roots (S1, L3): "am", "do"
    if core and _lower_solfege(core):
        raise _not_a_chord(token)
    if _WORD.match(token) or _NUMBER.match(token):
        return Piece(token, "word")
    raise _not_a_chord(token)


def _parses(token: str) -> bool:
    """Whether some reading of a token is a chord (its naming not settled yet)."""
    return any(_parts_or_none(token, root) is not None for root in _roots(token))


def _starts_like_a_chord(token: str) -> bool:
    """A capital A to H, or a do re mi name in title case or capitals."""
    return bool(token) and (token[0] in "ABCDEFGH" or any(root.system == "solfege" for root in _roots(token)))


def _parses_capitalised(token: str) -> bool:
    try:
        chord_of(token[0].upper() + token[1:])
    except ChordError:
        return False
    return True


def _lower_solfege(token: str) -> bool:
    lowered = token.casefold()
    for name in (*_SOLFEGE, *_ACCENTED):
        if lowered.startswith(name.casefold()):
            rest = token[len(name) :]
            try:
                chord_of(name + rest, _Song(solfege=True))
            except ChordError:
                continue
            return True
    return False


def tuning_drop(line: str) -> int:
    """How many half steps a tuning line tunes every string down (C4): half step down and E♭ standard 1, whole step
    down and D standard 2, a "Tuning:" line of six notes all lowered alike; Drop D, DADGAD, open tunings and anything
    else 0 (they move nothing)."""
    lowered = line.casefold()
    if not lowered:
        return 0
    if re.search(r"half[\s-]step\sdown", lowered):
        return 1
    if re.search(r"whole[\s-]step\sdown", lowered):
        return 2
    standard = re.match(r"\s*([a-g])([b♭#♯]?)\sstandard", lowered)
    if standard:
        note = Note(LETTERS.index(standard.group(1).upper()), _ACCIDENTAL.get(standard.group(2), 0))
        return (4 - note.pc) % 12
    named = re.match(r"\s*tuning\s?:\s?(.*)", line, re.IGNORECASE)
    if named:
        strings = named.group(1).split()
        if len(strings) == 6:
            try:
                sounds = [chord_of(string).root.pc for string in strings]
            except ChordError:
                return 0
            drops = {(standard_pc - pc) % 12 for standard_pc, pc in zip(_STANDARD, sounds, strict=True)}
            if len(drops) == 1:
                return drops.pop()
    return 0


# ---- notes (N1 to N4) ----


@dataclass(frozen=True)
class Notes:
    """One chord's notes: the bass first, then the chord from its root, each spelled by its interval from the root in
    the chord's own naming, a double accidental or E♯, B♯, C♭, F♭ followed by "(sounds as …)"; code's `remark` after
    them; the chord as read."""

    chord: str
    notes: tuple[str, ...]
    remark: str | None
    read_as: str
    spoken: str = ""  # as said on a call: in plain letters, sharps and flats in words


def notes(chord: str, *, language: str = "en", message: str = "") -> Notes:
    """A chord's notes (N1 to N4): one chord, its ending from the table (C7alt and C7+ are refused)."""
    reading = read(chord.strip(), language=language, message=message)
    if len(reading.chords) != 1 or any(piece.kind not in ("chord", "space") for piece in reading.pieces):
        raise ChordError("one_chord", f'notes takes one chord: "{shown(chord.strip())}"')
    found = reading.chords[0]
    name = found.table
    if name is None or name == UNREAD:
        raise ChordError("ending", f'ending not in the table: "{shown(found.text)}"')
    tones = [found.root.moved(*INTERVALS[interval]) for interval in TABLE[name]]
    if found.bass is not None:
        bass = found.bass
        tones = [bass, *(tone for tone in tones if tone.pc != bass.pc)]
    naming = found.naming
    written = tuple(
        naming.write(tone) + (f" (sounds as {naming.write(plain(tone.pc, tone.alter > 0))})" if tone.odd else "")
        for tone in tones
    )
    remark = None
    said_after = ""
    if name == "11":
        remark = "(3rd usually left out)"
        said_after = ", the 3rd usually left out"
    elif name == "dim" and found.ending in _DIM_TRIADS:
        remark = f"(many chord books play {found.root_text}dim as {found.root_text}dim7)"
        said_after = f". Many chord books play {letters_of(found)}dim as {letters_of(found)}dim7"
    words = [
        spoken_note(tone) + (f", which sounds as {spoken_note(plain(tone.pc, tone.alter > 0))}" if tone.odd else "")
        for tone in tones
    ]
    listed = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    spoken = f"{_ascii_chord(found.root, found.ending, found.bass)}: the notes {listed}{said_after}."
    return Notes(found.text, written, remark, found.reading, spoken)


# ---- the key (F1 to F4, R1, R2) ----

_MAJOR = (0, 2, 4, 5, 7, 9, 11)
_MINOR = (0, 2, 3, 5, 7, 8, 10)  # natural minor (P3)
_MAJ = frozenset({0, 4, 7})
_MIN = frozenset({0, 3, 7})
_DIM = frozenset({0, 3, 6})
_AUG = frozenset({0, 4, 8})
_SUS4 = frozenset({0, 5, 7})
_SUS2 = frozenset({0, 2, 7})
# each table ending's triad by its notes (sus and 5 chords by theirs, the 11 chord as a sus chord: no 3rd), its kind
# and whether it is a dominant 7th (F1: extensions ignored)
_TRIADS: dict[str, tuple[frozenset[int], str, bool]] = {
    **{name: (_MAJ, "maj", False) for name in ("", "6", "6/9", "add9", "maj7", "maj9", "maj13")},
    **{name: (_MAJ, "maj", True) for name in ("7", "9", "13", "7b9", "7#9", "7#11")},
    "7b5": (frozenset({0, 4, 6}), "maj", True),
    **{name: (_MIN, "min", False) for name in ("m", "m6", "m6/9", "madd9", "m7", "m9", "m11", "m13", "mMaj7")},
    **{name: (_DIM, "dim", False) for name in ("dim", "dim7", "m7b5")},
    "aug": (_AUG, "aug", False),
    "aug7": (_AUG, "aug", True),
    "sus4": (_SUS4, "open", False),
    "7sus4": (_SUS4, "open", True),
    "11": (_SUS4, "open", True),
    "sus2": (_SUS2, "open", False),
    "5": (frozenset({0, 7}), "open", False),
}
# a numeral's sign after its degree (R1): °, ø, + and 7, and the table's other endings by their numbers
_NUMERAL_SIGNS = {
    "dim": "°",
    "dim7": "°7",
    "m7b5": "ø7",
    "aug": "+",
    "aug7": "+7",
    "7": "7",
    "m7": "7",
    "maj7": "maj7",
    "mMaj7": "maj7",
    "6": "6",
    "m6": "6",
    "6/9": "6/9",
    "m6/9": "6/9",
    "9": "9",
    "m9": "9",
    "maj9": "maj9",
    "11": "11",
    "m11": "11",
    "13": "13",
    "m13": "13",
    "maj13": "maj13",
    "sus4": "sus4",
    "sus2": "sus2",
    "7sus4": "7sus4",
    "5": "5",
    "add9": "add9",
    "madd9": "add9",
    "7b5": "7b5",
    "7b9": "7b9",
    "7#9": "7#9",
    "7#11": "7#11",
}
_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII")
OUTSIDE = "outside the key"
TOO_FEW = "Too few chords to tell the key: 3 or more name one."


@dataclass(frozen=True)
class Heard:
    """A chord as the key finding hears it: its triad's sounds from the root, its kind (maj, min, dim, aug, or open:
    no 3rd), whether it is a dominant 7th, and the table's name of its ending ("" when it has none)."""

    chord: Chord
    pcs: frozenset[int]
    kind: str
    dominant: bool
    name: str

    @property
    def root(self) -> int:
        return self.chord.root.pc

    def tonic_of(self, tonic: int, minor: bool) -> bool:
        """Whether it is the tonic chord of a key: on the tonic, of the key's mode or with no 3rd."""
        return self.root == tonic and self.kind in ("min" if minor else "maj", "open")


def heard(chord: Chord) -> Heard | None:
    """A chord for the key (F1, S6): a table ending by its notes; any other by its root and 3rd, alt as a dominant
    7th; None for a "-" or "+" that is read nowhere (C7+)."""
    name = chord.table
    if name == UNREAD:
        return None
    if name is not None:
        pcs, kind, dominant = _TRIADS[name]
        return Heard(chord, pcs, kind, dominant, name)
    ending = chord.ending
    if "sus2" in ending:
        return Heard(chord, _SUS2, "open", "7" in ending, "")
    if "sus" in ending:
        return Heard(chord, _SUS4, "open", "7" in ending, "")
    if ending.startswith(("dim", "°", "º", "o", "ø", "h")):
        return Heard(chord, _DIM, "dim", False, "")
    if ending.startswith(("aug", "+")):
        return Heard(chord, _AUG, "aug", "7" in ending, "")
    if ending.startswith(("m", "-")) and not ending.startswith(("maj", "ma", "M")):
        return Heard(chord, _MIN, "min", False, "")
    major_seventh = any(mark in ending for mark in ("maj", "ma", "M", "Δ", "^"))
    dominant = "alt" in ending or (not major_seventh and any(n in ending for n in ("7", "9", "11", "13")))
    return Heard(chord, _MAJ, "maj", dominant, "")


def in_key(chord: Heard, tonic: int, minor: bool) -> bool:
    """A chord in the key (F1, R1): its triad in the scale (natural minor), or in minor V, V7 and vii°."""
    scale = {(tonic + step) % 12 for step in (_MINOR if minor else _MAJOR)}
    if {(chord.root + step) % 12 for step in chord.pcs} <= scale:
        return True
    degree = (chord.root - tonic) % 12
    if minor:
        return (degree == 7 and chord.kind == "maj") or (degree == 11 and chord.kind == "dim")
    return chord.dominant and chord.kind == "maj" and degree in (0, 5)  # I7 and IV7 (the blues)


_BORROWED_MAJOR = ((10, "maj"), (8, "maj"), (3, "maj"), (5, "min"))  # ♭VII, ♭VI, ♭III, iv
_BORROWED_MINOR = ((5, "maj"),)  # IV


def score(chords: list[Heard], tonic: int, minor: bool) -> float | None:
    """A key's score (F1, our weights): +1 a chord in the key, +0.5 a common borrowed chord (a sus or 5 chord on its
    root too), +2 when the first chord is the tonic, +1 when the last is, +1 each V to I; None when its tonic chord
    is not among the chords (a key is named only then)."""
    if not any(chord.tonic_of(tonic, minor) for chord in chords):
        return None
    borrowed = _BORROWED_MINOR if minor else _BORROWED_MAJOR
    total = 0.0
    for chord in chords:
        if in_key(chord, tonic, minor):
            total += 1
        elif any((chord.root - tonic) % 12 == step and chord.kind in (kind, "open") for step, kind in borrowed):
            total += 0.5
    total += (2 if chords[0].tonic_of(tonic, minor) else 0) + (1 if chords[-1].tonic_of(tonic, minor) else 0)
    for first, then in pairwise(chords):
        if (first.root - tonic) % 12 == 7 and first.kind == "maj" and then.tonic_of(tonic, minor):
            total += 1
    return total


@dataclass(frozen=True)
class KeyName:
    """A key: its tonic as spelled, its mode, and the naming its chord symbol is written in."""

    tonic: Note
    minor: bool
    naming: Naming = LETTERS_NAMING

    @property
    def symbol(self) -> str:
        """As a chord symbol in the song's naming: "Am", "C#m", "Bb", "SIm", "Hm"."""
        return self.naming.write(self.tonic) + ("m" if self.minor else "")

    @property
    def words(self) -> str:
        """In English words: "A minor", "B♭ major"."""
        return f"{self.tonic.words} {'minor' if self.minor else 'major'}"

    @property
    def shown(self) -> str:
        return f"{self.symbol} ({self.words})"

    @property
    def accidentals(self) -> int:
        """Its signature: + sharps, - flats."""
        return self.tonic.fifths - (3 if self.minor else 0)

    @property
    def scale(self) -> tuple[Note, ...]:
        """Its seven notes from the tonic, one letter each (the natural minor for a minor key, P3)."""
        steps = _MINOR if self.minor else _MAJOR
        return tuple(self.tonic.moved(degree, step) for degree, step in enumerate(steps))

    @property
    def relative(self) -> KeyName:
        """Its relative, by letter, a minor third away (F4: F♯ major's is D♯ minor)."""
        tonic = self.tonic.moved(2, 3) if self.minor else self.tonic.moved(5, 9)
        return KeyName(tonic, not self.minor, self.naming)

    def same_key(self, other: KeyName) -> bool:
        return self.tonic.pc == other.tonic.pc and self.minor == other.minor


@dataclass(frozen=True)
class Estimate:
    """The key finding's answer: the best keys (a tie lists each, in the order their tonic chords first come), each
    spelled by its tonic chord as written (F4), and the keys also possible (the relative when it fits, any other within
    1 point; `relative` marks it)."""

    best: tuple[KeyName, ...]
    also: tuple[tuple[KeyName, bool], ...]
    top: float

    @property
    def tie(self) -> bool:
        return len(self.best) > 1


def estimate(chords: list[Heard]) -> Estimate:
    """All 24 keys scored (F1); the best named (F2)."""
    scored: list[tuple[float, int, int, bool]] = []
    for tonic in range(12):
        for minor in (False, True):
            found = score(chords, tonic, minor)
            if found is not None:
                first = next(i for i, chord in enumerate(chords) if chord.tonic_of(tonic, minor))
                scored.append((found, first, tonic, minor))
    if not scored:  # no chord can be a tonic (only aug or dim chords): the first is its own, its 3rd the mode (U1)
        first_chord = chords[0]
        return Estimate((KeyName(first_chord.chord.root, first_chord.kind == "dim", first_chord.chord.naming),), (), 0)
    scored.sort(key=lambda row: (-row[0], row[1], row[3]))
    top = scored[0][0]
    best = tuple(_spelled(chords, tonic, minor) for value, _, tonic, minor in scored if value == top)
    also: list[tuple[KeyName, bool]] = []
    if len(best) == 1:
        relative = best[0].relative
        if score(chords, relative.tonic.pc, relative.minor) is not None:
            also.append((relative, True))
        for value, _, tonic, minor in scored:
            if value < top and value >= top - 1 and not (tonic == relative.tonic.pc and minor == relative.minor):
                also.append((_spelled(chords, tonic, minor), False))
    return Estimate(best, tuple(also), top)


def _spelled(chords: list[Heard], tonic: int, minor: bool) -> KeyName:
    """A key spelled by its tonic chord as the song writes it (F4: the scores count sounds, which have no spelling)."""
    chord = next(chord for chord in chords if chord.tonic_of(tonic, minor)).chord
    return KeyName(chord.root, minor, chord.naming)


def likely(chords: str, *, language: str = "en", message: str = "") -> tuple[str, ...]:
    """The best keys of a text of chords as chord symbols (a tie gives each), from one chord up (U1)."""
    reading = read(chords, language=language, message=message)
    found = [hearing for chord in reading.chords if (hearing := heard(chord)) is not None]
    if not found:
        raise ChordError("no_chords", "no chords to read")
    return tuple(name.symbol for name in estimate(found).best)


def numeral(chord: Heard, key: KeyName) -> str:
    """The chord's scale degree in the key (R1): upper case major, augmented or with no 3rd, lower case minor or
    diminished, with °, ø, + and 7 added; any chord not in the key is OUTSIDE (R2)."""
    tonic = key.tonic.pc
    if not in_key(chord, tonic, key.minor):
        return OUTSIDE
    step = (chord.root - tonic) % 12
    scale = _MINOR if key.minor else _MAJOR
    degree = scale.index(step) if step in scale else {7: 4, 11: 6}[step]
    roman = _ROMAN[degree]
    if chord.kind in ("min", "dim"):
        roman = roman.lower()
    sign = _NUMERAL_SIGNS.get(chord.name, "7" if chord.dominant else "")
    if not chord.name and chord.kind == "dim":
        sign = "°"
    return roman + sign


@dataclass(frozen=True)
class Key:
    """The likely key of given chords: `key` as a chord symbol and in words, spelled by its tonic chord (on a tie, the
    tied keys; None under 3 different chords); `also` the relative when it fits and any other key within 1 point
    (none on a tie); one numeral per chord in the likely key; `summary` in words (F2); the chords as read. Under 3
    chords (as given, "Am E7 Am" is 3): too few to tell."""

    key: str | None
    also: tuple[str, ...]
    numerals: tuple[str, ...]
    read_as: str
    summary: str
    spoken: str = ""  # as said on a call: the key only, no numerals (neither speech engine reads them)


def key(chords: str, *, language: str = "en", message: str = "") -> Key:
    """The key of given chords (F1 to F4, R1, R2); a "-" or "+" it cannot read is refused (S3: C7+)."""
    reading = read(chords, language=language, message=message)
    hearing: list[Heard] = []
    for chord in reading.chords:
        found = heard(chord)
        if found is None:
            raise ChordError("ending", f'ending not in the table: "{shown(chord.text)}"')
        hearing.append(found)
    if not hearing:
        raise ChordError("no_chords", "no chords to read")
    read_as = "; ".join([chords_read(reading, alt=True), *passed(reading)])
    if len(reading.chords) < 3:  # F2: 3 chords or more name a key
        return Key(None, (), (), read_as, TOO_FEW, "Too few chords to tell the key.")
    found_key = estimate(hearing)
    first = found_key.best[0]
    numerals = tuple(numeral(chord, first) for chord in hearing)
    shown_numerals = (", " if OUTSIDE in numerals else " ").join(numerals)
    if found_key.tie:
        named = " or ".join(name.shown for name in found_key.best)
        summary = f"Likely {_or_words(found_key.best)} (a tie). In {first.words}: {shown_numerals}."
        return Key(named, (), numerals, read_as, summary, speech(f"Likely {_or_words(found_key.best)}."))
    also = tuple(name.shown for name, _ in found_key.also)
    summary = " ".join(
        [
            f"Likely {first.words}: {shown_numerals}.",
            *(f"{name.words} is also possible{': the same chords' if same else ''}." for name, same in found_key.also),
        ]
    )
    spoken = " ".join([f"Likely {first.words}.", *(f"{name.words} is also possible." for name, _ in found_key.also)])
    return Key(first.shown, also, numerals, read_as, summary, speech(spoken))


def _or_words(keys: tuple[KeyName, ...]) -> str:
    """Tied keys in words: "G or C major", "F minor or D♭ major"."""
    if len({name.minor for name in keys}) == 1:
        return " or ".join(name.tonic.words for name in keys) + (" minor" if keys[0].minor else " major")
    return " or ".join(name.words for name in keys)


def chords_read(reading: Reading, *, alt: bool = False) -> str:
    """The chords as read, each once in order, with what it was read as when that is not plain letters (E2); `alt`:
    the key action's reading of an ending with no fixed notes (C7alt as C7, S6)."""
    seen: list[str] = []
    for chord in reading.chords:
        said = chord.reading
        if alt and "alt" in chord.ending and said == chord.text:
            said = f"{chord.text} ({chord.root.words}7 for the key)"
        english_b = chord.naming.system == "letters" and "B" in (chord.root_text, chord.bass_text)
        if english_b and not chord.naming.german and reading.language != "en" and said == chord.text:
            said = f"{chord.text} (English B)"  # L2: a B elsewhere is B♭
        if said not in seen:
            seen.append(said)
    return " ".join(seen)


SHOWN_WORDS = 6  # E2: at most this many words passed through are named, then how many more


def passed(reading: Reading) -> list[str]:
    """E2's token passed through: the words and numbers kept as written, each once ("not chords: now"), at most
    SHOWN_WORDS named (review I8: they went into code's note unsaid)."""
    words = list(reading.words)
    if not words:
        return []
    more = len(words) - SHOWN_WORDS
    return [f"not chords: {', '.join(words[:SHOWN_WORDS])}" + (f" and {more} more" if more > 0 else "")]


# ---- transposing (T1 to T3, K1 to K3, P1 to P5, U1 to U4, E1, E2) ----

_STEP_NAMES = (
    "",
    "a semitone",
    "a whole tone",
    "a minor third",
    "a major third",
    "a fourth",
    "a tritone",
    "a fifth",
    "a minor sixth",
    "a major sixth",
    "a minor seventh",
    "a major seventh",
)


def step_name(semitones: int) -> str:
    """A step by its sound, with fixed names (T2): "Up 4 (a major third)", "Down 5 (a fourth)"."""
    if semitones == 0:
        return "No step (the same sounds)"
    return f"{'Up' if semitones > 0 else 'Down'} {abs(semitones)} ({_STEP_NAMES[abs(semitones)]})"


@dataclass(frozen=True)
class Moved:
    """Chords moved by a step or to a key: the text with its chords moved (lines, sections and the rest as written),
    the step by its sound, both keys as a chord symbol and in words (an estimate "likely", with its other readings),
    for a sheet with a "Capo N" line the other answer (the same shapes with the capo moved), and every reading that is
    not plain letters (E1, E2). `given` and `moved`: the chords in and out, each as written; `semitones` the step."""

    chords: str
    step: str
    from_key: str
    to_key: str
    capo: str | None
    read_as: str
    given: tuple[str, ...] = ()
    moved: tuple[str, ...] = ()
    semitones: int = 0
    spoken: str = ""  # as said on a call (E3): the step, then the chords in plain letters, labels said


def _spellings(pc: int, minor: bool, naming: Naming) -> list[KeyName]:
    """The keys on a tonic sound with at most one sharp or flat, fewest accidentals first (P1)."""
    found = [
        KeyName(Note(letter, alter), minor, naming)
        for letter in range(7)
        for alter in (-1, 0, 1)
        if (_NATURAL[letter] + alter) % 12 == pc % 12
    ]
    return sorted(found, key=lambda name: abs(name.accidentals))


def _write(chord: Chord, root: Note, bass: Note | None) -> str:
    return chord.naming.write(root) + chord.ending + (f"/{chord.naming.write(bass)}" if bass is not None else "")


def _move_all(
    chords: tuple[Chord, ...], steps: int, semitones: int, sharp: bool
) -> tuple[list[str], list[tuple[Note, Note]]]:
    """Every root and bass moved by one interval (P1, T3: the ending as written); a double accidental, F♭, E♯, C♭
    or B♯ written plainly, with the key's sign (P5), and kept to be said."""
    out: list[str] = []
    respelled: list[tuple[Note, Note]] = []

    def one(note: Note) -> Note:
        moved = note.moved(steps, semitones)
        if not moved.odd:
            return moved
        written = plain(moved.pc, sharp)
        if (moved, written) not in respelled:
            respelled.append((moved, written))
        return written

    for chord in chords:
        root = one(chord.root)
        bass = one(chord.bass) if chord.bass is not None else None
        out.append(_write(chord, root, bass))
    return out, respelled


@dataclass(frozen=True)
class _Spelled:
    """One way to move the chords: the key moved to, the interval, the chords out, the respellings."""

    target: KeyName
    steps: int
    semitones: int
    chords: list[str]
    respelled: list[tuple[Note, Note]]
    either_mode: bool = False  # U4: spelled by the sign of the step, the key moved to major or minor


def _by_semitones(source: KeyName, semitones: int, chords: tuple[Chord, ...]) -> _Spelled:
    """The tonic moved first and spelled by P1 and P2, then every chord by the interval between the tonics (T1). Of
    two keys with 6 accidentals: fewer respelled roots and basses, then the song's own side, then F♯ major or E♭
    minor (P2)."""
    candidates = _spellings(source.tonic.pc + semitones, source.minor, source.naming)
    fewest = abs(candidates[0].accidentals)
    options: list[tuple[tuple[int, int, int], _Spelled]] = []
    for target in (name for name in candidates if abs(name.accidentals) == fewest):
        steps = (target.tonic.letter - source.tonic.letter) % 7
        out, respelled = _move_all(chords, steps, semitones % 12, target.accidentals > 0)
        side = source.accidentals
        same_side = side != 0 and (side > 0) == (target.accidentals > 0)
        default = target.accidentals < 0 if source.minor else target.accidentals > 0
        rank = (len(respelled), 0 if same_side else 1, 0 if default else 1)
        options.append((rank, _Spelled(target, steps, semitones, out, respelled)))
    return min(options, key=lambda option: option[0])[1]


def _to_key(source: KeyName, target: KeyName, chords: tuple[Chord, ...]) -> _Spelled:
    """Tonic to tonic as spelled, the closer way, a tritone up (K1)."""
    steps = (target.tonic.letter - source.tonic.letter) % 7
    semitones = (target.tonic.pc - source.tonic.pc) % 12
    out, respelled = _move_all(chords, steps, semitones, target.accidentals > 0)
    return _Spelled(target, steps, semitones if semitones <= 6 else semitones - 12, out, respelled)


def _by_sign(chords: tuple[Chord, ...], semitones: int) -> list[str]:
    """U4: sharps going up, flats going down, each root and bass on its own."""
    up = semitones > 0
    out = []
    for chord in chords:
        root = plain(chord.root.pc + semitones, up)
        bass = plain(chord.bass.pc + semitones, up) if chord.bass is not None else None
        out.append(_write(chord, root, bass))
    return out


_KEY_WORDS_EN = re.compile(
    r"(?P<root>[A-Ga-g])(?:[\s-]*(?P<sign>[#♯b♭]|sharp|flat))?\s+(?P<mode>major|minor)\Z", re.IGNORECASE
)


def parse_key(text: str, song: _Song | None = None, *, naming: Naming | None = None) -> KeyName:
    """A key as written: a chord symbol (Am, C#m, Bb, SIm, Hm; its 3rd gives the mode) or English words (A minor,
    B flat major); ChordError when it is neither."""
    written = text.strip().rstrip(".,;")
    worded = _key_in_words(written)
    if worded is not None:
        return KeyName(worded.tonic, worded.minor, naming or LETTERS_NAMING)
    song = song or _settle([written], "en", written)
    words = _KEY_WORDS_EN.match(written)
    if words:
        letter = LETTERS.index(words.group("root").upper())
        sign = (words.group("sign") or "").casefold()
        alter = 1 if sign in ("#", "♯", "sharp") else -1 if sign in ("b", "♭", "flat") else 0
        tonic = Note(letter, alter)
        return KeyName(tonic, words.group("mode").casefold() == "minor", naming or LETTERS_NAMING)
    try:
        chord = chord_of(written, song)
    except ChordError:
        raise ChordError("key", f'not a key: "{shown(written)}"') from None
    hearing = heard(chord)
    minor = hearing is not None and hearing.kind in ("min", "dim")
    return KeyName(chord.root, minor, naming or chord.naming)


def _named(name: KeyName, naming: Naming) -> KeyName:
    """A key written in the song's naming."""
    return KeyName(name.tonic, name.minor, naming)


@dataclass(frozen=True)
class _Source:
    """The song's key for a move: the key itself, how it was found ("given", "line" for a "Key:" line, "estimate"),
    and for an estimate the keys also possible and its other best keys (a tie)."""

    key: KeyName
    how: str
    estimate: Estimate | None = None


def _song_key(
    reading: Reading, hearing: list[Heard], from_key: str | None, naming: Naming
) -> tuple[_Source, list[str]]:
    """The song's key (K3): `from_key` first, then the shapes a capo line names in brackets (C4), then a "Key:"
    line, then the estimate (F1, from one chord up, U1); the notes for "Read as:" (a given key whose tonic chord is
    not among the chords: what they look like)."""
    said: list[str] = []
    for how, text in (("given", from_key), ("shapes", reading.shapes), ("line", reading.key_text)):
        if text:
            name = _named(parse_key(text, reading.song), naming)
            if how == "line":
                said.append(f"the page's Key: {reading.key_text}")
            if how == "shapes":
                said.append(f"the capo line names {text} shapes")
            if hearing and not any(chord.tonic_of(name.tonic.pc, name.minor) for chord in hearing):
                look = estimate(hearing).best[0]
                said.append(f"the chords look like {look.words}")
            return _Source(name, how), said
    if not hearing:  # only chords the key cannot read (C7+): the first chord's root, as a major key
        return _Source(KeyName(reading.chords[0].root, False, naming), "estimate", Estimate((), (), 0)), said
    found = estimate(hearing)
    return _Source(found.best[0], "estimate", found), said


def _from_words(source: _Source, chosen: KeyName) -> str:
    """The song's key for E1: as given, or "likely" with the other readings (F2): the other best keys of a tie, the
    relative and the keys within 1 point, and the likely key itself when its relative was the reading picked (K2)."""
    if source.how != "estimate" or source.estimate is None or not source.estimate.best:
        return chosen.shown
    others: list[KeyName] = []
    for name in (*source.estimate.best, *(name for name, _ in source.estimate.also)):
        if not name.same_key(chosen) and not any(name.same_key(other) for other in others):
            others.append(name)
    also = f"; {' and '.join(name.words for name in others)} also possible" if others else ""
    return f"{chosen.symbol} ({chosen.words}, likely{also})"


def transpose(
    chords: str,
    semitones: int | None = None,
    to_key: str | None = None,
    from_key: str | None = None,
    *,
    language: str = "en",
    message: str = "",
) -> Moved:
    """Chords moved by `semitones` (1 to 11 up, -1 to -11 down) or to `to_key`, never both (T1 to T3, K1 to K3,
    P1 to P5, U1 to U4). Only root and bass move, endings as written; everything else in the text passes through.
    The song's key: `from_key`, then a "Key:" line, then the estimate. `language` and `message` (the user's own
    words) feed the B and H reading (L2)."""
    if semitones is not None and to_key:
        raise ChordError("both", "semitones or to_key, not both")
    if semitones is None and not to_key:
        raise ChordError("neither", "transpose needs semitones or to_key")
    if semitones is not None and (
        isinstance(semitones, bool) or not isinstance(semitones, int) or not 0 < abs(semitones) < 12
    ):
        raise ChordError("semitones", "semitones from -11 to 11, not 0")
    keys_said = " ".join(part for part in (message, from_key or "", to_key or "") if part)
    reading = read(chords, language=language, message=keys_said)
    if not reading.chords:
        raise ChordError("no_chords", "no chords to read")
    naming = reading.chords[0].naming
    hearing = [found for chord in reading.chords if (found := heard(chord)) is not None]
    if reading.fret is not None:
        return _capo_sheet(reading, hearing, semitones, to_key, from_key, naming)
    source, said = _song_key(reading, hearing, from_key, naming)
    unread = [
        f"{chord.text} (the {'+' if '+' in chord.ending else '-'} kept as written)"
        for chord in reading.chords
        if heard(chord) is None
    ]
    said = [*dict.fromkeys(unread), *said]
    if semitones is not None:
        moved, chosen, more = _move_by(reading, hearing, source, semitones)
    else:
        moved, chosen, more = _move_to(reading, source, _named(parse_key(str(to_key), reading.song), naming))
    respelled = ", ".join(f"{a.words} written {b.words}" for a, b in moved.respelled)
    tuned = [f"tuning line kept as written: {reading.tuning}"] if reading.tuning else []  # no capo line: moves nothing
    notes_said = [*more, *said] + ([respelled] if respelled else []) + tuned + _german_said(reading.song, moved.chords)
    read_as = "; ".join([chords_read(reading), *passed(reading), *notes_said])
    target = _named(moved.target, naming)
    shown_target = f"{target.symbol} ({target.tonic.words} major or minor)" if moved.either_mode else target.shown
    return Moved(
        reading.written(moved.chords),
        step_name(moved.semitones),
        _from_words(source, chosen),
        shown_target,
        None,
        read_as,
        tuple(chord.text for chord in reading.chords),
        tuple(moved.chords),
        moved.semitones,
        _spoken_move(reading, moved.semitones, moved.chords),
    )


GERMAN_NAMES = "German names: B is B♭, H is B"


def _german_said(song: _Song, chords: list[str]) -> list[str]:
    """L2 for a result: in German names a B is B♭ and an H is B, said whenever the result writes either."""
    if not song.german:
        return []
    for text in chords:
        chord = chord_of(text, song)
        if chord.root.letter == 6 or (chord.bass is not None and chord.bass.letter == 6):
            return [GERMAN_NAMES]
    return []


def _move_by(
    reading: Reading, hearing: list[Heard], source: _Source, semitones: int
) -> tuple[_Spelled, KeyName, list[str]]:
    """A step in semitones, never refused (U2): a tie whose keys spell the result alike says nothing; otherwise the
    first chord's key decides, or the last chord's; a tie of one tonic's major and minor with no 3rd anywhere: both
    readings, and when they differ sharps up and flats down (U4)."""
    found = source.estimate
    if source.how != "estimate" or found is None or not found.tie:
        return _by_semitones(source.key, semitones, reading.chords), source.key, []
    options = [(name, _by_semitones(name, semitones, reading.chords)) for name in found.best]
    if len({tuple(option.chords) for _, option in options}) == 1:
        return options[0][1], options[0][0], []
    pair = {name.tonic.pc for name in found.best}
    if len(found.best) == 2 and len(pair) == 1 and all(chord.kind == "open" for chord in hearing):
        name = found.best[0]
        tonic = plain(name.tonic.pc + semitones, semitones > 0)
        moved = _by_sign(reading.chords, semitones)
        signed = _Spelled(KeyName(tonic, False, name.naming), 0, semitones, moved, [], either_mode=True)
        direction = "sharps going up" if semitones > 0 else "flats going down"
        return signed, name, [f"key unclear ({name.tonic.words} major or minor): {direction}"]
    decider = next((name for name, _ in options if hearing[0].tonic_of(name.tonic.pc, name.minor)), None)
    if decider is None:
        decider = next((name for name, _ in options if hearing[-1].tonic_of(name.tonic.pc, name.minor)), options[0][0])
    chosen = next(option for name, option in options if name is decider)
    unclear = f"key unclear ({_or_words(found.best)}): spelled for {chosen.target.words}"
    return chosen, decider, [unclear]


def _move_to(reading: Reading, source: _Source, target: KeyName) -> tuple[_Spelled, KeyName, list[str]]:
    """To a key (K1 to K3, U3): the mode is kept; with no song key the target's mode picks between relative readings,
    with a song key of the other mode the tonic letters set the step; a target of 7 accidentals (or more) is written
    as the key that sounds the same; a tie the target's mode cannot settle is refused."""
    said: list[str] = []
    song = source.key
    found = source.estimate
    if source.how == "estimate" and found is not None and found.best:
        if found.tie:
            fitting = [name for name in found.best if name.minor == target.minor]
            if len(fitting) != 1:
                raise ChordError("key_unclear", f"song key unclear: {_or_words(found.best)}. Say the song's key.")
            song = fitting[0]
        elif song.minor != target.minor:
            relative = next((name for name, is_relative in found.also if is_relative), None)
            if relative is not None:
                song = relative
                said.append(f"read as {relative.words}")
    if song.minor != target.minor:  # K2: the song's mode is kept, said
        target = KeyName(target.tonic, song.minor, target.naming)
        said.append(f"the song is {'minor' if song.minor else 'major'}")
    if abs(target.accidentals) >= 7:  # K3: 7 accidentals or more: the key that sounds the same, with fewer
        plainer = _spellings(target.tonic.pc, target.minor, target.naming)[0]
        said.append(f"{target.tonic.words} written as {plainer.tonic.words}")
        target = plainer
    return _to_key(song, target, reading.chords), song, said


# ---- the capo (C1 to C5), guitar only ----

_FAMILIES = {False: ("C", "A", "G", "E", "D"), True: ("Am", "Em", "Dm")}  # the open-shape families
HIGH_FRET = 8  # from here a capo is marked high (C2: hard to keep in tune up there)
NEEDS = "capo needs what sounds, or the shapes and the fret"


@dataclass(frozen=True)
class Capo:
    """A guitar capo, both ways: `frets` one row per open-shape family (fret, shape, high), frets 0 to 7 first, 8 to 11
    marked high (C2), or the one fret; what is `played` (the shapes, or the chords played at the first row's fret);
    what `sounds`, spelled by its own key (C3); `summary` in words; the readings."""

    frets: tuple[tuple[int, str, bool], ...]
    played: str
    sounds: str
    read_as: str
    summary: str

    @property
    def spoken(self) -> str:
        """As said on a call: the summary, sharps and flats in words."""
        return speech(self.summary)


@dataclass(frozen=True)
class _Side:
    """One side of a capo: a key, and its chords when it was given as chords."""

    key: KeyName
    reading: Reading | None = None
    said: tuple[str, ...] = ()
    written: str = ""  # a key as the user or Gemma wrote it ("B-Dur")

    @property
    def chords(self) -> tuple[Chord, ...]:
        return self.reading.chords if self.reading is not None else ()


def _side(text: str, language: str, message: str) -> _Side:
    """A key (one chord symbol or key words in any of the 11 languages: "La menor", "fis klein", "ля минор") or
    chords, with their key: a "Key:" line, else the estimate."""
    one = text.strip()
    plain = one.rstrip(".,;")
    worded = _KEY_WORDS_EN.match(plain) or _key_in_words(plain) is not None  # review C4: never read as chords
    if "\n" not in one and (" " not in one or worded):
        return _Side(parse_key(one, _settle([one], language, message)), written=one)
    reading = read(text, language=language, message=message)
    if not reading.chords:
        raise ChordError("no_chords", "no chords to read")
    hearing = [found for chord in reading.chords if (found := heard(chord)) is not None]
    naming = reading.chords[0].naming
    source, said = _song_key(reading, hearing, None, naming)
    return _Side(source.key, reading, tuple(said))


def _shifted(side: _Side, semitones: int) -> tuple[KeyName, list[str]]:
    """A side moved by a number of half steps, spelled by its own key (C3): the key and the chords."""
    if semitones % 12 == 0:
        return side.key, [chord.text for chord in side.chords]
    moved = _by_semitones(side.key, semitones, side.chords)
    return _named(moved.target, side.key.naming), moved.chords


def _heard_text(key: KeyName, chords: list[str]) -> str:
    return f"{' '.join(chords)} ({key.words})" if chords else key.words


def _frets_text(rows: tuple[tuple[int, str, bool], ...]) -> str:
    def one(fret: int, shape: str) -> str:
        return f"{shape} shapes with no capo" if fret == 0 else f"{shape} shapes at capo {fret}"

    low = [one(fret, shape) for fret, shape, high in rows if not high]
    high = [one(fret, shape) for fret, shape, high in rows if high]
    return ", ".join(low) + (f"; high up: {', '.join(high)}" if high else "")


def _with_capo(fret: int, played: str) -> str:
    """A summary's start: "Capo 3 with G shapes", and at fret 0 "G shapes with no capo" (C2: 0 is no capo, never
    "Capo 0")."""
    return f"{played} with no capo" if fret == 0 else f"Capo {fret} with {played}"


def _drop_said(drop: int) -> str:
    return {1: "tuned down a half step", 2: "tuned down a whole step"}.get(drop, f"tuned down {drop} half steps")


def capo(
    sounds: str | None = None,
    fret: int | None = None,
    shapes: str | None = None,
    *,
    language: str = "en",
    message: str = "",
) -> Capo:
    """A guitar capo, both ways (C1 to C5): `sounds` alone (a key or chords: the fret for each open-shape family),
    `sounds` and `shapes` (the fret), `sounds` and `fret` (the shapes), or `shapes` and `fret` (what sounds). A
    "Capo N" line in the shapes is the fret; a "Key:" line there is matched to the shapes or the key heard (C5); a
    tuning line moves what sounds only when it lowers every string alike (C4)."""
    if fret is not None and (isinstance(fret, bool) or not isinstance(fret, int) or not 0 <= fret <= 11):
        raise ChordError("fret", "fret from 0 to 11")
    shape_side = _side(shapes, language, message) if shapes else None
    reading = shape_side.reading if shape_side is not None else None
    if fret is None and reading is not None and reading.fret is not None:
        fret = reading.fret
    given = [bool(sounds), fret is not None, shape_side is not None]
    if all(given):
        raise ChordError("capo_two", "capo takes two of: what sounds, the shapes, the fret")
    if not given[0] and not (given[1] and given[2]):
        raise ChordError("capo_needs", NEEDS)
    drop = reading.drop if reading is not None else 0
    tuned = [_drop_said(drop)] if drop else []
    if shape_side is not None and fret is not None:  # what sounds (C1, C3, C5)
        shape_side = _matched(shape_side, fret - drop)
        heard_key, heard_chords = _shifted(shape_side, fret - drop)
        played = " ".join(chord.text for chord in shape_side.chords) or shape_side.key.symbol
        with_what = played if shape_side.chords else f"{played} shapes"
        verb = f"sounds {_heard_text(heard_key, heard_chords)}" if heard_chords else f"sounds in {heard_key.words}"
        summary = f"{_with_capo(fret, with_what)}: {verb}."
        read_as = _capo_read_as(shape_side, tuned)
        return Capo(
            ((fret, shape_side.key.symbol, fret >= HIGH_FRET),),
            played,
            _heard_text(heard_key, heard_chords),
            read_as,
            summary,
        )
    assert sounds is not None
    sound_side = _side(sounds, language, message)
    heard_text = _heard_text(sound_side.key, [chord.text for chord in sound_side.chords])
    if shape_side is not None:  # the fret for these shapes
        at = (sound_side.key.tonic.pc - shape_side.key.tonic.pc + drop) % 12
        played = " ".join(chord.text for chord in shape_side.chords) or shape_side.key.symbol
        with_what = played if shape_side.chords else f"{played} shapes"
        summary = f"{_with_capo(at, with_what)}: sounds in {sound_side.key.words}."
        read_as = _capo_read_as(sound_side, [*tuned, *_capo_read_as(shape_side, []).split("; ")])
        return Capo(((at, shape_side.key.symbol, at >= HIGH_FRET),), played, heard_text, read_as, summary)
    if fret is not None:  # the shapes at this fret
        shape_key, shape_chords = _shifted(sound_side, -fret)
        played = " ".join(shape_chords) or shape_key.symbol
        what = f"{played} ({shape_key.symbol} shapes)" if shape_chords else f"{played} shapes"
        summary = f"For {heard_text} {'with no capo' if fret == 0 else f'at capo {fret}'}: {what}."
        return Capo(
            ((fret, shape_key.symbol, fret >= HIGH_FRET),), played, heard_text, _capo_read_as(sound_side, []), summary
        )
    rows = _family_rows(sound_side.key)
    first_fret, first_shape, _ = rows[0]
    shape = KeyName(chord_of(first_shape.rstrip("m")).root, sound_side.key.minor, sound_side.key.naming)
    played = first_shape
    summary = f"For {sound_side.key.words}: {_frets_text(rows)}."
    if sound_side.chords:
        moved, _ = _move_all(
            sound_side.chords,
            (shape.tonic.letter - sound_side.key.tonic.letter) % 7,
            (shape.tonic.pc - sound_side.key.tonic.pc) % 12,
            shape.accidentals > 0,
        )
        played = " ".join(moved)
        where = "with no capo" if first_fret == 0 else f"at capo {first_fret}"
        summary += f" {where.capitalize()} with {first_shape} shapes: {played}."
    return Capo(rows, played, heard_text, _capo_read_as(sound_side, []), summary)


def _family_rows(key: KeyName) -> tuple[tuple[int, str, bool], ...]:
    """Each open-shape family's fret for a key heard: (key - shape) mod 12, frets 0 to 7 first (C2)."""
    rows = []
    for shape in _FAMILIES[key.minor]:
        at = (key.tonic.pc - chord_of(shape.rstrip("m")).root.pc) % 12
        rows.append((at, shape, at >= HIGH_FRET))
    return tuple(sorted(rows, key=lambda row: (row[2], row[0])))


def _matched(side: _Side, up: int, given_text: str = "") -> _Side:
    """C5: a key the user gives (`given_text`) or a "Key:" line on a capo sheet is the shapes' key when it fits the
    chords as written, the key heard when it fits them moved up by the fret; when it fits neither, the result says
    what the chords look like (and the key is taken as the shapes')."""
    reading = side.reading
    text = given_text or (reading.key_text if reading is not None and not reading.shapes else "")
    if reading is None or not text:
        return side
    hearing = [found for chord in reading.chords if (found := heard(chord)) is not None]
    given = _named(parse_key(text, reading.song), reading.chords[0].naming)
    named = f"the given key {text}" if given_text else f"the page's Key: {text}"
    said = [note for note in side.said if not note.startswith(("the page's Key:", "the chords look like"))]
    if any(chord.tonic_of(given.tonic.pc, given.minor) for chord in hearing):
        return _Side(given, reading, (*said, f"{named} is the shapes' key"))
    below = (given.tonic.pc - up) % 12
    tonic = next((chord for chord in hearing if chord.tonic_of(below, given.minor)), None)
    if tonic is not None:
        shapes_key = KeyName(tonic.chord.root, given.minor, given.naming)
        return _Side(shapes_key, reading, (*said, f"{named} is the key heard"))
    look = estimate(hearing).best[0] if hearing else given
    return _Side(given, reading, (*said, named, f"the chords look like {look.words}"))


def _capo_read_as(side: _Side, more: list[str]) -> str:
    if side.reading is not None:
        first = [chords_read(side.reading), *passed(side.reading)]
    else:
        first = [f"{side.written} ({side.key.words})"]
    return "; ".join(part for part in (*first, *side.said, *more) if part)


def _same_shapes(fret: int) -> str:
    """The other answer for a capo sheet (C4), in the words the capo's rows use ("with no capo", never capo 0)."""
    return "the same shapes with no capo" if fret == 0 else f"the same shapes at capo {fret}"


def _capo_sheet(
    reading: Reading,
    hearing: list[Heard],
    semitones: int | None,
    to_key: str | None,
    from_key: str | None,
    naming: Naming,
) -> Moved:
    """A sheet with a "Capo N" line shows shapes (C4): moving it gives new shapes at the same fret, or the same shapes
    with the capo moved (a new fret 0 to 7); a key to move to is the key heard (C5); the summary speaks of what
    sounds."""
    fret = reading.fret or 0
    up = fret - reading.drop
    source, said = _song_key(reading, hearing, from_key, naming)
    side = _matched(_Side(source.key, reading, tuple(said)), up, from_key or "")
    shapes = side.key
    heard_key, _ = _shifted(_Side(shapes), up)
    notes_said: list[str] = list(side.said)
    if semitones is not None:
        step = semitones
        target_heard, _ = _shifted(_Side(heard_key), step)
    else:
        target = _named(parse_key(str(to_key), reading.song), naming)
        moved_to, _, more = _move_to(Reading((), (), reading.song), _Source(heard_key, "given"), target)
        notes_said += more
        step, target_heard = moved_to.semitones, moved_to.target
    if step % 12:
        new_shapes = _by_semitones(shapes, step, reading.chords)
        shape_chords, new_key, respelled = new_shapes.chords, _named(new_shapes.target, naming), new_shapes.respelled
    else:
        shape_chords, new_key, respelled = [chord.text for chord in reading.chords], shapes, []
    other = fret + step
    likely = ", likely" if source.how == "estimate" and not side.said else ""
    respelled_said = ", ".join(f"{a.words} written {b.words}" for a, b in respelled)
    notes_said.append(f"capo {fret}: shapes in {shapes.words}, now {new_key.words}")
    if reading.drop:
        notes_said.append(_drop_said(reading.drop))
    if respelled_said:
        notes_said.append(respelled_said)
    notes_said += _german_said(reading.song, shape_chords)
    return Moved(
        reading.written(shape_chords, new_key.symbol),
        step_name(step),
        f"{heard_key.symbol} ({heard_key.words}{likely}, heard with capo {fret})",
        _named(target_heard, naming).shown,
        _same_shapes(other) if 0 <= other < HIGH_FRET and other != fret else None,
        "; ".join([chords_read(reading), *passed(reading), *notes_said]),
        tuple(chord.text for chord in reading.chords),
        tuple(shape_chords),
        step,
        _spoken_move(reading, step, shape_chords, new_key.symbol),
    )


# ---- key words in 11 languages (L4) ----

_GERMAN_LETTERS = "CDEFGAH"  # H is B; B (German, Polish) is B♭
_JA_NAMES = "ハニホヘトイロ"  # C to B
_KO_NAMES = "다라마바사가나"  # C to B
_SYLLABLES = {"do": 0, "dó": 0, "re": 1, "ré": 1, "mi": 2, "fa": 3, "fá": 3, "sol": 4, "la": 5, "lá": 5, "si": 6}
_RU_SYLLABLES = {"до": 0, "ре": 1, "ми": 2, "фа": 3, "соль": 4, "ля": 5, "си": 6}
_SHARP_WORDS = frozenset({"sharp", "dièse", "sostenido", "diesis", "sustenido", "диез", "#", "♯"})
_FLAT_WORDS = frozenset({"flat", "bémol", "bemol", "bemolle", "бемоль", "b", "♭"})
_MINOR_WORDS = frozenset({"minor", "moll", "klein", "mineur", "menor", "minore", "минор", "短調", "단조"})
_ROMANCE_MODES = "majeur|mineur|mayor|menor|maggiore|minore|maior"
# one pattern per way of writing a key; no repetition inside a repeated group (G3)
KEY_PHRASE = re.compile(
    r"(?<![\w])(?:"
    r"(?P<de>[A-Ha-h](?:is|es|s)?)[\s-](?P<de_mode>Dur|Moll|dur|moll)"
    r"|(?P<nl>[A-Ga-g](?:is|es|s)?)[\s-](?P<nl_mode>groot|klein|majeur|mineur)"
    r"|(?P<en>[A-Ga-g])(?:[\s-]?(?P<en_sign>[#♯b♭]|sharp|flat))?\s(?P<en_mode>major|minor)"  # "B-flat minor"
    rf"|(?P<ro>do|dó|ré|re|mi|fa|fá|sol|la|lá|si)(?:\s(?P<ro_sign>dièse|bémol|sostenido|bemolle|bemol|diesis|sustenido))?\s(?P<ro_mode>{_ROMANCE_MODES})"
    r"|(?P<ru>до|ре|ми|фа|соль|ля|си)(?:[\s-](?P<ru_sign>диез|бемоль))?\s(?P<ru_mode>мажор|минор)"
    r")(?![\w])"
    r"|(?P<ja_sign>嬰|変)?(?P<ja>[ハニホヘトイロ])(?P<ja_mode>長調|短調)"
    r"|(?P<ko_sign>올림|내림)?\s?(?P<ko>[다라마바사가나])\s?(?P<ko_mode>장조|단조)",
    re.IGNORECASE,
)
_FROM_BEFORE = re.compile(r"(?:\b(?:from|von|van|de|desde|da|dal|dalla|dallo)|(?<![\w])(?:из|от))\s+\Z", re.IGNORECASE)
_FROM_AFTER = ("から", "에서")
# the words that may follow a do-re-mi key ("la menor y mi menor", "a la menor, por favor"): and, or, nor, to, for,
# but, with ("si bémol majeur avec des formes de Sol", review I5); any other lower-case word makes it everyday words
# ("la menor idea", "la mayor parte", "do maior empresário")
_AFTER_ROMANCE_KEY = frozenset(
    {"y", "e", "o", "u", "ou", "et", "ed", "ni", "né", "nem", "a", "al", "ao", "au", "à", "para", "per", "pour",
     "por", "par", "ma", "mas", "mais", "pero", "avec", "con", "com"}
)  # fmt: skip
_NEXT_WORD = re.compile(r"\s*([^\W\d_][\w'’-]*)")


def is_key(match: re.Match[str], text: str) -> bool:
    """Whether a key phrase in running text (the user's message, her words) is a key: an English one is not the
    article "a" ("a minor change" is none; "e minor" and "A minor" are keys), and a do-re-mi one is not followed by a
    lower-case word other than and, or, to, for or but: "la menor idea", "la mayor parte", "mi mayor consejo" and "do
    maior empresário" are everyday Spanish, Italian and Portuguese (review C2)."""
    if match.group("en"):
        return match.group("en") != "a"
    if match.group("ro"):
        after = _NEXT_WORD.match(text, match.end())
        if after is not None and after.group(1)[0].islower():
            return after.group(1).casefold() in _AFTER_ROMANCE_KEY
    return True


def _named_note(name: str, *, german: bool) -> Note:
    """A German, Dutch or Polish note name (Cis, Es, As, Bes, B, H): B is B♭ in German and Polish (`german`)."""
    lowered = name.casefold()
    first = lowered[0].upper()
    letter = 6 if first in "BH" else LETTERS.index(first)
    rest = lowered[1:]
    if rest == "is":
        alter = 1
    elif rest in ("es", "s"):
        alter = -1
    else:
        alter = -1 if first == "B" and german else 0
    return Note(letter, alter)


def _key_in_words(text: str) -> KeyName | None:
    """A key written in words (L4), the whole text: "B-Dur", "fis klein", "si bémol majeur", "ля минор", "変ロ長調",
    "올림바단조", "B flat major"; None when it is none."""
    found = KEY_PHRASE.fullmatch(text.strip())
    return _phrase_key(found) if found is not None else None


def _phrase_key(found: re.Match[str]) -> KeyName:
    groups = found.groupdict()
    if groups["de"]:
        mode = groups["de_mode"].casefold()
        return KeyName(_named_note(groups["de"], german=True), mode == "moll")
    if groups["nl"]:
        return KeyName(_named_note(groups["nl"], german=False), groups["nl_mode"].casefold() in _MINOR_WORDS)
    if groups["en"]:
        sign = (groups["en_sign"] or "").casefold()
        alter = 1 if sign in _SHARP_WORDS else -1 if sign in _FLAT_WORDS else 0
        return KeyName(Note(LETTERS.index(groups["en"].upper()), alter), groups["en_mode"].casefold() == "minor")
    if groups["ro"]:
        sign = (groups["ro_sign"] or "").casefold()
        alter = 1 if sign in _SHARP_WORDS else -1 if sign in _FLAT_WORDS else 0
        return KeyName(Note(_SYLLABLES[groups["ro"].casefold()], alter), groups["ro_mode"].casefold() in _MINOR_WORDS)
    if groups["ru"]:
        sign = (groups["ru_sign"] or "").casefold()
        alter = 1 if sign in _SHARP_WORDS else -1 if sign in _FLAT_WORDS else 0
        return KeyName(Note(_RU_SYLLABLES[groups["ru"].casefold()], alter), groups["ru_mode"].casefold() == "минор")
    if groups["ja"]:
        alter = {"嬰": 1, "変": -1}.get(groups["ja_sign"] or "", 0)
        return KeyName(Note(_JA_NAMES.index(groups["ja"]), alter), groups["ja_mode"] == "短調")
    alter = {"올림": 1, "내림": -1}.get(groups["ko_sign"] or "", 0)
    return KeyName(Note(_KO_NAMES.index(groups["ko"]), alter), groups["ko_mode"] == "단조")


@dataclass(frozen=True)
class MessageKey:
    """A key in words in the user's own message (L4): the key, its words as written, and whether it is the key moved
    "from" (after from, von, de, из, before から or 에서) or the one moved "to"."""

    key: KeyName
    written: str
    role: str


def keys_in(message: str) -> list[MessageKey]:
    """The keys written in words in the user's message, in order (L4): code reads them itself, so a key Gemma
    translated or misread is never the one used."""
    found: list[MessageKey] = []
    message = message[:MAX_CHARS]
    for match in KEY_PHRASE.finditer(message):
        if not is_key(match, message):
            continue  # "a minor change", "la menor idea"
        written = match.group(0).strip()
        before = message[max(0, match.start() - 12) : match.start()]
        after = message[match.end() : match.end() + 3]
        role = "from" if _FROM_BEFORE.search(before) or after.startswith(_FROM_AFTER) else "to"
        found.append(MessageKey(_phrase_key(match), written, role))
    return found


# ---- as said on a call (E3, design 3.7) ----

_NUMBER_WORDS = {2: "twice", 3: "three times", 4: "four times"}


def _ascii_chord(root: Note, ending: str, bass: Note | None) -> str:
    """A chord in plain letters with # and b, its ending as written: what both speech engines read."""

    def name(note: Note) -> str:
        return LETTERS[note.letter] + _signs(note.alter, unicode=False)

    return name(root) + ending + (f"/{name(bass)}" if bass is not None else "")


def letters_of(chord: Chord) -> str:
    """A chord's root in plain letters with # and b ("B" for a German H, "G" for SOL)."""
    return LETTERS[chord.root.letter] + _signs(chord.root.alter, unicode=False)


def spoken_note(note: Note) -> str:
    """A note in English words: "E flat", "F sharp", "B double flat"."""
    signs = {1: " sharp", 2: " double sharp", -1: " flat", -2: " double flat"}
    return LETTERS[note.letter] + signs.get(note.alter, "")


_SIGNED = re.compile(r"\b([A-G])([♯♭𝄪𝄫])")


def speech(text: str) -> str:
    """Words for speech: "B♭ major" as "B flat major" (a chord symbol stays: the speech engines read those)."""
    words = {"♯": " sharp", "♭": " flat", "𝄪": " double sharp", "𝄫": " double flat"}
    return _SIGNED.sub(lambda match: match.group(1) + words[match.group(2)], text)


def _spoken_move(reading: Reading, semitones: int, chords: list[str], shapes: str = "") -> str:
    """A move as said on a call: the step by its sound, then the chords in plain letters with their section labels;
    separators, grid marks, a "Key:" line and words passed through are not said, a repeat mark is ("twice"), N.C. is
    "no chord", a capo line's new shapes are ("Capo 3, A shapes") (design 3.7)."""
    step = step_name(semitones).replace(" (", ", ").rstrip(")")
    lines: list[list[str]] = [[]]
    moved = iter(chords)
    for piece in reading.pieces:
        if piece.chord is not None:
            chord = chord_of(next(moved), reading.song)
            lines[-1].append(_ascii_chord(chord.root, chord.ending, chord.bass))
        elif piece.kind == "space":
            if "\n" in piece.text:
                lines.append([])
        elif piece.kind == "label":
            label = piece.text.strip("[]{}()")
            if label.casefold() != "key:":
                lines[-1].append(label if label.endswith(":") else label + ":")
        elif piece.kind == "repeat":
            count = int(re.findall(r"\d+", piece.text)[0])
            lines[-1].append(_NUMBER_WORDS.get(count, f"{count} times"))
        elif piece.kind == "nc":
            lines[-1].append("no chord")
        elif piece.kind == "capo":
            lines[-1].append(_SAID_BRACKET.sub(r", \1", _with_shapes(piece.text, shapes)).strip())
        elif piece.kind == "tuning":
            lines[-1].append(piece.text.strip())
    said: list[str] = []
    for words in (" ".join(line) for line in lines):
        if not words:
            continue
        if said and said[-1].endswith(":"):  # a label on a line of its own goes with the chords after it
            said[-1] = f"{said[-1]} {words}"
        else:
            said.append(words)
    return f"{step}: {'. '.join(said)}."
