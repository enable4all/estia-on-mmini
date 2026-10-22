"""The markup converter: plain text plus Signal's text styles, offsets in UTF-16 code units."""

import time
import unicodedata

import pytest

from local_assistant import signal_text
from local_assistant.calendar_tools import clean_note
from local_assistant.signal_text import BODY_BYTES, Styled, netted, spoken, styled, unlinked, unmarked, valid
from tests.android_links import android_links
from tests.no_links import CASES, PLANTED, assert_left_out, assert_no_link, assert_no_tappable_link


def units(text: str) -> int:
    """UTF-16 code units, the way signal-cli and Signal's apps count."""
    return len(text.encode("utf-16-le")) // 2


def test_plain_text_goes_out_as_it_is() -> None:
    assert styled("Hello there. It is 3.5 degrees.") == Styled("Hello there. It is 3.5 degrees.", ())
    assert styled("") == Styled("", ())


def test_bold_strike_and_code() -> None:
    assert styled("a **big** day") == Styled("a big day", ("2:3:BOLD",))
    assert styled("~~Bob~~ Anna comes") == Styled("Bob Anna comes", ("0:3:STRIKETHROUGH",))
    assert styled("run `uv sync` first") == Styled("run uv sync first", ("4:7:MONOSPACE",))
    assert styled("**a** and **b**") == Styled("a and b", ("0:1:BOLD", "6:1:BOLD"))


def test_a_heading_line_is_a_bold_line() -> None:
    assert styled("# Plan\nstep one") == Styled("Plan\nstep one", ("0:4:BOLD",))
    assert styled("### Plan ###") == Styled("Plan", ("0:4:BOLD",))
    assert styled("# **Plan**") == Styled("Plan", ("0:4:BOLD",))  # one range, not two
    assert styled("## C#") == Styled("C#", ("0:2:BOLD",))


def test_list_starts_become_bullets() -> None:
    assert styled("- one\n* two\n  - three") == Styled("• one\n• two\n  • three", ())
    assert styled("- **Mail**: 2 new") == Styled("• Mail: 2 new", ("2:4:BOLD",))
    assert styled("* item with *care*") == Styled("• item with care", ("12:4:ITALIC",))


def test_italics_only_with_clear_edges() -> None:
    assert styled("*Note:* the shop closes early") == Styled("Note: the shop closes early", ("0:5:ITALIC",))
    assert styled("Use *only* this one.") == Styled("Use only this one.", ("4:4:ITALIC",))
    assert styled("09:30 Dentist *(Prinsengracht 12)*.") == Styled(
        "09:30 Dentist (Prinsengracht 12).", ("14:18:ITALIC",)
    )
    assert styled("(*really*)") == Styled("(really)", ("1:6:ITALIC",))
    assert styled("**bold *and italic* text**") == Styled("bold and italic text", ("0:20:BOLD", "5:10:ITALIC"))


@pytest.mark.parametrize(
    "text",
    [
        "5 * 3 = 15",
        "2*3*4 and 2*3",
        "2**3 and 4**2",
        "x**2 + y**2",
        "f(**kwargs, **extra)",
        "my_var_name, __init__.py and _x_ stay",
        "__not bold__",
        "src/**/*.py and lib/**/x",
        "*.txt and *.md",
        "don`t and won`t",
        "f*ck that sh*t",
        "***both***",
        "**open only",
        "~~open only",
        "~5 min",
        "**across\nlines**",
        "*across\nlines*",
        "---",
        "* * *",
        "- - -",
        "#1 About: The user's name is Alex Example.",
        "#c1 Thu 15 Oct, evening · call the dentist",
        "/new - start a fresh conversation",
        "-5 degrees",
        "1. first\n2. second",
        "| a | b |\n|---|---|",
        "> quoted",
        "これは*重要*です",
        "a ** b ** c",
        "*!* and (*…*)",
        "``double``",
        "```\nnot closed",
        "Ignore **/node_modules/** in git",  # a glob's inner text ends in a path separator: never a pair
        "**src/**",
        "**/build/**",
        "**C:\\temp\\**",
        "~~old/~~",
        "Skip */tmp/* too",
    ],
)
def test_never_touched(text: str) -> None:
    assert styled(text) == Styled(text, ())


def test_a_command_in_bold_stays_bold() -> None:
    assert styled("**/quiet**") == Styled("/quiet", ("0:6:BOLD",))


