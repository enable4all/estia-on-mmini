import Foundation

/// French (France): 13:00, 13h and 9h30 as "N heures M" ("une heure", "vingt et une heures"); day 1 "premier"; 1er,
/// 1re, 3e and 3ème spelled; phone numbers in pairs; "N euros M"; under two is singular ("un virgule cinq degré");
/// chords in solfège ("si bémol", "ré sept").
struct French: Pack {
    let code = "fr"
    var groups: Groups { .space }
    var minus: String { "moins" }
    var numberSign: String { "numéro" }
    var decimalMark: String { "virgule" }
    var hTimes: Bool { true }

    func plural(_ x: Num) -> Inflect.Plural { (x.value ?? 2) < 2 ? .one : .many }

    /// Foundation's words with spaces for their hyphens (the live check, 17 Oct: "soixante-dix-huit" came back "60
    /// X8", "soixante dix huit" right; "dix-huit degrés" was "10,8 degrés").
    func cardinal(_ n: Int, _ gender: Inflect.Gender) -> String {
        Inflect.gender(Cardinals.words(n, code).replacingOccurrences(of: "-", with: " "), gender, code)
    }

    // MARK: times

    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String {
        cardinal(hour, .feminine) + (hour <= 1 ? " heure" : " heures") + (minute == 0 ? "" : " " + cardinal(minute, .feminine))
    }

    /// "de quatorze à quinze heures": a start on the hour says no "heures".
    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String {
        let opener = ["de", "entre", "des"].contains(before.last) ? "" : "de "
        let start = from.1 == 0 ? cardinal(from.0, .feminine) : clock(from.0, from.1, before)
        return opener + start + " à " + clock(to.0, to.1, before)
    }

    // MARK: dates

    var dateWords: Set<String> { ["le", "du", "au"] }
    var dayMonthWords: [String] { (1...12).map { Cardinals.month($0, code) } }

    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String {
        (day == 1 ? "premier" : cardinal(day, .plain)) + " " + Cardinals.month(month, code)
            + (year.map { " " + cardinal($0, .plain) } ?? "")
    }

    /// "1 octobre": "premier octobre"; any other day is read with the numbers.
    func dayMonth(_ match: Pattern.Match, _ before: Before) -> String? {
        match[1] == "1" ? "premier" + match[0].drop(while: \.isNumber) : nil
    }

    // MARK: phones

    var phoneWords: [String] { ["téléphone", "portable", "appelle", "appeler"] }

    /// In pairs, each read as a number ("06": "zéro six", "78": "soixante-dix-huit"); one long group in pairs.
    func phone(_ plus: Bool, _ groups: [String]) -> String {
        var parts = groups
        if groups.count == 1, groups[0].count > 4, groups[0].count % 2 == 0 {
            parts = stride(from: 0, to: groups[0].count, by: 2).map { offset in
                String(groups[0].dropFirst(offset).prefix(2))
            }
        }
        let said = parts.map { group -> String in
            let zeros = group.prefix { $0 == "0" }
            let rest = group.dropFirst(zeros.count)
            let words = zeros.map { _ in "zéro" } + (rest.isEmpty ? [] : [cardinal(Int(rest)!, .plain)])
            return words.joined(separator: " ")
        }
        return (plus ? self.plus + " " : "") + said.joined(separator: ", ")
    }

    // MARK: money, percent, temperatures and units

    var currencies: [String: Currency] { ["€": .euro, "EUR": .euro, "$": .dollar, "USD": .dollar] }

    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String {
        let nouns = currency == .euro ? ["euro", "euros"] : ["dollar", "dollars"]
        if whole.value == 0, cents > 0 { return cardinal(cents, .masculine) + (cents == 1 ? " centime" : " centimes") }
        let units = number(whole, .masculine)
        let millions = ["million", "millions", "milliard", "milliards"].contains { units.hasSuffix($0) }
        let noun = form(nouns, plural(whole))
        return units + (millions ? " d'" + noun : " " + noun) + (cents > 0 ? " " + cardinal(cents, .plain) : "")
    }

    func percent(_ amount: String, _ plural: Inflect.Plural) -> String { amount + " pour cent" }

    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String {
        amount + " " + form(["degré", "degrés"], plural) + (fahrenheit ? " Fahrenheit" : "")
    }

    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String {
        let words: [MeasureUnit: [String]] = [
            .kilometer: ["kilomètre", "kilomètres"], .meter: ["mètre", "mètres"], .centimeter: ["centimètre", "centimètres"],
            .kilogram: ["kilogramme", "kilogrammes"], .gram: ["gramme", "grammes"], .liter: ["litre", "litres"],
            .kilometerPerHour: ["kilomètre par heure", "kilomètres par heure"],
        ]
        return amount + " " + form(words[unit]!, plural)
    }

    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String? {
        "de " + number(from, gender) + " à " + number(to, gender)
    }

    // MARK: ordinals

    /// 1er, 1re, 1ère, 2nd, 2nde, 3e, 3ème, 3è.
    var ordinalPattern: String? { #"(?<![\p{L}\p{N}.,])([0-9]{1,3})(ère|re|er|ème|e|è|nde|nd)(?![\p{L}\p{N}])"# }

    func ordinal(_ match: Pattern.Match) -> String? {
        guard let n = Int(match[1]) else { return nil }
        switch match[2] {
        case "er": return n == 1 ? "premier" : nil
        case "re", "ère": return n == 1 ? "première" : nil
        case "nd": return n == 2 ? "second" : nil
        case "nde": return n == 2 ? "seconde" : nil
        default: return Inflect.french(n, feminine: false)?.replacingOccurrences(of: "-", with: " ")
        }
    }

    // MARK: chords

    var chords: ChordWords.Style {
        ChordWords.Style(roots: .solfege(["do", "ré", "mi", "fa", "sol", "la", "si"]), sharp: "dièse", flat: "bémol",
                         minor: "mineur", seventh: "sept", majorSeventh: "majeur sept", joins: ["et", "ou"],
                         words: ["Dom", "Fam", "Lab"], dash: ", ",
                         music: ["accords", "chanson", "guitare", "piano", "ukulélé", "ukulele", "tonalité",
                                 "capodastre", "couplet", "refrain", "mélodie", "musique", "partition", "progression",
                                 "intro", "riff"])
    }
}
