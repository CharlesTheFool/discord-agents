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

## Rolling back

A checkout never touches data, so rolling back is a checkout, with one
precaution. Since 0.13.0 every saved assistant message carries a `model`
stamp; releases older than 0.13.0 do not know the field and send it to the
API, which refuses the whole request. Strip the stamps before going below
0.13.0.

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
release. `pip install` runs only when `requirements.txt` changed in the
incoming range. The updater is deliberately conservative: it acts only on a
git checkout whose HEAD is on `main` with a clean tracked tree. A dev
workspace on another branch, or an install with local edits, is left alone,
and the manual `git pull` above remains the fallback. The self-contained
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
