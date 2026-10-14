"""Language practice (/babble): the pure checks (the tutor test's shared fixes), the session, the recap, the idle loop."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from local_assistant import babble, prompts
from local_assistant.babble import (
    LAST_KEY,
    SESSION_KEY,
    Babble,
    Point,
    broken,
    due_after,
    keep_point,
    practice_words,
    recap_text,
    tidy,
)
from local_assistant.gemma import Completion, GemmaUnavailableError
from local_assistant.language import CODES
from local_assistant.store import Message, Store
from tests.conftest import ME, STRANGER
from tests.fakes.fake_gemma import FakeGemma, text

TZ = ZoneInfo("Europe/Amsterdam")
NIGHT = (22 * 60, 8 * 60)  # the default quiet hours
PRACTICE_SETS = Path(__file__).parent.parent / "evals" / "practice"  # the tutor test's sets, with gold answers


def at(moment: str) -> float:
    return datetime.fromisoformat(moment).replace(tzinfo=TZ).timestamp()


def message(text: str, kind: str = "text", direction: str = "in") -> Message:
    return Message(1, 1, direction, "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", 1, kind, text, "received", None, 1, "")  # type: ignore[arg-type]


# the constants (owner's decisions)


def test_nine_languages_offered_two_not_yet_and_two_levels() -> None:
    assert babble.OFFERED == ("en", "nl", "de", "es", "fr", "pt", "ja", "it")
    assert babble.NOT_YET == ("ru", "pl", "ko")  # ru: purity below 95% in both real-model runs (13 Oct)
    assert set(babble.OFFERED) | set(babble.NOT_YET) == set(CODES)
    assert babble.LEVELS == ("A2", "B1") and babble.START_LEVEL == "A2"
    assert babble.IDLE_S == 1800
    assert babble.HEARD == ("nl", "fr", "ru", "ja", "ko")


# the broken-turn guard and the tidy reply (tutor test: shared fixes 1 and 2)


@pytest.mark.parametrize(
    ("reply", "spoken", "tidied"),
    [
        ("Wat leuk! Waar ga je naartoe?", False, "Wat leuk! Waar ga je naartoe?"),  # a plain reply stays as it is
        ("Super!\n---\nWat heb je gedaan?", False, "Super!\nWat heb je gedaan?"),
        ("C'est bien.\n--- de travail\nTu travailles où?", False, "C'est bien.\nTu travailles où?"),
        ("Super!\n---\n→ ben ik\nWat heb je gedaan?", False, "→ ben ik\nSuper!\nWat heb je gedaan?"),  # moved first
        ("→ ben ik\n→ het station\nWaar woon je?", False, "→ ben ik\nWaar woon je?"),  # never more than one
        ("-> ben ik\nWaar woon je?", False, "→ ben ik\nWaar woon je?"),  # a typed arrow is read as one
        ("→\nWaar woon je?", False, "Waar woon je?"),  # an arrow with nothing after it
        ("→ ben ik\nWaar woon je?", True, "Waar woon je?"),  # a voice note or a call: no arrow line read aloud
    ],
)
def test_tidy(reply: str, spoken: bool, tidied: str) -> None:
    assert tidy(reply, spoken=spoken) == tidied


@pytest.mark.parametrize(
    ("said", "reply", "tidied"),
    [
        # capitals and punctuation only: no change, no arrow
        ("ik woon in amsterdam", "→ Ik woon in Amsterdam.\nLeuk! Waar woon je?", "Leuk! Waar woon je?"),
        # the user's own correct words repeated
        ("Ik ben gisteren naar Amsterdam gegaan.", "→ naar Amsterdam gegaan\nLeuk!", "Leuk!"),
        ("昨日、駅へ行きました。", "→ 駅へ行きました\nいいですね！", "いいですね！"),
        # a real change stays, also when its words differ only inside a longer word ("gingen" → "ging")
        ("Zaterdag ik ben gegaan.", "→ ben ik\nLeuk!", "→ ben ik\nLeuk!"),
        ("Gisteren wij gingen.", "→ ging\nLeuk!", "→ ging\nLeuk!"),
    ],
)
def test_an_arrow_that_changes_nothing_is_dropped(said: str, reply: str, tidied: str) -> None:
    assert tidy(reply, spoken=False, said=said) == tidied


IT_23 = "I miei genitori abita in un paese vicino a Bari."
IT_02 = "Ieri ho andato al cinema con Luca."


@pytest.mark.parametrize(
    ("said", "reply", "tidied"),
    [
        # the practice evals (13 Oct): the wrong form next to the right one; only the right one is kept
        (
            IT_23,
            "→ abita <abitano>\n\nI tuoi genitori abitano vicino a Bari?",
            "→ abitano\n\nI tuoi genitori abitano vicino a Bari?",
        ),
        (
            "Non ti preoccupare, abbiamo già risolto la problema.",
            "→ la problema, il problema\n\nVa bene! Cosa fai oggi?",
            "→ il problema\n\nVa bene! Cosa fai oggi?",
        ),
        (
            "Normalmente levanto a las siete de la mañana.",
            "→ Levanto -> Te levantas\n\n¿Desayunas algo?",
            "→ Te levantas\n\n¿Desayunas algo?",
        ),
        (IT_02, "→ ho andato <sono andato>\n\nChe bello! Che film?", "→ sono andato\n\nChe bello! Che film?"),
        (IT_02, "→ ho andato → sono andato\nChe bello!", "→ sono andato\nChe bello!"),
        # not safely read: the wrong part is not the user's words, the user's words are unknown, or two pairs
        (IT_02, "→ sono andata <sono andato>\nChe bello!", "Che bello!"),
        ("", "→ ho andato <sono andato>\nChe bello!", "Che bello!"),
        (IT_02, "→ ho andato -> sono andato -> sono andata\nChe bello!", "Che bello!"),
        # a comma inside a correction is no pair: the part before it is not the user's words
        (
            "Ontem eu acordava às sete, tomei café e saí de casa.",
            "→ Ontem eu acordei às sete, tomei café e saí de casa.\nQue bom!",
            "→ Ontem eu acordei às sete, tomei café e saí de casa.\nQue bom!",
        ),
    ],
)
def test_an_arrow_with_the_wrong_form_next_to_the_right_one_keeps_only_the_right_one(
    said: str, reply: str, tidied: str
) -> None:
    assert tidy(reply, spoken=False, said=said) == tidied


@pytest.mark.parametrize(
    ("reply", "is_broken"),
    [
        ("", True),
        ("   \n", True),
        ("---", True),
        ("---\n---", True),
        ("--- de travail", True),
        ("→ ben ik", True),  # only an arrow line
        ("→", True),
        ("---\n→ ben ik", True),
        ("Super!\n---", False),
        ("→ ben ik\nWaar woon je?", False),
    ],
)
def test_broken(reply: str, is_broken: bool) -> None:
    assert broken(reply) is is_broken


# the user's words for the recap


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("Ik ben gisteren gegaan.", "Ik ben gisteren gegaan."),
        ("[voice] Ik woon in Utrecht", "Ik woon in Utrecht"),
        ("[call] Ik werk  bij een bank", "Ik werk bij een bank"),
        ("[call] (cutting in) Nee, ik bedoel morgen", "Nee, ik bedoel morgen"),
        ("[audio file] someone else's voice", None),
        ("[photo] mijn kat", None),
    ],
)
def test_practice_words(text: str, words: str | None) -> None:
    assert practice_words(message(text)) == words


def test_only_the_users_own_texts_are_practice_words() -> None:
    assert practice_words(message("/babble off", kind="command")) is None
    assert practice_words(message("Wat leuk!", direction="out")) is None


# the recap checks (tutor test: shared fix 5)

DUTCH = ["Zaterdag ik ben met vrienden naar het strand gegaan.", "omdat we hadden veel honger"]


@pytest.mark.parametrize(
    ("said", "better", "why", "turns", "code", "kept"),
    [
        ("Zaterdag ik ben", "Zaterdag ben ik", "The verb comes second.", DUTCH, "nl", True),
        (
            "omdat we hadden veel honger",
            "omdat we veel honger hadden",
            "After 'omdat' the verb goes last.",
            DUTCH,
            "nl",
            True,
        ),
        # Italian: dropping "non" reverses the meaning
        (
            "lei non piace camminare",
            "le piace camminare",
            "Use the indirect pronoun.",
            ["A lei non piace camminare."],
            "it",
            False,
        ),
        ("lei non piace camminare", "a lei non piace", "Use 'a lei'.", ["lei non piace camminare"], "it", True),
        (
            "gli è piaciuto",
            "gli è piaciuta",
            "Wait, actually the participle agrees.",
            ["gli è piaciuto molto"],
            "it",
            False,
        ),
        ("Ik ben", "Ik ben", "This is correct.", ["Ik ben moe"], "nl", False),
        ("de station", "het station", "This is correct.", ["bij de station"], "nl", False),
        ("de huis", "het huis", "'Huis' is a het-word.", ["Ik ben moe"], "nl", False),  # not in the user's words
        ("Ik ben", "ik ben.", "Capital and full stop.", ["Ik ben moe"], "nl", False),  # capitals and punctuation only
        (
            "обсуждали о новом проекте",
            "обсуждали новый проект",
            "'Обсуждать' takes the accusative.",
            ["Мы долго обсуждали о новом проекте."],
            "ru",
            True,
        ),
        ("駅に行きました", "駅へ行きました", "へ marks the direction.", ["昨日、駅に行きました。"], "ja", True),
        ("de station", "het station", "", ["bij de station"], "nl", False),  # no why
        ("de station", "het station", "   ", ["bij de station"], "nl", False),
        (
            "ik ga naar de winkel gisteren",
            "gisteren ben ik met mijn moeder naar de grote markt gegaan",
            "Use the past tense.",
            ["ik ga naar de winkel gisteren"],
            "nl",
            False,
        ),  # a rewrite, not a small edit
        ("", "het station", "'Station' is a het-word.", ["bij de station"], "nl", False),
        ("I goed", "I went", "Let me think: the past of 'go' is 'went'.", ["I goed home"], "en", False),
        ("I goed", "I went", "The past of 'go' is 'went'.", ["I  goed home"], "en", True),  # whitespace is normalised
    ],
)
def test_keep_point(said: str, better: str, why: str, turns: list[str], code: str, kept: bool) -> None:
    reason = keep_point(said, better, why, turns, code, "en")
    assert (reason == "") is kept, reason


def test_a_why_in_the_users_own_language_saying_it_is_correct_is_dropped() -> None:
    turns = ["Ich habe ein Hund."]
    assert keep_point("ein Hund", "einen Hund", "'Hund' ist maskulin: einen.", turns, "de", "de") == ""
    assert keep_point("ein Hund", "einen Hund", "Die Form ist korrekt so.", turns, "de", "de") != ""
    assert keep_point("ein Hund", "einen Hund", "Dit is correct.", turns, "de", "nl") != ""
    assert keep_point("ein Hund", "einen Hund", "Wait, that's fine.", turns, "de", "nl") != ""  # English always


def test_the_drop_reasons_are_fixed_words() -> None:
    turns = ["bij de station"]
    reasons = {
        keep_point("", "het station", "x", turns, "nl", "en"),
        keep_point("het huis", "het station", "x", turns, "nl", "en"),
        keep_point("de station", "de station!", "x", turns, "nl", "en"),
        keep_point("de station", "het station", "", turns, "nl", "en"),
        keep_point("de station", "het station", "Actually fine.", turns, "nl", "en"),
    }
    assert reasons <= set(babble.DROP_REASONS) and "" not in reasons


def test_every_planted_mistake_of_the_practice_sets_passes_the_checks() -> None:
    # the gold corrections (said, better) with a neutral why: none may be dropped by the size or negation rules
    dropped = []
    for path in sorted(PRACTICE_SETS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        code = data["language"]
        for conversation in data["conversations"]:
            for mistake in conversation["planted"]:
                why = keep_point(mistake["said"], mistake["better"], "A rule.", conversation["user_turns"], code, "en")
                dropped += [(conversation["id"], mistake["said"], why)] if why else []
        for item in data["items"]:
            if item["kind"] == "error":
                why = keep_point(item["gold_said"], item["gold_better"], "A rule.", [item["sentence"]], code, "en")
                dropped += [(item["id"], item["gold_said"], why)] if why else []
    assert dropped == []


@pytest.mark.parametrize(
    ("said", "better", "turns", "code", "reason"),
    [
        # Japanese, by characters: the i-adjective past is a right correction (the commonest A2 mistake) ...
        ("いいでした", "よかったです", ["天気がとてもいいでした。"], "ja", ""),
        ("しませんでした", "していません", ["まだ宿題をしませんでした。"], "ja", ""),
        # ... the tutor test's false item (a rewrite that adds a verb) is still too big
        ("作った資料", "作った資料を説明しました", ["昨日作った資料を見せました。"], "ja", "too_big"),
        # a pure reorder (word order) is one small change
        ("like very much this song", "like this song very much", ["I like very much this song."], "en", ""),
        ("Zaterdag ik ben", "Zaterdag ben ik", ["Zaterdag ik ben gegaan."], "nl", ""),
    ],
)
def test_the_size_rule(said: str, better: str, turns: list[str], code: str, reason: str) -> None:
    assert keep_point(said, better, "A rule.", turns, code, "en") == reason


@pytest.mark.parametrize(
    ("said", "better", "why", "turn", "code", "user_language"),
    [
        # a word the user practises is quoted, never the model thinking aloud
        (
            "Actually I work",
            "Currently I work",
            "'Actually' means 'in fact'; for 'now' use 'currently'.",
            "Actually I work in a bank.",
            "en",
            "en",
        ),
        ("wait you", "wait for you", "'Wait' takes 'for'.", "I wait you at the station.", "en", "en"),
        # the correction called right
        ("I goed", "I went", "'Went' is right: 'go' is irregular.", "I goed home.", "en", "en"),
        ("I goed", "I went", "goの過去形は不規則で、正しい形はwentです。", "I goed home.", "en", "ja"),
        (
            "いいでした",
            "よかったです",
            "「いい」の過去形は「よかった」が正しいです。",
            "天気がいいでした。",
            "ja",
            "ja",
        ),
        (
            "в Москва",
            "в Москве",
            "Правильно говорить «в Москве»: после «в» предложный падеж.",
            "Я живу в Москва.",
            "ru",
            "ru",
        ),
        (
            "Zeszłego lato",
            "Zeszłego lata",
            "'Lato' is neuter: 'zeszłego lata' is correct.",
            "Zeszłego lato.",
            "pl",
            "en",
        ),
        # the user's words called wrong (a negation)
        (
            "gekoopt",
            "gekocht",
            "'Gekoopt' klopt niet: van 'kopen' is het 'gekocht'.",
            "Ik heb het gekoopt.",
            "nl",
            "nl",
        ),
        ("soy cansado", "estoy cansado", "No está bien usar «ser» para un estado.", "Hoy soy cansado.", "es", "es"),
        ("de station", "het station", "It is correct to use 'het' with 'station'.", "bij de station", "nl", "en"),
    ],
)
def test_a_why_that_explains_a_rule_is_kept(
    said: str, better: str, why: str, turn: str, code: str, user_language: str
) -> None:
    assert keep_point(said, better, why, [turn], code, user_language) == ""


@pytest.mark.parametrize(
    ("said", "better", "why", "turn", "code", "user_language"),
    [
        # thinking aloud, at the start of a sentence (the tutor test's it and pl items)
        (
            "gli è piaciuto",
            "a loro è piaciuto",
            "The pronoun 'gli' is for 'him'. Wait, actually, 'gli' is widely accepted as 'them'.",
            "gli è piaciuto molto",
            "it",
            "en",
        ),
        (
            "Zeszłego lata",
            "Zeszłego roku",
            "'Lato' is neuter: 'zeszłego lata' or 'zeszłego roku'. Wait, 'zeszłego lata' is actually correct. "
            "Let me re-evaluate.",
            "Zeszłego lata byłem w górach.",
            "pl",
            "en",
        ),
        ("ik ben", "ben ik", "Hmm, the verb comes second.", "Morgen ik ben thuis.", "nl", "en"),
        ("ein Hund", "einen Hund", "Warte, das Verb verlangt den Akkusativ.", "Ich habe ein Hund.", "de", "de"),
        # the user's words called right
        ("I goed", "I went", "'I goed' is correct in casual speech.", "I goed home.", "en", "en"),
        ("いいでした", "よかったです", "「いいでした」は正しいです。", "天気がいいでした。", "ja", "ja"),
        ("в Москва", "в Москве", "«в Москва» — это правильно.", "Я живу в Москва.", "ru", "ru"),
        # no mistake at all
        ("de station", "het station", "There is no mistake here.", "bij de station", "nl", "en"),
        (
            "作った資料",
            "作った資料です",
            "While your sentence is grammatically correct, it's more natural.",
            "作った資料",
            "ja",
            "en",
        ),
    ],
)
def test_a_why_that_says_the_form_is_right_or_thinks_aloud_is_dropped(
    said: str, better: str, why: str, turn: str, code: str, user_language: str
) -> None:
    assert keep_point(said, better, why, [turn], code, user_language) == "why_marker"


def test_negations_of_a_language() -> None:
    from local_assistant.language import negations

    assert "non" in negations("it") and "niet" in negations("nl") and "ない" in negations("ja")
    assert negations("xx") == ()


# the recap is held to the end of quiet hours


@pytest.mark.parametrize(
    ("now", "due"),
    [
        ("2026-10-13 23:10", "2026-10-14 08:00"),  # past the start: tomorrow's end
        ("2026-10-14 07:30", "2026-10-14 08:00"),  # after midnight: today's end
        ("2026-10-14 22:00", "2026-10-15 08:00"),  # the start itself is quiet
    ],
)
def test_a_recap_due_in_quiet_hours_waits_for_their_end(now: str, due: str) -> None:
    assert due_after(at(now), TZ, NIGHT) == at(due)


def test_a_recap_outside_quiet_hours_or_without_them_is_due_now() -> None:
    assert due_after(at("2026-10-14 15:00"), TZ, NIGHT) == at("2026-10-14 15:00")
    assert due_after(at("2026-10-14 08:00"), TZ, NIGHT) == at("2026-10-14 08:00")
    assert due_after(at("2026-10-13 23:10"), TZ, None) == at("2026-10-13 23:10")
    assert due_after(at("2026-10-14 03:00"), TZ, (60, 6 * 60)) == at("2026-10-14 06:00")  # a window within one day


# the recap note (code-written)


def test_the_recap_lists_the_points_under_its_header() -> None:
    points = [Point("Zaterdag ik ben", "Zaterdag ben ik", "The verb comes second.")]
    text = recap_text("nl", "A2", points, heard=False)
    assert text.splitlines() == [
        prompts.BABBLE_RECAP_HEADER.format(english="Dutch", level="A2"),
        prompts.BABBLE_RECAP_POINT.format(
            said="Zaterdag ik ben", better="Zaterdag ben ik", why="The verb comes second."
        ),
    ]
    assert text.startswith("📚 Practice recap (Dutch, A2)")


def test_a_recap_without_points_says_well_done() -> None:
    text = recap_text("de", "B1", [], heard=False)
    assert text == f"📚 Practice recap (German, B1)\n{prompts.BABBLE_RECAP_NONE}"
    assert prompts.BABBLE_RECAP_NONE == "No mistakes to note: well done."


def test_a_recap_of_heard_turns_says_so() -> None:
    assert recap_text("nl", "A2", [], heard=True).splitlines()[-1] == prompts.BABBLE_RECAP_HEARD
    assert prompts.BABBLE_RECAP_HEARD not in recap_text("de", "A2", [], heard=False)


# the session, the recap call and the idle loop


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Harness:
    """A Babble on a temporary store, with a fake Gemma, a clock, and a send that marks notes sent (or failed)."""

    def __init__(
        self, store: Store, gemma: FakeGemma, *, now: str = "2026-10-14 14:00", language: str = "en", ok: bool = True
    ) -> None:
        self.store = store
        self.gemma = gemma
        self.clock = Clock(at(now))
        self.sent: list[str] = []
        self.changes = 0
        self.ok = ok
        self.calling = False
        self.answering = False
        self.locked = False  # /lock
        self.ruled: list[tuple[str, str]] = []
        self.said = 0
        self.babble = Babble(
            store,
            gemma,
            TZ,
            send=self.send,
            user_language=lambda: language,
            quiet_hours=NIGHT,
            rules=self.rules,
            on_change=self.changed,
            in_call=lambda: self.calling,
            busy=lambda: self.answering,
            locked=lambda: self.locked,
            clock=self.clock,
        )

    async def send(self, out_id: int) -> None:
        if not self.ok:
            self.store.set_status(out_id, "failed")
            return
        self.store.mark_sent(out_id, int(self.clock.now * 1000))
        self.sent.append(self.store.message(out_id).text)

    def rules(self, code: str, level: str) -> str:
        self.ruled.append((code, level))
        return f"PRACTICE RULES {code} {level}"

    def changed(self) -> None:
        self.changes += 1

    def say(self, words: str, minutes_ago: float = 0.0) -> int:
        """A practice message from the user, `minutes_ago` before the clock's now."""
        session = self.babble.running(ME)
        assert session is not None
        chat = self.store.current_conversation()
        self.said += 1  # a millisecond apart: each one its own delivery
        ts = int((self.clock.now - minutes_ago * 60) * 1000) - 100 + self.said
        message_id = self.store.add_incoming(chat.id, ME, ts, "text", words, defer_seq=True)
        assert message_id is not None
        self.store.assign_seq(message_id, session.conversation_id)
        return message_id


