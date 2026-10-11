# Contributing

Thanks for looking at Estia. Keep changes small, tested and free of personal data.

## Set up

- Requirements: see the **Requirements** section of [README.md](README.md).
- `uv sync` installs the bridge's development environment (Python 3.13).
- `scripts/verify.sh` runs the privacy check (`scripts/privacy_scan.py`), ruff (lint and format), mypy in strict mode
  and pytest. Run it before every commit. To run the privacy check on every commit as well:
  `git config core.hooksPath .githooks` (this replaces `.git/hooks`; if you already have a pre-commit hook, call
  `.githooks/pre-commit` from it instead).
- The helpers have their own tests:
  - `cd tools/voice && swift test` (Swift Testing; the Command Line Tools are enough);
  - `cd tools/tapo && uv sync --frozen && uv run pytest -q`;
  - `cd tools/ical && uv sync --frozen && uv run pytest -q`.
- The Gemma test set (`uv run python -m evals.gemma_suite`) needs the real model and is optional. The model is not
  deterministic, so run the set twice before you trust a prompt change.

## Rules

- **Test first.** Write a failing test before every change and every bug fix.
- **Runtime is standard library only.** Python 3.13, no runtime dependency without a discussion first.
- **Style.** Ruff with line length 120, mypy `--strict`, type hints everywhere. Docstrings for complex functions.
- **Commits.** Conventional commits (for example `fix(calls): ...`) with a short body. No attribution trailers.
- **No personal data.** Code, tests, fixtures and docs use the neutral persona "Alex Example", `.example` or
  `example.com` addresses, numbers such as `+306900000000`, and patterned UUIDs. Never commit a real name, phone number,
  account ID, device name, home path, address, key or log excerpt. The privacy check refuses them; a line that must keep
  such a value after review carries `privacy-ok: <reason>`.
- **No secrets in the repo.** Keys and settings live in the config, the login keychain or mode 0600 files. See
  [setup_assistant.md](setup_assistant.md).
- **Prompt changes** are checked with the Gemma test set, as above.
- **Her name is a setting.** Every install names its own assistant (`name` in the config, default Estia). A text that
  names her takes the name at runtime (in `prompts.py`: a `name` argument or a `{name}` template); never write a name
  into code, prompts or tests that do not mean it.
- **New features** start with an issue that agrees the design, then the code. Design specs and plans are not kept in
  this repository.
- **Security problems** go through [SECURITY.md](SECURITY.md), not a public issue.

## Licence

By contributing, you agree that your contribution is licensed under the MIT licence (see [LICENSE](LICENSE)). Changes to
`tools/call-tunnel/` are licensed under AGPL-3.0-only (see [its notice](tools/call-tunnel/LICENSE-NOTICE.md)).
