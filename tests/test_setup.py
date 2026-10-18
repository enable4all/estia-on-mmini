import io
import json
import os
import plistlib
import re
import select
import shlex
import stat
import subprocess
import sys
import termios
import threading
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from local_assistant import doctor, prompts
from local_assistant import setup as wizard_module
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
    read_hidden,
    render_config,
    senders,
)

EXAMPLE = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text(encoding="utf-8")
BOT = "+306900000000"
ACI = "11111111-2222-4333-8444-555555555555"


@pytest.mark.parametrize(
    ("memory_gb", "tag", "context"),
    [
        (16, "gemma4:12b-mlx", 16384),  # Mac mini M4, 16 GB
        (24, "gemma4:12b-mlx", 32768),  # 24 GB: the 26b would not fit next to macOS and the voice models
        (32, "gemma4:26b-mlx", 32768),  # the tested setup
        (48, "gemma4:26b-mlx", 65536),
        (64, "gemma4:26b-mlx", 65536),
    ],
)
def test_the_model_fits_the_macs_memory(memory_gb: float, tag: str, context: int) -> None:
    choice = choose_model(memory_gb)
    assert choice is not None and (choice.tag, choice.num_ctx) == (tag, context)


def test_the_models_are_the_mlx_builds_pinned_by_digest_with_the_ollama_they_need() -> None:
    """Owner, 15 Oct: the explicit -mlx tags, never the bare ones (lists whose pick depends on Ollama's version); each
    needs Ollama 0.33.1 or later (JSON-schema output on the MLX engine). The 12b's line claims no tests until the
    owner's tests on this Mac pass."""
    big, small = choose_model(32), choose_model(16)
    assert big is not None and small is not None
    assert (big.tag, big.digest[:12], small.tag, small.digest[:12]) == (
        "gemma4:26b-mlx",
        "f0fc7e0ae494",
        "gemma4:12b-mlx",
        "ded7a2735003",
    )
    assert all(len(choice.digest) == 64 for choice in (big, small))
    assert big.ollama == small.ollama == "0.33.1"
    assert "the tested model" in big.note and "the tested model" not in small.note
    # owner, 15 Oct: 12B failed some safety tests twice; setup says so on a line of its own and asks first (model())
    assert big.tested and not small.tested and "failed" not in small.note


def test_a_mac_with_8_gb_is_too_small() -> None:
    assert choose_model(8) is None


def test_the_modelfile_gives_the_alias_its_context_and_googles_prompt_for_its_size() -> None:
    """Empty answers (17 Oct): Ollama tells Gemma 4's sizes apart by the model's name, and local-worker names none, so
    it got the prompt for the small models (E2B, E4B), without the empty thought block Google's prompt for 12B and
    larger ends with when thinking is off. Both of setup's models are 12B or larger: their alias says so."""
    for memory, tag, context in ((32, "gemma4:26b-mlx", 32768), (16, "gemma4:12b-mlx", 16384)):
        choice = choose_model(memory)
        assert choice is not None
        assert modelfile(choice) == f"FROM {tag}\nPARAMETER num_ctx {context}\nRENDERER gemma4-large\n"


@pytest.mark.parametrize(
    ("tag", "large"),
    [
        ("gemma4:26b-mlx", True),
        ("gemma4:12b-mlx", True),
        ("gemma4:31b", True),
        ("gemma4:e4b", False),  # the small models: Ollama's own pick stays
        ("gemma4:e2b-mlx", False),
        ("qwen3:32b", False),  # not Gemma 4: no line
    ],
)
def test_only_gemma_4_tags_of_12b_or_more_get_the_large_prompt(tag: str, large: bool) -> None:
    from local_assistant.setup import ModelChoice

    assert ("RENDERER gemma4-large" in modelfile(ModelChoice(tag, "0" * 64, 8192, ""))) is large


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


@pytest.fixture(autouse=True)
def no_real_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never the Mac's own Ollama: no answer unless a test gives one."""

    def get_json(self: doctor.Probe, url: str) -> object:
        raise OSError("no Ollama in tests")

    def post_json(self: doctor.Probe, url: str, payload: object) -> object:
        raise OSError("no Ollama in tests")

    monkeypatch.setattr(doctor.Probe, "get_json", get_json)
    monkeypatch.setattr(doctor.Probe, "post_json", post_json)


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
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
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
    assert prompts.SETUP_STYLE_DRY_RUN in out and "hearth (Enter): steady, kind and frank." in out
    assert f"(dry run) would write config {config}" in out and "✓ Config" not in out
    assert not (tmp_path / "cfg").exists() and not (tmp_path / "home").exists()


