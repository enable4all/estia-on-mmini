"""Whether an answer says a reminder or a call is set, in the 11 languages: the precise set (the bridge's guard and
the check of a text she starts) and the broad set (the Gemma test set's judge)."""

import pytest

from local_assistant import prompts
from local_assistant.claims import (
    CLAIMED_REMINDER,
    CLAIMED_REMINDER_BROAD,
    NEGATION,
    REMINDER_WORDS,
    about_earlier,
    claims_a_reminder,
    claims_a_switch,
    names_music,
    restates,
    switch_clauses,
    without_reminder_talk,
)
from local_assistant.language import CODES


def test_every_language_has_its_patterns() -> None:
    assert set(CLAIMED_REMINDER) == set(CODES) and set(NEGATION) == set(CODES)
    assert set(CLAIMED_REMINDER_BROAD) == set(CODES) and set(REMINDER_WORDS) == set(CODES)


# claims both sets see: she reminds, calls or wakes, or a reminder word with a set word
CLAIMS = [
    # English
    "I'll call you tomorrow at 6:30.",
    "I’ll call you at 6:30 to wake you up.",
    "Sure, I'll remind you tomorrow at 7.",
    "I will wake you at seven.",
    "I'm going to text you at 5 so you don't forget.",
    "I'll send you a reminder at 17:00.",
    "I've noted the reminder for 17:00.",
    "I have set a reminder for you.",
    "I just scheduled your wake-up call.",
    "Done, reminder set for 7:00.",
    "Your reminder has been scheduled.",
    "Your reminder is in place for 17:00 😊",
    "The wake-up call is locked in.",
    "All set: the reminder is in place for 7.",
    "If you want, I'll remind you at 7.",  # an offer said as a promise: still a claim
    "No problem — I'll remind you at 17:00.",  # both clause splits: a dash ends the "no" clause
    "Sure: I'll call you at 6:30.",
    "Noted, and I'll remind you at 8:30.",
    "Tomorrow's wake-up call is set for 6:30.",  # a day's, not someone else's
    "Your reminder for 17:00 is set.",
    "Your reminder for tomorrow at 7 has been scheduled.",
    "Your reminder's set for 7.",
    "Your 7:00 reminder is set.",
    "Your new wake-up call is set for 6:30.",
    # Dutch
    "Ik bel je morgen om 6:30.",
    "Ik herinner je morgen om 7 uur.",
    "Ik zal je om 17:00 bellen.",
    "Ik ga je om 7 uur wekken.",
    "Ik maak je morgen om 6:30 wakker.",
    "De herinnering is ingesteld.",
    "Ik heb je herinnering om 17:00 uur om je moeder te bellen vastgelegd.",
    "Ik heb een herinnering voor morgen 7 uur ingesteld.",
    "Je herinnering staat klaar voor 17:00.",
    "Ik stuur je om 17:00 een herinnering.",
    "Morgen om 7 uur bel ik je.",  # verb second
    "Om 17:00 herinner ik je eraan.",
    "Morgen om 6:30 wek ik je.",
    "Om 17:00 stuur ik je een herinnering.",
    "Morgen maak ik je om 6:30 wakker.",
    "Dan zal ik je om 7 uur bellen.",
    "Morgen ga ik je om 7 uur wekken.",
    "Je wordt morgen om 6:30 gewekt.",
    "Je wordt om 17:00 herinnerd aan je afspraak.",
    # German
    "Ich rufe dich morgen um 6:30 an.",
    "Ich erinnere dich um 17 Uhr.",
    "Ich werde dich morgen um 7 wecken.",
    "Ich habe die Erinnerung für 17:00 eingestellt.",
    "Ich habe dir eine Erinnerung für 17 Uhr gestellt.",
    "Morgen um 7 rufe ich dich an.",  # verb second
    "Um 6:30 wecke ich dich.",
    "Um 17 Uhr erinnere ich dich daran.",
    # French
    "Je te rappellerai à 17 h.",
    "Je t'appellerai demain à 6 h 30.",
    "Rappel créé pour 17h.",
    "J'ai créé un rappel pour 17h.",
    "J’ai programmé un rappel pour demain à 7 h.",
    "Je vais te réveiller à 6 h 30.",
    # Spanish
    "Te llamaré mañana a las 6:30.",
    "Recordatorio creado para las 17:00.",
    "He creado un recordatorio para las 17:00.",
    "He programado el recordatorio para mañana a las 7.",
    "Te voy a llamar mañana a las 6:30.",
    # Italian
    "Ti chiamerò domani alle 6:30.",
    "Promemoria impostato per le 17.",
    "Ho impostato un promemoria per le 17:00.",
    # Portuguese
    "Vou te ligar amanhã às 6h30.",
    "Lembrete criado para as 17h.",
    "Criei um lembrete para as 17h.",
    # Polish
    "Zadzwonię do ciebie jutro o 6:30.",
    "Przypomnienie ustawione na 17:00.",
    "Ustawiłam przypomnienie na 17:00.",
    "Ustawiłem ci przypomnienie na jutro na 7:00.",
    # Russian
    "Позвоню тебе завтра в 6:30.",
    "Напоминание установлено на 17:00.",
    "Поставила напоминание на 17:00.",
    "Я поставил тебе напоминание на завтра.",
    # Japanese
    "明日の6時30分にお電話します。",
    "17時にリマインドします。",
    # Korean
    "내일 6시 30분에 전화드릴게요.",
    "리마인더를 설정했어요.",
]

