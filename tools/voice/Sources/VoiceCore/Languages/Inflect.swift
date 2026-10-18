import Foundation

/// The forms numbers take around their noun, by hand table (the 0.9.3 plan: no RBNF interpreter): the last word's
/// gender, the pl and ru plural rule, ordinals 1 to 99 (fr, it, nl and de by rule from the cardinal), pl and ru
/// genitive cardinals 0 to 99, and pl and ru years with their last part as an ordinal.
enum Inflect {
    /// The gender of the noun after a number; `plain` is a number said alone.
    enum Gender { case plain, masculine, feminine, neuter }

    /// `words` (a number in words) with its last word in `gender`: es un/una/veintiún, it un/una, pt uma/duas, fr une,
    /// de ein/eine, pl jedna/dwie/jedno, ru одна/две/одно; nl's 1 is always "één" (never the article "een"). es and pt
    /// feminine change every word below the millions: the hundreds ("doscientas", "duzentas") and pt's "duas mil".
    static func gender(_ words: String, _ gender: Gender, _ code: String) -> String {
        if code == "nl" { return words == "een" ? "één" : words }
        guard gender != .plain else { return words }
        if gender == .feminine, code == "es" || code == "pt" { return feminineBelowMillions(words, code) }
        let swaps: [String: String]
        switch (code, gender) {
        case ("es", _): swaps = ["uno": "un", "veintiuno": "veintiún"]
        case ("it", .feminine): swaps = ["uno": "una"]
        case ("it", _): swaps = ["uno": "un"]
        case ("fr", .feminine): return words == "un" || words.hasSuffix("-un") || words.hasSuffix(" un") ? words + "e" : words
        case ("de", .feminine): return words.hasSuffix("eins") ? String(words.dropLast()) + "e" : words
        case ("de", _): return words.hasSuffix("eins") ? String(words.dropLast()) : words
        case ("pl", .feminine): swaps = ["jeden": "jedna", "dwa": "dwie"]
        case ("pl", .neuter): swaps = ["jeden": "jedno"]
        case ("ru", .feminine): swaps = ["один": "одна", "два": "две"]
        case ("ru", .neuter): swaps = ["один": "одно"]
        default: return words
        }
        var parts = words.components(separatedBy: " ")
        if let last = parts.last, let swapped = swaps[last] { parts[parts.count - 1] = swapped }
        return parts.joined(separator: " ")
    }

    /// es and pt feminine (the review, 17 Oct: "doscientos páginas"): after the last millón or milhão (a masculine
    /// noun, "doscientos millones de personas"), the hundreds end in -as, es uno in "una", pt um and dois in "uma" and
    /// "duas" ("duas mil pessoas").
    private static func feminineBelowMillions(_ words: String, _ code: String) -> String {
        var parts = words.components(separatedBy: " ")
        let millions: Set<String> = ["millón", "millones", "milhão", "milhões"]
        let start = parts.lastIndex(where: millions.contains).map { $0 + 1 } ?? 0
        let swaps = code == "es" ? ["uno": "una", "veintiuno": "veintiuna"] : ["um": "uma", "dois": "duas"]
        let hundreds = code == "es" ? "ientos" : "entos"
        for i in start..<parts.count {
            if let swapped = swaps[parts[i]] {
                parts[i] = swapped
            } else if parts[i].hasSuffix(hundreds) {
                parts[i] = String(parts[i].dropLast(2)) + "as"
            }
        }
        return parts.joined(separator: " ")
    }

    /// Which form a noun takes after a number: pl and ru tell one, few (2 to 4, not 12 to 14) and many; a decimal
    /// takes `fraction` (pl and ru: the genitive singular, "2,5 kilometra"); the other languages one and many.
    enum Plural { case one, few, many, fraction }

    static func plural(_ n: Int, fraction: Bool, _ code: String) -> Plural {
        if fraction { return .fraction }
        let (tens, hundreds) = (n % 10, n % 100)
        let few = (2...4).contains(tens) && !(12...14).contains(hundreds)
        switch code {
        case "pl": return n == 1 ? .one : few ? .few : .many
        case "ru": return tens == 1 && hundreds != 11 ? .one : few ? .few : .many
        default: return n == 1 ? .one : .many
        }
    }

    // MARK: - Ordinals: es, it, pt, fr, nl, de

