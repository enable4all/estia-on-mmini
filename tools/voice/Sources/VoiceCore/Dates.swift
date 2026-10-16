import Foundation

/// Dates as people say them (NeMo's English dates): day first is "Friday the sixteenth of October", month first
/// "October sixteenth, twenty twenty six", and an ISO date ("2026-10-16") is read month first. A short day or month
/// name changes only next to a day number ("Sun is out", "May I", "Mon ami" stay), never before hours ("Fri 9-5"); a
/// short month name before a year changes in `Years` ("Oct 2026"). A numeric date whose day and month could swap
/// (both 12 or less: "3/4/2026") is not guessed.
enum Dates {
    static func spoken(_ text: String) -> String {
        var s = iso.rewrite(text) { m in
            guard let year = Int(m[1]), let month = Int(m[2]), let day = Int(m[3]) else { return nil }
            return monthFirst(monthNames[month - 1], day, comma: ",", year: year)
        }
        s = numeric.rewrite(s) { m in
            guard let first = Int(m[1]), let second = Int(m[3]), let year = Int(m[4]) else { return nil }
            if (13...31).contains(first), (1...12).contains(second) {
                return dayFirst(first, nil, monthNames[second - 1], year: year)
            }
            if m[2] == "/", (1...12).contains(first), (13...31).contains(second) {
                return monthFirst(monthNames[first - 1], second, comma: ",", year: year)
            }
            return nil
        }
        s = dayMonth.rewrite(s) { m in
            guard let day = day(m[4], m[5]), let month = month(m[8]) else { return nil }
            let last = m[6].isEmpty ? nil : self.day(m[6], m[7])
            if !m[6].isEmpty, last == nil { return nil }
            let lead = weekday(m[1]).map { $0 + m[2] + " " } ?? ""
            let year = Int(m[10]).map { m[9] + " " + Words.year($0) } ?? ""
            return lead + dayFirst(day, last, month, the: m[3].isEmpty ? "the" : m[3]) + year
        }
        s = monthDay.rewrite(s) { m in
            guard let month = month(m[3]), let day = day(m[4], m[5]) else { return nil }
            let last = m[6].isEmpty ? nil : self.day(m[6], m[7])
            if !m[6].isEmpty, last == nil { return nil }
            let lead = weekday(m[1]).map { $0 + m[2] + " " } ?? ""
            let words = month + " " + Words.ordinal(day) + (last.map { " to " + Words.ordinal($0) } ?? "")
            return lead + words + (Int(m[9]).map { m[8] + " " + Words.year($0) } ?? "")
        }
        return weekdayDay.rewrite(s) { m in
            guard let name = weekday(m[1]), let day = day(m[4], m[5]) else { return nil }
            return name + m[2] + " " + (m[3].isEmpty ? "the" : m[3]) + " " + Words.ordinal(day)
        }
    }

    // MARK: - Reading

    /// "the sixteenth of October", "the sixteenth to the eighteenth of October", with a year after it.
    private static func dayFirst(_ day: Int, _ last: Int?, _ month: String, the: String = "the", year: Int? = nil) -> String {
        let days = the + " " + Words.ordinal(day) + (last.map { " to the " + Words.ordinal($0) } ?? "")
        return days + " of " + month + (year.map { " " + Words.year($0) } ?? "")
    }

    /// "October sixteenth, twenty twenty six".
    private static func monthFirst(_ month: String, _ day: Int, comma: String, year: Int) -> String {
        month + " " + Words.ordinal(day) + comma + " " + Words.year(year)
    }

    /// A day number 1 to 31 with an ending that fits it ("16th", never "16st"), or nil.
    private static func day(_ digits: String, _ ending: String) -> Int? {
        guard let n = Int(digits), (1...31).contains(n), ending.isEmpty || ending == Words.ordinalSuffix(n) else { return nil }
        return n
    }