def recap(*items: tuple[str, str, str]) -> Completion:
    return text(json.dumps({"items": [{"said": s, "better": b, "why": w} for s, b, w in items]}))


def test_start_keeps_the_setting_and_opens_a_practice_conversation(store: Store) -> None:
    harness = Harness(store, FakeGemma())
    chat = store.current_conversation()
    session = harness.babble.start(ME, "nl", "A2")
    assert json.loads(store.get_state(LAST_KEY) or "") == {"code": "nl", "level": "A2"}
    saved = json.loads(store.get_state(SESSION_KEY) or "")
    assert (saved["code"], saved["level"], saved["aci"], saved["conversation"], saved["ended"]) == (
        "nl",
        "A2",
        ME,
        session.conversation_id,
        0,
    )
    assert store.conversation(session.conversation_id).kind == "practice"
    assert store.current_conversation().id == chat.id
    assert harness.babble.running(ME) == session and harness.babble.running() == session
    assert harness.babble.running(STRANGER) is None  # other allowed users stay in chat
    assert harness.babble.last() == ("nl", "A2") and harness.babble.held() is None
    assert harness.changes == 1


def test_a_turn_carries_the_practice_rules_and_the_languages_fallback(store: Store) -> None:
    harness = Harness(store, FakeGemma())
    harness.babble.start(ME, "ja", "B1")
    turn = harness.babble.turn(spoken=True)
    assert (turn.rules, turn.fallback, turn.spoken) == ("PRACTICE RULES ja B1", prompts.PRACTICE_FALLBACK["ja"], True)
    assert not harness.babble.turn(spoken=False).spoken


