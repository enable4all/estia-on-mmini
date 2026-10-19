"""The [chords] extra around theory.py: Gemma's tool, its results as code's fixed lines (design 3.5 to 3.7).

The chord work is code's (theory.py); the chords reach the user in a note from the code, never retyped by Gemma.
Chords from a page are data: the tool computes and starts nothing. Logs hold the action, a count, the time and an
error code, never chords, keys, song names or sources.
"""

from __future__ import annotations

import bisect
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from local_assistant import prompts, theory
from local_assistant.theory import ChordError
from local_assistant.web_search import WebResults

log = logging.getLogger(__name__)

ACTIONS = ("transpose", "capo", "notes", "key")
NAME = "chords"  # never music_…: that prefix is Spotify's handler, which refuses calls

# One tool, not four: every description is in every request, calls included (AGENTS.md, Gotchas)
CHORDS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME,
        # the capo's example and its own place for the key to sound in (to_key): without them a capo call got
        # chords "G" with shapes "G" ("sound in Bb, playing G shapes"), and "B-Dur mit G-Griffen" a key call or a web
        # search (eval of 18 Oct)
        "description": (
            "Chord arithmetic on given chords: transpose; capo, the fret for a key and shapes (to_key B-Dur, shapes "
            "G); a chord's notes; the key of given chords. Names exactly as the user or the site wrote them, never "
            "translated (H-Dur, H7, LAm, SIb). The bridge gives the chords itself."
        ),
        "parameters": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "chords": {
                    "type": "string",
                    "description": "the chords as written, sections too, e.g. Verse: Am F C G (notes: one chord)",
                },
                "from_key": {"type": "string", "description": "transpose: the song's key as written, if given"},
                "semitones": {
                    "type": "integer",
                    "description": "transpose: half steps, negative is down; not with to_key",
                },
                "to_key": {
                    "type": "string",
                    "description": "transpose: the key to move to; capo: the key to sound in; as written; not with "
                    "semitones",
                },
                "fret": {"type": "integer", "description": "capo: the fret, if given"},
                "shapes": {"type": "string", "description": "capo: the shapes played, a key or chords"},
            },
        },
    },
}
# after the lines, for Gemma only: the lines reach the user as code's note
FOR_GEMMA = (
    "(The bridge sends these lines to the user itself, word for word. Say in a few words what was done; never write "
    "the chords out.)"
)
_PAGE_KEY = re.compile(r"\bKey:\s?([A-H][^\s.,;]{0,5}(?:\s(?:flat|sharp))?(?:\s(?:major|minor))?)", re.IGNORECASE)
_WORDS = re.compile(r"[^\s·|,.;:()\[\]]+")


