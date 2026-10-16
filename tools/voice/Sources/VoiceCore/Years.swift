import Foundation

/// Years and decades as people say them (CLDR's %spellout-numbering-year, FluidAudio's decade rule): "in 1998" is "in
/// nineteen ninety eight", "1939-1945" "nineteen thirty nine to nineteen forty five", "the 1990s" "the nineteen
/// nineties", "'05" "oh five". A number is read as a year only in a year's context: after in, since, until, by, from, before, after or
/// around (and the second of "from 2020 to 2025"), after a month name, in a year range, alone in brackets ("(1997)") or with BC or AD. Anything else is a count
/// ("1998 people"; "in 1500 steps" is read as a year: accepted).
enum Years {
    static func spoken(_ text: String) -> String {
        var s = range.rewrite(text) { m in
            guard let start = Int(m[1]), let end = Int(m[2]), (1000...2099).contains(start) else { return nil }
            if m[2].count == 4, end > start, end <= 2099 { return Words.year(start) + " to " + Words.year(end) }
            if m[2].count == 2, end > start % 100 { return Words.year(start) + " to " + Words.cardinal(end) }
            return nil
        }
        s = fromTo.replace(s) { g in g[1] + " " + year(g[2]) + " " + g[3] + " " + year(g[4]) }
        s = afterWord.replace(s) { g in g[1] + " " + year(g[2]) }
        s = afterMonth.replace(s) { g in Dates.fullMonth(g[1]) + " " + year(g[2]) }  // "Oct 2026": "October …"
        s = inBrackets.replace(s) { g in "(" + year(g[1]) + ")" }
        s = eraAfter.replace(s) { g in year(g[1]) + " " + g[2] }
        s = eraBefore.replace(s) { g in g[1] + " " + year(g[2]) }
        s = shortYear.rewrite(s) { m in
            guard let n = Int(m[1]), n > 0 else { return nil }
            return n < 10 ? "oh " + Words.ones[n] : Words.cardinal(n)
        }
        let full = fullDecade.replace(s) { g in decade(g[1], tens: Int(g[2]) ?? 0) }
        return shortDecade.replace(full) { g in decadeNames[Int(g[1]) ?? 0] }
    }

    private static func year(_ digits: String) -> String {
        Int(digits).map(Words.year) ?? digits
    }

    private static let decadeNames = ["", "tens", "twenties", "thirties", "forties", "fifties", "sixties", "seventies",
                                      "eighties", "nineties"]

    /// "19", 9 -> "nineteen nineties"; "19", 0 -> "nineteen hundreds"; "20", 0 -> "two thousands"; "20", 1 -> "twenty tens".
    private static func decade(_ century: String, tens: Int) -> String {
        let hundreds = Words.number(century)
        if tens == 0 { return century == "20" ? "two thousands" : hundreds + " hundreds" }
        return hundreds + " " + decadeNames[tens]
    }

    private static let start = #"(?<![\p{L}\p{N}.,/–-])"#
    /// No more of a number or a word after a year.
    private static let end = #"(?![\p{L}\p{N}]|[.,][0-9])"#
    private static let fourDigits = "([12][0-9]{3})"

    /// "1939-1945", "1939–45", "2025-26": groups start, end (4 or 2 digits).
    private static let range = Pattern(start + fourDigits + #"\s*[-–]\s*([12][0-9]{3}|[0-9]{2})(?![\p{L}\p{N}]|[-–.,/][0-9])"#)
    /// "in 1998", "Since 2010": groups the word, the year.
    private static let afterWord = Pattern(#"(?<![\p{L}])(in|since|until|by|from|before|after|around)\s+"# + fourDigits + end,
                                           options: [.caseInsensitive])
    /// "from 2020 to 2025", "between 1990 and 2010": the second year has its context from the first. Groups the
    /// word, the year, the joining word, the year.
    private static let fromTo = Pattern(#"(?<![\p{L}])(from|between)\s+"# + fourDigits + #"\s+(to|and|until)\s+"#
                                        + fourDigits + end, options: [.caseInsensitive])
    /// "October 2026", "Oct 2026" (a short name before a year is a month's: "October twenty twenty six"): groups the
    /// month as written, the year.
    private static let afterMonth = Pattern(
        #"(?<![\p{L}])((?:January|February|March|April|May|June|July|August|September|October|November|December"#
            + #"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?)\s+"# + fourDigits + end)
    /// "(1997)": group the year.
    private static let inBrackets = Pattern(#"\("# + fourDigits + #"\)"#)
    /// "1066 AD", "500 BC": groups the year, the era.
    private static let eraAfter = Pattern(start + #"([0-9]{1,4})\s?(BC|AD|BCE|CE)(?![\p{L}\p{N}])"#)
    /// "AD 79": groups the era, the year.
    private static let eraBefore = Pattern(#"(?<![\p{L}\p{N}])(AD)\s?([0-9]{1,4})"# + end)

    /// "'05", "’69" (never in quotes: "'05'"): group the two digits.
    private static let shortYear = Pattern(#"(?<![\p{L}\p{N}])['’]([0-9]{2})(?![\p{L}\p{N}'’]|[.,][0-9])"#)
    /// Decades before the letter-digit space ("1990s" would be "nineteen ninety s").
    private static let fullDecade = Pattern(#"(?<![\p{L}\p{N}'’])(1[0-9]|20)([0-9])0s(?![\p{L}\p{N}])"#)
    private static let shortDecade = Pattern(#"(?<![\p{L}\p{N}])['’]?([2-9])0s(?![\p{L}\p{N}])"#)
}