@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry"])
def test_the_model_step_skips_an_existing_local_worker_in_a_dry_run_too(
    dry: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The preview reads the same model list (read-only) as a real run, so it shows the same skip."""
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    asked = ollama_lists(monkeypatch, "gemma4:26b", "local-worker:latest")
    Wizard(tmp_path / "config.toml", dry_run=dry).model()
    out = capsys.readouterr().out
    assert out == "✓ Model: local-worker is there.\n"
    assert asked == ["http://127.0.0.1:11434/api/tags"]


# Setup's two tags as Ollama 0.40.4 lists and shows them (17 Oct, read-only): (architecture, parameter count, size
# shown, bytes in /api/tags). An MLX build's manifest has no model layer, so Ollama names no parent for it, nor for an
# alias made from it, and /api/tags gives no parameter size.
TAGS = {
    "gemma4:26b-mlx": ("gemma4", 26_225_562_962, "26.2B", 18_334_412_658),
    "gemma4:12b-mlx": ("gemma4_unified", 12_382_568_756, "12.4B", 7_714_091_504),
}
GEMMA_PARAMETERS = [
    "temperature                    1",
    "top_k                          64",
    "top_p                          0.95",
]


def show_of(
    tag: str, *, name: str = "", renderer: str = "gemma4", parameters: list[str] | None = None
) -> dict[str, Any]:
    """Ollama's /api/show answer for one of setup's tags, or for an alias made from it (`name`), as it comes."""
    architecture, count, size, _bytes = TAGS[tag]
    modelfile = (
        f'# Modelfile generated by "ollama show"\nFROM {name or tag}\nTEMPLATE {{{{ .Prompt }}}}\nRENDERER {renderer}\n'
        "PARSER gemma4\nCAPABILITY completion\n"
    )
    return {
        "modelfile": modelfile,
        "parameters": "\n".join(GEMMA_PARAMETERS if parameters is None else parameters),
        "template": "{{ .Prompt }}",
        "details": {
            "parent_model": "",
            "format": "safetensors",
            "family": architecture,
            "families": None,
            "parameter_size": size,
            "quantization_level": "nvfp4",
        },
        "model_info": {"general.architecture": architecture, "general.parameter_count": count},
        "requires": "0.31.0",
    }


class OldAlias:
    """Ollama with local-worker made before 0.9.3 (read-only answers as doctor's Probe gets them): its entry in
    /api/tags, its Modelfile in /api/show, and after `ollama create` its new digest. By default the owner's: a copy
    of the 26b (`ollama cp`, the tag's digest). With `num_ctx`: one setup made, FROM `base` and PARAMETER num_ctx,
    which has its own digest and a few bytes more; `changes` then alters what its /api/show answer holds."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        base: str = "gemma4:26b-mlx",
        renderer: str = "gemma4",
        num_ctx: int | None = None,
        digest: str = "",
        extra_bytes: int = 16,  # "num_ctx":32768, in its parameters
        changes: dict[str, Any] | None = None,
        listed: dict[str, str] | None = None,
    ) -> None:
        from local_assistant.setup import GEMMA_12B, GEMMA_12B_DIGEST, GEMMA_26B, GEMMA_26B_DIGEST

        tested = {GEMMA_26B: GEMMA_26B_DIGEST, GEMMA_12B: GEMMA_12B_DIGEST}
        copy = num_ctx is None and not digest
        self.digest = tested[base] if copy else digest or "ef" * 32
        self.bytes = TAGS[base][3] + (0 if copy else extra_bytes)
        parameters = [*GEMMA_PARAMETERS[::-1], *([f"num_ctx                        {num_ctx}"] if num_ctx else [])]
        self.alias = show_of(base, name="local-worker:latest", renderer=renderer, parameters=parameters)
        for key, value in (changes or {}).items():  # "details.quantization_level": a key inside a part
            part, _, inner = key.partition(".")
            if inner and part in ("details", "model_info"):
                self.alias[part] = {**self.alias[part], inner: value}
            else:
                self.alias[key] = value
        self.tags = listed if listed is not None else tested
        self.shown: list[object] = []
        monkeypatch.setattr(doctor.Probe, "get_json", lambda probe, url: self.list())
        monkeypatch.setattr(doctor.Probe, "post_json", lambda probe, url, payload: self.show(url, payload))
        monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 32.0)

    def list(self) -> object:
        details = {"parent_model": "", "format": "safetensors", "parameter_size": "", "quantization_level": "nvfp4"}
        alias = {"name": "local-worker:latest", "digest": self.digest, "size": self.bytes, "details": details}
        tags = (
            {"name": name, "digest": digest, "size": TAGS[name][3], "details": details}
            for name, digest in self.tags.items()
        )
        return {"models": [alias, *tags]}

    def show(self, url: str, payload: object) -> object:
        self.shown.append((url, payload))
        name = payload.get("model") if isinstance(payload, dict) else None
        if name in ("local-worker", "local-worker:latest"):
            return self.alias
        if isinstance(name, str) and name in self.tags:
            return show_of(name)
        raise OSError("model not found")


def remake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, *, dry: bool = False, made: bool = True
) -> tuple[Wizard, list[list[str]], list[str], list[str]]:
    """The model step on a re-run, the person answering `answer`: what ran, what was asked, and each Modelfile."""
    ran: list[list[str]] = []
    asked: list[str] = []
    specs: list[str] = []

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = [str(arg) for arg in args[-1]] if isinstance(args[-1], list) else []
        ran.append(command)
        if "-f" in command:
            specs.append(Path(command[command.index("-f") + 1]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(command, 0 if made else 1, "", "")

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        return answer

    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    wizard = Wizard(tmp_path / "config.toml", dry_run=dry)
    wizard.ollama_program = "/opt/example/ollama"
    if not dry:
        monkeypatch.setattr(wizard, "run", run)
    monkeypatch.setattr("builtins.input", refuse if dry else type_answer)
    return wizard, ran, asked, specs


def test_a_rerun_makes_an_alias_with_the_small_models_prompt_again_on_a_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty answers (17 Oct): an alias from before 0.9.3 has the small models' prompt. On a re-run setup reads its
    Modelfile (read-only), says so, and on a yes makes it again from the same model with only the prompt changed: its
    context stays as it was (none here: the Ollama service's). The new digest is printed with the pin's line."""
    ollama = OldAlias(monkeypatch)
    wizard, ran, asked, specs = remake(tmp_path, monkeypatch, "y")
    original = wizard.run

    def create_then_new_digest(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        done = original(command)
        ollama.digest = "cd" * 32
        return done

    monkeypatch.setattr(wizard, "run", create_then_new_digest)
    wizard.model()
    out = capsys.readouterr().out
    assert asked == [f"{prompts.SETUP_MODEL_REMAKE_ASK} [y/N] "]
    assert ran == [["/opt/example/ollama", "create", "local-worker", "-f", str(tmp_path / ".local-worker.Modelfile")]]
    assert specs == ["FROM gemma4:26b-mlx\nRENDERER gemma4-large\n"]
    assert not (tmp_path / ".local-worker.Modelfile").exists()
    assert ollama.shown == [("http://127.0.0.1:11434/api/show", {"model": "local-worker"})]
    assert prompts.SETUP_MODEL_SMALL_PROMPT in out and "✓ Model: local-worker is there." not in out
    assert prompts.SETUP_MODEL_REMAKE.format(tag="gemma4:26b-mlx", stop=prompts.SETUP_MODEL_STOP_YOURS) in out
    assert prompts.SETUP_MODEL_REMADE in out and f"Its new digest: sha256:{'cd' * 32}." in out
    pin = prompts.SETUP_MODEL_REMADE_PIN.format(
        digest="cd" * 32, where=prompts.SETUP_MODEL_PIN_YOURS.format(path=tmp_path / "config.toml")
    )
    assert pin in out and f'model_digest = "sha256:{"cd" * 32}"' in pin


@pytest.mark.parametrize(
    ("base", "num_ctx"),
    [("gemma4:26b-mlx", 32768), ("gemma4:26b-mlx", 65536), ("gemma4:12b-mlx", 16384), ("gemma4:12b-mlx", 32768)],
)
def test_an_alias_setup_made_is_traced_by_what_it_holds_and_keeps_its_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: str, num_ctx: int
) -> None:
    """Review, 17 Oct (I1): setup before 0.9.3 made local-worker FROM its tag with PARAMETER num_ctx, so the alias has
    a digest of its own, and Ollama names no parent for an MLX build (parent_model ""). It is traced by what it holds:
    the tag's weights (architecture, parameter count, format, quantization, read from /api/show), its template and
    parameters but num_ctx, and a size within a few bytes of the tag's. Only the RENDERER line is new."""
    ollama = OldAlias(monkeypatch, base=base, num_ctx=num_ctx)
    wizard, ran, asked, specs = remake(tmp_path, monkeypatch, "y")
    wizard.model()
    assert asked == [f"{prompts.SETUP_MODEL_REMAKE_ASK} [y/N] "] and len(ran) == 1
    assert specs == [f"FROM {base}\nPARAMETER num_ctx {num_ctx}\nRENDERER gemma4-large\n"]
    assert ("http://127.0.0.1:11434/api/show", {"model": base}) in ollama.shown


@pytest.mark.parametrize(
    ("changes", "extra_bytes"),
    [
        ({"model_info.general.parameter_count": 25_805_933_000}, 16),  # another model of the same family
        ({"model_info.general.architecture": "gemma3"}, 16),
        ({"details.quantization_level": "bf16"}, 16),  # the same weights at another precision
        ({"details.format": "gguf"}, 16),
        ({"parameters": "temperature 0.7\ntop_k 64\ntop_p 0.95\nnum_ctx 32768"}, 16),  # a setting of its own: lost
        ({"system": "You are a pirate."}, 40),  # a system prompt of its own: lost
        ({"template": "{{ .System }} {{ .Prompt }}"}, 16),
        ({}, 52_428_800),  # 50 MB more: an adapter or a draft model, which the tag does not have
        ({"model_info": {}}, 16),  # weights that cannot be read
    ],
    ids=["count", "architecture", "quantization", "format", "parameter", "system", "template", "bytes", "unread"],
)
def test_an_alias_that_holds_more_or_other_than_the_tag_is_left_with_the_way_by_hand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changes: dict[str, Any],
    extra_bytes: int,
) -> None:
    """Never guessed: an alias with other weights, or with settings a remake from the tag would drop, is not made
    again by setup."""
    OldAlias(monkeypatch, num_ctx=32768, changes=changes, extra_bytes=extra_bytes)
    wizard, ran, asked, _specs = remake(tmp_path, monkeypatch, "y")
    wizard.model()
    out = capsys.readouterr().out
    assert ran == [] and asked == []
    assert prompts.SETUP_MODEL_SMALL_PROMPT in out and prompts.SETUP_MODEL_PROMPT_BY_HAND in out


def test_an_alias_whose_tag_does_not_answer_show_is_left_with_the_way_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ollama = OldAlias(monkeypatch, num_ctx=32768)
    alias = ollama.show

    def only_the_alias(url: str, payload: object) -> object:
        if isinstance(payload, dict) and payload.get("model") != "local-worker":
            raise OSError("connection refused")
        return alias(url, payload)

    monkeypatch.setattr(doctor.Probe, "post_json", lambda probe, url, payload: only_the_alias(url, payload))
    wizard, ran, asked, _specs = remake(tmp_path, monkeypatch, "y")
    wizard.model()
    assert ran == [] and asked == [] and prompts.SETUP_MODEL_PROMPT_BY_HAND in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["", "n"], ids=["enter", "no"])
def test_no_leaves_the_alias_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    OldAlias(monkeypatch)
    wizard, ran, asked, _specs = remake(tmp_path, monkeypatch, answer)
    wizard.model()
    assert ran == [] and len(asked) == 1 and prompts.SETUP_MODEL_REMAKE_LATER in capsys.readouterr().out


def test_a_dry_run_shows_the_question_and_the_command_and_makes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for target in ("local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    OldAlias(monkeypatch)
    wizard, _ran, _asked, _specs = remake(tmp_path, monkeypatch, "", dry=True)
    wizard.model()
    out = capsys.readouterr().out
    assert prompts.SETUP_MODEL_REMAKE_DRY_RUN.format(ask=prompts.SETUP_MODEL_REMAKE_ASK) in out
    assert "  $ /opt/example/ollama create local-worker -f" in out
    assert not (tmp_path / ".local-worker.Modelfile").exists()


@pytest.mark.parametrize(("num_ctx", "extra_bytes"), [(None, 0), (32768, 16)], ids=["copy", "own context"])
def test_an_alias_whose_tag_is_no_longer_the_tested_build_is_left_with_the_way_by_hand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    num_ctx: int | None,
    extra_bytes: int,
) -> None:
    """Its tag listed with another digest (Ollama's library changed it since): never made again from it."""
    OldAlias(
        monkeypatch, num_ctx=num_ctx, digest="ab" * 32, extra_bytes=extra_bytes, listed={"gemma4:26b-mlx": "ab" * 32}
    )
    wizard, ran, asked, _specs = remake(tmp_path, monkeypatch, "y")
    wizard.model()
    out = capsys.readouterr().out
    assert ran == [] and asked == []
    assert prompts.SETUP_MODEL_SMALL_PROMPT in out and prompts.SETUP_MODEL_PROMPT_BY_HAND in out


def test_an_alias_with_googles_prompt_is_simply_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    OldAlias(monkeypatch, renderer="gemma4-large")
    wizard, ran, _asked, _specs = remake(tmp_path, monkeypatch, "y")
    monkeypatch.setattr("builtins.input", refuse)
    wizard.model()
    assert capsys.readouterr().out == "✓ Model: local-worker is there.\n" and ran == []


def test_in_her_own_user_the_pin_line_says_how_to_edit_her_config(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    her_own_user(no_system_paths)
    OldAlias(monkeypatch)
    wizard, _ran, _asked, _specs = remake(tmp_path, monkeypatch, "y")
    wizard.model()
    out = capsys.readouterr().out
    assert prompts.SETUP_MODEL_REMAKE.format(tag="gemma4:26b-mlx", stop=prompts.SETUP_MODEL_STOP_HERS) in out
    assert prompts.SETUP_MODEL_PIN_HERS in out


def test_through_an_ollama_setup_refused_nothing_is_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    OldAlias(monkeypatch)
    wizard, ran, asked, _specs = remake(tmp_path, monkeypatch, "y")
    wizard.ollama_refused = True
    wizard.model()
    out = capsys.readouterr().out
    assert ran == [] and asked == [] and prompts.SETUP_MODEL_REMAKE_AFTER_OLLAMA in out


def test_a_failed_remake_leaves_the_alias_and_setup_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    OldAlias(monkeypatch)
    wizard, ran, _asked, _specs = remake(tmp_path, monkeypatch, "y", made=False)
    wizard.model()  # no SystemExit: the old alias still answers
    out = capsys.readouterr().out
    assert len(ran) == 1 and prompts.SETUP_MODEL_REMAKE_FAILED in out and "Its new digest" not in out
    assert not (tmp_path / ".local-worker.Modelfile").exists()


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


FAKE = Path(__file__).parent / "fakes" / "fake_signal_cli.py"


def fake_signal_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[str, Path]:
    """The fake signal-cli as a program of its own: a real process, so its arguments are what ps would show any user
    of the Mac. `env` sets its FAKE_SIGNAL_* answers (the captcha, the SMS code, the PIN)."""
    folder = tmp_path / "signal"
    folder.mkdir()
    monkeypatch.setenv("FAKE_SIGNAL_DIR", str(folder))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    program = tmp_path / "signal-cli"
    program.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE))} "$@"\n')
    program.chmod(0o755)
    return str(program), folder


