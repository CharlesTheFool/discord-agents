# Host mode: what the probes showed

Host mode was planned as a third workshop setting beside Container and Off:
a scope's commands would run on the operator's own Windows PC, for work a
Linux VM can't do (Blender, Unity, the operator's own tools), each scope
under its own restricted local account. It is parked (2026-11-01). Two probes
on Windows 11 Home ran before any of it was built; this note keeps what they
found.

## What both probes share

Two throwaway local accounts, plain members of Users. Each got a workspace
folder only it, the operator, administrators and SYSTEM can open, and an
outbound firewall rule blocking every connection the account makes. The
bot's egress proxy listened on loopback with a per-scope credential, as it
does for the microVMs. A battery of checks ran as each account.

## Probe 1: started from the operator's session

The accounts were launched with `CreateProcessWithLogonW` from the
operator's own, non-elevated session.

| Check | Result |
|---|---|
| The bot's `.env` (prod and dev), a bot database, the app config, the sandbox runtime's home, the operator's Documents | denied |
| The other account's workspace | denied, listing too |
| Direct TCP to the internet; the LAN gateway | blocked |
| HTTPS to an allowed host through the proxy | allowed by the proxy |
| A listener on loopback | reachable |
| The operator's clipboard | **read a canary the probe had put there** |
| Top-level windows on the operator's desktop | **26 and 27 enumerable** |
| The input desktop | **reachable (SendInput would work)** |

Files and network held; the desktop didn't. On Windows the UI boundary is
the window station and desktop, not the account, so a process on the
operator's desktop shares the operator's clipboard and can type into the
operator's own terminal. A private window station and desktop for the
account failed to start (`CreateDesktop`).

## Probe 2: session 0

The same accounts, given "Log on as a batch job" and an on-demand scheduled
task each that runs whether or not anyone is logged on. Such a task runs in
session 0, the non-interactive session Windows services use. The operator
starts the tasks without elevation.

| Check | Result |
|---|---|
| Session | 0, `UserInteractive` false |
| Every file and network check from probe 1 | unchanged |
| HTTPS to an allowed host through the proxy | 200 |
| The clipboard canary | not visible |
| Enumerable windows | 0 |
| The input desktop | not reachable |

## What this settles

Per-account file permissions and a per-account firewall rule work on Windows
11 Home, and session 0 takes the operator's desktop out of reach. Loopback
stays open to every account, so each local listener the bot runs has to
authenticate; the dashboard API and the egress proxy already do. Host mode
would be background work only: commands, scripts and headless apps
(`blender -b`, Unity in batch mode, ffmpeg), never a window on the
operator's screen. Windowed apps need a boundary these probes didn't have,
such as Windows Sandbox (Pro and up).

The piece that runs a scope's work inside session 0 and hands the results
back to the bot wasn't built. The microVM workshop is the only backend.
