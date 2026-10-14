import Foundation

/// A checked `/vad?stream=<id>` request: one chunk of the caller's audio for the speech detector (Silero VAD as
/// FluidAudio runs it: 4096 samples, 256 ms at 16 kHz, one probability each). The stream id is checked first, then
/// the size.
public struct SpeechChunk: Equatable, Sendable {
    public static let samples = 4096
    /// 16-bit little-endian mono PCM at 16 kHz, exactly one chunk: never padded or cut here (FluidAudio pads a short
    /// chunk by repeating its last sample, which the spike found breaks detection).
    public static let bytes = samples * 2
    static let maxStreamId = 64
    static let streamIdBytes = Set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_".utf8)

    /// The bridge's name for one call's audio: 1 to 64 ASCII letters, digits, `-` and `_`.
    public let stream: String
    /// Full scale is -1...1.
    public let samples: [Float]

    public init(target: String, body: Data) throws(VoiceError) {
        stream = try Self.streamId(target: target)
        guard body.count == Self.bytes else { throw .badChunk }
        samples = body.withUnsafeBytes { raw in
            (0..<Self.samples).map { Float(Int16(littleEndian: raw.loadUnaligned(fromByteOffset: $0 * 2, as: Int16.self))) / 32768 }
        }
    }

    /// `?stream=<id>` on the request target (the first one counts); anything else is `VoiceError.badStream`.
    static func streamId(target: String) throws(VoiceError) -> String {
        guard let query = target.split(separator: "?", maxSplits: 1).dropFirst().first else { throw .badStream }
        for pair in query.split(separator: "&") {
            let parts = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard parts[0] == "stream" else { continue }
            guard parts.count == 2, (1...maxStreamId).contains(parts[1].utf8.count), parts[1].utf8.allSatisfy(streamIdBytes.contains) else {
                throw .badStream
            }
            return String(parts[1])
        }
        throw .badStream
    }
}

/// The `/vad` answer: the chunk's speech probability as the model gave it (a Float, exact as a Double), kept within
/// 0...1. A probability that is not a number is `VoiceError.failed`: JSONSerialization would raise on it and end
/// the process.
public func speechBody(_ p: Float) throws(VoiceError) -> [String: Any] {
    guard p.isFinite else { throw .failed }
    return ["p": Double(min(max(p, 0), 1))]
}

/// The speech detector's streams: the latest state of each stream id (one call's Silero state and context), kept
/// while its chunks come. A stream without a chunk for `idleLimit` seconds is dropped, and a new stream over
/// `maxStreams` drops the one that has gone longest without a chunk; a dropped stream that comes back starts afresh.
/// Times are seconds on a monotonic clock.
public struct StreamTable<State> {
    public static var maxStreams: Int { 4 }
    public static var idleLimit: Double { 60 }

    private var streams: [String: (state: State, last: Double)] = [:]

    public init() {}

    public var count: Int { streams.count }

    public func state(of id: String) -> State? { streams[id]?.state }

    /// Drops the streams without a chunk for `idleLimit` or more at `now`; how many went.
    @discardableResult
    public mutating func expire(at now: Double) -> Int {
        let before = streams.count
        streams = streams.filter { now - $0.value.last < Self.idleLimit }
        return before - streams.count
    }

    /// Keeps `state` as the stream's latest, its chunk at `now`. A new stream when `maxStreams` are kept first drops
    /// the one longest without a chunk; how many went (0 or 1).
    @discardableResult
    public mutating func keep(_ state: State, for id: String, at now: Double) -> Int {
        var dropped = 0
        if streams[id] == nil, streams.count >= Self.maxStreams, let idle = streams.min(by: { $0.value.last < $1.value.last }) {
            streams[idle.key] = nil
            dropped = 1
        }
        streams[id] = (state, now)
        return dropped
    }
}
