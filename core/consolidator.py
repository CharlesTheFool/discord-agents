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
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.batch_client import BatchClient
from core.internal_constants import (
    CONSOLIDATION_INTERVAL_DAYS,
    CONSOLIDATION_CULTURE_EVERY_N_RUNS,
    CONSOLIDATION_ERA_AGE_DAYS,
    CONSOLIDATION_HISTORY_KEEP,
    CONSOLIDATION_EVIDENCE_MESSAGES,
    CONSOLIDATION_EVIDENCE_EPISODES,
    CONSOLIDATION_MAX_TOKENS,
)

logger = logging.getLogger(__name__)


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
        self._running = False

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
        if stamp is None:
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

        # Passes 1-4 land in the next tasks; the stamp seals a completed run
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
