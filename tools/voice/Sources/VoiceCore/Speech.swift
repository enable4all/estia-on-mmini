import Foundation

/// Who speaks a language: Kokoro English as before, Supertonic-3 (voice F1) the other ten.
public enum SpeechEngine: Equatable, Sendable { case kokoro, supertonic }

/// A checked `/v1/audio/speech` request (OpenAI shape: input, voice, speed; plus language).
public struct SpeechRequest: Equatable, Sendable {
    public struct Invalid: Error, Equatable { public let reason: String }

    public static let maxCharacters = 1500
    public static let defaultVoice = "af_heart"
    public static let defaultSpeed = 1.1
    static let speedRange = 0.7...1.5

    public let input: String
    /// Kokoro's voice; Supertonic-3 ignores it.
    public let voice: String
    public let speed: Double
    /// One of `Languages.all`: English when the request names none (the bridge's English requests are unchanged).
    public let language: String

    /// Throws `VoiceError.unsupportedLanguage` for any language but the eleven, else `Invalid`.
    public init(json: Data) throws {
        guard let object = try? JSONSerialization.jsonObject(with: json) as? [String: Any] else {
            throw Invalid(reason: "body is not a JSON object")
        }
        language = object.keys.contains("language") ? try Languages.checked(object["language"]) : Languages.english
        let input = (object["input"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty else { throw Invalid(reason: "input is empty") }
        guard input.count <= Self.maxCharacters else { throw Invalid(reason: "input is over \(Self.maxCharacters) characters") }
        self.input = input
        // Kokoro voice ids look like `af_heart`; anything else (paths, odd spellings) gets the default,
        // because FluidAudio caches voice packs under the raw string.
        let voice = object["voice"] as? String ?? ""
        self.voice = voice.range(of: #"^[a-z]{2}_[a-z]{2,20}$"#, options: .regularExpression) != nil ? voice : Self.defaultVoice
        let speed = (object["speed"] as? NSNumber)?.doubleValue ?? Self.defaultSpeed
        self.speed = speed.isFinite ? min(max(speed, Self.speedRange.lowerBound), Self.speedRange.upperBound) : Self.defaultSpeed
    }

    public var engine: SpeechEngine { language == Languages.english ? .kokoro : .supertonic }

    /// The text the engine gets: smart apostrophes plain, one line (Kokoro stops at a newline), and in English
    /// numbers as words; other languages keep their digits (Supertonic-3 reads them, English words would be wrong).
    public var spokenInput: String {
        let plain = input.replacingOccurrences(of: "\u{2019}", with: "'").replacingOccurrences(of: "\u{2018}", with: "'")
        let oneLine = plain.components(separatedBy: .newlines).filter { !$0.isEmpty }.joined(separator: " ")
        return engine == .kokoro ? NumberNormalizer.normalize(oneLine) : oneLine
    }
}
