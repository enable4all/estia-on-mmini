import Foundation

/// German: "N Uhr M" with "ein Uhr", "Uhr" never said twice, "vierzehn bis fünfzehn Uhr"; "N." an ordinal only
/// before a month or a capitalised noun, or paired with one ("am 1. und 2. Mai"), its ending by the word before (-e,
/// -en, else -er); years 1100 to 1999 in hundreds unless a capitalised noun follows; "N Euro M"; chords with their
/// letters ("H-Moll", "Cis-Moll"; English B is H in a run with sharps or flats and no H).
struct German: Pack {
    let code = "de"
    var groups: Groups { .dot }
    var numberSign: String { "Nummer" }
    var decimalMark: String { "Komma" }
    var timeUnit: String? { "Uhr" }
    var dotTimeBefore: Set<String> { ["um"] }

    // MARK: times

    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String {
        cardinal(hour, .masculine) + " Uhr" + (minute == 0 ? "" : " " + cardinal(minute, .plain))
    }

    /// "vierzehn bis fünfzehn Uhr": a start on the hour says no "Uhr"; "von" stays the writer's.
    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String {
        (from.1 == 0 ? cardinal(from.0, .plain) : clock(from.0, from.1, before)) + " bis " + clock(to.0, to.1, before)
    }

    // MARK: dates and ordinals

    var dateWords: Set<String> { ["am", "vom", "zum", "bis", "ab", "den", "der", "seit"] }

    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String {
        Inflect.germanStem(day)! + German.ending(before) + " " + Cardinals.month(month, code)
            + (year.map { " " + German.year($0) } ?? "")
    }

    /// -e after der, die, das; -en after am, im, zum, vom, den, dem, einem, einen, ab, bis, seit; otherwise -er
    /// (a neuter noun after ein or mein is a known gap: "dein 21. Jahr" gets -er).
    static func ending(_ before: Before) -> String {
        if ["der", "die", "das"].contains(before.last) { return "e" }
        if ["am", "im", "zum", "zur", "vom", "den", "dem", "einem", "einen", "ab", "bis", "seit", "beim"].contains(before.last) {
            return "en"
        }
        return "er"
    }

    /// "3. November", "21. Geburtstag": a dot after one or two digits, then a capitalised word; or the first of a pair
    /// joined by und, oder or bis ("am 1. und 2. Mai", "vom 1. bis 3. Oktober"; the review, 17 Oct).
    var ordinalPattern: String? {
        #"(?<![\p{L}\p{N}.,:/])([0-9]{1,2})\.(?=\s+(\p{Lu}\p{L}*)|\s+(?:und|oder|bis)\s+[0-9]{1,2}\.\s+\p{Lu})"#
    }

    /// Capitalised words that start a sentence and are no noun: the dot before them ended the sentence.
    private static let starters: Set<String> = [
        "Der", "Die", "Das", "Den", "Dem", "Des", "Ein", "Eine", "Einen", "Einem", "Einer", "Es", "Er", "Sie", "Wir",
        "Ihr", "Ich", "Du", "Und", "Oder", "Aber", "Dann", "Danach", "Doch", "Also", "So", "Wenn", "Weil", "Da", "Dort",
        "Hier", "Heute", "Morgen", "Gestern", "Jetzt", "Bitte", "Danke", "Ja", "Nein", "Am", "Im", "Um", "In", "Mit",
        "Auf", "Bei", "Bis", "Von", "Vom", "Zum", "Zur", "Nach", "Vor", "Seit", "Ab", "Für", "Was", "Wie", "Wo", "Wann",
        "Warum", "Wer", "Mein", "Dein", "Sein", "Unser", "Kein", "Nicht", "Noch", "Nur", "Auch", "Schon", "Gut", "Okay",
        "Alles", "Viel", "Viele", "Damit", "Dazu", "Deshalb", "Trotzdem", "Sonst", "Zuerst", "Zuletzt", "Außerdem",
    ]