async def test_the_recap_asks_gemma_over_the_users_own_turns_only(store: Store) -> None:
    gemma = FakeGemma(recap(("Zaterdag ik ben", "Zaterdag ben ik", "Das Verb steht an zweiter Stelle.")))
    harness = Harness(store, gemma, language="de")
    harness.babble.start(ME, "nl", "A2")
    harness.say("Zaterdag ik ben naar het strand gegaan.")
    harness.say("[voice] Het was  mooi weer")
    harness.say("[audio file] iemand anders")
    harness.say("[call] (cutting in) Nee, morgen")
    session = harness.babble.running(ME)
    assert session is not None
    reply = store.add_outgoing(session.conversation_id, ME, "text", "Wat leuk!", None)
    store.mark_sent(reply, 1)
    harness.babble.end("off")
    held = harness.babble.held()
    assert held is not None
    await harness.babble.finish(held)
    [call] = gemma.calls
    assert call["json_schema"] == prompts.PRACTICE_RECAP_SCHEMA
    assert (call["temperature"], call["reasoning_effort"], call["tools"]) == (0, "none", None)
    assert call["max_tokens"] == 200 + 60 * 3
    system, user = call["messages"][0]["content"], call["messages"][1]["content"]
    assert "Nederlands (Dutch)" in system and "level A2" in system and "German" in system
    assert user.splitlines() == [
        prompts.PRACTICE_RECAP_INPUT,
        "1. Zaterdag ik ben naar het strand gegaan.",
        "2. Het was mooi weer",
        "3. Nee, morgen",
    ]
    assert harness.sent == [
        "📚 Practice recap (Dutch, A2)\n"
        '• "Zaterdag ik ben" → "Zaterdag ben ik": Das Verb steht an zweiter Stelle.\n'
        f"{prompts.BABBLE_RECAP_HEARD}"
    ]


