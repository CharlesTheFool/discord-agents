# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.13.x  | Yes       |
| < 0.13  | No        |

Beta versions receive security patches on a best-effort basis. A formal
support policy arrives with 1.0.

## Reporting a vulnerability

Do not report security vulnerabilities through public GitHub issues. Use
GitHub Security Advisories: open the repository's **Security** tab, click
**Report a vulnerability**, and fill in the form.

Include what you can of the following: the type of vulnerability, the
affected component (Discord client, reactive engine, memory manager,
supervisor, and so on), affected versions, steps to reproduce, the impact
you believe it has, and a suggested fix if you have one.

What to expect: an acknowledgment within 48 hours, progress updates during
the investigation, and a patch for critical issues within 7 to 14 days.
Reporters are credited in the advisory unless they prefer not to be. A
90-day embargo is requested before public disclosure; advisories are
published for every confirmed vulnerability, and CVEs are requested for
significant ones.

## Threat model

Two populations can steer the model. Members of a server already see what
the bot sees there, and Discord itself is their exfiltration channel, so the
framework does not try to hide a server's content from its own members.
Outsiders reach the model through content it reads: fetched pages,
attachments, repository files. They cannot see the server, but the bot's
memory includes cross-server user profiles, so for them the prize is
exfiltration.

The binding design rule, in force since the 0.13 design: no safety property
may depend on the model declining something. The model is swappable from
0.13.0 on, and weaker models obey injections more readily, so every boundary
below is enforced in storage, SQL, or tool code. Where the model is still
the only guard, this document says so.

## What is enforced mechanically

### Vaults

`vaults: []` in the bot config lists channel or server IDs whose content
never leaves them. `VaultEnforcer` (`core/vaults.py`) is the one gate, and
every tool seam threads the current server and channel through it:

- message search and attachment listings exclude vaulted places in the SQL
  itself, with a vaulted channel's threads expanded into the exclusion;
- memory paths under a vaulted server or channel can be read or written only
  from inside it;
- repository saves are blocked from vaulted channels;
- global user-profile writes are refused from inside a vault, so what is
  learned there cannot land on a profile other servers read;
- since 0.13.1, every memory write from inside a vault must land in that
  vault's own tree. The innermost vault wins: a vaulted channel inside a
  vaulted server keeps its notes in the channel's tree, out of the rest of
  the server's reach.

Inside a vault the bot is fully itself. Threads inherit their parent's
vault. A context with no server (a DM) counts as outside every vault.

**Known residual:** `followups.json` stays writable from a vaulted channel.
Follow-ups are server-level by design, so a follow-up the bot schedules
inside a vault can be read from elsewhere in its server.

### DM privacy

Every DM is an implicit vault. Its memory lives under
`memories/<bot>/global/dms/<user_id>/`, readable only from that DM; from
anywhere else the path is refused outright. From a DM, memory writes stay
inside that DM's tree, with one exception: `/memory remember`, `forget` and
`feedback` thread a one-shot grant that opens exactly the caller's own
global profile for that turn, matched structurally rather than by suffix so
a crafted path cannot pass. DM messages and files never surface in search or
attachment access from any other context. This check runs before the vault
check and holds with zero vaults configured.

Both checks judge the path the memory tool will actually touch: since
0.13.1 every memory path is normalized (`..` resolved, Windows backslashes
read as separators) before any rule sees it. Before that, a crafted path
could pass a rule as written and resolve somewhere else.

### Cross-server coordination

`ask_prime` lets the bot in one server have its presence in another post a
question or announcement. Before the one bounded judgment call, mechanical
gates run: vault boundaries in both directions, a daily cap, and refusal
from DM context. API errors fail closed. Delivered messages are labelled
with their origin.

### The supervisor dashboard

The supervisor (and the desktop app built on it) listens on `127.0.0.1`
only, but loopback does not keep out the operator's own browser: any web
page you visit can send requests to `127.0.0.1`. Since 0.12.5 the dashboard
API defends against that:

- every `/api/` route requires a per-install token, generated on first run
  into `persistence/supervisor_token` and embedded in the pages the daemon
  serves (a cross-origin page cannot read it, and a custom header forces a
  CORS preflight the daemon never approves);
- requests whose `Host` is not `127.0.0.1:<port>` or `localhost:<port>` are
  refused, which defeats DNS rebinding;
- state-changing requests from a foreign `Origin` are refused;
- every response forbids framing, so a hostile page cannot overlay the
  dashboard and trick clicks.

The one open route is `GET /api/supervisor`, a read-only health check the
desktop launcher uses. Treat `supervisor_token` like a password: anything
holding it can drive the dashboard.

Two more properties of the daemon: every file route resolves its path
through one jailing builder that rejects absolute paths, drive letters and
traversal out of the root; and secrets set from the UI are write-only, since
the API reports whether a key is set and never returns its value.

### Discord-side controls

- Rate limits are per channel with preset tiers (`strict`, `moderate`,
  `permissive`, `unlimited`). After five consecutive ignored messages the
  bot goes quiet in that channel for half an hour; @mentions bypass the
  silence.
- Deleting a Discord message purges it from the bot's history, search index
  and attachment pipeline. The handlers use raw gateway events, so messages
  older than the running process are covered.
- Web search and fetch are capped per request.
- The `send_message` tool reports ground truth about what it posted (message
  id, which files attached), so the model cannot claim an attachment that
  never went out.

### Code execution

In 0.13.x, code execution and skills run in Anthropic's server-side
container. Model-written code never runs on your host, and the container is
air-gapped: no package installs, no network. Version 0.14 moves execution
into local microVMs with a default-deny egress policy; that design is in
[ROADMAP.md](ROADMAP.md) and nothing in it is shipped yet.

## What the model is still trusted with

- **Cross-server discretion.** Vaults are the only mechanical boundary
  between servers. Whether to mention that you know someone from another
  server, or to repeat what they said there, is governed by a mandatory
  prompt section, and a successful injection could make the bot cross that
  line. Put anything that must not travel in a vault.
- **External content.** Fetched pages, attachments and MCP tool results
  reach the model as text. Citations show where search results came from;
  review them before trusting a claim. Report patterns of injected behavior
  through a security advisory.

## Secrets and data at rest

API keys and Discord tokens live in environment variables, loaded from
`.env`, which is gitignored. Each bot config names its token variable
(`discord.token_env_var`), and `api.provider.api_key_env` can point a bot at
its own API key. The desktop app writes secrets to the data root's `.env`
through the write-only API above. Rotate a key immediately if it is
exposed, and regenerate a Discord token the same way; a token's first
base64 segment decodes to its bot id, which is the quickest way to confirm
which identity a token belongs to.

Memory is plaintext Markdown and message history is SQLite. Anyone with
filesystem access, including whoever operates the dashboard, can read
everything the bot knows. Treat the host accordingly: full-disk encryption
on laptops, a dedicated user for the bot process, and Discord's own channel
permissions to keep the bot out of conversations it has no business
remembering.

## Hardening checklist

- Run the bot as an unprivileged user, in a virtual environment or a
  container.
- Give the Discord application only the permissions it needs, and audit its
  server list.
- Keep `discord.py` and the `anthropic` SDK current; pin versions in
  production.
- Back up `memories/`, `persistence/` and `bots/` (`python
  deployment_tool.py export` produces a portable zip).
- Separate keys for development and production, and separate bot tokens for
  separate installs.

## Acknowledgments

Researchers who disclose responsibly are credited in the advisory, the
release notes and CHANGELOG.md, with their consent.
