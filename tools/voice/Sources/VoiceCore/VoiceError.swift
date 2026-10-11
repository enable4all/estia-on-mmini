import Foundation

/// Why a request could not be served; the bridge reads the status (and the code, for logs).
public enum VoiceError: Error, Equatable, Sendable {
    case notAudio, tooLong, unreadable, notReady, failed, unsupportedLanguage

    public var status: Int {
        switch self {
        case .notAudio: 415
        case .tooLong: 413
        case .unreadable: 422
        case .notReady: 503
        case .failed: 500
        case .unsupportedLanguage: 400
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
        }
    }

    public var response: Data { HTTP.json(status, ["error": code]) }
}