@pytest.mark.parametrize(
    "text",
    [
        "Go to **paypal-login**.com/verify",
        "Visit *evil*.com",
        "Visit *evil*.com today",
        "Visit `evil`.com",
        "Visit evil.**com** today",
        "bob@**evil**.com",
        "**bob**@evil.com",
        "**https**://evil.com",
        "**www**.evil.com",
        "See **пример**.com",
        "**Note**: see *evil*.com",  # the whole line stays as typed, its other pairs too
    ],
)
def test_markup_never_joins_a_link(text: str) -> None:
    """Signal makes a web or mail address tappable: a marker dropped inside one would join what the link guards read
    as two pieces ("**evil**.com"), so that line goes out as typed."""
    assert styled(text) == Styled(text, ())


def test_a_whole_link_or_name_in_markup_is_still_styled() -> None:
    assert styled("**evil.com**") == Styled("evil.com", ("0:8:BOLD",))
    assert styled("See `example.com` and *e.g.* this") == Styled(
        "See example.com and e.g. this", ("4:11:MONOSPACE", "20:4:ITALIC")
    )
    assert styled("**Mail**: 2 new\n*evil*.com") == Styled("Mail: 2 new\n*evil*.com", ("0:4:BOLD",))


def test_unmarked_leaves_out_every_marker() -> None:
    assert unmarked("Go to **paypal-login**.com/verify") == "Go to paypal-login.com/verify"
    assert unmarked("~~a~~ `b` *c*") == "a b c"
    assert unmarked("plain") == "plain"


@pytest.mark.parametrize(
    "link",
    [
        "https://example.com/a**b**c",
        "https://example.com/~~x~~/y",
        "https://example.com/*star*/",
        "www.example.com/a_b_c/`x`",
        "https://www.google.com/maps/dir/?api=1&origin=52.37,4.89&destination=Centraal",
    ],
)
def test_links_keep_every_character(link: str) -> None:
    assert styled(f"see {link} now") == Styled(f"see {link} now", ())
    assert styled(f"[docs]({link})").text == f"[docs]({link})"


def test_bold_around_a_link_keeps_the_link_whole() -> None:
    assert styled("**https://example.com/a_b**") == Styled("https://example.com/a_b", ("0:23:BOLD",))
    assert styled("**see https://example.com/x** now") == Styled("see https://example.com/x now", ("0:25:BOLD",))
    assert styled("More at **https://nos.nl**.") == Styled("More at https://nos.nl.", ("8:14:BOLD",))
    assert styled("See (**https://nos.nl**)") == Styled("See (https://nos.nl)", ("5:14:BOLD",))
    assert styled("**https://nos.nl**!") == Styled("https://nos.nl!", ("0:14:BOLD",))


def test_code_keeps_its_markers() -> None:
    assert styled("`**not bold**`") == Styled("**not bold**", ("0:12:MONOSPACE",))
    assert styled("**run `ls`**") == Styled("run ls", ("0:6:BOLD", "4:2:MONOSPACE"))


def test_a_closed_fence_is_a_monospace_block() -> None:
    assert styled("Here:\n```python\nx = 2**3\nprint(x)\n```\nDone.") == Styled(
        "Here:\nx = 2**3\nprint(x)\nDone.", ("6:17:MONOSPACE",)
    )
    assert styled("```\n```\nafter") == Styled("after", ())


@pytest.mark.parametrize(
    ("before", "lead"),
    [
        ("📚 ", 3),  # above U+FFFF: 2 units, then the space
        ("🎙️ ", 4),  # 🎙 (2) and its variation selector (1)
        ("👍🏽 ", 5),  # with a skin tone: 4
        ("👨‍👩‍👧 ", 9),  # a family joined by ZWJ: 8
        ("𠮷 ", 3),  # a CJK character above U+FFFF
        ("Το ", 3),  # Greek: 1 each
        ("これは", 3),  # Japanese kana: 1 each
        ("– → · • … ", 10),
    ],
)
def test_offsets_count_utf16_units(before: str, lead: int) -> None:
    result = styled(f"{before}**Practice recap** (Dutch)")
    assert result.text == f"{before}Practice recap (Dutch)"
    assert result.styles == (f"{lead}:14:BOLD",)
    assert lead == units(before)


def test_lengths_count_utf16_units_too() -> None:
    assert styled("**😀 hi**") == Styled("😀 hi", ("0:5:BOLD",))
    assert styled("Το **σημαντικό** είναι") == Styled("Το σημαντικό είναι", ("3:9:BOLD",))
    assert styled("これは**重要**です") == Styled("これは重要です", ("3:2:BOLD",))
    assert styled("📚 x\n**y**") == Styled("📚 x\ny", ("5:1:BOLD",))


