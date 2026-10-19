---
paths:
  - "tests/**"
---

# Tests

## Layout

- Tests in `tests/` (fakes for signal-cli and Gemma in `tests/fakes/`; the chords rule book's rows, named by rule, in `tests/test_theory.py`); real-model test set in `evals/gemma_suite.py`.

## Commands

- Hermetic suite: `env -u LANG uv run pytest -q` and `LANG=C uv run pytest -q` pass as the plain run does (`tests/test_hermetic.py` runs the helper-environment tests under both, and greps the code for system paths read outside `Host.path()`)
- Before every commit: `scripts/verify.sh` (in AGENTS.md, Commands).

## Gotchas

- Editing a file twice within a second with the same size can leave stale `.pyc` files: clear `__pycache__` if a test disagrees with the source.
- A test of the owner's shell scripts sets `ESTIA_TEST_ROOT` to a fake tree: they ignore `ESTIA_ROOT` on purpose, and once she is moved the live `/Library/LaunchDaemons` shows her moved: a test without it reads the live system and, where she is moved, runs the real `/usr/bin/sudo` (so `run_script` in `tests/test_scripts.py` always sets one: `/var/empty/estia-test-root`, which can never exist, unless the test names its own). Python tests get their own empty `ESTIA_ROOT` from `tests/conftest.py` (`no_live_system`, which also refuses the live voice service); doctor's "Always on" stat'ed the live service file 117 times before it followed `estia_root()`.
