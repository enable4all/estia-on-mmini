// Adapted from OpenWhisperer (app/Sources/OpenWhispererKit/NumberNormalizer.swift) by the same author, who
// licenses it here under this repository's MIT licence.
import Foundation

/// Expands numerics to spoken-form words *before* g2p, sidestepping the TTS
/// engine's weak number handling (FluidAudio Kokoro mangles `$5.99` → "X X").
/// Pure, dependency-free, unit-tested in isolation.
public enum NumberNormalizer {

    /// Returns `text` with clock times / money / percent / decimals / integers expanded to words: every English pass
    /// but the chords (`SpokenEnglish`). Non-numeric text is returned unchanged.
    public static func normalize(_ text: String) -> String {
        SpokenEnglish.withoutChords(text)
    }

    private static let ordinalNumber = Pattern(#"(?<![\p{L}\p{N}])([0-9]+)(st|nd|rd|th)(?![\p{L}\p{N}])"#)  // "2nd": "second"
    private static let codeDigits = Pattern(#"(?<=\p{L})([0-9]{4,})(?![0-9])"#)
    private static let letterMeetsDigit = Pattern(#"(?<=\p{L})(?=[0-9])|(?<=[0-9])(?=\p{L})"#)  // "E7", "MP3", "5km": a space
    /// Whatever number is left: its minus (only after a space, the start or "("), a whole number (thousands with
    /// commas) and its decimals.
    private static let leftNumber = Pattern(Measures.sign + "(" + integer + #")(?:\.([0-9]+))?"#)

    /// "21st", "12th": an ordinal only when its ending fits the number (FluidAudio's English rule): "2th" and "1rd"
    /// are none, and the letter-digit space reads them apart.
    static func ordinals(_ text: String) -> String {
        ordinalNumber.rewrite(text) { m in
            guard let n = Int(m[1]), m[2] == Words.ordinalSuffix(n) else { return nil }
            return Words.ordinal(n)
        }
    }

    /// A code: letters glued to four digits or more are said digit by digit ("KL1234" is "KL one two three four",
    /// Kokoro-FastAPI's rule); else a space where a letter meets a digit ("E7", "MP3", "B12": a number).
    static func lettersAndDigits(_ text: String) -> String {
        let codes = codeDigits.replace(text) { g in " " + Words.digits(g[1]) }
        return letterMeetsDigit.replace(codes) { _ in " " }
    }

    static func decimalsAndCardinals(_ text: String) -> String {
        leftNumber.rewrite(text) { m in
            // a leading zero digit by digit ("A01" is "A zero one"), but not after a colon ("24:00" keeps "zero") or
            // in a slash date ("05/10/2026" is "five/ten/…")
            let dated = m.before.hasSuffix("/") || m.after.hasPrefix("/")
            if m[2].count > 1, m[2].first == "0", m[3].isEmpty, !m.before.hasSuffix(":"), !dated { return Words.digits(m[2]) }
            return Measures.signed(m[1], m[2], m[3])
        }
    }

    /// A whole number: digits, or thousands with commas ("1,500"); a comma after a number stays, a pause (review,
    /// 16 Oct: "E7, A7, B7" lost its pauses; "Capo 3, play" ran on).
    static let integer = #"[0-9]{1,3}(?:,[0-9]{3})+(?![0-9])|[0-9]+"#

    // MARK: - Clock times

    /// am/pm after a time, in one case only ("am", "AM", "a.m.", "A.M.", right after the digits too): a chord's "Am"
    /// is no time (review, 16 Oct: "C G7 Am F" came out "in the morning"). A dotted "a.m." keeps its last dot where it
    /// ends a sentence (at the end, or before a capital).
    private static let meridiem = #"(?:\s?(?=[ap]\.?m\b|[AP]\.?M\b)([AaPp])\.?[Mm]\b(?:\.(?!\s*$|\s+\p{Lu}))?)"#

    /// One time: H:MM or HH:MM (hour 0 to 23, minutes 00 to 59), never inside a longer number or with seconds, and an
    /// optional am/pm.
    private static let clock = #"(?<![0-9:.,])([01]?[0-9]|2[0-3]):([0-5][0-9])(?![0-9]|:[0-9])"# + meridiem + "?"

    /// An hour 1 to 12 with am/pm and no minutes ("7pm", "11 a.m.").
    private static let hourOnly = #"(?<![\p{L}0-9:.,])(1[0-2]|0?[1-9])(?![0-9:])"# + meridiem  // never glued to a letter: "G7"

    /// Either end of a range: a clock time or an hour alone, am/pm optional (a range checks what it needs).
    private static let rangeEnd = #"(?<![\p{L}0-9:.,])([01]?[0-9]|2[0-3])(?::([0-5][0-9]))?(?![0-9]|:[0-9])"# + meridiem + "?"

    /// Clock times in words, as people say them (owner, 16 Oct: "13:00" was read "one three zero zero"; "the 'am',
    /// 'pm'": "7pm" was "sevenpm"): the hour on the 12-hour clock with its part of the day, noon and midnight by name;
    /// an hour alone only with am/pm; a range ("14:00–15:00", "3-5pm") names a part they share once ("two to three in
    /// the afternoon").
    public static func timesOfDay(_ text: String) -> String {
        let ranges = timeRange.replace(text) { g in
            guard let (start, end) = range(g) else { return g[0] }
            let first = start.part != nil && start.part == end.part ? start.words : start.spoken
            return first + " to " + end.spoken
        }
        let clocks = clockTime.replace(ranges) { g in time(g[1], g[2], g[3]).spoken }
        return hourAlone.replace(clocks) { g in time(g[1], "00", g[2]).spoken }
    }

    private static let timeRange = Pattern(rangeEnd + #"(?:\s*[-–—]\s*|\s+to\s+)"# + rangeEnd)
    private static let clockTime = Pattern(clock)
    private static let hourAlone = Pattern(hourOnly)

    /// A range's two times, or nil when it is no time range ("1-2 days": an hour alone needs am/pm). The first hour
    /// without am/pm takes the second's ("3-5pm"), or the other half of the day when it is later ("11-1pm" is eleven
    /// in the morning).
    private static func range(_ g: [String]) -> (Spoken, Spoken)? {
        let (startHour, startMinutes, endHour, endMinutes) = (Int(g[1]) ?? 0, g[2], Int(g[4]) ?? 0, g[5])
        var startMeridiem = g[3]
        let endMeridiem = g[6]
        if startMeridiem.isEmpty, !endMeridiem.isEmpty, (1...12).contains(startHour) {
            let sameHalf = startHour % 12 <= endHour % 12
            startMeridiem = sameHalf ? endMeridiem : (endMeridiem.lowercased() == "p" ? "a" : "p")
        }
        if (startMinutes.isEmpty && startMeridiem.isEmpty) || (endMinutes.isEmpty && endMeridiem.isEmpty) { return nil }
        return (time(g[1], startMinutes.isEmpty ? "00" : startMinutes, startMeridiem),
                time(g[4], endMinutes.isEmpty ? "00" : endMinutes, endMeridiem))
    }

    private typealias Spoken = (words: String, part: String?, spoken: String)

    /// A time's words and its part of the day (none for noon and midnight). With am/pm the hour is read on the
    /// 12-hour clock (1 to 12; another hour keeps its 24-hour reading).
    private static func time(_ hour: String, _ minutes: String, _ meridiem: String) -> Spoken {
        var h = Int(hour) ?? 0
        let m = Int(minutes) ?? 0
        if !meridiem.isEmpty, (1...12).contains(h) { h = h % 12 + (meridiem.lowercased() == "p" ? 12 : 0) }
        if m == 0, h == 0 || h == 12 { let name = h == 0 ? "midnight" : "noon"; return (name, nil, name) }
        let words = ones[h % 12 == 0 ? 12 : h % 12] + (m == 0 ? "" : m < 10 ? " oh " + ones[m] : " " + cardinal(m))
        let part = h < 5 ? "at night" : h < 12 ? "in the morning" : h < 18 ? "in the afternoon" : h < 22 ? "in the evening" : "at night"
        return (words, part, words + " " + part)
    }

    private static let ones = Words.ones
    private static func cardinal(_ n: Int) -> String { Words.cardinal(n) }
}