@dataclass(frozen=True)
class Result:
    """One chords call: its action, code's fixed lines (or the error's), what a call says, the chords it was given
    and the ones it gave (each as written), the web page that holds the given chords (site, link), and the error
    code ("" when it worked)."""

    action: str
    lines: str
    spoken: str
    given: tuple[str, ...] = ()
    chords: tuple[str, ...] = ()
    source: tuple[str, str] | None = None
    error: str = ""
    problem: str = ""  # the error in plain words, for Gemma
    # a refused chord as written, when it is the user's or a page's: always in code's note, even after a call that
    # worked (`told`); "" for any other error, and for a chord she mistyped herself
    unread: str = ""
    # every chord it names, given or given back, as (root as spelled, its ending's name); a note or a key by its root
    # alone (None): what her words are checked against
    known: tuple[tuple[theory.Note, str | None], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def for_gemma(self) -> str:
        """The tool's result: the lines and what to do with them, or the error."""
        return f"Error: {self.problem}" if self.error else f"{self.lines}\n{FOR_GEMMA}"

    @property
    def moved(self) -> bool:
        """A transpose that worked: the call's message after it is sent only when one ran."""
        return self.ok and self.action == "transpose"


# the user's words for the errors that name Gemma's fields (her own error text keeps them: she fills the fields)
_PLAIN_ERRORS = {
    "both": "a step and a key to move to, not both",
    "neither": "no step and no key to move to",
    "semitones": "a step of 1 to 11 half steps, up or down",
    "action": "not one of transpose, capo, notes or key",
}


def error_note(error: ChordError) -> str:
    """Code's words for a chords call that did not work (M5): "Not worked out: "Hm7b" is not a chord."; in plain
    words, never a field's name, and one full stop."""
    found = re.fullmatch(r'not a chord: "(.*)"( \((.*)\))?', str(error))
    if found:
        why = f" ({found.group(3)})" if found.group(3) else ""
        return f'Not worked out: "{found.group(1)}" is not a chord{why}.'
    return f"Not worked out: {_PLAIN_ERRORS.get(error.code, str(error)).rstrip('.')}."


def _whole(value: object, code: str, message: str) -> int | None:
    """A small whole number as Gemma gives it (4, "4", "+4", 4.0); never a long run of digits made a number (G2)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ChordError(code, message)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer() and abs(value) < 100:
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\s*[+-]?\d{1,2}\s*", value):
        return int(value)
    raise ChordError(code, message)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _agrees(written: str, key: theory.KeyName, song: theory._Song) -> bool:
    """Whether a key argument names the same key as the user's words (its spelling and its mode), read in the naming
    the work itself uses (`song`): "Bm" beside h-Moll is B♭ minor, as the move reads it (review C5)."""
    try:
        found = theory.parse_key(written, song)
    except ChordError:
        return False
    return (found.tonic, found.minor) == (key.tonic, key.minor)


def _misreads(written: str, key: theory.KeyName, song: theory._Song) -> bool:
    """Whether a key argument is a misreading of the user's key (`_agrees` said it is not that key): the same letter
    or the same sound ("B" read as B♭ for H-Dur, "F#" for F♯ minor, "Gb" for Fis-Dur), or a key code cannot read."""
    try:
        found = theory.parse_key(written, song)
    except ChordError:
        return True
    return found.tonic.letter == key.tonic.letter or found.tonic.pc == key.tonic.pc


def page_of(given: Sequence[str], web: Sequence[WebResults], language: str = "en") -> tuple[str, str, str] | None:
    """The web result whose text holds the most of the chords the tool was given: (site, link, text); none, or a
    tie, None (design 3.7). A page's chords are read as her words are (`read`, in the conversation's language and in
    English), so a word such as the article "A" is never a hit."""
    wanted = set(given)
    scored: list[tuple[int, str, str, str]] = []
    for search in web:
        for result in search.results:
            page = f"{result.title}\n{result.text}"
            words = {mention.text for reading in {language, "en"} for mention in read(page, reading, lines=True)}
            hits = len(wanted & words)
            if hits:
                scored.append((hits, result.site, result.url, result.text))
    if not scored:
        return None
    scored.sort(key=lambda row: -row[0])
    if len(scored) > 1 and scored[1][0] == scored[0][0] and scored[1][2] != scored[0][2]:
        return None
    _, site, url, text = scored[0]
    return site, url, text


class ChordTool:
    """Gemma's chords tool: theory.py's four actions, the user's own key words over her arguments (L4), the page's
    "Key:" line read by code (K3), code's fixed lines (3.6)."""

    def __init__(self, language: Callable[[], str] = lambda: "en", clock: Callable[[], float] = time.monotonic) -> None:
        self._language = language  # the conversation's (/language): one input to the B and H reading (L2)
        self._clock = clock

    def run(
        self,
        arguments: Mapping[str, Any],
        message: str,
        web: Sequence[WebResults] = (),
        earlier: Sequence[Result] = (),
    ) -> Result:
        """One call; a chord, key or field that cannot be used gives an error Result, never an exception.
        `earlier`: this answer's calls before it, whose refusals of the user's chords its "Read as:" accounts for; a
        call of the same action that puts another chord in place of one of them is refused (a guess)."""
        started = self._clock()
        action = _text(arguments.get("action")).casefold()
        try:
            if action not in ACTIONS:
                raise ChordError("action", "action must be transpose, capo, notes or key")
            result = self._run(action, arguments, message, web)
        except ChordError as exc:
            log.info("chords: %s, error %s, %d ms", action or "none", exc.code, (self._clock() - started) * 1000)
            unread = _unread(exc, _texts(message, web))
            note = error_note(exc)
            if unread:  # the user's spelling in code's note, never hers
                note = re.sub(r'"(.*?)"', lambda _: f'"{unread}"', note, count=1)
            return Result(
                action or "none",
                note,
                note,
                tuple(_WORDS.findall(_text(arguments.get("chords")))),
                error=exc.code,
                problem=str(exc),
                unread=unread,
            )
        texts = _texts(message, web)
        guessed = _guessed(result, earlier, texts)
        if guessed:  # review I4: a chord that cannot be read is never guessed, also when she tries a changed one
            log.info("chords: %s, error guessed, %d ms", action, (self._clock() - started) * 1000)
            problem = (
                f"{guessed} could not be read, and a chord in its place is a guess: leave it out, or ask the user "
                "which chord it is"
            )
            note = f"Not worked out: {problem}."
            return Result(action, note, note, result.given, error="guessed", problem=problem)
        log.info("chords: %s, %d chords, %d ms", action, len(result.given), (self._clock() - started) * 1000)
        return _accounted(result, earlier, texts)

    def _run(self, action: str, arguments: Mapping[str, Any], message: str, web: Sequence[WebResults]) -> Result:
        language = self._language()
        chords = _text(arguments.get("chords"))
        from_key = _text(arguments.get("from_key"))
        to_key = _text(arguments.get("to_key"))
        shapes = _text(arguments.get("shapes"))
        said: list[str] = []
        found = theory.keys_in(message)
        if action == "notes":
            notes = theory.notes(chords, language=language, message=message)
            lines = f"{notes.chord}: {' '.join(notes.notes)}" + (f" {notes.remark}" if notes.remark else "")
            plain = tuple(note.split(" (")[0] for note in notes.notes)
            song = theory.read(chords, language=language, message=message).song
            known = (*_known([notes.chord], song), *((root, None) for root, _ in _known(plain, song)))
            return Result(
                action, f"{lines}\nRead as: {notes.read_as}", notes.spoken, (notes.chord,), plain, known=known
            )
        if action == "key":
            key = theory.key(chords, language=language, message=message)
            reading = theory.read(chords, language=language, message=message)
            given = tuple(chord.text for chord in reading.chords)
            named = tuple(re.findall(r"(\S+) \(", " ".join((key.key or "", *key.also))))
            known = (*_known(given, reading.song), *((root, None) for root, _ in _known(named, reading.song)))
            return Result(action, f"{key.summary}\nRead as: {key.read_as}", key.spoken, given, named, known=known)
        if action == "capo":
            # the key to sound in: to_key, as a move's target (eval of 18 Oct), or from_key as before; chords beside
            # to_key are the chords played
            wanted = to_key or ("" if chords else from_key)
            if found and (to_key or not chords):  # what sounds, in the user's own words ("B-Dur mit G-Griffen")
                song = _song_of([wanted], language, " ".join((message, wanted)))
                wanted, said = self._mine(wanted, found, said, song, misread_only=True)
            sheet = bool(chords) and _capo_line(chords, language, message)
            if chords and not shapes and (sheet or wanted):  # a sheet with a "Capo N" line shows shapes (C4)
                chords, shapes = "", chords
            fret = _whole(arguments.get("fret"), "fret", "fret from 0 to 11")
            sounds = wanted or chords
            if sounds and shapes and fret is None:
                _other_key(sounds, shapes, message, language)
            capo = theory.capo(sounds or None, fret, shapes or None, language=language, message=message)
            read_as = "; ".join([capo.read_as, *said])
            given = tuple(dict.fromkeys(_WORDS.findall(f"{chords} {wanted} {sounds} {shapes}")))
            out = tuple(_WORDS.findall(f"{capo.played} {capo.sounds.split(' (')[0]}"))
            shapes_named = [shape for _, shape, _ in capo.frets]
            # each side in its own naming: the user's do re mi beside the letters of the shapes is no mix (review I3)
            known = (
                *_known(given, _song_of(given, language, message)),
                *_known(out, _song_of(out, language, message)),
                *((root, None) for root, _ in _known(shapes_named, _song_of(shapes_named, language, message))),
            )
            return Result(action, f"{capo.summary}\nRead as: {read_as}", capo.spoken, given, out, known=known)
        semitones = _whole(arguments.get("semitones"), "semitones", "semitones from -11 to 11, not 0")
        song = _move_song(chords, language, " ".join(part for part in (message, from_key, to_key) if part))
        # code reads key words in the user's message itself and uses them (L4, I4); several keys of a role: hers stays
        # when it is one of them ("to C minor; it is in G major now", review I1)
        froms = [mine for mine in found if mine.role == "from"]
        tos = [mine for mine in found if mine.role == "to"]
        if froms:
            from_key, said = self._mine(from_key, froms, said, song)
        if tos and to_key:
            # the key the chords are in now is no key to move to ("this song is in G major; put it in Bb"), unless
            # hers misreads it (review C1's twin: her Bb gave "No step")
            now = _keys_now(chords, from_key, language, message)
            tos = [
                mine
                for mine in tos
                if not any(mine.key.same_key(key) for key in now) or _misreads(to_key, mine.key, song)
            ]
        if tos and to_key:
            to_key, said = self._mine(to_key, tos, said, song)
        given = tuple(chord.text for chord in theory.read(chords, language=language, message=message).chords)
        page = page_of(given, web, language)
        if not from_key and page is not None and "key:" not in chords.casefold():  # the page's own "Key:" line
            line = _PAGE_KEY.search(page[2])
            if line is not None and _parses(line.group(1)):
                from_key = line.group(1)
                said.append(f"the page's Key: {from_key}")
        moved = theory.transpose(
            chords, semitones, to_key or None, from_key or None, language=language, message=message
        )
        read_as = "; ".join([moved.read_as, *said])
        lines = f"{moved.step}: from {moved.from_key} to {moved.to_key}\n{moved.chords}"
        if moved.capo:
            lines += f"\nOr: {moved.capo}"
        song = theory.read(chords, language=language, message=" ".join((message, from_key, to_key))).song
        keys = re.findall(r"(\S+) \(", f"{moved.from_key} {moved.to_key}")
        known = (*_known([*moved.given, *moved.moved], song), *((root, None) for root, _ in _known(keys, song)))
        return Result(
            action,
            f"{lines}\nRead as: {read_as}",
            moved.spoken,
            moved.given,
            moved.moved,
            (page[0], page[1]) if page is not None else None,
            known=known,
        )

    @staticmethod
    def _mine(
        argument: str,
        keys: Sequence[theory.MessageKey],
        said: list[str],
        song: theory._Song,
        *,
        misread_only: bool = False,
    ) -> tuple[str, list[str]]:
        """The user's own key words over an argument that agrees with none of them (then the first of them), said
        in "Read as:". `misread_only` (a capo's key to sound in, whose words may name any key: the song's, the one
        to sound in): only over an argument that misreads one of them, another key of the same letter or the same
        sound ("B" for H-Dur, "F#" for F sharp minor); a key of another letter and sound stays hers (review C1: "in
        G major ... 3 semitones higher" with her Bb gave "no capo")."""
        if argument and any(_agrees(argument, mine.key, song) for mine in keys):
            return argument, said
        if argument and misread_only:
            keys = [mine for mine in keys if _misreads(argument, mine.key, song)]
            if not keys:
                return argument, said
        mine = keys[0]
        return mine.written, [*said, f"key from your message: {mine.key.words} ({mine.written})"]


