import Foundation

/// Text in the ten Supertonic-3 languages as Supertonic-3 is to read it (the 0.9.3 plan; English is `SpokenEnglish`):
/// one list of passes in one run order, as in English, with each language's words, tables and context lists in its
/// pack (`Pack_<code>.swift`). A pass that runs early owns its text: chords their letters, times their colon, dates
/// their numbers, phones their groups, amounts their unit; whatever number is left is read last (es, it, pt, fr, nl,
/// de, pl, ru spell every number; ja and ko keep the digits Supertonic-3 reads right). Every pattern is compiled once:
/// the shared ones in `static let`s, a language's own when its reader is made (once, on first use).
public enum SpokenLanguage {
    typealias Pass = SpokenEnglish.Pass

    /// The languages with a pack (English has its own passes).
    static let packs: [any Pack] = [Spanish(), French(), Italian(), Portuguese(), Dutch(), German(), Polish(), Russian(), Japanese(), Korean()]

    private static let readers: [String: Reader] = Dictionary(uniqueKeysWithValues: packs.map { ($0.code, Reader($0)) })

    /// The passes' names in their run order for `language` (none for a language without a pack).
    public static func order(_ language: String) -> [String] { readers[language]?.passes.map(\.name) ?? [] }

    /// `text` as Supertonic-3 is to read it in `language`; a language without a pack keeps its text.
    public static func spoken(_ text: String, language: String) -> String {
        guard let reader = readers[language] else { return text }
        return reader.passes.reduce(text) { text, pass in pass.run(text) }
    }
}

// MARK: - What a pack says

/// A number as written: its whole part (digits only, group marks taken out) and its decimals ("" for none).
struct Num: Equatable {
    let whole: String
    let fraction: String

    /// The whole part, when it fits the words (`Cardinals.largest`).
    var value: Int? { whole.count <= 15 ? Int(whole) : nil }
    var isInteger: Bool { fraction.isEmpty }

    /// From a match's whole part (with its group marks) and decimals.
    init(_ written: String, _ fraction: String = "") {
        whole = written.filter(\.isNumber)
        self.fraction = fraction
    }
}

/// The words before a match, nearest first, in lower case (up to four): the context a reading looks at ("o 13:00":
/// "o"; "jest piątek, 16.10.2026": "piątek", "jest").
struct Before {
    let words: [String]

    init(_ text: String) {
        var words: [String] = []
        var current: [Character] = []
        for character in text.reversed() {
            if character.isLetter || character == "'" || character == "’" {
                current.append(character)
            } else if !current.isEmpty {
                words.append(String(current.reversed()).lowercased())
                current = []
                if words.count == 4 { break }
            }
        }
        if !current.isEmpty, words.count < 4 { words.append(String(current.reversed()).lowercased()) }
        self.words = words
    }

    /// The word right before.
    var last: String { words.first ?? "" }

    /// Whether one of the `count` words before is in `set`.
    func within(_ count: Int, _ set: Set<String>) -> Bool { words.prefix(count).contains(where: set.contains) }
}

/// How digits are grouped in threes: after a dot (es, it, pt, de, nl), after a space, NBSP or U+202F (fr, pl, ru), or
/// with commas (ja, ko). English-style comma groups ("1,250,000": two groups or more) count in every language, spaces
/// in the dot languages too ("1 250 000": the Spanish Academy and the German style guides allow them).
enum Groups { case dot, space, comma }

enum Currency: Equatable { case euro, dollar, zloty, ruble, yen, won }

enum MeasureUnit: String, CaseIterable { case kilometer = "km", meter = "m", centimeter = "cm", kilogram = "kg", gram = "g", liter = "l", kilometerPerHour = "km/h" }

/// One language's words, tables and context lists, and how its readings are put together. The defaults are the
/// spelled languages' (es, it, pt, fr, nl, de, pl, ru); ja and ko keep digits.
protocol Pack: Sendable {
    var code: String { get }

    // MARK: numbers
    var groups: Groups { get }
    var minus: String { get }
    var plus: String { get }
    /// Between a sign word and its number (ja: none, "マイナス3度").
    var signSpace: String { get }
    /// "#1": "número uno".
    var numberSign: String { get }
    var decimalMark: String { get }
    /// Whether the last pass reads every number left (ja and ko keep their digits).
    var spellsNumbers: Bool { get }
    /// A number said alone, or before a noun of `gender` (its last word changes, `Inflect.gender`).
    func cardinal(_ n: Int, _ gender: Inflect.Gender) -> String
    /// A number with its decimals: 1 to 2 digits after the mark are read as a number, more digit by digit.
    func number(_ x: Num, _ gender: Inflect.Gender) -> String
    /// The form a noun takes after `x`.
    func plural(_ x: Num) -> Inflect.Plural
    /// pl and ru: `x` in the genitive after a word that takes it ("od pięciu", "до двадцати одного"), with the form
    /// its noun takes then; nil elsewhere (and over 99, decimals or a sign: as before).
    func genitive(_ x: Num, _ before: Before) -> (words: String, plural: Inflect.Plural)?
    /// Words after which a number keeps its plain form ("1 y 2": "uno y dos"); before any other word the noun's
    /// gender is guessed from its ending (es, it, pt, de).
    var plainBefore: Set<String> { get }
    /// es, it, pt nouns in -a that are masculine ("día", "problema").
    var masculineInA: Set<String> { get }

