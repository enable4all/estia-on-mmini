// swift-tools-version: 6.0
// local-assistant's documents helper: the text of one PDF per run, for the bridge. Apple's frameworks only:
// PDFKit gives each page's text, Vision reads a page that has none (on the Mac, no network). No dependencies.
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
    name: "documents-helper",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "DocumentsCore", swiftSettings: [.swiftLanguageMode(.v5)]),
        .executableTarget(
            name: "documents-helper",
            dependencies: ["DocumentsCore"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        // Swift Testing works with the Command Line Tools (XCTest does not); `testingMacros` above.
        // The tests depend on the executable so `swift test` builds it: a few run the real binary.
        .testTarget(
            name: "DocumentsCoreTests",
            dependencies: ["DocumentsCore", "documents-helper"],
            swiftSettings: testingMacros
        ),
    ]
)
