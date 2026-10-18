import Foundation

/// One parsed HTTP/1.1 request (one request per connection).
public struct HTTPRequest: Equatable, Sendable {
    public let method: String
    public let path: String
    /// Lower-cased names; when a header repeats, the first one wins.
    public let headers: [String: String]
    public let body: Data

    public func header(_ name: String) -> String? { headers[name.lowercased()] }
}

public enum ParseResult: Equatable, Sendable {
    case incomplete
    case invalid(Int)  // the HTTP status to answer with
    case request(HTTPRequest)
}

/// The loopback server's pure parts, after OpenWhisperer's `TTSHTTPServer` (MIT).
public enum HTTP {
    static let maxHeaderBytes = 16 * 1024

    /// Parses what has arrived so far: `.incomplete` until the headers and the whole body are in.
    public static func parse(_ data: Data, maxBody: Int) -> ParseResult {
        guard let end = data.range(of: Data("\r\n\r\n".utf8)) else {
            return data.count > maxHeaderBytes ? .invalid(431) : .incomplete
        }
        var lines = String(decoding: data[data.startIndex..<end.lowerBound], as: UTF8.self).components(separatedBy: "\r\n")
        let start = lines.removeFirst().split(separator: " ")
        guard start.count == 3, start[2].hasPrefix("HTTP/1.") else { return .invalid(400) }
        var headers: [String: String] = [:]
        for line in lines {
            guard let colon = line.firstIndex(of: ":") else { return .invalid(400) }
            let name = line[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            if headers[name] == nil {
                headers[name] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            }
        }
        var length = 0
        if let raw = headers["content-length"] {
            guard let value = Int(raw), value >= 0 else { return .invalid(400) }
            length = value
        }
        guard length <= maxBody else { return .invalid(413) }
        guard data.endIndex - end.upperBound >= length else { return .incomplete }
        let body = Data(data[end.upperBound..<(end.upperBound + length)])
        return .request(HTTPRequest(method: String(start[0]), path: String(start[1]), headers: headers, body: body))
    }

    /// Refuses browser-style callers: any Origin (only web pages send one; the bridge never does), or a Host
    /// that is not loopback (DNS rebinding). No Origin and no Host is a raw client such as the bridge.
    public static func isTrustedCaller(origin: String?, host: String?) -> Bool {
        if let origin, !origin.isEmpty { return false }
        if let host = host?.lowercased(), !host.isEmpty {
            let name = host.hasPrefix("[") ? "[::1]" : String(host.split(separator: ":").first ?? "")
            guard ["localhost", "127.0.0.1", "[::1]"].contains(name) else { return false }
        }
        return true
    }

    static let reasons = [
        200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
        408: "Request Timeout", 413: "Content Too Large", 415: "Unsupported Media Type", 422: "Unprocessable Content",
        431: "Request Header Fields Too Large", 500: "Internal Server Error", 503: "Service Unavailable",
    ]

    public static func response(_ status: Int, contentType: String, body: Data) -> Data {
        let head = "HTTP/1.1 \(status) \(reasons[status] ?? "Error")\r\nContent-Type: \(contentType)\r\n"
            + "Content-Length: \(body.count)\r\nConnection: close\r\n\r\n"
        return Data(head.utf8) + body
    }

    public static func json(_ status: Int, _ object: [String: Any]) -> Data {
        let body = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
        return response(status, contentType: "application/json", body: body)
    }
}

public enum Route: Equatable, Sendable {
    case transcribe, speak, vad, turn, health, methodNotAllowed, notFound

    public init(_ method: String, _ target: String) {
        let path = target.split(separator: "?", maxSplits: 1).first.map(String.init) ?? target
        let routes: [String: (String, Route)] = [
            "/v1/audio/transcriptions": ("POST", .transcribe),
            "/v1/audio/speech": ("POST", .speak),
            "/vad": ("POST", .vad),
            "/turn": ("POST", .turn),
            "/health": ("GET", .health),
        ]
        guard let (wanted, route) = routes[path] else { self = .notFound; return }
        self = method == wanted ? route : .methodNotAllowed
    }
}