    // MARK: times
    /// A word said by the time itself (nl "uur", de "Uhr"): one written after it is not said twice.
    var timeUnit: String? { get }
    /// Words a dotted time ("9.30") must follow, without a time word after it.
    var dotTimeBefore: Set<String> { get }
    /// fr and pt also write "13h" and "9h30".
    var hTimes: Bool { get }
    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String
    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String

    // MARK: dates
    /// Words a day/month without a year must follow ("le 1/10"), so "1/2" stays a fraction.
    var dateWords: Set<String> { get }
    /// Month names a day before is read with (pl and ru: the genitive ordinal; fr, it, pt: day 1); none: empty.
    var dayMonthWords: [String] { get }
    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String
    /// A day before a month name: groups day, month, year, a year word; nil keeps it as written.
    func dayMonth(_ match: Pattern.Match, _ before: Before) -> String?
    /// pl and ru: the words that join a range's two days before a month ("od 1 do 3 listopada", "с 5 по 10 мая"; a
    /// dash too), read by `rangeStart`.
    var rangeDayJoins: [String] { get }
    /// The first day of a range and its join, said ("od pierwszego do "); `joiner` as written, a dash included.
    func rangeStart(_ day: Int, _ joiner: String, _ before: Before) -> String?

    // MARK: phones
    var phoneWords: [String] { get }
    func phone(_ plus: Bool, _ groups: [String]) -> String

    // MARK: money, percent, temperatures and units
    /// Symbols and codes for each currency said (`$` and `€` everywhere, the language's own too).
    var currencies: [String: Currency] { get }
    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String
    func percent(_ amount: String, _ plural: Inflect.Plural) -> String
    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String
    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String
    /// The language's own unit abbreviations (ru "км", "кг"), besides km, m, cm, kg, g, l and km/h.
    var unitAliases: [String: MeasureUnit] { get }
    /// The gender of a unit's noun (pl and ru: all masculine; es "un kilómetro").
    func unitGender(_ unit: MeasureUnit) -> Inflect.Gender
    /// A range of two amounts ("de cinco a diez"), or nil to leave it as written.
    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String?

    // MARK: years and ordinals
    /// A year's pattern (nl, de: four digits; pl "N roku"; ru "N году"), read by `year`.
    var yearPattern: String? { get }
    func year(_ match: Pattern.Match) -> String?
    var ordinalPattern: String? { get }
    func ordinal(_ match: Pattern.Match) -> String?
    /// es, it, pt: words after which a bare "°" is the ordinal sign typed with the degree key ("il 1° maggio",
    /// "arrivato 3°"; Italian keyboards type "°"), with the ordinal's gender; a month name after it counts too. Else
    /// a bare "°" is degrees.
    var ordinalSignBefore: [String: Inflect.Gender] { get }

    // MARK: chords
    var chords: ChordWords.Style { get }
}

extension Pack {
    var spellsNumbers: Bool { true }
    var minus: String { "minus" }
    var plus: String { "plus" }
    var signSpace: String { " " }
    var plainBefore: Set<String> { [] }
    var masculineInA: Set<String> { [] }
    var timeUnit: String? { nil }
    var dotTimeBefore: Set<String> { [] }
    var hTimes: Bool { false }
    var dateWords: Set<String> { [] }
    var dayMonthWords: [String] { [] }
    var rangeDayJoins: [String] { [] }
    var yearPattern: String? { nil }
    var ordinalPattern: String? { nil }
    var ordinalSignBefore: [String: Inflect.Gender] { [:] }
    var unitAliases: [String: MeasureUnit] { [:] }

    func cardinal(_ n: Int, _ gender: Inflect.Gender) -> String { Inflect.gender(Cardinals.words(n, code), gender, code) }

    func number(_ x: Num, _ gender: Inflect.Gender) -> String {
        guard let whole = x.value else { return x.whole + (x.fraction.isEmpty ? "" : "," + x.fraction) }
        if x.fraction.isEmpty { return cardinal(whole, gender) }
        return cardinal(whole, .plain) + " " + decimalMark + " " + decimals(x.fraction)
    }

    /// Decimals: 1 or 2 digits as a number ("cincuenta"), a leading zero or more digits one by one.
    func decimals(_ digits: String) -> String {
        if digits.count <= 2, digits.first != "0", let n = Int(digits) { return cardinal(n, .plain) }
        return said(digits: digits)
    }

    /// Each digit by its name ("0958": "cero nueve cinco ocho").
    func said(digits: String) -> String {
        digits.compactMap(\.wholeNumberValue).map { cardinal($0, .plain) }.joined(separator: " ")
    }

    func plural(_ x: Num) -> Inflect.Plural { Inflect.plural(x.value ?? 1000, fraction: !x.isInteger, code) }

