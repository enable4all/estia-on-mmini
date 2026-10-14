import Foundation

/// The service's log (voice.log: its stdout and stderr, which launchd opens for appending) kept under a size in-process:
/// emptied when it is over the cap, at the start and before every line, so nothing outside the service (no newsyslog,
/// no root) touches it. A terminal or a pipe is left alone.
public enum LogCap {
    public static let bytes: Int64 = 10_000_000

    /// Empties the regular file behind `fd` when it is larger than `limit`; whether it did.
    @discardableResult
    public static func trim(fd: Int32, limit: Int64 = bytes) -> Bool {
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, Int64(info.st_size) > limit else {
            return false
        }
        guard ftruncate(fd, 0) == 0 else { return false }
        lseek(fd, 0, SEEK_SET)  // opened for appending (launchd): writing goes on at the new end anyway
        return true
    }
}
