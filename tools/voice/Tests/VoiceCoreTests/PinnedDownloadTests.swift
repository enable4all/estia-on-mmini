import Foundation
import Testing

@testable import VoiceCore

/// A fresh temporary base holding `files` (path → text).
private func base(_ files: [String: String] = [:]) throws -> URL {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("pinned-download-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    for (path, text) in files {
        let url = root.appendingPathComponent(path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(text.utf8).write(to: url)
    }
    return root
}

private func names(in folder: URL) -> [String] {
    ((try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []).sorted()
}

@Suite struct PinnedDownloads {
    /// sha256 of "a" and of "b".
    private let pin = HubPin(
        repo: "example/turn", revision: String(repeating: "1", count: 40), folder: "",
        sha256: [
            "x.bin": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
            "model.mlmodelc/y.bin": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d",
        ])
    private let bytes = ["x.bin": "a", "model.mlmodelc/y.bin": "b"]

    /// A download that writes each file's pinned bytes (or `override`'s), recording what it was asked.
    private final class FakeFetch: @unchecked Sendable {
        var asked: [URL] = []
        let bytes: [String: Data]
        init(_ bytes: [String: Data]) { self.bytes = bytes }

        func fetch(_ url: URL, _ destination: URL, _ maxBytes: Int) throws {
            asked.append(url)
            let name = url.pathComponents.drop { $0 != "resolve" }.dropFirst(2).joined(separator: "/")
            try (bytes[name] ?? Data()).write(to: destination)
        }
    }

    private func fake(_ override: [String: Data] = [:]) -> FakeFetch {
        FakeFetch(bytes.mapValues { Data($0.utf8) }.merging(override) { $1 })
    }

    @Test func missingFilesAreDownloadedAtThePinnedCommitThenChecked() async throws {
        let root = try base()
        defer { try? FileManager.default.removeItem(at: root) }
        let fetch = fake()
        let files = try await prepareModel(pin, in: root) {
            try await downloadPinnedFiles($0, in: root, maxBytes: 100, fetch: fetch.fetch)
        }
        #expect(files == .ok)
        // each pinned file once, at the pinned commit
        #expect(fetch.asked == pin.sha256.keys.sorted().map(pin.fileURL))
        #expect(fetch.asked.allSatisfy { $0.absoluteString.contains("/resolve/\(pin.revision)/") })
        // the staging folder was moved into place: nothing else is left beside the pin's folder
        #expect(names(in: pin.location(in: root).deletingLastPathComponent()) == ["turn"])
    }

    @Test func aFolderMissingAFileIsReplacedWhole() async throws {
        // a download that stopped half way: never loaded, so the fresh copy takes its place
        let root = try base(["models/example/turn/x.bin": "a", "models/example/turn/model.mlmodelc/y.bin.partial": "b"])
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(pin.check(in: root) == .missing)
        try await downloadPinnedFiles(pin, in: root, maxBytes: 100, fetch: fake().fetch)
        #expect(pin.check(in: root) == .ok)
        #expect(names(in: pin.location(in: root).appendingPathComponent("model.mlmodelc")) == ["y.bin"])
    }

    @Test func aDownloadOverTheSizeCapIsRefused() async throws {
        let root = try base()
        defer { try? FileManager.default.removeItem(at: root) }
        let fetch = fake(["x.bin": Data(count: 101)])
        await #expect(throws: ModelDownloadError.overSizeCap) {
            try await downloadPinnedFiles(pin, in: root, maxBytes: 100, fetch: fetch.fetch)
        }
        // nothing moved into place, no staging folder left
        #expect(pin.check(in: root) == .missing)
        #expect(names(in: pin.location(in: root).deletingLastPathComponent()).isEmpty)
    }

    @Test func aFailedFetchLeavesNothingBehind() async throws {
        struct Offline: Error {}
        let root = try base(["models/example/turn/x.bin": "a"])
        defer { try? FileManager.default.removeItem(at: root) }
        var calls = 0
        await #expect(throws: Offline.self) {
            try await downloadPinnedFiles(pin, in: root, maxBytes: 100) { _, destination, _ in
                calls += 1
                if calls == 2 { throw Offline() }
                try Data("b".utf8).write(to: destination)
            }
        }
        // the folder there before is kept as it was, the staging folder is gone
        #expect(names(in: pin.location(in: root).deletingLastPathComponent()) == ["turn"])
        #expect(names(in: pin.location(in: root)) == ["x.bin"])
    }

    @Test func aStagingFolderLeftByACrashIsRemoved() async throws {
        let root = try base(["models/example/turn.download-0F1E/x.bin": "half"])
        defer { try? FileManager.default.removeItem(at: root) }
        try await downloadPinnedFiles(pin, in: root, maxBytes: 100, fetch: fake().fetch)
        #expect(names(in: pin.location(in: root).deletingLastPathComponent()) == ["turn"])
    }
}

