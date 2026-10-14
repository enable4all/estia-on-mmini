import CoreML
import Foundation
import Testing
import VoiceCore
import WhisperKit

@testable import voice_service

@Suite struct WhisperSettings {
    @Test func theNamedLanguageIsForcedNeverDetected() {
        for code in ["en", "nl", "ja"] {
            let options = whisperOptions(language: code)
            #expect(options.language == code)
            // WhisperKit forces the language token only with the prefill prompt; without it, it detects
            #expect(options.usePrefillPrompt && !options.detectLanguage)
            #expect(options.task == .transcribe)
        }
    }
}

@Suite struct WhisperStart {
    private let models = URL(fileURLWithPath: "/models")

    @Test func whisperLoadsOnlyFromItsCheckedFoldersAndNeverDownloads() {
        // a model folder and no download: WhisperKit neither lists the repository nor fetches a file at a start
        let config = whisperConfig(models: models)
        #expect(config.modelFolder == WhisperModel.files.location(in: models).path)
        #expect(config.download == false && config.load == true)
        #expect(config.tokenizerFolder == models)
        #expect(config.model == nil && config.modelRepo == nil && config.downloadBase == nil)
    }

    @Test func thePinnedFoldersAreWhereWhisperKitLooks() {
        // WhisperKit's own Hub layout: the model where its download put it, the tokenizer where it looks first
        let hub = HubApiWrapper(downloadBase: models)
        let model = hub.localRepoLocation(HubApiWrapper.Repo(id: WhisperModel.files.repo))
            .appendingPathComponent(WhisperModel.files.folder)
        #expect(WhisperModel.files.location(in: models).standardizedFileURL == model.standardizedFileURL)
        let tokenizer = hub.localRepoLocation(HubApiWrapper.Repo(id: "openai/whisper-large-v3"))
        #expect(WhisperModel.tokenizer.location(in: models).standardizedFileURL == tokenizer.standardizedFileURL)
        #expect(whisperPins.map(\.repo) == ["argmaxinc/whisperkit-coreml", "openai/whisper-large-v3"])
    }

    /// The real model: downloads about 1.6 GB from Hugging Face at the pinned commits into a temporary folder (or uses
    /// the folder in VOICE_WHISPER_MODELS, kept), checks every file, then starts Whisper from it on the CPU only (no
    /// Neural Engine compile) and transcribes a second of silence. Run it again with VOICE_WHISPER_MODELS under
    /// `sandbox-exec -p '(version 1)(allow default)(deny network-outbound (remote ip))'` (and `swift test
    /// --disable-sandbox --skip-build`: SwiftPM's own sandbox cannot nest) to see that a start with the files in place
    /// needs no network: main's start (`model:` and `downloadBase:`) fails there. HF_ENDPOINT proves nothing here:
    /// WhisperKit names Hugging Face's address itself for its download step. Only with VOICE_WHISPER_TESTS=1.
    @Test(.enabled(if: ProcessInfo.processInfo.environment["VOICE_WHISPER_TESTS"] == "1"))
    func thePinnedWhisperDownloadsChecksAndStartsWithoutTheNetwork() async throws {
        let given = ProcessInfo.processInfo.environment["VOICE_WHISPER_MODELS"]
        let models = given.map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.temporaryDirectory.appendingPathComponent("whisper-\(UUID().uuidString)")
        defer { if given == nil { try? FileManager.default.removeItem(at: models) } }
        try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
        for pin in whisperPins {
            #expect(try await prepareModel(pin, in: models) { try await downloadPinned($0, models: models) } == .ok)
        }
        // the next start: every file checked, nothing downloaded; the check costs a start well under 5 s (measured:
        // about 0.6 s for the 1.6 GB on an M-series Mac)
        var downloads = 0
        let clock = ContinuousClock()
        let began = clock.now
        for pin in whisperPins {
            #expect(try await prepareModel(pin, in: models) { _ in downloads += 1 } == .ok)
        }
        let checked = clock.now - began
        #expect(downloads == 0)
        #expect(checked < .seconds(5), "checked in \(checked)")
        let config = whisperConfig(models: models)
        config.computeOptions = ModelComputeOptions(melCompute: .cpuOnly, audioEncoderCompute: .cpuOnly, textDecoderCompute: .cpuOnly)
        config.verbose = false
        let whisper = try await WhisperKit(config)
        let silence = models.appendingPathComponent("silence.wav")
        try silentWAV(seconds: 1).write(to: silence)
        defer { try? FileManager.default.removeItem(at: silence) }
        let results = try await whisper.transcribe(audioPath: silence.path, decodeOptions: whisperOptions(language: "en"))
        #expect(!results.isEmpty)
    }
}

