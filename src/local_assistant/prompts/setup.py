"""Setup's lines: what the wizard asks and what it says back (scripts/setup.sh)."""

from __future__ import annotations

# fmt: off
__all__ = [
    "HER_CONFIG_EDIT", "NANO_SAVE", "SETUP_CAPTCHA", "SETUP_CAPTCHA_ASK", "SETUP_CHECK_HERS", "SETUP_CHORDS_ADDED",
    "SETUP_CHORDS_HERS", "SETUP_CHORDS_NOT_ADDED", "SETUP_CHORDS_NO_CONFIG", "SETUP_CHORDS_ON", "SETUP_CODE_FAILED",
    "SETUP_CODE_STOPPED", "SETUP_DONE", "SETUP_DOWNLOAD", "SETUP_EXTRAS", "SETUP_EXTRAS_ASK", "SETUP_EXTRAS_HERS",
    "SETUP_EXTRAS_YOURS", "SETUP_KNOWN_NUMBER", "SETUP_KNOWN_NUMBER_ASK", "SETUP_LANGUAGE", "SETUP_LANGUAGE_ASK",
    "SETUP_LANGUAGE_CHOSEN", "SETUP_LANGUAGE_DRY_RUN", "SETUP_LANGUAGE_UNKNOWN", "SETUP_LOCK_AGAIN", "SETUP_LOCK_ASK",
    "SETUP_LOCK_HOW", "SETUP_LOCK_PIN", "SETUP_LOCK_RETRY", "SETUP_LOOK_AGAIN", "SETUP_MODEL_AFTER_OLLAMA",
    "SETUP_MODEL_NOT_MADE", "SETUP_MODEL_OTHER_BUILD", "SETUP_MODEL_PIN_HERS", "SETUP_MODEL_PIN_YOURS",
    "SETUP_MODEL_PROMPT_BY_HAND", "SETUP_MODEL_REMADE", "SETUP_MODEL_REMADE_PIN", "SETUP_MODEL_REMADE_UNLISTED",
    "SETUP_MODEL_REMAKE", "SETUP_MODEL_REMAKE_AFTER_OLLAMA", "SETUP_MODEL_REMAKE_ASK", "SETUP_MODEL_REMAKE_DRY_RUN",
    "SETUP_MODEL_REMAKE_FAILED", "SETUP_MODEL_REMAKE_LATER", "SETUP_MODEL_SMALL_PROMPT", "SETUP_MODEL_STOP_HERS",
    "SETUP_MODEL_STOP_YOURS", "SETUP_MOVE", "SETUP_MOVE_ASK", "SETUP_MOVE_ASK_SSH", "SETUP_MOVE_FAILED", "SETUP_MOVE_OLDER",
    "SETUP_MOVE_OLDER_ASK", "SETUP_MOVE_OLDER_ASK_SSH", "SETUP_NAME", "SETUP_NAME_ASK", "SETUP_NAME_BAD", "SETUP_NAME_CHOSEN", "SETUP_NAME_DRY_RUN",
    "SETUP_NO_TERMINAL", "SETUP_NUCLEAR", "SETUP_NUMBER", "SETUP_NUMBER_ASK", "SETUP_NUMBER_BAD", "SETUP_OLD_PIN_ASK",
    "SETUP_OLLAMA_GOES_ON", "SETUP_OPENING", "SETUP_PERSONALITY", "SETUP_PERSONALITY_HERS", "SETUP_PINNED", "SETUP_PROJECTS",
    "SETUP_PROJECTS_ADDED", "SETUP_PROJECTS_ASK", "SETUP_PROJECTS_BAD_LEVEL", "SETUP_PROJECTS_CHOSEN",
    "SETUP_PROJECTS_DRY_RUN", "SETUP_PROJECTS_FOLDER", "SETUP_PROJECTS_HERS", "SETUP_PROJECTS_HOME",
    "SETUP_PROJECTS_LEVEL", "SETUP_PROJECTS_LEVELS", "SETUP_PROJECTS_LINK", "SETUP_PROJECTS_LINK_ON_THE_WAY",
    "SETUP_PROJECTS_NOT_ADDED", "SETUP_PROJECTS_NO_CONFIG", "SETUP_PROJECTS_NO_FOLDER", "SETUP_PROJECTS_ON",
    "SETUP_PROJECTS_OUTSIDE", "SETUP_PROJECTS_REFUSED", "SETUP_PROJECTS_SHARE", "SETUP_SECURITY", "SETUP_SECURITY_ASK",
    "SETUP_SHARE_FAILED", "SETUP_SMALLER_MODEL", "SETUP_SMALLER_MODEL_ASK", "SETUP_SMALLER_MODEL_DRY_RUN",
    "SETUP_SMALLER_MODEL_NO", "SETUP_SMS_ASK", "SETUP_SMS_SENT", "SETUP_SSH_SCREEN", "SETUP_STOPPED", "SETUP_STYLE",
    "SETUP_STYLE_ASK", "SETUP_STYLE_CHOSEN", "SETUP_STYLE_DRY_RUN", "SETUP_STYLE_HERS", "SETUP_STYLE_HERS_DRY_RUN",
    "SETUP_STYLE_UNKNOWN", "SETUP_TOO_SMALL", "SETUP_VOICE", "SETUP_VOICE_ASK", "SETUP_VOICE_UNKNOWN", "SETUP_WORD",
    "SETUP_WORD_ASK", "SETUP_WORD_MISSING",
]
# fmt: on

