import Foundation

public enum Limits {
    public static let maxAudioBytes = 10_000_000
    public static let maxAudioSeconds = 180.0
    public static let maxRequestBytes = maxAudioBytes + 64 * 1024
}

/// The audio format from a file's first bytes, never from a claimed type (Hermes: iOS sends M4A,
/// Android ADTS AAC, and both get mislabelled). nil when it is not audio this service reads.
public func sniffAudio(_ data: Data) -> String? {
    let bytes = [UInt8](data.prefix(12))
    func ascii(_ range: Range<Int>) -> String? {
        bytes.count >= range.upperBound ? String(bytes: bytes[range], encoding: .ascii) : nil
    }
    if ascii(4..<8) == "ftyp" { return "m4a" }
    if bytes.count >= 2, bytes[0] == 0xFF, bytes[1] & 0xF6 == 0xF0 { return "aac" }  // ADTS sync, layer 00
    if ascii(0..<4) == "RIFF", ascii(8..<12) == "WAVE" { return "wav" }
    if ascii(0..<3) == "ID3" { return "mp3" }
    if bytes.count >= 2, bytes[0] == 0xFF, bytes[1] & 0xE0 == 0xE0 { return "mp3" }  // MPEG frame sync
    if ascii(0..<4) == "OggS" { return "ogg" }
    return nil
}

/// A silent 16 kHz mono 16-bit WAV, for the warm-up transcription at start: the Neural Engine compiles
/// the model on its first use (about 15 minutes after a macOS update), which must not be a user's voice note.
public func silentWAV(seconds: Int) -> Data {
    pcm16WAV([Float](repeating: 0, count: 16_000 * seconds), sampleRate: 16_000)
}

/// A mono 16-bit PCM WAV of `samples` (full scale is -1...1; louder ones are clipped, non-finite ones are silence).
public func pcm16WAV(_ samples: [Float], sampleRate: Int) -> Data {
    let dataSize = samples.count * 2
    func le32(_ value: Int) -> [UInt8] { (0..<4).map { UInt8((value >> (8 * $0)) & 0xFF) } }
    func le16(_ value: Int) -> [UInt8] { (0..<2).map { UInt8((value >> (8 * $0)) & 0xFF) } }
    var header: [UInt8] = Array("RIFF".utf8) + le32(36 + dataSize) + Array("WAVEfmt ".utf8)
    header += le32(16) + le16(1) + le16(1) + le32(sampleRate) + le32(sampleRate * 2) + le16(2) + le16(16)
    header += Array("data".utf8) + le32(dataSize)
    let pcm = samples.map { $0.isFinite ? Int16((min(max($0, -1), 1) * 32767).rounded()).littleEndian : 0 }
    return Data(header) + pcm.withUnsafeBytes { Data($0) }
}

/// What the bridge gets from every voice: Kokoro's own format, 24 kHz mono 16-bit WAV.
public enum SpeechAudio {
    public static let sampleRate = 24_000

    /// Float samples (Supertonic-3: 44.1 kHz) in that format, resampled, at their own level (non-finite samples
    /// become silence). Not peak-normalised as FluidAudio's CLI and OpenWhisperer do: measured here, Supertonic-3
    /// and Kokoro both speak at about -26 dBFS RMS, and normalising made Supertonic 5-9 dB louder, varying by
    /// sentence (a call mixes both voices, sentence by sentence).
    public static func wav(_ samples: [Float], sampleRate: Int) -> Data {
        pcm16WAV(resample(samples.map { $0.isFinite ? $0 : 0 }, from: sampleRate, to: Self.sampleRate), sampleRate: Self.sampleRate)
    }
}
