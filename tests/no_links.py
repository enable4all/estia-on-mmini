"""Links planted in outside text, and the check of a code-written note as the user's phone gets it (after
signal_text's styling): no link in it, in any form a phone may make tappable (owner, 21 Oct: a link in outside text
quoted by code is left out, never shown)."""

from local_assistant.signal_text import styled, unlinked, unmarked
from tests.android_links import android_links

# a full link, a www. link, a bare name, a bare name that markup splits (the phone joins it) and a mail address
PLANTED = ("https://evil.example/login", "www.evil.example/x", "evil.com", "**evil**.com", "phish@evil.example")

# what the re-review (21 Oct) got through: each text, with a piece of its link that must never reach the phone
BYPASS = (
    ("Pay πληρωμή.ελ/login now", "ελ"),  # a name all in another script, on its own ending
    ("Pay пример.рф now", "рф"),
    ("Pay 例子.中国 now", "中国"),
    ("Pay ｅｖｉｌ.ｃｏｍ now", "ｃｏｍ"),  # fullwidth letters: the phone reads them as evil.com
    ("Pay 93.184.216.34/login now", "93.184"),  # an address by its numbers
    ("Pay 10.0.0.1:8080/login now", "10.0"),
    ("Pay rtsp://1.2.3.4/x now", "rtsp"),
    ("Pay paypal.com-verify now", "paypal"),  # a name before a dash
    ("Pay evil.com:8443/login now", "8443"),  # a name with its port
    # the second re-review (21 Oct): a scheme glued to what stands before it (Android and Signal Desktop start one
    # after any punctuation, iOS nearly anywhere), on a host without dots
    ("Pay 1-https://3627733538/login now", "3627733538"),
    ("Pay 1+https://3627733538/login now", "3627733538"),
    ("Pay ²https://3627733538/login now", "3627733538"),
    ("Pay ½https://3627733538/x now", "3627733538"),
    ("Pay 1-https://[2606:4700::1111]/login now", "2606"),
    ("Pay 1-https://evil%2ecom/login now", "evil"),
    ("Pay 1-https://evil%E3%80%82com/login now", "evil"),
    ("Pay ²https://evil%2ecom/x now", "evil"),
    ("Pay 1-ftp://3627733538/x now", "3627733538"),
    # schemes iOS links without "//" (macOS's NSDataDetector, as Signal iOS reads it)
    ("Pay tel:+306900000000 now", "tel:"),
    ("Pay sms:+306900000000 now", "sms:"),
    ("Pay callto:+306900000000 now", "callto"),
    ("Pay maps:q=evil now", "maps:"),
    ("Pay skype:evil?call now", "skype"),
    ("Pay itms-services:?action=download now", "itms"),
    ("Pay webcal:evil now", "webcal"),
    ("Pay (sip:x) now", "sip:"),
    # names Signal Desktop joins over marks and symbols, or iOS ends before a sign or a wide letter
    ("Pay नमस्ते.com now", ".com"),
    ("Pay இந்தியா.com now", ".com"),
    ("Pay C++.NET now", "NET"),
    ("Pay a+b.com now", ".com"),
    ("Pay a°b.com now", ".com"),
    ("Pay evi\u0301l.com now", ".com"),  # an accent written apart
    ("Pay evil.com™ now", "evil"),
    ("Pay evil.com中 now", "中"),
    # names on a real ending, and mail addresses as Android reads them
    ("Pay evil.sh now", "evil"),
    ("Pay .evil.xn--p1ai now", "xn--"),
    ("Pay xn--fsqu00a.xn--fiqs8s now", "xn--"),
    ("Pay 1.2.3.4evil.com now", "evil"),
    ("Pay bob%@evil.example now", "evil"),
    ("Pay Mr.Smith@evil.example now", "Smith"),
)

