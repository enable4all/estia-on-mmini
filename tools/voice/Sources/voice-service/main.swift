// voice-service: local-assistant's ears and voice. WhisperKit (large-v3 turbo; its files pinned and checked at start,
// see Whisper.swift) listens in the language the bridge names (one of eleven, never detected); FluidAudio speaks it:
// Kokoro English, Supertonic-3 the other ten, both on the Neural Engine; and FluidAudio's Silero VAD tells speech in a
// call's audio (`/vad`, for barge-in), on the CPU only. Served on 127.0.0.1:8150 for the bridge only. Logs hold
// metadata (sizes, durations, language, timings), never audio, transcripts or text.
import AVFoundation
import FluidAudio
import Foundation
import Network
import VoiceCore
import WhisperKit

func log(_ message: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    LogCap.trim(fd: STDERR_FILENO)  // voice.log stays under 10 MB, in-process
    FileHandle.standardError.write(Data("\(stamp) voice-service: \(message)\n".utf8))
}

func seconds(since start: Date) -> String { String(format: "%.2f s", Date().timeIntervalSince(start)) }

/// Whisper's settings for a transcription: the named language is forced through the prefill prompt and never
/// detected (a wrong guess costs more than the switch); greedy, no timestamps, long audio cut at pauses.
func whisperOptions(language: String) -> DecodingOptions {
    DecodingOptions(
        task: .transcribe, language: language, temperature: 0, usePrefillPrompt: true, detectLanguage: false,
        skipSpecialTokens: true, withoutTimestamps: true, chunkingStrategy: .vad
    )
}

/// Runs one piece of engine work at a time (the spec's serial queue); later callers wait their turn.
actor Serial {
    private var busy = false
    private var waiting: [CheckedContinuation<Void, Never>] = []

    func run<T>(_ work: () async throws -> T) async rethrows -> T {
        if busy { await withCheckedContinuation { waiting.append($0) } }
        busy = true
        defer {
            if waiting.isEmpty { busy = false } else { waiting.removeFirst().resume() }
        }
        return try await work()
    }
}

