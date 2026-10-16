// swift-tools-version: 6.2
// local-assistant's voice service: listening (WhisperKit) and speaking (FluidAudio: Kokoro for English,
// Supertonic-3 for ten more languages) on the Neural Engine, served on 127.0.0.1 for the bridge. The engine
// layer and its pins come from the OpenWhisperer voice app (MIT, app/Package.swift), where they are explained
// in full; change them only deliberately, after measuring.
import Foundation
import PackageDescription

/// The Command Line Tools' Testing macros folder, named for the test targets only while the Command Line Tools are the
/// active developer folder (DEVELOPER_DIR, else xcode-select's choice, else Xcode when installed): there SwiftPM
/// sometimes loses the Testing macros plugin after a failed build ("plugin for module 'TestingMacros' not found"),
/// and naming its folder keeps every run reliable. With Xcode active, Xcode's own compiler finds its own macros; the
/// Command Line Tools' could be another version. (Untested with Xcode active: this Mac has only the Command Line Tools.)
let commandLineTools = "/Library/Developer/CommandLineTools"
let activeDeveloperFolder = Context.environment["DEVELOPER_DIR"]
    ?? (try? FileManager.default.destinationOfSymbolicLink(atPath: "/var/db/xcode_select_link"))
    ?? (FileManager.default.fileExists(atPath: "/Applications/Xcode.app")
        ? "/Applications/Xcode.app/Contents/Developer" : commandLineTools)
let testingMacros: [SwiftSetting] = activeDeveloperFolder.hasPrefix(commandLineTools)
    ? [.unsafeFlags(["-plugin-path", commandLineTools + "/usr/lib/swift/host/plugins/testing"])] : []

let package = Package(
    name: "voice-service",
    platforms: [.macOS(.v14)],
    dependencies: [
        // WhisperKit 1.1.0+: fixes empty transcripts when prompt tokens are set (upstream #514).
        .package(url: "https://github.com/argmaxinc/WhisperKit.git", from: "1.1.0"),
        // FluidAudio exactly 0.15.8 (Kokoro strided-array fix #730, apostrophes #783, non-finite
        // durations crash #816; Supertonic-3 included); `traits: []` leaves out NeMo's text normalisation, a prebuilt
        // 9 MB library (owner, 16 Oct: dropped; it read "13:00" as "thirteen o'clock" and German and French times
        // with their colon): VoiceCore's NumberNormalizer reads English numbers and times, and a test
        // (VoiceServiceTests, `EnglishNumbers`) fails if an upgrade links the library again.
        .package(url: "https://github.com/FluidInference/FluidAudio.git", exact: "0.15.8", traits: []),
    ],
    targets: [
        .target(name: "VoiceCore", swiftSettings: [.swiftLanguageMode(.v5)]),
        .executableTarget(
            name: "voice-service",
            dependencies: [
                "VoiceCore",
                .product(name: "WhisperKit", package: "WhisperKit"),
                .product(name: "FluidAudio", package: "FluidAudio"),
            ],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        // Swift Testing works with the Command Line Tools (XCTest does not); `testingMacros` above.
        .testTarget(
            name: "VoiceCoreTests",
            dependencies: ["VoiceCore"],
            swiftSettings: testingMacros
        ),
        // The service's own glue to the engines, where it needs their types (Whisper's decoding options).
        .testTarget(
            name: "VoiceServiceTests",
            dependencies: [
                "voice-service",
                .product(name: "WhisperKit", package: "WhisperKit"),
                .product(name: "FluidAudio", package: "FluidAudio"),
            ],
            swiftSettings: testingMacros
        ),
    ]
)
