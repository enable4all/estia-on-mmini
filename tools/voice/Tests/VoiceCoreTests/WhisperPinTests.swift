import Foundation
import Testing
import VoiceCore

private func isHex(_ text: String, count: Int) -> Bool {
    text.count == count && text.allSatisfy { "0123456789abcdef".contains($0) }
}

/// A folder under a fresh temporary base holding `files` (path → text).
private func base(_ files: [String: String]) throws -> URL {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("whisper-pin-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    try write(files, into: root)
    return root
}

private func write(_ files: [String: String], into root: URL) throws {
    for (path, text) in files {
        let url = root.appendingPathComponent(path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(text.utf8).write(to: url)
    }
}

@Suite struct WhisperPin {
    @Test func theModelIsPinnedToOneCommitFileByFile() {
        let model = WhisperModel.files
        #expect(model.repo == "argmaxinc/whisperkit-coreml")
        #expect(model.folder == "openai_whisper-large-v3-v20240930_turbo")
        #expect(isHex(model.revision, count: 40))
        // every file of the folder at that commit (Hugging Face's listing has 24), each with its sha256
        #expect(model.sha256.count == 24)
        let parts = Set(model.sha256.keys.map { String($0.split(separator: "/")[0]) })
        #expect(parts == [
            "AudioEncoder.mlmodelc", "MelSpectrogram.mlmodelc", "TextDecoder.mlmodelc", "TextDecoderContextPrefill.mlmodelc",
            "config.json", "generation_config.json",
        ])
        #expect(model.sha256["AudioEncoder.mlmodelc/weights/weight.bin"] != nil)
        #expect(model.sha256.values.allSatisfy { isHex($0, count: 64) })
    }

    @Test func theTokenizerIsPinnedToo() {
        // WhisperKit would fetch it from openai/whisper-large-v3 at "main" when it finds none
        let tokenizer = WhisperModel.tokenizer
        #expect(tokenizer.repo == "openai/whisper-large-v3")
        #expect(tokenizer.folder.isEmpty)
        #expect(isHex(tokenizer.revision, count: 40))
        #expect(Set(tokenizer.sha256.keys) == ["config.json", "tokenizer.json", "tokenizer_config.json"])
        #expect(tokenizer.sha256.values.allSatisfy { isHex($0, count: 64) })
    }

    @Test func theFilesSitWhereHubDownloadsPutThem() {
        // <base>/models/<repo>/<folder>: WhisperKit's own layout, so a copy already downloaded is checked, not fetched
        let models = URL(fileURLWithPath: "/models")
        #expect(WhisperModel.files.location(in: models).path
            == "/models/models/argmaxinc/whisperkit-coreml/openai_whisper-large-v3-v20240930_turbo")
        #expect(WhisperModel.tokenizer.location(in: models).path == "/models/models/openai/whisper-large-v3")
        #expect(WhisperModel.files.path == "models/argmaxinc/whisperkit-coreml/openai_whisper-large-v3-v20240930_turbo")
        // a download asks for the model's whole folder, and for the tokenizer's pinned files only
        #expect(WhisperModel.files.globs == ["openai_whisper-large-v3-v20240930_turbo/*"])
        #expect(WhisperModel.tokenizer.globs == ["config.json", "tokenizer.json", "tokenizer_config.json"])
    }
}

@Suite struct HubPins {
    /// sha256 of "a" and of "t".
    private let a = "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    private let t = "e3b98a4da31a127d4bde6e43033f66ba274cab0eb7eb1c70ec41402bf6273dd8"
    private let revision = String(repeating: "0", count: 40)

    private var model: HubPin { HubPin(repo: "example/model", revision: revision, folder: "m", sha256: ["sub/x.bin": a]) }
    private var tokenizer: HubPin { HubPin(repo: "example/tok", revision: revision, folder: "", sha256: ["t.json": t]) }

    @Test func aLargeFileIsHashedInPieces() throws {
        // more than one piece (1 MiB) and a bit: the same sha256 as the bytes at once, without holding the file
        let bytes = Data((0..<(3 << 20) + 1).map { UInt8(truncatingIfNeeded: $0 &* 31) })
        let root = try base([:])
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("big.bin")
        try bytes.write(to: file)
        #expect(try sha256Hex(file: file) == sha256Hex(bytes))
    }

    @Test func theHubsRecordsBesideTheTokenizerAreLeftOut() throws {
        // a download keeps its records in .cache at the top of the repository's folder: never loaded, never pinned
        let root = try base(["models/example/tok/t.json": "t", "models/example/tok/.cache/huggingface/download/t.json.metadata": "x"])
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(tokenizer.check(in: root) == .ok)
        // only there: one deeper, or beside a model's own folder, is an added file
        try write(["models/example/tok/sub/.cache/x": "x"], into: root)
        #expect(tokenizer.check(in: root) == .changed)
        let other = try base(["models/example/model/m/sub/x.bin": "a", "models/example/model/m/.cache/x": "x"])
        defer { try? FileManager.default.removeItem(at: other) }
        #expect(model.check(in: other) == .changed)
    }

    @Test func pinnedFilesLoadWithoutADownload() async throws {
        let root = try base(["models/example/model/m/sub/x.bin": "a"])
        defer { try? FileManager.default.removeItem(at: root) }
        var downloads = 0
        let files = try await prepareModel(model, in: root) { _ in downloads += 1 }
        #expect(files == .ok && downloads == 0)
    }

