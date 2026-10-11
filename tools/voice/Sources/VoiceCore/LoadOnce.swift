import Foundation

/// An engine loaded on first use and then kept (after OpenWhisperer's Supertonic3TTS, MIT): concurrent first uses
/// share the one load, and a failed load is tried again by the next use. A failure is `VoiceError.notReady`
/// (503: the bridge tries again later), never the engine's own error.
public actor LoadOnce<Value: Sendable> {
    private let load: @Sendable () async throws -> Value
    private var value: Value?
    private var loading: Task<Value, Error>?

    public init(_ load: @escaping @Sendable () async throws -> Value) { self.load = load }

    public func get() async throws -> Value {
        if let value { return value }
        if let loading { return try await Self.ready(loading) }
        let task = Task(operation: load)
        loading = task
        defer { loading = nil }  // only the first caller clears it, so a retry never overlaps a load
        let value = try await Self.ready(task)
        self.value = value
        return value
    }

    private static func ready(_ task: Task<Value, Error>) async throws -> Value {
        do {
            return try await task.value
        } catch {
            throw VoiceError.notReady
        }
    }
}
