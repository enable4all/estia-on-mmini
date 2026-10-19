"""The agents' instructions: AGENTS.md, a short map, and the rule files in `docs/agents/` it points to (Claude Code
reads them through `.claude/rules/agents`, a link to that folder).

Since 0.9.5 (the AGENTS.md diet) each area's rules, commands and gotchas are in its rule file, word for word as they
were in AGENTS.md, so a test that pins a sentence of the agents' instructions reads all of them.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "docs" / "agents"
GOTCHAS = "\n## Gotchas\n"


def rule_names() -> tuple[str, ...]:
    """Every rule file, as a path from the repository's root, in name order."""
    return tuple(f"docs/agents/{path.name}" for path in sorted(RULES.glob("*.md")))


AGENT_DOCS = ("AGENTS.md", *rule_names())  # AGENTS.md first, then every rule file


def agent_docs() -> str:
    """AGENTS.md and every rule file, one after the other."""
    return "\n".join((ROOT / name).read_text(encoding="utf-8") for name in AGENT_DOCS)


def agent_gotchas() -> str:
    """The `## Gotchas` section of every rule file (each runs to the next `## ` heading), one after the other."""
    parts: list[str] = []
    for name in rule_names():
        text = (ROOT / name).read_text(encoding="utf-8")
        if GOTCHAS in text:
            parts.append(text.split(GOTCHAS, 1)[1].split("\n## ", 1)[0])
    return "\n".join(parts)
