"""
BotData - read-only views over a bot's artifacts for the dashboard.

Everything derives from the managed root: YAML configs, the three sqlite
DBs (opened read-only per call - the bot owns its WAL connections), the
memory/repository trees, logs, and the JSON state files. The daemon never
imports the bot's runtime objects.
"""

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional

import yaml

from core.internal_constants import estimate_cost_usd

from .paths import SupervisorRoot

logger = logging.getLogger(__name__)

# '2026-06-11 03:14:01,123 [INFO] core.reactive_engine: message'
_LOG_LINE = re.compile(r"^(\S+ \S+) \[(\w+)\] ([\w.]+): (.*)$")


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
        names = {r["id"]: dict(r) for r in rows}
        for r in _query(self.root.messages_db(bot_id),
                        "SELECT thread_id, name FROM threads"):
            names.setdefault(r["thread_id"], {
                "id": r["thread_id"], "name": r["name"] or r["thread_id"],
                "kind": "thread", "guild_id": None})
        return names

    @staticmethod
    def _display(names: dict, channel_id: str) -> str:
        """UI-facing channel label: '#general', 'DM · mara', or the raw id.
        The prototype matches streams to channels by THIS string."""
        meta = names.get(str(channel_id))
        if not meta:
            return str(channel_id)
        if meta["kind"] == "dm":
            return meta["name"]  # already "DM · user"
        return f"#{meta['name']}"

    @staticmethod
    def _server_color(server_id: str) -> str:
        palette = ["#b5703a", "#3a7ab5", "#6a9a4e", "#8a5fb0", "#b04f6a",
                   "#4fa3a0", "#a08c3e"]
        return palette[sum(ord(c) for c in str(server_id)) % len(palette)]

    # --- shared sub-reads ---------------------------------------------------

    def _followups_pending(self, bot_id: str, names: dict) -> List[dict]:
        items = []
        for path in sorted(self.root.memories_dir(bot_id).glob(
                "servers/*/followups.json")):
            server_id = path.parent.name
            for f in _read_json(path, {}).get("pending", []):
                items.append({
                    "channel": self._display(names, f.get("channel_id", "?")),
                    "server": (names.get(server_id) or {}).get("name", server_id),
                    "who": f.get("user_name", "?"),
                    "about": f.get("event", ""),
                    "fire_after": f.get("follow_up_after"),
                })
        return items

    def _active_watches(self, bot_id: str, names: dict) -> List[dict]:
        path = self.root.memories_dir(bot_id) / "global" / "watches.json"
        now = datetime.now(timezone.utc)
        items = []
        for w in _read_json(path, []):
            try:
                if datetime.fromisoformat(w["expires_at"]) <= now:
                    continue
            except (KeyError, ValueError):
                continue
            items.append({
                "origin": self._display(names, w.get("origin_channel_id", "?")),
                "target": (names.get(w.get("target_server_id", "")) or {}).get(
                    "name", w.get("target_server_id", "?")),
                "question": w.get("question", ""),
                "expires": w.get("expires_at"),
            })
        return items

    def _dm_rows(self, bot_id: str) -> List[dict]:
        return [dict(r) for r in _query(
            self.root.users_db(bot_id),
            "SELECT user_id, channel_id, last_message_at FROM dm_channels")]

    def _context_row(self, bot_id: str):
        rows = _query(
            self.root.states_db(bot_id),
            "SELECT channel_id, token_count, message_count FROM conversation_states"
            " WHERE bot_id = ? ORDER BY token_count DESC LIMIT 1", (bot_id,))
        return rows[0] if rows else None

    def _activity_7d(self, bot_id: str) -> List[int]:
        db = self.root.messages_db(bot_id)
        out = []
        for i in range(6, -1, -1):
            day = (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat()
            rows = _query(db, "SELECT COUNT(*) AS n FROM messages"
                              " WHERE substr(timestamp, 1, 10) = ?", (day,))
            out.append(rows[0]["n"] if rows else 0)
        return out

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

            ceiling = (config.get("api") or {}).get("context_tokens", 80000)
            ctx = self._context_row(bot_id)
            context_pct = round(100 * ctx["token_count"] / ceiling) if ctx else 0

            running = proc["running"]
            crashed = proc.get("crashed", False)
            health = ("stopped" if not running
                      else "warn" if (crashed or context_pct >= 85) else "ok")

            bots.append({
                "bot_id": bot_id,
                "running": running,
                "pid": proc["pid"],
                "uptime_s": proc["uptime_s"],
                "crashed": crashed,
                "model": (config.get("api") or {}).get("model", "?"),
                "servers": len((config.get("discord") or {}).get("servers", [])),
                "last_activity": last[0]["timestamp"] if last else None,
                "last_channel": (self._display(names, last[0]["channel_id"])
                                 if last else None),
                "messages_today": today[0]["n"] if today else 0,
                "episodes": episodes,
                "activity_7d": self._activity_7d(bot_id),
                "context_pct": context_pct,
                "followups": len(self._followups_pending(bot_id, names)),
                "watches": len(self._active_watches(bot_id, names)),
                "dms": len(self._dm_rows(bot_id)),
                "health": health,
            })
        return bots

    # --- status (shape is the PROTOTYPE's - it is normative) --------------------

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

        def last_event(kinds, columns="ts"):
            rows = _query(
                db, f"SELECT {columns} FROM events WHERE kind IN "
                    f"({','.join('?' * len(kinds))}) ORDER BY id DESC LIMIT 1",
                kinds)
            return rows[0] if rows else None

        # servers as objects with their channel labels (the monitor's rail)
        servers = []
        for sid in (config.get("discord") or {}).get("servers", []):
            sid = str(sid)
            chans = _query(db, "SELECT DISTINCT channel_id FROM messages"
                               " WHERE guild_id = ?", (sid,))
            servers.append({
                "id": sid,
                "name": (names.get(sid) or {}).get("name", sid),
                "color": self._server_color(sid),
                "channels": sorted(self._display(names, c["channel_id"])
                                   for c in chans),
            })

        dms = self._dm_rows(bot_id)
        dm_users = []
        for d in dms:
            label = (names.get(d["channel_id"]) or {}).get("name", "")
            dm_users.append(label.replace("DM · ", "") or d["user_id"])

        ctx = self._context_row(bot_id)
        reseeds = _query(db, "SELECT COUNT(*) AS n FROM events"
                             " WHERE kind = 'reseed' AND ts >= ?", (_today_iso(),))
        context = {
            "channel": self._display(names, ctx["channel_id"]) if ctx else None,
            "live_messages": ctx["message_count"] if ctx else 0,
            "tokens": ctx["token_count"] if ctx else 0,
            "ceiling": (config.get("api") or {}).get("context_tokens", 80000),
            "reseeds_today": reseeds[0]["n"] if reseeds else 0,
        }

        last_reactive = last_event(["mention", "dm", "scan"], "ts, channel_id")
        last_agentic = last_event(["proactive", "followup", "watch", "relay"])
        dm_active_today = sum(1 for r in events_today if r["kind"] == "dm")

        eng = _read_json(self.root.engagement_stats(bot_id), {})
        weighed = sum(1 for r in events_today if r["kind"] == "proactive")

        return {
            "running": running,
            "crashed": proc.get("crashed", False),
            "pid": proc["pid"],
            "uptime_s": proc["uptime_s"],
            "model": (config.get("api") or {}).get("model", "?"),
            "servers": servers,
            "dm_users": dm_users,
            "reactive": {
                "state": "idle" if running else "off",
                "last_response": last_reactive["ts"] if last_reactive else None,
                "last_channel": (self._display(names, last_reactive["channel_id"])
                                 if last_reactive else None),
            },
            "agentic": {
                "state": "sleeping" if running else "off",
                "last_check": last_agentic["ts"] if last_agentic else None,
                "next_check": None,  # the loop lives in the bot; honest unknown
                "followups_pending": len(self._followups_pending(bot_id, names)),
            },
            "context": context,
            "tokens_today": tokens,
            "cost_today_usd": estimate_cost_usd(
                tokens, (config.get("api") or {}).get("model", "")),
            "dms": {"open": len(dms), "active_today": dm_active_today},
            "followups": self._followups_pending(bot_id, names),
            "watches": self._active_watches(bot_id, names),
            "vaults": {
                "dm_vaults": len(dms),
                "configured": len(config.get("vaults") or []),
                "grants": [],
                "note": "no cross-server grants",
            },
            "engagement": {
                "sent": eng.get("total_proactive", 0),
                "replied": eng.get("total_engaged", 0),
                "weighed_today": weighed,
                "by_hour": eng.get("by_hour", {}),
            },
        }

    # --- the monitor's four feeds (trace / raw log / episodes / skills) ---------

    async def trace(self, bot_id: str, tail: int = 50) -> List[dict]:
        """Turn-events in the prototype's trace shape, newest-last."""
        db = self.root.messages_db(bot_id)
        names = self._names(bot_id)
        rows = _query(db, "SELECT ts, kind, server_id, channel_id, payload"
                          " FROM events WHERE kind NOT IN ('reseed', 'skill')"
                          " ORDER BY id DESC LIMIT ?", (tail,))
        out = []
        for r in rows:
            p = json.loads(r["payload"]) or {}
            out.append({
                "ts": r["ts"],
                "kind": r["kind"],
                "server_id": r["server_id"],
                "server_name": ((names.get(r["server_id"]) or {}).get("name")
                                if r["server_id"] else None),
                "channel": self._display(names, r["channel_id"]),
                "triggers": p.get("triggers", []),
                "scan_count": p.get("scan_count"),
                "thinking": p.get("thinking", ""),
                "tool_calls": p.get("tool_calls", []),
                "response": p.get("response"),
                "decision": p.get("decision"),
                "provenance": p.get("provenance"),
                "tokens": p.get("tokens", {}),
            })
        return out

    async def episodes_list(self, bot_id: str, tail: int = 50) -> List[dict]:
        db = self.root.messages_db(bot_id)
        names = self._names(bot_id)
        rows = _query(db, "SELECT ts, server_id, channel_id, payload FROM events"
                          " WHERE kind = 'reseed' ORDER BY id DESC LIMIT ?", (tail,))
        out = []
        for r in rows:
            p = json.loads(r["payload"]) or {}
            out.append({
                "ts": r["ts"],
                "channel": self._display(names, r["channel_id"]),
                "server_name": ((names.get(r["server_id"]) or {}).get("name")
                                if r["server_id"] else None),
                "reason": p.get("reason", "idle"),
                "retained": p.get("retained", 0),
                "tokens_before": p.get("tokens_before", 0),
                "title": p.get("episode_title", ""),
                "path": p.get("episodes_dir", ""),
                "summary": p.get("episode_summary", ""),
            })
        return out

    async def skills_list(self, bot_id: str, tail: int = 50) -> List[dict]:
        db = self.root.messages_db(bot_id)
        names = self._names(bot_id)
        rows = _query(db, "SELECT ts, server_id, channel_id, payload FROM events"
                          " WHERE kind = 'skill' ORDER BY id DESC LIMIT ?", (tail,))
        out = []
        for r in rows:
            p = json.loads(r["payload"]) or {}
            out.append({
                "ts": r["ts"],
                "channel": self._display(names, r["channel_id"]),
                "server_name": ((names.get(r["server_id"]) or {}).get("name")
                                if r["server_id"] else None),
                "skill": "request_skill",
                "name": p.get("skill_name", "?"),
                "container": "fresh",  # request_skill always rebuilds (v0.5 rule)
                "trigger": p.get("replace") and f"replacing {p['replace']}" or "",
                "outcome": "loaded",
                "tokens": p.get("tokens", {}),
            })
        return out

    def main_log_entries(self, bot_id: str, tail: int = 200) -> List[dict]:
        """Parse the bot's main log into the UI's {ts, level, src, msg} rows.
        Format: '%(asctime)s [%(levelname)s] %(name)s: %(message)s'."""
        entries = []
        for line in self.log_tail(bot_id, "main", tail):
            m = _LOG_LINE.match(line)
            if m:
                entries.append({"ts": m.group(1), "level": m.group(2),
                                "src": m.group(3), "msg": m.group(4)})
            elif entries:
                entries[-1]["msg"] += "\n" + line  # traceback continuation
            else:
                entries.append({"ts": "", "level": "INFO", "src": "?", "msg": line})
        return entries

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
        """Nested tree in the prototype's node shape (content NOT embedded -
        files are fetched on click)."""
        def walk(d: Path, rel: str) -> list:
            entries = []
            for p in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name)):
                child_rel = f"{rel}/{p.name}" if rel else p.name
                if p.is_dir():
                    entries.append({"name": p.name, "type": "dir",
                                    "children": walk(p, child_rel)})
                else:
                    stat = p.stat()
                    entries.append({
                        "name": p.name, "type": "file", "path": child_rel,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc).isoformat(),
                        "kind": (p.suffix.lstrip(".") or "txt"),
                    })
            return entries
        return walk(base, "") if base.exists() else []

    def memory_tree(self, bot_id: str) -> dict:
        return {"root": f"memories/{bot_id}",
                "tree": self._tree(self.root.memories_dir(bot_id))}

    def repository_tree(self, bot_id: str) -> dict:
        return {"root": f"repository/{bot_id}",
                "tree": self._tree(self.root.repository_dir(bot_id))}

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
