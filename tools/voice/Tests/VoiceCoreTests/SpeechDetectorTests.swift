import Foundation
import Testing

@testable import VoiceCore

/// 16-bit little-endian PCM bytes of `samples`.
private func pcm16(_ samples: [Int16]) -> Data {
    samples.withUnsafeBufferPointer { Data(buffer: $0) }
}

private func chunk(_ value: Int16 = 0) -> Data { pcm16([Int16](repeating: value, count: 4096)) }

@Suite struct SpeechChunks {
    @Test func streamAndSamples() throws {
        var samples = [Int16](repeating: 0, count: 4096)
        samples[0] = 32767
        samples[1] = -32768
        samples[2] = 1
        samples[4095] = -16384
        let request = try SpeechChunk(target: "/vad?stream=call-1_A", body: pcm16(samples))
        #expect(request.stream == "call-1_A")
        #expect(request.samples.count == 4096)
        #expect(request.samples[0] == 32767 / 32768 && request.samples[1] == -1 && request.samples[2] == 1 / 32768)
        #expect(request.samples[4095] == -0.5 && request.samples[3] == 0)
    }

    @Test func exactly4096SamplesOf16kHz() {
        #expect(SpeechChunk.samples == 4096 && SpeechChunk.bytes == 8192)
        for count in [0, 1, 8191, 8193, 16_384] {
            #expect(throws: VoiceError.badChunk) { try SpeechChunk(target: "/vad?stream=a", body: Data(count: count)) }
        }
    }

    @Test func streamIdsOf1To64LettersDigitsDashesAndUnderscores() throws {
        let longest = String(repeating: "a", count: 64)
        #expect(try SpeechChunk(target: "/vad?stream=\(longest)", body: chunk()).stream == longest)
        #expect(try SpeechChunk(target: "/vad?stream=Z9-_", body: chunk()).stream == "Z9-_")
        // the first stream counts, other fields are passed over
        #expect(try SpeechChunk(target: "/vad?x=1&stream=b&stream=c", body: chunk()).stream == "b")
    }

    @Test func anyOtherStreamIdIsRefused() {
        let ids = [
            "", "stream", "stream=", "stream=\(String(repeating: "a", count: 65))", "stream=a%20b", "stream=a/b",
            "stream=a.b", "stream=a b", "stream=\u{e9}", "stream=a\n", "other=a", "Stream=a",
        ]
        for query in ids {
            #expect(throws: VoiceError.badStream) { try SpeechChunk(target: "/vad?\(query)", body: chunk()) }
        }
        #expect(throws: VoiceError.badStream) { try SpeechChunk(target: "/vad", body: chunk()) }
    }

    @Test func theStreamIsCheckedBeforeTheLength() {
        #expect(throws: VoiceError.badStream) { try SpeechChunk(target: "/vad?stream=a.b", body: Data(count: 3)) }
    }

    @Test func theAnswerIsTheProbability() throws {
        let text = { (p: Float) throws in String(decoding: HTTP.json(200, try speechBody(p)), as: UTF8.self) }
        #expect(try text(0.5).hasSuffix(#"{"p":0.5}"#))
        #expect(try text(1.2).hasSuffix(#"{"p":1}"#) && text(-0.1).hasSuffix(#"{"p":0}"#))
        // the model's Float exactly, as the spike compared it with its thresholds
        let body = try #require(try text(0.99).components(separatedBy: "\r\n\r\n").last)
        let p = try #require((try JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Double])?["p"])
        #expect(p == Double(Float(0.99)) && p >= 0.99)
    }

    @Test func aBrokenProbabilityIsAFailureNeverAnAnswer() {
        // JSONSerialization raises (and the process ends) on NaN or infinity
        for p in [Float.nan, .infinity, -.infinity] {
            #expect(throws: VoiceError.failed) { try speechBody(p) }
        }
    }
}

@Suite struct SpeechStreams {
    @Test func aNewStreamHasNoStateUntilKept() {
        var streams = StreamTable<Int>()
        #expect(streams.state(of: "a") == nil)
        let first = streams.keep(1, for: "a", at: 0)
        #expect(first == 0 && streams.state(of: "a") == 1 && streams.count == 1)
        let next = streams.keep(2, for: "a", at: 0.256)
        #expect(next == 0 && streams.state(of: "a") == 2 && streams.count == 1)
    }

