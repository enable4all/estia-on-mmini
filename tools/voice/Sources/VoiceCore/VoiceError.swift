import Foundation

/// Why a request could not be served; the bridge reads the status (and the code, for logs).
public enum VoiceError: Error, Equatable, Sendable {
    case notAudio, tooLong, unreadable, notReady, failed, unsupportedLanguage
    /// The speech detector's: a stream id or a chunk the bridge got wrong, and a model that will not load.
    case badStream, badChunk, unavailable

    public var status: Int {
        switch self {
        case .notAudio: 415
        case .tooLong: 413
        case .unreadable: 422
        case .notReady: 503
        case .failed: 500
        case .unsupportedLanguage: 400
        case .badStream, .badChunk: 400
        case .unavailable: 503
        }
    }

    public var code: String {
        switch self {
        case .notAudio: "not_audio"
        case .tooLong: "too_long"
        case .unreadable: "unreadable"
        case .notReady: "not_ready"
        case .failed: "failed"
        case .unsupportedLanguage: "unsupported_language"
        case .badStream: "bad_stream"
        case .badChunk: "bad_chunk"
        case .unavailable: "unavailable"
        }
    }

    public var response: Data { HTTP.json(status, ["error": code]) }
}
