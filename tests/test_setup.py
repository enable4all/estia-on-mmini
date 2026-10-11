import json
import re
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

from local_assistant import doctor
from local_assistant.config import parse_config
from local_assistant.setup import (
    EXTRAS,
    Sender,
    Wizard,
    choose_model,
    main,
    modelfile,
    one_time_word,
    owner_by_word,
    parse_extras,
    render_config,
    senders,
)

EXAMPLE = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text(encoding="utf-8")
BOT = "+306900000000"
ACI = "11111111-2222-4333-8444-555555555555"


@pytest.mark.parametrize(
    ("memory_gb", "tag", "context"),
    [
        (16, "gemma4:12b", 16384),  # Mac mini M4, 16 GB
        (24, "gemma4:12b", 32768),  # 24 GB: the 26b would not fit next to macOS and the voice models
        (32, "gemma4:26b", 32768),  # the tested setup
        (48, "gemma4:26b", 65536),
        (64, "gemma4:26b", 65536),
    ],
)
def test_the_model_fits_the_macs_memory(memory_gb: float, tag: str, context: int) -> None:
    choice = choose_model(memory_gb)
    assert choice is not None and (choice.tag, choice.num_ctx) == (tag, context)


def test_a_mac_with_8_gb_is_too_small() -> None:
    assert choose_model(8) is None


def test_the_modelfile_gives_the_alias_its_context() -> None:
    choice = choose_model(32)
    assert choice is not None
    assert modelfile(choice) == "FROM gemma4:26b\nPARAMETER num_ctx 32768\n"


def envelope(source: str, name: str, text: str | None, *, uuid: str = ACI) -> str:
    data = {"timestamp": 1, "message": text} if text is not None else None
    env: dict[str, object] = {"sourceNumber": source, "sourceUuid": uuid, "sourceName": name, "timestamp": 1}
    if data is not None:
        env["dataMessage"] = data
    return json.dumps({"envelope": env, "account": BOT})


def test_who_sent_hello_is_found_in_what_signal_cli_received() -> None:
    lines = [
        envelope("+306900000001", "Alex", "hello"),
        envelope("+306900000001", "Alex", None),  # a receipt or typing notice: no message
        "not json",
        envelope("+306900000002", "Sam", "hi there", uuid="66666666-7777-4888-9999-000000000000"),
        envelope("+306900000001", "Alex", "hello again"),  # the same person once
    ]
    found = senders(lines)
    assert [(s.name, s.aci, s.text) for s in found] == [
        ("Alex", ACI, "hello"),
        ("Sam", "66666666-7777-4888-9999-000000000000", "hi there"),
    ]


def test_the_config_is_the_example_with_the_bot_and_the_owner_filled_in() -> None:
    text = render_config(EXAMPLE, BOT, ACI)
    cfg = parse_config(tomllib.loads(text))
    assert cfg.bot_number == BOT and cfg.allowed_acis == frozenset({ACI})
    assert 'bot_number = "+300000000000"' not in text and 'allowed_acis = ["00000000-' not in text


@pytest.mark.parametrize("number", ["0612345678", "+31 6 1234", ""])
def test_a_bad_number_is_refused(number: str) -> None:
    with pytest.raises(ValueError, match="number"):
        render_config(EXAMPLE, number, ACI)


def test_extras_are_picked_by_name_or_number_and_unknown_ones_are_named() -> None:
    names = list(EXTRAS)
    assert parse_extras("") == ([], [])
    assert parse_extras("voice, calendar") == (["voice", "calendar"], [])
    assert parse_extras("1 3") == ([names[0], names[2]], [])
    assert parse_extras("voice teleport") == (["voice"], ["teleport"])
    assert parse_extras("all") == (names, [])


def test_the_owner_is_the_one_who_sent_the_one_time_word() -> None:
    """A profile name can be copied by anyone; the one-time word only the owner was shown cannot."""
    found = [
        Sender("Alex", "66666666-7777-4888-9999-000000000000", "hello"),  # someone named Alex, but not the word
        Sender("Alex", ACI, "estia-4821"),
    ]
    assert owner_by_word(found, "estia-4821") == ACI
    assert owner_by_word(found, "estia-9999") is None


def test_the_one_time_word_is_her_name_and_a_number() -> None:
    assert one_time_word("Estia", 4821) == "estia-4821"
    assert one_time_word("Tiffany", 1000) == "tiffany-1000"
    assert one_time_word("Mary-Jane Zoë", 9999) == "mary-jane-zoë-9999"