/// Whisper and Kokoro, loaded and warmed once at start, then kept in memory; Supertonic-3 on first use; the
/// speech detector at start too, on its own.
actor Engines {
    enum State: String { case loading, ready, failed }

    private(set) var stt = State.loading
    private(set) var tts = State.loading
    /// Listening failed because Whisper's files are not the pinned ones: a restart would find the same files.
    private var sttRefused = false
    private var whisper: WhisperKit?
    private var kokoro: KokoroAneManager?
    private let supertonic = Supertonic()
    let detector: SpeechDetector
    private let models: URL
    private let temp: URL
    private let serial = Serial()

    init(models: URL, temp: URL) {
        self.models = models
        self.temp = temp
        detector = SpeechDetector(models: models)
    }

    /// Loads both engines. Listening that fails exits the process, so launchd starts it again (KeepAlive), except
    /// when Whisper's files are not the pinned ones: listening stays "failed" and the service stays up. Speaking that
    /// fails stays "failed" with the service up (listening and the detector work on) and is tried again in the
    /// process (`SpeakingRetry`: 1, 5 and 30 minutes, then every 30), never by a restart that reloads Whisper. The
    /// speech detector loads beside them, without waiting for Whisper's compile; when it fails it is only
    /// "unavailable" (the bridge keeps its loudness rule), and the service stays up.
    func load() async {
        let detector = self.detector
        Task { await detector.load() }
        await loadWhisper()
        await loadKokoro()
        if stt == .failed && !sttRefused {
            log("listening failed to load; exiting so launchd tries again")
            exit(1)
        }
        if tts == .failed {
            log("speaking failed to load; the service stays up and tries it again in 1 minute")
            Task {
                await SpeakingRetry.run(
                    sleep: { try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) },
                    load: { await self.retryKokoro() })
            }
        }
    }

    /// One more try at speaking: true once it is ready.
    private func retryKokoro() async -> Bool {
        await loadKokoro()
        if tts != .ready { log("speaking failed to load again; tried again later") }
        return tts == .ready
    }

    /// Whisper's pinned files, downloaded at their commits when missing: true when every one is the pinned one. A
    /// failed download throws, and files still missing after it (offline) return false: launchd tries again. Changed
    /// files are set aside (never loaded) and downloaded again once; changed again, they return false with listening
    /// refused.
    private func whisperFiles() async throws -> Bool {
        let start = Date()
        let models = self.models
        var downloaded = false
        for pin in whisperPins {
            let files = try await prepareModel(pin, in: models, setAside: { aside in
                log("listening: model files changed, not the pinned ones (\(pin.path)): set aside as \(aside.lastPathComponent), never loaded; downloading the pinned ones again")
            }) {
                downloaded = true
                try await downloadPinned($0, models: models)
            }
            switch files {
            case .ok:
                continue
            case .missing:
                log("listening failed to load: model files still missing after the download (\(pin.path))")
                return false
            case .changed:
                sttRefused = true
                log("listening unavailable: model files are not the pinned ones even after a fresh download, or could not be set aside (\(pin.path) in voice-models; never loaded)")
                return false
            }
        }
        log("listening: pinned model files \(downloaded ? "downloaded and checked" : "checked") in \(seconds(since: start))")
        return true
    }

    private func loadWhisper() async {
        let start = Date()
        do {
            guard try await whisperFiles() else {
                stt = .failed
                return
            }
            let whisper = try await WhisperKit(whisperConfig(models: models))
            // warm-up: the first transcription compiles the model for the Neural Engine (cached per macOS build)
            let silence = temp.appendingPathComponent("warm-up.wav")
            try silentWAV(seconds: 1).write(to: silence)
            defer { try? FileManager.default.removeItem(at: silence) }
            _ = try await whisper.transcribe(audioPath: silence.path, decodeOptions: DecodingOptions(language: "en"))
            self.whisper = whisper
            stt = .ready
            log("listening ready in \(seconds(since: start))")
        } catch {
            stt = .failed
            log("listening failed to load: \(type(of: error))")
        }
    }

    private func loadKokoro() async {
        let start = Date()
        do {
            let kokoro = KokoroAneManager(variant: .english)
            try await kokoro.initialize()
            _ = try await kokoro.synthesize(text: "Ready.", voice: SpeechRequest.defaultVoice, speed: 1.1)  // warm-up
            self.kokoro = kokoro
            tts = .ready
            log("speaking ready in \(seconds(since: start))")
        } catch {
            tts = .failed
            log("speaking failed to load: \(type(of: error))")
        }
    }

    func health() async -> [String: Any] {
        healthBody(stt: stt.rawValue, tts: tts.rawValue, vad: await detector.state.rawValue)
    }

    /// Transcribes audio bytes of a sniffed kind in the given language; the file lives only for the call.
    func transcribe(_ audio: Data, kind: String, language: String) async throws -> (text: String, duration: Double) {
        guard let whisper else { throw VoiceError.notReady }
        let file = temp.appendingPathComponent("\(UUID().uuidString).\(kind)")
        guard FileManager.default.createFile(atPath: file.path, contents: audio, attributes: [.posixPermissions: 0o600]) else {
            throw VoiceError.failed
        }
        defer { try? FileManager.default.removeItem(at: file) }
        let duration: Double
        do {
            let audioFile = try AVAudioFile(forReading: file)
            duration = Double(audioFile.length) / audioFile.fileFormat.sampleRate
        } catch {
            throw VoiceError.unreadable
        }
        guard duration <= Limits.maxAudioSeconds else { throw VoiceError.tooLong }
        let options = whisperOptions(language: language)
        let results = try await serial.run { try await whisper.transcribe(audioPath: file.path, decodeOptions: options) }
        let segments = results.flatMap(\.segments).map {
            TranscriptSegment(text: $0.text, noSpeechProb: $0.noSpeechProb, avgLogprob: $0.avgLogprob)
        }
        return (cleanTranscript(segments), duration)
    }

    /// 24 kHz mono 16-bit WAV from either engine (Kokoro writes it; Supertonic-3's 44.1 kHz is converted).
    func speak(_ request: SpeechRequest) async throws -> Data {
        switch request.engine {
        case .kokoro:
            guard let kokoro else { throw VoiceError.notReady }
            return try await serial.run {
                try await kokoro.synthesize(text: request.spokenInput, voice: request.voice, speed: Float(request.speed))
            }
        case .supertonic:
            let supertonic = self.supertonic
            try await supertonic.load()
            let samples = try await serial.run {
                try await supertonic.synthesize(request.spokenInput, language: request.language, speed: Float(request.speed))
            }
            return SpeechAudio.wav(samples, sampleRate: Supertonic3Constants.sampleRate)
        }
    }
}