# setup (scripts/setup.sh), in the terminal; {name}: her name
SETUP_OPENING = (  # its first line: every step, in order
    "{name} setup: Ollama, the model, her name, her Signal number, who may talk to her, the config, always on,\n"
    "  the security step and the extras.\n"
)
SETUP_NO_TERMINAL = (  # the same words as scripts/setup.sh's
    "Setup asks questions and Signal's one-time codes: run scripts/setup.sh in your own Terminal window."
)
SETUP_STOPPED = "Setup stopped. Run scripts/setup.sh again to go on: steps already done are skipped."
# the one-time word ({number}: her number; {word}; {name}; {seconds}: RECEIVE_SECONDS)
SETUP_WORD = (
    "Your account: from your own Signal on your phone, start a new chat with {number} (not Note to Self) and send "
    "exactly:  {word}\n"
    "  Only the account that sends it gets answers from {name}."
)
SETUP_WORD_ASK = "Press Enter once your phone shows it as sent (setup then looks for {seconds} seconds)."
SETUP_WORD_MISSING = """'{word}' has not arrived. The usual reasons, most common first:
  1. It went to the wrong number: send it to {number} ({name}'s new number), not to yourself or Note to Self.
  2. Enter was pressed before the message was sent: send it first, then look again.
  3. The {seconds}-second wait ran out: just look again.
  4. Your phone shows a clock or one tick only: wait until it is sent, then look again."""
