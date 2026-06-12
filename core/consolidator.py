"""
Memory reconsolidation (v0.7.0).

Memory only accretes during the week; this weekly per-server pass merges,
decays, and self-corrects it via the Batches API:
  1. episode compaction -> era digests
  2. profile rewrites re-derived from evidence
  3. provenance repair
  4. channel/culture refresh (monthly)
First run on a server migrates per-server user profiles to global.

Safety: every rewritten file is first copied to a .history/ sibling, pruned
to CONSOLIDATION_HISTORY_KEEP. Vault boundaries are absolute. A failed run
leaves the stamp untouched and retries next night.
"""

import json
import logging
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.batch_client import BatchClient
from core.internal_constants import (
    model_supports_effort,
    CONSOLIDATION_INTERVAL_DAYS,
    CONSOLIDATION_CULTURE_EVERY_N_RUNS,
    CONSOLIDATION_ERA_AGE_DAYS,
    CONSOLIDATION_HISTORY_KEEP,
    CONSOLIDATION_EVIDENCE_MESSAGES,
    CONSOLIDATION_EVIDENCE_EPISODES,
    CONSOLIDATION_MAX_TOKENS,
)

logger = logging.getLogger(__name__)

# Operator-authored "what this server is" note (servers/{id}/character.md):
# read into every distillation prompt so a workplace and a friend group don't
# get flattened into the same minutes. Capped to stay lean across many requests.
CHARACTER_CAP = 1200


def read_server_character(memory_manager, server_id: str) -> Optional[str]:
    """The operator's character.md for this server, capped, or None if absent."""
    try:
        fs = memory_manager.resolve_path(
            memory_manager.get_server_character_path(server_id))
        if not fs.exists():
            return None
        text = fs.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text[:CHARACTER_CAP] or None


def with_character(base_prompt: str, character: Optional[str]) -> str:
    """Append the operator's server-character note to a distillation system prompt."""
    if not character:
        return base_prompt
    return (base_prompt + "\n\nWhat this space actually is, from the operator who "
            "runs it - treat it as ground truth about the server's nature and "
            "register, and let it steer what is worth keeping:\n" + character)


# Memory is the bot's own — so the model that writes it should BE the bot, in
# first person and its own voice, not a detached archivist. The personality is
# capped to stay lean across many batch requests.
PERSONALITY_CAP = 1500


def read_personality(config) -> Optional[str]:
    """The bot's standing personality prompt (who it is), capped, or None."""
    personality = getattr(config, "personality", None)
    text = (getattr(personality, "base_prompt", None) or "").strip()
    return text[:PERSONALITY_CAP] or None


def as_self(task_prompt: str, personality: Optional[str],
            character: Optional[str] = None, lived: bool = True) -> str:
    """Frame a distillation prompt as the bot revisiting its OWN memory: first
    person, its own voice — not a report ABOUT the bot. `lived=False` is for
    induction, where the bot is reading history it wasn't around for and must
    stay honest that it's gathered-not-lived."""
    if lived:
        head = (
            "You are this Discord bot, going back over your own memory. The notes "
            "below are your own record of your own life on this server. Rewrite "
            "them as yourself: first person, your own voice, the way you'd write "
            "for yourself to read later. This is not a report about some bot - "
            "it's you, remembering. Reconsider the whole picture top-down and let "
            "newer evidence correct what you used to think.")
    else:
        head = (
            "You are this Discord bot, about to join a server that already has a "
            "history. Below is its backlog, from before you arrived. Read it as "
            "yourself and write what you take from it in the first person and "
            "your own voice - but stay honest that you weren't there: this is "
            "what you've gathered from reading the backlog, not what you lived. "
            "Don't fake familiarity you haven't earned yet.")
    if personality:
        head += "\n\n<who_you_are>\n" + personality + "\n</who_you_are>"
    out = head + "\n\n" + task_prompt
    if character:
        out += ("\n\nWhat this space actually is, from the operator who runs it - "
                "ground truth about the server's nature and register; let it "
                "steer what's worth keeping:\n" + character)
    return out


def memory_output_config(schema: dict, model: str) -> dict:
    """Structured-output config for a distillation request, with medium thinking
    effort on models that support it (the memory model reasons harder than the
    API default of none)."""
    cfg = {"format": {"type": "json_schema", "schema": schema}}
    if model_supports_effort(model):
        cfg["effort"] = "medium"
    return cfg