def _other_key(sounds: str, shapes: str, message: str, language: str) -> None:
    """A capo whose key to sound in is the shapes' own key (no capo) while the user's message names another key of
    their mode (eval of 18 Oct: "sound in Bb, playing G shapes" called with chords "G" and shapes "G" got "Capo 0",
    five times): a question back, never code's pick, which may be a key the user never asked for (review I3: "The
    original recording is in A major, but I sing it in G major" gave "Capo 2 ... A major"). No question when the key
    to sound in is one of the user's own key words ("I sing it in G major"); the shapes' own chords are no other key
    ("I play G Em C D"). A side it cannot read is left to theory.capo's own error."""
    try:
        wanted = theory._side(sounds, language, message).key
        played = theory._side(shapes, language, message).key
    except ChordError:
        return
    if wanted.tonic.pc != played.tonic.pc or any(mine.key.same_key(wanted) for mine in theory.keys_in(message)):
        return
    shaped = set(_WORDS.findall(shapes))
    others = [  # a capo keeps the mode: only a key of the shapes' own mode can sound
        written
        for written, key in _message_keys(message, language)
        if key.tonic.pc != played.tonic.pc and key.minor == played.minor and written not in shaped
    ]
    if others:
        raise ChordError(
            "capo_same",
            f"the key to sound in and the shapes are both {played.symbol}, and the message names "
            f"{' and '.join(others)}: say which key should sound",
        )


def _message_keys(message: str, language: str) -> list[tuple[str, theory.KeyName]]:
    """The keys the user's message names, in its order and each once: in words (theory.keys_in: "B-Dur", "C major")
    and as a chord symbol the reader finds ("sound in Bb")."""
    found = [(message.find(mine.written), mine.written, mine.key) for mine in theory.keys_in(message)]
    for reading in dict.fromkeys((language, "en")):
        for mention in read(message, reading):
            try:
                key = theory.parse_key(mention.text, _song_of([mention.text], language, message))
            except ChordError:
                continue
            found.append((mention.start, mention.text, key))
    keys: list[tuple[str, theory.KeyName]] = []
    for _, written, key in sorted(found, key=lambda row: (row[0], -len(row[1]))):
        if not any(key.same_key(other) for _, other in keys):
            keys.append((written, key))
    return keys


def _keys_now(chords: str, from_key: str, language: str, message: str) -> list[theory.KeyName]:
    """The key a move's chords are in now, as the move finds it (from_key, a capo line's shapes, a "Key:" line, else
    the estimate's best keys), each with its relative; none when code cannot read them (the move's own error says
    why)."""
    try:
        reading = theory.read(chords, language=language, message=" ".join((message, from_key)))
        if not reading.chords:
            return []
        hearing = [found for chord in reading.chords if (found := theory.heard(chord)) is not None]
        source, _ = theory._song_key(reading, hearing, from_key or None, reading.chords[0].naming)
    except ChordError:
        return []
    keys = [source.key, *(source.estimate.best if source.estimate is not None else ())]
    return [*keys, *(key.relative for key in keys)]


def _texts(message: str, web: Sequence[WebResults]) -> list[str]:
    """Where the user's chords come from: the message and the turn's web results."""
    return [message, *(f"{result.title}\n{result.text}" for search in web for result in search.results)]


_TOKEN = r"[^\s·|,.;:()\[\]]"  # a character of a word as _WORDS reads them


def _unread(error: ChordError, texts: Sequence[str]) -> str:
    """The chord a refusal names, when it is the user's or a page's: a whole word of `texts`, in its spelling there
    ("SIm7b" for her "Sim7b"; never "ama" inside "mama", review minor 6); "" for any other error and for a chord she
    mistyped herself (then wrote right: no news)."""
    found = re.search(r'"(.*?)"', str(error)) if error.code in ("not_a_chord", "ending") else None
    if found is None:
        return ""
    chord = found.group(1)
    seen = chord.removesuffix("…")  # an error shows at most 12 characters of it
    rest = f"{_TOKEN}*" if seen != chord else ""
    word = re.compile(rf"(?<!{_TOKEN}){re.escape(seen)}{rest}(?!{_TOKEN})", re.IGNORECASE)
    for text in texts:
        hit = word.search(text)
        if hit is not None:
            return hit.group(0) if seen == chord else chord
    return ""


def _unread_before(result: Result, earlier: Sequence[Result]) -> list[str]:
    """The user's chords that earlier calls of the same action could not read and this one does not name."""
    return list(
        dict.fromkeys(
            done.unread
            for done in earlier
            if done.unread and done.action == result.action and done.unread not in result.given
        )
    )


