import CryptoKit
import Foundation

/// Files from one Hugging Face repository at one commit, each checked against its sha256 before anything loads them.
/// They sit where a Hub download puts them (WhisperKit's layout): `<base>/models/<repo>/<folder>`.
public struct HubPin: Equatable, Sendable {
    public let repo: String
    /// The commit (40 hex digits), never a branch: a branch can change.
    public let revision: String
    /// The files' folder inside the repository; "" for its top.
    public let folder: String
    /// Every file, by its path inside `folder`.
    public let sha256: [String: String]

    public init(repo: String, revision: String, folder: String, sha256: [String: String]) {
        self.repo = repo
        self.revision = revision
        self.folder = folder
        self.sha256 = sha256
    }

    /// The files' folder under a download base, as the base's own path (for the log: the folder to delete).
    public var path: String { folder.isEmpty ? "models/\(repo)" : "models/\(repo)/\(folder)" }

    public func location(in base: URL) -> URL { base.appendingPathComponent(path) }

    /// What a download asks for: the whole folder, or the pinned files at the top.
    public var globs: [String] { folder.isEmpty ? sha256.keys.sorted() : ["\(folder)/*"] }

    /// The folder against the pins. At a repository's top, a download keeps its records (`.cache`) beside the files:
    /// never loaded, so left out.
    public func check(in base: URL) -> ModelFiles {
        checkModelFiles(at: location(in: base), against: sha256, skipping: folder.isEmpty ? [".cache"] : [])
    }
}

/// Makes sure a pin's files are there with their bytes before anything loads them. Missing files (a first start, a
/// download that stopped) are downloaded at the pinned commit (`download`) and checked again. A folder with different
/// bytes, an added file (an older download's extra file) or a link is never loaded and never downloaded over: it is
/// set aside whole as `<folder>.refused-<UTC time>` beside it (`setAside` is told where; only the latest such copy of
/// a pin is kept, so the disk never fills), and the pinned commit is downloaded into a fresh folder and checked: so no
/// recovery needs her user or admin rights. A fresh download that is changed too, or a folder that cannot be moved,
/// stays `.changed` (never loaded). A download that fails throws; one that brought nothing (offline) leaves `.missing`.
public func prepareModel(
    _ pin: HubPin, in base: URL, now: Date = Date(), setAside: (URL) -> Void = { _ in },
    download: (HubPin) async throws -> Void
) async throws -> ModelFiles {
    let files = pin.check(in: base)
    if files == .changed {
        // a folder that cannot be moved aside stays refused: no download over it, no restart loop
        guard let aside = try? setAsideFolder(of: pin, in: base, now: now) else { return .changed }
        setAside(aside)
    } else if files != .missing {
        return files
    }
    try await download(pin)
    return pin.check(in: base)
}

/// Moves a pin's folder aside (`<folder>.refused-<UTC time>`, beside it), after removing an older copy set aside for
/// the same pin. Links are moved or removed as links, never followed.
func setAsideFolder(of pin: HubPin, in base: URL, now: Date) throws -> URL {
    let files = FileManager.default
    let folder = pin.location(in: base)
    let parent = folder.deletingLastPathComponent()
    let prefix = "\(folder.lastPathComponent).refused-"
    for name in (try? files.contentsOfDirectory(atPath: parent.path)) ?? [] where name.hasPrefix(prefix) {
        try files.removeItem(at: parent.appendingPathComponent(name))
    }
    let stamp = DateFormatter()
    stamp.locale = Locale(identifier: "en_US_POSIX")
    stamp.timeZone = TimeZone(identifier: "UTC")
    stamp.dateFormat = "yyyyMMdd-HHmmss"
    let aside = parent.appendingPathComponent(prefix + stamp.string(from: now))
    try files.moveItem(at: folder, to: aside)
    return aside
}

