"""Jobs (spec 7.3): every workshop command and, from 0.14.1, every worker is a
row here, so a long command or a bot restart never loses track of work.

One table in messages.db, sharing MessageMemory's connection (the bot
process is the only writer). A row is `running` until it finishes `done`,
`failed`, or `interrupted` (its sandbox job was gone at boot); finished rows
never run again. `notified` records whether its completion note went out.
"""
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional

import aiosqlite

RUNNING, DONE, FAILED, INTERRUPTED = "running", "done", "failed", "interrupted"
FINISHED = (DONE, FAILED, INTERRUPTED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    bot_id          TEXT NOT NULL,
    scope_key       TEXT NOT NULL,
    home_server_id  TEXT,
    home_channel_id TEXT NOT NULL,
    kind            TEXT NOT NULL,          -- shell | worker
    executor_key    TEXT NOT NULL,
    command         TEXT NOT NULL,          -- the shell command, or the worker's task
    foreground      INTEGER NOT NULL,       -- 1 while a turn waits on it
    state           TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    exit_code       INTEGER,
    result          TEXT,                   -- worker report / failure reason
    log_path        TEXT,                   -- workspace path of the full log
    sandbox_job_id  TEXT,
    notified        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_scope_state ON jobs(scope_key, state);
"""


class IllegalTransition(Exception):
    pass


@dataclass(frozen=True)
class JobRecord:
    id: str
    bot_id: str
    scope_key: str
    home_server_id: Optional[str]
    home_channel_id: str
    kind: str
    executor_key: str
    command: str
    foreground: bool
    state: str
    started_at: str
    finished_at: Optional[str]
    exit_code: Optional[int]
    result: Optional[str]
    log_path: Optional[str]
    sandbox_job_id: Optional[str]
    notified: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(row) -> JobRecord:
    d = dict(row)
    d["foreground"] = bool(d["foreground"])
    d["notified"] = bool(d["notified"])
    return JobRecord(**d)


class JobStore:
    def __init__(self, db: aiosqlite.Connection, bot_id: str):
        self.db = db
        self.bot_id = bot_id

    async def create_schema(self) -> None:
        await self.db.executescript(SCHEMA)
        await self.db.commit()

    async def create(self, *, scope_key: str, home_server_id: Optional[str],
                     home_channel_id: str, kind: str, executor_key: str,
                     command: str, foreground: bool) -> JobRecord:
        job_id = "job_" + secrets.token_hex(6)
        await self.db.execute(
            "INSERT INTO jobs (id, bot_id, scope_key, home_server_id, home_channel_id, kind,"
            " executor_key, command, foreground, state, started_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, self.bot_id, scope_key,
             None if home_server_id in (None, "DM") else str(home_server_id),
             str(home_channel_id), kind, executor_key, command, int(foreground),
             RUNNING, _now()))
        await self.db.commit()
        return await self.get(job_id)

    async def get(self, job_id: str) -> Optional[JobRecord]:
        cursor = await self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        return _record(row) if row else None

    async def _update_running(self, job_id: str, sql: str, params: tuple) -> None:
        """Apply an update that is only legal while the job runs."""
        cursor = await self.db.execute(
            f"UPDATE jobs SET {sql} WHERE id = ? AND state = ?", (*params, job_id, RUNNING))
        await self.db.commit()
        if cursor.rowcount != 1:
            current = await self.get(job_id)
            raise IllegalTransition(
                f"{job_id} is {current.state if current else 'unknown'}, not running")

    async def set_sandbox_job(self, job_id: str, sandbox_job_id: str, log_path: str) -> None:
        await self._update_running(job_id, "sandbox_job_id = ?, log_path = ?",
                                   (sandbox_job_id, log_path))

    async def to_background(self, job_id: str) -> None:
        """A foreground command outlived the ceiling: nobody waits on it now."""
        await self._update_running(job_id, "foreground = 0", ())

    async def mark_finished(self, job_id: str, state: str, *, exit_code: Optional[int] = None,
                            result: Optional[str] = None, notified: bool = False) -> None:
        """notified=True when the outcome was already delivered (a foreground
        command whose result went back as its tool result)."""
        if state not in FINISHED:
            raise IllegalTransition(f"{state!r} is not a finished state")
        await self._update_running(
            job_id, "state = ?, finished_at = ?, exit_code = ?, result = ?, notified = ?",
            (state, _now(), exit_code, result, int(notified)))

    async def mark_interrupted(self, job_ids: Iterable[str], reason: str) -> None:
        """Boot reconciliation: these rows' sandbox jobs are gone."""
        for job_id in job_ids:
            await self.mark_finished(job_id, INTERRUPTED, result=reason)

    async def running(self, scope_key: Optional[str] = None) -> List[JobRecord]:
        sql, params = "SELECT * FROM jobs WHERE state = ?", [RUNNING]
        if scope_key is not None:
            sql += " AND scope_key = ?"
            params.append(scope_key)
        cursor = await self.db.execute(sql + " ORDER BY started_at", params)
        return [_record(r) for r in await cursor.fetchall()]

    async def background_count(self, scope_key: str) -> int:
        """Running work that counts against the scope's cap (spec 5.10):
        background jobs, converted foreground commands and workers; a
        command a turn is still waiting on doesn't count."""
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE scope_key = ? AND state = ? AND foreground = 0",
            (scope_key, RUNNING))
        return (await cursor.fetchone())[0]

    async def unnotified_finished(self) -> List[JobRecord]:
        cursor = await self.db.execute(
            "SELECT * FROM jobs WHERE state != ? AND notified = 0 ORDER BY finished_at", (RUNNING,))
        return [_record(r) for r in await cursor.fetchall()]

    async def mark_notified(self, job_id: str) -> None:
        await self.db.execute("UPDATE jobs SET notified = 1 WHERE id = ?", (job_id,))
        await self.db.commit()