def _guessed(result: Result, earlier: Sequence[Result], texts: Sequence[str]) -> str:
    """The user's unread chords, quoted, when a call that worked names a chord neither the user's texts nor the
    earlier calls hold (review I4: "Hm7" for the user's "Hm7b", the eval's run of 18 Oct): a guess; else ""."""
    unread = _unread_before(result, earlier)
    if not result.ok or not unread:
        return ""
    asked = {word for done in earlier for word in done.given} | {
        word for text in texts for word in _WORDS.findall(text)
    }
    if all(chord in asked for chord in result.given):
        return ""
    return " and ".join(f'"{chord}"' for chord in unread)


def _accounted(result: Result, earlier: Sequence[Result], texts: Sequence[str]) -> Result:
    """A result after a refusal of the user's chord says in "Read as:" that it was left out (eval of 18 Oct: "Hm7b"
    refused, then a call without it), with every other chord of the user's that the refused call held and this one
    drops (review minor 7): '"Hm7b" and "F" left out'; a chord in their place is refused before (`_guessed`)."""
    unread = _unread_before(result, earlier)
    if not result.ok or not unread:
        return result
    words = {word for text in texts for word in _WORDS.findall(text)}
    dropped = [
        word
        for done in earlier
        if done.unread and done.action == result.action
        for word in done.given
        if word in words and word not in result.given and word not in unread and _chord_like(word)
    ]
    quoted = " and ".join(f'"{chord}"' for chord in dict.fromkeys([*unread, *dropped]))
    return replace(result, lines=f"{result.lines}; {quoted} left out")


def _chord_like(word: str) -> bool:
    """A chord in some naming, or a name that starts like one and is no plain word ("Hm7b", never "Verse" or
    "Bridge")."""
    return theory._parses(word) or (theory._starts_like_a_chord(word) and not (word.isalpha() and len(word) > 3))


def _known(texts: Sequence[str], song: theory._Song) -> list[tuple[theory.Note, str | None]]:
    """Chords as (root as spelled, their ending's name in the table, else as written), read in the song's naming."""
    found: list[tuple[theory.Note, str | None]] = []
    for text in texts:
        chord = _parsed(text, song)
        if chord is not None:
            name = chord.table
            found.append((chord.root, name if name not in (None, theory.UNREAD) else chord.ending))
    return found


def _song_of(words: Sequence[str], language: str, message: str) -> theory._Song:
    """The naming some words settle; words that mix names get letters (with the German B and H of the language or
    the message), and `_parsed` still reads each word that is do re mi on its own."""
    try:
        return theory._settle(list(words), language, message)
    except ChordError:
        return theory._Song(german=theory._german(language, message))


def _move_song(chords: str, language: str, keys_said: str) -> theory._Song:
    """The naming a move reads its keys in: the chords' own, with the German B and H of the language, the message
    and the key arguments (theory.transpose's reading); chords it cannot read leave that to its own error."""
    try:
        return theory.read(chords, language=language, message=keys_said).song
    except ChordError:
        return _song_of([], language, keys_said)


def _capo_line(chords: str, language: str, message: str) -> bool:
    """Whether the chords given to capo are a sheet with a "Capo N" line (C4); a key in words ("fis klein") is
    none, and chords that cannot be read get their error from theory.capo itself."""
    try:
        return theory.read(chords, language=language, message=message).fret is not None
    except ChordError:
        return False


def _parses(key: str) -> bool:
    try:
        theory.parse_key(key)
    except ChordError:
        return False
    return True


# ---- the reader of chords in her words (design 3.7): one reader, over theory's own chord parser ----

# each language's music words, joining words and chord-like words, as her voice's packs have them
# (tools/voice/Sources/VoiceCore/Languages/Pack_<code>.swift, `chords`; a test compares the lists)
MUSIC_WORDS: dict[str, tuple[str, ...]] = {
    "de": ("akkord", "lied", "song", "gitarre", "klavier", "piano", "ukulele", "tonart", "kapodaster", "strophe",
           "refrain", "melodie", "musik", "intro", "riff"),
    "it": ("accordi", "canzon", "chitarr", "pianoforte", "ukulele", "tonalità", "capotasto", "progressione", "strof",
           "ritornello", "spartit", "melodi", "music", "intro", "riff"),
    "es": ("acorde", "canci", "guitarr", "piano", "ukelele", "ukulele", "tonalidad", "cejilla", "trastes", "progresi",
           "estrofa", "estribillo", "rasgue", "partitura", "melod", "músic", "music", "intro", "riff"),
    "fr": ("accords", "chanson", "guitare", "piano", "ukulélé", "ukulele", "tonalité", "capodastre", "couplet",
           "refrain", "mélodie", "musique", "partition", "progression", "intro", "riff"),
    "nl": ("akkoorden", "liedje", "lied", "gitaar", "piano", "ukelele", "ukulele", "toonsoort", "capo", "couplet",
           "refrein", "melodie", "muziek", "intro", "riff"),
    "pt": ("acorde", "canç", "músic", "music", "guitarra", "violão", "piano", "cavaquinho", "ukulele", "tonalidade",
           "capotraste", "progressão", "estrofe", "refrão", "melodi", "partitura", "intro", "riff"),
    "ru": ("аккорд", "песн", "гитар", "пианино", "фортепиано", "укулеле", "тональност", "каподастр", "куплет", "припев",
           "мелоди", "музык", "интро", "рифф"),
    "pl": ("akord", "piosenk", "gitar", "pianin", "fortepian", "ukulele", "tonacj", "kapodaster", "zwrotk", "refren",
           "melodi", "muzyk", "utwór", "utworu", "intro", "riff"),
}  # fmt: skip
JOINS: dict[str, tuple[str, ...]] = {
    "de": ("und", "oder"),
    "it": ("e", "ed", "o"),
    "es": ("y", "e", "o", "u"),
    "fr": ("et", "ou"),
    "nl": ("en", "of"),
    "pt": ("e", "ou"),
    "ru": ("и", "или"),
    "pl": ("i", "oraz", "lub", "albo"),
}
_WORD_CHORDS: dict[str, frozenset[str]] = {  # chords that are also words: chords only in a run
    "de": frozenset({"Am", "Ab", "Hm"}),
    "it": frozenset({"Dom", "Fam", "Lab"}),
    "es": frozenset({"Dom", "Fam", "Lab"}),
    "fr": frozenset({"Dom", "Fam", "Lab"}),
    "pt": frozenset({"Sim", "Em", "Dom", "Mim", "Fam", "Lab"}),
    "pl": frozenset({"Hm"}),
}
_STYLES = {"es": "solfege", "it": "solfege", "pt": "solfege", "fr": "solfege", "de": "german", "pl": "german",
           "nl": "dutch", "ru": "russian", "ja": "letters", "ko": "letters"}  # fmt: skip
