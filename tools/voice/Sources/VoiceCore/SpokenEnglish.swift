import Foundation

/// English text as Kokoro is to read it (Kokoro only: the other ten languages keep their digits). One list of passes
/// in one run order, the 0.9.2 plan's; a pass that runs early owns its text (times take their colon before any number
/// is read, a chord its digits). Every pattern is compiled once: 14 passes, about 55 patterns, run on every sentence
/// of a call.
public enum SpokenEnglish {
    struct Pass {
        let name: String
        let run: (String) -> String
    }

    static let passes: [Pass] = [
        Pass(name: "chords", run: ChordSpeller.spell),
        Pass(name: "abbreviations", run: Abbreviations.spoken),
        Pass(name: "emails and domains", run: Electronic.spoken),
        Pass(name: "times", run: NumberNormalizer.timesOfDay),
        Pass(name: "dates", run: Dates.spoken),
        Pass(name: "phones and leading zeros", run: Phones.spoken),
        Pass(name: "measures", run: Measures.spoken),
        Pass(name: "years and decades", run: Years.spoken),
        Pass(name: "bare ranges and fractions", run: Ranges.spoken),
        Pass(name: "versions", run: Versions.spoken),
        Pass(name: "ordinals", run: NumberNormalizer.ordinals),
        Pass(name: "letters and digits", run: NumberNormalizer.lettersAndDigits),
        // before the numbers are read (the plan has it last): "+" and "#" are words only before a digit
        Pass(name: "symbols", run: Symbols.spoken),
        Pass(name: "decimals and cardinals", run: NumberNormalizer.decimalsAndCardinals),
    ]

    /// The passes' names in their run order.
    public static var order: [String] { passes.map(\.name) }

    /// `text` as Kokoro is to read it.
    public static func spoken(_ text: String) -> String {
        passes.reduce(text) { text, pass in pass.run(text) }
    }

    /// Every pass but the chords (`NumberNormalizer.normalize`).
    static func withoutChords(_ text: String) -> String {
        passes.dropFirst().reduce(text) { text, pass in pass.run(text) }
    }
}

/// A pattern compiled once (keep it in a `static let`), with replacements computed from each match.
struct Pattern: @unchecked Sendable {  // NSRegularExpression is immutable and safe to share
    let regex: NSRegularExpression

    init(_ pattern: String, options: NSRegularExpression.Options = []) {
        // The patterns are constants: one that does not compile is a programming error, caught by any test.
        regex = try! NSRegularExpression(pattern: pattern, options: options)
    }

    /// One match: its groups (`[0]` the whole match, "" for a group that took no part) and where it stands.
    struct Match {
        let groups: [String]
        let range: NSRange
        let text: NSString

        subscript(_ index: Int) -> String { groups[index] }
        /// The text before the match.
        var before: String { text.substring(to: range.location) }
        /// The text after the match.
        var after: String { text.substring(from: NSMaxRange(range)) }
    }

    /// `text` with every match replaced by `transform`'s words; nil keeps the match as written.
    func rewrite(_ text: String, _ transform: (Match) -> String?) -> String {
        let ns = text as NSString
        var out = ""
        var last = 0
        for found in regex.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            var groups: [String] = []
            for index in 0..<found.numberOfRanges {
                let range = found.range(at: index)
                groups.append(range.location == NSNotFound ? "" : ns.substring(with: range))
            }
            let match = Match(groups: groups, range: found.range, text: ns)
            out += ns.substring(with: NSRange(location: last, length: found.range.location - last))
            out += transform(match) ?? groups[0]
            last = NSMaxRange(found.range)
        }
        return out + ns.substring(from: last)
    }

    /// `text` with every match replaced by `transform`'s words, from the capture groups alone.
    func replace(_ text: String, _ transform: ([String]) -> String) -> String {
        rewrite(text) { transform($0.groups) }
    }

    func matches(_ text: String) -> Bool {
        regex.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)) != nil
    }
}

