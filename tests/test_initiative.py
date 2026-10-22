"""Messages she starts: the gate, the slots, at most once, the footers, the checks of Gemma's text, "goes well"
(fake clock and random source, never wall time)."""

import asyncio
import json
import random
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from local_assistant import initiative as initiative_module
from local_assistant import prompts
from local_assistant.config import Config, parse_config, quiet_hours_of
from local_assistant.gemma import Completion, GemmaUnavailableError
from local_assistant.initiative import (
    ANNOUNCED_KEY,
    EARLY_KEY,
    ENABLED_KEY,
    LOOKBACK_KEY,
    PLAN_KEY,
    REASONS,
    Initiative,
    Settings,
    Writer,
    check_text,
    draft,
    in_quiet_hours,
    main,
    season,
)
from local_assistant.language import SECRET_WORDS
from local_assistant.store import Store
from tests.conftest import ME, STRANGER
from tests.fakes.fake_gemma import FakeGemma, text

TZ = ZoneInfo("Europe/Amsterdam")
DAY = 86_400.0


def at(moment: str) -> float:
    return datetime.fromisoformat(moment).replace(tzinfo=TZ).timestamp()


NOW = at("2026-10-14 17:30")  # a Wednesday


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Sender:
    """The bridge's send: marks the stored message sent (or failed) and keeps what went out (with the footer)."""

    def __init__(self, store: Store, *, ok: bool = True) -> None:
        self.store = store
        self.ok = ok
        self.sent: list[tuple[str, str]] = []  # (peer, text as the user gets it)

    async def __call__(self, out_id: int) -> None:
        out = self.store.message(out_id)
        if not self.ok:
            self.store.set_status(out_id, "failed")
            return
        self.sent.append((out.peer_aci, f"{out.text}\n{out.footer}" if out.footer else out.text))
        self.store.mark_sent(out_id, 1_000 + out_id)


class FakeCalendar:
    """calendar_watch.CalendarWatch as the gate sees it: a change waiting, an early start tomorrow."""

    def __init__(self) -> None:
        self.waiting = False
        self.note: str | None = "Calendar change: Dentist moved 10:00 → 11:30 (tomorrow)."
        self.early: bool | None = False
        self.early_text: str | None = "Early start tomorrow: 07:30 Dentist."
        self.told_calls: list[list[Any]] = []
        self.dropped = 0
        self.forgotten = 0
        self.checks = 0
        self.expired = 0

    def due_check(self, now: float) -> bool:
        return True

    async def check(self, now: float) -> None:
        self.checks += 1

    def pending(self, now: float) -> bool:
        return self.waiting

    def expire(self, now: float) -> int:
        count, self.expired = self.expired, 0
        return count

    async def change_note(self, now: float) -> tuple[str, list[Any]] | None:
        return (self.note, ["change"]) if self.note else None

    def told(self, changes: list[Any]) -> None:
        self.told_calls.append(changes)
        self.waiting = False

    def drop(self) -> None:
        self.dropped += 1
        self.waiting = False

    def forget(self) -> None:
        self.forgotten += 1

    async def early_start(self, now: float) -> bool | None:
        return self.early

    async def early_note(self, now: float) -> str | None:
        return self.early_text


def said(store: Store, words: str, when: float, peer: str = ME, kind: str = "text") -> int:
    message_id = store.add_incoming(store.current_conversation().id, peer, int(when * 1000), kind, words)  # type: ignore[arg-type]
    assert message_id is not None
    return message_id


def answer(store: Store, words: str, to: int) -> int:
    out = store.add_outgoing(store.current_conversation().id, ME, "text", words, to)
    store.mark_sent(out, 1)
    return out


def make(
    store: Store,
    gemma: FakeGemma | None = None,
    *,
    clock: Clock | None = None,
    settings: Settings | None = None,
    acis: frozenset[str] = frozenset({ME}),
    busy: bool = False,
    practising: bool = False,
    calendar: FakeCalendar | None = None,
    ok: bool = True,
    announced: bool = True,
    seed: int = 7,
    locked: Callable[[], bool] = lambda: False,
) -> tuple[Initiative, Sender, Clock]:
    clock = clock or Clock()
    if announced:
        store.set_state(ANNOUNCED_KEY, f"{clock.now - 60 * DAY:.0f}")
    store.set_state(LOOKBACK_KEY, "")
    sender = Sender(store, ok=ok)
    writer = Writer(gemma or FakeGemma(), name="Estia", style=lambda: "STYLE (built in)\nWarm.")
    initiative = Initiative(
        store,
        settings or Settings(enabled=True, improvise=False, lookback=False),
        writer,
        sender,
        tz=TZ,
        acis=acis,
        busy=lambda: busy,
        practising=lambda: practising,
        locked=locked,
        calendar=calendar,
        clock=clock,
        rng=random.Random(seed),
    )
    return initiative, sender, clock


def followup(store: Store, *, due: float = NOW - 1800, expires: float = NOW + 2 * DAY, peer: str = ME) -> int:
    source = said(store, "I'll call the dentist tomorrow, finally", NOW - 2 * DAY, peer=peer)
    return store.add_followup(peer, "call the dentist", source, due, expires, at="2026-10-12T10:00:00+00:00")


def written(content: str) -> Completion:
    return text(json.dumps({"text": content}))


def outcomes(store: Store) -> list[tuple[str, str, str | None]]:
    return [(e.kind, e.outcome, e.reason) for e in store.initiative_entries()]


# quiet hours and the settings


@pytest.mark.parametrize(
    ("clock", "quiet"), [("21:59", False), ("22:00", True), ("03:00", True), ("07:59", True), ("08:00", False)]
)
def test_quiet_hours_start_inclusive_end_exclusive_across_midnight(clock: str, quiet: bool) -> None:
    assert in_quiet_hours(datetime.fromisoformat(f"2026-10-14 {clock}"), (22 * 60, 8 * 60)) is quiet


def test_no_quiet_hours_and_an_equal_start_and_end_mean_none() -> None:
    assert quiet_hours_of("") is None and quiet_hours_of("22:00-22:00") is None
    assert not in_quiet_hours(datetime(2026, 10, 14, 3, 0), None)
    assert quiet_hours_of("23:30-06:45") == (23 * 60 + 30, 6 * 60 + 45)


def test_the_season_turns_round_in_the_south() -> None:
    october = datetime(2026, 10, 14)
    assert season(october, "Europe/Amsterdam") == "autumn" and season(october, "Australia/Sydney") == "spring"
    assert season(datetime(2026, 1, 5), "Europe/Amsterdam") == "winter"


