import Foundation

/// The turn detector's model, pinned: Smart Turn v3.2 (Daily, pipecat-ai/smart-turn-v3, BSD-2-Clause) in the
/// community CoreML port aufklarer/Smart-Turn-v3.2-CoreML (soniqo speech-swift), converted from Pipecat's fp32 ONNX
/// file `smart-turn-v3.2-gpu.onnx`. One commit, every file of the repository with its sha256: measured on the files
/// downloaded at that commit and matching Hugging Face's own listing of it (the Git LFS files by their sha256, the
/// others by their blob ids). Spike A (17 Oct 2026) compared its answers with Pipecat's ONNX file: mean |dp| 0.001,
/// the same side of 0.5 on 7,192 of 7,200 windows. The model is data: built-in MIL operations only, no custom layers.
public enum TurnModel {
    public static let pin = HubPin(
        repo: "aufklarer/Smart-Turn-v3.2-CoreML",
        revision: "75f069717f500947195fa1945c46ddb472559cff",
        folder: "",
        sha256: [
            ".gitattributes": "11ad7efa24975ee4b0c3c3a38ed18737f0658a5f75a0a96787b576a78a023361",
            "LICENSE": "db31415d226689aaf6fa6274da0416171edb70ca7ebf97bd3d99d8d8b88cd34e",
            "README.md": "417620f5686072f108bafdd91c91d9393295c243aac0b81e6eef33d65cfcba2f",
            "config.json": "bc09d1b40285465b389dc42cef54290fa1db7b4928348018bc61d819b4914402",
            "smart_turn.mlmodelc/analytics/coremldata.bin": "1646746d0029e78d26be065e62602d4f89e51013ec238b653adf7d7d21057f89",
            "smart_turn.mlmodelc/coremldata.bin": "9245ac99889c9d738cba6c89da9e851fd16488bf8effbc15a109d06b401c9383",
            "smart_turn.mlmodelc/model.mil": "41a6d9ac0f7d01d738e56ecdf4435e55f139591541b7cf1a4dba6b9ce2f1c9ef",
            "smart_turn.mlmodelc/weights/weight.bin": "b6295316741a0149e1fcab7cdf030e1b9bfb0eef778207accee91a2a9703a39c",
        ]
    )
    /// The compiled model inside the pin's folder.
    public static let bundle = "smart_turn.mlmodelc"
    /// The most a download takes of one file (the weights are 16.7 MB).
    public static let maxFileBytes = 32 << 20
}

/// A checked `/turn` request: the last (up to) 8 s of the caller's audio, as the model hears it: 128,000 samples at
/// 16 kHz, the most recent last, zeros at the front when there is less (Pipecat's own padding). The model normalises
/// the window itself; nothing is cut here.
public struct TurnWindow: Equatable, Sendable {
    public static let samples = 128_000
    /// 16-bit little-endian mono PCM at 16 kHz: at most 8 s.
    public static let maxBytes = samples * 2

    /// Full scale is -1...1 (as `/vad` reads a chunk).
    public let samples: [Float]

    /// `body` is 2 to 256,000 bytes, an even count; anything else is `VoiceError.badWindow`.
    public init(body: Data) throws(VoiceError) {
        guard !body.isEmpty, body.count.isMultiple(of: 2), body.count <= Self.maxBytes else { throw .badWindow }
        let count = body.count / 2
        var samples = [Float](repeating: 0, count: Self.samples - count)
        samples.reserveCapacity(Self.samples)
        body.withUnsafeBytes { raw in
            for index in 0..<count {
                samples.append(Float(Int16(littleEndian: raw.loadUnaligned(fromByteOffset: index * 2, as: Int16.self))) / 32768)
            }
        }
        self.samples = samples
    }

    private init(samples: [Float]) { self.samples = samples }

    /// 8 s of zeros: the warm-up, the model's first use.
    public static let silence = TurnWindow(samples: [Float](repeating: 0, count: samples))
}

/// The `/turn` answer: the probability that the turn is finished, as the model gave it (a Float, exact as a Double),
/// kept within 0...1. One that is not a number is `VoiceError.failed`: JSONSerialization would raise on it and end
/// the process.
public func turnBody(_ p: Float) throws(VoiceError) -> [String: Any] {
    guard p.isFinite else { throw .failed }
    return ["p": Double(min(max(p, 0), 1))]
}
