import Foundation

/// Kokoro's own pronunciations to put over its English lexicon (FluidAudio's `setEnglishCustomLexicon`, which checks
/// the exact spelling first, then the lower-case one): capitals that Kokoro reads as words but are meant as letters.
/// Misaki has "ID" as "id", "ETA" as the Greek letter and "WHO" as "who"; an entry by its capitals leaves "id" and
/// "who" as they were. Pure data, tested; the voice service hands it to Kokoro.
public enum EnglishLexicon {
    /// Capitals said as their letters ("I D", "E T A", "W H O").
    static let spelledWords = ["ID", "ETA", "WHO"]

    /// Each letter's name as Misaki writes it (its own entries for "A" to "Z", which FluidAudio spells "FBI" from).
    static let letterNames: [Character: String] = [
        "A": "ˈA", "B": "bˈi", "C": "sˈi", "D": "dˈi", "E": "ˈi", "F": "ˈɛf", "G": "ʤˈi", "H": "ˈAʧ", "I": "ˈI",
        "J": "ʤˈA", "K": "kˈA", "L": "ˈɛl", "M": "ˈɛm", "N": "ˈɛn", "O": "ˈO", "P": "pˈi", "Q": "kjˈu", "R": "ˈɑɹ",
        "S": "ˈɛs", "T": "tˈi", "U": "jˈu", "V": "vˈi", "W": "dˈʌbᵊlju", "X": "ˈɛks", "Y": "wˈI", "Z": "zˈi",
    ]

    /// The word's letters' names, a space between them (as FluidAudio spells an initialism).
    static func spelled(_ word: String) -> String {
        word.compactMap { letterNames[$0] }.joined(separator: " ")
    }

    /// The table for `setEnglishCustomLexicon`: word as written to its pronunciation.
    public static let entries: [String: String] = Dictionary(
        uniqueKeysWithValues: spelledWords.map { ($0, spelled($0)) })
}
