import CoreGraphics
import Foundation
import PDFKit
import Testing

@testable import DocumentsCore

/// White space collapsed to single spaces: Vision's line breaks are not the text's.
func flat(_ text: String) -> String {
    text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
}

/// Scanned pages: no text of their own, so Vision reads the page's picture, on the Mac.
/// The first text recognition of a new build waits for macOS to prepare Vision's model (about a minute).
@Suite(.timeLimit(.minutes(5))) struct ScannedPages {
    private let lease = "The tenant may keep one cat. Rent is due on the first day of each month."

    private func read(_ data: Data) throws -> Reading {
        try Result { () throws(ReadError) in
            try DocumentReader.read(
                data, maxPages: 300, deadline: .now.advanced(by: .seconds(240)), recognizer: VisionRecognizer())
        }.get()
    }

    @Test func estiasElevenLanguagesAreAllInVisionsOwnList() throws {
        #expect(VisionRecognizer.estia == ["en", "es", "it", "ko", "pt", "nl", "pl", "de", "ja", "ru", "fr"])
        #expect(VisionRecognizer.languages(supported: ["fr-FR", "en-US", "zh-Hans"]) == ["en-US", "fr-FR"])
        let all = ["en-US", "es-ES", "it-IT", "ko-KR", "pt-BR", "nl-NL", "pl-PL", "de-DE", "ja-JP", "ru-RU", "fr-FR"]
        #expect(VisionRecognizer.languages(supported: try VisionRecognizer.supported()) == all)
    }

    @Test func aScannedPageIsReadByVision() throws {
        let reading = try read(TestPDF.scanned([lease]))
        #expect(reading.pageCount == 1 && reading.skipped.isEmpty)
        let page = try #require(reading.pages.first)
        #expect(page.ocr)
        #expect(flat(page.text) == lease)
    }

    @Test func aScanShownTurnedAQuarterIsReadUpright() throws {
        let data = TestPDF.scanned([lease], rotated: true)
        #expect(PDFDocument(data: data)?.page(at: 0)?.rotation == 90)
        let page = try #require(try read(data).pages.first)
        #expect(page.ocr && flat(page.text) == lease)
    }

    @Test func scansInOtherScriptsAreReadWithoutBeingTold() throws {
        let russian = "Арендатор может держать одну кошку."
        let japanese = "借主は猫を一匹飼うことができます。"
        let korean = "임차인은 고양이 한 마리를 키울 수 있습니다."
        let polish = "Najemca może trzymać jednego kota."
        let reading = try read(TestPDF.scanned([russian, japanese, korean, polish]))
        try #require(reading.pages.map(\.ocr) == [true, true, true, true])
        let texts = reading.pages.map { flat($0.text) }
        #expect(texts[0] == russian)
        #expect(texts[1].filter { !$0.isWhitespace } == japanese)
        #expect(texts[2] == korean)
        #expect(texts[3] == polish)
    }

    @Test func aPageIsDrawnAt300DotsPerInchAndAtMost4096Pixels() throws {
        let document = try #require(PDFDocument(data: TestPDF.text([""])))
        let page = try #require(document.page(at: 0))
        let image = try #require(PageImage.render(page))
        #expect(image.width == 2479 && image.height == 3508)
        // a poster page is drawn smaller; a page shown turned is drawn turned
        let poster = try #require(PDFDocument(data: TestPDF.make(pages: 1, box: CGRect(x: 0, y: 0, width: 3000, height: 1500)) { _, _ in }))
        let big = try #require(poster.page(at: 0))
        let bigImage = try #require(PageImage.render(big))
        #expect(bigImage.width == 4096 && bigImage.height == 2048)
        big.rotation = 270
        let turned = try #require(PageImage.render(big))
        #expect(turned.width == 2048 && turned.height == 4096)
    }

    @Test(arguments: [0, 90, 180, 270])
    func aPageIsDrawnAsItIsShownWhateverItsTurnAndCrop(rotation: Int) throws {
        // the picture turned a quarter left and shown a quarter right reads upright; then turned further and cropped
        let document = try #require(PDFDocument(data: TestPDF.scanned([lease], rotated: true)))
        let page = try #require(document.page(at: 0))
        page.rotation = 90
        page.setBounds(page.bounds(for: .mediaBox).insetBy(dx: 30, dy: 20), for: .cropBox)
        let picture = try #require(PageImage.render(page))
        let upright = flat(try VisionRecognizer.recognize(picture))
        #expect(upright == lease)
        page.rotation = rotation
        let turned = try #require(PageImage.render(page))
        #expect((turned.width > turned.height) == (rotation % 180 == 0))
    }

    @Test func warmingReadsItsOwnPictureAndNoFile() {
        #expect(VisionRecognizer.warm())
    }
}
