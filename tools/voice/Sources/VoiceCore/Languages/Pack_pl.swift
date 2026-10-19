import Foundation

/// Polish: hours are feminine ordinals ("trzynasta", "o trzynastej", "przed dziewiątą"); a date's day a genitive
/// ordinal, nominative within three words after to, dzisiaj, dziś, jutro or wczoraj or right after a weekday, both
/// days of a range genitive ("od pierwszego do trzeciego listopada"); the year in a date with its last part in the
/// genitive, then "roku"; "N roku" alone locative after "w", else genitive; plurals one, few and many ("pięć
/// złotych", "trzy stopnie"); a number after od, do or około in the genitive, to 99 ("od pięciu do dziesięciu
/// kilometrów"); chords with their letters, minor in lower case ("cis-moll").
struct Polish: Pack {
    let code = "pl"
    var groups: Groups { .space }
    var numberSign: String { "numer" }
    var decimalMark: String { "przecinek" }
    var dotTimeBefore: Set<String> { ["o", "od", "do", "po", "około", "przed", "za"] }

    // MARK: times

    /// The case an hour takes after the word before it; "a" after "między" too ("między trzynastą a czternastą").
    private func hourForm(_ before: Before) -> Inflect.PolishForm {
        if ["o", "od", "do", "po", "około"].contains(before.last) { return .feminineGenitive }
        if ["przed", "za", "między"].contains(before.last) { return .feminineInstrumental }
        if before.last == "a", before.words.dropFirst().first == "między" { return .feminineInstrumental }
        return .feminineNominative
    }

    private func hour(_ hour: Int, _ minute: Int, _ form: Inflect.PolishForm) -> String {
        let said = hour == 0 ? "zero" : Inflect.polish(hour, form)!
        return said + (minute == 0 ? "" : (minute < 10 ? " zero " : " ") + cardinal(minute, .plain))
    }

    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String { self.hour(hour, minute, hourForm(before)) }

    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String {
        (before.last == "od" ? "" : "od ") + hour(from.0, from.1, .feminineGenitive) + " do " + hour(to.0, to.1, .feminineGenitive)
    }

    // MARK: dates

    var dateWords: Set<String> { ["dnia", "dn"] }
    var dayMonthWords: [String] { (1...12).map { Cardinals.month($0, code) } }

