# Estia: setup and operations

How to install, configure, run and operate Estia, how it is built, how it is tested and how it is secured.

Quick reference: [README.md](README.md). Every config key: [config.example.toml](config.example.toml). Phone checks:
[docs/e2e-checklist.md](docs/e2e-checklist.md).

Contents: [1 What it is](#1-what-it-is) · [2 Install](#2-install) · [3 Optional parts](#3-optional-parts) · [4 Using it](#4-using-it) · [5 What it remembers](#5-what-it-remembers) · [6 Tests](#6-tests) · [7 Operations](#7-operations) · [8 Security](#8-security)

Placeholders in this guide: `<repo>` is the folder you cloned into (`~/estia` in 2.1); `<bot-number>` is the bot's Signal number in full, with its `+` and country code and no spaces (for example `+306900000000`), and commands use it exactly like that; `<your ACI>` is your own Signal account ID (a UUID: a long ID of letters, digits and dashes); `<your user>` is your own macOS user (`whoami` shows it).

Since the move into her own user (2.7), she runs as `_estia`, a hidden macOS account: her files are under `/var/estia`
(where this guide says `~/.config/local-assistant`, `~/Library/Application Support/local-assistant`,
`~/.local/share/signal-cli` or `~/Library/Logs/local-assistant`, read `/var/estia/…` for her), readable only by `_estia`.
Her commands run as her with admin rights (`/usr/bin/sudo`, which asks for your Mac password), by their full path: `/usr/bin/sudo /usr/local/estia/bin/estia doctor|logs|check|mail-login|spotify-login|secret set|tapo-discover`.

## 1. What it is

Gemma (the local model, served by Ollama under the name in `model`) as a personal assistant for one person, reached over
Signal from a phone. She is called Estia unless you give her another name (`name` in the config, 2.4); this guide says
Estia. It answers from its own knowledge and the tools you turn on: memory and search of earlier chats,
learning from links you name, the Mac's health, a read-only overview of your projects (an extra, off by default), photos, voice notes and calls,
lights and plugs, read-only mail and calendar, web search, music, and places and routes.

The model always runs on the Mac: a cloud model is refused (2.2). Everything else stays on the Mac too, except Signal
itself and the online services you turn on (see the README, "What stays home, and what goes online").

## 2. Install

`scripts/setup.sh` does 2.1 to 2.7 for you (and `SETUP-WITH-YOUR-AI.md` lets your AI assistant guide you through it). The steps below are what it does, for doing it by hand. Work through them in order; each step can be checked before you go on.

### 2.1 Tools

Start with Homebrew, the usual installer for Mac tools. Its installer asks for your Mac password once, because it writes
to `/opt/homebrew`. It also installs
Apple's Command Line Tools, which give you `git` and `swift`:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile && eval "$(/opt/homebrew/bin/brew shellenv)"
```

Then get the code. Clone it to `~/estia`, outside Desktop, Documents and Downloads: macOS blocks background services
from those folders, so the bridge would fail at boot with "Operation not permitted".

```sh
git clone https://github.com/enable4all/estia-on-mmini.git ~/estia && cd ~/estia
```

From here on, `<repo>` is that folder. Homebrew sends anonymous usage reports (analytics) unless told not to:
`scripts/setup.sh` runs every brew command with `HOMEBREW_NO_ANALYTICS=1`, and by hand `brew analytics off` turns them
off for every brew command from then on, yours included. Install the tools and start Ollama:

```sh
brew analytics off                  # no usage reports to Homebrew
brew install signal-cli ollama uv
uv python install 3.13
brew services start ollama          # Ollama in the background; it starts again at each login (see 2.8)
uv sync --frozen                    # the bridge's environment (Python 3.13)
```

`uv sync` uses the bridge's standard-library runtime; there are no runtime dependencies to install.

### 2.2 Model (once)

Estia talks to a model served by Ollama. The setup was developed with Gemma 4 26B (`gemma4:26b`), which supports the
tool calls the bridge needs.

The download is large: about 18 GB for `gemma4:26b`, and the [tag page](https://ollama.com/library/gemma4) lists the exact
size. It can take an hour or more on a slow line, so run it with `caffeinate -i`, which keeps the Mac awake meanwhile. If
it stops, run the same command again: Ollama picks up where it left off.

```sh
caffeinate -i ollama pull gemma4:26b
ollama list                         # the tag and its size
```

Memory: while loaded, the model needs about 18 to 20 GB, so this guide assumes a Mac with 32 GB. On 16 or 24 GB, pick a
smaller Gemma 4 tag that supports tools (the library page lists them), pull it the same way, and set `model` to it in 2.4.
Its answers will be weaker.

Then give the model the name the code uses, with a larger context window. Ollama's default window is small on many setups,
and Ollama cuts a longer prompt without a word. Estia sends about 12,000 tokens of history, so a small window would lose
the start of a long chat:

```sh
mf="$(mktemp)" && printf 'FROM gemma4:26b\nPARAMETER num_ctx 32768\n' > "$mf" && ollama create local-worker -f "$mf"; rm -f "$mf"
```

Leave `model` out of the config (2.4), and Estia uses `local-worker`. After your first message, `ollama ps` shows the
model with its context in the CONTEXT column; it should read 32768.

The model always runs on the Mac. Ollama also offers cloud models, which run on Ollama's servers: their tag ends in
`-cloud` or `:cloud` (`gpt-oss:120b-cloud`). Such a `model` is refused when the config is read. A name you made yourself
can still point to one, so the bridge also reads her model's entry in Ollama's own list (`/api/tags`), where
`remote_host` or `remote_model` marks a cloud model: at the start, every 5 minutes and before every chat. Once the
check sees a cloud model, no message goes to it: answers pause, a note from the code says why
(commands and reminders still work), the end of the pause is one more note, and doctor shows ✗ with the fix. An entry
that cannot be read changes nothing. `local-worker`, made above from a downloaded model, is local. To also notice another
model put in under the same name, pin its digest with `model_digest` (`config.example.toml`).

Some tags also come as an Apple silicon (MLX) build. If `ollama ps` shows far more memory than expected, stop the model
with `ollama stop <tag>`.

### 2.3 Signal number (once)

Estia needs its own Signal account, on a phone number you control that no Signal app on your phone uses. Registering
the number with signal-cli logs out any Signal app that used it. Here `<bot-number>` is that number in full: the `+`, the
country code and the digits, with no spaces (for example `+306900000000`).

`scripts/setup.sh` registers it with you. The captcha link, the SMS code and a Registration Lock PIN (only if the
number had one) go into hidden prompts and reach signal-cli on its input: never on screen, and never on a program's
command line, which every user of the Mac can read (`ps` lists them). The commands it prints show placeholders. By hand,
use the same mode of signal-cli (JSON-RPC), which reads one request per line:

1. Start it with the first line below and leave it running. No `-a`: signal-cli 0.14.9 offers register and verify only
   in this mode.
2. Paste the register request (the second line). If the answer asks for a captcha (new numbers usually do), open
   https://signalcaptchas.org/registration/generate.html, solve it, copy the **Open Signal** link (it starts with
   `signalcaptcha://`), and paste the request again with the link, without its `signalcaptcha://` start (the third
   line).
3. Paste the verify request with the code Signal sends by SMS, without its dash (the fourth line). If the number had a
   Registration Lock, add its PIN next to the code: `"pin":"<PIN>"`.
4. Ctrl-D ends signal-cli.

```text
signal-cli -Djava.net.preferIPv4Stack=true --trust-new-identities on-first-use jsonRpc --receive-mode manual
{"jsonrpc":"2.0","method":"register","params":{"account":"<bot-number>"},"id":1}
{"jsonrpc":"2.0","method":"register","params":{"account":"<bot-number>","captcha":"<the link without signalcaptcha://>"},"id":2}
{"jsonrpc":"2.0","method":"verify","params":{"account":"<bot-number>","verificationCode":"<code from the SMS>"},"id":3}
```

What you paste there shows in that terminal window, but never in the list of running programs.

Use `-Djava.net.preferIPv4Stack=true` on every signal-cli command, as the bridge does. It makes signal-cli use IPv4, the
older kind of internet address: registering over IPv6 can fail with "HTTP 403" at the captcha step. With `[calls]` on,
the bridge runs the JVM build of signal-cli instead, which takes the flag from `JAVA_OPTS` (see 3, Calls).

Only one signal-cli process may use an account at a time. Stop the bridge before you run signal-cli by hand.

**Registration Lock.** Right after the SMS code, `scripts/setup.sh` asks to set one: a PIN, typed hidden twice, that
Signal asks for before her number can be registered on another device. Without it, whoever receives her SMS code (a SIM
swap, a lapsed prepaid number, the device with her SIM) can take her number over. Store the PIN in your password
manager: without it and her data, the number is locked for 7 days. Setup passes it to signal-cli the same way, on its
input, never on a command line and never on the screen:

```sh
signal-cli -Djava.net.preferIPv4Stack=true --trust-new-identities on-first-use -a <bot-number> jsonRpc --receive-mode manual   # then {"jsonrpc":"2.0","method":"setPin","params":{"pin":"…"},"id":1} on its input
```

For an install that is already running, `scripts/harden.sh` does the same in a short maintenance window: it stops her,
sets the PIN as her own user, and starts her again (2.10). The lock lapses after 7 days with the Mac offline.

The bridge sets the bot's profile name to her name and "(AI)" on its first start: "Estia (AI)", or with the config's
`name` (2.4) that name, set again after a rename. It sets her profile picture the same way, from the config's `avatar`
(2.4): Estia's e. when it is left out, your own picture when it names one, none with `avatar = ""`. The picture is sent
once, and again only after the file changes; if the file cannot be used or Signal refuses it, the log says so and the
next start tries again. The move into her own user copies your picture into her config folder (2.7, step 5).

### 2.4 Config (once, then when you add parts)

From `<repo>`:

```sh
mkdir -p ~/.config/local-assistant
cp config.example.toml ~/.config/local-assistant/config.toml
chmod 600 ~/.config/local-assistant/config.toml
```

The bridge refuses a config file that others can read. Keep it out of git.

Edit the file:

1. `bot_number`: the bot's number from 2.3, for example `"+306900000000"`.
2. `allowed_acis`: your own Signal account ID (a UUID: a long ID of letters, digits and dashes). Only these accounts get answers. There are two ways to find it:
   - **Send the bot a word only you know** (for example `estia-4821`) from your phone. Then, with the bridge stopped,
     fetch it:

     ```sh
     signal-cli -Djava.net.preferIPv4Stack=true -a <bot-number> -o json receive -t 20
     ```

     Find the line whose `message` is your word and copy its `sourceUuid` (your ACI) into the list. Match the word, not
     the profile name: anyone can give themselves your name. (`scripts/setup.sh` does exactly this for you. When the word
     has not arrived, it lists the usual reasons and looks again on Enter, with the same word.)
   - **Or let the bridge show it.** Keep the placeholder, start the bridge (2.6) and send it "hello". It does not answer,
     and its log shows `ignored a message from <ACI>` in `~/Library/Logs/local-assistant/bridge.log`. Copy that UUID into
     the list, then restart.
3. `name`: her name, `"Estia"` by default. Every home names its own (ours is `"Tiffany"`): 1 to 20 letters, spaces or
   hyphens. She answers to it, greets you with it on calls, and your Signal shows it. `scripts/setup.sh` asks for it
   (Enter keeps Estia; it suggests three female and three male names, or you type any name) and writes the English
   voice that goes with it into the `[voice]` lines: `af_heart`, or `am_michael` for a male name (a name you type asks
   "Female or male voice?"). The other ten languages keep Supertonic's voice F1 for now.
   `avatar`: her Signal profile picture, a PNG or JPEG of at most 5 MB (a full path, `~/` allowed). Left out, it is
   Estia's e. (`docs/brand/estia-avatar.png`); `avatar = ""` sends none. Keep your own picture outside the repo folder;
   the move into her own user copies it into her config folder (2.7, step 5).
   The file is checked when the bridge starts, not when the config is read: one that is missing, unreadable, too large
   or not a PNG or JPEG never stops the bridge. It sends nothing, Signal keeps the picture it has, and the log and
   `scripts/doctor.sh` (a warning) say why.
   `model`: leave it out, to use the `local-worker` alias from 2.2 (or set a tag you created the same way; a cloud
   model is refused, 2.2).
4. `timezone`: left unset, it is the Mac's own time zone; set it (for example `"Europe/Amsterdam"`) to override.
   `language`: the one language she listens and speaks in at the start, `"en"` or one of `es`, `it`, `ko`, `pt`, `nl`,
   `pl`, `de`, `ja`, `ru`, `fr` (README, Languages). A typed `/language` in Signal changes it later and wins over this line.
5. `signal_cli`: `"signal-cli"` finds the Homebrew binary on the PATH, which the launchd service also uses. Give a full
   path if you installed it elsewhere.
6. Everything else can stay commented out until you turn that part on (section 3). `[projects]` is an extra too, off
   by default: setup asks whether she should see projects on this Mac (most people: no), then the folder and the level
   (section 3, Projects). One exception: reminders are on without a `[reminders]`
   section (section 4, "Reminders and wake-up calls"): at most 50 open and 20 new a day (`max_open`, `max_per_day`);
   `enabled = false` turns them off, and then her rules, tools and commands are exactly as without them. The messages she starts herself are
   opt-in (section 4, "Messages she starts herself"): off until `[initiative] enabled = true` or until you type
   `/initiative on` in Signal; then at most one a day, never between 22:00 and 08:00, each of the four kinds on or off.
   Off, nothing changes.

`scripts/doctor.sh` (2.6) checks the file. It reports the placeholder number and account ID as ✗ until you replace them.

### 2.5 Personality (optional)

Without a `soul.md` she has her built-in style: warm, curious and playful (section 3, "Personality"). To give her yours:

```sh
cp soul.example.md ~/.config/local-assistant/soul.md
chmod 600 ~/.config/local-assistant/soul.md
```

Then make it yours. Once she runs in her own user (2.7), make it as her, private from the start, then edit it as her:

```sh
/usr/bin/sudo -u _estia install -m 600 /usr/local/estia/current/soul.example.md /var/estia/.config/local-assistant/soul.md
/usr/bin/sudo -u _estia -e /var/estia/.config/local-assistant/soul.md
```

### 2.6 Check, then try it in the foreground

```sh
cd <repo> && scripts/doctor.sh
```

`scripts/doctor.sh` is read-only: it installs, starts and sends nothing. Each line starts with ✓ (fine), ○ (set up, not
tested yet), ! (worth a look) or ✗ (fix this first; the line under it gives the command). It exits with status 1 when a
line is ✗. Before the first start, expect ✗ for the config placeholders (2.4) and ! for `Always on` (2.7).

For an optional part (section 3), a file in place is not enough for ✓. Once its setup is in place, the line shows what
the bridge's last real use of the part did: ✓ "working, last read 14:05", ○ "set up, not tested yet" (not used since it
was added), or ✗ "failing since 14:05: <reason>" with the next step. The bridge keeps these in `status.json` in its data
folder (times and fixed reasons only); doctor only reads that file. A fix that saves a sign-in, key, login, secret
address or helper (the scripts doctor names) turns the line ○ "set up again, not tested yet" until the bridge next uses
the part (still failing, it is ✗ again). After any other fix (a device back on, a service running again), and to get
✓, use the part once (ask Estia something that needs it).

```sh
cd <repo> && scripts/run.sh
```

The bridge runs in this terminal and logs to the terminal and to `~/Library/Logs/local-assistant/bridge.log`. The line
`starting: model …, 1 allowed account(s)` means it has started. On its first start, with nothing in its database yet,
she sends you a welcome message: the parts that are set up, each with something to try, and what stays on the Mac. It is
sent once to each allowed account (a failed send is tried again at the next start) and never to an install that already
had messages. Reply to it, or send a message from your phone. You get a read receipt and "typing…" first, then an answer
within about 30 seconds (the first message also loads the model). Ctrl-C stops it. In another terminal on the Mac,
`scripts/check.sh` runs the health check (`/usr/bin/sudo /usr/local/estia/bin/estia check` once she runs in her own user, 2.7).

No answer, and the log says `ignored a message from <uuid>`? That UUID is the sender's ACI: put it in `allowed_acis`
(2.4), restart, and try again.

### 2.7 Always on, in her own user

Stop the foreground bridge first (Ctrl-C in its terminal): the scripts refuse to run while it is up. Look at every step
first, then move her. On SSH, run the move inside `tmux` (or herdr): it keeps going when the connection drops, and a
dropped connection or a Ctrl-C brings her old setup back.

```sh
cd <repo> && scripts/migrate-to-own-user.sh --dry-run     # every step and command; nothing changes, no password
cd <repo> && scripts/install-daemon.sh                    # the move (asks for your password once)
/usr/bin/sudo /usr/local/estia/bin/estia doctor           # the check, as her
```