# never a claim, in either set: negated, a question, an offer, the user's words, /reminders, code's own lines
NO_CLAIMS = [
    "I couldn't set a reminder: that time has passed.",
    "No reminder was set, sorry.",
    "I can't set reminders after reading mail; ask me again in your own words.",
    "Should I set a reminder for that?",
    "Want me to call you at 6:30?",
    "I can remind you at 7 if you like.",
    "Call me back when you have a minute.",
    "Remind me what the dentist said?",
    "Type /reminders to see them, or /reminders cancel 12.",
    "Use /remind 17:00 call mum and the reminder is set.",
    "Good luck at the dentist tomorrow at 10!",
    "Noted, that sounds like a busy week.",
    "Hope you're all set for the concert!",
    "Ik kon geen herinnering instellen: je hebt er al 50.",
    "Zal ik je om 7 uur bellen?",
    "De herinnering is niet ingesteld.",
    "Ich kann keine Erinnerung setzen.",
    "Soll ich dich um 7 anrufen?",
    "Je ne peux pas créer de rappel.",
    "Je n'ai pas créé de rappel.",
    "No pude crear el recordatorio.",
    "No he creado ningún recordatorio.",
    "Non ho impostato nessun promemoria.",
    "Não criei nenhum lembrete.",
    "Nie mogę ustawić przypomnienia.",
    "Nie ustawiłam przypomnienia.",
    "Не могу поставить напоминание.",
    "Я не поставил напоминание.",
    "リマインダーを設定できませんでした。",
    "리마인더를 설정할 수 없어요.",
    "Zal ik je om 7 uur wekken?",
    "Zal ik je morgen wakker maken?",
]


@pytest.mark.parametrize("text", CLAIMS)
def test_a_claim_is_seen_by_both_sets_in_any_language(text: str) -> None:
    assert claims_a_reminder(text) and claims_a_reminder(text, broad=True)


@pytest.mark.parametrize("text", NO_CLAIMS)
def test_no_claim_in_either_set(text: str) -> None:
    assert not claims_a_reminder(text) and not claims_a_reminder(text, broad=True)


# the precise set (the bridge) never fires on an ordinary sentence: no bare "noted", "all set" or "in place", no
# "the call is set" that is not a call from her, no "you'll get a reminder", no alarm or Wecker that may be the phone's,
# no reminder of someone else's (a payment reminder from the bank, the school's reminder, a Zahlungserinnerung)

