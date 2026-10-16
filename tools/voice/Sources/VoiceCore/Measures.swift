import Foundation

/// Amounts with what they measure: temperatures, money (misaki's and NeMo's currencies), percent, and units and
/// durations (a chosen part of NeMo's unit list, American spellings). Each takes one number or a range ("18–22°C"),
/// with a minus only after a space, the start or "(" ("-5°C"; never "5-10" or "COVID-19").
enum Measures {
    static func spoken(_ text: String) -> String {
        let warm = temperature.replace(text) { g in
            let amount = range(g, 1)
            let unit = g[6].isEmpty ? (g[7] == "℃" ? "C" : g[7] == "℉" ? "F" : "") : g[6]
            let degrees = amount.single ? " degree" : " degrees"
            return amount.words + degrees + (unit == "C" ? " Celsius" : unit == "F" ? " Fahrenheit" : "")
        }
        // a word glued after an amount keeps a space ("$2.99each")
        var money = symbolFirst.rewrite(warm) { m in amountOf(m.groups, from: 2, currency: m[1], scale: m[7] + m[8]) + gap(m) }
        money = codeFirst.rewrite(money) { m in amountOf(m.groups, from: 2, currency: m[1], scale: m[7] + m[8]) + gap(m) }
        money = currencyAfter.replace(money) { g in amountOf(g, from: 1, currency: g[6], scale: "") }
        let percents = percent.replace(money) { g in range(g, 1).words + " percent" }
        let durations = duration.replace(percents) { g in
            let parts = [(g[1], "hour", "hours"), (g[2], "minute", "minutes"), (g[3], "second", "seconds")]
            return parts.filter { !$0.0.isEmpty }.map { Words.number($0.0) + " " + ($0.0 == "1" ? $0.1 : $0.2) }
                .joined(separator: " ")
        }
        return measured.replace(durations) { g in
            guard let unit = units[g[6]] else { return g[0] }
            let amount = range(g, 1)
            return amount.words + " " + (amount.single ? unit.one : unit.many)
        }
    }

    /// A minus or a plus before a number, only after a space, the start or "(": one group, the sign.
    static let sign = #"(?:(?<![^\s(])([-−+]))?"#

    /// A number with its sign and decimals in words: "minus five", "plus three", "twenty one point five".
    static func signed(_ sign: String, _ whole: String, _ fraction: String) -> String {
        (sign.isEmpty ? "" : sign == "+" ? "plus " : "minus ") + Words.decimal(whole, fraction)
    }

    /// One number or a range of two, from group `first` on (sign, whole, decimals; then whole, decimals of the range's
    /// end): its words and whether it is exactly one (a singular unit: "one degree", "minus one degree").
    static func range(_ g: [String], _ first: Int) -> (words: String, single: Bool) {
        let start = signed(g[first], g[first + 1], g[first + 2])
        guard !g[first + 3].isEmpty else { return (start, g[first + 1] == "1" && g[first + 2].isEmpty) }
        return (start + " to " + Words.decimal(g[first + 3], g[first + 4]), false)
    }

    /// A number with its minus and decimals, or a range of two ("18–22"): five groups.
    static let amount = sign + "(" + NumberNormalizer.integer + #")(?:\.([0-9]+))?(?:\s*[-–]\s*("#
        + NumberNormalizer.integer + #")(?:\.([0-9]+))?)?"#
    /// Nothing glued before an amount: a letter, a digit, a dot or a comma (the plan's word edge).
    static let edge = #"(?<![\p{L}\p{N}.,])"#

