"""
Server induction (v0.8.0): reconsolidation at t = 0.

A deliberate operator action that distills a server's stored backlog into
channel digests, lean global user profiles, and server culture - explicitly
marked as observations from reading the backlog, not lived memory. Reads the
messages DB only (never Discord); requires backfill to have run; refuses to
run while the bot is live (watermark races).
"""

import json
import logging
from typing import Optional

from core.batch_client import BatchClient
from core.consolidator import (
    ERA_DIGEST_SCHEMA,
    PROFILE_SCHEMA,
    render_profile,
    CULTURE_SCHEMA,
    CULTURE_SYSTEM_PROMPT,
    archive_to_history,
    read_server_character,
    read_personality,
    as_self,
    memory_output_config,
)
from core.internal_constants import (
    CONSOLIDATION_MAX_TOKENS,
    INDUCTION_CHUNK_TOKENS,
    INDUCTION_OUTPUT_RATIO,
    MODEL_BATCH_PRICES,
)

logger = logging.getLogger(__name__)

ARCHAEOLOGY_HEADER = "*(from reading the backlog before I was here - observations, not lived memory)*"

ARCHAEOLOGY_SYSTEM = (
    "Read this channel's backlog - from before your time here, so it's "
    "homework, not memories you lived. Pull out the PATTERNS and facts worth "
    "knowing, not a log of exchanges: the recurring dynamics and relationships, "
    "the inside jokes and bits that landed, the few moments that shaped the "
    "group, the standing facts, and - for a working channel - the deliverables, "
    "roles, and workflows. Don't try to capture everything; cataloguing every "
    "interaction caricatures people, and you're only just meeting them through "
    "this. Note roughly when time-sensitive things were true - this backlog may "
    "be old, so treat what you read as 'true as of then', not necessarily now. "
    "Match the register honestly - dark, casual, in-jokey - don't sand it into "
    "corporate minutes. People and channels by name, never raw numeric IDs (the "
    "transcript gives both - use the name). First person, but honest you "
    "weren't there: gathered from reading, not lived."
)

INDUCT_PROFILE_SYSTEM = (
    "Form a first impression of someone from backlog you're reading before "
    "you've ever met them - the durable patterns and facts you can actually "
    "infer, not a tally of their messages. First person, lean, and honest you "
    "haven't met them yet. Note when time-sensitive things were last true - "
    "this backlog may be months old, so a job, city, or status you see may "
    "already have changed; write it as 'as of then', not now. Let the register "
    "of the space inform what's worth noting. Every note is origin-tagged with "
    "this server."
)

# Chunk digests reuse the era schema + a per-user observation channel
CHUNK_DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        **{k: v for k, v in ERA_DIGEST_SCHEMA["properties"].items()
           if k not in ("title", "slug")},
        "user_observations": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string",
                            "description": "numeric Discord id from the transcript"},
                "observations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["user_id", "observations"], "additionalProperties": False}},
    },
    "required": [k for k in ERA_DIGEST_SCHEMA["required"]
                 if k not in ("title", "slug")] + ["user_observations"],
    "additionalProperties": False,
}

# Channel-note ledgers are merged code-side from chunk digests; each list
# keeps its newest entries
LEDGER_CAP = 12


def estimate_cost(model: str, input_tokens: int) -> Optional[float]:
    """Batch-rate dollar estimate; None for unpriced models."""
    for marker, (in_price, out_price) in MODEL_BATCH_PRICES.items():
        if marker in model:
            output_tokens = input_tokens * INDUCTION_OUTPUT_RATIO
            return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return None


def chunk_messages(messages: list, chunk_tokens: int = INDUCTION_CHUNK_TOKENS) -> list:
    """Oldest->newest chunks targeting ~chunk_tokens input each (chars/4)."""
    chunks, current, mass = [], [], 0
    for m in messages:
        current.append(m)
        mass += len(m.content or "") // 4
        if mass >= chunk_tokens:
            chunks.append(current)
            current, mass = [], 0
    if current:
        chunks.append(current)
    return chunks


