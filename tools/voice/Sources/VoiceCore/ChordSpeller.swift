import Foundation

/// Chord names said as music before Kokoro reads them (owner, 16 Oct, on a call: "chord progression still chinese ...
/// # and flats have words"; the rules are the review's): "Em7" is "E minor seven", "D/F#" "D over F sharp",
/// "Bb" "B flat". A chord that could be a word ("Am", "Ab", "A+", "A/B") is spelled only among other chords or in a
/// sentence about music; "Am I", lower case and words that only start with A to G stay as written. A run of chords
/// gets a comma between them (punctuation is Kokoro's only pause). A chart's marks are words among chords (the chord
/// speech audit, 18 Oct): "N.C." is "no chord" (in a sentence about music too, where a chart puts it: `chartSpot`), "%" "repeat", and "x2" after chords, a
/// section's name or a bracket "twice". Numbers are left to `NumberNormalizer`.
public enum ChordSpeller {

    /// Between the chords of a run: a comma, Kokoro's pause (owner, 16 Oct: "just use comma... for chords"; measured
    /// 0.12-0.24 s; a full stop gives 0.21-0.33 s, so a longer pause would need silence put in by the service).
    static let separator = ", "

    public static func spell(_ text: String) -> String {
        let ns = text as NSString
        let full = NSRange(location: 0, length: ns.length)
        // a chart's marks first: the letters of "N.C." are no chord
        var tokens: [Token] = markPattern.matches(in: text, range: full).compactMap { found in
            Mark(found, in: ns).map { Token(range: found.range, chord: nil, mark: $0, article: false) }
        }
        let marks = tokens.map(\.range)
        for match in candidate.matches(in: text, range: full)
        where !marks.contains(where: { NSIntersectionRange($0, match.range).length > 0 }) {
            let word = ns.substring(with: match.range)
            guard let chord = parse(word) else { continue }
            if chord.kind == .ambiguousMinor, followedByPronoun(ns, after: match.range) { continue }  // "Am I late?"
            let article = chord.kind == .letter && word == "A" && startsSentence(ns, at: match.range.location)
            tokens.append(Token(range: match.range, chord: chord, mark: nil, article: article))
        }
        tokens.sort { $0.range.location < $1.range.location }
        // an "A" that starts a sentence is the article, unless two chords or more follow it with spaces or bars alone
        // between them ("A D E A"; "A D chord", "A G7 is" keep the article)
        tokens = tokens.indices.filter { !tokens[$0].article || opensSheet($0, tokens, ns) }.map { tokens[$0] }
        guard !tokens.isEmpty else { return text }
        // runs: chords and marks joined only by separators or and/then/to/or
        var run = [Int](repeating: -1, count: tokens.count)
        var gapIsSymbol = [Bool](repeating: false, count: tokens.count)  // the gap before token i, within a run
        var current = -1
        for i in tokens.indices {
            if i > 0 {
                let gap = gapBefore(i, tokens, ns)
                if matches(symbolGap, gap) || matches(wordGap, gap) {
                    if current < 0 { current = i - 1; run[i - 1] = current }
                    run[i] = current
                    gapIsSymbol[i] = matches(symbolGap, gap)
                    continue
                }
            }
            current = -1
        }
        var chordsIn: [Int: Int] = [:]  // a run's chords
        var runHasMark: Set<Int> = []  // a run with "N.C." or "%": a chart
        var runHasChord: Set<Int> = []
        for (i, token) in tokens.enumerated() where run[i] >= 0 {
            if let chord = token.chord {
                chordsIn[run[i], default: 0] += 1
                // a run that holds a chord other than a bare letter ("C G7 Am F"): its bare letters are chords too
                if chord.kind != .letter { runHasChord.insert(run[i]) }
            } else if let mark = token.mark, !mark.isRepeat {
                runHasMark.insert(run[i])
                runHasChord.insert(run[i])
            }
        }
        // and so does a row of three chords or more with spaces or bars alone between them that is a sentence of its
        // own ("A D E A", "G | C | D"); inside a sentence such letters are notes, a tuning or a list ("Standard tuning is
        // E A D G B E", "Vitamins A C E keep you healthy"; the review, 18 Oct), and so are letters each the next in the
        // alphabet ("The A B C of", "C D E F G are the notes") and letters in a sentence about notes ("The notes are G
        // B D F")
        var rows: [[Int]] = []
        for i in tokens.indices where tokens[i].chord != nil {
            let follows = rows.last?.last == i - 1 && run[i] >= 0 && run[i - 1] == run[i]
                && isSheet(gapBefore(i, tokens, ns))
            if follows { rows[rows.count - 1].append(i) } else { rows.append([i]) }
        }
        for row in rows where row.count >= 3 {
            let sentence = sentenceRange(ns, around: tokens[row[0]].range.location)
            if !inOrder(row.map { ns.substring(with: tokens[$0].range) }), !matches(notesWord, ns.substring(with: sentence)),
               standsAlone(from: tokens[row[0]].range, to: tokens[row[row.count - 1]].range, in: sentence, ns) {
                runHasChord.insert(run[row[0]])
            }
        }
        // what each token is said as, nil as written: chords first, then the marks that follow them
        var said = [String?](repeating: nil, count: tokens.count)
        for (i, token) in tokens.enumerated() {
            guard let chord = token.chord else { continue }
            let sentence = ns.substring(with: sentenceRange(ns, around: token.range.location))
            let inRun = run[i] >= 0 && (chordsIn[run[i], default: 0] >= 2 || runHasMark.contains(run[i]))
            let musical = inRun || mentionsMusic(sentence) || strongIn(sentence: token.range, tokens, ns)
            if chord.kind == .letter, (inRun && runHasChord.contains(run[i])) || mentionsMusic(sentence),
               !followedByQuality(ns, after: token.range) {
                // owner, 17 Oct: a lone "E" or "D" sounded "like exhaling" (0.16-0.23 s at half the level around
                // it); a bare letter chord is the major chord, said so
                said[i] = ns.substring(with: token.range) + " major"
            } else if chord.kind == .strong || (chord.kind.isAmbiguous && musical) {
                said[i] = chord.spoken
            }
        }
        for (i, token) in tokens.enumerated() {
            guard let mark = token.mark else { continue }
            let sentence = sentenceRange(ns, around: token.range.location)
            let musicWords = mentionsMusic(ns.substring(with: sentence)) || strongIn(sentence: token.range, tokens, ns)
            let saysChords = musicWords
                || tokens.indices.contains { said[$0] != nil && NSIntersectionRange(tokens[$0].range, sentence).length > 0 }
            switch mark {
            case .noChord:
                // among chords; "N.C." also where a chart puts it in a sentence about music ("The song ends on N.C."),
                // never "NC" alone ("guitar in NC", "an NC switch": the review, 18 Oct)
                guard (run[i] >= 0 && chordsIn[run[i], default: 0] > 0)
                    || musicWords && chartSpot(token.range, ns, music: mentionsMusic, words: chartWords)
                else { continue }
            case .again:  // the bar again: only between chords ("What % of the song" stays)
                guard run[i] >= 0 && chordsIn[run[i], default: 0] > 0 else { continue }
            case .times(_, let bracketed):
                // after a chord said in its run, a section's name ("Chorus x2") or a bracket ("(x2)", "[Chorus] x2")
                // in a sentence about music
                let afterChord = run[i] >= 0 && (0..<i).contains { run[$0] == run[i] && said[$0] != nil }
                let before = ns.substring(to: token.range.location).trimmingCharacters(in: .whitespaces)
                let afterBracket = bracketed || before.last.map { ")]".contains($0) } == true
                let afterSection = mentionsMusic(String(before.reversed().prefix { $0.isLetter }.reversed()))
                // a bracket only at the end of a clause: inside one it is a quantity ("Buy strings (x2) for the
                // guitar": the review, 18 Oct); a section's name anywhere ("Chorus 2x then outro")
                guard afterChord || afterSection || afterBracket && saysChords && endsClause(after: token.range, ns)
                else { continue }
            }
            said[i] = mark.words
        }
        var out = ""
        var last = 0
        for (i, token) in tokens.enumerated() {
            let start = token.range.location
            // the gap after the token before, said as a pause; never next to a mark kept as written ("Plan B x2"), and
            // a semicolon or a slash only next to a chord said ("Take route A; B is closed" stays: the review, 18 Oct)
            let gap = i > 0 ? gapBefore(i, tokens, ns) : ""
            let joined = i > 0 && run[i] >= 0 && run[i - 1] == run[i] && gapIsSymbol[i]
                && (token.mark == nil || said[i] != nil) && (tokens[i - 1].mark == nil || said[i - 1] != nil)
                && (!gap.contains(where: { ";/".contains($0) }) || said[i] != nil || said[i - 1] != nil)
            out += joined ? separator : ns.substring(with: NSRange(location: last, length: start - last))
            var words = said[i] ?? ns.substring(with: token.range)
            if token.mark == .noChord, said[i] != nil, ns.substring(with: token.range).hasSuffix("."),
               endsSentence(after: token.range, ns), !(i + 1 < tokens.count && run[i] >= 0 && run[i + 1] == run[i]) {
                words += "."  // "The song ends on N.C.": its dot ended the sentence too
            }
            out += words
            last = NSMaxRange(token.range)
        }
        out += ns.substring(from: last)
        return out
    }

