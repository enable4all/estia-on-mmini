"""The one language she listens and speaks in: the 11 she offers, their names and their yes words.

Only the languages Whisper large-v3 hears best (OpenAI's published word error rates, all at most 5.3 %) that also have
a voice; English first, then from the lowest error rate. One at a time: Whisper is told the language on every
request (no detection) and Gemma answers in it. The language changes only by the user's typed /language.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str  # what the voice service and the config take, like "nl"
    english: str  # "Dutch"
    own: str  # "Nederlands"


LANGUAGES: dict[str, Language] = {
    language.code: language
    for language in (
        Language("en", "English", "English"),  # Whisper's error rate 4.1 %
        Language("es", "Spanish", "Español"),  # 2.8 %
        Language("it", "Italian", "Italiano"),  # 3.0 %
        Language("ko", "Korean", "한국어"),  # 3.1 %
        Language("pt", "Portuguese", "Português"),  # 4.1 %
        Language("nl", "Dutch", "Nederlands"),  # 4.3 %
        Language("pl", "Polish", "Polski"),  # 4.6 %
        Language("de", "German", "Deutsch"),  # 4.9 %
        Language("ja", "Japanese", "日本語"),  # 4.9 %
        Language("ru", "Russian", "Русский"),  # 5.0 %
        Language("fr", "French", "Français"),  # 5.3 %
    )
}
CODES = tuple(LANGUAGES)
ENGLISH = "en"
LANGUAGE_KEY = "language"  # the state table's key for the one chosen with /language; wins over the config's

# a device's "ask first" yes, in each language: the whole message must be one of these (casefolded, trailing
# punctuation dropped, commas as spaces; see devices._is_yes)
_YES: dict[str, tuple[str, ...]] = {
    "en": ("yes", "y", "ok", "okay", "yes please", "do it"),
    "es": ("sí", "si", "sí por favor", "si por favor", "vale"),
    "it": ("sì", "si", "sì grazie", "si grazie", "va bene"),
    "ko": ("네", "예", "응", "네 해 주세요"),
    "pt": ("sim", "sim por favor"),
    "nl": ("ja", "ja graag", "ja hoor", "doe maar", "oké"),
    "pl": ("tak", "tak proszę"),
    "de": ("ja", "ja bitte", "jawohl"),
    "ja": ("はい", "ええ", "うん", "はい お願いします"),
    "ru": ("да", "да пожалуйста", "давай"),
    "fr": ("oui", "oui merci", "d'accord", "vas-y"),
}
YES_WORDS = frozenset(word for words in _YES.values() for word in words)


def yes_form(text: str) -> str:
    """A message as a yes is told: without the [voice] or [call] mark, casefolded, trailing punctuation and a leading
    "¡" dropped, commas as spaces ("[call] Ja, graag." -> "ja graag"). An [audio file] keeps its mark: no yes."""
    spoken = text.strip()
    for mark in ("[voice]", "[call]"):
        spoken = spoken.removeprefix(mark)
    answer = re.sub(r"[\s.!,。！、]+$", "", spoken.strip().casefold()).lstrip("¡")
    return re.sub(r"[\s,、]+", " ", answer)


def is_yes(text: str, code: str) -> bool:
    """A clear yes in the language `code` or in English (code's own language): the whole message is one of the yes
    words ("Ja, graag!" on a Dutch call; "yes, and what about…" is no yes)."""
    return yes_form(text) in {*_YES[ENGLISH], *_YES.get(code, ())}


# on a call, one of these said alone over her is an interruption ("wait", "stop", "no"), like a yes word
_INTERRUPT: dict[str, tuple[str, ...]] = {
    "en": ("stop", "wait", "no", "hold"),
    "es": ("espera", "para", "no"),
    "it": ("aspetta", "basta", "fermati", "no"),
    "ko": ("잠깐", "잠깐만", "아니요", "아니", "그만"),
    "pt": ("espera", "espere", "para", "pare", "não"),
    "nl": ("wacht", "stop", "nee"),
    "pl": ("czekaj", "poczekaj", "stop", "nie"),
    "de": ("warte", "stopp", "halt", "nein"),
    "ja": ("待って", "ちょっと", "いいえ"),
    "ru": ("стоп", "подожди", "погоди", "нет"),
    "fr": ("attends", "arrête", "stop", "non"),
}
INTERRUPTIONS = frozenset(word for words in _INTERRUPT.values() for word in words)

# On a call, "but" said alone over her stops her too, like "wait" (owner, 14 Oct)
BUT: dict[str, tuple[str, ...]] = {
    "en": ("but",),
    "es": ("pero",),
    "it": ("ma",),
    "ko": ("하지만", "그런데"),
    "pt": ("mas",),
    "nl": ("maar",),
    "pl": ("ale",),
    "de": ("aber",),
    "ja": ("でも",),
    "ru": ("но",),
    "fr": ("mais",),
}
BUT_WORDS = frozenset(word for words in BUT.values() for word in words)

# On a call, a listener's acknowledgement over her answer ("okay", "yeah", "mm-hm"): a transcript of only these (and
# fillers) in the call's language lets her carry on, as after a cough, unless she asked something or a device question
# waits (calls.is_acknowledgement; owner, 14 Oct: a listener's "okay" ended two of her answers on a live call). Words
# as Whisper writes them, lower case; a hyphen or a space between the parts of one is the same ("mm-hm", "mm hm").
# More of each language's common ones (owner, 15 Oct): de klar/richtig, fr très bien/bien sûr, pt tá/entendi, es ya,
# ru угу, pl no tak/dobra, ko 맞아요, nl and it ok.
ACKNOWLEDGEMENTS: dict[str, tuple[str, ...]] = {
    "en": (
        "ok", "okay", "yes", "yeah", "yep", "yup", "mm", "mhm", "mm-hm", "uh-huh", "right", "sure", "alright",
        "all right", "i see", "got it", "great", "good", "cool", "nice", "fine", "exactly", "true",
    ),
    "es": ("sí", "vale", "claro", "bien", "exacto", "ya"),
    "it": ("sì", "va bene", "certo", "esatto", "giusto", "ok", "okay"),
    "ko": ("네", "응", "예", "알겠어요", "맞아요"),
    "pt": ("sim", "certo", "claro", "beleza", "exato", "tá", "entendi"),
    "nl": ("ja", "oké", "okee", "ok", "okay", "prima", "goed", "mooi", "klopt", "precies"),
    "pl": ("tak", "dobrze", "jasne", "rozumiem", "no tak", "dobra"),
    "de": ("ja", "okay", "gut", "genau", "stimmt", "alles klar", "klar", "richtig"),
    "ja": ("はい", "うん", "ええ", "なるほど", "そうですね"),
    "ru": ("да", "ага", "угу", "хорошо", "понятно", "ясно"),
    "fr": ("oui", "d'accord", "bien", "très bien", "bien sûr", "exactement", "ouais"),
}  # fmt: skip
# the sounds Whisper writes for a listener's hum, in any language
ACKNOWLEDGEMENT_SOUNDS = ("mm", "mhm", "mhmm", "mm-hm", "mm-hmm", "uh-huh", "uh huh")


def acknowledgements(code: str) -> tuple[str, ...]:
    """A language's acknowledgement words on a call and the hums Whisper writes (only the hums for an unknown code)."""
    return ACKNOWLEDGEMENTS.get(code, ()) + ACKNOWLEDGEMENT_SOUNDS


# On a call, leave-taking ("I have to go", "I'm hanging up") ends the call once her reply is spoken (owner, 14 Oct;
# calls.takes_leave). Conservative: only a sentence (or a part between commas) that is one of these, with at most the
# language's soft words around it (LEAVING_SOFT, a filler or her name): "I have to go to the dentist" is none, and so
# is "I have to go, uh, to the dentist" (CONTINUING). English, Dutch and German first; the others only phrases that
# are clear on a phone. Leaving with a reason counts too (owner, 18 Oct: leaving with a reason kept a call open):
# "I have something to do", "I have to run", in each language; the reasons that also complain or apologise are
# LEAVING_UNCLEAR's.
LEAVING: dict[str, tuple[str, ...]] = {
    "en": (
        "i'm hanging up", "i am hanging up", "i'll hang up", "i will hang up", "i'm going to hang up",
        "i'm gonna hang up", "hanging up", "i have to go", "i really have to go", "i've got to go", "i've gotta go",
        "i got to go", "i gotta go", "gotta go", "got to go", "i need to go", "i really need to go", "i must go",
        "i'm off", "i am off", "i have something to do", "i've got something to do", "i got something to do",
        "i have something i need to do", "i have to run", "i've got to run", "i got to run", "i gotta run",
        "gotta run", "got to run", "i need to run", "i have to dash", "i need to get going", "i have to get going",
    ),
    "es": (
        "tengo que irme", "me tengo que ir", "voy a colgar", "te cuelgo", "tengo que colgar", "tengo algo que hacer",
        "tengo que irme corriendo", "me voy corriendo",
    ),
    "it": (
        "devo andare", "devo scappare", "devo riattaccare", "riattacco", "ho una cosa da fare", "ho qualcosa da fare",
        "devo correre",
    ),
    "ko": (
        "끊을게", "끊을게요", "가 봐야 해", "가봐야 해", "가 봐야 해요", "가봐야 해요", "가 봐야겠어요", "가봐야겠어요",
        "할 일이 있어", "할 일이 있어요",
    ),
    "pt": (
        "tenho que ir", "tenho de ir", "preciso ir", "vou desligar", "tenho que desligar", "tenho uma coisa para fazer",
        "tenho uma coisa pra fazer", "tenho algo para fazer", "tenho algo pra fazer", "tenho que correr",
        "preciso correr",
    ),
    "nl": (
        "ik hang op", "ik hang nu op", "ik ga ophangen", "ik moet ophangen", "ik moet nu ophangen", "ik moet gaan",
        "ik moet nu gaan", "ik moet ervandoor", "ik moet er nu vandoor", "ik ga ervandoor", "ik moet weg",
        "ik moet nu weg", "ik moet nog iets doen", "ik heb nog iets te doen", "ik moet snel weg",
        "ik moet er snel vandoor",
    ),
    "pl": ("muszę iść", "muszę lecieć", "muszę kończyć", "rozłączam się", "mam coś do zrobienia", "muszę uciekać", "muszę pędzić"),
    "de": (
        "ich lege auf", "ich leg auf", "ich lege jetzt auf", "ich leg jetzt auf", "ich muss auflegen",
        "ich muss jetzt auflegen", "ich muss los", "ich muss jetzt los", "ich muss gehen", "ich muss jetzt gehen",
        "ich muss weg", "ich muss jetzt weg", "ich habe noch was zu tun", "ich hab noch was zu tun",
        "ich habe noch etwas zu tun", "ich hab noch etwas zu tun", "ich muss noch was erledigen",
        "ich muss noch etwas erledigen", "ich muss schnell los",
    ),
    # never "失礼します" alone: it is also "excuse me"
    "ja": ("切るね", "切るよ", "切ります", "行かなきゃ", "そろそろ失礼します", "それでは失礼します", "用事がある", "用事があります"),
    "ru": (
        "мне пора", "мне надо идти", "мне нужно идти", "кладу трубку", "я кладу трубку", "мне надо бежать",
        "мне нужно бежать", "мне пора бежать",
    ),
    "fr": (
        "je raccroche", "je dois raccrocher", "je dois y aller", "je dois partir", "il faut que j'y aille",
        "j'ai quelque chose à faire", "j'ai un truc à faire", "je dois filer", "il faut que je file", "je file",
    ),
}  # fmt: skip
# Leaving with a reason that also complains or apologises ("My back hurts. I need to do something.", "Sorry I didn't
# call earlier. Something came up."; review 18 Oct): leave-taking only with nothing before it in the turn but soft
# words (LEAVING_SOFT, a filler, her name) and thanks (THANKS): "Sorry, something came up." Never "I have things to
# do" or "I have to do something": a complaint as often ("I'm so busy this week. I have things to do."), and Korean's
# 일이 생겼어요 is also "something happened", French "il y a un imprévu" "there is a hitch", Polish "coś wypadło"
# "something fell out" and Russian "у меня дела" "I'm fine" ("У меня дела, хорошо.").
LEAVING_UNCLEAR: dict[str, tuple[str, ...]] = {
    "en": (
        "i need to do something", "something came up", "something has come up", "something's come up",
        "something just came up",
    ),
    "es": ("me surgió algo", "me ha surgido algo", "me salió algo", "ha surgido algo"),
    "it": ("mi è venuto un imprevisto", "ho un imprevisto", "è saltato fuori qualcosa"),
    "ko": ("할 일이 생겼어", "할 일이 생겼어요", "급한 일이 생겼어", "급한 일이 생겼어요"),
    "pt": ("surgiu uma coisa", "surgiu algo", "surgiu um imprevisto", "tive um imprevisto"),
    "nl": ("ik moet iets doen", "er kwam iets tussen", "er is iets tussengekomen", "er komt iets tussen"),
    "pl": ("muszę coś zrobić", "coś mi wypadło"),
    "de": (
        "mir ist was dazwischengekommen", "mir ist etwas dazwischengekommen", "es ist was dazwischengekommen",
        "es ist etwas dazwischengekommen",
    ),
    "ja": ("用事ができた", "用事ができました", "急用ができた", "急用ができました"),
    "ru": ("у меня появились дела", "мне надо кое-что сделать", "мне нужно кое-что сделать"),
    "fr": ("j'ai un imprévu",),
}  # fmt: skip
# the short words that may come before or after leave-taking in its sentence ("okay, so, I have to go now"): a
# language's "okay" and "sorry", never a "yes", "exactly" or "true" that answers her ("Yes, I have to go" is an
# answer); English's count on every call
LEAVING_SOFT: dict[str, tuple[str, ...]] = {
    "en": (
        "ok", "okay", "alright", "all right", "right", "so", "well", "anyway", "sorry", "now", "then", "bye",
        "goodbye", "for now", "right now", "right away", "immediately", "urgently",
    ),
    "es": ("vale", "bueno", "pues", "ya", "ahora", "adiós", "perdón", "perdona", "lo siento"),
    "it": ("va bene", "allora", "ora", "adesso", "ciao", "scusa", "scusami", "mi dispiace"),
    "ko": ("알겠어요", "그럼", "이제", "안녕", "미안", "미안해", "미안해요", "죄송해요", "죄송합니다"),
    "pt": ("certo", "beleza", "bem", "então", "agora", "tchau", "desculpa", "desculpe"),
    "nl": ("oké", "okee", "prima", "nou", "zo", "dus", "even", "hoor", "doei", "dag", "sorry"),
    "pl": ("dobrze", "no", "dobra", "teraz", "pa", "przepraszam", "sorry"),
    "de": ("alles klar", "so", "also", "na", "tschüss", "sorry", "entschuldigung", "tut mir leid"),
    "ja": ("じゃあ", "じゃ", "それじゃあ", "それじゃ", "では", "もう", "そろそろ", "ごめん", "ごめんね", "すみません"),
    "ru": ("хорошо", "ну", "всё", "все", "ладно", "пока", "извини", "извините", "прости", "простите"),
    "fr": ("d'accord", "bon", "bref", "maintenant", "salut", "désolé", "désolée", "pardon"),
}  # fmt: skip
# a caller's thanks on a call, in each language: before a reason that also complains (LEAVING_UNCLEAR: "Thanks.
# Something came up.")
THANKS: dict[str, tuple[str, ...]] = {
    "en": (
        "thanks", "thank you", "thanks a lot", "thank you so much", "thanks so much", "thank you very much",
        "many thanks", "cheers",
    ),
    "es": ("gracias", "muchas gracias"),
    "it": ("grazie", "grazie mille"),
    "ko": ("고마워", "고마워요", "감사합니다", "감사해요"),
    "pt": ("obrigado", "obrigada", "muito obrigado", "muito obrigada", "valeu"),
    "nl": ("dank je", "dank je wel", "dankjewel", "dank u", "dank u wel", "bedankt"),
    "pl": ("dzięki", "dziękuję", "dziękuję bardzo"),
    "de": ("danke", "danke schön", "dankeschön", "danke sehr", "vielen dank"),
    "ja": ("ありがとう", "ありがとうございます", "どうも"),
    "ru": ("спасибо", "спасибо большое", "большое спасибо"),
    "fr": ("merci", "merci beaucoup", "merci bien"),
}  # fmt: skip
# leave-taking that also means something else alone ("I'm off" is a day off too): it counts only with a soft word
# beside it ("Okay, I'm off.", "I'm off now")
LEAVING_NOT_ALONE: dict[str, tuple[str, ...]] = {"en": ("i'm off", "i am off")}
# On a call, the caller asking her to end it ("let's end the call", "shall we hang up?", "let's call it a day") ends it
# once her answer is spoken, even when it asks something (calls.wants_hang_up; owner, 18 Oct: a request to end the
# call kept a call open). Matched as leave-taking is: a part of its own with only soft words beside it (LEAVING_SOFT and
# ENDING_SOFT: "please", "I think"), only closing talk after it; unlike leave-taking it may be asked ("Shall we stop
# here?"). English's that also mean something else alone ("stop here": stop reading here; "wrap it up": finish the
# answer; "end it here": end the message) count only after "let's", "we can", "shall we" or the like (_ENDING_LEADS);
# in the other languages only the ones about the call or the phone, never a bare "turn it off" ("desliga", 切って,
# 끊자), and never one that also ends something else ("tot zover": so far, "kommen wir zum Ende", "możemy skończyć";
# review 18 Oct). English's count on every call.
_ENDING_LEADS = (
    "let's", "let us", "we can", "we could", "we should", "shall we", "should we", "can we", "could we", "time to",
)  # fmt: skip
_ENDING_AFTER_A_LEAD = (
    "stop here", "stop there", "stop now", "stop for now", "stop for today", "end here", "end there", "end it here",
    "end it there", "finish here", "finish there", "leave it here", "leave it there", "leave it at that", "wrap up",
    "wrap it up", "wrap this up", "wrap things up",
)  # fmt: skip
# Portuguese "pode desligar" is also "you can switch it off", 끊어도 돼요 also "may I quit": only with the call or the
# phone named (review 18 Oct)
_DESLIGAR = ("pode desligar", "podemos desligar", "vamos desligar")
ENDING: dict[str, tuple[str, ...]] = {
    "en": (
        "hang up", "hang up the call", "hang up the phone", "end the call", "end this call", "end our call",
        "end call", "close the call", "close this call", "finish the call", "finish this call", "stop the call",
        "stop this call", "end the conversation", "end this conversation", "finish the conversation", "call it a day",
        "call it a night",
        *(f"{lead} {words}" for lead in _ENDING_LEADS for words in _ENDING_AFTER_A_LEAD),
    ),
    "es": (
        "cuelga", "puedes colgar", "podemos colgar", "vamos a colgar", "colguemos", "termina la llamada",
        "terminemos la llamada", "vamos a terminar la llamada", "acabemos la llamada", "cerremos la llamada",
        "corta la llamada", "dejémoslo aquí", "lo dejamos aquí", "dejémoslo ahí", "lo dejamos ahí",
        "dejémoslo por hoy", "lo dejamos por hoy", "paremos aquí", "podemos parar aquí", "terminemos aquí",
        "terminamos aquí", "acabemos aquí", "suficiente por hoy", "por hoy es suficiente",
    ),
    "it": (
        "riattacca", "puoi riattaccare", "possiamo riattaccare", "riattacchiamo", "chiudi la chiamata",
        "chiudiamo la chiamata", "puoi chiudere la chiamata", "terminiamo la chiamata", "chiudiamo qui",
        "chiudiamola qui", "finiamo qui", "fermiamoci qui", "possiamo fermarci qui", "ci fermiamo qui",
        "per oggi basta", "basta per oggi",
    ),
    "ko": (
        "통화 끝내자", "통화를 끝내자", "통화 끝낼까요", "통화를 끝낼까요", "통화 끝내요", "통화를 끝내요",
        "전화 끊자", "전화를 끊자", "전화 끊을까요", "전화를 끊을까요", "전화 끊어도 돼요", "전화 끊어도 돼",
        "전화를 끊어도 돼요", "전화를 끊어도 돼", "여기까지 하자",
        "여기까지 해요", "여기까지 할까요", "오늘은 여기까지", "오늘은 여기까지 하자", "오늘은 여기까지 해요",
        "여기서 끝내자", "여기서 끝낼까요",
    ),
    "pt": (
        *(f"{verb} {what}" for verb in _DESLIGAR for what in ("a chamada", "a ligação", "o telefone")),
        "encerra a chamada", "encerrar a chamada",
        "vamos encerrar a chamada", "termina a chamada", "vamos terminar a chamada", "vamos parar por aqui",
        "podemos parar por aqui", "vamos ficar por aqui", "podemos ficar por aqui", "vamos deixar por aqui",
        "vamos encerrar por aqui", "chega por hoje", "por hoje chega",
    ),
    "nl": (
        "hang maar op", "hang op", "je mag ophangen", "je kunt ophangen", "je kan ophangen", "laten we ophangen",
        "zullen we ophangen", "we kunnen ophangen", "beëindig het gesprek", "laten we het gesprek beëindigen",
        "laten we hier stoppen", "we kunnen hier stoppen", "zullen we hier stoppen", "laten we stoppen voor vandaag",
        "laten we het hierbij laten", "we laten het hierbij", "laten we het daarbij laten", "laten we afronden",
        "zullen we afronden", "we ronden af", "laten we er een punt achter zetten", "we zetten er een punt achter",
        "genoeg voor vandaag", "we stoppen ermee",
    ),
    "pl": (
        "rozłącz się", "możesz się rozłączyć", "rozłączmy się", "kończymy rozmowę", "skończmy rozmowę",
        "zakończmy rozmowę", "skończmy na tym", "zakończmy na tym", "na tym skończmy", "na tym zakończmy",
        "możemy na tym skończyć", "na dziś wystarczy", "na dzisiaj wystarczy", "wystarczy na dziś",
        "kończymy na dziś",
    ),
    "de": (
        "leg auf", "du kannst auflegen", "lass uns auflegen", "wollen wir auflegen", "sollen wir auflegen",
        "wir können auflegen", "beende das gespräch", "beende den anruf", "lass uns das gespräch beenden",
        "lass uns den anruf beenden", "wir können das gespräch beenden", "lass uns hier aufhören",
        "wir können hier aufhören", "hören wir hier auf", "lass uns schluss machen", "machen wir schluss",
        "wir machen schluss", "belassen wir es dabei", "lass es uns dabei belassen", "für heute reicht's",
        "für heute reicht es", "das reicht für heute",
    ),
    "ja": (
        "電話を切ろう", "電話切ろう", "電話を切ろうか", "電話を切りましょう", "電話を切っていいよ",
        "電話を切っていいですよ", "電話切っていいよ", "通話を終わろう", "通話を終わりにしよう",
        "終わりにしよう", "終わりにしましょう", "今日はここまで", "今日はここまでにしよう",
        "今日はここまでにしましょう", "ここまでにしよう", "ここまでにしましょう",
    ),
    "ru": (
        "клади трубку", "положи трубку", "можешь положить трубку", "давай положим трубку", "давай закончим разговор",
        "закончим разговор", "давай заканчивать", "давай на этом закончим", "давай закончим на этом",
        "на этом закончим", "закончим на этом", "давай на этом остановимся", "остановимся на этом", "на сегодня всё",
        "на сегодня все", "на сегодня хватит", "давай прощаться",
    ),
    "fr": (
        "raccroche", "tu peux raccrocher", "on peut raccrocher", "on raccroche", "raccrochons", "terminons l'appel",
        "on termine l'appel", "mettons fin à l'appel", "on arrête l'appel", "arrêtons-nous là", "on s'arrête là",
        "on peut s'arrêter là", "on va s'arrêter là", "restons-en là", "on en reste là", "ça suffit pour aujourd'hui",
        "on arrête pour aujourd'hui", "arrêtons pour aujourd'hui",
    ),
}  # fmt: skip
# the leads and polite words beside a request to end the call ("let's", "can we", "please", "I think", "I'll", "here":
# "I'll end the call here"): soft words for ENDING only, never for leave-taking; English's count on every call
ENDING_SOFT: dict[str, tuple[str, ...]] = {
    "en": (
        "let's", "let us", "we can", "we could", "you can", "you could", "you may", "can we", "could we", "shall we",
        "should we", "can you", "could you", "would you", "can i", "may i", "shall i", "maybe", "i think", "i guess",
        "please", "i'd like to", "i want to", "go ahead and", "just", "for today", "we should", "i'm going to",
        "i am going to", "i'm gonna", "i'll", "i will", "here", "there",
    ),
    "es": ("por favor", "mejor", "creo que"),
    "it": ("per favore", "dai", "direi", "ok", "okay"),
    "ko": ("우리", "이만", "좀"),
    "pt": ("por favor", "acho que", "melhor"),
    "nl": ("alsjeblieft", "dan", "nu", "denk ik", "misschien"),
    "pl": ("proszę", "chyba", "to", "już"),
    "de": ("bitte", "jetzt", "dann", "doch", "ich denke", "für heute", "einfach"),
    "ja": ("ね",),
    "ru": ("пожалуйста", "наверное", "тогда"),
    "fr": ("s'il te plaît", "s'il vous plaît", "allez", "alors", "je crois"),
}  # fmt: skip
# On a call, her own plain goodbyes: an answer of hers that ends with one and asks nothing, after a caller's turn of
# closing talk, ends the call once spoken, also without her end_call (calls.ends_with_goodbye; owner, 18 Oct: her "Bye!"
# ended nothing). Never a word that is also "hello" ("ciao", "salut", "dag", "안녕", "cześć", Portuguese "boa noite"
# is "good evening" too); English's count on every call.
GOODBYES: dict[str, tuple[str, ...]] = {
    "en": (
        "bye", "bye bye", "goodbye", "good bye", "bye for now", "see you", "see you later", "see you soon",
        "see you tomorrow", "talk soon", "talk to you soon", "talk to you later", "talk later", "speak soon",
        "speak to you soon", "catch you later", "take care", "good night", "goodnight", "so long", "farewell",
    ),
    "es": (
        "adiós", "hasta luego", "hasta pronto", "hasta la próxima", "hasta mañana", "nos vemos", "nos hablamos",
        "cuídate", "chao", "chau",
    ),
    "it": (
        "arrivederci", "a presto", "alla prossima", "ci sentiamo", "ci sentiamo presto", "a dopo", "a domani",
        "stammi bene", "buonanotte",
    ),
    "ko": ("안녕히 계세요", "안녕히 가세요", "잘 가요", "잘 가", "또 봐요", "또 만나요", "다음에 봐요", "들어가세요"),
    "pt": ("tchau", "até logo", "até mais", "até breve", "até amanhã", "até a próxima", "adeus", "se cuida"),
    "nl": (
        "doei", "doeg", "tot ziens", "tot later", "tot straks", "tot snel", "tot gauw", "tot de volgende keer",
        "tot morgen", "houdoe", "welterusten",
    ),
    "pl": ("do widzenia", "do zobaczenia", "do usłyszenia", "na razie", "trzymaj się", "pa pa", "do jutra", "dobranoc"),
    "de": (
        "tschüss", "tschüs", "auf wiedersehen", "auf wiederhören", "bis später", "bis bald", "bis dann", "bis morgen",
        "mach's gut", "machs gut", "gute nacht",
    ),
    "ja": ("さようなら", "またね", "じゃあね", "バイバイ", "またあとで", "また今度", "おやすみ", "おやすみなさい"),
    "ru": (
        "до свидания", "до встречи", "до связи", "всего доброго", "всего хорошего", "пока-пока", "до завтра",
        "счастливо", "спокойной ночи",
    ),
    "fr": ("au revoir", "à bientôt", "à plus", "à plus tard", "à tout à l'heure", "à demain", "bonne nuit"),
}  # fmt: skip
# On a call, words after a goodbye, leave-taking or a request to end the call that say more is coming: "wait", "one
# more thing", "actually", "before you go" (calls._closing_talk keeps the call open; review 18 Oct: they were English
# only), besides each language's "but" (BUT) and stop words; English's count on every call. Korean is matched inside
# its words ("잠깐만요"), Japanese inside its characters. Never a word a closing phrase holds ("nog iets" is in "ik moet
# nog iets doen", "noch was" in "ich habe noch was zu tun").
MORE_COMING: dict[str, tuple[str, ...]] = {
    "en": (
        "wait", "but", "actually", "hold on", "hang on", "one more", "one thing", "another thing", "another question",
        "before you go", "before i go", "quick question", "one second", "one sec",
    ),
    "es": (
        "una cosa más", "otra cosa", "una pregunta más", "otra pregunta", "un momento", "un segundo", "espérate",
        "antes de colgar", "en realidad",
    ),
    "it": (
        "ancora una cosa", "un'altra cosa", "un'ultima cosa", "un'altra domanda", "ancora una domanda", "un attimo",
        "un momento", "un secondo", "prima di chiudere", "prima di riattaccare", "in realtà",
    ),
    "ko": ("하나 더", "한 가지 더", "질문 하나", "잠깐", "잠시만", "끊기 전에", "사실은"),
    "pt": (
        "mais uma coisa", "só mais uma coisa", "outra coisa", "mais uma pergunta", "outra pergunta", "um momento",
        "um segundo", "peraí", "pera aí", "antes de desligar", "na verdade",
    ),
    "nl": (
        "nog één ding", "nog een ding", "nog een vraag", "nog één vraag", "een momentje", "momentje", "moment",
        "eigenlijk", "voordat je ophangt", "voor je ophangt",
    ),
    "pl": (
        "jeszcze jedno", "jeszcze jedna rzecz", "jeszcze jedno pytanie", "jeszcze coś", "chwila", "chwileczkę",
        "sekundę", "moment", "właściwie", "zanim się rozłączysz",
    ),
    "de": (
        "moment", "moment mal", "einen moment", "sekunde", "noch eine frage", "noch eine sache", "noch ein ding",
        "eine frage noch", "eine sache noch", "eigentlich", "bevor du auflegst", "bevor ich auflege",
    ),
    "ja": ("もう一つ", "もうひとつ", "あと一つ", "あとひとつ", "ちょっと待って", "それと", "実は", "切る前に"),
    "ru": (
        "ещё одно", "еще одно", "ещё один вопрос", "еще один вопрос", "ещё кое-что", "еще кое-что", "секунду",
        "минутку", "минуточку", "вообще-то", "прежде чем",
    ),
    "fr": (
        "encore une chose", "une dernière chose", "encore une question", "une autre question", "une dernière question",
        "un instant", "un moment", "une seconde", "en fait", "avant de raccrocher", "attendez",
    ),
}  # fmt: skip
# Her closing wishes and offers to wait at the end of a call (owner, 18 Oct, after reading how her calls end: half of
# 44 ended "Enjoy your evening!", "I'll be here whenever you're ready."): each a pattern a part of her last sentence must
# start with, and the most words that part may have (a wish is short: "Good luck with that is what I'd say" is none).
# Matched by calls._farewell on the call language's and English's, case folded; a leading "and" is left out (AND).
# Known gap: the other nine languages' patterns are untested by a native speaker.
FAREWELLS: dict[str, tuple[tuple[str, int], ...]] = {
    "en": (
        (r"enjoy (?:your|the rest of your) \w+", 6),
        (r"have an? (?:\w+ )?(?:rest of your )?(?:day|afternoon|evening|night|morning|weekend|week|one|game|match|trip"
         r"|time|session|workout|run|flight|holiday|meal|dinner|lunch)\b", 8),
        (r"have fun\b", 5),
        (r"(?:sleep (?:well|tight)|sweet dreams|rest well)\b", 5),
        (r"good luck\b", 6),
        (r"(?:i'll|i will) (?:catch|talk to|speak to) you\b", 8),
        (r"(?:i'll|i will) be (?:right )?here\b", 20),
        (r"i'm (?:still |always )?here (?:if|when|whenever|for you)\b", 20),
        (r"(?:just )?say the word\b", 20),
        (r"(?:until|till) next time\b", 4),
    ),
    "nl": (
        (r"(?:nog )?(?:een )?fijne? (?:dag|avond|middag|ochtend|nacht|weekend|week|wedstrijd|reis)\b", 6),
        (r"geniet (?:nog )?van je \w+", 6),
        (r"slaap lekker\b", 4),
        (r"(?:veel )?succes\b", 6),
        (r"veel plezier\b", 6),
        (r"ik ben er (?:als|wanneer|voor je)\b", 12),
        (r"ik ben hier (?:als|wanneer)\b", 12),
        (r"ik spreek je\b", 6),
    ),
    "de": (
        (r"(?:einen )?schönen (?:tag|abend|nachmittag|morgen)\b", 6),
        (r"(?:ein )?schönes wochenende\b", 6),
        (r"schlaf gut\b", 4),
        (r"viel (?:erfolg|spaß|glück)\b", 6),
        (r"ich bin (?:da|hier),? (?:wenn|falls)\b", 12),
    ),
    "fr": (
        (r"bonne (?:journée|soirée|fin de journée|fin de soirée|après-midi|semaine)\b", 6),
        (r"bon (?:week-end|weekend|après-midi|courage)\b", 6),
        (r"dors bien\b", 4),
        (r"je suis là (?:si|quand|pour toi)\b", 12),
    ),
    "es": (
        (r"que (?:tengas|pases) (?:un |una )?(?:buen|buena|bonito|bonita|feliz) (?:día|tarde|noche|fin de semana)\b", 8),
        (r"disfruta (?:de )?(?:tu|el resto de tu) \w+", 7),
        (r"que descanses\b", 4),
        (r"(?:mucha )?suerte\b", 6),
        (r"aquí (?:estoy|estaré)\b", 12),
    ),
    "it": (
        (r"buona (?:giornata|serata|notte|domenica|settimana)\b", 5),
        (r"buon (?:pomeriggio|weekend|fine settimana|lavoro|divertimento)\b", 5),
        (r"dormi bene\b", 4),
        (r"in bocca al lupo\b", 6),
        (r"sono qui (?:se|quando|per te)\b", 12),
    ),
    "pt": (
        (r"tenha (?:um |uma )?(?:bom|boa|ótimo|ótima) (?:dia|tarde|noite|fim de semana)\b", 8),
        (r"aproveite (?:o resto d[aeo]|a sua|o seu|sua|seu) \w+", 7),
        (r"durma bem\b", 4),
        (r"boa sorte\b", 6),
        (r"estou aqui (?:se|quando|para)\b", 12),
    ),
    "pl": (
        (r"miłego (?:dnia|wieczoru|weekendu|popołudnia)\b", 5),
        (r"(?:dobrej nocy|śpij dobrze)\b", 4),
        (r"powodzenia\b", 6),
        (r"jestem tu(?:taj)?,? (?:jeśli|gdy|kiedy)\b", 12),
    ),
    "ru": (
        (r"хорошего (?:дня|вечера|выходных)\b", 5),
        (r"удачи\b", 6),
        (r"я (?:здесь|на связи)\b", 12),
    ),
    "ja": ((r"(?:良い|よい)(?:一日|夜|週末)を", 1), (r"頑張って", 1), (r"いつでも", 1)),
    "ko": ((r"좋은 (?:하루|저녁|주말) 보내(?:세요|요)", 6), (r"잘 자(?:요)?", 3), (r"화이팅", 3), (r"언제든(?:지)?", 8)),
}  # fmt: skip
AND: dict[str, str] = {
    "en": "and", "nl": "en", "de": "und", "fr": "et", "es": "y", "it": "e", "pt": "e", "pl": "i", "ru": "и",
}  # fmt: skip
# her goodbyes that are also "until <a time>" ("Es bleibt bewölkt, bis morgen."): one counts only with nothing but
# soft words, acknowledgements or thanks before it in its sentence ("Okay, bis morgen!"; review 18 Oct)
GOODBYES_UNTIL: dict[str, tuple[str, ...]] = {
    "es": ("hasta mañana", "hasta luego"),
    "it": ("a domani", "a dopo"),
    "pt": ("até amanhã", "até logo"),
    "nl": ("tot morgen", "tot later", "tot straks"),
    "pl": ("do jutra",),
    "de": ("bis morgen", "bis später", "bis dann"),
    "ru": ("до завтра",),
    "fr": ("à demain", "à plus tard", "à tout à l'heure"),
}
# a comma part that starts with one of these goes on from the part before it in its sentence: "I have to go, uh, to
# the dentist" (Whisper writes a hesitation as a comma) is an errand, "See you, at eight" a plan; English's count on
# every call. None for Japanese and Korean (their particles come after the word).
CONTINUING: dict[str, tuple[str, ...]] = {
    "en": ("to", "for", "at", "with", "back", "home", "into", "by", "on", "of"),
    "es": ("a", "al", "para", "con", "de vuelta"),
    "it": ("a", "al", "alla", "da", "per", "con"),
    "pt": ("a", "ao", "à", "para", "pra", "com"),
    "nl": ("naar", "om", "voor", "met", "terug"),
    "pl": ("do", "na", "po", "z"),
    "de": ("zu", "zum", "zur", "nach", "um", "für", "mit", "zurück"),
    "ru": ("в", "на", "к", "домой"),
    "fr": ("à", "au", "aux", "chez", "pour", "avec"),
}  # fmt: skip


# Stage directions in her answer ("(pause)", "(short pause)", "[beat]", "*smiles*", "(laughs)") are never spoken or
# sent (voice.without_stage_directions; owner, 18 Oct: her "(pause)" between chords was read aloud): a whole bracketed
# group made only of these words and their STAGE_SOFT words ("short", "long", "a"), with one of these at least, in any
# of the languages, or a starred one of STAGE_STARRED's. Brackets that hold anything else stay ("C (live version)",
# "(G shapes)", "(x2)").
STAGE_DIRECTIONS: dict[str, tuple[str, ...]] = {
    "en": (
        "pause", "pauses", "pausing", "beat", "laughs", "laugh", "laughing", "laughter", "chuckles", "chuckle",
        "chuckling", "giggles", "giggle", "giggling", "sighs", "sigh", "sighing", "smiles", "smile", "smiling", "grins",
        "grin", "grinning", "winks", "wink", "winking", "nods", "nod", "nodding", "gasps", "gasp", "whispers",
        "whispering", "clears throat", "clears her throat", "clears my throat", "breath", "takes a breath",
        "takes a deep breath", "breathes", "inhales", "exhales", "silence", "hums", "humming", "claps", "applause",
        "yawns",
    ),
    "es": (
        "pausa", "silencio", "ríe", "se ríe", "risas", "risa", "suspira", "suspiro", "sonríe", "sonrisa", "guiña",
        "guiño", "carcajada",
    ),
    "it": (
        "pausa", "silenzio", "ride", "risata", "risate", "ridacchia", "sospira", "sospiro", "sorride", "sorriso",
        "occhiolino", "fa l'occhiolino",
    ),
    "ko": ("멈춤", "쉼", "침묵", "웃음", "웃으며", "웃는다", "한숨", "미소", "미소 지으며"),
    "pt": (
        "pausa", "silêncio", "ri", "risos", "risada", "risadas", "suspira", "suspiro", "sorri", "sorriso", "pisca",
        "piscadela",
    ),
    "nl": (
        "pauze", "stilte", "lacht", "gelach", "grinnikt", "giechelt", "zucht", "zuchten", "glimlacht", "glimlach",
        "knipoogt", "knipoog", "knikt",
    ),
    "pl": (
        "pauza", "przerwa", "cisza", "śmiech", "śmieje się", "chichot", "wzdycha", "westchnienie", "uśmiech",
        "uśmiecha się", "mruga",
    ),
    "de": (
        "pause", "stille", "schweigen", "lacht", "lachen", "kichert", "seufzt", "seufzer", "lächelt", "lächeln",
        "zwinkert", "nickt", "räuspert sich",
    ),
    "ja": ("間", "沈黙", "笑", "笑い", "笑う", "ため息", "微笑む", "微笑み", "ほほえむ", "一拍", "間を置く", "間を置いて"),
    "ru": (
        "пауза", "тишина", "молчание", "смеётся", "смеется", "смех", "смешок", "вздыхает", "вздох", "улыбается",
        "улыбка", "подмигивает", "кивает",
    ),
    "fr": (
        "pause", "silence", "un temps", "rit", "rires", "rire", "ricane", "glousse", "soupire", "soupir", "sourit",
        "sourire", "clin d'œil", "fait un clin d'œil",
    ),
}  # fmt: skip
# the ones of STAGE_DIRECTIONS that count between stars too ("*smiles*", "*laughing*"): third-person and -ing forms
# only, never one that is also a plain word in a text ("Tap the *pause* button", "Don't forget to *smile*!", "*Pause*",
# Italian's "ride": "Enjoy the *ride*!"; review 18 Oct)
STAGE_STARRED: dict[str, tuple[str, ...]] = {
    "en": (
        "pauses", "pausing", "laughs", "laughing", "chuckles", "chuckling", "giggles", "giggling", "sighs", "sighing",
        "smiles", "smiling", "grins", "grinning", "winks", "winking", "nods", "nodding", "gasps", "whispers",
        "whispering", "clears throat", "clears her throat", "clears my throat", "takes a breath", "takes a deep breath",
        "breathes", "inhales", "exhales", "hums", "humming", "claps", "yawns",
    ),
    "es": ("ríe", "se ríe", "suspira", "sonríe", "guiña"),
    "it": ("ridacchia", "sospira", "sorride", "fa l'occhiolino"),
    "ko": ("웃으며", "웃는다", "미소 지으며"),
    "pt": ("suspira", "sorri", "pisca"),
    "nl": ("lacht", "grinnikt", "giechelt", "zucht", "glimlacht", "knipoogt", "knikt"),
    "pl": ("śmieje się", "wzdycha", "uśmiecha się", "mruga"),
    "de": ("lacht", "kichert", "seufzt", "lächelt", "zwinkert", "nickt", "räuspert sich"),
    "ja": ("笑う", "微笑む", "ほほえむ", "間を置く", "間を置いて"),
    "ru": ("смеётся", "смеется", "вздыхает", "улыбается", "подмигивает", "кивает"),
    "fr": ("rit", "ricane", "glousse", "soupire", "sourit", "fait un clin d'œil"),
}  # fmt: skip
# the words beside a stage direction inside its brackets: "(short pause)", "(a beat)", "(laughs softly)"
STAGE_SOFT: dict[str, tuple[str, ...]] = {
    "en": (
        "a", "an", "and", "then", "short", "long", "brief", "slight", "small", "little", "quick", "dramatic",
        "thoughtful", "deep", "soft", "softly", "gentle", "gently", "warm", "warmly", "quiet", "quietly", "light",
        "lightly", "big", "slowly", "happily", "nervously", "playfully", "briefly",
    ),
    "es": ("una", "un", "y", "breve", "corta", "larga", "pequeña", "suavemente"),
    "it": ("una", "un", "e", "breve", "lunga", "piccola", "dolcemente"),
    "ko": ("잠시", "잠깐", "짧은", "긴", "작은", "큰"),
    "pt": ("uma", "um", "e", "breve", "curta", "longa", "pequena"),
    "nl": ("een", "en", "korte", "lange", "kleine", "even", "zacht", "zachtjes"),
    "pl": ("i", "krótka", "długa", "mała"),
    "de": ("eine", "ein", "und", "kurze", "lange", "kleine", "leise", "sanft"),
    "ja": ("短い", "長い", "少し", "小さな", "ちょっと"),
    "ru": ("и", "короткая", "долгая", "длинная", "небольшая", "тихо"),
    "fr": ("une", "un", "et", "courte", "longue", "petite", "petit", "doucement"),
}  # fmt: skip


def made_of(units: tuple[str, ...], phrases: Collection[tuple[str, ...]], soft: Collection[tuple[str, ...]]) -> bool:
    """Whether `units` are wholly `phrases` and `soft` ones in any order, with at least one of `phrases`."""
    reached = {0: False}  # how far they are made of them: whether one of `phrases` came before that point
    for start in range(len(units)):
        if start not in reached:
            continue
        for options, counts in ((phrases, True), (soft, False)):
            for option in options:
                end = start + len(option)
                if units[start:end] == option:
                    reached[end] = reached.get(end, False) or reached[start] or counts
    return reached.get(len(units), False)


# On a call, her own voice from a loud speaker is told from the caller's words by her content words (calls.is_own_voice):
# these short function words are left out of the count ("the dentist at three" is two words of hers, not four).
# Japanese, written without spaces, is compared by characters instead.
_FUNCTION: dict[str, tuple[str, ...]] = {
    "en": (
        "a", "an", "the", "i", "you", "he", "she", "it", "we", "they", "me", "him", "us", "them", "my", "your", "his",
        "her", "its", "our", "their", "is", "are", "was", "were", "be", "am", "do", "does", "did", "have", "has", "had",
        "will", "would", "can", "to", "of", "in", "on", "at", "for", "with", "from", "by", "as", "and", "or", "but",
        "so", "if", "that", "this", "there", "it's", "i'm", "you're", "that's",
    ),
    "es": (
        "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "a", "al", "en", "y", "o", "que", "es",
        "son", "está", "hay", "por", "con", "se", "lo", "le", "les", "su", "sus", "mi", "tu", "me", "te", "nos",
    ),
    "it": (
        "il", "lo", "la", "i", "gli", "le", "un", "una", "uno", "di", "a", "da", "in", "con", "su", "per", "e", "o",
        "che", "è", "del", "della", "dei", "al", "alla", "nel", "nella", "mi", "ti", "si", "ci", "sono",
    ),
    "ko": ("이", "그", "저", "것", "수", "더", "좀", "잘", "또", "제", "내", "그리고"),
    "pt": (
        "o", "a", "os", "as", "um", "uma", "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas", "e", "ou",
        "que", "é", "por", "com", "se", "me", "te", "seu", "sua",
    ),
    "nl": (
        "de", "het", "een", "en", "of", "van", "in", "op", "te", "aan", "met", "voor", "is", "zijn", "er", "dat", "die",
        "dit", "ik", "je", "jij", "u", "hij", "zij", "ze", "we", "wij", "om", "ook", "maar", "als", "bij", "naar",
    ),
    "pl": (
        "i", "w", "we", "z", "ze", "na", "do", "się", "to", "jest", "są", "że", "a", "o", "od", "po", "za", "jak",
        "ten", "ta", "co", "u",
    ),
    "de": (
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "und", "oder", "in", "im",
        "an", "am", "auf", "zu", "zum", "zur", "mit", "von", "vom", "für", "ist", "sind", "es", "ich", "du", "er",
        "sie", "wir", "ihr", "dass", "so", "auch", "aber", "um",
    ),
    "ja": (),
    "ru": (
        "и", "в", "во", "на", "с", "со", "к", "по", "о", "об", "у", "за", "из", "от", "до", "а", "но", "что", "это",
        "как", "я", "ты", "он", "она", "оно", "мы", "вы", "они", "же", "ли", "бы", "то", "так",
    ),
    "fr": (
        "le", "la", "les", "l", "un", "une", "des", "de", "du", "d", "à", "au", "aux", "en", "et", "ou", "que", "qu",
        "qui", "est", "sont", "c'est", "il", "elle", "je", "j", "tu", "nous", "vous", "ils", "elles", "on", "ce", "se",
        "sa", "son", "ses", "mon", "ma", "mes", "pour", "par", "avec", "dans", "sur",
    ),
}  # fmt: skip
FUNCTION_WORDS = frozenset(word for words in _FUNCTION.values() for word in words)


def function_words(language: str) -> frozenset[str]:
    """One language's function words ("the", "is", "to" in English), as written in `_FUNCTION`."""
    return frozenset(_FUNCTION[language])


# a "no" the caller says over her: in a transcript of what stopped her, one she did not say herself makes it the
# caller's, however much of her sentence it repeats (Japanese: inside the characters)
_NEGATION: dict[str, tuple[str, ...]] = {
    "en": (
        "no", "not", "never", "nope", "don't", "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't", "can't",
        "cannot", "won't", "wouldn't", "shouldn't", "couldn't", "haven't", "hasn't",
    ),
    "es": ("no", "nunca", "tampoco", "ni"),
    "it": ("non", "mai", "né"),
    "ko": ("안", "못", "아니", "아니요", "아뇨", "아니야", "않아", "않아요"),
    "pt": ("não", "nunca", "nem"),
    "nl": ("niet", "nee", "geen", "nooit"),
    "pl": ("nie", "nigdy", "ani"),
    "de": ("nicht", "nein", "kein", "keine", "keinen", "keiner", "nie", "niemals"),
    "ja": ("ない", "ません", "いいえ", "いや", "だめ", "違う"),
    "ru": ("не", "нет", "никогда", "ни"),
    "fr": ("non", "ne", "pas", "jamais"),
}  # fmt: skip
NEGATIONS = frozenset(word for words in _NEGATION.values() for word in words)
# Japanese stop, yes and no words, matched inside a transcript's characters
JAPANESE_CALLER_WORDS = tuple(word for word in _INTERRUPT["ja"] + _YES["ja"] + _NEGATION["ja"] if " " not in word)

# Words that ask for a code or a password: a message she starts herself never holds one (initiative.check_text), in
# any of the 11 languages whatever the language setting. Matched as whole words in any case; a "*" at the end takes
# any ending ("hasł*": hasło, hasła, hasłem); Japanese and Korean (in SUBSTRING_CODES) are matched inside the text,
# since their words carry no spaces or have particles attached ("비밀번호를").
SECRET_WORDS: dict[str, tuple[str, ...]] = {
    "en": (
        "password", "passwords", "passcode", "passcodes", "passphrase", "passphrases", "pin", "pins", "pin code",
        "pin codes", "login", "logins", "log-in", "log-ins", "credentials", "bank details", "bank account",
        "bank accounts", "bank login", "bank logins", "bank app", "bank apps", "bank card", "bank cards",
        "account number", "account numbers", "card number", "card numbers", "card details", "credit card",
        "credit cards", "debit card", "debit cards", "cvv", "cvc", "iban", "verification code", "verification codes",
        "security code", "security codes", "one-time code", "one-time codes", "sms code", "sms codes", "login code",
        "login codes", "access code", "access codes", "confirmation code", "confirmation codes", "2fa", "otp",
        "code from the sms", "code in the sms",
    ),
    "es": (
        "contraseña*", "clave de acceso", "clave secreta", "clave bancaria", "pin", "código pin", "códigos pin",
        "código de verificación", "código de seguridad", "código de acceso", "código de confirmación", "código sms",
        "código de un solo uso", "credenciales", "datos bancarios", "número de cuenta", "número de tarjeta",
        "tarjeta de crédito", "tarjeta de débito",
    ),
    "it": (
        "password", "parola d'ordine", "parola chiave", "pin", "codice pin", "codice di verifica",
        "codice di sicurezza", "codice di accesso", "codice di conferma", "codice sms", "codice otp", "credenziali",
        "dati bancari", "numero di conto", "numero della carta", "numero di carta", "carta di credito",
        "carta di debito",
    ),
    "ko": (
        "비밀번호", "암호", "인증번호", "인증 번호", "인증코드", "인증 코드", "보안코드", "보안 코드", "확인코드",
        "확인 코드", "핀 번호", "핀번호", "계좌번호", "계좌 번호", "카드번호", "카드 번호", "신용카드",
        "로그인 정보", "일회용 비밀번호",
    ),
    "pt": (
        "senha*", "palavra-passe", "palavras-passe", "pin", "código pin", "código de verificação",
        "código de segurança", "código de acesso", "código de confirmação", "código sms", "credenciais",
        "dados bancários", "número da conta", "número de conta", "número do cartão", "cartão de crédito",
        "cartão de débito",
    ),
    "nl": (
        "wachtwoord*", "pincode*", "pin", "toegangscode*", "verificatiecode*", "beveiligingscode*",
        "bevestigingscode*", "inlogcode*", "sms-code*", "eenmalige code*", "inloggegevens", "bankgegevens",
        "rekeningnummer*", "kaartnummer*", "creditcard*", "betaalpas*",
    ),
    "pl": (
        "hasł*", "haseł", "pin", "kod pin", "kodu pin", "kod weryfikacyjn*", "kodu weryfikacyjn*",
        "kod bezpieczeństwa", "kodu bezpieczeństwa", "kod dostępu", "kodu dostępu", "kod potwierdzając*",
        "kodu potwierdzając*", "kod sms", "kodu sms", "kod jednorazow*", "kodu jednorazow*", "dane logowania",
        "danych logowania", "dane do logowania", "dane bankowe", "danych bankowych", "numer konta", "numeru konta",
        "numer karty", "numeru karty", "dane karty", "danych karty",
    ),
    "de": (
        "passwort*", "passwört*", "kennwort*", "kennwört*", "pin", "pin-code*", "pincode*", "geheimzahl*",
        "zugangsdaten", "anmeldedaten", "bestätigungscode*", "sicherheitscode*", "zugangscode*",
        "verifizierungscode*", "einmalcode*", "sms-code*", "tan-nummer*", "mtan", "pushtan", "smstan", "sms-tan",
        "bankdaten", "kontodaten", "kontonummer*", "kartennummer*", "kreditkarte*",
    ),
    "ja": (
        "パスワード", "パスコード", "暗証番号", "認証コード", "確認コード", "セキュリティコード", "ワンタイム",
        "ログイン情報", "口座番号", "カード番号", "クレジットカード",
    ),
    "ru": (
        "парол*", "пин", "пин-код*", "пинкод*", "код подтверждени*", "кода подтверждени*", "код безопасности",
        "кода безопасности", "код доступа", "кода доступа", "код из смс", "смс-код*", "одноразов* код*",
        "проверочн* код*", "учётн* данн*", "учетн* данн*", "банковск* данн*", "номер счёта", "номер счета",
        "номер карты", "данные карты", "кредитн* карт*",
    ),
    "fr": (
        "mot de passe", "mots de passe", "pin", "code pin", "code de vérification", "code de sécurité",
        "code d'accès", "code de confirmation", "code sms", "code à usage unique", "identifiants",
        "coordonnées bancaires", "données bancaires", "numéro de compte", "numéro de carte", "carte bancaire",
        "carte de crédit", "cryptogramme",
    ),
}  # fmt: skip
SUBSTRING_CODES = frozenset({"ja", "ko"})


def _plain(text: str) -> str:
    """Casefolded, without accents: "Français" and "francais" are the same name."""
    decomposed = unicodedata.normalize("NFKD", text.strip().casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def stop_words(code: str) -> tuple[str, ...]:
    """A language's words that stop her on a call ("wacht", "warte"; none for an unknown code)."""
    return _INTERRUPT.get(code, ())


def negations(code: str) -> tuple[str, ...]:
    """A language's negation words (none for an unknown code): a practice recap point that drops one is refused."""
    return _NEGATION.get(code, ())


def find(name: str) -> Language | None:
    """The language by its code, English name or own name (any case, accents optional), or None."""
    wanted = _plain(name)
    for language in LANGUAGES.values():
        if wanted in (language.code, _plain(language.english), _plain(language.own)):
            return language
    return None