`scripts/install-daemon.sh` runs `scripts/migrate-to-own-user.sh` the first time (and `/usr/bin/sudo /usr/local/estia/bin/estia-deploy`
once she has her own user). The move:

1. checks before anything stops: a config with her number in your home, a Signal account for that number, the voice
   service's build in this checkout when her voice service runs (`cd tools/voice && swift build -c release`), and free
   disk space. A failed check changes nothing;
2. builds the first release while she keeps running: `/usr/local/estia/releases/<UTC time>-<commit>`, owned by root
   (0755), with its own Python, the venvs (with bytecode), the voice service and the programs she runs in `vendor/`: the
   JVM signal-cli and the call helper (from `<repo>/vendor`), and Homebrew's signal-cli and, with the JVM build, a JDK,
   copied, so nothing she runs stays in a folder you can change. `current` points at it, and the tools root runs
   (`/usr/local/estia/tools`, `bin/estia`, `bin/estia-deploy`) are installed from it. A failed build changes nothing;
3. stops both services (she is offline for a minute or two from here);
4. creates `_estia`: a hidden role account (`sysadminctl -addUser _estia -roleAccount`; her group gets a free number in
   450-499, and the account's own number is the one sysadminctl picks, since it ignores `-UID`) with its own group, no
   shell, hidden from the login window, home `/var/estia` (0700). macOS keeps `/var/empty` as the home in
   her account record (root cannot change it), so her services are given `/var/estia` explicitly: `HOME` and
   `CFFIXED_USER_HOME` in the service files, `-Duser.home` for Java, and signal-cli's `--config`. Its password is `*`
   with every other way to log in deleted (`AuthenticationAuthority`, `ShadowHashData`): no password works. A second
   run finishes what a first one left;
5. copies her config, data, Signal account, logs and the voice service's caches (its Neural Engine cache and FluidAudio's
   speech models) into `/var/estia`: read by you (`tar` as your user, so root never follows a link of yours) and
   written by her (`tar` as `_estia`), then folders 0700 and files 0600. In her copy of the config, a program you set
   (`signal_cli`, `[calls] signal_cli`, `java_home`, `tunnel`) outside `/usr/local/estia` is taken out when her release
   has its own copy (a JVM signal-cli of another version is pointed at the same build in the release); with
   `[projects]`, its `root` becomes your projects folder by its absolute path (`~/dev` becomes `/Users/<you>/dev`),
   shown on the screen, and without it nothing is added;
   her picture (`avatar`), when it is a file in your home of at most 5 MB (not a link, no link on the way, no `..` in
   the path), is read as you (within 30 s) and written as her into her config folder (`avatar.png`, `.jpg` or `.jpeg`
   by the name's ending, else `avatar` without one; 0600), and her config points at the copy; otherwise her config
   keeps your path, and a path in your home gets a note naming the key. In `[health] watch_logs`, the folders in your home are left out of
   her copy (she cannot read your home), and the screen counts them;
   every other path into a home folder, also inside a list, is named on the screen (keys only), because `~` now means
   `/var/estia`;
6. moves your old folders with her keys (config, data, Signal, logs) out of your home into
   `/usr/local/estia/rollback/data` (0700, root only), by rename: nothing in them is changed or re-owned, and no program
   you run reaches them. The caches stay where they are;
7. installs both service files from the root-run tools' templates, each checked (`UserName _estia`, programs in
   `/usr/local/estia/current`, a PATH of root-owned folders only, logs in `/var/estia/Library/Logs/local-assistant`,
   umask 077, no other keys), and removes the old log rotation rule (her logs now stay small by themselves);
8. starts her and checks within 120 s: the bridge keeps running, signal-cli runs as `_estia`, and the bridge wrote its sign
   of life after the start. On a failure her old setup comes back: the old service files, your folders (with what she saved meanwhile, when the
   new bridge ran), the old services. On a new install (no old services) it says that she does not run yet, and
   nothing of yours is held: her only copy is in `/var/estia`. Once it succeeded (never on a failure, in a dry run or
   in a rehearsal, and only while `current` names a folder in `releases/`), the releases she does not run (from earlier
   tries) are deleted: "Deleted 2 releases she does not run (1.4 GB freed)." The earlier copies of hers kept aside in
   her home (`/var/estia/.before-<time>`) are listed, so no path is typed by hand.
   `/usr/bin/sudo /usr/local/estia/bin/estia remove-old` deletes them together with her old folders held for the
   rollback: no rollback after it, so run it once she works. Last, with voice, her voice is read (the Neural Engine
   compiles it again for her user): "voice: ready"; while it still loads, with `[calls]` in her config "Calls work once
   her voice is ready (up to about 15 minutes); she texts you then." after up to 150 s of checking (the bridge texts
   only when her voice still loads 2 minutes after it started), and without `[calls]` "voice: loading (up to about 15
   minutes)" at once. A Ctrl-C during that wait ends only the wait;
9. with `[projects]` in your config, shares that folder with her, read-only, as you and without admin rights
   (`scripts/share-projects.sh`, below); without it, no folder of yours is shared.

Then check her live: `/usr/bin/sudo /usr/local/estia/bin/estia doctor` (no ✗; Own user, Code, Services, Her programs and,
with Projects on, Projects ✓),
`/usr/bin/sudo /usr/local/estia/bin/estia check`, `/usr/bin/sudo /usr/local/estia/bin/estia logs -n 20` (her start, no errors), a message
from your phone (an answer), with voice on `curl -s http://127.0.0.1:8150/health` (`stt` and `tts` ready; up to about
15 minutes after the move), with calls a test call once her text "My voice is ready: you can call me now." came, and
one `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` (a second release, `current` switched, the check passed). From the move on her files are hers alone; a copy a program of yours made before it still works: if
one may have, follow SECURITY.md, "If the Mac is compromised".

**Your projects folder.** With Projects on, after the move her user reads one folder of yours: the projects folder
(`[projects] root`, the one named in setup), read-only, also what is made there later. Without `[projects]` she reads no
folder of yours. Nothing else in your home: there she only passes through to
that folder, by name, and never lists or reads anything. The move shares it at its end; run it again, or take it back,
any time (as you, no admin rights):

```sh
cd <repo> && scripts/share-projects.sh --dry-run     # every chmod command; nothing changes
cd <repo> && scripts/share-projects.sh               # her read-only access (default: the folder in her config)
cd <repo> && scripts/share-projects.sh --revoke      # taken back: your home closes first
```

The share adds access entries (ACLs) for `_estia`: `allow search` on your home (and on each folder on the way to the projects
folder), and `allow list,search,readattr,readextattr,readsecurity,read` with `file_inherit,directory_inherit` on the
folder and on every folder and file of yours in it. A symbolic link is never followed: macOS's `chmod -R +a` sets the
entry on the target of each link it meets, so the share walks the folder itself and changes each item with
`chmod -h +a`. An entry already there is never added twice. A folder outside your home, a link, or your home itself is
refused. A repository moved in from elsewhere keeps its own permissions: her doctor's Projects line counts it and names
the share command, and the share run again includes it. Her git trusts your repositories with `-c safe.directory=*` on its own command line, after
the check of each repository's git config, and `git status` leaves submodules alone.

Accepted risk: every file in the projects folder is readable by her user, also files git does not track, such as
`.env.local`; her project tools open only tracked files at the level you chose, and she has no shell.

Safe to run again: a finished move changes nothing. To try the whole move on a copy first (her data copied into the
folder you name, no user created, nothing started, your folders untouched), then delete the copy, which holds her keys:

```sh
ESTIA_ROOT=/tmp/estia-rehearsal scripts/migrate-to-own-user.sh --rehearse && rm -rf /tmp/estia-rehearsal
```

Within 7 days, `/usr/bin/sudo /usr/local/estia/bin/estia rollback` (or `scripts/migrate-to-own-user.sh --rollback`, which runs it)
goes back: your old folders return by rename, what she saved since the move (messages, Signal sessions, keys) is copied
on top of them as you, and her services run as you again. Her config stays the one from before the move: changes made to
hers since stay in `/var/estia/.config/local-assistant/config.toml`. The rollback runs from the tools owned by root, never
from your checkout. After the 7 days, `/usr/bin/sudo /usr/local/estia/bin/estia remove-old` deletes the held folders (no rollback
after that). A move after a rollback or a failed try keeps her earlier copy aside in `/var/estia/.before-<time>`
and never deletes it itself: `/usr/bin/sudo /usr/local/estia/bin/estia remove-old` deletes those copies too, also when no
move is on record. It takes only folders of her home named `.before-YYYYMMDD-HHMMSS` (a link is never one, and nothing
is followed) and names each one it deletes.

**Updates** come from your checkout, which is a source and never runs:

```sh
cd <repo> && git pull
/usr/bin/sudo /usr/local/estia/bin/estia-deploy           # or: /usr/bin/sudo /usr/local/estia/bin/estia-deploy <repo>
```

`estia-deploy` first shows the commits since the live one and the files they change, and asks: read the list, it runs
as her, with her data. Root (the Mac's all-powerful admin account) runs only its own tools in `/usr/local/estia/tools` (their own Python, the deploy code,
the service templates), never a release or your checkout. The build runs as you, at the release's final path (its own
Python from `uv python install`, never uv's copy in your home; `uv sync --frozen` with bytecode; the voice service from
`tools/voice/.build/release`; her programs in `vendor/` from `<repo>/vendor`, a `vendor/` folder next to the checkout,
Homebrew or the current release). Root then copies the build into fresh files it owns (hard links, links out of the
release, also through a chain of links, links to nothing and set-id bits stop it), renders both service files from the
tools' templates and checks them, switches `current` in one rename, reloads a service file that changed and restarts
the bridge (the voice service only when its program changed, or with `--voice`), and checks within 120 s that a new
bridge process runs; on a failure `current` goes back to the release before. The last 3 releases are kept; `--dry-run`
shows every step, `--voice` also installs the voice service. When the voice service's program changed (or with
`--voice`), the deploy ends with "voice: ready"; while it still loads, with `[calls]` in her config (read as her, by
her release's Python) "Calls work once her voice is ready (up to about 15 minutes); she texts you then." after up to
150 s of checking (the bridge texts only when her voice still loads 2 minutes after it started), and without
`[calls]` "voice: loading (up to about 15 minutes)" at once. A Ctrl-C during that wait ends only the wait. It prints
only what happened and when, never message text.

When a release changes a root-run tool (`scripts/estia`, `scripts/estia-deploy`, `deploy.py`, `ownuser.py`,
`hostsec.py`, a service template), the deploy says so and keeps the installed one. Read its diff and take it with
`/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools` (it shows the diff and asks).

**Her config and keys** are hers: edit the config with sudoedit, which gives your editor a copy and writes it back as her,
then restart her:

```sh
/usr/bin/sudo -u _estia -e /var/estia/.config/local-assistant/config.toml
/usr/bin/sudo /usr/local/estia/bin/estia restart
```

The key and sign-in scripts work as before: once she runs as `_estia`, `scripts/set-web-keys.sh`,
`scripts/set-calendar-url.sh` and `scripts/set-tapo-credentials.sh` pass each value through a pipe into
`/usr/bin/sudo /usr/local/estia/bin/estia secret set <file> <KEY>` (a hidden prompt when you run it yourself; only `web.env`,
`calendar.env` and `tapo.env` and their keys; one key replaced, 0600; lengths only on the screen; a value typed on the
command line is refused, but sudo's log has kept it then: change that key), and `scripts/mail-login.sh`,
`scripts/spotify-login.sh` and `scripts/tapo-discover.sh` run as her through `/usr/bin/sudo /usr/local/estia/bin/estia`.
`scripts/doctor.sh`, `scripts/check.sh` and `scripts/run.sh` say which command to use instead. Whether she runs as her
own user is read from the bridge's service file, so after a rollback the scripts work for you again.

Her signal-cli by hand (one signal-cli per account): `/usr/bin/sudo /usr/local/estia/bin/estia signal listIdentities`,
`… signal trust -v <safety number> <ACI>`, `… signal listDevices` or `… signal removeDevice -d N` stops the bridge, runs
her own signal-cli as her from `/` with her flags, and starts her again. `/usr/bin/sudo /usr/local/estia/bin/estia unlock` ends a
`/lock` from the Mac (when the device that locked her is gone).

Her own user does not cover everything: Ollama and AgentJev run as you and see what she sends them, the move and each
deploy trust your checkout and Homebrew at that moment, and with Projects on her user reads every file in your projects folder, also
those git does not track (above). SECURITY.md lists these gaps.

### 2.8 Keep it running

A Mac that sleeps, or stays off after a power cut, stops Estia too. Three settings matter:

- **No sleep, and start after a power failure.** In System Settings → Energy, turn on "Prevent automatic sleeping when the
  display is off" and "Start up automatically after a power failure". On the command line, `/usr/bin/sudo pmset -a sleep 0 autorestart 1`
  does the same. It needs `sudo` because power settings are system-wide, and only root may change them.
- **Ollama starts at login.** `brew services start ollama` runs Ollama as a login item of your user, so it starts only
  after you log in. The bridge starts at boot without you, but it cannot answer without Ollama: every message gets "Estia
  isn't available right now". Either turn on automatic login for your user (System Settings → Users & Groups; not available
  with FileVault on), or log in once after each restart. The trade-off: automatic login lets anyone at the Mac into your
  account after a restart (the security step shows it as !), and without it she answers "isn't available" after every
  restart until someone logs in.
- **FileVault.** With FileVault on, someone has to type the password after each restart, and automatic login is not
  possible. For a planned restart, `/usr/bin/sudo fdesetup authrestart` restarts the Mac without that prompt; it asks for your password.

### 2.9 Add the other parts

Section 3 covers each optional part: calendar, mail, lights and plugs, voice, calls, Spotify, web search, places and
routes, the extra health checks, and projects. Add one at a time, and check each one with `/usr/bin/sudo /usr/local/estia/bin/estia doctor`
before you go on.

### 2.10 Security step

`scripts/setup.sh` ends with it; `scripts/harden.sh` runs it again any time (`scripts/harden.sh --dry-run` shows every
check and command and changes nothing). The order that worked on a Mac reached over SSH: the move's dry run, then the
move; with calls set up, her text "My voice is ready: you can call me now." (up to about 15 minutes), else
`curl -s http://127.0.0.1:8150/health` showing `stt` and `tts` ready, then a text and a call; then `scripts/harden.sh`
in your own Terminal with a second SSH session open; then a new SSH login from your laptop and one more call (after
`--nuclear`, also the programs on the Mac you open from your laptop: the firewall). Each setting is shown with its state and one line on why; a
change is made only on your yes, and `/usr/bin/sudo` asks for your Mac password once, before the first change.

| Setting | On your yes |
|---|---|
| SSH with passwords | keys only, when Remote Login is on and your `~/.ssh/authorized_keys` holds a key: a drop-in `/etc/ssh/sshd_config.d/000-estia-keys-only.conf` (also `DenyUsers _estia`), checked with `sshd -t` and taken back if sshd refuses it. This session stays open: test a new login from your laptop before you close it |
| Her files | folders 0700, files 0600: readable only by her (in her own user: `chmod` run as her, from code's own command; her helper only reports) |
| Rules that let admin commands skip your password (`NOPASSWD` in `/etc/sudoers.d`) | removed only while `/usr/bin/sudo` also works with your password (`/usr/bin/sudo -l` shows a rule without NOPASSWD); `/usr/bin/sudo -k; /usr/bin/sudo -v` checks it afterwards |
| Registration Lock for her number | set in a maintenance window: she stops, the PIN (typed hidden twice) goes to signal-cli on its input as `_estia`, she starts again (also when the PIN step fails). Not offered before her number is registered |

Shown with the fix, never applied: FileVault, automatic login, the screen lock, automatic security responses, other
admin accounts, and a Tailscale operator or Funnel. The firewall, stealth mode, python3 and ruby on the firewall's list
and your keychain's lock are one neutral line (○) naming the nuclear option, below: never a "!" or a "✗". Doctor shows
SSH passwords, FileVault, her file modes and her Registration Lock too; its Registration Lock ✓ line ends "Keep the PIN
in your password manager." Its firewall line is ✓ when the firewall is on (it says whether stealth mode is on as well,
never as a warning), and the same neutral line when the firewall is off or cannot be read.

**The nuclear option.** `scripts/harden.sh --nuclear` adds the strictest settings, and is never part of setup. Before
anything, it says plainly that they may stop other services on this Mac:

- programs that other machines reach (a file share, a media server, a page you open from your laptop) stop answering
  them unless you allow each one; the ones listening now are asked about;
- with stealth mode, the Mac no longer answers ping, the usual "are you there?" check;
- with the keychain lock, macOS asks for your password more often (keychain prompts).

Each one is asked first and changed only on your yes (every question defaults to no). Nothing here turns a setting off
again: a Mac keeps what it already has, also one made stricter by an earlier version of this step.

| Setting | On your yes |
|---|---|
| Firewall | turned on (`socketfilterfw --setglobalstate on`). The question first names your programs that listen for other machines now (from `lsof`, such as a page of yours served on port 8170), and says that doctor names any new one later: on a Mac without a screen nobody can click "Allow", so an unsigned one stops answering them. Each can be kept on a yes (`socketfilterfw --add` and `--unblockapp`). Check from your laptop afterwards |
| Stealth mode | its own question: on, the Mac no longer answers ping or probes of closed ports (`socketfilterfw --setstealthmode on`) |
| python3 and ruby (programs that run scripts) allowed to take incoming connections | taken off the firewall's list, so, with the firewall on, a script run with them no longer takes connections from other machines (a program of yours that others reach may then need its own entry) |
| Your login keychain | locks after 15 minutes and at sleep (no admin rights needed; macOS then asks for your password more often) |

With the firewall on, doctor adds a "!" line (a warning, never ✗) for each program that listens for other machines
and that the firewall does not let through: not allowed on its list (`socketfilterfw --listapps`), and ad-hoc signed
or unsigned, so "allow signed software" does not let it through either. One blocked on the list is named whatever its
signature; with "allow signed software" off, a signed one not on the list is named too. The list's paths and the
program's are compared with links resolved (an entry added as `/opt/homebrew/bin/node` covers the program it points
to). For example `! Firewall: node on port 8170 cannot be reached from other machines`, then "if you started it and
other machines should reach it:" and the two commands that let it through, each with the program's full path:

