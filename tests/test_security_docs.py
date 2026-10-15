"""SECURITY.md, AGENTS.md and the live checklist say what the hardening does, and the runbook lists every step."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECURITY = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
CHECKLIST = (ROOT / "docs" / "e2e-checklist.md").read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """The text under a `## heading`, up to the next one."""
    found = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert found is not None, f"no section {heading!r}"
    return found.group(1)


def test_the_mac_compromised_runbook_lists_every_step_with_its_command() -> None:
    runbook = section(SECURITY, "If the Mac is compromised")
    steps = [
        ("re-register", "her number"),
        ("Microsoft", "/usr/bin/sudo /usr/local/estia/bin/estia mail-login"),
        ("iCal", "scripts/set-calendar-url.sh"),
        ("Brave", "scripts/set-web-keys.sh"),
        ("Ollama", "scripts/set-web-keys.sh"),
        ("Google Maps", "scripts/set-web-keys.sh"),
        ("TP-Link", "scripts/set-tapo-credentials.sh"),
        ("Registration Lock PIN", "setPin"),
    ]
    numbered = [" ".join(step.split()) for step in re.split(r"\n(?=\d+\. )", runbook) if re.match(r"\d+\. ", step)]
    for subject, command in steps:
        step = next((item for item in numbered if subject.casefold() in item.casefold()), "")
        assert step and command in step, f"{subject}: {command}"
    first = [number for number, step in enumerate(numbered) if "re-register" in step.casefold()]
    assert first and first[0] < next(n for n, step in enumerate(numbered) if "Microsoft" in step)  # her number first


def test_the_known_gaps_are_current() -> None:
    gaps = section(SECURITY, "Known accepted gaps")
    assert "newsyslog" not in gaps  # logs are bounded in-process now
    assert "not a pinned" not in gaps and "fingerprint" not in gaps  # the JVM installer pins the key
    assert "signal-cli is an unofficial client" in gaps and "terms" in gaps and "separate number" in gaps
    assert "calls name no device" in gaps.lower()
    assert "AgentJev" in gaps and "Host" in gaps and "Origin" in gaps
    assert "_estia" in gaps and "Homebrew" in gaps  # the own-user install's residuals


def test_what_it_protects_names_her_own_user_and_the_new_guards() -> None:
    protects = section(SECURITY, "What the project tries to protect")
    for words in ("`_estia`", "/usr/local/estia", "/lock", "/devices ok", "safety number", "model_digest", "keep_days"):
        assert words in protects, words


def test_agents_md_has_the_rules_and_the_names() -> None:
    for words in (
        "--trust-new-identities on-first-use",
        "/devices ok N",
        "/lock",
        "UNLOCK_WAIT_SECONDS",
        "listDevices",
        "model_digest",
        "keep_days",
        "secure_delete",
        "RotatingFileHandler",
        "defang",
        "own_words",
        "FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570",
        "hostsec",
        "secretfile",
        "ownuser",
        "deploy",
        "harden",
        "/usr/bin/sudo /usr/local/estia/bin/estia-deploy",
        "/usr/bin/sudo /usr/local/estia/bin/estia doctor",
        "scripts/migrate-to-own-user.sh --dry-run",
        "scripts/harden.sh --dry-run",
        "ESTIA_ROOT",
        "daemons run only from /usr/local/estia as _estia; the checkout is a source, never run",
    ):
        assert words in AGENTS, words


def test_the_live_checklist_has_the_hardening_steps() -> None:
    hardening = section(CHECKLIST, "Hardening (devices, lock, safety number, model pin)")
    for words in ("/lock", "/unlock", "/devices ok", "Signal Desktop", "safety number", "model_digest", "status.json"):
        assert words in hardening, words


def test_the_review_limits_are_inside_the_claims() -> None:
    protects = section(SECURITY, "What the project tries to protect")
    assert "/devices forget N" in protects and "14 days" in protects  # a device number is reused by Signal
    assert "before every chat" in protects and "your own account, which runs Ollama" in protects  # the model pin
    assert "/new" in protects  # retention: the current conversation's summary stays until /new or /forget
    assert "--update-tools" in protects and "asks first" in protects  # what root runs, and the deploy's review
    gaps = section(SECURITY, "Known accepted gaps")
    for words in (
        "Ollama and AgentJev run as you",  # every prompt passes through a program your account can change
        "safe.directory",  # on her git's command line only: the race between the config check and git
        "before the move",  # a copy taken before it still works
        "sudo's log",  # a value typed on a sudo command line
        "launchd.log",  # opened by launchd as root in her folder: untested
        "an admin rule skips the password",
        "AuthenticationAuthority",  # her account has no password: set and checked, untested on the real Mac
    ):
        assert words in gaps, words
    runbook = section(SECURITY, "If the Mac is compromised")
    assert "before the move" in runbook


def test_the_checklist_covers_the_reviews_live_checks() -> None:
    hardening = section(CHECKLIST, "Hardening (devices, lock, safety number, model pin)")
    for words in (
        "launchd.log",
        "DenyUsers",
        "--update-tools",
        "rollback",
        "/devices forget",
        "estia signal",
        "unlock",
    ):
        assert words in hardening, words


def test_her_projects_folder_is_named_with_its_limit_everywhere() -> None:
    """Her user reads your projects folder read-only, nothing else in your home; the accepted risk is said in one line
    (files git does not track are readable by her user) in each doc that names the share."""
    setup = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
    readme = (ROOT / "GUIDE.md").read_text(encoding="utf-8")  # the README's body since the split (15 Oct)
    gaps = section(SECURITY, "Known accepted gaps")
    protects = section(SECURITY, "What the project tries to protect")
    for text in (setup, gaps, protects, readme):
        assert "scripts/share-projects.sh" in text
    for text in (setup, gaps):
        assert ".env.local" in text and "no shell" in text
    assert "nothing else in your home" in setup and "read-only" in protects
    assert "never set git's `safe.directory" not in setup.lower()  # her git names it on its own command line
    for words in ("scripts/share-projects.sh", "safe.directory=*", "--ignore-submodules=all", "chmod -h +a"):
        assert words in AGENTS, words