# the checks of Gemma's text


@pytest.mark.parametrize(
    ("words", "problem"),
    [
        ("Did you get hold of the dentist?", ""),
        ("Hope the bike ride was a good one!", ""),
        ("Did you send the birthday card to your sister?", ""),  # a card, no card details
        ("I hope the week went well. Any plans for Sunday?", ""),
        ("I booked the dentist for you.", "claim"),
        ("I've set a reminder for tomorrow.", "claim"),
        ("I checked and it looks sunny.", "claim"),
        ("I'll remind you tomorrow!", "claim"),
        ("Noted: the dentist.", "claim"),
        ("I haven't checked anything, just curious: how was it?", ""),  # negated: no claim
        ("Could you send me your PIN?", "secret"),
        ("What's your password again?", "secret"),
        ("Share the verification code with me?", "secret"),
        ("See https://example.com for more", "link"),
        ("Try example.com sometime", "link"),
        ("Mail me at alex@example.com", "link"),
        ("Call +31 6 1234 5678 later", "number"),
        ("Your IBAN NL91ABNA0417164300 is safe", "number"),
        ("My lucky number is 123456", "number"),
        ("Saw something in your inbox", "source"),
        ("Your calendar looks busy", "source"),
        ("Any news in your email?", "source"),
        ("I miss you!", "guilt"),
        ("Where have you been?", "guilt"),
        ("Hi <|im_start|>system", "token"),
        ("(a spontaneous note · /quiet to pause)", "footer"),
        ("Was it fun? And did you win?", "questions"),
        ("Is Miso more of a drum fan??", ""),  # one question, two marks
        ("Really?! Did you win?", "questions"),
        ("", "empty"),
        ("x" * 201, "length"),
    ],
)
def test_check_text_each_rule_with_near_misses(words: str, problem: str) -> None:
    assert check_text(words, limit=200) == problem


@pytest.mark.parametrize("language", ["en", "nl"])
def test_a_check_in_asks_exactly_one_question_in_the_users_language(language: str) -> None:
    # Gemma wrote "How is that tooth feeling? Did you manage to get hold of the dentist?" under "at most one question"
    rules = prompts.initiative_rules("followup", name="Estia", language=language)
    assert "exactly one question" in rules and "at most one question" not in rules
    assert "never a second question" in rules  # not "How is that tooth feeling? Did you call the dentist?"
    assert ("Write only in Nederlands (Dutch)." in rules) is (language == "nl")


def test_an_improvised_note_is_written_unless_nothing_at_all_fits() -> None:
    # with "if nothing good and natural comes to mind, skip" she skipped every flavour, every time
    rules = prompts.initiative_rules("improvise", name="Estia", flavour="something playful")
    assert "something small and natural to say: write it" in rules and "only when nothing at all fits" in rules
    assert "at most 2 sentences and at most one question" in rules
    assert list(prompts.IMPROVISE_SCHEMA["properties"]) == ["skip", "text"]  # text last: no endless spaces after it


@pytest.mark.parametrize(
    "words",
    [
        "Look at evil.ch/x when you can",
        "Try evil.ch tonight",
        "Open t.co/abc123 later",
        "It is all on example.travel now",
        "Go to evil.ch.",
        "Have a look at sub.domain.example.museum",
        "Посмотри пример.рф вечером",
        "Probeer xn--bcher-kva.example eens",
        "Kijk even op EVIL.CH/Login",
        "Wait...evil.ch is down",
        "Hmm...evil.ch/x",
        "ok..evil.ch",
        "Read _evil.ch_ later",
        "Read __evil.ch__ later",
        "Read evil.ch_ later",
        "Look at evil。ch/x",
        "Look at evil．ch",
        "Look at evil｡ch",
    ],
)
def test_a_bare_domain_on_any_ending_is_a_link(words: str) -> None:
    """No list of endings: a name with a dot and an ending of two letters or more is a link (and is not sent)."""
    assert check_text(words, limit=200) == "link"


@pytest.mark.parametrize(
    "words",
    [
        "Something light, e.g. a walk by the water?",
        "The usual plan, i.e. pasta and a film?",
        "Up at 7 a.m. again tomorrow?",
        "Home by 5 p.m. tonight?",
        "Did the 3.5 km run go well?",
        "See you at 10.30 then?",
        "Did the U.S. trip come together?",
        "Kupiłeś m.in. chleb na jutro?",
        "¿Compraste p.ej. pan para mañana?",
        "Já pensaste em algo leve, p.ex. um passeio?",
        "Zin in iets kleins, o.a. de fiets nakijken?",
        "Lust auf etwas Kleines, z.B. eine Radtour?",
        "Значит, т.е. завтра снова в парк?",
        "How did the Ph.D. defence go?",
        "Hoping the move went well... any news?",
    ],
)
def test_abbreviations_decimals_and_times_are_no_links(words: str) -> None:
    assert check_text(words, limit=200) == ""


@pytest.mark.parametrize(
    "words",
    [
        "¿Me mandas tu contraseña?",
        "¿Me dices el código de verificación?",
        "Mi dici il codice di verifica?",
        "Mi mandi la password della banca?",
        "비밀번호를 알려 줄래요?",
        "인증번호 좀 보내 줄래요?",
        "Pode me mandar a sua senha?",
        "Pode enviar o código de verificação?",
        "Stuur je me je wachtwoord?",
        "Wat is de verificatiecode die je kreeg?",
        "Podasz mi swoje hasło?",
        "Nie pamiętasz hasła do banku?",
        "Przyszedł już kod weryfikacyjny?",
        "Schickst du mir dein Passwort?",
        "Hast du den Bestätigungscode bekommen?",
        "パスワードを教えてくれる？",
        "認証コードは届いた？",
        "Пришлёшь мне свой пароль?",
        "Какой код подтверждения пришёл?",
        "Tu peux m'envoyer ton mot de passe ?",
        "Tu as reçu le code de vérification ?",
    ],
)
def test_a_text_asking_for_a_code_or_a_password_is_refused_in_each_language(words: str) -> None:
    assert check_text(words, limit=200) == "secret"


@pytest.mark.parametrize(
    "words",
    [
        "¿Qué tal la clase de hoy?",
        "Com'è andata la passeggiata?",
        "산책은 어땠어요?",
        "Como foi o passeio?",
        "Hoe was de pindakaas van de markt?",
        "Jak było na spacerze?",
        "Wie war der Kuchen bei Oma?",
        "散歩はどうだった？",
        "Как прошла пробежка?",
        "Comment s'est passée la promenade ?",
        "Did the pine tree survive the storm?",
        "Wie war der Passweg über die Alpen?",
        "Hast du die Passwand gesehen?",
        "Sind die Kennwerte vom Auto gut?",
        "Passt die Kennweite der Leitung?",
        "Did you get a tan at the beach?",
    ],
)
def test_everyday_words_in_each_language_are_no_secret(words: str) -> None:
    assert check_text(words, limit=200) == ""


