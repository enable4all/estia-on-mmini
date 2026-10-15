"""Statements that several files make must say the same thing."""

import importlib.util
import re
import subprocess
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEVICE_LOGIN = (
    "Older Kasa devices need no login; newer Kasa firmware and Tapo devices need the TP-Link login (Tapo is untested)."
)


GUIDE = ROOT / "GUIDE.md"  # the README's body since the split (owner, 15 Oct); README.md is the front page
NOT_AFFILIATED = (  # every company or service README.md and GUIDE.md name (owner, 15 Oct), in both files
    "Not affiliated with Signal, Spotify, Google, Microsoft, Apple, Amazon, Bose, TP-Link, Philips, Fastmail, Nextcloud, "
    "Scaleway, MacStadium, Ollama, OpenAI, Anthropic, Hugging Face, Brave, GitHub, Homebrew, crates.io or OpenClaw."
)


def prose(path: Path) -> str:
    """The file's words on one line: comment marks, list dashes and line breaks left out."""
    lines = (re.sub(r"^\s*(#+|-)?\s*", "", line) for line in path.read_text(encoding="utf-8").splitlines())
    return " ".join(line for line in lines if line)


@pytest.mark.parametrize(
    "name", ["scripts/tapo-discover.sh", "setup_assistant.md", "config.example.toml", "tools/tapo/helper.py"]
)
def test_the_device_login_is_explained_with_one_sentence(name: str) -> None:
    text = prose(ROOT / name)
    assert DEVICE_LOGIN in text
    assert "Tapo devices need the TP-Link login first" not in text and "Kasa devices need none" not in text


