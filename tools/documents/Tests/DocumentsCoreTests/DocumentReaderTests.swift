import CoreGraphics
import Foundation
import PDFKit
import Testing

@testable import DocumentsCore

/// Stands in for Vision: answers per page number, or fails for the pages in `failing` (the page cannot be drawn) and
/// in `visionFailing` (Vision's own error, as with /var/empty as Foundation's home: live, 15 Oct).
final class FakeRecognizer: PageRecognizer {
    var answers: [Int: String] = [:]
    var failing: Set<Int> = []
    var visionFailing: Set<Int> = []
    private(set) var asked: [Int] = []

    func text(of page: PDFPage, number: Int) throws -> String {
        asked.append(number)
        if failing.contains(number) { throw ReadError.unreadable }
        if visionFailing.contains(number) { throw NSError(domain: "com.apple.Vision", code: 9) }
        return answers[number] ?? ""
    }
}

/// One PDF's pages: PDFKit's text for a page that has text, the recognizer for a page that has none, a page
/// that fails skipped and named; the whole document refused for the reasons the bridge knows.
@Suite struct DocumentReading {
    private let later = ContinuousClock.now.advanced(by: .seconds(60))

    private func read(
        _ data: Data, maxPages: Int = 300, deadline: ContinuousClock.Instant? = nil, recognizer: PageRecognizer = FakeRecognizer()
    ) -> Result<Reading, ReadError> {
        Result { () throws(ReadError) in
            try DocumentReader.read(data, maxPages: maxPages, deadline: deadline ?? later, recognizer: recognizer)
        }
    }

    @Test func aTextPDFGivesEachPagesTextFromPDFKit() throws {
        let fake = FakeRecognizer()
        let reading = try read(TestPDF.text(["The tenant may keep one cat.", "Rent is due on the first day."]), recognizer: fake).get()
        #expect(reading.pageCount == 2)
        #expect(reading.pages.map(\.n) == [1, 2])
        #expect(reading.pages.map(\.ocr) == [false, false])
        #expect(reading.pages[0].text.contains("The tenant may keep one cat."))
        #expect(reading.pages[1].text.contains("Rent is due on the first day."))
        #expect(reading.skipped.isEmpty)
        #expect(fake.asked.isEmpty)
    }