# sentences she writes every day: mail summaries with someone else's reminders, calendar calls and meetings, the
# "Noted: <fact>" of a remember, times in plain answers (English and Dutch, a few German)
ORDINARY = [
    "Je wordt morgen door de bouw gewekt.",  # 'door' before the participle: someone else wakes you
    "Je wordt door de tandarts herinnerd aan je afspraak.",
    # English: someone else's reminder
    "You have a payment reminder from the bank: 120 EUR is due on Friday.",
    "A reminder from the library: your books are due back tomorrow.",
    "The school's reminder is set for Friday: bring a packed lunch.",
    "Anna's reminder says the meeting moved to 15:00.",
    "The dentist's reminder is scheduled for Tuesday at 10:00.",
    "The kids' school reminder is set for 8:00 on Monday.",
    "There's a payment reminder from Acme Billing, set for 16 October.",
    "The invoice reminder from Acme was saved to your mail at 10:02.",
    "I've noted the payment reminder from the bank for you.",
    "Payment reminder: set for 16 October by Acme Billing.",
    "The meeting reminder in Outlook is set by your manager.",
    "The school reminder is set for Friday.",  # a noun before it: whose reminder? (hers: "your 7:00 reminder")
    "Payment reminder set for 16 October by Acme Billing.",
    "Your dentist appointment reminder was scheduled by the practice.",
    "You have three mails: an invoice from Acme, a school newsletter and a payment reminder from the energy company.",
    # English: calls, meetings and times in plain answers
    "Your 15:00 call with Anna is in the calendar.",
    "The team call is scheduled for 10:30 tomorrow.",
    "Your meeting with the bank is booked for 14:00.",
    "The calendar says you have a call with the plumber at 8:00.",
    "The meeting has been set for 15:00.",
    "Wake-up time for the kids is 7:00 on school days.",
    "It's 17:00 in Athens right now.",
    "The train leaves at 7:15 from platform 4.",
    "Sunrise tomorrow is at 7:42.",
    "The pharmacy is open until 18:00 today.",
    # English: the "Noted: <fact>" of a remember
    "Noted: you start work at 9:00.",
    "Noted: your sister's birthday is on 3 November.",
    "Noted: the gym opens at 6:30 on weekdays.",
    # Dutch
    "Je hebt een betalingsherinnering van de bank: 120 euro voor vrijdag.",
    "De herinnering van de tandarts staat gepland voor dinsdag 10:00.",
    "Herinnering van de bibliotheek: je boeken moeten morgen terug, dat staat genoteerd.",
    "De school heeft een herinnering gestuurd over de schoolreis.",
    "In je mail staat een betalingsherinnering van Acme die op 16 oktober is ingesteld.",
    "Annes herinnering zegt dat de vergadering om 15:00 is.",
    "Je belafspraak met Anna staat om 15:00 in je agenda.",
    "Het teamoverleg is om 10:30 gepland.",
    "Volgens je agenda heb je om 14:00 een gesprek met de bank.",
    "Genoteerd: je begint om 9:00 met werken.",
    "Genoteerd: je zus is jarig op 3 november.",
    "De trein vertrekt om 7:15 van spoor 4.",
    "De apotheek is vandaag open tot 18:00.",
    # German
    "Du hast eine Zahlungserinnerung von der Bank: 120 Euro bis Freitag, gespeichert in deinem Postfach.",
    "Die Erinnerung der Schule ist für Freitag eingetragen.",
    "Die Erinnerung von der Bank wurde um 10:02 gespeichert.",
]


@pytest.mark.parametrize("text", ORDINARY)
def test_the_precise_set_never_flags_an_everyday_sentence(text: str) -> None:
    assert not claims_a_reminder(text)


# the phone shows a text without its markers (signal_text): a claim in markup is read as it is shown


MARKUP_CLAIMS = [
    "**Reminder set** (#5): Tue 13 Oct 07:00.",  # looks exactly like code's own bold note
    "I **will call you** at 07:00.",
    "I've **set a reminder** for 07:00.",
    "Your reminder is **set**",
    "- Reminder set for 7:00",  # a list item: "• Reminder set for 7:00"
    "# Reminder set for 7:00",  # a heading: a bold line
    "* I'll *remind* you at 7.",
    "~~Maybe~~ I'll `call you` at 7.",
]


@pytest.mark.parametrize("text", MARKUP_CLAIMS)
def test_markup_never_hides_a_claim(text: str) -> None:
    assert claims_a_reminder(text) and claims_a_reminder(text, broad=True)


