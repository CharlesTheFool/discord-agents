# Roadmap

Where Discord Agents is headed. Versions before 1.0 are beta; themes are
commitments, details are not. History lives in [CHANGELOG.md](CHANGELOG.md).

## 0.6.2 — Companion feature *(reserved)*

Slot held for a small follow-on to the 0.6.1 bot file repository.

## 0.7.0 — The Unified Mind

One bot, one continuous identity across every server it inhabits — with the
judgment to handle that gracefully.

**Identity model**
- **People are global, places are local.** User profiles move to a single
  per-human file (`global/users/`); channels, episodes, culture, and
  follow-ups stay server-scoped. Existing per-server profiles merge
  automatically on first run.
- **Provenance everywhere.** Unified data keeps its origin: profile claims
  carry source tags, cross-server search/attachment results are labeled with
  the server they came from, repository files stay organized by server even
  though all drives are reachable.
- **Vaults** replace the `data_isolation` modes: one config key
  (`vaults: []`) marking channels/servers whose content never leaves them —
  a one-way valve (the bot is fully itself inside; nothing escapes:
  excluded from outside search, memory writes contained, repository saves
  blocked). Everything coarser is Discord's job: role permissions, and
  separate tokens for separate minds.
- **Tool scopes.** Search and repository listing default to the current
  server and widen to `global` on request — local and quiet by default,
  cross-server reach explicit and labeled.
- **Discretion norms.** A mandatory prompt section turns provenance into
  decorum: knowing someone from another server is fair to acknowledge;
  their business stays where it was learned. Unified, not a gossip pipe.

**Memory reconsolidation**
- Memory currently only accretes; nothing merges, decays, or self-corrects.
  A weekly background pass (Batches API, configurable
  `api.consolidation_model`, Sonnet default) fixes that:
  1. **Episode compaction** — old episodes merge into era digests
  2. **Profile rewrites** — re-derived from evidence, contradictions resolve
     toward recency, stale claims demoted, lean register enforced
  3. **Provenance repair** — origin tags normalized
  4. **Channel/culture refresh** — slower-cadence evidence-anchored rewrites
- Every rewritten file's prior version is archived (`.history/`, pruned) —
  one bad pass is always recoverable. Vault boundaries are absolute.

**Substrate**
- Message Batches API client: latency-tolerant distillation at 50% cost.

## 0.8.0 — Day One

Make a bot joining a populated server instantly tuned-in instead of amnesiac.

- **Server induction** — a deliberate operator action (`induct`, with
  dry-run cost preview) that distills existing history into channel
  digests, lean third-person user profiles, and server culture — explicitly
  marked as *observations from reading the backlog, not lived memory*, so
  the bot does its homework without faking familiarity. Built on the
  reconsolidation machinery (induction is reconsolidation at t = 0).
- **Thread support** — threads become first-class conversation surfaces
  (backfill, replies, memory).

## Later / unscheduled

- **Ensemble dynamics** — multiple bots in one server, aware of each other,
  with their own relationships
- **Richer presence** — events, polls, voice-channel awareness
- **1.0 operational maturity** — config stability guarantees, observability,
  onboarding for outside operators (only if the project decides it wants
  them)
- **Daily web-search quota** — dormant; revisit only if per-request caps
  prove insufficient
