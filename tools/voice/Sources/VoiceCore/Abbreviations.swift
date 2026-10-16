import Foundation

/// Abbreviations said in full (NeMo's English list): "e.g." is "for example", "i.e." "that is", "vs." "versus",
/// "approx." "approximately", "No." before a digit "number" ("no." only glued to it), and "St." "Saint" before a
/// capitalised word ("Mount St. Helens", "Paris, St. Tropez"), "Street" after a street's name or number ("Main St.
/// is", "5th St. Bakery").
/// Anything else stays ("etc.", "No. That's it."). An abbreviation's dot at the end of the text also ends the
/// sentence, so it stays there as a full stop ("Main St." is "Main Street.").
enum Abbreviations {
    static func spoken(_ text: String) -> String {
        var s = example.rewrite(text) { m in
            let words = m[1].isEmpty ? (m[2] == "I" ? "That is" : "that is") : (m[1] == "E" ? "For example" : "for example")
            return words + fullStop(m)
        }
        s = versus.replace(s) { _ in "versus" }
        s = approximately.rewrite(s) { m in (m[1] == "A" ? "Approximately" : "approximately") + (m[2].isEmpty ? "" : fullStop(m)) }
        s = number.replace(s) { _ in "number " }
        return saint.rewrite(s) { m in
            let before = m.before.hasSuffix(" ") ? m.before.split(separator: " ").last.map(String.init) ?? "" : ""
            let next = m.after.hasPrefix(" ") ? String(m.after.dropFirst().prefix(while: \.isLetter)) : ""  // "I'll": "I"
            // a street's number ("5th St."), or its name: a capitalised word with nothing after it ("Main St.", never
            // "Paris, St.")
            let number = before.first?.isNumber == true
            let name = before.first?.isUppercase == true && before.last?.isLetter == true && !functionWords.contains(before)
            let capital = next.first?.isUppercase == true
            if capital, !sentenceStarts.contains(next) { return number ? "Street" : "Saint" }
            if number || name { return "Street" + (capital ? "." : fullStop(m)) }
            return nil
        }
    }

    /// "." when nothing but spaces follows the match: its dot also ended the sentence.
    private static func fullStop(_ m: Pattern.Match) -> String {
        m.after.allSatisfy(\.isWhitespace) ? "." : ""
    }

    /// Capitalised words that start a sentence or a place, never a street's name ("The St. is").
    private static let functionWords: Set<String> = ["The", "In", "On", "At", "To", "From", "Near", "By", "Of", "And",
                                                    "Or", "Visit", "See"]
    /// Capitalised words that start a sentence, never a saint's name: after "Main St. Then" the dot ended one.
    /// Its letters only: "It's", "I'll" count too.
    private static let sentenceStarts: Set<String> = ["The", "Then", "It", "I", "We", "You", "She", "He", "They",
                                                     "This", "That", "There", "A", "An", "And", "But", "So", "Or"]

    /// "e.g.", "E.g.", "i.e.", "I.e.": groups e or E, i or I.
    private static let example = Pattern(#"(?<![\p{L}\p{N}.])(?:([eE])\.g|([iI])\.e)\."#)
    private static let versus = Pattern(#"(?<![\p{L}\p{N}])vs\.?(?![\p{L}\p{N}])"#)
    /// "approx." or "approx": groups A or a, the dot.
    private static let approximately = Pattern(#"(?<![\p{L}\p{N}])([aA])pprox(\.)?(?![\p{L}\p{N}])"#)
    /// "No. 5", "No.5", "no.7": the number's own sign, only before a digit; a lower-case one only glued to it ("I said
    /// no. 5 people agreed" is the word).
    private static let number = Pattern(#"(?<![\p{L}\p{N}])(?:No\.\s?|no\.)(?=[0-9])"#)
    private static let saint = Pattern(#"(?<![\p{L}\p{N}])St\.(?![\p{L}\p{N}])"#)
}