    private static let monthNames = ["January", "February", "March", "April", "May", "June", "July", "August",
                                     "September", "October", "November", "December"]
    private static let shortMonths = ["Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April",
                                      "Jun": "June", "Jul": "July", "Aug": "August", "Sep": "September",
                                      "Sept": "September", "Oct": "October", "Nov": "November", "Dec": "December"]
    private static let weekdays = ["Mon": "Monday", "Tue": "Tuesday", "Tues": "Tuesday", "Wed": "Wednesday",
                                   "Thu": "Thursday", "Thur": "Thursday", "Thurs": "Thursday", "Fri": "Friday",
                                   "Sat": "Saturday", "Sun": "Sunday"]

    private static func month(_ name: String) -> String? {
        monthNames.contains(name) ? name : shortMonths[name]
    }

    /// A month's full name from its name as written, a short one's dot left out ("Oct", "Sept." -> "September").
    static func fullMonth(_ written: String) -> String {
        month(written.hasSuffix(".") ? String(written.dropLast()) : written) ?? written
    }

    private static func weekday(_ name: String) -> String? {
        name.isEmpty ? nil : weekdays[name] ?? name
    }

    // MARK: - Patterns

    private static let months = "January|February|March|April|May|June|July|August|September|October|November|December"
        + "|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec"
    private static let days = "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
        + "|Mon|Tues|Tue|Wed|Thurs|Thur|Thu|Fri|Sat|Sun"
    /// Nothing glued before a date: a word, a number, a dot, a comma, a colon, a slash or a dash ("16-18 Oct" is a range).
    private static let start = #"(?<![\p{L}\p{N}.,/:–-])"#
    /// A day name, its dot and comma: groups weekday, comma.
    private static let weekdayPart = "(" + days + #")(?![\p{L}])\.?(,?)"#
    /// A day number and its ending: groups digits, ending.
    private static let dayPart = #"(3[01]|[12][0-9]|0?[1-9])(st|nd|rd|th)?"#
    /// A month name; a short one's dot only before a number ("Oct. 2026"), else it may end the sentence.
    private static let monthPart = "(" + months + #")(?![\p{L}])(?:\.(?=\s*[0-9]))?"#
    /// After a day number: no more of a number, a time or a word, and no range of hours ("Fri 9-5" is no date).
    private static let dayEnd = #"(?![\p{L}\p{N}:]|[.,][0-9]|\s*[-–]\s*[0-9])"#
    private static let yearPart = #"(?:(,?)\s+([12][0-9]{3})(?![\p{L}\p{N}]))?"#

    /// "2026-10-16": groups year, month, day.
    private static let iso = Pattern(start + #"([12][0-9]{3})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])(?![\p{N}])"#)
    /// "16.10.2026", "16/10/2026", "10/16/2026": groups first, separator, second, year.
    private static let numeric = Pattern(start + #"([0-9]{1,2})([./])([0-9]{1,2})\2([12][0-9]{3})(?![\p{N}]|[./][0-9])"#)
    /// "Fri 16 Oct", "the 16th of October 2026", "16-18 Oct": groups weekday, comma, the, day, ending, last day,
    /// its ending, month, comma, year.
    private static let dayMonth = Pattern(start + "(?:" + weekdayPart + #"\s+)?(?:(the|The)\s+)?"# + dayPart
        + #"(?:\s*[-–]\s*"# + dayPart + #")?(?:\s+of)?\s+"# + monthPart + yearPart)
    /// "Oct 16, 2026", "Thu Oct 22", "Oct 16-18": groups weekday, comma, month, day, ending, last day, its ending,
    /// comma, year.
    private static let monthDay = Pattern(start + "(?:" + weekdayPart + #"\s+)?"# + monthPart + #"\s+"# + dayPart
        + #"(?:\s*[-–]\s*"# + dayPart + ")?" + dayEnd + yearPart)
    /// "Fri 16" (the calendar's days within a week): groups weekday, comma, the, day, ending.
    private static let weekdayDay = Pattern(start + weekdayPart + #"\s+(?:(the)\s+)?"# + dayPart + dayEnd)
}