_ROOT_PATTERNS = {
    "solfege": "Do|Dó|Re|Ré|Mi|Fa|Fá|Sol|La|Lá|Si|[A-G]",
    "german": "[A-H](?:is|es)|As|Es|[A-H]",
    "dutch": "[A-G](?:is|es)|As|Es|[A-G]",
    "russian": "[A-H]",
    "letters": "[A-G]",
}
_LATIN = "A-Za-zÀ-ɏЀ-ӿ"  # Latin and Cyrillic letters: kana and Hangul glued to a chord never block it
_WORD_CANDIDATES = {
    style: re.compile(
        rf"(?<![{_LATIN}0-9#♯♭+/°º'’])({roots})([#♯♭b]?)(maj7|m7|m|7)?"
        r"((?:sus[24]?|dim7?|aug|add[0-9]{1,2}|[0-9]{1,2}|°|ø|/[A-H](?:is|es|[#♯♭b])?)*)"
        rf"(?![{_LATIN}0-9#♯♭+])"
    )
    for style, roots in _ROOT_PATTERNS.items()
}
# between two chords of a run: a separator, or a new line with a list's mark ("- ", "* ", "• ", "2. "); bold or
# italic markers around the chords ("**Bm**") are no gap (review C3)
_MARKS = r"[*_]{0,3}"
_LIST_GAP = rf"{_MARKS}\s*,?[ \t]*\n\s*(?:(?:[-*•+]|\d{{1,2}}[.)])\s+)?{_MARKS}\Z"
_SYMBOL_GAP = re.compile(rf"{_MARKS}\s*(?:,|、|-|–|—|\|)?\s*{_MARKS}\Z|{_LIST_GAP}")
_ENGLISH_CANDIDATE = re.compile(r"(?<![\w#♯♭+°øΔ/])[A-G][A-Za-z0-9#♯♭+°øΔ]*(?:/(?:[A-G][#♯♭b]?|9))?(?!\w)")
_ENGLISH_SYMBOL_GAP = re.compile(rf"{_MARKS}\s*(?:,|-|–|—|\||→|->)?\s*{_MARKS}\Z|{_LIST_GAP}")
_ENGLISH_WORD_GAP = re.compile(rf"{_MARKS}\s*,?\s+(?:and|then|to|or)\s+{_MARKS}\Z")
_ENGLISH_MUSIC = re.compile(
    r"\b(?:chords?|keys?|capo|frets?|barre|progressions?|strum\w*|verses?|chorus(?:es)?|bridges?|intro|outro|riffs?|"
    r"tabs?|guitar|ukulele|piano|songs?)\b",
    re.IGNORECASE,
)
_PRONOUNS = frozenset({"i", "you", "we", "they", "he", "she", "it", "not", "me", "us", "him", "her", "them"})
_BOUNDARY = re.compile(r"[.!?](?:\s|\Z)|[。！？]")
_LINE_BOUNDARY = re.compile(r"[.!?](?:\s|\Z)|[。！？]|\n")  # her answer: each line its own sentence too
_LIST_ITEM = re.compile(r"[ \t]*(?:[-*•+]|\d{1,2}[.)])[ \t]+[*_]{0,3}")  # a line's start up to its item


@dataclass(frozen=True)
class Mention:
    """A chord in her words: as written, where it starts, its root as spelled, and its ending's name in the table
    (as written when it has none); `ending` None for a bare letter or syllable or a key in words, which count by
    their root alone (design 3.7). `run`: the start of the run it is in (its own start when alone)."""

    text: str
    start: int
    root: theory.Note
    ending: str | None
    run: int = -1


class _Sentences:
    """A text's sentences, worked out once (review I2: each chord looked them up anew, in time growing with the cube
    of the text): their starts and ends, a chord's sentence found by its position, and what each one says, cached."""

    def __init__(self, text: str, boundary: re.Pattern[str]) -> None:
        self.text = text
        self.starts = [0]
        self.ends: list[int] = []
        for found in boundary.finditer(text):
            self.ends.append(found.start())
            self.starts.append(found.end())
        self.ends.append(len(text))
        self._said: dict[int, str] = {}

    def at(self, position: int) -> int:
        """The sentence around a position (a chord's start: never inside a boundary)."""
        return bisect.bisect_right(self.starts, position) - 1

    def said(self, index: int) -> str:
        """The sentence's text."""
        if index not in self._said:
            self._said[index] = self.text[self.starts[index] : self.ends[index]]
        return self._said[index]


def _song(language: str, text: str) -> theory._Song:
    return theory._Song(german=language in ("de", "pl") or bool(re.search(r"(?<![\w])H(?:is)?[0-9m]?(?!\w)", text)))


def _parsed(word: str, song: theory._Song) -> theory.Chord | None:
    for settled in (song, theory._Song(german=song.german, solfege=True)):
        try:
            return theory.chord_of(word, settled)
        except ChordError:
            continue
    return None


def _speller_reads(word: str) -> bool:
    """Whether ChordSpeller reads a word as a chord (ChordSpeller.parse, tools/voice/Sources/VoiceCore): a root A to
    G, a sign, one quality (maj, min, m, M before a number, dim or °, aug or +, ø, Δ), a number or 6/9, maj7 or M7
    after a minor, sus, add with its number, alterations and a bass, and nothing after them. "Do", "Go", "Ah",
    "C++" and "Emma" are none (review C1)."""
    size = len(word)
    at = 1

    def take(*options: str) -> str:
        nonlocal at
        for option in options:
            if word.startswith(option, at):
                at += len(option)
                return option
        return ""

    def number(*allowed: str) -> bool:
        nonlocal at
        for value in sorted(allowed, key=len, reverse=True):
            end = at + len(value)
            if word.startswith(value, at) and not (end < size and word[end].isdigit()):  # "71" is no "7"
                at = end
                return True
        return False

    if not word or word[0] not in "ABCDEFG":
        return False
    take("#", "♯", "♭", "b")
    quality = take("maj", "min", "m")
    if not quality and word[at : at + 1] == "M" and word[at + 1 : at + 2].isdigit():
        quality = take("M")
    quality = quality or take("dim", "°", "aug", "+", "ø", "Δ")
    if not take("6/9"):
        number("5", "6", "7", "9", "11", "13")
    if quality in ("m", "min"):
        take("maj7", "M7")
    if take("sus"):
        number("2", "4")
    if take("add") and not number("2", "4", "9", "11", "13"):
        return False
    while at < size and word[at] in "#♯b♭":
        at += 1
        if not number("5", "9", "11", "13"):
            return False
    if take("/"):
        if word[at : at + 1] not in tuple("ABCDEFG"):
            return False
        at += 1
        take("#", "♯", "♭", "b")
    return at == size


@dataclass(frozen=True)
class _Key:
    """A key in words in the text (L4: "B minor", "si menor", "h-Moll"): where it stands, as written, its tonic."""

    start: int
    end: int
    text: str
    tonic: theory.Note


class _Covered:
    """Whether a position lies inside one of the keys (in order, never overlapping): its letter is the key's."""

    def __init__(self, keys: Sequence[_Key]) -> None:
        self._keys = keys
        self._starts = [key.start for key in keys]

    def __call__(self, at: int) -> bool:
        index = bisect.bisect_right(self._starts, at) - 1
        return index >= 0 and at < self._keys[index].end


