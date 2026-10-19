"""AGENTS.md stays a short map, and every rule file loads by its paths (the 0.9.5 AGENTS.md diet).

Claude Code loads AGENTS.md (through CLAUDE.md) and `docs/agents/safety.md` into every session, and an area's rule
file only when it reads a file its `paths:` front matter names; its docs ask for under 200 lines in that file.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePosixPath

from tests.agent_docs import ROOT, RULES

AGENTS = ROOT / "AGENTS.md"
SAFETY = RULES / "safety.md"


def rule_files() -> list[Path]:
    """Every rule file; safety.md must be among them."""
    files = sorted(RULES.glob("*.md"))
    assert SAFETY in files, "no docs/agents/safety.md"
    return files


def front_matter_globs(text: str) -> list[str] | None:
    """The `paths:` globs of a file's YAML front matter; None when there is no front matter or no `paths:` key."""
    if not text.startswith("---\n"):
        return None
    lines = text[4:].split("\n---\n", 1)[0].splitlines()
    if "paths:" not in lines:
        return None
    globs: list[str] = []
    for line in lines[lines.index("paths:") + 1 :]:
        if not line.startswith("  - "):
            break
        globs.append(line[4:].strip().strip('"'))
    return globs


def tracked_files() -> list[str]:
    """The paths git tracks, relative to the repository root."""
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return listed.stdout.splitlines()


def test_agents_md_is_short() -> None:
    text = AGENTS.read_text(encoding="utf-8")
    lines = len(text.splitlines())
    size = len(text.encode("utf-8"))
    assert lines <= 200, f"AGENTS.md has {lines} lines (max 200)"
    assert size <= 12_000, f"AGENTS.md is {size} bytes (max 12,000)"


def test_each_rule_file_is_short_and_safety_is_shorter_still() -> None:
    for rule in rule_files():
        lines = len(rule.read_text(encoding="utf-8").splitlines())
        limit = 80 if rule == SAFETY else 150
        assert lines <= limit, f"{rule.name} has {lines} lines (max {limit})"
    size = len(SAFETY.read_bytes())
    assert size <= 6_000, f"safety.md is {size} bytes (max 6,000)"


def test_safety_loads_every_session_and_every_other_rule_file_loads_by_path() -> None:
    safety = front_matter_globs(SAFETY.read_text(encoding="utf-8"))
    assert safety is None, "safety.md must have no paths: front matter (it loads every session)"
    for rule in rule_files():
        if rule != SAFETY:
            globs = front_matter_globs(rule.read_text(encoding="utf-8"))
            assert globs, f"{rule.name} needs a paths: front matter"


def test_every_paths_glob_matches_a_tracked_file() -> None:
    files = tracked_files()
    for rule in rule_files():
        for glob in front_matter_globs(rule.read_text(encoding="utf-8")) or []:
            matched = any(PurePosixPath(name).full_match(glob) for name in files)
            assert matched, f"{rule.name}: {glob} matches no tracked file"


def test_the_index_names_every_rule_file_and_every_named_file_exists() -> None:
    text = AGENTS.read_text(encoding="utf-8")
    for rule in rule_files():
        assert f"docs/agents/{rule.name}" in text, f"AGENTS.md does not name {rule.name}"
    for path in sorted(set(re.findall(r"docs/agents/[\w-]+\.md", text))):
        assert (ROOT / path).is_file(), f"AGENTS.md names {path}, which does not exist"


def test_agents_md_tells_other_tools_to_open_the_rule_files() -> None:
    """Codex and other tools read AGENTS.md but never load docs/agents on their own."""
    text = " ".join(AGENTS.read_text(encoding="utf-8").split())
    assert "Other tools (Codex and others) do not load them on their own" in text
    assert "read `docs/agents/safety.md` first, and open the area's rule file before changing anything" in text


def test_claude_code_reads_the_rules_through_one_link() -> None:
    """The rule files are plain Markdown for any tool, so they live in docs/agents (owner, 19 Oct: "why do we call
    those files .claude since they are not claude specific?"); Claude Code loads rules by path only from
    .claude/rules, which follows links: it holds one, to docs/agents, and nothing else."""
    rules = ROOT / ".claude" / "rules"
    assert sorted(path.name for path in rules.iterdir()) == ["agents"]
    link = rules / "agents"
    assert link.is_symlink() and os.readlink(link) == "../../docs/agents"
    assert link.resolve() == RULES.resolve()