    func ordinal(_ match: Pattern.Match) -> String? {
        guard let n = Int(match[1]), let stem = Inflect.germanStem(n), !German.starters.contains(match[2]) else { return nil }
        return stem + German.ending(German.pairBefore(match.before))
    }

    /// The words before an ordinal, or for the second of a pair the words before the first ("am 1. und 2. Mai":
    /// "am"), so both take one ending.
    static func pairBefore(_ before: String) -> Before {
        guard let pair = before.range(of: #"[0-9]{1,2}\.\s+(?:und|oder|bis)\s+$"#, options: .regularExpression) else {
            return Before(before)
        }
        return Before(String(before[..<pair.lowerBound]))
    }

    // MARK: years

    var yearPattern: String? { #"(?<![\p{L}\p{N}.,:/+#-])(1[1-9][0-9]{2})(?![\p{N}]|[.,:][0-9])"# }

    /// Words a year follows whatever comes after it ("seit 1987 Mitglied").
    private static let yearWords: Set<String> = ["jahr", "jahre", "jahres", "seit", "im", "anno", "ab", "bis", "von", "vor", "nach"]

    /// 1100 to 1999 in hundreds, unless a capitalised noun follows ("1987 Einwohner" is a number); a unit follows as
    /// its word, read before this.
    func year(_ match: Pattern.Match) -> String? {
        guard let n = Int(match[1]) else { return nil }
        let next = match.after.drop { $0 == " " }
        let noun = match.after.first == " " && next.first?.isUppercase == true
        guard !noun || German.yearWords.contains(Before(match.before).last) else { return nil }
        return German.year(n)
    }

    static func year(_ n: Int) -> String {
        guard (1100...1999).contains(n) else { return Cardinals.words(n, "de") }
        let rest = n % 100
        return Cardinals.words(n / 100, "de") + "hundert" + (rest == 0 ? "" : Cardinals.words(rest, "de"))
    }

    // MARK: phones

    var phoneWords: [String] { ["telefon", "handy", "anruf", "rufnummer", "mobilnummer", "mobiltelefon", "festnetz"] }

    // MARK: money, percent, temperatures and units

    var currencies: [String: Currency] { ["€": .euro, "EUR": .euro, "$": .dollar, "USD": .dollar] }

    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String {
        if whole.value == 0, cents > 0 { return cardinal(cents, .plain) + " Cent" }
        let noun = currency == .euro ? "Euro" : "Dollar"
        return number(whole, .masculine) + " " + noun + (cents > 0 ? " " + cardinal(cents, .plain) : "")
    }

    func percent(_ amount: String, _ plural: Inflect.Plural) -> String { amount + " Prozent" }

    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String {
        amount + " Grad" + (fahrenheit ? " Fahrenheit" : "")
    }

    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String {
        let words: [MeasureUnit: String] = [
            .kilometer: "Kilometer", .meter: "Meter", .centimeter: "Zentimeter", .kilogram: "Kilogramm", .gram: "Gramm",
            .liter: "Liter", .kilometerPerHour: "Kilometer pro Stunde",
        ]
        return amount + " " + words[unit]!
    }

    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String? {
        number(from, .plain) + " bis " + number(to, gender)
    }

    // MARK: chords

    var chords: ChordWords.Style {
        ChordWords.Style(roots: .german(lowerMinor: false), sharp: "is", flat: "es", minor: "Moll", seventh: "sieben",
                         majorSeventh: "Major sieben", joins: ["und", "oder"], words: ["Am", "Ab", "Hm"], dash: ", ",
                         music: ["akkord", "lied", "song", "gitarre", "klavier", "piano", "ukulele", "tonart",
                                 "kapodaster", "strophe", "refrain", "melodie", "musik", "intro", "riff"],
                         forms: ChordWords.Forms(sharp: "Kreuz", flat: "b", noThird: "ohne Terz"),
                         marks: ChordWords.Marks(noChord: "kein Akkord", again: "wiederholen") { [self] n in cardinal(n, .plain) + "mal" })
    }
}
