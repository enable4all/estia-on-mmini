import Foundation
import Testing

@testable import VoiceCore

private func raw(_ text: String) -> Data { Data(text.utf8) }

/// Little-endian fields and 16-bit samples of a WAV built by the service (44-byte header).
private func le(_ wav: Data, _ at: Int, _ size: Int) -> Int {
    wav[at..<at + size].enumerated().reduce(0) { $0 | Int($1.element) << (8 * $1.offset) }
}

private func pcm(_ wav: Data) -> [Int16] {
    stride(from: 44, to: wav.count - 1, by: 2).map { Int16(bitPattern: UInt16(wav[$0]) | UInt16(wav[$0 + 1]) << 8) }
}

private func sine(_ hertz: Double, rate: Int, count: Int, amplitude: Float = 1) -> [Float] {
    (0..<count).map { amplitude * Float(sin(2 * .pi * hertz * Double($0) / Double(rate))) }
}

@Suite struct HTTPParsing {
    @Test func completeRequestWithBody() {
        let request = raw("POST /v1/audio/transcriptions HTTP/1.1\r\nHost: 127.0.0.1:8150\r\nContent-Length: 4\r\n\r\nabcd")
        guard case .request(let req) = HTTP.parse(request, maxBody: 10) else { Issue.record("not parsed"); return }
        #expect(req.method == "POST" && req.path == "/v1/audio/transcriptions" && req.body == raw("abcd"))
        #expect(req.header("host") == "127.0.0.1:8150")
    }

    @Test func incompleteUntilTheBodyArrives() {
        #expect(HTTP.parse(raw("POST / HTTP/1.1\r\nContent-Length: 4\r\n\r\nab"), maxBody: 10) == .incomplete)
        #expect(HTTP.parse(raw("GET /health HTTP/1.1\r\nHost: x"), maxBody: 10) == .incomplete)
    }

    @Test func tooBigOrMalformedBodies() {
        #expect(HTTP.parse(raw("POST / HTTP/1.1\r\nContent-Length: 11\r\n\r\n"), maxBody: 10) == .invalid(413))
        #expect(HTTP.parse(raw("POST / HTTP/1.1\r\nContent-Length: -1\r\n\r\n"), maxBody: 10) == .invalid(400))
        #expect(HTTP.parse(raw("POST / HTTP/1.1\r\nContent-Length: x\r\n\r\n"), maxBody: 10) == .invalid(400))
        #expect(HTTP.parse(raw("NONSENSE\r\n\r\n"), maxBody: 10) == .invalid(400))
    }

    @Test func firstHeaderWins() {
        let request = raw("GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nHost: evil.example\r\n\r\n")
        guard case .request(let req) = HTTP.parse(request, maxBody: 10) else { Issue.record("not parsed"); return }
        #expect(req.header("host") == "127.0.0.1")
    }

    @Test func browsersAndRebindingAreRefused() {
        #expect(HTTP.isTrustedCaller(origin: nil, host: "127.0.0.1:8150"))
        #expect(HTTP.isTrustedCaller(origin: nil, host: nil))
        // the bridge never sends an Origin: any web page, even a local one, is refused
        #expect(!HTTP.isTrustedCaller(origin: "http://localhost:3000", host: "localhost:8150"))
        #expect(!HTTP.isTrustedCaller(origin: "null", host: "127.0.0.1:8150"))
        #expect(!HTTP.isTrustedCaller(origin: "https://evil.example", host: "127.0.0.1:8150"))
        #expect(!HTTP.isTrustedCaller(origin: nil, host: "evil.example:8150"))
    }

    @Test func responseBytes() {
        let response = String(decoding: HTTP.response(200, contentType: "application/json", body: raw("{}")), as: UTF8.self)
        #expect(response == "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
    }
}

@Suite struct LanguageChoice {
    @Test func theElevenEnglishFirst() {
        #expect(Languages.all == ["en", "es", "it", "ko", "pt", "nl", "pl", "de", "ja", "ru", "fr"])
        #expect(Languages.english == "en")
    }