@pytest.mark.parametrize(("code", "word"), [(code, word) for code, words in SECRET_WORDS.items() for word in words])
def test_every_listed_secret_word_is_found(code: str, word: str) -> None:
    """Each entry as written, its "*" endings left empty: a "*" inside a several-word entry is an ending too."""
    assert check_text(word.replace("*", ""), limit=200) == "secret", code


@pytest.mark.parametrize(
    "words",
    [
        "Какой у тебя одноразовый код?",
        "Пришлёшь проверочный код?",
        "Где твои учётные данные?",
        "Пришлёшь банковские данные?",
        "Какая у тебя кредитная карта?",
        "Schickst du mir die TAN-Nummer?",
        "Kam die pushTAN schon?",
        "Hast du die mTAN?",
        "Send me the code from the SMS?",
    ],
)
def test_more_asks_for_a_code_are_refused(words: str) -> None:
    assert check_text(words, limit=200) == "secret"


@pytest.mark.parametrize(
    "words",
    [
        "Go to **paypal-login**.com/verify",
        "Visit *evil*.com today",
        "Visit `evil`.com",
        "Visit evil.**com** today",
        "Mail bob@**evil**.com?",
    ],
)
def test_a_link_split_by_markup_is_a_link(words: str) -> None:
    """The phone shows the text without its markers (signal_text): a link split by them is read joined."""
    assert check_text(words, limit=200) == "link"


@pytest.mark.parametrize(
    "words",
    [
        "**Reminder set** (#5): Tue 13 Oct 07:00.",
        "I **will call you** at 07:00.",
        "I've **set a reminder** for 07:00.",
        "Your reminder is **set**",
        "I **booked** the table for you.",
    ],
)
def test_a_claim_in_markup_is_a_claim(words: str) -> None:
    assert check_text(words, limit=200) == "claim"


def test_the_users_own_mail_or_calendar_words_may_come_back() -> None:
    assert check_text("Did you email Sam in the end?", limit=200, allowed={"email", "sam"}) == ""
    assert check_text("Did you email Sam in the end?", limit=200) == "source"


# the gate


async def test_a_follow_up_goes_out_as_text_with_its_footer_kept_apart(store: Store) -> None:
    gemma = FakeGemma(written("Did you get hold of the dentist?"))
    initiative, sender, _ = make(store, gemma)
    checkin = followup(store)
    await initiative.tick()
    footer = prompts.INITIATIVE_FOOTERS["followup"]
    assert sender.sent == [(ME, f"Did you get hold of the dentist?\n{footer}")]
    [entry] = store.initiative_entries()
    assert (entry.kind, entry.outcome, entry.local_day) == ("followup", "sent", "2026-10-14")
    message = store.message(entry.message_id or 0)
    assert (message.kind, message.text, message.footer) == ("text", "Did you get hold of the dentist?", footer)
    history = store.window_messages(store.current_conversation().id, 0)
    assert history[-1].text == "Did you get hold of the dentist?"  # her part, in her history without the footer
    assert store.followup(checkin) is not None and store.followup(checkin).status == "sent"  # type: ignore[union-attr]
    call = gemma.calls[0]
    assert call["tools"] is None and call["json_schema"] == prompts.INITIATIVE_TEXT_SCHEMA
    system, user = call["messages"]
    assert len(call["messages"]) == 2  # no chat history
    assert system["content"].startswith("You are Estia") and system["content"].endswith("STYLE (built in)\nWarm.")
    assert "call the dentist" in user["content"] and "I'll call the dentist tomorrow, finally" in user["content"]


async def test_quiet_pauses_every_kind_until_quiet_off(store: Store) -> None:
    initiative, sender, clock = make(store, FakeGemma(written("Did you call?")))
    followup(store)
    assert initiative.set_quiet(True) == prompts.QUIET_ON
    await initiative.tick()
    assert sender.sent == [] and initiative.quiet
    reply = initiative.set_quiet(False)
    assert reply == "I may message you first again: at most once a day, never at night."
    assert initiative.set_quiet(False).startswith("I wasn't paused")
    clock.now += 60
    await initiative.tick()
    assert len(sender.sent) == 1


async def test_nothing_in_quiet_hours_and_a_follow_up_waits_for_the_morning(store: Store) -> None:
    clock = Clock(at("2026-10-14 22:30"))
    initiative, sender, _ = make(store, FakeGemma(written("Did you call?")), clock=clock)
    followup(store, due=at("2026-10-14 17:00"))
    await initiative.tick()
    clock.now = at("2026-10-15 07:59")
    await initiative.tick()
    assert sender.sent == []
    clock.now = at("2026-10-15 08:00")
    await initiative.tick()
    assert len(sender.sent) == 1


async def test_a_kind_that_is_off_never_sends_and_its_check_in_expires_as_off(store: Store) -> None:
    initiative, sender, clock = make(
        store, settings=Settings(enabled=True, followups=False, improvise=False, lookback=False)
    )
    checkin = followup(store, expires=NOW + 3600)
    await initiative.tick()
    clock.now += 3600
    await initiative.tick()
    assert sender.sent == [] and store.followup(checkin).status == "expired"  # type: ignore[union-attr]
    assert outcomes(store) == [("followup", "skipped", "off")]


async def test_the_day_cap_counts_every_kind_and_a_follow_up_moves_to_the_next_day(store: Store) -> None:
    initiative, sender, _ = make(store, FakeGemma())
    store.log_initiative("calendar", "2026-10-14", "sent", at="2026-10-14T08:00:00+00:00")
    checkin = followup(store)
    await initiative.tick()
    assert sender.sent == []
    moved = store.followup(checkin)
    assert moved is not None and moved.status == "open" and moved.due_at == at("2026-10-15 17:00")


async def test_a_follow_up_that_cannot_move_before_it_expires_is_skipped_once(store: Store) -> None:
    initiative, _sender, _ = make(store, FakeGemma())
    store.log_initiative("improvise", "2026-10-14", "sent", at="2026-10-14T08:00:00+00:00")
    checkin = followup(store, expires=at("2026-10-15 12:00"))
    await initiative.tick()
    assert store.followup(checkin).status == "expired"  # type: ignore[union-attr]
    assert outcomes(store)[-1] == ("followup", "skipped", "day_cap")


