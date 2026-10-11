// swift-tools-version: 6.2
// local-assistant's voice service: listening (WhisperKit) and speaking (FluidAudio: Kokoro for English,
// Supertonic-3 for ten more languages) on the Neural Engine, served on 127.0.0.1 for the bridge. The engine
// layer and its pins come from the OpenWhisperer voice app (MIT, app/Package.swift), where they are explained
// in full; change them only deliberately, after measuring.
import PackageDescription

let package = Package(
    name: "voice-service",
    platforms: [.macOS(.v14)],
    dependencies: [
        // WhisperKit 1.1.0+: fixes empty transcripts when prompt tokens are set (upstream #514).
        .package(url: "https://github.com/argmaxinc/WhisperKit.git", from: "1.1.0"),
        // FluidAudio exactly 0.15.8 (Kokoro strided-array fix #730, apostrophes #783, non-finite
        // durations crash #816; Supertonic-3 included); `traits: []` leaves out a 9 MB text-normalisation library.
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
        // Swift Testing works with the Command Line Tools (XCTest does not). After a failed build SwiftPM
        // sometimes loses the Testing macros plugin ("plugin for module 'TestingMacros' not found");
        // naming its folder keeps every run reliable (harmless where the folder does not exist).
        .testTarget(
            name: "VoiceCoreTests",
            dependencies: ["VoiceCore"],
            swiftSettings: [.unsafeFlags(["-plugin-path", "/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing"])]
        ),
        // The service's own glue to the engines, where it needs their types (Whisper's decoding options).
        .testTarget(
            name: "VoiceServiceTests",
            dependencies: ["voice-service", .product(name: "WhisperKit", package: "WhisperKit")],
            swiftSettings: [.unsafeFlags(["-plugin-path", "/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing"])]
        ),
    ]
)
