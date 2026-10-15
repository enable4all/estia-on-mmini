import CoreGraphics
import Foundation
import PDFKit
import Testing

@testable import DocumentsCore

/// Edits (insertions, deletions, changes) between two texts, by letter.
func edits(_ first: String, _ second: String) -> Int {
    let a = Array(first), b = Array(second)
    if a.isEmpty || b.isEmpty { return max(a.count, b.count) }
    var previous = Array(0...b.count)
    for i in 1...a.count {
        var current = [i] + Array(repeating: 0, count: b.count)
        for j in 1...b.count {
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1))
        }
        previous = current
    }
    return previous[b.count]
}

/// Numbers for the docs, printed: run them on the Mac that reads the documents, after a macOS update too.
/// `DOCUMENTS_MEASURE=1 swift test --filter Measurements` (the first recognition of a new build waits for macOS
/// to prepare Vision's model first: about a minute).
@Suite(.serialized, .enabled(if: ProcessInfo.processInfo.environment["DOCUMENTS_MEASURE"] == "1"))
struct Measurements {
    private let page = String(
        repeating: "The landlord repairs the boiler within five working days of a written notice from the tenant. ",
        count: 24)

    private func read(_ data: Data) throws -> (Reading, Double) {
        let start = ContinuousClock.now
        let reading = try Result { () throws(ReadError) in
            try DocumentReader.read(data, maxPages: 300, deadline: .now.advanced(by: .seconds(600)), recognizer: VisionRecognizer())
        }.get()
        let elapsed = ContinuousClock.now - start
        return (reading, Double(elapsed.components.seconds) + Double(elapsed.components.attoseconds) / 1e18)
    }

    @Test func secondsPerPage() throws {
        #expect(VisionRecognizer.warm())
        let count = 20
        let (text, textSeconds) = try read(TestPDF.text(Array(repeating: page, count: count)))
        let (scan, scanSeconds) = try read(TestPDF.scanned(Array(repeating: page, count: count)))
        #expect(text.pages.allSatisfy { !$0.ocr } && scan.pages.allSatisfy(\.ocr))
        let wrong = scan.pages.map { edits(flat($0.text), flat(page)) }.reduce(0, +)
        print(String(format: "PDFKit: %.1f ms per text page; Vision: %.0f ms per scanned page (A4, 300 dpi, %d words), %d letters wrong in %d pages",
            textSeconds * 1000 / Double(count), scanSeconds * 1000 / Double(count), page.split(separator: " ").count, wrong, count))
    }

    @Test func scannedColumnsOverEighteenLayouts() throws {
        let left = String(repeating: "The tenant may keep one cat in the flat. The cat may not be left alone for more than two days. ", count: 3)
        let right = String(repeating: "Rent is due on the first day of each month and is paid by bank transfer to the landlord. ", count: 3)
        var good = 0
        for font in ["Helvetica", "Times New Roman"] {
            for size in [10.0, 11.0, 12.0] {
                for gap in [18.0, 25.0, 36.0] {
                    let (reading, _) = try read(TestPDF.scannedColumns(left: left, right: right, font: font, size: size, gap: gap))
                    let wrong = edits(flat(reading.pages.first?.text ?? ""), flat(left + " " + right))
                    if wrong <= 2 { good += 1 }
                    print("scanned columns, \(font) \(size) pt, \(gap) pt apart: \(wrong) letters wrong")
                }
            }
        }
        print("scanned columns: \(good) of 18 layouts read with at most 2 letters wrong")
    }
}