def refuse(*args: object, **kwargs: object) -> object:
    raise AssertionError("a dry run must not ask or run anything")


def ollama_lists(monkeypatch: pytest.MonkeyPatch, *names: str) -> list[str]:
    """Ollama's model list as the read-only GET (doctor's) returns it; the URLs asked are recorded."""
    asked: list[str] = []

    def get_json(self: doctor.Probe, url: str) -> object:
        asked.append(url)
        return {"models": [{"name": name} for name in names]}

    monkeypatch.setattr(doctor.Probe, "get_json", get_json)
    return asked


def test_a_dry_run_asks_nothing_runs_nothing_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """setup --dry-run describes every step (model, Signal, account, config, always on, extras) without prompts,
    commands or a missing Ollama, and says what it would write instead of a ✓."""
    for target in ("builtins.input", "getpass.getpass", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    monkeypatch.setattr("shutil.which", lambda name: None)  # a fresh Mac: no ollama, no signal-cli yet
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ollama_lists(monkeypatch)  # Ollama has no model yet
    config = tmp_path / "cfg" / "config.toml"
    assert main(["--dry-run", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "ollama pull" in out and "register" in out and "verify" in out and "one-time word" in out
    assert "Extras" in out and "(asks which ones" in out
    assert "or type any name" in out and "Enter keeps Estia" in out  # the name question, shown, not asked
    assert f"(dry run) would write config {config}" in out and "✓ Config" not in out
    assert not (tmp_path / "cfg").exists() and not (tmp_path / "home").exists()


@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry"])
def test_the_model_step_skips_an_existing_local_worker_in_a_dry_run_too(
    dry: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The preview reads the same model list (read-only) as a real run, so it shows the same skip."""
    for target in ("builtins.input", "getpass.getpass", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    asked = ollama_lists(monkeypatch, "gemma4:26b", "local-worker:latest")
    Wizard(tmp_path / "config.toml", dry_run=dry).model()
    out = capsys.readouterr().out
    assert out == "✓ Model: local-worker is there.\n"
    assert asked == ["http://127.0.0.1:11434/api/tags"]


GOOD = f'bot_number = "+306900000000"\nallowed_acis = ["{ACI}"]\n'
HAND_WRITTEN = '\n[mail]\ntenant_id = "example-tenant-kept-by-hand"\n'


@pytest.mark.parametrize(
    ("text", "mode", "error"),
    [
        (GOOD + 'timezone = "Europe/Amsterdm"\n' + HAND_WRITTEN, 0o600, "unknown timezone"),  # one typo
        (GOOD + HAND_WRITTEN, 0o644, "must be private"),  # readable by others
    ],
    ids=["typo", "readable"],
)
@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry"])
def test_setup_stops_before_anything_when_the_config_is_there_but_does_not_load(
    text: str,
    mode: int,
    error: str,
    dry: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A hand-edited config with one mistake is never replaced by the example: setup names the error and stops."""
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    steps: list[str] = []

    def model(self: Wizard) -> None:
        steps.append("model")

    def signal_number(self: Wizard) -> str:
        steps.append("signal number")
        return BOT

    def owner(self: Wizard, number: str) -> str:
        steps.append("owner")
        return ACI

    def later(self: Wizard) -> None:
        steps.append("later step")

    def check(self: Wizard) -> int:
        steps.append("check")
        return 0

    for name, step in [("model", model), ("signal_number", signal_number), ("owner", owner), ("check", check)]:
        monkeypatch.setattr(Wizard, name, step)
    monkeypatch.setattr(Wizard, "always_on", later)
    monkeypatch.setattr(Wizard, "extras", later)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert main(["--config", str(path), *(["--dry-run"] if dry else [])]) == 1
    assert steps == []  # not the model, not Signal, nothing written
    assert path.read_text(encoding="utf-8") == text and stat.S_IMODE(path.stat().st_mode) == mode
    assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]
    out = capsys.readouterr().out
    assert error in out and "scripts/doctor.sh" in out


def test_replacing_a_config_keeps_a_private_copy_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The second guard: a config that loads but still has the example's placeholders is copied before it is
    replaced (a dry run only says it would)."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # the language question: Enter keeps English
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE, encoding="utf-8")
    path.chmod(0o600)
    Wizard(path, dry_run=True).config(BOT, ACI)
    assert "would keep a copy" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml"]
    Wizard(path, dry_run=False).config(BOT, ACI)
    copies = list(tmp_path.glob("config.toml.bak-*"))
    assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == EXAMPLE
    assert stat.S_IMODE(copies[0].stat().st_mode) == 0o600
    assert f'bot_number = "{BOT}"' in path.read_text(encoding="utf-8")
    assert str(copies[0]) in capsys.readouterr().out


def owner_step(
    monkeypatch: pytest.MonkeyPatch, answers: list[str], arrivals: list[bool]
) -> tuple[Wizard, list[str], list[list[str]]]:
    """The owner step with a fake person and a fake signal-cli: the person types `answers` in order (then no terminal:
    EOF), and receive number i brings the shown one-time word when arrivals[i], else nothing."""
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    said: list[str] = []
    commands: list[list[str]] = []
    typed = iter(answers)

    def type_answer(prompt: str) -> str:
        said.append(prompt)
        try:
            return next(typed)
        except StopIteration:
            raise EOFError from None

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        shown = re.search(r"estia-\d{4}", said[0])
        assert shown is not None
        lines = [envelope("+306900000001", "Alex", shown.group())] if arrivals[len(commands) - 1] else []
        return subprocess.CompletedProcess(command, 0, "\n".join(lines), "")

    monkeypatch.setattr("builtins.input", type_answer)
    monkeypatch.setattr(wizard, "say", said.append)
    monkeypatch.setattr(wizard, "run", run)
    return wizard, said, commands


def test_the_owner_step_looks_again_until_the_word_arrives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing arrived: the likely causes (most common first, with her number), then Enter looks again."""
    wizard, said, commands = owner_step(monkeypatch, ["", ""], [False, True])
    assert wizard.owner(BOT) == ACI
    assert len(commands) == 2 and all("receive" in command for command in commands)
    text = "\n".join(said)
    assert "wrong number" in text and f"send it to {BOT}" in text and "Note to Self" in text
    assert text.index("wrong number") < text.index("before") < text.index("ran out")
    assert "Press Enter to look again, or type q to stop." in text
    assert len(set(re.findall(r"estia-\d{4}", text))) == 1  # the same word on every try
    assert f"send it to {BOT} (Estia's new number)" in text
    assert "Only your account can talk to Estia" in said[-1]


@pytest.mark.parametrize("answers", [["", "q"], [""]], ids=["q", "no terminal"])
def test_q_or_no_terminal_stops_the_owner_step(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    wizard, _said, commands = owner_step(monkeypatch, answers, [False, True])
    with pytest.raises(SystemExit, match="Run setup again"):
        wizard.owner(BOT)
    assert len(commands) == 1


def signal_step(
    monkeypatch: pytest.MonkeyPatch, hidden: list[str], verified: list[bool]
) -> tuple[Wizard, list[str], list[list[str]]]:
    """The Signal number step: the person types her number, then `hidden` at the hidden prompts (code, PIN, ...);
    signal-cli registers without a captcha and verify number i works when verified[i]."""
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    said: list[str] = []
    commands: list[list[str]] = []
    typed = iter(hidden)

    def type_hidden(prompt: str = "") -> str:
        try:
            return next(typed)
        except StopIteration:
            raise EOFError from None

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        ok = "register" in command or verified[sum("verify" in c for c in commands) - 1]
        return subprocess.CompletedProcess(command, 0 if ok else 1, "", "" if ok else "Verify error: wrong code")

    monkeypatch.setattr("builtins.input", lambda prompt="": BOT)
    monkeypatch.setattr("getpass.getpass", type_hidden)
    monkeypatch.setattr(wizard, "registered_numbers", list)
    monkeypatch.setattr(wizard, "say", said.append)
    monkeypatch.setattr(wizard, "run", run)
    return wizard, said, commands


def test_a_wrong_sms_code_can_be_typed_again(monkeypatch: pytest.MonkeyPatch) -> None:
    wizard, said, commands = signal_step(monkeypatch, ["111-111", "", "222-222", ""], [False, True])
    assert wizard.signal_number() == BOT
    assert [command[-1] for command in commands if "verify" in command] == ["111111", "222222"]
    assert any("type q to stop" in line for line in said)


@pytest.mark.parametrize("hidden", [["111111", "", "q"], ["111111", ""]], ids=["q", "no terminal"])
def test_q_or_no_terminal_stops_the_sms_code_step(monkeypatch: pytest.MonkeyPatch, hidden: list[str]) -> None:
    wizard, _said, commands = signal_step(monkeypatch, hidden, [False, True])
    with pytest.raises(SystemExit, match="run setup again"):
        wizard.signal_number()
    assert sum("verify" in command for command in commands) == 1


@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry"])
def test_the_config_step_leaves_soul_md_to_the_user(
    dry: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No soul.md is written: her built-in playful style is the default until the user writes their own."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # the language question: Enter keeps English
    path = tmp_path / "config.toml"
    Wizard(path, dry_run=dry).config(BOT, ACI)
    assert not (tmp_path / "soul.md").exists()
    out = capsys.readouterr().out
    assert "Personality: Estia's built-in style (warm, curious, playful)." in out
    assert f"cp soul.example.md {tmp_path / 'soul.md'} && chmod 600 {tmp_path / 'soul.md'}" in out


def test_setup_ends_with_what_done_means(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check in the terminal is not the end: done is her welcome on the phone, and a reply to it."""
    from local_assistant import setup

    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(Wizard, "check", lambda self: 0)  # doctor's real probes are tested in test_doctor
    monkeypatch.setattr(Wizard, "always_on", lambda self: None)  # it looks at /Library/LaunchDaemons
    assert setup.main(["--dry-run", "--config", str(tmp_path / "config.toml")]) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert "welcome" in last and "no ✗" in last and "Reply to it" in last


def test_setup_says_the_configured_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A config that names her (the owner's Tiffany): the banner and the last line use that name; none: Estia."""
    from local_assistant import setup

    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "check", lambda self: 0)
    monkeypatch.setattr(Wizard, "always_on", lambda self: None)
    monkeypatch.setattr(Wizard, "extras", lambda self: None)
    path = tmp_path / "config.toml"
    path.write_text(GOOD + 'name = "Tiffany"\n', encoding="utf-8")
    path.chmod(0o600)
    assert setup.main(["--dry-run", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Tiffany setup: the model, her Signal number") and "Tiffany's welcome" in out
    assert "Estia" not in out
    path.unlink()
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("builtins.input", refuse)
    assert setup.main(["--dry-run", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Estia setup:") and "Estia's number" in out and "like estia-4821" in out
    assert "Language: Estia listens and speaks" in out and "Tiffany setup" not in out and "Tiffany's" not in out


def test_the_captcha_code_and_pin_are_never_shown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Typed hidden, and not printed afterwards either: the commands are shown with placeholders, on every try (the
    terminal may be an AI's transcript, SETUP-WITH-YOUR-AI.md)."""
    captcha = "signalcaptcha://signal-hcaptcha.00000000-0000-4000-8000-000000000000.registration.fake-token-777"
    secrets = [captcha, "111-111", "9876", "222-222", "9876"]
    hidden = iter(secrets)
    commands: list[list[str]] = []

    def signal_cli(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[-1] == "register":
            return subprocess.CompletedProcess(command, 1, "", "Captcha required for verification")
        ok = "register" in command or "222222" in command
        return subprocess.CompletedProcess(command, 0 if ok else 1, "", "" if ok else "Verify error: wrong code")

    monkeypatch.setattr("shutil.which", lambda name: f"/opt/homebrew/bin/{name}")
    monkeypatch.setattr("builtins.input", lambda prompt="": BOT)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(hidden))
    monkeypatch.setattr("subprocess.run", signal_cli)
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    monkeypatch.setattr(wizard, "registered_numbers", list)
    assert wizard.signal_number() == BOT
    assert [c[c.index("-a") + 2 :] for c in commands] == [
        ["register"],
        ["register", "--captcha", captcha],
        ["verify", "111111", "--pin", "9876"],
        ["verify", "222222", "--pin", "9876"],
    ]
    out = capsys.readouterr().out
    for secret in (*secrets, "111111", "222222", "fake-token-777"):
        assert secret not in out
    assert out.count("verify <SMS code> --pin <PIN>") == 2 and "register --captcha <captcha link>" in out


def test_the_config_gets_the_chosen_language() -> None:
    assert parse_config(tomllib.loads(render_config(EXAMPLE, BOT, ACI))).language == "en"
    text = render_config(EXAMPLE, BOT, ACI, language="nl")
    assert parse_config(tomllib.loads(text)).language == "nl" and 'language = "en"' not in text
    with pytest.raises(ValueError, match="language"):
        render_config(EXAMPLE.replace('language = "en"', ""), BOT, ACI, language="nl")


@pytest.mark.parametrize(("answers", "language"), [([""], "en"), (["dutch"], "nl"), (["klingon", "ja"], "ja")])
def test_the_wizard_asks_for_the_language_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answers: list[str],
    language: str,
) -> None:
    from local_assistant.setup import Wizard

    given = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(given))
    path = tmp_path / "config" / "config.toml"
    Wizard(path, dry_run=False).config(BOT, ACI)
    assert parse_config(tomllib.loads(path.read_text(encoding="utf-8"))).language == language
    out = capsys.readouterr().out
    assert "Nederlands, Dutch (nl)" in out and "日本語, Japanese (ja)" in out and "/language" in out


def named_wizard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[str], *, dry: bool = False
) -> tuple[Wizard, list[str]]:
    """The name step with a person who types `answers` in order (then no terminal); the prompts asked are recorded."""
    typed = iter(answers)
    asked: list[str] = []

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        try:
            return next(typed)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", type_answer)
    return Wizard(tmp_path / "config.toml", dry_run=dry), asked


def voice_on(text: str) -> str:
    """The rendered config with its [voice] block turned on (the # taken off [voice] and its voice line)."""
    return re.sub(r"^#(\[voice\]|voice = )", r"\1", text, flags=re.MULTILINE)


def test_enter_keeps_estia_with_a_female_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One question with the suggestions; Enter keeps Estia (and af_heart), written into the config."""
    wizard, asked = named_wizard(tmp_path, monkeypatch, ["", ""])  # the name, then the language: Enter for both
    wizard.her_name()
    assert (wizard.name, wizard.voice) == ("Estia", "af_heart")
    wizard.config(BOT, ACI)
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert parse_config(tomllib.loads(text)).name == "Estia"
    assert parse_config(tomllib.loads(voice_on(text))).voice_name == "af_heart"
    out = capsys.readouterr().out
    assert "1. Estia (default)" in out and "female: Tiffany · Maya · Melina" in out
    assert "male: Socrates · Perikles · Plato" in out and "or type any name" in out
    assert "Female or male" not in " ".join(asked) and len(asked) == 2


@pytest.mark.parametrize(
    ("answer", "name", "voice"),
    [("Socrates", "Socrates", "am_michael"), ("plato", "Plato", "am_michael"), ("Melina", "Melina", "af_heart")],
)
def test_a_suggested_name_brings_its_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, name: str, voice: str
) -> None:
    wizard, asked = named_wizard(tmp_path, monkeypatch, [answer, ""])
    wizard.her_name()
    assert (wizard.name, wizard.voice) == (name, voice)
    wizard.config(BOT, ACI)
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert parse_config(tomllib.loads(text)).name == name
    assert parse_config(tomllib.loads(voice_on(text))).voice_name == voice
    assert not any("Female or male" in prompt for prompt in asked)  # a suggestion needs no voice question


@pytest.mark.parametrize(("choice", "voice"), [("m", "am_michael"), ("male", "am_michael"), ("", "af_heart")])
def test_a_typed_name_asks_for_the_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: str, voice: str
) -> None:
    wizard, asked = named_wizard(tmp_path, monkeypatch, ["Mary-Jane Zoë", choice])
    wizard.her_name()
    assert (wizard.name, wizard.voice) == ("Mary-Jane Zoë", voice)
    assert asked[-1].startswith("Female or male voice? (Enter: female)")


def test_a_bad_name_is_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The config's name rule: 1-20 letters, spaces or hyphens. A name that breaks it is named and asked again."""
    wizard, asked = named_wizard(tmp_path, monkeypatch, ["R2-D2", "x" * 21, "Juno", "f"])
    wizard.her_name()
    assert (wizard.name, wizard.voice) == ("Juno", "af_heart")
    out = capsys.readouterr().out
    assert '"R2-D2" does not fit' in out and "1-20 letters, spaces or hyphens" in out
    assert sum("Enter keeps Estia" in prompt for prompt in asked) == 3


def test_a_dry_run_shows_the_name_question_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", refuse)
    wizard = Wizard(tmp_path / "config.toml", dry_run=True)
    wizard.her_name()
    assert (wizard.name, wizard.voice) == ("Estia", "af_heart")
    out = capsys.readouterr().out
    assert "1. Estia (default)" in out and "or type any name" in out and "Enter keeps Estia" in out


def test_the_config_gets_the_chosen_name_and_voice() -> None:
    text = render_config(EXAMPLE, BOT, ACI, name="Perikles", voice="am_michael")
    assert parse_config(tomllib.loads(text)).name == "Perikles" and 'name = "Estia"' not in text
    assert parse_config(tomllib.loads(voice_on(text))).voice_name == "am_michael"
    assert parse_config(tomllib.loads(text)).voice_url == ""  # voice stays off until you turn it on
    with pytest.raises(ValueError, match="name"):
        render_config(EXAMPLE, BOT, ACI, name='Bad"name')
