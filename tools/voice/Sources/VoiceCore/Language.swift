import Foundation

/// The languages the service hears and speaks, one at a time: the bridge names one on every request and
/// Whisper never detects it (a wrong guess costs more than the switch). Whisper large-v3's lowest error rates
/// (OpenAI's chart, as recorded in OpenWhisperer's STTLanguages.swift: 2.8 to 5.3 %) that also have a voice.
public enum Languages {
    /// English first, then by error rate.
    public static let all = ["en", "es", "it", "ko", "pt", "nl", "pl", "de", "ja", "ru", "fr"]
    /// A request that names no language: the bridge's English requests stay exactly as they were.
    public static let english = "en"

    /// The language of a transcription: `?language=<code>` on the request target (the first one counts),
    /// English without one; any other value is refused, never guessed.
    public static func transcription(target: String) throws(VoiceError) -> String {
        guard let query = target.split(separator: "?", maxSplits: 1).dropFirst().first else { return english }
        for pair in query.split(separator: "&") {
            let parts = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            if parts[0] == "language" { return try checked(parts.count == 2 ? String(parts[1]) : nil) }
        }
        return english
    }

    /// `value` when it is exactly one of the eleven codes, else `VoiceError.unsupportedLanguage`.
    static func checked(_ value: Any?) throws(VoiceError) -> String {
        guard let code = value as? String, all.contains(code) else { throw .unsupportedLanguage }
        return code
    }
}

/// The `/health` answer: each engine's state, and the languages (the bridge reads their presence to know the
/// installed service takes a language); `vad` is the speech detector's (its presence: the service has `/vad`), `turn`
/// the turn detector's (its presence: the service has `/turn`).
public func healthBody(stt: String, tts: String, vad: String, turn: String) -> [String: Any] {
    ["stt": stt, "tts": tts, "vad": vad, "turn": turn, "languages": Languages.all]
}
