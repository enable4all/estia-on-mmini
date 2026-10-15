import Darwin
import Foundation

/// The file itself, before any parsing: a regular file only, opened without following a symlink and without
/// waiting on a pipe, its size checked before a byte is read, and read from the very file that was checked.
public enum DocumentFile {
    /// A PDF's header must appear within its first 1024 bytes (as PDF readers accept it).
    static let headerWindow = 1024

    public static func load(path: String, maxBytes: Int) throws(ReadError) -> Data {
        var named = stat()
        guard lstat(path, &named) == 0, named.st_mode & S_IFMT == S_IFREG else { throw .unreadable }
        guard Int(named.st_size) <= maxBytes else { throw .tooBig }

        // O_NOFOLLOW: a symlink swapped in since lstat fails; O_NONBLOCK: a pipe swapped in never blocks the open
        let descriptor = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard descriptor >= 0 else { throw .unreadable }
        defer { close(descriptor) }
        var opened = stat()
        guard fstat(descriptor, &opened) == 0, opened.st_mode & S_IFMT == S_IFREG,
            opened.st_dev == named.st_dev, opened.st_ino == named.st_ino
        else { throw .unreadable }
        guard Int(opened.st_size) <= maxBytes else { throw .tooBig }

        let data = try readAll(descriptor, limit: maxBytes)
        guard isPDF(data) else { throw .notPDF }
        return data
    }

    /// Everything up to `limit` bytes; a file that grew past it since the check is too big.
    private static func readAll(_ descriptor: Int32, limit: Int) throws(ReadError) -> Data {
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 1 << 20)
        while true {
            let count = buffer.withUnsafeMutableBytes { Darwin.read(descriptor, $0.baseAddress, $0.count) }
            if count == 0 { return data }
            if count < 0 {
                if errno == EINTR { continue }
                throw .unreadable
            }
            data.append(contentsOf: buffer[0..<count])
            if data.count > limit { throw .tooBig }
        }
    }

    static func isPDF(_ data: Data) -> Bool {
        data.prefix(headerWindow).range(of: Data("%PDF-".utf8)) != nil
    }
}