# =============================================================================
# Task 12: Era digest compaction (Pass 1)
# =============================================================================

ERA_DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Era title, <= 8 words"},
        "slug": {"type": "string", "description": "kebab-case, <= 4 words"},
        "summary_markdown": {"type": "string",
                             "description": "The era in <= 250 words: what mattered, how it ended up."},
        "standing_facts": {"type": "array", "items": {"type": "string"}},
        "memorable_moments": {"type": "array", "items": {"type": "string"},
                              "description": "who-said-what worth keeping, attributed"},
        "running_jokes": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "slug", "summary_markdown", "standing_facts",
                 "memorable_moments", "running_jokes", "decisions"],
    "additionalProperties": False,
}

ERA_SYSTEM_PROMPT = (
    "Condense your old episode notes from this channel into one era digest - "
    "what's still worth your remembering months from now. Notice what kind of "
    "space this is and how people are in it: a working channel turns on "
    "decisions, specs, and ownership; a social or gaming one turns on its "
    "relationships, its humor, and who's who with each other - let that steer "
    "what survives. Keep the standing facts, the decisions, the lines worth "
    "remembering (attributed by name), the jokes and dynamics that stuck, and "
    "the register of the room. Drop the play-by-play and one-off logistics. "
    "People by name, never raw numeric IDs. Plain, tight markdown, in your voice."
)

# =============================================================================
# Task 13: Profile rewrites (Pass 2) + provenance repair (Pass 3)
# =============================================================================

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "header_line": {"type": "string",
                        "description": "Display name (username), e.g. 'Charles (charlesthefool)'"},
        "known_from": {"type": "array", "items": {"type": "string"},
                       "description": "Contexts as currently listed, preserved verbatim"},
        "claims": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"},
                           "origin": {"type": "string",
                                      "description": "server id, 'DMs', or 'private' - where this was learned"}},
            "required": ["text", "origin"], "additionalProperties": False}},
    },
    "required": ["header_line", "known_from", "claims"],
    "additionalProperties": False,
}

PROFILE_SYSTEM_PROMPT = (
    "These are your own notes on one person - the same human wherever you run "
    "into them. Rewrite them from the evidence, in the first person: what you "
    "know about them and, where it's earned, your own read on them. Recent "
    "messages outrank old notes when they conflict; date or drop anything "
    "stale; keep it lean. Let the register of the space inform what's worth "
    "noting - how someone shows up in a workplace differs from a friend group. "
    "Every note keeps the origin it was learned in - preserve an existing "
    "origin tag; new notes from this evidence are tagged with this server. "
    "Name them, never raw numeric IDs."
)


def render_profile(data: dict) -> str:
    lines = [f"# {data['header_line']}",
             f"Known from: {', '.join(data['known_from'])}",
             "", "## Profile"]
    lines += [f"- {c['text']} [origin: {c['origin']}]" for c in data["claims"]]
    return "\n".join(lines) + "\n"


# =============================================================================
# Task 14: Monthly channel/culture refresh (Pass 4)
# =============================================================================

CULTURE_SCHEMA = {
    "type": "object",
    "properties": {"culture_markdown": {"type": "string",
        "description": "The server culture file: what this place is like, anchored in evidence. <= 400 words."}},
    "required": ["culture_markdown"], "additionalProperties": False,
}

