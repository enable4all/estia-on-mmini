"""The one language she listens and speaks in: the 11 she offers, their names and their yes words.

Only the languages Whisper large-v3 hears best (OpenAI's published word error rates, all at most 5.3 %) that also have
a voice; English first, then from the lowest error rate. One at a time: Whisper is told the language on every
request (no detection) and Gemma answers in it. The language changes only by the user's typed /language.
"""

from __future__ import annotations

import re
import unicodedata
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
# are clear on a phone.
LEAVING: dict[str, tuple[str, ...]] = {
    "en": (
        "i'm hanging up", "i am hanging up", "i'll hang up", "i will hang up", "i'm going to hang up",
        "i'm gonna hang up", "hanging up", "i have to go", "i really have to go", "i've got to go", "i've gotta go",
        "i got to go", "i gotta go", "gotta go", "got to go", "i need to go", "i really need to go", "i must go",
        "i'm off", "i am off",
    ),
    "es": ("tengo que irme", "me tengo que ir", "voy a colgar", "te cuelgo", "tengo que colgar"),
    "it": ("devo andare", "devo scappare", "devo riattaccare", "riattacco"),
    "ko": ("끊을게", "끊을게요", "가 봐야 해", "가봐야 해", "가 봐야 해요", "가봐야 해요", "가 봐야겠어요", "가봐야겠어요"),
    "pt": ("tenho que ir", "tenho de ir", "preciso ir", "vou desligar", "tenho que desligar"),
    "nl": (
        "ik hang op", "ik hang nu op", "ik ga ophangen", "ik moet ophangen", "ik moet nu ophangen", "ik moet gaan",
        "ik moet nu gaan", "ik moet ervandoor", "ik moet er nu vandoor", "ik ga ervandoor", "ik moet weg",
        "ik moet nu weg",
    ),
    "pl": ("muszę iść", "muszę lecieć", "muszę kończyć", "rozłączam się"),
    "de": (
        "ich lege auf", "ich leg auf", "ich lege jetzt auf", "ich leg jetzt auf", "ich muss auflegen",
        "ich muss jetzt auflegen", "ich muss los", "ich muss jetzt los", "ich muss gehen", "ich muss jetzt gehen",
        "ich muss weg", "ich muss jetzt weg",
    ),
    # never "失礼します" alone: it is also "excuse me"
    "ja": ("切るね", "切るよ", "切ります", "行かなきゃ", "そろそろ失礼します", "それでは失礼します"),
    "ru": ("мне пора", "мне надо идти", "мне нужно идти", "кладу трубку", "я кладу трубку"),
    "fr": ("je raccroche", "je dois raccrocher", "je dois y aller", "je dois partir", "il faut que j'y aille"),
}  # fmt: skip
# the short words that may come before or after leave-taking in its sentence ("okay, so, I have to go now"): a
# language's "okay", never a "yes", "exactly" or "true" that answers her ("Yes, I have to go" is an answer); English's
# count on every call
LEAVING_SOFT: dict[str, tuple[str, ...]] = {
    "en": (
        "ok", "okay", "alright", "all right", "right", "so", "well", "anyway", "sorry", "now", "then", "bye",
        "goodbye", "for now", "right now",
    ),
    "es": ("vale", "bueno", "pues", "ya", "ahora", "adiós"),
    "it": ("va bene", "allora", "ora", "adesso", "ciao"),
    "ko": ("알겠어요", "그럼", "이제", "안녕"),
    "pt": ("certo", "beleza", "bem", "então", "agora", "tchau"),
    "nl": ("oké", "okee", "prima", "nou", "zo", "dus", "even", "hoor", "doei", "dag"),
    "pl": ("dobrze", "no", "dobra", "teraz", "pa"),
    "de": ("alles klar", "so", "also", "na", "tschüss"),
    "ja": ("じゃあ", "じゃ", "それじゃあ", "それじゃ", "では", "もう", "そろそろ"),
    "ru": ("хорошо", "ну", "всё", "все", "ладно", "пока"),
    "fr": ("d'accord", "bon", "bref", "maintenant", "salut"),
}  # fmt: skip
# leave-taking that also means something else alone ("I'm off" is a day off too): it counts only with a soft word
# beside it ("Okay, I'm off.", "I'm off now")
LEAVING_NOT_ALONE: dict[str, tuple[str, ...]] = {"en": ("i'm off", "i am off")}
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