    /// es 1 to 99: "primero", "vigésimo primero"; feminine "primera"; `short` the form before a noun ("primer",
    /// "tercer", written 1.er and 3.er).
    static func spanish(_ n: Int, feminine: Bool, short: Bool = false) -> String? {
        guard (1...99).contains(n) else { return nil }
        let units = ["", "primero", "segundo", "tercero", "cuarto", "quinto", "sexto", "séptimo", "octavo", "noveno"]
        let teens = ["décimo", "undécimo", "duodécimo", "decimotercero", "decimocuarto", "decimoquinto", "decimosexto",
                     "decimoséptimo", "decimoctavo", "decimonoveno"]
        let tens = ["", "", "vigésimo", "trigésimo", "cuadragésimo", "quincuagésimo", "sexagésimo", "septuagésimo",
                    "octogésimo", "nonagésimo"]
        var words = n < 10 ? [units[n]] : n < 20 ? [teens[n - 10]] : [tens[n / 10]] + (n % 10 == 0 ? [] : [units[n % 10]])
        if feminine {
            words = words.map { String($0.dropLast()) + "a" }
        } else if short, let last = words.last, last.hasSuffix("primero") || last.hasSuffix("tercero") {
            words[words.count - 1] = String(last.dropLast())
        }
        return words.joined(separator: " ")
    }

    /// it: "primo" to "decimo", then the cardinal with -esimo ("undicesimo", "ventitreesimo", "ventiseiesimo").
    static func italian(_ n: Int, feminine: Bool) -> String? {
        guard (1...99).contains(n) else { return nil }
        let first = ["primo", "secondo", "terzo", "quarto", "quinto", "sesto", "settimo", "ottavo", "nono", "decimo"]
        var word: String
        if n <= 10 {
            word = first[n - 1]
        } else {
            let cardinal = Cardinals.words(n, "it")
            if cardinal.hasSuffix("tré") { word = String(cardinal.dropLast()) + "eesimo" }
            else if cardinal.hasSuffix("sei") { word = cardinal + "esimo" }
            else { word = String(cardinal.dropLast()) + "esimo" }
        }
        if feminine { word = String(word.dropLast()) + "a" }
        return word
    }

    /// pt 1 to 99: "primeiro", "vigésimo primeiro"; feminine "primeira", "vigésima primeira".
    static func portuguese(_ n: Int, feminine: Bool) -> String? {
        guard (1...99).contains(n) else { return nil }
        let units = ["", "primeiro", "segundo", "terceiro", "quarto", "quinto", "sexto", "sétimo", "oitavo", "nono"]
        let tens = ["", "décimo", "vigésimo", "trigésimo", "quadragésimo", "quinquagésimo", "sexagésimo", "septuagésimo",
                    "octogésimo", "nonagésimo"]
        var words = n < 10 ? [units[n]] : [tens[n / 10]] + (n % 10 == 0 ? [] : [units[n % 10]])
        if feminine { words = words.map { String($0.dropLast()) + "a" } }
        return words.joined(separator: " ")
    }

    /// fr: "premier"/"première", then the cardinal with -ième ("troisième", "vingt-et-unième", "quatre-vingtième").
    static func french(_ n: Int, feminine: Bool) -> String? {
        guard (1...999).contains(n) else { return nil }
        if n == 1 { return feminine ? "première" : "premier" }
        let cardinal = Cardinals.words(n, "fr")
        let cut = cardinal.lastIndex { $0 == "-" || $0 == " " }.map { cardinal.index(after: $0) } ?? cardinal.startIndex
        let head = String(cardinal[..<cut])
        let last = String(cardinal[cut...])
        let stem: String
        switch last {
        case "un": stem = "un"
        case "cinq": stem = "cinqu"
        case "neuf": stem = "neuv"
        default:
            if last.hasSuffix("e") || last == "vingts" || last.hasSuffix("cents") { stem = String(last.dropLast()) }
            else { stem = last }
        }
        return head + stem + "ième"
    }

    /// nl: -ste for 1, 8 and from 20 up ("eerste", "achtste", "twintigste"), -de otherwise ("tweede", "derde").
    static func dutch(_ n: Int) -> String? {
        guard (1...999).contains(n) else { return nil }
        let small = ["eerste", "tweede", "derde", "vierde", "vijfde", "zesde", "zevende", "achtste", "negende", "tiende",
                     "elfde", "twaalfde", "dertiende", "veertiende", "vijftiende", "zestiende", "zeventiende", "achttiende",
                     "negentiende"]
        if n < 20 { return small[n - 1] }
        let rest = n % 100
        if n > 100, (1...19).contains(rest) { return Cardinals.words(n - rest, "nl") + small[rest - 1] }
        return Cardinals.words(n, "nl") + "ste"
    }

    /// de: the stem an ending goes on: the cardinal with -t (2 to 19; erst, dritt, siebt, acht) or -st (from 20).
    static func germanStem(_ n: Int) -> String? {
        guard (1...999).contains(n) else { return nil }
        let rest = n % 100
        if n > 100, (1...19).contains(rest) { return Cardinals.words(n - rest, "de") + germanStem(rest)! }
        switch n {
        case 1: return "erst"
        case 3: return "dritt"
        case 7: return "siebt"
        case 8: return "acht"
        case 2...19: return Cardinals.words(n, "de") + "t"
        default: return Cardinals.words(n, "de") + "st"
        }
    }