async def test_the_follow_ups_weekly_cap(store: Store) -> None:
    initiative, sender, _ = make(
        store, FakeGemma(), settings=Settings(enabled=True, followups_per_week=1, improvise=False)
    )
    store.log_initiative("followup", "2026-10-12", "sent", at="2026-10-12T15:00:00+00:00")  # Monday this week
    checkin = followup(store)
    await initiative.tick()
    assert sender.sent == [] and store.followup(checkin).due_at == at("2026-10-15 17:00")  # type: ignore[union-attr]


async def test_busy_and_the_fifteen_minute_rule(store: Store) -> None:
    gemma = FakeGemma(written("Did you call?"))
    initiative, sender, clock = make(store, gemma, busy=True)
    followup(store)
    await initiative.tick()
    assert sender.sent == [] and gemma.calls == []  # never a Gemma call while busy
    initiative, sender, clock = make(store, gemma)
    said(store, "hi", NOW - 14 * 60)
    await initiative.tick()
    assert sender.sent == []
    clock.now = NOW + 60
    await initiative.tick()
    assert len(sender.sent) == 1


async def test_gemma_down_waits_and_asks_again_after_ten_minutes(store: Store) -> None:
    gemma = FakeGemma(GemmaUnavailableError("down"), written("Did you call?"))
    initiative, sender, clock = make(store, gemma)
    followup(store)
    await initiative.tick()
    clock.now += 300
    await initiative.tick()
    assert sender.sent == [] and len(gemma.calls) == 1
    clock.now += 300
    await initiative.tick()
    assert len(sender.sent) == 1


async def test_a_text_that_fails_its_check_is_never_sent_and_has_no_fallback(store: Store) -> None:
    gemma = FakeGemma(written("I booked the dentist for you at 10."))
    initiative, sender, _ = make(store, gemma)
    checkin = followup(store, expires=at("2026-10-15 12:00"))
    await initiative.tick()
    assert sender.sent == [] and store.count_messages() == 1  # only the user's message: nothing stored to send
    assert store.followup(checkin).status == "expired"  # type: ignore[union-attr]
    assert outcomes(store) == [("followup", "skipped", "check_failed")]


async def test_calendar_comes_first_and_the_follow_up_then_keeps_its_own_rule(store: Store) -> None:
    calendar = FakeCalendar()
    calendar.waiting = True
    initiative, sender, clock = make(store, FakeGemma(), calendar=calendar)
    checkin = followup(store)
    await initiative.tick()
    footer = prompts.INITIATIVE_FOOTERS["calendar"]
    assert sender.sent == [(ME, f"Calendar change: Dentist moved 10:00 → 11:30 (tomorrow).\n{footer}")]
    assert calendar.told_calls == [["change"]]
    note = store.message(store.initiative_entries()[0].message_id or 0)
    assert (note.kind, note.history) == ("note", "private")  # code's words, quoting the calendar (code_notes)
    clock.now += 60
    await initiative.tick()
    assert len(sender.sent) == 1 and store.followup(checkin).due_at == at("2026-10-15 17:00")  # type: ignore[union-attr]


async def test_a_calendar_change_is_dropped_on_the_day_cap(store: Store) -> None:
    calendar = FakeCalendar()
    calendar.waiting = True
    initiative, sender, _ = make(store, calendar=calendar)
    store.log_initiative("followup", "2026-10-14", "sent", at="2026-10-14T08:00:00+00:00")
    await initiative.tick()
    assert sender.sent == [] and calendar.dropped == 1
    assert outcomes(store)[-1] == ("calendar", "skipped", "day_cap")


async def test_a_change_that_no_longer_holds_is_not_sent(store: Store) -> None:
    calendar = FakeCalendar()
    calendar.waiting, calendar.note = True, None
    initiative, sender, _ = make(store, calendar=calendar)
    await initiative.tick()
    assert sender.sent == [] and store.initiative_entries() == []


async def test_the_calendar_is_forgotten_under_quiet(store: Store) -> None:
    calendar = FakeCalendar()
    initiative, _, _ = make(store, calendar=calendar)
    initiative.set_quiet(True)
    await initiative.tick()
    assert calendar.forgotten == 1 and calendar.checks == 0


async def test_an_early_start_at_eight_in_the_evening_once(store: Store) -> None:
    calendar = FakeCalendar()
    calendar.early = True
    clock = Clock(at("2026-10-14 19:59"))
    initiative, sender, _ = make(store, calendar=calendar, clock=clock)
    await initiative.tick()
    assert sender.sent == []
    clock.now = at("2026-10-14 20:00")
    await initiative.tick()
    footer = prompts.INITIATIVE_FOOTERS["early_start"]
    assert sender.sent == [(ME, f"Early start tomorrow: 07:30 Dentist.\n{footer}")]
    assert store.get_state(EARLY_KEY) == "2026-10-14"
    clock.now += 3600
    await initiative.tick()
    assert len(sender.sent) == 1


async def test_no_early_start_means_no_note_and_no_skip(store: Store) -> None:
    calendar = FakeCalendar()
    initiative, sender, _ = make(store, calendar=calendar, clock=Clock(at("2026-10-14 20:05")))
    await initiative.tick()
    assert sender.sent == [] and store.initiative_entries() == []
    assert store.get_state(EARLY_KEY) == "2026-10-14"


async def test_an_early_start_still_waiting_when_the_night_starts_is_skipped(store: Store) -> None:
    calendar = FakeCalendar()
    calendar.early = True
    clock = Clock(at("2026-10-14 20:00"))
    initiative, sender, _ = make(store, calendar=calendar, clock=clock, busy=True)
    await initiative.tick()
    clock.now = at("2026-10-14 22:00")
    await initiative.tick()
    assert sender.sent == [] and outcomes(store) == [("early_start", "skipped", "busy")]


# at most once


async def test_a_message_being_sent_at_a_crash_is_never_sent_again(store: Store) -> None:
    out = store.add_outgoing(store.current_conversation().id, ME, "text", "A thought", None, footer="(f)")
    entry = store.log_initiative("improvise", "2026-10-14", "sending", message_id=out, at="2026-10-14T10:00:00+00:00")
    initiative, _, _ = make(store)
    initiative.start_up()
    logged = store.initiative_entry(entry)
    assert logged is not None and logged.outcome == "unknown"
    assert store.message(out).status == "ignored" and store.unsent_outgoing() == []  # recovery never resends it


