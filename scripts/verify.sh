#!/usr/bin/env bash
# Privacy check, lint, format check, strict types, unit tests: run before every commit.
set -euo pipefail
cd "$(dirname "$0")/.."
# no private files or values in tracked files (also checks against your own config, if there is one)
uv run python scripts/privacy_scan.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
# the documents helper (Swift, Apple's frameworks only) before the tests: the end-to-end test reads PDFs with it
# (tests/test_documents_e2e.py), never skipped here
if [[ -f tools/documents/Package.swift ]]; then
  (cd tools/documents && swift build -c release --quiet)
  export DOCUMENTS_HELPER_REQUIRED=1
fi
uv run pytest -q
# the device helper has its own environment (python-kasa): its tests and strict types
if [[ -f tools/tapo/pyproject.toml ]]; then
  (cd tools/tapo && uv run --frozen -q pytest -q && uv run --frozen -q mypy)
fi
# the calendar helper too (icalendar, recurring-ical-events)
if [[ -f tools/ical/pyproject.toml ]]; then
  (cd tools/ical && uv run --frozen -q pytest -q && uv run --frozen -q mypy)
fi
# and the YouTube helper (youtube-transcript-api, requests)
if [[ -f tools/youtube/pyproject.toml ]]; then
  (cd tools/youtube && uv run --frozen -q pytest -q && uv run --frozen -q mypy)
fi
# and the documents helper's own tests (Swift Testing, offline; a new build's first scan waits for Vision's model),
# one at a time as the helper reads: tests reading scans at once deadlocked a 4-core Mac in Vision (16 Oct)
if [[ -f tools/documents/Package.swift ]]; then
  (cd tools/documents && swift test --quiet --no-parallel)
fi
