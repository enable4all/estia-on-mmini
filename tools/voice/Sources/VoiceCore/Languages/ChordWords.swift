import Foundation

/// Chord names said as players of each language say them (the 0.9.3 plan, standards research), with its own parser
/// for letter roots (C, F#m7), solfège (Fa#m, Sib, Ré7) and German names (Fis7, Es): es, it, pt, fr and ru say the root
/// in solfège ("la menor"), de, pl and nl keep the letters with their -is and -es ("Cis-Moll", "Fis mineur"), ja and
/// ko say the English letter names in katakana and Hangul. Chords are spelled only in a music context, as in English
/// (the review, 17 Oct: "la cumbre del G7" was "sol séptima", "C# y F#" "do sostenido y fa sostenido"): a strong
/// chord ("F#m", "Em7", "Sib", "Fis7", "Csus4") wherever it stands; one that is also a model, a programming language
/// or a word ("G7", "C#", "Am", "Bb", "Sim") only in a sentence with a music word (the pack's, "acordes",
/// "Akkorde"), a strong chord, a run of three or more, or a run that is the whole sentence ("C, G, Am y F"); a bare
/// letter or syllable only in such a run that holds a suffixed chord ("A, B y C" stays a list; La, Mi, Si, A and E
/// are words). Elsewhere a letter with "#" is the programming language, "C sharp". ja and ko spell every suffixed
/// chord and every run as before: their letter names are how the letters are said there anyway. sus, dim, aug, add
/// and slash chords are deferred: they stay as written, with `#` and `b` said as words.
enum ChordWords {
    /// How one language says chords.
    struct Style: Sendable {
        enum Roots: Sendable {
            /// es, it, pt, fr: the seven syllables for C to B, as said; written syllables are read too.
            case solfege([String])
            /// de, pl: letters with -is and -es, H for B natural; pl writes minor in lower case ("cis-moll"). An
            /// English B is H in a run with sharps or flats, unless the text has an H or a German name already.
            case german(lowerMinor: Bool)
            /// nl: letters with -is and -es, B natural is B ("Bes" its flat).
            case dutch
            /// ru: the seven syllables; H is си, B is си-бемоль only in a text that has an H.
            case russian([String])
            /// ja, ko: the English letter names for C to B; `joiner` between the words ("" in ja).
            case letters([String], joiner: String)
        }
        let roots: Roots
        let sharp: String
        let flat: String
        let minor: String
        let seventh: String
        let majorSeventh: String
        /// The language's and/or words a run may join with.
        let joins: [String]
        /// Chords that are also words, spelled only in a run (de "Am", "Ab"; pt "Sim", "Em").
        let words: Set<String>
        /// What a dash or a bar between two chords of a run becomes (a pause).
        let dash: String
        /// Words that make a sentence about music, matched at a word's start ("acord" in "acordes"); ja and ko none.
        var music: [String] = []
    }

    /// One language's chord reader, its patterns compiled once.
    final class Speller: @unchecked Sendable {  // immutable; NSRegularExpression is safe to share
        let style: Style
        private let candidate: Pattern
        private let wordGap: Pattern?
        private let musicWords: Pattern?

