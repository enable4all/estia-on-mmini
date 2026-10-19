"""Doctor's lines: what each check says, and the fix it names."""

from __future__ import annotations

# fmt: off
__all__ = [
    "DOCTOR_ADMIN", "DOCTOR_ALWAYS_ON_ALIVE", "DOCTOR_ALWAYS_ON_ALIVE_ONLY", "DOCTOR_ALWAYS_ON_EXIT",
    "DOCTOR_ALWAYS_ON_MISSING", "DOCTOR_ALWAYS_ON_NONE_YET", "DOCTOR_ALWAYS_ON_NOT_LOADED",
    "DOCTOR_ALWAYS_ON_RESTARTING", "DOCTOR_ALWAYS_ON_SILENT", "DOCTOR_ALWAYS_ON_SILENT_SINCE",
    "DOCTOR_ALWAYS_ON_STOPPED", "DOCTOR_ALWAYS_ON_STOPPED_NEVER", "DOCTOR_ALWAYS_ON_UNREAD", "DOCTOR_BARGE_UNREAD",
    "DOCTOR_BARGE_UNREAD_FIX", "DOCTOR_DETECTOR_FALLS", "DOCTOR_DETECTOR_LOADING", "DOCTOR_DETECTOR_LOADING_FIX",
    "DOCTOR_DETECTOR_OLD", "DOCTOR_DETECTOR_SILENT", "DOCTOR_DETECTOR_UNAVAILABLE", "DOCTOR_DETECTOR_UNAVAILABLE_FIX",
    "DOCTOR_DEVICES_NOT_COUNTED", "DOCTOR_DOCUMENTS_BUILD_FIX", "DOCTOR_DOCUMENTS_CLOSED", "DOCTOR_DOCUMENTS_HERS",
    "DOCTOR_DOCUMENTS_HERS_FIX", "DOCTOR_DOCUMENTS_MAKE_FIX", "DOCTOR_DOCUMENTS_MISSING",
    "DOCTOR_DOCUMENTS_MKDIR_FIX", "DOCTOR_DOCUMENTS_NOT_BUILT", "DOCTOR_DOCUMENTS_NOT_KB",
    "DOCTOR_DOCUMENTS_NO_HELPER", "DOCTOR_DOCUMENTS_OK", "DOCTOR_DOCUMENTS_REBUILD_FIX", "DOCTOR_DOCUMENTS_REFUSED",
    "DOCTOR_DOCUMENTS_REFUSED_FIX", "DOCTOR_DOCUMENTS_REFUSED_KB_FIX", "DOCTOR_DOCUMENTS_SHARE_FIX",
    "DOCTOR_DOCUMENTS_UNSHARED", "DOCTOR_DOCUMENTS_WHY_HOME", "DOCTOR_EVENING_CONFIG", "DOCTOR_EVENING_GONE",
    "DOCTOR_EVENING_OFF", "DOCTOR_EVENING_OFF_BY_USER", "DOCTOR_EVENING_TYPED", "DOCTOR_EVENING_UNUSED",
    "DOCTOR_EVENING_UNUSED_FIX", "DOCTOR_HISTORY_FIX", "DOCTOR_HISTORY_KEPT", "DOCTOR_HISTORY_NOT_YET",
    "DOCTOR_HISTORY_PRUNED", "DOCTOR_HOME_USUAL_NAMES", "DOCTOR_HOST_FAILED", "DOCTOR_INITIATIVE_FIX",
    "DOCTOR_INITIATIVE_OFF", "DOCTOR_INITIATIVE_OFF_BY_USER", "DOCTOR_INTERRUPT_OLD", "DOCTOR_INTERRUPT_OLD_FIX",
    "DOCTOR_MODEL_BUILD", "DOCTOR_MODEL_CLOUD", "DOCTOR_MODEL_PROMPT", "DOCTOR_MODEL_PROMPT_FIX",
    "DOCTOR_MODEL_PROMPT_FIX_HERS", "DOCTOR_MODEL_PROMPT_OTHER_FIX", "DOCTOR_MODEL_TAG", "DOCTOR_OLLAMA_DOWN_FIX",
    "DOCTOR_OLLAMA_JOB_FIX", "DOCTOR_OLLAMA_NOT_LOADED_FIX", "DOCTOR_OLLAMA_OK", "DOCTOR_OLLAMA_OLD",
    "DOCTOR_OLLAMA_SERVICE_CHANGED", "DOCTOR_OLLAMA_SERVICE_FIX", "DOCTOR_OLLAMA_SETUP_FIX",
    "DOCTOR_OLLAMA_UPDATE_FIX", "DOCTOR_OWN_EDIT", "DOCTOR_OWN_USER_CONFIG", "DOCTOR_PICTURE_BAD",
    "DOCTOR_PICTURE_ESTIA", "DOCTOR_PICTURE_FIX", "DOCTOR_PICTURE_LETTER", "DOCTOR_PICTURE_NONE",
    "DOCTOR_PICTURE_OWN", "DOCTOR_PIN_CHANGED", "DOCTOR_PIN_CHANGED_FIX", "DOCTOR_PIN_FIX", "DOCTOR_PIN_FIX_UNKNOWN",
    "DOCTOR_PIN_NONE", "DOCTOR_PIN_OK", "DOCTOR_PIN_UNREAD", "DOCTOR_POWER_FIX", "DOCTOR_POWER_NO_RESTART",
    "DOCTOR_POWER_OFF", "DOCTOR_POWER_OK", "DOCTOR_POWER_SLEEPS", "DOCTOR_PROJECTS_CLOSED", "DOCTOR_PROJECTS_EMPTY",
    "DOCTOR_PROJECTS_MISSING", "DOCTOR_PROJECTS_NONE_READ", "DOCTOR_PROJECTS_OK", "DOCTOR_PROJECTS_ROOT_FIX",
    "DOCTOR_PROJECTS_SHARE_FIX", "DOCTOR_PROJECTS_SOME", "DOCTOR_REMINDERS_FIX", "DOCTOR_REMINDERS_NONE",
    "DOCTOR_REMINDERS_STUCK", "DOCTOR_SAFETY_UNCHANGED", "DOCTOR_SIGNAL_CLI_FIX", "DOCTOR_SIGNAL_CLI_OTHER",
    "DOCTOR_SIGNAL_UNFINISHED", "DOCTOR_SIGNAL_UNFINISHED_FIX", "DOCTOR_SOUL_CALM_OFF", "DOCTOR_SOUL_NOTHING",
    "DOCTOR_SOUL_NO_CALLS", "DOCTOR_SOUL_NO_TEXTS", "DOCTOR_SOUL_PARTS", "DOCTOR_TEXT_ONLY", "DOCTOR_TOOLS_HERS",
    "DOCTOR_TURN_LOADING", "DOCTOR_TURN_LOADING_FIX", "DOCTOR_TURN_OLD", "DOCTOR_TURN_SILENT",
    "DOCTOR_TURN_UNAVAILABLE", "DOCTOR_TURN_UNAVAILABLE_FIX", "DOCTOR_VOICE_LACKS", "DOCTOR_VOICE_LOG",
    "DOCTOR_VOICE_MODELS", "DOCTOR_VOICE_REINSTALL", "DOCTOR_VOICE_RESTART_OWN", "DOCTOR_VOICE_SILENT",
    "DOCTOR_VOICE_TOO_OLD", "DOCTOR_YOUTUBE_DEPLOY_FIX", "DOCUMENTS_REFUSED_KB", "LINKED_FIX", "LINKED_LABEL",
    "LINKED_MORE", "LINKED_ONE", "MODEL_CLOUD_LINE", "MODEL_LABEL", "MODEL_PAUSED_FIX", "MODEL_PAUSED_LINE",
    "PART_FAILING", "PART_FIXES", "PART_LABELS", "PART_LABELS_PLAIN", "PART_REASONS", "PART_SET_UP_AGAIN",
    "PART_UNTESTED", "PART_VERBS", "PART_WARNING", "PART_WORKING", "SAFETY_NUMBER_CHANGED", "SAFETY_NUMBER_FIX",
    "SAFETY_NUMBER_LABEL", "SETUP_MODEL_FIX", "model_build_fix", "model_cloud_fix",
]
# fmt: on