    @Test func transcriptionWithoutALanguageIsEnglish() throws {
        #expect(try Languages.transcription(target: "/v1/audio/transcriptions") == "en")
        #expect(try Languages.transcription(target: "/v1/audio/transcriptions?") == "en")
        #expect(try Languages.transcription(target: "/v1/audio/transcriptions?other=1") == "en")
    }

    @Test func transcriptionTakesEachOfTheEleven() throws {
        for code in Languages.all {
            #expect(try Languages.transcription(target: "/v1/audio/transcriptions?language=\(code)") == code)
        }
        #expect(try Languages.transcription(target: "/v1/audio/transcriptions?x=1&language=nl") == "nl")
        #expect(try Languages.transcription(target: "/v1/audio/transcriptions?language=nl&language=de") == "nl")
    }

    @Test func anyOtherLanguageIsRefused() {
        for query in ["language=xx", "language=", "language", "language=NL", "language=zh", "language=en-US", "language=n%6C"] {
            #expect(throws: VoiceError.unsupportedLanguage) {
                try Languages.transcription(target: "/v1/audio/transcriptions?\(query)")
            }
        }
    }

    @Test func healthListsTheLanguages() throws {
        let body = healthBody(stt: "ready", tts: "loading", vad: "unavailable")
        #expect(body["stt"] as? String == "ready" && body["tts"] as? String == "loading")
        #expect(body["languages"] as? [String] == Languages.all)
        // the speech detector's state is one more field; the others stay byte for byte as they were
        let text = String(decoding: HTTP.json(200, body), as: UTF8.self)
        #expect(text.hasSuffix(#"{"languages":["en","es","it","ko","pt","nl","pl","de","ja","ru","fr"],"stt":"ready","tts":"loading","vad":"unavailable"}"#))
    }
}

@Suite struct Routing {
    @Test func routes() {
        #expect(Route("POST", "/v1/audio/transcriptions") == .transcribe)
        #expect(Route("POST", "/v1/audio/transcriptions?language=nl") == .transcribe)
        #expect(Route("POST", "/v1/audio/speech") == .speak)
        #expect(Route("GET", "/health") == .health)
        #expect(Route("GET", "/health?x=1") == .health)
        #expect(Route("POST", "/vad?stream=call-1") == .vad)
        #expect(Route("GET", "/v1/audio/speech") == .methodNotAllowed)
        #expect(Route("GET", "/vad?stream=call-1") == .methodNotAllowed)
        #expect(Route("GET", "/other") == .notFound)
    }
}