    // MARK: - Finding chords

    private struct Token {
        let range: NSRange
        let chord: Chord?
        let mark: Mark?
        /// An "A" that starts a sentence: the article, unless chords follow it (`opensSheet`).
        let article: Bool
    }

    /// A chart's mark, said as words among chords: "N.C." (no chord), "%" (the bar again), a repeat ("x2", "(3x)").
    enum Mark: Equatable {
        case noChord
        case again
        case times(Int, bracketed: Bool = false)

        init?(_ found: NSTextCheckingResult, in ns: NSString) {
            func group(_ name: String) -> String? {
                let range = found.range(withName: name)
                return range.location == NSNotFound ? nil : ns.substring(with: range)
            }
            if group("nc") != nil { self = .noChord; return }
            if group("again") != nil { self = .again; return }
            let bracketed = group("b1") ?? group("b2")
            guard let count = Int(bracketed ?? group("n1") ?? group("n2") ?? ""), count >= 2 else { return nil }
            self = .times(count, bracketed: bracketed != nil)
        }

        var isRepeat: Bool {
            if case .times = self { return true }
            return false
        }

        var words: String {
            switch self {
            case .noChord: return "no chord"
            case .again: return "repeat"
            case .times(let count, _): return count == 2 ? "twice" : Words.cardinal(count) + " times"
            }
        }
    }