async def test_a_point_the_checks_drop_never_reaches_the_note(store: Store) -> None:
    gemma = FakeGemma(
        recap(
            ("de station", "het station", "'Station' is a het-word."),
            ("de huis", "het huis", "'Huis' is a het-word."),  # never said
            ("Ik woon", "Ik woon.", "Full stop."),  # punctuation only
            ("graag", "graag", "This is correct."),  # a fourth item is never read
        )
    )
    harness = Harness(store, gemma)
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon bij de station")
    harness.babble.end("off")
    await harness.babble.finish(harness.babble.held())  # type: ignore[arg-type]
    [note] = harness.sent
    assert note.splitlines()[1:] == ['• "de station" → "het station": \'Station\' is a het-word.']


async def test_points_that_the_checks_all_drop_are_no_mistakes_without_well_done(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap(("de huis", "het huis", "'Huis' is a het-word."))))  # never said
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon bij het station.")
    harness.babble.end("off")
    await harness.babble.finish(harness.babble.held())  # type: ignore[arg-type]
    assert harness.sent == [f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_NONE_KEPT}"]
    assert prompts.BABBLE_RECAP_NONE_KEPT == "No mistakes to note."


async def test_no_points_is_well_done_and_typed_turns_need_no_heard_line(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap()))
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon in Utrecht.")
    harness.babble.end("off")
    await harness.babble.finish(harness.babble.held())  # type: ignore[arg-type]
    assert harness.sent == [f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_NONE}"]


