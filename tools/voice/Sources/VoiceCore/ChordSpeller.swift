import Foundation

/// Chord names said as music before Kokoro reads them (owner, 16 Oct, on a call: "chord progression still chinese ...
/// # and flats have words"; the rules are the review's): "Em7" is "E minor seven", "D/F#" "D over F sharp",
/// "Bb" "B flat". A chord that could be a word ("Am", "Ab", "A+", "A/B") is spelled only among other chords or in a
/// sentence about music; "Am I", lower case and words that only start with A to G stay as written. A run of chords
/// gets a comma between them (punctuation is Kokoro's only pause). Numbers are left to `NumberNormalizer`.
public enum ChordSpeller {

    /// Between the chords of a run: a comma, Kokoro's pause (owner, 16 Oct: "just use comma... for chords"; measured
    /// 0.12-0.24 s; a full stop gives 0.21-0.33 s, so a longer pause would need silence put in by the service).
    static let separator = ", "

    public static func spell(_ text: String) -> String {
        let ns = text as NSString
        let full = NSRange(location: 0, length: ns.length)
        var tokens: [Token] = []
        for match in candidate.matches(in: text, range: full) {
            let word = ns.substring(with: match.range)
            guard let chord = parse(word) else { continue }
            if chord.kind == .ambiguousMinor, followedByPronoun(ns, after: match.range) { continue }  // "Am I late?"
            if chord.kind == .letter, word == "A", startsSentence(ns, at: match.range.location) { continue }  // article
            tokens.append(Token(range: match.range, chord: chord))
        }
        guard !tokens.isEmpty else { return text }
        // runs: chords joined only by separators or and/then/to/or
        var run = [Int](repeating: -1, count: tokens.count)
        var gapIsSymbol = [Bool](repeating: false, count: tokens.count)  // the gap before token i, within a run
        var current = -1
        for i in tokens.indices {
            if i > 0 {
                let gapRange = NSRange(location: NSMaxRange(tokens[i - 1].range),
                                       length: tokens[i].range.location - NSMaxRange(tokens[i - 1].range))
                let gap = ns.substring(with: gapRange)
                if matches(symbolGap, gap) || matches(wordGap, gap) {
                    if current < 0 { current = i - 1; run[i - 1] = current }
                    run[i] = current
                    gapIsSymbol[i] = matches(symbolGap, gap)
                    continue
                }
            }
            current = -1
        }
        var out = ""
        var last = 0
        for (i, token) in tokens.enumerated() {
            let sentence = sentenceRange(ns, around: token.range.location)
            let inRun = run[i] >= 0
            let musical = inRun || mentionsMusic(ns.substring(with: sentence))
                || tokens.contains { $0.chord.kind == .strong && NSIntersectionRange($0.range, sentence).length > 0 }
            let start = token.range.location
            if inRun, i > 0, run[i - 1] == run[i], gapIsSymbol[i] {
                out += separator  // the gap after the chord before, said as a pause
            } else {
                out += ns.substring(with: NSRange(location: last, length: start - last))
            }
            let spelled = token.chord.kind == .strong || (token.chord.kind.isAmbiguous && musical)
            out += spelled ? token.chord.spoken : ns.substring(with: token.range)
            last = NSMaxRange(token.range)
        }
        out += ns.substring(from: last)
        return out
    }

    // MARK: - Finding chords

    private struct Token {
        let range: NSRange
        let chord: Chord
    }

    struct Chord: Equatable {
        enum Kind: Equatable {
            case letter  // "C": as written
            case digits  // "E7": as written, the digits are numbers
            case ambiguousMinor  // "Am", "Amin": also a word
            case ambiguous  // "Ab", "A+", "A/B": also a word or a grade
            case strong  // "Em7", "Bb", "C#", "D/F#": a chord wherever it stands

            var isAmbiguous: Bool { self == .ambiguousMinor || self == .ambiguous }
        }
        let spoken: String
        let kind: Kind
    }

