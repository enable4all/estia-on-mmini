import Foundation
import Testing

@testable import VoiceCore

/// 16-bit little-endian PCM bytes of `samples`.
private func pcm16(_ samples: [Int16]) -> Data {
    samples.withUnsafeBufferPointer { Data(buffer: $0) }
}

@Suite struct TurnPin {
    @Test func theTurnModelIsPinnedToOneCommitFileByFile() {
        // Smart Turn v3.2 in the community CoreML port, at one commit, every file of the repository with its sha256
        // (measured on the files downloaded at that commit, matching Hugging Face's own listing: spike A)
        let pin = TurnModel.pin
        #expect(pin.repo == "aufklarer/Smart-Turn-v3.2-CoreML")
        #expect(pin.revision == "75f069717f500947195fa1945c46ddb472559cff")
        #expect(pin.folder.isEmpty)
        #expect(pin.sha256 == [
            ".gitattributes": "11ad7efa24975ee4b0c3c3a38ed18737f0658a5f75a0a96787b576a78a023361",
            "LICENSE": "db31415d226689aaf6fa6274da0416171edb70ca7ebf97bd3d99d8d8b88cd34e",
            "README.md": "417620f5686072f108bafdd91c91d9393295c243aac0b81e6eef33d65cfcba2f",
            "config.json": "bc09d1b40285465b389dc42cef54290fa1db7b4928348018bc61d819b4914402",
            "smart_turn.mlmodelc/analytics/coremldata.bin": "1646746d0029e78d26be065e62602d4f89e51013ec238b653adf7d7d21057f89",
            "smart_turn.mlmodelc/coremldata.bin": "9245ac99889c9d738cba6c89da9e851fd16488bf8effbc15a109d06b401c9383",
            "smart_turn.mlmodelc/model.mil": "41a6d9ac0f7d01d738e56ecdf4435e55f139591541b7cf1a4dba6b9ce2f1c9ef",
            "smart_turn.mlmodelc/weights/weight.bin": "b6295316741a0149e1fcab7cdf030e1b9bfb0eef778207accee91a2a9703a39c",
        ])
        #expect(TurnModel.bundle == "smart_turn.mlmodelc")
        #expect(TurnModel.maxFileBytes == 32 << 20)
    }

    @Test func theFilesSitBesideTheOtherModels() {
        // <models>/models/<repo>: the Hub layout Whisper's files use, under the same models folder
        let models = URL(fileURLWithPath: "/models")
        #expect(TurnModel.pin.location(in: models).path == "/models/models/aufklarer/Smart-Turn-v3.2-CoreML")
        #expect(TurnModel.pin.path == "models/aufklarer/Smart-Turn-v3.2-CoreML")
    }

    @Test func downloadURLsNameThePinnedCommitNeverMain() {
        let revision = "75f069717f500947195fa1945c46ddb472559cff"
        #expect(TurnModel.pin.fileURL("smart_turn.mlmodelc/weights/weight.bin").absoluteString
            == "https://huggingface.co/aufklarer/Smart-Turn-v3.2-CoreML/resolve/\(revision)/smart_turn.mlmodelc/weights/weight.bin")
        #expect(TurnModel.pin.fileURL(".gitattributes").absoluteString
            == "https://huggingface.co/aufklarer/Smart-Turn-v3.2-CoreML/resolve/\(revision)/.gitattributes")
        for path in TurnModel.pin.sha256.keys {
            let url = TurnModel.pin.fileURL(path)
            #expect(url.scheme == "https" && url.host == "huggingface.co")
            #expect(url.path.contains("/resolve/\(revision)/") && !url.path.contains("/main/"))
        }
        // every segment percent-encoded: a name can never add a query, a fragment or another folder
        let odd = HubPin(repo: "example/model", revision: revision, folder: "sub dir", sha256: [:])
        #expect(odd.fileURL("a b/c#d?e%.bin").absoluteString
            == "https://huggingface.co/example/model/resolve/\(revision)/sub%20dir/a%20b/c%23d%3Fe%25.bin")
    }
}

@Suite struct TurnWindows {
    @Test func aShortWindowIsZeroPaddedAtTheFront() throws {
        // 1 s of audio: 112,000 zeros, then the samples in order, the most recent last
        let samples = (0..<16_000).map { Int16(truncatingIfNeeded: $0 &* 7 - 20_000) }
        let window = try TurnWindow(body: pcm16(samples))
        #expect(TurnWindow.samples == 128_000 && TurnWindow.maxBytes == 256_000)
        #expect(window.samples.count == 128_000)
        #expect(window.samples[..<112_000].allSatisfy { $0 == 0 })
        #expect(Array(window.samples[112_000...]) == samples.map { Float($0) / 32768 })
        // full scale is -1...1, as /vad reads it
        let edges = try TurnWindow(body: pcm16([32767, -32768]))
        #expect(edges.samples[127_998] == 32767 / 32768 && edges.samples[127_999] == -1)
    }

    @Test func aFull8SecondWindowIsTakenAsItIs() throws {
        let samples = (0..<128_000).map { Int16(truncatingIfNeeded: $0 % 2001 - 1000) }
        let window = try TurnWindow(body: pcm16(samples))
        #expect(window.samples == samples.map { Float($0) / 32768 })  // nothing cut, nothing added
        #expect(window.samples[0] == Float(-1000) / 32768)
    }

    @Test func emptyOddOrOver256000BytesIsBadWindow() throws {
        for count in [0, 1, 3, 255_999, 256_001, 256_002, 1_000_000] {
            #expect(throws: VoiceError.badWindow) { try TurnWindow(body: Data(count: count)) }
        }
        #expect(try TurnWindow(body: Data(count: 2)).samples.count == 128_000)  // one sample is enough
        #expect(VoiceError.badWindow.status == 400 && VoiceError.badWindow.code == "bad_window")
    }

    @Test func silenceIsAWholeWindowOfZeros() {
        #expect(TurnWindow.silence.samples.count == 128_000 && TurnWindow.silence.samples.allSatisfy { $0 == 0 })
    }

    @Test func aBrokenProbabilityIsAFailure() throws {
        let text = { (p: Float) throws in String(decoding: HTTP.json(200, try turnBody(p)), as: UTF8.self) }
        #expect(try text(0.5).hasSuffix(#"{"p":0.5}"#))
        #expect(try text(1.2).hasSuffix(#"{"p":1}"#) && text(-0.1).hasSuffix(#"{"p":0}"#))
        // JSONSerialization raises (and the process ends) on NaN or infinity
        for p in [Float.nan, .infinity, -.infinity] {
            #expect(throws: VoiceError.failed) { try turnBody(p) }
        }
    }

    @Test func turnIsAPostRouteAndInHealth() {
        #expect(Route("POST", "/turn") == .turn)
        #expect(Route("POST", "/turn?x=1") == .turn)  // no query is read
        #expect(Route("GET", "/turn") == .methodNotAllowed)
        #expect(Route("POST", "/turns") == .notFound)
        let body = healthBody(stt: "ready", tts: "ready", vad: "ready", turn: "loading")
        let text = String(decoding: HTTP.json(200, body), as: UTF8.self)
        #expect(text.hasSuffix(
            #"{"languages":["en","es","it","ko","pt","nl","pl","de","ja","ru","fr"],"stt":"ready","tts":"ready","turn":"loading","vad":"ready"}"#))
    }
}
