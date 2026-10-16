import Foundation

/// Phone numbers and numbers with a leading zero, digit by digit (NeMo's telephone): "+44 20 7946 0958" is "plus four
/// four, two zero, seven nine four six, zero nine five eight", "0800 123 456" "zero eight zero zero, one two three,
/// four five six", "(555) 123-4567" "five five five, one two three, four five six seven", "007" "zero zero seven". A
/// comma between the groups (Kokoro's pause); a number written without groups is said in threes. "zero", never "oh"
/// (American).
enum Phones {
    static func spoken(_ text: String) -> String {
        let plus = international.rewrite(text) { m in
            let groups = m[1].split(whereSeparator: { $0 == " " || $0 == "-" }).map(String.init)
            guard (7...15).contains(groups.joined().count) else { return nil }
            return "plus " + said(groups)
        }
        let american = northAmerican.replace(plus) { g in said([g[1].isEmpty ? g[2] : g[1], g[3], g[4]]) }
        return leadingZero.rewrite(american) { m in
            let groups = m[1].split(whereSeparator: { $0 == " " || $0 == "-" }).map(String.init)
            // groups only at a phone's length: "09-17" is a range of hours
            guard groups.count == 1 || groups.joined().count >= 7 else { return nil }
            return said(groups)
        }
    }

    /// The groups digit by digit, a comma between them; one long group in threes, the last two to four digits together
    /// ("0800123456" is 3, 3 and 4; "+306900000000" four threes).
    private static func said(_ groups: [String]) -> String {
        var parts = groups
        if groups.count == 1, groups[0].count > 4 {
            var rest = Substring(groups[0])
            parts = []
            while rest.count > 4 {
                parts.append(String(rest.prefix(3)))
                rest = rest.dropFirst(3)
            }
            parts.append(String(rest))
        }
        return parts.map(Words.digits).joined(separator: ", ")
    }

    /// "+44 20 7946 0958", "+1-555-0100", "+306900000000": group the digits with their spaces or dashes (7 to 15
    /// digits; checked in code).
    private static let international = Pattern(
        #"(?<![\p{L}\p{N}+])\+([0-9]{1,3}(?:[ -][0-9]{2,4}){1,5}|[0-9]{7,15})(?![\p{N}]|[.,][0-9])"#)
    /// "(555) 123-4567", "555-123-4567" (a range never has two dashes, nor brackets before it): groups the area code
    /// in brackets, without, the exchange, the line.
    private static let northAmerican = Pattern(
        #"(?<![\p{L}\p{N}.,/–-])(?:\(([0-9]{3})\)\s?|([0-9]{3})-)([0-9]{3})-([0-9]{4})(?![\p{N}]|[.,/–-][0-9])"#)
    /// "0800 123 456", "007", "05": a leading zero and at least one more digit, with its groups (7 digits or more:
    /// checked in code); never a decimal's digits, a time's, a slash date's or a short year's ("'05").
    private static let leadingZero = Pattern(
        #"(?<![\p{L}\p{N}.,:+/–'’-])(0[0-9]+(?:[ -][0-9]{2,4})*)(?![\p{N}]|[.,:/][0-9])"#)
}
