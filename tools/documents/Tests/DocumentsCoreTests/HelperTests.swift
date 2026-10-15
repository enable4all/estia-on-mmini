import Foundation
import Testing

@testable import DocumentsCore

/// The whole run as the bridge sees it: one JSON line, exit 0 with `"ok": true`, exit 1 with one of the error codes.
@Suite struct HelperRuns {
    private func object(_ line: String) throws -> [String: Any] {
        #expect(!line.contains("\n"))
        return try #require(try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any])
    }

    @Test func aReadingIsOneLineWithTheContractsKeys() throws {
        let line = Output.line(.success(Reading(
            pages: [Page(n: 1, text: "Line one\nLine two / \"quoted\" é", ocr: false), Page(n: 2, text: "", ocr: true)],
            skipped: [Skipped(n: 3, why: "unreadable")], pageCount: 3)))
        let json = try object(line)
        #expect(Set(json.keys) == ["ok", "pages", "skipped", "page_count"])
        #expect(json["ok"] as? Bool == true)
        #expect(json["page_count"] as? Int == 3)
        let pages = try #require(json["pages"] as? [[String: Any]])
        #expect(pages.count == 2)
        #expect(Set(pages[0].keys) == ["n", "text", "ocr"])
        #expect(pages[0]["n"] as? Int == 1)
        #expect(pages[0]["text"] as? String == "Line one\nLine two / \"quoted\" é")
        #expect(pages[0]["ocr"] as? Bool == false)
        #expect(pages[1]["ocr"] as? Bool == true)
        #expect(json["skipped"] as? [[String: AnyHashable]] == [["n": 3, "why": "unreadable"]])
        // readable as written: no escaped slashes, letters as they are
        #expect(line.contains("Line two / ") && line.contains("é"))
    }

    /// The longest answer a run can give (300 pages, each at the cap, of the characters that take the most bytes)
    /// stays under the bridge's output limit (documents.OUTPUT_LIMIT, 32 MiB), so a valid reading is never cut.
    @Test func theLongestReadingFitsTheBridgesLimit() throws {
        let worst = PageText.clean(String(repeating: "\u{1F600}\"\\\n", count: 20_000))
        let pages = (1...Limits.maxPages).map { Page(n: $0, text: worst, ocr: false) }
        let line = Output.line(.success(Reading(pages: pages, skipped: [], pageCount: Limits.maxPages)))
        #expect(line.utf8.count < 32 * 1024 * 1024)
        #expect(!line.contains("\u{2028}") && !line.contains("\u{2029}") && !line.contains("\u{85}"))
    }

    @Test(arguments: [
        (ReadError.notPDF, "not_pdf"), (.encrypted, "encrypted"), (.tooBig, "too_big"),
        (.tooManyPages, "too_many_pages"), (.timeout, "timeout"), (.unreadable, "unreadable"),
    ])
    func eachFailureIsItsCode(error: ReadError, code: String) throws {
        let json = try object(Output.line(.failure(error)))
        #expect(json as NSDictionary == ["ok": false, "error": code] as NSDictionary)
    }

    @Test func aGoodReadExitsZeroAndAFailureOne() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let pdf = try TestPDF.write(TestPDF.text(["The boiler is serviced every year."]), "manual.pdf", in: folder)
        let note = try TestPDF.write(Data("plain text".utf8), "note.pdf", in: folder)

        let good = Helper.run(["read", pdf.path, "--max-pages", "300", "--max-seconds", "60"])
        #expect(good.code == 0)
        let json = try object(good.line)
        #expect(json["ok"] as? Bool == true && json["page_count"] as? Int == 1)

        let bad = Helper.run(["read", note.path])
        #expect(bad.code == 1)
        #expect(try object(bad.line) as NSDictionary == ["ok": false, "error": "not_pdf"] as NSDictionary)
    }

    @Test func wrongArgumentsAreInvalid() throws {
        let run = Helper.run(["read", "relative.pdf"])
        #expect(run.code == 1)
        #expect(try object(run.line) as NSDictionary == ["ok": false, "error": "invalid"] as NSDictionary)
    }

    @Test(.timeLimit(.minutes(1)))
    func theWatchdogAnswersAtTheLimitWhateverTheWorkIsDoing() {
        let start = ContinuousClock.now
        let result = Watchdog.run(seconds: 0.3) { () -> Int in
            Thread.sleep(forTimeInterval: 3)
            return 1
        }
        #expect(result == nil)
        #expect(ContinuousClock.now - start < .seconds(2))
        #expect(Watchdog.run(seconds: 5) { 7 } == 7)
    }
}
