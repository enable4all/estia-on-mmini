import Foundation

/// Spanish (Spain): hours feminine ("a la una", "veintiuna"), minutes after "y"; "N euros con M"; ordinals from the
/// table (1.º, 1.ª, 3.er); chords in solfège ("la menor", "fa sostenido").
struct Spanish: Pack {
    let code = "es"
    var groups: Groups { .dot }
    var minus: String { "menos" }
    var plus: String { "más" }
    var numberSign: String { "número" }
    var decimalMark: String { "coma" }
    var masculineInA: Set<String> {
        ["día", "días", "mapa", "mapas", "problema", "problemas", "programa", "programas", "sistema", "sistemas", "tema", "temas", "idioma", "idiomas", "clima", "planeta", "planetas", "sofá", "poema", "poemas", "drama", "dramas", "esquema", "dilema", "cometa", "papa"]
    }
    var plainBefore: Set<String> {
        ["y", "e", "o", "u", "a", "al", "de", "del", "en", "por", "con", "para", "entre", "hasta", "sobre", "sin", "menos", "más"]
    }

    // MARK: times

    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String {
        cardinal(hour, .feminine) + (minute == 0 ? "" : " y " + cardinal(minute, .plain))
    }

    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String {
        let opener = ["de", "desde", "las", "la", "entre"].contains(before.last) ? "" : "de "
        return opener + clock(from.0, from.1, before) + " a " + clock(to.0, to.1, before)
    }

    // MARK: dates

    var dateWords: Set<String> { ["el", "del", "al"] }

    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String {
        cardinal(day, .plain) + " de " + Cardinals.month(month, code) + (year.map { " de " + cardinal($0, .plain) } ?? "")
    }

    // MARK: phones

    var phoneWords: [String] { ["teléfono", "móvil", "llámame", "llamar"] }

    // MARK: money, percent, temperatures and units

    var currencies: [String: Currency] { ["€": .euro, "EUR": .euro, "$": .dollar, "USD": .dollar] }

    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String {
        let nouns = currency == .euro ? ["euro", "euros"] : ["dólar", "dólares"]
        if whole.value == 0, cents > 0 { return cardinal(cents, .masculine) + (cents == 1 ? " céntimo" : " céntimos") }
        let units = number(whole, .masculine)
        let of = units.hasSuffix("millón") || units.hasSuffix("millones") ? " de " : " "
        return units + of + form(nouns, plural(whole)) + (cents > 0 ? " con " + cardinal(cents, .plain) : "")
    }

    func percent(_ amount: String, _ plural: Inflect.Plural) -> String { amount + " por ciento" }

    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String {
        amount + " " + form(["grado", "grados"], plural) + (fahrenheit ? " Fahrenheit" : "")
    }

    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String {
        let words: [MeasureUnit: [String]] = [
            .kilometer: ["kilómetro", "kilómetros"], .meter: ["metro", "metros"], .centimeter: ["centímetro", "centímetros"],
            .kilogram: ["kilogramo", "kilogramos"], .gram: ["gramo", "gramos"], .liter: ["litro", "litros"],
            .kilometerPerHour: ["kilómetro por hora", "kilómetros por hora"],
        ]
        return amount + " " + form(words[unit]!, plural)
    }

    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String? {
        "de " + number(from, gender) + " a " + number(to, gender)
    }

    // MARK: ordinals

    /// 1.º, 1.ª, 3.er and 1º, 1ª (es and pt "18º" before C or F are degrees, read before this); "1°" where the
    /// degrees left it (`ordinalSignBefore`).
    var ordinalPattern: String? { #"(?<![\p{L}\p{N}.,])([0-9]{1,2})\.?(º|ª|°|er)(?![\p{L}\p{N}])"# }

    var ordinalSignBefore: [String: Inflect.Gender] {
        ["el": .masculine, "al": .masculine, "del": .masculine, "un": .masculine, "la": .feminine, "una": .feminine,
         "llegó": .masculine, "quedó": .masculine, "terminó": .masculine, "acabó": .masculine, "llegué": .masculine,
         "quedé": .masculine]
    }

    func ordinal(_ match: Pattern.Match) -> String? {
        guard let n = Int(match[1]) else { return nil }
        return Inflect.spanish(n, feminine: feminineOrdinal(match, sign: match[2]), short: match[2] == "er")
    }

    // MARK: chords

    var chords: ChordWords.Style {
        ChordWords.Style(roots: .solfege(["do", "re", "mi", "fa", "sol", "la", "si"]), sharp: "sostenido", flat: "bemol",
                         minor: "menor", seventh: "séptima", majorSeventh: "séptima mayor", joins: ["y", "e", "o", "u"],
                         words: ["Dom", "Fam", "Lab"], dash: ", ",
                         music: ["acorde", "canci", "guitarr", "piano", "ukelele", "ukulele", "tonalidad", "cejilla",
                                 "trastes", "progresi", "estrofa", "estribillo", "rasgue", "partitura", "melod",
                                 "músic", "music", "intro", "riff"])
    }
}
