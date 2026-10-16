import Foundation
import Testing
@testable import VoiceCore

/// What Kokoro is given for an English text: the call's and the voice note's path (`SpeechRequest.spokenInput`).
private func spoken(_ text: String) throws -> String {
    let body = try JSONSerialization.data(withJSONObject: ["input": text])
    return try SpeechRequest(json: body).spokenInput
}

/// The 0.9.2 pronunciation plan's rows: what is written, what Kokoro is given. Each class keeps rows that must stay as
/// they are; every row runs through the whole English path, so a pass in the wrong order fails here.
enum PronunciationTable {
    static let rows: [(kind: String, written: String, said: String)] = [
        // chords run first; the number passes leave them alone
        ("chords", "C G7 Am F", "C major, G seven, A minor, F major"),
        ("chords", "Em7 - Cadd9 - G/B - D/F#", "E minor seven, C add nine, G over B, D over F sharp"),
        // cardinals up to trillions; a comma after a number stays (a pause)
        ("big numbers", "1,000,000", "one million"),
        ("big numbers", "1,250,000 people", "one million two hundred fifty thousand people"),
        ("big numbers", "2,000,000,000", "two billion"),
        ("big numbers", "999,999,999,999,999",
         "nine hundred ninety nine trillion nine hundred ninety nine billion nine hundred ninety nine million nine hundred ninety nine thousand nine hundred ninety nine"),
        ("big numbers", "1,000,000,000,000,000", "1,000,000,000,000,000"),  // past trillions: as written
        ("big numbers", "the 3, 4 and 5", "the three, four and five"),
        ("big numbers", "1,000 and 1,2,3", "one thousand and one,two,three"),
        // a decimal is read before its percent sign
        ("decimals", "2.5%", "two point five percent"),
        ("decimals", "20% off", "twenty percent off"),
        // dates: day first "Friday the sixteenth of October", month first "October sixteenth, twenty twenty six"
        ("dates", "Fri 16 Oct", "Friday the sixteenth of October"),
        ("dates", "Mon 19 Oct", "Monday the nineteenth of October"),
        ("dates", "Oct 16, 2026", "October sixteenth, twenty twenty six"),
        ("dates", "2026-10-16", "October sixteenth, twenty twenty six"),
        ("dates", "16.10.2026", "the sixteenth of October twenty twenty six"),
        ("dates", "16/10/2026", "the sixteenth of October twenty twenty six"),
        ("dates", "10/16/2026", "October sixteenth, twenty twenty six"),
        ("dates", "Fri 16 Oct 2026", "Friday the sixteenth of October twenty twenty six"),
        ("dates", "Sat, 17 Oct: nothing", "Saturday, the seventeenth of October: nothing"),
        ("dates", "Fri 16 at 14:00", "Friday the sixteenth at two in the afternoon"),
        // a day name before hours is no date: the hours are read
        ("dates", "Open Mon to Fri 9-5", "Open Mon to Fri nine-five"),
        ("dates", "Sat 10-4 and Mon to Sat 9-17", "Sat ten-four and Mon to Sat nine to seventeen"),
        ("dates", "Oct 16-18 and Fri 16–18 Oct", "October sixteenth to eighteenth and Friday the sixteenth to the eighteenth of October"),
        ("dates", "Thu Oct 22", "Thursday October twenty second"),
        ("dates", "on the 5 Dec.", "on the fifth of December."),
        ("dates", "Jan 1st and 2nd March", "January first and the second of March"),
        ("dates", "16-18 Oct", "the sixteenth to the eighteenth of October"),
        ("dates", "Sept 3, 1998", "September third, nineteen ninety eight"),
        ("dates", "1 May 2005", "the first of May two thousand five"),
        ("dates", "Sun is out", "Sun is out"),
        ("dates", "May I", "May I"),
        ("dates", "Mon ami", "Mon ami"),
        ("dates", "3/4/2026", "three/four/two thousand twenty six"),  // day and month could swap: no date guessed
        ("dates", "3.4.2026", "three point four.two thousand twenty six"),  // the same, read as before
        ("dates", "31 Oct 2026, then 2 Nov", "the thirty first of October twenty twenty six, then the second of November"),
        // ordinals: the ending must fit the number; one that does not is no ordinal (its letters are read apart)
        ("ordinals", "21st", "twenty first"),
        ("ordinals", "12th", "twelfth"),
        ("ordinals", "11th, 12th and 13th", "eleventh, twelfth and thirteenth"),
        ("ordinals", "22nd, 23rd, 111th and 101st", "twenty second, twenty third, one hundred eleventh and one hundred first"),
        ("ordinals", "2th", "two th"),
        ("ordinals", "1rd", "one rd"),
        ("ordinals", "16st Oct", "sixteen st Oct"),
        // temperatures and minus: a minus only after a space, the start or "("
        ("temperatures", "20°C", "twenty degrees Celsius"),
        ("temperatures", "72°F", "seventy two degrees Fahrenheit"),
        ("temperatures", "-5°C", "minus five degrees Celsius"),
        ("temperatures", "20°", "twenty degrees"),
        ("temperatures", "1°C, -1 °C and 21.5 °C", "one degree Celsius, minus one degree Celsius and twenty one point five degrees Celsius"),
        ("temperatures", "18–22°C today (−3°C at night)",
         "eighteen to twenty two degrees Celsius today (minus three degrees Celsius at night)"),
        ("temperatures", "20℃", "twenty degrees Celsius"),
        ("minus", "-20 euros", "minus twenty euros"),
        ("minus", "it was -5 and windy", "it was minus five and windy"),
        ("minus", "COVID-19", "COVID-nineteen"),
        // money: € £ ¥ and EUR, GBP, USD, JPY, before or after the amount; big amounts with million, billion, k, m, bn
        ("money", "€1", "one euro"),
        ("money", "€12.99", "twelve euros and ninety nine cents"),
        ("money", "£3.50", "three pounds and fifty pence"),
        ("money", "£1.01", "one pound and one penny"),
        ("money", "¥100", "one hundred yen"),
        ("money", "5 EUR", "five euros"),
        ("money", "EUR 20, USD 1 and 20 GBP", "twenty euros, one dollar and twenty pounds"),
        ("money", "12€ or 5.50 €", "twelve euros or five euros and fifty cents"),
        ("money", "$1.5 million", "one point five million dollars"),
        ("money", "€2 billion, $3bn, $5m and $5k", "two billion euros, three billion dollars, five million dollars and five thousand dollars"),
        ("money", "€1,250,000", "one million two hundred fifty thousand euros"),
        // a capital scale and "mn" too; the decimals never back off ("$1.5M" was "one dollar.five M")
        ("money", "$1.5M, €2.5B and £1.5M", "one point five million dollars, two point five billion euros and one point five million pounds"),
        ("money", "$5M, $3B, $2T and $4mn", "five million dollars, three billion dollars, two trillion dollars and four million dollars"),
        ("money", "USD 1.5M", "one point five million dollars"),
        ("money", "$2.99each", "two dollars and ninety nine cents each"),
        ("money", "US$5 and $5 thousand", "five dollars and five thousand dollars"),
        ("money", "€10–15 and $5-10", "ten to fifteen euros and five to ten dollars"),
        ("money", "$0.50 or €0.99", "fifty cents or ninety nine cents"),
        ("money", "$20", "twenty dollars"),
        ("money", "$5.99 and $13.00", "five dollars and ninety nine cents and thirteen dollars"),
        ("money", "EUR", "EUR"),
        ("money", "the EUR rate", "the EUR rate"),
        // units and durations: only right after a number, case-sensitive (5G is letters, 5g grams), singular only at 1
        ("units", "1 km", "one kilometer"),
        ("units", "50 km/h", "fifty kilometers per hour"),
        ("units", "23 kg", "twenty three kilograms"),
        ("units", "500 MB/s", "five hundred megabytes per second"),
        ("units", "1h 30m", "one hour thirty minutes"),
        ("units", "2h 15m 30s, 1 h 30 min", "two hours fifteen minutes thirty seconds, one hour thirty minutes"),
        ("units", "5-10 min", "five to ten minutes"),
        ("units", "1–2 km", "one to two kilometers"),
        ("units", "12 min, 4.3 km", "twelve minutes, four point three kilometers"),
        ("units", "5G", "five G"),
        ("units", "5g of sugar and 1 g of salt", "five grams of sugar and one gram of salt"),
        ("units", "a 100m sprint, 0.5 km", "a one hundred meters sprint, zero point five kilometers"),
        ("units", "2 h, 1 hr, 90 mins, 30 sec, 250 ms", "two hours, one hour, ninety minutes, thirty seconds, two hundred fifty milliseconds"),
        ("units", "500 ml, 1 L, 2 l", "five hundred milliliters, one liter, two liters"),
        ("units", "60 mph, 10 m/s", "sixty miles per hour, ten meters per second"),
        ("units", "5 mi, 6 ft, 1 ft", "five miles, six feet, one foot"),
        ("units", "1 lb, 10 lbs, 8 oz", "one pound, ten pounds, eight ounces"),
        ("units", "3 GB, 1 TB, 512 KB, 100 Mbps, 1 Gbps",
         "three gigabytes, one terabyte, five hundred twelve kilobytes, one hundred megabits per second, one gigabit per second"),
        ("units", "5 kW, 3 kWh, 60 W, 12 V, 5000 mAh",
         "five kilowatts, three kilowatt hours, sixty watts, twelve volts, five thousand milliamp hours"),
        ("units", "50 Hz, 2.4 GHz", "fifty hertz, two point four gigahertz"),
        ("units", "20 m² and 5 km²", "twenty square meters and five square kilometers"),
        ("units", "km", "km"),
        ("units", "the kg and the mph", "the kg and the mph"),
        ("units", "16 Gb free", "sixteen Gb free"),  // Gb, Mb: left out (a chord's "Gb", or GB written small)
        ("units", "5 cm, 5km", "five centimeters, five kilometers"),
        // abbreviations (NeMo's list); "St." is Saint before a name, Street after one
        ("abbreviations", "e.g. apples", "for example apples"),
        ("abbreviations", "Fruit (e.g., pears), i.e. the green ones", "Fruit (for example, pears), that is the green ones"),
        ("abbreviations", "E.g. this", "For example this"),
        ("abbreviations", "PSV vs. Ajax", "PSV versus Ajax"),
        ("abbreviations", "PSV vs Ajax", "PSV versus Ajax"),
        ("abbreviations", "No. 5", "number five"),
        ("abbreviations", "no.7 and No.5", "number seven and number five"),
        ("abbreviations", "approx. 5 km, Approx 20", "approximately five kilometers, Approximately twenty"),
        ("abbreviations", "St. Mark's", "Saint Mark's"),
        ("abbreviations", "visit St. Paul and The St. Louis arch", "visit Saint Paul and The Saint Louis arch"),
        ("abbreviations", "Main St. is closed", "Main Street is closed"),
        ("abbreviations", "5th St. and Main St.", "fifth Street and Main Street."),  // a last dot also ends the sentence
        ("abbreviations", "Main St.", "Main Street."),
        // before a capitalised word "St." is Saint, whatever stands before it, but a number ("5th St. Bakery")
        ("abbreviations", "Mount St. Helens, Fort St. John and Paris, St. Tropez",
         "Mount Saint Helens, Fort Saint John and Paris, Saint Tropez"),
        ("abbreviations", "Then St. Paul's opens. Nearby St. Mark's Square", "Then Saint Paul's opens. Nearby Saint Mark's Square"),
        ("abbreviations", "5th St. Bakery and 3rd St. Market", "fifth Street Bakery and third Street Market"),
        // a word that starts a sentence is no saint's name: the dot ended the sentence
        ("abbreviations", "It's on Main St. Then turn left.", "It's on Main Street. Then turn left."),
        ("abbreviations", "Meet at Main St. I'll wait.", "Meet at Main Street. I'll wait."),
        ("abbreviations", "Paris, St. is", "Paris, St. is"),
        ("abbreviations", "like pears, e.g.", "like pears, for example."),
        ("abbreviations", "No. That's it.", "No. That's it."),
        ("abbreviations", "say no. Then", "say no. Then"),
        ("abbreviations", "I said no. 5 people agreed.", "I said no. five people agreed."),  // "no." with a space is the word
        ("abbreviations", "etc.", "etc."),
        ("abbreviations", "the vsync and Steve", "the vsync and Steve"),
        // years and decades: a year is read as one only in a year's context
        ("years", "the 1990s", "the nineteen nineties"),
        ("years", "the '90s", "the nineties"),
        ("years", "the class of '05, ’09 and '69", "the class of oh five, oh nine and sixty nine"),
        ("years", "code '05' and '007'", "code 'zero five' and 'zero zero seven'"),  // in quotes: no year
        ("years", "the 80s", "the eighties"),
        ("years", "the 1900s", "the nineteen hundreds"),
        ("years", "in 1998", "in nineteen ninety eight"),
        ("years", "in 2005", "in two thousand five"),
        ("years", "In 1066, since 2010, by 2030 and around 1900", "In ten sixty six, since twenty ten, by twenty thirty and around nineteen hundred"),
        ("years", "until 2027, from 1995, before 1914, after 1945", "until twenty twenty seven, from nineteen ninety five, before nineteen fourteen, after nineteen forty five"),
        ("years", "1939-1945", "nineteen thirty nine to nineteen forty five"),
        ("years", "1939–45", "nineteen thirty nine to forty five"),
        ("years", "2025-26", "twenty twenty five to twenty six"),
        ("years", "October 2026", "October twenty twenty six"),
        ("years", "in Oct 2026, Sept. 1998 and Mar 2020", "in October twenty twenty six, September nineteen ninety eight and March twenty twenty"),
        ("years", "May 2026", "May twenty twenty six"),
        ("years", "Casablanca (1942)", "Casablanca (nineteen forty two)"),
        ("years", "1066 AD, AD 79 and 2500 BC", "ten sixty six AD, AD seventy nine and twenty five hundred BC"),
        ("years", "1998 people", "one thousand nine hundred ninety eight people"),
        ("years", "1,998", "one thousand nine hundred ninety eight"),
        ("years", "2023-10 and 13pm", "two thousand twenty three-ten and thirteen pm"),
        ("years", "in 5 minutes and in 15000 years", "in five minutes and in fifteen thousand years"),
        ("years", "From 2020 to 2025, between 1990 and 2010", "From twenty twenty to twenty twenty five, between nineteen ninety and twenty ten"),
        // phones and leading zeros: digit by digit, a comma between the groups ("zero", never "oh")
        ("phones", "+44 20 7946 0958", "plus four four, two zero, seven nine four six, zero nine five eight"),
        ("phones", "0800 123 456", "zero eight zero zero, one two three, four five six"),
        ("phones", "+306900000000", "plus three zero six, nine zero zero, zero zero zero, zero zero zero"),
        ("phones", "+1-555-0100 or 0800-123-456", "plus one, five five five, zero one zero zero or zero eight zero zero, one two three, four five six"),
        ("phones", "Call 0800123456 now.", "Call zero eight zero, zero one two, three four five six now."),
        ("phones", "007", "zero zero seven"),
        ("phones", "gate 05 and A01", "gate zero five and A zero one"),
        ("phones", "0.5", "zero point five"),
        ("phones", "3.05", "three point zero five"),
        ("phones", "07:00", "seven in the morning"),
        ("phones", "Room 101, +5 and 0", "Room one hundred one, plus five and zero"),
        // groups only at a phone's length (7 digits or more); a slash date's numbers are numbers
        ("phones", "Open 09-17 daily", "Open nine to seventeen daily"),
        // a North American number's shapes, never a range
        ("phones", "(555) 123-4567 or 555-123-4567",
         "five five five, one two three, four five six seven or five five five, one two three, four five six seven"),
        ("phones", "pages 100-200 and 500-1500 kcal", "pages one hundred to two hundred and five hundred to one thousand five hundred kcal"),
        ("phones", "05/10/2026 and 10/05/2026", "five/ten/two thousand twenty six and ten/five/two thousand twenty six"),
        // emails and bare domains (NeMo's electronic); links are dropped before this, as before
        ("electronic", "info@example.com", "info at example dot com"),
        ("electronic", "example.com", "example dot com"),
        ("electronic", "Visit www.example.com.", "Visit WWW dot example dot com."),
        ("electronic", "alex.example@example.co.uk", "alex dot example at example dot co dot UK"),
        ("electronic", "example.nl, example.gr and example.io", "example dot NL, example dot GR and example dot IO"),
        ("electronic", "first_last+news@example-site.example", "first underscore last plus news at example dash site dot example"),
        ("electronic", "Node.js and file.txt", "Node dot JS and file dot TXT"),
        ("electronic", "like e.g. this", "like for example this"),
        ("electronic", "macOS 15.6", "macOS fifteen point six"),
        ("electronic", "the U.S. and 8 a.m.", "the U.S. and eight in the morning."),
        ("electronic", "1.2.beta and end.Then", "one point two.beta and end.Then"),
        // percent with decimals, bare ranges said with "to", fractions (n < d ≤ 10, never inside a slash date)
        ("ranges", "10–15%", "ten to fifteen percent"),
        ("ranges", "-5% and 2.5–3.5%", "minus five percent and two point five to three point five percent"),
        ("ranges", "5-10", "five to ten"),
        ("ranges", "pages 10–20, 1.5-2.5 and 5 – 10", "pages ten to twenty, one point five to two point five and five to ten"),
        ("ranges", "the score was 3-1, then 1-1", "the score was three-one, then one-one"),
        ("ranges", "1-2-3", "one-two-three"),
        ("fractions", "3/4", "three quarters"),
        ("fractions", "2 1/2", "two and a half"),
        ("fractions", "½", "one half"),
        ("fractions", "1/2, 1/3, 2/3, 1/4 and 1/10", "one half, one third, two thirds, one quarter and one tenth"),
        ("fractions", "3/8 cup, 2 3/4 and 1 1/4", "three eighths cup, two and three quarters and one and a quarter"),
        ("fractions", "2½, ¼ and ¾, ⅓", "two and a half, one quarter and three quarters, one third"),
        ("fractions", "24/7", "twenty four seven"),
        ("fractions", "9/11", "nine eleven"),
        ("fractions", "50/50 and 5/3", "fifty fifty and five three"),
        // letter and digit codes, versions, and "x" glued to a number (never "0x…")
        ("codes", "KL1234", "KL one two three four"),
        ("codes", "flight AB12345 at gate A3", "flight AB one two three four five at gate A three"),
        ("codes", "MP3, B12 and A320", "MP three, B twelve and A three hundred twenty"),
        ("codes", "1234AB", "one thousand two hundred thirty four AB"),
        ("versions", "v0.9.1", "version zero point nine point one"),
        ("versions", "v1.5 and 0.10.2", "version one point five and zero point ten point two"),
        ("versions", "v2 and v8", "v two and v eight"),
        ("times", "x2", "times two"),
        ("times", "2x faster, x10 and 1.5x", "two times faster, times ten and one point five times"),
        ("times", "a 4x4 and 1920x1080", "a four by four and one thousand nine hundred twenty by one thousand eighty"),
        ("times", "0x1F", "zero x one F"),  // hexadecimal: no "times"
        ("times", "x86 and x64, x32, x264 and x265",  // processor and video names: no "times"
         "x eighty six and x sixty four, x thirty two, x two hundred sixty four and x two hundred sixty five"),
        ("times", "BMW X2 and box2", "BMW X two and box two"),
        // symbols & + = × ± # ("+" only before a digit, "#" too)
        ("symbols", "Tom & Jerry", "Tom and Jerry"),
        ("symbols", "R&D and AT&T", "R and D and AT and T"),
        ("symbols", "2+2=4", "two plus two equals four"),
        ("symbols", "2 + 3 = 5 and x = 7", "two plus three equals five and x equals seven"),
        ("symbols", "3×4 and 2 × 3", "three times four and two times three"),
        ("symbols", "±5 and ± 0.5 mm", "plus or minus five and plus or minus zero point five millimeters"),
        ("symbols", "+5°C and (+3%)", "plus five degrees Celsius and (plus three percent)"),
        ("symbols", "#1 hit", "number one hit"),
        ("symbols", "C++", "C++"),
        ("symbols", "#tag", "#tag"),
        ("symbols", "an A+ and Google+", "an A+ and Google+"),
        ("symbols", "a = b", "a = b"),
    ]
}

