import CoreGraphics
import CoreText
import Foundation
import PDFKit

/// PDFs made in the tests with Apple's own drawing (CoreGraphics, CoreText): no fixture files in the repository.
enum TestPDF {
    static let a4 = CGRect(x: 0, y: 0, width: 595, height: 842)

    /// A new, empty folder of its own under the temporary folder; the caller removes it.
    static func folder() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("documents-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false)
        return url
    }

    /// One PDF; `draw` is called once per page, with the page's index.
    static func make(
        pages: Int, box: CGRect = a4, info: [CFString: Any] = [:], draw: (CGContext, Int) -> Void
    ) -> Data {
        let data = NSMutableData()
        var media = box
        let consumer = CGDataConsumer(data: data as CFMutableData)!
        let context = CGContext(consumer: consumer, mediaBox: &media, info.isEmpty ? nil : info as CFDictionary)!
        for index in 0..<pages {
            context.beginPDFPage(nil)
            draw(context, index)
            context.endPDFPage()
        }
        context.closePDF()
        return data as Data
    }

    static func font(_ size: CGFloat, _ name: String = "Helvetica") -> CTFont {
        CTFontCreateWithName(name as CFString, size, nil)
    }

    /// `text` set in a frame (CoreText wraps the lines), in the context's own coordinates.
    static func frame(_ text: String, in rect: CGRect, size: CGFloat = 11, context: CGContext) {
        let attributed = NSAttributedString(
            string: text, attributes: [NSAttributedString.Key(kCTFontAttributeName as String): font(size)])
        let setter = CTFramesetterCreateWithAttributedString(attributed)
        let frame = CTFramesetterCreateFrame(setter, CFRange(location: 0, length: 0), CGPath(rect: rect, transform: nil), nil)
        CTFrameDraw(frame, context)
    }

    /// One line of `text` with its baseline at `point`.
    static func line(_ text: String, at point: CGPoint, size: CGFloat = 11, context: CGContext) {
        let attributed = NSAttributedString(
            string: text, attributes: [NSAttributedString.Key(kCTFontAttributeName as String): font(size)])
        context.textPosition = point
        CTLineDraw(CTLineCreateWithAttributedString(attributed), context)
    }

    /// A text PDF: one page per string, the text in the page's margins.
    static func text(_ pages: [String], info: [CFString: Any] = [:]) -> Data {
        make(pages: pages.count, info: info) { context, index in
            frame(pages[index], in: a4.insetBy(dx: 60, dy: 60), context: context)
        }
    }

    /// A two-column page: `left` in the left half, `right` in the right half, the same top and line height.
    /// `rightFirst` draws the right column first, so the file's own order differs from the reading order.
    static func columns(left: String, right: String, rightFirst: Bool = false) -> Data {
        make(pages: 1) { context, _ in
            let leftRect = CGRect(x: 50, y: 60, width: 235, height: 722)
            let rightRect = CGRect(x: 310, y: 60, width: 235, height: 722)
            if rightFirst {
                frame(right, in: rightRect, context: context)
                frame(left, in: leftRect, context: context)
            } else {
                frame(left, in: leftRect, context: context)
                frame(right, in: rightRect, context: context)
            }
        }
    }

    /// A ruled table: `rows[0]` is the header; each cell's text drawn at its own place, row by row
    /// (or column by column with `columnFirst`).
    static func table(_ rows: [[String]], columnFirst: Bool = false) -> Data {
        make(pages: 1) { context, _ in
            let left: CGFloat = 60, top: CGFloat = 760, width: CGFloat = 150, height: CGFloat = 24
            let columns = rows[0].count
            context.setLineWidth(0.5)
            for r in 0...rows.count {
                context.move(to: CGPoint(x: left, y: top - CGFloat(r) * height))
                context.addLine(to: CGPoint(x: left + CGFloat(columns) * width, y: top - CGFloat(r) * height))
            }
            for c in 0...columns {
                context.move(to: CGPoint(x: left + CGFloat(c) * width, y: top))
                context.addLine(to: CGPoint(x: left + CGFloat(c) * width, y: top - CGFloat(rows.count) * height))
            }
            context.strokePath()
            let cells = columnFirst
                ? (0..<columns).flatMap { c in rows.indices.map { r in (r, c) } }
                : rows.indices.flatMap { r in (0..<columns).map { c in (r, c) } }
            for (r, c) in cells {
                let point = CGPoint(x: left + CGFloat(c) * width + 6, y: top - CGFloat(r + 1) * height + 8)
                line(rows[r][c], at: point, context: context)
            }
        }
    }