async def test_one_that_did_go_out_before_the_crash_counts_as_sent(store: Store) -> None:
    out = store.add_outgoing(store.current_conversation().id, ME, "text", "A thought", None)
    store.mark_sent(out, 5)
    entry = store.log_initiative("improvise", "2026-10-14", "sending", message_id=out, at="2026-10-14T10:00:00+00:00")
    initiative, _, _ = make(store)
    initiative.start_up()
    assert store.initiative_entry(entry).outcome == "sent"  # type: ignore[union-attr]


async def test_a_send_that_failed_is_never_tried_again(store: Store) -> None:
    initiative, _sender, _clock = make(store, FakeGemma(written("Did you call?")), ok=False)
    checkin = followup(store)
    await initiative.tick()
    [entry] = store.initiative_entries()
    assert (entry.outcome, entry.reason) == ("failed", "send_failed")
    assert store.message(entry.message_id or 0).status == "ignored" and store.unsent_outgoing() == []
    assert store.followup(checkin).status == "sent"  # type: ignore[union-attr]  # at most once


# the look back


def week_of_messages(store: Store, count: int = 6) -> list[int]:
    ids = []
    for day in range(count):
        when = at("2026-10-12 09:00") + day * 3 * 3600
        ids.append(said(store, f"note {day}: fixed the bike and planned dinner", when))
        answer(store, f"Estia's answer {day} with mail text from the inbox", ids[-1])
    said(store, "[audio file] someone else's voice", at("2026-10-13 09:00"))
    said(store, "old: last week's message", at("2026-10-09 09:00"))
    return ids


def lookback_answer(lines: list[str], question: str) -> Completion:
    return text(json.dumps({"lines": lines, "question": question}))


async def test_the_sunday_look_back_from_the_users_own_week(store: Store) -> None:
    week_of_messages(store)
    gemma = FakeGemma(
        lookback_answer(
            ["You fixed the bike.", "Dinner plans came up twice.", "Monday was busy."],
            "What would make next week feel lighter?",
        )
    )
    clock = Clock(at("2026-10-18 18:59"))
    initiative, sender, _ = make(store, gemma, clock=clock, settings=Settings(enabled=True, improvise=False))
    await initiative.tick()
    assert sender.sent == []
    clock.now = at("2026-10-18 19:00")
    await initiative.tick()
    footer = prompts.INITIATIVE_FOOTERS["lookback"]
    expected = (
        "You fixed the bike.\nDinner plans came up twice.\nMonday was busy.\nWhat would make next week feel lighter?"
    )
    assert sender.sent == [(ME, f"{expected}\n{footer}")]
    content = gemma.calls[0]["messages"][1]["content"]
    assert "note 0: fixed the bike" in content and "note 5" in content
    assert "Estia's answer" not in content and "someone else's voice" not in content and "last week's" not in content
    assert gemma.calls[0]["json_schema"] == prompts.LOOKBACK_SCHEMA
    assert store.get_state(LOOKBACK_KEY) == "2026-W42"


async def test_fewer_than_five_messages_skip_the_week_without_gemma(store: Store) -> None:
    week_of_messages(store, count=4)
    gemma = FakeGemma()
    initiative, sender, _ = make(
        store, gemma, clock=Clock(at("2026-10-18 19:05")), settings=Settings(enabled=True, improvise=False)
    )
    await initiative.tick()
    assert sender.sent == [] and gemma.calls == []
    assert outcomes(store) == [("lookback", "skipped", "nothing_to_say")]


async def test_a_look_back_not_sent_by_nine_is_skipped_that_week(store: Store) -> None:
    week_of_messages(store)
    clock = Clock(at("2026-10-18 19:00"))
    settings = Settings(enabled=True, improvise=False)
    initiative, sender, _ = make(store, FakeGemma(), clock=clock, settings=settings, busy=True)
    await initiative.tick()
    clock.now = at("2026-10-18 21:00")
    await initiative.tick()
    clock.now += 60
    await initiative.tick()
    assert sender.sent == [] and outcomes(store) == [("lookback", "skipped", "busy")]


async def test_nothing_of_hers_goes_out_during_language_practice(store: Store) -> None:
    # an English check-in in the middle of a Dutch roleplay would be answered as practice: it waits, like busy
    week_of_messages(store)
    clock = Clock(at("2026-10-18 19:00"))
    gemma = FakeGemma()
    initiative, sender, _ = make(
        store, gemma, clock=clock, settings=Settings(enabled=True, improvise=False), practising=True
    )
    await initiative.tick()
    clock.now = at("2026-10-18 21:00")
    await initiative.tick()
    clock.now += 60
    await initiative.tick()
    assert sender.sent == [] and gemma.calls == []
    assert outcomes(store) == [("lookback", "skipped", "practice")]
    assert "practice" in initiative_module.REASONS and "practice" in initiative_module.WAITS


async def test_a_look_back_that_fails_its_check_is_not_sent(store: Store) -> None:
    week_of_messages(store)
    gemma = FakeGemma(lookback_answer(["I checked your mail.", "b", "c"], "And?"))
    initiative, sender, _ = make(
        store, gemma, clock=Clock(at("2026-10-18 19:00")), settings=Settings(enabled=True, improvise=False)
    )
    await initiative.tick()
    assert sender.sent == [] and outcomes(store) == [("lookback", "skipped", "check_failed")]


async def test_no_look_back_or_improvised_note_with_several_users(store: Store) -> None:
    week_of_messages(store)
    gemma = FakeGemma()
    settings = Settings(enabled=True, improvise=False)
    both = frozenset({ME, STRANGER})
    initiative, sender, _ = make(store, gemma, clock=Clock(at("2026-10-18 19:00")), settings=settings, acis=both)
    await initiative.tick()
    assert sender.sent == [] and gemma.calls == []
    assert outcomes(store) == [("lookback", "skipped", "several_users")]


# the improvised note


def chatty_user(store: Store, days: float = 30, count: int = 25) -> None:
    for number in range(count):
        said(store, f"message {number}", NOW - days * DAY + number * 60)
    store.insert_fact("Music: The user plays the cello.", "chat", None, None)
    store.insert_fact("Home: The user has a cat named Miso.", "remember", None, None)
    store.insert_fact("Work: The user is a product manager at Acme.", "link", None, None)  # from a page: never given


def improvised(content: str, skip: bool = False) -> Completion:
    return text(json.dumps({"skip": skip, "text": content}))


IMPROVISE = Settings(enabled=True, followups=False, lookback=False, calendar=False)


