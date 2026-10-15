import Testing

@testable import DocumentsCore

/// The bridge runs `documents-helper read <absolute path> --max-pages 300 --max-seconds 60`: anything else is
/// refused as a whole, never guessed.
@Suite struct ArgumentReading {
    @Test func theBridgesOwnCallIsRead() {
        let command = Arguments.parse(["read", "/tmp/lease.pdf", "--max-pages", "300", "--max-seconds", "60"])
        #expect(command == .read(path: "/tmp/lease.pdf", maxPages: 300, maxSeconds: 60))
    }

    @Test func theLimitsMayComeInEitherOrderAndDefaultToTheOwnersLimits() {
        #expect(
            Arguments.parse(["read", "/a.pdf", "--max-seconds", "5", "--max-pages", "2"])
                == .read(path: "/a.pdf", maxPages: 2, maxSeconds: 5))
        #expect(Arguments.parse(["read", "/a.pdf"]) == .read(path: "/a.pdf", maxPages: 300, maxSeconds: 60))
        #expect(Limits.maxPages == 300 && Limits.maxSeconds == 60)
    }

    @Test func aDocumentIsAtMost25MBAsFinderCountsThem() {
        #expect(Limits.maxBytes == 25_000_000)
    }

    @Test func warmTakesOnlyItsTimeLimit() {
        #expect(Arguments.parse(["warm"]) == .warm(maxSeconds: Limits.warmSeconds))
        #expect(Arguments.parse(["warm", "--max-seconds", "30"]) == .warm(maxSeconds: 30))
        #expect(Arguments.parse(["warm", "/a.pdf"]) == nil)
        #expect(Arguments.parse(["warm", "--max-pages", "3"]) == nil)
    }

    @Test(arguments: [
        [],
        ["read"],
        ["open", "/a.pdf"],
        ["read", "a.pdf"],
        ["read", "./a.pdf"],
        ["read", ""],
        ["read", "/a.pdf", "/b.pdf"],
        ["read", "/a.pdf", "--max-pages"],
        ["read", "/a.pdf", "--max-pages", "0"],
        ["read", "/a.pdf", "--max-pages", "-3"],
        ["read", "/a.pdf", "--max-pages", "ten"],
        ["read", "/a.pdf", "--max-pages", "3.5"],
        ["read", "/a.pdf", "--max-seconds", "0"],
        ["read", "/a.pdf", "--max-seconds", "9223372036854775807"],
        ["read", "/a.pdf", "--max-pages", "99999999999999999999"],
        ["read", "/a.pdf", "--max-pages", "3", "--max-pages", "4"],
        ["read", "/a.pdf", "--pages", "3"],
        ["read", "--max-pages", "3", "/a.pdf"],
        ["read", "/a.pdf\u{0}.txt"],
    ])
    func anythingElseIsRefused(_ argv: [String]) {
        #expect(Arguments.parse(argv) == nil)
    }
}