async def test_spoken_german_turns_need_no_heard_line(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap()))
    harness.babble.start(ME, "de", "A2")
    harness.say("[voice] Ich wohne in Berlin.")
    harness.babble.end("off")
    await harness.babble.finish(harness.babble.held())  # type: ignore[arg-type]
    assert prompts.BABBLE_RECAP_HEARD not in harness.sent[0]


async def test_a_session_without_practice_turns_says_so_without_gemma(store: Store) -> None:
    gemma = FakeGemma()
    harness = Harness(store, gemma)
    session = harness.babble.start(ME, "fr", "B1")
    harness.babble.end("off")
    await harness.babble.finish(harness.babble.held())  # type: ignore[arg-type]
    assert harness.sent == [f"📚 Practice recap (French, B1)\n{prompts.BABBLE_RECAP_NOTHING}"] and gemma.calls == []
    assert store.get_state(SESSION_KEY) == "" and harness.babble.last() == ("fr", "B1")
    ended = store._conn.execute("SELECT ended_at FROM conversations WHERE id = ?", (session.conversation_id,))
    assert ended.fetchone()[0] is not None


@pytest.mark.parametrize(
    "failures",
    [
        [GemmaUnavailableError("down")],  # Gemma is down: not asked again (its client retries)
        [Completion(content='{"items": [', finish_reason="length")] * 2,  # asked once more, cut off again
        [text("not json")] * 2,
        [text('{"points": []}')] * 2,
        [text("not json"), GemmaUnavailableError("down")],
    ],
)
async def test_a_failed_recap_is_codes_note_and_the_session_is_cleared(
    store: Store, failures: list[Completion | Exception]
) -> None:
    gemma = FakeGemma(*failures)
    harness = Harness(store, gemma)
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon bij de station")
    harness.babble.end("off")
    await harness.babble.finish(harness.babble.held())  # type: ignore[arg-type]
    assert len(gemma.calls) == len(failures) and not gemma.queue
    assert harness.sent == [f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_FAILED}"]
    assert harness.babble.held() is None and harness.babble.running() is None