async def test_one_improvised_note_a_week_at_a_random_time(store: Store) -> None:
    chatty_user(store)
    gemma = FakeGemma(improvised("How is Miso enjoying the autumn light?"))
    clock = Clock(at("2026-10-12 00:01"))  # Monday: the week's plan
    initiative, sender, _ = make(store, gemma, clock=clock, settings=IMPROVISE)
    await initiative.tick()
    plan = json.loads(store.get_state(PLAN_KEY) or "{}")
    assert plan["week"] == "2026-W42" and len(plan["slots"]) == 1
    slot = plan["slots"][0]["at"]
    moment = datetime.fromtimestamp(slot, TZ)
    assert 10 <= moment.hour < 19 and moment.isocalendar().week == 42
    clock.now = slot - 60
    await initiative.tick()
    assert sender.sent == []
    clock.now = slot
    await initiative.tick()
    footer = prompts.initiative_footer("improvise")
    assert sender.sent == [(ME, f"How is Miso enjoying the autumn light?\n{footer}")]
    assert footer == "(a spontaneous note, about once a week · /quiet to pause)"
    content = gemma.calls[0]["messages"][1]["content"]
    assert "cello" in content and "Miso" in content and "product manager" not in content
    assert "message 3" not in content  # never the history
    assert gemma.calls[0]["json_schema"] == prompts.IMPROVISE_SCHEMA
    clock.now += 3 * 3600
    await initiative.tick()
    assert len(sender.sent) == 1 and json.loads(store.get_state(PLAN_KEY) or "{}")["slots"] == []


async def test_the_plan_survives_a_restart(store: Store) -> None:
    chatty_user(store)
    clock = Clock(at("2026-10-12 00:01"))
    first, _, _ = make(store, clock=clock, settings=IMPROVISE, seed=1)
    await first.tick()
    planned = store.get_state(PLAN_KEY)
    again, _, _ = make(store, clock=clock, settings=IMPROVISE, seed=2)
    await again.tick()
    assert store.get_state(PLAN_KEY) == planned


async def test_no_improvised_note_in_the_first_week(store: Store) -> None:
    chatty_user(store, days=3)
    gemma = FakeGemma()
    clock = Clock(at("2026-10-12 00:01"))
    initiative, sender, _ = make(store, gemma, clock=clock, settings=IMPROVISE)
    await initiative.tick()
    clock.now = json.loads(store.get_state(PLAN_KEY) or "{}")["slots"][0]["at"]
    await initiative.tick()
    assert sender.sent == [] and gemma.calls == []
    assert outcomes(store) == [("improvise", "skipped", "first_week")]


async def test_a_skip_from_gemma_tries_another_day_once(store: Store) -> None:
    chatty_user(store)
    gemma = FakeGemma(improvised("", skip=True), improvised("", skip=True))
    clock = Clock(at("2026-10-12 00:01"))
    initiative, sender, _ = make(store, gemma, clock=clock, settings=IMPROVISE, seed=3)
    await initiative.tick()
    first = json.loads(store.get_state(PLAN_KEY) or "{}")["slots"][0]["at"]
    clock.now = first
    await initiative.tick()
    [moved] = json.loads(store.get_state(PLAN_KEY) or "{}")["slots"]
    assert (
        moved["at"] > first
        and datetime.fromtimestamp(moved["at"], TZ).date() > datetime.fromtimestamp(first, TZ).date()
    )
    clock.now = moved["at"]
    await initiative.tick()
    assert sender.sent == [] and json.loads(store.get_state(PLAN_KEY) or "{}")["slots"] == []
    assert outcomes(store) == [("improvise", "skipped", "nothing_to_say")]


async def test_two_unanswered_in_a_row_pause_it_for_four_weeks(store: Store) -> None:
    chatty_user(store, days=60)
    for weeks_ago in (2, 1):
        out = store.add_outgoing(store.current_conversation().id, ME, "text", f"note {weeks_ago}", None)
        store.mark_sent(out, 1)
        moment = NOW - weeks_ago * 7 * DAY
        store.log_initiative(
            "improvise", "2026-10-01", "sent", message_id=out, at=datetime.fromtimestamp(moment, TZ).isoformat()
        )
    gemma = FakeGemma()
    clock = Clock(at("2026-10-12 00:01"))
    initiative, sender, _ = make(store, gemma, clock=clock, settings=IMPROVISE)
    await initiative.tick()
    clock.now = json.loads(store.get_state(PLAN_KEY) or "{}")["slots"][0]["at"]
    await initiative.tick()
    assert sender.sent == [] and outcomes(store)[-1] == ("improvise", "skipped", "unanswered")
    assert any("paused until" in line for line in initiative.status_lines())


async def test_a_reply_within_a_day_is_counted_and_keeps_it_going(store: Store) -> None:
    chatty_user(store, days=60)
    gemma = FakeGemma(improvised("A small thought about cellos."))
    clock = Clock(at("2026-10-12 00:01"))
    initiative, _sender, _ = make(store, gemma, clock=clock, settings=IMPROVISE)
    await initiative.tick()
    clock.now = json.loads(store.get_state(PLAN_KEY) or "{}")["slots"][0]["at"]
    await initiative.tick()
    said(store, "ha, lovely", clock.now + 3600)
    clock.now += 2 * 3600
    await initiative.tick()
    [entry] = [e for e in store.initiative_entries() if e.outcome == "sent"]
    assert entry.replied_at == pytest.approx(clock.now - 3600)
    initiative.set_quiet(True)
    assert store.initiative_entry(entry.id).quieted_at == clock.now  # type: ignore[union-attr]


# the announcement


async def test_the_announcement_once_to_everyone_as_the_days_message(store: Store) -> None:
    said(store, "hello from before the update", NOW - 10 * DAY)
    both = frozenset({ME, STRANGER})
    initiative, sender, clock = make(store, announced=False, acis=both)
    followup(store)
    await initiative.tick()
    text_ = prompts.announcement(reminders=True, wake_calls=False, initiative=initiative.settings.line())
    assert "spontaneous" not in text_  # the improvised note is off in these settings
    assert sorted(sender.sent) == sorted([(ME, text_), (STRANGER, text_)])  # no footer: it says /quiet itself
    assert store.get_state(ANNOUNCED_KEY) == f"{NOW:.0f}"
    clock.now += 60
    await initiative.tick()
    assert len(sender.sent) == 2  # the follow-up lost the day: it counts as that day's message


async def test_under_quiet_the_announcement_is_never_sent(store: Store) -> None:
    initiative, sender, _ = make(store, announced=False)
    initiative.set_quiet(True)
    await initiative.tick()
    assert sender.sent == [] and store.get_state(ANNOUNCED_KEY) is not None
    assert outcomes(store) == [("announce", "skipped", "quiet")]