DOCTOR_REMINDERS_NONE = "none yet"
DOCTOR_REMINDERS_STUCK = " · {count} stuck"
DOCTOR_REMINDERS_FIX = "/reminders in Signal shows what failed and why; bridge.log has the details (ids only)"
DOCTOR_INITIATIVE_FIX = "/status in Signal shows the last skips; bridge.log has the details (ids and reasons only)"
DOCTOR_INITIATIVE_OFF = "off (opt-in): /initiative on in Signal, or [initiative] enabled = true"
DOCTOR_INITIATIVE_OFF_BY_USER = "off (/initiative off in Signal, which wins over the config): /initiative on"
# the evening note (owner, D4): off, on at its time and by whose switch (never an account's number)
DOCTOR_EVENING_OFF = 'off: /evening on in Signal, or [initiative] evening = "21:00"'
DOCTOR_EVENING_OFF_BY_USER = "off (/evening off in Signal, which wins over the config): /evening on"
DOCTOR_EVENING_TYPED = "on at {time} (/evening, for the account that typed it)"
DOCTOR_EVENING_GONE = "off: the account that typed /evening on is no longer allowed"
DOCTOR_EVENING_CONFIG = "on at {time} ([initiative] evening, for the one allowed account)"
DOCTOR_EVENING_UNUSED = (
    '[initiative] evening = "{time}" goes to no one: several accounts are allowed, and none typed /evening on'
)
DOCTOR_EVENING_UNUSED_FIX = "/evening on in Signal, from the account that should get it"
# doctor's Personality line for a soul.md with parts ({style}: the built-in style a kind with nothing keeps)
DOCTOR_SOUL_PARTS = "soul.md: {texts} characters for texts, {calls} for calls"
DOCTOR_SOUL_NO_TEXTS = "soul.md: nothing for texts (the built-in style, {style}), {calls} characters for calls"
DOCTOR_SOUL_NO_CALLS = "soul.md: {texts} characters for texts, nothing for calls (the built-in style, {style})"
DOCTOR_SOUL_NOTHING = "soul.md: nothing for texts or calls (the built-in style, {style})"
DOCTOR_SOUL_CALM_OFF = "; the calm-on-worry line after it is off ([personality] calm_on_worry)"
# doctor's fix for a missing YouTube helper in her own user: a deploy builds it in the release
DOCTOR_YOUTUBE_DEPLOY_FIX = (
    "{deploy} (if it says the root-run tools differ: read the diff, run {deploy} --update-tools, then {deploy} again)"
)
# doctor's Profile picture line ({file}: the picture's file name; {path}, {problem}: why it cannot be used; {name}: hers)
DOCTOR_PICTURE_ESTIA = "Estia's e. (docs/brand/estia-avatar.png)"
DOCTOR_PICTURE_OWN = "{file}"
DOCTOR_PICTURE_LETTER = "her letter, {letter}. (docs/brand/letters/{letter}.png)"  # a name other than Estia
DOCTOR_PICTURE_NONE = "none set (Signal keeps the one it has)"
DOCTOR_PICTURE_BAD = "{path}: {problem}; {name} runs without it (Signal keeps the one it has)"
DOCTOR_PICTURE_FIX = 'set avatar in the config to a PNG or JPEG of at most 5 MB (or "" for none), then restart'
DOCTOR_TEXT_ONLY = "{name}, text only (no [voice])"
DOCTOR_VOICE_TOO_OLD = "{name}, but the voice service is older than the language feature"
DOCTOR_VOICE_LACKS = "{name}, but the voice service does not have it"
DOCTOR_VOICE_SILENT = "{name}; the voice service does not answer, so its languages are unknown"
DOCTOR_VOICE_REINSTALL = "scripts/install-voice-daemon.sh (asks for your Mac password: run it yourself)"
# how the voice service starts again in her own user ({estia}: her wrapper); else DOCTOR_VOICE_REINSTALL
DOCTOR_VOICE_RESTART_OWN = "{estia} stop voice, then {estia} start voice"
# a folder in the voice service's models ({home}: hers in her own user, where deleting it needs DOCTOR_ADMIN; else ~)
DOCTOR_VOICE_MODELS = "{home}/Library/Application Support/local-assistant/voice-models/{folder}"
DOCTOR_ADMIN = " (with admin rights)"
# doctor's Speech detector line, with [calls] interrupt = "only speech", the default (voice.detector_state)
# doctor's Speech detector line: {falls}, what any sound does on calls until it is ready (DOCTOR_DETECTOR_FALLS, by
# what waits for it: barge-in with interrupt = "only speech", the pause with pause = "no speech", or both)
DOCTOR_DETECTOR_FALLS = {
    (True, False): "any sound stops her on calls",
    (False, True): "any sound keeps a turn open on calls",
    (True, True): "any sound stops her and keeps a turn open on calls",
}
DOCTOR_DETECTOR_OLD = "old voice service: {falls} until it is updated"
DOCTOR_DETECTOR_LOADING = "loading: {falls} until it is ready"
# {log}: DOCTOR_VOICE_LOG, or in her own user her wrapper's `logs voice` (her output is in /var/log/estia)
DOCTOR_DETECTOR_LOADING_FIX = (
    "wait a minute and run doctor again (the model, about 1 MB, downloads at the first start); if it stays, see {log}"
)
DOCTOR_DETECTOR_UNAVAILABLE = "unavailable (its model did not download or failed its check): {falls} until it is fixed"
# the reason is in the log: a failed download (no folder: tried again at the next start, the usual case), files not
# the pinned ones (the service never sets this folder aside: deleting it downloads it again) or a model that did not load
DOCTOR_DETECTOR_UNAVAILABLE_FIX = (
    "see {log} for why; a failed download is tried again when the voice service starts again: {restart}; files that"
    " are not the pinned ones: delete {folder} first{rights}"
)
DOCTOR_VOICE_LOG = "~/Library/Logs/local-assistant/voice.log"  # before her own user: the voice service's output
DOCTOR_DETECTOR_SILENT = "not ready: {falls} until it is"
# doctor's Turn detector line (Smart Turn, with [calls] early_end on): until it is ready, every turn waits the full pause
DOCTOR_TURN_OLD = "old voice service: turns end after the full pause until it is updated"
DOCTOR_TURN_LOADING = "loading: turns end after the full pause until it is ready"
DOCTOR_TURN_LOADING_FIX = (
    "wait a minute and run doctor again (the model, about 17 MB, downloads at the first start); if it stays, see {log}"
)
DOCTOR_TURN_UNAVAILABLE = (
    "unavailable (its model did not download or failed its check): turns end after the full pause until it is fixed"
)
# as the speech detector's, but changed files are set aside and downloaded again by the service itself: deleting the
# folder helps only when it could not be set aside (the log says so)
DOCTOR_TURN_UNAVAILABLE_FIX = (
    "see {log} for why; a failed download is tried again when the voice service starts again: {restart}; files that"
    " could not be set aside: delete {folder} first{rights}"
)
DOCTOR_TURN_SILENT = "not ready: turns end after the full pause until it is"
# doctor's Calls setting line: an older wording of what stops her, still read ({old}: the line, {new}: the new one)
DOCTOR_INTERRUPT_OLD = "{old} still works"
DOCTOR_INTERRUPT_OLD_FIX = "write {new} in [calls] instead"
DOCTOR_BARGE_UNREAD = "{barge} is not read: {new} wins"
DOCTOR_BARGE_UNREAD_FIX = "remove barge from [calls]"