SETUP_LOOK_AGAIN = "Press Enter to look again, or type q to stop."
SETUP_PERSONALITY = (  # no soul.md yet ({soul}: where it goes; {style}: her built-in style, {words}: STYLE_WORDS)
    "Personality: {name}'s built-in style, {style} ({words}). To give her yours instead:\n"
    "  cp soul.example.md {soul} && chmod 600 {soul}   (then edit it; it counts from her next answer)"
)
# her config and style file are hers alone after the move: nano runs as her, through her wrapper (sudoedit would give
# your account a copy while it is edited, and run the editor as you)
HER_CONFIG_EDIT = "/usr/bin/sudo /usr/local/estia/bin/estia config"
NANO_SAVE = "Ctrl-O, Enter saves; Ctrl-X quits"
SETUP_PERSONALITY_HERS = (
    "Personality: {name}'s built-in style (hearth, the default, or playful: [personality] style in her config) unless\n"
    "you wrote your own. To write yours, as her "
    "(the first time from soul.example.md):\n"
    "  /usr/bin/sudo /usr/local/estia/bin/estia config soul   ({save}; it counts from her next answer)"
)
# the setup wizard's style question (config.STYLES; {name}: hers): one plain line each, hearth first (Enter)
SETUP_STYLE = (
    "Style: how {name} sounds while there is no soul.md of yours (the rules always win; [personality] style in the "
    "config changes it later):\n"
    "  hearth (Enter): steady, kind and frank. One concern on a risky plan; a joke only when asked for fun or on "
    "banter.\n"
    "  playful: warm, curious and playful. The answer first, now and then a light joke, never on a worry."
)
SETUP_STYLE_ASK = "Which style? Recommendation: hearth. (Enter keeps hearth)"
SETUP_STYLE_DRY_RUN = "  (asks which style; Enter keeps hearth)"
SETUP_STYLE_UNKNOWN = '  "{answer}" is not one of them: hearth or playful.'
SETUP_STYLE_CHOSEN = "✓ Style: {style}."
# in her own user setup never writes her config: the lines to add as her ({edit}: HER_CONFIG_EDIT, {save}: NANO_SAVE)
SETUP_STYLE_HERS = (
    "Her config is hers alone, so setup does not write it. For this style:\n"
    "  1. {edit}   ({save})\n"
    "     and add, or change, these lines:\n"
    "    [personality]\n"
    '    style = "{style}"\n'
    "  2. /usr/bin/sudo /usr/local/estia/bin/estia restart"
)
SETUP_STYLE_HERS_DRY_RUN = "  (then prints the lines to add to her config, [personality] and style, and her restart)"
SETUP_EXTRAS_HERS = (
    "Her config is hers alone: add each section as her, then restart her:\n  {edit}   ({save})\n"
    "  /usr/bin/sudo /usr/local/estia/bin/estia restart"
)
SETUP_EXTRAS = (
    "Extras (all optional): picking one shows its steps, nothing is installed here; each can be added later with\n"
    "  scripts/setup.sh --extras."
)
SETUP_EXTRAS_ASK = "Which ones? Recommendation: only the ones you need now. (names, numbers or all; Enter for none)"
SETUP_EXTRAS_YOURS = "Her config: {path} (add each section there; she reads it from her next start)."
# a re-run's alias with Ollama's prompt for Gemma 4's small models (made before 0.9.3; empty answers, 17 Oct)
SETUP_MODEL_SMALL_PROMPT = (
    "! Model: local-worker gets Ollama's prompt for Gemma 4's small models, not Google's prompt for 12B and larger: "
    "some answers come back empty and are asked again."
)
SETUP_MODEL_REMAKE = (
    "  Making it again fixes that and changes only the prompt: the same model ({tag}), the same context.\n"
    "  Its digest changes, so with model_digest in the config her answers pause until the config has the new one\n"
    "  (printed below). No leaves it as it is. {stop}"
)
SETUP_MODEL_STOP_HERS = "Stop her first: /usr/bin/sudo /usr/local/estia/bin/estia stop (start puts her back)."
SETUP_MODEL_STOP_YOURS = "Stop her first if she runs (scripts/run.sh: Ctrl-C)."
SETUP_MODEL_REMAKE_ASK = "Make local-worker again now? Recommendation: yes, once she is stopped."
SETUP_MODEL_REMAKE_DRY_RUN = '  (dry run: asks "{ask} [y/N]"; only y makes it)'
SETUP_MODEL_REMAKE_LATER = "  Later: scripts/setup.sh again (or by hand: setup_assistant.md, 2.2)."
SETUP_MODEL_REMAKE_AFTER_OLLAMA = "  Setup makes it again once the Ollama fix above is done and setup runs again."
SETUP_MODEL_PROMPT_BY_HAND = (
    "  Setup cannot tell which of its models local-worker is made from, so it leaves it as it is. Make it again by "
    "hand with the line RENDERER gemma4-large (setup_assistant.md, 2.2), then pin the new digest."
)
SETUP_MODEL_REMAKE_FAILED = (
    "✗ Model: local-worker was not made again (the lines above say why); it stays as it was. Check that Ollama runs, "
    "then run scripts/setup.sh again."
)
SETUP_MODEL_REMADE = "✓ Model: local-worker made again with Google's prompt for 12B and larger."
SETUP_MODEL_REMADE_PIN = (
    '  Its new digest: sha256:{digest}. With model_digest in the config: model_digest = "sha256:{digest}" in place '
    "of the old line, {where}. Without it, nothing more to do."
)
SETUP_MODEL_REMADE_UNLISTED = "  Ollama does not list its digest yet: doctor's Model pin line shows it once it does."
SETUP_MODEL_PIN_YOURS = "in {path}, then restart her"
SETUP_MODEL_PIN_HERS = (
    "as her: /usr/bin/sudo /usr/local/estia/bin/estia config, then /usr/bin/sudo /usr/local/estia/bin/estia start "
    "(or restart, when she runs)"
)
SETUP_MODEL_NOT_MADE = (
    "local-worker was not made (the lines above say why). Check that Ollama runs, then run scripts/setup.sh again."
)
# the Registration Lock right after the SMS code (harden.LOCK_NOTE first); Enter turns it on (owner, 19 Oct)
SETUP_LOCK_HOW = "  Yes: a new PIN (4 to 64 characters, no spaces), typed twice; q there skips it. No or q: the security step asks again."
SETUP_LOCK_ASK = "Turn on Registration Lock now? Recommendation: yes."
SETUP_LOCK_PIN = "Registration Lock PIN, a new one (hidden: nothing shows while you type; q skips): "
SETUP_LOCK_AGAIN = "The same PIN again (hidden: nothing shows while you type): "
SETUP_LOCK_RETRY = (
    "Press Enter to try again, or type q to leave it for the security step (scripts/harden.sh offers it again)."
)
# always on: why her own user, and what yes and no do, in plain words ({name}); Enter moves her (owner, 19 Oct)
SETUP_MOVE = (
    "Always on: {name} starts with the Mac, without a login, as her own hidden user _estia.\n"
    "  She never runs as you or as root, the Mac's all-powerful admin account. Her data goes to /var/estia, which only\n"
    "  that user reads; her code to /usr/local/estia, which only admin rights change. Then no program of yours reads\n"
    "  her data or changes her code without your Mac password, as long as no admin rule skips the password.\n"
    "  Yes: your Mac password once (nothing shows while you type), for admin rights to make her user and copy her files.\n"
    "  No: she runs only while scripts/run.sh is open, and setup asks again at its next run."
)
SETUP_MOVE_OLDER = (
    "Always on: {name} runs as you today. In her own hidden user _estia she starts with the Mac, without a login.\n"
    "  She never runs as you or as root, the Mac's all-powerful admin account. Her data moves to /var/estia, which\n"
    "  only that user reads; her code to /usr/local/estia, which only admin rights change. Then no program of yours\n"
    "  reads her data or changes her code without your Mac password, as long as no admin rule skips the password.\n"
    "  Yes: your Mac password once (nothing shows while you type); /usr/bin/sudo /usr/local/estia/bin/estia rollback\n"
    "  goes back within 7 days. No: she stays as she is."
)
SETUP_MOVE_ASK = "Install it now? Recommendation: yes."
SETUP_MOVE_OLDER_ASK = "Move her into her own user now? Recommendation: yes."
# over SSH without screen: the same question, recommending what SETUP_SSH_SCREEN says (Enter is no there)
SETUP_MOVE_ASK_SSH = "Install it now? Recommendation: no here; run setup again inside screen."
SETUP_MOVE_OLDER_ASK_SSH = "Move her into her own user now? Recommendation: no here; run setup again inside screen."
SETUP_SECURITY_ASK = "Go through the security step now? Recommendation: yes."
# the security step: the hardened level, then the nuclear one in a short line (owner, 19 Oct: only for deeply
# technical users; never part of setup)
SETUP_SECURITY = (
    "Security level hardened: remote logins (SSH) with keys only, no admin rule that skips your password, her files\n"
    "  private and her Registration Lock, each with its reason and changed only on your yes. FileVault and the rest\n"
    "  only you can change are shown with their fixes. No: scripts/harden.sh runs the same step later.\n"
    "  Yes: your Mac password, if asked (once 5 minutes have passed since admin rights were last used; nothing shows\n"
    "  while you type)."
)
SETUP_NUCLEAR = (
    "  The nuclear level, only for deeply technical users (scripts/harden.sh --nuclear): the Mac's firewall on,\n"
    "  python3 and ruby off its list, stealth mode and the keychain lock, each asked first. It may stop\n"
    "  other services on this Mac, such as a file share or a page opened from a laptop. Never part of setup."
)
SETUP_SSH_SCREEN = (
    "Over a remote login (SSH), a dropped connection stops the move, and it is undone. Run it inside screen, built\n"
    "  into macOS: answer no here (Enter), type screen, then run scripts/setup.sh again (after a drop, screen -r goes back to it)."
)
SETUP_SHARE_FAILED = (
    "✓ Always on: {name} runs in her own user {user}. Only her projects folder is not shared yet (the lines above\n"
    "  say why): fix that, then run {share}. A folder outside your home: give {user} read access to it yourself."
)
SETUP_MOVE_FAILED = (
    "The move did not finish: the ✗ above says why. Fix that, look at every step with {move} --dry-run,\n"
    "then run scripts/setup.sh again. The security step and the extras come after the move."
)
# setup's projects question (an extra; recommendation: no)
SETUP_PROJECTS = (
    "Projects (extra, off by default): {name} reads the git projects directly in one folder of this Mac but never\n"
    "  changes them. Only files git tracks, never secret-like names such as .env, never outside the folder you named.\n"
    "  Yes: her account passes through your home to that folder, so the files in your home that every account may\n"
    "  open are open to her too (the move's end names them and offers to close them). No: she reads no projects."
)
SETUP_PROJECTS_ASK = "Should she see projects on this Mac? Recommendation: no."
SETUP_PROJECTS_DRY_RUN = (
    '  (dry run: asks "Should she see projects on this Mac? Recommendation: no.", then the folder and the level)'
)
SETUP_PROJECTS_FOLDER = (
    "Which folder holds them? Recommendation: a folder in your home, which the move shares with her. "
    "(Enter: ~/dev; q: no projects)"
)
SETUP_PROJECTS_LEVELS = (
    "  summary: the README, open to-do items and recent commits; docs: also the project's documents; code: also its "
    "source code"
)
SETUP_PROJECTS_HOME = "That is your home itself: name the folder in it that holds your projects."
SETUP_PROJECTS_NO_FOLDER = "No folder {folder}: type another one, or q for no projects."
SETUP_PROJECTS_LEVEL = "How much may she read: summary, docs or code? Recommendation: summary. (Enter: summary)"
SETUP_PROJECTS_BAD_LEVEL = "'{answer}' is not a level: summary, docs or code."
SETUP_CHORDS_ON = "✓ Chords: on already ([chords] in the config)."
SETUP_CHORDS_NO_CONFIG = "Chords: no config yet at {path}. scripts/setup.sh writes it; then run --extras again."
SETUP_CHORDS_ADDED = "✓ Chords: [chords] added to {path}; read from her next start (restart her)."
SETUP_CHORDS_NOT_ADDED = "✗ Chords: not added, the config does not load ({why}); {path} is unchanged."
SETUP_CHORDS_HERS = (  # her own user: her config is hers alone
    "Her config is hers alone, so setup does not write it. To switch chords on, add this line as her, then restart "
    "her:\n    [chords]\n  {edit}   ({save})\n  /usr/bin/sudo /usr/local/estia/bin/estia restart"
)
SETUP_PROJECTS_ON = "✓ Projects: on already ([projects] in the config: root = the folder, default = the level)."
SETUP_PROJECTS_NO_CONFIG = (
    "Projects: no config yet at {path}. scripts/setup.sh writes it and asks about projects there."
)
SETUP_PROJECTS_ADDED = "✓ Projects: [projects] added to {path}; read from her next start (restart her)."
SETUP_PROJECTS_NOT_ADDED = "✗ Projects: not added, the config does not load ({why}); {path} is unchanged."
# in her own user her config is hers alone: the lines, the share and the restart, run by you
SETUP_PROJECTS_HERS = (
    "Her config is hers alone, so setup does not write it. To switch projects on:\n"
    "  1. /usr/bin/sudo /usr/local/estia/bin/estia config   (Ctrl-O, Enter saves; Ctrl-X quits)\n"
    "     and add, with the folder by its absolute path (her ~ is /var/estia):\n"
    "    [projects]\n"
    "    root = {root}\n"
    '    default = "{level}"\n'
    "  2. {share}\n"
    "  3. /usr/bin/sudo /usr/local/estia/bin/estia restart"
)
SETUP_PROJECTS_SHARE = (
    "scripts/share-projects.sh {folder}   (no admin rights needed: her user reads that folder read-only)"
)
SETUP_PROJECTS_OUTSIDE = (  # share.py refuses a folder outside your home
    "{folder} is outside your home, so scripts/share-projects.sh does not share it: give _estia read access to it "
    "yourself"
)
# share.refusal's words for a folder outside your home (inside it, share.refusal itself says why)
SETUP_PROJECTS_LINK = "{folder} is a symbolic link: name the folder it leads to"
SETUP_PROJECTS_LINK_ON_THE_WAY = "a folder on the way to {folder} is a symbolic link"
SETUP_PROJECTS_REFUSED = "{why}. Type another one, or q for no projects."
SETUP_PROJECTS_CHOSEN = (
    "✓ Projects: {folder}, level {level}. [projects] in the config changes it (per project too: docs = [...], "
    "code = [...])."
)
SETUP_PINNED = (
    "✓ Model pin: the digest Ollama lists for local-worker now is in the config. Answers pause, with a note, when "
    "another model takes that name."
)
SETUP_CHECK_HERS = (
    "Her doctor runs as her own user, and it only reads.\n"
    "  Your Mac password again, if asked (once 5 minutes have passed since admin rights were last used; nothing\n"
    "  shows while you type)."
)
SETUP_DONE = (
    "\nDone when the check shows no ✗ and {name}'s welcome has reached your phone (she sends it when she "
    "first runs: always on, or scripts/run.sh). Reply to it to see her answer."
)
SETUP_CODE_FAILED = (
    "That did not work ({reason}).\n"
    "  Check the code (from the latest SMS) and the PIN, if the number had one, then type them again, or type q to stop."
)
# her Signal number: a number set up here before ({number}), a new one ({name}), the captcha and the SMS code
SETUP_KNOWN_NUMBER = (
    "{number} finished registering with Signal on this Mac before. Yes uses it: no new captcha or SMS code, and the\n"
    "  security step offers its Registration Lock. No registers a number again, with a new captcha link and SMS code."
)
SETUP_KNOWN_NUMBER_ASK = (
    "Use the Signal number already set up here ({number})? Recommendation: yes, when it is her number."
)
SETUP_NUMBER = (
    "{name} needs her own Signal number: one you control that no Signal app uses (registering it here logs out any\n"
    "  Signal app on it). Recommendation: a new prepaid SIM in any phone, used for nothing else and kept topped up:\n"
    "  a lapsed number can go to someone else."
)
SETUP_NUMBER_ASK = "Her number, with + and the country code (like +306900000000; spaces and dashes are fine):"
SETUP_NUMBER_BAD = "That is not + and the country code and the digits (like +306900000000). Run setup again."
SETUP_CODE_STOPPED = (  # q at the code or the old PIN, before a code was checked or after one was refused
    "Stopped: her number is not registered yet.\n"
    "  Run setup again: it registers the number again, with a new SMS code (and maybe a new captcha link)."
)
SETUP_CAPTCHA = (
    "Signal asks for a captcha (new numbers usually do):\n"
    "  1. On any device, open https://signalcaptchas.org/registration/generate.html and solve it.\n"
    "  2. Right-click (or long-press) 'Open Signal' and copy the link; it starts with signalcaptcha://\n"
    "  3. Paste it below and press Enter once: nothing shows while you paste, and it is never stored. q stops."
)
SETUP_CAPTCHA_ASK = "captcha link (hidden: nothing shows while you paste): "
SETUP_SMS_SENT = "Signal has sent a code by SMS to {number}: it arrives on the phone with her SIM."
SETUP_SMS_ASK = "The code Signal sent by SMS (hidden: nothing shows while you type; q stops): "
SETUP_OLD_PIN_ASK = (
    "Registration Lock PIN, only if this number used Signal with one before (a new SIM: Enter; hidden: nothing shows "
    "while you type): "
)