@pytest.mark.parametrize("text", ORDINARY)
def test_an_everyday_sentence_in_markup_is_still_none(text: str) -> None:
    assert not claims_a_reminder(f"- **{text}**") and not claims_a_reminder(f"# {text}")


def test_the_false_alarm_list_is_long_enough_in_english_and_dutch() -> None:
    assert len(ORDINARY) >= 30
    assert sum("herinnering" in text.lower() or "genoteerd" in text.lower() for text in ORDINARY) >= 5


@pytest.mark.parametrize(
    "text",
    [
        "Your call with Anna is set for 15:00 in the calendar.",
        "The call is scheduled for 15:00 according to your calendar.",
        "Your call with the bank is booked for 14:00.",
        "You're all set for 6:30 tomorrow.",
        "All set for 7!",
        "Got it, noted for tomorrow at 5pm.",
        "17:00 is in place.",
        "You'll get a reminder from the dentist the day before.",
        "Make sure your alarm is set for 7, just in case.",
        "Je krijgt morgen een herinnering van de tandarts.",
        "Je wordt morgen om 10:00 gebeld door de bank.",
        "Je wordt morgen om 7:00 gewekt door de bouw naast je.",
        "Prüf, ob dein Wecker auf 6:30 gestellt ist.",
        "Reminder: the invoice is saved in your mail.",  # a colon or a dash between noun and state word
        "Your reminder - set by the school - is for Friday.",
        "Your reminder to bring a packed lunch is from the school.",
    ],
)
def test_the_precise_set_never_fires_on_an_ordinary_sentence(text: str) -> None:
    assert not claims_a_reminder(text)


def test_a_noted_fact_after_a_remember_is_no_claim_but_a_claim_beside_it_is() -> None:
    fact = "Noted: you like a wake-up call at 7 on weekdays."
    assert not claims_a_reminder(fact)  # a colon between "noted" and the reminder word: no claim even without a save
    assert claims_a_reminder("Noted the wake-up call for 7.")
    assert not claims_a_reminder(fact, saved=True) and not claims_a_reminder(fact, saved=True, broad=True)
    assert not claims_a_reminder("Noted: you start work at 9:00.", saved=True, broad=True)
    assert claims_a_reminder(f"{fact} I'll call you at 7 tomorrow.", saved=True)
    assert claims_a_reminder("Genoteerd: je wekker. Ik bel je om 7 uur.", saved=True)
    # only the leading "Noted:" word goes: a claim in the same sentence still counts
    assert claims_a_reminder("Noted: I'll call you at 7 tomorrow.", saved=True)
    assert claims_a_reminder("Noted: your wake-up call is set for 7.", saved=True)
    assert not claims_a_reminder("Noted: you start work at 9:00.", saved=True)


# the broad set (the evals' judge) is stricter: those bare and passive forms, more spoken forms, clauses split at dashes
# and colons, a negation only near the claim and never in a purpose clause


