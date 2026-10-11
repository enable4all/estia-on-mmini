import Foundation

/// A checked `/v1/audio/transcriptions` request: the language first (a wrong one is refused whatever the body
/// holds), then the audio's kind from its bytes, then its size.
public struct TranscriptionRequest: Equatable, Sendable {
    /// One of `Languages.all`: English when the request names none.
    public let language: String
    /// What `sniffAudio` found, also the temporary file's extension.
    public let kind: String

    public init(target: String, body: Data) throws(VoiceError) {
        language = try Languages.transcription(target: target)
        guard let kind = sniffAudio(body) else { throw .notAudio }
        guard body.count <= Limits.maxAudioBytes else { throw .tooLong }
        self.kind = kind
    }
}

/// What the service needs from a Whisper segment (WhisperKit's `TranscriptionSegment` maps onto it).
public struct TranscriptSegment: Equatable, Sendable {
    public let text: String
    public let noSpeechProb: Float
    public let avgLogprob: Float

    public init(text: String, noSpeechProb: Float, avgLogprob: Float) {
        self.text = text
        self.noSpeechProb = noSpeechProb
        self.avgLogprob = avgLogprob
    }
}

/// Whisper's habits on silence and noise (Hermes, OpenClaw): confident-silence segments go,
/// bracketed noise tags go, and a transcript that is only a known filler phrase is empty.
public func cleanTranscript(_ segments: [TranscriptSegment]) -> String {
    let kept = segments.filter { !($0.noSpeechProb > 0.6 && $0.avgLogprob < -1.0) }
    let text = kept.map(\.text).joined(separator: " ")
        .replacingOccurrences(of: #"[\[(][^\])]{0,40}[\])]"#, with: " ", options: .regularExpression)
        .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        .trimmingCharacters(in: .whitespacesAndNewlines)
    let junk: Set<String> = ["", "you", "thank you", "thank you.", "thanks.", "thanks for watching", "thanks for watching!", "bye.", "."]
    return junk.contains(text.lowercased()) ? "" : text
}