    @Test func missingFilesAreDownloadedAtThePinnedCommitThenChecked() async throws {
        let root = try base([:])
        defer { try? FileManager.default.removeItem(at: root) }
        var asked: [HubPin] = []
        let files = try await prepareModel(model, in: root) { pin in
            asked.append(pin)
            try write(["models/example/model/m/sub/x.bin": "a"], into: root)
        }
        #expect(files == .ok)
        #expect(asked.map(\.repo) == ["example/model"] && asked.map(\.revision) == [revision])
    }

    @Test func aChangedFolderIsSetAsideNeverLoadedNorDownloadedOverThenFetchedFreshOnce() async throws {
        // an older download's stray file, or changed bytes: the folder goes aside whole (kept, never loaded) and the
        // pinned commit is downloaded into a fresh folder, so no recovery needs her user or admin rights
        let root = try base(["models/example/model/m/sub/x.bin": "A", "models/example/model/m/extra.json": "x"])
        defer { try? FileManager.default.removeItem(at: root) }
        var downloads = 0
        var aside: [URL] = []
        let files = try await prepareModel(model, in: root, setAside: { aside.append($0) }) { _ in
            downloads += 1
            try write(["models/example/model/m/sub/x.bin": "a"], into: root)
        }
        #expect(files == .ok && downloads == 1 && aside.count == 1)
        let kept = try #require(aside.first)
        #expect(kept.deletingLastPathComponent().path == root.appendingPathComponent("models/example/model").path)
        #expect(kept.lastPathComponent.hasPrefix("m.refused-"))
        #expect(try String(contentsOf: kept.appendingPathComponent("sub/x.bin"), encoding: .utf8) == "A")
        #expect(FileManager.default.fileExists(atPath: kept.appendingPathComponent("extra.json").path))
    }

    @Test func aFreshDownloadThatIsChangedTooIsRefused() async throws {
        let root = try base(["models/example/model/m/sub/x.bin": "A"])
        defer { try? FileManager.default.removeItem(at: root) }
        var downloads = 0
        let files = try await prepareModel(model, in: root) { _ in
            downloads += 1
            try write(["models/example/model/m/sub/x.bin": "not the model"], into: root)
        }
        #expect(files == .changed && downloads == 1)
    }

    @Test func onlyTheLatestChangedCopyIsKeptAside() async throws {
        // a restart that meets changed files again sets them aside again: one copy per pin, the disk never fills
        let root = try base([
            "models/example/model/m/sub/x.bin": "A", "models/example/model/m.refused-20261001-000000/sub/x.bin": "B",
            "models/example/model/other/keep.bin": "k",
        ])
        defer { try? FileManager.default.removeItem(at: root) }
        _ = try await prepareModel(model, in: root) { _ in try write(["models/example/model/m/sub/x.bin": "a"], into: root) }
        let names = try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("models/example/model").path)
        #expect(names.filter { $0.hasPrefix("m.refused-") }.count == 1)
        #expect(!names.contains("m.refused-20261001-000000") && names.contains("other") && names.contains("m"))
    }

    @Test func aChangedFolderThatCannotBeSetAsideIsRefusedNotRetried() async throws {
        // a folder she cannot move (its parent not hers to write): never loaded, nothing downloaded, no restart loop
        let root = try base(["models/example/model/m/sub/x.bin": "A"])
        let parent = root.appendingPathComponent("models/example/model")
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: parent.path)
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: parent.path)
            try? FileManager.default.removeItem(at: root)
        }
        var downloads = 0
        let files = try await prepareModel(model, in: root) { _ in downloads += 1 }
        #expect(files == .changed && downloads == 0)
    }

    @Test func aChangedTokenizerGoesAsideWithTheHubsRecords() async throws {
        let root = try base(["models/example/tok/t.json": "T", "models/example/tok/.cache/huggingface/download/t.json.metadata": "x"])
        defer { try? FileManager.default.removeItem(at: root) }
        var aside: [URL] = []
        let files = try await prepareModel(tokenizer, in: root, setAside: { aside.append($0) }) { _ in
            try write(["models/example/tok/t.json": "t"], into: root)
        }
        #expect(files == .ok)
        let kept = try #require(aside.first)
        #expect(kept.lastPathComponent.hasPrefix("tok.refused-"))
        #expect(FileManager.default.fileExists(atPath: kept.appendingPathComponent(".cache/huggingface/download/t.json.metadata").path))
        #expect(!FileManager.default.fileExists(atPath: root.appendingPathComponent("models/example/tok/.cache").path))
    }

    @Test func aDownloadThatBringsOtherBytesIsNeverLoaded() async throws {
        let root = try base([:])
        defer { try? FileManager.default.removeItem(at: root) }
        let changed = try await prepareModel(model, in: root) { _ in
            try write(["models/example/model/m/sub/x.bin": "not the model"], into: root)
        }
        #expect(changed == .changed)
        // a download that brought nothing (offline: the hub returns what is there) is still missing: tried again later
        let empty = try base([:])
        defer { try? FileManager.default.removeItem(at: empty) }
        #expect(try await prepareModel(model, in: empty) { _ in } == .missing)
    }

    @Test func aFailedDownloadThrows() async throws {
        struct Offline: Error {}
        let root = try base([:])
        defer { try? FileManager.default.removeItem(at: root) }
        await #expect(throws: Offline.self) { try await prepareModel(model, in: root) { _ in throw Offline() } }
    }
}
