"""Ollama's official build: setup's and doctor's words for its install, checks, service and update (ollama_build)."""

from __future__ import annotations

# fmt: off
__all__ = [
    "OLLAMA_APP", "OLLAMA_APP_FIX", "OLLAMA_CHECKS", "OLLAMA_CURRENT_NOT_LINK", "OLLAMA_DOWNLOAD_FAILED",
    "OLLAMA_DRY_RUN_GOES_ON", "OLLAMA_FOLDER_KEPT", "OLLAMA_FOLDER_REFUSED", "OLLAMA_GGUF_SOURCE", "OLLAMA_HOMEBREW",
    "OLLAMA_HOMEBREW_FIX", "OLLAMA_HOMEBREW_REMOVE", "OLLAMA_HOMEBREW_THERE", "OLLAMA_INSTALL", "OLLAMA_INSTALLED",
    "OLLAMA_JOB", "OLLAMA_JOB_APP", "OLLAMA_JOB_FIX", "OLLAMA_JOB_HOMEBREW", "OLLAMA_KEPT", "OLLAMA_LOG_EMPTIED",
    "OLLAMA_LOG_PRIVATE", "OLLAMA_NOT_AT_BOOT", "OLLAMA_NOT_AT_BOOT_FIX", "OLLAMA_NOT_A_FOLDER", "OLLAMA_NOT_FOUND",
    "OLLAMA_NOT_FOUND_FIX", "OLLAMA_NOT_UP", "OLLAMA_NO_TERMINAL", "OLLAMA_ON_INPUT", "OLLAMA_OTHER",
    "OLLAMA_OTHER_FIX", "OLLAMA_PATH_ADDED", "OLLAMA_PATH_WOULD", "OLLAMA_REFUSED", "OLLAMA_RESTART_AGENT",
    "OLLAMA_RESTART_SYSTEM", "OLLAMA_ROLLED_BACK", "OLLAMA_SERVICE", "OLLAMA_SERVICE_CHANGED",
    "OLLAMA_SERVICE_FAILED", "OLLAMA_SHARED_NOT_FOUND", "OLLAMA_SHARED_UNREAD", "OLLAMA_UNSAFE",
    "OLLAMA_UNSAFE_OWNER", "OLLAMA_UNSAFE_UNREAD", "OLLAMA_UNSAFE_WRITABLE", "OLLAMA_UNTESTED", "OLLAMA_UNTESTED_FIX",
    "OLLAMA_UP", "OLLAMA_UPDATE", "OLLAMA_WHY_ACCOUNT", "OLLAMA_WHY_ACCOUNT_UNREAD", "OLLAMA_WHY_FINGERPRINT",
    "OLLAMA_WHY_LIBRARY", "OLLAMA_WHY_LINK", "OLLAMA_WHY_NOT_APPLE", "OLLAMA_WHY_NOT_NOTARIZED",
    "OLLAMA_WHY_NO_PROGRAM", "OLLAMA_WHY_OLD", "OLLAMA_WHY_OPEN", "OLLAMA_WHY_SHARED", "OLLAMA_WHY_TEAM",
    "OLLAMA_WHY_UNPACKED", "OLLAMA_WHY_UNSIGNED",
]
# fmt: on