def test_a_long_text_keeps_only_the_styles_in_its_first_2000_bytes() -> None:
    """signal-cli sends a text over 2000 UTF-8 bytes as its first 2000 bytes plus the whole text as an attachment:
    how the apps style past that piece is untested, so only ranges inside it go out."""
    filler = "ά" * 1200  # 2 bytes each: 2400 bytes
    result = styled(f"**start** {filler} **end**")
    assert result.text == f"start {filler} end"
    assert result.styles == ("0:5:BOLD",)
    crossing = styled(f"{'a' * (BODY_BYTES - 3)} **edge** and more")
    assert crossing.text.index("edge") == BODY_BYTES - 2 and crossing.styles == ()  # crosses the cut: left out
    assert styled(f"{'a' * (BODY_BYTES - 11)} **ok** {'b' * 50}").styles == (f"{BODY_BYTES - 10}:2:BOLD",)


def test_every_range_it_makes_is_valid() -> None:
    samples = [
        "**a** ~~b~~ `c` *d*\n# e\n- **f**",
        "👨‍👩‍👧 **family** and 𠮷 ~~old~~ `new`",
        "Το **σημαντικό** είναι",
        "```\ncode\n```\n**after**",
    ]
    for sample in samples:
        result = styled(sample)
        assert result.styles and valid(result.text, result.styles)


def test_valid_checks_what_signal_cli_would_refuse() -> None:
    assert valid("hello", ["0:5:BOLD", "1:2:ITALIC", "0:1:SPOILER", "4:1:STRIKETHROUGH", "2:1:MONOSPACE"])
    assert valid("hello", [])
    assert not valid("hello", ["0:5:bold"])  # signal-cli: "Invalid style"
    assert not valid("hello", ["0:5:UNDERLINE"])
    assert not valid("hello", ["0-5-BOLD"])  # "Invalid textStyle syntax"
    assert not valid("hello", ["-1:2:BOLD"])
    assert not valid("hello", ["0:6:BOLD"])  # past the end (signal-cli would send it anyway)
    assert not valid("hello", ["2:0:BOLD"])
    assert not valid("😀", ["0:3:BOLD"]) and valid("😀", ["0:2:BOLD"])


def test_spoken_drops_markers_and_struck_words_and_keeps_line_starts() -> None:
    assert spoken("Dinner with ~~Bob~~ Anna") == "Dinner with  Anna"
    assert spoken("09:30 Dentist *(Prinsengracht 12)*.") == "09:30 Dentist (Prinsengracht 12)."
    assert spoken("**Today**\n- *Gym* at 7") == "Today\n- Gym at 7"
    assert spoken("# Plan\n* one") == "# Plan\n* one"
    assert spoken("2*3 is 6") == "2*3 is 6"


@pytest.mark.parametrize("link", PLANTED)
def test_unlinked_leaves_out_every_link_a_phone_may_make_tappable(link: str) -> None:
    """Code's notes quote outside text without its links (owner, 21 Oct: never shown, not defanged): a full link, a
    www. link, a bare name, one that markup splits and a mail address; the other words and their styling stay."""
    text = unlinked(f"**Verify** your account at {link} today")
    assert text == "**Verify** your account at today"
    assert_no_link(text, "Verify", "your account at today")
    assert styled(text) == Styled("Verify your account at today", ("0:6:BOLD",))


@pytest.mark.parametrize(
    ("text", "left"),
    [
        ("Verify account: https://evil.example/login **URGENT**", "Verify account: **URGENT**"),
        ("Pay at evil.com/pay now.", "Pay at now."),
        ("Visit www.evil.example/x.", "Visit."),
        ("Mail phish@evil.example, today", "Mail, today"),
        ("https://evil.example/login", ""),
        ("evil.com: verify", "verify"),
        ("Verify account: evil.com", "Verify account"),
        ("Link: evil.com, Room 4", "Link: Room 4"),
        ("Join (https://zoom.us/j/1) at 9", "Join at 9"),
        ('See "evil.com" now', "See now"),
        ("Zoom https://zoom.us/j/1\nRoom 4", "Zoom\nRoom 4"),
        ("Go to **evil**.com now", "Go to now"),  # markup split it: the phone joins it
        ("Go to evil.**com** now", "Go to now"),
        ("**evil.com** now", "now"),  # markers that only wrapped it go with it
        ("**Pay at evil.com**", "**Pay at**"),  # the closing marker stays the bold's
        ("**evil.com and pay**", "**and pay**"),
        ("**URGENT** evil.com", "**URGENT**"),
        ("**Hi** go **evil**.com **now**", "**Hi** go **now**"),
        ("Wait...evil.ch", "Wait"),  # after dots, inside _ or after a dash, as initiative.has_link reads it
        ("Read _evil.com_ now", "Read now"),
        ("Call -evil.com now", "Call now"),
        ("See evil\u3002com", "See"),  # another script's full stop: a dot to the phone
        ("Two: evil.com, www.evil.example and phish@evil.example.", "Two: and."),
    ],
)
def test_unlinked_tidies_what_a_removed_link_leaves(text: str, left: str) -> None:
    assert unlinked(text) == left
    assert_no_link(unlinked(text))


