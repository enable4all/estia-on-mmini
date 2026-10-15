import Foundation

/// Speaking (Kokoro) that failed to load is tried again in the process, never by a restart: a restart would check and
/// reload Whisper's 1.6 GB each time, and listening would flip between loading and ready. 1, 5 and 30 minutes, then
/// every 30 minutes, until it loads.
public enum SpeakingRetry {
    static let delays: [TimeInterval] = [60, 300, 1800]

    /// Seconds before the next try, after `count` failed tries since the first load.
    public static func delay(afterFailures count: Int) -> TimeInterval {
        delays[min(count, delays.count - 1)]
    }

    /// Waits (`sleep`) and tries (`load`, true once speaking is ready) until speaking loads.
    public static func run(sleep: (TimeInterval) async -> Void, load: () async -> Bool) async {
        var failures = 0
        while true {
            await sleep(delay(afterFailures: failures))
            if await load() { return }
            failures += 1
        }
    }
}