# doctor's lines for the hardening (the Mac's own checks come with their words from hostsec and ownuser)
DOCTOR_OWN_USER_CONFIG = "she runs as her own user: /usr/bin/sudo /usr/local/estia/bin/estia doctor"
# doctor's "Always on": alive, not just installed (launchd's state and last exit code, the bridge's last sign of life)
DOCTOR_ALWAYS_ON_UNREAD = "installed; whether it runs could not be read"
DOCTOR_ALWAYS_ON_ALIVE = "running, last sign of life {when}"
DOCTOR_ALWAYS_ON_ALIVE_ONLY = "installed, last sign of life {when}"
DOCTOR_ALWAYS_ON_SILENT = "installed, no sign of life for {minutes} min"
DOCTOR_ALWAYS_ON_SILENT_SINCE = "installed, no sign of life since {when}"
DOCTOR_ALWAYS_ON_EXIT = ", last exit code {code}"
DOCTOR_ALWAYS_ON_NONE_YET = "running, no sign of life on record yet"
DOCTOR_ALWAYS_ON_RESTARTING = "restarting, last exit code {code}"
DOCTOR_ALWAYS_ON_STOPPED = "installed, stopped (last exit code {code})"
DOCTOR_ALWAYS_ON_STOPPED_NEVER = "installed, stopped"
DOCTOR_ALWAYS_ON_NOT_LOADED = "installed, not loaded: launchd does not run it"
DOCTOR_ALWAYS_ON_MISSING = "not installed yet (try scripts/run.sh first)"
# in her own user: her config is edited as her, and the bridge restarted through her wrapper
DOCTOR_OWN_EDIT = (
    "; in her own user: /usr/bin/sudo /usr/local/estia/bin/estia config, then /usr/bin/sudo /usr/local/estia/bin/estia "
    "restart"
)
# doctor's Signal number and signal-cli lines: a registration never verified, and a version the code was not written for
DOCTOR_SIGNAL_UNFINISHED = "signal-cli lists the bot's number, but its registration did not finish (no SMS code)"
DOCTOR_SIGNAL_UNFINISHED_FIX = "register it again, with the SMS code: setup_assistant.md, 2.3"
DOCTOR_SIGNAL_CLI_OTHER = (
    "{version}: Estia is written for signal-cli 0.14.x; another version may fail to register or receive"
)
DOCTOR_SIGNAL_CLI_FIX = (
    "install signal-cli 0.14.x (brew install signal-cli while Homebrew's is 0.14.x); in her own user, then "
    "/usr/bin/sudo /usr/local/estia/bin/estia-deploy (it copies it into her release)"
)
# doctor's Power line (pmset): a Mac that sleeps, or stays off after a power cut, stops her (setup_assistant.md, 2.8)
DOCTOR_POWER_OK = "no sleep, and the Mac starts again after a power failure"
DOCTOR_POWER_SLEEPS = "the Mac sleeps after {minutes} min without use"
DOCTOR_POWER_NO_RESTART = "it stays off after a power failure"
DOCTOR_POWER_OFF = "{problems}: she stops with it"
DOCTOR_POWER_FIX = (
    "/usr/bin/sudo /usr/bin/pmset -a sleep 0 autorestart 1, or System Settings → Energy (setup_assistant.md, 2.8)"
)
# doctor's Your home line (share.OPEN_TEXT): as her it cannot list your home, so it looks the usual names up one by one
DOCTOR_HOME_USUAL_NAMES = " (the usual names only: her account cannot list your home)"
DOCTOR_TOOLS_HERS = "she runs as _estia: your tools (ollama, uv) are not hers; what she runs is under Her programs"
DOCTOR_HOST_FAILED = "the check could not run ({why})"
# the projects folder, as the user doctor runs as reads it (as her: what her project tools can read)
DOCTOR_PROJECTS_OK = "{root}: {count} project(s); a tracked README reads fine"
DOCTOR_PROJECTS_SOME = "{root}: a tracked README reads fine; {closed} folder(s) or repositories cannot be read"
DOCTOR_PROJECTS_CLOSED = "{root} cannot be read: the project tools find nothing"
DOCTOR_PROJECTS_NONE_READ = "{root}: no tracked README can be read; {closed} folder(s) or repositories cannot be read"
DOCTOR_PROJECTS_EMPTY = "no git repository with a tracked README in {root}: nothing to check"
DOCTOR_PROJECTS_MISSING = "no folder {root}: the project tools find nothing"
DOCTOR_PROJECTS_ROOT_FIX = "set [projects] root in the config to your projects folder"
DOCTOR_PROJECTS_SHARE_FIX = "as you, in your checkout (no sudo): scripts/share-projects.sh {root}"
DOCTOR_DOCUMENTS_OK = "{folder}: shared and readable"
DOCTOR_DOCUMENTS_NO_HELPER = "the documents helper is not built: PDFs cannot be read"
# a release whose documents build failed (an older Swift): estia-deploy went live without the helper
DOCTOR_DOCUMENTS_NOT_BUILT = (
    "this release was built without the documents helper (its build failed): PDFs cannot be read"
)
DOCTOR_DOCUMENTS_REBUILD_FIX = (
    "{deploy} builds it again and says why it failed (it needs Swift 6.0 or later: swift --version)"
)
DOCTOR_DOCUMENTS_MISSING = "no folder {folder}"
DOCTOR_DOCUMENTS_CLOSED = "{folder} cannot be read: not shared with her"
DOCTOR_DOCUMENTS_UNSHARED = "{folder} is not shared with her yet (no share token)"
DOCTOR_DOCUMENTS_REFUSED = (
    "{folder} cannot be read: macOS refuses it (operation not permitted), which the share does not change"
)
# live, 14 Oct: macOS refused even the owner's SSH session a change to ~/Documents' access entries; hence ~/Estia-KB
DOCTOR_DOCUMENTS_WHY_HOME = (
    "macOS guards Documents, Desktop and Downloads; a folder in your home needs no extra permission"  # the docs' words
)
DOCTOR_DOCUMENTS_REFUSED_FIX = (
    f"{DOCTOR_DOCUMENTS_WHY_HOME}. Use your "
    '~/Estia-KB: [documents] folder = "/Users/<you>/Estia-KB" in her config, then scripts/share-documents.sh, then '
    "restart her. "
    "Doctor reads from your session, which macOS may judge apart from her background service: bridge.log (EPERM) says "
    "what she gets"
)
# the folder already is ~/Estia-KB: "use your ~/Estia-KB" would go in a circle
DOCUMENTS_REFUSED_KB = "macOS refuses ~/Estia-KB itself (unexpected)"
DOCTOR_DOCUMENTS_REFUSED_KB_FIX = (
    f"{DOCUMENTS_REFUSED_KB}: bridge.log (EPERM) says what she gets; doctor reads from your session, which macOS may "
    "judge apart from her background service; SECURITY.md, known gaps"
)
DOCTOR_DOCUMENTS_HERS = "the folder is in her own home, not yours: documents are always your ~/Estia-KB"
DOCTOR_DOCUMENTS_HERS_FIX = (
    'in her config: [documents] folder = "/Users/<you>/Estia-KB" (your folder, in full), then restart her'
)
# in her own user her config still names another folder (the first share's, in Documents): the share never opens it
DOCTOR_DOCUMENTS_NOT_KB = "{folder} is not your ~/Estia-KB: the share opens only ~/Estia-KB to her"
DOCTOR_DOCUMENTS_SHARE_FIX = "as you, in your checkout (no admin rights): scripts/share-documents.sh"
DOCTOR_DOCUMENTS_MAKE_FIX = "as you, in your checkout (no admin rights): scripts/share-documents.sh (it makes it)"
DOCTOR_DOCUMENTS_MKDIR_FIX = "as you: mkdir -p {folder}"  # before her own user: the bridge runs as you, no share
# the documents helper's build in a checkout; in her own user a deploy builds it (DOCTOR_YOUTUBE_DEPLOY_FIX)
DOCTOR_DOCUMENTS_BUILD_FIX = "cd tools/documents && swift build -c release"
DOCTOR_SAFETY_UNCHANGED = "no changed safety number seen"
DOCTOR_DEVICES_NOT_COUNTED = "not counted yet (the bridge counts them at its start)"
DOCTOR_PIN_NONE = "not pinned: any model put under the name {model} would get her messages"
DOCTOR_PIN_FIX = (
    'add model_digest = "sha256:{digest}" to the config (the digest Ollama lists for {model} now), then restart her'
)
DOCTOR_PIN_FIX_UNKNOWN = "curl -s {base}/api/tags lists {model} with its digest: add it as model_digest to the config"
DOCTOR_PIN_UNREAD = "pinned, but Ollama does not list {model} with a digest now: answers stay paused until it does"
DOCTOR_PIN_OK = "{model} has the pinned digest"
DOCTOR_PIN_CHANGED = "{model} does not have the pinned digest: answers are paused"
DOCTOR_PIN_CHANGED_FIX = (
    'Ollama lists sha256:{digest} now; only if you changed the model yourself: model_digest = "sha256:{digest}", '
    "then restart her"
)
DOCTOR_HISTORY_KEPT = "kept until you delete it ([privacy] keep_days is not set)"
DOCTOR_HISTORY_NOT_YET = "older than {days} days deleted each night; not pruned yet (between 03:00 and 05:00)"
DOCTOR_HISTORY_PRUNED = "older than {days} days deleted each night; last {when}"
DOCTOR_HISTORY_FIX = "she prunes between 03:00 and 05:00 when idle: check that the bridge runs at night (bridge.log)"

