import Foundation

/// Signs said as words (misaki's English symbols): "&" is "and", "×" "times", "±" "plus or minus"; "+" only before
/// a digit ("2+2" "two plus two"; "C++", "A+" stay), "=" only next to a digit ("x = 7"; "a = b" stays), "#" only before
/// a digit ("#1" "number one"; "#tag" stays). Runs before the numbers are read: its rules look at the digits.
enum Symbols {
    static func spoken(_ text: String) -> String {
        var s = and.rewrite(text) { m in m.range.location == 0 ? "and " : " and " }
        s = times.replace(s) { _ in " times " }
        s = plusOrMinus.replace(s) { _ in "plus or minus " }
        s = plus.replace(s) { _ in " plus " }
        s = equals.rewrite(s) { m in
            let digitBefore = m.before.last?.isNumber == true
            let digitAfter = m.after.first?.isNumber == true || m.after.first == "-"
            return digitBefore || digitAfter ? " equals " : nil
        }
        return number.replace(s) { _ in "number " }
    }

    private static let and = Pattern(#"\s*&\s*"#)
    private static let times = Pattern(#"\s*×\s*"#)
    private static let plusOrMinus = Pattern(#"±\s?"#)
    /// Between a digit and a digit ("2+2", "2 + 3"), spaces and all.
    private static let plus = Pattern(#"(?<=[0-9])\s*\+\s*(?=[0-9])"#)
    /// "=" with its spaces; checked for a digit on either side.
    private static let equals = Pattern(#"\s*=\s*"#)
    private static let number = Pattern(#"(?<![\p{L}\p{N}&#])#(?=[0-9])"#)
}
