import Foundation

/// Russian: "N ноль-ноль", "N M", genitive after с, до, от, после and около ("с четырнадцати до пятнадцати"); a
/// date's day a neuter ordinal, genitive by default, nominative after сегодня, завтра, вчера and это, accusative
/// after по ("с пятого по десятое мая"); the year in a date ends with "года"; году, года and год set a year's last
/// word; a number after от, до, около or после in the genitive, to 99 ("от пяти до десяти километров"); "21-й" by
/// its ending; decimals Foundation's ("две целых пять десятых"); phone groups each read as a number; chords in
/// solfège ("ля минор", "си-бемоль").
struct Russian: Pack {
    let code = "ru"
    var groups: Groups { .space }
    var minus: String { "минус" }
    var plus: String { "плюс" }
    var numberSign: String { "номер" }
    var decimalMark: String { "" }
    var dotTimeBefore: Set<String> { ["в", "с", "до", "к", "после", "около"] }
    var unitAliases: [String: MeasureUnit] {
        ["км/ч": .kilometerPerHour, "км": .kilometer, "м": .meter, "см": .centimeter, "кг": .kilogram, "л": .liter]
    }

    func number(_ x: Num, _ gender: Inflect.Gender) -> String {
        guard x.isInteger || x.value == nil else { return Cardinals.decimal(x.whole, x.fraction, code) }
        guard let whole = x.value else { return x.whole }
        return cardinal(whole, gender)
    }

    // MARK: times

    /// Words after which a time is in the genitive ("с девяти", "до тринадцати тридцати").
    private static let genitiveBefore: Set<String> = ["с", "до", "от", "после", "около"]

    private func genitive(_ hour: Int, _ minute: Int) -> String {
        Inflect.russianGenitive(hour)! + (minute == 0 ? "" : (minute < 10 ? " ноль " : " ") + Inflect.russianGenitive(minute)!)
    }

    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String {
        if Russian.genitiveBefore.contains(before.last) { return genitive(hour, minute) }
        return cardinal(hour, .plain) + (minute == 0 ? " ноль-ноль" : (minute < 10 ? " ноль " : " ") + cardinal(minute, .feminine))
    }

    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String {
        (before.last == "с" ? "" : "с ") + genitive(from.0, from.1) + " до " + genitive(to.0, to.1)
    }

    // MARK: dates

    var dayMonthWords: [String] { (1...12).map { Cardinals.month($0, code) } }

    /// A day is nominative within three words after these ("Сегодня пятница, 16.10.2026": "шестнадцатое").
    private static let nominative: Set<String> = ["сегодня", "завтра", "вчера", "это"]

    private func day(_ day: Int, _ before: Before) -> String {
        // по and на take the accusative ("по десятое мая", "на третье ноября"), к the dative ("к третьему ноября")
        if before.last == "по" || before.last == "на" { return Inflect.russian(day, .neuterNominative)! }
        if before.last == "к" { return Inflect.russian(day, .dative)! }
        return Inflect.russian(day, before.within(3, Russian.nominative) ? .neuterNominative : .genitive)!
    }

    /// "с 5 по 10 мая", "с 5 до 10 мая" and "5-10 мая": the first day in the genitive, "с пятого по десятое мая" (the
    /// review, 17 Oct).
    var rangeDayJoins: [String] { ["по", "до"] }

    func rangeStart(_ day: Int, _ joiner: String, _ before: Before) -> String? {
        let start = Inflect.russian(day, .genitive)!
        guard joiner.contains(where: { $0 == "-" || $0 == "–" }) else { return start + joiner }
        return (["с", "от"].contains(before.last) ? "" : "с ") + start + " по "
    }

    private func year(_ year: Int) -> String { (Inflect.russianYear(year, .genitive) ?? cardinal(year, .plain)) + " года" }

    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String {
        self.day(day, before) + " " + Cardinals.month(month, code) + (year.map { " " + self.year($0) } ?? "")
    }

