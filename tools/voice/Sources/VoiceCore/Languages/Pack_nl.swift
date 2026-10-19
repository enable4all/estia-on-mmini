import Foundation

/// Dutch: "N uur M" (never "half tien"), "van veertien tot vijftien uur"; days cardinal; 1 is "één"; ordinals -ste
/// for 1, 8 and from 20, -de otherwise; years 1100 to 1999 in hundreds and 2001 to 2099 "tweeduizend", a space, the
/// rest, only after a year word, a month or "("; chords with -is and -es ("Fis mineur", "Bes").
struct Dutch: Pack {
    let code = "nl"
    var groups: Groups { .dot }
    var minus: String { "min" }
    var numberSign: String { "nummer" }
    var decimalMark: String { "komma" }
    var timeUnit: String? { "uur" }
    var dotTimeBefore: Set<String> { ["om", "tot", "vanaf", "rond", "omstreeks"] }

    // MARK: times

    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String {
        cardinal(hour, .plain) + " uur" + (minute == 0 ? "" : " " + cardinal(minute, .plain))
    }

    /// "van veertien tot vijftien uur": a start on the hour says no "uur"; after "tussen", "en" ("tussen veertien en
    /// vijftien uur").
    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String {
        let opener = ["van", "vanaf", "tussen"].contains(before.last) ? "" : "van "
        let start = from.1 == 0 ? cardinal(from.0, .plain) : clock(from.0, from.1, before)
        return opener + start + (before.last == "tussen" ? " en " : " tot ") + clock(to.0, to.1, before)
    }

    // MARK: dates

    var dateWords: Set<String> { ["op", "van", "tot", "per", "vanaf", "sinds"] }

    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String {
        cardinal(day, .plain) + " " + Cardinals.month(month, code) + (year.map { " " + Dutch.year($0) } ?? "")
    }

    // MARK: phones

    var phoneWords: [String] { ["telefoon", "mobiel", "bel me", "bellen"] }

    // MARK: money, percent, temperatures and units

    var currencies: [String: Currency] { ["€": .euro, "EUR": .euro, "$": .dollar, "USD": .dollar] }

    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String {
        if whole.value == 0, cents > 0 { return cardinal(cents, .plain) + " cent" }
        let noun = currency == .euro ? "euro" : "dollar"
        return number(whole, .plain) + " " + noun + (cents > 0 ? " " + cardinal(cents, .plain) : "")
    }

    func percent(_ amount: String, _ plural: Inflect.Plural) -> String { amount + " procent" }

    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String {
        amount + " " + form(["graad", "graden"], plural) + (fahrenheit ? " Fahrenheit" : "")
    }

    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String {
        let words: [MeasureUnit: String] = [
            .kilometer: "kilometer", .meter: "meter", .centimeter: "centimeter", .kilogram: "kilogram", .gram: "gram",
            .liter: "liter", .kilometerPerHour: "kilometer per uur",
        ]
        return amount + " " + words[unit]!
    }

    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String? {
        "van " + number(from, gender) + " tot " + number(to, gender)
    }

    // MARK: years and ordinals

    var yearPattern: String? { #"(?<![\p{L}\p{N}.,:/+#-])(1[0-9]{3}|20[0-9]{2})(?![\p{N}]|[.,:][0-9])"# }

    /// The words a year follows (a month name too, or "("); "1998 mensen" is a number.
    private static let yearWords: Set<String> = [
        "in", "sinds", "vanaf", "van", "tot", "uit", "rond", "omstreeks", "na", "voor", "vóór", "jaar", "begin", "eind",
        "medio", "zomer", "winter", "lente", "voorjaar", "herfst", "najaar", "anno",
    ]

    func year(_ match: Pattern.Match) -> String? {
        guard let n = Int(match[1]) else { return nil }
        let before = Before(match.before)
        let months = Set((1...12).map { Cardinals.month($0, code) })
        guard Dutch.yearWords.contains(before.last) || months.contains(before.last) || match.before.hasSuffix("(") else {
            return nil
        }
        return Dutch.year(n)
    }

    /// 1100 to 1999 in hundreds ("negentienhonderdachtennegentig"), 2001 to 2099 "tweeduizend", a space, the rest.
    static func year(_ n: Int) -> String {
        let rest = n % 100
        if (1100...1999).contains(n) { return Cardinals.words(n / 100, "nl") + "honderd" + (rest == 0 ? "" : Cardinals.words(rest, "nl")) }
        if (2001...2099).contains(n) { return "tweeduizend " + Cardinals.words(rest, "nl") }
        return Cardinals.words(n, "nl")
    }

    var ordinalPattern: String? { #"(?<![\p{L}\p{N}.,])([0-9]{1,3})(ste|de|e)(?![\p{L}\p{N}])"# }

    func ordinal(_ match: Pattern.Match) -> String? { Int(match[1]).flatMap(Inflect.dutch) }

    // MARK: chords

    var chords: ChordWords.Style {
        ChordWords.Style(roots: .dutch, sharp: "is", flat: "es", minor: "mineur", seventh: "zeven",
                         majorSeventh: "majeur zeven", joins: ["en", "of"], words: [], dash: ", ",
                         music: ["akkoorden", "liedje", "lied", "gitaar", "piano", "ukelele", "ukulele", "toonsoort",
                                 "capo", "couplet", "refrein", "melodie", "muziek", "intro", "riff"],
                         forms: ChordWords.Forms(sharp: "kruis", flat: "mol", noThird: "zonder terts"),
                         marks: ChordWords.Marks(noChord: "geen akkoord", again: "herhalen") { [self] n in cardinal(n, .plain) + " keer" })
    }
}