    @Test func aStreamIsDroppedAfter60SecondsWithoutAChunk() {
        var streams = StreamTable<Int>()
        #expect(StreamTable<Int>.idleLimit == 60)
        streams.keep(1, for: "a", at: 100)
        streams.keep(1, for: "b", at: 130)
        let early = streams.expire(at: 159.9)
        #expect(early == 0 && streams.state(of: "a") == 1)
        let due = streams.expire(at: 160)
        #expect(due == 1 && streams.count == 1)
        // a dropped stream that comes back starts afresh
        #expect(streams.state(of: "a") == nil && streams.state(of: "b") == 1)
    }

    @Test func atMostFourStreamsTheOneIdleLongestGoes() {
        var streams = StreamTable<Int>()
        #expect(StreamTable<Int>.maxStreams == 4)
        for (n, id) in ["a", "b", "c", "d"].enumerated() { streams.keep(n, for: id, at: Double(n)) }
        streams.keep(10, for: "a", at: 5)  // "a" is the oldest stream but had a chunk just now
        let fifth = streams.keep(4, for: "e", at: 6)
        #expect(fifth == 1 && streams.count == 4 && streams.state(of: "b") == nil)
        #expect(streams.state(of: "a") == 10 && streams.state(of: "e") == 4)
        // a known stream never pushes another out
        let known = streams.keep(11, for: "c", at: 7)
        #expect(known == 0 && streams.count == 4)
    }
}

@Suite struct ModelPin {
    private func folder(_ files: [String: String]) throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("pin-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        for (path, text) in files {
            let url = root.appendingPathComponent(path)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data(text.utf8).write(to: url)
        }
        return root
    }

    /// The pins of a folder holding "a" in `x.bin` and "b" in `sub/y.bin`.
    private let pins = [
        "x.bin": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
        "sub/y.bin": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d",
    ]

    @Test func sha256OfKnownBytes() {
        #expect(sha256Hex(Data("abc".utf8)) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        #expect(sha256Hex(Data()) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    }

    @Test func theSileroModelIsPinnedFileByFile() {
        #expect(SpeechModel.bundle == "silero-vad-unified-256ms-v6.2.1.mlmodelc")
        #expect(SpeechModel.revision.count == 40 && SpeechModel.revision.allSatisfy(\.isHexDigit))
        #expect(Set(SpeechModel.sha256.keys) == ["analytics/coremldata.bin", "coremldata.bin", "metadata.json", "model.mil", "weights/weight.bin"])
        #expect(SpeechModel.sha256.values.allSatisfy { $0.count == 64 && $0.allSatisfy { "0123456789abcdef".contains($0) } })
    }

    @Test func pinnedFilesPass() throws {
        let root = try folder(["x.bin": "a", "sub/y.bin": "b"])
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(checkModelFiles(at: root, against: pins) == .ok)
    }

    @Test func aMissingFolderOrFileCanBeDownloaded() throws {
        let root = try folder(["x.bin": "a", "sub/y.bin.partial": "b"])  // a download that stopped half way
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(checkModelFiles(at: root, against: pins) == .missing)
        #expect(checkModelFiles(at: root.appendingPathComponent("absent"), against: pins) == .missing)
    }

    @Test func aChangedOrAddedFileIsRefused() throws {
        let changed = try folder(["x.bin": "a", "sub/y.bin": "B"])
        let added = try folder(["x.bin": "a", "sub/y.bin": "b", "z.bin": ""])
        let hidden = try folder(["x.bin": "a", "sub/y.bin": "b", "sub/.extra": ""])
        defer { for root in [changed, added, hidden] { try? FileManager.default.removeItem(at: root) } }
        #expect(checkModelFiles(at: changed, against: pins) == .changed)
        #expect(checkModelFiles(at: added, against: pins) == .changed)
        #expect(checkModelFiles(at: hidden, against: pins) == .changed)
    }

    @Test func aLinkIsRefusedEvenToTheRightBytes() throws {
        let root = try folder(["x.bin": "a", "elsewhere/y.bin": "b"])
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createSymbolicLink(
            at: root.appendingPathComponent("sub"), withDestinationURL: root.appendingPathComponent("elsewhere"))
        let pins = self.pins.merging(["elsewhere/y.bin": self.pins["sub/y.bin"]!]) { $1 }
        #expect(checkModelFiles(at: root, against: pins) == .changed)
        let linked = try folder([:])
        defer { try? FileManager.default.removeItem(at: linked) }
        try FileManager.default.createSymbolicLink(at: linked.appendingPathComponent("model"), withDestinationURL: root)
        #expect(checkModelFiles(at: linked.appendingPathComponent("model"), against: ["x.bin": self.pins["x.bin"]!]) == .changed)
    }
}