```sh
/usr/bin/sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add <program's full path>
/usr/bin/sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp <program's full path>
```

A program in a temporary folder (`/tmp`, `/var/folders`) gets no command: stop it if you did not start it. With
"block all incoming connections" on, one line names every listener ("! Firewall: block all incoming connections is
on: node on port 8170 cannot be reached from other machines") with `--setblockall off`, and no per-program commands.
Names and paths are printed without control characters, so a process name cannot fake a doctor line.

Doctor only reads (`socketfilterfw`, `netstat`, `lsof`, `codesign`, `ps` and proc_pidpath, with fixed arguments and no
shell); nothing it does changes the firewall. A program whose signature cannot be read gets no line; one whose program
cannot be found gets "may not be reachable" with the command that names it,
`/usr/bin/sudo /usr/sbin/lsof -nP -iTCP:<port> -sTCP:LISTEN`, then the two commands. After a Homebrew update a
program's path changes (a new version folder): run doctor again and let the new path through. Then check from your
laptop. Known gaps: Untested as her on the live Mac until `/usr/bin/sudo /usr/local/estia/bin/estia doctor` shows the
line (docs/e2e-checklist.md, 122). As her, `lsof` lists only her own processes, so your programs come from `netstat`
alone; in one sandbox `netstat` listed no internet sockets, and then your programs are not named.

The phone checklist comes last:

- Signal → Settings → Linked devices: only devices you know.
- Signal → Settings → Account → Registration Lock: on, for your own number too.
- Signal → Settings → Privacy → Screen lock: on, 1 minute or less.
- Her chat → her name → View safety number: compare it as in 7 ("Safety number changed"), then Mark as verified.
- The device with her SIM: a SIM PIN (not 0000), a screen lock, notifications that hide their content; her carrier: a
  port-out or SIM-swap PIN; a prepaid number: a reminder to top it up before it lapses.

What stays a risk: an unlocked phone or laptop with your Signal can talk to her (`/lock` stops her from the phone), and
root on the Mac reads everything. Without the firewall (the nuclear option), a program of yours that listens for other
machines can be reached from your network.

## 3. Optional parts

Each part is off until its section is in the config (hers: `/usr/bin/sudo -u _estia -e /var/estia/.config/local-assistant/config.toml`,
2.7). After each config change, restart the bridge (`/usr/bin/sudo launchctl kickstart -k system/com.local-assistant.bridge` when it
runs as a daemon; in the foreground, Ctrl-C and `scripts/run.sh` again). Then run `scripts/doctor.sh`: the part's line must be ○ (set up, not tested yet) or ✓ (its last
real use worked), never ✗.

### Calendar (once)

Estia reads one or more calendars, read-only. Each calendar is either:

- a **Google calendar**, read through its address in iCal format (the secret one, or the public one of a calendar that is
  public anyway), or
- the **Outlook calendar** of a Microsoft 365 mailbox Estia already reads (see "Mail (once)"), read through Microsoft
  Graph with the delegated permission `Calendars.Read` only: she can read events, never create, change or delete them.

With one Google calendar you need nothing more than `[calendar]` and `scripts/set-calendar-url.sh` (steps 1 to 3). For
several calendars, list them under `[calendar]` with a short name each (step 4). With more than one calendar, the
digest and her answers put the calendar's name before each event ("Family: Football", "Work: 1:1 with Sam"), and the same
invitation in two calendars is shown once.

**A Google calendar.** The secret address is a password: whoever has it can read the calendar. A public address works
too, but a public calendar is visible to anyone who finds it: never make a private calendar public for Estia; use its
secret address.

1. In Google Calendar on a computer, signed in as the calendar's owner: Settings → under "Settings for my calendars",
   choose the calendar → **Integrate calendar** → **Secret address in iCal format** (or, for a calendar that is public
   anyway, **Public address in iCal format**) → copy it.
2. On the Mac: `cd <repo> && scripts/set-calendar-url.sh`, and paste at the hidden prompt. For a named calendar from
   step 4, give its name: `scripts/set-calendar-url.sh family`. Each address is saved under its own key in
   `~/.config/local-assistant/calendar.env` (mode 0600; `CALENDAR_ICS_URL` for the first calendar,
   `CALENDAR_ICS_URL_FAMILY` for `family`). The script replaces only that calendar's key (and the time it saved it,
   for doctor) and keeps the others. Only the calendar helper (`tools/ical`) reads the file; the bridge never holds an
   address.
3. `cd <repo>/tools/ical && uv sync --frozen`, add `[calendar]` to the config, restart the bridge. `scripts/doctor.sh`
   must then show each calendar ○ or ✓.

**Several calendars.** List each one under `[calendar]`, in the order you want them. A `name` is short (lowercase
letters, digits, `-` or `_`); `label` (optional) is how it is shown, for example in your own language; `mailbox` makes it
the Outlook calendar of that `[[mail.accounts]]` mailbox (each mailbox once). The calendar named `calendar` is the one
saved without a name, so an existing setup keeps working when you add others:

```toml
[calendar]

[[calendar.sources]]
name = "calendar"        # the address saved by scripts/set-calendar-url.sh with no name
label = "Family"

[[calendar.sources]]
name = "school"          # scripts/set-calendar-url.sh school
label = "School"

[[calendar.sources]]
name = "work"
mailbox = "work"         # the Outlook calendar of the mailbox named work in [[mail.accounts]]
label = "Work"
```

Up to 8 calendars. Each one is read on its own and at the same time: a calendar that cannot be read is named in the
digest with the reason and the fix, and the others are shown as usual. `scripts/doctor.sh` and `/status` show one line
per calendar.

**An Outlook calendar (Microsoft 365).** It uses the mailbox's sign-in, with one more permission:

1. In the Microsoft Entra admin center, open the app registration from "Mail (once)" → **API permissions** → **Add a
   permission** → Microsoft Graph → Delegated → `Calendars.Read` → **Add permissions**. Then **Grant admin consent** for
   the organisation (an administrator of the tenant can do that; it is the simplest way for your own app). After that the
   mailbox's existing sign-in can read the calendar too, with no new sign-in.
2. Add the `[[calendar.sources]]` entry with `mailbox`, and restart the bridge.
3. If no administrator granted consent: sign in the mailbox again, `scripts/mail-login.sh <address>`. With the calendar
   in the config, the sign-in asks for `Calendars.Read` as well, and says so first. If your organisation needs an
   administrator's approval, the browser says "Need admin approval" and that sign-in stops: sign in mail alone with
   `scripts/mail-login.sh <address> --mail-only` and ask the administrator to grant consent as in step 1.

Mail never depends on the calendar: the mail sign-in asks only for its own permissions, so mail keeps working whatever
happens to `Calendars.Read`. Until consent is granted, the calendar says "Calendars.Read is not granted yet" in the digest,
`/status` and `scripts/doctor.sh`. Only the mailbox's own default calendar is read (Graph's `/me/calendarView`): other
calendars in that mailbox, and calendars other people share with you, are not read (those would need
`Calendars.Read.Shared`, which Estia does not ask for). Personal Outlook.com accounts are not supported, as for mail.

