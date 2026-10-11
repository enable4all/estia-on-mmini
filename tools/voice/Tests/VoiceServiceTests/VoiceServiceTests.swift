import Testing
import WhisperKit

@testable import voice_service

@Suite struct WhisperSettings {
    @Test func theNamedLanguageIsForcedNeverDetected() {
        for code in ["en", "nl", "ja"] {
            let options = whisperOptions(language: code)
            #expect(options.language == code)
            // WhisperKit forces the language token only with the prefill prompt; without it, it detects
            #expect(options.usePrefillPrompt && !options.detectLanguage)
            #expect(options.task == .transcribe)
        }
    }
}
