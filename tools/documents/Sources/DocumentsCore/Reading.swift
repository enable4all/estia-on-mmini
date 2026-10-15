import Foundation

/// Why a whole document was not read: exactly the codes the bridge knows, never a framework's own text.
public enum ReadError: String, Error, Equatable, Sendable {
    case notPDF = "not_pdf"
    case encrypted
    case tooBig = "too_big"
    case tooManyPages = "too_many_pages"
    case timeout
    case unreadable
}

/// One page's text: from the PDF itself (`ocr` false) or recognised by Vision from the page's picture (`ocr` true).
public struct Page: Equatable, Encodable, Sendable {
    public let n: Int
    public let text: String
    public let ocr: Bool

    public init(n: Int, text: String, ocr: Bool) {
        self.n = n
        self.text = text
        self.ocr = ocr
    }
}

/// A page that failed: left out and named, the rest of the document still read.
public struct Skipped: Equatable, Encodable, Sendable {
    public let n: Int
    public let why: String

    public init(n: Int, why: String) {
        self.n = n
        self.why = why
    }
}

/// A read document: every page is either in `pages` or in `skipped`, in page order.
public struct Reading: Equatable, Sendable {
    public let pages: [Page]
    public let skipped: [Skipped]
    public let pageCount: Int

    public init(pages: [Page], skipped: [Skipped], pageCount: Int) {
        self.pages = pages
        self.skipped = skipped
        self.pageCount = pageCount
    }
}

/// The owner's limits (14 Oct 2026): 25 MB and 300 pages per document, 60 seconds per document.
public enum Limits {
    /// 25 MB as Finder counts them (1 MB = 1,000,000 bytes): a file Finder shows as 25.1 MB is too big.
    public static let maxBytes = 25_000_000
    public static let maxPages = 300
    public static let maxSeconds = 60
    /// `warm`: the first text recognition of a new copy of the helper waits for macOS to prepare Vision's model
    /// for the Neural Engine (about a minute on an M6), so warming gets longer than a document.
    public static let warmSeconds = 180
    /// The highest values the arguments take (a typo never turns into an endless run).
    static let highestPages = 100_000
    static let highestSeconds = 86_400
}