@Suite struct Speech {
    @Test func validRequestWithDefaults() throws {
        let request = try SpeechRequest(json: raw(#"{"input": "  Hello there.  "}"#))
        #expect(request.input == "Hello there." && request.voice == "af_heart" && request.speed == 1.1)
    }

    @Test func voiceAndSpeedAreChecked() throws {
        let request = try SpeechRequest(json: raw(#"{"input": "Hi.", "voice": "bm_george", "speed": 9}"#))
        #expect(request.voice == "bm_george" && request.speed == 1.5)
        let odd = try SpeechRequest(json: raw(#"{"input": "Hi.", "voice": "../../x", "speed": 0.1}"#))
        #expect(odd.voice == "af_heart" && odd.speed == 0.7)
    }

    @Test func emptyTooLongOrBroken() {
        #expect(throws: SpeechRequest.Invalid.self) { try SpeechRequest(json: raw(#"{"input": "   "}"#)) }
        #expect(throws: SpeechRequest.Invalid.self) { try SpeechRequest(json: raw(#"{"input": "\#(String(repeating: "a", count: 1501))"}"#)) }
        #expect(throws: SpeechRequest.Invalid.self) { try SpeechRequest(json: raw("not json")) }
    }

    @Test func spokenInputIsOneLineWithPlainApostrophes() throws {
        let request = try SpeechRequest(json: raw(#"{"input": "It\u2019s done.\n\nNext: 2 lamps."}"#))
        #expect(request.spokenInput == "It's done. Next: two lamps.")
    }

    @Test func englishWithKokoroUnlessALanguageIsNamed() throws {
        let request = try SpeechRequest(json: raw(#"{"input": "Hi.", "voice": "bm_george"}"#))
        #expect(request.language == "en" && request.engine == .kokoro && request.voice == "bm_george")
        #expect(try SpeechRequest(json: raw(#"{"input": "Hi.", "language": "en"}"#)).engine == .kokoro)
    }

    @Test func theOtherTenSpeakWithSupertonic() throws {
        for code in Languages.all.dropFirst() {
            let request = try SpeechRequest(json: raw(#"{"input": "Hallo.", "language": "\#(code)"}"#))
            #expect(request.language == code && request.engine == .supertonic)
        }
    }

    @Test func anyOtherLanguageIsRefused() {
        for language in [#""xx""#, #""""#, #""NL""#, #""zh""#, #""en-US""#, "5", "null", #"["nl"]"#] {
            #expect(throws: VoiceError.unsupportedLanguage) {
                try SpeechRequest(json: raw(#"{"input": "Hallo.", "language": \#(language)}"#))
            }
        }
    }

    @Test func otherLanguagesKeepTheirDigits() throws {
        // English number words inside a Dutch sentence would be read out wrong; Supertonic-3 reads digits itself
        let request = try SpeechRequest(json: raw(#"{"input": "Het kost 5 euro.\nDat is 20% minder, l\u2019heure.", "language": "nl"}"#))
        #expect(request.spokenInput == "Het kost 5 euro. Dat is 20% minder, l'heure.")
    }

    @Test func numbersAreSpelledOut() {
        #expect(NumberNormalizer.normalize("It costs $5.99, 20% off, 3.5 kg, 1,200 items")
            == "It costs five dollars and ninety nine cents, twenty percent off, three point five kg, one thousand two hundred items")
    }
}

@Suite struct Transcripts {
    @Test func confidentSilenceIsDropped() {
        let segments = [
            TranscriptSegment(text: " Turn off the lamp.", noSpeechProb: 0.1, avgLogprob: -0.2),
            TranscriptSegment(text: " Thank you.", noSpeechProb: 0.9, avgLogprob: -1.4),
        ]
        #expect(cleanTranscript(segments) == "Turn off the lamp.")
    }

    @Test func knownJunkAndNoiseTagsAreEmpty() {
        for junk in ["Thank you.", " you", "[BLANK_AUDIO]", "(music)", "Thanks for watching!", "  "] {
            #expect(cleanTranscript([TranscriptSegment(text: junk, noSpeechProb: 0.2, avgLogprob: -0.5)]) == "")
        }
        #expect(cleanTranscript([TranscriptSegment(text: "[Music] Hello Gemma", noSpeechProb: 0.2, avgLogprob: -0.5)]) == "Hello Gemma")
    }
}

@Suite struct Audio {
    @Test func sniffing() {
        #expect(sniffAudio(Data([0, 0, 0, 0x1C]) + raw("ftypM4A ") + Data(count: 8)) == "m4a")
        #expect(sniffAudio(Data([0xFF, 0xF9, 0x5C, 0x60])) == "aac")
        #expect(sniffAudio(Data([0xFF, 0xF1, 0x50, 0x80])) == "aac")
        #expect(sniffAudio(raw("RIFF") + Data(count: 4) + raw("WAVE")) == "wav")
        #expect(sniffAudio(raw("ID3") + Data(count: 5)) == "mp3")
        #expect(sniffAudio(Data([0xFF, 0xFB, 0x90, 0x44])) == "mp3")
        #expect(sniffAudio(raw("OggS") + Data(count: 4)) == "ogg")
        #expect(sniffAudio(raw("%PDF-1.7")) == nil)
        #expect(sniffAudio(Data()) == nil)
    }
}

@Suite struct Errors {
    @Test func eachErrorHasItsStatusAndCode() {
        let cases: [(VoiceError, Int, String)] = [
            (.notAudio, 415, "not_audio"), (.tooLong, 413, "too_long"), (.unreadable, 422, "unreadable"),
            (.notReady, 503, "not_ready"), (.failed, 500, "failed"), (.unsupportedLanguage, 400, "unsupported_language"),
            (.badStream, 400, "bad_stream"), (.badChunk, 400, "bad_chunk"), (.unavailable, 503, "unavailable"),
        ]
        for (error, status, code) in cases {
            let text = String(decoding: error.response, as: UTF8.self)
            #expect(text.hasPrefix("HTTP/1.1 \(status) "))
            #expect(text.hasSuffix(#"{"error":"\#(code)"}"#))
        }
    }
}

@Suite struct WarmUp {
    @Test func silentWAVIsAValid16kHzMonoFile() {
        let wav = silentWAV(seconds: 1)
        #expect(wav.count == 44 + 32_000)
        #expect(sniffAudio(wav) == "wav")
        let le32 = { (at: Int) in wav[at..<at + 4].enumerated().reduce(0) { $0 | Int($1.element) << (8 * $1.offset) } }
        #expect(le32(4) == wav.count - 8)  // RIFF size
        #expect(le32(24) == 16_000)  // sample rate
        #expect(le32(40) == 32_000)  // data size
        #expect(wav[44...].allSatisfy { $0 == 0 })
    }
}

@Suite struct Resampling {
    @Test func sameRateOrNothingIsUnchanged() {
        #expect(resample([0.1, -0.2, 0.3], from: 24_000, to: 24_000) == [0.1, -0.2, 0.3])
        #expect(resample([], from: 44_100, to: 24_000).isEmpty)
    }

    @Test func theDurationIsKept() {
        #expect(resample([Float](repeating: 0, count: 44_100), from: 44_100, to: 24_000).count == 24_000)
        #expect(resample([Float](repeating: 0, count: 441), from: 44_100, to: 24_000).count == 240)
        #expect(resample([0], from: 44_100, to: 24_000).count == 1)
    }

    @Test func speechFrequenciesPassUnchanged() {
        // a 1 kHz tone at 44.1 kHz becomes the same tone at 24 kHz (away from the edges, where the filter runs out)
        let out = resample(sine(1000, rate: 44_100, count: 44_100), from: 44_100, to: 24_000)
        let expected = sine(1000, rate: 24_000, count: 24_000)
        #expect((200..<23_800).map { abs(out[$0] - expected[$0]) }.max()! < 0.002)
    }

    @Test func aToneAboveTheNewNyquistIsRemovedNotFolded() {
        // 15 kHz cannot exist at 24 kHz (Nyquist 12 kHz): unfiltered it would come back as a 9 kHz whistle
        let out = resample(sine(15_000, rate: 44_100, count: 44_100), from: 44_100, to: 24_000)[200..<23_800]
        #expect((out.map { $0 * $0 }.reduce(0, +) / Float(out.count)).squareRoot() < 0.003)
    }

    @Test func aSteadyLevelIsKept() {
        let out = resample([Float](repeating: 0.5, count: 4410), from: 44_100, to: 24_000)
        #expect(out[100..<2300].allSatisfy { abs($0 - 0.5) < 0.0001 })
    }
}

@Suite struct WAVFiles {
    @Test func monoSixteenBitLayoutAndClipping() {
        let wav = pcm16WAV([0, 0.5, -0.5, 1, -1, 2, -2, .nan, .infinity], sampleRate: 24_000)
        #expect(wav.count == 44 + 18 && sniffAudio(wav) == "wav")
        #expect(le(wav, 4, 4) == wav.count - 8)  // RIFF size
        #expect(le(wav, 20, 2) == 1 && le(wav, 22, 2) == 1)  // PCM, mono
        #expect(le(wav, 24, 4) == 24_000 && le(wav, 28, 4) == 48_000)  // sample rate, bytes a second
        #expect(le(wav, 32, 2) == 2 && le(wav, 34, 2) == 16)  // block size, bits
        #expect(le(wav, 40, 4) == 18)  // data size
        #expect(pcm(wav) == [0, 16384, -16384, 32767, -32767, 32767, -32767, 0, 0])
    }

    @Test func speechIs24kHzMonoAtItsOwnLevel() {
        // Supertonic-3's 44.1 kHz output as the bridge gets every voice: Kokoro's 24 kHz mono 16-bit. Not
        // peak-normalised: both engines speak at about -26 dBFS RMS, normalising made Supertonic 5-9 dB louder.
        let wav = SpeechAudio.wav(sine(440, rate: 44_100, count: 44_100, amplitude: 0.3), sampleRate: 44_100)
        #expect(le(wav, 22, 2) == 1 && le(wav, 24, 4) == 24_000 && le(wav, 34, 2) == 16)
        #expect(le(wav, 40, 4) == 48_000)
        #expect(abs(pcm(wav).map { abs(Int($0)) }.max()! - 9830) < 50)
    }

    @Test func silenceAndBrokenSamplesStaySilent() {
        let wav = SpeechAudio.wav([Float](repeating: 0, count: 440) + [.nan], sampleRate: 44_100)
        #expect(le(wav, 40, 4) == 480 && pcm(wav).allSatisfy { $0 == 0 })
    }
}

/// Counts loads; the test's loader decides what each one does.
private actor Loads {
    private(set) var count = 0
    func next() -> Int {
        count += 1
        return count
    }
}

private struct LoadFailed: Error {}

@Suite struct FirstUseLoading {
    @Test func concurrentFirstUsesShareOneLoad() async throws {
        let loads = Loads()
        let engine = LoadOnce<Int> {
            let n = await loads.next()
            try await Task.sleep(for: .milliseconds(100))  // the others arrive while it loads
            return n
        }
        let values = try await withThrowingTaskGroup(of: Int.self) { group in
            for _ in 0..<10 { group.addTask { try await engine.get() } }
            return try await group.reduce(into: []) { $0.append($1) }
        }
        #expect(values == [Int](repeating: 1, count: 10))
        #expect(await loads.count == 1)
    }

    @Test func aLoadedValueIsKept() async throws {
        let loads = Loads()
        let engine = LoadOnce<Int> { await loads.next() }
        #expect(try await engine.get() == 1)
        #expect(try await engine.get() == 1)
        #expect(await loads.count == 1)
    }

    @Test func aFailedLoadIsNotReadyAndTriedAgainByTheNextUse() async throws {
        let loads = Loads()
        let engine = LoadOnce<Int> {
            let n = await loads.next()
            if n == 1 { throw LoadFailed() }
            return n
        }
        // the bridge gets 503 not_ready, never the engine's own error
        await #expect(throws: VoiceError.notReady) { try await engine.get() }
        #expect(try await engine.get() == 2)
        #expect(try await engine.get() == 2)
        #expect(await loads.count == 2)
    }
}

@Suite struct TranscriptionRequests {
    private let wav = raw("RIFF") + Data(count: 4) + raw("WAVE")

    @Test func languageAndKind() throws {
        let request = try TranscriptionRequest(target: "/v1/audio/transcriptions?language=nl", body: wav)
        #expect(request.language == "nl" && request.kind == "wav")
        #expect(try TranscriptionRequest(target: "/v1/audio/transcriptions", body: wav).language == "en")
    }

    @Test func theLanguageIsCheckedBeforeTheAudio() {
        // a wrong language is 400 whatever the body holds: not audio, or too big
        for body in [raw("%PDF-1.7"), Data(), wav + Data(count: Limits.maxAudioBytes)] {
            #expect(throws: VoiceError.unsupportedLanguage) {
                try TranscriptionRequest(target: "/v1/audio/transcriptions?language=xx", body: body)
            }
        }
    }

    @Test func notAudioOrTooBig() {
        #expect(throws: VoiceError.notAudio) {
            try TranscriptionRequest(target: "/v1/audio/transcriptions?language=nl", body: raw("%PDF-1.7"))
        }
        #expect(throws: VoiceError.tooLong) {
            try TranscriptionRequest(target: "/v1/audio/transcriptions?language=nl", body: wav + Data(count: Limits.maxAudioBytes))
        }
    }
}