    func dayMonth(_ match: Pattern.Match, _ before: Before) -> String? { nil }
    func rangeStart(_ day: Int, _ joiner: String, _ before: Before) -> String? { nil }
    func genitive(_ x: Num, _ before: Before) -> (words: String, plural: Inflect.Plural)? { nil }

    /// Whether an ordinal written with its sign is feminine: "ª", or "°" after a feminine word ("la 2° volta").
    func feminineOrdinal(_ match: Pattern.Match, sign: String) -> Bool {
        sign == "ª" || sign == "°" && ordinalSignBefore[Before(match.before).last] == .feminine
    }

    func year(_ match: Pattern.Match) -> String? { nil }
    func ordinal(_ match: Pattern.Match) -> String? { nil }
    func unitGender(_ unit: MeasureUnit) -> Inflect.Gender { .masculine }

    /// The phone's groups digit by digit, a comma between them (es, it, pt, nl, de, pl); one long group in threes,
    /// the last two to four digits together.
    func phone(_ plus: Bool, _ groups: [String]) -> String {
        (plus ? self.plus + " " : "") + Reader.split(groups).map { said(digits: $0) }.joined(separator: ", ")
    }

    /// One form of a noun from its forms: [one, many], or pl and ru [one, few, many, fraction].
    func form(_ forms: [String], _ plural: Inflect.Plural) -> String {
        if forms.count == 4 { return forms[[Inflect.Plural.one, .few, .many, .fraction].firstIndex(of: plural)!] }
        return plural == .one ? forms[0] : forms[forms.count - 1]
    }
}

// MARK: - The passes

/// One language's passes with its patterns, compiled once.
final class Reader: @unchecked Sendable {  // immutable after init; NSRegularExpression is safe to share
    let pack: any Pack
    private(set) var passes: [SpokenEnglish.Pass] = []

    private let temperature: Pattern
    private let moneyBefore: Pattern
    private let moneyAfter: Pattern
    private let percent: Pattern
    private let units: Pattern
    /// An amount's sign or unit right after a number: "350 000 €" is no phone.
    private let amountAfter: Pattern
    /// The pack's phone words at a word's start ("Handynummer"; never inside "Immobilie"), anywhere in ja and ko.
    private let phoneWords: Pattern
    private let numbers: Pattern
    private let dayMonth: Pattern?
    private let dayRange: Pattern?
    private let years: Pattern?
    private let ordinals: Pattern?
    private let chords: ChordWords.Speller
    private let months: Set<String>