/// Whisper large-v3 turbo, pinned: WhisperKit's CoreML build and the tokenizer WhisperKit reads with it, each at one
/// commit with every file's sha256. WhisperKit given only a model name lists the repository at "main" and checks each
/// file against Hugging Face at every start, fetching whatever changed. Measured on the files downloaded at each
/// commit, and matching Hugging Face's own listing of it (its sha256 for the Git LFS files, its blob ids for the
/// others). The model's folder is unchanged since commit 04e5c42 (14 Oct 2024), the tokenizer's files since Nov 2023.
public enum WhisperModel {
    public static let files = HubPin(
        repo: "argmaxinc/whisperkit-coreml",
        revision: "0f63a7800b00dd0226abd051b906c246e1907482",
        folder: "openai_whisper-large-v3-v20240930_turbo",
        sha256: [
            "AudioEncoder.mlmodelc/analytics/coremldata.bin": "b58d36a7f4a729570b46b424ed8d847baefa07580e6cb9d47773ae738f8b845a",
            "AudioEncoder.mlmodelc/coremldata.bin": "ffa9eb76e8e9d9be75a4d527e5249e61d67fd43081c5aa110fd24efa6c8c5ea3",
            "AudioEncoder.mlmodelc/metadata.json": "3f8920fecd553c40dfc978e1b2664cefbac80d97937c2c160e079b37cfa95e13",
            "AudioEncoder.mlmodelc/model.mil": "9aac7799f12bc5fc414cb0bd6b60536d4fb7723d8bb0d879e3c0ceb220b224aa",
            "AudioEncoder.mlmodelc/model.mlmodel": "c5eb570c19871d7b0c59e0a84718cc9c8cde77a94273886de87e866961262e2c",
            "AudioEncoder.mlmodelc/weights/weight.bin": "98daf651a919978e28fe185daf55ce2f70085a8e59fa07fe8a4d08c87d368ae4",
            "MelSpectrogram.mlmodelc/analytics/coremldata.bin": "c5be419f8622083ac7046306400643539f0e7577c843448c36defc090d41e7ce",
            "MelSpectrogram.mlmodelc/coremldata.bin": "98efa1e351b759e078c4044668926d32bee886caf7596ae897e08e21da45565a",
            "MelSpectrogram.mlmodelc/metadata.json": "2bc552e09a6f124d9e6c178dd1a6979e010206acb26308b2224887c9dcbeb35f",
            "MelSpectrogram.mlmodelc/model.mil": "c270b95b5f81d7f7d0b8a3e8f991d4e5812a37cad29349868a35b91f3a6a4463",
            "MelSpectrogram.mlmodelc/weights/weight.bin": "009d9fb8f6b589accfa08cebf1c712ef07c3405229ce3cfb3a57ee033c9d8a49",
            "TextDecoder.mlmodelc/analytics/coremldata.bin": "47703aed03fbfa5128e118cfe6519024006f953a53e921d66003f1412c27996c",
            "TextDecoder.mlmodelc/coremldata.bin": "605dad4099a82cf2c7afe93e6d8e322f1c16d4160ab27bd017ec2517b81c1bdd",
            "TextDecoder.mlmodelc/metadata.json": "de6afb1e8fa1d01568b8d96283ca17af19f6124f151d0fdbdbd5917edc7b4836",
            "TextDecoder.mlmodelc/model.mil": "1a1c2ec962fc7c9d2de9e2e4cf2d3827c8671f86a918b5698c00bf62c322ed3f",
            "TextDecoder.mlmodelc/model.mlmodel": "03b79e4355e814c56239ea809e5d8f6432d70d74256bc9d70c27184fe9fcec48",
            "TextDecoder.mlmodelc/weights/weight.bin": "47b2703aa37448e09cf2f06e45984fabd5ded4c34ba3400cec38a5294af39dc1",
            "TextDecoderContextPrefill.mlmodelc/analytics/coremldata.bin": "97639d36c7b137ea51c3c39b175911788f4d4a601ab03cd67a4b14164c3145e1",
            "TextDecoderContextPrefill.mlmodelc/coremldata.bin": "2c159f5c862ec187092ea58e755d8c0b298952e22f3d75da023d7693c1c7389e",
            "TextDecoderContextPrefill.mlmodelc/metadata.json": "eb88dc350fa6748a8bc3fa5fb10958152c138752ebbbac1824d2f99b4c9fc068",
            "TextDecoderContextPrefill.mlmodelc/model.mil": "990ff5052fd817e28ba7c34d9d06d324c69c7c0630b6eaac9cfdf08329dbcb34",
            "TextDecoderContextPrefill.mlmodelc/weights/weight.bin": "1310070082639173e9d81508c5f220692d489e85655aa6883cc1c7506da7fcfd",
            "config.json": "f01d83dd891791d6f12421c05d3ed8ebbe70866f10d6c9a7a7e80b558ce5a0f1",
            "generation_config.json": "7fbb053a023be11fbeccd8421811610308143daa93d9617c52aab4a0fa1491c6",
        ]
    )