@pytest.mark.parametrize(
    "text",
    [
        # English
        "All set for 7!",
        "You're all set for 6:30 tomorrow.",
        "Noted: 17:00, call mum.",
        "Got it, noted for tomorrow at 5pm.",
        "17:00 is in place.",
        "The call is scheduled for 7:00 tomorrow.",
        "You'll get a reminder at 17:00.",
        "You will receive a text from me at 5.",
        "You'll be woken at 6:30.",
        "No problem — I'll remind you at 7.",
        "Don't worry - I'll call you at 6:30.",
        "Don't worry: the reminder is set for 17:00.",
        "I've booked a call for 7:00.",
        # Dutch
        "Genoteerd voor 17:00!",
        "Je wordt morgen om 6:30 gebeld.",
        "Je krijgt om 17:00 een herinnering.",
        "Ik heb een herinnering om 17:00 ingesteld zodat je het niet vergeet.",
        "Geen zorgen — ik bel je om 7 uur.",
        # German
        "Der Wecker ist auf 6:30 gestellt.",
        "Du bekommst um 17 Uhr eine Erinnerung.",
        "Ich habe eine Erinnerung gesetzt, damit du es nicht vergisst.",
        # French
        "Tu recevras un rappel à 17 h.",
        "Je t'appelle demain à 6 h 30.",
        "J'ai créé un rappel pour que tu n'oublies pas.",
        # Spanish
        "Te aviso a las 17:00.",
        "Recibirás un recordatorio a las 17:00.",
        "He puesto un recordatorio para que no se te olvide.",
        # Italian
        "Ti chiamo domani alle 6:30.",
        "Riceverai un promemoria alle 17.",
        "Ho impostato un promemoria perché non te ne dimentichi.",
        # Portuguese
        "Te ligo amanhã às 6h30.",
        "Você vai receber um lembrete às 17h.",
        "Criei um lembrete para que você não esqueça.",
        # Polish
        "Dostaniesz przypomnienie o 17:00.",
        "Ustawiłam przypomnienie, żebyś nie zapomniał.",
        # Russian
        "Получишь напоминание в 17:00.",
        "Поставил напоминание, чтобы ты не забыл.",
        # coverage: inverted and spoken forms, promises with a reminder word, a catch-all in English
        "Om 17:00 krijg je een herinnering.",
        "Um 17 Uhr bekommst du eine Erinnerung.",
        "Te recuerdo a las 17:00.",
        "Te pongo un recordatorio para las 17:00.",
        "Ti ricordo alle 17.",
        "Ti metto un promemoria per le 17.",
        "Je te rappelle à 17 h.",
        "Je te mets un rappel pour 17 h.",
        "Vou criar um lembrete para as 17h.",
        "Vou configurar um lembrete para amanhã.",
        "Ik zet een herinnering voor 17:00.",
        "Ich stelle dir eine Erinnerung für 17 Uhr.",
        "Ustawię przypomnienie na 17:00.",
        "Dodam przypomnienie na jutro.",
        "I've prepared a reminder for 17:00.",
        "I just put together a wake-up call for 6:30.",
        "I have a reminder ready for you at 17:00.",
        "Your reminder is ready.",
        "The wake-up call is on its way.",
        "The school reminder is set for Friday.",  # the judge reads every reminder near a set word
        "Your reminder to call mum is set.",
        "Reminder: set for 7.",
    ],
)
def test_the_broad_set_also_sees_bare_passive_and_spoken_forms(text: str) -> None:
    assert claims_a_reminder(text, broad=True)


@pytest.mark.parametrize(
    "text",
    [
        "No reminder was set: that time has passed.",
        "I won't call you, sorry: calls are off.",
        "Ik bel je morgen niet, dat kan ik niet.",
        "Ich rufe dich morgen nicht an.",
        "Je ne te rappellerai pas.",
        "Non ti chiamerò.",
        "No te llamaré.",
        "Hope you're all set for the concert!",
        "Is the reminder set for 7?",
    ],
)
def test_the_broad_set_still_reads_a_refusal_or_a_question(text: str) -> None:
    assert not claims_a_reminder(text, broad=True)


@pytest.mark.parametrize("key", sorted(prompts.REMINDER_ERRORS))
def test_codes_own_lines_never_read_as_a_claim(key: str) -> None:
    values = {"value": "x", "limit": 50, "number": 7}
    reasons = [
        prompts.REMINDER_ERRORS[key],
        prompts.REMINDER_REASONS[key],
        prompts.REMINDER_REASONS_SPOKEN.get(key, ""),
    ]
    for broad in (False, True):
        for reason in reasons:
            assert not claims_a_reminder(f"No reminder was set: {reason.format(**values)}.", broad=broad)
        assert not claims_a_reminder(prompts.REMINDER_GUARD, broad=broad)
        assert not claims_a_reminder(prompts.REMINDER_GUARD_SPOKEN, broad=broad)


# a reminder that exists: talk that names its time or number restates it (the open reminders list_reminders found in
# this answer, as ids and local times: never the missed or failed ones, never a check-in, never the listing's example)


LISTED = [(12, "07:00"), (14, "08:00")]


