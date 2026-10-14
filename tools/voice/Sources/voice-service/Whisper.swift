// Whisper's files at start: the CoreML model and its tokenizer, each pinned to one commit and checked file by file
// against its sha256 (`WhisperModel`; about 0.6 s for the 1.6 GB). Missing files are downloaded at the pinned commit
// (the first start, or a download that stopped); then WhisperKit loads only from those checked folders: no listing of
// the repository, no download, no request to Hugging Face.
import Foundation
import VoiceCore
import WhisperKit

/// What Whisper needs, the model first.
let whisperPins = [WhisperModel.files, WhisperModel.tokenizer]

/// Downloads a pin's files at its commit into the models folder, in WhisperKit's own layout (so a copy already there
/// is checked, not fetched again).
func downloadPinned(_ pin: HubPin, models: URL) async throws {
    _ = try await HubApiWrapper(downloadBase: models).snapshot(
        from: HubApiWrapper.Repo(id: pin.repo), revision: pin.revision, matching: pin.globs)
}

/// WhisperKit's settings: the checked model folder and the tokenizer under the models folder, never a download. With a
/// model folder WhisperKit skips its download step entirely; the tokenizer it reads from where it looks first,
/// `<models>/models/openai/whisper-large-v3` (it would fetch one from Hugging Face only if those checked files failed
/// to read).
func whisperConfig(models: URL) -> WhisperKitConfig {
    WhisperKitConfig(
        modelFolder: WhisperModel.files.location(in: models).path, tokenizerFolder: models, load: true, download: false)
}
