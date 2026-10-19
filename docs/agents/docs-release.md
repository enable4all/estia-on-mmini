---
paths:
  - "README.md"
  - "GUIDE.md"
  - "SECURITY.md"
  - "setup_assistant.md"
  - "SETUP-WITH-YOUR-AI.md"
  - "CHANGELOG.md"
  - ".github/**"
  - "tests/test_docs.py"
---

# Public docs, the website's quotes and releases

## Commands

- Release notes: a tag's section of `CHANGELOG.md`, published word for word by `.github/workflows/release.yml`, inside the fixed page `.github/release-page.md` (the tagline, then "What changed in {{VERSION}}" with the section at `{{SECTION}}`; owner, 18 Oct; no list of what is in 0.9 since 0.9.5, owner, 19 Oct). Before tagging vX.Y.Z: rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, put an empty `## [Unreleased]` above it, and at the end move the `[Unreleased]` link to `compare/vX.Y.Z...HEAD` and add `[X.Y.Z]: …/compare/v<previous>...vX.Y.Z` (`tests/test_docs.py` checks each link); a tag without its section stops the workflow before anything is published (`tests/test_docs.py` runs the step with a gh stub). The workflow reads `CHANGELOG.md` as the tag has it: a section added after tagging needs the tag moved

## Gotchas

- The website (estia.one, its own repo) checks README.md and GUIDE.md with drift tests (`tests/drift.test.mjs`, `ESTIA_REPO=<checkout>`) that read some README.md or GUIDE.md, SECURITY.md and setup_assistant.md sentences word for word (`tests/test_docs.py` pins every one in `SITE_README` (in README.md or GUIDE.md: a pinned sentence may move between the two), `SITE_SECURITY`, `SITE_SETUP`, `SITE_AGENTS` and `SITE_PROMPTS` (a prompt line the site quotes: a prompt change tells the site too), with her commands, the slash commands and the languages the page names; `site_misses`): a changed one turns the site's tests red until the site matches, so change the pin on purpose and name every changed one (old and new) for the site. README.md is the front page (under 120 lines); GUIDE.md holds the rest, and the site's guide page renders it as written: no HTML (a tag shows as text), no image (it becomes a link), no http:// link, every #link a heading of GUIDE.md (GitHub's anchors: -1, -2 for a repeated heading), one H1, and an `sh` fence holds only commands, one per line (`tests/test_docs.py` checks each).