    init(_ pack: any Pack) {
        self.pack = pack
        let integer: String
        switch pack.groups {
        case .dot:
            integer = #"[0-9]{1,3}(?:\.[0-9]{3})+(?![0-9])|[0-9]{1,3}(?:[   ][0-9]{3})+(?![0-9])"#
                + #"|[0-9]{1,3}(?:,[0-9]{3}){2,}(?![0-9])|[0-9]+"#
        case .space:
            integer = #"[0-9]{1,3}(?:[   ][0-9]{3})+(?![0-9])|[0-9]{1,3}(?:\.[0-9]{3}){2,}(?![0-9])"#
                + #"|[0-9]{1,3}(?:,[0-9]{3}){2,}(?![0-9])|[0-9]+"#
        case .comma: integer = #"[0-9]{1,3}(?:,[0-9]{3})+(?![0-9])|[0-9]+"#
        }
        let mark = pack.groups == .comma ? #"\."# : "[,.]"
        let number = "(" + integer + ")(?:" + mark + "([0-9]+))?"
        // sign, whole, decimals, then a range's end: whole, decimals
        let amount = Reader.sign + number + #"(?:\s?[-–]\s?"# + number + ")?"
        temperature = Pattern(Reader.edge + amount + #"\s?(?:[°º]\s?([CF])(?![\p{Latin}\p{Cyrillic}])|(℃|℉)|°(?![\p{Latin}\p{Cyrillic}\p{N}]))"#)
        let symbols = pack.currencies.keys.filter { !$0.first!.isLetter }.sorted { $0.count > $1.count }
            .map(NSRegularExpression.escapedPattern(for:)).joined(separator: "|")
        let all = pack.currencies.keys.sorted { $0.count > $1.count }
            .map(NSRegularExpression.escapedPattern(for:)).joined(separator: "|")
        let codes = pack.currencies.keys.filter { $0.allSatisfy(\.isUppercase) }.joined(separator: "|")
        moneyBefore = Pattern(Reader.edge + "(" + symbols + (codes.isEmpty ? "" : #"|\b(?:"# + codes + ")") + #")\s?"# + number)
        moneyAfter = Pattern(Reader.edge + number + #"\s?("# + all + #")(?![\p{Latin}\p{Cyrillic}\p{N}])"#)
        percent = Pattern(Reader.edge + amount + #"\s?%"#)
        let aliases = pack.unitAliases.keys.sorted { $0.count > $1.count }.map(NSRegularExpression.escapedPattern(for:))
        units = Pattern(Reader.edge + amount + #"\s?("# + (aliases + ["km/h", "km", "cm", "kg", "m", "g", "l", "L"]).joined(separator: "|")
            + #")(?![\p{Latin}\p{Cyrillic}\p{N}/²³])"#)
        numbers = Pattern(Reader.sign + #"(?<![\p{N}])"# + number)
        phoneWords = Pattern((["ja", "ko"].contains(pack.code) ? "" : #"(?<![\p{L}])"#) + "(?:"
            + pack.phoneWords.map(NSRegularExpression.escapedPattern(for:)).joined(separator: "|") + ")", options: [.caseInsensitive])
        amountAfter = Pattern(#"^\s?(?:"# + (pack.currencies.keys.sorted { $0.count > $1.count } + aliases
            + ["km/h", "km", "cm", "kg", "m", "g", "l", "L", "%", "°", "℃", "℉"]).map(NSRegularExpression.escapedPattern(for:))
            .joined(separator: "|") + #")(?![\p{Latin}\p{Cyrillic}\p{N}])"#)
        dayMonth = pack.dayMonthWords.isEmpty ? nil : Pattern(
            #"(?<![\p{L}\p{N}.,:/])([0-3]?[0-9])\s+(?:de\s+)?("# + pack.dayMonthWords.joined(separator: "|")
                + #")(?![\p{L}])(?:\s+(?:de\s+)?([0-9]{4})(?![0-9]))?(\s+(?:roku|r\.|года|г\.))?"#,
            options: [.caseInsensitive])
        dayRange = pack.rangeDayJoins.isEmpty ? nil : Pattern(
            #"(?<![\p{L}\p{N}.,:/])([0-3]?[0-9])(\s+(?:"# + pack.rangeDayJoins.joined(separator: "|") + #")\s+|\s?[-–]\s?)"#
                + #"(?=[0-3]?[0-9]\s+(?:"# + pack.dayMonthWords.joined(separator: "|") + #")(?![\p{L}]))"#,
            options: [.caseInsensitive])
        years = pack.yearPattern.map { Pattern($0) }
        ordinals = pack.ordinalPattern.map { Pattern($0) }
        chords = ChordWords.Speller(pack.chords)
        months = Set((1...12).map { Cardinals.month($0, pack.code).lowercased() })
        passes = makePasses()
    }

    private func makePasses() -> [SpokenEnglish.Pass] {
        typealias Pass = SpokenEnglish.Pass
        var list = [
            Pass(name: "chords", run: chords.spell),
            Pass(name: "times", run: times),
            Pass(name: "dates", run: dates),
            Pass(name: "phones", run: phones),
            Pass(name: "money, percent, temperatures and units", run: measures),
        ]
        if years != nil { list.append(Pass(name: "years", run: yearsPass)) }
        if ordinals != nil { list.append(Pass(name: "ordinals", run: ordinalsPass)) }
        if pack.spellsNumbers {
            list.append(Pass(name: "ranges", run: ranges))
            list.append(Pass(name: "cardinals and decimals", run: cardinals))
        } else {
            list.append(Pass(name: pack.code == "ja" ? "signs and group commas" : "signs", run: signs))
        }
        return list
    }

    /// A minus or a plus before a number, only at the start, after a space or "(": one group, the sign.
    static let sign = #"(?:(?<![^\s(])([-−+]))?"#
    /// Nothing glued before an amount: a letter, a digit, a dot or a comma.
    static let edge = #"(?<![\p{Latin}\p{Cyrillic}\p{N}.,])"#

    private func signWord(_ sign: String) -> String {
        sign.isEmpty ? "" : (sign == "+" ? pack.plus : pack.minus) + pack.signSpace
    }

    // MARK: times

    /// "14:00-15:00", "13h-15h" (fr, pt): groups hour, minutes, "h", minutes; the same for the end; a time word.
    private static let timeRange = Pattern(
        #"(?<![0-9:.,])([01]?[0-9]|2[0-3])(?::([0-5][0-9])|(h)([0-5][0-9])?)\s?[-–—]\s?([01]?[0-9]|2[0-3])"#
            + #"(?::([0-5][0-9])|(h)([0-5][0-9])?)(?![\p{Latin}\p{Cyrillic}\p{N}]|:[0-9])(\s*(?:uur|Uhr|h)\b)?"#)
    /// H:MM, hour 0 to 23 and two minute digits ("2:1" stays), never inside a longer number or with seconds.
    private static let colonTime = Pattern(
        #"(?<![0-9:.,])([01]?[0-9]|2[0-3]):([0-5][0-9])(?![0-9]|:[0-9])(\s*(?:uur|Uhr|h)\b)?"#)
    /// H.MM, a time only with uur, Uhr or h after it, or after the pack's words (om, um, alle, o, в).
    private static let dotTime = Pattern(
        #"(?<![0-9:.,])([01]?[0-9]|2[0-3])\.([0-5][0-9])(?![0-9]|[.,][0-9])(\s*(?:uur|Uhr|h)\b)?"#)
    /// "13h", "9h30" (fr, pt).
    private static let hTime = Pattern(#"(?<![\p{L}\p{N}:.,])([01]?[0-9]|2[0-3])h([0-5][0-9])?(?![\p{L}\p{N}])"#)

    /// The time word after a time: dropped when the time says it already (nl "uur", de "Uhr"; "h" in every
    /// language: "18:30 h"), else kept.
    private func unitAfter(_ written: String) -> String {
        let word = written.trimmingCharacters(in: .whitespaces)
        return word.isEmpty || word == pack.timeUnit || word == "h" ? "" : written
    }

    func times(_ text: String) -> String {
        var s = Reader.timeRange.rewrite(text) { m in
            if (!m[3].isEmpty || !m[7].isEmpty) && !self.pack.hTimes { return nil }
            let from = (Int(m[1])!, Int(m[2].isEmpty ? (m[4].isEmpty ? "0" : m[4]) : m[2])!)
            let to = (Int(m[5])!, Int(m[6].isEmpty ? (m[8].isEmpty ? "0" : m[8]) : m[6])!)
            return self.pack.clockRange(from, to, Before(m.before)) + self.unitAfter(m[9])
        }
        s = Reader.colonTime.rewrite(s) { m in
            self.pack.clock(Int(m[1])!, Int(m[2])!, Before(m.before)) + self.unitAfter(m[3])
        }
        s = Reader.dotTime.rewrite(s) { m in
            let before = Before(m.before)
            guard !m[3].isEmpty || self.pack.dotTimeBefore.contains(before.last) else { return nil }
            return self.pack.clock(Int(m[1])!, Int(m[2])!, before) + self.unitAfter(m[3])
        }
        guard pack.hTimes else { return s }
        return Reader.hTime.rewrite(s) { m in self.pack.clock(Int(m[1])!, Int(m[2].isEmpty ? "0" : m[2])!, Before(m.before)) }
    }

    // MARK: dates

    /// D/M/YYYY, D.M.YYYY, D-M-YYYY (one mark), then pl's or ru's year word.
    private static let numericDate = Pattern(
        #"(?<![0-9./-])([0-3]?[0-9])([./-])([01]?[0-9])\2([0-9]{4})(?![0-9]|[./-][0-9])(\s+(?:roku|r\.|года|г\.))?"#)
    /// YYYY-MM-DD and YYYY/MM/DD.
    private static let isoDate = Pattern(#"(?<![0-9./-])([0-9]{4})([/-])([01]?[0-9])\2([0-3]?[0-9])(?![0-9]|[./-][0-9])"#)
    /// D.M. with its dot (a dot date needs a four-digit year or a trailing dot).
    private static let dottedDate = Pattern(#"(?<![0-9./-])([0-3]?[0-9])\.([01]?[0-9])\.(?![0-9])"#)
    /// D/M after the pack's date words ("le 1/10"); else "1/2" stays a fraction.
    private static let slashDate = Pattern(#"(?<![0-9./-])([0-3]?[0-9])/([01]?[0-9])(?![0-9]|[./][0-9])"#)
    /// ko months 6 and 10 are 유월 and 시월.
    private static let koreanMonth = Pattern(#"(?<![0-9])(0?6|10)월"#)

    private static func valid(_ day: Int, _ month: Int) -> Bool { (1...31).contains(day) && (1...12).contains(month) }

    func dates(_ text: String) -> String {
        var s = Reader.numericDate.rewrite(text) { m in
            let (day, month) = (Int(m[1])!, Int(m[3])!)
            guard Reader.valid(day, month) else { return nil }
            let kept = ["pl", "ru"].contains(self.pack.code) ? "" : m[5]
            return self.pack.date(day, month, Int(m[4])!, Before(m.before)) + kept
        }
        s = Reader.isoDate.rewrite(s) { m in
            let (day, month) = (Int(m[4])!, Int(m[3])!)
            guard Reader.valid(day, month) else { return nil }
            return self.pack.date(day, month, Int(m[1])!, Before(m.before))
        }
        s = Reader.dottedDate.rewrite(s) { m in
            let (day, month) = (Int(m[1])!, Int(m[2])!)
            guard Reader.valid(day, month) else { return nil }
            // the dot also ends a sentence before a capital or the end
            let ends = m.after.isEmpty || m.after.range(of: #"^\s+\p{Lu}"#, options: .regularExpression) != nil
            return self.pack.date(day, month, nil, Before(m.before)) + (ends ? "." : "")
        }
        s = Reader.slashDate.rewrite(s) { m in
            let (day, month) = (Int(m[1])!, Int(m[2])!)
            let before = Before(m.before)
            guard Reader.valid(day, month), self.pack.dateWords.contains(before.last) else { return nil }
            return self.pack.date(day, month, nil, before)
        }
        if let dayRange {  // the first day of a range; the second is a day-month below
            s = dayRange.rewrite(s) { m in
                guard let day = Int(m[1]), (1...31).contains(day) else { return nil }
                return self.pack.rangeStart(day, m[2], Before(m.before))
            }
        }
        if let dayMonth {
            s = dayMonth.rewrite(s) { m in
                guard let day = Int(m[1]), (1...31).contains(day) else { return nil }
                return self.pack.dayMonth(m, Before(m.before))
            }
        }
        if pack.code == "ko" { s = Reader.koreanMonth.replace(s) { g in g[1] == "10" ? "시월" : "유월" } }
        return s
    }

    // MARK: phones

    /// "+49 30 1234567", "+7 495 123-45-67": 7 to 15 digits (checked in code).
    private static let international = Pattern(
        #"(?<![\p{Latin}\p{Cyrillic}\p{N}+])\+([0-9]{1,3}(?:[ -][0-9]{1,8}){1,6}|[0-9]{7,15})(?![\p{N}]|[.,][0-9])"#)
    /// A leading zero with 8 digits or more ("06 12 34 56 78", "03-1234-5678", "0612345678"), or five pairs with dots
    /// (fr "06.12.34.56.78"; the review, 17 Oct: it was read as decimals).
    private static let leadingZero = Pattern(
        #"(?<![\p{Latin}\p{Cyrillic}\p{N}.,:+/–'-])(0[0-9]{1,4}(?:[ -][0-9]{1,8}){1,6}|0[0-9](?:\.[0-9]{2}){4}|0[0-9]{7,14})"#
            + #"(?![\p{N}]|[.,:/-][0-9])"#)
    /// Groups split by spaces (7 digits or more; the dot-group languages; never from inside a run of groups), or in a
    /// sentence that names a phone, by spaces or dashes (6 or more) or none (7 to 15).
    private static let spaced = Pattern(
        #"(?<![\p{Latin}\p{Cyrillic}\p{N}.,:+/–-])(?<![0-9] )([0-9]{2,4}(?: [0-9]{2,4}){1,4})(?![\p{N}]|[.,:/-][0-9])"#)
    private static let grouped = Pattern(
        #"(?<![\p{Latin}\p{Cyrillic}\p{N}.,:+/–-])([0-9]{1,4}(?:[ -][0-9]{2,4}){1,5}|[0-9]{7,15})(?![\p{N}]|[.,:/-][0-9])"#)

    private static func groups(_ written: String) -> [String] {
        written.split(whereSeparator: { $0 == " " || $0 == "-" || $0 == "." }).map(String.init)
    }

    /// One long group said in threes, the last two to four digits together (as in English).
    static func split(_ groups: [String]) -> [String] {
        guard groups.count == 1, groups[0].count > 4 else { return groups }
        var rest = Substring(groups[0])
        var parts: [String] = []
        while rest.count > 4 {
            parts.append(String(rest.prefix(3)))
            rest = rest.dropFirst(3)
        }
        return parts + [String(rest)]
    }

    /// Thousands in groups of three after spaces ("1 250 000", "12 500 000"): a number, not a phone, when the first
    /// group has one or two digits, the last is "000" or an amount's sign or unit follows; "612 345 678" stays a phone
    /// (the review, 17 Oct: "1 250 000 habitantes" was read digit by digit).
    private func isThousands(_ m: Pattern.Match) -> Bool {
        let groups = m[1].split(separator: " ", omittingEmptySubsequences: false)
        guard groups.count >= 2, (1...3).contains(groups[0].count), groups.dropFirst().allSatisfy({ $0.count == 3 }) else {
            return false
        }
        return groups[0].count <= 2 || groups.last == "000" || amountAfter.matches(m.after)
    }

    /// Whether the match's sentence has a phone word (the review, 17 Oct: "mobil" inside "Immobilie" made a price a
    /// phone).
    private func namesPhone(_ m: Pattern.Match) -> Bool {
        let sentence = (m.before.components(separatedBy: CharacterSet(charactersIn: ".!?。")).last ?? "")
            + " " + (m.after.components(separatedBy: CharacterSet(charactersIn: ".!?。")).first ?? "")
        return phoneWords.matches(sentence)
    }

    /// Two years joined by a dash ("2019-2020", the second later): a range of years, never a phone.
    static func isYearRange(_ first: String, _ second: String) -> Bool {
        guard first.count == 4, second.count == 4, let a = Int(first), let b = Int(second) else { return false }
        return (1000...2999).contains(a) && (1000...2999).contains(b) && a < b
    }

    func phones(_ text: String) -> String {
        var s = Reader.international.rewrite(text) { m in
            let groups = Reader.groups(m[1])
            guard (7...15).contains(groups.joined().count) else { return nil }
            return self.pack.phone(true, groups)
        }
        s = Reader.leadingZero.rewrite(s) { m in
            let groups = Reader.groups(m[1])
            guard groups.joined().count >= 8 else { return nil }
            return self.pack.phone(false, groups)
        }
        if pack.groups == .dot {
            s = Reader.spaced.rewrite(s) { m in
                let groups = Reader.groups(m[1])
                guard groups.joined().count >= 7, !self.isThousands(m) else { return nil }
                return self.pack.phone(false, groups)
            }
        }
        return Reader.grouped.rewrite(s) { m in
            let groups = Reader.groups(m[1])
            let years = groups.count == 2 && m[1].contains("-") && Reader.isYearRange(groups[0], groups[1])
            guard groups.joined().count >= 6, !years, !self.isThousands(m), self.namesPhone(m) else { return nil }
            return self.pack.phone(false, groups)
        }
    }

    // MARK: money, percent, temperatures and units

    /// An amount's words from groups sign, whole, decimals and a range's whole and decimals, and the form its noun
    /// takes (a range: pl and ru the genitive plural, else by its end); nil to leave it as written.
    private func amount(_ g: [String], _ first: Int, _ gender: Inflect.Gender, _ before: Before)
        -> (words: String, plural: Inflect.Plural)? {
        let start = Num(g[first + 1], g[first + 2])
        guard !g[first + 3].isEmpty else {
            if g[first].isEmpty, let genitive = pack.genitive(start, before) { return genitive }  // "do pięciu kilometrów"
            return (signWord(g[first]) + pack.number(start, gender), pack.plural(start))
        }
        let end = Num(g[first + 3], g[first + 4])
        guard g[first].isEmpty, let words = pack.range(start, end, gender) else { return nil }
        return (words, ["pl", "ru"].contains(pack.code) ? .many : pack.plural(end))
    }

    func measures(_ text: String) -> String {
        var s = temperature.rewrite(text) { m in
            if m[6].isEmpty, m[7].isEmpty, self.isOrdinalSign(m) { return nil }  // left to the ordinals
            let scale = m[6].isEmpty ? (m[7] == "℉" ? "F" : m[7] == "℃" ? "C" : "") : m[6]
            if self.pack.code == "ko", m[1] == "-" || m[1] == "−", m[4].isEmpty {  // ko: 영하 3도
                return "영하 " + self.pack.degrees(self.pack.number(Num(m[2], m[3]), .plain), .many, fahrenheit: scale == "F")
            }
            guard let (words, plural) = self.amount(m.groups, 1, .masculine, Before(m.before)) else { return nil }
            return self.pack.degrees(words, plural, fahrenheit: scale == "F")
        }
        s = moneyBefore.rewrite(s) { m in self.money(m[1].trimmingCharacters(in: .whitespaces), m[2], m[3]) }
        s = moneyAfter.rewrite(s) { m in self.money(m[3], m[1], m[2]) }
        s = percent.rewrite(s) { m in
            guard let (words, plural) = self.amount(m.groups, 1, .masculine, Before(m.before)) else { return nil }
            return self.pack.percent(words, plural)
        }
        return units.rewrite(s) { m in
            guard let unit = MeasureUnit(rawValue: m[6] == "L" ? "l" : m[6]) ?? self.pack.unitAliases[m[6]],
                  let (words, plural) = self.amount(m.groups, 1, self.pack.unitGender(unit), Before(m.before)) else {
                return nil
            }
            return self.pack.unit(words, unit, plural)
        }
    }

    /// A bare "°" that is the ordinal sign (es, it, pt; the review, 17 Oct: "Il 1° maggio" was "un grado maggio"): one
    /// or two digits with no sign, decimals or range, after one of the pack's words or before a month name ("1° de
    /// mayo").
    private func isOrdinalSign(_ m: Pattern.Match) -> Bool {
        guard !pack.ordinalSignBefore.isEmpty, m[1].isEmpty, m[3].isEmpty, m[4].isEmpty, m[2].count <= 2 else { return false }
        if pack.ordinalSignBefore[Before(m.before).last] != nil { return true }
        let rest = m.after.drop { $0 == " " }
        guard rest.count < m.after.count else { return false }
        var word = String(rest.prefix(while: \.isLetter)).lowercased()
        if word == "de" || word == "di" {
            word = String(rest.dropFirst(word.count).drop { $0 == " " }.prefix(while: \.isLetter)).lowercased()
        }
        return months.contains(word)
    }

    /// Money split into units and cents: two decimals are the cents, one is tens of cents ("12,5 €": fifty).
    private func money(_ symbol: String, _ whole: String, _ fraction: String) -> String? {
        guard let currency = pack.currencies[symbol] else { return nil }
        guard fraction.count <= 2 else { return nil }
        let cents = fraction.isEmpty ? 0 : Int(fraction.count == 1 ? fraction + "0" : fraction)!
        return pack.money(Num(whole), cents: cents, currency)
    }

    // MARK: years, ordinals, ranges

    func yearsPass(_ text: String) -> String { years!.rewrite(text) { self.pack.year($0) } }

    func ordinalsPass(_ text: String) -> String { ordinals!.rewrite(text) { self.pack.ordinal($0) } }

    /// A bare range "5-10", "5–10" (the first number smaller: "3-1" is a score), never inside a date or a code; two
    /// years ("2019-2020") are left to the numbers, each read as a year with the dash between them.
    private static let bareRange = Pattern(
        #"(?<![\p{L}\p{N}.,:/–-])([0-9]{1,4})\s?[–-]\s?([0-9]{1,4})(?![\p{N}]|[.,:/–-][0-9])"#)

    func ranges(_ text: String) -> String {
        Reader.bareRange.rewrite(text) { m in
            guard let a = Int(m[1]), let b = Int(m[2]), a < b, m[1].first != "0", !Reader.isYearRange(m[1], m[2]) else { return nil }
            // ru "2-3 дня": an end of two to four before a noun keeps the nominative, which "дня" fits ("от двух до трёх"
            // would need "дней"; the review, 17 Oct)
            if self.pack.code == "ru", m.after.first == " ", m.after.dropFirst().first?.isLetter == true,
               self.pack.plural(Num(m[2])) == .few {
                return self.pack.cardinal(a, .plain) + "-" + self.pack.cardinal(b, .plain)
            }
            return self.pack.range(Num(m[1]), Num(m[2]), .plain)
        }
    }

    // MARK: cardinals and decimals

    /// "+" between digits and "#" before one.
    private static let plusBetween = Pattern(#"(?<=[0-9])\s*\+\s*(?=[0-9])"#)
    private static let numberSign = Pattern(#"(?<![\p{Latin}\p{Cyrillic}\p{N}&#])#\s?(?=[0-9])"#)

    func cardinals(_ text: String) -> String {
        var s = Reader.plusBetween.replace(text) { _ in " " + self.pack.plus + " " }
        s = Reader.numberSign.replace(s) { _ in self.pack.numberSign + " " }
        return numbers.rewrite(s) { m in
            let x = Num(m[2], m[3])
            let grouped = m[2].contains { !$0.isNumber }
            var words: String
            if x.isInteger, !grouped, m[2].count > 1, m[2].first == "0" {
                words = self.pack.said(digits: m[2])  // "007", "05": digit by digit
            } else if m[1].isEmpty, let genitive = self.pack.genitive(x, Before(m.before)) {
                words = genitive.words  // pl and ru "od pięciu lat", "после пяти лет"
            } else {
                words = self.signWord(m[1]) + self.pack.number(x, self.genderBefore(m.after))
            }
            if m.before.last?.isLetter == true { words = " " + words }  // "A320": "A trescientos veinte"
            // de decades keep their ending glued ("20er": "zwanziger"; the review, 17 Oct)
            let decade = self.pack.code == "de" && m.after.hasPrefix("er") && m.after.dropFirst(2).first?.isLetter != true
            if m.after.first?.isLetter == true, !decade { words += " " }
            return words
        }
    }

    /// The gender of the noun after a number, from its ending (the review, 17 Oct: "1 hija" was "un hija"): es, it
    /// and pt a word in -a or -as is feminine but the pack's exceptions ("día", "problema"), de a capitalised noun in
    /// -e, -ung, -heit, -keit, -schaft, -ion, -tät, -ik or -ei ("eine Stunde"; "ein Tag"); never before a word the pack
    /// keeps the plain form for ("1 y 2"). A guess: "eine Name" is a known gap.
    private func genderBefore(_ after: String) -> Inflect.Gender {
        guard ["es", "it", "pt", "de"].contains(pack.code), after.first == " " else { return .plain }
        let word = String(after.dropFirst().prefix { $0.isLetter })
        guard let first = word.first, !pack.plainBefore.contains(word.lowercased()) else { return .plain }
        if pack.code == "de" {
            guard first.isUppercase else { return .plain }
            let feminine = ["e", "ung", "heit", "keit", "schaft", "ion", "tät", "ik", "ei"].contains { word.hasSuffix($0) }
            return feminine ? .feminine : .masculine
        }
        guard first.isLowercase else { return .plain }
        let feminine = (word.hasSuffix("a") || word.hasSuffix("as")) && !pack.masculineInA.contains(word)
        return feminine ? .feminine : .masculine
    }

    // MARK: ja and ko: signs, and ja's group commas

    private static let groupCommas = Pattern(#"(?<![0-9.,])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{5,12})(?![0-9]|[.,][0-9])"#)
    private static let signed = Pattern(#"(?<![\p{Latin}\p{N}])([-−+])(?=[0-9])"#)

    func signs(_ text: String) -> String {
        var s = Reader.plusBetween.replace(text) { _ in self.pack.signSpace + self.pack.plus + self.pack.signSpace }
        s = Reader.signed.replace(s) { g in (g[1] == "+" ? self.pack.plus : self.pack.minus) + self.pack.signSpace }
        s = Reader.numberSign.replace(s) { _ in self.pack.numberSign }
        guard pack.code == "ja" else { return s }
        // ja: the commas dropped, and 10,000 and over in 万 and 億 (the 17 Oct listen: "1234567人" was misread,
        // "123万4567人" heard right); a number after 番号 is an ID and keeps its digits ("注文番号12345678")
        return Reader.groupCommas.rewrite(s) { m in
            m.before.hasSuffix("番号") ? nil : Reader.man(m[1].filter(\.isNumber))
        }
    }

    /// A ja number from 10,000 up written with 万 and 億 ("1234567": "123万4567"); smaller ones as digits.
    static func man(_ digits: String) -> String {
        guard let n = Int(digits), n >= 10_000 else { return digits }
        let (oku, rest) = n.quotientAndRemainder(dividingBy: 100_000_000)
        let (man, ones) = rest.quotientAndRemainder(dividingBy: 10_000)
        return (oku > 0 ? "\(oku)億" : "") + (man > 0 ? "\(man)万" : "") + (ones > 0 ? "\(ones)" : "")
    }
}