CHANNEL_BODY_SCHEMA = {
    "type": "object",
    "properties": {
        "standing_facts": {"type": "array", "items": {"type": "string"}},
        "settled_questions": {"type": "array", "items": {"type": "string"}},
        "used_jokes": {"type": "array", "items": {"type": "string"}},
        "open_threads": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["standing_facts", "settled_questions", "used_jokes", "open_threads"],
    "additionalProperties": False,
}

REFRESH_SYSTEM_PROMPT = (
    "Refresh your standing notes for this channel from your recent episodes. "
    "Notice what kind of channel this is - work, project, social, play - and "
    "keep what matters for that: a working channel's decisions and open "
    "threads, a social one's relationships, running bits, and dynamics. Keep "
    "what the evidence still supports, drop what went stale, merge duplicates. "
    "Lean and concrete, in your own voice."
)

CULTURE_SYSTEM_PROMPT = (
    "Refresh your sense of this server's culture from your channel notes - what "
    "kind of place this is to you (a workplace, a project team, a friend group, "
    "a gaming hangout), its rhythms, its register, and how its people are with "
    "each other, anchored in what actually happens here. Name the register "
    "honestly - if it's dark, casual, in-jokey, say so. People by name, never "
    "raw numeric IDs. Lean markdown, no fluff, first person."
)


def archive_to_history(fs_path: Path, keep: int = CONSOLIDATION_HISTORY_KEEP) -> None:
    """Copy a file into a .history sibling dir before rewriting it."""
    if not fs_path.exists():
        return
    hist = fs_path.parent / ".history"
    hist.mkdir(exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    target = hist / f"{fs_path.name}.{stamp}"
    shutil.copy2(fs_path, target)
    versions = sorted(hist.glob(f"{fs_path.name}.*"))
    for stale in versions[:-keep]:
        stale.unlink()


class MemoryConsolidator:
    def __init__(self, bot_id, config, message_memory, memory_manager,
                 user_cache, anthropic_client, vaults,
                 guild_name_resolver=None):
        self.bot_id = bot_id
        self.config = config
        self.message_memory = message_memory
        self.memory = memory_manager
        self.user_cache = user_cache
        self.batch = BatchClient(anthropic_client)
        self.vaults = vaults
        self.guild_name_resolver = guild_name_resolver
        self._personality = read_personality(config)  # frame the writer AS the bot
        self._running = False
        self._pending_era_files: dict = {}  # key "channel_id::month" -> list[Path]

    # ---------- cadence ----------

    def _servers_root(self) -> Path:
        return self.memory.base_path / "servers"

    def _stamp_path(self, server_id: str) -> Path:
        return self._servers_root() / server_id / ".consolidation_stamp"

    def _read_stamp(self, server_id: str) -> Optional[dict]:
        p = self._stamp_path(server_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_stamp(self, server_id: str, runs: int) -> None:
        self._stamp_path(server_id).write_text(
            json.dumps({"last_run": datetime.utcnow().isoformat(), "runs": runs}),
            encoding="utf-8",
        )

    def _is_due(self, server_id: str) -> bool:
        stamp = self._read_stamp(server_id)
        if stamp is None or "last_run" not in stamp:
            return True
        last = datetime.fromisoformat(stamp["last_run"])
        return datetime.utcnow() - last >= timedelta(days=CONSOLIDATION_INTERVAL_DAYS)

    def _culture_due(self, server_id: str) -> bool:
        stamp = self._read_stamp(server_id)
        runs = (stamp or {}).get("runs", 0)
        return (runs + 1) % CONSOLIDATION_CULTURE_EVERY_N_RUNS == 0 or runs == 0

    async def nightly_tick(self) -> None:
        """3am hook: consolidate AT MOST one due server (stagger = cost smoothing)."""
        if self._running:
            return
        root = self._servers_root()
        if not root.exists():
            return
        candidates = [p.name for p in root.iterdir() if p.is_dir() and p.name.isdigit()]
        due = sorted((sid for sid in candidates if self._is_due(sid)),
                     key=lambda sid: (self._read_stamp(sid) or {}).get("last_run", ""))
        if not due:
            return
        self._running = True
        try:
            await self.consolidate_server(due[0])
        except Exception as e:
            logger.error(f"Consolidation of {due[0]} failed (stamp untouched): {e}",
                         exc_info=True)
        finally:
            self._running = False

    # ---------- the run ----------

    async def consolidate_server(self, server_id: str, force: bool = False) -> dict:
        """Run one server's weekly pass. Returns a report dict."""
        if not force and not self._is_due(server_id):
            return {"server": server_id, "skipped": "not due"}
        stamp = self._read_stamp(server_id)
        first_run = stamp is None
        report = {"server": server_id, "first_run": first_run}

        if first_run:
            report["migration"] = await self.migrate_profiles(server_id)

        # Pass 1: era compaction; Passes 2+3: profile rewrites + provenance repair;
        # Pass 4 (monthly): channel/culture refresh
        era_requests = self._build_era_requests(server_id)
        all_requests = era_requests + await self._build_profile_requests(server_id)
        all_requests += self._build_refresh_requests(server_id)

        if all_requests:
            results = await self.batch.run(all_requests)
            report["compacted"] = 0
            for cid, result in results.items():
                if result.type != "succeeded":
                    logger.warning(f"Batch item {cid} {result.type} - skipped")
                    continue
                try:
                    text = next(b.text for b in result.message.content if b.type == "text")
                    data = json.loads(text)
                except (StopIteration, json.JSONDecodeError) as e:
                    logger.warning(f"Batch item {cid} unparseable ({e}) - skipped")
                    continue
                # custom_id charset is API-constrained to [a-zA-Z0-9_-]; "_" is
                # the separator and no segment (kind/cid/month/uid) contains it
                kind, *rest = cid.split("_")
                if kind == "era":
                    await self._apply_era_result(server_id, rest[0], rest[1], data)
                    report["compacted"] += 1
                elif kind == "profile":
                    self._apply_profile_result(rest[0], data)
                    report["profiles"] = report.get("profiles", 0) + 1
                elif kind == "culture":
                    self._apply_culture(server_id, data)
                elif kind == "channel":
                    self._apply_channel_refresh(server_id, rest[0], data)

        report["provenance_fixed"] = self.repair_provenance()
        self._write_stamp(server_id, runs=(stamp or {}).get("runs", 0) + 1)
        logger.info(f"Consolidation report for {server_id}: {report}")
        return report

    async def migrate_profiles(self, server_id: str) -> dict:
        """First-run migration: per-server profiles -> global, origin-tagged.

        Vaulted servers are skipped wholesale: their person-notes stay inside.
        """
        if self.vaults and self.vaults.vaults and server_id in self.vaults.vaults:
            logger.info(f"Server {server_id} is vaulted - profiles stay put")
            return {"skipped": "vaulted"}

        users_dir = self._servers_root() / server_id / "users"
        if not users_dir.exists():
            return {"migrated": 0}
        global_dir = self.memory.get_global_users_dir()
        global_dir.mkdir(parents=True, exist_ok=True)

        migrated, unresolved_count = 0, 0
        for f in sorted(users_dir.glob("*.md")):
            stem = f.stem
            if stem.isdigit():
                user_id = stem
            elif stem == "SYSTEM":
                user_id = None
            elif self.user_cache is not None:
                user_id = await self.user_cache.resolve_username(stem)
            else:
                user_id = None

            content = f.read_text(encoding="utf-8")
            tagged = self._tag_untagged_claims(content, server_id)

            if user_id:
                target = global_dir / f"{user_id}.md"
                self._merge_into_global(target, tagged, server_id)
                migrated += 1
            else:
                udir = global_dir / "unresolved"
                udir.mkdir(exist_ok=True)
                target = udir / f.name
                self._merge_into_global(target, tagged, server_id)
                unresolved_count += 1
                logger.warning(f"Profile {f.name} in {server_id}: username not "
                               f"resolvable - parked in global/users/unresolved/")

            archive_to_history(f)
            f.unlink()

        return {"migrated": migrated, "unresolved": unresolved_count}

    def _tag_untagged_claims(self, content: str, server_id: str) -> str:
        out = []
        for line in content.splitlines():
            if line.lstrip().startswith("- ") and "[origin:" not in line:
                line = f"{line} [origin: {server_id}]"
            out.append(line)
        return "\n".join(out)

    def _server_label(self, server_id: str) -> str:
        name = self.guild_name_resolver(server_id) if self.guild_name_resolver else None
        return f"{name} ({server_id})" if name else f"({server_id})"

    def _merge_into_global(self, target: Path, tagged_content: str, server_id: str) -> None:
        entry = self._server_label(server_id)
        if not target.exists():
            lines = tagged_content.splitlines()
            title = lines[0] if lines and lines[0].startswith("# ") else "# (unknown)"
            body = "\n".join(lines[1:] if lines and lines[0].startswith("# ") else lines)
            target.write_text(f"{title}\nKnown from: {entry}\n{body}\n", encoding="utf-8")
            return
        archive_to_history(target)
        existing = target.read_text(encoding="utf-8")
        lines = existing.splitlines()
        if len(lines) > 1 and lines[1].startswith("Known from:"):
            if f"({server_id})" not in lines[1]:
                lines[1] += f", {entry}"
        else:
            lines.insert(1, f"Known from: {entry}")
        body_new = "\n".join(
            l for l in tagged_content.splitlines() if not l.startswith("# "))
        merged = "\n".join(lines) + f"\n{body_new}\n"
        target.write_text(merged, encoding="utf-8")

    # ---------- Pass 1: era compaction ----------

    def _eligible_episodes(self, server_id: str, channel_id: str) -> dict:
        """Episode files older than the era age, grouped by YYYY-MM."""
        episodes_dir = (self._servers_root() / server_id / "channels"
                        / channel_id / "episodes")
        if not episodes_dir.exists():
            return {}
        cutoff = datetime.utcnow() - timedelta(days=CONSOLIDATION_ERA_AGE_DAYS)
        groups: dict = {}
        for f in sorted(episodes_dir.glob("*.md")):
            if f.name.startswith("era_"):
                continue
            try:
                file_date = datetime.strptime(f.name[:10], "%Y-%m-%d")
            except ValueError:
                continue
            if file_date < cutoff:
                groups.setdefault(f.name[:7], []).append(f)
        return groups

    def _build_era_requests(self, server_id: str) -> list:
        self._pending_era_files.clear()
        requests = []
        character = read_server_character(self.memory, server_id)
        channels_dir = self._servers_root() / server_id / "channels"
        if not channels_dir.exists():
            return requests
        for ch_dir in sorted(p for p in channels_dir.iterdir() if p.is_dir()):
            for month, files in self._eligible_episodes(server_id, ch_dir.name).items():
                key = f"{ch_dir.name}::{month}"
                self._pending_era_files[key] = files
                corpus = "\n\n---\n\n".join(
                    f.read_text(encoding="utf-8") for f in files)
                requests.append({
                    "custom_id": f"era_{ch_dir.name}_{month}",
                    "params": {
                        "model": self.config.api.consolidation_model,
                        "max_tokens": CONSOLIDATION_MAX_TOKENS,
                        "system": as_self(ERA_SYSTEM_PROMPT, self._personality, character),
                        "messages": [{"role": "user", "content":
                            f"<episodes channel_id=\"{ch_dir.name}\" month=\"{month}\">\n"
                            f"{corpus}\n</episodes>"}],
                        "output_config": memory_output_config(
                            ERA_DIGEST_SCHEMA, self.config.api.consolidation_model),
                    },
                })
        return requests

    async def _apply_era_result(self, server_id: str, channel_id: str,
                                month: str, data: dict) -> None:
        episodes_dir = (self._servers_root() / server_id / "channels"
                        / channel_id / "episodes")
        slug = re.sub(r"[^a-z0-9-]", "", data["slug"].lower().replace(" ", "-"))[:40] or "era"
        era_file = episodes_dir / f"era_{month}_{slug}.md"
        body = [f"# {data['title']} ({month})", "", data["summary_markdown"], ""]
        for heading, key in (("Standing Facts", "standing_facts"),
                             ("Memorable Moments", "memorable_moments"),
                             ("Running Jokes", "running_jokes"),
                             ("Decisions", "decisions")):
            if data[key]:
                body.append(f"## {heading}")
                body.extend(f"- {item}" for item in data[key])
                body.append("")
        era_file.write_text("\n".join(body), encoding="utf-8")

        # archive + remove originals captured at request-build time; rewrite index.
        # Fall back to _eligible_episodes only when called directly (e.g. tests)
        # without a preceding _build_era_requests call.
        key = f"{channel_id}::{month}"
        captured_files = self._pending_era_files.pop(
            key, self._eligible_episodes(server_id, channel_id).get(month, []))
        compacted_starts = []
        for f in captured_files:
            if not f.exists():
                continue  # already gone (manual delete between batch submit and apply)
            parts = f.name.split("_")
            if len(parts) >= 3:
                compacted_starts.append(parts[2])
            archive_to_history(f)
            f.unlink()
        self._rewrite_index_after_compaction(server_id, channel_id, month,
                                             data["title"], era_file.name,
                                             compacted_starts)

    def _rewrite_index_after_compaction(self, server_id, channel_id, month,
                                        title, era_filename, compacted_starts):
        state_fs = self._servers_root() / server_id / "channels" / f"{channel_id}.md"
        if not state_fs.exists():
            return
        content = state_fs.read_text(encoding="utf-8")
        parts = content.split("\n## Episode Index")
        if len(parts) < 2:
            return
        keep = [l for l in parts[1].splitlines()
                if not any(f"msgs {start}-" in l for start in compacted_starts)]
        era_line = f"- {month} | Era digest: {title} | {era_filename}"
        lines = [l for l in keep if l.strip()]
        archive_to_history(state_fs)
        state_fs.write_text(
            parts[0] + "\n## Episode Index\n" + era_line + "\n" + "\n".join(lines) + "\n",
            encoding="utf-8")

    # ---------- Pass 2: profile rewrites ----------

    def _vaulted_channel_ids(self, server_id: str) -> list:
        if not self.vaults or not self.vaults.vaults:
            return []
        return [v for v in self.vaults.vaults if v != server_id]

    async def _build_profile_requests(self, server_id: str) -> list:
        if self.vaults and server_id in getattr(self.vaults, "vaults", set()):
            return []  # vaulted server evidence never updates global profiles
        since = datetime.utcnow() - timedelta(days=CONSOLIDATION_INTERVAL_DAYS)
        authors = await self.message_memory.get_active_authors(server_id, since)
        exclude = self._vaulted_channel_ids(server_id)
        episodes_blurb = self._recent_episode_blurb(server_id)
        character = read_server_character(self.memory, server_id)
        requests = []
        for uid in authors:
            if uid == "SYSTEM" or not uid.isdigit():
                continue
            msgs = await self.message_memory.get_user_messages(
                uid, server_id, limit=CONSOLIDATION_EVIDENCE_MESSAGES,
                exclude_channel_ids=exclude or None)
            if not msgs:
                continue
            transcript = "\n".join(
                f"[{m.timestamp:%Y-%m-%d %H:%M}] {m.author_name}: {m.content or '[no text]'}"
                for m in reversed(msgs))
            gpath = self.memory.get_global_user_profile_path(uid)
            existing = self.memory.resolve_path(gpath)
            current = existing.read_text(encoding="utf-8") if existing.exists() else "(no profile yet)"
            requests.append({
                "custom_id": f"profile_{uid}",
                "params": {
                    "model": self.config.api.consolidation_model,
                    "max_tokens": CONSOLIDATION_MAX_TOKENS,
                    "system": as_self(PROFILE_SYSTEM_PROMPT, self._personality, character),
                    "messages": [{"role": "user", "content":
                        f"This server's id: {server_id}\n\n"
                        f"<current_profile>\n{current}\n</current_profile>\n\n"
                        f"<recent_episodes>\n{episodes_blurb}\n</recent_episodes>\n\n"
                        f"<recent_messages user_id=\"{uid}\">\n{transcript}\n</recent_messages>"}],
                    "output_config": memory_output_config(
                        PROFILE_SCHEMA, self.config.api.consolidation_model),
                },
            })
        return requests

    def _recent_episode_blurb(self, server_id: str) -> str:
        """Newest few episode files server-wide (non-vaulted channels)."""
        vaulted = set(self._vaulted_channel_ids(server_id))
        channels_dir = self._servers_root() / server_id / "channels"
        candidates = []
        if channels_dir.exists():
            for ch_dir in channels_dir.iterdir():
                if not ch_dir.is_dir() or ch_dir.name in vaulted:
                    continue
                candidates.extend((ch_dir / "episodes").glob("*.md")
                                  if (ch_dir / "episodes").exists() else [])
        recent = [p for p in candidates if not p.name.startswith("era_")]
        newest = sorted(recent, key=lambda p: p.name)[-CONSOLIDATION_EVIDENCE_EPISODES:]
        return "\n\n---\n\n".join(f.read_text(encoding="utf-8") for f in newest) or "(none)"

    def _apply_profile_result(self, uid: str, data: dict) -> None:
        target = self.memory.resolve_path(self.memory.get_global_user_profile_path(uid))
        target.parent.mkdir(parents=True, exist_ok=True)
        archive_to_history(target)
        target.write_text(render_profile(data), encoding="utf-8")

    # ---------- Pass 3: provenance repair ----------

    def repair_provenance(self) -> int:
        """Pass 3 (code-only): every claim line gets an origin tag; single-server
        files inherit that server, otherwise 'unspecified'."""
        gdir = self.memory.get_global_users_dir()
        if not gdir.exists():
            return 0
        fixed = 0
        for f in gdir.glob("*.md"):
            content = f.read_text(encoding="utf-8")
            lines = content.splitlines()
            known = lines[1] if len(lines) > 1 and lines[1].startswith("Known from:") else ""
            ids = re.findall(r"\((\d+)\)", known)
            default_origin = ids[0] if len(ids) == 1 else "unspecified"
            changed = False
            for i, line in enumerate(lines):
                if line.lstrip().startswith("- ") and "[origin:" not in line:
                    lines[i] = f"{line} [origin: {default_origin}]"
                    changed = True
            if changed:
                archive_to_history(f)
                f.write_text("\n".join(lines) + "\n", encoding="utf-8")
                fixed += 1
        return fixed

    # ---------- Pass 4: monthly channel/culture refresh ----------

    def _build_refresh_requests(self, server_id: str) -> list:
        """Build channel + culture requests when the culture pass is due."""
        if not self._culture_due(server_id):
            return []
        from core.episode_manager import split_channel_state
        requests = []
        character = read_server_character(self.memory, server_id)
        channels_dir = self._servers_root() / server_id / "channels"
        channel_bodies = {}  # cid -> body text for the culture request
        if channels_dir.exists():
            for ch_dir in sorted(p for p in channels_dir.iterdir() if p.is_dir()):
                cid = ch_dir.name
                state_fs = channels_dir / f"{cid}.md"
                if not state_fs.exists():
                    continue
                state_content = state_fs.read_text(encoding="utf-8")
                body, _ = split_channel_state(state_content)
                channel_bodies[cid] = body
                # Collect newest N non-era episode files (era digests are derived, not evidence)
                ep_dir = ch_dir / "episodes"
                ep_files = []
                if ep_dir.exists():
                    all_eps = sorted(ep_dir.glob("*.md"), key=lambda p: p.name)
                    ep_files = [p for p in all_eps if not p.name.startswith("era_")]
                    ep_files = ep_files[-CONSOLIDATION_EVIDENCE_EPISODES:]
                episodes_text = "\n\n---\n\n".join(
                    f.read_text(encoding="utf-8") for f in ep_files) or "(none)"
                requests.append({
                    "custom_id": f"channel_{cid}",
                    "params": {
                        "model": self.config.api.consolidation_model,
                        "max_tokens": CONSOLIDATION_MAX_TOKENS,
                        "system": as_self(REFRESH_SYSTEM_PROMPT, self._personality, character),
                        "messages": [{"role": "user", "content":
                            f"<channel_state channel_id=\"{cid}\">\n{body}\n</channel_state>\n\n"
                            f"<recent_episodes>\n{episodes_text}\n</recent_episodes>"}],
                        "output_config": memory_output_config(
                            CHANNEL_BODY_SCHEMA, self.config.api.consolidation_model),
                    },
                })
        # Culture request: current culture.md + all channel bodies
        culture_fs = self._servers_root() / server_id / "culture.md"
        culture_text = culture_fs.read_text(encoding="utf-8") if culture_fs.exists() else "(no culture file yet)"
        channels_summary = "\n\n".join(
            f"<channel id=\"{cid}\">\n{body}\n</channel>"
            for cid, body in channel_bodies.items())
        requests.append({
            "custom_id": "culture",
            "params": {
                "model": self.config.api.consolidation_model,
                "max_tokens": CONSOLIDATION_MAX_TOKENS,
                "system": as_self(CULTURE_SYSTEM_PROMPT, self._personality, character),
                "messages": [{"role": "user", "content":
                    f"<culture>\n{culture_text}\n</culture>\n\n"
                    f"<channels>\n{channels_summary}\n</channels>"}],
                "output_config": memory_output_config(
                    CULTURE_SCHEMA, self.config.api.consolidation_model),
            },
        })
        return requests

    def _apply_channel_refresh(self, server_id: str, channel_id: str, data: dict) -> None:
        from core.episode_manager import render_channel_state, split_channel_state
        state_fs = self._servers_root() / server_id / "channels" / f"{channel_id}.md"
        if not state_fs.exists():
            return
        _, index_lines = split_channel_state(state_fs.read_text(encoding="utf-8"))
        archive_to_history(state_fs)
        state_fs.write_text(
            render_channel_state(channel_id, data, index_lines), encoding="utf-8")

    def _apply_culture(self, server_id: str, data: dict) -> None:
        culture_fs = self._servers_root() / server_id / "culture.md"
        archive_to_history(culture_fs)
        culture_fs.write_text(data["culture_markdown"], encoding="utf-8")
