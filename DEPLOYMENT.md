# Deployment: development and production side by side

The repository supports a two-install workflow: a **development workspace**
where you build and test, and one or more **production installs** that run
real bots and change only when a release is cut.

## The contract

- **`Beta`** (or any working branch) is development. It may be broken at any
  moment.
- **`main` advances only at releases.** It is fast-forwarded from the
  working branch, tagged (`v0.13.0`, ...) and pushed together with a GitHub
  release.
- **Everything stateful is gitignored:** `.env`, `bots/*.yaml`,
  `persistence/`, `memories/`, `logs/`. A `git pull` in production can never
  touch bot data, config or memories.
- **Data migrations are automatic.** Databases and persisted conversation
  states migrate themselves on first load after an update. This is a
  standing constraint on development: a release must never require a manual
  migration step.

## Setting up a production install

```powershell
git clone https://github.com/CharlesTheFool/discord-agents.git C:\path\to\prod
cd C:\path\to\prod                     # clones onto main = latest release
python -m venv .venv                   # own venv: dev dependency churn can't break prod
.\.venv\Scripts\pip install -r requirements.txt

# .env with this install's secrets (bot token + ANTHROPIC_API_KEY)
# bots/<name>.yaml with this install's bot config
.\.venv\Scripts\python.exe bot_manager.py spawn <name>
```

Linux is the same flow; the README's systemd, PM2 and Docker sections cover
supervision.

## The workshop needs hardware virtualization (0.14)

Each bot runs its commands in microVMs (see [SECURITY.md](SECURITY.md)), so
the host must be able to run a VM. Before a bot logs in to Discord it
test-boots one; if that fails, the bot doesn't start, the dashboard shows
**Can't start** with the reason, and **Try again** reruns the check. What
the host needs:

- **Windows:** virtualization switched on in the PC's firmware (BIOS/UEFI;
  often called Intel VT-x, AMD-V or SVM), and the **Windows Hypervisor
  Platform** feature. The desktop app offers to turn the feature on (an
  administrator prompt, then a restart). By hand, from an elevated prompt:
  `dism /online /enable-feature /featurename:HypervisorPlatform /all`, then
  restart.
- **Linux:** `/dev/kvm` present, and the bot's user in the `kvm` group. On
  a VPS that means a plan with nested virtualization.
- **Docker:** pass the device through: `docker run --device /dev/kvm ...`.
- **macOS (Apple Silicon):** nothing to install.

The first start also builds the workshop's template: a Debian image with
Python, Node, git, ffmpeg and the document libraries, downloaded through the
bot's own egress proxy. It takes a minute or two and happens once per
install; every scope's sandbox starts from it in seconds. The runtime's
files live in `~/.discord-agents/msb`. Budget host disk: each scope can
grow to 20 GiB (a 10 GiB workspace and a 10 GiB root filesystem), and a
bot refuses new sandbox work when the drive has less than 5 GiB free.

If a host can't run VMs at all, `workshop.enabled: false` in the bot's
config is the last resort: the bot starts with no execution capability,
and the dashboard shows "workshop off".

### One identity per install

Never let two installs share a Discord bot token; both instances would
answer everything. Production identities belong to production, and dev
tests run under separate bot applications. A token's first base64 segment
decodes to its bot id if you need to check which identity a token belongs
to.

### Moving an existing bot into production

With all bots stopped, copy from the old workspace into the new install:

- `bots/<name>.yaml`. Review test-era settings: `allowed_channels`,
  `allow_bot_interactions`, `mcp.enabled`.
- `memories/<name>/`: profiles, channel notes, episode archives.
- `persistence/<name>_*`: the databases, including their `-wal` and `-shm`
  companions. Skip `_running.flag`.
- `persistence/attachments/`: local attachment files.

`deployment_tool.py export` and `import` do the same as a zip round trip.

## Updating production

When a release lands:

```powershell
git pull                                          # main = releases only
.\.venv\Scripts\pip install -r requirements.txt   # in case dependencies changed
# restart the bot
```

Dependencies are pinned to exact versions in `requirements.txt`, so a
production venv runs what the release was tested with.

## Rolling back

A checkout never touches data, so rolling back is a checkout, with two
precautions:

- **Below 0.14.0, restore an export.** 0.14.0 stores attachments in
  conversation state by the framework's own id and converts older states
  on load; 0.13.x can't read the converted form. Take a
  `deployment_tool.py export` before updating to 0.14, and restore it if
  you roll back below 0.14.0.
- **Below 0.13.0, strip the model stamps.** Since 0.13.0 every saved
  assistant message carries a `model` stamp; older releases send it to the
  API, which refuses the whole request.

```powershell
# stop the bot first
python scripts/strip_model_stamps.py persistence/<bot>_conversation_states.db   # only if the target is older than 0.13.0
git checkout v0.12.5                                                            # or any prior tag
# restart the bot; `git checkout main` returns to the latest release
```

The desktop app's framework updater acts only when HEAD is on `main`, so a
checked-out tag stays pinned until you return to `main`.

## Desktop app: automatic updates (0.9.1 and later)

The packaged Windows app (`app/`) updates on two fronts.

**The launcher** updates through GitHub Releases (`electron-updater`). It
checks on launch and every six hours, downloads a new installer in the
background, and offers a one-click restart. Bots, config and memories are
never touched.

**The framework** catches up on its own. Because `main` advances only at
releases, the app fast-forwards its git checkout to `origin/main` before
starting the supervisor daemon, so the daemon always boots on the latest
release. `pip install` runs whenever `requirements.txt` differs from the
last install that succeeded, so a failed install is retried on the next
launch. The updater is deliberately conservative: it acts only on a git
checkout whose HEAD is on `main` with a clean tracked tree. A dev workspace
on another branch, or an install with local edits, is left alone; since
0.14.0 the fleet dashboard says when updates are paused and names the
locally changed files. The manual `git pull` above remains the fallback. The self-contained
installer carries the framework inside its bundle and updates it with the
launcher instead.

`app-config.json`, beside the executable, points the launcher at a
git-checkout install:

```json
{
  "root": "C:\\path\\to\\prod",
  "python": "C:\\path\\to\\prod\\.venv\\Scripts\\python.exe",
  "autoUpdateFramework": true,
  "frameworkBranch": "main"
}
```

Set `"python"` to the install's venv interpreter so dependency installs land
in the right place. Set `"autoUpdateFramework": false` to pin an install to
what it has. This is forward-only: a pre-0.9.1 install has no updater and
must be reinstalled once to reach 0.9.1.

## Auto-start on Windows (optional)

Run the bot at logon through Task Scheduler:

```powershell
schtasks /Create /TN "discord-agent-<name>" /SC ONLOGON /TR `
  "C:\path\to\prod\.venv\Scripts\python.exe C:\path\to\prod\bot_manager.py spawn <name>"
```

Or use [NSSM](https://nssm.cc/) to run it as a Windows service with
restart-on-crash.