# a recap cut off at its token limit (the practice evals, it-conv-3: a "why" that looped "Let's re-evaluate")
LOOPING = " 'Gli' is singular. 'Loro' is plural. Let's re-evaluate." * 3
ITEM = {"said": "ho andato", "better": "sono andato", "why": "'Andare' takes 'essere'."}
GOOD = recap(("ho andato", "sono andato", "'Andare' takes 'essere'."))
TURNS = ["Ieri ho andato al cinema.", "Gli è piaciuto il film."]


def cut(content: str) -> Completion:
    return Completion(content=content, prompt_tokens=100, finish_reason="length")


async def test_a_cut_off_recap_keeps_its_complete_points(caplog: pytest.LogCaptureFixture) -> None:
    head = json.dumps({"items": [ITEM]})[:-2]  # the list and the object left open
    gemma = FakeGemma(cut(f'{head}, {{"said": "Gli", "better": "A loro", "why": "{LOOPING}'))
    points = await babble.ask_recap(gemma, "it", "A2", TURNS, "en")
    assert points == [Point("ho andato", "sono andato", "'Andare' takes 'essere'.")]
    assert len(gemma.calls) == 1  # not asked again
    assert "cut off: 1 complete points kept" in caplog.text


async def test_a_cut_off_recap_with_no_complete_point_is_asked_once_more_with_short_whys() -> None:
    gemma = FakeGemma(cut(f'{{"items": [{{"said": "Gli", "better": "A loro", "why": "{LOOPING}'), GOOD)
    points = await babble.ask_recap(gemma, "it", "A2", TURNS, "en")
    assert points == [Point("ho andato", "sono andato", "'Andare' takes 'essere'.")]
    first, second = (call["messages"][0]["content"] for call in gemma.calls)
    assert prompts.PRACTICE_RECAP_SHORT_WHY not in first
    assert second == f"{first}\n\n{prompts.PRACTICE_RECAP_SHORT_WHY}"
    assert gemma.calls[0]["messages"][1:] == gemma.calls[1]["messages"][1:]  # the same turns
    assert gemma.calls[1]["json_schema"] == prompts.PRACTICE_RECAP_SCHEMA
    assert "one short sentence" in prompts.PRACTICE_RECAP_SHORT_WHY