def launches(folder: Path) -> list[list[str]]:
    """Every start's arguments."""
    path = folder / "launches.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def rpc_requests(folder: Path) -> list[dict[str, object]]:
    path = folder / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def signal_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hidden: list[str], **env: str
) -> tuple[Wizard, list[str], Path]:
    """The Signal number step against the fake signal-cli: the person types her number, then `hidden` at the hidden
    prompts (code, PIN, ...), and no to the Registration Lock; `env` sets the fake's answers."""
    program, folder = fake_signal_cli(tmp_path, monkeypatch, **env)
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli = program
    said: list[str] = []
    typed = iter(hidden)

    def type_hidden(prompt: str = "") -> str:
        try:
            return next(typed)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", lambda prompt="": "n" if "Registration Lock" in prompt else BOT)
    monkeypatch.setattr("local_assistant.setup.read_hidden", type_hidden)
    monkeypatch.setattr(wizard, "registered_numbers", list)
    monkeypatch.setattr(wizard, "say", said.append)
    return wizard, said, folder


def verify_codes(folder: Path) -> list[object]:
    return [r["params"]["verificationCode"] for r in rpc_requests(folder) if r["method"] == "verify"]  # type: ignore[index]


def test_a_wrong_sms_code_can_be_typed_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wizard, said, folder = signal_step(tmp_path, monkeypatch, ["111-111", "", "222-222", ""], FAKE_SIGNAL_CODE="222222")
    assert wizard.signal_number() == BOT
    assert verify_codes(folder) == ["111111", "222222"]
    assert any("type q to stop" in line for line in said)


@pytest.mark.parametrize("hidden", [["111111", "", "q"], ["111111", ""]], ids=["q", "no terminal"])
def test_q_or_no_terminal_stops_the_sms_code_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hidden: list[str]
) -> None:
    wizard, _said, folder = signal_step(tmp_path, monkeypatch, hidden, FAKE_SIGNAL_CODE="222222")
    with pytest.raises(SystemExit, match="run setup again"):
        wizard.signal_number()
    assert len(verify_codes(folder)) == 1


