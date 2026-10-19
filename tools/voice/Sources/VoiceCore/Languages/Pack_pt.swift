import Foundation

/// European Portuguese (pt_PT, the plan's choice): "N horas" with "e" minutes ("vinte e uma horas"), 13h and 9h30
/// too; day 1 "primeiro"; "N euros e M cêntimos"; "mil milhões"; ordinals from the table; chords in solfège
/// ("fá sustenido menor").
struct Portuguese: Pack {
    let code = "pt"
    var groups: Groups { .dot }
    var minus: String { "menos" }
    var plus: String { "mais" }
    var numberSign: String { "número" }
    var decimalMark: String { "vírgula" }
    var plainBefore: Set<String> { ["e", "ou", "a", "de", "do", "da", "em", "no", "na", "por", "com", "para", "entre", "até", "sobre", "sem", "mais", "menos"] }
    var masculineInA: Set<String> {
        ["dia", "dias", "mapa", "mapas", "problema", "problemas", "programa", "programas", "sistema", "sistemas", "tema", "temas", "idioma", "idiomas", "clima", "planeta", "planetas", "sofá", "poema", "poemas", "drama", "dramas", "papa"]
    }
    var hTimes: Bool { true }
    var dotTimeBefore: Set<String> { ["às", "as", "das", "pelas"] }

    // MARK: times

    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String {
        cardinal(hour, .feminine) + (hour == 1 ? " hora" : " horas") + (minute == 0 ? "" : " e " + cardinal(minute, .plain))
    }

    /// "das catorze às quinze horas": a start on the hour says no "horas".
    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String {
        let opener = ["das", "da", "de", "entre"].contains(before.last) ? "" : from.0 == 1 ? "da " : "das "
        let start = from.1 == 0 ? cardinal(from.0, .feminine) : clock(from.0, from.1, before)
        return opener + start + (to.0 == 1 ? " à " : " às ") + clock(to.0, to.1, before)
    }

    // MARK: dates

    var dateWords: Set<String> { ["em", "a", "dia", "no", "desde", "até"] }
    var dayMonthWords: [String] { (1...12).map { Cardinals.month($0, code) } }

    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String {
        (day == 1 ? "primeiro" : cardinal(day, .plain)) + " de " + Cardinals.month(month, code)
            + (year.map { " de " + cardinal($0, .plain) } ?? "")
    }

    func dayMonth(_ match: Pattern.Match, _ before: Before) -> String? {
        match[1] == "1" ? "primeiro" + match[0].drop(while: \.isNumber) : nil
    }

    // MARK: phones

    var phoneWords: [String] { ["telefone", "telemóvel", "ligue", "ligar"] }

    // MARK: money, percent, temperatures and units

    var currencies: [String: Currency] { ["€": .euro, "EUR": .euro, "$": .dollar, "USD": .dollar] }

    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String {
        let nouns = currency == .euro ? ["euro", "euros"] : ["dólar", "dólares"]
        let centWords = cents == 0 ? "" : cardinal(cents, .masculine) + (cents == 1 ? " cêntimo" : " cêntimos")
        if whole.value == 0, cents > 0 { return centWords }
        let units = number(whole, .masculine)
        let millions = ["milhão", "milhões"].contains { units.hasSuffix($0) }
        return units + (millions ? " de " : " ") + form(nouns, plural(whole)) + (cents > 0 ? " e " + centWords : "")
    }

    func percent(_ amount: String, _ plural: Inflect.Plural) -> String { amount + " por cento" }

    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String {
        amount + " " + form(["grau", "graus"], plural) + (fahrenheit ? " Fahrenheit" : "")
    }

    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String {
        let words: [MeasureUnit: [String]] = [
            .kilometer: ["quilómetro", "quilómetros"], .meter: ["metro", "metros"], .centimeter: ["centímetro", "centímetros"],
            .kilogram: ["quilograma", "quilogramas"], .gram: ["grama", "gramas"], .liter: ["litro", "litros"],
            .kilometerPerHour: ["quilómetro por hora", "quilómetros por hora"],
        ]
        return amount + " " + form(words[unit]!, plural)
    }

    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String? {
        "de " + number(from, gender) + " a " + number(to, gender)
    }

    // MARK: ordinals

    /// 1º, 1ª, 1.º, 1.ª (es and pt "18º" before C or F are degrees, read before this); "1°" where the degrees left it
    /// (`ordinalSignBefore`; "a" is left out: "a 30°" is also "at 30 degrees").
    var ordinalPattern: String? { #"(?<![\p{L}\p{N}.,])([0-9]{1,2})\.?(º|ª|°)(?![\p{L}\p{N}])"# }

    var ordinalSignBefore: [String: Inflect.Gender] {
        ["o": .masculine, "ao": .masculine, "do": .masculine, "no": .masculine, "pelo": .masculine, "um": .masculine,
         "da": .feminine, "na": .feminine, "pela": .feminine, "uma": .feminine, "ficou": .masculine, "chegou": .masculine,
         "terminou": .masculine, "acabou": .masculine]
    }

    func ordinal(_ match: Pattern.Match) -> String? {
        Int(match[1]).flatMap { Inflect.portuguese($0, feminine: feminineOrdinal(match, sign: match[2])) }
    }

    // MARK: chords

    var chords: ChordWords.Style {
        ChordWords.Style(roots: .solfege(["dó", "ré", "mi", "fá", "sol", "lá", "si"]), sharp: "sustenido", flat: "bemol",
                         minor: "menor", seventh: "sétima", majorSeventh: "sétima maior", joins: ["e", "ou"],
                         words: ["Sim", "Em", "Dom", "Mim", "Fam", "Lab"], dash: ", ",
                         music: ["acorde", "canç", "músic", "music", "guitarra", "violão", "piano", "cavaquinho",
                                 "ukulele", "tonalidade", "capotraste", "progressão", "estrofe", "refrão", "melodi",
                                 "partitura", "intro", "riff"],
                         forms: ChordWords.Forms(sharp: "sustenido", flat: "bemol", noThird: "sem terça"),
                         marks: ChordWords.Marks(noChord: "sem acorde", again: "repetir") { [self] n in cardinal(n, .feminine) + " vezes" })
    }
}