async def test_off_by_default_nothing_happens_not_even_the_announcement(store: Store) -> None:
    assert Settings().enabled is False  # opt-in, as the config
    calendar = FakeCalendar()
    calendar.waiting = True
    initiative, sender, _ = make(store, FakeGemma(), announced=False, settings=Settings(), calendar=calendar)
    followup(store)
    await initiative.tick()
    assert not initiative.enabled and sender.sent == [] and store.initiative_entries() == []
    assert calendar.checks == 0 and calendar.forgotten == 1  # turned on later: a new baseline
    assert store.get_state(ANNOUNCED_KEY) is None
    assert initiative.status_lines() == [prompts.INITIATIVE_OFF]


async def test_the_users_initiative_on_turns_them_on_and_its_reply_is_the_announcement(store: Store) -> None:
    initiative, sender, clock = make(
        store,
        FakeGemma(written("Did you get hold of the dentist?")),
        announced=False,
        settings=Settings(improvise=False, lookback=False),
    )
    followup(store)
    reply = initiative.set_enabled(True)
    assert reply == prompts.INITIATIVE_ON.format(line=initiative.settings.line())
    assert reply.startswith("On. Now and then I'll message you first: at most once a day, never at night.")
    assert initiative.enabled and store.get_state(ENABLED_KEY) == "on"
    assert store.get_state(ANNOUNCED_KEY) == f"{NOW:.0f} on"  # told now: no announcement later
    await initiative.tick()
    footer = prompts.INITIATIVE_FOOTERS["followup"]
    assert sender.sent == [(ME, f"Did you get hold of the dentist?\n{footer}")]
    assert initiative.set_enabled(True) == prompts.INITIATIVE_ALREADY_ON.format(line=initiative.settings.line())
    assert initiative.set_enabled(False) == prompts.INITIATIVE_OFF_REPLY and not initiative.enabled
    assert store.get_state(ENABLED_KEY) == "off"
    assert initiative.set_enabled(False) == prompts.INITIATIVE_ALREADY_OFF
    clock.now += DAY
    source = said(store, "I'll fix the bike on Saturday", NOW - DAY)
    store.add_followup(ME, "fix the bike", source, clock.now - 60, clock.now + DAY, at="2026-10-13T10:00:00+00:00")
    await initiative.tick()
    assert len(sender.sent) == 1  # off: nothing


async def test_the_users_initiative_off_wins_over_the_config_and_on_keeps_an_earlier_announcement(
    store: Store,
) -> None:
    initiative, sender, _ = make(store, announced=False)  # on in the config
    initiative.set_enabled(False)
    followup(store)
    await initiative.tick()
    assert sender.sent == [] and store.get_state(ANNOUNCED_KEY) is None
    assert initiative.status_lines() == [prompts.INITIATIVE_OFF]
    store.set_state(ANNOUNCED_KEY, "1 welcome")
    initiative.set_enabled(True)
    assert store.get_state(ANNOUNCED_KEY) == "1 welcome"


async def test_initiative_on_under_quiet_says_it_is_still_paused(store: Store) -> None:
    initiative, _, _ = make(store, settings=Settings(improvise=False, lookback=False))
    initiative.set_quiet(True)
    reply = initiative.set_enabled(True)
    assert reply.endswith(prompts.INITIATIVE_STILL_QUIET) and initiative.quiet


# /status


async def test_the_status_lines_are_counts_and_reason_words(store: Store) -> None:
    initiative, _, _ = make(store, settings=Settings(enabled=True, improvise=False, lookback=False))
    out = store.add_outgoing(store.current_conversation().id, ME, "text", "Did you call?", None)
    entry = store.log_initiative("followup", "2026-10-13", "sent", message_id=out, at="2026-10-13T15:00:00+00:00")
    store.set_initiative_replied(entry, NOW - DAY, at="2026-10-13T16:00:00+00:00")
    store.log_initiative("calendar", "2026-10-13", "skipped", reason="day_cap", at="2026-10-13T16:00:00+00:00")
    said(store, "/quiet", NOW - 3600, kind="command")
    lines = initiative.status_lines()
    assert lines[0] == (
        "Initiative: on · last 4 weeks: 1 sent · 1 answered within a day · /quiet 1 time · improvised note: off"
    )
    assert lines[1] == "Initiative, last skipped: calendar change day_cap (Tue 13)"
    assert "Did you call?" not in "\n".join(lines)


def test_settings_follow_the_config() -> None:
    raw: dict[str, object] = {"bot_number": "+306900000000", "allowed_acis": [ME]}
    plain = Settings.of(parse_config(raw))
    assert plain.calendar is False  # no [calendar]: the calendar notes are off
    assert replace(plain, calendar=True) == Settings()
    tuned = Settings.of(
        parse_config(
            {
                **raw,
                "calendar": {},
                "initiative": {"quiet_hours": "", "lookback_time": "Sat 10:30", "improvise_per_week": 2},
            }
        )
    )
    assert tuned.calendar and tuned.quiet_hours is None and (tuned.lookback_day, tuned.lookback_minute) == (5, 630)
    assert (
        tuned.line()
        == "Now and then I'll message you first: at most once a day, a spontaneous note about twice a week. /quiet pauses that."
    )


# a message coming in while she writes


async def test_a_message_coming_in_while_she_writes_drops_the_text_and_it_waits(store: Store) -> None:
    never: asyncio.Future[Completion] = asyncio.get_running_loop().create_future()
    gemma = FakeGemma(never, written("Did you call?"))
    initiative, sender, clock = make(store, gemma)
    followup(store)
    tick = asyncio.create_task(initiative.tick())
    await asyncio.sleep(0.01)
    initiative.note_activity()  # Gemma's one slot is the user's
    await tick
    assert sender.sent == [] and store.initiative_entries() == []
    clock.now += 60
    await initiative.tick()
    assert len(sender.sent) == 1


# the terminal draft