    // MARK: - Ordinals: pl

    enum PolishForm: Int { case masculineNominative, masculineGenitive, masculineLocative, feminineNominative, feminineGenitive, feminineInstrumental }

    private enum PolishStem { case hard, velar, soft }

    private static let polishEndings: [PolishStem: [String]] = [
        .hard: ["y", "ego", "ym", "a", "ej", "ą"],
        .velar: ["i", "iego", "im", "a", "iej", "ą"],
        .soft: ["i", "iego", "im", "ia", "iej", "ią"],
    ]

    private static let polishUnits: [(String, PolishStem)] = [
        ("", .hard), ("pierwsz", .hard), ("drug", .velar), ("trzec", .soft), ("czwart", .hard), ("piąt", .hard),
        ("szóst", .hard), ("siódm", .hard), ("ósm", .hard), ("dziewiąt", .hard),
    ]
    private static let polishTeens = ["dziesiąt", "jedenast", "dwunast", "trzynast", "czternast", "piętnast", "szesnast",
                                      "siedemnast", "osiemnast", "dziewiętnast"]
    private static let polishTens = ["", "", "dwudziest", "trzydziest", "czterdziest", "pięćdziesiąt", "sześćdziesiąt",
                                     "siedemdziesiąt", "osiemdziesiąt", "dziewięćdziesiąt"]

    /// pl 1 to 99, both words of a compound inflected ("dwudziestego szóstego"); hours are feminine ("trzynastej").
    static func polish(_ n: Int, _ form: PolishForm) -> String? {
        guard (1...99).contains(n) else { return nil }
        func say(_ stem: String, _ kind: PolishStem) -> String { stem + polishEndings[kind]![form.rawValue] }
        if n < 10 { return say(polishUnits[n].0, polishUnits[n].1) }
        if n < 20 { return say(polishTeens[n - 10], .hard) }
        let tens = say(polishTens[n / 10], .hard)
        return n % 10 == 0 ? tens : tens + " " + say(polishUnits[n % 10].0, polishUnits[n % 10].1)
    }

    private static let polishHundreds = ["", "setn", "dwusetn", "trzechsetn", "czterechsetn", "pięćsetn", "sześćsetn",
                                         "siedemsetn", "osiemsetn", "dziewięćsetn"]
    private static let polishThousands = ["", "tysięczn", "dwutysięczn", "trzytysięczn"]

    /// A pl year with its last part an ordinal: 1987 "tysiąc dziewięćset osiemdziesiątym siódmym" (locative), 2026
    /// "dwa tysiące dwudziestego szóstego" (genitive), 2000 "dwutysięcznego"; nil past 3999.
    static func polishYear(_ n: Int, _ form: PolishForm) -> String? {
        guard (1...3999).contains(n) else { return nil }
        let ending = polishEndings[.hard]![form.rawValue]
        let rest = n % 100
        if rest != 0 {
            let ordinal = polish(rest, form)!
            return n < 100 ? ordinal : Cardinals.words(n - rest, "pl") + " " + ordinal
        }
        if n % 1000 == 0 { return polishThousands[n / 1000] + ending }
        let hundreds = polishHundreds[(n % 1000) / 100] + ending
        return n < 1000 ? hundreds : Cardinals.words(n - n % 1000, "pl") + " " + hundreds
    }

    /// pl genitive cardinals 0 to 99 ("od pięciu do dziesięciu"; "dwudziestu jeden").
    static func polishGenitive(_ n: Int) -> String? {
        guard (0...99).contains(n) else { return nil }
        let small = ["zera", "jednego", "dwóch", "trzech", "czterech", "pięciu", "sześciu", "siedmiu", "ośmiu", "dziewięciu",
                     "dziesięciu", "jedenastu", "dwunastu", "trzynastu", "czternastu", "piętnastu", "szesnastu",
                     "siedemnastu", "osiemnastu", "dziewiętnastu"]
        let tens = ["", "", "dwudziestu", "trzydziestu", "czterdziestu", "pięćdziesięciu", "sześćdziesięciu",
                    "siedemdziesięciu", "osiemdziesięciu", "dziewięćdziesięciu"]
        if n < 20 { return small[n] }
        let unit = n % 10
        return tens[n / 10] + (unit == 0 ? "" : " " + (unit == 1 ? "jeden" : small[unit]))
    }

    // MARK: - Ordinals: ru