# Ollama's official build (owner, 15 Oct: "use only the official build"): setup's and doctor's words (ollama_build)
OLLAMA_KEPT = "✓ Ollama: its official build {version} answers ({program}); kept."
OLLAMA_HOMEBREW = (
    "Homebrew's Ollama answers ({program}): its build has no JSON-schema library (libollama_xgrammar.dylib), so "
    "Ollama's engine for Apple silicon (MLX) gives no JSON-schema output, which she needs"
)
OLLAMA_HOMEBREW_REMOVE = "brew services stop ollama, then brew uninstall ollama (the models in ~/.ollama stay)"
OLLAMA_HOMEBREW_FIX = f"{OLLAMA_HOMEBREW_REMOVE}, then run scripts/setup.sh again: it installs Ollama's official build"
OLLAMA_HOMEBREW_THERE = (
    "Homebrew's Ollama is installed too ({path}), without the JSON-schema library: setup never uses it"
)
OLLAMA_APP = (
    "the Ollama app {state} ({path}): it updates itself, starts only when someone logs in on the Mac's screen, and "
    "stops any other Ollama when its own cannot start"
)
OLLAMA_APP_FIX = (
    "quit it (osascript -e 'quit app \"Ollama\"'), then move it to the Trash: mv {path} ~/.Trash/; then run "
    "scripts/setup.sh again (it installs Ollama's official build as a background service)"
)
OLLAMA_NOT_FOUND = "something answers at 127.0.0.1:11434, and its program could not be found"
OLLAMA_NOT_FOUND_FIX = (
    "/usr/bin/sudo /usr/sbin/lsof -nP -iTCP:11434 -sTCP:LISTEN names it; stop it, then run scripts/setup.sh again"
)
OLLAMA_OTHER = "the Ollama that answers ({program}) is not one setup can keep: {why}"
OLLAMA_OTHER_FIX = "stop it, then run scripts/setup.sh again: it installs Ollama's official build"
OLLAMA_WHY_UNSIGNED = "its signature does not check out"
OLLAMA_WHY_TEAM = "signed by {team}, not Ollama's ({want})"
OLLAMA_WHY_NOT_APPLE = (
    "its signature names Ollama's team ID but is not Apple's Developer ID certificate for it (made without Apple, as "
    "anyone can)"
)
OLLAMA_WHY_OPEN = (
    "it listens for other machines too ({addresses}), and Ollama has no login: set OLLAMA_HOST=127.0.0.1:{port} in "
    "its service, so only the Mac itself reaches it"
)
OLLAMA_WHY_ACCOUNT = "it runs as {account}, another account on this Mac, not you: that account controls her answers"
OLLAMA_WHY_ACCOUNT_UNREAD = "the account it runs as could not be read"
OLLAMA_WHY_SHARED = "{program}, run by {account}, also listens on its port: her requests may go to that one instead"
OLLAMA_SHARED_NOT_FOUND = "a program that could not be found"
OLLAMA_SHARED_UNREAD = "an account that could not be read"
OLLAMA_WHY_OLD = "its version {version} is older than {needed}, the first with JSON-schema output on the MLX engine"
OLLAMA_WHY_LIBRARY = "no {library}: no JSON-schema output on the MLX engine"
OLLAMA_WHY_FINGERPRINT = "its fingerprint is sha256:{got}, not the pinned sha256:{want}"
OLLAMA_WHY_UNPACKED = "it could not be unpacked"
OLLAMA_WHY_LINK = "{name} links outside its folder"
OLLAMA_WHY_NO_PROGRAM = "no ollama program in it"
OLLAMA_WHY_NOT_NOTARIZED = "ollama: Apple's notarization check did not accept it"
OLLAMA_NOT_A_FOLDER = "not a folder"
OLLAMA_INSTALL = (
    "Ollama: its official build {version} ({size} MB, from GitHub; {needed} or later is needed), checked before "
    "anything is installed: its fingerprint (sha256), every program's signature (Ollama's developer ID, {team}) and "
    "Apple's notarization. It goes into {folder} (no admin rights), then runs as a background service that starts "
    "with the Mac."
)
OLLAMA_CHECKS = (
    "  (then every program's signature, codesign --verify --strict -R: Apple's Developer ID certificate for Ollama's "
    "team {team}; Apple's notarization, spctl; both JSON-schema libraries)"
)
OLLAMA_INSTALLED = "✓ Ollama: {version} checked and installed in {folder}."
OLLAMA_FOLDER_KEPT = "✓ Ollama: {folder} is there and checks out; kept."
OLLAMA_FOLDER_REFUSED = (
    "✗ Ollama: {folder} is there but does not check out ({why}). Move it aside (mv {folder} {folder}.refused), then "
    "run scripts/setup.sh again."
)
OLLAMA_REFUSED = (
    "✗ Ollama: the download did not check out, so nothing was installed: {why}. Run scripts/setup.sh again; if it "
    "says the same, report it (SECURITY.md)."
)
OLLAMA_DOWNLOAD_FAILED = (
    "✗ Ollama: the download did not finish: check the internet connection, then run scripts/setup.sh again."
)
OLLAMA_CURRENT_NOT_LINK = "✗ Ollama: {path} is not a link: move it aside, then run scripts/setup.sh again."
OLLAMA_PATH_ADDED = "✓ Ollama: added to new Terminal windows ({line} in {profile})"
OLLAMA_PATH_WOULD = "Ollama: would add {line} to {profile}, so new Terminal windows find ollama"
OLLAMA_SERVICE = (
    "Ollama's background service: it runs as you, starts with the Mac (no login needed) and is reachable only from the\n"
    "  Mac itself. Its file: {daemon}; its log: {log}.\n"
    "Your Mac password next, if asked: admin rights for this service only (nothing shows while you type)."
)
OLLAMA_ON_INPUT = "the service file on its input"
OLLAMA_SERVICE_CHANGED = (
    "Ollama: its background service file ({daemon}) is not the one this version of setup writes: it is written "
    "again, then Ollama starts again (a few seconds without answers)."
)
OLLAMA_SERVICE_FAILED = (
    "✗ Ollama: its background service was not installed (the lines above say why). Run scripts/setup.sh again; "
    "/usr/bin/sudo /bin/launchctl print system/{label} shows its state."
)
OLLAMA_NOT_UP = (
    "✗ Ollama: no answer within {seconds} s. Look at /usr/bin/sudo /bin/launchctl print system/{label} and {log}, "
    "then run scripts/setup.sh again."
)
OLLAMA_UP = "✓ Ollama: {version} answers, always on (it starts with the Mac, no login needed)."
OLLAMA_UNTESTED = "{version} answers, not {pin}, the version she is tested with"
OLLAMA_UNTESTED_FIX = (
    "install {pin} the way this Ollama was installed; or remove it and its service, then run scripts/setup.sh again"
)
OLLAMA_NOT_AT_BOOT = (
    "no background service starts it with the Mac ({program}): after a restart she has no model until someone starts "
    "it again"
)
OLLAMA_NOT_AT_BOOT_FIX = (
    "stop it, then run scripts/setup.sh again: it installs Ollama's official build as a background service that "
    "starts with the Mac"
)
OLLAMA_JOB = (
    "another background service runs Ollama ({label}, {plist}) and nothing answers now: a second one from setup would "
    "fight it for 127.0.0.1:11434 forever"
)
OLLAMA_JOB_FIX = "start it again: {restart}; or remove it; then run scripts/setup.sh again"
OLLAMA_JOB_HOMEBREW = (
    "Homebrew's background service for Ollama is set up ({plist}): at a login it can take 127.0.0.1:11434 first, "
    "with Homebrew's build (no JSON-schema output on Apple silicon)"
)
OLLAMA_JOB_APP = "the Ollama app starts at each login ({plist}) and stops any other Ollama when its own cannot start"
OLLAMA_RESTART_SYSTEM = (
    "/usr/bin/sudo /bin/launchctl kickstart -k system/{label} (when launchctl cannot find it: /usr/bin/sudo "
    "/bin/launchctl bootstrap system {plist})"
)
OLLAMA_RESTART_AGENT = (
    "/bin/launchctl kickstart -k gui/{uid}/{label} (when launchctl cannot find it: /bin/launchctl bootstrap gui/{uid} "
    "{plist})"
)
OLLAMA_UPDATE = (
    "Ollama: {version} answers; the tested version is now {pin}. It is downloaded and checked while {version} keeps "
    "running; then her Ollama switches to it and starts again (a few seconds without answers)."
)
OLLAMA_ROLLED_BACK = (
    "✗ Ollama: {pin} did not take over, so {folder} is in use again ({version}) and its service was started again. "
    "{new} stays for a look; scripts/setup.sh tries again."
)
OLLAMA_DRY_RUN_GOES_ON = "  (setup stops here until this is fixed; the rest as it would run after)"
OLLAMA_UNSAFE = (
    "✗ Ollama: {path} is not yours alone ({why}), and its background service would run the program there as you at "
    "every start of the Mac. Fix: /bin/chmod go-w {path} when it is yours, else move it aside; then run "
    "scripts/setup.sh again."
)
OLLAMA_UNSAFE_OWNER = "another account owns it"
OLLAMA_UNSAFE_WRITABLE = "other accounts can change it"
OLLAMA_UNSAFE_UNREAD = "it could not be read"
OLLAMA_LOG_EMPTIED = "✓ Ollama: its log was over 10 MB and is now empty ({log})."
OLLAMA_LOG_PRIVATE = (
    "Ollama: its log ({log}) made yours and private, used from its next start.\n"
    "  Your Mac password next, if asked (nothing shows while you type)."
)
OLLAMA_NO_TERMINAL = (
    "scripts/install-ollama.sh asks for your Mac password for Ollama's background service: run it in your own "
    "Terminal window. scripts/install-ollama.sh --dry-run shows every step and changes nothing."
)
OLLAMA_GGUF_SOURCE = "a llama.cpp (GGUF) build of Gemma 4"
