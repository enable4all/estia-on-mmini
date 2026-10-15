import CoreGraphics
import CoreText
import Foundation
import PDFKit
import Vision

/// A page without text of its own (a scanned page), read by Apple's Vision on the Mac: its text recognition at
/// the accurate level, with automatic language detection over Estia's languages. No network, no cloud.
public final class VisionRecognizer: PageRecognizer {
    /// Estia's eleven languages, in her own order (as tools/voice's `Languages.all`).
    public static let estia = ["en", "es", "it", "ko", "pt", "nl", "pl", "de", "ja", "ru", "fr"]

    /// Vision's name for each of them.
    static let visionNames = [
        "en": "en-US", "es": "es-ES", "it": "it-IT", "ko": "ko-KR", "pt": "pt-BR", "nl": "nl-NL", "pl": "pl-PL",
        "de": "de-DE", "ja": "ja-JP", "ru": "ru-RU", "fr": "fr-FR",
    ]

    public init() {}

    public func text(of page: PDFPage, number: Int) throws -> String {
        guard let image = PageImage.render(page) else { throw ReadError.unreadable }
        return try Self.recognize(image)
    }

    /// The text Vision sees in `image`, one line per line it found, in Vision's own order.
    public static func recognize(_ image: CGImage) throws -> String {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = languages(supported: (try? request.supportedRecognitionLanguages()) ?? [])
        // with the list alone, Japanese and Korean pages come back empty; detection reads them (measured)
        request.automaticallyDetectsLanguage = true
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        return lines.joined(separator: "\n")
    }

    /// Estia's languages that Vision reads at the accurate level, in Estia's order, by Vision's names.
    public static func languages(supported: [String]) -> [String] {
        estia.compactMap { visionNames[$0] }.filter(supported.contains)
    }

    /// The languages Vision reads at the accurate level on this Mac.
    public static func supported() throws -> [String] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        return try request.supportedRecognitionLanguages()
    }

    /// One recognition of a small picture drawn here (no file): the first recognition of a new copy of the helper
    /// waits while macOS prepares Vision's model for the Neural Engine (about a minute on an M6, measured); warming
    /// spends that wait before a document does. True when Vision read the picture's word.
    public static func warm() -> Bool {
        let word = "Estia"
        guard let image = picture(of: word), let seen = try? recognize(image) else { return false }
        return seen.localizedCaseInsensitiveContains(word)
    }

    private static func picture(of word: String) -> CGImage? {
        guard let context = CGContext(
            data: nil, width: 600, height: 160, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)
        else { return nil }
        context.setFillColor(gray: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: 600, height: 160))
        let font = CTFontCreateWithName("Helvetica" as CFString, 72, nil)
        let text = NSAttributedString(
            string: word, attributes: [NSAttributedString.Key(kCTFontAttributeName as String): font])
        context.textPosition = CGPoint(x: 40, y: 50)
        CTLineDraw(CTLineCreateWithAttributedString(text), context)
        return context.makeImage()
    }
}

/// A page drawn as Vision sees it: as the page is shown (its crop box, turned by its rotation), on white, in grey,
/// at 300 dots per inch (a scanner's own resolution; Vision read synthetic scans best there, measured against
/// 144 and 216) and at most 4096 pixels on its longest side.
public enum PageImage {
    static let scale: CGFloat = 300.0 / 72.0
    static let longestSide: CGFloat = 4096

    public static func render(_ page: PDFPage) -> CGImage? {
        let box = page.bounds(for: .cropBox)
        let quarterTurns = ((page.rotation % 360) + 360) % 360 / 90
        let shown = quarterTurns % 2 == 1 ? CGSize(width: box.height, height: box.width) : box.size
        guard shown.width >= 1, shown.height >= 1 else { return nil }
        let scale = min(Self.scale, longestSide / max(shown.width, shown.height))
        let width = Int((shown.width * scale).rounded())
        let height = Int((shown.height * scale).rounded())
        guard width >= 1, height >= 1,
            let context = CGContext(
                data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)
        else { return nil }
        context.setFillColor(gray: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.scaleBy(x: scale, y: scale)
        // draw(with:to:) applies the crop box and the rotation itself (a transform(_:for:) before it turns twice)
        page.draw(with: .cropBox, to: context)
        return context.makeImage()
    }
}
