import Foundation

/// Japanese: digits stay where the audit heard them right (年月日, 番目, decimals, percentages, years); "%" is written
/// パーセント (the review's listen, 17 Oct: "87.5%" came back "87.5センツ", "87.5パーセント" right); times become N時M分
/// (4時 as よじ: the 17 Oct listen heard "4時" as "表示"); group commas are dropped and 10,000 and over written with 万
/// and 億 ("123万4567": the listen misread "1234567"); phone digits in katakana with の between the groups; chords
/// with the English letter names in katakana ("エフシャープマイナーセブン").
struct Japanese: Pack {
    let code = "ja"
    var groups: Groups { .comma }
    var minus: String { "マイナス" }
    var plus: String { "プラス" }
    var signSpace: String { "" }
    var numberSign: String { "ナンバー" }
    var decimalMark: String { "." }
    var spellsNumbers: Bool { false }

    /// Digits as written, decimals kept ("2.5").
    func number(_ x: Num, _ gender: Inflect.Gender) -> String { x.whole + (x.isInteger ? "" : "." + x.fraction) }

    // MARK: times

    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String {
        (hour == 4 ? "よじ" : "\(hour)時") + (minute == 0 ? "" : "\(minute)分")
    }

    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String {
        clock(from.0, from.1, before) + "から" + clock(to.0, to.1, before) + "まで"
    }

    // MARK: dates

    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String {
        (year.map { "\($0)年" } ?? "") + "\(month)月\(day)日"
    }

    // MARK: phones

    var phoneWords: [String] { ["電話"] }

    private static let digits = ["ゼロ", "イチ", "ニー", "サン", "ヨン", "ゴー", "ロク", "ナナ", "ハチ", "キュー"]

    func phone(_ plus: Bool, _ groups: [String]) -> String {
        let said = Reader.split(groups).map { $0.compactMap(\.wholeNumberValue).map { Japanese.digits[$0] }.joined() }
        return (plus ? self.plus + " " : "") + said.joined(separator: "の ")
    }

    // MARK: money, percent, temperatures and units

    var currencies: [String: Currency] {
        ["€": .euro, "EUR": .euro, "$": .dollar, "USD": .dollar, "¥": .yen, "円": .yen, "JPY": .yen]
    }

    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String {
        let amount = whole.whole + (cents > 0 ? "." + String(format: "%02d", cents) : "")
        switch currency {
        case .dollar: return amount + "ドル"
        case .yen: return amount + "円"
        default: return amount + "ユーロ"
        }
    }

    func percent(_ amount: String, _ plural: Inflect.Plural) -> String { amount + "パーセント" }

    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String {
        (fahrenheit ? "華氏" : "") + amount + "度"
    }

    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String {
        let words: [MeasureUnit: String] = [
            .kilometer: "キロメートル", .meter: "メートル", .centimeter: "センチメートル", .kilogram: "キログラム", .gram: "グラム",
            .liter: "リットル",
        ]
        return unit == .kilometerPerHour ? "時速" + amount + "キロメートル" : amount + words[unit]!
    }

    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String? { nil }

    // MARK: chords

    var chords: ChordWords.Style {
        ChordWords.Style(roots: .letters(["シー", "ディー", "イー", "エフ", "ジー", "エー", "ビー"], joiner: ""), sharp: "シャープ",
                         flat: "フラット", minor: "マイナー", seventh: "セブン", majorSeventh: "メジャーセブン", joins: [],
                         words: [], dash: "、")
    }
}
