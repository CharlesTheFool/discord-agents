# Deployment: Development + Production Side by Side

This repo supports a two-install workflow: a **development workspace** where
you build and test, and one or more **production installs** that run real bots
and update only when you cut a release.

## The contract that makes it work

- **`Beta`** (or any working branch) is development. It may be broken at any
  moment.
- **`main` only advances at releases** — fast-forwarded from the working
  branch, tagged (`v0.6.0`, ...), and pushed together with a GitHub release.
- **Everything stateful is gitignored**: `.env`, `bots/*.yaml`,
  `persistence/`, `memories/`, `logs/`. A `git pull` in production can never
  touch bot data, config, or memories.
- **Data migrations are automatic.** Databases and persisted conversation
  states migrate themselves on first load after an update. Keeping this true
  is a standing constraint on development: a release must never require
  manual migration steps.

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

Linux is the same flow; see the README's systemd/PM2/Docker sections for
supervision.

### One identity per install

Never let two installs share a Discord bot token — both instances would
respond to everything. Production identities belong to production; dev tests
run under separate bot applications. (A token's first base64 segment decodes
to its bot ID if you need to check which identity a token belongs to.)

### Migrating an existing bot into production

Copy from the old workspace into the new install (with all bots stopped):

- `bots/<name>.yaml` — review test-era settings: `allowed_channels`
  restrictions, `allow_bot_interactions`, `mcp.enabled`
- `memories/<name>/` — profiles, channel notes, episode archives
- `persistence/<name>_*` — databases (include `-wal`/`-shm` companions; skip
  `_running.flag`)
- `persistence/attachments/` — local attachment files

`deployment_tool.py export` / `import` automates this as a zip roundtrip if
you prefer.

## Updating production

When a new release lands:

```powershell
git pull                                          # main = releases only
.\.venv\Scripts\pip install -r requirements.txt   # in case deps changed
# restart the bot
```

Rollback is one command and never touches data:

```powershell
git checkout v0.6.0    # or any prior tag; `git checkout main` to return
```

## Desktop app: automatic updates (v0.9.1+)

The packaged Windows app (`app/`) updates itself on two fronts:

- **The launcher** updates via GitHub Releases (`electron-updater`): it checks
  on launch and every 6 hours, downloads a new installer in the background, and
  offers a one-click restart. Bots, config, and memories are never touched.
- **The framework** catches up on its own. Because `main` only advances at
  releases, the app fast-forwards its git checkout to `origin/main` *before*
  starting the supervisor daemon, so the daemon always boots on the latest
  release. `pip install` runs only when `requirements.txt` changed.

The framework updater is deliberately conservative — it acts only on a clean
git checkout sitting on `main`. A dev workspace (any other branch) or an
install with local edits is left untouched. This means a production install
gets new releases with zero manual steps, while the manual `git pull` flow
above still works and stays the fallback.

`app-config.json` (beside the executable) controls it:

```json
{
  "root": "C:\\path\\to\\prod",
  "python": "C:\\path\\to\\prod\\.venv\\Scripts\\python.exe",
  "autoUpdateFramework": true,
  "frameworkBranch": "main"
}
```

Set `"python"` to the install's venv interpreter so the framework's dependency
installs land in the right place. Set `"autoUpdateFramework": false` to pin an
install to whatever it has. Forward-only: a pre-0.9.1 install has no updater
and must be reinstalled once to reach 0.9.1; every release after is automatic.

## Auto-start on Windows (optional)

Run the bot at logon via Task Scheduler:

```powershell
schtasks /Create /TN "discord-agent-slh01" /SC ONLOGON /TR `
  "C:\path\to\prod\.venv\Scripts\python.exe C:\path\to\prod\bot_manager.py spawn <name>"
```

Or use [NSSM](https://nssm.cc/) to run it as a proper Windows service with
restart-on-crash.