    /// "20°C", "-5 °C", "18–22°C", "20°", "20℃": groups amount (5), C or F, ℃ or ℉.
    private static let temperature = Pattern(edge + amount + #"\s?(?:[°º]\s?([CF])?|(℃|℉))(?![\p{L}])"#)
    // MARK: - Units

    /// Units only right after a number, in their own case ("5G" is letters, "5g" grams). Left out: "s" and "in"
    /// (words), "t", "d", "Gb" and "Mb" (a chord's "Gb", or GB written small); "m" is meters, but minutes after an hour
    /// ("1h 30m").
    private static let units: [String: (one: String, many: String)] = [
        "mm": ("millimeter", "millimeters"), "cm": ("centimeter", "centimeters"), "m": ("meter", "meters"),
        "km": ("kilometer", "kilometers"), "mi": ("mile", "miles"), "ft": ("foot", "feet"),
        "m²": ("square meter", "square meters"), "km²": ("square kilometer", "square kilometers"),
        "mg": ("milligram", "milligrams"), "g": ("gram", "grams"), "kg": ("kilogram", "kilograms"),
        "lb": ("pound", "pounds"), "lbs": ("pound", "pounds"), "oz": ("ounce", "ounces"),
        "ml": ("milliliter", "milliliters"), "mL": ("milliliter", "milliliters"), "l": ("liter", "liters"),
        "L": ("liter", "liters"),
        "km/h": ("kilometer per hour", "kilometers per hour"), "mph": ("mile per hour", "miles per hour"),
        "m/s": ("meter per second", "meters per second"),
        "h": ("hour", "hours"), "hr": ("hour", "hours"), "hrs": ("hour", "hours"), "min": ("minute", "minutes"),
        "mins": ("minute", "minutes"), "sec": ("second", "seconds"), "secs": ("second", "seconds"),
        "ms": ("millisecond", "milliseconds"),
        "KB": ("kilobyte", "kilobytes"), "kB": ("kilobyte", "kilobytes"), "MB": ("megabyte", "megabytes"),
        "GB": ("gigabyte", "gigabytes"), "TB": ("terabyte", "terabytes"),
        "MB/s": ("megabyte per second", "megabytes per second"), "GB/s": ("gigabyte per second", "gigabytes per second"),
        "Mbps": ("megabit per second", "megabits per second"), "Gbps": ("gigabit per second", "gigabits per second"),
        "W": ("watt", "watts"), "kW": ("kilowatt", "kilowatts"), "kWh": ("kilowatt hour", "kilowatt hours"),
        "V": ("volt", "volts"), "mAh": ("milliamp hour", "milliamp hours"),
        "Hz": ("hertz", "hertz"), "kHz": ("kilohertz", "kilohertz"), "MHz": ("megahertz", "megahertz"),
        "GHz": ("gigahertz", "gigahertz"),
    ]

    /// Nothing glued before a measured amount: the plan's word edge, and no slash ("3/4 km" is a fraction).
    private static let unitEdge = #"(?<![\p{L}\p{N}.,/])"#
    /// "5 km", "50 km/h", "5-10 min", "20 m²": groups amount (5), unit (the longest first, so "km/h" before "km").
    private static let measured = Pattern(unitEdge + amount + #"\s?("#
        + units.keys.sorted { $0.count > $1.count }.map(NSRegularExpression.escapedPattern(for:)).joined(separator: "|")
        + #")(?![\p{L}\p{N}])"#)
    /// "1h 30m", "2h 15m 30s", "1 h 30 min": hours, minutes and maybe seconds (their "m" is minutes).
    private static let duration = Pattern(unitEdge + #"([0-9]+)\s?h\s?([0-9]+)\s?m(?:in)?(?:\s?([0-9]+)\s?s(?:ec)?)?(?![\p{L}\p{N}])"#)

    // MARK: - Money

    private struct Currency {
        let one: String, many: String
        /// The hundredth's name, or nil (yen): "cent", "cents".
        let hundredth: (one: String, many: String)?
    }

    private static let dollar = Currency(one: "dollar", many: "dollars", hundredth: ("cent", "cents"))
    private static let euro = Currency(one: "euro", many: "euros", hundredth: ("cent", "cents"))
    private static let pound = Currency(one: "pound", many: "pounds", hundredth: ("penny", "pence"))
    private static let yen = Currency(one: "yen", many: "yen", hundredth: nil)
    private static let currencies: [String: Currency] = ["$": dollar, "US$": dollar, "USD": dollar, "€": euro,
                                                         "EUR": euro, "£": pound, "GBP": pound, "¥": yen, "JPY": yen]
    private static let scales = ["k": "thousand", "K": "thousand", "m": "million", "mn": "million", "M": "million",
                                 "bn": "billion", "B": "billion", "T": "trillion"]

    /// A space when a letter is glued after the match.
    private static func gap(_ m: Pattern.Match) -> String {
        m.after.first?.isLetter == true ? " " : ""
    }

    /// An amount of money in words from its five amount groups at `from`: "twelve euros and ninety nine cents", "one
    /// point five million dollars", "ten to fifteen euros", "fifty cents".
    private static func amountOf(_ g: [String], from first: Int, currency symbol: String, scale: String) -> String {
        guard let currency = currencies[symbol] else { return g[0] }
        let (whole, fraction, end) = (g[first + 1], g[first + 2], g[first + 3])
        let sign = g[first].isEmpty ? "" : g[first] == "+" ? "plus " : "minus "
        if !scale.isEmpty {
            return sign + range(g, first).words + " " + (scales[scale] ?? scale) + " " + currency.many
        }
        if !end.isEmpty { return range(g, first).words + " " + currency.many }
        if fraction.count == 2, let hundredth = currency.hundredth, let cents = Int(fraction) {
            let small = Words.cardinal(cents) + " " + (cents == 1 ? hundredth.one : hundredth.many)
            if Int(whole) == 0, cents > 0 { return sign + small }
            let big = Words.number(whole) + " " + (whole == "1" ? currency.one : currency.many)
            return sign + big + (cents > 0 ? " and " + small : "")
        }
        if !fraction.isEmpty { return sign + Words.decimal(whole, fraction) + " " + currency.many }
        return sign + Words.number(whole) + " " + (whole == "1" ? currency.one : currency.many)
    }

    /// After an amount of money: a scale ("5m", "$1.5M", "$1.5 million"), then no more of a word or a number; without
    /// one, no more of a number, so its decimals never back off ("$1.5X" is never "$1" and ".5X"). Two groups.
    private static let scale = #"(?:(bn|mn|m|k|K|M|B|T)(?![\p{L}\p{N}])|\s(thousand|million|billion|trillion)(?![\p{L}\p{N}])"#
        + #"|(?![\p{N}]|\.[0-9]))"#
    /// "$5", "US$5", "€12.99", "£ 3.50", "€10–15", "$1.5 million": groups symbol, amount (5), scale (2).
    private static let symbolFirst = Pattern(#"(?<![\p{L}\p{N}])(US\$|[$€£¥])\s?"# + amount + scale)
    /// "EUR 20", "USD 1": groups code, amount (5), scale (2).
    private static let codeFirst = Pattern(#"(?<![\p{L}\p{N}])(EUR|USD|GBP|JPY)\s?"# + amount + scale)
    /// "5 EUR", "12€", "5.50 €": groups amount (5), symbol or code.
    private static let currencyAfter = Pattern(edge + amount + #"\s?([€£¥]|EUR|USD|GBP|JPY)(?![\p{L}\p{N}])"#)
    /// A percent takes its decimals ("2.5%" was "two.five percent"), a minus and a range ("10–15%"): groups amount (5).
    private static let percent = Pattern(edge + amount + #"\s?%"#)
}
