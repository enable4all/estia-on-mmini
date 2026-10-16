import Foundation

/// Bare ranges and fractions (NeMo's range and fraction): "5-10" is "five to ten", "3/4" "three quarters", "2 1/2"
/// "two and a half", "½" "one half". A range only when it goes up ("3-1", a score, stays); a fraction only when its
/// top is smaller than its bottom and the bottom is 10 or less, and never inside a slash date ("3/4/2026"). Two other
/// numbers with a slash between are said one after the other: "24/7" is "twenty four seven".
enum Ranges {
    static func spoken(_ text: String) -> String {
        var s = range.rewrite(text) { m in
            guard let low = Double(m[1].replacingOccurrences(of: ",", with: "") + (m[2].isEmpty ? "" : "." + m[2])),
                  let high = Double(m[3].replacingOccurrences(of: ",", with: "") + (m[4].isEmpty ? "" : "." + m[4])),
                  low < high else { return nil }
            return Words.decimal(m[1], m[2]) + " to " + Words.decimal(m[3], m[4])
        }
        s = fraction.rewrite(s) { m in
            guard let top = Int(m[2]), let bottom = Int(m[3]), top < bottom else { return nil }
            return said(whole: m[1], top, bottom)
        }
        s = vulgar.rewrite(s) { m in
            guard let (top, bottom) = vulgarFractions[m[2]] else { return nil }
            return said(whole: m[1], top, bottom)
        }
        return slash.replace(s) { g in Words.number(g[1]) + " " + Words.number(g[2]) }
    }

    /// "three quarters", "one half"; with a whole number "two and a half", "two and three quarters".
    private static func said(whole: String, _ top: Int, _ bottom: Int) -> String {
        let (one, many) = bottom == 2 ? ("half", "halves") : bottom == 4 ? ("quarter", "quarters")
            : (Words.ordinal(bottom), Words.ordinal(bottom) + "s")
        if whole.isEmpty { return Words.cardinal(top) + " " + (top == 1 ? one : many) }
        return Words.number(whole) + " and " + (top == 1 ? "a " + one : Words.cardinal(top) + " " + many)
    }

    private static let vulgarFractions: [String: (Int, Int)] = [
        "½": (1, 2), "⅓": (1, 3), "⅔": (2, 3), "¼": (1, 4), "¾": (3, 4), "⅕": (1, 5), "⅖": (2, 5), "⅗": (3, 5),
        "⅘": (4, 5), "⅙": (1, 6), "⅚": (5, 6), "⅐": (1, 7), "⅛": (1, 8), "⅜": (3, 8), "⅝": (5, 8), "⅞": (7, 8),
        "⅑": (1, 9), "⅒": (1, 10),
    ]

    private static let start = #"(?<![\p{L}\p{N}.,/–-])"#
    private static let number = "(" + NumberNormalizer.integer + #")(?:\.([0-9]+))?"#
    /// "5-10", "10–15", "5 – 10" (a hyphen only without spaces: "5 - 3" may be a sum): groups low (2), high (2).
    private static let range = Pattern(start + number + #"(?:-|\s*[–—]\s*)"# + number + #"(?![\p{N}]|[.,/–-][0-9])"#)
    /// "3/4", "2 1/2": groups whole, top, bottom.
    private static let fraction = Pattern(#"(?<![\p{L}\p{N}.,/])(?:([0-9]+)\s+)?([1-9])/([1-9]|10)(?![\p{N}]|[./][0-9])"#)
    /// "½", "2½", "2 ½": groups whole, the sign.
    private static let vulgar = Pattern(#"(?<![\p{L}\p{N}.,/])(?:([0-9]+)\s?)?([½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅐⅛⅜⅝⅞⅑⅒])"#)
    /// "24/7", "9/11", "50/50": groups both numbers.
    private static let slash = Pattern(#"(?<![\p{L}\p{N}.,/])([0-9]+)/([0-9]+)(?![\p{N}]|[./][0-9])"#)
}