@Suite struct SpeechDetection {
    private func models() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("vad-\(UUID().uuidString)")
    }

    /// One chunk of silence.
    private func chunk() -> Data { Data(count: 8192) }

    @Test func theDetectorRunsOnTheCPUOnly() {
        // never on the Neural Engine, where a chunk would wait behind Whisper, Kokoro or Supertonic: both the CoreML
        // model's configuration and FluidAudio's VadConfig say so
        #expect(speechModelConfiguration().computeUnits == .cpuOnly)
        #expect(speechVadConfig().computeUnits == .cpuOnly)
    }

    @Test func theModelIsWhereFluidAudioPutsItUnderTheServiceModels() {
        // FluidAudio's own names, so a FluidAudio update that renames the model fails here, not at a start
        let bundle = speechModelBundle(models: URL(fileURLWithPath: "/models"))
        #expect(bundle.path == "/models/silero-vad/\(SpeechModel.bundle)")
    }

    @Test func notReadyWhileLoading() async {
        let detector = SpeechDetector(models: models())
        #expect(await detector.state == .loading)
        let response = String(decoding: await Server.vad(chunk(), target: "/vad?stream=call-1", detector), as: UTF8.self)
        #expect(response.hasPrefix("HTTP/1.1 503 ") && response.hasSuffix(#"{"error":"not_ready"}"#))
    }

    @Test func aChangedModelIsUnavailableAndNeverLoaded() async throws {
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        let bundle = speechModelBundle(models: models)
        for path in SpeechModel.sha256.keys {
            let file = bundle.appendingPathComponent(path)
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data("not the model".utf8).write(to: file)
        }
        let detector = SpeechDetector(models: models)
        await detector.load()  // all files there: no download is tried
        #expect(await detector.state == .unavailable)
        let response = String(decoding: await Server.vad(chunk(), target: "/vad?stream=call-1", detector), as: UTF8.self)
        #expect(response.hasPrefix("HTTP/1.1 503 ") && response.hasSuffix(#"{"error":"unavailable"}"#))
    }

    @Test func badRequestsAre400WhateverTheModel() async {
        let detector = SpeechDetector(models: models())
        let badStream = String(decoding: await Server.vad(chunk(), target: "/vad?stream=a.b", detector), as: UTF8.self)
        #expect(badStream.hasPrefix("HTTP/1.1 400 ") && badStream.hasSuffix(#"{"error":"bad_stream"}"#))
        let badChunk = String(decoding: await Server.vad(Data(count: 8190), target: "/vad?stream=a", detector), as: UTF8.self)
        #expect(badChunk.hasPrefix("HTTP/1.1 400 ") && badChunk.hasSuffix(#"{"error":"bad_chunk"}"#))
    }

    /// `p` from a `/vad` answer.
    private func probability(_ response: Data) throws -> Double {
        let text = String(decoding: response, as: UTF8.self)
        #expect(text.hasPrefix("HTTP/1.1 200 "))
        let body = try #require(text.components(separatedBy: "\r\n\r\n").last)
        return try #require((try JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Double])?["p"])
    }

    /// A sentence spoken by macOS's `say`, as the bridge sends a call's audio: 16 kHz mono 16-bit, little-endian.
    private func spoken(_ text: String, in folder: URL) throws -> Data {
        let file = folder.appendingPathComponent("speech.wav")
        let say = Process()
        say.executableURL = URL(fileURLWithPath: "/usr/bin/say")
        say.arguments = ["-o", file.path, "--file-format=WAVE", "--data-format=LEI16@16000", text]
        try say.run()
        say.waitUntilExit()
        let wav = try Data(contentsOf: file)
        let data = try #require(wav.range(of: Data("data".utf8)))
        return wav[(data.upperBound + 4)...]
    }

    /// The real model: downloads about 1 MB from Hugging Face into a temporary folder at the pinned commit, checks
    /// its sha256 and runs it on silence and on speech. Only with VOICE_MODEL_TESTS=1 (`swift test` stays offline
    /// otherwise).
    @Test(.enabled(if: ProcessInfo.processInfo.environment["VOICE_MODEL_TESTS"] == "1"))
    func thePinnedModelDownloadsLoadsAndAnswers() async throws {
        pinSpeechModel()
        let models = models()
        defer { try? FileManager.default.removeItem(at: models) }
        try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
        let detector = SpeechDetector(models: models)
        await detector.load()
        #expect(await detector.state == .ready)
        // read back from the loaded model and FluidAudio: the CPU only, as the builders say
        let runsOn = try #require(await detector.runsOn)
        #expect(runsOn.model == .cpuOnly && runsOn.vad == .cpuOnly)
        let bundle = speechModelBundle(models: models)
        #expect(checkModelFiles(at: bundle, against: SpeechModel.sha256) == .ok)
        let marker = bundle.deletingLastPathComponent().appendingPathComponent(".fluidaudio-revision")
        #expect(try String(contentsOf: marker, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines) == SpeechModel.revision)
        for stream in ["call-1", "call-2", "call-1"] {
            #expect(try probability(await Server.vad(chunk(), target: "/vad?stream=\(stream)", detector)) < 0.5)  // silence
        }
        #expect(await detector.streams == 2)
        let speech = try spoken("Wait, stop. I want to ask you something about tomorrow morning.", in: models)
        let clock = ContinuousClock()
        var ps: [Double] = []
        var times: [Duration] = []
        for start in stride(from: speech.startIndex, to: speech.endIndex - 8191, by: 8192) {
            let began = clock.now
            let response = await Server.vad(speech[start..<start + 8192], target: "/vad?stream=call-3", detector)
            times.append(clock.now - began)
            ps.append(try probability(response))
        }
        #expect(ps.count >= 10 && ps.allSatisfy { (0...1).contains($0) })
        #expect(ps.filter { $0 >= 0.99 }.count >= 3)  // the spike's stop threshold, on clear speech
        // one /vad chunk (256 ms of audio), request to answer, on the CPU: well under 50 ms, never near the bridge's
        // 600 ms limit (measured: about 0.4 ms a chunk on an M-series CPU)
        let sorted = times.sorted()
        #expect(sorted[sorted.count / 2] < .milliseconds(10), "chunk times: \(sorted)")
        #expect(sorted[sorted.count - 1] < .milliseconds(25), "chunk times: \(sorted)")
    }
}
