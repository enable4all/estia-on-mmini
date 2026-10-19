import Foundation

/// Italian: "alle nove e trenta", "l'una", "dalle A alle B"; day 1 "primo"; "N euro e M"; ordinals "terzo" and, from
/// 11, the cardinal with -esimo; chords in solfège ("la minore", "re settima").
struct Italian: Pack {
    let code = "it"
    var groups: Groups { .dot }
    var minus: String { "meno" }
    var plus: String { "più" }
    var numberSign: String { "numero" }
    var decimalMark: String { "virgola" }
    var masculineInA: Set<String> {
        ["problema", "programma", "sistema", "tema", "clima", "pianeta", "poema", "dramma", "cinema", "papa", "pigiama", "pilota", "collega", "atleta", "artista", "turista"]
    }
    var plainBefore: Set<String> { ["e", "ed", "o", "di", "a", "da", "in", "con", "su", "per", "tra", "fra", "meno", "più"] }
    var dotTimeBefore: Set<String> { ["alle", "dalle", "ore", "le"] }

    // MARK: times

    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String {
        cardinal(hour, .feminine) + (minute == 0 ? "" : " e " + cardinal(minute, .plain))
    }

    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String {
        let opener = ["dalle", "dall'", "da", "tra", "fra"].contains(before.last) ? "" : from.0 == 1 ? "dall'" : "dalle "
        return opener + clock(from.0, from.1, before) + (to.0 == 1 ? " all'" : " alle ") + clock(to.0, to.1, before)
    }

    // MARK: dates

    var dateWords: Set<String> { ["il", "del", "al", "dal"] }
    var dayMonthWords: [String] { (1...12).map { Cardinals.month($0, code) } }

    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String {
        (day == 1 ? "primo" : cardinal(day, .plain)) + " " + Cardinals.month(month, code)
            + (year.map { " " + cardinal($0, .plain) } ?? "")
    }

    func dayMonth(_ match: Pattern.Match, _ before: Before) -> String? {
        match[1] == "1" ? "primo" + match[0].drop(while: \.isNumber) : nil
    }

    // MARK: phones

    var phoneWords: [String] { ["telefono", "cellulare", "chiamami", "chiamare"] }

    // MARK: money, percent, temperatures and units

    var currencies: [String: Currency] { ["€": .euro, "EUR": .euro, "$": .dollar, "USD": .dollar] }

    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String {
        let nouns = currency == .euro ? ["euro", "euro"] : ["dollaro", "dollari"]
        if whole.value == 0, cents > 0 { return cardinal(cents, .masculine) + (cents == 1 ? " centesimo" : " centesimi") }
        let units = number(whole, .masculine)
        let millions = ["milione", "milioni", "miliardo", "miliardi"].contains { units.hasSuffix($0) }
        return units + (millions ? " di " : " ") + form(nouns, plural(whole)) + (cents > 0 ? " e " + cardinal(cents, .plain) : "")
    }

    func percent(_ amount: String, _ plural: Inflect.Plural) -> String { amount + " per cento" }

    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String {
        amount + " " + form(["grado", "gradi"], plural) + (fahrenheit ? " Fahrenheit" : "")
    }

    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String {
        let words: [MeasureUnit: [String]] = [
            .kilometer: ["chilometro", "chilometri"], .meter: ["metro", "metri"], .centimeter: ["centimetro", "centimetri"],
            .kilogram: ["chilogrammo", "chilogrammi"], .gram: ["grammo", "grammi"], .liter: ["litro", "litri"],
            .kilometerPerHour: ["chilometro orario", "chilometri orari"],
        ]
        return amount + " " + form(words[unit]!, plural)
    }

    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String? {
        "da " + number(from, gender) + " a " + number(to, gender)
    }

    // MARK: ordinals

    /// 3º, 1ª; "1°" where the degrees left it (`ordinalSignBefore`).
    var ordinalPattern: String? { #"(?<![\p{L}\p{N}.,])([0-9]{1,2})(º|ª|°)(?![\p{L}\p{N}])"# }

    var ordinalSignBefore: [String: Inflect.Gender] {
        let masculine = ["il", "lo", "l'", "al", "allo", "all'", "del", "dello", "dell'", "dal", "nel", "nell'", "sul", "un",
                         "arrivato", "classificato", "piazzato", "giunto"]
        let feminine = ["la", "alla", "della", "dalla", "nella", "sulla", "una", "arrivata", "classificata", "piazzata", "giunta"]
        return Dictionary(uniqueKeysWithValues: masculine.map { ($0, Inflect.Gender.masculine) } + feminine.map { ($0, .feminine) })
    }

    func ordinal(_ match: Pattern.Match) -> String? {
        Int(match[1]).flatMap { Inflect.italian($0, feminine: feminineOrdinal(match, sign: match[2])) }
    }

    // MARK: chords

    var chords: ChordWords.Style {
        ChordWords.Style(roots: .solfege(["do", "re", "mi", "fa", "sol", "la", "si"]), sharp: "diesis", flat: "bemolle",
                         minor: "minore", seventh: "settima", majorSeventh: "settima maggiore", joins: ["e", "ed", "o"],
                         words: ["Dom", "Fam", "Lab"], dash: ", ",
                         music: ["accordi", "canzon", "chitarr", "pianoforte", "ukulele", "tonalità", "capotasto",
                                 "progressione", "strof", "ritornello", "spartit", "melodi", "music", "intro",
                                 "riff"],
                         forms: ChordWords.Forms(sharp: "diesis", flat: "bemolle", noThird: "senza terza"),
                         marks: ChordWords.Marks(noChord: "senza accordo", again: "ripetere") { [self] n in cardinal(n, .feminine) + " volte" })
    }
}
