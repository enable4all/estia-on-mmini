// The turn detector for early end of turn on calls: Smart Turn v3.2 (the community CoreML port, `TurnModel`) judges
// whether the last 8 s of the caller's audio sound like a finished sentence; CoreML on the CPU only, never the Neural
// Engine the other models share. No stream, no state: one window in, one probability out (`/turn`). Its files (about
// 17 MB) are pinned to one commit and checked file by file against their sha256 at every start, with Whisper's rules
// (`prepareModel`): missing files are downloaded at the pinned commit, a changed or added file or a link is never
// loaded (that folder is set aside and downloaded fresh once; changed again, unavailable). When it cannot load, the
// detector is "unavailable" and the bridge waits the full pause, as before. Never audio in the log, nothing per
// prediction.
import CoreML
import Foundation
import VoiceCore

/// The CoreML configuration the model loads with: the CPU only. On the Neural Engine a window would wait behind
/// Whisper, Kokoro or Supertonic (one speaking or transcribing for seconds), and the bridge gives up on an answer
/// after 0.3 s; the CPU answers a window in about 3 ms (spike A: 18 ms at most with every core busy).
func turnModelConfiguration() -> MLModelConfiguration {
    let configuration = MLModelConfiguration()
    configuration.computeUnits = .cpuOnly
    return configuration
}

/// The service's download of a turn model's pinned files: over https, at the pinned commit, at most 32 MiB each.
func downloadTurnModel(
    _ pin: HubPin, models: URL, fetch: FileFetch = { try await fetchFile($0, to: $1, maxBytes: $2) }
) async throws {
    try await downloadPinnedFiles(pin, in: models, maxBytes: TurnModel.maxFileBytes, fetch: fetch)
}

/// A loaded turn model: CoreML's, or a test's.
protocol TurnPredictor {
    /// What it runs on, read back from the loaded model.
    var computeUnits: MLComputeUnits { get }
    /// The probability that the window ends a finished turn; the model's own number, unchecked.
    func probability(of window: TurnWindow) throws -> Float
}

/// Smart Turn in CoreML: input `audio` (float32, [1, 128000]), output `probability` ([1, 1]).
struct CoreMLTurnModel: TurnPredictor {
    let model: MLModel

    init(bundle: URL) throws {
        model = try MLModel(contentsOf: bundle, configuration: turnModelConfiguration())
    }

    var computeUnits: MLComputeUnits { model.configuration.computeUnits }

    func probability(of window: TurnWindow) throws -> Float {
        let audio = try MLMultiArray(shape: [1, NSNumber(value: TurnWindow.samples)], dataType: .float32)
        audio.withUnsafeMutableBufferPointer(ofType: Float.self) { buffer, _ in
            _ = buffer.update(from: window.samples)
        }
        let output = try model.prediction(from: MLDictionaryFeatureProvider(dictionary: ["audio": MLFeatureValue(multiArray: audio)]))
        guard let p = output.featureValue(for: "probability")?.multiArrayValue, p.count >= 1 else { throw VoiceError.failed }
        return p[0].floatValue
    }
}

actor TurnDetector {
    enum State: String { case loading, ready, unavailable }

    private(set) var state = State.loading
    /// What the loaded model runs on, read back once ready (nil before): the CPU only.
    private(set) var runsOn: MLComputeUnits?
    private var model: (any TurnPredictor)?
    private let models: URL
    private let pin: HubPin
    private let download: @Sendable (HubPin, URL) async throws -> Void
    private let loadModel: @Sendable (URL) throws -> any TurnPredictor

    /// `download` fetches a pin's files into the models folder, `loadModel` loads the checked bundle: the service's
    /// own, or a test's.
    init(
        models: URL, pin: HubPin = TurnModel.pin,
        download: @escaping @Sendable (HubPin, URL) async throws -> Void = { try await downloadTurnModel($0, models: $1) },
        loadModel: @escaping @Sendable (URL) throws -> any TurnPredictor = { try CoreMLTurnModel(bundle: $0) }
    ) {
        self.models = models
        self.pin = pin
        self.download = download
        self.loadModel = loadModel
    }

    /// Checks the pinned files (downloading them when missing), loads the model on the CPU and runs one window of
    /// silence (its first use). Never throws: what fails is logged and leaves it "unavailable".
    func load() async {
        let start = Date()
        let (pin, models, download) = (self.pin, self.models, self.download)
        let files: ModelFiles
        do {
            files = try await prepareModel(pin, in: models, setAside: { aside in
                log("turn detector: model files changed, not the pinned ones (\(pin.path)): set aside as \(aside.lastPathComponent), never loaded; downloading the pinned ones again")
            }) {
                try await download($0, models)
            }
        } catch {
            state = .unavailable
            log("turn detector unavailable: download failed (\(downloadFailure(error)))")
            return
        }
        switch files {
        case .ok:
            break
        case .missing:
            state = .unavailable
            log("turn detector unavailable: model files still missing after the download (\(pin.path))")
            return
        case .changed:
            state = .unavailable
            log("turn detector unavailable: model files changed, not the pinned ones (\(pin.path) in voice-models, even after a fresh download, or could not be set aside; never loaded)")
            return
        }
        do {
            let model = try loadModel(pin.location(in: models).appendingPathComponent(TurnModel.bundle))
            _ = try model.probability(of: TurnWindow.silence)
            self.model = model
            runsOn = model.computeUnits
            state = .ready
            log("turn detector ready in \(seconds(since: start))")
        } catch {
            state = .unavailable
            log("turn detector unavailable: \(type(of: error))")
        }
    }

    /// The window's probability of a finished turn, as the model gave it. One prediction at a time: it runs inside
    /// the actor, without a suspension.
    func probability(of window: TurnWindow) throws -> Float {
        guard let model else { throw state == .loading ? VoiceError.notReady : VoiceError.unavailable }
        return try model.probability(of: window)
    }
}