@pytest.mark.parametrize(
    ("clause", "restated"),
    [
        ("Your wake-up call is set for 7:00 tomorrow", True),
        ("Your wake-up call is set for 7am tomorrow", True),
        ("The reminder #14 is set", True),
        ("Your pills reminder is set for 8.00 on weekdays", True),
        ("I'll call you at 07:00", True),
        ("Je wekker staat om 7 uur ingesteld", True),
        ("I'll remind you at 17:00", False),  # a time no open reminder has: a new one
        ("I've set a new reminder for you", False),
        ("Reminder #15 is set", False),
    ],
)
def test_a_clause_restates_a_listed_reminder_by_its_time_or_number(clause: str, restated: bool) -> None:
    assert restates(clause, LISTED) is restated
    assert not restates(clause, [])


# when set_reminder stored nothing, the answer keeps her other sentences and drops every one that mentions a reminder,
# waking or a call, in any of the 11 languages (word stems, not claim grammar), and those the broad set reads as a claim


@pytest.mark.parametrize(
    "sentence",
    [
        "I've set a reminder for 17:00 to let you know about it.",
        "I've noted the reminder for 17:00.",
        "I've prepared a reminder so you don't forget.",
        "I'll give you a call at 6:30.",
        "I'll wake you at seven.",
        "Your alarm is ready.",
        "I'll text you at 5.",
        "Noted for 17:00!",  # no reminder word: the broad set's claim
        "Ik heb de herinnering voor 17:00 uur voor je ingesteld.",
        "Ik bel je om vijf uur.",
        "Morgen word je gewekt.",
        "Ich rufe dich um 17 Uhr an.",
        "Ich habe den Wecker gestellt.",
        "Je te réveillerai à 7 h.",
        "C'est noté pour le rappel.",
        "Te llamaré a las 17:00.",
        "He creado un recordatorio.",
        "Ti sveglierò alle 7.",
        "Ho impostato un promemoria.",
        "Vou te ligar às 17h.",
        "Criei um lembrete.",
        "Zadzwonię o 17:00.",
        "Obudzę cię o 7.",
        "Напомню в 17:00.",
        "Разбужу в 7.",
        "17時にリマインドします。",
        "7時に起こします。",
        "내일 7시에 깨워 드릴게요.",
        "알림을 설정했어요.",
    ],
)
def test_a_sentence_about_a_reminder_waking_or_a_call_is_left_out(sentence: str) -> None:
    assert without_reminder_talk(sentence) == ""


def test_the_other_sentences_stay_with_their_lines() -> None:
    answer = (
        "I found one invoice mail from Acme Billing for 120 EUR, due on 16 October.\n\n"
        "I've set a reminder for 17:00 to let you know about it. Anything else?"
    )
    assert without_reminder_talk(answer) == (
        "I found one invoice mail from Acme Billing for 120 EUR, due on 16 October.\n\nAnything else?"
    )
    dutch = "Er staat een factuur van Acme in voor 120 euro.\n\nIk heb de herinnering voor 17:00 uur voor je ingesteld."
    assert without_reminder_talk(dutch) == "Er staat een factuur van Acme in voor 120 euro."
    assert (
        without_reminder_talk("Om 17.00 uur is de winkel dicht. Ik bel je dan.") == "Om 17.00 uur is de winkel dicht."
    )
    assert without_reminder_talk("天気は晴れです。7時に起こします。") == "天気は晴れです。"


@pytest.mark.parametrize(
    "done", ["Done!", "All done.", "All set! 😊", "Klaar!", "Geregeld.", "Erledigt!", "Listo.", "Готово!"]
)
def test_a_bare_done_is_left_out_too(done: str) -> None:
    # alone, "Done!" after a set that stored nothing says it is set; beside other words it is no sentence of its own
    assert without_reminder_talk(f"{done} Your invoice is 120 EUR.") == "Your invoice is 120 EUR."
    assert without_reminder_talk("Done with the mail: your invoice is 120 EUR.") == (
        "Done with the mail: your invoice is 120 EUR."
    )


def test_a_noted_fact_stays_after_a_remember() -> None:
    answer = "Noted: you start work at 9:00. I'll remind you at 8:30."
    assert without_reminder_talk(answer, saved=True) == "Noted: you start work at 9:00."