        init(_ style: Style) {
            self.style = style
            let roots: String
            switch style.roots {
            case .solfege: roots = "Do|Dó|Re|Ré|Mi|Fa|Fá|Sol|La|Lá|Si|[A-G]"
            case .german: roots = "[A-H](?:is|es)|As|Es|[A-H]"
            case .dutch: roots = "[A-G](?:is|es)|As|Es|[A-G]"
            case .russian: roots = "[A-H]"
            case .letters: roots = "[A-G]"
            }
            candidate = Pattern(#"(?<![\p{Latin}\p{Cyrillic}\p{N}#♯♭+/°º'’])("# + roots + #")([#♯♭b]?)(maj7|m7|m|7)?"#
                + #"((?:sus[24]?|dim7?|aug|add[0-9]{1,2}|[0-9]{1,2}|°|ø|/[A-H](?:is|es|[#♯♭b])?)*)"#
                + #"(?![\p{Latin}\p{Cyrillic}\p{N}#♯♭+])"#)
            wordGap = style.joins.isEmpty ? nil
                : Pattern(#"^\s*,?\s+(?:"# + style.joins.joined(separator: "|") + #")\s+$"#)
            musicWords = style.music.isEmpty ? nil
                : Pattern(#"(?<![\p{L}])(?:"# + style.music.joined(separator: "|") + ")", options: [.caseInsensitive])
        }

        /// ja and ko: their letter names keep a letter's meaning, so every suffixed chord and every run is spelled.
        private var lettersStyle: Bool {
            if case .letters = style.roots { return true }
            return false
        }

        private static let boundary = Pattern(#"[.!?](?=\s|$)|[。！？]"#)
        private static let onlyPunctuation = Pattern(#"^[\s\p{P}]*$"#)

        /// The sentence around `location`: from after the last ".", "!" or "?" before a space (or "。"), to the next.
        private static func sentence(_ ns: NSString, around location: Int) -> NSRange {
            var start = 0
            var end = ns.length
            for found in boundary.regex.matches(in: ns as String, range: NSRange(location: 0, length: ns.length)) {
                if NSMaxRange(found.range) <= location { start = NSMaxRange(found.range) }
                else if found.range.location >= location { end = found.range.location; break }
            }
            return NSRange(location: start, length: end - start)
        }

        /// A chord whatever stands around it: a sharp or flat with m or 7 ("F#m", "Bb7"), m7 or maj7 ("Em7"), a
        /// solfège or German name with a sharp, flat or 7 ("Fa#", "Sib", "Sol7", "Fis7"), or a sus, dim, aug or add
        /// tail or a bass with a sharp or flat ("Csus4", "D/F#"); never one of the pack's words ("Lab").
        private func strong(_ chord: Chord) -> Bool {
            guard !style.words.contains(chord.written) else { return false }
            if chord.tail.range(of: #"sus|dim|aug|add|°|ø|/[A-H](?:[#♯♭b]|is|es)"#, options: .regularExpression) != nil {
                return true
            }
            if chord.quality == "m7" || chord.quality == "maj7" { return true }
            if !chord.accidental.isEmpty && !chord.quality.isEmpty { return true }
            // a solfège or German name
            return chord.root.count > 1 && (!chord.accidental.isEmpty || chord.quality == "7")
        }

        private static let symbolGap = Pattern(#"^\s*(?:,|、|-|–|—|\|)?\s*$"#)

        private struct Token {
            let range: NSRange
            let chord: Chord
        }

        struct Chord {
            let written: String
            let root: String
            /// 0 to 6 for C to B; 7 for H.
            let letter: Int
            /// A German name ("Fis", "Es"): its accidental is in the name.
            let named: Bool
            let accidental: String
            let quality: String
            let tail: String
            var suffixed: Bool { !accidental.isEmpty || !quality.isEmpty || !tail.isEmpty }
            var sharp: Bool { accidental == "#" || accidental == "♯" || (named && root.hasSuffix("is")) }
            var flat: Bool { accidental == "b" || accidental == "♭" || (named && !root.hasSuffix("is")) }
        }

        private static let syllables: [String: Int] = ["do": 0, "dó": 0, "re": 1, "ré": 1, "mi": 2, "fa": 3, "fá": 3,
                                                        "sol": 4, "la": 5, "lá": 5, "si": 6]

        private func parse(_ m: Pattern.Match) -> Chord? {
            let root = m[1]
            // a tail of digits alone is no chord ("A4", "B12"); "C9" waits for the deferred list
            if !m[4].isEmpty, m[4].allSatisfy(\.isNumber) { return nil }
            let letter: Int
            var named = false
            if let syllable = Self.syllables[root.lowercased()], root.count > 1 {
                letter = syllable
            } else {
                letter = Array("CDEFGABH").firstIndex(of: root.first!)!
                named = root.count > 1
            }
            if named, !m[2].isEmpty { return nil }  // "Fis#"
            // "16 Gb" is a size, never G flat
            if root == "G", m[2] == "b", m[3].isEmpty, m[4].isEmpty, m.before.last?.isNumber == true
                || m.before.hasSuffix(" ") && m.before.dropLast().last?.isNumber == true { return nil }
            return Chord(written: m[0], root: root, letter: letter, named: named, accidental: m[2], quality: m[3], tail: m[4])
        }

        func spell(_ text: String) -> String {
            let ns = text as NSString
            var tokens: [Token] = []
            _ = candidate.rewrite(text) { m in
                if let chord = self.parse(m) { tokens.append(Token(range: m.range, chord: chord)) }
                return nil
            }
            guard !tokens.isEmpty else { return text }
            // runs: chords joined only by a separator or and/or
            var run = [Int](repeating: -1, count: tokens.count)
            var gaps = [String](repeating: "", count: tokens.count)  // the gap before token i
            for i in tokens.indices.dropFirst() {
                let start = NSMaxRange(tokens[i - 1].range)
                let gap = ns.substring(with: NSRange(location: start, length: tokens[i].range.location - start))
                gaps[i] = gap
                if Self.symbolGap.matches(gap) || wordGap?.matches(gap) == true {
                    if run[i - 1] < 0 { run[i - 1] = i - 1 }
                    run[i] = run[i - 1]
                }
            }
            var suffixedRun: Set<Int> = []
            var hasSymbols: Set<Int> = []
            var members: [Int: [Int]] = [:]
            for (i, token) in tokens.enumerated() where run[i] >= 0 {
                if token.chord.suffixed { suffixedRun.insert(run[i]) }
                if !token.chord.accidental.isEmpty { hasSymbols.insert(run[i]) }
                members[run[i], default: []].append(i)
            }
            // German names: an H or a name such as Fis anywhere in the text (the live audit, 17 Oct: "Hm, Fis7,
            // Es-Dur, B und C#m" is two runs, and its B is B flat)
            let german = tokens.contains { $0.chord.letter == 7 || $0.chord.named }
            let spelled = tokens.indices.map { i in
                spells(i, tokens, ns, members: run[i] >= 0 ? members[run[i]]! : [i],
                       suffixedRun: run[i] >= 0 && suffixedRun.contains(run[i]))
            }
            var out = ""
            var last = 0
            for (i, token) in tokens.enumerated() {
                let start = token.range.location
                if spelled[i], i > 0, spelled[i - 1], run[i] >= 0, run[i - 1] == run[i],
                   gaps[i].contains(where: { "-–—|".contains($0) }) {
                    out += style.dash
                } else {
                    out += ns.substring(with: NSRange(location: last, length: start - last))
                }
                if spelled[i] {
                    out += say(token.chord, hasH: german, symbols: run[i] >= 0 && hasSymbols.contains(run[i]))
                } else if !lettersStyle, token.chord.accidental == "#" || token.chord.accidental == "♯" {
                    // "C#": the programming language, never "do sostenido"
                    out += token.chord.root + " sharp" + token.chord.quality + token.chord.tail
                } else {
                    out += token.chord.written
                }
                last = NSMaxRange(token.range)
            }
            return out + ns.substring(from: last)
        }

        /// Whether token `i` is said as a chord: in its run's `members` (itself alone outside a run), see the type's
        /// note for the context each kind needs.
        private func spells(_ i: Int, _ tokens: [Token], _ ns: NSString, members: [Int], suffixedRun: Bool) -> Bool {
            let chord = tokens[i].chord
            if lettersStyle { return suffixedRun || chord.suffixed && !style.words.contains(chord.written) }
            if strong(chord) { return true }
            guard chord.suffixed || suffixedRun else { return false }
            let sentence = Self.sentence(ns, around: tokens[i].range.location)
            let first = tokens[members.first!].range
            let lastToken = tokens[members.last!].range
            let before = ns.substring(with: NSRange(location: sentence.location,
                                                    length: max(0, first.location - sentence.location)))
            let after = ns.substring(with: NSRange(location: NSMaxRange(lastToken),
                                                   length: max(0, NSMaxRange(sentence) - NSMaxRange(lastToken))))
            let musical = members.count >= 3
                || Self.onlyPunctuation.matches(before) && Self.onlyPunctuation.matches(after)
                || musicWords?.matches(ns.substring(with: sentence)) == true
            if !chord.suffixed { return suffixedRun && musical }  // a bare letter or syllable in a run
            return musical || tokens.contains { strong($0.chord) && NSIntersectionRange($0.range, sentence).length > 0 }
        }

        // MARK: Saying one chord

        private func say(_ chord: Chord, hasH: Bool, symbols: Bool) -> String {
            if !chord.tail.isEmpty { return deferred(chord) }
            let minor = chord.quality == "m" || chord.quality == "m7"
            let seventh = chord.quality == "7" || chord.quality == "m7"
            switch style.roots {
            case .solfege(let names):
                var words = [Self.syllables[chord.root.lowercased()] != nil && chord.root.count > 1
                    ? chord.root.lowercased() : names[chord.letter]]
                if chord.sharp { words.append(style.sharp) } else if chord.flat { words.append(style.flat) }
                if minor { words.append(style.minor) }
                if seventh { words.append(style.seventh) } else if chord.quality == "maj7" { words.append(style.majorSeventh) }
                return words.joined(separator: " ")
            case .russian(let names):
                var root = chord.letter == 7 ? "си" : names[chord.letter]
                if chord.letter == 6, hasH || chord.flat { root = "си-бемоль" }
                else if chord.sharp { root += "-" + style.sharp } else if chord.flat { root += "-" + style.flat }
                return root + (minor ? " " + style.minor : "") + (seventh ? " " + style.seventh : "")
                    + (chord.quality == "maj7" ? " " + style.majorSeventh : "")
            case .german(let lowerMinor):
                var name = germanName(chord, h: true)
                if chord.letter == 6, !chord.named, chord.accidental.isEmpty, symbols, !hasH { name = "H" }  // English B
                let root = minor ? (lowerMinor ? name.lowercased() : name) + "-" + style.minor : name
                return root + (seventh ? " " + style.seventh : "") + (chord.quality == "maj7" ? " " + style.majorSeventh : "")
            case .dutch:
                let name = germanName(chord, h: false)
                return name + (minor ? " " + style.minor : "") + (seventh ? " " + style.seventh : "")
                    + (chord.quality == "maj7" ? " " + style.majorSeventh : "")
            case .letters(let names, let joiner):
                var words = [names[chord.letter]]
                if chord.sharp { words.append(style.sharp) } else if chord.flat { words.append(style.flat) }
                if minor { words.append(style.minor) }
                if seventh { words.append(style.seventh) } else if chord.quality == "maj7" { words.append(style.majorSeventh) }
                return words.joined(separator: joiner)
            }
        }

        /// A letter with its accidental as a German or Dutch name: Cis, Fis, Es, As, Bes (nl) or B (de: B flat).
        private func germanName(_ chord: Chord, h: Bool) -> String {
            if chord.named { return chord.root }
            let letter = chord.root
            if chord.sharp { return (letter == "B" && h ? "H" : letter) + "is" }
            guard chord.flat else { return letter }
            switch letter {
            case "A": return "As"
            case "E": return "Es"
            case "B", "H": return h ? "B" : "Bes"
            default: return letter + "es"
            }
        }

        /// A deferred chord as written, with its sharps and flats said ("C#sus4": "C sostenido sus4", "D/F#": "D/F
        /// sostenido"; de "Cis sus4").
        private func deferred(_ chord: Chord) -> String {
            func root(_ letter: String, _ accidental: String) -> String {
                guard !accidental.isEmpty, accidental != "is", accidental != "es" else { return letter + accidental }
                let sharp = accidental == "#" || accidental == "♯"
                switch style.roots {
                case .german, .dutch:
                    if sharp { return letter + "is" }
                    return letter == "A" ? "As" : letter == "E" ? "Es" : letter == "B" || letter == "H" ? "B" : letter + "es"
                case .russian: return letter + "-" + (sharp ? style.sharp : style.flat)
                case .letters(_, let joiner): return letter + joiner + (sharp ? style.sharp : style.flat)
                case .solfege: return letter + " " + (sharp ? style.sharp : style.flat)
                }
            }
            var said = root(chord.root, chord.accidental)
            let rest = chord.quality + chord.tail
            var tail = rest
            if let slash = rest.firstIndex(of: "/") {
                let bass = rest[rest.index(after: slash)...]
                let letter = String(bass.prefix(1))
                tail = String(rest[..<slash]) + "/" + root(letter, String(bass.dropFirst()))
            }
            if !chord.accidental.isEmpty, let first = tail.first, first.isLetter || first.isNumber { said += " " }
            return said + tail
        }
    }
}