def _ending_of(chord: theory.Chord) -> str:
    name = chord.table
    return name if name not in (None, theory.UNREAD) else chord.ending


_SIGN_WORD = re.compile(r"[ -](flat|sharp)\b", re.IGNORECASE)  # "B flat", "C-sharp": a letter's sign in words


def _english(text: str, boundary: re.Pattern[str], keys: Sequence[_Key]) -> list[Mention]:
    """English, as ChordSpeller reads it (tools/voice/Sources/VoiceCore/ChordSpeller.swift): a strong chord anywhere;
    one that is also a word ("Am", "Ab", "A+", "A/B", "E7", "C#") in a run or a sentence about music; a bare letter
    in a run with a chord or a sentence about music; never "Am I", nor the article "A" starting a sentence. A word
    counts only as ChordSpeller reads it, or as theory reads it with a number in its ending ("Cmi7", "Fo7"). A key
    in words is a chord of its run ("B minor, G, D, A"), and a letter's sign may follow in words ("B flat")."""
    song = _song("en", text)
    sentences = _Sentences(text, boundary)
    covered = _Covered(keys)
    # each: start, end, as written, its root, the chord (None for a key) and its kind
    found: list[tuple[int, int, str, theory.Note, theory.Chord | None, str]] = []
    for match in _ENGLISH_CANDIDATE.finditer(text):
        if covered(match.start()):
            continue
        word = match.group(0)
        chord = _parsed(word, song)
        if chord is None or chord.naming.system != "letters":
            continue
        if not _speller_reads(word) and not any(char.isdigit() for char in chord.ending):
            continue  # "Do", "Go", "Ah", "C++": words her voice never says as chords
        kind = _english_kind(chord)
        rest = text[match.end() : match.end() + 40]
        if kind == "minor" and rest[:1] == " " and rest.lstrip(" ").split(" ")[0].strip("?,.!").casefold() in _PRONOUNS:
            continue  # "Am I late?"
        if kind == "letter" and word == "A" and _starts_sentence(text, match.start(), boundary):
            continue  # the article, at a sentence's start
        end, root = match.end(), chord.root
        sign = _SIGN_WORD.match(text, end) if kind == "letter" else None
        if sign is not None:  # review I9: "it sounds in B flat" is B♭, never B
            end, root = sign.end(), theory.Note(root.letter, -1 if sign.group(1).casefold() == "flat" else 1)
        found.append((match.start(), end, text[match.start() : end], root, chord, kind))
    found.extend((key.start, key.end, key.text, key.tonic, None, "key") for key in keys)
    found.sort(key=lambda row: row[0])
    runs = _runs(text, [(row[0], row[1]) for row in found], (_ENGLISH_SYMBOL_GAP, _ENGLISH_WORD_GAP))
    strong_in = {sentences.at(row[0]) for row in found if row[5] == "strong"}
    music: dict[int, bool] = {}
    out: list[Mention] = []
    for index, (start, _, word, root, chord, kind) in enumerate(found):
        members = runs[index]
        run = found[members[0]][0]
        if chord is None:  # a key in words, by its root alone
            out.append(Mention(word, start, root, None, run))
            continue
        here = sentences.at(start)
        if here not in music:
            music[here] = _ENGLISH_MUSIC.search(sentences.said(here)) is not None
        in_run = len(members) > 1
        strong_here = here in strong_in
        musical = in_run or music[here] or strong_here
        if (
            kind == "strong"
            or (kind in ("minor", "ambiguous", "digits") and musical)
            or (
                kind == "sharp"
                and (
                    strong_here or music[here] or any(found[member][5] not in ("sharp", "letter") for member in members)
                )
            )
        ):
            out.append(Mention(word, start, root, _ending_of(chord), run))
        elif kind == "letter":
            run_has_chord = in_run and any(found[member][5] != "letter" for member in members)
            if run_has_chord or music[here]:
                out.append(Mention(word, start, root, None, run))
    return out


def _starts_sentence(text: str, at: int, boundary: re.Pattern[str]) -> bool:
    """Whether a word starts a sentence (after spaces): the article "A". A list's item ("4. A", "- A") is none."""
    line = text.rfind("\n", 0, at) + 1
    if _LIST_ITEM.fullmatch(text, line, at) is not None:
        return False
    before = at
    while before > 0 and text[before - 1] == " ":
        before -= 1
    return not before or boundary.match(text[before - 1] + " ") is not None


def _english_kind(chord: theory.Chord) -> str:
    """ChordSpeller's kinds: letter ("C"), digits ("E7"), minor ("Am"), ambiguous ("Ab", "A+", "A/B", "C#") or
    strong ("Em7", "Bb", "D/F#")."""
    sign = chord.root_text[1:]
    ending = chord.ending
    if not sign and not ending:
        if not chord.bass_text:
            return "letter"
        return "ambiguous" if len(chord.bass_text) == 1 else "strong"
    if chord.bass_text:
        return "strong"
    if not sign and ending.isdigit():
        return "digits"
    if not sign and ending in ("m", "min"):
        return "minor"
    if sign == "b" and not ending and chord.root_text[0] not in "BE":
        return "ambiguous"  # "Ab", "Db": also words; "Bb", "Eb" never are
    if not sign and ending == "+":
        return "ambiguous"  # a grade
    if sign in ("#", "♯") and not ending:
        return "sharp"  # also the programming language: "C sharp" either way, a chord only with music around it
    return "strong"


def _runs(text: str, spans: list[tuple[int, int]], gaps: tuple[re.Pattern[str], ...]) -> list[list[int]]:
    """For each candidate (start, end), the members of its run: candidates joined only by a separator or an and-or
    word."""
    groups: list[list[int]] = []
    for index, (start, _) in enumerate(spans):
        if index:
            gap = text[spans[index - 1][1] : start]
            if any(pattern.match(gap) for pattern in gaps):
                groups[-1].append(index)
                continue
        groups.append([index])
    members: list[list[int]] = [[] for _ in spans]
    for group in groups:
        for index in group:
            members[index] = group
    return members