/// Answers by path, without a network: the fetch's own URLSession code, offline.
final class StubProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let url = request.url, let client else { return }
        func respond(_ status: Int, _ body: Data, headers: [String: String] = [:]) {
            let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
            client.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client.urlProtocol(self, didLoad: body)
            client.urlProtocolDidFinishLoading(self)
        }
        func redirect(to target: String) {
            let response = HTTPURLResponse(url: url, statusCode: 302, httpVersion: "HTTP/1.1", headerFields: ["Location": target])!
            client.urlProtocol(self, wasRedirectedTo: URLRequest(url: URL(string: target)!), redirectResponse: response)
        }
        switch url.path {
        case "/ok": respond(200, Data("abc".utf8))
        case "/big": respond(200, Data(count: 101))
        case "/declared": respond(200, Data(count: 1000), headers: ["Content-Length": "1000"])
        case "/missing": respond(404, Data("not found".utf8))
        case "/to-http": redirect(to: "http://example.test/ok")
        case "/to-https": redirect(to: "https://example.test/ok")
        default: respond(500, Data())
        }
    }

    override func stopLoading() {}
}

@Suite struct HTTPSFetches {
    private func stubbed() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        return configuration
    }

    private func fetched(_ address: String, maxBytes: Int = 100) async throws -> Data {
        let root = try base()
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("file")
        try await fetchFile(URL(string: address)!, to: file, maxBytes: maxBytes, configuration: stubbed())
        return try Data(contentsOf: file)
    }

    @Test func aFileIsWrittenAsServed() async throws {
        #expect(try await fetched("https://example.test/ok") == Data("abc".utf8))
        #expect(try await fetched("https://example.test/to-https") == Data("abc".utf8))  // an https redirect is followed
    }

    @Test func overTheCapIsRefusedWhileItComes() async throws {
        await #expect(throws: ModelDownloadError.overSizeCap) { try await fetched("https://example.test/big") }
        await #expect(throws: ModelDownloadError.overSizeCap) { try await fetched("https://example.test/declared") }
        #expect(try await fetched("https://example.test/big", maxBytes: 101).count == 101)  // the cap itself is allowed
    }

    @Test func onlyHTTPSEverRedirectsIncluded() async throws {
        await #expect(throws: ModelDownloadError.notHTTPS) { try await fetched("http://example.test/ok") }
        await #expect(throws: ModelDownloadError.notHTTPS) { try await fetched("https://example.test/to-http") }
    }

    @Test func anythingButA200IsRefused() async throws {
        await #expect(throws: ModelDownloadError.status(404)) { try await fetched("https://example.test/missing") }
    }

    @Test func theRefusalsSayWhatWentWrong() {
        #expect(ModelDownloadError.notHTTPS.description == "not https")
        #expect(ModelDownloadError.status(404).description == "HTTP 404")
        #expect(ModelDownloadError.overSizeCap.description == "over the size cap")
        // what the log says: ours in words, URLSession's by its code (offline is -1009), anything else by its type
        struct Other: Error {}
        #expect(downloadFailure(ModelDownloadError.status(503)) == "HTTP 503")
        #expect(downloadFailure(URLError(.notConnectedToInternet)) == "URLError -1009")
        #expect(downloadFailure(NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut)) == "URLError -1001")
        #expect(downloadFailure(Other()) == "Other")
    }
}
