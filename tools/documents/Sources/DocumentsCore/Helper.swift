import Foundation

/// One run of the helper: arguments in, one JSON line and an exit code out (0 with `"ok": true`, else 1).
/// The work runs on its own thread under `Watchdog`: at the time limit the answer is `timeout`, whatever a
/// framework is doing, and the process ends with it.
public enum Helper {
    public static func run(_ argv: [String]) -> (code: Int32, line: String) {
        switch Arguments.parse(argv) {
        case nil:
            return (1, Output.invalid)
        case .read(let path, let maxPages, let maxSeconds):
            let deadline = ContinuousClock.now.advanced(by: .seconds(maxSeconds))
            let result =
                Watchdog.run(seconds: Double(maxSeconds)) {
                    Result { () throws(ReadError) -> Reading in
                        let data = try DocumentFile.load(path: path, maxBytes: Limits.maxBytes)
                        return try DocumentReader.read(
                            data, maxPages: maxPages, deadline: deadline, recognizer: VisionRecognizer())
                    }
                } ?? .failure(.timeout)
            return ((try? result.get()) == nil ? 1 : 0, Output.line(result))
        case .warm(let maxSeconds):
            switch Watchdog.run(seconds: Double(maxSeconds), { VisionRecognizer.warm() }) {
            case nil: return (1, Output.failure(ReadError.timeout.rawValue))
            case false?: return (1, Output.failure(ReadError.unreadable.rawValue))
            case true?: return (0, Output.warmed)
            }
        }
    }
}

/// Runs `work` on a thread of its own and waits at most `seconds` for it: nil when the time is up. The thread is
/// left running then; the helper prints its answer and exits, which ends it.
public enum Watchdog {
    private final class Box<Value> {
        var value: Value?
    }

    public static func run<Value>(seconds: Double, _ work: @escaping () -> Value) -> Value? {
        let box = Box<Value>()
        let done = DispatchSemaphore(value: 0)
        let thread = Thread {
            box.value = work()
            done.signal()
        }
        thread.stackSize = 16 << 20  // deep PDF structures recurse in PDFKit
        thread.start()
        guard done.wait(timeout: .now() + seconds) == .success else { return nil }
        return box.value
    }
}