class ServerInductor:
    """Distills a backfilled server's message history into starting memory."""

    def __init__(self, bot_id, config, message_memory, memory_manager,
                 user_cache, anthropic_client, vaults):
        self.bot_id = bot_id
        self.config = config
        self.message_memory = message_memory
        self.memory = memory_manager
        self.user_cache = user_cache
        self.batch = BatchClient(anthropic_client)
        self.vaults = vaults
        self._personality = read_personality(config)  # frame the writer AS the bot

    async def induct(self, server_id: str, dry_run: bool = False,
                     channels: Optional[list] = None,
                     force_full: bool = False) -> dict:
        channel_ids = [str(c) for c in
                       (channels or await self.message_memory.get_channels_in_server(server_id))
                       if str(c).isdigit()]

        watermarks, volumes = {}, {}
        for cid in channel_ids:
            wm = None if force_full else await self.message_memory.get_episode_watermark(cid)
            watermarks[cid] = wm
            count, chars = await self.message_memory.get_channel_volume(
                cid, after_message_id=wm)
            volumes[cid] = {"messages": count, "est_input_tokens": chars // 4}

        if dry_run:
            total = sum(v["est_input_tokens"] for v in volumes.values())
            return {
                "dry_run": True, "server": server_id, "channels": volumes,
                "est_total_tokens": total,
                "est_cost_usd": estimate_cost(
                    self.config.api.consolidation_model, total),
            }

        # Round 1: every channel's unprocessed span, chunked, one batch
        model = self.config.api.consolidation_model
        character = read_server_character(self.memory, server_id)
        requests, spans, chunk_map, report_channels = [], {}, {}, {}
        for cid in channel_ids:
            if volumes[cid]["messages"] == 0:
                report_channels[cid] = {"messages": 0, "chunks": 0, "status": "empty"}
                continue
            span = await self.message_memory.get_messages_after_id(cid, watermarks[cid])
            chunks = chunk_messages(span)
            spans[cid], chunk_map[cid] = span, chunks
            report_channels[cid] = {"messages": volumes[cid]["messages"],
                                    "chunks": len(chunks), "status": "ok"}
            for idx, chunk in enumerate(chunks):
                transcript = "\n".join(
                    f"[{m.timestamp:%Y-%m-%d %H:%M}] {m.author_name} (id:{m.author_id})"
                    f"{' (bot)' if m.is_bot else ''}: {m.content or '[no text]'}"
                    for m in chunk)
                requests.append({
                    "custom_id": f"chunk_{cid}_{idx}",
                    "params": {
                        "model": model,
                        "max_tokens": CONSOLIDATION_MAX_TOKENS,
                        "system": as_self(ARCHAEOLOGY_SYSTEM, self._personality, character, lived=False),
                        "messages": [{"role": "user", "content":
                            f"<backlog channel_id=\"{cid}\" chunk=\"{idx + 1}/{len(chunks)}\">\n"
                            f"{transcript}\n</backlog>"}],
                        "output_config": memory_output_config(CHUNK_DIGEST_SCHEMA, model),
                    },
                })

        if not requests:
            return {"server": server_id, "channels": report_channels,
                    "profiles": 0, "culture": False}

        results = await self.batch.run(requests)

        # Collect: a channel survives only if EVERY chunk parsed - no partial files
        digests = {}
        for cid, chunks in chunk_map.items():
            payloads = []
            for idx in range(len(chunks)):
                result = results.get(f"chunk_{cid}_{idx}")
                if result is None or result.type != "succeeded":
                    break
                try:
                    text = next(b.text for b in result.message.content if b.type == "text")
                    payloads.append(json.loads(text))
                except (StopIteration, json.JSONDecodeError):
                    break
            if len(payloads) == len(chunks):
                digests[cid] = payloads
            else:
                report_channels[cid]["status"] = "failed"
                logger.warning(f"Induction of channel {cid}: chunk failed - "
                               f"watermark kept, nothing written")

        # Apply per surviving channel: era digests, channel note, watermark
        server_vaulted = server_id in self.vaults.vaults
        state_bodies, observations = {}, {}
        for cid, payloads in digests.items():
            index_lines = self._write_era_digests(server_id, cid, payloads, chunk_map[cid])
            state_bodies[cid] = self._write_channel_state(
                server_id, cid, payloads, index_lines)
            await self.message_memory.set_episode_watermark(
                cid, spans[cid][-1].message_id)
            # Vault evidence never reaches global files
            if not server_vaulted and cid not in self.vaults.vaults:
                for data in payloads:
                    for entry in data.get("user_observations", []):
                        uid = str(entry.get("user_id", ""))
                        if uid.isdigit():
                            observations.setdefault(uid, []).extend(entry["observations"])

        # Round 2: first profiles + culture (skipped wholesale for vaulted servers)
        profiles_written, culture_written = 0, False
        round2 = [] if server_vaulted else await self._build_round2(
            server_id, observations, state_bodies, character)
        if round2:
            results2 = await self.batch.run(round2)
            for key, result in results2.items():
                if result.type != "succeeded":
                    logger.warning(f"Induction batch item {key} {result.type} - skipped")
                    continue
                try:
                    text = next(b.text for b in result.message.content if b.type == "text")
                    data = json.loads(text)
                except (StopIteration, json.JSONDecodeError) as e:
                    logger.warning(f"Induction batch item {key} unparseable ({e}) - skipped")
                    continue
                # "_" separator: the API constrains custom_id to [a-zA-Z0-9_-]
                kind, *rest = key.split("_")
                if kind == "profile":
                    self._apply_profile(rest[0], data)
                    profiles_written += 1
                elif kind == "culture":
                    self._apply_culture(server_id, data)
                    culture_written = True

        report = {"server": server_id, "channels": report_channels,
                  "profiles": profiles_written, "culture": culture_written}
        logger.info(f"Induction report for {server_id}: {report}")
        return report

    # ---------- Round 1 application ----------

    def _write_era_digests(self, server_id: str, cid: str,
                           payloads: list, chunks: list) -> list:
        """One era file per month touched (month of each chunk's last message).
        Returns the episode-index lines for the channel note."""
        months: dict = {}
        for idx, data in enumerate(payloads):
            month = chunks[idx][-1].timestamp.strftime("%Y-%m")
            agg = months.setdefault(month, {
                "summaries": [], "standing_facts": [], "memorable_moments": [],
                "running_jokes": [], "decisions": []})
            agg["summaries"].append(data["summary_markdown"])
            for k in ("standing_facts", "memorable_moments", "running_jokes", "decisions"):
                agg[k].extend(data[k])

        ep_dir = self.memory.resolve_path(
            self.memory.get_episodes_dir_path(server_id, cid))
        ep_dir.mkdir(parents=True, exist_ok=True)
        index_lines = []
        for month in sorted(months):
            agg = months[month]
            era_name = f"era_{month}_backlog.md"
            body = [ARCHAEOLOGY_HEADER, "", f"# Backlog: {month}", "",
                    "\n\n".join(agg["summaries"]), ""]
            for heading, key in (("Standing Facts", "standing_facts"),
                                 ("Memorable Moments", "memorable_moments"),
                                 ("Running Jokes", "running_jokes"),
                                 ("Decisions", "decisions")):
                if agg[key]:
                    body.append(f"## {heading}")
                    body.extend(f"- {item}" for item in agg[key])
                    body.append("")
            (ep_dir / era_name).write_text("\n".join(body), encoding="utf-8")
            index_lines.append(f"- {month} | Era digest: Backlog {month} | {era_name}")
        return index_lines

    def _write_channel_state(self, server_id: str, cid: str,
                             payloads: list, index_lines: list) -> str:
        """Seed the channel note from code-merged chunk ledgers."""
        from core.episode_manager import render_channel_state, split_channel_state

        def merged(key):
            out = []
            for data in payloads:
                for item in data[key]:
                    if item not in out:
                        out.append(item)
            return out[-LEDGER_CAP:]

        ledgers = {
            "standing_facts": merged("standing_facts"),
            "settled_questions": merged("decisions"),
            "used_jokes": merged("running_jokes"),
            "open_threads": [],
        }
        state_fs = self.memory.resolve_path(
            self.memory.get_channel_context_path(server_id, cid))
        state_fs.parent.mkdir(parents=True, exist_ok=True)
        existing_index = []
        if state_fs.exists():  # incremental top-up keeps prior episode index
            _, existing_index = split_channel_state(
                state_fs.read_text(encoding="utf-8"))
            archive_to_history(state_fs)
        content = (ARCHAEOLOGY_HEADER + "\n\n"
                   + render_channel_state(cid, ledgers, index_lines + existing_index))
        state_fs.write_text(content, encoding="utf-8")
        return content

    # ---------- Round 2 ----------

    async def _build_round2(self, server_id: str, observations: dict,
                            state_bodies: dict,
                            character: Optional[str] = None) -> list:
        model = self.config.api.consolidation_model
        requests = []
        for uid, obs in observations.items():
            if self.user_cache is not None:
                cached = await self.user_cache.get_user(uid)
                if cached is not None and cached.is_bot:
                    continue
            existing_fs = self.memory.resolve_path(
                self.memory.get_global_user_profile_path(uid))
            current = (existing_fs.read_text(encoding="utf-8")
                       if existing_fs.exists() else "(no profile yet)")
            obs_text = "\n".join(f"- {o}" for o in obs)
            requests.append({
                "custom_id": f"profile_{uid}",
                "params": {
                    "model": model,
                    "max_tokens": CONSOLIDATION_MAX_TOKENS,
                    "system": as_self(INDUCT_PROFILE_SYSTEM, self._personality, character, lived=False),
                    "messages": [{"role": "user", "content":
                        f"This server's id: {server_id}\n\n"
                        f"<current_profile>\n{current}\n</current_profile>\n\n"
                        f"<backlog_observations user_id=\"{uid}\">\n{obs_text}\n"
                        f"</backlog_observations>"}],
                    "output_config": memory_output_config(PROFILE_SCHEMA, model),
                },
            })
        if state_bodies:
            channels_summary = "\n\n".join(
                f"<channel id=\"{cid}\">\n{body}\n</channel>"
                for cid, body in state_bodies.items())
            requests.append({
                "custom_id": f"culture_{server_id}",
                "params": {
                    "model": model,
                    "max_tokens": CONSOLIDATION_MAX_TOKENS,
                    "system": as_self(CULTURE_SYSTEM_PROMPT, self._personality, character, lived=False),
                    "messages": [{"role": "user", "content":
                        f"<channels>\n{channels_summary}\n</channels>"}],
                    "output_config": memory_output_config(CULTURE_SCHEMA, model),
                },
            })
        return requests

    def _apply_profile(self, uid: str, data: dict) -> None:
        target = self.memory.resolve_path(
            self.memory.get_global_user_profile_path(uid))
        target.parent.mkdir(parents=True, exist_ok=True)
        archive_to_history(target)
        lines = render_profile(data).splitlines()
        at = 2 if len(lines) > 1 and lines[1].startswith("Known from:") else 1
        lines.insert(at, ARCHAEOLOGY_HEADER)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _apply_culture(self, server_id: str, data: dict) -> None:
        fs = self.memory.resolve_path(self.memory.get_server_culture_path(server_id))
        fs.parent.mkdir(parents=True, exist_ok=True)
        archive_to_history(fs)
        fs.write_text(f"{ARCHAEOLOGY_HEADER}\n\n{data['culture_markdown']}\n",
                      encoding="utf-8")