    /// A scanner's resolution: 300 dots per inch.
    static let scan: CGFloat = 300.0 / 72.0

    /// A white bitmap of `size` points at `scale` pixels per point, with `draw` in point coordinates.
    static func bitmap(_ size: CGSize, scale: CGFloat = scan, draw: (CGContext) -> Void) -> CGImage {
        let context = CGContext(
            data: nil, width: Int(size.width * scale), height: Int(size.height * scale), bitsPerComponent: 8,
            bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: context.width, height: context.height))
        context.scaleBy(x: scale, y: scale)
        draw(context)
        return context.makeImage()!
    }

    /// A scanned page: the text only as a picture of an A4 page at 300 dots per inch, so the page has no text of its own.
    /// `rotated` draws the picture turned a quarter left on a landscape page and marks the page to be shown
    /// turned a quarter right (/Rotate 90), as some scanners do: shown, it reads upright.
    static func scanned(_ pages: [String], rotated: Bool = false, columns: (String, String)? = nil) -> Data {
        let images = pages.map { text in
            bitmap(a4.size) { context in frame(text, in: a4.insetBy(dx: 60, dy: 60), size: 12, context: context) }
        }
        let box = rotated ? CGRect(x: 0, y: 0, width: a4.height, height: a4.width) : a4
        let data = make(pages: pages.count, box: box) { context, index in
            if rotated {
                context.translateBy(x: a4.height, y: 0)
                context.rotate(by: .pi / 2)
            }
            context.draw(images[index], in: a4)
        }
        guard rotated, let document = PDFDocument(data: data) else { return data }
        for index in 0..<document.pageCount { document.page(at: index)?.rotation = 90 }
        return document.dataRepresentation()!
    }

    /// A scanned two-column page (a picture only): two columns of `size` point text in `font`, `gap` points apart.
    static func scannedColumns(
        left: String, right: String, font name: String = "Times New Roman", size: CGFloat = 11, gap: CGFloat = 25
    ) -> Data {
        let width = (495 - gap) / 2
        let image = bitmap(a4.size) { context in
            for (text, x) in [(left, 50), (right, 50 + width + gap)] {
                let attributed = NSAttributedString(
                    string: text, attributes: [NSAttributedString.Key(kCTFontAttributeName as String): font(size, name)])
                let setter = CTFramesetterCreateWithAttributedString(attributed)
                let rect = CGRect(x: x, y: 60, width: width, height: 722)
                CTFrameDraw(CTFramesetterCreateFrame(setter, CFRange(location: 0, length: 0), CGPath(rect: rect, transform: nil), nil), context)
            }
        }
        return make(pages: 1) { context, _ in context.draw(image, in: a4) }
    }

    /// A PDF written by hand from its objects (object 1 the catalogue), with a correct cross-reference table:
    /// for files CoreGraphics never makes (it adds an empty page to a PDF with none).
    static func raw(_ objects: [String]) -> Data {
        var out = "%PDF-1.7\n"
        var offsets: [Int] = []
        for (index, body) in objects.enumerated() {
            offsets.append(out.utf8.count)
            out += "\(index + 1) 0 obj\n\(body)\nendobj\n"
        }
        let xref = out.utf8.count
        out += "xref\n0 \(objects.count + 1)\n0000000000 65535 f \n"
        for offset in offsets { out += String(format: "%010d 00000 n \n", offset) }
        out += "trailer\n<< /Size \(objects.count + 1) /Root 1 0 R >>\nstartxref\n\(xref)\n%%EOF\n"
        return Data(out.utf8)
    }

    /// Writes `data` as `name` in `folder`.
    @discardableResult
    static func write(_ data: Data, _ name: String, in folder: URL) throws -> URL {
        let url = folder.appendingPathComponent(name)
        try data.write(to: url)
        return url
    }
}