@pytest.mark.parametrize(
    "text",
    [
        "Dentist at 09:30, e.g. Room 4.2/B; 3.5 km",
        "**Board** ~~meeting~~ `notes` *today*",
        "Mr. Brightside (live), feat. Alex",
        "会議。資料を持参",  # Japanese and Chinese end a sentence with 。: no name, whatever follows
        "Ραντεβού στις 10.30 π.μ. Καλημέρα. Τι κάνεις",
        "Version 1.2.3 on 21.10.2026, 3.5 km",
        "",
    ],
)
def test_a_text_without_a_link_comes_back_as_it_was(text: str) -> None:
    assert unlinked(text) == text


@pytest.mark.parametrize(("text", "piece", "left"), CASES)
def test_unlinked_leaves_out_what_the_re_review_got_through(text: str, piece: str, left: str) -> None:
    """The re-reviews (21 Oct): a link the tidying built from pieces ("evil(ab.cd).com"), a name all in another script
    or in fullwidth letters, an address by its numbers, another scheme, a name before a dash or with its port; a scheme
    glued to what stands before it, a scheme iOS links without "//", a name Signal Desktop joins over a mark or a
    symbol, a mail address as Android reads one."""
    assert unlinked(text) == left
    assert_left_out(left, piece, left)


@pytest.mark.parametrize(
    ("text", "left"),
    [
        ("Pay evil\u034f.com now", "Pay now"),  # a character no app shows, between the letters
        ("Pay evil\ufe0f.com now", "Pay now"),
        ("Pay https%3A%2F%2Fevil.com now", "Pay now"),  # a scheme written as a link's own encoding
        ("Mail mailto:phish@evil.example now", "Mail now"),
        ("Pay evil.com*1 now", "Pay *1 now"),  # as typed the phone sees evil.com before the lone marker
        ("Pay evil.com?x=1#top now", "Pay now"),
        ("Pay evil.coméx/login now", "Pay now"),  # a name on any letters' ending before a path
    ],
)
def test_unlinked_reads_hidden_marks_encodings_and_markers_as_the_phone_does(text: str, left: str) -> None:
    assert unlinked(text) == left
    assert_left_out(left, "evil")


# the re-review's probe of the tidying: whatever is left out, what is left holds no link, in any reading
TIDY = (
    "Pay evil ab.cd .com now",
    "Pay evil ab.cd .com/login now",
    "Pay evil(ab.cd).com now",
    'Pay evil"ab.cd".com now',
    "Pay evil[ab.cd].com/login now",
    "Pay evil- ab.cd .com now",
    "Pay evil: ab.cd .com now",
    "Pay evil (x@ab.example) .com now",
    "Pay evil{https://a.b}.com now",
    "Pay evil ab.cd, ef.gh .com now",
    "Pay paypal(ab.cd).com/verify now",
    "Pay evil ab.cd!com now",
    "Pay evil(ab.cd)@x.com now",
    "Mail bob(ab.cd)@evil.com now",
    "Pay evil<ab.cd>.com now",
    "Pay www(ab.cd).evil.com now",
    "Pay e(a.bc)v(d.ef)il(g.hi).(j.kl)co(m.no)m now",
)


@pytest.mark.parametrize("text", TIDY)
def test_what_unlinked_leaves_never_holds_a_link(text: str) -> None:
    left = unlinked(text)
    assert unlinked(left) == left
    for shown in (styled(left).text, unmarked(left)):
        assert not android_links(shown), shown
        assert unlinked(shown) == shown, shown
    assert left.startswith(("Pay", "Mail")) and left.endswith("now")


