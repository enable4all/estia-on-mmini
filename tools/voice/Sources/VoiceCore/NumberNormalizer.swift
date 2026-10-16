// Adapted from OpenWhisperer (app/Sources/OpenWhispererKit/NumberNormalizer.swift) by the same author, who
// licenses it here under this repository's MIT licence.
import Foundation

/// Expands numerics to spoken-form words *before* g2p, sidestepping the TTS
/// engine's weak number handling (FluidAudio Kokoro mangles `$5.99` → "X X").
/// Pure, dependency-free, unit-tested in isolation.
public enum NumberNormalizer {

    /// Returns `text` with clock times / money / percent / decimals / integers expanded to words.
    /// Non-numeric text is returned unchanged.
    public static func normalize(_ text: String) -> String {
        // Order matters: times first (they own the `:`), then money (it owns the `$` + decimal), then percent,
        // then bare decimals, then any remaining standalone integers.
        var s = timesOfDay(text)
        s = replace(s, #"\$([0-9][0-9,]*)(?:\.([0-9]{2}))?"#) { g in
            let dollars = intOf(g[1])
            let cents = g.count > 2 ? intOf(g[2]) ?? 0 : 0
            var out = spoken(g[1]) + " dollar" + (dollars == 1 ? "" : "s")
            if cents > 0 { out += " and " + cardinal(cents) + " cent" + (cents == 1 ? "" : "s") }
            return out
        }
        s = replace(s, #"([0-9][0-9,]*)%"#) { g in spoken(g[1]) + " percent" }
        s = replace(s, #"([0-9]+)\.([0-9]+)"#) { g in
            let frac = g[2].compactMap { Int(String($0)) }.map { ones[$0] }.joined(separator: " ")
            return spoken(g[1]) + " point " + frac
        }
        s = replace(s, #"[0-9][0-9,]*"#) { g in spoken(g[0]) }
        return s
    }

    // MARK: - Clock times

    /// am/pm after a time ("PM", "p.m.", "am", "pm" right after the digits). A dotted "a.m." keeps its last dot where
    /// it ends a sentence (at the end, or before a capital).
    private static let meridiem = #"(?:\s?([AaPp])\.?[Mm]\b(?:\.(?!\s*$|\s+\p{Lu}))?)"#

    /// One time: H:MM or HH:MM (hour 0 to 23, minutes 00 to 59), never inside a longer number or with seconds, and an
    /// optional am/pm.
    private static let clock = #"(?<![0-9:.,])([01]?[0-9]|2[0-3]):([0-5][0-9])(?![0-9]|:[0-9])"# + meridiem + "?"

    /// An hour 1 to 12 with am/pm and no minutes ("7pm", "11 a.m.").
    private static let hourOnly = #"(?<![0-9:.,])(1[0-2]|0?[1-9])(?![0-9:])"# + meridiem

    /// Either end of a range: a clock time or an hour alone, am/pm optional (a range checks what it needs).
    private static let rangeEnd = #"(?<![0-9:.,])([01]?[0-9]|2[0-3])(?::([0-5][0-9]))?(?![0-9]|:[0-9])"# + meridiem + "?"

    /// Clock times in words, as people say them (owner, 16 Oct: "13:00" was read "one three zero zero"; "the 'am',
    /// 'pm'": "7pm" was "sevenpm"): the hour on the 12-hour clock with its part of the day, noon and midnight by name;
    /// an hour alone only with am/pm; a range ("14:00–15:00", "3-5pm") names a part they share once ("two to three in
    /// the afternoon").
    public static func timesOfDay(_ text: String) -> String {
        let ranges = replace(text, rangeEnd + #"(?:\s*[-–—]\s*|\s+to\s+)"# + rangeEnd) { g in
            guard let (start, end) = range(g) else { return g[0] }
            let first = start.part != nil && start.part == end.part ? start.words : start.spoken
            return first + " to " + end.spoken
        }
        let clocks = replace(ranges, clock) { g in time(g[1], g[2], g[3]).spoken }
        return replace(clocks, hourOnly) { g in time(g[1], "00", g[2]).spoken }
    }

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

    // MARK: - Number words

    private static let ones = [
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
        "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
        "seventeen", "eighteen", "nineteen",
    ]
    private static let tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

    private static func cardinal(_ n: Int) -> String {
        if n < 0 { return "minus " + cardinal(-n) }
        if n < 20 { return ones[n] }
        if n < 100 { let o = n % 10; return o == 0 ? tens[n / 10] : tens[n / 10] + " " + ones[o] }
        if n < 1000 { let r = n % 100; return r == 0 ? ones[n / 100] + " hundred" : ones[n / 100] + " hundred " + cardinal(r) }
        if n < 1_000_000 { let r = n % 1000; return r == 0 ? cardinal(n / 1000) + " thousand" : cardinal(n / 1000) + " thousand " + cardinal(r) }
        return String(n)  // beyond range: leave as digits
    }

    /// A digit run in words, or as written when it is too large for Int (19 digits or more): never "zero".
    private static func spoken(_ s: String) -> String {
        intOf(s).map(cardinal) ?? s
    }

    private static func intOf(_ s: String) -> Int? {
        Int(s.replacingOccurrences(of: ",", with: ""))
    }

    /// Regex replace where the replacement is computed from the match's capture groups
    /// (`g[0]` = whole match). Uses Foundation so it builds under Command Line Tools.
    private static func replace(_ s: String, _ pattern: String, _ transform: ([String]) -> String) -> String {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return s }
        let ns = s as NSString
        var out = ""
        var last = 0
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            var groups: [String] = []
            for i in 0..<m.numberOfRanges {
                let r = m.range(at: i)
                groups.append(r.location == NSNotFound ? "" : ns.substring(with: r))
            }
            out += transform(groups)
            last = m.range.location + m.range.length
        }
        out += ns.substring(with: NSRange(location: last, length: ns.length - last))
        return out
    }
}
