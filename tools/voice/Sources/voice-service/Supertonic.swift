// Supertonic-3 (FluidAudio 0.15.8) speaks the ten languages Kokoro has no voice for. Modelled on OpenWhisperer's
// app/Sources/OpenWhisperer/Supertonic3TTS.swift (MIT, same author): an actor, int8 on the Neural Engine, loaded
// once on first use (`LoadOnce`: concurrent first uses share the one load, a failed one is tried again). The first
// use ever downloads ~264 MB into FluidAudio's cache and compiles (about a minute); afterwards a load takes ~0.1 s.
import FluidAudio
import Foundation
import VoiceCore

actor Supertonic {
    private let manager: Supertonic3Manager
    /// Voice F1 (female, the owner's pick) for every language, fetched once with the model.
    private let style: LoadOnce<Supertonic3VoiceStyle>

    init() {
        // ANE-bucketed int8: ~95 % on the Neural Engine and the closest of the quantised builds to FP16
        // (OpenWhisperer measured it against FP16, which is slower and off the Neural Engine, and int4).
        let manager = Supertonic3Manager(computeUnits: .cpuAndNeuralEngine, vectorEstimator: .aneBucketed(.int8))
        self.manager = manager
        style = LoadOnce {
            let start = Date()
            do {
                try await manager.initialize()
                let style = try await Supertonic3ResourceDownloader.loadVoiceStyle(.f1)
                log("speaking other languages ready in \(seconds(since: start))")
                return style
            } catch {
                log("speaking other languages failed to load: \(type(of: error))")
                throw error
            }
        }
    }

    /// Loads on first use (`VoiceError.notReady` when it fails), outside the caller's serial queue: a first load
    /// must not hold up listening.
    func load() async throws {
        _ = try await style.get()
    }

    /// Speech samples at `Supertonic3Constants.sampleRate` (44.1 kHz mono).
    func synthesize(_ text: String, language: String, speed: Float) async throws -> [Float] {
        let style = try await style.get()
        return try await manager.synthesize(text: text, language: language, style: style, speed: speed).samples
    }
}
