"""Statements that several files make must say the same thing."""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEVICE_LOGIN = (
    "Older Kasa devices need no login; newer Kasa firmware and Tapo devices need the TP-Link login (Tapo is untested)."
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
    assert "Estia listens, thinks and speaks in **one language at a time**." in readme
    assert 'name = "Estia"' in (ROOT / "config.example.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["README.md", "SETUP-WITH-YOUR-AI.md", "setup_assistant.md", "AGENTS.md"])
def test_the_docs_call_her_estia_and_tiffany_only_as_ours(name: str) -> None:
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        if "Tiffany" in line:
            assert "ours" in line.lower() or "Ours" in line, line


def test_the_notices_credit_the_fonts_and_name_no_brand_we_do_not_use() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "\nNot affiliated with Signal, Spotify, Google, Microsoft, Bose, TP-Link or OpenClaw.\n" in readme
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
    for name in ("README.md", "setup_assistant.md"):
        assert clone in (ROOT / name).read_text(encoding="utf-8"), name
    advisories = "https://github.com/enable4all/estia-on-mmini/security/advisories/new"
    assert advisories in (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_the_speech_detector_is_documented_and_credited() -> None:
    example = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert '#barge = "loudness"' in example  # the default, as config.py has it
    for name in ("README.md", "setup_assistant.md", "AGENTS.md"):
        assert 'barge = "speech"' in (ROOT / name).read_text(encoding="utf-8"), name
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
    readme = prose(ROOT / "README.md")
    assert "until it is ready, loudness decides" in readme  # the limit inside the claim
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

INSTALL_DOCS = ("README.md", "setup_assistant.md", "SETUP-WITH-YOUR-AI.md")
SITE_PROMISES = (
    "Local.",
    "Read-only mail and calendars.",
    "Lights and plugs follow a rule set per device.",
    "Only Signal.",
    "Keys stay put.",
)


@pytest.mark.parametrize("name", INSTALL_DOCS)
def test_her_commands_are_named_by_their_full_root_owned_path(name: str) -> None:
    """`sudo estia` would find whatever `estia` comes first on PATH, and that folder may be yours to write."""
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "/usr/bin/sudo /usr/local/estia/bin/estia doctor" in text
    assert not re.search(r"sudo estia(-deploy)? ", text)
    assert "newsyslog" not in text  # her logs rotate themselves


def test_the_readme_says_where_she_lives_and_how_she_runs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
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
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for promise in SITE_PROMISES:
        assert f"- **{promise}**" in readme, promise
    assert "Chats, memory and profile stay on the Mac." in readme
    assert "Signal Desktop on your laptop" in readme and "Only Kasa devices are tested" in readme
    assert '"Always relay calls"' in readme


def test_the_update_and_restart_rows_keep_their_shape() -> None:
    """The site shows these two commands: the first code span of each row stays as it was."""
    guide = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    upgrade = re.search(r"\| Upgrade \| `([^`]+)`", guide)
    restart = re.search(r"\| Restart the daemon \| `([^`]+)`", guide)
    assert upgrade and upgrade.group(1).startswith("git pull && uv sync --frozen")
    assert restart and restart.group(1) == "/usr/bin/sudo launchctl kickstart -k system/com.local-assistant.bridge"
    assert "/usr/bin/sudo /usr/local/estia/bin/estia-deploy" in guide.split("| Upgrade |", 1)[1].split("\n", 1)[0]


def test_the_readme_names_the_device_commands_and_the_signal_terms_gap() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
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
        "sudo -u _estia install -m 600",
        "tmux",
        "/usr/bin/sudo /usr/local/estia/bin/estia-deploy",
        "scripts/harden.sh --dry-run",
        "/usr/bin/sudo -u _estia -e /var/estia/.config/local-assistant/config.toml",
        "/usr/bin/sudo /usr/local/estia/bin/estia secret set",
        "Registration Lock",
        "locked for 7 days",
        "Linked devices",
    ):
        assert words in guide, words


def test_the_installing_ai_has_a_security_section() -> None:
    text = (ROOT / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8")
    section = text.split("## Security", 1)[1].split("\n## ", 1)[0]
    for words in (
        "scripts/harden.sh",
        "Before each sudo prompt",
        "Never run `sudo`",
        "phone checklist",
        "unlocked phone",
        "_estia",
    ):
        assert words in section, words
    assert "cannot read her data" in section


def test_the_readme_puts_the_limits_inside_its_own_user_claims() -> None:
    """Every claim provable: her own account keeps her files from your programs, not what she sends Ollama (yours), and
    only while no sudo rule skips your password."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    account = readme.split("- **Her own account on the Mac.**", 1)[1].split("\n- **", 1)[0]
    assert "Ollama" in account and "password" in account and "every program she runs" in account
    keys = readme.split("- **Keys stay put.**", 1)[1].split("\n", 1)[0]
    assert "skips" in keys
    commands = readme.split("## Slash commands", 1)[1].split("\n## ", 1)[0]
    assert "`/devices forget N`" in commands
    assert "/usr/bin/sudo /usr/local/estia/bin/estia rollback" in readme


def test_security_md_says_the_own_user_limit_in_the_same_plain_words() -> None:
    """The same fact keeps the same words: the own-user claim's limit as the README says it, never "sudo rule"."""
    security = prose(ROOT / "SECURITY.md")
    protects = security.split("What the project tries to protect", 1)[1].split("If the Mac is compromised", 1)[0]
    assert "as long as no admin rule skips the password" in protects
    assert "as long as no admin rule skips the password" in prose(ROOT / "README.md")
    assert "sudo rule" not in security and "passwordless sudo" not in security and "metadata" not in security


def test_the_installing_ai_sends_every_sudo_to_the_persons_own_terminal() -> None:
    text = (ROOT / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8")
    assert "own Terminal window" in text and "password prompts" in text
    security = text.split("## Security", 1)[1].split("\n## ", 1)[0]
    assert "Ollama" in security  # the limit inside the claim


# a fake `sudo` earlier on the owner's PATH catches the password: every documented command types /usr/bin/sudo

OWNER_DOCS = (*INSTALL_DOCS, "SECURITY.md", "AGENTS.md", "docs/e2e-checklist.md")
# plain words (owner, 14 Oct): the same two limits, without "PATH", "deploy" or "commits"
DEPLOY_LIMIT = (
    "a program of yours can change what your next update puts live; the update shows the list of changes and the "
    "files they touch, not the code itself"
)
SUDO_LIMIT = (
    "a program of yours can catch your password with a fake `sudo` that your Terminal finds before the real one, so "
    "type `/usr/bin/sudo`"
)


def code_in(text: str) -> list[str]:
    """The fenced blocks and the inline code spans of a Markdown text."""
    fenced = re.compile(r"```.*?```", re.DOTALL)
    return [*fenced.findall(text), *re.findall(r"`([^`\n]+)`", fenced.sub("", text))]


@pytest.mark.parametrize("name", OWNER_DOCS)
def test_every_documented_owner_command_types_usr_bin_sudo(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert [code for code in code_in(text) if re.search(r"(?<![/\w.-])sudo\s", code)] == []


@pytest.mark.parametrize(
    ("name", "start"),
    [
        ("README.md", "- **Her own account on the Mac.**"),
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
    for name in ("AGENTS.md", "SECURITY.md", "setup_assistant.md", "README.md"):
        for cited in re.findall(r"e2e-checklist\.md, (\d+)(?:-(\d+))?\)", (ROOT / name).read_text(encoding="utf-8")):
            for number in filter(None, cited):
                assert 1 <= int(number) <= highest, (name, number)


# owner, 13 Oct: projects are an extra, off by default (not everyone is a developer); the site's drift test reads these


def test_the_readme_has_projects_as_an_extra_as_the_site_reads() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
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
    readme = prose(ROOT / "README.md")
    assert "At the start of a call she knows today's numbers, never the titles" in readme
    guide = prose(ROOT / "setup_assistant.md")
    assert "What she knows at the start of a call." in guide and "Calendar today: 3 events left" in guide
    agents = prose(ROOT / "AGENTS.md")
    assert "call's glance" in agents and "never a title" in agents


def test_a_spoken_project_name_is_documented() -> None:
    """With neutral names: the owner's own projects stay out of the public docs (the tests keep the real case)."""
    for name in ("README.md", "setup_assistant.md", "AGENTS.md"):
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
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    flat = prose(ROOT / "README.md")
    for words in (
        "`/babble nl` starts practice in Dutch",  # language practice
        'With `barge = "speech"` in `[calls]`, a speech detector on the Mac (Silero VAD) decides what stops her',
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

    for name in ("README.md", "setup_assistant.md"):
        text = prose(ROOT / name)
        for quoted in (prompts.CALL_NOT_READY, prompts.VOICE_READY, ownuser.VOICE_LOADING):
            assert quoted in text, (name, quoted)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
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
    assert "without them, go on once `curl -s http://127.0.0.1:8150/health` shows `stt` and `tts` ready" in order
    guide = prose(ROOT / "setup_assistant.md").split("`scripts/harden.sh` runs it again any time", 1)[1][:900]
    assert "with calls set up, her text" in guide
    assert "The person runs `scripts/harden.sh` in their own Terminal" in order


def test_remove_old_is_never_offered_without_its_limit() -> None:
    """remove-old also deletes the folders held for the rollback: each place that offers it says so."""
    for name in ("README.md", "SETUP-WITH-YOUR-AI.md", "setup_assistant.md"):
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
    for script in ("set-web-keys.sh", "set-calendar-url.sh", "set-tapo-credentials.sh", "mail-login.sh"):
        assert '"$sudo" "$estia"' in (ROOT / "scripts" / script).read_text(encoding="utf-8"), script


# batch2 (owner, 14 Oct): the facts the code now keeps, in the docs and in plain words; the site's drift tests read the
# README sentences marked "site"


def test_the_readme_says_what_goes_online_in_the_sites_words() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    online = readme.split("### What stays home, and what goes online", 1)[1].split("\n## ", 1)[0]
    assert "\nThe model always runs on the Mac. " in online
    assert (
        "Outside services are reached only for Signal, links you send and the extras you switch on." in online
    )  # site
    assert "| Google's calendar links, for Google calendars (read-only) |" in online  # site
    assert "Services on the internet are reached only for features that are turned on." not in readme
    assert "iCal feeds" not in readme


def test_the_readme_puts_the_ai_and_the_code_in_plain_words() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
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
    for name in ("README.md", "SETUP-WITH-YOUR-AI.md", "setup_assistant.md", "SECURITY.md", "AGENTS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "--captcha" not in text and "--pin" not in text and "<code-from-the-sms>" not in text, name
    readme = prose(ROOT / "README.md")
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
    readme = prose(ROOT / "README.md")
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
    assert "analytics" in prose(ROOT / "README.md")
    for name in ("setup_assistant.md", "SECURITY.md"):
        text = prose(ROOT / name)
        assert "HOMEBREW_NO_ANALYTICS=1" in text and "brew analytics off" in text, name


def test_the_docs_name_the_languages_and_the_links_her_own_messages_check() -> None:
    """F6: a link on any ending; the words that ask for a code or a password in the 11 languages."""
    readme = prose(ROOT / "README.md")
    assert "The word checks read English, except for reminder and call claims" not in readme
    assert "example.ch/x" in readme and "all 11 languages" in readme
    security = prose(ROOT / "SECURITY.md")
    assert "The checks of a message she starts are English word lists" not in security
    assert '"x.co."' in security and "all 11 languages" in security
    guide = prose(ROOT / "setup_assistant.md")
    assert "example.ch/x" in guide and "all 11 languages" in guide


def test_the_docs_keep_the_home_address_out_of_the_database() -> None:
    """F10: the database keeps the rounded area and the address's tag, never the address."""
    places = prose(ROOT / "README.md").split("**Places stay rough.**", 1)[1].split("Known gaps are listed", 1)[0]
    assert "never the address itself" in places
    for name in ("setup_assistant.md", "SECURITY.md", "config.example.toml"):
        assert "never the address" in prose(ROOT / name), name


def test_the_voice_models_are_described_as_the_code_loads_them() -> None:
    """F2: Whisper and the speech detector pinned and checked, Whisper offline after the first download; the speaking
    voices as published."""
    readme = prose(ROOT / "README.md")
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
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    can_do = readme.split("## What she can do", 1)[1].split("\n### ", 1)[0]
    assert "| YouTube videos | Send a YouTube link" in can_do
    online = readme.split("### What stays home, and what goes online", 1)[1].split("\n## ", 1)[0]
    assert (
        "Outside services are reached only for Signal, links you send and the extras you switch on." in online
    )  # site: word for word, YouTube is one of the links you send
    assert "| | YouTube, for the captions of a video link you send |" in online
    flat = prose(ROOT / "README.md")
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
    for name in ("README.md", "setup_assistant.md"):
        assert note in prose(ROOT / name), name
    guide = prose(ROOT / "setup_assistant.md")
    assert prompts.VIDEO_LEARN_COMMAND in guide and prompts.VIDEO_ERRORS["no_captions"] in guide
    assert f"{video.PART_CHARS:,} characters" in guide and f"{video.CAP_CHARS:,} characters" in guide
    assert prompts.VIDEO_NOT_SENT.split("{")[0] in guide  # code's line after copied captions, word for word
    copies = f"{video.COPY_WORDS} or more of their words in a row"
    shares = f"shorter stretches of {video.SHARE_WORDS} or more that together make up more than {video.COPY_SHARE:.0%}"
    for name in ("README.md", "SECURITY.md", "setup_assistant.md"):
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
