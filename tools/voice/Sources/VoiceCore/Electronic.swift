import Foundation

/// Mail addresses and bare domains said as people say them (NeMo's electronic): "info@example.com" is "info at example
/// dot com", "example.com" "example dot com". A domain ends in two or more lower-case letters and starts with a letter,
/// so "e.g.", "U.S.", "a.m.", "macOS 15.6" and "1.2.beta" are none. A part Kokoro would sound out as a word is written
/// in capitals, so its letters are said: two letters ("uk", "nl"; "co" stays) or no vowel ("www", "js"). Known gap:
/// two letters that are a word in Misaki's lexicon stay that word in capitals too ("IT" is an entry read "it"; "AT",
/// "IN", "ME", "BE", "IS", "NO", "TO" fall back to the word), so "example.it" is "example dot it". Links are dropped
/// before the voice service, as before.
enum Electronic {
    static func spoken(_ text: String) -> String {
        let mail = address.replace(text) { g in symbols(g[1]) + " at " + domainWords(g[2]) }
        return domain.replace(mail) { g in domainWords(g[1]) }
    }

    private static func domainWords(_ domain: String) -> String {
        let labels = domain.split(separator: ".").map { label -> String in
            let letters = label.allSatisfy { $0.isASCII && $0.isLetter }
            let spelled = letters && ((label.count == 2 && label.lowercased() != "co")
                || (label.count <= 5 && !label.lowercased().contains { "aeiouy".contains($0) }))
            return spelled ? label.uppercased() : String(label)
        }
        return symbols(labels.joined(separator: "."))
    }

    /// "." "dot", "_" "underscore", "-" "dash", "+" "plus", "%" "percent", each between spaces.
    private static func symbols(_ text: String) -> String {
        let names: [Character: String] = [".": "dot", "_": "underscore", "-": "dash", "+": "plus", "%": "percent"]
        var out = ""
        for char in text {
            if let name = names[char] { out += " " + name + " " } else { out.append(char) }
        }
        return out.replacingOccurrences(of: "  ", with: " ").trimmingCharacters(in: .whitespaces)
    }

    private static let label = #"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"#
    /// A domain: labels, the first starting with a letter, and a top level of two or more lower-case letters.
    private static let domainPart = #"[A-Za-z](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\."# + label + #")*\.[a-z]{2,24}"#
    /// After an address or a domain: no more of a word or a domain ("example.com." keeps its full stop).
    private static let after = #"(?![\p{L}\p{N}@]|\.[\p{L}\p{N}])"#
    /// "info@example.com": groups the name, the domain.
    private static let address = Pattern(#"(?<![\p{L}\p{N}._%+-])([A-Za-z0-9._%+-]+)@("# + domainPart + ")" + after)
    /// "example.com", "www.example.co.uk": group the domain.
    private static let domain = Pattern(#"(?<![\p{L}\p{N}._@/-])("# + domainPart + ")" + after)
}