@pytest.mark.parametrize(
    "text",
    [
        "Call Mr.Smith back",
        "Meet at St.Mary's",
        "Jan.Novak 1:1",
        "Dr.Who night",
        "Read notes.txt and data.csv",
        "Send report.pdf today",
        "node.js meetup",
        "Look at index.html",
        "Φέρε χαρτιά, στυλό κ.λπ.",
        "Pay evil.coméx now",
        "Hotel:Ritz, Claim:x",  # a letter before the scheme ("hoTel:"): no link
        "Awww...so cute",
        "Lunch@12.30 with Alex",  # no mail address: its ending is no letters
    ],
)
def test_a_dotted_word_on_no_real_ending_stays_in_unlinked(text: str) -> None:
    """The second re-review (21 Oct): `unlinked` leaves out a bare name only on a real ending, as Signal Android and
    Desktop read one (`web_endings.ENDINGS`), so its removal keeps a title's meaning ("Call Mr.Smith back" was "Call
    back"); no Signal app links these. The structural net in a note (`clean_note`, tested below) does break their
    glue, the price of a guarantee that does not read links as a phone does."""
    assert unlinked(text) == text
    assert not android_links(text)


@pytest.mark.parametrize(
    ("text", "note"),
    [
        ("Call Mr.Smith back", "Call Mr Smith back"),  # the net breaks a dot between two letters
        ("Jan.Novak 1:1", "Jan Novak 1:1"),  # a time (digit:digit) stays
        ("Read notes.txt and data.csv", "Read notes txt and data csv"),
        ("node.js meetup", "node js meetup"),
        ("Φέρε χαρτιά, στυλό κ.λπ.", "Φέρε χαρτιά, στυλό κ λπ."),
        ("Awww...so cute", "Awww...so cute"),  # a run of dots is not between two letters: it stays
        ("Lunch@12.30 with Alex", "Lunch 12.30 with Alex"),  # @ breaks; the price's dot stays
        ("Dentist e.g. Room 4.2/B; 3.5 km", "Dentist e.g. Room 4.2 B; 3.5 km"),  # abbreviation and price kept
        ("Hotel:Ritz, Claim:x", "Hotel Ritz, Claim x"),  # a colon between two letters breaks
        ("cost a@b each", "cost a b each"),  # the net breaks @ (a local part, then a domain), not only unlinked
    ],
)
def test_the_net_breaks_a_notes_glue_a_phone_would_join(text: str, note: str) -> None:
    """The structural net (owner, 21 Oct): a note's quoted outside text loses every dot, colon, @, slash, % or
    underscore that joins a link's parts between two letters or digits, the guarantee past what `unlinked` recognises.
    Kept for reading: a dot or colon between two digits (a price, a time, a version), a one-letter abbreviation, a run
    of dots, and punctuation at a word's end. The cost: a name on no real ending ("node.js") loses its dot too."""
    assert clean_note(text, 80) == note
    assert not android_links(styled(clean_note(text, 80)).text)


@pytest.mark.parametrize(
    ("text", "piece", "left"),
    [
        ("ASP.NET talk", "ASP", "talk"),  # .net is a real ending: every app links it
        ("file setup.sh", "setup", "file"),  # and .sh (Saint Helena)
        ("Zebranie m.in. budżet", "m.in", "Zebranie. budżet"),  # and .in
        ("Pay evil.notatld/login now", "evil", "Pay now"),  # any ending before a path, a query or a port
        ("Pay evil.notatld:8443 now", "evil", "Pay now"),
        ("Pay www.evil.notatld now", "evil", "Pay now"),  # www. whatever the ending, as Android and iOS link it
        ("Mail bob@evil.example now", "evil", "Mail now"),  # and a mail address, as Android does
        ("Pay tel:x and news:x now", "x", "Pay and now"),  # iOS's schemes without "//", in lower case
    ],
)
def test_a_name_on_a_real_ending_or_before_a_path_goes(text: str, piece: str, left: str) -> None:
    assert unlinked(text) == left
    assert_left_out(left, piece, left)


def test_the_tidying_never_glues_two_words_into_a_link() -> None:
    """The second re-review (21 Oct): a link left out between two pieces leaves a space between them, so a round of
    `unlinked` never builds a link only the next round would find; a full stop or a comma that ends a sentence
    still closes up."""
    assert unlinked("Pay https:(ab.cd)//3627733538/x now") == "Pay https: //3627733538/x now"
    assert unlinked("Pay ww(ab.cd)w.evil.notatld now") == "Pay ww w.evil.notatld now"
    assert unlinked("Visit evil.com. Then pay") == "Visit. Then pay"
    assert unlinked("Pay evil.com, then rest") == "Pay, then rest"
    for left in ("Pay https: //3627733538/x now", "Pay ww w.evil.notatld now"):
        assert_left_out(left, "ab.cd", left)


