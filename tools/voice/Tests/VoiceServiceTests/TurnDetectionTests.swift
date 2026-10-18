import CoreML
import Foundation
import Testing
import VoiceCore

@testable import voice_service

/// What a test's fake download and model saw.
private final class Spy: @unchecked Sendable {
    private let lock = NSLock()
    private var _downloads: [HubPin] = []
    private var _loaded: [URL] = []
    private var _windows: [TurnWindow] = []

    var downloads: [HubPin] { lock.withLock { _downloads } }
    var loaded: [URL] { lock.withLock { _loaded } }
    var windows: [TurnWindow] { lock.withLock { _windows } }

    func downloaded(_ pin: HubPin) { lock.withLock { _downloads.append(pin) } }
    func load(_ bundle: URL) { lock.withLock { _loaded.append(bundle) } }
    func heard(_ window: TurnWindow) { lock.withLock { _windows.append(window) } }
}

/// A loaded model that answers `p` (or throws), telling the spy what it heard.
private struct FakeModel: TurnPredictor {
    struct Broken: Error {}
    let p: Float?
    let spy: Spy
    var computeUnits: MLComputeUnits { .cpuOnly }

    func probability(of window: TurnWindow) throws -> Float {
        spy.heard(window)
        guard let p else { throw Broken() }
        return p
    }
}