    /// "N.C.", "NC" or "n.c."; "%" alone (never after a number: "20 %"); a repeat in brackets ("(x2)", "[2x]") or
    /// alone ("x2", "×3", "2x"; never "1.5x" or "0x1F").
    static let markPattern = try! NSRegularExpression(
        pattern: #"(?<nc>(?<![\p{L}\p{N}.])(?:N\.C\.?|NC|n\.c\.)(?![\p{L}\p{N}]))"#
            + #"|(?<again>(?<![\p{L}\p{N}%])(?<![\p{N}]\s)%(?![\p{L}\p{N}%]))"#
            + #"|[(\[]\s*(?:[x×X]\s?(?<b1>[0-9]{1,2})|(?<b2>[0-9]{1,2})\s?[x×X])\s*[)\]]"#
            + #"|(?<![\p{L}\p{N}.,])(?:[x×](?<n1>[0-9]{1,2})|(?<n2>[0-9]{1,2})[x×])(?![\p{L}\p{N}])"#)

    private static func gapBefore(_ i: Int, _ tokens: [Token], _ ns: NSString) -> String {
        let start = NSMaxRange(tokens[i - 1].range)
        return ns.substring(with: NSRange(location: start, length: tokens[i].range.location - start))
    }

    /// A chord sheet's gap: spaces, bars, semicolons, beat slashes, "…", "‖" or "⏸", never a comma, a dash or a word.
    private static func isSheet(_ gap: String) -> Bool {
        matches(symbolGap, gap) && !gap.contains(where: { ",-–—→".contains($0) })
    }

    /// Letters each the next in the alphabet ("A B C", "C D E F G"): a list or a scale, never a row of chords.
    private static func inOrder(_ words: [String]) -> Bool {
        let codes = words.compactMap { $0.count == 1 ? $0.unicodeScalars.first?.value : nil }
        return codes.count == words.count && zip(codes, codes.dropFirst()).allSatisfy { $1 == $0 + 1 }
    }

    /// Whether the text from `first` to `last` is its sentence alone: only marks ("x2"), punctuation, bars or other
    /// symbols around it.
    private static func standsAlone(from first: NSRange, to last: NSRange, in sentence: NSRange, _ ns: NSString) -> Bool {
        let before = ns.substring(with: NSRange(location: sentence.location, length: max(0, first.location - sentence.location)))
        let after = ns.substring(with: NSRange(location: NSMaxRange(last), length: max(0, NSMaxRange(sentence) - NSMaxRange(last))))
        return [before, after].allSatisfy { part in
            matches(onlyPunctuation, markPattern.stringByReplacingMatches(
                in: part, range: NSRange(location: 0, length: (part as NSString).length), withTemplate: ""))
        }
    }

    private static let onlyPunctuation = try! NSRegularExpression(pattern: #"^[\s\p{P}\p{S}]*$"#)

    /// Whether the "A" at `i` opens a run: two chords or more follow it with sheet gaps alone between them.
    private static func opensSheet(_ i: Int, _ tokens: [Token], _ ns: NSString) -> Bool {
        var j = i + 1
        while j < tokens.count, tokens[j].chord != nil, isSheet(gapBefore(j, tokens, ns)) { j += 1 }
        return j - i >= 3
    }

    /// Whether a strong chord ("Em7", "Bb") stands in the sentence around `range`.
    private static func strongIn(sentence range: NSRange, _ tokens: [Token], _ ns: NSString) -> Bool {
        let sentence = sentenceRange(ns, around: range.location)
        return tokens.contains { $0.chord?.kind == .strong && NSIntersectionRange($0.range, sentence).length > 0 }
    }

    /// Whether the dotted "N.C." at `range` stands where a chart puts it: after a colon ("Intro: N.C."), a section's
    /// name or another music word (`music`: "Chorus N.C.") or one of `words` ("ends on N.C.", "is N.C.", "with N.C.").
    /// "NC" and "n.c." alone never are, and nor is a place's "N.C." ("lives in N.C.", "Raleigh, N.C.", "de N.C.").
    static func chartSpot(_ range: NSRange, _ ns: NSString, music: (String) -> Bool, words: Set<String>) -> Bool {
        guard ns.substring(with: range).hasPrefix("N.C") else { return false }
        let before = ns.substring(to: range.location).trimmingCharacters(in: .whitespaces)
        if before.hasSuffix(":") { return true }
        let word = String(before.reversed().prefix { $0.isLetter }.reversed())
        return !word.isEmpty && (music(word) || words.contains(word.lowercased()))
    }

    /// English words a chart's "N.C." follows in a sentence ("The song ends on N.C.", "The intro is N.C.").
    private static let chartWords: Set<String> = ["on", "is", "with"]

    /// Whether the text after `range` ends its clause: nothing, or a mark that ends one (",", ";", ".", a bracket).
    static func endsClause(after range: NSRange, _ ns: NSString) -> Bool {
        let rest = ns.substring(from: NSMaxRange(range)).drop { $0 == " " || $0 == "\t" }
        return rest.first.map { ",;:.!?)]\n".contains($0) } ?? true
    }

    /// Whether the text after `range` starts a new sentence: nothing, or a space and a capital.
    static func endsSentence(after range: NSRange, _ ns: NSString) -> Bool {
        let rest = ns.substring(from: NSMaxRange(range))
        guard let next = rest.first(where: { !$0.isWhitespace }) else { return true }
        return rest.first?.isWhitespace == true && next.isUppercase
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

    /// A word that starts with A to G and holds only what a chord may hold, not inside another word: a "-" right
    /// after the root before a number ("C-7", minor), and alterations, "maj7", "add9" or "no3" in brackets ("C7(b9)").
    private static let candidate = try! NSRegularExpression(
        pattern: #"(?<![\p{L}\p{N}#♯♭+°øΔ/])[A-G](?:[#♯♭b]?-(?=[0-9]))?[A-Za-z0-9#♯♭+°øΔ]*"#
            + #"(?:\((?:maj7|Maj7|M7|no3|add(?:2|4|9|11|13)|[#♯♭b](?:5|9|11|13))(?:,(?:[#♯♭b](?:5|9|11|13)))*\))?"#
            + #"(?:/(?:[A-G][#♯♭b]?|9))?(?![\p{L}\p{N}])"#)
    /// Between two chords of a run: spaces, a comma, a dash, a bar, an arrow, a semicolon (a table's bar lines, as the
    /// bridge says them), "‖", "…", "⏸", "𝄐" or slashes with a space before them (beat slashes: "C / / / G", "C / / /; G";
    /// the chord speech audit, 18 Oct: "C; G; Am" was "CGM").
    private static let symbolGap = try! NSRegularExpression(
        pattern: #"^(?:\s*(?:,|;|-|–|—|\||‖|→|->|…|⏸\x{FE0F}?|𝄐)|\s+/)*\s*$"#)
    /// Or a joining word, kept as said.
    private static let wordGap = try! NSRegularExpression(pattern: #"^\s*,?\s+(?:and|then|to|or)\s+$"#)
    private static let musicWords = try! NSRegularExpression(
        pattern: #"\b(?:chords?|keys?|capo|frets?|barre|progressions?|strum\w*|verses?|chorus(?:es)?|bridges?|intro|outro|riffs?|tabs?|guitar|ukulele|piano|songs?)\b"#,
        options: [.caseInsensitive])
    private static let notesWord = try! NSRegularExpression(pattern: #"\bnotes?\b"#, options: [.caseInsensitive])
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

    /// Words after a letter that already say what it is ("the key of A minor", "the E string", "a D chord").
    private static let qualityWords: Set<String> = [
        "major", "minor", "maj", "min", "chord", "chords", "sharp", "flat", "natural", "string", "strings", "fret",
        "shape", "shapes", "note", "notes", "key", "power", "seven", "seventh", "sus", "dim", "diminished", "aug",
        "augmented", "add",
    ]

    private static func followedByQuality(_ ns: NSString, after range: NSRange) -> Bool {
        let rest = ns.substring(from: NSMaxRange(range))
        guard rest.first == " " else { return false }
        return qualityWords.contains(String(rest.drop { $0 == " " }.prefix { $0.isLetter }).lowercased())
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
        var dashed = false  // "C-7": minor in jazz notation, also a road's or a model's name
        if next("-") { quality = "m"; dashed = true; words.append("minor") }
        else if next("maj") { quality = "maj"; words.append("major") }
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
        if (quality == "m" || quality == "min"), next("maj7") || next("Maj7") || next("M7") {
            words.append(contentsOf: ["major", "seven"])  // "CmMaj7": minor, with a major seventh
            extended = true
        }
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
        if next("(") {  // "C7(b9)", "Cm(maj7)", "C(no3)", "G7(b9,#11)"
            repeat {
                if next("maj7") || next("Maj7") || next("M7") { words.append(contentsOf: ["major", "seven"]) }
                else if next("no3") { words.append(contentsOf: ["no", "third"]) }
                else if next("add") {
                    guard let n = number(["2", "4", "9", "11", "13"]) else { return nil }
                    words.append(contentsOf: ["add", n])
                }
                else if i < chars.count, "#♯b♭".contains(chars[i]) {
                    let sharp = chars[i] == "#" || chars[i] == "♯"
                    i += 1
                    guard let n = number(["5", "9", "11", "13"]) else { return nil }
                    words.append(contentsOf: [sharp ? "sharp" : "flat", n])
                } else { return nil }
            } while next(",")
            guard next(")") else { return nil }
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
        // "the A-7 south"; with a sharp or flat a chord anywhere: no road is named "F#-7" (the review, 18 Oct)
        if dashed, !extra, accidental.isEmpty { return Chord(spoken: spoken, kind: .ambiguous) }
        return Chord(spoken: spoken, kind: .strong)
    }
}