# security facts from status.json (part_status.security_lines; doctor uses the same words)
SAFETY_NUMBER_LABEL = "Safety number"
SAFETY_NUMBER_CHANGED = "changed {when}: messages from you that Signal could not open were dropped"
SAFETY_NUMBER_FIX = (
    "compare it on your phone with /usr/bin/sudo /usr/local/estia/bin/estia signal listIdentities on the Mac, then trust it "
    '(setup_assistant.md, 7. Operations, "Safety number changed")'
)
LINKED_LABEL = "Her Signal account"
LINKED_ONE = "1 device: this Mac (checked {when})"
LINKED_MORE = "{count} devices, only this Mac expected (checked {when})"
LINKED_FIX = (
    "a device was linked to her number: list it with /usr/bin/sudo /usr/local/estia/bin/estia signal listDevices, remove it "
    "with /usr/bin/sudo /usr/local/estia/bin/estia signal removeDevice -d N, then follow SECURITY.md, "
    '"If the Mac is compromised"'
)
MODEL_LABEL = "Model"
MODEL_PAUSED_LINE = "answers paused since {when}: {model} does not match the pinned digest"
DOCTOR_MODEL_CLOUD = (
    "Ollama lists {model} as a cloud model: it runs on Ollama's servers, not on this Mac, so answers are paused"
)
MODEL_CLOUD_LINE = "answers paused since {when}: {model} is an Ollama cloud model (it runs on Ollama's servers)"


