import Foundation

/// FluidAudio 0.15.8 flags macOS 26.4 and 26.5 for Kokoro (KokoroAneManager.isBnnsCrashProneOS): an Apple BNNS bug
/// there can crash English speech (EXC_BAD_ACCESS in libBNNS) whatever the compute units, and the whole service with
/// it; macOS 26.6 fixes it. Its own advisory goes only to FluidAudio's logger: the service says it in voice.log.
public enum KokoroAdvisory {
    public static func crashProne(_ version: OperatingSystemVersion) -> Bool {
        version.majorVersion == 26 && (4...5).contains(version.minorVersion)
    }

    public static let warning =
        "speaking: macOS 26.4 and 26.5 have an Apple bug that can crash English speech (Kokoro) and the voice service "
        + "with it; update to macOS 26.6 or later"
}
