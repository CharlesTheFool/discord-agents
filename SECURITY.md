# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.15.x  | Yes       |
| < 0.14  | No        |

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
  from inside it. Since 0.15.0 the path is judged case-folded (Windows and
  macOS ignore case), and a segment Windows could resolve to another name
  (an 8.3 short name with `~`, an alternate data stream with `:`, a
  trailing dot or space) is refused outright;
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
  older than the running process are covered. Copies already in a workshop
  stay (see the workshop's residuals).
- Web search and fetch are capped per request.
- The `send_message` tool reports ground truth about what it posted (message
  id, which files attached), so the model cannot claim an attachment that
  never went out.

### The workshop (0.14)

Since 0.14.0 the bot runs commands in a workshop: a Linux microVM per scope
(a server, a vaulted channel or thread, a DM), started through
[microsandbox](https://github.com/microsandbox/microsandbox) on the host's
hardware virtualization. Members of a server can ask the bot to run
anything there, and content an outsider planted can steer it too, so the
boundaries are the VM and the network policy, never the model's judgment.

**Isolation.**

- Each scope is its own VM with its own disk. A vault's workshop is
  separate from its server's, and a DM's from everything else. Commands
  run under the runtime's restricted profile (no new privileges; mount
  refused), with 2 CPUs, 2 GiB of memory, a 10 GiB `/workspace` and a
  10 GiB root filesystem. Both disks are fixed-size images, so filling one
  fails the guest's writes, never the host's. A free-space floor on the host drive
  refuses new work below 5 GiB.
- Nothing on the host is mounted into a sandbox. Files cross as bytes
  through the bot's own reads and writes: a fetched attachment into
  `/workspace/inbox`, a file attached to a message by its path, a file
  copied to or from the repository. Symlinks resolve inside the guest. The
  bot's skills are copied in at `/skills`.
- The sandbox holds no secrets. API keys, Discord tokens and the
  operator's own logins (git credential managers, `gh` tokens, browser
  sessions) stay on the host, and the workshop never borrows them.
- A command runs at most six hours, enforced by the runtime. A scope runs
  at most three background jobs at once, and at most four sandboxes run
  per bot, fewer when the host's free commit memory can't hold another
  (4 GiB stays reserved for the host).

**Egress.** A sandbox's network policy denies everything, DNS included,
except TCP to one port on the host: the bot's egress proxy. The proxy:

- accepts only `CONNECT` to port 443 (no plain HTTP, no SSH, no SMTP);
- identifies the scope by a random per-scope credential, so a sandbox gets
  only its own scope's allowlist (unless it obtains another scope's
  credential; see the residuals);
- allows exact host names only: a download preset (`pypi.org`,
  `files.pythonhosted.org`, `codeload.github.com`,
  `objects.githubusercontent.com`, `deb.debian.org`,
  `security.debian.org`), the operator's `workshop.allowlist`, and hosts an
  operator approved for that scope;
- resolves names itself and refuses any address that isn't globally
  routable (private, loopback, link-local, multicast, reserved, and the IPv6
  forms that wrap them: IPv4-mapped, IPv4-translated, NAT64, 6to4, Teredo),
  or that belongs to the host itself or its IPv6 LAN (a global IPv6
  address on your network is "public", so the host's own /64 prefixes are
  refused by name); then it connects to the exact address it checked, so DNS
  rebinding can't redirect a tunnel;
- requires the TLS ClientHello to name exactly one server, the host the
  `CONNECT` asked for.

`github.com` and `registry.npmjs.org` are not in the preset: both accept
uploads, so each needs an approval. `request_network_access` DMs the
bot's operators: the ids in `workshop.operators`, or else the Discord
application's owner (for a team-owned app, the team's owner and admins). The DM shows the host and where the request came
from; the model's stated reason sits behind a Details button, so text a
model wrote never lands in the DM's preview. The first decision wins, a
request expires after 24 hours, and a host an operator declined can't be
asked for again from that scope for 24 hours. An operator can also open a
scope to any public host, still port 443 only.

**Helpers and projects (0.15.0).**

- A helper (`delegate`) runs a model loop in its scope's workshop with a
  tool set built for it: memory, message and file reads, web search and
  fetch where enabled, the shell, the editor, skill guides. It never gets
  `send_message`, memory writes, `request_network_access`, `ask_prime`,
  the repository, MCP tools or `delegate`, and its own dispatch refuses
  each of those names whatever the model emits; the memory tool refuses
  every command but `view` for a helper's call. Its reads follow the vault
  rules of the channel it was delegated from. Its web fetches take URLs
  from search results only: its brief is the bot's words, not a person's.
- A project note counts only in the scope whose memory folder holds it,
  and only if its `home_channel` resolves to that same scope (a thread
  through its parent); anything else is skipped and logged. The memory
  tool writes a path with a `projects` folder only under the
  conversation's own tree, and a thread whose parent isn't known writes
  none. Vault rules alone would have let a channel outside every vault
  plant a note in another server's tree and get turns there.
- The dashboard's Workshop tab reads the bot's workshop state for named
  fields only (approved hosts, pending requests), never the proxy
  credentials beside them, and changes nothing directly: it leaves request
  files (allow or remove a host, reset, wipe, decide a pending request)
  the bot validates and applies. A wipe must carry its scope as
  confirmation, and reset and wipe are refused while the scope has
  running work.