    /// "3 ноября": "третьего ноября"; a year after it with "года" (a written "года" or "г." is said once).
    func dayMonth(_ match: Pattern.Match, _ before: Before) -> String? {
        guard let n = Int(match[1]) else { return nil }
        return day(n, before) + " " + match[2] + (Int(match[3]).map { " " + year($0) } ?? match[4])
    }

    // MARK: years and ordinals

    var yearPattern: String? { #"(?<![\p{L}\p{N}.,:/-])([0-9]{1,4})\s+(году|года|год)(?![\p{L}])"# }

    /// году: prepositional ("в тысяча девятьсот восемьдесят седьмом году"), года: genitive, год: nominative. One or
    /// two digits are a year only before году ("в 21 году"); before года or год they are an age or a span, read by
    /// the numbers ("Мне 2 года": "два года"; the review, 17 Oct).
    func year(_ match: Pattern.Match) -> String? {
        guard let n = Int(match[1]), match[1].count >= 3 || match[2] == "году" else { return nil }
        let form: Inflect.RussianForm = match[2] == "году" ? .prepositional : match[2] == "года" ? .genitive : .masculineNominative
        return Inflect.russianYear(n, form).map { $0 + " " + match[2] }
    }

    var ordinalPattern: String? {
        #"(?<![\p{L}\p{N}.,])([0-9]{1,2}|1[0-9]{3}|20[0-9]{2})-(ая|ое|ого|ому|ом|ую|ый|ий|ой|ым|ей|ых|ые|й|я|е|го|му|м|ю|х)(?![\p{L}\p{N}])"#
    }

    /// The written ending picks the form: -й, -я, -е, -го, -му, -м, -ю, -ой, -ым; a decade ("1990-х", "1990-е") is
    /// plural (the review, 17 Oct: "одна тысяча девятьсот девяносто-х").
    func ordinal(_ match: Pattern.Match) -> String? {
        if match[1].count == 4 {
            guard let n = Int(match[1]), ["х", "ых", "е", "ые"].contains(match[2]) else { return nil }
            return Inflect.russianYear(n, match[2].hasSuffix("х") ? .pluralOblique : .pluralNominative)
        }
        let forms: [String: Inflect.RussianForm] = [
            "й": .masculineNominative, "ый": .masculineNominative, "ий": .masculineNominative,
            "я": .feminineNominative, "ая": .feminineNominative, "е": .neuterNominative, "ое": .neuterNominative,
            "го": .genitive, "ого": .genitive, "му": .dative, "ому": .dative, "м": .prepositional, "ом": .prepositional,
            "ю": .feminineAccusative, "ую": .feminineAccusative, "ой": .feminineOblique, "ей": .feminineOblique,
            "ым": .instrumental,
        ]
        guard let n = Int(match[1]), let form = forms[match[2]] else { return nil }
        return Inflect.russian(n, form)
    }

    // MARK: phones

    var phoneWords: [String] { ["телефон", "позвони", "звонить"] }

    /// Each group read as a number ("495": "четыреста девяносто пять"), a leading zero digit by digit; one long group
    /// in 1-3-3-2-2 (11 digits) or threes.
    func phone(_ plus: Bool, _ groups: [String]) -> String {
        var parts = groups
        if groups.count == 1, groups[0].count == 11 {
            let g = Array(groups[0])
            parts = [[0, 1], [1, 4], [4, 7], [7, 9], [9, 11]].map { String(g[$0[0]..<$0[1]]) }
        } else {
            parts = Reader.split(groups)
        }
        let said = parts.map { group -> String in
            let zeros = group.prefix { $0 == "0" }
            let rest = group.dropFirst(zeros.count)
            return (zeros.map { _ in "ноль" } + (rest.isEmpty ? [] : [cardinal(Int(rest)!, .plain)])).joined(separator: " ")
        }
        return (plus ? self.plus + " " : "") + said.joined(separator: ", ")
    }

    // MARK: money, percent, temperatures and units

    var currencies: [String: Currency] {
        ["€": .euro, "EUR": .euro, "$": .dollar, "USD": .dollar, "₽": .ruble, "RUB": .ruble, "руб.": .ruble, "руб": .ruble]
    }

    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String {
        let (nouns, centNouns, centGender): ([String], [String], Inflect.Gender)
        switch currency {
        case .ruble: (nouns, centNouns, centGender) = (["рубль", "рубля", "рублей", "рубля"], ["копейка", "копейки", "копеек", "копейки"], .feminine)
        case .dollar: (nouns, centNouns, centGender) = (["доллар", "доллара", "долларов", "доллара"], ["цент", "цента", "центов", "цента"], .masculine)
        default: (nouns, centNouns, centGender) = (["евро", "евро", "евро", "евро"], ["цент", "цента", "центов", "цента"], .masculine)
        }
        let centWords = cardinal(cents, centGender) + " " + form(centNouns, plural(Num(String(cents))))
        if whole.value == 0, cents > 0 { return centWords }
        return number(whole, .masculine) + " " + form(nouns, plural(whole)) + (cents > 0 ? " " + centWords : "")
    }

    func percent(_ amount: String, _ plural: Inflect.Plural) -> String {
        amount + " " + form(["процент", "процента", "процентов", "процента"], plural)
    }

    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String {
        amount + " " + form(["градус", "градуса", "градусов", "градуса"], plural) + (fahrenheit ? " по Фаренгейту" : "")
    }

    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String {
        let words: [MeasureUnit: [String]] = [
            .kilometer: ["километр", "километра", "километров", "километра"],
            .meter: ["метр", "метра", "метров", "метра"],
            .centimeter: ["сантиметр", "сантиметра", "сантиметров", "сантиметра"],
            .kilogram: ["килограмм", "килограмма", "килограмм", "килограмма"],
            .gram: ["грамм", "грамма", "грамм", "грамма"],
            .liter: ["литр", "литра", "литров", "литра"],
            .kilometerPerHour: ["километр в час", "километра в час", "километров в час", "километра в час"],
        ]
        return amount + " " + form(words[unit]!, plural)
    }

    /// Words a number takes the genitive after ("от пяти до десяти километров", "до двадцати одного километра").
    private static let genitiveAfter: Set<String> = ["от", "до", "около", "после", "без", "для", "из", "более", "менее", "свыше"]

    /// The genitive to 99; its noun the genitive singular after 1, 21, 31 ..., else the genitive plural.
    func genitive(_ x: Num, _ before: Before) -> (words: String, plural: Inflect.Plural)? {
        guard Russian.genitiveAfter.contains(before.last), x.isInteger, let n = x.value,
              let words = Inflect.russianGenitive(n) else { return nil }
        return (words, n % 10 == 1 && n % 100 != 11 ? .fraction : .many)
    }

    /// "от пяти до десяти": genitive cardinals to 99 (over 99 deferred: as written).
    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String? {
        guard from.isInteger, to.isInteger, let a = from.value.flatMap(Inflect.russianGenitive),
              let b = to.value.flatMap(Inflect.russianGenitive) else { return nil }
        return "от " + a + " до " + b
    }

    // MARK: chords

    var chords: ChordWords.Style {
        ChordWords.Style(roots: .russian(["до", "ре", "ми", "фа", "соль", "ля", "си"]), sharp: "диез", flat: "бемоль",
                         minor: "минор", seventh: "семь", majorSeventh: "мажор семь", joins: ["и", "или"], words: [],
                         dash: ", ",
                         music: ["аккорд", "песн", "гитар", "пианино", "фортепиано", "укулеле", "тональност",
                                 "каподастр", "куплет", "припев", "мелоди", "музык", "интро", "рифф"])
    }
}
