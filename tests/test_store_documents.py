"""The documents index in her database (schema v10): files keyed by their path in the folder, size and change time;
each page's text in a full-text index; a deleted file's rows gone, its words merged out of the index."""

import sqlite3
from pathlib import Path

from local_assistant.store import SCHEMA_VERSION, DocPage, Store
from tests.conftest import ME, T0

LEASE = [
    DocPage(1, "Lease agreement between Alex Example and Example Homes.", False),
    DocPage(2, "Pets: one cat or one small dog is allowed with the landlord's written consent.", False),
    DocPage(3, "Rent is due on the first day of each month.", True),
]


def test_schema_10_has_the_documents_index(store: Store) -> None:
    assert SCHEMA_VERSION == 10
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 10
    files = {row[1] for row in store._conn.execute("PRAGMA table_info(doc_files)")}
    assert {"path", "size", "mtime_ns", "pages", "skipped", "error", "source", "announced", "found_at"} <= files
    assert {"created_at", "updated_at"} <= files
    pages = {row[1] for row in store._conn.execute("PRAGMA table_info(doc_pages)")}
    assert {"file_id", "page", "ocr", "text", "created_at", "updated_at"} <= pages
    assert "filename" in {row[1] for row in store._conn.execute("PRAGMA table_info(attachments)")}