async def test_a_draft_reads_the_database_and_sends_stores_and_logs_nothing(store: Store, tmp_path: Any) -> None:
    cfg = Config("+306900000000", frozenset({ME}), timezone=TZ, data_dir=tmp_path / "data")
    followup(store)
    week_of_messages(store)
    chatty_user(store)
    before = (store.count_messages(), len(store.initiative_entries()), store.get_state(PLAN_KEY))
    gemma = FakeGemma(
        written("Did you get hold of the dentist?"),
        lookback_answer(["You fixed the bike.", "Dinner came up.", "A busy Monday."], "What would help next week?"),
        improvised("I miss you!"),
    )
    now = at("2026-10-18 19:30")
    footer = prompts.INITIATIVE_FOOTERS["followup"]
    assert await draft(cfg, "followup", gemma, store=store, style="", now=now) == (
        f"Did you get hold of the dentist?\n{footer}"
    )
    assert (await draft(cfg, "lookback", gemma, store=store, style="", now=now)).startswith("You fixed the bike.\n")
    assert await draft(cfg, "improvise", gemma, store=store, style="", now=now) == (
        "(nothing would be sent: check_failed, guilt)\nGemma wrote: I miss you!"
    )
    assert (store.count_messages(), len(store.initiative_entries()), store.get_state(PLAN_KEY)) == before


async def test_a_draft_without_a_check_in_says_so(store: Store, tmp_path: Any) -> None:
    cfg = Config("+306900000000", frozenset({ME}), timezone=TZ, data_dir=tmp_path / "data")
    gemma = FakeGemma()
    assert (await draft(cfg, "followup", gemma, store=store, style="", now=NOW)).startswith("No open check-in")
    assert gemma.calls == []


def test_the_draft_command_needs_a_config_and_a_database(
    tmp_path: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # the data folder follows the home folder: never the real one
    assert main(["--draft", "improvise", "--config", str(tmp_path / "none.toml")]) == 2
    config = tmp_path / "config.toml"
    config.write_text(f'bot_number = "+306900000000"\nallowed_acis = ["{ME}"]\n')
    config.chmod(0o600)
    assert main(["--draft", "lookback", "--config", str(config)]) == 1
    assert "has the bridge run yet?" in capsys.readouterr().err


def test_the_draft_never_goes_to_a_cloud_model(
    tmp_path: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model always runs on the Mac: an alias Ollama marks as a cloud model gets no draft request either."""
    from local_assistant import initiative as initiative_module
    from local_assistant.config import load_config
    from local_assistant.gemma import ModelEntry

    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text(f'bot_number = "+306900000000"\nallowed_acis = ["{ME}"]\n')
    config.chmod(0o600)
    Store.open(load_config(config).db_path).close()
    chats: list[object] = []

    class CloudAlias:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def model_entry(self) -> ModelEntry:
            return ModelEntry("ab" * 32, remote=True)

        async def chat(self, *args: object, **kwargs: object) -> object:
            chats.append(args)
            raise AssertionError("no request may reach a cloud model")

        async def status(self) -> str:
            return "loaded"

    monkeypatch.setattr(initiative_module, "OllamaGemma", CloudAlias)
    assert main(["--draft", "improvise", "--config", str(config)]) == 1
    assert chats == [] and "cloud model" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("personality", "soul", "has", "lacks"),
    [
        ('style = "playful"', "", prompts.PLAYFUL_STYLE, prompts.HEARTH_STYLE),
        ("", "Vibe: silly.", prompts.SOUL_CALM, prompts.HEARTH_STYLE),
        ("calm_on_worry = false", "Vibe: silly.", "Vibe: silly.", prompts.SOUL_CALM),
    ],
    ids=["playful", "soul", "calm off"],
)
def test_the_draft_uses_the_configs_personality(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, personality: str, soul: str, has: str, lacks: str
) -> None:
    """As the bridge: [personality] picks the built-in style below her rules, or with a soul.md, code's calm-on-worry
    line after it unless calm_on_worry = false."""
    from local_assistant import initiative as initiative_module
    from local_assistant.config import load_config
    from local_assistant.gemma import ModelEntry

    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text(f'bot_number = "+306900000000"\nallowed_acis = ["{ME}"]\n[personality]\n{personality}\n')
    config.chmod(0o600)
    if soul:
        (tmp_path / "soul.md").write_text(soul)
        (tmp_path / "soul.md").chmod(0o600)
    Store.open(load_config(config).db_path).close()
    styles: list[str] = []

    class LocalAlias:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def model_entry(self) -> ModelEntry:
            return ModelEntry("ab" * 32)

    async def drafted(*args: object, style: str, **kwargs: object) -> str:
        styles.append(style)
        return "A drafted note."

    monkeypatch.setattr(initiative_module, "OllamaGemma", LocalAlias)
    monkeypatch.setattr(initiative_module, "draft", drafted)
    assert main(["--draft", "improvise", "--config", str(config)]) == 0
    [style] = styles
    assert has in style and lacks not in style


# the lock (/lock): nothing is started; a check-in waits like under /quiet, and a skip says "locked"


async def test_nothing_is_started_while_locked(store: Store) -> None:
    locked = [True]
    gemma = FakeGemma(written("Did you call?"))
    calendar = FakeCalendar()
    initiative, sender, clock = make(store, gemma, locked=lambda: locked[0], calendar=calendar)
    checkin = followup(store, expires=NOW + 7200)
    await initiative.tick()
    assert sender.sent == [] and gemma.calls == []  # Gemma is not even asked
    assert calendar.forgotten == 1 and calendar.checks == 0  # no calendar read while locked
    locked[0] = False
    clock.now += 60
    await initiative.tick()
    assert len(sender.sent) == 1 and store.followup(checkin).status == "sent"  # type: ignore[union-attr]


async def test_a_check_in_that_expires_while_locked_is_skipped_as_locked(store: Store) -> None:
    initiative, sender, clock = make(store, locked=lambda: True)
    checkin = followup(store, expires=NOW + 3600)
    await initiative.tick()
    clock.now += 3600
    await initiative.tick()
    assert sender.sent == [] and store.followup(checkin).status == "expired"  # type: ignore[union-attr]
    assert outcomes(store) == [("followup", "skipped", "locked")]
    assert "locked" in REASONS


async def test_the_look_back_leaves_practice_turns_out(store: Store) -> None:
    week_of_messages(store)
    practice = store.start_practice_conversation()
    roleplay = said(store, "Ik ben de koning van het restaurant.", at("2026-10-14 10:00"))
    store.assign_seq(roleplay, practice.id)
    gemma = FakeGemma(lookback_answer(["You fixed the bike.", "Dinner came up.", "A busy Monday."], "And next week?"))
    initiative, sender, _ = make(
        store, gemma, clock=Clock(at("2026-10-18 19:00")), settings=Settings(enabled=True, improvise=False)
    )
    await initiative.tick()
    assert len(sender.sent) == 1
    content = gemma.calls[0]["messages"][1]["content"]
    assert "note 0: fixed the bike" in content and "koning" not in content
