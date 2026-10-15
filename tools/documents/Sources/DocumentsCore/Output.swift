import Foundation

/// The one JSON line a run prints. On success: `{"ok": true, "pages": [{"n": 1, "text": "…", "ocr": false}, …],
/// "skipped": [{"n": 7, "why": "unreadable"}], "page_count": N}`; on failure: `{"ok": false, "error": "<code>"}`.
public enum Output {
    private struct Success: Encodable {
        let ok = true
        let pages: [Page]
        let skipped: [Skipped]
        let pageCount: Int

        enum CodingKeys: String, CodingKey {
            case ok, pages, skipped
            case pageCount = "page_count"
        }
    }

    private struct Failure: Encodable {
        let ok = false
        let error: String
    }

    private struct Done: Encodable {
        let ok = true
    }

    public static func line(_ result: Result<Reading, ReadError>) -> String {
        switch result {
        case .success(let reading):
            return encoded(Success(pages: reading.pages, skipped: reading.skipped, pageCount: reading.pageCount))
                ?? failure(ReadError.unreadable.rawValue)
        case .failure(let error):
            return failure(error.rawValue)
        }
    }

    /// The arguments were not the helper's own (`Arguments.parse`). The bridge sends none only to check that the
    /// helper answers at all, after a document that got no answer.
    public static let invalid = failure("invalid")

    /// `warm` done.
    public static let warmed = encoded(Done()) ?? #"{"ok":true}"#

    static func failure(_ code: String) -> String {
        encoded(Failure(error: code)) ?? #"{"error":"unreadable","ok":false}"#
    }

    private static func encoded(_ value: some Encodable) -> String? {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        guard let data = try? encoder.encode(value) else { return nil }
        return String(data: data, encoding: .utf8)
    }
}
