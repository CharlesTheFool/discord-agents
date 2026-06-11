"""
BotData - read-only views over a bot's artifacts for the dashboard.

Everything derives from the managed root: YAML configs, the three sqlite
DBs (opened read-only per call - the bot owns its WAL connections), the
memory/repository trees, logs, and the JSON state files. The daemon never
imports the bot's runtime objects.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional

import yaml

from .paths import SupervisorRoot

logger = logging.getLogger(__name__)


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _connect_ro(path: Path) -> Optional[sqlite3.Connection]:
    """Read-only sqlite open; None when the DB doesn't exist yet."""
    if not path.exists():
        return None
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _query(path: Path, sql: str, params=()) -> List[sqlite3.Row]:
    con = _connect_ro(path)
    if con is None:
        return []
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        logger.debug(f"Query failed on {path.name}: {e}")
        return []
    finally:
        con.close()


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


class BotData:
    def __init__(self, root: SupervisorRoot):
        self.root = root

    # --- config -----------------------------------------------------------

    def load_config(self, bot_id: str) -> dict:
        path = self.root.bot_yaml(bot_id)
        with open(path, encoding="utf-8-sig") as f:
            return yaml.safe_load(f) or {}

    # --- names ------------------------------------------------------------

    def _names(self, bot_id: str) -> dict:
        rows = _query(self.root.messages_db(bot_id),
                      "SELECT id, name, kind, guild_id FROM channel_names")
        return {r["id"]: dict(r) for r in rows}

    # --- bots list ----------------------------------------------------------

    async def list_bots(self, running_status: Callable[[str], dict]) -> List[dict]:
        bots = []
        for bot_id in self.root.bot_ids():
            try:
                config = self.load_config(bot_id)
            except Exception:
                logger.exception(f"Unreadable config for {bot_id}")
                config = {}
            proc = running_status(bot_id)
            db = self.root.messages_db(bot_id)
            names = self._names(bot_id)

            last = _query(db, "SELECT channel_id, timestamp FROM messages"
                              " ORDER BY id DESC LIMIT 1")
            today = _query(db, "SELECT COUNT(*) AS n FROM messages"
                               " WHERE timestamp >= ?", (_today_iso(),))
            episodes = len(list(self.root.memories_dir(bot_id).rglob("episodes/*.md")))

            last_channel = None
            if last:
                meta = names.get(last[0]["channel_id"])
                last_channel = meta["name"] if meta else last[0]["channel_id"]

            bots.append({
                "bot_id": bot_id,
                "running": proc["running"],
                "pid": proc["pid"],
                "uptime_s": proc["uptime_s"],
                "crashed": proc.get("crashed", False),
                "model": (config.get("api") or {}).get("model", "?"),
                "servers": len((config.get("discord") or {}).get("servers", [])),
                "last_activity": last[0]["timestamp"] if last else None,
                "last_channel": last_channel,
                "messages_today": today[0]["n"] if today else 0,
                "episodes": episodes,
            })
        return bots

    # --- status ---------------------------------------------------------------

    async def status(self, bot_id: str, proc: dict) -> dict:
        config = self.load_config(bot_id)
        db = self.root.messages_db(bot_id)
        names = self._names(bot_id)
        running = proc["running"]

        # tokens today + last activity timestamps, all from the events table
        events_today = _query(
            db, "SELECT kind, ts, payload FROM events WHERE ts >= ?", (_today_iso(),))
        tokens = {"uncached_in": 0, "cache_read": 0, "out": 0}
        for row in events_today:
            t = (json.loads(row["payload"]) or {}).get("tokens") or {}
            for k in tokens:
                tokens[k] += t.get(k, 0) or 0

        def last_event_ts(kinds):
            rows = _query(
                db, "SELECT ts FROM events WHERE kind IN (%s)"
                    " ORDER BY id DESC LIMIT 1" % ",".join("?" * len(kinds)),
                kinds)
            return rows[0]["ts"] if rows else None

        # live context: the busiest open conversation state
        ctx_rows = _query(
            self.root.states_db(bot_id),
            "SELECT channel_id, token_count FROM conversation_states"
            " WHERE bot_id = ? ORDER BY token_count DESC LIMIT 1", (bot_id,))
        context = None
        if ctx_rows:
            cid = ctx_rows[0]["channel_id"]
            meta = names.get(cid)
            reseeds = _query(
                db, "SELECT COUNT(*) AS n FROM events"
                    " WHERE kind = 'reseed' AND ts >= ?", (_today_iso(),))
            context = {
                "channel": cid,
                "channel_name": meta["name"] if meta else cid,
                "tokens": ctx_rows[0]["token_count"],
                "ceiling": (config.get("api") or {}).get("context_tokens", 80000),
                "reseeds_today": reseeds[0]["n"] if reseeds else 0,
            }

        followups = 0
        for path in self.root.memories_dir(bot_id).glob("servers/*/followups.json"):
            followups += len(_read_json(path, {}).get("pending", []))

        watches_path = self.root.memories_dir(bot_id) / "global" / "watches.json"
        now = datetime.now(timezone.utc)
        watches = [w for w in _read_json(watches_path, [])
                   if datetime.fromisoformat(w["expires_at"]) > now]

        dm_rows = _query(self.root.users_db(bot_id),
                         "SELECT COUNT(*) AS n FROM dm_channels")

        eng = _read_json(self.root.engagement_stats(bot_id), {})
        weighed = sum(1 for r in events_today if r["kind"] == "proactive")

        return {
            "running": running,
            "crashed": proc.get("crashed", False),
            "pid": proc["pid"],
            "uptime_s": proc["uptime_s"],
            "reactive": {
                "state": "idle" if running else "off",
                "last_response": last_event_ts(["mention", "dm", "scan"]),
            },
            "agentic": {
                "state": "sleeping" if running else "off",
                "last_action": last_event_ts(["proactive", "followup", "watch", "relay"]),
            },
            "context": context,
            "tokens_today": tokens,
            "dms": dm_rows[0]["n"] if dm_rows else 0,
            "followups_pending": followups,
            "watches": len(watches),
            "vaults": len(config.get("vaults") or []),
            "engagement": {
                "sent": eng.get("total_proactive", 0),
                "replied": eng.get("total_engaged", 0),
                "weighed_today": weighed,
                "by_hour": eng.get("by_hour", {}),
            },
        }

    # --- stats ------------------------------------------------------------------

    async def stats(self, bot_id: str) -> dict:
        db = self.root.messages_db(bot_id)
        count = _query(db, "SELECT COUNT(*) AS n FROM messages")
        attachments = _query(db, "SELECT COUNT(*) AS n FROM attachments")
        per_day = []
        for i in range(6, -1, -1):
            day = (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat()
            rows = _query(db, "SELECT COUNT(*) AS n FROM messages"
                              " WHERE substr(timestamp, 1, 10) = ?", (day,))
            per_day.append({"d": day, "msgs": rows[0]["n"] if rows else 0})

        mem_dir = self.root.memories_dir(bot_id)
        repo_dir = self.root.repository_dir(bot_id)
        return {
            "messages_stored": count[0]["n"] if count else 0,
            "episodes": len(list(mem_dir.rglob("episodes/*.md"))),
            "attachments": attachments[0]["n"] if attachments else 0,
            "memory_files": sum(1 for p in mem_dir.rglob("*") if p.is_file()),
            "repository_files": sum(1 for p in repo_dir.rglob("*") if p.is_file())
            if repo_dir.exists() else 0,
            "per_day": per_day,
        }

    # --- trees + files ------------------------------------------------------------

    @staticmethod
    def _tree(base: Path) -> list:
        """Nested {name, type, children|path} tree, stable order."""
        def walk(d: Path, rel: str) -> list:
            entries = []
            for p in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name)):
                child_rel = f"{rel}/{p.name}" if rel else p.name
                if p.is_dir():
                    entries.append({"name": p.name, "type": "dir",
                                    "children": walk(p, child_rel)})
                else:
                    entries.append({"name": p.name, "type": "file",
                                    "path": child_rel})
            return entries
        return walk(base, "") if base.exists() else []

    def memory_tree(self, bot_id: str) -> list:
        return self._tree(self.root.memories_dir(bot_id))

    def repository_tree(self, bot_id: str) -> list:
        return self._tree(self.root.repository_dir(bot_id))

    def memory_file(self, bot_id: str, rel_path: str) -> str:
        p = self.root.jailed(self.root.memories_dir(bot_id), rel_path)
        return p.read_text(encoding="utf-8")

    def write_memory_file(self, bot_id: str, rel_path: str, content: str) -> None:
        p = self.root.jailed(self.root.memories_dir(bot_id), rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def repository_file(self, bot_id: str, rel_path: str) -> bytes:
        p = self.root.jailed(self.root.repository_dir(bot_id), rel_path)
        return p.read_bytes()

    # --- logs -----------------------------------------------------------------------

    def log_tail(self, bot_id: str, which: str, tail: int = 200) -> List[str]:
        path = self.root.log_file(bot_id, which)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-tail:]

    # --- channels nav ------------------------------------------------------------------

    async def channels(self, bot_id: str) -> dict:
        db = self.root.messages_db(bot_id)
        names = self._names(bot_id)
        threads = {r["thread_id"]: r for r in _query(
            db, "SELECT thread_id, parent_id, name FROM threads")}

        rows = _query(db, """
            SELECT channel_id, guild_id, COUNT(*) AS total,
                   SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END) AS today,
                   MAX(timestamp) AS last_ts
            FROM messages GROUP BY channel_id""", (_today_iso(),))

        # DM rail comes from the registry (a DM exists the moment it's
        # registered, messages or not); activity joins in from messages.db
        activity = {r["channel_id"]: r for r in rows}
        dms: list = []
        for r in _query(self.root.users_db(bot_id),
                        "SELECT user_id, channel_id, last_message_at FROM dm_channels"):
            cid = r["channel_id"]
            meta = names.get(cid)
            act = activity.get(cid)
            dms.append({
                "channel_id": cid,
                "user_id": r["user_id"],
                "name": meta["name"] if meta else f"DM {r['user_id']}",
                "activity_today": act["today"] if act else 0,
                "last_activity": act["last_ts"] if act else r["last_message_at"],
            })

        servers: dict = {}
        for r in rows:
            cid, gid = r["channel_id"], r["guild_id"]
            if gid == "DM":
                continue  # already on the rail via the registry
            entry = servers.setdefault(gid, {
                "id": gid,
                "name": (names.get(gid) or {}).get("name", gid),
                "channels": [],
            })
            if cid in threads:
                t = threads[cid]
                entry["channels"].append({
                    "id": cid, "name": t["name"] or cid, "kind": "thread",
                    "parent_id": t["parent_id"],
                    "activity_today": r["today"], "last_activity": r["last_ts"]})
            else:
                meta = names.get(cid)
                entry["channels"].append({
                    "id": cid, "name": meta["name"] if meta else cid,
                    "kind": "channel", "parent_id": None,
                    "activity_today": r["today"], "last_activity": r["last_ts"]})

        for s in servers.values():
            s["channels"].sort(key=lambda c: c["last_activity"] or "", reverse=True)
        dms.sort(key=lambda d: d["last_activity"] or "", reverse=True)
        return {"servers": sorted(servers.values(), key=lambda s: s["name"]),
                "dms": dms}

    # --- the channel stream ----------------------------------------------------------------

    async def stream(self, bot_id: str, channel_id: str,
                     limit: int = 50, before: Optional[str] = None) -> List[dict]:
        """Messages ⋈ events for one channel, newest-first; `before` is an
        ISO-timestamp cursor (exclusive)."""
        db = self.root.messages_db(bot_id)

        msg_sql = ("SELECT message_id, author_name, author_id, content,"
                   " timestamp, is_bot, has_attachments FROM messages"
                   " WHERE channel_id = ? AND is_system = 0")
        ev_sql = "SELECT id, ts, kind, payload FROM events WHERE channel_id = ?"
        msg_params: list = [str(channel_id)]
        ev_params: list = [str(channel_id)]
        if before:
            msg_sql += " AND timestamp < ?"
            ev_sql += " AND ts < ?"
            msg_params.append(before)
            ev_params.append(before)
        msg_sql += " ORDER BY timestamp DESC LIMIT ?"
        ev_sql += " ORDER BY ts DESC LIMIT ?"
        msg_params.append(limit)
        ev_params.append(limit)

        items = [{
            "type": "message",
            "id": r["message_id"],
            "ts": r["timestamp"],
            "author": r["author_name"],
            "author_id": r["author_id"],
            "is_bot": bool(r["is_bot"]),
            "content": r["content"],
            "has_attachments": bool(r["has_attachments"]),
        } for r in _query(db, msg_sql, msg_params)]

        items += [{
            "type": "event",
            "id": r["id"],
            "ts": r["ts"],
            "kind": r["kind"],
            "payload": json.loads(r["payload"]),
        } for r in _query(db, ev_sql, ev_params)]

        items.sort(key=lambda i: i["ts"], reverse=True)
        return items[:limit]
