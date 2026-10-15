import Foundation

/// What one run does.
public enum Command: Equatable, Sendable {
    /// `read <absolute path> [--max-pages N] [--max-seconds S]`: one PDF's text.
    case read(path: String, maxPages: Int, maxSeconds: Int)
    /// `warm [--max-seconds S]`: one text recognition of a small built-in picture, no file read, so macOS prepares
    /// Vision's model for this copy of the helper before a document needs it.
    case warm(maxSeconds: Int)
}

/// The helper's arguments, read strictly: the path right after `read`, absolute; each limit at most once, a whole
/// number from 1 up; anything else makes the whole call invalid (nil), never a guess.
public enum Arguments {
    public static func parse(_ argv: [String]) -> Command? {
        guard let verb = argv.first else { return nil }
        switch verb {
        case "read":
            guard argv.count >= 2, isAbsolute(argv[1]),
                let limits = options(Array(argv.dropFirst(2)), allowed: ["--max-pages", "--max-seconds"])
            else { return nil }
            return .read(
                path: argv[1], maxPages: limits["--max-pages"] ?? Limits.maxPages,
                maxSeconds: limits["--max-seconds"] ?? Limits.maxSeconds)
        case "warm":
            guard let limits = options(Array(argv.dropFirst()), allowed: ["--max-seconds"]) else { return nil }
            return .warm(maxSeconds: limits["--max-seconds"] ?? Limits.warmSeconds)
        default:
            return nil
        }
    }

    private static func isAbsolute(_ path: String) -> Bool {
        path.hasPrefix("/") && !path.contains("\u{0}")
    }

    /// `--name value` pairs, each name in `allowed` and at most once; nil on anything else.
    private static func options(_ rest: [String], allowed: Set<String>) -> [String: Int]? {
        guard rest.count % 2 == 0 else { return nil }
        var found: [String: Int] = [:]
        for index in stride(from: 0, to: rest.count, by: 2) {
            let name = rest[index]
            guard allowed.contains(name), found[name] == nil, let value = number(rest[index + 1], name: name) else {
                return nil
            }
            found[name] = value
        }
        return found
    }

    private static func number(_ text: String, name: String) -> Int? {
        let highest = name == "--max-pages" ? Limits.highestPages : Limits.highestSeconds
        guard text.allSatisfy(\.isASCII), text.allSatisfy(\.isNumber), let value = Int(text), value >= 1,
            value <= highest
        else { return nil }
        return value
    }
}
