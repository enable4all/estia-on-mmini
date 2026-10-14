// The speech detector for barge-in on calls: Silero VAD v6.2.1 through FluidAudio 0.15.8's VadManager (CoreML on the
// CPU only, never the Neural Engine the other models share; 256 ms chunks at 16 kHz). The bridge sends each call's
// audio as a stream of chunks and gets one speech probability per chunk; one Silero state per stream (`StreamTable`:
// dropped after 60 s without a chunk, at most four). The model (about 1 MB) is pinned to one commit and checked file by
// file against its sha256 (`SpeechModel`) before it is loaded; when it cannot be, the detector is "unavailable" and the
// bridge keeps its loudness rule. Never audio or stream ids in the log.
import CoreML
import FluidAudio
import Foundation
import VoiceCore

/// The model's folder: FluidAudio's own layout, under the service's models (beside Whisper's).
func speechModelBundle(models: URL) -> URL {
    models.appendingPathComponent(Repo.vad.folderName).appendingPathComponent(ModelNames.VAD.sileroVadFile)
}

/// Makes FluidAudio download the model at the pinned commit; once, at start, before anything else loads.
func pinSpeechModel() {
    ModelRegistry.revisionOverrides[Repo.vad.remotePath] = SpeechModel.revision
}

/// The CoreML configuration the model loads with: the CPU only. On the Neural Engine a chunk would wait behind
/// Whisper, Kokoro or Supertonic (one speaking or transcribing for seconds), and the bridge gives up on an answer
/// later than 600 ms of audio; Silero is small enough that the CPU answers a chunk in about a millisecond.
func speechModelConfiguration() -> MLModelConfiguration {
    let configuration = MLModelConfiguration()
    configuration.computeUnits = .cpuOnly
    return configuration
}

/// FluidAudio's VadConfig for the detector: the CPU only too, so it never says otherwise than the model it is given.
func speechVadConfig() -> VadConfig { VadConfig(computeUnits: .cpuOnly) }

actor SpeechDetector {
    enum State: String { case loading, ready, unavailable }

    private(set) var state = State.loading
    /// What the loaded model and FluidAudio run on, read back from both once ready (nil before): the CPU only.
    private(set) var runsOn: (model: MLComputeUnits, vad: MLComputeUnits)?
    private var manager: VadManager?
    private var table = StreamTable<VadStreamState>()
    private let models: URL
    /// One chunk at a time, in turn: a stream's next chunk needs the state its last one left.
    private var busy = false
    private var waiting: [CheckedContinuation<Void, Never>] = []

    init(models: URL) { self.models = models }

    var streams: Int { table.count }

    /// Downloads the model when it is not there yet, checks it against the pins, loads it on the CPU and runs one
    /// chunk of silence (its first use). Never throws: what fails is logged and leaves it "unavailable".
    func load() async {
        let start = Date()
        let bundle = speechModelBundle(models: models)
        var files = checkModelFiles(at: bundle, against: SpeechModel.sha256)
        if files == .missing {
            do {
                try await ModelHub.download(.vad, to: models)
            } catch {
                state = .unavailable
                log("speech detector unavailable: download failed (\(type(of: error)))")
                return
            }
            files = checkModelFiles(at: bundle, against: SpeechModel.sha256)
        }
        guard files == .ok else {
            state = .unavailable
            log("speech detector unavailable: model files \(files.rawValue), not the pinned ones (delete \(Repo.vad.folderName) in the models folder to download it again)")
            return
        }
        do {
            let model = try MLModel(contentsOf: bundle, configuration: speechModelConfiguration())
            let manager = VadManager(config: speechVadConfig(), vadModel: model)
            _ = try await manager.processStreamingChunk([Float](repeating: 0, count: SpeechChunk.samples), state: VadStreamState.initial())
            self.manager = manager
            runsOn = (model.configuration.computeUnits, await manager.config.computeUnits)
            state = .ready
            log("speech detector ready in \(seconds(since: start))")
        } catch {
            state = .unavailable
            log("speech detector unavailable: \(type(of: error))")
        }
    }

    /// The chunk's speech probability (0...1), carrying its stream's state on to the next chunk.
    func probability(of chunk: SpeechChunk) async throws -> Float {
        guard let manager else { throw state == .loading ? VoiceError.notReady : VoiceError.unavailable }
        if busy { await withCheckedContinuation { waiting.append($0) } }
        busy = true
        defer {
            if waiting.isEmpty { busy = false } else { waiting.removeFirst().resume() }
        }
        let now = ProcessInfo.processInfo.systemUptime
        let idle = table.expire(at: now)
        let known = table.state(of: chunk.stream)
        let result = try await manager.processStreamingChunk(chunk.samples, state: known ?? VadStreamState.initial())
        let pushed = table.keep(result.state, for: chunk.stream, at: now)
        var changes: [String] = []
        if known == nil { changes.append("a new stream") }
        if idle > 0 { changes.append("\(idle) idle dropped") }
        if pushed > 0 { changes.append("the longest idle dropped (at most \(StreamTable<VadStreamState>.maxStreams))") }
        if !changes.isEmpty { log("speech detector: \(changes.joined(separator: ", ")); \(table.count) open") }
        return result.probability
    }
}