# the model for this Mac's memory (16 or 24 GB: setup.GEMMA_12B; 32 GB or more: setup.GEMMA_26B), as setup picks it
SETUP_MODEL_FIX = "scripts/setup.sh (it downloads the model that fits this Mac's memory and makes local-worker from it)"
DOCTOR_OLLAMA_OK = "{version} answers ({program}), with its JSON-schema library"
DOCTOR_OLLAMA_OLD = "{version} answers: older than {needed}, the first with JSON-schema output on the MLX engine"
DOCTOR_OLLAMA_DOWN_FIX = "/usr/bin/sudo /bin/launchctl kickstart -k system/{label} (setup's background service)"
DOCTOR_OLLAMA_NOT_LOADED_FIX = (
    "/usr/bin/sudo /bin/launchctl bootstrap system {daemon} (setup's background service, not loaded now)"
)
DOCTOR_OLLAMA_JOB_FIX = "{restart} (the background service that runs Ollama here)"
DOCTOR_OLLAMA_UPDATE_FIX = (
    "scripts/install-ollama.sh (setup's background service: updated in place, the old version kept for a switch back)"
)
DOCTOR_OLLAMA_SERVICE_CHANGED = (
    "setup's background service file for Ollama ({daemon}) is not the one this version of setup writes"
)
DOCTOR_OLLAMA_SERVICE_FIX = (
    "scripts/install-ollama.sh (it writes the file again and starts Ollama again: a few seconds without answers)"
)
DOCTOR_OLLAMA_SETUP_FIX = "scripts/setup.sh (it installs Ollama's official build and starts it)"
# {big} and {small}: setup's tags (setup.GEMMA_26B, setup.GEMMA_12B), filled in by doctor
DOCTOR_MODEL_TAG = (
    "{model} is not one of the builds for Apple silicon (MLX) that setup installs and the tests use ({big}, {small})"
)
DOCTOR_MODEL_BUILD = "{model} is made from {source}: " + DOCTOR_MODEL_TAG.removeprefix("{model} is ")