private func write(_ files: [String: Data], into root: URL) throws {
    for (path, bytes) in files {
        let url = root.appendingPathComponent(path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try bytes.write(to: url)
    }
}

/// The HTTP status and body of a service answer.
private func answer(_ response: Data) -> (status: String, body: String) {
    let text = String(decoding: response, as: UTF8.self)
    return (String(text.prefix(12)), text.components(separatedBy: "\r\n\r\n").last ?? "")
}

@Suite struct TurnDetection {
    private func models() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("turn-\(UUID().uuidString)")
    }

    /// A pin of two small files, the model's bundle among them: "a" and "b".
    private let pin = HubPin(
        repo: "example/turn", revision: String(repeating: "2", count: 40), folder: "",
        sha256: [
            "\(TurnModel.bundle)/model.mil": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
            "config.json": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d",
        ])
    private let good = ["\(TurnModel.bundle)/model.mil": Data("a".utf8), "config.json": Data("b".utf8)]
    private let changed = ["\(TurnModel.bundle)/model.mil": Data("A".utf8), "config.json": Data("b".utf8)]

    /// A detector of `pin` whose download writes `downloads` (nothing: offline) and whose model answers `p`.
    private func detector(
        _ models: URL, _ spy: Spy, downloads: [String: Data]? = nil, p: Float? = 0.9
    ) -> TurnDetector {
        TurnDetector(
            models: models, pin: pin,
            download: { pin, base in
                spy.downloaded(pin)
                if let downloads { try write(downloads, into: pin.location(in: base)) }
            },
            loadModel: { bundle in
                spy.load(bundle)
                return FakeModel(p: p, spy: spy)
            })
    }

    /// One second of quiet, as the bridge sends it.
    private let second = Data(count: 32_000)

    @Test func theTurnDetectorRunsOnTheCPUOnly() {
        // never on the Neural Engine, where a window would wait behind Whisper, Kokoro or Supertonic
        #expect(turnModelConfiguration().computeUnits == .cpuOnly)
    }

    @Test func notReadyWhileLoading() async {
        let detector = TurnDetector(models: models())
        #expect(await detector.state == .loading)
        let reply = answer(await Server.turn(second, detector))
        #expect(reply.status == "HTTP/1.1 503" && reply.body == #"{"error":"not_ready"}"#)
    }

    @Test func missingFilesAreDownloadedAtThePinnedCommitThenChecked() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy()
        let detector = detector(models, spy, downloads: good, p: 0.9)
        await detector.load()
        #expect(await detector.state == .ready)
        #expect(spy.downloads == [pin])
        #expect(pin.check(in: models) == .ok)
        // the checked bundle is what loads, and its warm-up is 8 s of zeros
        #expect(spy.loaded == [pin.location(in: models).appendingPathComponent(TurnModel.bundle)])
        #expect(spy.windows == [TurnWindow.silence])
        #expect(await detector.runsOn == .cpuOnly)
        let reply = answer(await Server.turn(second, detector))
        #expect(reply.status == "HTTP/1.1 200")
        // the model's Float exactly, as the bridge compares it with its tiers
        let p = try #require((try JSONSerialization.jsonObject(with: Data(reply.body.utf8)) as? [String: Double])?["p"])
        #expect(p == Double(Float(0.9)))
        #expect(spy.windows.count == 2 && spy.windows[1].samples.count == TurnWindow.samples)
    }

    @Test func pinnedFilesLoadWithoutADownload() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        try write(good, into: pin.location(in: models))
        let spy = Spy()
        let detector = detector(models, spy, downloads: good)
        await detector.load()
        #expect(await detector.state == .ready && spy.downloads.isEmpty && spy.loaded.count == 1)
    }

    @Test func aChangedOrAddedFileIsSetAsideNeverLoaded() async throws {
        // Whisper's rules: set aside whole, downloaded fresh once; changed again, unavailable and never loaded
        let added = good.merging(["extra.bin": Data()]) { $1 }
        for files in [changed, added] {
            let models = models()
            defer { try? FileManager.default.removeItem(at: models) }
            try write(files, into: pin.location(in: models))
            let spy = Spy()
            let detector = detector(models, spy, downloads: changed)
            await detector.load()
            #expect(await detector.state == .unavailable)
            #expect(spy.loaded.isEmpty && spy.windows.isEmpty)
            #expect(spy.downloads == [pin])
            let parent = pin.location(in: models).deletingLastPathComponent()
            let names = try FileManager.default.contentsOfDirectory(atPath: parent.path)
            #expect(names.filter { $0.hasPrefix("turn.refused-") }.count == 1)
            let reply = answer(await Server.turn(second, detector))
            #expect(reply.status == "HTTP/1.1 503" && reply.body == #"{"error":"unavailable"}"#)
        }
    }

    @Test func aChangedFolderDownloadedFreshLoads() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        try write(changed, into: pin.location(in: models))
        let spy = Spy()
        let detector = detector(models, spy, downloads: good)
        await detector.load()
        #expect(await detector.state == .ready && spy.downloads.count == 1 && spy.loaded.count == 1)
    }

    @Test func aFailedOrEmptyDownloadIsUnavailable() async throws {
        struct Offline: Error {}
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy()
        let failing = TurnDetector(
            models: models, pin: pin, download: { _, _ in throw Offline() }, loadModel: { _ in FakeModel(p: 0.9, spy: spy) })
        await failing.load()
        #expect(await failing.state == .unavailable)
        // a download that brought nothing: still missing, never loaded
        let empty = detector(models, spy, downloads: nil)
        await empty.load()
        #expect(await empty.state == .unavailable && spy.loaded.isEmpty)
    }

    @Test func aModelThatWillNotLoadOrWarmUpIsUnavailable() async throws {
        struct Unreadable: Error {}
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        try write(good, into: pin.location(in: models))
        let spy = Spy()
        let unreadable = TurnDetector(models: models, pin: pin, download: { _, _ in }, loadModel: { _ in throw Unreadable() })
        await unreadable.load()
        #expect(await unreadable.state == .unavailable)
        let broken = detector(models, spy, p: nil)  // its warm-up throws
        await broken.load()
        #expect(await broken.state == .unavailable)
    }

    @Test func aDownloadOverTheSizeCapIsRefused() async throws {
        // the service's download: each pinned file at most 32 MiB
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        await #expect(throws: ModelDownloadError.overSizeCap) {
            try await downloadTurnModel(pin, models: models) { _, destination, _ in
                try Data(count: TurnModel.maxFileBytes + 1).write(to: destination)
            }
        }
        #expect(pin.check(in: models) == .missing)
        // the cap itself passes
        var asked: [URL] = []
        try await downloadTurnModel(pin, models: models) { url, destination, maxBytes in
            asked.append(url)
            #expect(maxBytes == TurnModel.maxFileBytes)
            try Data(count: TurnModel.maxFileBytes).write(to: destination)
        }
        #expect(asked == pin.sha256.keys.sorted().map(pin.fileURL))
    }

    @Test func badWindowsAre400WhateverTheModel() async throws {
        // the refusal comes first: a loading, a ready or an unavailable detector says the same
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let spy = Spy()
        let ready = detector(models, spy, downloads: good)
        await ready.load()
        let unavailable = TurnDetector(models: models, pin: pin, download: { _, _ in }, loadModel: { _ in throw CocoaError(.fileReadUnknown) })
        for detector in [TurnDetector(models: self.models()), ready, unavailable] {
            for body in [Data(), Data(count: 3), Data(count: 256_002)] {
                let reply = answer(await Server.turn(body, detector))
                #expect(reply.status == "HTTP/1.1 400" && reply.body == #"{"error":"bad_window"}"#)
            }
        }
        #expect(spy.windows.count == 1)  // only the warm-up: no refused window reached the model
    }

    @Test func aBrokenProbabilityIsAFailure() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        try write(good, into: pin.location(in: models))
        for (p, expected) in [(Float.nan, #"{"error":"failed"}"#), (.infinity, #"{"error":"failed"}"#), (1.5, #"{"p":1}"#), (-0.5, #"{"p":0}"#)] {
            let detector = detector(models, Spy(), p: p)
            await detector.load()  // a warm-up answer is not checked: only that the model ran
            let reply = answer(await Server.turn(second, detector))
            #expect(reply.body == expected)
            #expect(reply.status == (expected.contains("error") ? "HTTP/1.1 500" : "HTTP/1.1 200"))
        }
    }

    @Test func aModelThatFailsAnswers500() async throws {
        // a prediction that throws after the warm-up: failed, and the detector stays ready for the next window
        final class Flaky: TurnPredictor, @unchecked Sendable {
            struct Broken: Error {}
            var calls = 0
            var computeUnits: MLComputeUnits { .cpuOnly }
            func probability(of window: TurnWindow) throws -> Float {
                calls += 1
                if calls == 2 { throw Broken() }
                return 0.2
            }
        }
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        try write(good, into: pin.location(in: models))
        let flaky = Flaky()
        let detector = TurnDetector(models: models, pin: pin, download: { _, _ in }, loadModel: { _ in flaky })
        await detector.load()
        let failed = answer(await Server.turn(second, detector))
        #expect(failed.status == "HTTP/1.1 500" && failed.body == #"{"error":"failed"}"#)
        let next = answer(await Server.turn(second, detector))
        #expect(next.status == "HTTP/1.1 200")
        #expect(await detector.state == .ready)
    }

    @Test func theDetectorIsBesideTheSpeechDetectorAndInHealth() async throws {
        let engines = Engines(models: URL(fileURLWithPath: "/models"), temp: URL(fileURLWithPath: "/tmp"))
        #expect(await engines.turn.state == .loading)
        let reply = answer(await engines.health())
        #expect(reply.status == "HTTP/1.1 200")
        let health = try #require(try JSONSerialization.jsonObject(with: Data(reply.body.utf8)) as? [String: Any])
        #expect(health["turn"] as? String == "loading" && health["vad"] as? String == "loading")
    }

    // MARK: - The real model

    /// A sentence spoken by macOS's `say`, as the bridge sends it: 16 kHz mono 16-bit little-endian, cut 0.2 s after
    /// its last loud sample (the window the bridge sends ends there: spike A's trim).
    private func spoken(_ text: String, in folder: URL) throws -> Data {
        let file = folder.appendingPathComponent("say-\(UUID().uuidString).wav")
        let say = Process()
        say.executableURL = URL(fileURLWithPath: "/usr/bin/say")
        say.arguments = ["-o", file.path, "--file-format=WAVE", "--data-format=LEI16@16000", text]
        try say.run()
        say.waitUntilExit()
        let wav = try Data(contentsOf: file)
        let data = try #require(wav.range(of: Data("data".utf8)))
        let pcm = Data(wav[(data.upperBound + 4)...])
        let samples = pcm.withUnsafeBytes { raw in
            (0..<pcm.count / 2).map { Int16(littleEndian: raw.loadUnaligned(fromByteOffset: $0 * 2, as: Int16.self)) }
        }
        let last = try #require(samples.lastIndex { abs(Int($0)) > 1000 })
        let end = min(samples.count, last + 1 + 3_200)
        return Data(pcm.prefix(end * 2).suffix(TurnWindow.maxBytes))
    }

    /// The real model: the pinned files from VOICE_TURN_MODEL_DIR (a folder holding them, such as the spike's copy,
    /// copied into a fresh models folder and checked as any copy is) or downloaded from Hugging Face at the pinned
    /// commit (about 17 MB) with the service's own download; loaded on the CPU only, then asked about finished and
    /// unfinished sentences spoken by `say`. Only with VOICE_MODEL_TESTS=1 (`swift test` stays offline otherwise).
    @Test(.enabled(if: ProcessInfo.processInfo.environment["VOICE_MODEL_TESTS"] == "1"))
    func thePinnedTurnModelLoadsAndAnswers() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let given = ProcessInfo.processInfo.environment["VOICE_TURN_MODEL_DIR"]
        if let given {
            let location = TurnModel.pin.location(in: models)
            try FileManager.default.createDirectory(at: location.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: URL(fileURLWithPath: given), to: location)
        }
        let spy = Spy()
        let detector = TurnDetector(models: models, download: { pin, base in
            spy.downloaded(pin)
            try await downloadTurnModel(pin, models: base)
        })
        await detector.load()
        #expect(await detector.state == .ready)
        #expect(await detector.runsOn == .cpuOnly)  // read back from the loaded model
        #expect(TurnModel.pin.check(in: models) == .ok)
        #expect(spy.downloads.count == (given == nil ? 1 : 0))
        let pairs = [
            ("Can you remind me to call the dentist tomorrow morning?", "Can you remind me to call the dentist and"),
            ("I think the meeting is on Friday.", "I think the meeting is on Friday, but the"),
            ("Please turn off the lights in the kitchen.", "Please turn off the lights in the"),
        ]
        var inOrder = 0
        var ps: [(Double, Double)] = []
        for (finished, unfinished) in pairs {
            var pair: [Double] = []
            for text in [finished, unfinished] {
                let reply = answer(await Server.turn(try spoken(text, in: models), detector))
                #expect(reply.status == "HTTP/1.1 200")
                let p = try #require((try JSONSerialization.jsonObject(with: Data(reply.body.utf8)) as? [String: Double])?["p"])
                #expect((0...1).contains(p))
                pair.append(p)
            }
            ps.append((pair[0], pair[1]))
            if pair[0] > pair[1] { inOrder += 1 }
        }
        #expect(inOrder >= 2, "finished, unfinished: \(ps)")
        // and on the right side of the steps, which audio turned to noise by a wiring bug (byte order, padding side)
        // would meet only by chance: 2 of 3 finished at least sure (0.8), every unfinished one under fairly sure (0.5)
        // (spike A's pinned files: 0.57, 0.99, 0.99 and 0.27, 0.006, 0.011)
        #expect(ps.filter { $0.0 >= 0.8 }.count >= 2, "finished, unfinished: \(ps)")
        #expect(ps.allSatisfy { $0.1 < 0.5 }, "finished, unfinished: \(ps)")
        // one /turn window (8 s), request to answer, on the CPU: median well under 20 ms (spike A: 3.4 ms, 18 ms
        // slowest with every core busy)
        let window = try spoken(pairs[0].0, in: models)
        let clock = ContinuousClock()
        var times: [Duration] = []
        for _ in 0..<21 {
            let began = clock.now
            _ = await Server.turn(window, detector)
            times.append(clock.now - began)
        }
        let sorted = times.sorted()
        #expect(sorted[sorted.count / 2] < .milliseconds(20), "window times: \(sorted)")
        print("turn detector: finished, unfinished p \(ps); median \(sorted[sorted.count / 2]), slowest \(sorted.last!)")
    }
}
