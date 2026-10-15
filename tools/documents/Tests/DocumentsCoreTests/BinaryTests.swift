import Foundation
import Testing

/// The built `documents-helper` itself, run as the bridge runs it.
@Suite(.serialized, .timeLimit(.minutes(5))) struct HelperBinary {
    /// `swift test` builds the helper in the products folder that holds the test bundle: found from the test
    /// bundle's own file, whichever build folder SwiftPM uses.
    private func binary() throws -> URL {
        var info = Dl_info()
        try #require(dladdr(#dsohandle, &info) != 0 && info.dli_fname != nil)
        var folder = URL(fileURLWithPath: String(cString: info.dli_fname)).deletingLastPathComponent()
        while folder.path != "/" {
            let candidate = folder.appendingPathComponent("documents-helper")
            if FileManager.default.isExecutableFile(atPath: candidate.path) { return candidate }
            folder = folder.deletingLastPathComponent()
        }
        throw HelperNotBuilt()
    }

    private struct HelperNotBuilt: Error {}

    private struct Run {
        let code: Int32
        let stdout: String
        let seconds: Double
    }

    private func run(_ executable: URL, _ arguments: [String]) throws -> Run {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = [:]
        let out = Pipe()
        process.standardOutput = out
        process.standardError = FileHandle.nullDevice
        let start = Date()
        try process.run()
        let data = out.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return Run(
            code: process.terminationStatus, stdout: String(decoding: data, as: UTF8.self),
            seconds: Date().timeIntervalSince(start))
    }

    private func json(_ run: Run) throws -> [String: Any] {
        #expect(run.stdout.hasSuffix("\n") && run.stdout.filter { $0 == "\n" }.count == 1)
        return try #require(try JSONSerialization.jsonObject(with: Data(run.stdout.utf8)) as? [String: Any])
    }

    @Test func itPrintsExactlyOneJSONLineAndExitsZero() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let pdf = try TestPDF.write(TestPDF.text(["First line.\nSecond line.", "Page two."]), "two.pdf", in: folder)
        let done = try run(binary(), ["read", pdf.path, "--max-pages", "300", "--max-seconds", "60"])
        #expect(done.code == 0)
        let object = try json(done)
        #expect(object["ok"] as? Bool == true && object["page_count"] as? Int == 2)
        let refused = try run(binary(), ["read", folder.path])
        #expect(refused.code == 1)
        #expect(try json(refused) as NSDictionary == ["ok": false, "error": "unreadable"] as NSDictionary)
    }

    /// No network: the helper reads a scanned page with every network connection denied by the kernel
    /// (`deny network*` covers local sockets too), so Vision and PDFKit need none.
    @Test func itReadsAScanWithEveryNetworkConnectionDenied() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let pdf = try TestPDF.write(TestPDF.scanned(["Read with the network denied."]), "scan.pdf", in: folder)
        let done = try run(
            URL(fileURLWithPath: "/usr/bin/sandbox-exec"),
            ["-p", "(version 1)(allow default)(deny network*)", try binary().path, "read", pdf.path, "--max-seconds", "240"])
        print("scan under sandbox-exec: \(done.seconds) s, \(done.stdout)")
        #expect(done.code == 0)
        let pages = try #require(try json(done)["pages"] as? [[String: Any]])
        #expect(pages.first?["ocr"] as? Bool == true)
        #expect(flat(pages.first?["text"] as? String ?? "") == "Read with the network denied.")
    }

    /// Each page's pictures and PDFKit's objects are let go after the page: 60 scanned pages (an 8.7 MB picture
    /// each) stay far below what keeping them all would take.
    @Test func memoryStaysFlatOverALongScan() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let page = String(repeating: "The landlord repairs the boiler within five working days of a written notice. ", count: 20)
        let pdf = try TestPDF.write(TestPDF.scanned(Array(repeating: page, count: 60)), "long.pdf", in: folder)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/time")
        process.arguments = ["-l", try binary().path, "read", pdf.path, "--max-seconds", "240"]
        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err
        try process.run()
        let stdout = out.fileHandleForReading.readDataToEndOfFile()
        let stderr = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        process.waitUntilExit()
        let line = try #require(stderr.split(separator: "\n").first { $0.contains("maximum resident set size") })
        let peak = try #require(Int(line.split(separator: " ").first ?? ""))
        print("60 scanned pages: peak memory \(peak / 1_000_000) MB")
        #expect(process.terminationStatus == 0)
        let object = try #require(try JSONSerialization.jsonObject(with: stdout) as? [String: Any])
        #expect((object["pages"] as? [Any])?.count == 60, "skipped: \(object["skipped"] ?? "none")")
        #expect(peak < 250_000_000)
    }

    @Test func aDocumentPastItsLimitAnswersTimeout() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let page = String(repeating: "The landlord repairs the boiler within five working days of a written notice. ", count: 30)
        let pdf = try TestPDF.write(TestPDF.scanned(Array(repeating: page, count: 40)), "long-scan.pdf", in: folder)
        let done = try run(binary(), ["read", pdf.path, "--max-seconds", "1"])
        #expect(done.code == 1)
        #expect(try json(done) as NSDictionary == ["ok": false, "error": "timeout"] as NSDictionary)
        #expect(done.seconds < 3)
    }
}