    enum RussianForm: Int {
        case masculineNominative, feminineNominative, neuterNominative, genitive, dative, prepositional, feminineOblique,
             feminineAccusative, instrumental, pluralNominative, pluralOblique
    }

    private enum RussianStem { case y, o, third }

    private static let russianEndings: [RussianStem: [String]] = [
        .y: ["ый", "ая", "ое", "ого", "ому", "ом", "ой", "ую", "ым", "ые", "ых"],
        .o: ["ой", "ая", "ое", "ого", "ому", "ом", "ой", "ую", "ым", "ые", "ых"],
        .third: ["ий", "ья", "ье", "ьего", "ьему", "ьем", "ьей", "ью", "ьим", "ьи", "ьих"],
    ]

    private static let russianUnits: [(String, RussianStem)] = [
        ("", .y), ("перв", .y), ("втор", .o), ("трет", .third), ("четвёрт", .y), ("пят", .y), ("шест", .o), ("седьм", .o),
        ("восьм", .o), ("девят", .y),
    ]
    private static let russianTeens = ["десят", "одиннадцат", "двенадцат", "тринадцат", "четырнадцат", "пятнадцат",
                                       "шестнадцат", "семнадцат", "восемнадцат", "девятнадцат"]
    private static let russianTens: [(String, RussianStem)] = [
        ("", .y), ("", .y), ("двадцат", .y), ("тридцат", .y), ("сороков", .o), ("пятидесят", .y), ("шестидесят", .y),
        ("семидесят", .y), ("восьмидесят", .y), ("девяност", .y),
    ]

    /// ru 1 to 99, only the last word inflected ("двадцать первый", "восемьдесят седьмом").
    static func russian(_ n: Int, _ form: RussianForm) -> String? {
        guard (1...99).contains(n) else { return nil }
        func say(_ stem: String, _ kind: RussianStem) -> String { stem + russianEndings[kind]![form.rawValue] }
        if n < 10 { return say(russianUnits[n].0, russianUnits[n].1) }
        if n < 20 { return say(russianTeens[n - 10], .y) }
        if n % 10 == 0 { return say(russianTens[n / 10].0, russianTens[n / 10].1) }
        return Cardinals.words(n - n % 10, "ru") + " " + say(russianUnits[n % 10].0, russianUnits[n % 10].1)
    }

    private static let russianHundreds = ["", "сот", "двухсот", "трёхсот", "четырёхсот", "пятисот", "шестисот",
                                          "семисот", "восьмисот", "девятисот"]
    private static let russianThousands = ["", "тысячн", "двухтысячн", "трёхтысячн"]

    /// A ru year with its last word an ordinal: 1987 "тысяча девятьсот восемьдесят седьмом" (Foundation's "одна
    /// тысяча" said "тысяча", as years are), 2026 "две тысячи двадцать шестого", 2000 "двухтысячного"; nil past 3999.
    static func russianYear(_ n: Int, _ form: RussianForm) -> String? {
        guard (1...3999).contains(n) else { return nil }
        func cardinal(_ n: Int) -> String {
            let words = Cardinals.words(n, "ru")
            return words.hasPrefix("одна тысяча") ? String(words.dropFirst("одна ".count)) : words
        }
        let ending = russianEndings[.y]![form.rawValue]
        let rest = n % 100
        if rest != 0 {
            let ordinal = russian(rest, form)!
            return n < 100 ? ordinal : cardinal(n - rest) + " " + ordinal
        }
        if n % 1000 == 0 { return russianThousands[n / 1000] + ending }
        let hundreds = russianHundreds[(n % 1000) / 100] + ending
        return n < 1000 ? hundreds : cardinal(n - n % 1000) + " " + hundreds
    }

    /// ru genitive cardinals 0 to 99 ("с четырнадцати до пятнадцати", "двадцати одного").
    static func russianGenitive(_ n: Int) -> String? {
        guard (0...99).contains(n) else { return nil }
        let small = ["нуля", "одного", "двух", "трёх", "четырёх", "пяти", "шести", "семи", "восьми", "девяти", "десяти",
                     "одиннадцати", "двенадцати", "тринадцати", "четырнадцати", "пятнадцати", "шестнадцати",
                     "семнадцати", "восемнадцати", "девятнадцати"]
        let tens = ["", "", "двадцати", "тридцати", "сорока", "пятидесяти", "шестидесяти", "семидесяти", "восьмидесяти",
                    "девяноста"]
        if n < 20 { return small[n] }
        return tens[n / 10] + (n % 10 == 0 ? "" : " " + small[n % 10])
    }

    // MARK: - ko

    /// ko native hours 1 to 12, as said before 시 ("한 시", "열두 시").
    static let koreanHours = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열", "열한", "열두"]
}
