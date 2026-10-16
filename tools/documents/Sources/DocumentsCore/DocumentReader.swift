import Foundation
import PDFKit

/// Reads a page that has no text of its own (a scanned page): Vision in the helper, a fake in the tests.
public protocol PageRecognizer {
    /// The page's text, or an error when the page cannot be read; `number` counts from 1.
    func text(of page: PDFPage, number: Int) throws -> String
}

/// One PDF, page by page: PDFKit's own text where a page has text, the recognizer where it has none. A page that
/// fails is skipped and named (`PageFailure`); the document as a whole is refused only for the reasons in `ReadError`.
public enum DocumentReader {
    public static func read(
        _ data: Data, maxPages: Int, deadline: ContinuousClock.Instant, recognizer: PageRecognizer
    ) throws(ReadError) -> Reading {
        guard let document = PDFDocument(data: data) else { throw .unreadable }
        // only an owner password (limits on printing or copying) opens with the empty one, as in Preview
        if document.isLocked && !document.unlock(withPassword: "") { throw .encrypted }
        let count = document.pageCount
        guard count > 0 else { throw .unreadable }
        guard count <= maxPages else { throw .tooManyPages }

        var pages: [Page] = []
        var skipped: [Skipped] = []
        for index in 0..<count {
            guard ContinuousClock.now < deadline else { throw .timeout }
            let number = index + 1
            // each page's objects (a scan's picture is 8.7 MB) are let go after the page, not at the end
            switch autoreleasepool(invoking: { readPage(document.page(at: index), number: number, recognizer: recognizer) }) {
            case .success(let page): pages.append(page)
            case .failure(let why): skipped.append(Skipped(n: number, why: why.rawValue))
            }
        }
        return Reading(pages: pages, skipped: skipped, pageCount: count)
    }

    /// One page: its own text, or the recognizer's when it has none. The recognizer's `ReadError.unreadable` is the
    /// page's (it cannot be drawn); any other error is Vision's own: text recognition failed, not the page.
    private static func readPage(_ page: PDFPage?, number: Int, recognizer: PageRecognizer) -> Result<Page, PageFailure> {
        guard let page else { return .failure(.unreadable) }
        let own = PageText.clean(page.string ?? "")
        if !own.isEmpty { return .success(Page(n: number, text: own, ocr: false)) }
        do {
            let seen = try recognizer.text(of: page, number: number)
            return .success(Page(n: number, text: PageText.clean(seen), ocr: true))
        } catch ReadError.unreadable {
            return .failure(.unreadable)
        } catch {
            return .failure(.ocrFailed)
        }
    }
}

/// Why a page was skipped: it cannot be read ("unreadable"), or Vision failed on it ("ocr_failed": live, 15 Oct, with
/// /var/empty as Foundation's home every scan failed at once). The bridge reads a document with an "ocr_failed" page
/// again after a start, and shows text recognition's warning.
public enum PageFailure: String, Error, Sendable {
    case unreadable
    case ocrFailed = "ocr_failed"
}

/// A page's text as the bridge stores it: lines end with line feeds only (U+2028, U+2029 and U+0085 too: JSON leaves
/// them unescaped, and other readers end a line there), no NUL, other control characters as spaces (tabs stay), at
/// most `maxCharacters`; only white space counts as none. So 300 pages always fit the bridge's output limit.
public enum PageText {
    /// As the bridge caps a page (documents.PAGE_CHARS), in Unicode scalars, which Python counts as characters.
    public static let maxCharacters = 20_000

    public static func clean(_ text: String) -> String {
        if text.allSatisfy({ $0.isWhitespace }) { return "" }
        var scalars = String.UnicodeScalarView()
        var kept = 0
        var previous: Unicode.Scalar?
        for scalar in text.unicodeScalars {
            defer { previous = scalar }
            switch scalar {
            case "\u{0}": continue
            case "\n" where previous == "\r": continue  // \r\n: its \r already gave the line feed
            case "\r", "\u{2028}", "\u{2029}", "\u{85}": scalars.append("\n")
            case "\n", "\t": scalars.append(scalar)
            case _ where scalar.value < 0x20 || scalar.value == 0x7F: scalars.append(" ")
            default: scalars.append(scalar)
            }
            kept += 1
            if kept == maxCharacters { break }
        }
        return String(scalars)
    }
}