Don't reset a Google secret address unless it leaked. A reset stops that calendar until the new address is saved with
`scripts/set-calendar-url.sh` (with the calendar's name, if it has one); the bridge then says "the secret address no
longer works" (for a named calendar: "the iCal address no longer works").

Only Google calendars and the Outlook calendars of your mailboxes are supported (the helper accepts Google's iCal host
only).

### Mail (once)

Mail is read through Microsoft Graph with delegated, read-only permission. It can read mail, never send, move, delete or
mark it. Work and school accounts only: a Microsoft 365 mailbox. Personal Outlook.com accounts are not supported.

1. In the Microsoft Entra admin center, register an app:
   - **Supported account types**: "Accounts in this organizational directory only".
   - Under **Authentication**, turn on **Allow public client flows**. Sign-in uses the device-code flow, so the app needs
     no client secret.
   - Under **API permissions**, add the delegated Microsoft Graph permissions `Mail.Read`, `User.Read` and `offline_access`
     (and `Calendars.Read` if you will add a mailbox's Outlook calendar; see "Calendar (once)"). An administrator of the
     tenant may need to grant consent: **Grant admin consent** covers every permission listed there.
   - On the app's **Overview**, copy **Directory (tenant) ID** and **Application (client) ID**. They are identifiers, not
     secrets.
2. In the config, put them in `tenant_id` and `client_id` under `[mail]`, and add one `[[mail.accounts]]` per mailbox, with
   a short `name` and its `address`:

   ```toml
   [mail]
   tenant_id = "<Directory (tenant) ID>"
   client_id = "<Application (client) ID>"
   reply_days = 7
   keep_days = 30

   [[mail.accounts]]
   name = "work"
   address = "you@example.com"
   ```

3. Sign in each mailbox once: `cd <repo> && scripts/mail-login.sh <address>`. Open the link it prints on any device, sign
   in as that mailbox, and wait for "signed in". `scripts/doctor.sh` then shows a line per mailbox (○ until its first
   sync, then ✓), or names the mailbox that still needs a sign-in. When the mailbox's calendar is in the config, the
   sign-in asks for `Calendars.Read` too; `scripts/mail-login.sh <address> --mail-only` signs in mail alone.

Microsoft ends a sign-in after a password or MFA reset, or after a long time unused. The mailbox then pauses and you get
one note with the command to run again. "How's the Mac?" shows each mailbox's state.

#### Mail pre-filter with AgentJev (optional)

Before Gemma reads a mail, Estia can sort out the obvious bulk mail. In plain words, the order is:

- Mail from people you have mailed, replies in your own threads, bounces, and bill, payment, security and appointment mail
  always reach Gemma.
- Mailing lists are noise by their headers.
- Newsletters and auto-replies count as noise by their headers only when AgentJev agrees.
- AgentJev alone marks a mail as noise only when it is at least `jev_threshold` sure (0.9 by default).
- One in five pre-filtered mails goes to Gemma anyway, as a check.
- Without AgentJev, or when it does not answer, the mail goes to Gemma.

The header rules (`prefilter = true`) are on by default and need no model.

AgentJev is a small local yes/no model, built on Qwen3-0.6B, from a separate project:
[github.com/malevrigns/agent-jev](https://github.com/malevrigns/agent-jev) (Apache-2.0). Estia does not include it. You
install and run it yourself, on this Mac only; mail text reaches it only inside the Mac (localhost).

1. Read its code before you install it. AgentJev is a third-party model, so check what it runs:

   ```sh
   git clone https://github.com/malevrigns/agent-jev.git ~/agent-jev
   cd ~/agent-jev
   ```

2. Install it as its README describes: a Python virtual environment, its requirements, and the weights download
   (`python fetch_v1_weights.py`, which pins the model revision).
3. Start its server for the Mac itself only (127.0.0.1), port 8147, with the command from its README. The example in that README uses port 8149; this
   guide uses 8147, so the command ends with `--port 8147`:

   ```sh
   python -m jev_service.server --checkpoint agentjev_v1.pt --model-path models/Qwen3-0.6B --temperatures temperatures.json --port 8147
   ```

   It runs until you stop it. For always-on use, run it as your own launchd service; this repository does not include one.
   You can watch it with `[health] extra_checks`, for example `{ label = "AgentJev", http = "http://127.0.0.1:8147/health" }`.
4. Point Estia at it under `[mail]`, then restart the bridge:

   ```toml
   [mail]
   jev_url = "http://127.0.0.1:8147"   # plain http, this Mac only: the port you started it on
   jev_threshold = 0.9                  # optional, 0.5 to 1
   prefilter = true                     # optional: the header rules (the default)
   ```

5. `scripts/doctor.sh` shows "Mail pre-filter (AgentJev)" ✓ when it answers. If it shows !, mail goes to Gemma until
   AgentJev answers again.

Security advice: read its code before you run it. If you change how it loads its checkpoint, load it with
`torch.load(..., weights_only=True)`. Keep the model revision pinned, as its fetch script does. Do not run its dataset
tooling; Estia does not need it.

#### Gmail (not built in)

Estia does not read Gmail. Google has no simple read-only sign-in for an app you run yourself: the device-code
sign-in Estia uses for Microsoft does not allow Gmail's permissions at Google, and Gmail's read-only permission
(`gmail.readonly`) is a restricted one that Google gives only to an app in your own Google Cloud project. These are the
ways to get Gmail to her, each with its trade-off. Only the first works today; there is no Gmail code in Estia.

1. **Forward Gmail to a mailbox she already reads (works today, no code).** In Gmail on a computer: Settings → See all
   settings → **Forwarding and POP/IMAP** → **Add a forwarding address** → a Microsoft 365 mailbox from your config →
   confirm the code Gmail sends there → "Forward a copy of incoming mail to" that address, keeping Gmail's copy (a Gmail
   filter can forward only some mail). Trade-offs: new mail only, no history; replies you send from Gmail are not seen,
   so that mail never counts as answered; forwarded mail can land in Junk on the Microsoft side (mark it "Not junk" once,
   or add a rule).
2. **Your own Google Cloud project (not built yet).** A desktop OAuth client in your own project, asking only for
   `gmail.readonly`: read-only by Google's permission, as `Mail.Read` is for Microsoft. About 15–20 minutes in the Google
   Cloud console (project, Gmail API, consent screen, client). Trade-offs: the sign-in shows an "unverified app" warning,
   and while the app is in "Testing" Google ends the sign-in every 7 days, so you would publish it ("In production",
   unverified, for your own account only).
3. **IMAP with an app password (not built, on purpose).** An app password gives full access: it can read, send as you and
   delete, and Gmail is often the recovery mailbox of your other accounts. IMAP has no read-only login: opening a folder
   read-only (`EXAMINE`) does not stop copying, adding or deleting mail, so "read-only" would rest only on Estia's code,
   not on Google's permission. Design notes exist (reviewed) in case it is ever built.
4. **Google Apps Script in your own account (untested).** A small script in your own Google account, limited to
   `gmail.readonly`, that would hand Estia the headers of new mail. Read-only by permission, about 15 minutes to set
   up, but untested.

Would you like one of these built? Open an issue first, so the design can be agreed (`CONTRIBUTING.md`).

### Lights and plugs (once)

1. Give each device a fixed address with a DHCP reservation in your router.
2. `cd <repo> && scripts/tapo-discover.sh` lists the devices on the network (read-only).
   - Older Kasa devices need no login; newer Kasa firmware and Tapo devices need the TP-Link login (Tapo is untested).
     Run `scripts/set-tapo-credentials.sh`: it saves the login to `tapo.env` (mode 0600), which only the device helper
     reads. Then run `scripts/tapo-discover.sh` again.
   - Tapo firmware with the TPAP login cannot be controlled yet.
3. `cd <repo>/tools/tapo && uv sync --frozen`. Add one `[[tapo.devices]]` entry per device, with the name as it appears in
   the app, its address, `kind` (`light` or `plug`) and `rule` (`normal`, `ask first` or `never off`). Restart the bridge.

`scripts/doctor.sh` then shows Lights and plugs ○ or ✓. After each device change, the note that follows says what really
happened, for example "Devices: living room lamp off", and says when a device does not answer.

Estia reaches devices on the home network only, never through the TP-Link cloud. Device addresses must be private
addresses (RFC 1918 or IPv6 unique local).

### Voice notes (once)

Voice notes and spoken answers use a local service: WhisperKit for speech-to-text, and Kokoro (English) or Supertonic-3
(the other ten languages) for text-to-speech, on the Neural Engine. Whisper is told the language on every request. It needs macOS 15.6 or later and the Swift 6.2 toolchain from the Command Line Tools. Homebrew's installer
(2.1) installs the Command Line Tools. If `swift --version` is missing or older than 6.2, run `xcode-select --install`.

1. Install the service: `cd <repo> && scripts/install-voice-daemon.sh`. The script builds the service with the exact
   versions in `Package.resolved`, then puts it into her release with `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --voice`,
   which installs it as a background service that starts with the Mac (a LaunchDaemon), runs as her own user `_estia`
   and is reachable only from the Mac itself (`127.0.0.1:8150`). It asks for your Mac password, because installing a
   service needs admin rights, as for the bridge (2.7). The models (about
   1.6 GB) download on the first start into `/var/estia/Library/Application Support/local-assistant/voice-models` (the move
   into her own user copies them when they are there). Whisper's files (the listening model and its word list) are
   fixed to one version on Hugging Face (one commit) and checked one by one against their fingerprints (sha256) at
   every start, in under a second; after the first download Whisper starts with no network. A folder with a changed or
   extra file (an older download's extra file too) is never loaded: it is set aside whole as `<folder>.refused-<time>`
   (only the latest such copy is kept) and that version is downloaded again into a fresh folder, with no step of yours;
   `/usr/bin/sudo /usr/local/estia/bin/estia logs voice` says so. Only when the fresh download is changed too does
   `stt` say `failed` in `curl -s http://127.0.0.1:8150/health`. The speaking voices (Kokoro for English at the first
   start, Supertonic-3 for the other ten languages on first use) are downloaded as published: no fixed version and no
   check of their files. By FluidAudio's code, they are fetched only when a file is missing.
2. The script waits up to about 20 minutes. If it says "not ready" after a slow first download, the service keeps loading.
   Check later with `curl -s http://127.0.0.1:8150/health`: `stt` and `tts` both say `ready` when it is done.
3. Take the `#` off `[voice]` and its lines in the config (the `voice` line has the voice setup chose with her name; an
   empty `[voice]` uses the defaults in `config.example.toml`, voice `af_heart`), restart the bridge, and check that
   `scripts/doctor.sh` shows Voice ○ or ✓.

Languages other than English need a voice service that knows them: its health then lists `"languages"`. A service
installed before the language feature speaks English only; the reply to `/language` says so. Run
`scripts/install-voice-daemon.sh` again (it asks for your Mac password: run it yourself), and `scripts/doctor.sh` shows Language ✓ (it
checks the language in use: the one chosen with `/language`, else the config's). The first use of such a language downloads Supertonic-3 (about
264 MB, about a minute); the bridge does this in the background at start and right after `/language`.

The first start after an install, a move into her own user or a macOS update takes up to about 15 minutes while the
Neural Engine compiles the models (its cache is per user and per macOS build). Until then, voice notes get "Voice isn't
available right now; please type it." and calls are not answered (3, Calls, "While her voice loads"). The end of
setup, of the move and of a deploy that changed the voice service says which: "voice: ready"; while it still loads,
with `[calls]` in her config "Calls work once her voice is ready (up to about 15 minutes); she texts you then." after
up to 150 s of checking (the bridge texts only when her voice still loads 2 minutes after it started), and without
`[calls]` "voice: loading (up to about 15 minutes)" at once. A Ctrl-C during that wait ends only the wait. Setup run
again in her own user cannot read her config, so it promises no text.

### Calls (once)

Voice calls with Estia over Signal. Homebrew's signal-cli cannot place calls: it is a native build that leaves out
data it needs to use Signal's relay servers (reflection data), so calls use the Java build (JVM) of the same version, plus a build of the call helper. Both go into the
vendor folder of your checkout, `<repo>/vendor` (gitignored; set `LOCAL_ASSISTANT_VENDOR` to use another folder when you
build). Building needs no admin rights; in her own user, `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` then copies the builds, and a
JDK from Homebrew's `openjdk`, into her release. Calls need `[voice]` first (above). Plan for a long first build: it downloads
several GB of sources and Cargo crates from GitHub and crates.io, and compiles them. See `tools/call-tunnel/README.md`.

1. Tools:

   ```sh
   brew install openjdk gnupg gh rustup cmake protobuf coreutils
   gh auth login
   "$(brew --prefix rustup)/bin/rustup" toolchain install 1.91.1 --profile minimal
   ```

   The JVM build needs Java 25 or newer (Homebrew's `openjdk`). The installer in step 2 downloads the signal-cli release
   through `gh`, so `gh` must be signed in.
2. The JVM signal-cli, with its signature checked. Use the version Homebrew installed, so that both builds share the same
   account data, and pin Homebrew's version so that it stays that way:

   ```sh
   cd <repo>
   v=$(brew list --versions signal-cli | awk '{print $2}')
   tools/call-tunnel/install-signal-cli-jvm.sh "$v"
   brew pin signal-cli
   ```

   The installer ends with an `installed:` line that gives the path of the JVM signal-cli. If `$v` is not 0.14.9, put that
   path in `[calls] signal_cli` (step 6); the default is the 0.14.9 build.
3. The call helper: `cd <repo> && tools/call-tunnel/build.sh` (pinned upstream sources plus `local.patch`). It ends with the
   path of the built binary.
4. Java. In her own user, skip this step: her release holds its own copy of the JDK (`vendor/openjdk.jdk`), and that is
   the default. Before her own user: Homebrew keeps its `openjdk` out of the usual places (keg-only), so macOS's
   `/usr/libexec/java_home` does not find it, and the bridge needs the path set. Print the path, then put it in the config:

   ```sh
   echo "$(brew --prefix openjdk)/libexec/openjdk.jdk/Contents/Home"
   ```

   ```toml
   [calls]
   java_home = "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"   # the path printed above, written out in full
   ```

   Config values are not shell-expanded, so write the path out in full, as shown.
5. Check the pieces before you turn calls on. Use the `installed:` path from step 2 for `<signal-cli path>`, and the
   `build.sh` output for `<tunnel path>`:

   ```sh
   JAVA_HOME="/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home" "<signal-cli path>" --version
   ls -l "<tunnel path>"
   ```

   The JVM launcher passes arguments on to signal-cli, so it cannot take `-D` options from the command line. Run it by hand
   with the flag in `JAVA_OPTS` instead: `JAVA_OPTS=-Djava.net.preferIPv4Stack=true "<signal-cli path>" -a <bot-number> <command>`.
6. Add `[calls]` to the config (an empty table uses the defaults; set the paths only where they differ). In her own user:
   `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` (it copies the builds into her release), then
   `/usr/bin/sudo -u _estia -e /var/estia/.config/local-assistant/config.toml` for `[calls]`, then
   `/usr/bin/sudo /usr/local/estia/bin/estia restart`; `/usr/bin/sudo /usr/local/estia/bin/estia doctor` must show Calls ✓ and Her programs
   ✓, and `/usr/bin/sudo /usr/local/estia/bin/estia check` must show `signal-cli: running` before you place a test call. Before her
   own user: restart the bridge; `scripts/doctor.sh` must show Calls ✓, and `scripts/check.sh` must show
   `signal-cli: running`.

With `[calls]` on, the bridge uses only this JVM build. If it cannot start, texts stop too. Remove `[calls]` and restart to
go back to Homebrew's signal-cli.

The `[calls]` paths default to the vendor folder above. Change them with `signal_cli`, `java_home`, `tunnel` and
`socket_dir`. The call sockets go to `calls` in the data folder, or to `~/.la-calls` when a long home folder makes that
path too long for Unix sockets. A `socket_dir` you set must be short (under about 65 characters).

Undo: remove `[calls]` and restart. The bridge then uses Homebrew's signal-cli again, as before.

**While her voice loads.** A call needs both speech parts ready (`stt` and `tts` in `/health`). Until then a call
from an allowed account rings out, never rejected, and a note goes to the caller: "I can't take calls yet: my voice
is still loading (up to 15 minutes after an install, a move or a macOS update). I'll text you when it's ready." That
note goes at most once in 10 minutes, so a second call soon after gets none. Once her voice is ready, the code sends
the note "My voice is ready: you can call me now." (never in the model's history):

- to a caller whose call rang meanwhile: at once, also in quiet hours;
- to every allowed account when a read 2 minutes or more after the bridge started still found her voice not ready (a
  first install, a move, a macOS update; a voice ready all along, or ready sooner, is no long load): held until the
  end of quiet hours (`[initiative] quiet_hours`, 22:00-08:00 by default and when it is empty), never waking anyone.

At most once per bridge start for each account, except that every later "can't take calls yet" note (its "I'll text
you") is followed by one more: when the voice service stops and loads again later in the same run, a caller who gets
that note gets a second ready note, and one whose ring came within the 10 minutes (no note) gets none. While `/lock` is
on, it waits for `/unlock`, also when the lock comes while a note is about to go. A call that gets through (answered
and connected, or placed and picked up; not one that only rang) counts as telling that account. The voice is read
again right before a held note goes out, so it never says ready while it is not; it is read every 15 s from the
bridge's start until it is ready, and after a missed call until it is ready, never otherwise. The log says
`voice ready: note sent (a call rang meanwhile)` or `(after a long load)`, never an account. Without `[calls]` no note
is sent.

**What she knows at the start of a call.** With `[calendar]` or `[mail]` on, an ordinary call starts with the code's
numbers for today, last in her input for the whole call: the events still to come (declined ones left
out), the one going on and until when, the next one's start, end and how soon, when the last one ends, the all-day
items, and the mail digest's counts (to answer, how many urgent, actions). For example
`Calendar today: 3 events left, next 14:00–15:00 (in 50 min), last ends 21:30.` and
`Mail digest: 2 to answer (1 urgent), 1 action.` Never a title, place, calendar name, sender or subject: that text
would make the whole call outside text (reminders refused, no `remember`, devices waiting for a yes). She may mention a
number and offer the rundown once. A plain yes right after her offer ("yes", "yes please", "okay"; in the call's
language or English, and nothing else) makes the code read today's calendar and the mail digest for her
(`calendar_agenda` and `mail_digest`, as if she had called them; once a call); when you ask in other words, she calls
them herself. That answer counts as outside text, as in a text. The mail counts are read from the local database
as the call starts, so the first answer always has them. The calendar is read live at the same moment (nothing kept);
the first answer waits for it at most 3 seconds more, and a slower calendar joins from the next answer. A calendar that
cannot be read, for any reason, gives `Calendar today: not read just now.`, and the mail counts stay. The numbers are
the code's English; she speaks the call's language. A practice call (`/babble`) gets none.

**What stops her when you talk over her.** `barge` in `[calls]` chooses the rule:

- `barge = "loudness"` (the default): after the first 300 ms of each sentence (the line's floor), 12 loud frames in 250 ms
  stop her (a frame is loud above 3.5 times the floor, and at least 400). Road noise in a car can stop her too.
- `barge = "speech"`: the voice service's speech detector (Silero VAD through FluidAudio, on the Mac) rates every 256 ms
  of your audio. One stretch that it is at least 99% sure is speech, and that is louder than 3 times the floor (at least
  150), stops her, about 0.1 s later than loudness. On one recorded drive (100 minutes of car noise, mixed in tests) it
  stopped her 0 times, against 42.5 per 10 minutes (3.5 at the levels live calls show) for loudness on the same noise,
  and still caught 99% of interruptions. Real cars are not measured yet. Whispers are mostly missed; a radio, singing
  or a passenger talking counts as speech; bus noise stops her less often than with loudness, but not never.

The rule not chosen runs beside it and is never acted on. It is counted at most once per stretch of her speech (a
sentence resumed after a cough counts again), and only when the rule in charge did not stop her there, or stopped her on
no words or on her own voice. Each call's end logs one line with the counts per rule, for example
`calls: barge-in speech: speech stopped her 1 time, would have 0; loudness stopped her 0 times, would have 4; her own
voice 0; detector 412 chunks, slowest 38 ms, 0 failed`. When loudness took over during the call, the line says why,
for example `calls: barge-in speech (loudness after 3 failed answers in a row): …`, and counts the failures by kind:
`timeout` (no answer within 0.3 s), `http` (no service, a refusal or no probability in the answer) and `late` (an
answer for audio more than 600 ms old). `/usr/bin/sudo /usr/local/estia/bin/estia logs -n 5000 | grep "calls: barge-in"` lists them:
these counts decide whether speech becomes the default. Each stop logs which rule stopped her, the detector's
probability or the number of loud frames, the level and the floor. When the level gate turns away what the detector
heard as speech, one line per stretch logs the loudest such chunk, for example
`calls: speech gated (p 1.00, level 132, floor 40)`. Never audio or words.

The speech detector needs the current voice service (`scripts/install-voice-daemon.sh`, asks for your Mac password: run it
yourself). Its model (about 1 MB) is downloaded at the first start into
`~/Library/Application Support/local-assistant/voice-models/silero-vad`, pinned to one commit and checked against its
sha256 values; delete that folder to download it again. `curl -s http://127.0.0.1:8150/health` shows the detector as
`"vad"`: `ready`, `loading` or `unavailable` (the download failed or the files are not the pinned ones;
`~/Library/Logs/local-assistant/voice.log` says which). An older voice service has no `"vad"` field. Until the detector
is ready, loudness decides; a call whose detector fails or answers too late three times in a row goes on with loudness,
and the log says so once. A call never waits for the detector. With `barge = "speech"`, `scripts/doctor.sh` shows a
Speech detector line (ready, loading, unavailable, old voice service or not ready) with its fix, and `/status` adds the
state to the Calls line.

**Her own voice.** A loud speaker next to the microphone (a laptop on Signal Desktop) can play her back and stop her.
After every stop, the transcript is compared with what she had already said: the sentence before and the current one
up to the stop. It is her own voice when it has at least 3 of her content words (short words such as "the", "at" or
"it" left out), at least 60% of its content words are hers, and at least 3 words come in a row exactly as she said
them. A stop, yes or no word she did not say ("wait", "yes", "no", "not") makes it the caller's: "the dentist at three"
or "no, not Amsterdam" over her own sentence is the caller talking. All 11 languages; Japanese by characters (pairs of
characters, and 6 in a row). She carries on, as after a cough. The first time in a call, a stop then needs 1.5 times
the level that stopped her, for the rest of the call (both rules; raised once, never again). Her own words never reach
the model. The log says `calls: her own voice heard; carrying on`.

**Wake-up calls.** With `[calls]` on, a reminder with "call me" (or `/remind call 07:00 wake up`) also places a Signal
call: the text first, always, then the call. `wake_call = false` in `[calls]` keeps those reminders as wake-up texts (a
second text two minutes later unless you wrote), and
`max_calls_per_day` (3) limits them per day. Before 11:00 she opens with "Good morning! It's 7:00." and the reminder's
text, later with "It's 17:00. You asked me to call you."; a minute of silence after the good morning ends the call with a
goodbye. Not answered, busy or a failed dial: one more try two minutes later, unless you wrote to her in between. A
decline means you are awake: no second try. Then one note, such as "Wake-up call not answered (07:00, 07:02)." A call is
never placed more than five minutes late, and never while she is already on a call with you (the text says why).

- **Your phone's Focus or Sleep mode** silences Signal calls unless Estia's contact is allowed in that Focus.
- **Linked devices** (Signal Desktop, an iPad) ring too; a hang-up on one of them can end the call on the others.
- Outgoing calls use signal-cli's `startCall`, which upstream calls experimental. Test one before you rely on it:
  `/remind call 2m test`, with the phone locked, then with a linked device on (docs/e2e-checklist.md, Reminders).

> **Keep your phone's own alarm for anything that matters.** Estia runs on one Mac. A power cut, an update, a crash or a
> Signal problem means no reminder and no call. The wake-up call is a friendly extra.

### Spotify (once)

Music by text message, through Spotify's official Web API. Spotify's developer policy forbids voice control, so calls and
voice notes never control music.

1. Spotify Premium on your account. A development-mode app needs a Premium owner.
2. On a computer, at developer.spotify.com/dashboard, choose **Create app** (Web API). Set the redirect URI to exactly
   `http://127.0.0.1:8888/callback`. Under **User Management**, add your own Spotify name and email. Copy the **Client ID**
   (no secret is needed).
3. Only if you have Bose SoundTouch speakers: give them fixed addresses (DHCP reservations), so Estia can wake them from
   standby, and uncomment the `[spotify.bose]` lines in step 4.
4. Add to the config. Each setting is its own line; uncomment only the ones you need:

   ```toml
   [spotify]
   client_id = "<the Client ID>"
   # default_device = "Kitchen speaker"
   # volume_start = 30
   # volume_cap = 60
   # [spotify.bose]   # only with Bose SoundTouch speakers (step 3)
   # "Kitchen speaker" = "192.168.1.40"
   # "Bedroom speaker" = "192.168.1.41"
   ```

   The names are the speakers' names in Spotify. Without `default_device`, music goes to Spotify's active device, or the
   first one listed.
5. `cd <repo> && scripts/spotify-login.sh`: open the printed address on a computer, sign in, and paste back the address
   the browser could not open (`127.0.0.1:8888…`). Restart the bridge. `scripts/doctor.sh` must show Spotify ○ or ✓.

The sign-in lasts six months (Spotify's limit). Estia sends a note once a week during the last 30 days. Undo: remove
`[spotify]` and restart, then revoke the app's access in your Spotify account settings.

### Web search (once)

Estia looks things up with Ollama's web search (it returns the pages' text), and falls back to Brave's web search.
Brave's free plan allows 2,000 searches a month; Estia reads Brave's top page when it answers from Brave. A service that
answers "too many requests" or refuses its key is paused for a while (shown in "How's the Mac?").

1. Put the keys in the login keychain, with a hidden prompt. Over SSH, unlock the keychain first (it asks for your login
   password):

   ```sh
   security unlock-keychain ~/Library/Keychains/login.keychain-db   # over SSH, if the keychain is locked
   security add-generic-password -U -a "$USER" -s brave-search -w
   security add-generic-password -U -a "$USER" -s ollama -w
   ```

   Brave's key comes from the Brave Search API dashboard (api-dashboard.search.brave.com). The Ollama key comes from your
   account at ollama.com/settings/keys. The keychain service names are `brave-search` and `ollama` (and `google-maps` for
   places and routes). `-U` replaces a key that is already stored, so run the command again to change a key.
2. `cd <repo> && scripts/set-web-keys.sh`. It copies the keys into `~/.config/local-assistant/web.env` (mode 0600), and
   may ask for your login password to unlock the keychain. It prints only the keys' lengths. Run it again after you change
   a key; no restart is needed. Without a login keychain, write `web.env` yourself (mode 0600), one per line:
   `BRAVE_API_KEY=`, `OLLAMA_API_KEY=` and `GOOGLE_MAPS_API_KEY=`, each followed by its key.
3. Add `[web]` to the config (optionally `country = "NL"` for Brave's search country, and `brave_monthly_cap = 2000`, set to
   your Brave plan's monthly limit; `0` means Ollama only). Restart. `scripts/doctor.sh` must show Web search ○ or ✓.
4. A few days later, look at your ollama.com account's usage page to see whether web searches use its free credits.

Undo: remove `[web]` and restart, then delete `web.env`.

### Places and routes (once)

Estia finds places with Google Places (rating, price, open now, today's hours) and works out routes with Google Routes
(time, distance, public transport lines), when you ask. The Google Maps links arrive in the Sources note. "Near me" means
the location you last sent in Signal (attach → Location; kept for 3 hours, in memory only), otherwise your home area.

The quick way is a script. It needs the Google Cloud CLI (`gcloud`):

```sh
brew install --cask gcloud-cli
gcloud auth login --no-launch-browser     # open the link it prints on any device, then paste the code back
cd <repo> && scripts/setup-google-maps.sh ["Project name"] [project-id]
```

The script creates a Google Cloud project, links your first open billing account (it prints which one and asks first),
switches on Places API (New) and Routes API, and creates an API key that may call only those two. It stores the key in the
login keychain (service `google-maps`) and runs `scripts/set-web-keys.sh`. Then set the quotas (step 3 below).

By hand, in the Google Cloud console, in a project with billing:

1. Enable **Places API (New)** and **Routes API**.
2. **Credentials** → **Create API key**, with API restrictions set to only those two APIs.
3. Set the quotas. Estia stops at `[maps] daily_cap` itself (30 a day by default). Setting the console quotas to 30 a
   day is a second guard in case the key leaks (recommended): **Places API (New)** "Text Search requests per day" and
   **Routes API** "Compute Routes requests per day", 30 each. Budgets only send alerts; they do not cap spending.
4. Put the key in the login keychain, with a hidden prompt (unlock the keychain over SSH first, as in Web search):
   `security add-generic-password -U -a "$USER" -s google-maps -w`, then `scripts/set-web-keys.sh`.
5. Add `[maps]` to the config: `home = "<your address>"` (private; only a rounded area of about 1 km goes to Google for
   "near me"), optionally `daily_cap = 30`, and `region` (a two-letter region bias sent to Google; leave it out to send
   none). Restart. `scripts/doctor.sh` must show Places ○ or ✓.

The home address stays in the config file only. It goes to Google once, to find its area, and for routes from or to
home. The database keeps that area, rounded to about 1 km, and a tag of the address (a fingerprint made with your Maps
key, so the database alone cannot confirm a guessed address), never the address itself: a changed address, or a new
key, means one new lookup. With no key, nothing is looked up or kept. A Directions link to or from home names that
rounded area, never the address (without a known area, a route home has no link, and one from home starts where the
phone is). An earlier version kept the address beside the area, in the row of a failed lookup and in the Directions
links it sent: the next start rewrites such rows and links or deletes them (deleted rows are overwritten in the
database file).

Undo: remove `[maps]` and restart, then delete the key in Google Cloud.

### Health checks (optional extras)

`[health]` sets how the Mac is checked. The built-in checks cover the model service (Ollama), disk, memory, uptime and new
ERROR lines in the watched logs. The name the alerts use for the machine is `host_label`, a top-level key: write it above
`[health]`, not inside it (default: "the Mac").

```toml
host_label = "the study Mac"    # top level: the name status messages use for this machine

[health]
interval_hours = 4              # how often the Mac is checked while all is fine
confirm_minutes = 5             # a new problem is re-checked after this long before it is reported
disk_free_min_percent = 10
watch_logs = ["~/Library/Logs/my-service"]   # folders whose *.log files are scanned for ERROR lines
                                             # (default: the local-assistant log folder only)
extra_checks = [
  { label = "my-service", http = "http://127.0.0.1:8080/health" },
  { label = "my-agent", launchd = "system/com.example.my-agent" },
  { label = "my-backup", launchd = "system/com.example.backup", stuck_after_minutes = 1500 },
]
```

- `extra_checks` adds your own services to the check. Each entry is a local address or a launchd service. The list is
  empty by default, so nothing else is checked.
- `http` must be a plain `http://` address on this Mac or on your home network (no https). The check reports a problem
  when the address does not answer, or when its JSON `status` says it is not healthy (`ready`, `ok`, `healthy`, `up`,
  `running` and `pass` are fine).
- `launchd` names a service, such as `system/com.example.my-agent`. `stuck_after_minutes` (launchd only) reports a problem
  when that service has not run for this many minutes.
- A problem is reported once it has lasted `confirm_minutes`, and "back to normal" is sent once it is fixed.
- In her own user, she cannot read your home: the move leaves the `watch_logs` folders in your home out of her copy
  of the config and counts them on the screen ("health.watch_logs: 2 folders in your home left out of her copy (she
  cannot read your home)"). A folder outside your home stays.

### Projects (extra, off by default)

Estia reads the git repositories directly in one folder of this Mac, read-only, at one of three levels. Off without
`[projects]`: most people need none of it. `scripts/setup.sh` asks "Should she see projects on this Mac? Most people:
no." when it writes the config, and `scripts/setup.sh --extras` asks again later: the folder (it must exist; your home
itself is refused) and the level. Nothing is sniffed: no folder is read before you name it.

```toml
[projects]
root = "~/dev"                  # the folder that holds the projects (in her own user: its absolute path)
default = "summary"             # every project: summary, docs or code
docs = ["my-project"]           # single projects at level docs
code = ["other-project"]        # single projects at level code
```

- `summary`: `project_status` (the start of the README, the project's `AGENTS.md`, the open `- [ ]` items of `todo.md`,
  the last 30 commits, the number of uncommitted files) and the whole README with `project_read`, 200 lines at a time.
- `docs`: also the project's documents (`README*`, `todo*` and `setup_*` as .md, .txt, .rst or plain text, and
  `docs/*.md`), read and searched (`project_read`, `project_search`).
- `code`: also its source code: every text file git tracks, read and searched (a search reads at most 2,000 files and
  20 MB, in path order, and says how far it got when it stops early).

Only files git tracks, never secret-like names such as `.env` (the list is in section 8), never binaries (a NUL byte),
never files over 200 KB, never a symbolic link, never outside the folder you named. Asked what she can see of your
projects, she calls `project_list`: each project's level, and how you change it here. With Projects off, her rules say
so and how to turn them on; no project tool is offered.

A name heard on a call or in a voice note is matched to the nearest project: the same letters without case, accents,
spaces or separators ("garden app" is garden_app), else the nearest by spelling or by a small sound key ("Photo Book"
is fotobook), and the tool's answer says which project it took; she names it in her answer. A name alike only in sound
and spelled far apart, two about as near, a single word of a longer name, a name in another script (Russian, Japanese,
Korean) or none close: the answer asks her to ask you ("Do you mean fotobook?") instead of guessing.

In her own user her config names the folder by its absolute path (her `~` is `/var/estia`), and her user reads that
folder, read-only, and nothing else in your home, once it is shared (2.7, "Your projects folder"): `/usr/bin/sudo -u _estia -e /var/estia/.config/local-assistant/config.toml`,
then `scripts/share-projects.sh <folder>` (as you, no admin rights), then `/usr/bin/sudo /usr/local/estia/bin/estia restart`.
`scripts/setup.sh --extras` prints these lines with your folder and level. `scripts/share-projects.sh` shares only a
folder inside your home: for one outside it, setup says so in place of that step, and read access for `_estia` is
given by hand. Before her own user, setup adds `[projects]` to your config itself (a copy of the old one is kept);
restart her afterwards. Doctor's Projects line shows only with Projects on. With Projects off,
`scripts/share-projects.sh` shares nothing and says so.

### Personality (soul.md)

Without a style file of yours, Estia has her own: warm, curious and playful. The answer comes first, and when the moment
invites it one short, light joke may follow, never when you are worried, sad or in a crisis, about health, money trouble
or safety, or when something serious is going on, never at your expense and never instead of the answer.

Your style lives in `~/.config/local-assistant/soul.md` (mode 0600): who she is, her tone, how long she answers and how she
talks on calls. It replaces her built-in style entirely. You write it; she never changes it. Start from `soul.example.md`. Lines starting with `#` are notes for you
and are left out. The file is limited to 2,000 characters.

It is read for every answer, so an edit counts from your next message; no restart is needed. It sits below the rules in
her instructions, so it can change how she sounds, never what the rules and the bridge's checks allow. A line such as "never
ask me for a yes" changes nothing. `/soul` shows the text in use, or why none is (missing, readable by others, too long,
hidden characters); a file that is not used leaves her built-in style in place. Undo: delete the file.

## 4. Using it

Write to the bot from Signal as you would to a person. Replies come as short plain text in the current language (see
"Languages" below), with a read receipt and "typing…" while Gemma works.

| Command | What it does |
|---|---|
| `/new` | Start a fresh conversation. The profile stays and old chats remain searchable; learning from the closed conversation runs a minute later |
| `/profile` | Numbered facts with where they came from (learned from chat, you asked, from a link) and the date |
| `/fix N new text` | Correct fact N in place: same number, the text checked like a "remember" fact, and from then on it counts as yours ("you asked"). The change log keeps the old text |
| `/forget N` · `/forget words` | Remove fact N, or the facts containing the words (asks for a number if several match). Takes effect at once |
| `/learn <link>` | Learn about you from a page (e.g. your public LinkedIn profile): the bridge reads it, one structured model call takes out the facts about you, and you get the exact list of what was saved and what was already known |
| `/status` | Model loaded or not, waiting messages, profile fill, last learning run, history size, Signal restarts, and a line per part that is turned on: working (last read), set up but not tested yet, or failing with the fix |
| `/voice` · `/voice auto\|on\|off` · `/voice echo on\|off` | Spoken answers: for voice notes (auto, default), always, or never; the transcript echo (off by default) |
| `/language` · `/language <name or code>` | The 11 languages with the current one marked; switch with a code or a name in English or in the language itself (`/language nl`, `/language dutch`, `/language nederlands`) |
| `/babble` · `/babble <name or code> [a2\|b1]` · `/babble off` | Language practice: start one (every language at A2; `b1` for B1), resume the last setting with a plain `/babble` (the first time: the list), or end it with a recap. Typed only; refused while another allowed account practises |
| `/remind [call] [daily\|weekdays\|weekly] <when> <text>` | A reminder without the model: `<when>` is `17:00` (today, or tomorrow once passed), `2026-10-13 07:00`, `20m`, `2h` or `3d`; `call` adds a wake-up call. The same checks and confirmation as asking her |
| `/reminders` · `/reminders cancel N\|cN\|all` | Your reminders, the check-ins she may send (`#cN`) and what was missed or failed in the last 7 days, with the reason; cancel one or all |
| `/initiative on` · `/initiative off` · `/initiative` | Let her write first now and then (opt-in: off until you turn it on; wins over the config), stop it, or see which |
| `/quiet` · `/quiet off` | Pause every message she starts herself, until `/quiet off`; reminders and wake-up calls still come |
| `/lock` | An emergency stop, typed from a device she answers: no answers, no calls, nothing sent until `/unlock` from the same device, 10 minutes later at the earliest; it holds after a restart (`/usr/bin/sudo /usr/local/estia/bin/estia unlock` ends it from the Mac) |
| `/unlock` | Ends `/lock`: only from the device that sent it, after the wait |
| `/devices` · `/devices ok N` · `/devices forget N` | The devices she answers and the held ones; allow held device N (only from a device she already answers); take device N off the list after you unlink it (a linked device silent for 14 days is held again) |
| `/help` | The list above |

In normal messages:

- "Remember that …" saves the fact at once, and Gemma answers "Noted: …".
- Every profile change in an answer is listed in a separate message right after the reply, written by the bridge itself
  (not by Gemma), e.g. "Profile changes: Saved #14: … (/forget 14 undoes it)". No such message means nothing was saved.
  Fact numbers are never reused. Facts learned in the background are mentioned once with the next reply.
- "Forget …" → Gemma removes the matching fact (or asks which one). Unlike `/forget`, a removal by Gemma keeps the old text
  in the change log, so a removal you didn't want can be undone.
- "Learn from my profile: <link>" → Gemma calls its learn tool, which reads the page and saves the facts itself (the same
  extraction as `/learn`). Asking again re-reads the page; facts already saved are skipped.
- "Have a look at <link>" → Gemma reads the page to answer you and saves nothing. Only links in that same message can be read.
- "What did I say about …?" → Gemma searches the history.
- "How's the Mac?" → Gemma runs `mini_status` (Gemma and Ollama, optional extra checks, disk, memory, uptime, new ERROR lines
  in the logs since the last status, Signal restarts) and answers in a few lines.
- "What's next on my-project?" (with Projects on) → `project_status`: the start of the README, the project's `AGENTS.md`,
  the open `- [ ]` items of `todo.md`, the last 30 commits and the number of uncommitted files. `project_list` shows all
  projects with their level and how to change it. The whole README reads at every level (`project_read`, 200 lines at a
  time); at level `docs` also the documents, at level `code` also the source code, read and searched (`project_search`).
  A misheard name is matched to the nearest project, or she asks "Do you mean …?" (section 3, Projects).
- A photo, with or without a question → Gemma answers about it (HEIC, JPEG, PNG, WebP, GIF; up to 4 per message). Follow-up
  questions within 5 minutes still show Gemma the photo (kept in memory only, never on disk); later it says it can no longer
  see it. The file is deleted after the first answer.

Alerts need no question: "⚠ Problem on the Mac:" with one line per problem and its start time, when a check still fails 5
minutes after it first failed; "✓ Back to normal on the Mac:" once it is fixed. Checks run every 4 hours, and every 5 minutes
while a problem lasts. The bridge cannot report its own outage: `scripts/check.sh`, run on the Mac (for example over SSH
from another computer), covers that.

Mail (read-only; see "Mail (once)"):

- "What needs my reply?" → `mail_digest`: unanswered mail that needs a reply (at most 7 days old, urgent first) and new
  actions, from all configured mailboxes, numbered for the day. "Read 2" → `mail_read` fetches that mail live. "Did Alex
  answer about the quote?" → `mail_search` (sender, subject, summary; the mail text when nothing else matches).
- Every day at 09:15 the morning digest arrives as a note: the calendar first (with `[calendar]`), then the mail part ("Mail:
  nothing needs you today." on quiet days). A digest missed while the bridge was down comes at start, until noon. The time
  is `digest_time` at the top of the config (before 12:00).
- Every 5 minutes the bridge syncs the headers of Inbox and Sent Items (Graph delta queries). Mail you archive or delete in
  Outlook disappears here too. A mail counts as answered when Sent Items has a later message in the same conversation. When
  idle, Gemma labels new mail (urgent reply, reply, action, reference, noise; waiting when you replied) and writes a one-line
  summary. The body is fetched for that and not kept.

Calendar (read-only; see "Calendar (once)"):

- The 09:15 digest starts with the day: "Fri 9 Oct", "Calendar today:" (all-day items first, then what is still to come, with
  times and short places; an invitation the calendar's owner hasn't answered is marked, declined ones are left out),
  "Tomorrow: …" in one line, then the mail part. With several calendars each event's title starts with its calendar's
  name ("- 16:00–17:30 Club: Football"), and a calendar that could not be read gets its own line with the reason and the
  fix.
- "What's on Friday?" and "am I free Tuesday afternoon?" → `calendar_agenda` (one day, or up to 14 days). "When is the dentist?"
  → `calendar_search` (the next events within a year whose title or place has every word).
- Events are read live, never cached: Google calendars through the calendar helper (`tools/ical`), Outlook calendars through
  Graph. Event text stays only where mail text does (the sent digest note and Gemma's answers). Event text is never
  followed as an instruction or saved to the profile, and a device request in an answer that read the calendar waits for
  your yes.
- If Google stops serving an address (for example after a reset), one note says "Calendar: the secret address no longer
  works…" (for a named calendar: "Calendar School: the iCal address no longer works…"), and the digest says so too. An
  Outlook calendar whose mailbox needs a new sign-in, or whose `Calendars.Read` is not granted yet, says so in the digest
  and in `/status`. "How's the Mac?" covers the calendars as well.

Reminders and wake-up calls (on unless `[reminders] enabled = false`):

- "Remind me tomorrow at 7 to call mum", "in 20 minutes, the pizza", "every weekday at 8, the pills" → `set_reminder`.
  The time must be at least a minute ahead and at most 366 days; the text (your words, in your language) at most 200
  characters. At most 50 open, 20 new a day and 3 calls on one day; a refusal names the limit. "What reminders do I
  have?" → `list_reminders`; "cancel tomorrow's wake-up call" → `cancel_reminder`.
- **The contract: a reminder exists only when the bridge's note says so.** The confirmation is a separate note written
  by the bridge, never by Gemma: `Reminder set (#12): Tue 13 Oct 07:00 · wake-up call · "wake up". /reminders to see or
  cancel.` The first one each user gets adds "Only this note means a reminder is set."; the welcome and `/help` say it
  too. What Gemma says is never the proof. The note shows the time it understood, so a misheard "15" or "50" in a voice
  note shows at once. A time that does not exist (the night the clocks go forward) becomes the same time an hour on, and
  the note says so. Gemma sees the year in the stamp of your newest message; a date she still writes with an earlier
  year is read as the nearest such day, and a day more than 2 days past, with that year or this one, as the next one
  ("20 May" asked in October: next May; "5 January" asked in December: next January), never for a repeating reminder. Up
  to 2 days past is refused ("that time has passed"). The note shows the date it understood, with the year when it is
  not this one.
- When no reminder was set, the code says so, in any language: every sentence of her answer that mentions a reminder,
  waking or a call is left out, her other sentences stay (the summary of a mail, say), and the code's own line follows
  them, in English, with the reason ("No reminder was set: that time has passed."; only that line when nothing else is
  left), so an "it's set" never reaches you. When one reminder of several in an answer was set and another was not,
  that one gets its own note: `Not set: "water the plants" (that time has passed).` If she says a reminder or a call is
  set when she never tried to set one in that answer, a "No reminder was set. Ask me again, or use /remind." note
  follows when the bridge recognises the claim: a precise set of patterns in
  the 11 languages that never fires on an ordinary sentence ("Noted: you start work at 9:00", "your call with Anna is
  set for 15:00" in the calendar, "a payment reminder from the bank"), so it misses some; it is a safety net, the note
  above is the proof. After she listed your reminders in that answer, talk that names an open reminder's time or number
  is no claim, and a claim of a new one gets "No new reminder was set.". On a call she says the confirmation in English
  ("Reminder set for Tuesday 13 October, 07:00.") and the note follows as a text; when none was set, she says the rest
  of her answer and then the code's line why, in English.
- Only your own words: a reminder is refused in code in an answer that read mail, the calendar, the web, a page, a
  project file or the history, and for a shared audio file or a photo (Gemma is told you can ask again in your own
  words). A voice note or a call counts as your words. A time you mention ("I have the dentist at 10") is not a request.
- At its time: `Reminder: call the dentist` (a repeat adds "(weekdays at 08:00 · /reminders to stop)"). Reminders come
  at night too, and quiet hours or `/quiet` never hold them: you set them. While she is on a call with you, texts wait
  until it ends. A note that cannot be sent is tried every 5 minutes for an hour, then shows as failed in `/reminders`
  and doctor.
- Repeating reminders keep their local time (08:00 stays 08:00 across summer and winter time), in the config's
  `timezone` (else the Mac's).
- Missed while the bridge was down: one note after the restart, `Late, I was offline 06:40-09:05:` with each missed
  reminder (at most 10, then "and N more: /reminders"); "(may be a repeat)" marks one that was being sent when it
  stopped (it may have arrived already); a repeat appears once, then goes on. A wake-up call is not placed late: "(not
  placed late)". Up to 2 minutes late counts as on time.
- Wake-up calls: see section 3, Calls, "Wake-up calls". Without `[calls]` (or with `wake_call = false`), "call me"
  reminders are wake-up texts: the confirmation says "calls are off: a text only", and at its time the text comes, then
  a second one two minutes later unless you wrote to her in between.

Messages she starts herself (opt-in: off until you turn them on; each kind on or off in `[initiative]`):

- **On and off.** Type `/initiative on` in Signal, or set `[initiative] enabled = true` in config.toml.
  `/initiative off` turns them off again; `/initiative` shows which. What you type wins over the config (like
  `/language`), and only a typed command counts: never a voice note, a call or the model. The reply to `/initiative on`
  tells the rules, so no other announcement follows; turned on in the config, she sends one note with them once (a
  fresh install's welcome says it instead). Off, nothing runs for them: no calendar reading, no model call, no message,
  and what you say meanwhile is never looked at for check-ins later.
- Four kinds. **Check-ins:** a plan you stated yourself ("I'll call the dentist tomorrow"), found in the idle pass (one
  structured model call, only on new messages: typed, voice notes and calls; never audio files, photos, commands, links,
  long pasted text or a message that set a reminder; an answer cut off at its length is asked again in halves, so no
  plan is lost). The model sorts each message (a task you will do yourself, an appointment, a maybe, someone else's
  plan, a question or anything else); only a task counts. The code checks it against your words, and it comes the day
  after that day at 17:00 (at least 20 hours later), for 2 days; at most 2 a week, at most 5 open; it asks one question.
  `/reminders` lists them as `#cN`. **Calendar notes** (with `[calendar]`): every 30 minutes she reads the next 48 hours
  and keeps only each event's id and times, in memory; an event in the next 24 hours that moved by 15 minutes or more,
  or disappeared from every calendar while all of them read fine, brings `Calendar change: Dentist moved 10:00 → 11:30
  (today).`; at 20:00 (`early_check`) a first event tomorrow before `digest_time` brings `Early start tomorrow: 07:30
  Dentist.` New events are not reported, and a calendar that fails never looks like cancelled events (an invitation in
  two calendars too).
  **The look back:** Sunday 19:00 (`lookback_time`), three lines from your own messages of the week and one question;
  fewer than 5 messages, or not sent by 21:00: skipped that week.
  **The improvised note:** about once a week (`improvise_per_week`) at a random time between 10:00 and 19:00, from the
  moment, your profile facts (from chat and "remember", never from a page) and her last notes, in one of four moods (a
  small thought, a curious question, something playful, a light wish for the week); never in the first week, she skips
  it only when nothing at all fits, and two unanswered in a row pause it for four weeks.
- The gate, in this order: on and the kind on, not `/quiet`, not in quiet hours (`quiet_hours`, 22:00-08:00; `""` for
  none), at most `max_per_day` (1) for all kinds together, the weekly caps, not busy (an answer, a queue, a call, or a
  message from you in the last 15 minutes), no language practice running (`practice`); the look back and the
  improvised note only with one allowed account. When
  two are due, the calendar goes first, then a check-in, then the look back, then the improvised note; a check-in that
  loses its day comes the next day, a calendar note that loses is dropped.
- The model writes the check-in, the look back and the improvised note in a call of its own, with its own short rules
  and her style below them: no tools, no chat history, no mail, calendar, web or pages. The code then checks the text
  (no claim that she did something, no question for a password, PIN or code, no link, number, mail or calendar word, no
  guilt such as "I miss you", no imitation of the footer); a text that fails is not sent, and nothing is sent instead.
  The words that ask for a code or a password, and claims of a reminder or a call, are checked in all 11 languages; the
  other word checks read English. A link is any web address, with or without https:// or www., on any ending of two
  letters or more, such as example.ch/x, with no list of endings; an abbreviation such as "e.g.", "a.m." or "z.B.", a
  decimal or a time is never one. Known gap: a one-letter name with a two-letter ending right before a full stop
  ("x.co.") reads as an abbreviation.
  Calendar notes are the code's own words.
- Each message ends with a line from the code: `(a check-in on something you said · /quiet to pause)`, `(calendar
  change · …)`, `(early start tomorrow · …)`, `(weekly look back · …)`, `(a spontaneous note, about once a week · …)`.
  Her part is kept in the chat history (your reply has its context), the line is not. Each is sent at most once: one
  that was being sent when the bridge stopped is never sent again.
- `/quiet` pauses them all until `/quiet off`; `/initiative off` turns them off. An install from before that turns
  them on in the config gets one note once ("New: reminders and wake-up calls…"); a fresh install with them on in the
  config hears it in the welcome.
- "Goes well" is counted, never judged by the code: `/status` shows the last four weeks (sent, answered within a day,
  `/quiet` uses) and the last skip of each kind with its reason. You decide whether she may write more often.
- To judge her style without sending anything: `uv run python -m local_assistant.initiative --draft improvise` (or
  `lookback`, `followup`) prints one draft from the real database, read only; it uses the model, so not while she is
  busy.

Lights and plugs (see "Lights and plugs (once)"):

- "Turn off the living room lamp", "dim the bedroom light to 30 %, warm white", "is the heater on?" → `device_set` and
  `device_status` (only when `[[tapo.devices]]` lists devices). Colours: warm white, cool white, daylight, red, orange, yellow,
  green, blue, purple, pink.
- After the answer, a note says what really happened, written by the bridge: "Devices: living room lamp off · bedroom light on,
  30 %, warm white" (or "… didn't answer").
- Rules per device: `normal`; `ask first` (Gemma asks you to reply yes, and the bridge's note says exactly what the yes does,
  e.g. "Devices: reply yes within 2 minutes to switch heater off"; your yes within 2 minutes is carried out by the bridge
  itself, anything else cancels it, one device at a time); `never off` (switching off is refused). A device request made in an
  answer that read a mail, a page or a project file waits for your yes too, whatever the device's rule.

Languages:

- One language at a time, set by `language` in the config and changed only by a typed `/language` (kept in the database,
  where it wins over the config). Not by a voice note, not by Gemma (there is no tool for it), and never during a call: a
  `/language` sent during a call is answered when the call ends.
- In another language than English, Gemma's rules get one line: answer only in that language, whatever language you write
  in, keeping names, code and quotes as they are. Voice notes are transcribed and spoken in it, and calls use its greeting,
  "one moment", "sorry, could you say that again?", "take your time" and goodbye, with its short "got it" ("Begrepen.")
  when she heard a request of five words or more.
- Code-written text stays English: notes, the digest's headings, `/status`, `/help`, errors. On a call the device
  question and the rare lines (the idle and length-cap goodbyes, device results) are spoken in English with Kokoro. When
  a device waits for the yes, she says only that question, and only it is kept (in Dutch Gemma claimed a switch before
  the yes). An answer in which no reminder was set keeps none of her words about it, in any language, and ends with
  the code's line why, in a text and on a call. A device yes counts in any of the 11 languages, still only as the whole message and only after the question was
  heard whole.

Language practice (`/babble`):

- Eight languages (English, Dutch, German, Spanish, French, Portuguese, Japanese, Italian) at A2 or B1; Russian, Polish
  and Korean are listed as not yet (in a test of the local model, too many of its Russian replies broke the practice
  rules, its own Polish had errors and its Korean corrections were less reliable). Every language starts at A2; the level is set only at the start (`/babble off` first to change
  it).
- Only the typed `/babble` starts it: never a voice note, a call or Gemma (no tool). It ends with `/babble off`, the
  end of a practice call, or 30 minutes without a practice message. `/language <x>` is refused while practice runs
  ("End practice first: /babble off"); a plain `/language` still lists. A `/babble` typed during a call is answered
  when the call ends.
- A session has its own conversation (`conversations.kind = 'practice'`): the chat conversation stays open and current,
  `/new` never touches practice, a practice conversation is never summarised, and idle learning, check-ins, the look
  back and the history search leave practice turns out. A practice message still counts as you writing (the busy
  gates, a reminder's retry). Her own messages (check-ins, the look back, the improvised note) wait while practice
  runs (the gate's reason word `practice`).
- Each practice answer: Gemma gets the practice rules, which mention no tool at all: the practice line (with the
  level's CEFR line) in place of the language line; no Memory bullets about remember, forget, search_history or links;
  no host, mail or device section and none of the configured tool sections; and one line that nothing is saved,
  switched, read, searched or set until `/babble off`. No tools are offered. A reply that is empty, only "---" or only
  a "→" line is asked again once, then replaced by a short question in the practised language (the broken reply is
  never kept). "---" lines go; at most one "→" line, moved first; none in a reply to a voice note or a call (it would
  be read aloud), and none whose words you already wrote (only capitals or punctuation differ). A "Noted: ..." gets
  "Profile changes: none. Practice never saves a fact; ...". A claimed reminder, when your message or the one before
  asked for one, gets "No reminder was set: practice never sets one. Type /remind, or ask again after /babble off."
  (a roleplay line such as a shop's "I'll call you tomorrow" gets none).
- Voice notes and calls are heard and spoken in the practised language. A call you make during practice is a practice
  call: its greeting in that language, and when you said something, hanging up ends practice and the recap replaces
  the call summary. Texts you typed during that call are answered first, as practice (for at most 2 minutes), so the
  recap reads them. A call she places for a reminder or a wake-up is an ordinary call: your `/language`, her tools,
  its summary, and practice keeps running. Goodbye words on a call are recognised in English only: in another
  language, hang up yourself.
- The recap: one structured call (JSON schema, temperature 0) over your practice turns (the latest 40, at most 12,000
  characters), at most 3 points {said, better, why}, the why in your `/language`. The code keeps a point only when
  "said" is verbatim in one of your turns, "better" differs by more than punctuation or capitals, the change is small
  (the same words in another order, or at most max(3, half the words) words changed; Japanese by characters, at most
  max(6, half of them)), a negation in "said" is still in "better", and the why is not empty and neither thinks aloud
  nor says your form is right. Thinking aloud counts only at the start of a sentence and never quoted ("Wait, ...",
  "Let me ...", not "'Wait' takes 'for'"); "no mistake" and the like count unless the why quotes the correction; "is
  correct" and the like count only when the why quotes your words, not the correction, and no "not" goes with it
  ("'I goed' is correct" is dropped; "'went' is right" and "'Gekoopt' klopt niet" stay). English always, plus short
  lists in your language that need a native check. Dropped points are logged as counts per reason, never text. When
  the model named points and the checks kept none, the recap says "No mistakes to note." (without "well done"). For
  Dutch, French, Russian, Japanese and Korean voice or call turns the recap says it is based on what was heard.
- The end: `/babble off` (the recap at once), a practice call (at once), or 30 minutes without a practice message: then
  the session ends and the recap says so; during the quiet hours (`[initiative] quiet_hours`, 22:00-08:00 by default)
  it is written and sent when they end, or at once when you write again first (before that message is answered, in
  chat). `/quiet` and `/initiative` never hold it: you started it. The recap is a note in the practice conversation,
  stored with the session cleared in one transaction: never written twice; a failed send is a failed note, resent by
  the recovery at the next start within its window, like any failed note. When Gemma fails, "No recap this time" is
  sent (no retry).
- Practice messages are kept like other messages, in the practice conversation; no list of mistakes is kept. State
  keys: `babble:last` (the last language and level) and `babble:session` (the running or held session: language,
  level, account, start, conversation, end, due time and why it ended; empty when none). One session at a time, for
  the account that started it; another allowed account is refused while it runs, and its held recap goes out at once
  when that account starts its own.
- `/lock` stops practice too: no practice answer, no call (a running practice call is hung up, which ends practice),
  no recap. A recap that falls due meanwhile (that call's end, or 30 minutes without a practice message) is written
  and sent after `/unlock`, within 30 seconds (an idle end in quiet hours still waits for their end); a lock that
  comes while Gemma writes it keeps it held, written again after `/unlock`. Links in a practice answer follow the
  chat's rule (defanged after outside text: here an audio file or a photo in the practice conversation); practice
  runs no tool, so nothing in it can set a reminder.
- After a restart during practice, the practised language's voice is loaded too.

Voice notes (in the current language):

- Send a voice note: the bridge fetches it, the voice service on the Mac transcribes it, and Gemma answers the transcript as
  if it were typed (the history keeps "[voice] what you said"). The audio is deleted afterwards.
- The text answer comes first. Then the same answer comes as a Signal voice note (Kokoro in English, Supertonic-3 otherwise; cleaned for speaking: no code, links,
  lists or emoji), cut at a sentence after 1,500 characters ("The rest is in the text."). Notes are never spoken.
- `/voice auto` (the default: voice answers for voice notes), `/voice on` (every answer), `/voice off`; `/voice echo on` first
  shows what was heard (`🎙️ "…"`); `/voice` shows the settings.
- Notes: "I couldn't make out that voice note, could you say it again or type it?" (too short or silent), "That voice note is
  too long for me (max 3 minutes).", "Voice isn't available right now; please type it." (service down or still loading).
- Without `[voice]`, voice notes get "I can only handle text and photos for now."

What it does not do: send, draft, move or delete mail; change calendar events; or handle files other than photos, voice notes
and the link pages it is asked to read. Files are answered with "I can only handle text, photos and voice notes for now."

Notes you may see:

- "Estia is busy with another job, your answer is queued." After 60 s, when another client is using the model.
- "Estia isn't available right now, try again in a bit." Ollama is unreachable for 2 minutes.
- "I was offline HH:MM–HH:MM, ask again if you still need something." Once after a restart, instead of answering messages older
  than 10 minutes (including those Signal delivers only after the restart).
- "Sorry, I got confused. Could you say that again?" When anything unexpected goes wrong. You always hear back.
- "Late, I was offline 06:40-09:05:" with a list of reminders: they came due while the bridge was down (see above).
- "No reminder was set: <why>." at the end of her answer: Gemma tried to set a reminder and none was set; whatever she
  wrote about it is left out. "No reminder was set. Ask me again, or use /remind." as a note: she said one was set when
  she never tried, in that answer. `Not set: "…" (<why>).`: one reminder of several in that answer was not set.
- A message ending "(… · /quiet to pause)": one she started herself (see above).
- "I couldn't open that photo. Could you send it again?" A photo that is too big (over 20 MB) or that `sips` cannot read.
- "(I looked at the first 4 photos only; send the others in a new message.)" and "(I couldn't open N of the photos.)".

## 5. What it remembers

| Table | Holds |
|---|---|
| `conversations` | One row per conversation (`/new` to `/new`) with its summary and summary point; `kind` is `chat`, or `practice` for a `/babble` session (schema v8) |
| `messages` | Every message in and out, its status and its place in the conversation (`seq`); for an incoming one, the sender's device that sent it (`source_device`, schema v9) |
| `documents` | Pages learned from links (full text, searchable) |
| `profile_facts` | The live profile |
| `profile_changes` | Every add, replace and remove, with the reason and the message it came from |
| `tool_calls` | Every tool call Gemma made (audit) |
| `mail_messages` | Mail headers of the configured mailboxes (sender, recipients, subject, date, read, Focused/Other), Gemma's label and one-line summary, answered time; no bodies; deleted after 30 days (schema v4) |
| `mail_sync` | Graph delta link and last sync per mailbox and folder |
| `known_devices` | Your devices she answers or holds, by Signal device number: the phone (1) from the start, a linked device allowed with `/devices ok N` or held; when each was first and last seen (schema v9) |
| `attachments` | Photos of a message (signal-cli's file id, type, size) until answered; `deleted_at` once the file is gone (schema v3) |
| `state` | Small values: profile snapshot, learned-up-to pointer, last learning run, chars-per-token ratio; `watch:mini` (open problems and whether they were alerted), `health:logs` (log offsets and error counts); `reminders:calls_day` (calls placed today), `reminders:contract_told:<account>` (that account's first confirmation said only the note means a reminder is set); `initiative:quiet` (`/quiet`), `initiative:enabled` (`/initiative on` or `off`), `initiative:improvise_plan` (this week's times for the improvised note), `followups_after_id`; `babble:last` and `babble:session` (language practice); `lock:on`, `lock:device`, `lock:since` (`/lock`), `devices:noted:<account>:<device>` (the last held-device note), `privacy:pruned_at` (the last retention pass) |
| `reminders` | Your reminders: your words, the local time as meant and its time zone, the next time in UTC, repeat, call, status, the note of the current firing and a fixed outcome word (sent, late, no_answer …). Done and cancelled ones are deleted after 30 days (schema v7) |
| `followups`, `initiative_log` | Check-ins she may send (your words, at most 120 characters, the message they came from, due and expiry times; deleted 30 days after they close) and a log of the messages she starts: kind, day, outcome, a fixed reason word, the message, reply and `/quiet` times, never text (kept a year). `messages.footer` keeps the line the code adds to them (schema v7) |

- **What Gemma sees:** rules, then the profile snapshot, the summary, recent messages (about 12,000 tokens, with earlier tool
  calls replayed in short form), and your new message with its local time. Fixed parts come first, so Ollama can reuse its
  cache. Notes (profile changes, busy, offline) are never part of it.
- **Summaries:** when recent messages pass about 12,000 tokens, the older half is summarised in the background. Above 20,000
  tokens the oldest messages are dropped without a summary (an emergency, logged).
- **Snapshot:** the profile Gemma sees changes only at `/new`, after a summary, after a forget or a `/fix` and at start.
- **Learning:** only your own messages. Text you paste from others (emails, articles) is ignored unless you ask to learn it.
  Learning may add facts and change the ones it learned itself, but never replaces or removes a fact you stated ("remember
  that …", a link you asked to learn, a `/fix`): such a change is skipped and logged by fact number only. To change one of
  your own facts, use `/fix N new text`.
- **Forget vs delete:** `/forget` removes the fact and blanks its text in the change log. The original chat message stays in
  the history. Messages are kept until you delete them.

## 6. Tests

1. **Unit tests** (offline, about 30 seconds): `scripts/verify.sh` runs the privacy check (`scripts/privacy_scan.py`),
   ruff (lint and format), mypy in strict mode, and
   pytest, with a fake signal-cli, a fake voice service, a fake device helper and a scripted fake Gemma. The helpers have
   their own suites: `cd tools/voice && swift test` (Swift Testing), `cd tools/tapo && uv run pytest -q` and
   `cd tools/ical && uv run pytest -q`.
2. **Gemma test set** (the real model): `uv run python -m evals.gemma_suite`, or with your own style file:
   `EVAL_SOUL=~/.config/local-assistant/soul.md uv run python -m evals.gemma_suite`. It uses the model in your config
   (`local-worker` when `model` is unset, see 2.2); `EVAL_MODEL=<tag>` runs it with another tag. The gate is simple: every
   safety case passes, and at least 90% of the rest do. The model is not deterministic, so run the set twice before you
   trust a change. `EVAL_LANGUAGE=<code>` (for example `nl`) adds the language's rule line and runs the safety cases and
   three language cases, which pass only when the answer is in that language; the safety cases then look for that
   language's words too (a yes, "it is off", "noted", a refusal). `EVAL_NAME=<name>` gives her another name (default
   Estia; `knows_her_name` checks it). The cases cover:
   - **Chat:** short plain answers, the language of the question, use of the message time, rolling memory, voice answers
     without lists or links, and her built-in style (without `EVAL_SOUL`): a light joke when invited, none on a worry.
   - **Tools:** remembering and forgetting facts, searching history, honest misses, learning from a link, the Mac's status,
     project status and a misheard project name on a call (taken as the nearest project, or "Do you mean …?" when two
     sound alike), photos (and follow-ups), the mail digest and reading a mail, device switching and dimming, the
     calendar, the start of a call (today's numbers said without a title, the titles read after a yes, and with
     `EVAL_LANGUAGE` in that language), and reminders (a time tomorrow, "in 20 minutes", a wake-up call; a mentioned
     time sets none).
   - **Safety:** planted instructions in a web page (in English and Greek), a mail, a project README and a pasted email; a
     link without "learn" saves nothing; "ignore previous instructions" is not stored; "ask first" and "never off" devices
     behave as the rules say; a hostile personality file cannot loosen the rules; no reminder from a mail or a planted
     page, and no claim of one that was refused; a personality file that asks her to request a PIN in a message she
     starts herself.
   - **Learning:** facts are extracted, a changed fact replaces the old one, small talk adds nothing, mail is sorted, and
     a plan the user states becomes one check-in on the right day.
   - **Messages she starts:** a gentle check-in, a look back only about that week, spontaneous notes that pass the
     code's checks (a skip is fine), and, with `EVAL_LANGUAGE`, her text in that language with the code's English line.
   - **Language practice:** "what can you do?" names it, no tool runs and no light, reminder or save is claimed in
     practice (safety), the recap's why comes in the user's own language, and a voice note gets no "→" line. Only by
     name, `uv run python -m evals.gemma_suite practice` (or `practice_nl` for one language) runs two cases per offered
     language from the tutor test's sets (`evals/practice/`, A2 and B1): the "→" lines' precision on sentences with a
     mistake (at least 90%), "→" lines on correct sentences (at most 10%), pure replies (at least 95%), and the recap's
     precision against the planted mistakes (at least 90%) and its recall (at least 50%). Run it twice.
3. **Phone walk-through:** [docs/e2e-checklist.md](docs/e2e-checklist.md) (127 items: texts, voice, calls, devices, mail,
   calendar, music, web search, places and routes, languages, language practice, reminders and wake-up calls, the
   messages she starts, what she knows at the start of a call, projects as an extra, and the hardening: linked devices, the lock, the safety number, the model pin and her own
   user; after the move: the firewall line as her, the voice-ready text and `remove-old`'s copies).

## 7. Operations

| Task | How |
|---|---|
| Check the setup | `/usr/bin/sudo /usr/local/estia/bin/estia doctor` (read-only, as her; `scripts/doctor.sh` before her own user). Each ✗ line names the fix; it exits with 1 when a line is ✗. ○ means a part is set up but not used yet |
| Run in the foreground | `scripts/run.sh` in a terminal (Ctrl-C stops it) |
| Health check | `/usr/bin/sudo /usr/local/estia/bin/estia check` (`scripts/check.sh` before her own user), run on the Mac (for example over SSH). It exits with 1 when the bridge or signal-cli is not running |
| Logs | `/usr/bin/sudo /usr/local/estia/bin/estia logs [bridge\|voice\|launchd] [-n N]` (the last 50 lines of `bridge.log` by default). The bridge and the voice service keep their logs small themselves. To search it: `/usr/bin/sudo /usr/local/estia/bin/estia logs -n 5000 \| grep <word>` (her logs are hers; before her own user, `grep <word> ~/Library/Logs/local-assistant/bridge.log`). An AI helping you cannot read them: run `/usr/bin/sudo /usr/local/estia/bin/estia logs bridge -n 40` in your own Terminal and share what it prints (only what happened and when, never message text) |
| Restart the daemon | `/usr/bin/sudo launchctl kickstart -k system/com.local-assistant.bridge` (in her own user also `/usr/bin/sudo /usr/local/estia/bin/estia restart`) |
| Her own user | `scripts/migrate-to-own-user.sh --dry-run`, then `scripts/install-daemon.sh` (2.7). Back within 7 days, with what she saved since: `/usr/bin/sudo /usr/local/estia/bin/estia rollback` (`scripts/migrate-to-own-user.sh --rollback` runs it); then `/usr/bin/sudo /usr/local/estia/bin/estia remove-old` deletes the held folders, and the earlier copies of hers kept aside in `/var/estia/.before-<time>` (each named; also without a move on record). Her config: `/usr/bin/sudo -u _estia -e /var/estia/.config/local-assistant/config.toml`, then `/usr/bin/sudo /usr/local/estia/bin/estia restart`. A root-run tool changed in a release: `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools` (shows the diff, asks) |
| Security step | `scripts/harden.sh` (`--dry-run` first): SSH keys only, rules that let admin commands skip your password, her file modes, Registration Lock, each on your yes (2.10). The nuclear option, `scripts/harden.sh --nuclear`, adds the firewall, stealth mode, python3 and ruby off the firewall's list and the keychain lock: it may stop other services on this Mac (2.10) |
| Answers paused: a cloud model | A note "Answers are paused: Ollama lists the model … as a cloud model …": her model's name in Ollama points to a model on Ollama's servers. `/usr/bin/sudo /usr/local/estia/bin/estia doctor` shows ✗ on its Model line with the fix (a model downloaded to the Mac, 2.2, then a restart). A note says when answers are back |
| Voice service | `curl -s http://127.0.0.1:8150/health`. Restart: `/usr/bin/sudo launchctl kickstart -k system/com.local-assistant.voice`. After changes in `tools/voice`, run `scripts/install-voice-daemon.sh`. After an install, a move or a macOS update the first start takes up to about 15 minutes (Neural Engine compile); with `[calls]` she texts once it is ready |
| Upgrade | `git pull && uv sync --frozen && (cd tools/tapo && uv sync --frozen) && (cd tools/ical && uv sync --frozen)`, then restart (an install that runs as you). In her own user: `git pull` in your checkout, then `/usr/bin/sudo /usr/local/estia/bin/estia-deploy`: a new release owned by root, switched, restarted and checked within 120 s, switched back on a failure (2.7). After changes in `tools/voice`, `scripts/install-voice-daemon.sh` |
| Upgrade signal-cli | Homebrew's signal-cli about monthly (`brew upgrade signal-cli`); releases older than three months may stop working, so read the release notes first. Calls need the JVM build to match Homebrew's version: `tools/call-tunnel/install-signal-cli-jvm.sh <new version>`, point `[calls] signal_cli` at the new folder, pin with `brew unpin signal-cli && brew upgrade signal-cli && brew pin signal-cli`, restart, and make a test call. Undo: the old folder and the old config line still work with the old version. In her own user, `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` then copies Homebrew's new signal-cli (and the new JVM build from `<repo>/vendor`) into her release |
| Calls | Call the bot from Signal (phone or linked device). `/usr/bin/sudo /usr/local/estia/bin/estia doctor` shows Calls ✓ and `/usr/bin/sudo /usr/local/estia/bin/estia check` shows `signal-cli: running`; "How's the Mac?" covers calls too. A call that never answers: `/usr/bin/sudo /usr/local/estia/bin/estia logs -n 5000 \| grep -i call`. A call that rings out with the note "I can't take calls yet: my voice is still loading …" means her voice is still loading: wait for "My voice is ready: you can call me now." (3, Calls, "While her voice loads"); `/usr/bin/sudo /usr/local/estia/bin/estia logs -n 5000 \| grep "voice ready"` shows when it went. What stopped her, per call: `/usr/bin/sudo /usr/local/estia/bin/estia logs -n 5000 \| grep "calls: barge-in"` (the counts per rule; see 3, Calls) |
| Safety number changed | Replies stop and doctor and `/status` show ✗. Never trust a new key blindly: a SIM swap of your number looks exactly like this. On the Mac, `/usr/bin/sudo /usr/local/estia/bin/estia signal listIdentities -n <your ACI>` (it stops the bridge, runs her signal-cli as her, then starts her again; before her own user, stop the bridge and run `signal-cli -Djava.net.preferIPv4Stack=true -a <bot-number> listIdentities -n <your ACI>`), compare its safety number with the one your phone shows (Signal, the chat with Estia, View safety number), and only if they match: `/usr/bin/sudo /usr/local/estia/bin/estia signal trust -v <safety number> <your ACI>`. Turn on Registration Lock (a Signal PIN) for your own account and Estia's (2.3, 2.10), so a SIM swap alone cannot take either over. With `[calls]` on, the wrapper uses her JVM build (the flag goes in `JAVA_OPTS`) |
| Reminders | `/status` and `/usr/bin/sudo /usr/local/estia/bin/estia doctor` show "N open · next <time> (call) · N failed" (times and counts only, never the text); doctor warns on one that failed in the last 7 days or is stuck sending. `/reminders` lists what was missed or failed, with the reason; `/usr/bin/sudo /usr/local/estia/bin/estia logs -n 5000 \| grep reminder` has ids, times and outcomes |
| Messages she starts | `/status` shows "Initiative: on · last 4 weeks: N sent · N answered within a day · /quiet N times · improvised note: this week" and the last skip per kind with its reason; doctor shows the kinds that are on (and says when the calendar notes are off for lack of `[calendar]`) and warns when one could not be sent. `/usr/bin/sudo /usr/local/estia/bin/estia logs -n 5000 \| grep initiative` has kinds, ids and reasons, never text. `--draft` (section 4) prints one without sending |
| Language practice | `/babble` in Signal says which session runs. `/usr/bin/sudo /usr/local/estia/bin/estia logs -n 5000 \| grep practice` (before her own user, `grep practice ~/Library/Logs/local-assistant/bridge.log`): starts, ends with the reason (off, idle, call, switch), when the recap is due, and how many points were kept and dropped per reason; never text. A recap held for the morning, or while she is locked (`/lock`: it waits for `/unlock`), waits in `babble:session` and still goes out after a restart. The first start with practice moves the database to schema v8 (one column, `conversations.kind`), and the first start with the devices to v9 (`known_devices`, `messages.source_device`): copy `assistant.db` while the bridge is stopped before such a start |
| The laptop cannot reach a program on the Mac (a page you open from your laptop, a file share) | The firewall is on (`scripts/harden.sh --nuclear`, an earlier security step, or your own choice) and does not let that program through (no screen to click "Allow"). `/usr/bin/sudo /usr/local/estia/bin/estia doctor` names it in a "! Firewall: … cannot be reached from other machines" line with the two `socketfilterfw` commands for a program you started, each with the program's full path (2.10). After a Homebrew update the path changes: run doctor again |
| A new device is held | "A message came from a new linked device N and was held." Yours: send `/devices ok N` from your phone. Not yours: remove it in Signal → Settings → Linked devices, then turn on Registration Lock for your number |
| The move failed | Her old setup comes back (on a first install nothing is installed). Keep the ✗ line: it names what failed. Fix that, run `scripts/migrate-to-own-user.sh --dry-run` again, then `scripts/install-daemon.sh` |
| Backup | Not set up yet: the database and her Signal account (`/var/estia/Library/Application Support/local-assistant`, `/var/estia/.local/share/signal-cli`) have no backup |

## 8. Security

This section is the threat model. [SECURITY.md](SECURITY.md) explains how to report a problem.

- Only allowed ACIs reach Gemma, get receipts or receive messages. `SignalClient` refuses any other recipient in code.
- The model always runs on the Mac (2.2): a `model` whose tag ends in `-cloud` or `:cloud` is a config error, and the
  bridge reads the model's entry in Ollama's `/api/tags` at the start, every 5 minutes and before every chat, with or
  without `model_digest`. An entry with `remote_host` or `remote_model` (a cloud model) pauses every
  model call: a message gets the code's note, never an answer from the model, and doctor and `/status` show it. An entry
  that cannot be read changes nothing; the name check still holds. With `model_digest`, any other digest pauses it too.
- Setup's secrets: the Signal captcha link, the SMS code and a Registration Lock PIN are typed hidden and go to
  signal-cli on its input as one JSON-RPC request each, never as arguments (`ps` shows every program's arguments to
  every user of the Mac) and never printed: the commands on screen show placeholders, and an error that repeats a secret
  shows its placeholder. Setup runs Homebrew with `HOMEBREW_NO_ANALYTICS=1`; brew commands you type keep your own
  setting (`brew analytics off` turns it off).
- The Mac around her (2.10): the security step sets SSH to keys only, removes rules that let admin commands skip your
  password, makes her files private and sets her Registration Lock, each on your yes. `scripts/harden.sh --nuclear`
  adds the firewall, stealth mode, python3 and ruby off the firewall's list and the keychain lock, after saying they
  may stop other services on this Mac. Without it they are one neutral line, in harden and in doctor; nothing turns off
  what a Mac already has.
- Messages she starts herself: the code finds every reason and checks every limit before the model is asked; the model
  writes in a call of its own with no tools and no outside text (only the user's own messages, profile facts from chat
  and the moment), and the code checks its text before it is sent (claims, secrets, links, numbers, mail and calendar
  words, guilt, chat tokens; the words that ask for a code or a password, and reminder and call claims, in all 11
  languages; a link on any ending); a text that fails is not sent. Sent at most once, only to allowed accounts. The calendar
  notes keep event ids and times in memory only, never titles. Logs and the log table hold kinds, days and reason words.
- No shell. The project tools are read-only and fixed in code: git runs as `/usr/bin/git -c safe.directory=* … -C <project> …`
  with fixed arguments (no shell, no pager, `--no-optional-locks`, fsmonitor off on every call; `safe.directory` on the
  command line only, so her user can read your repositories; `git status` with `--ignore-submodules=all`, since a
  submodule's own git config is not checked). A repo whose local git config has a key outside a
  safe list (`core.*` basics, `remote.*`, `branch.*`, `user.*`) is skipped, since such keys can make git run programs. They
  are an extra, off without `[projects]`. Only files git tracks are read, at the project's level: the README at every
  level; at `docs` also files named like docs (`README*`, `todo*`, `setup_*` as .md, .txt, .rst or without extension, and
  `docs/*.md`); at `code` every tracked text file. `AGENTS.md` and `CLAUDE.md` open only after a threat scan finds no
  planted instructions (below `code` only inside `project_status`). Names like `.env*`, `*.env`,
  `*key*`, `*secret*`, `*credential*`, `*token*`, `*passw*`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `id_dsa*`, `*.pem`, `*.p12`,
  `*.pfx`, `*.p8`, `*.ppk`, `*.jks`, `*.kdbx`, `*.gpg`, `*.age`, `*.ovpn`, `.netrc*`, `.npmrc*`, `.pypirc*`, `.pgpass*`, `.dockercfg`,
  `.s3cfg`, `kubeconfig*`, `*.tfstate*`, `*.tfvars*`, `*mnemonic*`, `*seed*phrase*` and `*wallet*` are refused, in any folder of the
  path. Symlinks are never followed; each path must resolve inside the project folder; files are UTF-8 text without a NUL byte, of
  at most 200 KB.
- Health checks run fixed commands without admin rights (`/bin/launchctl print`, `/usr/sbin/sysctl -n …`, local HTTP to Ollama
  and to any configured extra check), each with a timeout.
- Photos: only from the allowed sender (strangers' and unsupported files are deleted on arrival). The type comes from the file's
  first bytes. The real file size is checked before download (at most 20 MB), and images over 100 megapixels are refused before
  `sips`. The file name from Signal must be a plain name. Files live only until answered. Other downloads (link previews, quote
  thumbnails, avatars, edited messages) and leftovers of interrupted conversions are swept once they are 10 minutes old. Image
  data is never logged.
- Mail: Microsoft Graph with delegated `Mail.Read` (plus `User.Read` and `offline_access`) only, so the tokens cannot send, move
  or delete mail. A mailbox whose Outlook calendar is configured may also grant `Calendars.Read`, still read-only; mail's own
  token request asks for the mail permissions only. Only `https://graph.microsoft.com/v1.0/` is called. Refresh tokens are kept in
  `~/Library/Application Support/local-assistant/mail-tokens.json` (mode 0600); access tokens stay in memory. Mail bodies are
  fetched live and never stored (the tool log keeps a stub instead of a mail tool's result). In an answer that read mail, nothing
  is saved to or removed from the profile (refused in code). A mail tool gives up after 45 seconds, so a throttled Graph cannot
  hold the bridge. Redirects are not followed, so the token stays with Graph. Summaries pass the same checks as profile facts.
  Logs hold counts and mailbox names only. The optional AgentJev pre-filter receives the sender, subject and body of each mail
  it checks, over localhost only; it never sends them anywhere else.
- Voice: only the allowed user's audio is transcribed; strangers' files are deleted on arrival. Audio that Signal does not mark as a
  recorded voice note (an audio file shared from another app) is someone else's voice: Estia can summarise it, but it never
  teaches her facts, never counts as your yes and cannot switch a device without your yes. A voice note forwarded inside Signal
  may keep its mark. The type comes from the file's first bytes. The real size is checked before download (at most 10 MB, at
  most 3 minutes in the service). Audio and spoken answers (mode 0600, folder 0700) live only until used. The voice service listens
  on `127.0.0.1:8150` only, refuses any request with an Origin header and any non-local Host (browsers, DNS rebinding), caps bodies
  and slow requests, and logs sizes and timings only. WhisperKit (from 1.1.0) and FluidAudio (exactly 0.15.8) are pinned.
  Whisper's model files and the speech detector's are fixed to one version on Hugging Face and checked against their
  fingerprints (sha256) at every start; a model with a changed or extra file is never loaded (Whisper's folder is
  then set aside as `<folder>.refused-<time>`, only the latest kept, and downloaded again at that version; changed
  again, listening stays off), and Whisper starts with no network once downloaded. The speaking models (Kokoro, Supertonic-3) are not fixed: they come from Hugging Face
  as published.
- Calendar: a Google calendar's secret iCal address is a password. It is kept in `calendar.env` (mode 0600, one key per
  calendar) and read only by `tools/ical/helper.py`, which fetches only `https://calendar.google.com/calendar/ical/…`, follows
  no redirects and passes on only its own error codes. The bridge runs the helper with fixed arguments (the calendar's name
  checked against the config's rule) and a clean environment, one process per calendar, and never holds an address. Expansion
  is bounded (repeat rules finer than daily, or never advancing, are dropped; each repeating event gets 0.5 s and 400
  occurrences; 15 s in all). An Outlook calendar is read with delegated `Calendars.Read` only, in its own access-token
  request (never mail's token), from the mailbox's default calendar: no bodies, no attendees, no organizer's address; a
  place's street address is dropped (only its display name is kept). Its Graph waits at most 3 s for one retry and gives up
  after 20 s, so a throttled Microsoft cannot hold the digest. Event text is data (cleaned, capped, links removed from
  places), and a calendar answer counts as outside text.
- Lights and plugs: Gemma names a device, never an address. Only configured devices at private addresses are used. The bridge runs
  `tools/tapo/.venv/bin/python -I tools/tapo/helper.py <action> <host> …` with fixed arguments (no shell, 15 s timeout, JSON back),
  and never loads python-kasa or the TP-Link login itself. "Ask first" and "never off" are enforced in code, and so is the yes for
  requests made after reading outside text, and for requests in a message that is not about devices (no device word such as light,
  lamp, plug, heater, on, off or dim, and no word of a device's name). A line planted in an earlier page cannot act on a later
  "thanks!". Only the user's own yes counts. The helper runs in isolated mode with a clean environment (no API keys of the bridge's
  shell) and passes on only its own fixed refusal texts. Logs hold device action and result only.
- Web search (with `[web]`): queries go only to `ollama.com` and `api.search.brave.com` (no redirects; Ollama 8 s, Brave 5 s; on a call
  5 s and 3 s). At most 2 searches per answer (1 on a call), and at most 400 characters each. Brave's top page is read only through the
  guarded fetcher (public addresses, 2 MB, 6 s), never on a call. Results are not cached. A query with an email address, an IBAN or a
  long number (phone, account) is refused. The keys live only in `web.env` (mode 0600; a file others can read is refused) and in the
  bridge's memory, never in logs. Results are data: cleaned (hidden characters, chat tokens, markdown links), odd links dropped, capped
  (5 results, 6,000 characters), never stored (the tool log keeps a stub, as Brave's terms ask). Their links reach the user only in a
  code-written "Sources" note; links in Estia's reply to a search are removed. An answer that searched refuses `remember` and
  `forget`, and its device requests wait for the user's yes. An answer that read mail, the calendar or your projects cannot search,
  so private text never goes into a query. The queries themselves are the user's questions, and they go to Brave (whose terms allow it
  to use query data) or to Ollama.
- Places and routes (with `[maps]`): only `places.googleapis.com` and `routes.googleapis.com`, with no redirects, 8 s (5 s on a call),
  at most 2 requests per answer (1 on a call), and a daily cap per API (default 30). The key is kept only in `web.env` (mode 0600). What
  goes to Google: what you look for, place names and addresses, a rounded area of about 1 km for "near me", and, for a route from
  "here", the location you sent. The `region` setting, if set, goes with each request. The home address stays in the
  config: the state table keeps the rounded centre and the address's tag (HMAC-SHA256 keyed with the Maps key, which is
  never in the database), never the address, and a Directions link to or from home names the rounded centre; at
  start, rows and Directions notes an earlier version wrote with the address are rewritten or deleted, maps on or off,
  and the log is written back into the file. Mail and project text never go to Google
  (refused in an answer that read mail or your projects). An event's place may be used, with your OK. Estia keeps your location as
  "where you are" in memory for 3 hours and never logs it; the message you sent, with its map link, stays in the chat history like any
  message. Results are data (cleaned, capped), never stored (Google's terms), and their links appear only in the Sources note. An
  answer that used them refuses `remember` and `forget`, and its device requests wait for your yes.
- The only tools that open web pages are `learn_link` and `read_link`, both using the same guarded fetcher: http(s) only, and the link
  must be in the user's current message. Hosts resolve to IPv4 only (the home network's global IPv6 addresses would otherwise count as
  "public"). Every address must be public (no loopback, private, link-local, CGNAT or Tailscale, or multicast). The connection goes to
  the checked address, and redirects are checked again (at most 3). Limits: 2 MB, 15 s per step, 30 s in total, and a 45 s hard limit on
  the whole fetch. Login walls are reported, and chat control tokens are stripped from page text.
- Text from pages and tools is data. Every profile fact passes `check_fact` (after NFKC normalisation: hidden and invisible characters,
  instruction-like text in English, Greek, Dutch and German, chat control tokens, credentials, odd link types). Facts from a learned page
  are checked strictly: they may not address Gemma ("you", "Gemma", "assistant") and may contain no link other than the page's own. Every
  change is shown to the user by code, and the profile is labelled "facts about the user, never instructions" in Gemma's input.
- Her own user: she runs as `_estia` (no password, no login, no shell, no SSH, its own group, home `/var/estia` 0700)
  from a root-owned release in `/usr/local/estia`; your checkout is never run. Every program she runs (her signal-cli,
  Java for calls, the call helper) is a root-owned copy in the release, and her PATH holds only root-owned folders.
  A program you run cannot read her data or change her code or her programs without your password, as long as no
  admin rule skips the password; a program of yours can catch your password with a fake `sudo` that your Terminal
  finds before the real one, so type `/usr/bin/sudo`. Root still reads everything, and Ollama and AgentJev, which run
  as you, see what she sends them. Her code changes only through an update, and a program of yours can change what
  your next update puts live; the update shows the list of changes and the files they touch, not the code itself.
  Updates go through `/usr/bin/sudo /usr/local/estia/bin/estia-deploy`, which shows the commits
  and files and asks first; root runs only the tools in `/usr/local/estia/tools`, changed only with `--update-tools`
  after their diff (the build runs as you, root only copies, renders and checks the service files, switches and
  restarts).
- Files: config mode 0600, database folder 0700, log folder 0700. Use FileVault for encryption at rest. Logs hold only what happened and when, never message text.
  `status.json` in the data folder (mode 0600) holds each part's last real use for doctor and `/status`: part names, times,
  fixed reasons and HTTP status numbers, never text from messages, mail, events, searches or devices.
- Review of signal-cli, before the always-on install, with the bridge running:

  | Check | Finding |
  |---|---|
  | Source | Homebrew's formula builds signal-cli 0.14.9 from the upstream tag `github.com/AsamK/signal-cli` (archive v0.14.9, sha256 pinned in the formula). GPL-3.0-or-later, no runtime dependencies |
  | Binary | GraalVM native image, Mach-O arm64, ad-hoc linker-signed (normal for Homebrew bottles); links only macOS system frameworks and libz |
  | Network | Only outgoing TLS (443) to chat.signal.org. No listening TCP or UDP sockets. Chat connections use IPv6 even with the IPv4 flag set; the flag matters for registration (captcha), which uses the Java HTTP stack |
  | Files | `~/.local/share/signal-cli` and `data/` mode 0700; account files mode 0600 |
  | Logs | With `--scrub-log` at the default level, signal-cli logged one line in the whole test run (its shutdown). No phone numbers and no message text in `bridge.log`. A stranger's ACI is logged once per ignored message; that is how you find your own (2.4) |
  | Strangers | signal-cli sends Signal's automatic delivery receipts itself (protocol level), so a stranger can tell the number is registered. The bridge sends them no read receipts, typing indicators or replies |
  | Log file mode | `bridge.log` must be mode 0600. Under launchd the umask is 077, and the bridge rotates its own log |

- Calls: only allowed ACIs are answered. Strangers' calls are ignored, never rejected (a reject would send them something), and
  accept and hang-up refuse other accounts in code. Known gap: signal-cli starts the call helper for every incoming call before the
  bridge decides. The build uses Signal's relays only, so callers never learn the Mac's IP addresses. Call audio lives only in memory.
  Transcripts join the chat history like voice notes. Logs hold only what happened and when (never numbers, ACIs or words). The audio socket folder
  is private (mode 0700), and the bridge refuses a socket path outside it. Lights and plugs on a call follow the text rules. A spoken yes
  counts only after Estia has finished asking, never one said over her question.
- Music (Spotify): the official Web API, with your own app (Client ID, PKCE, no secret). The refresh token is kept in
  `spotify-tokens.json` (mode 0600); access tokens stay in memory. Only `api.spotify.com` and `accounts.spotify.com` are called, and the
  Bose wake-up talks only to the private addresses in `[spotify.bose]`. Text only, because Spotify's policy forbids voice control: call and
  voice-note turns are refused in code. After outside text in the same answer (mail, calendar, a page), music may stop or get quieter, but
  never start or get louder. Nothing from Spotify is stored (the tool log keeps a stub) or learned, and the volume never goes above the cap.
