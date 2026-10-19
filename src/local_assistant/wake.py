"""Her name in a call turn, which ends standby on a call (owner, 18 Oct). Whisper writes a name as it hears it, so the
name and each word of the turn are compared by sound: Russian, Japanese and Korean letters written in Latin ones,
then letters that sound alike made one ("ph", "f" and "p"; "l" and "r"; doubled letters), as a name in those
languages is written with the letters they have (Korean has no "f": 티파니 is "tipani"). A name of six or more such
letters may be one letter off (Whisper's "Tiffanie"); a shorter one must match exactly, so Spanish "esta" never
counts for Estia. Known limits: a word that sounds like her name counts as it ("Tiffany said that" ends standby, and
Italian "mia" for Mia); a name of two words counts only whole; a short name another language writes with another
vowel is missed (Russian "Сэм" for Sam); untested on Whisper's output on a live call."""

from __future__ import annotations

import re
import unicodedata

_CYRILLIC = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        ("a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
         "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"),
        strict=True,
    )
)  # fmt: skip
# Hangul syllables: initial, vowel and final jamo in Revised Romanization; ㅡ ("eu") is left out, as it is the vowel
# Korean adds between consonants of a foreign word (에스티아, "estia"), and ㅐ is "a", the a of a foreign name (샘, Sam)
_L = ("g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h")  # fmt: skip
_V = ("a", "a", "ya", "ya", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "",
      "ui", "i")  # fmt: skip
_T = ("", "k", "k", "ks", "n", "nj", "nh", "t", "l", "lk", "lm", "lb", "ls", "lt", "lp", "lh", "m", "p", "ps", "t", "t",
      "ng", "t", "t", "k", "t", "p", "t")  # fmt: skip
# Katakana in Hepburn; the small vowels and ャュョ join the kana before them (ティ "ti", ファ "fa"), ー and ッ are left
# out, and the "u" or "o" Japanese adds to a foreign consonant (ス "su" in エスティア) is left out before another
# consonant and at a word's end
_KANA = dict(
    zip(
        "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
        "ガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポヴ",
        ("a", "i", "u", "e", "o", "ka", "ki", "ku", "ke", "ko", "sa", "shi", "su", "se", "so", "ta", "chi", "tsu", "te",
         "to", "na", "ni", "nu", "ne", "no", "ha", "hi", "fu", "he", "ho", "ma", "mi", "mu", "me", "mo", "ya", "yu", "yo",
         "ra", "ri", "ru", "re", "ro", "wa", "o", "n", "ga", "gi", "gu", "ge", "go", "za", "ji", "zu", "ze", "zo", "da",
         "ji", "zu", "de", "do", "ba", "bi", "bu", "be", "bo", "pa", "pi", "pu", "pe", "po", "vu"),
        strict=True,
    )
)  # fmt: skip
_SMALL = {"ァ": "a", "ィ": "i", "ゥ": "u", "ェ": "e", "ォ": "o", "ャ": "ya", "ュ": "yu", "ョ": "yo"}
_ADDED_VOWEL = {"ス", "ク", "グ", "ズ", "ブ", "プ", "フ", "ム", "ル", "ト", "ド"}
_KOREAN_ENDINGS = ("이야", "야", "아", "씨", "님", "이")  # the vocative and the polite forms after a name: 티파니야
_TOKEN = re.compile(r"[゠-ヿ]+|[぀-ゟ]+|(?:[^\W\d_぀-ヿ㐀-鿿]|')+")
_ALIKE = str.maketrans({"p": "f", "v": "b", "l": "r", "c": "k", "q": "k", "g": "k", "d": "t", "z": "s", "y": "i"})


def _hangul(word: str) -> str:
    out = []
    for char in word:
        code = ord(char) - 0xAC00
        if 0 <= code < 11172:
            out.append(_L[code // 588] + _V[code % 588 // 28] + _T[code % 28])
        else:
            out.append(char)
    return "".join(out)


def _katakana(word: str) -> str:
    out: list[str] = []
    for n, char in enumerate(word):
        if char in _SMALL and out:
            # ティ: "te" + small i is "ti"; ファ: "fu" + small a is "fa"; キャ: "ki" + small ya is "kya"
            out[-1] = out[-1][:-1] + _SMALL[char]
            continue
        sound = _KANA.get(char, "")
        after = word[n + 1 : n + 2]
        if char in _ADDED_VOWEL and (not after or (after in _KANA and _KANA[after][0] not in "aiueo")):
            sound = sound[:-1]
        out.append(sound)
    return "".join(out)


def _sound(word: str) -> str:
    """A word as compared: in Latin letters, without accents, and letters that sound alike made one."""
    word = unicodedata.normalize("NFKC", word).casefold().removesuffix("'s")
    if any("゠" <= char <= "ヿ" for char in word):
        word = _katakana(word)
    elif any("가" <= char <= "힣" for char in word):
        word = _hangul(word)
    word = "".join(_CYRILLIC.get(char, char) for char in word)
    word = "".join(char for char in unicodedata.normalize("NFKD", word) if not unicodedata.combining(char))
    word = re.sub(r"[^a-z]", "", word).replace("ph", "f")
    word = re.sub(r"(?<=[b-df-hj-np-tv-z])h", "", word).replace("x", "ks").translate(_ALIKE)
    return re.sub(r"(.)\1+", r"\1", word)


def _near(heard: str, name: str) -> bool:
    if heard == name:
        return True
    if len(name) < 6 or abs(len(heard) - len(name)) > 1:
        return False
    before = list(range(len(name) + 1))  # edit distance, one row at a time
    for i, char in enumerate(heard, 1):
        row = [i]
        for j, other in enumerate(name, 1):
            row.append(min(before[j] + 1, row[j - 1] + 1, before[j - 1] + (char != other)))
        before = row
    return before[-1] <= 1


def _forms(token: str) -> set[str]:
    """A turn's word as compared, and for a Korean word also without a vocative or polite ending (티파니야)."""
    forms = {_sound(token)}
    for ending in _KOREAN_ENDINGS:
        if token.endswith(ending) and len(token) > len(ending) and "가" <= token[0] <= "힣":
            forms.add(_sound(token[: -len(ending)]))
    return forms - {""}


def says_name(text: str, name: str) -> bool:
    """Whether a call turn says her name: one word of it, or its words in a row, as the name sounds."""
    wanted = "".join(_sound(word) for word in _TOKEN.findall(name))
    if not wanted:
        return False
    words = [_forms(token) for token in _TOKEN.findall(text)]
    parts = len([word for word in _TOKEN.findall(name) if _sound(word)])
    for start in range(len(words)):
        for size in {1, parts}:
            run = words[start : start + size]
            if len(run) < size:
                continue
            for joined in _joined(run):
                if _near(joined, wanted):
                    return True
    return False


def _joined(run: list[set[str]]) -> set[str]:
    out = {""}
    for forms in run:
        out = {done + form for done in out for form in forms}
    return out
