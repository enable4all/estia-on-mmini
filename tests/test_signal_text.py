"""The markup converter: plain text plus Signal's text styles, offsets in UTF-16 code units."""

import pytest

from local_assistant.signal_text import BODY_BYTES, Styled, spoken, styled, unmarked, valid


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