# her model gets Ollama's prompt for Gemma 4's small models (doctor's Model prompt line; ollama_build.gets_small_prompt)
DOCTOR_MODEL_PROMPT = (
    "{model} gets Ollama's prompt for Gemma 4's small models, not Google's prompt for 12B and larger: some answers "
    "come back empty and are asked again"
)
DOCTOR_MODEL_PROMPT_FIX = (
    "stop her, then scripts/setup.sh makes local-worker again with Google's prompt (it asks first and prints the new "
    "digest); with model_digest set, answers pause until the config has that digest: put it in, then start her "
    "(setup_assistant.md, 2.2)"
)
DOCTOR_MODEL_PROMPT_FIX_HERS = (
    "/usr/bin/sudo /usr/local/estia/bin/estia stop, then scripts/setup.sh as you makes local-worker again with "
    "Google's prompt (it asks first and prints the new digest); with model_digest set, put that digest in with "
    "/usr/bin/sudo /usr/local/estia/bin/estia config, then /usr/bin/sudo /usr/local/estia/bin/estia start "
    "(setup_assistant.md, 2.2)"
)
DOCTOR_MODEL_PROMPT_OTHER_FIX = (
    "make {model} again with the line RENDERER gemma4-large in its Modelfile (setup_assistant.md, 2.2), then pin the "
    "new digest (the Model pin line) and restart her"
)


