# documents-helper

The text of one PDF per run, for the bridge. Apple's own frameworks only: PDFKit for a page's text, Vision for a page that has none (a scan). No third-party code and no network; the only model is Vision's own text recognition, on the Mac (Gemma is never used).

## Call

```
documents-helper read <absolute path to a PDF> --max-pages 300 --max-seconds 60
```

One JSON line on stdout. Exit 0 with `"ok": true`, exit 1 otherwise.

- Read: `{"ok": true, "pages": [{"n": 1, "text": "…", "ocr": false}, …], "skipped": [{"n": 7, "why": "unreadable"}], "page_count": N}`. Every page is in `pages` or in `skipped`, in page order. `ocr` is true when Vision read the page. A blank page is in `pages` with empty text. A page's text has line feeds only (also for U+2028, U+2029 and U+0085, which JSON leaves unescaped), no NUL, other control characters as spaces (tabs stay), and at most 20,000 characters, as the bridge keeps it: 300 pages always fit the bridge's 32 MiB limit.
- Not read: `{"ok": false, "error": "<code>"}`:
  - `not_pdf`: no PDF header in the first 1024 bytes.
  - `encrypted`: the PDF opens only with a password. A PDF with only an owner password (limits on printing or copying) opens without one and is read, as in Preview.
  - `too_big`: over 25 MB (25,000,000 bytes, as Finder counts them). Checked before a byte is read.
  - `too_many_pages`: over `--max-pages`. Checked before a page is read.
  - `timeout`: past `--max-seconds`. Nothing is returned for that document, not even the pages already read.
  - `unreadable`: a symlink, a folder, a pipe, a missing file, a file without read access, or a PDF that PDFKit cannot open.
  - `invalid`: arguments other than the call above. The bridge runs the helper without arguments only after a
    document that got no answer (a crash): an `invalid` answer shows that the helper itself works, so only that
    document is marked unreadable.

`documents-helper warm [--max-seconds 180]` reads a small picture drawn in the helper, no file: `{"ok": true}`. See "First scan after a build".

The defaults are the owner's limits: 300 pages, 60 seconds.

## What the helper never does

- Never follows a symlink: the file itself must be a regular file (the folders on the way are followed).
- Never waits on a pipe, and never reads a file that changed between the check and the open.
- Opens no socket in its own code, and needs no network: a scan is read with every network connection denied by macOS (`sandbox-exec`, tested).
- Never writes a file (macOS keeps its own copy of the prepared Vision model; see "First scan after a build").

## Reading

- **Text pages:** PDFKit's text, about 1 ms per page.
- **Scanned pages** (no text of their own): drawn as shown (crop box, rotation) at 300 dots per inch, at most 4096 pixels on the longest side, then read by Vision's text recognition at its accurate level, on the Mac. Languages: Estia's 11 (all in Vision's list on macOS 27) with Vision's automatic detection; without detection, Japanese and Korean pages come back empty (measured).
- A page with any text of its own is never sent to Vision: a scan with a stamped page number gives only the page number.

## Measured (Mac mini M6, macOS 27.0.1, 14 Oct 2026)

`DOCUMENTS_MEASURE=1 swift test --filter Measurements` repeats these.

- **Speed:** 315 ms per scanned A4 page with 384 words; 1.3 ms per text page. 60 seconds hold about 190 dense scanned pages: a 300-page scan ends in `timeout`.
- **Memory:** at most 133 MB over 60 scanned pages: each page's picture is let go after the page (583 MB without that).
- **Accuracy on clean synthetic scans:** 0 letters wrong in 20 English pages. Over 10 sample pages (English, Polish, German, French, Russian, Dutch, Japanese, two columns; 8 to 12 points), about 1 letter in 120 wrong: mostly accents (French) and a letter at the start or end of a line (10-point Times). At 144 or 216 dots per inch, 5 to 7 letters in 100 wrong, and one German page read empty. Real scans (skew, noise, stamps) are untested.
- **Two columns, text PDF:** read one column after the other, left first, even when the file writes the right column first.
- **Two columns, scanned:** over 18 layouts (Helvetica and Times, 10 to 12 points, columns 18 to 36 points apart): 14 read with at most 2 letters wrong; 1 mixed the lines of the two columns (12-point Helvetica, 18 points apart); 3 lost words (10-point Times).
- **Tables, text PDF:** read in the file's own order. A table written row by row (Word, Excel and most tools) gives one line per row, cells separated by spaces. A table written column by column gives one cell per line, column after column.
- **Tables, scanned:** one cell per line, column after column. Vision's document request (macOS 26) reads tables row by row but gets about 8 times more letters wrong on plain text: not used.

## First scan after a build

macOS prepares Vision's model for the Neural Engine the first time a new build of the helper reads a scan: 50 to 70 seconds, measured (longer when two start at once), at about 5% CPU in the helper while the system's compiler service (ANECompilerService) works. A copy in a new folder or a rebuild in the same place starts it again; the next scans take their usual time. Two builds of the helper take turns: after the debug build (`swift test`) reads a scan, the release build waits again, and the other way round (measured: release 0.08 s, then debug 68 s, then release 60 s). So `scripts/verify.sh`, which runs both, waits about two minutes for it; a release has one build. Without a warm-up, the first scanned document after a deploy likely ends in `timeout`. The bridge runs `documents-helper warm` at each start, as the user it runs as (the preparation is untested across users and restarts).

## Build and test

```
cd tools/documents
swift test                      # offline; the first run of a new build waits for Vision's model (see above)
swift build -c release          # .build/…/Release/documents-helper
```

The tests make their own PDFs (text, two columns, table, scans, turned and cropped scans, password-protected) in temporary folders.

`scripts/run.sh` and `scripts/verify.sh` build the helper at `.build/release/documents-helper`, the bridge's default path. `scripts/verify.sh` builds it before the bridge's tests: `tests/test_documents_e2e.py` has the bridge read text and scanned PDFs with it, index them and search them.

## In a release

`estia-deploy` builds the helper as the owner from the deployed commit's own sources (`swift build -c release --product documents-helper`, in a scratch folder of its own). Only the program is kept, at `.build/release/documents-helper`, the same path as in a checkout. It must then read `check.pdf` (one text page, written by hand: no producer, no dates); any other answer stops the deploy before anything goes live. The deploy code runs as root from the installed tools: a live install builds the helper only after `estia-deploy --update-tools` and one more deploy.