**Residuals**, stated as they are:

- **Approved upload hosts.** Once `github.com` or npm is approved for a
  scope, a sandbox there can push or publish with credentials an injected
  page hands it (it has none of yours). Restricting methods needs TLS
  interception, which the proxy doesn't do.
- **Fronting inside TLS.** The proxy checks the `CONNECT` host and the SNI,
  but not the HTTP `Host` header inside the tunnel. On a CDN edge shared by
  many tenants, a request could name another tenant behind an allowed name.
  GitHub's edge was checked and refuses this across the preset's hosts; the
  general case stays open until TLS interception.
- **The runtime's share.** microsandbox mounts each sandbox's own runtime
  folder (its control files, no operator data) read-write into the guest at
  `/.msb`. It is per sandbox. On Windows a symlink the guest makes there
  lands on the host as a plain file holding its target's path; on macOS and
  Linux it may be a real link.
- **No process cap.** The guest runs as root, which a per-process limit
  doesn't bind, and the restricted profile doesn't delegate the pids
  controller. A fork bomb is contained by its VM's CPU and memory caps and
  takes down only its own sandbox.
- **Disk only grows.** Deleting files inside a sandbox doesn't return host
  disk (`fstrim` is refused under the restricted profile), so a scope's host
  footprint ratchets up to its caps, 20 GiB at most.
- **A scope's proxy credential is visible to its model.** It rides each
  command's environment, so the model can read it (and, steered, post it).
  Another server's sandbox that obtains it could use the first scope's
  approved hosts. Vaulted scopes are out of reach (their messages never
  surface elsewhere); between ordinary servers, an approval is only as
  private as that server's channels. Every start mints new credentials, so
  a leaked one lasts until the next restart; a job still running across a
  restart keeps its old one until that job ends.
- **Your router's public IPv4.** A name resolving to the network's own
  public IPv4 address passes the check and, through NAT hairpinning, could
  reach a service you forward on port 443. Open-internet scopes only; IPv6
  LAN addresses are refused as above.
- **Deleting a Discord message doesn't reach the workshop.** A file the bot
  copied into `/workspace` (a fetched attachment, a repository copy) stays
  there after the message is deleted.
- **Web fetch is a second way out, and it isn't the workshop's.** Where web
  search is on (`api.web_search.enabled`, off by default), `web_fetch` runs
  on Anthropic's servers, outside the proxy and its allowlist. It fetches
  only URLs that appeared in the conversation, and since 0.15.0 the bot's
  own tool results don't count (`url_sources`): a URL it prints with `bash`
  or reads back from a file or a message search isn't fetchable. What still
  counts is anything in a user turn, and some user turns are the bot's own
  words relayed: a background job's completion note carries the last lines
  of its output, and the per-request context lists recent messages,
  the bot's included. A URL laundered through either is fetchable a turn
  later, query string and all. Leave web search off where that matters.
- **The hypervisor is the boundary.** The VM monitor runs as the operator's
  user. A VM escape would land with that user's privileges, which is one
  more reason to run the app under an account that isn't an administrator.

`workshop.enabled: false` turns all of it off: the bot then has no
execution capability at all, and the dashboard says so.

## What the model is still trusted with

- **Cross-server discretion.** Vaults are the only mechanical boundary
  between servers. Whether to mention that you know someone from another
  server, or to repeat what they said there, is governed by a mandatory
  prompt section, and a successful injection could make the bot cross that
  line. Put anything that must not travel in a vault.
- **What runs in the workshop.** Inside a scope's VM the model runs what it
  chooses, so an injected instruction can run code there. What bounds it is
  the VM and the proxy above: the scope's own files, the allowed hosts, no
  secrets.
- **A second processor, if you choose one (0.15.1).** With an OpenAI
  `background_model`, every conversation the bot distills into episodes
  goes to OpenAI: channels, threads, vaulted channels and DMs alike, the
  bot's own replies included, together with that place's current notes
  file; so do the messages a watch checks. With an OpenAI `worker_model`, a
  helper's task (written by the bot, from whatever conversation delegated
  it, DMs included) and everything the helper reads. Requests are sent with `store: false` to a pinned base URL, but
  `store: false` is not zero retention: OpenAI's abuse-monitoring retention
  applies unless your organization has zero data retention. Both settings
  are opt-in, per role, in the YAML; the main model and the consolidation
  batches always stay on Anthropic. The OpenAI SDK also honors
  `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` from the environment, which
  decide whose retention terms apply.
- **External content.** Fetched pages, attachments and MCP tool results
  reach the model as text. Citations show where search results came from;
  review them before trusting a claim. Report patterns of injected behavior
  through a security advisory.

## Secrets and data at rest

API keys and Discord tokens live in environment variables, loaded from
`.env`, which is gitignored. Each bot config names its token variable
(`discord.token_env_var`), and `api.provider.api_key_env` can point a bot at
its own API key (`api.providers.openai_responses.api_key_env` for OpenAI,
default `OPENAI_API_KEY`). The desktop app writes secrets to the data root's `.env`
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
- Approve network hosts per scope and sparingly; open internet only for
  scopes whose members you trust with it.

## Acknowledgments

Researchers who disclose responsibly are credited in the advisory, the
release notes and CHANGELOG.md, with their consent.