    /// A word that starts with A to G and holds only what a chord may hold, not inside another word.
    private static let candidate = try! NSRegularExpression(
        pattern: #"(?<![\p{L}\p{N}#♯♭+°øΔ/])[A-G][A-Za-z0-9#♯♭+°øΔ]*(?:/(?:[A-G][#♯♭b]?|9))?(?![\p{L}\p{N}])"#)
    /// Between two chords of a run: spaces, a comma, a dash, a bar or an arrow.
    private static let symbolGap = try! NSRegularExpression(pattern: #"^\s*(?:,|-|–|—|\||→|->)?\s*$"#)
    /// Or a joining word, kept as said.
    private static let wordGap = try! NSRegularExpression(pattern: #"^\s*,?\s+(?:and|then|to|or)\s+$"#)
    private static let musicWords = try! NSRegularExpression(
        pattern: #"\b(?:chords?|keys?|capo|frets?|barre|progressions?|strum\w*|verses?|chorus(?:es)?|bridges?|intro|outro|riffs?|tabs?|guitar|ukulele|piano|songs?)\b"#,
        options: [.caseInsensitive])
    private static let pronouns: Set<String> = ["I", "you", "we", "they", "he", "she", "it", "not", "me", "us", "him", "her", "them"]

    private static func matches(_ re: NSRegularExpression, _ text: String) -> Bool {
        re.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)) != nil
    }

    private static func mentionsMusic(_ sentence: String) -> Bool { matches(musicWords, sentence) }

    private static func followedByPronoun(_ ns: NSString, after range: NSRange) -> Bool {
        let rest = ns.substring(from: NSMaxRange(range))
        guard rest.first == " " else { return false }
        let word = rest.drop { $0 == " " }.prefix { $0.isLetter }
        return pronouns.contains(String(word)) || pronouns.contains(String(word).lowercased())
    }

    private static func startsSentence(_ ns: NSString, at location: Int) -> Bool {
        let before = ns.substring(to: location).trimmingCharacters(in: .whitespaces)
        return before.isEmpty || before.hasSuffix(".") || before.hasSuffix("!") || before.hasSuffix("?")
    }

    private static let boundary = try! NSRegularExpression(pattern: #"[.!?](?:\s|$)"#)

    /// The sentence around `location`: from after the last ".", "!" or "?" followed by a space, to the next.
    private static func sentenceRange(_ ns: NSString, around location: Int) -> NSRange {
        let text = ns as String
        var start = 0
        var end = ns.length
        for match in boundary.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            if NSMaxRange(match.range) <= location { start = NSMaxRange(match.range) } else { end = match.range.location; break }
        }
        return NSRange(location: start, length: max(0, end - start))
    }

    // MARK: - Reading one chord

    private static let numberWords = ["2": "two", "4": "four", "5": "five", "6": "six", "7": "seven", "9": "nine",
                                      "11": "eleven", "13": "thirteen"]

    /// A chord's spoken words and how sure it is a chord, or nil when the word is no chord ("Chorus", "CD").
    static func parse(_ word: String) -> Chord? {
        let chars = Array(word)
        var i = 0
        func next(_ s: String) -> Bool {
            let w = Array(s)
            guard i + w.count <= chars.count, Array(chars[i..<(i + w.count)]) == w else { return false }
            i += w.count
            return true
        }
        func number(_ allowed: [String]) -> String? {
            for n in allowed.sorted(by: { $0.count > $1.count }) where next(n) {
                if i < chars.count, chars[i].isNumber { i -= n.count; continue }  // "71" is no "7"
                return numberWords[n]
            }
            return nil
        }
        guard let rootChar = chars.first, "ABCDEFG".contains(rootChar) else { return nil }
        let root = String(rootChar)
        i = 1
        var words = [root]
        var accidental = ""
        if next("#") || next("♯") { accidental = "#"; words.append("sharp") }
        else if next("♭") { accidental = "♭"; words.append("flat") }
        else if next("b") { accidental = "b"; words.append("flat") }
        var quality = ""
        if next("maj") { quality = "maj"; words.append("major") }
        else if next("min") { quality = "min"; words.append("minor") }
        else if next("m") { quality = "m"; words.append("minor") }
        else if i < chars.count, chars[i] == "M", i + 1 < chars.count, chars[i + 1].isNumber, next("M") {
            quality = "M"; words.append("major")
        }
        else if next("dim") || next("°") { quality = "dim"; words.append("diminished") }
        else if next("aug") || next("+") { quality = chars[i - 1] == "+" ? "+" : "aug"; words.append("augmented") }
        else if next("ø") { quality = "ø"; words.append("half-diminished") }
        else if next("Δ") { quality = "Δ"; words.append("major") }
        var extended = false
        if next("6/9") { words.append(contentsOf: ["six", "nine"]); extended = true }
        else if let n = number(["5", "6", "7", "9", "11", "13"]) { words.append(n); extended = true }
        else if quality == "Δ" { words.append("seven") }
        if (quality == "m" || quality == "min"), next("maj7") || next("M7") { words.append(contentsOf: ["major", "seven"]) }
        var extra = false  // sus, add, an alteration or a bass note
        if next("sus") {
            words.append("sus")
            words.append(number(["2", "4"]) ?? "four")
            extra = true
        }
        if next("add") {
            guard let n = number(["2", "4", "9", "11", "13"]) else { return nil }
            words.append(contentsOf: ["add", n])
            extra = true
        }
        while i < chars.count, "#♯b♭".contains(chars[i]) {
            let sharp = chars[i] == "#" || chars[i] == "♯"
            i += 1
            guard let n = number(["5", "9", "11", "13"]) else { return nil }
            words.append(contentsOf: [sharp ? "sharp" : "flat", n])
            extra = true
        }
        var bass = ""
        if next("/") {
            guard i < chars.count, "ABCDEFG".contains(chars[i]) else { return nil }
            bass = String(chars[i])
            i += 1
            words.append(contentsOf: ["over", bass])
            if next("#") || next("♯") { words.append("sharp"); bass += "#" }
            else if next("♭") || next("b") { words.append("flat"); bass += "b" }
            extra = true
        }
        guard i == chars.count else { return nil }
        let spoken = words.joined(separator: " ")
        let plain = accidental.isEmpty && quality.isEmpty
        if plain && !extended && !extra { return Chord(spoken: word, kind: .letter) }
        if plain && extended && !extra {
            return word.contains("6/9") ? Chord(spoken: spoken, kind: .strong) : Chord(spoken: word, kind: .digits)
        }
        if accidental.isEmpty, quality == "m" || quality == "min", !extended, !extra {
            return Chord(spoken: spoken, kind: .ambiguousMinor)
        }
        if accidental == "b", quality.isEmpty, !extended, !extra, root != "B", root != "E" {
            return Chord(spoken: spoken, kind: .ambiguous)  // "Ab", "Db": also words; "Bb", "Eb" never are
        }
        if accidental.isEmpty, quality == "+", !extended, !extra { return Chord(spoken: spoken, kind: .ambiguous) }  // a grade
        if plain, !extended, bass.count == 1, words.count == 3 { return Chord(spoken: spoken, kind: .ambiguous) }  // A/B
        return Chord(spoken: spoken, kind: .strong)
    }
}
