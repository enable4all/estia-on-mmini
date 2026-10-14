import Foundation
import Testing

@testable import VoiceCore

/// voice.log is the service's stdout and stderr (launchd opens it for appending): kept under its cap in-process, so
/// nothing outside the service (no newsyslog, no root) touches its log.
@Suite struct LogCapping {
    private func file(_ bytes: Int) throws -> (URL, Int32) {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("logcap-\(UUID().uuidString).log")
        try Data(repeating: UInt8(ascii: "x"), count: bytes).write(to: url)
        let fd = open(url.path, O_WRONLY | O_APPEND)
        #expect(fd >= 0)
        return (url, fd)
    }

    private func size(_ url: URL) throws -> Int {
        (try FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? -1
    }

    @Test func aFileOverTheCapIsEmptiedAndWritingGoesOnAtItsStart() throws {
        let (url, fd) = try file(2_000)
        defer { close(fd); try? FileManager.default.removeItem(at: url) }
        #expect(LogCap.trim(fd: fd, limit: 1_000))
        #expect(try size(url) == 0)
        _ = "line\n".withCString { write(fd, $0, 5) }
        #expect(try size(url) == 5)
    }

    @Test func aFileUnderTheCapIsLeftAlone() throws {
        let (url, fd) = try file(500)
        defer { close(fd); try? FileManager.default.removeItem(at: url) }
        #expect(!LogCap.trim(fd: fd, limit: 1_000))
        #expect(try size(url) == 500)
    }

    @Test func aPipeIsLeftAlone() {
        var ends: [Int32] = [0, 0]
        #expect(pipe(&ends) == 0)
        defer { close(ends[0]); close(ends[1]) }
        #expect(!LogCap.trim(fd: ends[1], limit: 0))
    }

    @Test func theCapIsTenMegabytes() {
        #expect(LogCap.bytes == 10_000_000)
    }
}