def _words_language(text: str, language: str, boundary: re.Pattern[str], keys: Sequence[_Key]) -> list[Mention]:
    """The ten other languages, as ChordWords reads them (tools/voice/Sources/VoiceCore/Languages/ChordWords.swift):
    a strong chord anywhere; a suffixed one in a sentence with a music word, a strong chord, a run of three or a run
    that is the whole sentence; a bare letter or syllable only in such a run that holds a suffixed chord; syllables
    in title case or capitals, never lower case. A key in words counts as a suffixed chord of its run ("si menor,
    Sol, Re, La")."""
    style = _STYLES[language]
    words = _WORD_CHORDS.get(language, frozenset())
    music = MUSIC_WORDS.get(language, ())
    joins = JOINS.get(language, ())
    song = _song(language, text)
    sentences = _Sentences(text, boundary)
    covered = _Covered(keys)
    # each: the candidate's match (None for a key), start, end, as written, its root, the chord (None for a key)
    found: list[tuple[re.Match[str] | None, int, int, str, theory.Note, theory.Chord | None]] = []
    for match in _WORD_CANDIDATES[style].finditer(text):
        if covered(match.start()):
            continue
        root, sign, quality, tail = match.group(1), match.group(2), match.group(3) or "", match.group(4)
        if tail and tail.isdigit():
            continue  # "A4", "B12"
        named = len(root) > 1 and root[0] in "ABCDEFGH" and root not in theory._ACCENTED and root[:2] not in ("Do",)
        if named and style == "solfege":
            named = False
        if named and sign:
            continue  # "Fis#"
        before = text[max(0, match.start() - 2) : match.start()]
        if (
            root == "G"
            and sign == "b"
            and not quality
            and not tail
            and (before[-1:].isdigit() or (before[-2:-1].isdigit() and before.endswith(" ")))
        ):
            continue  # "16 Gb": a size
        chord = _parsed(match.group(0), song)
        if chord is None:
            continue
        found.append((match, match.start(), match.end(), match.group(0), chord.root, chord))
    found.extend((None, key.start, key.end, key.text, key.tonic, None) for key in keys)
    found.sort(key=lambda row: row[1])
    gaps: list[re.Pattern[str]] = [_SYMBOL_GAP]
    if joins:
        gaps.append(re.compile(r"\s*,?\s+(?:" + "|".join(map(re.escape, joins)) + r")\s+\Z"))
    runs = _runs(text, [(row[1], row[2]) for row in found], tuple(gaps))

    def suffixed(match: re.Match[str] | None) -> bool:
        return match is None or bool(match.group(2) or match.group(3) or match.group(4))  # a key counts as one

    def strong(match: re.Match[str] | None) -> bool:
        if match is None or match.group(0) in words:
            return False
        tail, quality, sign = match.group(4), match.group(3) or "", match.group(2)
        if re.search(r"sus|dim|aug|add|°|ø|/[A-H](?:[#♯♭b]|is|es)", tail):
            return True
        if quality in ("m7", "maj7") or (sign and quality):
            return True
        return len(match.group(1)) > 1 and bool(sign or quality == "7")

    strong_in = {sentences.at(row[1]) for row in found if strong(row[0])}
    heard_in: dict[int, bool] = {}
    whole_in: dict[tuple[int, int], bool] = {}
    out: list[Mention] = []
    for index, (candidate, start, _, written, root, chord) in enumerate(found):
        members = runs[index]
        run = found[members[0]][1]
        if candidate is None or chord is None:  # a key in words, by its root alone
            out.append(Mention(written, start, root, None, run))
            continue
        suffixed_run = len(members) > 1 and any(suffixed(found[m][0]) for m in members)
        if style == "letters":
            said = suffixed_run or (suffixed(candidate) and candidate.group(0) not in words)
        elif strong(candidate):
            said = True
        elif not (suffixed(candidate) or suffixed_run):
            said = False
        else:
            here = sentences.at(start)
            if here not in heard_in:
                folded = sentences.said(here).casefold()
                heard_in[here] = any(word.casefold() in folded for word in music)
            if (members[0], here) not in whole_in:  # the run is the whole sentence, punctuation aside
                before = text[sentences.starts[here] : found[members[0]][1]]
                after = text[found[members[-1]][2] : sentences.ends[here]]
                whole = not before.strip(" \t,.;:!?()[]-–—|、") and not after.strip(" \t,.;:!?()[]-–—|、。")
                whole_in[members[0], here] = whole
            musical = len(members) >= 3 or whole_in[members[0], here] or heard_in[here]
            # a bare one only in a suffixed run; a suffixed one with music around it or a strong chord in its sentence
            said = (suffixed_run and musical) if not suffixed(candidate) else (musical or here in strong_in)
        if said:
            out.append(Mention(written, start, root, _ending_of(chord) if suffixed(candidate) else None, run))
    return out


SOURCE_CHARS = 4_000  # what a source gives the check: a web result keeps 1,200 characters of a page
ANSWER_CHARS = 16_000  # her answer, read whole: a document answer runs to 2,000 tokens, about 8,000 characters
# three names or more joined by slashes are a list ("Bm/G/E/A"), never a slash chord (review I9); each part starts
# with a letter, so 6/9 and dates stay as they are
_SLASH_LIST = re.compile(r"(?<![\w/])[^\W\d_][^\s/]{0,11}(?:/[^\W\d_][^\s/]{0,11}){2,}")


def read(text: str, language: str = "en", *, lines: bool = False, limit: int = SOURCE_CHARS) -> list[Mention]:
    """The chords in her words, in order, as her voice reads them in the conversation's language (the bridge's, never
    Gemma's), and the keys in words (L4: "B minor", "si minore", "h-Moll"), which count by their root alone.
    `lines`: a new line ends a sentence too (her answer, read whole: a list's lines still join one run). At most
    `limit` characters are read; the time grows in line with them."""
    text = _SLASH_LIST.sub(lambda listed: listed.group(0).replace("/", " "), text[:limit])  # same length
    boundary = _LINE_BOUNDARY if lines else _BOUNDARY
    keys = [
        _Key(match.start(), match.end(), match.group(0).strip(), theory._phrase_key(match).tonic)
        for match in theory.KEY_PHRASE.finditer(text)
        if theory.is_key(match, text)  # never "a minor issue", "la menor idea"
    ]
    if language not in _STYLES:
        return _english(text, boundary, keys)
    return _words_language(text, language, boundary, keys)


# ---- the reply check (design 3.7) ----

