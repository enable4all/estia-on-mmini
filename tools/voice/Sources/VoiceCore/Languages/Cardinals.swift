import Foundation

/// Numbers in words in the ten Supertonic-3 languages: Foundation's spell-out in the language's full locale (CLDR's
/// rules; pt is pt_PT), with the 0.9.3 plan's fixes: the soft hyphens Foundation puts inside it, nl and de words
/// (U+00AD) are taken out, pt_PT's "um mil milhões" is "mil milhões", and ru says "ноль целых" before a fraction under
/// one. One formatter per language, made once (NumberFormatter is safe to share for formatting).
enum Cardinals {
    static let locales: [String: String] = [
        "es": "es_ES", "it": "it_IT", "pt": "pt_PT", "fr": "fr_FR", "nl": "nl_NL",
        "de": "de_DE", "pl": "pl_PL", "ru": "ru_RU", "ja": "ja_JP", "ko": "ko_KR",
    ]

    /// The largest number said in words, as in English (beyond it: the digits as written).
    static let largest = 999_999_999_999_999

    private static let formatters: [String: NumberFormatter] = locales.mapValues { id in
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: id)
        formatter.numberStyle = .spellOut
        return formatter
    }

    private static let months: [String: [String]] = locales.mapValues { id in
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: id)
        return formatter.monthSymbols
    }

    /// Month `m` (1 to 12) by name, as a date says it (DateFormatter's month symbols: pl and ru in the genitive,
    /// "października", "октября").
    static func month(_ m: Int, _ code: String) -> String { months[code]?[m - 1] ?? String(m) }

    /// `n` in words in `code`'s language, said alone (Foundation's form: es "uno", de "eins").
    static func words(_ n: Int, _ code: String) -> String {
        guard abs(n) <= largest, let formatter = formatters[code] else { return String(n) }
        return fixed(formatter.string(from: NSNumber(value: n)) ?? String(n), code)
    }

    /// A number with a decimal part in Foundation's words: ru only ("две целых пять десятых"), whose fractions are
    /// read as tenths and hundredths; under one it starts "ноль целых" (Foundation leaves it out).
    static func decimal(_ whole: String, _ fraction: String, _ code: String) -> String {
        guard let formatter = formatters[code],
              let said = formatter.string(from: NSDecimalNumber(string: whole + "." + fraction)) else { return whole + "," + fraction }
        let words = fixed(said, code)
        return code == "ru" && (Int(whole) ?? 1) == 0 ? "ноль целых " + words : words
    }

    private static func fixed(_ said: String, _ code: String) -> String {
        var words = said.replacingOccurrences(of: "\u{00AD}", with: "")
        if code == "pt" { words = words.replacingOccurrences(of: "um mil milhões", with: "mil milhões") }
        return words
    }
}