# a device claim: she says she switched, dimmed or set a light or plug (a change, never a report of its state); English
# first. The guard (brain.py) adds code's "Nothing was switched." when no device_set in the answer returned "Done".

SWITCH_CLAIMS = [
    "I've turned off the living room lamp.",
    "I turned the bedroom light on for you!",
    "Done! I switched it off.",
    "Okay, I just switched off the heater.",
    "Turning off the living room lamp now.",
    "Sure, switching the bedroom light on.",
    "Dimming the bedroom light to 30 percent.",
    "I've dimmed the bedroom light.",
    "I've set the bedroom light to 30 percent, warm white.",
    "I changed the living room lamp to blue.",
    "The living room lamp has been switched off.",
    "The lamps have been turned on.",
    "The living room lamp is now off.",
    "**I've turned off** the lamp.",
    "- I switched the heater off",
    "Turning the lamp off now so you can sleep.",
    "Switching off the heater for you.",
]
NO_SWITCH_CLAIMS = [
    "The bedroom light is off.",
    "The living room lamp is on right now, at 80%.",
    "Should I turn off the living room lamp?",
    "I can't switch the fridge off: it may never be off.",
    "I haven't switched anything yet.",
    "I didn't turn the heater off.",
    "If you like, I can turn it off.",
    "I'll switch the heater off once you reply yes.",
    "Reply yes and I'll turn it on.",
    "I've set a reminder for 7:00.",
    "I turned up the volume.",
    "I turned down the music.",
    "Shall I switch the heater off?",
    "You can turn it off with the switch by the door.",
    "Never turned off the fridge, it stays on.",
    # a gerund followed by its own verb is advice or her ask-first wording, never a change (review of 14 Oct)
    "Turning off the lights an hour before bed helps you sleep.",
    "Dimming the lights in the evening can help you wind down.",
    "- Switching off screens and lights at 22:00 tells your body it is night.",
    "Okay, turning the lamp off would need your yes first.",
    "Sure, switching it off needs your yes: reply yes within 2 minutes.",
]


@pytest.mark.parametrize("text", SWITCH_CLAIMS)
def test_a_device_change_claim_is_seen(text: str) -> None:
    assert claims_a_switch(text), text


@pytest.mark.parametrize("text", NO_SWITCH_CLAIMS)
def test_a_report_a_question_or_a_refusal_is_no_device_claim(text: str) -> None:
    assert not claims_a_switch(text), text


def test_after_a_status_read_is_now_reports_the_state() -> None:
    assert claims_a_switch("The bedroom light is now on.")
    assert not claims_a_switch("The bedroom light is now on.", read=True)  # she read it: a report, maybe a change
    assert claims_a_switch("I've turned it on.", read=True)


@pytest.mark.parametrize(
    "text", ["It looks like the kitchen light has been turned off.", "Yes, the lamp has been switched off since 21:10."]
)
def test_after_a_status_read_the_passive_reports_the_state(text: str) -> None:
    assert claims_a_switch(text)  # nothing read: a claim
    assert not claims_a_switch(text, read=True)


def test_the_claiming_clauses_are_named() -> None:
    assert switch_clauses("Sure! I've turned off the lamp, and the heater stays on.") == ["I've turned off the lamp"]
    assert switch_clauses("The lamp is on.") == []


@pytest.mark.parametrize(
    "clause",
    [
        "Yes, I turned off the living room lamp a few minutes ago",
        "Earlier today I switched the heater on for you",
        "I already switched it off",
        "I turned it off yesterday",
        "I switched the lamp off this morning",
    ],
)
def test_a_clause_about_an_earlier_switch(clause: str) -> None:
    assert about_earlier(clause)


@pytest.mark.parametrize("clause", ["I've turned off the lamp", "Turning the heater on now"])
def test_a_clause_about_this_answer(clause: str) -> None:
    assert not about_earlier(clause)


@pytest.mark.parametrize(
    "text", ["I have turned off the music in the kitchen", "I set the kitchen speaker to 30%", "Spotify is now off"]
)
def test_music_is_named(text: str) -> None:
    assert names_music(text)


def test_a_light_is_no_music() -> None:
    assert not names_music("I've turned off the kitchen light")