    /// The files WhisperKit reads a tokenizer from (`config.json`, `tokenizer_config.json`, `tokenizer.json`; the
    /// repository has no chat template), where it looks first: `<base>/models/openai/whisper-large-v3`.
    public static let tokenizer = HubPin(
        repo: "openai/whisper-large-v3",
        revision: "06f233fe06e710322aca913c1bc4249a0d71fce1",
        folder: "",
        sha256: [
            "config.json": "ad0e8d1e46f4d01f7861a21509e5d0f977d6cc1f367a370603c92541d819807b",
            "tokenizer.json": "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",  // a public file hash, not a key (gitleaks:allow)
            "tokenizer_config.json": "844b642c73a91359722f47b35705f7174686df33d252695d8572cf9ac03a6389",  // a public file hash, not a key (gitleaks:allow)
        ]
    )
}

/// The speech detector's model, pinned: Silero VAD v6.2.1 in FluidAudio's CoreML build (256 ms chunks), from the
/// Hugging Face repository FluidInference/silero-vad-coreml at one commit (FluidAudio 0.15.8 would take the branch
/// "main", which can change), and every file checked against its sha256 before the model is loaded.
public enum SpeechModel {
    public static let revision = "b419383c55c110e2c9271fa6ee0ea83d03c70d96"
    /// FluidAudio's `ModelNames.VAD.sileroVadFile`.
    public static let bundle = "silero-vad-unified-256ms-v6.2.1.mlmodelc"
    /// Every file in the bundle, by its path inside it: measured on the files downloaded at `revision`, and matching
    /// Hugging Face's own listing of that commit (its sha256 for the Git LFS files, its blob ids for the others).
    public static let sha256 = [
        "analytics/coremldata.bin": "8067594eb3126ab8318af507f0c00cabfed40d5fedb8a0ee5075dd02e903d909",
        "coremldata.bin": "7db35a4fd995222a7fb0129713473b15d1462572ab4a2e5e4d56bcaad9e40f41",
        "metadata.json": "2740be542c611e1ba358e1849b4e265c65cdf0b17192767e1e5de86a31ac94d6",
        "model.mil": "c6a9d1bf22d413265da0a07a1d14151c3ea2fad296b3aa5859275b33ef1c3270",
        "weights/weight.bin": "53ecc8b5081146140ab654c89109cf001f2183abddd7a2411c5081feeffff063",
    ]
}

/// How a model folder compares with its pins.
public enum ModelFiles: String, Equatable, Sendable {
    case ok
    /// The folder or a pinned file is not there (a first start, a download that stopped): a download can complete it.
    case missing
    /// A file differs from its sha256, a file that is not pinned is there, or an entry is a link: never loaded.
    case changed
}

public func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

/// A file's sha256, read in 1 MiB pieces: Whisper's encoder weights alone are 1.3 GB.
public func sha256Hex(file: URL) throws -> String {
    let handle = try FileHandle(forReadingFrom: file)
    defer { try? handle.close() }
    var hasher = SHA256()
    while true {
        let more = try autoreleasepool { () throws -> Bool in
            guard let piece = try handle.read(upToCount: 1 << 20), !piece.isEmpty else { return false }
            hasher.update(data: piece)
            return true
        }
        if !more { break }
    }
    return hasher.finalize().map { String(format: "%02x", $0) }.joined()
}

/// Compares the folder at `folder` with `sha256` (path inside the folder → sha256): exactly those files, with those
/// bytes. Links are never followed, the folder itself included. Entries named in `skipping`, at the folder's top only,
/// are left out (a download's own records, never loaded).
public func checkModelFiles(at folder: URL, against sha256: [String: String], skipping: Set<String> = []) -> ModelFiles {
    let files = FileManager.default
    guard let type = (try? files.attributesOfItem(atPath: folder.path))?[.type] as? FileAttributeType else { return .missing }
    guard type == .typeDirectory, let entries = files.enumerator(atPath: folder.path) else { return .changed }
    var found: Set<String> = []
    for case let path as String in entries {
        if skipping.contains(path) {
            entries.skipDescendants()
            continue
        }
        switch entries.fileAttributes?[.type] as? FileAttributeType {
        case .typeDirectory?: continue
        case .typeRegular?: found.insert(path)
        default: return .changed
        }
    }
    guard Set(sha256.keys).isSubset(of: found) else { return .missing }
    guard found.count == sha256.count else { return .changed }
    for (path, expected) in sha256 {
        guard (try? sha256Hex(file: folder.appendingPathComponent(path))) == expected else { return .changed }
    }
    return .ok
}