def test_after_its_rounds_unlinked_leaves_out_whole_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """A text that still holds a link after `_ROUNDS` rounds loses each word that holds one, whole: no word left can
    make a link with another, and the time stays linear."""
    monkeypatch.setattr(signal_text, "_ROUNDS", 0)
    text = unlinked("Pay (evil.com) and\nww(ab.cd)w.evil.notatld now")
    assert text == "Pay and\nnow"
    assert_left_out(text, "evil", text)


def nested(depth: int) -> str:
    """Pieces inside pieces, each a link only once the one inside it is left out: "a1(a0(x.com).com).com"."""
    text = "x.com"
    for n in range(depth):
        text = f"a{n % 10}({text}).com"
    return text


def fastest(call: object) -> float:
    """The best of three runs of `call`, in seconds."""
    assert callable(call)
    times = []
    for _ in range(3):
        started = time.perf_counter()
        call()
        times.append(time.perf_counter() - started)
    return min(times)


def test_unlinked_stays_fast_on_nested_pieces() -> None:
    """The second re-review (21 Oct): nested pieces took a round each, so the time grew with the square of the length
    (clean_note at 255: 63.5 ms on 4,000 characters; the audio echo, without a cap: 622 ms on 3,205). The tidying
    never glues two words now, so a few rounds are enough."""
    title = nested(126) + " " + "y" * 2990
    echo = nested(500)
    assert len(title) >= 4000 and len(echo) >= 4000
    assert fastest(lambda: clean_note(title, 255)) < 0.05
    assert fastest(lambda: unlinked(echo)) < 0.05
    assert_left_out(unlinked(echo), "x.com")


def test_the_nets_dot_and_colon_sets_are_built_from_the_unicode_data() -> None:
    """The net's dot-like set is every Po character whose name holds FULL STOP, DOT or POINT (owner, 21 Oct: build it
    from the Unicode data, and test it), so a new dot look-alike is not missed. The listed colon-like characters are a
    subset of the colon glyphs; every one of them is kept. NFKC folds the forms it can before the sets are read."""
    derived = {
        chr(cp)
        for cp in range(0x110000)
        if unicodedata.category(chr(cp)) == "Po"
        and (name := _name(chr(cp)))
        and any(word in name for word in ("FULL STOP", "DOT", "POINT"))
    }
    assert derived == signal_text._DOT_LIKE
    assert "。" in signal_text._DOT_LIKE and "｡" in signal_text._DOT_LIKE  # 。 and ｡
    for colon in signal_text._COLON_LIKE:
        assert colon == ":" or "COLON" in _name(colon) or _name(colon) == "RATIO"


def _name(char: str) -> str:
    try:
        return unicodedata.name(char)
    except ValueError:
        return ""


@pytest.mark.parametrize(
    ("text", "piece"),
    [(f"Verify your account at {link} today", "evil") for link in PLANTED]
    + [(text, piece) for text, piece, _left in CASES[:12]],
)
def test_a_planted_link_leaves_a_note_with_no_tappable_link(text: str, piece: str) -> None:
    """Every planted link, through `clean_note` and the uncapped echo path (`netted` after `unlinked`), leaves a note
    Signal shows with no tappable link and no piece of the link's structure, at every cap and uncapped."""
    for note in (clean_note(text, 80), clean_note(text, 255), netted(unlinked(text))):
        assert_no_tappable_link(note)
        assert piece not in styled(note).text.casefold() or piece == "evil"  # a broken label word may stay


def test_the_net_stays_linear_on_16000_characters() -> None:
    """The net is one left-to-right pass (owner, 21 Oct: no rescans), so 16,000 characters of any shape stay well under
    50 ms, and the time grows only with the length."""
    shapes = [
        "evil.com " * 2000,
        "bob@evil.example " * 1000,
        "www.pay.example/login " * 750,
        "a.b.c.d.e.f.g " * 1300,
        "a_" * 8000,
        "maps:q=x " * 2000,
        "Team sync 4.99 e.g. v1.2.3 " * 600,
    ]
    for shape in shapes:
        text = shape[:16000]
        assert len(text) >= 15900
        assert fastest(lambda s=text: netted(s)) < 0.05
        assert netted(netted(text)) == netted(text)  # idempotent: the output always passes the net
