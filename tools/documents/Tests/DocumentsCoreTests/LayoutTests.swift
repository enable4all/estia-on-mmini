import Foundation
import PDFKit
import Testing

@testable import DocumentsCore

/// What PDFKit (and Vision, for a scan) make of a layout: measured here so the docs state the limit honestly.
@Suite(.timeLimit(.minutes(5))) struct LayoutOrder {
    private let left = "LEFT-START The tenant may keep one cat in the flat. The cat may not be left alone for more than two days. LEFT-END"
    private let right = "RIGHT-START Rent is due on the first day of each month and is paid by bank transfer to the landlord. RIGHT-END"
    private let rows = [
        ["Item", "Due", "Amount"],
        ["Rent", "1 March", "EUR 950"],
        ["Service charge", "1 March", "EUR 80"],
        ["Deposit", "At signing", "EUR 1900"],
    ]

    private func text(_ data: Data, recognizer: PageRecognizer = FakeRecognizer()) throws -> String {
        let reading = try Result { () throws(ReadError) in
            try DocumentReader.read(data, maxPages: 300, deadline: .now.advanced(by: .seconds(240)), recognizer: recognizer)
        }.get()
        return try #require(reading.pages.first).text
    }

    private func order(_ text: String, _ marks: [String]) -> [String] {
        marks.compactMap { mark in text.range(of: mark).map { (mark, $0.lowerBound) } }
            .sorted { $0.1 < $1.1 }.map(\.0)
    }

    @Test func twoColumnsAreReadOneColumnAfterTheOther() throws {
        let read = try text(TestPDF.columns(left: left, right: right))
        print("two columns, PDFKit:\n\(read)\n--")
        #expect(order(read, ["LEFT-START", "LEFT-END", "RIGHT-START", "RIGHT-END"]) == ["LEFT-START", "LEFT-END", "RIGHT-START", "RIGHT-END"])
        #expect(flat(read).contains(flat(left)) && flat(read).contains(flat(right)))
    }

    @Test func twoColumnsWrittenRightColumnFirstAreStillReadLeftColumnFirst() throws {
        let read = try text(TestPDF.columns(left: left, right: right, rightFirst: true))
        print("two columns written right first, PDFKit:\n\(read)\n--")
        #expect(order(read, ["LEFT-START", "LEFT-END", "RIGHT-START", "RIGHT-END"]) == ["LEFT-START", "LEFT-END", "RIGHT-START", "RIGHT-END"])
    }

    @Test func aTableIsReadRowByRowEachRowOnOneLine() throws {
        let read = try text(TestPDF.table(rows))
        print("table, PDFKit:\n\(read)\n--")
        let lines = read.split(separator: "\n").map { flat(String($0)) }
        #expect(lines == rows.map { $0.joined(separator: " ") })
    }

    /// The limit: for a table PDFKit keeps the file's own order. Word, Excel and most tools write a table row by
    /// row (the test above); a file that writes it column by column is read column by column, a cell per line.
    @Test func aTableWrittenColumnByColumnIsReadColumnByColumn() throws {
        let read = try text(TestPDF.table(rows, columnFirst: true))
        print("table written column by column, PDFKit:\n\(read)\n--")
        let lines = read.split(separator: "\n").map { flat(String($0)) }
        #expect(lines == (0..<3).flatMap { column in rows.map { $0[column] } })
    }

    /// Vision on a scan: column after column here (11-point Times, 25 points apart). Not always: over 18 layouts
    /// (Helvetica and Times, 10 to 12 points, 18 to 36 points apart; `Measurements`), 14 were read with at most
    /// 2 letters wrong, 1 mixed lines of the two columns (12-point Helvetica 18 points apart) and 3 lost words
    /// (10-point Times).
    @Test func aScannedTwoColumnPageIsReadOneColumnAfterTheOther() throws {
        let read = try text(TestPDF.scannedColumns(left: left, right: right), recognizer: VisionRecognizer())
        print("two columns, scanned, Vision:\n\(read)\n--")
        #expect(order(read, ["LEFT-START", "LEFT-END", "RIGHT-START", "RIGHT-END"]) == ["LEFT-START", "LEFT-END", "RIGHT-START", "RIGHT-END"])
    }
}