def test_the_readme_still_says_only_kasa_is_tested() -> None:
    assert "Only Kasa devices are tested." in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_readme_says_estia_and_that_every_home_names_its_own() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Estia\n")
    assert "Name her what you like. Ours is called Tiffany." in readme
    assert "Estia listens, thinks and speaks in **one language at a time**." in GUIDE.read_text(encoding="utf-8")
    assert 'name = "Estia"' in (ROOT / "config.example.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["README.md", "GUIDE.md", "SETUP-WITH-YOUR-AI.md", "setup_assistant.md", "AGENTS.md"])
def test_the_docs_call_her_estia_and_tiffany_only_as_ours(name: str) -> None:
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        if "Tiffany" in line:
            assert "ours" in line.lower() or "Ours" in line, line


def test_the_notices_credit_the_fonts_and_name_no_brand_we_do_not_use() -> None:
    for doc in (ROOT / "README.md", GUIDE):
        assert f"\n{NOT_AFFILIATED}\n" in doc.read_text(encoding="utf-8"), doc.name
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    brand = (ROOT / "docs" / "brand" / "source" / "README.md").read_text(encoding="utf-8")
    for text in (notices, brand):
        assert "Manrope ExtraBold" in text and "Shippori Mincho B1" in text and "SIL Open Font License 1.1" in text


def test_the_tagline_is_a_thinking_partner_living_at_home() -> None:
    """The owner's tagline, the same in the README's opening line, the package and the logo's lockup."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "\n**A private AI thinking partner living at home, on your own Mac.** " in readme
    assert "thinking partner living at home, on your own Mac" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lockup = (ROOT / "docs" / "brand" / "source" / "lockup.py").read_text(encoding="utf-8")
    assert '"A THINKING PARTNER LIVING AT HOME"' in lockup


def test_every_link_names_the_new_repo() -> None:
    """The project lives at enable4all/estia-on-mmini; the old repo's name is gone from every tracked file."""
    old = "tiffany-on-" + "mmini"  # split, so this file does not name it either
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    spec = importlib.util.spec_from_file_location("privacy_scan", ROOT / "scripts" / "privacy_scan.py")
    assert spec and spec.loader
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    private = scan.load_skip_patterns(ROOT)  # a private fork's own notes (.privacy-scan-skip) may name the old repo
    for name in filter(None, tracked.decode().split("\0")):
        if scan.skipped(name, private):
            continue
        path = ROOT / name
        if path.is_file() and path.suffix not in {".png", ".ttf"}:
            assert old not in path.read_text(encoding="utf-8", errors="replace"), name
    clone = "git clone https://github.com/enable4all/estia-on-mmini.git ~/estia && cd ~/estia"
    for name in ("README.md", "GUIDE.md", "setup_assistant.md"):
        assert clone in (ROOT / name).read_text(encoding="utf-8"), name
    advisories = "https://github.com/enable4all/estia-on-mmini/security/advisories/new"
    assert advisories in (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_the_speech_detector_is_documented_and_credited() -> None:
    example = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert '#interrupt = "only speech"' in example  # the default, as config.py has it (owner, 14 Oct)
    assert "barge" in example.split('#interrupt = "only speech"', 1)[1].split("\n\n", 1)[0]  # the old key still read
    for name in ("GUIDE.md", "setup_assistant.md", "AGENTS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert 'interrupt = "only speech"' in text and 'interrupt = "any sound"' in text, name
    setup = prose(ROOT / "setup_assistant.md")
    assert 'grep "calls: barge-in"' in setup  # the call's end line, to read the counts in the log
    assert '/usr/bin/sudo /usr/local/estia/bin/estia logs -n 5000 | grep "calls: barge-in"' in setup  # her log
    assert (
        "~/Library/Logs/local-assistant/bridge.log" not in setup.split("calls: barge-in", 1)[1].split("lists them")[0]
    )
    assert "calls: barge-in speech: speech stopped her 1 time, would have 0; loudness stopped her" in setup
    assert "(loudness after 3 failed answers in a row)" in setup
    assert "at most once per stretch of her speech (a sentence resumed after a cough counts again)" in setup
    assert "42.5 per 10 minutes (3.5 at the levels live calls show)" in setup  # the spike's numbers, as measured
    assert '"vad"' in setup and "voice-models/silero-vad" in setup  # the health field and the model's folder
    assert "calls: speech gated (p 1.00, level 132, floor 40)" in setup
    readme = prose(GUIDE)
    assert "until it is ready, any loud sound does" in readme  # the limit inside the claim
    assert 'barge = "speech"' in setup and 'barge = "loudness"' in setup  # the old wording, still read
    assert "loudness" not in readme  # the old rule's name stays in the guide's log lines
    assert "only speech stops her" not in readme
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "VOICE_MODEL_TESTS=1 swift test --filter SpeechDetection" in agents
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "Silero VAD" in notices and "MIT" in notices.split("Silero VAD", 1)[1].split("\n", 1)[0]
    silero = notices.split("Silero VAD", 1)[1].split("\n", 1)[0]
    assert "via FluidAudio" in silero and "FluidInference/silero-vad-coreml" in silero and "pinned" in silero


def test_the_speech_detectors_fix_names_the_folder_the_voice_service_uses() -> None:
    from local_assistant import prompts

    swift = (ROOT / "tools/voice/Sources/voice-service/main.swift").read_text(encoding="utf-8")
    assert 'appendingPathComponent("Library/Application Support/local-assistant")' in swift
    assert 'appendingPathComponent("voice-models")' in swift
    assert (
        "~/Library/Application Support/local-assistant/voice-models/silero-vad,"
        in prompts.DOCTOR_DETECTOR_UNAVAILABLE_FIX
    )


# ------------------------------------------------------------------ her own user, in the install docs

INSTALL_DOCS = ("GUIDE.md", "setup_assistant.md", "SETUP-WITH-YOUR-AI.md")  # README.md: the front page
SITE_PROMISES = (
    "Local.",
    "Read-only mail and calendars.",
    "Lights and plugs follow a rule set per device.",
    "Only Signal.",
    "Keys stay put.",
)


@pytest.mark.parametrize("name", ("README.md", *INSTALL_DOCS))
def test_her_commands_are_named_by_their_full_root_owned_path(name: str) -> None:
    """`sudo estia` would find whatever `estia` comes first on PATH, and that folder may be yours to write."""
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "/usr/bin/sudo /usr/local/estia/bin/estia doctor" in text
    assert not re.search(r"sudo estia(-deploy)? ", text)
    assert "newsyslog" not in text  # her logs rotate themselves


def test_the_readme_says_where_she_lives_and_how_she_runs() -> None:
    readme = GUIDE.read_text(encoding="utf-8")
    where = readme.split("### Where things live on the Mac", 1)[1].split("\n## ", 1)[0]
    for path in (
        "/var/estia/.config/local-assistant/config.toml",
        "/var/estia/Library/Application Support/local-assistant",
        "/usr/local/estia",
    ):
        assert path in where, path
    assert "`_estia`" in readme and "/usr/bin/sudo /usr/local/estia/bin/estia-deploy" in readme
    assert "scripts/migrate-to-own-user.sh" in readme and "scripts/harden.sh" in readme


def test_the_readme_keeps_the_promises_the_site_reads() -> None:
    """The front page leads with the five promises, short; the guide has each in full."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    guide = GUIDE.read_text(encoding="utf-8")
    for promise in SITE_PROMISES:
        assert f"- **{promise}**" in readme and f"- **{promise}**" in guide, promise
    for said in (
        "Chats, memory and profile stay on the Mac.",
        "Signal Desktop on your laptop",
        "Only Kasa devices are tested",
    ):
        assert said in readme and said in guide, said
    assert '"Always relay calls"' in guide


def test_the_update_and_restart_rows_keep_their_shape() -> None:
    """The site shows these two commands: the first code span of each row stays as it was."""
    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    upgrade = re.search(r"\| Upgrade \| `([^`]+)`", guide)
    restart = re.search(r"\| Restart the daemon \| `([^`]+)`", guide)
    assert upgrade and upgrade.group(1).startswith("git pull && uv sync --frozen")
    assert restart and restart.group(1) == "/usr/bin/sudo /usr/local/estia/bin/estia restart"  # no bare launchctl (48)
    assert "/usr/bin/sudo /usr/local/estia/bin/estia-deploy" in guide.split("| Upgrade |", 1)[1].split("\n", 1)[0]


def test_the_readme_names_the_device_commands_and_the_signal_terms_gap() -> None:
    readme = GUIDE.read_text(encoding="utf-8")
    commands = readme.split("## Slash commands", 1)[1].split("\n## ", 1)[0]
    for command in ("`/lock`", "`/unlock`", "`/devices`", "`/devices ok N`"):
        assert command in commands, command
    assert "new linked device" in readme
    good = readme.split("## Good to know", 1)[1].split("\n## ", 1)[0]
    assert "signal-cli, an unofficial client" in good and "SECURITY.md#known-accepted-gaps" in good


def test_the_compare_then_trust_steps_stay_in_section_7() -> None:
    """Doctor's and /status's fix for a changed safety number points here."""
    operations = (ROOT / "setup_assistant.md").read_text(encoding="utf-8").split("## 7. Operations", 1)[1]
    row = operations.split("| Safety number changed |", 1)[1].split("\n", 1)[0]
    assert "listIdentities" in row and "trust -v" in row and "only if they match" in row
    assert "/usr/bin/sudo /usr/local/estia/bin/estia signal listIdentities" in row  # as her, from /, the bridge stopped
    assert "/usr/bin/sudo /usr/local/estia/bin/estia signal trust -v" in row


def test_the_guide_covers_the_move_deploy_rollback_and_security_step() -> None:
    guide = prose(ROOT / "setup_assistant.md")
    for words in (
        "scripts/migrate-to-own-user.sh --dry-run",
        "scripts/migrate-to-own-user.sh --rehearse",
        "scripts/migrate-to-own-user.sh --rollback",
        "/usr/bin/sudo /usr/local/estia/bin/estia rollback",
        "/usr/bin/sudo /usr/local/estia/bin/estia remove-old",
        "/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools",
        "/usr/bin/sudo /usr/local/estia/bin/estia config soul",
        "tmux",
        "/usr/bin/sudo /usr/local/estia/bin/estia-deploy",
        "scripts/harden.sh --dry-run",
        "/usr/bin/sudo /usr/local/estia/bin/estia config",
        "/usr/bin/sudo /usr/local/estia/bin/estia secret set",
        "Registration Lock",
        "locked for 7 days",
        "Linked devices",
    ):
        assert words in guide, words


def test_the_installing_ai_has_a_security_section() -> None:
    text = (ROOT / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8")
    section = text.split("## Security", 1)[1].split("\n## ", 1)[0]
    assert "Never run `sudo` yourself" in text.split("## The rules", 1)[1].split("\n## ", 1)[0]  # said once (15 Oct)
    for words in (
        "scripts/harden.sh",
        "Before each sudo prompt",
        "phone checklist",
        "unlocked phone",
        "_estia",
    ):
        assert words in section, words
    assert "cannot read her data" in section


def test_the_readme_puts_the_limits_inside_its_own_user_claims() -> None:
    """Every claim provable: her own account keeps her files from your programs only while no sudo rule skips your
    password. What she sends Ollama (yours) is a known gap that only SECURITY.md names (owner, 15 Oct)."""
    readme = GUIDE.read_text(encoding="utf-8")
    account = readme.split("- **Her own account on the Mac.**", 1)[1].split("\n- **", 1)[0]
    assert "password" in account and "every program she runs" in account
    assert account.rstrip().endswith("as long as no admin rule skips the password.")
    for doc in (ROOT / "README.md", GUIDE):
        keys = doc.read_text(encoding="utf-8").split("- **Keys stay put.**", 1)[1].split("\n", 1)[0]
        assert "skips" in keys, doc.name
    assert "Ollama and AgentJev run as you: every prompt she sends" in site_flat(ROOT / "SECURITY.md")
    commands = readme.split("## Slash commands", 1)[1].split("\n## ", 1)[0]
    assert "`/devices forget N`" in commands
    assert "/usr/bin/sudo /usr/local/estia/bin/estia rollback" in readme


def test_security_md_says_the_own_user_limit_in_the_same_plain_words() -> None:
    """The same fact keeps the same words: the own-user claim's limit as the README says it, never "sudo rule"."""
    security = prose(ROOT / "SECURITY.md")
    protects = security.split("What the project tries to protect", 1)[1].split("If the Mac is compromised", 1)[0]
    assert "as long as no admin rule skips the password" in protects
    assert "as long as no admin rule skips the password" in prose(GUIDE)
    assert "sudo rule" not in security and "passwordless sudo" not in security and "metadata" not in security


def test_the_installing_ai_sends_every_sudo_to_the_persons_own_terminal() -> None:
    text = (ROOT / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8")
    assert "own Terminal window" in text and "password prompts" in text
    security = text.split("## Security", 1)[1].split("\n## ", 1)[0]
    assert "Ollama" in security  # the limit inside the claim


# a fake `sudo` earlier on the owner's PATH catches the password: every documented command types /usr/bin/sudo

OWNER_DOCS = ("README.md", *INSTALL_DOCS, "SECURITY.md", "AGENTS.md", "docs/e2e-checklist.md")
# plain words (owner, 14 Oct): the same two limits, without "PATH", "deploy" or "commits"; since the split (owner,
# 15 Oct) README.md and GUIDE.md leave them to SECURITY.md, which names them in its own words
DEPLOY_LIMIT = (
    "a program of yours can change what your next update puts live; the update shows the list of changes and the "
    "files they touch, not the code itself"
)
SUDO_LIMIT = (  # zsh runs a function or alias named /usr/bin/sudo even for the full path (review 15 Oct, 15)
    "a program of yours can catch your password with a fake `sudo` that your Terminal finds before the real one, so "
    "type `/usr/bin/sudo`; a fake one written into your shell's startup files runs even then, so "
    "`whence -va /usr/bin/sudo` must list only the file, never a shell function or an alias"
)


def code_in(text: str) -> list[str]:
    """The fenced blocks and the inline code spans of a Markdown text."""
    fenced = re.compile(r"```.*?```", re.DOTALL)
    return [*fenced.findall(text), *re.findall(r"`([^`\n]+)`", fenced.sub("", text))]


@pytest.mark.parametrize("name", OWNER_DOCS)
def test_every_documented_owner_command_types_usr_bin_sudo(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert [code for code in code_in(text) if re.search(r"(?<![/\w.-])sudo\s", code)] == []


@pytest.mark.parametrize("name", OWNER_DOCS)
def test_every_program_after_sudo_in_the_docs_is_named_by_its_full_path(name: str) -> None:
    """sudo looks a bare name up in your PATH (macOS sets no secure_path), where Homebrew's folder comes first and is
    yours: a planted `launchctl` there runs as root once the password is typed (finding 48). A command copied from the
    docs names the program after sudo by its full path, or is one of hers: /usr/local/estia/bin/estia. The scripts and
    the code are held to the same in tests/test_scripts.py."""
    from tests.test_scripts import programs_after_sudo

    assert programs_after_sudo("`/usr/bin/sudo pmset -a sleep 0`") == ["pmset"]
    assert programs_after_sudo("`/usr/bin/sudo -u _estia install -m 600 a b`") == ["install"]
    assert programs_after_sudo("`/usr/bin/sudo /usr/bin/pmset -a sleep 0` or `/usr/bin/sudo -u _estia -e x`") == []
    text = (ROOT / name).read_text(encoding="utf-8")
    found = [
        f"{name}:{number}: {programs_after_sudo(line)}"
        for number, line in enumerate(text.splitlines(), 1)
        if programs_after_sudo(line)
    ]
    assert found == []


@pytest.mark.parametrize(
    ("name", "start"),
    [
        ("SETUP-WITH-YOUR-AI.md", "- **Once she runs as `_estia`"),
        ("setup_assistant.md", "- Her own user: she runs as `_estia`"),
    ],
)
def test_the_own_user_claims_carry_the_deploy_and_the_sudo_limit(name: str, start: str) -> None:
    """Every claim provable: a program of the owner's still reaches her through the next deploy, and through a fake
    sudo that catches the password."""
    text = (ROOT / name).read_text(encoding="utf-8")
    claim = " ".join(text.split(start, 1)[1].split("\n- ", 1)[0].split())
    assert DEPLOY_LIMIT in claim and SUDO_LIMIT in claim


def test_the_live_checklist_numbers_are_unique_and_the_guide_counts_them() -> None:
    """Each item of docs/e2e-checklist.md has its own number (41a-style extras aside), 1 to N with none left out, and
    the guide's walk-through says N items; a doc that cites an item cites one that exists."""
    checklist = (ROOT / "docs" / "e2e-checklist.md").read_text(encoding="utf-8")
    numbers = re.findall(r"^\| (\d+)[a-z]? \|", checklist, flags=re.MULTILINE)
    plain = re.findall(r"^\| (\d+) \|", checklist, flags=re.MULTILINE)
    assert len(plain) == len(set(plain)), sorted({n for n in plain if plain.count(n) > 1})
    highest = max(int(number) for number in numbers)
    assert sorted(int(number) for number in plain) == list(range(1, highest + 1))
    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    assert f"({highest} items:" in guide
    for name in ("AGENTS.md", "SECURITY.md", "setup_assistant.md", "README.md", "GUIDE.md"):
        for cited in re.findall(r"e2e-checklist\.md, (\d+)(?:-(\d+))?\)", (ROOT / name).read_text(encoding="utf-8")):
            for number in filter(None, cited):
                assert 1 <= int(number) <= highest, (name, number)


# owner, 13 Oct: projects are an extra, off by default (not everyone is a developer); the site's drift test reads these


def test_the_readme_has_projects_as_an_extra_as_the_site_reads() -> None:
    readme = GUIDE.read_text(encoding="utf-8")
    assert "### Projects (extra, off by default)" in readme
    assert "Setup asks whether she should see projects on this Mac, which folder, and how much." in readme
    for line in (
        "- **summary**: the README, open to-do items and recent commits",
        "- **docs**: also the project's documents",
        "- **code**: also its source code",
    ):
        assert line in readme, line
    assert (
        "Only files git tracks, never secret-like names such as `.env`, never outside the folder you named." in readme
    )
    assert "| Projects | `[projects]` | Nothing to install: setup asks the folder and the level |" in readme
    assert (
        "- **No shell.** The AI has no command line: it cannot type commands on the Mac. With Projects on, she reads "
        "only files git tracks in the folder you named, at "
        "the level you chose, never secret-like files such as `.env`."
    ) in readme
    assert "| Health checks and project reading |" in readme
    assert "default `~/dev`" not in readme  # no folder of yours is read unless setup named it


@pytest.mark.parametrize("name", ["setup_assistant.md", "config.example.toml", "AGENTS.md", "SECURITY.md"])
def test_projects_are_off_by_default_in_every_doc(name: str) -> None:
    text = prose(ROOT / name)
    assert "off by default" in text or "off without [projects]" in text or "off without `[projects]`" in text, name
    assert "is on by default" not in text and "On by default" not in text, name


def test_the_call_glance_is_documented_with_its_limit() -> None:
    """At the start of a call: numbers and times only, never a title; the details only after a yes."""
    readme = prose(GUIDE)
    assert "At the start of a call she knows today's numbers, never the titles" in readme
    guide = prose(ROOT / "setup_assistant.md")
    assert "What she knows at the start of a call." in guide and "Calendar today: 3 events left" in guide
    agents = prose(ROOT / "AGENTS.md")
    assert "call's glance" in agents and "never a title" in agents


def test_a_spoken_project_name_is_documented() -> None:
    """With neutral names: the owner's own projects stay out of the public docs (the tests keep the real case)."""
    for name in ("GUIDE.md", "setup_assistant.md", "AGENTS.md"):
        text = prose(ROOT / name)
        assert "nearest project" in text and "Do you mean" in text, name
        assert not re.search(r"\b(?:authe|othie)\b", text, re.IGNORECASE), name


# the site's drift tests (estia-13oct, from the site session) read these: the website under the tagline, Tiffany as
# our own thinking partner, and a plain README sentence for each of the site's items


def test_the_readme_links_the_website_under_its_tagline() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tagline = readme.index("\n**A private AI thinking partner")
    assert readme.index("\nWebsite: [estia.fun](https://estia.fun)\n") > tagline
    assert "Estia started as Tiffany, our own thinking partner, before" in readme
    assert "our own assistant" not in readme


def test_the_readme_has_a_sentence_for_each_item_the_site_lists() -> None:
    readme = GUIDE.read_text(encoding="utf-8")
    flat = prose(GUIDE)
    for words in (
        "`/babble nl` starts practice in Dutch",  # language practice
        'With `interrupt = "only speech"` in `[calls]` (the default), a speech detector on the Mac (Silero VAD) '
        "decides what stops her",  # site: was 'With `barge = "speech"` in `[calls]`, a speech detector …'
        "Her own voice from a loud speaker next to the microphone (a laptop on Signal Desktop) is told apart from the "
        "caller's",  # her own echo does not stop her
        "**Her own account on the Mac.** She runs as `_estia`, a hidden macOS account",
        "**A security step in setup.**",
        "Projects (extra, off by default) Setup asks whether she should see projects on this Mac",
    ):
        assert words in flat, words
    commands = readme.split("## Slash commands", 1)[1].split("\n## ", 1)[0]
    for row in ("| `/babble`", "| `/lock` |", "| `/unlock` |", "| `/devices` |"):
        assert row in commands, row


# owner, 13 Oct: after the move her voice compiled for 15 minutes and calls went unanswered; the docs quote the code's
# own texts, so the words she sends and the words the docs promise stay the same


def test_the_docs_quote_the_voice_texts_as_the_code_writes_them() -> None:
    from local_assistant import ownuser, prompts

    for name in ("GUIDE.md", "setup_assistant.md"):
        text = prose(ROOT / name)
        for quoted in (prompts.CALL_NOT_READY, prompts.VOICE_READY, ownuser.VOICE_LOADING):
            assert quoted in text, (name, quoted)
    readme = GUIDE.read_text(encoding="utf-8")
    assert (
        "\nWith calls set up, calls work once her voice is ready (up to about 15 minutes after an install, a move or a "
        "macOS update); she texts you then." in readme
    )
    assert prompts.VOICE_READY in prose(ROOT / "SETUP-WITH-YOUR-AI.md")
    assert ownuser.VOICE_LOADING_NO_CALLS in prose(ROOT / "setup_assistant.md")


def test_the_text_is_promised_only_with_calls_set_up() -> None:
    """The ready text exists only with [calls]: an AI following the order on a Mac without them must not wait for it."""
    order = prose(ROOT / "SETUP-WITH-YOUR-AI.md").split("The order that worked** (a Mac reached over SSH):", 1)[1][:900]
    assert "With calls set up, wait for her text" in order
    assert (
        "with voice but without calls, go on once `curl -s http://127.0.0.1:8150/health` shows `stt` and `tts` ready"
        in order
    )
    guide = prose(ROOT / "setup_assistant.md").split("`scripts/harden.sh` runs it again any time", 1)[1][:900]
    assert "with calls set up, her text" in guide
    assert "The person runs `scripts/harden.sh` in their own Terminal" in order


def test_remove_old_is_never_offered_without_its_limit() -> None:
    """remove-old also deletes the folders held for the rollback: each place that offers it says so."""
    for name in ("GUIDE.md", "SETUP-WITH-YOUR-AI.md", "setup_assistant.md"):
        text = prose(ROOT / name)
        assert "together with her old folders held for the rollback: no rollback after it" in text, name
    assert "unless they want the space back" in prose(ROOT / "SETUP-WITH-YOUR-AI.md")
    security = prose(ROOT / "SECURITY.md")
    assert "/var/estia/.before-<time>" in security and "hold older copies of her keys" in security


def test_the_docs_say_what_sysadminctl_really_does_with_her_number() -> None:
    guide = prose(ROOT / "setup_assistant.md")
    assert "a free number in 450-499) with its" not in guide
    assert "her group gets a free number in 450-499" in guide and "it ignores `-UID`" in guide


def test_the_firewall_docs_name_every_tool_and_the_known_gaps() -> None:
    guide = prose(ROOT / "setup_assistant.md")
    assert "`ps` and proc_pidpath" in guide
    assert "Untested as her on the live Mac" in guide
    assert "block all incoming connections is on" in guide
    assert "-nP -iTCP:<port> -sTCP:LISTEN" in guide
    gotchas = prose(ROOT / "AGENTS.md").split("Gotchas (learned the hard way)", 1)[1]
    assert "the owner's only in `scripts/doctor.sh` before the move" in gotchas
    assert "merged with the owner's own from `lsof`" not in gotchas
    checklist = (ROOT / "docs" / "e2e-checklist.md").read_text(encoding="utf-8")
    for words in ("! Firewall: node on port 8170", "My voice is ready: you can call me now.", ".before-"):
        assert words in checklist.split("## After the move into her own user", 1)[1], words
    assert "`voice_ready`" in (ROOT / "AGENTS.md").read_text(encoding="utf-8").split("## Commands", 1)[0]


def test_the_guide_gives_the_firewall_fix_doctor_prints() -> None:
    from local_assistant import hostsec

    guide = prose(ROOT / "setup_assistant.md")
    for flag in ("--add", "--unblockapp"):
        assert f"{hostsec.SUDO} {hostsec.SOCKETFILTERFW} {flag} " in guide, flag
    assert "cannot be reached from other machines" in guide


def test_the_guide_says_remove_old_also_deletes_her_copies_kept_aside() -> None:
    guide = prose(ROOT / "setup_assistant.md")
    assert "/var/estia/.before-" in guide and "remove-old" in guide.split("/var/estia/.before-", 1)[1][:400]
    assert "Deleted 2 releases she does not run" in guide


def test_the_installing_ai_asks_for_her_log_and_knows_the_order_that_worked() -> None:
    text = prose(ROOT / "SETUP-WITH-YOUR-AI.md")
    assert "/usr/bin/sudo /usr/local/estia/bin/estia logs bridge -n 40" in text
    assert "second SSH session" in text


def test_the_gotchas_keep_what_the_live_move_taught() -> None:
    gotchas = prose(ROOT / "AGENTS.md").split("Gotchas (learned the hard way)", 1)[1]
    for words in (
        "sysadminctl",
        "eDSPermissionError",
        "CFFIXED_USER_HOME",
        "-Duser.home",
        "ESTIA_TEST_ROOT",
        "mkdir(parents=True",
    ):
        assert words in gotchas, words


BARE_SUDO = re.compile(r"(?<![/\w])sudo (/usr/local/estia|-u |launchctl|chown|chmod|\{)")


def test_commands_people_copy_from_the_screen_never_name_a_bare_sudo() -> None:
    """A planted `sudo` earlier on the owner's PATH catches the password: every command code prints, and every line a
    script echoes, names /usr/bin/sudo (live 13 Oct: the move's last screen showed both forms)."""
    from local_assistant import deploy, ownuser

    assert ownuser.ESTIA == "/usr/bin/sudo /usr/local/estia/bin/estia"
    assert ownuser.DEPLOY_CMD == deploy.DEPLOY == "/usr/bin/sudo /usr/local/estia/bin/estia-deploy"
    found = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in sorted((ROOT / "src/local_assistant").glob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if BARE_SUDO.search(line)
    ]
    found += [
        f"{path.relative_to(ROOT)}:{number}"
        for path in [*sorted((ROOT / "scripts").glob("*.sh")), ROOT / "scripts/estia", ROOT / "scripts/estia-deploy"]
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "echo" in line and BARE_SUDO.search(line)
    ]
    assert found == []


RUNS_SUDO = re.compile(r"(?:^|[|;&(]|\bexec|\bthen|\bdo|\belse)\s*sudo\s")


def test_the_owners_scripts_run_usr_bin_sudo_never_a_bare_sudo() -> None:
    """The scripts the docs send people to pipe keys into sudo: a planted `sudo` earlier on the owner's PATH would
    catch the password and the key, so each one runs /usr/bin/sudo by its full path."""
    found = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in sorted((ROOT / "scripts").glob("*.sh"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("#") and RUNS_SUDO.search(line)
    ]
    assert found == []
    own_user = (ROOT / "scripts/own-user.sh").read_text(encoding="utf-8")
    assert '\nsudo="$estia_root/usr/bin/sudo"' in own_user  # /usr/bin/sudo, or a test's fake tree's stub
    for script in (
        "set-web-keys.sh",
        "set-calendar-url.sh",
        "set-calendar-password.sh",
        "set-tapo-credentials.sh",
        "mail-login.sh",
    ):
        assert '"$sudo" "$estia"' in (ROOT / "scripts" / script).read_text(encoding="utf-8"), script


# batch2 (owner, 14 Oct): the facts the code now keeps, in the docs and in plain words; the site's drift tests read the
# README sentences marked "site"


def test_the_readme_says_what_goes_online_in_the_sites_words() -> None:
    readme = GUIDE.read_text(encoding="utf-8")
    online = readme.split("### What stays home, and what goes online", 1)[1].split("\n## ", 1)[0]
    assert "\nThe model always runs on the Mac. " in online
    assert (
        "Outside services are reached only for Signal, links you send and the extras you switch on." in online
    )  # site
    assert "| Google's calendar links, for Google calendars (read-only) |" in online  # site
    assert "Services on the internet are reached only for features that are turned on." not in readme
    assert "iCal feeds" not in readme


def test_the_readme_puts_the_ai_and_the_code_in_plain_words() -> None:
    readme = GUIDE.read_text(encoding="utf-8")
    assert "**A reminder is set only when a note from the code, not from the AI, says so.**" in readme  # site
    assert (
        "**When the AI writes part of a message she starts, it sees only your words, your profile and the moment.**"
        in readme
    )  # site
    assert "Code rules reduce prompt injection (orders hidden in that text) but cannot rule it out." in readme  # site
    assert "the code's note" not in readme and "Gemma's part of a message" not in readme
    words = readme.split("## Words used here", 1)[1].split("\n## ", 1)[0]
    for word in ("- Bridge:", "- root:", "- Prompt injection:", "- SSH:"):
        assert word in words, word


def test_the_secrets_typed_at_setup_never_reach_a_command_line_in_any_doc() -> None:
    """F1: the captcha link, the SMS code and a PIN go to signal-cli on its input (JSON-RPC), as setup sends them;
    no doc shows them as arguments, which every user of the Mac can read."""
    for name in ("README.md", "GUIDE.md", "SETUP-WITH-YOUR-AI.md", "setup_assistant.md", "SECURITY.md", "AGENTS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "--captcha" not in text and "--pin" not in text and "<code-from-the-sms>" not in text, name
    readme = prose(GUIDE)
    assert (
        "go into hidden prompts in your own Terminal: never on screen, and never on a program's command line" in readme
    )
    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    section = guide.split("### 2.3 Signal number (once)", 1)[1].split("\n### ", 1)[0]
    assert '"method":"register"' in section and '"method":"verify"' in section and '"verificationCode"' in section
    assert "signal-cli -Djava.net.preferIPv4Stack=true --trust-new-identities on-first-use jsonRpc" in section
    assert "on its input" in prose(ROOT / "SECURITY.md") and "Signal captcha link" in prose(ROOT / "SECURITY.md")


def test_the_nuclear_option_is_offered_with_what_it_may_stop() -> None:
    """F9: the default step leaves the firewall alone; --nuclear says first that it may stop other services."""
    readme = prose(GUIDE)
    nuclear = readme.split("**The nuclear option.**", 1)[1].split("**Mail and calendars are read-only.**", 1)[0]
    for words in ("`scripts/harden.sh --nuclear`", "may stop other services on this Mac", "ping", "keychain", "doctor"):
        assert words in nuclear, words
    step = readme.split("**A security step in setup.**", 1)[1].split("**The nuclear option.**", 1)[0]
    assert "firewall" not in step.lower() and "keychain" not in step
    for name in ("SETUP-WITH-YOUR-AI.md", "setup_assistant.md", "SECURITY.md"):
        assert "scripts/harden.sh --nuclear" in prose(ROOT / name), name
        assert "may stop other services on this Mac" in prose(ROOT / name), name
    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    assert "| Firewall and stealth mode | turned on" not in guide
    operations = guide.split("## 7. Operations", 1)[1]
    assert "--nuclear" in operations.split("| Security step |", 1)[1].split("\n", 1)[0]


def test_the_docs_say_the_model_never_runs_in_the_cloud() -> None:
    """F3: a cloud model is refused by name and by Ollama's own list."""
    assert "cloud model" in prose(ROOT / "README.md")
    for name in ("setup_assistant.md", "SECURITY.md"):
        text = prose(ROOT / name)
        assert "-cloud" in text and ":cloud" in text and "remote_host" in text, name
    assert "cloud model" in (ROOT / "config.example.toml").read_text(encoding="utf-8")


def test_the_docs_say_homebrew_runs_without_its_analytics() -> None:
    """F4."""
    assert "analytics" in prose(GUIDE)
    for name in ("setup_assistant.md", "SECURITY.md"):
        text = prose(ROOT / name)
        assert "HOMEBREW_NO_ANALYTICS=1" in text and "brew analytics off" in text, name


def test_the_docs_name_the_languages_and_the_links_her_own_messages_check() -> None:
    """F6: a link on any ending; the words that ask for a code or a password in the 11 languages."""
    readme = prose(GUIDE)
    assert "The word checks read English, except for reminder and call claims" not in readme
    assert "example.ch/x" in readme and "all 11 languages" in readme
    security = prose(ROOT / "SECURITY.md")
    assert "The checks of a message she starts are English word lists" not in security
    assert '"x.co."' in security and "all 11 languages" in security
    guide = prose(ROOT / "setup_assistant.md")
    assert "example.ch/x" in guide and "all 11 languages" in guide


def test_the_docs_keep_the_home_address_out_of_the_database() -> None:
    """F10: the database keeps the rounded area and the address's tag, never the address."""
    places = prose(GUIDE).split("**Places stay rough.**", 1)[1].split("Known gaps are listed", 1)[0]
    assert "never the address itself" in places
    for name in ("setup_assistant.md", "SECURITY.md", "config.example.toml"):
        assert "never the address" in prose(ROOT / name), name


def test_the_voice_models_are_described_as_the_code_loads_them() -> None:
    """F2: Whisper and the speech detector pinned and checked, Whisper offline after the first download; the speaking
    voices as published."""
    readme = prose(GUIDE)
    # Kokoro comes at the service's first start (FluidAudio's initialize at every start), Supertonic-3 on first use
    assert "(Kokoro for English at the first start, Supertonic-3 for the other ten languages on first use)" in readme
    assert "fixed to one version" in readme and "with no network" in readme and "as published" in readme
    assert "set aside and downloaded again at that version" in readme  # a changed Whisper folder: no root-only fix
    assert "set aside" in prose(ROOT / "SECURITY.md") and "set aside" in prose(ROOT / "setup_assistant.md")
    gaps = prose(ROOT / "SECURITY.md").split("Known accepted gaps", 1)[1]
    assert "Kokoro" in gaps and "only when a file is missing" in gaps


# YouTube (owner, 13-14 Oct): always on with read_link; the docs say what is read, what is kept and the known gaps in the
# words the code uses, and the notices list the helper's packages at the versions its lock pins


def _youtube_runtime() -> dict[str, str]:
    """youtube-transcript-api and every package it needs at runtime, with the versions tools/youtube/uv.lock pins."""
    import tomllib

    lock = tomllib.loads((ROOT / "tools/youtube/uv.lock").read_text(encoding="utf-8"))
    packages = {package["name"]: package for package in lock["package"]}
    found: dict[str, str] = {}
    todo = ["youtube-transcript-api"]
    while todo:
        name = todo.pop()
        if name not in found:
            found[name] = packages[name]["version"]
            todo.extend(dependency["name"] for dependency in packages[name].get("dependencies", []))
    return found


def test_the_notices_list_the_youtube_helpers_packages_at_their_pinned_versions() -> None:
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    runtime = _youtube_runtime()
    assert runtime["youtube-transcript-api"] == "1.2.4" and len(runtime) == 7
    for name, version in runtime.items():
        assert f"{name} {version}" in notices, name
    assert "YouTube" in notices.split("Online services", 1)[1]


def test_the_readme_has_youtube_in_what_she_can_do_and_what_goes_online() -> None:
    assert "\n- **YouTube videos.** A summary from the video's captions" in (ROOT / "README.md").read_text(
        encoding="utf-8"
    )  # the front page's line
    readme = GUIDE.read_text(encoding="utf-8")
    can_do = readme.split("## What she can do", 1)[1].split("\n### ", 1)[0]
    assert "| YouTube videos | Send a YouTube link" in can_do
    online = readme.split("### What stays home, and what goes online", 1)[1].split("\n## ", 1)[0]
    assert (
        "Outside services are reached only for Signal, links you send and the extras you switch on." in online
    )  # site: word for word, YouTube is one of the links you send
    assert "| | YouTube, for the captions of a video link you send |" in online
    flat = prose(GUIDE)
    for words in (
        "Only a link in your current message is read. A video found by a web search is named, never read.",
        "YouTube's terms forbid automated access",
        "| YouTube helper | `tools/youtube/` |",
    ):
        assert words in flat, words


def test_the_docs_quote_the_youtube_texts_as_the_code_writes_them() -> None:
    from local_assistant import prompts, video

    captions = video.Captions(segments=((0.0, "words"),), language="en", generated=False, length=6_000.0, cut=False)
    note = video.reading_note(captions, 3)
    assert note == "Reading a 1 h 40 min video: about 1 minute."
    for name in ("GUIDE.md", "setup_assistant.md"):
        assert note in prose(ROOT / name), name
    guide = prose(ROOT / "setup_assistant.md")
    assert prompts.VIDEO_LEARN_COMMAND in guide and prompts.VIDEO_ERRORS["no_captions"] in guide
    assert f"{video.PART_CHARS:,} characters" in guide and f"{video.CAP_CHARS:,} characters" in guide
    assert prompts.VIDEO_NOT_SENT.split("{")[0] in guide  # code's line after copied captions, word for word
    copies = f"{video.COPY_WORDS} or more of their words in a row"
    shares = f"shorter stretches of {video.SHARE_WORDS} or more that together make up more than {video.COPY_SHARE:.0%}"
    for name in ("GUIDE.md", "SECURITY.md", "setup_assistant.md"):
        flat = prose(ROOT / name)
        assert copies in flat and shares in flat, name  # the copy check, in the numbers the code uses


def test_the_guide_covers_youtube_from_setup_to_security() -> None:
    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    upgrade = re.search(r"^\| Upgrade \| `([^`]+)`", guide, re.M)
    assert upgrade and "(cd tools/youtube && uv sync --frozen)" in upgrade.group(1)
    assert "cd tools/youtube && uv run pytest -q" in guide.split("## 6. Tests", 1)[1].split("\n## ", 1)[0]
    security = prose(ROOT / "setup_assistant.md").split("8. Security", 1)[1]
    assert "https://www.youtube.com" in security and "never the link" in security
    assert "deploy again" in prose(ROOT / "setup_assistant.md").split("2.7 Always on", 1)[1].split("2.8 Keep", 1)[0]
    assert "cd tools/youtube && uv sync --frozen" in (ROOT / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8")


def test_security_md_names_the_youtube_helper_and_its_gaps() -> None:
    security = prose(ROOT / "SECURITY.md")
    assert "`tools/youtube/`" in security.split("Out of scope", 1)[0]
    gaps = security.split("Known accepted gaps", 1)[1]
    for words in (
        "YouTube's terms forbid automated access",
        "accepted for personal use",
        "undocumented",
        "youtube-transcript-api 1.2.4",
        "downloading its content",
        "still show YouTube working",  # a changed YouTube can look like an answer
    ):
        assert words in gaps, words
    assert "no key of its own" in security and "local-assistant youtube helper" in security


# owner, 14 Oct: /babble offers eight languages in all (babble.OFFERED), so the README's row names seven others
NUMBER_WORDS = {7: "seven", 8: "eight", 9: "nine"}


def test_the_practice_languages_are_counted_as_babble_offers_them() -> None:
    from local_assistant import babble

    total = len(babble.OFFERED)
    for readme in (prose(ROOT / "README.md"), prose(GUIDE)):  # the front page's line and the guide's row
        assert f"A short conversation in Dutch (or one of {NUMBER_WORDS[total - 1]} other languages)" in readme  # site
        assert f"one of {NUMBER_WORDS[total]} other languages" not in readme
    assert (babble.__doc__ or "").startswith(f"Language practice (/babble): a session in one of {NUMBER_WORDS[total]} ")
    assert f"- **{NUMBER_WORDS[total].capitalize()} languages:**" in GUIDE.read_text(encoding="utf-8")


# owner, 14 Oct: the Mac's security has two levels, by name in every doc and in harden, doctor and setup: hardened
# (setup's security step) and nuclear (--nuclear); never "default"
SECURITY_LEVELS = (
    "The Mac's security has two levels: hardened, set by the security step (`scripts/harden.sh`), and nuclear, the "
    "hardened level with the Mac's firewall on and python3 and ruby off its list (`scripts/harden.sh --nuclear`; its "
    "stealth mode and keychain lock do not count toward the level). Doctor names the level the Mac is at."
)


@pytest.mark.parametrize("name", ["GUIDE.md", "setup_assistant.md", "SECURITY.md", "SETUP-WITH-YOUR-AI.md"])
def test_the_docs_name_the_two_security_levels_in_the_same_words(name: str) -> None:
    text = prose(ROOT / name)
    assert SECURITY_LEVELS in text
    never = r"default (security|harden|step)|(security|harden)\w* (level|step)[^.]{0,30}\bdefault\b"
    assert not re.search(never, text, re.IGNORECASE)  # "--scrub-log at the default level" is signal-cli's log level


def test_the_code_names_the_hardened_step_never_a_default_one() -> None:
    for name in ("src/local_assistant/harden.py", "src/local_assistant/hostsec.py"):
        assert "default step" not in (ROOT / name).read_text(encoding="utf-8"), name


def test_the_docs_say_what_she_can_do_comes_from_codes_list() -> None:
    """Owner, 14 Oct: "what can you do?" is answered from the list of the parts on in this install; /help shows it."""
    readme = prose(GUIDE)
    assert '"What can you do?" is answered from a list of the parts set up in this install' in readme
    assert "a part not set up is left out" in readme
    assert "the messages she starts (on or off, listed either way)" in readme
    assert "`/help` shows the same list, word for word, above the commands." in readme
    for levels in ("auto, on or off", "only speech or any sound", "off, summary, docs or code", "hardened or nuclear"):
        assert levels in readme, levels
    assert "Documents (on or off, with its folder)" in readme  # the documents merge: an extra, listed only when on
    guide = prose(ROOT / "setup_assistant.md")
    assert "**What she can do.** The code builds a list of the parts that are set up in this install" in guide
    assert "web search, places and routes, Projects, Documents, the Mac, language practice and security" in guide
    assert (
        "Documents (on or off, with its folder: `[documents]`; a folder in your home is shown from `~`, as "
        "`~/Estia-KB`, any other written out in full)" in guide
    )
    assert "not hardened yet" in guide and "not read yet" in guide
    assert '"not read yet" before the first read or when one of them could not be read' in " ".join(guide.split())


# the Gladys batch (owner, 13-14 Oct): what was built, in the docs a person reads, in the words the code uses


def test_the_docs_quote_the_line_for_a_mailbox_not_read() -> None:
    """G1: one line per mailbox not read, in the digest and the mail tools; the call's start only counts and times."""
    from local_assistant import prompts

    line = prompts.MAIL_STALE.format(name="work", when="Mon 09:10", reason=prompts.MAIL_STALE_REASONS["sign_in"])
    glance = prompts.GLANCE_STALE.format(count=1, es="", when="Mon 09:10")
    assert line == "[work] not read since Mon 09:10: needs sign-in." and glance == "1 mailbox not read since Mon 09:10."
    readme = prose(GUIDE)
    mail = readme.split("Mail comes from the Exchange (Microsoft 365) mailboxes", 1)[1].split(
        "Gmail is not built in", 1
    )[0]
    assert f'"{line}"' in mail and "The rest of the digest stays." in mail and f'("{glance}")' in mail
    off = GUIDE.read_text(encoding="utf-8").split("## When something is off", 1)[1].split("\n## ", 1)[0]
    assert '| "[work] not read since Mon 09:10: …" in the digest |' in off
    assert line in prose(ROOT / "setup_assistant.md") and glance in prose(ROOT / "setup_assistant.md")
    example = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    sync = example.split("#sync_minutes = 5", 1)[1].split("\n#first_sync_days", 1)[0]
    assert "not read for 2 rounds" in sync


def test_the_docs_say_doctors_always_on_line_means_alive() -> None:
    """G4: doctor's Always on line, with the words doctor prints, where a person and an installing AI look."""
    from local_assistant import prompts

    silent = prompts.DOCTOR_ALWAYS_ON_SILENT.format(minutes=12)
    restarting = prompts.DOCTOR_ALWAYS_ON_RESTARTING.format(code=1)
    for name in ("GUIDE.md", "SETUP-WITH-YOUR-AI.md", "setup_assistant.md"):
        text = prose(ROOT / name)
        assert f'"{silent}"' in text and f'"{restarting}"' in text, name
        assert "Doctor only reads" in text, name
    off = GUIDE.read_text(encoding="utf-8").split("## When something is off", 1)[1].split("\n## ", 1)[0]
    assert "| Doctor's Always on line shows ✗" in off
    for name in ("SETUP-WITH-YOUR-AI.md", "setup_assistant.md"):  # installed alone is never ✓ (review of 14 Oct)
        assert f'○ "{prompts.DOCTOR_ALWAYS_ON_UNREAD}"' in prose(ROOT / name), name


def test_the_docs_never_say_every_cross_ends_with_a_fix_alone() -> None:
    """Review of 14 Oct: Always on's ✗ for a silent or restarting bridge prints a log to read, not a fix."""
    for name, said in (
        ("GUIDE.md", "Each ✗ line gives the reason and the command that fixes it, or the log to read first"),
        ("GUIDE.md", "prints the fix for each ✗, or the log to read first"),
        ("SETUP-WITH-YOUR-AI.md", "Every ✗ line ends with the command that fixes it, or the log to read first."),
        ("SETUP-WITH-YOUR-AI.md", "fix every ✗ with the command it prints; for a log it names,"),
        ("setup_assistant.md", "Each ✗ line names the fix, or the log to read first;"),
    ):
        assert said in prose(ROOT / name), (name, said)
    readme = GUIDE.read_text(encoding="utf-8")
    row = readme.split("| Doctor's Always on line shows ✗", 1)[1].split("\n", 1)[0]
    assert '"installed, stopped"' in row and "her restart" in row and "the command that loads her service again" in row


def test_the_docs_name_the_marks_on_todays_agenda() -> None:
    """G5: today's events carry code's mark after their times."""
    marks = '"(over)", "(now, until 15:00)" or "(in 40 min)"'
    readme = prose(GUIDE)
    day = readme.split("One calendar or several can be read", 1)[1].split("A Google calendar's secret link", 1)[0]
    assert f"When she looks up today, its timed events carry a mark from the code after their times: {marks}" in day
    assert marks in prose(ROOT / "setup_assistant.md")


# review of 14 Oct: a calendar change does start a note (calendar_watch), written by code with no model; a document is
# outside text too (the documents merge), and a new PDF someone else puts in the folder starts the documents' note,
# which names it ("I found 2 new documents: lease.pdf, …"), also written by code with no model
HOLD_RULE = (
    "Outside text may hold her back, never start anything but a note the code writes: a mail, a calendar entry, a "
    "page, a search or map result, YouTube captions, a project file, a document, a photo or a shared audio file can "
    "pause the music or turn it down, make a device change wait for your yes, or fill a note the code writes (the "
    "digest; a calendar change, or a new PDF in the documents folder, starts one); it never starts a message the model "
    "writes, a call, a reminder or a device change."
)


def test_outside_data_may_hold_her_back_and_never_start_anything() -> None:
    """G6: one written rule in AGENTS.md, and the same plain words in the threat model."""
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    rules = agents.split("## Rules (safety)", 1)[1].split("\n## ", 1)[0]
    rule = rules.split("- Outside data may hold her back, never start anything but a code-written note", 1)[1]
    rule = rule.split("\n- ", 1)[0]
    for words in (
        "a calendar change starts one of the calendar watch's notes, and a new PDF in the folder the documents' note "
        "naming it, with no model",
        "never starts a Gemma-written message, a call, a reminder or a device change",
        "hold, pass or retry",
    ):
        assert words in rule, words
    security = " ".join((ROOT / "SECURITY.md").read_text(encoding="utf-8").split())
    assert HOLD_RULE in security.split("## What the project tries to protect", 1)[1].split("## If the Mac", 1)[0]
    guide = " ".join((ROOT / "setup_assistant.md").read_text(encoding="utf-8").split())
    assert HOLD_RULE in guide.split("## 8. Security", 1)[1]


# review of 14 Oct (callfix): how a call ends, and a listener's "okay", in the same words in GUIDE.md and the guide.
# Leave-taking comes right after the goodbye it is like: a wrap-up is the one that a question of hers keeps open
CALL_ENDS = (
    'A listener\'s "okay", "yeah" or "mm-hm" alone, in the call\'s language, does not stop her either: she carries on, as '
    "after a cough, unless she just asked something.",
    'In English, a goodbye ("bye-bye", "good-bye", "talk to you later", "take care") ends the call once her answer is '
    'spoken, also with closing words after it ("okay, goodbye, I\'m going to check the log now") and also when her '
    "answer asks something; only a device question waiting for a yes keeps the call open.",
    'Leave-taking ends the call the same way, as a phrase of its own ("Okay, I have to go"), not inside a longer '
    'sentence ("I have to go to the dentist"): "I have to go" or "I\'m hanging up" in English, "ik moet gaan" in '
    'Dutch, "ich muss los" in German, and a few clear phrases in the other languages ("je dois y aller", '
    '"じゃあ、切るね"). A wrap-up ("that\'s all") ends it too, unless she asks something.',
)


@pytest.mark.parametrize("name", ["GUIDE.md", "setup_assistant.md"])
def test_the_docs_say_how_a_call_ends_in_the_same_words(name: str) -> None:
    text = prose(ROOT / name)
    for words in CALL_ENDS:
        assert words in text, (name, words)


def test_the_guide_says_how_a_call_ends_in_its_calls_section() -> None:
    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    calls = guide.split('**A listener\'s "okay".**', 1)[1].split("**Wake-up calls.**", 1)[0]
    assert "**Goodbyes.**" in calls
    flat = " ".join(calls.split())
    assert CALL_ENDS[1] in flat and CALL_ENDS[2] in flat


DEVICE_CLAIM = "an answer that says a light or plug was switched, dimmed or set while it was not changed gets"


def test_the_docs_quote_the_note_after_a_device_claim() -> None:
    """G7: code's line "Nothing was switched." after a claimed switch with no device change ("Nothing else was
    switched." when the answer switched another one); English first; a plain report of the state is never flagged."""
    from local_assistant import prompts

    assert prompts.DEVICE_GUARD == "Nothing was switched."
    assert prompts.DEVICE_GUARD_ELSE == "Nothing else was switched."
    for name in ("GUIDE.md", "SECURITY.md", "setup_assistant.md"):
        text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
        assert DEVICE_CLAIM in text.lower(), name
        for said in (prompts.DEVICE_GUARD, prompts.DEVICE_GUARD_ELSE, "a plain report of a device's state"):
            assert said in text, (name, said)
    readme = prose(GUIDE)
    lights = readme.split("Each device has one rule:", 1)[1].split("**Only Kasa devices are tested.**", 1)[0]
    assert "In English, an answer that says" in lights
    assert 'a plain report of a device\'s state ("the lamp is on") is never flagged' in lights


def test_security_notes_go_at_once_and_health_alerts_wait_for_quiet_hours_to_end() -> None:
    """Q1: the Mac's health alerts wait for the end of quiet hours; security notes never do."""
    never = "Security notes (a new linked device on her account, the model paused or a cloud model) are never held."
    for name in ("SECURITY.md", "setup_assistant.md"):
        assert never in " ".join((ROOT / name).read_text(encoding="utf-8").split()), name
    example = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    health = example.split("\n[health]", 1)[0].rsplit("\n\n", 1)[1]
    assert "In quiet hours" in health and "wait until they end" in health
    quiet = example.split('#quiet_hours = "22:00-08:00"', 1)[1].split("\n#lookback_time", 1)[0]
    assert "health alerts" in quiet and '"" for no quiet hours for her messages' in quiet and "22:00-08:00" in quiet


def test_the_guide_says_a_slash_command_is_spoken() -> None:
    """C5(c): a command in a spoken answer is said as "slash reminders", never left out like a path."""
    from local_assistant import voice

    assert voice.spoken_text("Use /reminders to see them.") == "Use slash reminders to see them."
    voice_notes = prose(ROOT / "setup_assistant.md").split("Voice notes (in the current language):", 1)[1][:900]
    assert 'a command such as /reminders is said as "slash reminders"' in voice_notes


# ------------------------------------------------------------------ P1: what the website quotes
# the site estia.fun quotes these word for word: changing one means telling the site session
# (its drift test, tests/drift.test.mjs in the site's own repo, reads them with ESTIA_REPO=<this checkout>: README.md and
# GUIDE.md as written, SECURITY.md and setup_assistant.md with their line breaks read as spaces; the lists are the site
# session's asks of 14 Oct, 03:00 and 06:40, and the Projects strings of 13 Oct; the interrupt sentence and the practice
# row in their form of 14 Oct). Since the split (owner, 15 Oct) a SITE_README sentence may live in README.md, the
# front page, or GUIDE.md, the rest, as its guide-page drift test reads them (the two files together)
SITE_README = (
    "git clone https://github.com/enable4all/estia-on-mmini.git ~/estia && cd ~/estia",
    "**A private AI thinking partner living at home, on your own Mac.**",
    "Website: [estia.fun](https://estia.fun)",
    '<img src="docs/brand/estia-hero-light.png" alt="A stone garden seen from above, its gravel raked in rings around a '
    'Mac mini"',
    "Name her what you like. Ours is called Tiffany.",
    NOT_AFFILIATED,  # owner, 15 Oct: every company or service the two files name (was: "… Bose, TP-Link or OpenClaw.")
    "- **Local.**",
    "- **Read-only mail and calendars.**",
    "- **Lights and plugs follow a rule set per device.**",
    "- **Only Signal.**",
    "- **Keys stay put.**",
    "Chats, memory and profile stay on the Mac.",
    "the bridge listens on nothing",
    "Signal Desktop on your laptop",
    'turn on "Always relay calls" in Signal on your phone',
    "never on a program's command line, where other programs on the Mac can read them",
    "Only her own hidden macOS account, `_estia`, can read the keys",
    "16 GB of memory or more",
    "macOS 14 or later",
    "macOS 15.6 or later",
    "She answers first. On a call, when you think aloud, she may ask one question back.",
    "| YouTube videos | Send a YouTube link, with or without a question | A summary from the video's captions, with the "
    "time of each main point. Follow-up questions by text about the last video, within 10 minutes, need no new link. "
    "The captions are never stored. |",
    "the Outlook calendars of the Microsoft 365 mailboxes she already reads",
    "`Calendars.Read`",
    "never make a private calendar public for her",
    "Mail syncs every five minutes in the background.",
    "Gmail is not built in",
    "Music is text only. Spotify's rules don't allow voice control",
    "Only Kasa devices are tested",
    "A long video is read in parts, up to its first 2 hours",
    "The captions are kept in memory only, never on disk, and are gone after those 10 minutes",
    "with the video's ID (the 11-character code in the link), never the whole link.",
    'With `interrupt = "only speech"` in `[calls]` (the default), a speech detector on the Mac (Silero VAD) decides what '
    "stops her, so road noise rarely cuts her off; until it is ready, any loud sound does.",
    "Her own voice from a loud speaker next to the microphone (a laptop on Signal Desktop) is told apart from the "
    "caller's and does not stop her",
    "**A reminder is set only when a note from the code, not from the AI, says so.**",
    "**For anything that matters, keep your phone's own alarm.**",
    "It is off until you do",
    "At most one a day, never between 22:00 and 08:00",
    "A short conversation in Dutch (or one of seven other languages) at level A2 or B1",
    "### Projects (extra, off by default)",
    "Setup asks whether she should see projects on this Mac, which folder, and how much.",
    "Only files git tracks, never secret-like names such as `.env`, never outside the folder you named.",
    "With Projects on, she reads only files git tracks in the folder you named",
    "| Projects | `[projects]` | Nothing to install: setup asks the folder and the level |",
    "| Health checks and project reading |",
    "Estia listens, thinks and speaks in **one language at a time**.",
    "`/babble nl` starts practice in Dutch: a short conversation at your level, and a recap of your mistakes when it "
    "ends.",
    "**Eight languages:** English, Dutch, German, Spanish, French, Portuguese, Japanese and Italian",
    "No pronunciation scoring",
    "- **Only the devices she knows.**",
    "- **An emergency stop on your phone.**",
    "`/lock` stops her answers and her calls until `/unlock` comes from the same device after a short wait. It holds "
    "after a restart and needs no Mac.",
    "- **Her own account on the Mac.**",
    "She runs as `_estia`, a hidden macOS account with no password and no login.",
    "as long as no admin rule skips the password",
    # unpinned (owner, 15 Oct): "type `/usr/bin/sudo`" and "the update shows the list of changes and the files they
    # touch, not the code itself" left README.md and GUIDE.md for SECURITY.md's known gaps
    "- **The model stays on the Mac.**",
    "- **A security step in setup.**",
    "**Outside text is read as information, not as orders.** That covers web pages, YouTube captions, search and map "
    "results, mail,",
    "Code rules reduce prompt injection (orders hidden in that text) but cannot rule it out.",
    "Mail, calendar entries, YouTube captions, web and map results never teach her anything.",
    "**Reminders come only from you.**",
    "never because a mail, a page, a video, a calendar entry or a shared audio file",
    "**When the AI writes part of a message she starts, it sees only your words, your profile and the moment.** Never "
    "mail, calendar entries, the web or a page.",
    "Calendar notes are written by the code from the calendar",
    "The words that ask for a code or a password, and claims of a reminder or a call, are checked in all 11 languages.",
    "Outside services are reached only for Signal, links you send and the extras you switch on.",
    "| Microsoft, to read mail and Outlook calendars (read-only) |",
    "| Google's calendar links, for Google calendars (read-only) |",
    "| | YouTube, for the captions of a video link you send |",
    "with Homebrew's usage reports (analytics) off",
    "fixed to one version and checked against their fingerprints on the Mac at every start: a changed or extra file is "
    "never loaded",
    "Whisper then starts with no network",
    "are downloaded as published, with no fixed version and no check of their files",
    "| She says a video has no captions | The video has none on YouTube | Nothing to fix: a video is never transcribed "
    "on the Mac |",
    "| She says YouTube is refusing requests from this Mac",
    "No proxy is used, so there is no way around a block",
    '| She says the YouTube helper is not set up, or doctor shows "! YouTube"',
    "○ set up but not tested yet",
    "| `/fix N new text` |",
    "asks for her name (Enter keeps Estia)",
    "Setup is done when `/usr/bin/sudo /usr/local/estia/bin/estia doctor` shows no ✗ and her welcome message has arrived "
    "on your phone",
    "| AgentJev | Optional: a cheap yes/no check that marks obvious bulk mail as noise before Gemma.",
    "- Terminal chat: talk to her from a terminal on the Mac.",
    # review of 14 Oct: every other whole sentence or table cell the page (site main 4de88c1) quotes word for word
    "A device change after outside text waits for your yes.",
    "Signal itself still confirms delivery, so a stranger can tell the number exists.",
    "FileVault, automatic login, the screen lock and a few more are shown with their fix, never changed.",
    "Calls go through Signal's relay servers, so your phone never learns the Mac's internet (IP) address.",
    "Messages travel end-to-end encrypted through Signal.",
    "After a restart she skips messages older than ten minutes and tells you she was offline, so you can ask again.",
    "Signal's terms discourage automated accounts, and Signal can disable one: keep her to your own chats, at a low "
    "volume, on a number of her own.",
    "If that fails, the Mac itself is off or offline.",
    "She never runs as you or as root.",
    "It asks for your Mac password once.",
    "The voice models come from Hugging Face, an online library of AI models.",
    "The model always runs on the Mac.",
    "Open to-do items and recent changes from a project on the Mac.",
    "`/devices ok N`, sent from a device she already knows, lets it in.",
    "After an install, a move or a macOS update, allow up to about 15 minutes.",
    "Her model's name in Ollama now points to a cloud model, which runs on Ollama's servers",
    "Save the new address with `scripts/set-calendar-url.sh` (with the calendar's name, if it has one)",
    "Run `scripts/mail-login.sh <address>` again",
    "Log in to the Mac remotely (SSH) and run `/usr/bin/sudo /usr/local/estia/bin/estia check`, then "
    "`/usr/bin/sudo /usr/local/estia/bin/estia logs`.",
    "Start it with `brew services start ollama`",
    "The AI model (Gemma, run by the Ollama app)",
    "Signal, for your messages and calls (end-to-end encrypted)",
    "Ollama's web search and Brave Search",
    "Web pages: links you send her, and the top result of a Brave search",
    "Speech-to-text and text-to-speech",
    "Lights and plugs (home network)",
    "Send a photo with a question",
    "root, the Mac's all-powerful admin account.",
    "each shown with its state, and changed only on your yes",
    "encrypted texts, voice notes and calls.",
    "`/reminders` lists and cancels them.",
    "A few lines on its health.",
    "The Mac is checked every four hours.",
    "Two things: a team call at 10:00, and dinner at 19:00.",
    "A cloud model, which Ollama runs on its own servers, is refused: by its name when the config is read, and by "
    "Ollama's own list at the start, every five minutes and before every chat.",
    "it cannot type commands on the Mac.",
    "Setup also downloads the model",
    "It runs Homebrew, the installer for Mac tools, with Homebrew's usage reports",
    "Try a video you know has captions.",
    '| "A message came from a new linked device N and was held." |',
    "Yours: send `/devices ok N` from your phone.",
    "| No answer after `/lock` | The emergency stop is on | Send `/unlock` from the device that sent `/lock`, after the "
    "wait it names |",
    '| "Wake-up: 07:00 … No call: …" |',
    "voice still loading, calls off, a call already running, or three calls that day",
    "A message you didn't ask for, ending",
    "(… · /quiet to pause)",
    "One of the messages she starts herself",
    "what is set up, something to try, and what stays on the Mac.",
)
SITE_LANGUAGES = (
    "English",
    "Spanish",
    "Italian",
    "Korean",
    "Portuguese",
    "Dutch",
    "Polish",
    "German",
    "Japanese",
    "Russian",
    "French",
)
SITE_COMMANDS = (  # her commands the page shows: each one stays in README.md or GUIDE.md
    "/usr/bin/sudo /usr/local/estia/bin/estia doctor",
    "/usr/bin/sudo /usr/local/estia/bin/estia check",
    "/usr/bin/sudo /usr/local/estia/bin/estia logs",
    "/usr/bin/sudo /usr/local/estia/bin/estia rollback",
    "/usr/bin/sudo /usr/local/estia/bin/estia-deploy",
)
SITE_SLASH = (
    "/babble",
    "/devices",
    "/fix",
    "/forget",
    "/initiative",
    "/language",
    "/lock",
    "/profile",
    "/quiet",
    "/reminders",
    "/status",
    "/unlock",
)
SITE_SECURITY = (
    "One user per Mac is assumed.",
    "A voice note forwarded inside Signal keeps its voice-note flag, so it counts as your own words.",
    "YouTube's terms forbid automated access and downloading its content, captions included, with no exception for "
    "personal use",
    "two reads from a home connection worked, repeated use is untested",
    "No proxy is used, so a block stops video links until YouTube lifts it",
    "relies on an undocumented YouTube endpoint",
    "`/status` and doctor still show YouTube working",
    "a redirect to consent.youtube.com is refused by the host check and the read fails",
    "The model always runs on the Mac. A cloud model, which Ollama runs on its own servers, is refused:",
    "a made-up example is enough.",
    "the security step removes such a rule.",
)
SITE_SETUP = (  # setup_assistant.md (was SITE_GUIDE: GUIDE.md is the README's body now)
    "When a release changes a root-run tool (`scripts/estia`, `scripts/estia-deploy`, `deploy.py`, `ownuser.py`, "
    "`hostsec.py`, a service template), the deploy says so and keeps the installed one.",
    "deploy again after `--update-tools`",
    "The model always runs on the Mac.",
    "no fixed version and no check of their files.",
    "Yours: send `/devices ok N` from your phone.",
)


def site_flat(path: Path) -> str:
    """The file's text with every run of white space read as one space, as the site's drift test reads it."""
    return " ".join(path.read_text(encoding="utf-8").split())


def site_docs(root: Path) -> str:
    """README.md and GUIDE.md as written, one after the other, as the site's guide-page drift test reads them."""
    return "\n".join((root / name).read_text(encoding="utf-8") for name in ("README.md", "GUIDE.md"))


def site_misses(root: Path) -> list[str]:
    """Every sentence, command and language the site quotes that the docs under `root` no longer hold as written."""
    readme = site_docs(root)
    security, guide = site_flat(root / "SECURITY.md"), site_flat(root / "setup_assistant.md")
    missing = [f"README.md or GUIDE.md: {said}" for said in (*SITE_README, *SITE_COMMANDS) if said not in readme]
    table = readme.split("## Slash commands", 1)[1].split("\n## ", 1)[0]
    missing += [f"GUIDE.md, slash commands: {command}" for command in SITE_SLASH if f"`{command}" not in table]
    languages = readme.split("## Languages", 1)[1].split("\n\n")[2]
    rows = re.findall(r"^\| ([^|]+?) \| `[a-z]{2}` \|", languages, flags=re.MULTILINE)
    named = [cell.split("(", 1)[1].rstrip(")") if "(" in cell else cell for cell in rows]
    if named != list(SITE_LANGUAGES):
        missing.append(f"GUIDE.md, languages: {named}")
    missing += [f"SECURITY.md: {said}" for said in SITE_SECURITY if said not in security]
    if "\n## Known accepted gaps\n" not in (root / "SECURITY.md").read_text(encoding="utf-8"):
        missing.append("SECURITY.md: ## Known accepted gaps (the site links #known-accepted-gaps)")
    missing += [f"setup_assistant.md: {said}" for said in SITE_SETUP if said not in guide]
    upgrade = re.search(r"^\| Upgrade \|(.+)$", (root / "setup_assistant.md").read_text(encoding="utf-8"), re.MULTILINE)
    deploy = "In her own user: `git pull` in your checkout, then `/usr/bin/sudo /usr/local/estia/bin/estia-deploy`"
    if not upgrade or deploy not in upgrade.group(1):
        missing.append(f"setup_assistant.md, Upgrade row: {deploy}")
    return missing


def test_the_docs_keep_every_sentence_the_site_quotes_word_for_word() -> None:
    """A changed sentence turns the site's drift test red: change it here on purpose, and tell the site session (old
    and new)."""
    assert site_misses(ROOT) == []


# owner, 14 Oct: documents, an extra (off without [documents]). The docs say the code's facts in the same words: her
# access to that one folder, where a PDF is read, the owner's limits, and the limits measured in tools/documents/README.md
DOCUMENTS_ACCESS = "In your home, her account adds files only to `~/Estia-KB`, and never deletes or changes yours."
# owner, 14 Oct (live: macOS refused even the owner's SSH session a change to ~/Documents' access entries): why the
# folder is ~/Estia-KB, said where the folder is introduced
DOCUMENTS_WHY_HOME = "macOS guards Documents, Desktop and Downloads; a folder in your home needs no extra permission."
# SETUP-WITH-YOUR-AI.md speaks to the installing AI: "your home" there would be the AI's
DOCUMENTS_WHY_HOME_PERSON = (
    "macOS guards Documents, Desktop and Downloads; a folder in the person's home needs no extra permission."
)
# the share's .git refusal, said as it is: checked when the share runs (SECURITY.md's known gaps say the rest)
DOCUMENTS_NO_CHECKOUT = (
    "The share refuses a folder that holds a git checkout (`.git`): her account would add files to code you run."
)
DOCUMENTS_PARSING = (
    "A PDF is read by Apple's PDFKit and Vision in a helper program that runs as her account: a PDF made to attack "
    "the reader reaches only that program and her account, never yours."
)
DOCUMENTS_LIMITS = "25 MB and 300 pages a document; 60 seconds to read it; one document at a time."
TWO_COLUMNS = "A PDF with its own text is read one column after the other, left first."
SCANNED_COLUMNS = (
    "Scanned pages in two columns: of 18 test layouts, 14 were read with at most 2 letters wrong, 1 mixed the lines of "
    "its two columns (set close together) and 3 lost words (10-point type)."
)
TABLES = (
    "A table comes out as lines: one line per row when the PDF was written row by row (Word, Excel), else one cell per "
    "line, column after column, as for every scanned table."
)
DOCUMENTS_FOUND_EXAMPLE = "I found 2 new documents: lease.pdf, boiler-manual.pdf."


@pytest.mark.parametrize(
    ("name", "facts"),
    [
        (
            "GUIDE.md",
            (
                DOCUMENTS_ACCESS,
                DOCUMENTS_WHY_HOME,
                DOCUMENTS_NO_CHECKOUT,
                DOCUMENTS_PARSING,
                DOCUMENTS_LIMITS,
                TWO_COLUMNS,
                SCANNED_COLUMNS,
                TABLES,
            ),
        ),
        (
            "setup_assistant.md",
            (
                DOCUMENTS_ACCESS,
                DOCUMENTS_WHY_HOME,
                DOCUMENTS_NO_CHECKOUT,
                DOCUMENTS_PARSING,
                DOCUMENTS_LIMITS,
                TWO_COLUMNS,
                SCANNED_COLUMNS,
                TABLES,
            ),
        ),
        ("SECURITY.md", (DOCUMENTS_ACCESS, DOCUMENTS_WHY_HOME, DOCUMENTS_NO_CHECKOUT, DOCUMENTS_PARSING)),
        ("config.example.toml", (DOCUMENTS_LIMITS, DOCUMENTS_WHY_HOME)),
        ("SETUP-WITH-YOUR-AI.md", (DOCUMENTS_WHY_HOME_PERSON,)),
    ],
)
def test_the_docs_say_the_documents_facts_in_the_same_words(name: str, facts: tuple[str, ...]) -> None:
    text = prose(ROOT / name)
    for fact in facts:
        assert fact in text, (name, fact)


def test_the_ai_guide_names_the_persons_home_and_no_claim_outruns_the_git_check() -> None:
    """SETUP-WITH-YOUR-AI.md speaks to the AI ("your home" would be its own); SECURITY.md never says her account can
    never add files to code you run: the share looks for a .git only when it runs, which its known gaps say."""
    assert DOCUMENTS_WHY_HOME not in prose(ROOT / "SETUP-WITH-YOUR-AI.md")
    security = prose(ROOT / "SECURITY.md")
    assert "never adds files to code you run" not in security
    gaps = security.split("Known accepted gaps", 1)[1]
    assert "only when it runs" in gaps and "Keep no code in `~/Estia-KB`." in gaps


def test_the_documents_limits_match_the_code() -> None:
    from local_assistant import documents, prompts

    assert (documents.MAX_BYTES, documents.MAX_PAGES, documents.MAX_SECONDS) == (25_000_000, 300, 60)
    assert "25 MB" in prompts.DOCUMENT_ERRORS["too_big"] and "300 pages" in prompts.DOCUMENT_ERRORS["too_many_pages"]
    assert "60 seconds" in prompts.DOCUMENT_ERRORS["timeout"]
    found = prompts.DOCUMENTS_FOUND.format(count=2, documents="documents", names="lease.pdf, boiler-manual.pdf")
    assert found == DOCUMENTS_FOUND_EXAMPLE  # the note the docs quote, as the code writes it


def test_the_readme_has_documents_in_what_she_can_do_and_its_own_section() -> None:
    from local_assistant import prompts

    readme = GUIDE.read_text(encoding="utf-8")
    table = readme.split("## What she can do", 1)[1].split("\n### ", 1)[0]
    assert "\n| Documents |" in table
    section = readme.split("### Documents (extra, off by default)", 1)[1].split("\n## ", 1)[0]
    assert "scripts/share-documents.sh" in section and "--revoke" in section
    assert DOCUMENTS_FOUND_EXAMPLE in prose(GUIDE)
    commands = readme.split("## Slash commands", 1)[1].split("\n## ", 1)[0]
    assert "| `/docs`" in commands and "`/docs send N`" in commands
    assert "| Documents | `[documents]` |" in readme  # set it up, step 7
    runs = readme.split("## What runs where", 1)[1].split("\n### ", 1)[0]
    assert "`tools/documents/`" in runs
    off = readme.split("## When something is off", 1)[1].split("\n## ", 1)[0]
    assert prompts.DOCS_CLOSED.split(":", 1)[0] in off and "scripts/share-documents.sh" in off


def test_the_guide_covers_documents_from_setup_to_security() -> None:
    from local_assistant import prompts

    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    section = guide.split("### Documents (extra, off by default)", 1)[1].split("\n### ", 1)[0]
    for command in ("scripts/share-documents.sh --dry-run", "scripts/share-documents.sh --revoke", "look_minutes"):
        assert command in section, command
    flat = prose(ROOT / "setup_assistant.md")
    assert DOCUMENTS_FOUND_EXAMPLE in flat
    assert prompts.DOCTOR_DOCUMENTS_REFUSED.split(": ", 1)[1] in flat  # doctor's EPERM line, as the code writes it
    assert prompts.DOCTOR_DOCUMENTS_UNSHARED.split("{folder} ", 1)[1] in flat
    assert prompts.DOCTOR_DOCUMENTS_NOT_KB.split("{folder} ", 1)[1] in flat  # her config still names another folder
    assert prompts.DOCTOR_DOCUMENTS_REFUSED_KB_FIX.split(":", 1)[0] in flat  # ~/Estia-KB itself refused
    ai_guide = prose(ROOT / "SETUP-WITH-YOUR-AI.md")
    assert prompts.DOCTOR_DOCUMENTS_NOT_KB.split("{folder} ", 1)[1] in ai_guide
    assert "is not yours (her account may have added it)" in ai_guide  # the share's .git of hers
    operations = guide.split("## 7. Operations", 1)[1].split("\n## ", 1)[0]
    assert "\n| Documents |" in operations and "scripts/share-documents.sh" in operations
    remembers = guide.split("## 5. What it remembers", 1)[1].split("\n## ", 1)[0]
    assert "`doc_files`" in remembers and "`doc_pages`" in remembers
    commands = guide.split("## 4. Using it", 1)[1].split("\n## ", 1)[0]
    assert "| `/docs`" in commands
    security = guide.split("## 8. Security", 1)[1]
    assert "Documents" in security and "`O_NOFOLLOW`" in security


def test_security_md_names_the_documents_helper_and_its_gaps() -> None:
    security = prose(ROOT / "SECURITY.md")
    assert "`tools/documents/`" in security.split("Out of scope", 1)[0]
    gaps = security.split("Known accepted gaps", 1)[1]
    assert "privacy protection" in gaps and "untested" in gaps
    assert "sandbox" in gaps  # the helper runs without its own sandbox yet


@pytest.mark.parametrize("name", ["README.md", "GUIDE.md", "setup_assistant.md", "SECURITY.md"])
def test_nothing_else_in_your_home_is_said_only_with_the_documents_folder_beside_it(name: str) -> None:
    """With [documents] on, her user also reads ~/Estia-KB: no doc may say "nothing else" without it."""
    text = prose(ROOT / name)
    for found in re.finditer(r"nothing else in your home|never lists or reads anything else", text):
        near = text[max(0, found.start() - 500) : found.end() + 200]
        assert "`~/Estia-KB`" in near, (name, near)


# owner, 14 Oct: the documents folder moved out of ~/Documents (macOS guards Documents, Desktop and Downloads: live,
# the share could not open ~/Documents to her), and then from a folder named Estia in the home (on a Mac's usual disk,
# which ignores case, the README's clone ~/estia) to ~/Estia-KB. No file of the code or the docs names an old one, but
# AGENTS.md's history note. The patterns are written so that a grep for the old names does not find them here
MOVED_FROM = re.compile(
    r'Documents[/]Estia|"Documents" / "Estia"|Path\("Documents"\)|~[/]Estia(?![-\w])|[/]Estia"|/ "Estia"'
    r'|Path\("Estia"\)|\b(?:your|their) Estia folder'
)


def test_nothing_names_the_old_documents_folder() -> None:
    names = subprocess.run(
        ["git", "ls-files", "src", "scripts", "evals", "tests", "tools/documents/README.md", "docs/e2e-checklist.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    names += ["README.md", "GUIDE.md", "SECURITY.md", "setup_assistant.md", "SETUP-WITH-YOUR-AI.md", "AGENTS.md"]
    names += ["config.example.toml"]
    for name in names:
        path = ROOT / name
        if name == "tests/test_docs.py" or not path.is_file() or path.suffix in {".pdf", ".png", ".wav", ".db"}:
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            history = name == "AGENTS.md" and "~/estia" in line  # the gotcha that says why, by the clone's name
            assert history or not MOVED_FROM.search(line), (name, line[:200])


def test_the_online_list_has_no_icloud_row_for_documents() -> None:
    """~/Estia-KB is outside Desktop and Documents: iCloud Drive's Desktop and Documents Folders never take it."""
    readme = GUIDE.read_text(encoding="utf-8")
    online = readme.split("### What stays home, and what goes online", 1)[1].split("\n## ", 1)[0]
    assert "for the PDFs in" not in online
    section = prose(GUIDE).split("Documents (extra, off by default)", 1)[1].split("## Languages", 1)[0]
    assert (
        "`~/Estia-KB` is outside Desktop and Documents: iCloud Drive's Desktop and Documents Folders never send it to "
        "Apple's iCloud." in section
    )


def test_the_installing_ai_knows_documents_are_shared_by_the_person() -> None:
    text = prose(ROOT / "SETUP-WITH-YOUR-AI.md")
    assert "scripts/share-documents.sh" in text and "[documents]" in text


def test_the_readme_keeps_the_teaching_sentence_the_site_reads_and_names_documents() -> None:
    readme = GUIDE.read_text(encoding="utf-8")
    teach = readme.split("- **Only your words teach her.**", 1)[1].split("\n", 1)[0]
    assert "Mail, calendar entries, YouTube captions, web and map results never teach her anything." in teach  # site
    assert "Neither do documents." in teach


def test_the_readme_keeps_the_reminder_sentence_the_site_reads_and_names_documents() -> None:
    readme = GUIDE.read_text(encoding="utf-8")
    reminders = readme.split("- **Reminders come only from you.**", 1)[1].split("\n", 1)[0]
    assert (
        "never because a mail, a page, a video, a calendar entry or a shared audio file says so." in reminders
    )  # site
    assert "A document never sets one either." in reminders


# review, 14 Oct: what the documents docs must say plainly. Sent PDFs go into the folder itself (a folder her account
# made would be hers: in it she could rename or delete any file); a hard link is never shared; Gemma, not "AI", is
# what the look leaves out (Vision's text recognition is a model too); and the gaps: iCloud, the pass through
# ~/Documents by name, files of any kind a taken-over helper could leave, and doctor's read from your own session
DOCUMENTS_DOCS = (
    "README.md",
    "GUIDE.md",
    "setup_assistant.md",
    "SECURITY.md",
    "config.example.toml",
    "tools/documents/README.md",
)


@pytest.mark.parametrize("name", DOCUMENTS_DOCS)
def test_the_documents_docs_name_no_year_folder_and_no_ai_claim(name: str) -> None:
    text = prose(ROOT / name)
    for gone in ("<year>", "folder for the year", "year folders", "for a year", "2026/2026-10-14", "no AI model"):
        assert gone not in text, (name, gone)


def test_the_documents_gaps_are_named() -> None:
    gaps = prose(ROOT / "SECURITY.md").split("Known accepted gaps", 1)[1]
    for fact in ("passes through your home by name", "Gatekeeper", "SSH or Terminal session", "`EPERM`"):
        assert fact in gaps, fact
    assert "passes through `~/Documents`" not in gaps and "iCloud Drive's Desktop and Documents" not in gaps
    assert "hard link" in prose(ROOT / "SECURITY.md").split("Known accepted gaps", 1)[0]
    readme = prose(GUIDE).split("Documents (extra, off by default)", 1)[1].split("## Languages", 1)[0]
    assert "iCloud" in readme and "written in full" in readme and "SSH or Terminal session" in readme
    guide = prose(ROOT / "setup_assistant.md")
    assert "hard link" in guide and "mkdir -p ~/Estia-KB" in guide and "written out in full" in guide


def test_the_docs_say_what_a_caldav_calendar_reads_and_where_it_goes() -> None:
    """CalDAV calendars (owner, 14 Oct): read only, their password script, where their requests go; a line in the
    existing calendar sections, the facts in SECURITY.md."""
    readme = GUIDE.read_text(encoding="utf-8")
    assert "| | iCloud, Fastmail or your Nextcloud, for CalDAV calendars (read-only) |" in readme
    assert "iCloud, Fastmail or Nextcloud calendars through CalDAV, the calendar standard they share" in readme
    assert "iCloud, Fastmail or Nextcloud: `scripts/set-calendar-password.sh [name]`" in readme
    security = site_flat(ROOT / "SECURITY.md")
    assert (
        "with CalDAV's two read requests only, PROPFIND and REPORT (any other request is refused in code)" in security
    )
    assert "reach the helper on its input, never on its command line" in security
    assert "`scripts/set-calendar-password.sh [name]`" in security
    guide = site_flat(ROOT / "setup_assistant.md")
    assert "only CalDAV's two read requests" in guide and "`scripts/set-calendar-password.sh family`" in guide
    assert "Only Google calendars and the Outlook calendars of your mailboxes are supported" not in guide
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "`scripts/set-calendar-password.sh [name]`" in agents and "PROPFIND and REPORT only" in agents


def test_the_docs_say_a_caldav_calendars_requests_go_to_its_own_server_only() -> None:
    """The configured server only, iCloud's own hosts aside (review, 14 Oct): never a guess at a domain, which let
    another tailnet or DynDNS customer in."""
    readme = GUIDE.read_text(encoding="utf-8")
    assert "go only to that server (iCloud: Apple's icloud.com servers), encrypted" in readme
    security = site_flat(ROOT / "SECURITY.md")
    assert "to the configured server only (iCloud: Apple's icloud.com servers, as it answers from several)" in security
    assert "all within 12 seconds" in security
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "`MULTI_HOST_SITES`" in agents and "`CALDAV_SECONDS`" in agents
    for text in (readme, security, agents):
        assert "own domain" not in text and "last two names" not in text and "last two labels" not in text


HUE_ROUTE = (
    "Philips Hue lights are reached only through the Hue Bridge on the home network, never through Philips' cloud"
)
HUE_UNTESTED = "Hue lights are untested on a real Hue Bridge so far"


@pytest.mark.parametrize("name", ["GUIDE.md", "SECURITY.md", "setup_assistant.md"])
def test_the_hue_facts_say_the_same_in_every_doc(name: str) -> None:
    text = prose(ROOT / name)
    assert HUE_ROUTE in text and HUE_UNTESTED in text
    assert "scripts/hue-pair.sh" in text


def test_the_hue_pairing_is_named_in_her_commands_and_the_example_config() -> None:
    assert "estia hue-pair [address]" in (ROOT / "scripts" / "estia").read_text(encoding="utf-8")
    example = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert '#brand = "hue"' in example and "scripts/hue-pair.sh" in example


# the list reads as one (owner, 14 Oct): the calendars by service, the lights and plugs by brand, the same words in both
LIST_NAMES = (
    "The calendar line names each calendar's service (Google, Outlook, iCloud, Fastmail or Nextcloud; another CalDAV "
    "server by its host name), and the lights and plugs line names TP-Link (Kasa and Tapo), Philips Hue or both."
)


@pytest.mark.parametrize("name", ["GUIDE.md", "setup_assistant.md"])
def test_the_docs_say_the_list_names_the_calendars_services_and_the_lights_brands(name: str) -> None:
    from local_assistant.config import CalendarSource, DeviceSpec

    assert LIST_NAMES in prose(ROOT / name)
    family = CalendarSource("family", caldav="https://caldav.icloud.com", user="alex@example.com", calendar="Family")
    services = [
        CalendarSource("calendar").service,
        CalendarSource("work", mailbox="work").service,
        family.service,
        CalendarSource("f", caldav="https://caldav.fastmail.com/dav/", user="a", calendar="A").service,
        CalendarSource("n", caldav="https://nas.example.com/remote.php/dav", user="a", calendar="A").service,
        CalendarSource("o", caldav="https://dav.example.com", user="a", calendar="A").service,
    ]
    assert services == ["Google", "Outlook", "iCloud", "Fastmail", "Nextcloud", "dav.example.com"]
    lamp = DeviceSpec("lamp", "192.168.1.20", "light", "normal")
    hall = DeviceSpec("hall light", "", "light", "normal", brand="hue", light="11111111-1111-4111-8111-111111111111")
    assert (lamp.service, hall.service) == ("TP-Link", "Philips Hue")


# review of 15 Oct, builder A: setup's hand-over, the new install's facts and the editor, in the same words everywhere

FIRST_INSTALL_NO_ROLLBACK = (
    "A first install has no rollback: the move keeps no copy, and her only copy of her data is in `/var/estia`."
)
NANO_SAVE = "Ctrl-O, Enter saves; Ctrl-X quits"


@pytest.mark.parametrize("name", (*INSTALL_DOCS, "SECURITY.md"))
def test_the_docs_say_a_first_install_has_no_rollback(name: str) -> None:
    """The rollback and the 7-day hold exist only after moving an install that ran as the owner (findings 7, 49)."""
    text = prose(ROOT / name).replace("**", "")
    assert FIRST_INSTALL_NO_ROLLBACK in text
    assert "Your old folders are held for 7 days where only root reaches them" not in text
    for held in re.finditer(r"[^.]*held for (?:the )?7 days[^.]*\.", text):  # the hold is named with its limit
        assert "older install" in held.group(0), (name, held.group(0))


@pytest.mark.parametrize("name", ("README.md", *INSTALL_DOCS, "SECURITY.md", "AGENTS.md", "docs/e2e-checklist.md"))
def test_her_config_is_edited_as_her_and_the_docs_say_how_to_save(name: str) -> None:
    """sudoedit gives your account a copy of her config while it is edited, and runs the editor as you (a `nano` of
    yours earlier on your PATH reads and changes it): every edit runs nano as her, through her wrapper (review 15 Oct,
    2; findings 18, 56 before it)."""
    text = (ROOT / name).read_text(encoding="utf-8")
    assert not re.search(r"sudo -u _estia -e |sudoedit", text), name
    if name in INSTALL_DOCS:
        assert "/usr/bin/sudo /usr/local/estia/bin/estia config" in text
        assert NANO_SAVE in text


def test_the_ai_guide_is_a_hand_over_to_the_persons_own_terminal() -> None:
    """The AI checks, explains and reads; the person runs setup (findings 19, 38, 51, 58, 64)."""
    guide = prose(ROOT / "SETUP-WITH-YOUR-AI.md")
    assert "`scripts/setup.sh` runs only in the person's own Terminal window" in guide
    for words in (
        "1. Check first",
        "git -C ~/estia rev-parse HEAD",
        "`screen` (built into macOS)",
        'eval "$(/opt/homebrew/bin/brew shellenv)"',
        "3. The briefing: what setup asks, in order",
        "captcha link:",
        "Nothing shows: press Enter once.",
    ):
        assert words in guide, words
    assert "| you" not in guide  # no step table that gives setup's steps to the AI


def test_the_ai_guide_says_what_a_new_install_has_and_how_she_keeps_running() -> None:
    """No voice and no rollback on a new install; sleep, power failures, Ollama at login, FileVault (8, 50, 57)."""
    guide = prose(ROOT / "SETUP-WITH-YOUR-AI.md")
    assert "A new install has her service, without voice" in guide
    assert "/usr/bin/sudo /usr/bin/pmset -a sleep 0 autorestart 1" in guide
    assert "/usr/bin/sudo /usr/bin/fdesetup authrestart" in guide
    assert "Ollama starts at login" in guide and "Doctor's Power line" in guide
    readme = prose(GUIDE)
    assert "Ollama starts only when you log in, so after a restart she answers once someone has logged in" in readme


def test_the_ai_guide_names_every_one_time_code_as_a_secret() -> None:
    """Sign-in codes and addresses a script asks to paste are secrets too (finding 61)."""
    text = (ROOT / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8")
    rules = " ".join(text.split("## The rules", 1)[1].split("\n## ", 1)[0].split())
    for words in ("Microsoft's device code", "gcloud's and gh's codes", "Spotify's sign-in", "TP-Link login"):
        assert words in rules, words


def test_the_by_hand_registration_turns_the_line_limit_off() -> None:
    """A pasted request with the captcha link is over 1,023 characters (findings 1, 37)."""
    section = (ROOT / "setup_assistant.md").read_text(encoding="utf-8").split("### 2.3 Signal number (once)", 1)[1]
    section = section.split("\n### ", 1)[0]
    assert "stty -icanon" in section and "stty sane" in section and "1,023 characters" in section
    assert "Ctrl-D ends signal-cli." not in section


# integration of 15 Oct: the follow-ups the builders left, in the same words wherever a doc says them
VOICE_REBUILT = "Each update builds her voice service again when `tools/voice` changed."


def test_the_docs_name_another_program_at_her_voice_address_in_codes_words() -> None:
    """Finding 41: doctor and /status say not_hers in prompts' words; the docs quote them, never other words."""
    from local_assistant import prompts

    said = prompts.PART_REASONS["not_hers"]
    for name in ("GUIDE.md", "SECURITY.md", "setup_assistant.md"):
        assert said in site_flat(ROOT / name), name
    assert "/usr/bin/sudo /usr/sbin/lsof -nP -iTCP:8150 -sTCP:LISTEN" in GUIDE.read_text(encoding="utf-8")


def test_the_guide_says_each_update_builds_her_voice_service_when_it_changed() -> None:
    """The deploy builds the voice service from the commit's own sources (finding 27): no install-voice-daemon.sh
    after each change in tools/voice."""
    guide = site_flat(ROOT / "setup_assistant.md")
    assert guide.count(VOICE_REBUILT) >= 3
    assert "After changes in `tools/voice`, run `scripts/install-voice-daemon.sh`" not in guide


# ------------------------------------------------------------------ the split (owner, 15 Oct): README.md, a short front
# page; GUIDE.md, the rest, which the site's guide page renders as written (markdown-it, no raw HTML: a tag shows as
# text; an image becomes a link; an http:// link stops its build; #links are GitHub's heading anchors; a shell fence
# shows each line as one command)

README_LINES = 120
SHELL_FENCE = re.compile(r"(sh|bash|shell|zsh)\b")  # the site's test for a block of commands
COMMAND_LINE = re.compile(r"(?:[A-Z_][A-Z0-9_]*=\S*\s+)*(?:~/|\./|/)?[a-z][\w.+-]*(?:/[\w.+-]+)*(?:\s|$)")
INSTALL_TIME = (
    "ownloads and builds take about 30 minutes on a 100 Mbit/s line, most of it the model download (18.7 GB); about 15 minutes on a Mac with 16 "
    "or 24 GB of memory, where the model is 8.0 GB."
)
VOICE_TIME = (
    "Voice adds about 15 minutes while her voice loads, after its build and a 1.7 GB download (about 4 minutes)."
)
# owner, 15 Oct: four known issues leave README.md and GUIDE.md; SECURITY.md keeps each in its own words
MOVED_TO_SECURITY = (
    "Typing `/usr/bin/sudo` stops a fake `sudo` in your Terminal's folders",
    "`whence -va /usr/bin/sudo` must list only the file, never a shell function or an alias.",
    "what she runs (the update shows the list of changes and the files first)",
    "Ollama and AgentJev run as you: every prompt she sends",
    "Calls name no device: a call from a new linked device rings like one from your phone",
)
GONE_FROM_README_AND_GUIDE = (
    "fake `sudo`",
    "whence -va",
    "Her code changes only through an update",
    "the update shows the list of changes",
    "What she sends to Ollama",
    "passes through your account",
    "Calls carry no device number",
    "Calls name no device",
)


def fences(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The lines outside fenced blocks, and each fenced block's info string and lines."""
    outside: list[str] = []
    found: list[tuple[str, list[str]]] = []
    inside: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("```"):
            if inside is None:
                inside = []
                found.append((line[3:].strip(), inside))
            else:
                inside = None
            continue
        (outside if inside is None else inside).append(line)
    return outside, found


def without_code(lines: list[str]) -> str:
    """The lines with their inline code spans left out."""
    return re.sub(r"`[^`\n]*`", "", "\n".join(lines))


def github_slug(heading: str) -> str:
    """A heading's anchor as GitHub and the site make it: its text in lower case, punctuation left out, spaces as
    hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "").replace("*", "")
    kept = "".join(ch for ch in text.lower() if ch in " -_" or unicodedata.category(ch)[0] in "LMN")
    return kept.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    """Every heading anchor of a Markdown file; a repeated heading gets -1, -2."""
    outside, _ = fences(path.read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    found: set[str] = set()
    for line in outside:
        heading = re.match(r"#{1,6} (.+?)\s*$", line)
        if heading:
            base = github_slug(heading.group(1))
            count = seen.get(base, 0)
            seen[base] = count + 1
            found.add(f"{base}-{count}" if count else base)
    return found


def link_targets(path: Path) -> list[str]:
    """Every link target of a Markdown file, code left out: inline links, reference definitions and autolinks."""
    outside, _ = fences(path.read_text(encoding="utf-8"))
    text = without_code(outside)
    return [
        *re.findall(r"\]\(([^)\s]+)\)", text),
        *re.findall(r"^\[[^\]]+\]:\s*(\S+)", text, flags=re.MULTILINE),
        *re.findall(r"<([a-z][a-z0-9+.-]*:[^>\s]*)>", text, flags=re.IGNORECASE),
    ]


def test_the_helpers_read_markdown_as_github_and_the_site_do() -> None:
    assert github_slug("What's next") == "whats-next"
    assert github_slug("Safe and private, in plain words") == "safe-and-private-in-plain-words"
    assert github_slug("A rented Mac (VPS)") == "a-rented-mac-vps"
    assert github_slug("Projects (extra, off by default)") == "projects-extra-off-by-default"
    outside, found = fences("# A\n```sh\ngit pull\n```\nsee `<b>` [x](#a)\n## A\n")
    assert outside == ["# A", "see `<b>` [x](#a)", "## A"] and found == [("sh", ["git pull"])]
    assert without_code(outside) == "# A\nsee  [x](#a)\n## A"


def test_the_readme_stays_a_short_front_page() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(readme.splitlines()) < README_LINES
    headings = [line for line in readme.splitlines() if line.startswith("## ")]
    assert headings == ["## Security", "## What she does", "## Install", "## Everything else", "## License"]
    assert "[GUIDE.md](GUIDE.md)" in readme


def test_the_guide_has_one_h1_no_html_and_no_image() -> None:
    outside, _ = fences(GUIDE.read_text(encoding="utf-8"))
    assert [line for line in outside if line.startswith("# ")] == ["# The Estia guide"]
    text = without_code(outside)
    assert re.findall(r"(?<!\\)<[A-Za-z!/][^>\n]*>", text) == []  # a tag, or a word in angle brackets, unescaped
    assert "![" not in text


def test_the_guide_links_https_or_the_repo_and_every_anchor_hits_its_own_heading() -> None:
    targets = link_targets(GUIDE)
    assert targets
    assert [t for t in targets if re.match(r"[a-z][a-z0-9+.-]*:", t, re.I) and not t.startswith("https://")] == []
    own = anchors(GUIDE)
    assert [t for t in targets if t.startswith("#") and t[1:] not in own] == []
    assert len(own) == len({*own}) and {"a-rented-mac-vps", "how-long-it-takes", "whats-next"} <= own


def test_every_link_into_the_guide_or_the_readme_hits_a_heading() -> None:
    guide, readme = anchors(GUIDE), anchors(ROOT / "README.md")
    for name in ("README.md", "GUIDE.md", "SETUP-WITH-YOUR-AI.md", "setup_assistant.md", "SECURITY.md", "AGENTS.md"):
        for target in link_targets(ROOT / name):
            if target.startswith("GUIDE.md#"):
                assert target.split("#", 1)[1] in guide, (name, target)
            if target.startswith("README.md#"):
                assert target.split("#", 1)[1] in readme, (name, target)
            if name == "README.md" and target.startswith("#"):
                assert target[1:] in readme, (name, target)


@pytest.mark.parametrize("name", ["README.md", "GUIDE.md"])
def test_shell_blocks_hold_only_commands_one_per_line(name: str) -> None:
    _, found = fences((ROOT / name).read_text(encoding="utf-8"))
    shell = [lines for info, lines in found if SHELL_FENCE.match(info)]
    assert shell, name
    for lines in shell:
        for line in filter(str.strip, lines):
            assert COMMAND_LINE.match(line) and not line.rstrip().endswith("\\"), (name, line)


def test_the_known_issues_the_owner_moved_live_only_in_security_md() -> None:
    security = site_flat(ROOT / "SECURITY.md")
    for kept in MOVED_TO_SECURITY:
        assert kept in security, kept
    for doc in (ROOT / "README.md", GUIDE):
        flat = site_flat(doc)
        for gone in GONE_FROM_README_AND_GUIDE:
            assert gone not in flat, (doc.name, gone)
    guide = GUIDE.read_text(encoding="utf-8")
    assert "\n- sudo: the command that runs another command with admin rights, after your Mac password.\n" in guide
    devices = guide.split("- **Only the devices she knows.**", 1)[1].split("\n", 1)[0]
    assert devices.endswith("`/devices ok N`, sent from a device she already knows, lets it in.")


def test_the_guide_says_what_a_rented_mac_can_and_cannot_do() -> None:
    section = GUIDE.read_text(encoding="utf-8").split("\n## A rented Mac (VPS)\n", 1)[1].split("\n## ", 1)[0]
    flat = " ".join(section.split())
    for words in (
        "Amazon EC2 Mac, Scaleway or MacStadium",
        "Apple silicon and 16 GB of memory or more",
        "Untested",
        "physical and admin access",
        "They need the home network.",
        "YouTube blocks addresses of data centres.",
        "EC2 Mac is rented for 24 hours at a time at least.",
        "An ordinary Linux VPS cannot run her: her voice needs macOS on Apple silicon.",
    ):
        assert words in flat, words
    assert not re.search(r"[$€£]|\bprices?\b|\bper (?:hour|day|month)\b", flat, re.IGNORECASE)  # no prices


def test_the_install_time_is_said_the_same_on_the_front_page_and_in_the_guide() -> None:
    """Measured or computed, never guessed (15 Oct): each step's time in the guide's table, its source marked."""
    from local_assistant.setup import choose_model

    readme, guide = site_flat(ROOT / "README.md"), site_flat(GUIDE)
    for text in (readme, guide):
        assert INSTALL_TIME in text and VOICE_TIME in text
    assert "](GUIDE.md#how-long-it-takes)" in readme
    big, small = choose_model(32), choose_model(16)
    assert big and small and (big.tag, small.tag) == ("gemma4:26b", "gemma4:12b")
    table = GUIDE.read_text(encoding="utf-8").split("\n### How long it takes\n", 1)[1].split("\n## ", 1)[0]
    assert "`gemma4:26b`, 18.7 GB | About 25 minutes | Computed from the size |" in table
    assert "`gemma4:12b`, 8.0 GB | About 11 minutes | Computed from the size |" in table
    assert "| About 15 minutes | Measured live, 13 Oct |" in table  # the Neural Engine (AGENTS.md, Gotchas)
    rows = [line for line in table.splitlines() if line.startswith("| ") and not line.startswith("| Step ")]
    sources = {"Measured", "Measured live, 13 Oct", "Computed from the size", "Not measured"}
    assert rows and all(row.rstrip(" |").rsplit("| ", 1)[1] in sources for row in rows), rows
    assert "your own typing" in guide.lower() and "typing" not in readme.lower()
