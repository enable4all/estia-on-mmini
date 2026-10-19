"""The user's devices, the lock and the model's pause: code's notes and replies (commands.Guard, /lock)."""

from __future__ import annotations

# fmt: off
__all__ = [
    "DEVICES_ALLOWED", "DEVICES_CALLS", "DEVICES_FORGET_HINT", "DEVICES_FORGET_PHONE", "DEVICES_FORGET_UNKNOWN",
    "DEVICES_FORGOTTEN", "DEVICES_HEADER", "DEVICES_HELD_HEADER", "DEVICES_HELD_LINE", "DEVICES_NOT_FROM_KNOWN",
    "DEVICES_OK", "DEVICES_OK_ALREADY", "DEVICES_OK_UNKNOWN", "DEVICES_PHONE", "DEVICES_USAGE", "DEVICE_HELD",
    "DEVICE_HELD_SILENT", "DEVICE_HELD_UNNAMED", "LINKED_ALERT", "LOCKED", "LOCK_NOT_FROM_KNOWN", "MODEL_CLOUD",
    "MODEL_CLOUD_RESUMED", "MODEL_CLOUD_SPOKEN", "MODEL_PAUSED", "MODEL_PAUSED_SPOKEN", "MODEL_RESUMED", "UNLOCKED",
    "UNLOCK_NOT_LOCKED", "UNLOCK_TOO_SOON",
]
# fmt: on

# The user's devices and the lock (commands.Guard): code's words only, never Gemma's. Device 1 is the phone the user's
# Signal account was registered on; linked devices (a computer, a tablet) are 2 and up.
DEVICE_HELD = (
    "A message came from a new linked device ({device}) and was held: not answered, not read. To allow that device, "
    "send /devices ok {device} from your phone."
)
DEVICE_HELD_UNNAMED = "A message came from a device Signal did not name and was held: not answered, not read."
DEVICE_HELD_SILENT = (
    "A message came from linked device {device}, silent for more than {days} days, and was held: not answered, not "
    "read. Signal gives a new linked device the number of a removed one. If it is still yours, send /devices ok "
    "{device} from your phone."
)
# /devices: two sections under bold headings (markup, sent as Signal's text styles), one device per line
DEVICES_HEADER = "**Devices I answer**"
DEVICES_PHONE = "Device {device}: your phone"
DEVICES_ALLOWED = "Device {device}: allowed {day}"
DEVICES_HELD_HEADER = "**Held, not answered**"
DEVICES_HELD_LINE = "Device {device}: held since {day} · /devices ok {device} allows it"
DEVICES_CALLS = "Calls name no device: a call from any device of yours rings."
DEVICES_OK = "Allowed: device {device}. I answer it from now on."
DEVICES_OK_ALREADY = "Device {device} is already allowed."
DEVICES_OK_UNKNOWN = "No message from a device {device} was held. /devices lists the held ones."
DEVICES_NOT_FROM_KNOWN = "Not allowed: /devices ok works only from a device I already answer, such as your phone."
DEVICES_USAGE = (
    "Usage: /devices, /devices ok N (N: a held device's number from /devices), or /devices forget N (a device you "
    "unlinked)"
)
DEVICES_FORGOTTEN = "Forgotten: device {device}. A message from that number is held again until /devices ok {device}."
DEVICES_FORGET_PHONE = "Not forgotten: device 1 is your phone, the device your account was registered on."
DEVICES_FORGET_UNKNOWN = "No device {device} is on the list. /devices lists them."
DEVICES_FORGET_HINT = (
    "After you unlink a device in Signal, send /devices forget N: Signal gives its number to the next device you link. "
    "A linked device silent for {days} days is held again."
)
LOCKED = (
    "Locked: I answer nothing and take no calls until /unlock comes from this device ({device}), {minutes} minutes "
    "from now at the earliest. Messages sent meanwhile are not read and not answered later; reminders wait until "
    "then, and messages I start myself are skipped."
)
UNLOCK_TOO_SOON = "Still locked: /unlock works from this device from {time}."
UNLOCKED = "Unlocked: I answer again. Messages sent while I was locked were not read: send again what you still need."
UNLOCK_NOT_LOCKED = "I'm not locked. /lock stops all my answers and calls until /unlock."
LOCK_NOT_FROM_KNOWN = "Not allowed: /lock and /unlock work only from a device I already answer, such as your phone."

# her own Signal account's devices (only this Mac is expected) and the model's pin: code's notes to the user
LINKED_ALERT = (
    "My Signal account has {count} devices; only this Mac should be there. A device linked to my number can read our "
    'chats: doctor on the Mac shows it, and SECURITY.md, "If the Mac is compromised", says what to do.'
)
MODEL_PAUSED = (
    "Answers are paused: the model {model} on {host} no longer has the digest pinned in my config, so no message goes "
    "to it. Commands and reminders still work. If you changed the model yourself, doctor on the Mac shows the new "
    "digest to pin."
)
MODEL_PAUSED_SPOKEN = "Answers are paused: the model on the Mac changed. Details are in a text."
MODEL_RESUMED = "Answers are back: the model {model} has its pinned digest again."
# the model always runs on this Mac: an alias Ollama lists as a cloud model gets nothing
MODEL_CLOUD = (
    "Answers are paused: Ollama lists the model {model} as a cloud model, which runs on Ollama's servers, not on "
    "{host}. {name}'s model runs only on {host}, so no message goes to it. Commands and reminders still work. Doctor on "
    "the Mac shows the fix."
)
MODEL_CLOUD_SPOKEN = "Answers are paused: the model is not on this Mac. Details are in a text."
MODEL_CLOUD_RESUMED = "Answers are back: the model {model} runs on {host} again."