@pytest.mark.parametrize("first", [text("Sure! Here are the mistakes."), text('{"points": []}'), cut('{"items": [')])
async def test_an_unreadable_recap_is_asked_once_more(first: Completion) -> None:
    gemma = FakeGemma(first, GOOD)
    points = await babble.ask_recap(gemma, "it", "A2", TURNS, "en")
    assert points == [Point("ho andato", "sono andato", "'Andare' takes 'essere'.")] and len(gemma.calls) == 2


async def test_a_recap_cut_off_twice_keeps_the_complete_points_of_the_second() -> None:
    head = json.dumps({"items": [ITEM]})[:-2]
    gemma = FakeGemma(cut('{"items": [{"said": "Gli'), cut(f'{head}, {{"said": "Gli'))
    points = await babble.ask_recap(gemma, "it", "A2", TURNS, "en")
    assert points == [Point("ho andato", "sono andato", "'Andare' takes 'essere'.")]


async def test_a_list_that_closed_before_the_cut_is_the_whole_recap() -> None:
    gemma = FakeGemma(cut('{"items": []'))  # nothing to note, cut off before the last brace
    assert await babble.ask_recap(gemma, "it", "A2", TURNS, "en") == []
    assert len(gemma.calls) == 1


async def test_a_recap_cut_off_twice_with_no_complete_point_is_codes_note(store: Store) -> None:
    gemma = FakeGemma(cut('{"items": [{"said": "de'), cut('{"items": [{"said": "de station", "better'))
    harness = Harness(store, gemma)
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon bij de station")
    harness.babble.end("off")
    await harness.babble.finish(harness.babble.held())  # type: ignore[arg-type]
    assert harness.sent == [f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_FAILED}"]  # never silent


async def test_complete_points_of_a_cut_off_recap_reach_the_note(store: Store) -> None:
    head = json.dumps({"items": [{"said": "de station", "better": "het station", "why": "'Station' is a het-word."}]})
    gemma = FakeGemma(cut(f'{head[:-2]}, {{"said": "woon", "better": "wonen", "why": "{LOOPING}'))
    harness = Harness(store, gemma)
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon bij de station")
    harness.babble.end("off")
    await harness.babble.finish(harness.babble.held())  # type: ignore[arg-type]
    [note] = harness.sent
    assert note.splitlines()[1:] == ['• "de station" → "het station": \'Station\' is a het-word.']


async def test_a_recap_that_could_not_be_sent_is_a_failed_note_never_sent_twice(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap()), ok=False)
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon in Utrecht.")
    harness.babble.end("off")
    held = harness.babble.held()
    assert held is not None
    await harness.babble.finish(held)
    [failed] = store.unsent_outgoing()  # recovery resends it
    assert failed.status == "failed" and failed.kind == "note"
    assert harness.babble.held() is None
    await harness.babble.finish(held)  # an old copy of the session finishes nothing
    assert len(store.unsent_outgoing()) == 1


async def test_two_finishes_at_once_send_one_recap(store: Store) -> None:
    answer: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    harness = Harness(store, FakeGemma(answer))
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon in Utrecht.")
    harness.babble.end("off")
    held = harness.babble.held()
    assert held is not None
    first = asyncio.create_task(harness.babble.finish(held))
    second = asyncio.create_task(harness.babble.finish(held))
    await asyncio.sleep(0)
    answer.set_result(recap())
    await asyncio.gather(first, second)
    assert len(harness.sent) == 1 and len(harness.gemma.calls) == 1


async def test_the_recap_reads_at_most_the_latest_forty_turns(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap()))
    harness.babble.start(ME, "nl", "A2")
    for number in range(45):
        harness.say(f"zin {number}", minutes_ago=45 - number)
    harness.babble.end("off")
    await harness.babble.finish(harness.babble.held())  # type: ignore[arg-type]
    lines = harness.gemma.calls[0]["messages"][1]["content"].splitlines()[1:]
    assert len(lines) == 40 and lines[0] == "1. zin 5" and lines[-1] == "40. zin 44"


async def test_the_recap_reads_at_most_twelve_thousand_characters(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap()))
    harness.babble.start(ME, "nl", "A2")
    for number in range(5):
        harness.say(f"{number} " + "x" * 3_900, minutes_ago=5 - number)  # three fit in 12,000 characters
    harness.babble.end("off")
    await harness.babble.finish(harness.babble.held())  # type: ignore[arg-type]
    lines = harness.gemma.calls[0]["messages"][1]["content"].splitlines()[1:]
    assert [line.split()[1] for line in lines] == ["2", "3", "4"]