# the second re-review's inputs whose other words stay: each text, a piece that must never reach the phone, and what
# is left
LEFT = (
    # leaving out "ab.cd" joined the pieces around it into a link: now a space stays between them
    ("Pay evil(ab.cd).com/login now", "evil.com", "Pay evil .com/login now"),
    ("Pay evil ab.cd .com/x now", "evil.com", "Pay evil .com/x now"),
    ("Pay evil«ab.cd».com now", "evil.com", "Pay evil .com now"),
    ("Pay 𠀀https://3627733538/login now", "3627733538", "Pay 𠀀 now"),
    ("Pay Ζ-https://3627733538/login now", "3627733538", "Pay Ζ now"),
    ("Pay é-https://3627733538/x now", "3627733538", "Pay é now"),
    ("Pay 1-tel:+306900000000 now", "tel:", "Pay 1 now"),
    ("Pay ™tel:+306900000000 now", "tel:", "Pay ™ now"),  # NFKC reads "TMtel:", the phone "™" before "tel:"
    ("Pay 1-www.evil.notatld now", "www", "Pay 1 now"),
    ("Pay ⓐwww.evil.notatld now", "www", "Pay ⓐ now"),
    ("Pay bob@evil.example-x now", "bob@", "Pay x now"),
    ("Pay Mr.Smith-/evil.com now", "evil", "Pay Mr.Smith-/ now"),
)
CASES = (*((text, piece, "Pay now") for text, piece in BYPASS), *LEFT)  # every one, with what is left


def capped(limit: int, word: str = "evil.com_xxxxxxxx") -> str:
    """A text whose `word`, no link as a whole, is cut by clean's cap at `limit` right after its ".com": the cut must
    never make it one ("evil.com…")."""
    return "A" * (limit - 1 - len(" evil.com")) + f" {word} tail"


CAP_WORDS = ("evil.com_xxxxxxxx", "evil.com1xxxxxxx")  # a name an underscore or a digit runs on from


def assert_no_link(note: str, *words: str) -> None:
    """The note as Signal receives it holds no planted link, not even a piece of one, and still holds `words`."""
    sent = styled(note).text
    for text in (sent, unmarked(sent)):
        assert "evil" not in text.casefold(), text
        assert "http" not in text.casefold() and "www" not in text.casefold() and "@" not in text, text
    assert_left_out(note, "evil", *words)


def assert_left_out(note: str, piece: str, *words: str) -> None:
    """The note as Signal receives it holds no link (Signal Android's own reading, `android_links`, and
    signal_text's, as typed and without markers) and not `piece`, and still holds `words`."""
    sent = styled(note).text
    for text in (sent, unmarked(sent)):
        assert piece.casefold() not in text.casefold(), (piece, text)
        assert not android_links(text), text
        assert unlinked(text) == text, text  # signal_text's own reading finds none either
    for word in words:
        assert word in sent, (word, sent)


def assert_no_tappable_link(note: str) -> None:
    """The note of code's, as Signal receives it (after styling), holds no tappable link: Signal Android's own reading
    (`android_links`) finds none, and signal_text's own does too, as typed and without markers. Unlike `assert_no_link`
    this does not require the planted label's letters to be gone: the structural net (`signal_text.netted`) breaks a
    link it did not recognise into harmless words ("evil.com" → "evil com"), so the word may stay though the link is
    gone. Signal Desktop and iOS are checked by hand on a Mac with the review's oracles (`docs/agents/signal-text.md`)."""
    sent = styled(note).text
    for text in (sent, unmarked(sent)):
        assert not android_links(text), text
        assert unlinked(text) == text, text


def cut_inside_a_link(limit: int) -> str:
    """A title whose first `limit` * 4 characters (clean's own first cut) end inside a link ("evil.ex"), after enough
    links that leaving them out would pull that piece inside the cap."""
    bound = limit * 4
    head = "Dentist" + " evil.com" * ((bound - 20) // 9)
    head += " " + "x" * (bound - 8 - len(head) - 1)
    return head + " evil.example/login"