# a capo's fret in her words (review C2: "Capo im 4. Bund" beside code's Capo 3), in the conversation's language and
# English: a number right after a fret word (up to three short words between: "on the", "im", "en el"), or one with
# its ordinal mark right before it ("3rd fret", "4. Bund", "4e case"); a fret in words ("the third fret") is not read
FRET_WORDS: dict[str, tuple[str, ...]] = {
    "en": ("capo", "fret"),
    "de": ("capo", "kapo", "kapodaster", "bund"),
    "nl": ("capo", "fret"),
    "fr": ("capo", "capodastre", "case", "frette"),
    "it": ("capo", "capotasto", "tasto"),
    "es": ("capo", "cejilla", "traste", "capotraste"),
    "pt": ("capo", "capotraste", "casa", "traste"),
    "pl": ("capo", "kapodaster", "próg", "progu"),
    "ru": ("капо", "каподастр", "лад", "ладу"),
    "ja": ("カポ", "フレット"),
    "ko": ("카포", "프렛"),
}
# a sentence or a line of her answer; a list's number starts its item ("2. Bm"), never a sentence of its own, and an
# ordinal's full stop before a fret word ("im 4. Bund") ends none
_ANY_FRET_WORD = "|".join(sorted({word for words in FRET_WORDS.values() for word in words}, key=len, reverse=True))
_SENTENCE = re.compile(
    rf"[ \t]*(?:\d{{1,2}}[.)][ \t]+)?(?:[^.!?。！？\n]|(?<=\d)\.(?=[ \t]+(?i:{_ANY_FRET_WORD})))*"
    r"(?:[.!?]+(?=\s|\Z)|[。！？]+|\n|\Z)"
)
_LETTER = f"[{_LATIN}]"
_KANA_HANGUL = "[\u3040-\u30ff\uac00-\ud7af]"  # a particle between a fret word and its number: カポを4
_ORDINAL = r"(?:st|nd|rd|th|ème|er|re|ste|de|e|°|º|ª|o|a|-й|-я|-м|й|번|番)?\.?"
_FRETS: dict[str, re.Pattern[str]] = {}
_CODE_FRET = re.compile(r"\bcapo (\d{1,2})\b", re.IGNORECASE)  # code's lines: "Capo 3 with", "at capo 1"


def frets_in(text: str, language: str = "en") -> list[tuple[int, int]]:
    """The capo frets a text names, as (where, fret), in order."""
    if language not in _FRETS:
        words = sorted({*FRET_WORDS["en"], *FRET_WORDS.get(language, ())}, key=len, reverse=True)
        names = "|".join(map(re.escape, words))
        _FRETS[language] = re.compile(
            rf"(?<!{_LETTER})(?:{names}){_LETTER}{{0,2}}(?:[ \t]+{_LETTER}{{1,4}}\.?|{_KANA_HANGUL}{{1,2}}){{0,3}}"
            rf"[ \t]*:?[ \t]*(\d{{1,2}})(?![\d]|[.,]\d)"
            rf"|(?<![\w.,])(\d{{1,2}}){_ORDINAL}[ \t]*(?:{names})(?!{_LETTER}{{3}})",
            re.IGNORECASE,
        )
    return [(found.start(), int(found.group(1) or found.group(2))) for found in _FRETS[language].finditer(text)]


def _code_frets(result: Result) -> set[int]:
    """The frets a result that worked names in code's lines: a capo's, a capo sheet's, a move's "Or:" line."""
    frets = {int(fret) for fret in _CODE_FRET.findall(result.lines)}
    return frets | ({0} if "no capo" in result.lines else set())


@dataclass(frozen=True)
class Checked:
    """Her answer after the check: without the sentences that name another chord, how many went, and whether the
    check ran at all (the tool ran, or a source holds chords)."""

    text: str
    left_out: int
    held: bool


def _key(note: theory.Note) -> tuple[int, int]:
    return note.letter, note.alter


def check(
    answer: str,
    *,
    results: Sequence[Result],
    sources: Sequence[str],
    language: str = "en",
    earlier: Sequence[Result] = (),
) -> Checked:
    """Her answer's chords against code's (design 3.7). The tool ran: each chord she names must be one its results
    name or were given, by its root as spelled and its ending (a bare letter or a key in words by its root); a page's
    other chords do not count. The tool did not run: each must have a root that the sources' chords have (the user's
    message, the turn before, the web results, and on a call its earlier results). Her answer is read whole, so a
    list's lines are one run (review C3); every sentence or line that holds a chord of a run with another chord is
    left out. A result that names frets (a capo's): each fret she names must be one of them or a source's (review
    C2), else its sentence is left out too."""
    worked = [result for result in (*results, *earlier) if result.ok]
    frets = {fret for result in worked for fret in _code_frets(result)}
    wrong_frets: list[int] = []
    if frets:
        frets |= {fret for source in sources for _, fret in frets_in(source[:SOURCE_CHARS], language)}
        wrong_frets = [at for at, fret in frets_in(answer[:ANSWER_CHARS], language) if fret not in frets]
    if any(result.ok for result in results):
        full = {(*_key(root), ending) for result in worked for root, ending in result.known if ending is not None}
        roots = {_key(root) for result in worked for root, _ in result.known}

        def fine(mention: Mention) -> bool:
            if mention.ending is None:
                return _key(mention.root) in roots
            return (*_key(mention.root), mention.ending) in full

    else:
        roots = {_key(root) for result in worked for root, _ in result.known}
        for source in sources:
            for reading in {language, "en"}:
                roots |= {_key(mention.root) for mention in read(source, reading)}
        if not roots and not frets:
            return Checked(answer, 0, False)

        def fine(mention: Mention) -> bool:
            return not roots or _key(mention.root) in roots  # no chords in the sources: only her frets are read

    mentions = read(answer, language, lines=True, limit=ANSWER_CHARS)
    wrong = {mention.run for mention in mentions if not fine(mention)}
    flagged = sorted([*(mention.start for mention in mentions if mention.run in wrong), *wrong_frets])
    kept: list[str] = []
    left_out = 0
    for match in _SENTENCE.finditer(answer):
        sentence = match.group(0)
        if not sentence:
            continue
        index = bisect.bisect_left(flagged, match.start())
        if index < len(flagged) and flagged[index] < match.end():
            left_out += 1
            continue
        kept.append(sentence)
    text = re.sub(r"[ \t]{2,}", " ", "".join(kept)).strip()
    return Checked(text if left_out else answer, left_out, True)


def told(results: Sequence[Result]) -> list[Result]:
    """The calls the user is told of, in call order: each that worked, each refusal of the user's own chord (eval of
    18 Oct: a later call with a changed chord hid it), and any other error only when none worked (a retried one is
    no news); the same error once."""
    worked = any(result.ok for result in results)
    shown: list[Result] = []
    for result in results:
        if result.error == "guessed":  # no news: the refusal of the chord itself is told
            continue
        if result.ok or (
            (result.unread or not worked) and all(result.lines != other.lines for other in shown if not other.ok)
        ):
            shown.append(result)
    return shown


def note(results: Sequence[Result], *, left_out: bool = False) -> str:
    """Code's note: the fixed lines of each call the user is told of (`told`), then why her own chords were left
    out; "" when there is nothing to say."""
    parts = [result.lines for result in told(results)]
    if not parts:
        return prompts.CHORDS_LEFT_OUT if left_out else ""
    return "\n".join([prompts.CHORDS_HEAD, "\n\n".join(parts)] + ([prompts.CHORDS_LEFT_OUT] if left_out else []))


def spoken(results: Sequence[Result], *, left_out: bool = False) -> str:
    """What a call says after her words: each result, then, after a move, that the chords come in a message after
    the call (E3)."""
    parts = [result.spoken for result in told(results)]
    if left_out:
        parts.append(prompts.CHORDS_LEFT_OUT)
    if any(result.moved for result in results):
        parts.append(prompts.CHORDS_LATER)
    return " ".join(part for part in parts if part)