# the setup wizard's language question ({name}: her name), and the doctor's line about it
SETUP_LANGUAGE = (
    "Language: {name} listens and speaks in one language at a time, whatever language a message is in; what the code\n"
    "  writes (her notes, /status, /help) stays English. Changed later by typing /language in Signal, which then wins\n"
    "  over language in the config."
)
SETUP_LANGUAGE_ASK = (
    "Which one? Recommendation: the language spoken at home. (a name or the letters in brackets; Enter: English)"
)
SETUP_LANGUAGE_DRY_RUN = "  (asks which one; Enter: English)"
SETUP_LANGUAGE_UNKNOWN = '  "{answer}" is not one of these.'
SETUP_LANGUAGE_CHOSEN = "✓ Language: {name}."
# the setup wizard's name question ({default}: Estia; {female}, {male}: the suggestions)
SETUP_NAME = (
    "Name: every home names its own. The assistant answers to it, and her Signal profile shows it, with (AI) after it.\n"
    "  1 to 20 letters, spaces or hyphens; a suggested name brings its English voice. Changed later with name in the config.\n"
    "  1. {default} (default)\n"
    "  female: {female}\n"
    "  male: {male}\n"
    "  or type any name"
)
SETUP_NAME_ASK = "Which name? Recommendation: a short name everyone at home likes to say. (Enter keeps {default})"
SETUP_NAME_DRY_RUN = "  (asks for the name; Enter keeps {default}; a name you type asks for a female or male voice)"
SETUP_NAME_BAD = '  "{answer}" does not fit ({problem}).'
SETUP_VOICE = (
    "Voice: her English voice in voice notes and calls, once voice is on (the other ten languages have one voice for "
    "now)."
)
SETUP_VOICE_ASK = "Female or male voice? Recommendation: the one that fits the name. (Enter: female)"
SETUP_VOICE_UNKNOWN = "  Type f for female or m for male."
SETUP_NAME_CHOSEN = (  # {voice}: the Kokoro voice written into the config's [voice] lines
    "✓ Name: {name}, with a {kind} voice ({voice}, for English voice notes and calls once [voice] is on)."
)
SETUP_TOO_SMALL = "✗ This Mac has {memory:.0f} GB of memory. {name} needs 16 GB or more."
# 16 and 24 GB: Gemma 4 12B, then a question (owner, 19 Oct: "use with caution", what that means for her answers in
# plain words, never a test or what failed in one; SECURITY.md, known gaps, has the details); Enter stays a no
SETUP_SMALLER_MODEL = (
    "! Gemma 4 12B, the smaller model that fits this Mac: use it with caution. Gemma 26B, on 32 GB or more, keeps her\n"
    "  rules better. After a forwarded audio file this one may follow orders hidden in it, and on a call it may switch\n"
    "  a light or plug before you say yes. Code still keeps facts from an audio file out of her memory and, after one,\n"
    "  makes a device change wait for your yes."
)
SETUP_SMALLER_MODEL_ASK = (
    "Go on with the smaller model? No stops setup, with nothing downloaded. Recommendation: yes, with caution."
)
SETUP_SMALLER_MODEL_DRY_RUN = '  (dry run: asks "{ask} [y/N]"; only y downloads it)'
SETUP_SMALLER_MODEL_NO = (
    "Nothing was downloaded, and setup stops here. Gemma 26B needs a Mac with 32 GB or more; scripts/setup.sh asks "
    "again when it runs again."
)
SETUP_DOWNLOAD = (
    "  Nothing to answer meanwhile; the Mac stays awake. If the download stops, run setup again: it goes on where it "
    "stopped."
)
SETUP_MODEL_OTHER_BUILD = (
    "✗ Model: {tag} came down as {got}, not the tested build {want}: Ollama's library changed it since. Nothing was "
    "made from it. Update Estia (git pull), then run scripts/setup.sh again."
)
SETUP_OLLAMA_GOES_ON = (
    "  (she is set up already, so setup goes on with the other steps; run the fix, then scripts/setup.sh again)"
)
SETUP_MODEL_AFTER_OLLAMA = (
    "! Model: local-worker is not there yet; setup downloads it once the Ollama fix above is done and setup runs again."
)