/// Loopback HTTP/1.1 server on Network.framework, one request per connection (after OpenWhisperer).
final class Server {
    /// A connection that has not delivered its whole request by then is dropped.
    static let receiveTimeout: TimeInterval = 30

    private final class Connection {
        let nw: NWConnection
        var received = false
        init(_ nw: NWConnection) { self.nw = nw }
    }

    private let engines: Engines
    private let queue = DispatchQueue(label: "voice.http")
    private var listener: NWListener?

    init(engines: Engines) { self.engines = engines }

    func start(port: UInt16) throws {
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        params.requiredInterfaceType = .loopback
        params.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!)
        let listener = try NWListener(using: params)
        listener.stateUpdateHandler = { state in
            if case .failed(let error) = state {
                log("cannot listen on 127.0.0.1:\(port): \(error)")
                exit(1)  // launchd restarts the service
            }
        }
        listener.newConnectionHandler = { [weak self] in self?.accept($0) }
        listener.start(queue: queue)
        self.listener = listener
        log("listening on 127.0.0.1:\(port)")
    }

    private func accept(_ nw: NWConnection) {
        let connection = Connection(nw)
        nw.start(queue: queue)
        queue.asyncAfter(deadline: .now() + Self.receiveTimeout) {
            if !connection.received { nw.cancel() }
        }
        receive(connection, buffer: Data())
    }

    private func receive(_ connection: Connection, buffer: Data) {
        connection.nw.receive(minimumIncompleteLength: 1, maximumLength: 1 << 16) { [weak self] data, _, complete, error in
            guard let self else { return }
            var buffer = buffer
            if let data { buffer.append(data) }
            switch HTTP.parse(buffer, maxBody: Limits.maxRequestBytes) {
            case .request(let request):
                connection.received = true
                self.handle(connection.nw, request)
            case .invalid(let status):
                connection.received = true
                self.send(connection.nw, HTTP.json(status, ["error": "bad_request"]))
            case .incomplete:
                if complete || error != nil { connection.nw.cancel() } else { self.receive(connection, buffer: buffer) }
            }
        }
    }

    private func handle(_ nw: NWConnection, _ request: HTTPRequest) {
        guard HTTP.isTrustedCaller(origin: request.header("origin"), host: request.header("host")) else {
            return send(nw, HTTP.json(403, ["error": "forbidden"]))
        }
        let engines = self.engines
        switch Route(request.method, request.path) {
        case .health:
            Task { self.send(nw, HTTP.json(200, await engines.health())) }
        case .transcribe:
            Task { self.send(nw, await Self.transcribe(request.body, target: request.path, engines)) }
        case .speak:
            Task { self.send(nw, await Self.speak(request.body, engines)) }
        case .vad:
            Task { self.send(nw, await Self.vad(request.body, target: request.path, engines.detector)) }
        case .methodNotAllowed:
            send(nw, HTTP.json(405, ["error": "method_not_allowed"]))
        case .notFound:
            send(nw, HTTP.json(404, ["error": "not_found"]))
        }
    }

    private static func transcribe(_ body: Data, target: String, _ engines: Engines) async -> Data {
        let start = Date()
        let request: TranscriptionRequest
        do {
            request = try TranscriptionRequest(target: target, body: body)
        } catch {
            log("transcription refused: \(error.code), \(body.count) bytes")
            return error.response
        }
        let (kind, language) = (request.kind, request.language)
        do {
            let (text, duration) = try await engines.transcribe(body, kind: kind, language: language)
            log("transcribed \(body.count) bytes \(kind) \(language), \(String(format: "%.1f", duration)) s audio, \(text.count) chars in \(seconds(since: start))")
            return HTTP.json(200, ["text": text, "duration": (duration * 10).rounded() / 10])
        } catch let error as VoiceError {
            log("transcription refused: \(error.code), \(body.count) bytes \(kind) \(language)")
            return error.response
        } catch {
            log("transcription failed: \(type(of: error)), \(body.count) bytes \(kind) \(language)")
            return VoiceError.unreadable.response
        }
    }

    private static func speak(_ body: Data, _ engines: Engines) async -> Data {
        let start = Date()
        let request: SpeechRequest
        do {
            request = try SpeechRequest(json: body)
        } catch let error as VoiceError {
            log("speech refused: \(error.code)")
            return error.response
        } catch {
            return HTTP.json(400, ["error": "bad_request"])
        }
        do {
            let wav = try await engines.speak(request)
            log("spoke \(request.input.count) chars \(request.language) as \(wav.count) bytes in \(seconds(since: start))")
            return HTTP.response(200, contentType: "audio/wav", body: wav)
        } catch let error as VoiceError {
            return error.response
        } catch {
            log("speech failed: \(type(of: error)), \(request.language)")
            return VoiceError.failed.response
        }
    }

    /// One chunk's speech probability; refusals are logged (the bridge's mistakes), not "loading" or "unavailable",
    /// which the bridge reads from `/health` and answers with its loudness rule.
    static func vad(_ body: Data, target: String, _ detector: SpeechDetector) async -> Data {
        let chunk: SpeechChunk
        do {
            chunk = try SpeechChunk(target: target, body: body)
        } catch {
            log("speech detector refused: \(error.code), \(body.count) bytes")
            return error.response
        }
        do {
            return HTTP.json(200, try speechBody(try await detector.probability(of: chunk)))
        } catch let error as VoiceError {
            if error == .failed { log("speech detector failed: not a probability") }
            return error.response
        } catch {
            log("speech detector failed: \(type(of: error))")
            return VoiceError.failed.response
        }
    }

    private func send(_ nw: NWConnection, _ response: Data) {
        nw.send(content: response, completion: .contentProcessed { _ in nw.cancel() })
    }
}

// MARK: - Start

LogCap.trim(fd: STDOUT_FILENO)
LogCap.trim(fd: STDERR_FILENO)
let support = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support/local-assistant")
let models = support.appendingPathComponent("voice-models")
let temp = support.appendingPathComponent("voice-tmp")
do {
    for folder in [models, temp] {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    }
    for leftover in try FileManager.default.contentsOfDirectory(at: temp, includingPropertiesForKeys: nil) {
        try? FileManager.default.removeItem(at: leftover)  // audio from a crash: never kept
    }
} catch {
    log("cannot prepare \(support.path): \(error)")
    exit(1)
}
pinSpeechModel()
if KokoroAdvisory.crashProne(ProcessInfo.processInfo.operatingSystemVersion) { log(KokoroAdvisory.warning) }
let port = CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--port" ? UInt16(CommandLine.arguments[2]) ?? 8150 : 8150
let engines = Engines(models: models, temp: temp)
let server = Server(engines: engines)
do {
    try server.start(port: port)
} catch {
    log("cannot listen: \(error)")
    exit(1)
}
Task { await engines.load() }
dispatchMain()