def model_build_fix(model: str, *, big: str, small: str) -> str:
    """The step to an MLX build: the alias made again by setup, or another tag (`big` and `small`: setup's tags)."""
    if model == "local-worker":
        return (
            f"ollama rm local-worker, then run {SETUP_MODEL_FIX}; then pin the new digest (the Model pin line) and "
            "restart her"
        )
    return f"ollama pull {big} ({small} on 16 or 24 GB), set model to it, then restart her"


def model_cloud_fix(model: str) -> str:
    """The step back to a model on this Mac: the alias made again from a downloaded model, or another name."""
    if model == "local-worker":
        return f"ollama rm local-worker, then run {SETUP_MODEL_FIX}, then restart her"
    return 'set model to one downloaded to this Mac ("ollama list" shows them), then restart her'


MODEL_PAUSED_FIX = (
    "doctor on the Mac shows the digest Ollama has now; pin it (model_digest in the config) only if you changed the "
    "model yourself, then restart her"
)

# What each part last did for real (part_status), in doctor and /status: written by code, never by Gemma.
PART_LABELS = {
    "voice": "Voice",
    "mail": "Mail ({name})",  # one line per mailbox
    "calendar": "Calendar ({name})",  # one line per calendar
    "devices": "Lights and plugs",
    "web": "Web search",
    "places": "Places",
    "spotify": "Spotify",
    "youtube": "YouTube",
    "documents": "Documents",
}
PART_LABELS_PLAIN = {"calendar": "Calendar"}  # the part without a name: the one calendar named "calendar"
PART_UNTESTED = "set up, not tested yet"
PART_SET_UP_AGAIN = "set up again, not tested yet"  # its fix was saved after the failure
PART_WORKING = "working, last {verb} {when}"
PART_VERBS = {"mail": "read", "calendar": "read", "youtube": "read", "documents": "read"}  # the other parts: "used"
PART_FAILING = "failing since {when}: {reason}"
PART_WARNING = "{text}; since {when}: {reason}"  # a part that works, but not all of it: "!" with the warning's fix
# the fixed reasons of a failure (at most 60 characters with an HTTP status): never text from a service
PART_REASONS = {
    "sign_in": "sign-in expired",
    "no_answer": "no answer",
    "busy": "too many requests",
    "refused": "refused by the service",
    "no_key": "no key",
    "helper_missing": "helper missing",
    "helper_failed": "helper failed",
    "not_set": "secret address not saved",
    "gone": "secret address no longer works",
    "login": "TP-Link login refused",
    "unsupported": "a device uses a login not supported yet",
    "loading": "still loading",
    "bad_answer": "unreadable answer",
    "no_consent": "Calendars.Read not granted yet",
    "not_shared": "the folder cannot be read",
    "os_refused": "macOS refuses it (operation not permitted)",
    "no_password": "app-specific password not saved",
    "password_refused": "user name or password refused",
    "no_calendar": "no calendar by that name",
    "not_paired": "Hue Bridge not paired yet",
    "hue_key": "Hue Bridge refused its key",
    "certificate": "Hue Bridge certificate changed: nothing sent",
    "no_light": "a light is not on the Hue Bridge",
    "not_hers": "another program holds its address: nothing sent",  # the voice service's port, not her account's
    "ocr_unavailable": "scanned pages not read (text recognition failed)",  # a warning: text pages are still read
    "error": "unexpected error",
}
# the next step per "part/reason", else per reason
PART_FIXES = {
    "mail/sign_in": "scripts/mail-login.sh <address of {name}>",
    "mail/refused": "check the app registration (setup_assistant.md, Mail)",
    "calendar/helper_missing": "cd tools/ical && uv sync --frozen",
    "calendar/not_set": "scripts/set-calendar-url.sh {name}",
    "calendar/gone": "scripts/set-calendar-url.sh {name}",
    "calendar/sign_in": "scripts/mail-login.sh <address of {mailbox}>",
    "calendar/no_consent": (
        "an administrator grants it in Entra (the app → API permissions → add Calendars.Read → Grant admin consent; "
        "no new sign-in needed), or sign in again: scripts/mail-login.sh <address of {mailbox}>"
    ),
    "calendar/refused": "check that the app registration has Calendars.Read (setup_assistant.md, Calendar)",
    "calendar/no_password": "scripts/set-calendar-password.sh {name}",
    "calendar/password_refused": (
        "a new app-specific password, then scripts/set-calendar-password.sh {name} (or check user in config.toml)"
    ),
    "calendar/no_calendar": (
        "check caldav and calendar in config.toml: the server's address and the calendar's name as the calendar app "
        "shows it"
    ),
    "voice/no_answer": "check the voice service (scripts/install-voice-daemon.sh)",
    "voice/refused": "check the voice service (scripts/install-voice-daemon.sh)",
    "voice/not_hers": (
        "find the program at her voice service's address: /usr/bin/sudo /usr/sbin/lsof -nP -iTCP:8150 -sTCP:LISTEN; "
        "quit it, then /usr/bin/sudo /usr/local/estia/bin/estia stop voice and "
        "/usr/bin/sudo /usr/local/estia/bin/estia start voice"
    ),
    "devices/helper_missing": "cd tools/tapo && uv sync --frozen",
    "devices/login": "scripts/set-tapo-credentials.sh",
    "devices/no_answer": "check the device: power, or a new address (scripts/tapo-discover.sh)",
    "devices/not_paired": "scripts/hue-pair.sh",
    "devices/hue_key": "scripts/hue-pair.sh",
    "devices/certificate": "if the bridge was replaced or reset: scripts/hue-pair.sh",
    "devices/no_light": "scripts/tapo-discover.sh lists the bridge's lights; fix the light id in [[tapo.devices]]",
    "web/no_key": "scripts/set-web-keys.sh",
    "web/refused": "check the keys: scripts/set-web-keys.sh",
    "places/no_key": "scripts/setup-google-maps.sh",
    "places/refused": "check the key and its APIs: scripts/setup-google-maps.sh",
    "spotify/sign_in": "scripts/spotify-login.sh",
    "spotify/refused": "scripts/spotify-login.sh",
    "youtube/helper_missing": "cd tools/youtube && uv sync --frozen (in her own user a deploy builds it: see doctor)",
    "youtube/refused": "wait: YouTube refuses this Mac for a while after many requests or a bot check",
    "youtube/bad_answer": "YouTube may have changed: a newer pinned version of tools/youtube may be needed (bridge.log)",
    "documents/not_shared": (
        "in her own user, as you (no admin rights): scripts/share-documents.sh (it makes ~/Estia-KB when missing); "
        "before it: mkdir -p ~/Estia-KB"
    ),
    "documents/os_refused": (
        "macOS guards Documents, Desktop and Downloads: use your ~/Estia-KB (doctor says how); bridge.log names the "
        "refusal (EPERM); SECURITY.md, known gaps"
    ),
    # the folder already is ~/Estia-KB (part_status.fix_for, estia_kb): moving it there would go in a circle
    "documents/os_refused/estia_kb": (
        f"{DOCUMENTS_REFUSED_KB}: bridge.log names the refusal (EPERM); SECURITY.md, known gaps"
    ),
    "documents/helper_missing": f"{DOCTOR_DOCUMENTS_BUILD_FIX} (in her own user a deploy builds it: see doctor)",
    # her start's warm-up failed, or Vision failed on a page (live, 15 Oct: under her account, before helper_env gave
    # Foundation its home): text pages are still read
    "documents/ocr_unavailable": (
        "restart her: each start tries text recognition again and reads again the scanned pages it skipped (in her "
        "own user: /usr/bin/sudo /usr/local/estia/bin/estia restart); bridge.log names the failure"
    ),
    "sign_in": "sign in again (setup_assistant.md)",
    "no_answer": "check the service and the Mac's internet connection",
    "busy": "wait: it is tried again later",
    "refused": "check the key or sign-in (setup_assistant.md)",
    "no_key": "add the key (setup_assistant.md)",
    "helper_missing": "install the helper (setup_assistant.md)",
    "helper_failed": "see the bridge's log (bridge.log)",
    "not_set": "save it again (setup_assistant.md)",
    "gone": "save it again (setup_assistant.md)",
    "login": "save the login again (setup_assistant.md)",
    "unsupported": "use the Kasa or Tapo app for that device (README, Lights and plugs)",
    "loading": "wait: after a macOS update the first start takes about 15 minutes",
    "bad_answer": "see the bridge's log (bridge.log)",
    "no_consent": "grant the permission (setup_assistant.md)",
    "no_password": "save it (setup_assistant.md, Calendar)",
    "password_refused": "save a new one (setup_assistant.md, Calendar)",
    "no_calendar": "check the config (setup_assistant.md, Calendar)",
    "not_paired": "pair it (setup_assistant.md)",
    "hue_key": "pair it again (setup_assistant.md)",
    "certificate": "if it was replaced or reset, pair it again (setup_assistant.md)",
    "no_light": "check the light's id (setup_assistant.md)",
    "not_hers": "see the bridge's log (bridge.log)",
    "error": "see the bridge's log (bridge.log)",
}