async def test_half_an_hour_without_practice_ends_it_with_the_recap(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap()), now="2026-10-14 14:31")
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon in Utrecht.")
    harness.clock.now += 29 * 60
    await harness.babble.tick()
    assert harness.sent == [] and harness.babble.running(ME) is not None
    harness.clock.now = at("2026-10-14 15:01")  # 30 minutes after the last practice message
    await harness.babble.tick()
    # the user may not know it ended: the recap says why
    assert harness.sent == [f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_NONE}\n{prompts.BABBLE_RECAP_IDLE}"]
    assert store.get_state(SESSION_KEY) == "" and harness.babble.running() is None
    assert (
        prompts.BABBLE_RECAP_IDLE == "Practice ended after 30 minutes without a practice message. /babble resumes it."
    )


async def test_a_session_with_no_message_is_idle_from_its_start(store: Store) -> None:
    harness = Harness(store, FakeGemma(), now="2026-10-14 14:00")
    harness.babble.start(ME, "nl", "A2")
    harness.clock.now = at("2026-10-14 14:30")
    await harness.babble.tick()
    assert harness.sent == [
        f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_NOTHING}\n{prompts.BABBLE_RECAP_IDLE}"
    ]


async def test_a_session_idle_at_night_ends_then_its_recap_waits_for_the_morning(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap()), now="2026-10-14 22:00")
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon in Utrecht.")
    harness.clock.now = at("2026-10-14 22:30")
    await harness.babble.tick()
    assert harness.babble.running() is None and harness.babble.held() is not None
    assert harness.sent == [] and harness.gemma.calls == []  # Gemma writes it in the morning
    harness.clock.now = at("2026-10-15 07:59")
    await harness.babble.tick()
    assert harness.sent == []
    harness.clock.now = at("2026-10-15 08:00")
    await harness.babble.tick()
    assert len(harness.sent) == 1 and harness.babble.held() is None


async def test_the_idle_loop_waits_during_a_call_or_an_answer(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap()), now="2026-10-14 14:00")
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon in Utrecht.")
    harness.clock.now = at("2026-10-14 15:00")
    harness.calling = True
    await harness.babble.tick()
    harness.calling, harness.answering = False, True
    await harness.babble.tick()
    assert harness.sent == [] and harness.babble.running() is not None
    harness.answering = False
    await harness.babble.tick()
    assert len(harness.sent) == 1


# /lock: no answers at all; a recap that falls due while locked waits until /unlock


async def test_while_locked_the_idle_loop_does_nothing_and_the_recap_comes_after_unlock(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap()), now="2026-10-14 14:00")
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon in Utrecht.")
    harness.locked = True
    harness.clock.now = at("2026-10-14 16:00")
    await harness.babble.tick()
    assert harness.sent == [] and harness.gemma.calls == [] and harness.babble.running() is not None
    harness.locked = False  # /unlock
    await harness.babble.tick()
    assert harness.sent == [f"📚 Practice recap (Dutch, A2)\n{prompts.BABBLE_RECAP_NONE}\n{prompts.BABBLE_RECAP_IDLE}"]
    assert harness.babble.session() is None


async def test_a_recap_that_falls_due_while_locked_waits_until_unlock(store: Store) -> None:
    harness = Harness(store, FakeGemma(recap()), now="2026-10-14 14:00")
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon in Utrecht.")
    harness.locked = True  # /lock hung up a practice call: practice ends, its recap waits
    ended = harness.babble.end("call")
    assert ended is not None
    await harness.babble.finish(ended)
    assert harness.sent == [] and harness.gemma.calls == []
    assert harness.babble.held() == ended and store.count_messages() == 1  # nothing stored for it either
    harness.clock.now += 3600
    await harness.babble.tick()
    assert harness.sent == [] and harness.babble.held() == ended
    harness.locked = False
    await harness.babble.tick()
    assert len(harness.sent) == 1 and harness.babble.held() is None


async def test_a_lock_during_the_recaps_gemma_call_keeps_it_held_and_it_goes_out_once_after_unlock(
    store: Store,
) -> None:
    answer: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    harness = Harness(store, FakeGemma(answer, recap()), now="2026-10-14 14:00")
    harness.babble.start(ME, "nl", "A2")
    harness.say("Ik woon in Utrecht.")
    ended = harness.babble.end("off")
    assert ended is not None
    finishing = asyncio.create_task(harness.babble.finish(ended))
    await asyncio.sleep(0)
    harness.locked = True  # /lock while Gemma writes the recap
    answer.set_result(recap())
    await finishing
    assert harness.sent == [] and harness.babble.held() == ended and store.count_messages() == 1
    harness.locked = False
    await harness.babble.tick()
    await harness.babble.tick()
    assert len(harness.sent) == 1 and harness.babble.held() is None and len(harness.gemma.calls) == 2


def test_a_damaged_state_is_no_session(store: Store) -> None:
    harness = Harness(store, FakeGemma())
    store.set_state(SESSION_KEY, "{not json")
    store.set_state(LAST_KEY, '{"code": "xx", "level": "A2"}')
    assert harness.babble.running() is None and harness.babble.held() is None and harness.babble.last() is None
