import Foundation

/// Why a pinned file was not downloaded (the log names it; a URLSession error is logged by its type).
public enum ModelDownloadError: Error, Equatable, CustomStringConvertible {
    /// The address, or a redirect, is not https.
    case notHTTPS
    /// Any answer but 200.
    case status(Int)
    /// More bytes than the cap, declared or sent.
    case overSizeCap

    public var description: String {
        switch self {
        case .notHTTPS: "not https"
        case .status(let code): "HTTP \(code)"
        case .overSizeCap: "over the size cap"
        }
    }
}

/// A failed download as the log names it: ours in words ("HTTP 404"), URLSession's by its code ("URLError -1009":
/// offline), anything else by its type. Never an address or a path.
public func downloadFailure(_ error: Error) -> String {
    if let refused = error as? ModelDownloadError { return refused.description }
    if let failed = error as? URLError { return "URLError \(failed.code.rawValue)" }
    return "\(type(of: error))"
}

/// One file from `url` into the local file `destination`, at most `maxBytes`.
public typealias FileFetch = (_ url: URL, _ destination: URL, _ maxBytes: Int) async throws -> Void

/// Downloads every file of `pin` from Hugging Face at the pinned commit (`HubPin.fileURL`, one request each, at most
/// `maxBytes` each) into a staging folder beside the pin's folder, then moves it into place, so a load never meets
/// half a download. A folder already there (incomplete, so never loaded) is replaced; a staging folder a crash left
/// is removed first. On any failure nothing is moved and the staging folder is removed. The bytes are not checked
/// here: `prepareModel` checks them after, against the same pins.
public func downloadPinnedFiles(
    _ pin: HubPin, in base: URL, maxBytes: Int,
    fetch: FileFetch = { try await fetchFile($0, to: $1, maxBytes: $2) }
) async throws {
    let files = FileManager.default
    let folder = pin.location(in: base)
    let parent = folder.deletingLastPathComponent()
    let prefix = "\(folder.lastPathComponent).download-"
    try files.createDirectory(at: parent, withIntermediateDirectories: true)
    for name in (try? files.contentsOfDirectory(atPath: parent.path)) ?? [] where name.hasPrefix(prefix) {
        try files.removeItem(at: parent.appendingPathComponent(name))
    }
    let staging = parent.appendingPathComponent(prefix + UUID().uuidString)
    do {
        for path in pin.sha256.keys.sorted() {
            let file = staging.appendingPathComponent(path)
            try files.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try await fetch(pin.fileURL(path), file, maxBytes)
            let size = (try files.attributesOfItem(atPath: file.path)[.size] as? NSNumber)?.intValue ?? Int.max
            guard size <= maxBytes else { throw ModelDownloadError.overSizeCap }
        }
        if (try? files.attributesOfItem(atPath: folder.path)) != nil { try files.removeItem(at: folder) }
        try files.moveItem(at: staging, to: folder)
    } catch {
        try? files.removeItem(at: staging)
        throw error
    }
}

/// `fetchFile` with an ephemeral session: no cookies, no cache, nothing kept on disk but the file.
public func fetchFile(_ url: URL, to destination: URL, maxBytes: Int) async throws {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.timeoutIntervalForResource = 600  // a download that stalls ends; the detector is then unavailable
    try await fetchFile(url, to: destination, maxBytes: maxBytes, configuration: configuration)
}

/// Writes the body of a GET of `url` to `destination` as it comes: https only (a redirect too), a 200 only, and at
/// most `maxBytes` (a larger declared length is refused before the body, a larger body as soon as it passes the cap).
func fetchFile(_ url: URL, to destination: URL, maxBytes: Int, configuration: URLSessionConfiguration) async throws {
    guard url.scheme?.lowercased() == "https" else { throw ModelDownloadError.notHTTPS }
    guard FileManager.default.createFile(atPath: destination.path, contents: nil) else { throw CocoaError(.fileWriteUnknown) }
    let handle = try FileHandle(forWritingTo: destination)
    defer { try? handle.close() }
    let fetch = CappedFetch(handle: handle, maxBytes: maxBytes)
    let session = URLSession(configuration: configuration, delegate: fetch, delegateQueue: nil)
    defer { session.finishTasksAndInvalidate() }
    // never cancelled: the detector's load runs to its end, bounded by the session's timeouts
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
        fetch.continuation = continuation
        session.dataTask(with: url).resume()
    }
}

/// The session's delegate: its callbacks come one at a time on the session's own queue.
private final class CappedFetch: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let handle: FileHandle
    private let maxBytes: Int
    private var written = 0
    private var failure: Error?
    var continuation: CheckedContinuation<Void, Error>?

    init(handle: FileHandle, maxBytes: Int) {
        self.handle = handle
        self.maxBytes = maxBytes
    }

    func urlSession(
        _ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void
    ) {
        guard request.url?.scheme?.lowercased() == "https" else {
            failure = failure ?? ModelDownloadError.notHTTPS
            completionHandler(nil)
            task.cancel()
            return
        }
        completionHandler(request)
    }

    func urlSession(
        _ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if failure == nil, status != 200 { failure = ModelDownloadError.status(status) }
        if failure == nil, response.expectedContentLength > Int64(maxBytes) { failure = ModelDownloadError.overSizeCap }
        completionHandler(failure == nil ? .allow : .cancel)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard failure == nil else { return }
        written += data.count
        guard written <= maxBytes else {
            failure = ModelDownloadError.overSizeCap
            dataTask.cancel()
            return
        }
        do {
            try handle.write(contentsOf: data)
        } catch {
            failure = error
            dataTask.cancel()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let failure {
            continuation?.resume(throwing: failure)
        } else if let error {
            continuation?.resume(throwing: error)
        } else {
            continuation?.resume()
        }
        continuation = nil
    }
}