@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry"])
def test_the_config_step_leaves_soul_md_to_the_user(
    dry: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No soul.md is written: her built-in style is the default until the user writes their own."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # the language question: Enter keeps English
    path = tmp_path / "config.toml"
    Wizard(path, dry_run=dry).config(BOT, ACI)
    assert not (tmp_path / "soul.md").exists()
    assert "soul.example.md" not in capsys.readouterr().out  # said at the end, where her folder is known


def test_setup_ends_with_what_done_means(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check in the terminal is not the end: done is her welcome on the phone, and a reply to it."""
    from local_assistant import setup

    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(Wizard, "check", lambda self: 0)  # doctor's real probes are tested in test_doctor
    monkeypatch.setattr(Wizard, "always_on", lambda self: True)  # it looks at /Library/LaunchDaemons
    assert setup.main(["--dry-run", "--config", str(tmp_path / "config.toml")]) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert "welcome" in last and "no ✗" in last and "Reply to it" in last


def test_ctrl_c_at_a_question_stops_setup_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C at a question (seen in a fresh-install test, 16 Oct): one plain line on how to go on and the shell's
    usual 130, never Python's traceback."""
    from local_assistant import setup

    def ctrl_c(self: Wizard) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(Wizard, "ollama", ctrl_c)
    assert setup.main(["--config", str(tmp_path / "config.toml")]) == 130
    captured = capsys.readouterr()
    assert captured.out.strip().splitlines()[-1] == prompts.SETUP_STOPPED
    assert "Traceback" not in captured.out + captured.err
    assert "scripts/setup.sh" in prompts.SETUP_STOPPED and "skipped" in prompts.SETUP_STOPPED


def test_setup_says_the_configured_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A config that names her (the owner's Tiffany): the banner and the last line use that name; none: Estia."""
    from local_assistant import setup

    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "check", lambda self: 0)
    monkeypatch.setattr(Wizard, "always_on", lambda self: True)
    monkeypatch.setattr(Wizard, "extras", lambda self: None)
    path = tmp_path / "config.toml"
    path.write_text(GOOD + 'name = "Tiffany"\n', encoding="utf-8")
    path.chmod(0o600)
    assert setup.main(["--dry-run", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Tiffany setup: Ollama, the model, her Signal number") and "Tiffany's welcome" in out
    assert "Estia" not in out
    path.unlink()
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("builtins.input", refuse)
    assert setup.main(["--dry-run", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Estia setup:") and "Estia's number" in out and "like estia-4821" in out
    assert "Language: Estia listens and speaks" in out and "Tiffany setup" not in out and "Tiffany's" not in out


def test_the_captcha_code_and_pin_go_to_signal_cli_on_stdin_never_in_its_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Typed hidden, sent over signal-cli's JSON-RPC on stdin (ps shows a program's arguments to every user of the
    Mac), and not printed either: the commands are shown with placeholders, on every try (the terminal may be an AI's
    transcript, SETUP-WITH-YOUR-AI.md). register and verify exist only in signal-cli's multi-account mode (no -a)."""
    token = "signal-hcaptcha.00000000-0000-4000-8000-000000000000.registration.fake-token-777"
    captcha = f"signalcaptcha://{token}"
    secrets = [captcha, "111-111", "9876", "222-222", "9876"]
    hidden = iter(secrets)
    program, folder = fake_signal_cli(
        tmp_path, monkeypatch, FAKE_SIGNAL_CAPTCHA=token, FAKE_SIGNAL_CODE="222222", FAKE_SIGNAL_PIN="9876"
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "n" if "Registration Lock" in prompt else BOT)
    monkeypatch.setattr("local_assistant.setup.read_hidden", lambda prompt="": next(hidden))
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli = program
    monkeypatch.setattr(wizard, "registered_numbers", list)
    assert wizard.signal_number() == BOT
    assert [(r["method"], r["params"]) for r in rpc_requests(folder)] == [
        ("register", {"account": BOT}),
        ("register", {"account": BOT, "captcha": token}),
        ("verify", {"account": BOT, "verificationCode": "111111", "pin": "9876"}),
        ("verify", {"account": BOT, "verificationCode": "222222", "pin": "9876"}),
    ]
    started = launches(folder)
    assert len(started) == 4 and all("jsonRpc" in argv and "-a" not in argv for argv in started)
    for secret in (*secrets, "111111", "222222", token):
        assert not any(secret in arg for argv in started for arg in argv)
    out = capsys.readouterr().out
    for secret in (*secrets, "111111", "222222", "fake-token-777"):
        assert secret not in out
    assert out.count(f"verify {BOT} with <SMS code> and <PIN> on stdin") == 2
    assert f"register {BOT} with <captcha link> on stdin" in out


def test_a_refused_registration_says_why_without_the_captcha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A captcha signal-cli refuses again: setup stops with its reason, and the link is not in it."""
    wizard, _said, _folder = signal_step(
        tmp_path, monkeypatch, ["signalcaptcha://wrong-token-1234"], FAKE_SIGNAL_CAPTCHA="right-token"
    )
    with pytest.raises(SystemExit, match="Registration failed: Captcha required") as stopped:
        wizard.signal_number()
    assert "wrong-token-1234" not in str(stopped.value)


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
    monkeypatch.setattr("builtins.input", lambda prompt="": next(given, ""))  # then Enter: no projects
    path = tmp_path / "config" / "config.toml"
    Wizard(path, dry_run=False).config(BOT, ACI)
    assert parse_config(tomllib.loads(path.read_text(encoding="utf-8"))).language == language
    out = capsys.readouterr().out
    assert "Nederlands, Dutch (nl)" in out and "日本語, Japanese (ja)" in out and "/language" in out


def test_the_config_gets_the_chosen_style() -> None:
    """[personality] style: the example's hearth line, or playful when chosen (the rest of the example as it is)."""
    assert parse_config(tomllib.loads(render_config(EXAMPLE, BOT, ACI))).personality_style == "hearth"
    text = render_config(EXAMPLE, BOT, ACI, style="playful")
    assert parse_config(tomllib.loads(text)).personality_style == "playful" and 'style = "hearth"' not in text
    assert text.replace('style = "playful"', 'style = "hearth"') == render_config(EXAMPLE, BOT, ACI)
    with pytest.raises(ValueError, match="style"):
        render_config(EXAMPLE, BOT, ACI, style="warm")
    with pytest.raises(ValueError, match="style"):
        render_config(EXAMPLE.replace('style = "hearth"', ""), BOT, ACI, style="playful")


@pytest.mark.parametrize(
    ("answers", "style"),
    [([""], "hearth"), (["playful"], "playful"), (["Playful "], "playful"), (["p"], "playful"), (["x", "h"], "hearth")],
)
def test_the_wizard_asks_her_style_once_after_the_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answers: list[str], style: str
) -> None:
    given = iter(["", *answers])  # the language first: Enter keeps English; then Enter: no projects
    asked: list[str] = []

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        return next(given, "")

    monkeypatch.setattr("builtins.input", type_answer)
    path = tmp_path / "config" / "config.toml"
    wizard = Wizard(path, dry_run=False)
    wizard.config(BOT, ACI)
    assert parse_config(tomllib.loads(path.read_text(encoding="utf-8"))).personality_style == style == wizard.style
    out = capsys.readouterr().out
    assert "  hearth (Enter): steady, kind and frank." in out and "  playful: warm, curious and playful." in out
    assert out.index("Language:") < out.index("Style:") < out.index("Projects (extra")
    assert f"✓ Style: {style}." in out and sum("Enter keeps hearth" in prompt for prompt in asked) == len(answers)
    assert not (tmp_path / "config" / "soul.md").exists()  # owner's D2: her style file stays the user's to write


def test_each_style_is_one_plain_line() -> None:
    lines = prompts.SETUP_STYLE.format(name="Estia").splitlines()[1:]
    assert [line.split(":")[0].strip() for line in lines] == ["hearth (Enter)", "playful"]
    for line in lines:
        assert len(line) <= 120 and "you" not in line.lower().split() and "&" not in line, line
    assert (
        "hearth (Enter): steady, kind and frank." in lines[0]
        and "a joke only when asked for fun or on banter" in lines[0]
    )
    assert "playful: warm, curious and playful." in lines[1] and "never on a worry" in lines[1]


def test_a_dry_run_shows_the_style_question_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", refuse)
    wizard = Wizard(tmp_path / "config.toml", dry_run=True)
    assert wizard.her_style() == "hearth"
    out = capsys.readouterr().out
    assert "Style:" in out and prompts.SETUP_STYLE_DRY_RUN in out and "✓ Style" not in out


def test_in_her_own_user_setup_asks_her_style_and_prints_the_line_to_add(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Her config is hers alone: a re-run in her own user asks the style, then prints the lines to add as her and the
    restart; it writes nothing."""
    her_own_user(no_system_paths)
    config = tmp_path / "config.toml"
    rerun(monkeypatch, config, typed="playful")
    out = capsys.readouterr().out
    lines = prompts.SETUP_STYLE_HERS.format(style="playful", edit=prompts.HER_CONFIG_EDIT, save=prompts.NANO_SAVE)
    assert lines in out and '    [personality]\n    style = "playful"\n' in out
    assert "/usr/bin/sudo /usr/local/estia/bin/estia restart" in lines and prompts.HER_CONFIG_EDIT in lines
    assert out.index("Style:") < out.index("Personality:") < out.index("Checking everything:")
    assert not config.exists() and not (tmp_path / "soul.md").exists()


def test_in_her_own_user_a_dry_run_shows_the_style_question_and_the_lines_it_would_print(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    her_own_user(no_system_paths)
    monkeypatch.setattr("builtins.input", refuse)
    Wizard(tmp_path / "config.toml", dry_run=True).personality()
    out = capsys.readouterr().out
    assert prompts.SETUP_STYLE_DRY_RUN in out and prompts.SETUP_STYLE_HERS_DRY_RUN in out


def test_a_first_setup_asks_her_style_once_even_after_the_move(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The config step asked it and wrote it, and the move took that config with her: the end asks nothing again."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.config(BOT, ACI)
    her_own_user(no_system_paths)  # always on: the move
    monkeypatch.setattr("builtins.input", refuse)
    wizard.personality()
    out = capsys.readouterr().out
    assert out.count("Style: how") == 1 and 'style = "' not in out


def test_the_ai_briefing_names_the_style_question() -> None:
    briefing = (Path(__file__).resolve().parents[1] / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8")
    briefing = briefing.split("## 3. The briefing", 1)[1].split("\n## ", 1)[0]
    assert briefing.index("**The language**") < briefing.index("**Her style**") < briefing.index("projects on this Mac")
    style = briefing.split("**Her style**", 1)[1].split("\n", 1)[0]
    assert "hearth" in style and "playful" in style and "Enter" in style


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
    # the name, the language, her style, then whether she sees projects: Enter for each
    wizard, asked = named_wizard(tmp_path, monkeypatch, ["", "", "", ""])
    wizard.her_name()
    assert (wizard.name, wizard.voice) == ("Estia", "af_heart")
    wizard.config(BOT, ACI)
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert parse_config(tomllib.loads(text)).name == "Estia"
    assert parse_config(tomllib.loads(voice_on(text))).voice_name == "af_heart"
    out = capsys.readouterr().out
    assert "1. Estia (default)" in out and "female: Tiffany · Maya · Melina" in out
    assert "male: Socrates · Perikles · Plato" in out and "or type any name" in out
    assert "Female or male" not in " ".join(asked) and len(asked) == 4


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


# ------------------------------------------------------------------ her own user, the security step, Registration Lock


@pytest.fixture(autouse=True)
def no_system_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Setup reads /Library/LaunchDaemons and /usr/local/estia under ESTIA_ROOT: a fresh temporary one per test."""
    root = tmp_path / "system-root"
    monkeypatch.setenv("ESTIA_ROOT", str(root))
    return root


def test_the_registration_lock_pin_is_typed_twice_and_never_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Right after the SMS code: the PIN twice, hidden; it goes to signal-cli on stdin and is never printed."""
    from local_assistant import harden

    hidden = iter(["111-111", "", "7319-04", "731904", "731904", "731904"])
    pins: list[tuple[str, str, str]] = []

    def set_pin(signal_cli: str, number: str, pin: str, **kwargs: object) -> tuple[bool, str]:
        pins.append((signal_cli, number, pin))
        return True, ""

    def answer(prompt: str = "") -> str:
        return "y" if "Registration Lock" in prompt else BOT

    monkeypatch.setattr(harden, "set_pin", set_pin)
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr("local_assistant.setup.read_hidden", lambda prompt="": next(hidden))
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli, _folder = fake_signal_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(wizard, "registered_numbers", list)
    assert wizard.signal_number() == BOT
    assert [pin for _cli, number, pin in pins] == ["731904"] and pins[0][1] == BOT
    out = capsys.readouterr().out
    assert "731904" not in out and "7319-04" not in out
    assert "password manager" in out and "locked for 7 days" in out
    assert "The PIN needs no spaces" not in out and "did not match" in out
    assert "setPin with <PIN> on stdin" in out


def test_the_registration_lock_can_be_left_for_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import harden

    monkeypatch.setattr(harden, "set_pin", refuse)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n" if "Registration Lock" in prompt else BOT)
    monkeypatch.setattr("local_assistant.setup.read_hidden", lambda prompt="": "111111" if "SMS" in prompt else "")
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli, _folder = fake_signal_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(wizard, "registered_numbers", list)
    assert wizard.signal_number() == BOT
    assert "scripts/harden.sh" in capsys.readouterr().out


def test_signal_cli_calls_pin_the_identity_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """register, verify and receive trust a new identity only on first use, like the bridge."""
    wizard, _said, folder = signal_step(tmp_path, monkeypatch, ["111-111", "", "q"])
    wizard.signal_number()
    started = launches(folder)
    assert len(started) == 2 and all(c[c.index("--trust-new-identities") + 1] == "on-first-use" for c in started)


def recorder(ran: list[list[str]]) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = [str(arg) for arg in args[-1]] if isinstance(args[-1], list) else []
        ran.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def test_always_on_moves_her_into_her_own_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import setup

    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(wizard, "run", recorder(ran))
    wizard.always_on()
    assert ran == [[str(setup.REPO / "scripts" / "migrate-to-own-user.sh")]]
    out = capsys.readouterr().out
    assert "_estia" in out and "/var/estia" in out and "never runs as you or as root" in out


def her_own_user(root: Path, user: str = "_estia") -> Path:
    """An own-user install under ESTIA_ROOT: bin/estia and the bridge's service file, which says who she runs as."""
    estia = root / "usr/local/estia/bin/estia"
    estia.parent.mkdir(parents=True)
    estia.write_text("#!/bin/sh\n")
    plist = root / "Library/LaunchDaemons/com.local-assistant.bridge.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({"Label": "com.local-assistant.bridge", "UserName": user}))
    return estia


def test_always_on_is_done_once_she_has_her_own_user(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    her_own_user(no_system_paths)
    monkeypatch.setattr("builtins.input", refuse)
    Wizard(tmp_path / "config.toml", dry_run=False).always_on()
    assert "✓ Always on: as her own user _estia" in capsys.readouterr().out


def test_the_security_step_runs_harden_and_shows_the_phone_checklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import setup

    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(wizard, "run", recorder(ran))
    wizard.security()
    assert ran == [[str(setup.REPO / "scripts" / "harden.sh")]]
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    wizard.security()
    out = capsys.readouterr().out
    assert "Linked devices" in out and "port-out" in out  # declined: the checklist still comes
    assert "Security level hardened:" in out and "default" not in out.lower()


def test_a_rerun_in_her_own_user_never_registers_or_writes_the_config(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import setup

    her_own_user(no_system_paths)
    ran: list[list[str]] = []
    for name in ("signal_number", "owner", "config", "her_name"):
        monkeypatch.setattr(Wizard, name, refuse)
    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "security", lambda self: None)
    monkeypatch.setattr(Wizard, "extras", lambda self: None)
    monkeypatch.setattr(Wizard, "run", recorder(ran))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # her style: Enter keeps hearth (the lines printed)
    config = tmp_path / "config.toml"
    assert setup.main(["--config", str(config)]) == 0
    assert ran == [["/usr/bin/sudo", "/usr/local/estia/bin/estia", "doctor"]]  # the fixed path, never ESTIA_ROOT's
    assert not config.exists()
    assert "her own user _estia" in capsys.readouterr().out


def rerun(monkeypatch: pytest.MonkeyPatch, config: Path, typed: str = "") -> None:
    """setup.main again in her own user, every step but the end stubbed; `typed` answers her style question."""
    from local_assistant import setup

    monkeypatch.setattr("builtins.input", lambda prompt="": typed)
    for name in ("signal_number", "owner", "config", "her_name"):
        monkeypatch.setattr(Wizard, name, refuse)
    for name in ("model", "security", "extras"):
        monkeypatch.setattr(Wizard, name, lambda self: None)
    monkeypatch.setattr(Wizard, "run", recorder([]))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert setup.main(["--config", str(config)]) == 0


def test_the_end_of_setup_says_when_calls_work(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import ownuser

    her_own_user(no_system_paths)
    (no_system_paths / "Library/LaunchDaemons/com.local-assistant.voice.plist").write_bytes(b"x")
    monkeypatch.setattr(Wizard, "voice_health", lambda self: {"stt": "loading", "tts": "ready"})
    rerun(monkeypatch, tmp_path / "config.toml")
    out = capsys.readouterr().out.splitlines()
    # her config is hers alone: setup cannot see [calls], so it promises no text
    assert ownuser.VOICE_LOADING_NO_CALLS in out and ownuser.VOICE_LOADING not in out
    assert out.index(ownuser.VOICE_LOADING_NO_CALLS) > out.index("Checking everything:")  # at the end, after the check
    monkeypatch.setattr(Wizard, "voice_health", lambda self: {"stt": "ready", "tts": "ready"})
    rerun(monkeypatch, tmp_path / "config.toml")
    assert "voice: ready" in capsys.readouterr().out.splitlines()


def test_with_calls_the_end_of_setup_promises_the_text_only_after_the_long_load_wait(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import ownuser

    (no_system_paths / "Library/LaunchDaemons").mkdir(parents=True, exist_ok=True)
    (no_system_paths / "Library/LaunchDaemons/com.local-assistant.voice.plist").write_bytes(b"x")
    monkeypatch.setattr(Wizard, "voice_health", lambda self: {"stt": "loading", "tts": "loading"})
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    now = [0.0]
    wizard.clock = lambda: now[0]
    wizard.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
    wizard.voice_end(calls=True)
    assert capsys.readouterr().out.splitlines()[-1] == ownuser.VOICE_LOADING
    assert now[0] >= ownuser.VOICE_WAIT_SECONDS


def test_without_a_voice_service_setup_says_nothing_about_calls(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import ownuser

    her_own_user(no_system_paths)
    assert Wizard(tmp_path / "config.toml", dry_run=False).voice_health() is None  # a test root: never the live one
    rerun(monkeypatch, tmp_path / "config.toml")
    out = capsys.readouterr().out
    assert ownuser.VOICE_LOADING not in out and "voice: ready" not in out


def test_the_dry_run_describes_her_own_user_the_lock_and_the_security_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ollama_lists(monkeypatch)
    assert main(["--dry-run", "--config", str(tmp_path / "cfg" / "config.toml")]) == 0
    out = capsys.readouterr().out
    assert "Registration Lock" in out and "setPin with <PIN> on stdin" in out
    assert "scripts/migrate-to-own-user.sh" in out and "_estia" in out
    assert "scripts/harden.sh" in out and "Linked devices" in out
    assert "scripts/harden.sh --nuclear" in out and "other services on this Mac" in out  # never part of setup
    # the two levels by name (owner, 14 Oct): setup's step is the hardened level, --nuclear the nuclear one
    assert "Security level hardened:" in out and "The nuclear level: the firewall" in out
    assert "Go through the security step now?" not in out  # a dry run asks nothing


def test_after_a_rollback_setup_takes_your_path_again(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bin/estia stays after a rollback; the bridge's service file names you again."""
    her_own_user(no_system_paths, user="alex")
    assert not Wizard(tmp_path / "config.toml", dry_run=False).own_user()


def test_a_new_config_pins_the_model_ollama_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Setup just made the alias: its digest goes into the new config, so doctor has no pin to ask for."""
    digest = "ab" * 32

    def get_json(self: doctor.Probe, url: str) -> object:
        return {"models": [{"name": "local-worker:latest", "digest": f"sha256:{digest}"}]}

    monkeypatch.setattr(doctor.Probe, "get_json", get_json)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    path = tmp_path / "config.toml"
    Wizard(path, dry_run=False).config(BOT, ACI)
    assert parse_config(tomllib.loads(path.read_text(encoding="utf-8"))).model_digest == digest
    assert "Model pin" in capsys.readouterr().out
    text = render_config(EXAMPLE, BOT, ACI)
    assert "#model_digest" in text and not parse_config(tomllib.loads(text)).model_digest  # none given: no pin


# projects: an extra, off by default; setup asks explicitly (never looks for git folders itself)


def test_the_example_config_has_projects_off() -> None:
    cfg = parse_config(tomllib.loads(render_config(EXAMPLE, BOT, ACI)))
    assert not cfg.projects_enabled
    assert "\n#[projects]\n" in EXAMPLE and "\n[projects]\n" not in EXAMPLE


@pytest.mark.parametrize("level", ["summary", "docs", "code"])
def test_the_config_gets_the_projects_folder_and_level(level: str) -> None:
    text = render_config(EXAMPLE, BOT, ACI, projects=("~/code", level))
    cfg = parse_config(tomllib.loads(text))
    assert cfg.projects_enabled and cfg.projects_default == level and cfg.projects_root == Path.home() / "code"
    assert cfg.projects_docs == frozenset() and cfg.projects_code == frozenset()
    with pytest.raises(ValueError, match="level"):
        render_config(EXAMPLE, BOT, ACI, projects=("~/code", "everything"))


def projects_wizard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> tuple[Wizard, list[str]]:
    """The config step with a person who types `answers` after the language and style questions (then Enter)."""
    typed = iter(["", "", *answers])  # the language and her style first: Enter keeps English and hearth
    asked: list[str] = []

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        return next(typed, "")

    monkeypatch.setattr("builtins.input", type_answer)
    return Wizard(tmp_path / "cfg" / "config.toml", dry_run=False), asked


def test_setup_asks_about_projects_and_enter_means_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wizard, asked = projects_wizard(tmp_path, monkeypatch, [])
    wizard.config(BOT, ACI)
    assert "Should she see projects on this Mac? Most people: no. [y/N] " in asked
    assert not parse_config(tomllib.loads(wizard.config_path.read_text(encoding="utf-8"))).projects_enabled
    out = capsys.readouterr().out
    assert "summary: the README, open to-do items and recent commits" in out
    assert "docs: also the project's documents" in out and "code: also its source code" in out


def test_setup_writes_the_folder_and_level_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    wizard, _asked = projects_wizard(tmp_path, monkeypatch, ["y", "~/nowhere", "~", "~/work", "all", "docs"])
    wizard.config(BOT, ACI)
    cfg = parse_config(tomllib.loads(wizard.config_path.read_text(encoding="utf-8")))
    assert cfg.projects_enabled and cfg.projects_root == home / "work" and cfg.projects_default == "docs"
    out = capsys.readouterr().out
    assert f"No folder {home / 'nowhere'}" in out and "your home itself" in out and "'all' is not a level" in out
    assert "✓ Projects: ~/work, level docs" in out


@pytest.mark.parametrize("where", ["the-folder", "on-the-way", "outside-on-the-way"])
def test_a_projects_folder_reached_through_a_link_is_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """The share refuses a link (its entry would land on the target): setup asks the folder by the share's rules, so
    a first install never records one the move cannot share."""
    home = tmp_path / "home"
    (home / "real/projects").mkdir(parents=True)
    (tmp_path / "srv/projects").mkdir(parents=True)
    (home / "dev").symlink_to(home / "real/projects")
    (home / "linked").symlink_to(home / "real")
    (tmp_path / "srv-link").symlink_to(tmp_path / "srv")
    monkeypatch.setenv("HOME", str(home))
    typed = {
        "the-folder": "~/dev",
        "on-the-way": "~/linked/projects",
        "outside-on-the-way": str(tmp_path / "srv-link/projects"),
    }
    wizard, _asked = projects_wizard(tmp_path, monkeypatch, ["y", typed[where], "~/real/projects", "docs"])
    wizard.config(BOT, ACI)
    out = capsys.readouterr().out
    if where == "the-folder":
        assert f"{home / 'dev'} is a symbolic link: name the folder it leads to. Type another one" in out
    else:
        shown = home / "linked/projects" if where == "on-the-way" else tmp_path / "srv-link/projects"
        assert f"A folder on the way to {shown} is a symbolic link. Type another one" in out
    cfg = parse_config(tomllib.loads(wizard.config_path.read_text(encoding="utf-8")))
    assert cfg.projects_root == home / "real/projects" and "✓ Projects: ~/real/projects, level docs" in out


def test_a_first_install_with_a_projects_folder_outside_your_home_says_it_is_opened_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    elsewhere = tmp_path / "srv" / "projects"
    elsewhere.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    wizard, _asked = projects_wizard(tmp_path, monkeypatch, ["y", str(elsewhere), "docs"])
    wizard.config(BOT, ACI)
    out = capsys.readouterr().out
    assert (
        f"{elsewhere} is outside your home, so scripts/share-projects.sh does not share it: give _estia read access "
        "to it yourself"
    ) in out
    cfg = parse_config(tomllib.loads(wizard.config_path.read_text(encoding="utf-8")))
    assert cfg.projects_root == elsewhere


def test_a_dry_run_shows_the_projects_question_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", refuse)
    assert Wizard(tmp_path / "config.toml", dry_run=True).projects() is None
    out = capsys.readouterr().out
    assert "Should she see projects on this Mac? Most people: no." in out and "(dry run" in out


def test_projects_are_among_the_extras() -> None:
    projects = EXTRAS["projects"]
    assert projects.how.startswith("Nothing to install: setup asks the folder and the level")
    assert projects.leaves_the_mac == "nothing (the files are read on the Mac)"
    assert list(EXTRAS).index("projects") == 7  # the numbers of the earlier ones stay


def test_the_extras_name_every_kind_of_kit_and_every_extra() -> None:
    """Hue and the CalDAV calendars are supported, and documents and calls are extras too (findings 21, 60): the list
    names them, with what each needs that only the person can do."""
    assert list(EXTRAS)[-2:] == ["documents", "calls"]
    assert "Hue" in EXTRAS["lights"].title and "Kasa" in EXTRAS["lights"].title
    assert "scripts/hue-pair.sh" in EXTRAS["lights"].how
    for service in ("Google", "Outlook", "iCloud"):
        assert service in EXTRAS["calendar"].title, service
    assert "scripts/share-documents.sh" in EXTRAS["documents"].how and "~/Estia-KB" in EXTRAS["documents"].how
    calls = EXTRAS["calls"]
    assert "gh auth login" in calls.how and "voice" in calls.how
    assert "install-signal-cli-jvm.sh" in calls.how and "build.sh" in calls.how


def test_the_web_extra_says_how_a_key_gets_in() -> None:
    """set-web-keys.sh copies keys from the login keychain; it never asks for one (finding 54)."""
    how = EXTRAS["web"].how
    assert 'security add-generic-password -U -a "$USER" -s ollama -w' in how and "-s brave-search" in how
    assert "scripts/set-web-keys.sh" in how


def test_in_her_own_user_the_extras_name_her_config_and_the_restart(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """After the move her config is in /var/estia: each picked extra ends with how to edit it (nano, and how to save)
    and the restart (findings 18, 55, 56)."""
    her_own_user(no_system_paths)
    wizard, _asked = extras_wizard(tmp_path, monkeypatch, ["voice calendar"])
    wizard.extras()
    out = capsys.readouterr().out
    assert "/usr/bin/sudo /usr/local/estia/bin/estia config" in out  # nano as her: no copy of hers in your account
    assert "sudo -u _estia -e" not in out
    assert "Ctrl-O, Enter saves; Ctrl-X quits" in out
    assert "/usr/bin/sudo /usr/local/estia/bin/estia restart" in out
    assert "~/.config/local-assistant" not in out


def test_before_the_move_the_extras_name_your_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wizard, _asked = extras_wizard(tmp_path, monkeypatch, ["voice"])
    wizard.extras()
    out = capsys.readouterr().out
    assert str(wizard.config_path) in out and "_estia -e" not in out


def test_the_projects_lines_in_her_own_user_say_how_to_save(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    her_own_user(no_system_paths)
    wizard, _asked = extras_wizard(tmp_path, monkeypatch, ["projects", "~/work", "summary"])
    wizard.extras()
    out = capsys.readouterr().out
    assert "/usr/bin/sudo /usr/local/estia/bin/estia config" in out and "sudo -u _estia -e" not in out
    assert "Ctrl-O, Enter saves; Ctrl-X quits" in out


def extras_wizard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[str], config: str | None = None
) -> tuple[Wizard, list[str]]:
    """setup --extras for a person who types `answers` (then Enter), with `config` as the config file, if any."""
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    path = tmp_path / "cfg" / "config.toml"
    if config is not None:
        path.parent.mkdir(parents=True)
        path.write_text(config, encoding="utf-8")
        path.chmod(0o600)
    typed = iter(answers)
    asked: list[str] = []

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        return next(typed, "")

    monkeypatch.setattr("builtins.input", type_answer)
    return Wizard(path, dry_run=False), asked


def test_the_projects_extra_asks_the_folder_and_level_and_switches_them_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """setup --extras, projects: no sniffing for git folders; the folder and the level asked, then [projects]."""
    before = render_config(EXAMPLE, BOT, ACI)
    wizard, asked = extras_wizard(tmp_path, monkeypatch, ["projects", "~/work", "code"], before)
    wizard.extras()
    assert asked[1:] == [
        "Which folder holds them? (Enter: ~/dev, q: no projects) ",
        "How much may she read: summary, docs or code? (Enter: summary) ",
    ]
    text = wizard.config_path.read_text(encoding="utf-8")
    cfg = parse_config(tomllib.loads(text))
    assert cfg.projects_enabled and cfg.projects_root == tmp_path / "home" / "work" and cfg.projects_default == "code"
    assert len(re.findall(r"^\[projects\]$", text, re.MULTILINE)) == 1  # the example's block, filled in
    assert stat.S_IMODE(wizard.config_path.stat().st_mode) == 0o600
    copies = list(wizard.config_path.parent.glob("config.toml.bak-*"))
    assert [copy.read_text(encoding="utf-8") for copy in copies] == [before]  # the old config kept, never just replaced
    out = capsys.readouterr().out
    assert "summary: the README, open to-do items and recent commits" in out
    assert "✓ Projects: ~/work, level code." in out and "from her next start" in out


def test_the_projects_extra_on_a_config_without_the_example_lines_adds_the_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = (
        f'bot_number = "{BOT}"\nallowed_acis = ["{ACI}"]\n\n[[tapo.devices]]\nname = "lamp"\nhost = "192.168.1.30"\n'
    )
    wizard, _asked = extras_wizard(tmp_path, monkeypatch, ["projects", "", "docs"], before)
    (tmp_path / "home" / "dev").mkdir()
    wizard.extras()
    text = wizard.config_path.read_text(encoding="utf-8")
    assert text.startswith(before)
    cfg = parse_config(tomllib.loads(text))
    assert cfg.projects_enabled and cfg.projects_root == tmp_path / "home" / "dev" and cfg.projects_default == "docs"
    assert [device.name for device in cfg.tapo_devices] == ["lamp"]


def test_the_projects_extra_leaves_a_config_with_projects_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The owner's own config already has [projects] (default = "docs"): nothing is asked or changed."""
    before = f'bot_number = "{BOT}"\nallowed_acis = ["{ACI}"]\n[projects]\ndefault = "docs"\n'
    wizard, asked = extras_wizard(tmp_path, monkeypatch, ["projects"], before)
    wizard.extras()
    assert len(asked) == 1 and wizard.config_path.read_text(encoding="utf-8") == before
    assert "✓ Projects: on already" in capsys.readouterr().out


def test_the_projects_extra_in_her_own_user_shows_the_lines_and_writes_nothing(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Her config is hers alone: the folder by its absolute path (her ~ is /var/estia), shared, then a restart."""
    her_own_user(no_system_paths)
    wizard, _asked = extras_wizard(tmp_path, monkeypatch, ["projects", "~/work", "summary"])
    wizard.extras()
    assert not wizard.config_path.exists()
    out = capsys.readouterr().out
    work = tmp_path / "home" / "work"
    assert "/usr/bin/sudo /usr/local/estia/bin/estia config" in out
    assert f'[projects]\n    root = "{work}"\n    default = "summary"' in out
    assert f"scripts/share-projects.sh {work}" in out and "/usr/bin/sudo /usr/local/estia/bin/estia restart" in out


def test_the_projects_extra_in_her_own_user_with_a_folder_outside_your_home(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """scripts/share-projects.sh shares only a folder in your home: for one outside it, the step says so instead."""
    her_own_user(no_system_paths)
    elsewhere = tmp_path / "srv" / "projects"
    elsewhere.mkdir(parents=True)
    wizard, _asked = extras_wizard(tmp_path, monkeypatch, ["projects", str(elsewhere), "docs"])
    wizard.extras()
    out = capsys.readouterr().out
    assert f'[projects]\n    root = "{elsewhere}"\n    default = "docs"' in out
    assert f"scripts/share-projects.sh {elsewhere}" not in out
    assert (
        f"  2. {elsewhere} is outside your home, so scripts/share-projects.sh does not share it: give _estia read "
        "access to it yourself"
    ) in out
    assert "/usr/bin/sudo /usr/local/estia/bin/estia restart" in out


def test_the_extras_help_says_projects_are_added(capsys: pytest.CaptureFixture[str]) -> None:
    from local_assistant.setup import main

    with pytest.raises(SystemExit):
        main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "show the extras and how to add them (projects: asks the folder and the level and adds [projects])" in (
        help_text
    )


def test_the_projects_folder_question_without_a_terminal_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`printf 'projects\\n' | scripts/setup.sh --extras` (an AI driving setup through a pipe), with no ~/dev: no
    terminal counts as q, asked once, never a loop on the default folder."""
    before = render_config(EXAMPLE, BOT, ACI)
    wizard, asked = extras_wizard(tmp_path, monkeypatch, ["projects"], before)
    typed = iter(["projects"])

    def piped(prompt: str = "") -> str:
        asked.append(prompt)
        if len(asked) > 5:
            raise AssertionError("asked again and again")
        try:
            return next(typed)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", piped)
    assert not (tmp_path / "home" / "dev").exists()
    wizard.extras()
    assert asked[1:] == ["Which folder holds them? (Enter: ~/dev, q: no projects) "]
    assert wizard.config_path.read_text(encoding="utf-8") == before
    assert "No folder" not in capsys.readouterr().out


def test_the_projects_extra_on_a_config_that_does_not_load_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = f'bot_number = "{BOT}"\n'  # no allowed_acis: the bridge would refuse it
    wizard, asked = extras_wizard(tmp_path, monkeypatch, ["projects", "~/work", "docs"], before)
    wizard.extras()
    assert len(asked) == 1 and wizard.config_path.read_text(encoding="utf-8") == before
    assert "✗ Projects: not added, the config does not load (allowed_acis" in capsys.readouterr().out


def test_the_projects_extra_without_a_config_points_to_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wizard, asked = extras_wizard(tmp_path, monkeypatch, ["projects"])
    wizard.extras()
    assert len(asked) == 1 and not wizard.config_path.exists()
    assert "no config yet" in capsys.readouterr().out


# ------------------------------------------------------------------ hidden answers of any length, only from a terminal


REAL_HAS_TERMINAL = wizard_module.has_terminal


@pytest.fixture(autouse=True)
def a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The person's own Terminal: setup's full run needs one (a test of its absence says so)."""
    monkeypatch.setattr(wizard_module, "has_terminal", lambda: True)


class Pty:
    """A terminal pair: the program reads `path` (the terminal's end); the test types into `master`."""

    def __init__(self) -> None:
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        self.seen = b""

    def wait_for(self, text: bytes, seconds: float = 10) -> None:
        deadline = time.monotonic() + seconds
        while text not in self.seen:
            assert time.monotonic() < deadline, self.seen[-200:]
            ready, _, _ = select.select([self.master], [], [], 0.2)
            if ready:
                self.seen += os.read(self.master, 4096)

    def type(self, text: str) -> None:
        data = text.encode()
        while data:
            data = data[os.write(self.master, data) :]

    def close(self) -> None:
        os.close(self.master)
        os.close(self.slave)


def hidden_in_a_pty(typed: str) -> tuple[str | BaseException, Pty]:
    """read_hidden on a real terminal (a pty), with `typed` typed once the prompt shows."""
    pty = Pty()
    got: list[str | BaseException] = []

    def read() -> None:
        try:
            got.append(read_hidden("captcha link: ", pty.path))
        except (EOFError, OSError) as exc:
            got.append(exc)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    pty.wait_for(b"captcha link: ")
    pty.type(typed)
    reader.join(10)
    assert got, "the hidden prompt never returned"
    return got[0], pty


def test_a_captcha_link_longer_than_a_terminal_line_is_read_whole() -> None:
    """macOS keeps a typed line to 1,023 characters and drops the rest with the Enter: Signal's captcha link is longer
    (findings 1, 37). Read with the line mode off, any length arrives, never echoed, and the terminal is put back."""
    link = "signalcaptcha://signal-hcaptcha.00000000-0000-4000-8000-000000000000.registration.P1_" + "a1B2" * 750
    assert len(link) > 3000
    got, pty = hidden_in_a_pty(link + "\r")
    try:
        assert got == link
        assert b"aaa" not in pty.seen and b"a1B2" not in pty.seen  # nothing echoed
        mode = termios.tcgetattr(pty.slave)[3]
        assert mode & termios.ICANON and mode & termios.ECHO  # the line mode and the echo are back
    finally:
        pty.close()


def test_a_hidden_answer_can_be_corrected_and_ctrl_d_stops() -> None:
    got, pty = hidden_in_a_pty("12x\x7f3\x15456\n")  # a typo erased, then the line cleared with Ctrl-U
    pty.close()
    assert got == "456"
    got, pty = hidden_in_a_pty("\x04")
    pty.close()
    assert isinstance(got, EOFError)


def test_a_hidden_answer_never_comes_from_stdin_without_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """No terminal (an AI's command tool, a pipe): setup stops and says where to run it, and never reads a code from
    its input, which may be an AI's transcript (finding 38)."""
    monkeypatch.setattr(wizard_module, "TTY", "/nonexistent/tty")
    monkeypatch.setattr("sys.stdin", io.StringIO("fake-captcha-value\n"))
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    with pytest.raises(SystemExit) as stopped:
        wizard.hidden("The code Signal sent by SMS (hidden): ")
    assert str(stopped.value) == prompts.SETUP_NO_TERMINAL
    assert sys.stdin.read() == "fake-captcha-value\n"  # left unread


def test_setup_without_a_terminal_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under pytest stdin is no terminal: the real check says so, and setup's full run stops before its first step."""
    monkeypatch.setattr(wizard_module, "has_terminal", REAL_HAS_TERMINAL)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert not wizard_module.has_terminal()
    for name in ("model", "her_name", "signal_number", "always_on", "security", "extras", "check"):
        monkeypatch.setattr(Wizard, name, refuse)
    assert main(["--config", str(tmp_path / "config.toml")]) == 1
    assert prompts.SETUP_NO_TERMINAL in capsys.readouterr().out


def test_the_documented_by_hand_route_reads_a_long_line_with_the_line_mode_off() -> None:
    """setup_assistant.md, 2.3: `stty -icanon` before signal-cli, so a pasted request with the captcha link (over
    1,023 characters) arrives whole; a program reading a line, as signal-cli's jsonRpc does, gets all of it."""
    pty = Pty()
    reader = "import sys; print('ready', flush=True); line = sys.stdin.readline(); print('got', len(line), flush=True)"
    proc = subprocess.Popen(
        ["/bin/sh", "-c", 'stty -icanon && exec "$0" -I -c "$1"', sys.executable, reader],
        stdin=pty.slave,
        stdout=pty.slave,
        stderr=pty.slave,
    )
    try:
        pty.wait_for(b"ready")
        pty.type('{"jsonrpc":"2.0","method":"register","params":{"captcha":"' + "x" * 2500 + '"},"id":2}\r')
        pty.wait_for(b"got ")
        pty.wait_for(b"\n", 5)
        assert re.search(rb"got (\d+)", pty.seen) and int(re.search(rb"got (\d+)", pty.seen).group(1)) > 2500  # type: ignore[union-attr]
    finally:
        proc.kill()
        proc.wait()
        pty.close()


# ------------------------------------------------------------------ a step that failed stops setup (findings 10, 59)


def ollama_has_pulled(monkeypatch: pytest.MonkeyPatch, digests: dict[str, str] | None = None) -> None:
    """Ollama's list after the pull: both MLX builds with their tested digests (or the ones given)."""
    from local_assistant.setup import GEMMA_12B, GEMMA_12B_DIGEST, GEMMA_26B, GEMMA_26B_DIGEST

    listed = digests or {GEMMA_26B: GEMMA_26B_DIGEST, GEMMA_12B: GEMMA_12B_DIGEST}

    def get_json(self: doctor.Probe, url: str) -> object:
        return {"models": [{"name": name, "digest": digest} for name, digest in listed.items()]}

    monkeypatch.setattr(doctor.Probe, "get_json", get_json)
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 32.0)


def test_a_failed_ollama_create_stops_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    create = [wizard.ollama_program, "create"]

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        return subprocess.CompletedProcess(command, 1 if command[:2] == create else 0, "", "")

    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    monkeypatch.setattr(wizard, "run", run)
    ollama_has_pulled(monkeypatch)
    with pytest.raises(SystemExit, match="local-worker was not made"):
        wizard.model()
    assert ran[-1][:2] == create
    assert "✓ Model" not in capsys.readouterr().out
    assert not (tmp_path / ".local-worker.Modelfile").exists()


def test_the_model_is_pulled_by_its_mlx_tag_with_the_ollama_in_use_and_checked_by_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.ollama_program = "/opt/example/ollama"  # the one the Ollama step kept or installed
    monkeypatch.setattr(wizard, "run", recorder(ran))
    ollama_has_pulled(monkeypatch)
    wizard.model()
    assert ran[0] == ["caffeinate", "-i", "/opt/example/ollama", "pull", "gemma4:26b-mlx"]
    assert ran[1][:3] == ["/opt/example/ollama", "create", "local-worker"]
    out = capsys.readouterr().out
    assert "Model: gemma4:26b-mlx for this Mac's 32 GB" in out and "needs Ollama 0.33.1 or later" in out
    assert "✓ Model: local-worker = gemma4:26b-mlx" in out


def smaller_mac(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory: float, answer: str, *, dry: bool = False
) -> tuple[Wizard, list[list[str]], list[str]]:
    """The model step on a Mac with `memory` GB, the person answering `answer`: what ran and what was asked."""
    ran: list[list[str]] = []
    asked: list[str] = []

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        return answer

    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    wizard = Wizard(tmp_path / "config.toml", dry_run=dry)
    monkeypatch.setattr(wizard, "run", recorder(ran))
    ollama_has_pulled(monkeypatch)
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: memory)
    monkeypatch.setattr("builtins.input", refuse if dry else type_answer)
    return wizard, ran, asked


@pytest.mark.parametrize("memory", [16.0, 24.0])
def test_on_16_and_24_gb_setup_says_what_12b_failed_and_asks_before_the_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], memory: float
) -> None:
    """Owner, 15 Oct: setup still picks 12B there, but the person reads what failed, in plain words, on a line of its
    own, and only a yes starts the 8 GB download."""
    wizard, ran, asked = smaller_mac(tmp_path, monkeypatch, memory, "y")
    wizard.model()
    assert asked == ["Go on with the smaller model? [y/N] "]
    assert ran[0][3:] == ["pull", "gemma4:12b-mlx"]
    out = capsys.readouterr().out
    lines = out.splitlines()
    model = next(line for line in lines if line.startswith("Model: gemma4:12b-mlx"))
    assert "failed" not in model and "SECURITY.md" not in model
    warning = next(line for line in lines if line.startswith("! Gemma 4 12B"))
    assert "a forwarded audio file with planted instructions" in warning and "on a call" in warning
    assert (  # the limit inside the claim (SEC4 re-check): a direct "turn off the lamp" switches at once
        'still makes a device change wait for your yes after an audio file, for an "ask first" device and in a '
        "message that is not about devices" in warning
    )
    assert "each device change" not in warning
    assert warning.endswith("32 GB with Gemma 26B is the tested setup.")
    assert (
        lines.index(model)
        < lines.index(warning)
        < next(i for i, line in enumerate(lines) if "The download can take a while" in line)
    )


@pytest.mark.parametrize("answer", ["", "n"], ids=["enter", "no"])
def test_no_to_the_smaller_model_downloads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    wizard, ran, _asked = smaller_mac(tmp_path, monkeypatch, 16.0, answer)
    with pytest.raises(SystemExit, match="Nothing was downloaded"):
        wizard.model()
    assert ran == [] and "The download can take a while" not in capsys.readouterr().out


def test_a_dry_run_shows_the_smaller_models_question_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wizard, ran, _asked = smaller_mac(tmp_path, monkeypatch, 16.0, "", dry=True)
    wizard.model()
    out = capsys.readouterr().out
    assert (
        "! Gemma 4 12B" in out and '(dry run: asks "Go on with the smaller model? [y/N]"; only y downloads it)' in out
    )
    assert ran[0][3:] == ["pull", "gemma4:12b-mlx"]  # shown as it would run after a yes


def test_the_tested_model_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wizard, ran, _asked = smaller_mac(tmp_path, monkeypatch, 32.0, "", dry=False)
    monkeypatch.setattr("builtins.input", refuse)
    wizard.model()
    assert ran[0][3:] == ["pull", "gemma4:26b-mlx"] and "smaller model" not in capsys.readouterr().out


def test_the_ai_briefing_names_the_smaller_model_question() -> None:
    briefing = (Path(__file__).resolve().parents[1] / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8")
    briefing = briefing.split("## 3. The briefing", 1)[1].split("\n## ", 1)[0]
    model = briefing.split("**The model download:**", 1)[1].split("\n", 1)[0]
    assert '**"Go on with the smaller model?"**' in model and "16 or 24 GB" in model
    assert "only y downloads it" in model


def test_a_pulled_model_with_another_digest_is_never_made_into_local_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry changed the tag since the tests: setup says so and makes nothing from it."""
    ran: list[list[str]] = []
    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "run", recorder(ran))
    ollama_has_pulled(monkeypatch, {"gemma4:26b-mlx": "ab" * 32})
    with pytest.raises(SystemExit, match="came down as abababababab, not the tested build f0fc7e0ae494"):
        wizard.model()
    assert len(ran) == 1 and ran[0][3:] == ["pull", "gemma4:26b-mlx"]  # pulled, nothing made from it
    assert not (tmp_path / ".local-worker.Modelfile").exists()


def test_the_ollama_step_comes_first_and_off_the_live_mac_only_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under a test's root nothing is read or run: the step shows the download, the checks and the service."""
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    monkeypatch.setattr("local_assistant.ollama_build.owner_home", lambda: tmp_path / "home")
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 32.0)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.ollama()
    out = capsys.readouterr().out
    assert "Ollama: its official build 0.40.4" in out and "/usr/bin/curl -q -fL" in out
    assert (
        "/usr/bin/sudo /bin/launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.ollama.plist" in out
    )
    assert wizard.ollama_program == str(tmp_path / "home/opt/ollama/current/ollama")
    assert not (tmp_path / "home").exists()


def test_setup_runs_the_ollama_step_before_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr("local_assistant.setup.has_terminal", lambda: True)

    def model(self: Wizard) -> None:
        order.append("model")
        raise SystemExit(3)

    monkeypatch.setattr(Wizard, "ollama", lambda self: order.append("ollama"))
    monkeypatch.setattr(Wizard, "model", model)
    with pytest.raises(SystemExit):
        main(["--config", str(tmp_path / "config.toml")])
    assert order == ["ollama", "model"]


def test_a_mac_too_small_stops_before_ollama_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 8.0)
    with pytest.raises(SystemExit):
        Wizard(tmp_path / "config.toml", dry_run=False).ollama()
    out = capsys.readouterr().out
    assert "This Mac has 8 GB of memory. Estia needs 16 GB or more." in out and "curl" not in out


def test_a_failed_move_stops_before_the_security_step_and_the_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(GOOD, encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(Wizard, "model", lambda self: None)
    for name in ("security", "extras", "check"):
        monkeypatch.setattr(Wizard, name, refuse)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(Wizard, "run", lambda self, command, **kwargs: subprocess.CompletedProcess(command, 1, "", ""))
    assert main(["--config", str(path)]) == 1
    out = capsys.readouterr().out
    assert "The move did not finish" in out and "scripts/migrate-to-own-user.sh --dry-run" in out


def test_a_move_that_succeeds_while_the_share_fails_goes_on_to_the_security_step(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The move script ends 3 when only its last step, the projects share, fails (a folder outside your home):
    she runs in her own user, so setup says only the share failed, with its command, and goes on."""
    from local_assistant import setup

    path = tmp_path / "config.toml"
    path.write_text(GOOD, encoding="utf-8")
    path.chmod(0o600)
    reached: list[str] = []
    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "security", lambda self: reached.append("security"))
    monkeypatch.setattr(Wizard, "extras", lambda self: reached.append("extras"))
    monkeypatch.setattr(Wizard, "personality", lambda self: None)
    monkeypatch.setattr(Wizard, "check", lambda self: 0)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    def run(self: Wizard, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        her_own_user(no_system_paths)  # the move went through: her service file names _estia; then the share failed
        return subprocess.CompletedProcess(command, 3, "", "")

    monkeypatch.setattr(Wizard, "run", run)
    assert main(["--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert "The move did not finish" not in out and reached == ["security", "extras"]
    assert "✓ Always on: Estia runs in her own user _estia. Only her projects folder is not shared yet" in out
    assert f"then run {setup.REPO / 'scripts' / 'share-projects.sh'}" in out


def test_a_failed_move_whose_restore_left_her_service_file_still_stops(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SEC1 re-check: a move that fails and whose restore fails too can leave her service file naming _estia.
    Only the share's own exit code (3) means she was moved: any other failure stops setup before the security step."""
    path = tmp_path / "config.toml"
    path.write_text(GOOD, encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(Wizard, "model", lambda self: None)
    for name in ("security", "extras", "check"):
        monkeypatch.setattr(Wizard, name, refuse)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    def run(self: Wizard, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        her_own_user(no_system_paths)  # left behind by a restore that failed
        return subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(Wizard, "run", run)
    assert main(["--config", str(path)]) == 1
    out = capsys.readouterr().out
    assert "The move did not finish" in out and "Only her projects folder is not shared yet" not in out


# ------------------------------------------------------------------ only a verified number is offered (findings 20, 53)


def signal_accounts(home: Path, **registered: bool | None) -> None:
    """signal-cli's account list, and each account's own file with its "registered" flag (None: no file)."""
    data = home / ".local/share/signal-cli/data"
    data.mkdir(parents=True)
    listed = []
    for i, (number, flag) in enumerate(registered.items()):
        listed.append({"path": str(100 + i), "environment": "LIVE", "number": number.replace("n", "+")})
        if flag is not None:
            (data / str(100 + i)).write_text(json.dumps({"version": 8, "registered": flag}))
    (data / "accounts.json").write_text(json.dumps({"accounts": listed, "version": 2}))


@pytest.mark.parametrize(
    ("registered", "offered"),
    [
        ({"n306900000000": True}, ["+306900000000"]),
        ({"n306900000000": False}, []),  # register ran, verify did not: register it again
        ({"n306900000000": None}, []),  # no account file
        ({"n306900000000": False, "n306900000001": True}, ["+306900000001"]),
    ],
)
def test_only_a_verified_number_is_offered_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: dict[str, bool | None], offered: list[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    signal_accounts(tmp_path, **registered)
    assert Wizard(tmp_path / "config.toml", dry_run=False).registered_numbers() == offered


# ------------------------------------------------------------------ over SSH, the move inside screen (finding 51)


@pytest.mark.parametrize(("env", "warned"), [({}, True), ({"STY": "1.x"}, False), ({"TMUX": "/tmp/t,1,0"}, False)])
def test_over_ssh_the_move_is_offered_inside_screen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env: dict[str, str],
    warned: bool,
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 50000 192.0.2.2 22")
    for name in ("STY", "TMUX"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    Wizard(tmp_path / "config.toml", dry_run=False).always_on()
    out = capsys.readouterr().out
    assert ("screen" in out and "dropped connection" in out) is warned


# ------------------------------------------------------------------ the personality, where her files are (finding 11)


def test_the_web_extra_leads_with_the_hidden_prompt_that_works_over_ssh() -> None:
    """Over SSH the login keychain is not the default one and stays locked: her hidden prompt works (review 15 Oct,
    21); both keys are named."""
    how = EXTRAS["web"].how
    assert "/usr/bin/sudo /usr/local/estia/bin/estia secret set web.env OLLAMA_API_KEY" in how
    assert "BRAVE_API_KEY" in how and how.index("secret set") < how.index("security add-generic-password")


def test_the_calls_extra_deploys_the_builds_before_calls_are_on() -> None:
    """In her own user the JVM signal-cli and the call helper reach her only with a deploy: [calls] before it stops
    her texts too (review 15 Oct, 11)."""
    how = EXTRAS["calls"].how
    assert "/usr/bin/sudo /usr/local/estia/bin/estia-deploy" in how
    assert how.index("estia-deploy") < how.index("[calls]")


def test_the_personality_hint_names_her_folder_once_she_has_her_own_user(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # her style, in her own user: Enter keeps hearth
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.personality()
    out = capsys.readouterr().out
    assert f"cp soul.example.md {tmp_path / 'soul.md'} && chmod 600 {tmp_path / 'soul.md'}" in out
    assert "Personality: Estia's built-in style, hearth (steady, kind and frank). To give her yours instead:" in out
    wizard.style = "playful"  # a config that chose it
    wizard.personality()
    assert "built-in style, playful (warm, curious and playful)." in capsys.readouterr().out
    her_own_user(no_system_paths)
    wizard.personality()
    out = capsys.readouterr().out
    assert "/usr/bin/sudo /usr/local/estia/bin/estia config soul" in out  # nano as her; the example the first time
    assert "built-in style (hearth, the default, or playful: [personality] style in her config)" in out
    assert "sudo -u _estia -e" not in out
    assert "cp soul.example.md" not in out


def test_setup_gives_the_personality_hint_after_the_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    order: list[str] = []
    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "her_name", lambda self: None)
    monkeypatch.setattr(Wizard, "signal_number", lambda self: BOT)
    monkeypatch.setattr(Wizard, "owner", lambda self, number: ACI)
    monkeypatch.setattr(Wizard, "config", lambda self, number, aci: order.append("config"))

    def always_on(self: Wizard) -> bool:
        order.append("always on")
        return True

    monkeypatch.setattr(Wizard, "always_on", always_on)
    monkeypatch.setattr(Wizard, "security", lambda self: order.append("security"))
    monkeypatch.setattr(Wizard, "extras", lambda self: order.append("extras"))
    monkeypatch.setattr(Wizard, "personality", lambda self: order.append("personality"))
    monkeypatch.setattr(Wizard, "check", lambda self: 0)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert main(["--config", str(tmp_path / "config.toml")]) == 0
    assert order == ["config", "always on", "security", "extras", "personality"]
