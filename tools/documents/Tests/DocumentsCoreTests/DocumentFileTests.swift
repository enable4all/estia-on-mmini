import Darwin
import Foundation
import Testing

@testable import DocumentsCore

/// The file itself: only a regular file is opened (never a symlink, a folder, a pipe or a device), its size is
/// checked before a byte is read, and only a file that says it is a PDF is handed on.
@Suite struct DocumentFileChecks {
    private func load(_ url: URL, maxBytes: Int = Limits.maxBytes) -> Result<Data, ReadError> {
        Result { () throws(ReadError) in try DocumentFile.load(path: url.path, maxBytes: maxBytes) }
    }

    @Test func aRegularPDFIsLoadedWhole() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let pdf = TestPDF.text(["The tenant may keep one cat."])
        let url = try TestPDF.write(pdf, "lease.pdf", in: folder)
        #expect(load(url) == .success(pdf))
    }

    @Test func aSymlinkIsRefusedEvenToAGoodPDF() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let target = try TestPDF.write(TestPDF.text(["Kept out."]), "real.pdf", in: folder)
        let link = folder.appendingPathComponent("link.pdf")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        #expect(load(link) == .failure(.unreadable))
    }

    @Test func aFolderAMissingFileAndAFileWithoutReadAccessAreRefused() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let inner = folder.appendingPathComponent("inner.pdf")
        try FileManager.default.createDirectory(at: inner, withIntermediateDirectories: false)
        #expect(load(inner) == .failure(.unreadable))
        #expect(load(folder.appendingPathComponent("missing.pdf")) == .failure(.unreadable))
        let closed = try TestPDF.write(TestPDF.text(["Closed."]), "closed.pdf", in: folder)
        #expect(chmod(closed.path, 0) == 0)
        #expect(load(closed) == .failure(.unreadable))
    }

    @Test(.timeLimit(.minutes(1)))
    func aNamedPipeIsRefusedWithoutWaitingForAWriter() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let fifo = folder.appendingPathComponent("pipe.pdf")
        #expect(mkfifo(fifo.path, 0o600) == 0)
        #expect(load(fifo) == .failure(.unreadable))
    }

    @Test func aFileOverTheLimitIsTooBigBeforeItIsRead() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let pdf = TestPDF.text(["Big enough."])
        let url = try TestPDF.write(pdf, "big.pdf", in: folder)
        #expect(load(url, maxBytes: pdf.count - 1) == .failure(.tooBig))
        #expect(load(url, maxBytes: pdf.count) == .success(pdf))
    }

    @Test func aFileThatIsNotAPDFIsNamedSo() throws {
        let folder = try TestPDF.folder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let text = try TestPDF.write(Data("Dear Alex Example, ...".utf8), "letter.pdf", in: folder)
        let empty = try TestPDF.write(Data(), "empty.pdf", in: folder)
        let png = try TestPDF.write(Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]), "photo.pdf", in: folder)
        #expect(load(text) == .failure(.notPDF))
        #expect(load(empty) == .failure(.notPDF))
        #expect(load(png) == .failure(.notPDF))
    }
}