def test_upgrade_from_version_9_adds_the_index_and_keeps_attachments(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    old = Store.open(path)
    conv = old.current_conversation()
    message = old.add_incoming(conv.id, ME, T0, "text", "[photo] hi")
    assert message is not None
    old.add_attachment(message, "a1.jpeg", "image/jpeg", 10)
    old.close()
    conn = sqlite3.connect(path)
    for name in ("doc_pages_fts", "doc_pages", "doc_files"):
        conn.execute(f"DROP TABLE {name}")
    conn.execute("ALTER TABLE attachments DROP COLUMN filename")
    conn.execute("PRAGMA user_version = 9")
    conn.commit()
    conn.close()
    store = Store.open(path)
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 10
    assert [(a.signal_id, a.filename) for a in store.attachments(message)] == [("a1.jpeg", "")]
    assert store.doc_files() == []
    store.close()


def test_an_attachment_keeps_the_name_it_was_sent_with(store: Store) -> None:
    conv = store.current_conversation()
    message = store.add_incoming(conv.id, ME, T0, "text", "[document] lease.pdf")
    assert message is not None
    store.add_attachment(message, "a2.pdf", "application/pdf", 2000, filename="Lease 2026.pdf")
    assert store.attachments(message)[0].filename == "Lease 2026.pdf"


def test_a_read_file_and_its_pages(store: Store) -> None:
    file_id = store.put_doc_file("lease.pdf", 2000, 111, LEASE, page_count=3, source="folder")
    found = store.doc_file(file_id)
    assert found is not None and (found.path, found.size, found.mtime_ns, found.pages) == ("lease.pdf", 2000, 111, 3)
    assert (found.error, found.source, found.announced, found.skipped) == ("", "folder", False, ())
    assert store.doc_file_by_path("lease.pdf") == found
    page = store.doc_page(file_id, 2)
    assert page is not None and "cat" in page.text and not page.ocr
    third = store.doc_page(file_id, 3)
    assert third is not None and third.ocr  # read by Vision
    assert store.doc_page(file_id, 4) is None


def test_a_changed_file_keeps_its_number_and_gets_its_new_text(store: Store) -> None:
    file_id = store.put_doc_file("lease.pdf", 2000, 111, LEASE, page_count=3, source="signal", announced=True)
    again = store.put_doc_file("lease.pdf", 2100, 222, [DocPage(1, "A new lease: no pets.", False)], page_count=1)
    assert again == file_id
    found = store.doc_file(file_id)
    assert found is not None and (found.size, found.mtime_ns, found.pages, found.source, found.announced) == (
        2100,
        222,
        1,
        "signal",
        True,
    )
    assert [hit.page for hit in store.search_doc_pages("cat dog")] == []  # the old text is gone from the index
    assert [hit.page for hit in store.search_doc_pages("pets")] == [1]


def test_search_finds_passages_with_document_and_page(store: Store) -> None:
    lease = store.put_doc_file("2026/lease.pdf", 2000, 111, LEASE, page_count=3, source="folder")
    manual = [DocPage(1, "Boiler manual. Reset: hold the button for five seconds.", False)]
    store.put_doc_file("boiler-manual.pdf", 900, 112, manual, page_count=1, source="folder")
    hits = store.search_doc_pages("Are PETS allowed? a dog")
    assert [(hit.file_id, hit.path, hit.page) for hit in hits][:1] == [(lease, "2026/lease.pdf", 2)]
    assert "dog" in hits[0].passage
    assert store.search_doc_pages("?!") == [] and store.search_doc_pages('"zebra" NEAR(xylophone)') == []


def test_a_file_that_could_not_be_read_keeps_why_and_no_pages(store: Store) -> None:
    file_id = store.put_doc_file("secret.pdf", 3000, 113, [], page_count=0, error="encrypted", source="folder")
    found = store.doc_file(file_id)
    assert found is not None and found.error == "encrypted" and found.pages == 0
    skipped = store.put_doc_file(
        "scan.pdf", 4000, 114, LEASE[:1], page_count=2, skipped=((2, "unreadable"),), source="folder"
    )
    scan = store.doc_file(skipped)
    assert scan is not None and scan.skipped == ((2, "unreadable"),)


def test_removed_files_leave_no_rows_and_no_words(store: Store) -> None:
    lease = store.put_doc_file("lease.pdf", 2000, 111, LEASE, page_count=3, source="folder")
    store.put_doc_file("other.pdf", 10, 1, [DocPage(1, "Other words.", False)], page_count=1, source="folder")
    store.remove_doc_files([lease])
    assert store.doc_file(lease) is None and store.doc_page(lease, 2) is None
    assert store.search_doc_pages("cat") == []
    rows = store._conn.execute("SELECT count(*) FROM doc_pages WHERE file_id = ?", (lease,)).fetchone()[0]
    assert rows == 0
    assert [file.path for file in store.doc_files()] == ["other.pdf"]


def test_a_fresh_index_drops_everything_and_never_reuses_a_number(store: Store) -> None:
    first = store.put_doc_file("lease.pdf", 2000, 111, LEASE, page_count=3, source="folder")
    store.clear_documents()
    assert store.doc_files() == [] and store.search_doc_pages("cat") == []
    again = store.put_doc_file("lease.pdf", 2000, 111, LEASE, page_count=3, source="folder")
    assert again > first  # /docs send N never reaches another document


def test_newest_first_and_the_announcement(store: Store) -> None:
    older = store.put_doc_file("a.pdf", 1, 1, [], page_count=0, source="folder", found_at="2026-10-13T08:00:00+00:00")
    newer = store.put_doc_file("b.pdf", 1, 1, [], page_count=0, source="folder", found_at="2026-10-14T08:00:00+00:00")
    saved = store.put_doc_file(
        "c.pdf", 1, 1, [], page_count=0, source="signal", announced=True, found_at="2026-10-14T09:00:00+00:00"
    )
    assert [file.id for file in store.doc_files()] == [saved, newer, older]
    assert [file.id for file in store.unannounced_doc_files()] == [older, newer]
    store.mark_doc_files_announced([older, newer])
    assert store.unannounced_doc_files() == []


def test_retention_never_touches_the_documents(store: Store) -> None:
    """The owner keeps these documents: [privacy] keep_days prunes the history, never their text."""
    lease = store.put_doc_file(
        "lease.pdf", 2000, 111, LEASE, page_count=3, source="folder", found_at="2020-01-01T00:00:00+00:00"
    )
    store.prune(1, now=2_000_000_000.0)
    assert store.doc_file(lease) is not None and [hit.page for hit in store.search_doc_pages("cat")] == [2]
