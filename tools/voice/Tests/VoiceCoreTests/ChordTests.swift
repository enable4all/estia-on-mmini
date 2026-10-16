import Foundation
import Testing
import VoiceCore

/// What Kokoro is given for an English text: the call's and the voice note's path (`SpeechRequest.spokenInput`).
private func spoken(_ text: String) throws -> String {
    let body = try JSONSerialization.data(withJSONObject: ["input": text])
    return try SpeechRequest(json: body).spokenInput
}

/// Owner, 16 Oct, on a call: "chord progression still chinese ... # and flats have words". A chord is said as music
/// ("E minor seven, C add nine, G over B"); a run of chords gets commas, Kokoro's only pause.
@Suite struct ChordSpelling {
    @Test(arguments: [
        ("C G7 Am F", "C, G seven, A minor, F"),
        ("Capo 2 Am F C G", "Capo two A minor, F, C, G"),
        ("E7, A7, B7", "E seven, A seven, B seven"),
        ("Em7 - Cadd9 - G/B - D/F#", "E minor seven, C add nine, G over B, D over F sharp"),
        ("Bb, Ebmaj7, F7sus4", "B flat, E flat major seven, F seven sus four"),
        ("C#m7b5 to F#7", "C sharp minor seven flat five to F sharp seven"),
        ("Bdim, Caug and G+", "B diminished, C augmented and G augmented"),
        ("Fm6, Abm, then Db/Ab", "F minor six, A flat minor, then D flat over A flat"),
        ("Verse: G D Em C", "Verse: G, D, E minor, C"),
        ("F# minor is the relative minor of A.", "F sharp minor is the relative minor of A."),
        ("Then Dsus2, Dsus4 and Cmaj7.", "Then D sus two, D sus four and C major seven."),
        ("The key is E♭ major, with a B♭7 and an F♯m at the end.",
         "The key is E flat major, with a B flat seven and an F sharp minor at the end."),
        ("Play Am7 | D7 | Gmaj7 | Cmaj7 | F#m7b5 | B7 | Em", "Play A minor seven, D seven, G major seven, C major seven, F sharp minor seven flat five, B seven, E minor"),
        ("Try C6/9 or G7#9.", "Try C six nine or G seven sharp nine."),
    ])
    func aChordIsSaidAsMusic(written: String, said: String) throws {
        #expect(try spoken(written) == said)
    }

    /// Ordinary words that look like chords stay as written (only numbers become words, as before).
    @Test(arguments: [
        ("Am I late?", "Am I late?"),
        ("Dm me later.", "Dm me later."),
        ("Run an A/B test.", "Run an A/B test."),
        ("Get an A+.", "Get an A+."),
        ("Choose A, B or C.", "Choose A, B or C."),
        ("Em, I mean, maybe.", "Em, I mean, maybe."),
        ("16 Gb free", "sixteen Gb free"),
        ("the bb gun", "the bb gun"),
        ("Amin said hi", "Amin said hi"),
        ("Bar 12 Am", "Bar twelve Am"),
        ("I like C# and F# for code", "I like C sharp and F sharp for code"),
        ("The CD and the DJ", "The CD and the DJ"),
        ("The Chorus begins", "The Chorus begins"),
    ])
    func whatIsNoChordStays(written: String, said: String) throws {
        #expect(try spoken(written) == said)
    }
}

/// The review's number fixes (16 Oct): a comma after a number stays (it is a pause), an ordinal is a word, and a
/// letter glued to a digit gets a space ("E7" was "Eseven", "2nd" was "twond").
@Suite struct NumbersInSpeech {
    @Test(arguments: [
        ("Capo 3, play", "Capo three, play"),
        ("2nd fret, 3rd string, 21st time, 12th bar, 101st try", "second fret, third string, twenty first time, twelfth bar, one hundred first try"),
        ("1,000 and 1,2,3", "one thousand and one,two,three"),
        ("G7 summit, B2 level, B12 and MP3", "G seven summit, B two level, B twelve and MP three"),
        ("5 cm, 5km", "five centimeters, five kilometers"),
        ("$1,500, 20%, then 3", "one thousand five hundred dollars, twenty percent, then three"),
        ("the 1990s, the '90s, the 80s, the 1900s, the 2000s and the 2010s",
         "the nineteen nineties, the nineties, the eighties, the nineteen hundreds, the two thousands and the twenty tens"),
    ])
    func numbersKeepTheirCommasAndOrdinalsAreWords(written: String, said: String) throws {
        #expect(try spoken(written) == said)
    }
}

