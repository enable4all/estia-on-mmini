import Foundation

/// Korean: digits stay where the audit heard them right (년월일, 번째, decimals, %, years, grouped numbers); times use
/// 오전 and 오후 with a native hour ("오후 한 시") and digits with 분; months 6 and 10 are 유월 and 시월; a
/// temperature below zero is 영하; € and $ "N유로 M센트", ₩ "N원"; phone digits with 공 for 0 and 에 between the
/// groups; chords with the English letter names in Hangul ("에프 샵 마이너 세븐").
struct Korean: Pack {
    let code = "ko"
    var groups: Groups { .comma }
    var minus: String { "마이너스" }
    var plus: String { "플러스" }
    var numberSign: String { "넘버 " }
    var decimalMark: String { "." }
    var spellsNumbers: Bool { false }

    /// Digits as written, decimals kept ("2.5").
    func number(_ x: Num, _ gender: Inflect.Gender) -> String { x.whole + (x.isInteger ? "" : "." + x.fraction) }

    // MARK: times

    private func part(_ hour: Int) -> String { hour < 12 ? "오전" : "오후" }

    /// "한 시 30분": the native hour on the 12-hour clock, minutes as digits.
    private func hourAndMinutes(_ hour: Int, _ minute: Int) -> String {
        Inflect.koreanHours[hour % 12 == 0 ? 12 : hour % 12] + " 시" + (minute == 0 ? "" : " \(minute)분")
    }

    func clock(_ hour: Int, _ minute: Int, _ before: Before) -> String { part(hour) + " " + hourAndMinutes(hour, minute) }

    /// "오후 두 시부터 세 시까지": a part of the day both share is said once.
    func clockRange(_ from: (Int, Int), _ to: (Int, Int), _ before: Before) -> String {
        let second = part(to.0) == part(from.0) ? "" : part(to.0) + " "
        return clock(from.0, from.1, before) + "부터 " + second + hourAndMinutes(to.0, to.1) + "까지"
    }

    // MARK: dates

    func date(_ day: Int, _ month: Int, _ year: Int?, _ before: Before) -> String {
        (year.map { "\($0)년 " } ?? "") + "\(month)월 \(day)일"
    }

    // MARK: phones

    var phoneWords: [String] { ["전화"] }

    private static let digits = ["공", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]

    func phone(_ plus: Bool, _ groups: [String]) -> String {
        let said = Reader.split(groups).map { $0.compactMap(\.wholeNumberValue).map { Korean.digits[$0] }.joined() }
        return (plus ? self.plus + " " : "") + said.joined(separator: "에 ")
    }

    // MARK: money, percent, temperatures and units

    var currencies: [String: Currency] {
        ["€": .euro, "EUR": .euro, "유로": .euro, "$": .dollar, "USD": .dollar, "달러": .dollar, "₩": .won, "원": .won, "KRW": .won]
    }

    func money(_ whole: Num, cents: Int, _ currency: Currency) -> String {
        switch currency {
        case .won: return whole.whole + "원"
        case .dollar: return whole.whole + "달러" + (cents > 0 ? " \(cents)센트" : "")
        default: return whole.whole + "유로" + (cents > 0 ? " \(cents)센트" : "")
        }
    }

    func percent(_ amount: String, _ plural: Inflect.Plural) -> String { amount + "%" }

    /// "18도"; below zero the reader puts 영하 before it ("영하 3도").
    func degrees(_ amount: String, _ plural: Inflect.Plural, fahrenheit: Bool) -> String {
        (fahrenheit ? "화씨 " : "") + amount + "도"
    }

    func unit(_ amount: String, _ unit: MeasureUnit, _ plural: Inflect.Plural) -> String {
        let words: [MeasureUnit: String] = [
            .kilometer: "킬로미터", .meter: "미터", .centimeter: "센티미터", .kilogram: "킬로그램", .gram: "그램", .liter: "리터",
        ]
        return unit == .kilometerPerHour ? "시속 " + amount + "킬로미터" : amount + words[unit]!
    }

    func range(_ from: Num, _ to: Num, _ gender: Inflect.Gender) -> String? { nil }

    // MARK: chords

    var chords: ChordWords.Style {
        ChordWords.Style(roots: .letters(["씨", "디", "이", "에프", "지", "에이", "비"], joiner: " "), sharp: "샵", flat: "플랫",
                         minor: "마이너", seventh: "세븐", majorSeventh: "메이저 세븐", joins: [], words: [], dash: ", ")
    }
}
