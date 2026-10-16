import Foundation

/// Versions and "x" glued to a number (Kokoro-FastAPI's English rules): "v0.9.1" is "version zero point nine point
/// one", a number with two dots or more (each part three digits or fewer) "zero point ten point two"; "x2" is "times
/// two", "2x" "two times", "4x4" "four by four". Never "0x…" (hexadecimal), a capital X ("BMW X2") or a name ("x86").
enum Versions {
    static func spoken(_ text: String) -> String {
        var s = version.replace(text) { g in
            let parts = (g[1].isEmpty ? g[3] : g[2]).split(separator: ".").map { Words.number(String($0)) }
            return (g[1].isEmpty ? "" : "version ") + parts.joined(separator: " point ")
        }
        s = by.rewrite(s) { m in m[1] == "0" ? nil : Words.number(m[1]) + " by " + Words.number(m[2]) }
        s = timesBefore.rewrite(s) { m in names.contains(m[0]) ? nil : "times " + Words.decimal(m[1], m[2]) }
        return timesAfter.rewrite(s) { m in m[1] == "0" && m[2].isEmpty ? nil : Words.decimal(m[1], m[2]) + " times" }
    }

    /// "v0.9.1", "v1.5", "0.10.2" (a "v", or two dots or more, each part up to three digits): groups v, its number,
    /// a number without one.
    private static let version = Pattern(
        #"(?<![\p{L}\p{N}.,])(?:(v)([0-9]{1,3}(?:\.[0-9]{1,3})+)|([0-9]{1,3}(?:\.[0-9]{1,3}){2,}))(?![\p{N}]|\.[0-9])"#)
    /// "4x4", "1920x1080": groups both numbers.
    private static let by = Pattern(#"(?<![\p{L}\p{N}.,])([0-9]+)x([0-9]+)(?![\p{L}\p{N}])"#)
    /// Names written as "x" and a number, never "times": processors ("x86", "x64") and video formats ("x264").
    private static let names: Set<String> = ["x86", "x64", "x32", "x264", "x265"]
    /// "x2", "x10": groups whole, decimals.
    private static let timesBefore = Pattern(#"(?<![\p{L}\p{N}])x([0-9]+)(?:\.([0-9]+))?(?![\p{L}\p{N}]|\.[0-9])"#)
    /// "2x", "1.5x": groups whole, decimals.
    private static let timesAfter = Pattern(#"(?<![\p{L}\p{N}.,])([0-9]+)(?:\.([0-9]+))?x(?![\p{L}\p{N}])"#)
}
