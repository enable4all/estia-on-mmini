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
/// and slash chords are deferred: they stay as written, with `#` and `b` said as words. Since the chord speech audit
/// (18 Oct), es, it, pt, fr, nl, de, pl and ru also read "C-7" (minor seventh, in a music context; with a sharp or flat anywhere), a minor chord with
/// a major seventh ("CmMaj7", "Cm(maj7)"), alterations ("C7(b9)", "Cm7b5": the sign by its name, then the number)
/// and "C(no3)" (`Style.forms`), and a chart's marks among chords: "N.C.", "%" and a repeat ("x2") in their own
/// words (`Style.marks`); ja and ko read them as before.
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
        /// What any gap but a comma between two chords of a run becomes (a pause): spaces, a dash, a bar, a slash.
        let dash: String
        /// Words that make a sentence about music, matched at a word's start ("acord" in "acordes"); ja and ko none.
        var music: [String] = []
        /// The words for the forms read since the chord speech audit (18 Oct): "C-7", "CmMaj7", "Cm(maj7)",
        /// "C7(b9)", "Cm7(b5)", "C(no3)"; ja and ko none (nil): those stay as before.
        var forms: Forms? = nil
        /// A chart's marks in the language's words; ja and ko read none (nil): their marks stay as written.
        var marks: Marks? = nil
    }

    /// An alteration's signs said by their names ("b9": "bemol 9", its number read by the numbers pass) and "no3".
    struct Forms: Sendable {
        let sharp: String
        let flat: String
        let noThird: String
    }

    /// A chart's marks said among chords (the chord speech audit, 18 Oct): "N.C." (no chord), "%" (the bar again)
    /// and a repeat ("x2", "(x3)").
    struct Marks: Sendable {
        let noChord: String
        let again: String
        /// A repeat's count, said ("dos veces", "zweimal").
        let times: @Sendable (Int) -> String

        func said(_ mark: ChordSpeller.Mark) -> String {
            switch mark {
            case .noChord: return noChord
            case .again: return again
            case .times(let count, _): return times(count)
            }
        }
    }

    /// One language's chord reader, its patterns compiled once.
    final class Speller: @unchecked Sendable {  // immutable; NSRegularExpression is safe to share
        let style: Style
        private let candidate: Pattern
        /// Without the forms (`Style.forms`): the reading before them, for a chord whose brackets come with a bass
        /// or a sus ("Cm7(b5)/E": "Cm7" read, the rest as written); nil when the style has no forms.
        private let plainCandidate: Pattern?
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
            // root, sign, quality, alterations ("b5"), brackets ("(maj7)", "(b9)", "(no3)"), then a deferred tail;
            // without the forms' words (ja, ko) the quality is one of the first four and the next two groups stay empty
            func pattern(forms: Bool) -> Pattern {
                let quality = !forms ? "(maj7|m7|m|7)?()()"
                    : #"(mMaj7|mmaj7|mM7|maj7|m7|-7|m|7)?((?:[#♯♭b](?:5|9|11|13)(?![0-9]))*)"#
                    + #"(\((?:maj7|Maj7|M7|no3|[#♯♭b](?:5|9|11|13))(?:,(?:[#♯♭b](?:5|9|11|13)))*\))?"#
                return Pattern(#"(?<![\p{Latin}\p{Cyrillic}\p{N}#♯♭+/°º'’])("# + roots + #")([#♯♭b]?)"# + quality
                    + #"((?:sus[24]?|dim7?|aug|add[0-9]{1,2}|[0-9]{1,2}|°|ø|/[A-H](?:is|es|[#♯♭b])?)*)"#
                    + #"(?![\p{Latin}\p{Cyrillic}\p{N}#♯♭+])"#)
            }
            candidate = pattern(forms: style.forms != nil)
            plainCandidate = style.forms == nil ? nil : pattern(forms: false)
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
            if !chord.alterations.isEmpty || chord.noThird || chord.majorSeventh && chord.quality != "maj7" { return true }
            // "la A-7": a road, a chord only in a music context; with a sharp or flat a chord anywhere ("Fa#-7", "Sib-7",
            // "Fis-7": no road is named so; the review, 18 Oct)
            if chord.dashed { return chord.sharp || chord.flat }
            if chord.tail.range(of: #"sus|dim|aug|add|°|ø|/[A-H](?:[#♯♭b]|is|es)"#, options: .regularExpression) != nil {
                return true
            }
            if chord.quality == "m7" || chord.quality == "maj7" { return true }
            if !chord.accidental.isEmpty && !chord.quality.isEmpty { return true }
            // a solfège or German name
            return chord.root.count > 1 && (!chord.accidental.isEmpty || chord.quality == "7")
        }

        /// Between two chords of a run: spaces, a comma, a dash, a bar, a semicolon (a table's bar lines, as the
        /// bridge says them), "‖", "…", "⏸", "𝄐" or slashes with a space before them (beat slashes), as in English.
        private static let symbolGap = Pattern(#"^(?:\s*(?:,|、|;|-|–|—|\||‖|…|⏸\x{FE0F}?|𝄐)|\s+/)*\s*$"#)
        /// A comma alone, kept as written; any other gap between two chords said is the language's pause (`dash`).
        private static let commaGap = Pattern(#"^\s*[,、]\s*$"#)

        private struct Token {
            let range: NSRange
            /// A chord, or else a chart's mark.
            let chord: Chord?
            let mark: ChordSpeller.Mark?
        }

        struct Chord {
            let written: String
            let root: String
            /// 0 to 6 for C to B; 7 for H.
            let letter: Int
            /// A German name ("Fis", "Es"): its accidental is in the name.
            let named: Bool
            let accidental: String
            /// "m", "7", "m7", "maj7" or "mMaj7" ("CmMaj7", "Cm(maj7)"); "" for none.
            let quality: String
            let tail: String
            /// "C-7": minor in jazz notation.
            var dashed = false
            /// Alterations ("b5", "#11"), bare or in brackets: their sign and number.
            var alterations: [(sharp: Bool, number: String)] = []
            /// "(no3)".
            var noThird = false
            var majorSeventh: Bool { quality == "maj7" || quality == "mMaj7" }
            var suffixed: Bool { !accidental.isEmpty || !quality.isEmpty || !tail.isEmpty || !alterations.isEmpty || noThird }
            var sharp: Bool { accidental == "#" || accidental == "♯" || (named && root.hasSuffix("is")) }
            var flat: Bool { accidental == "b" || accidental == "♭" || (named && !root.hasSuffix("is")) }
        }

        private static let syllables: [String: Int] = ["do": 0, "dó": 0, "re": 1, "ré": 1, "mi": 2, "fa": 3, "fá": 3,
                                                        "sol": 4, "la": 5, "lá": 5, "si": 6]

        private func parse(_ m: Pattern.Match) -> Chord? {
            let root = m[1]
            // a tail of digits alone is no chord ("A4", "B12"); "C9" waits for the deferred list
            if !m[6].isEmpty, m[6].allSatisfy(\.isNumber) { return nil }
            // alterations and brackets go with a plain chord only; with a deferred tail it stays as written
            if !m[6].isEmpty, !m[4].isEmpty || !m[5].isEmpty { return nil }
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
            if root == "G", m[2] == "b", m[3].isEmpty, m[6].isEmpty, m.before.last?.isNumber == true
                || m.before.hasSuffix(" ") && m.before.dropLast().last?.isNumber == true { return nil }
            var quality = ["mmaj7": "mMaj7", "mM7": "mMaj7", "-7": "m7"][m[3]] ?? m[3]
            var alterations: [(sharp: Bool, number: String)] = []
            var noThird = false
            let pieces = Self.alteration.regex.matches(in: m[4], range: NSRange(location: 0, length: (m[4] as NSString).length))
                .map { (m[4] as NSString).substring(with: $0.range) }
            for piece in pieces + m[5].trimmingCharacters(in: CharacterSet(charactersIn: "()")).split(separator: ",").map(String.init) {
                switch piece {
                case "maj7", "Maj7", "M7": quality = quality == "m" ? "mMaj7" : quality.isEmpty ? "maj7" : quality
                case "no3": noThird = true
                default: alterations.append((sharp: "#♯".contains(piece.first!), number: String(piece.dropFirst())))
                }
            }
            return Chord(written: m[0], root: root, letter: letter, named: named, accidental: m[2], quality: quality,
                         tail: m[6], dashed: m[3] == "-7", alterations: alterations, noThird: noThird)
        }

        private static let alteration = Pattern(#"[#♯♭b](?:5|9|11|13)"#)

        func spell(_ text: String) -> String {
            let ns = text as NSString
            // a chart's marks first, where the language has words for them: the letters of "N.C." are no chord
            var tokens: [Token] = style.marks == nil ? [] : ChordSpeller.markPattern
                .matches(in: text, range: NSRange(location: 0, length: ns.length))
                .compactMap { found in ChordSpeller.Mark(found, in: ns).map { Token(range: found.range, chord: nil, mark: $0) } }
            let marks = tokens.map(\.range)
            _ = candidate.rewrite(text) { m in
                guard !marks.contains(where: { NSIntersectionRange($0, m.range).length > 0 }) else { return nil }
                if let chord = self.parse(m) {
                    tokens.append(Token(range: m.range, chord: chord, mark: nil))
                } else if let shorter = self.plainMatch(at: m.range.location, in: ns), let chord = self.parse(shorter) {
                    tokens.append(Token(range: shorter.range, chord: chord, mark: nil))  // "Cm7(b5)/E": "Cm7", as before
                }
                return nil
            }
            tokens.sort { $0.range.location < $1.range.location }
            guard !tokens.isEmpty else { return text }
            // runs: chords and marks joined only by a separator or and/or
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
                // "N.C." and "%" make a chart, as a suffixed chord does
                if token.chord?.suffixed == true || token.mark.map({ !$0.isRepeat }) == true { suffixedRun.insert(run[i]) }
                if token.chord?.accidental.isEmpty == false { hasSymbols.insert(run[i]) }
                members[run[i], default: []].append(i)
            }
            // German names: an H or a name such as Fis anywhere in the text (the live audit, 17 Oct: "Hm, Fis7,
            // Es-Dur, B und C#m" is two runs, and its B is B flat)
            let german = tokens.contains { $0.chord.map { $0.letter == 7 || $0.named } == true }
            var spelled = tokens.indices.map { i in
                tokens[i].chord != nil && spells(i, tokens, ns, members: run[i] >= 0 ? members[run[i]]! : [i],
                                                 suffixedRun: run[i] >= 0 && suffixedRun.contains(run[i]))
            }
            if style.marks != nil {
                for (i, token) in tokens.enumerated() {
                    guard let mark = token.mark else { continue }
                    let sentence = Self.sentence(ns, around: token.range.location)
                    let aboutMusic = musicWords?.matches(ns.substring(with: sentence)) == true
                        || tokens.contains { $0.chord.map(strong) == true && NSIntersectionRange($0.range, sentence).length > 0 }
                    let runMates = run[i] >= 0 ? members[run[i]]! : []
                    let chordMates = runMates.contains { tokens[$0].chord != nil && spelled[$0] }
                    switch mark {
                    case .noChord:
                        // among chords; "N.C." also after a colon or a music word ("Estribillo: N.C."), never "NC"
                        // alone or a place's "N.C." ("de N.C.", "aus N.C.": the review, 18 Oct)
                        guard chordMates || aboutMusic && ChordSpeller.chartSpot(
                            token.range, ns, music: { self.musicWords?.matches($0) == true }, words: []) else { continue }
                    case .again:  // the bar again: only between chords
                        guard chordMates else { continue }
                    case .times(_, let bracketed):
                        // after a chord said in its run, a section's name ("Estribillo x2") or a bracket ("(x2)",
                        // "[Estribillo] x2") about music
                        let afterChord = runMates.contains { $0 < i && spelled[$0] }
                        let before = ns.substring(to: token.range.location).trimmingCharacters(in: .whitespaces)
                        let afterBracket = bracketed || before.last.map { ")]".contains($0) } == true
                        let word = String(before.reversed().prefix { $0.isLetter }.reversed())
                        let saysChords = aboutMusic || tokens.indices.contains {
                            spelled[$0] && NSIntersectionRange(tokens[$0].range, sentence).length > 0
                        }
                        // a bracket only at the end of a clause (the review, 18 Oct: "cuerdas (x2) para" is a quantity)
                        guard afterChord || musicWords?.matches(word) == true
                            || afterBracket && saysChords && ChordSpeller.endsClause(after: token.range, ns) else { continue }
                    }
                    spelled[i] = true
                }
            }
            var out = ""
            var last = 0
            for (i, token) in tokens.enumerated() {
                let start = token.range.location
                let joined = spelled[i] && i > 0 && spelled[i - 1] && run[i] >= 0 && run[i - 1] == run[i]
                if joined, Self.symbolGap.matches(gaps[i]), !Self.commaGap.matches(gaps[i]) {
                    out += style.dash
                } else {
                    out += ns.substring(with: NSRange(location: last, length: start - last))
                }
                if let mark = token.mark, spelled[i], let words = style.marks {
                    out += words.said(mark)
                    if mark == .noChord, ns.substring(with: token.range).hasSuffix("."),
                       ChordSpeller.endsSentence(after: token.range, ns),
                       !(i + 1 < tokens.count && run[i] >= 0 && run[i + 1] == run[i]) {
                        out += "."  // its dot ended the sentence too
                    }
                } else if let chord = token.chord, spelled[i] {
                    out += say(chord, hasH: german, symbols: run[i] >= 0 && hasSymbols.contains(run[i]))
                } else if let chord = token.chord, !lettersStyle, chord.accidental == "#" || chord.accidental == "♯" {
                    // "C#": the programming language, never "do sostenido"; the rest as written ("C#/E": "C sharp/E")
                    out += chord.root + " sharp" + chord.written.dropFirst(chord.root.count + chord.accidental.count)
                } else {
                    out += ns.substring(with: token.range)
                }
                last = NSMaxRange(token.range)
            }
            return out + ns.substring(from: last)
        }

        /// The reading without the forms at `location` ("Cm7" of "Cm7(b5)/E"), or nil.
        private func plainMatch(at location: Int, in ns: NSString) -> Pattern.Match? {
            guard let plain = plainCandidate, let found = plain.regex.firstMatch(
                in: ns as String, options: [.anchored, .withTransparentBounds],
                range: NSRange(location: location, length: ns.length - location)) else { return nil }
            let groups = (0..<found.numberOfRanges).map { index in
                found.range(at: index).location == NSNotFound ? "" : ns.substring(with: found.range(at: index))
            }
            return Pattern.Match(groups: groups, range: found.range, text: ns)
        }

        /// Whether token `i` is said as a chord: in its run's `members` (itself alone outside a run), see the type's
        /// note for the context each kind needs.
        private func spells(_ i: Int, _ tokens: [Token], _ ns: NSString, members: [Int], suffixedRun: Bool) -> Bool {
            guard let chord = tokens[i].chord else { return false }
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
            return musical || tokens.contains { $0.chord.map(strong) == true && NSIntersectionRange($0.range, sentence).length > 0 }
        }

        // MARK: Saying one chord

        private func say(_ chord: Chord, hasH: Bool, symbols: Bool) -> String {
            if !chord.tail.isEmpty { return deferred(chord) }
            var extras = ""  // alterations and "no3", after the chord's own words
            if let forms = style.forms {
                extras = (chord.alterations.map { ($0.sharp ? forms.sharp : forms.flat) + " " + $0.number }
                    + (chord.noThird ? [forms.noThird] : [])).map { " " + $0 }.joined()
            }
            return name(chord, hasH: hasH, symbols: symbols) + extras
        }

        private func name(_ chord: Chord, hasH: Bool, symbols: Bool) -> String {
            let minor = chord.quality == "m" || chord.quality == "m7" || chord.quality == "mMaj7"
            let seventh = chord.quality == "7" || chord.quality == "m7"
            switch style.roots {
            case .solfege(let names):
                var words = [Self.syllables[chord.root.lowercased()] != nil && chord.root.count > 1
                    ? chord.root.lowercased() : names[chord.letter]]
                if chord.sharp { words.append(style.sharp) } else if chord.flat { words.append(style.flat) }
                if minor { words.append(style.minor) }
                if seventh { words.append(style.seventh) } else if chord.majorSeventh { words.append(style.majorSeventh) }
                return words.joined(separator: " ")
            case .russian(let names):
                var root = chord.letter == 7 ? "си" : names[chord.letter]
                if chord.letter == 6, hasH || chord.flat { root = "си-бемоль" }
                else if chord.sharp { root += "-" + style.sharp } else if chord.flat { root += "-" + style.flat }
                return root + (minor ? " " + style.minor : "") + (seventh ? " " + style.seventh : "")
                    + (chord.majorSeventh ? " " + style.majorSeventh : "")
            case .german(let lowerMinor):
                var name = germanName(chord, h: true)
                if chord.letter == 6, !chord.named, chord.accidental.isEmpty, symbols, !hasH { name = "H" }  // English B
                let root = minor ? (lowerMinor ? name.lowercased() : name) + "-" + style.minor : name
                return root + (seventh ? " " + style.seventh : "") + (chord.majorSeventh ? " " + style.majorSeventh : "")
            case .dutch:
                let name = germanName(chord, h: false)
                return name + (minor ? " " + style.minor : "") + (seventh ? " " + style.seventh : "")
                    + (chord.majorSeventh ? " " + style.majorSeventh : "")
            case .letters(let names, let joiner):
                var words = [names[chord.letter]]
                if chord.sharp { words.append(style.sharp) } else if chord.flat { words.append(style.flat) }
                if minor { words.append(style.minor) }
                if seventh { words.append(style.seventh) } else if chord.majorSeventh { words.append(style.majorSeventh) }
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