/// Numbers in words, American English (CLDR's en spell-out rules, with spaces where they have hyphens: Kokoro reads
/// "twenty one" well).
enum Words {
    static let ones = [
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
        "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
        "seventeen", "eighteen", "nineteen",
    ]
    static let tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

    private static let scales: [(size: Int, name: String)] = [
        (1_000_000_000_000, "trillion"), (1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand"),
    ]

    /// The largest number said in words: 999 trillion and so on (beyond it, digits as written).
    static let largest = 999_999_999_999_999

    /// `n` in words, up to the trillions (`largest`); a larger number as digits.
    static func cardinal(_ n: Int) -> String {
        if n < 0 { return n == Int.min ? String(n) : "minus " + cardinal(-n) }
        if n > largest { return String(n) }
        if n < 20 { return ones[n] }
        if n < 100 { let o = n % 10; return o == 0 ? tens[n / 10] : tens[n / 10] + " " + ones[o] }
        if n < 1000 { let r = n % 100; return ones[n / 100] + " hundred" + (r == 0 ? "" : " " + cardinal(r)) }
        let scale = scales.first { n >= $0.size }!
        let (count, rest) = n.quotientAndRemainder(dividingBy: scale.size)
        return cardinal(count) + " " + scale.name + (rest == 0 ? "" : " " + cardinal(rest))
    }

    /// A digit run, thousands with commas allowed ("1,500"), in words; too large for words: as written, never "zero".
    static func number(_ digits: String) -> String {
        guard let n = Int(digits.replacingOccurrences(of: ",", with: "")), n <= largest else { return digits }
        return cardinal(n)
    }

    /// Each digit by its name: "0958" is "zero nine five eight".
    static func digits(_ digits: String) -> String {
        digits.compactMap { $0.wholeNumberValue }.map { ones[$0] }.joined(separator: " ")
    }

    /// A number with an optional decimal part: "3.50" is "three point five zero" (the decimals digit by digit).
    static func decimal(_ whole: String, _ fraction: String) -> String {
        fraction.isEmpty ? number(whole) : number(whole) + " point " + digits(fraction)
    }

    /// "2" -> "second", "21" -> "twenty first", "12" -> "twelfth"; too large: as written.
    static func ordinal(_ n: Int) -> String {
        guard n >= 0, n <= largest else { return String(n) }
        var words = cardinal(n).components(separatedBy: " ")
        let last = words.removeLast()
        let irregular = ["one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
                         "nine": "ninth", "twelve": "twelfth"]
        let spoken = irregular[last] ?? (last.hasSuffix("y") ? String(last.dropLast()) + "ieth" : last + "th")
        return (words + [spoken]).joined(separator: " ")
    }

    /// The ending an ordinal of `n` takes: "st" for 1, 21, 101; "nd", "rd"; "th" for 11, 12, 13 and the rest.
    static func ordinalSuffix(_ n: Int) -> String {
        if (11...13).contains(n % 100) { return "th" }
        switch n % 10 {
        case 1: return "st"
        case 2: return "nd"
        case 3: return "rd"
        default: return "th"
        }
    }

    /// A year as people say it (CLDR's en %spellout-numbering-year): 1998 "nineteen ninety eight", 1905 "nineteen oh
    /// five", 1900 "nineteen hundred", 2026 "twenty twenty six"; 2000 to 2009 and anything under 1010 as a number
    /// ("two thousand five"), and so 10,000 and over.
    static func year(_ n: Int) -> String {
        guard (1000..<10_000).contains(n), n % 1000 >= 10 else { return cardinal(n) }
        let (hundreds, rest) = n.quotientAndRemainder(dividingBy: 100)
        let tail = rest == 0 ? "hundred" : rest < 10 ? "oh " + ones[rest] : cardinal(rest)
        return cardinal(hundreds) + " " + tail
    }
}