    @Test func aPageWithoutTextGoesToTheRecognizerAndAFailingPageIsSkippedAndNamed() throws {
        // pages 2 and 4 have no text: 2 is recognised, 4 fails; 3 is a blank page: read, with no text
        let data = TestPDF.make(pages: 4) { context, index in
            if index == 0 { TestPDF.frame("Page one.", in: TestPDF.a4.insetBy(dx: 60, dy: 60), context: context) }
        }
        let fake = FakeRecognizer()
        fake.answers = [2: "Scanned page two."]
        fake.failing = [4]
        let reading = try read(data, recognizer: fake).get()
        #expect(fake.asked == [2, 3, 4])
        #expect(reading.pageCount == 4)
        #expect(reading.pages == [
            Page(n: 1, text: reading.pages[0].text, ocr: false),
            Page(n: 2, text: "Scanned page two.", ocr: true),
            Page(n: 3, text: "", ocr: true),
        ])
        #expect(reading.pages[0].text.contains("Page one."))
        #expect(reading.skipped == [Skipped(n: 4, why: "unreadable")])
    }

    @Test func aPageVisionFailsOnIsNamedApartFromAPageThatCannotBeRead() throws {
        // Vision's own failure is text recognition failing, not the page: the bridge reads the document again after
        // a start, and the part shows its warning
        let data = TestPDF.make(pages: 3) { _, _ in }
        let fake = FakeRecognizer()
        fake.answers = [1: "Scanned page one."]
        fake.failing = [2]
        fake.visionFailing = [3]
        let reading = try read(data, recognizer: fake).get()
        #expect(reading.pages == [Page(n: 1, text: "Scanned page one.", ocr: true)])
        #expect(reading.skipped == [Skipped(n: 2, why: "unreadable"), Skipped(n: 3, why: "ocr_failed")])
    }

    @Test func aPageWithOnlySpacesCountsAsAPageWithoutText() throws {
        let data = TestPDF.text(["   "])
        let fake = FakeRecognizer()
        fake.answers = [1: "From the picture."]
        let reading = try read(data, recognizer: fake).get()
        #expect(reading.pages == [Page(n: 1, text: "From the picture.", ocr: true)])
    }

    @Test func moreThanTheMaximumOfPagesIsRefusedBeforeAPageIsRead() {
        let fake = FakeRecognizer()
        let data = TestPDF.make(pages: 3) { _, _ in }
        #expect(read(data, maxPages: 2, recognizer: fake) == .failure(.tooManyPages))
        #expect(fake.asked.isEmpty)
        #expect((try? read(data, maxPages: 3, recognizer: fake).get())?.pageCount == 3)
    }

    @Test func aDocumentPastItsTimeStopsWithTimeout() {
        let fake = FakeRecognizer()
        let data = TestPDF.make(pages: 2) { _, _ in }
        #expect(read(data, deadline: .now.advanced(by: .seconds(-1)), recognizer: fake) == .failure(.timeout))
        #expect(fake.asked.isEmpty)
    }

    @Test func aPDFLockedByAPasswordIsEncrypted() {
        let locked = TestPDF.text(["Private."], info: [kCGPDFContextUserPassword: "user-pass", kCGPDFContextOwnerPassword: "owner-pass"])
        #expect(read(locked) == .failure(.encrypted))
    }

    @Test func aPDFWithOnlyAnOwnerPasswordOpensWithoutOneAndIsRead() throws {
        // limits on printing or copying only: the file opens without a password, as in Preview
        let limited = TestPDF.text(
            ["Opens for anyone."], info: [kCGPDFContextOwnerPassword: "owner-pass", kCGPDFContextAllowsCopying: false])
        #expect(PDFDocument(data: limited)?.isEncrypted == true)
        let reading = try read(limited).get()
        #expect(reading.pages.first?.text.contains("Opens for anyone.") == true)
    }

    @Test func aFileThatOnlyStartsLikeAPDFIsUnreadable() {
        #expect(read(Data("%PDF-1.7\nnot really a document\n".utf8)) == .failure(.unreadable))
    }

    @Test func aPDFWithNoPagesIsUnreadable() {
        let empty = TestPDF.raw(["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [] /Count 0 >>"])
        #expect(read(empty) == .failure(.unreadable))
    }

    @Test func pageTextKeepsItsLinesWithLineFeedsOnlyAndNoNulCharacters() {
        #expect(PageText.clean("one\r\ntwo\rthree\u{0}\nfour") == "one\ntwo\nthree\nfour")
        #expect(PageText.clean("  \n\t ").isEmpty)
        #expect(PageText.clean("  Lease  \n") == "  Lease  \n")
    }

    /// JSON leaves U+2028, U+2029 and U+0085 unescaped, and other readers end a line there: they become line feeds.
    /// Other control characters (escaped as six bytes each) become spaces; tabs stay.
    @Test func pageTextEndsLinesOnlyWithLineFeedsAndHasNoControlCharacters() {
        #expect(PageText.clean("one\u{2028}two\u{2029}three\u{85}four") == "one\ntwo\nthree\nfour")
        #expect(PageText.clean("a\u{1}b\u{1B}c\u{7F}d\te") == "a b c d\te")
    }

    /// A page's text is capped as the bridge caps it (20,000 characters), so 300 pages always fit its output limit.
    @Test func pageTextIsCappedAtTheBridgesLimit() {
        let long = PageText.clean(String(repeating: "\u{1F600}", count: 30_000))
        #expect(long.unicodeScalars.count == PageText.maxCharacters && PageText.maxCharacters == 20_000)
        #expect(PageText.clean("short") == "short")
    }
}