@Suite struct Pronunciation {
    @Test(arguments: PronunciationTable.rows)
    func everyRowIsReadAsPlanned(row: (kind: String, written: String, said: String)) throws {
        #expect(try spoken(row.written) == row.said, "\(row.kind): \(row.written)")
    }

    /// A minus only after a space, the start or "(": never inside a range, a name or a date.
    @Test(arguments: ["5-10", "COVID-19", "1-2 days", "2023-10", "pages 3-4", "B-52", "x-1"])
    func aDashBetweenIsNoMinus(written: String) throws {
        #expect(!(try spoken(written)).contains("minus"), "\(written)")
    }

    /// The plan's run order: a pass that runs early owns its text (times take their colon before any number is read).
    @Test func thePassesRunInThePlansOrder() {
        #expect(SpokenEnglish.order == [
            "chords", "abbreviations", "emails and domains", "times", "dates", "phones and leading zeros", "measures", "years and decades", "bare ranges and fractions", "versions", "ordinals", "letters and digits", "symbols", "decimals and cardinals",
        ])
    }
}

/// Capitals Kokoro reads as words (Misaki has "ID" as "id", "ETA" as the Greek letter, "WHO" as "who"): the custom
/// lexicon gives them their letters' names, by exact spelling only, so "id" and "who" keep theirs.
@Suite struct CapitalsAsLetters {
    @Test func idEtaAndWhoAreTheirLettersNames() {
        #expect(EnglishLexicon.entries == ["ID": "ˈI dˈi", "ETA": "ˈi tˈi ˈA", "WHO": "dˈʌbᵊlju ˈAʧ ˈO"])
    }

    @Test func onlyTheCapitalsHaveAnEntry() {
        #expect(EnglishLexicon.entries["id"] == nil && EnglishLexicon.entries["who"] == nil && EnglishLexicon.entries["eta"] == nil)
        #expect(EnglishLexicon.entries.keys.allSatisfy { $0 == $0.uppercased() })
    }

    /// The text keeps them as written: the lexicon, not the text, says them.
    @Test func theTextKeepsThem() throws {
        #expect(try spoken("Your ID, the ETA and the WHO") == "Your ID, the ETA and the WHO")
    }
}