    /// A day is nominative within three words after these ("Dzisiaj jest 1 maja": "pierwszy"), or right after a
    /// weekday ("jest piątek, 16.10.2026": "szesnasty"); else genitive, also after "jest" alone ("Wizyta u dentysty
    /// jest 3 listopada": "trzeciego"; the live audit, 17 Oct).
    private static let nominative: Set<String> = ["to", "dzisiaj", "dziś", "jutro", "wczoraj"]
    private static let weekdays: Set<String> = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "pl_PL")
        return Set(formatter.weekdaySymbols.map { $0.lowercased() })
    }()

    private func day(_ day: Int, _ before: Before) -> String {
        let nominative = before.within(3, Polish.nominative) || Polish.weekdays.contains(before.last)
        return Inflect.polish(day, nominative ? .masculineNominative : .masculineGenitive)!
    }

    private func year(_ year: Int) -> String {
        (Inflect.polishYear(year, .masculineGenitive) ?? cardinal(year, .plain)) + " roku"
    }

    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String {
        self.day(day, before) + " " + Cardinals.month(month, code) + (year.map { " " + self.year($0) } ?? "")
    }

    /// "od 1 do 3 listopada" and "1-3 listopada": "od pierwszego do trzeciego listopada" (the review, 17 Oct).
    var rangeDayJoins: [String] { ["do"] }

    func rangeStart(_ day: Int, _ joiner: String, _ before: Before) -> String? {
        let start = Inflect.polish(day, .masculineGenitive)!
        guard joiner.contains(where: { $0 == "-" || $0 == "–" }) else { return start + joiner }
        return (before.last == "od" ? "" : "od ") + start + " do "
    }

    /// "3 listopada": "trzeciego listopada"; a year after it with "roku" (a written "roku" or "r." is said once).
    func dayMonth(_ match: Pattern.Match, _ before: Before) -> String? {
        guard let n = Int(match[1]) else { return nil }
        return day(n, before) + " " + match[2] + (Int(match[3]).map { " " + year($0) } ?? match[4])
    }

    // MARK: years

    var yearPattern: String? { #"(?<![\p{L}\p{N}.,:/-])([0-9]{1,4})\s+(roku|rok|r\.)(?![\p{L}])"# }

    /// "w 1987 roku": locative after "w", else genitive; "1987 rok" nominative. One or two digits are a year only
    /// before "roku" ("w 21 roku"); before "rok" or "r." they are a count, read by the numbers ("na 1 rok": "jeden
    /// rok"; the review, 17 Oct).
    func year(_ match: Pattern.Match) -> String? {
        guard let n = Int(match[1]), match[1].count >= 3 || match[2] == "roku" else { return nil }
        if match[2] == "rok" { return Inflect.polishYear(n, .masculineNominative).map { $0 + " rok" } }
        let form: Inflect.PolishForm = Before(match.before).last == "w" ? .masculineLocative : .masculineGenitive
        return Inflect.polishYear(n, form).map { $0 + " roku" }
    }

    // MARK: phones

    var phoneWords: [String] { ["telefon", "komórk", "zadzwoń", "dzwoń"] }

    // MARK: money, percent, temperatures and units

    var currencies: [String: Currency] {
        ["€": .euro, "EUR": .euro, "$": .dollar, "USD": .dollar, "zł": .zloty, "PLN": .zloty]
    }

    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String {
        let (nouns, centNouns): ([String], [String])
        switch currency {
        case .zloty: (nouns, centNouns) = (["złoty", "złote", "złotych", "złotego"], ["grosz", "grosze", "groszy", "grosza"])
        case .dollar: (nouns, centNouns) = (["dolar", "dolary", "dolarów", "dolara"], ["cent", "centy", "centów", "centa"])
        default: (nouns, centNouns) = (["euro", "euro", "euro", "euro"], ["cent", "centy", "centów", "centa"])
        }
        let centWords = cardinal(cents, .plain) + (currency == .euro ? "" : " " + form(centNouns, plural(Num(String(cents)))))
        if whole.value == 0, cents > 0 { return cardinal(cents, .plain) + " " + form(centNouns, plural(Num(String(cents)))) }
        let units = number(whole, currency == .euro ? .neuter : .masculine)
        return units + " " + form(nouns, plural(whole)) + (cents > 0 ? " " + centWords : "")
    }

    func percent(_ amount: String, _ plural: Inflect.Plural) -> String { amount + " procent" }

    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String {
        amount + " " + form(["stopień", "stopnie", "stopni", "stopnia"], plural) + (fahrenheit ? " Fahrenheita" : "")
    }

    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String {
        let words: [MeasureUnit: [String]] = [
            .kilometer: ["kilometr", "kilometry", "kilometrów", "kilometra"],
            .meter: ["metr", "metry", "metrów", "metra"],
            .centimeter: ["centymetr", "centymetry", "centymetrów", "centymetra"],
            .kilogram: ["kilogram", "kilogramy", "kilogramów", "kilograma"],
            .gram: ["gram", "gramy", "gramów", "grama"],
            .liter: ["litr", "litry", "litrów", "litra"],
            .kilometerPerHour: ["kilometr na godzinę", "kilometry na godzinę", "kilometrów na godzinę", "kilometra na godzinę"],
        ]
        return amount + " " + form(words[unit]!, plural)
    }

    /// Words a number takes the genitive after ("od pięciu do dziesięciu kilometrów", "do jednego kilometra").
    private static let genitiveBefore: Set<String> = ["od", "do", "około", "bez", "dla", "powyżej", "poniżej"]

    /// The genitive to 99; its noun the genitive singular after 1, else the genitive plural.
    func genitive(_ x: Num, _ before: Before) -> (words: String, plural: Inflect.Plural)? {
        guard Polish.genitiveBefore.contains(before.last), x.isInteger, let n = x.value,
              let words = Inflect.polishGenitive(n) else { return nil }
        return (words, n == 1 ? .fraction : .many)
    }

    /// "od pięciu do dziesięciu": genitive cardinals to 99 (over 99 deferred: as written).
    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String? {
        guard from.isInteger, to.isInteger, let a = from.value.flatMap(Inflect.polishGenitive),
              let b = to.value.flatMap(Inflect.polishGenitive) else { return nil }
        return "od " + a + " do " + b
    }

    // MARK: chords

    var chords: ChordWords.Style {
        ChordWords.Style(roots: .german(lowerMinor: true), sharp: "is", flat: "es", minor: "moll", seventh: "siedem",
                         majorSeventh: "major siedem", joins: ["i", "oraz", "lub", "albo"], words: ["Hm"], dash: ", ",
                         music: ["akord", "piosenk", "gitar", "pianin", "fortepian", "ukulele", "tonacj",
                                 "kapodaster", "zwrotk", "refren", "melodi", "muzyk", "utwór", "utworu", "intro",
                                 "riff"],
                         forms: ChordWords.Forms(sharp: "krzyżyk", flat: "bemol", noThird: "bez tercji"),
                         marks: ChordWords.Marks(noChord: "bez akordu", again: "powtórzyć") { [self] n in cardinal(n, .masculine) + " razy" })
    }
}
