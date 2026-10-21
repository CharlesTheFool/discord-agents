"""Projects (spec 6, 0.15.0): work that runs over days, kept as a memory note.

A project is `projects/<slug>.md` in a scope's memory tree, with its files in
/workspace/projects/<slug>/. The loop reads the note's frontmatter without a
model call: `status` (active | paused | done), `next_check` (an ISO date or
datetime, local time) and `home_channel` (stamped by the memory executor with
the channel the note was created from).

Discovery is mechanical and scope-bound: a note counts only where its tree
belongs to a real scope and its home channel resolves to that same scope.
Anything else is skipped and logged once per file version, never guessed: a
model-written note must not aim turns at another scope or server.
"""
import asyncio
import logging
import pathlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Tuple, Union

import yaml

from core.internal_constants import PROJECT_CHECK_FIRST_SECONDS, PROJECT_CHECK_SECONDS
from core.workshop.notes import NOTE_HEAD, NOTE_TAIL, quiet_now
from core.workshop.scopes import Scope

logger = logging.getLogger(__name__)

STATUSES = ("active", "paused", "done")
MANIFEST_MAX = 5
# Every place a scope keeps its tree, as globs under the bot's memory root
TREE_GLOBS = ("servers/*", "servers/*/channels/*", "servers/*/channels/*/threads/*",
              "global/dms/*", "global/dms/_unresolved/*")

# channel id -> (server id or None for a DM, thread parent or None, is_thread),
# or None when the channel isn't known right now
ChannelPlace = Callable[[str], Optional[Tuple[Optional[str], Optional[str], bool]]]


@dataclass(frozen=True)
class Project:
    slug: str
    path: str                  # memory path, /memories/{bot}/.../projects/{slug}.md
    status: str
    next_check: Union[date, datetime, None]
    home_channel: str
    home_server: Optional[str]
    home_parent: Optional[str]   # the home channel's thread parent
    scope: Scope
    mtime: float

    def due(self, now: datetime) -> bool:
        """now is aware local time; a naive next_check is local, a date is due
        from that day's start."""
        if self.status != "active" or self.next_check is None:
            return False
        if isinstance(self.next_check, datetime):
            check = self.next_check
            return check <= (now if check.tzinfo else now.replace(tzinfo=None))
        return self.next_check <= now.date()

    @property
    def check_key(self) -> str:
        """Which next_check a fired check-in answered (fire once per value)."""
        return self.next_check.isoformat() if self.next_check is not None else ""


def frontmatter(text: str) -> Optional[dict]:
    """The YAML block between a leading --- and the next --- line, or None."""
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def with_home(text: str, channel_id: str) -> Optional[str]:
    """text with home_channel added to its frontmatter (a frontmatter block
    is opened if there's none), or None when it already names one."""
    data = frontmatter(text)
    if data is not None and data.get("home_channel") not in (None, ""):
        return None
    line = f"home_channel: '{channel_id}'"
    body = text.lstrip("﻿")
    if data is not None:
        return body.replace("---", f"---\n{line}", 1)
    return f"---\n{line}\n---\n{body}"


def _next_check(value) -> Union[date, datetime, None]:
    """YAML gives dates and second-precision timestamps as objects; anything
    else must parse as ISO. Raises ValueError when it doesn't."""
    if value is None or isinstance(value, (date, datetime)):
        return value
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return date.fromisoformat(text)


def tree_scope(tree: str, vaults) -> Optional[Tuple[Scope, Optional[str]]]:
    """(the scope whose tree this is, its server id), or None when no scope
    keeps its notes here (a channel that isn't a vault has no tree)."""
    parts = tree.split("/")
    if len(parts) == 2 and parts[0] == "servers":
        return Scope("server", parts[1]), parts[1]
    if len(parts) == 4 and parts[0] == "servers" and parts[2] == "channels" and parts[3] in vaults.vaults:
        return Scope("vault", parts[3]), parts[1]
    if (len(parts) == 6 and parts[0] == "servers" and parts[2:5:2] == ["channels", "threads"]
            and parts[5] in vaults.vaults):
        return Scope("vault", parts[5]), parts[1]
    if parts[:3] == ["global", "dms", "_unresolved"] and len(parts) == 4:
        return Scope("dm-channel", parts[3]), None
    if parts[:2] == ["global", "dms"] and len(parts) == 3 and parts[2] != "_unresolved":
        return Scope("dm", parts[2]), None
    return None


class ProjectBook:
    """Reads project notes; never writes them (the memory tool does)."""

    def __init__(self, memory_root: pathlib.Path, bot_id: str, vaults, channel_place: ChannelPlace):
        self.root = memory_root          # memories/{bot_id}
        self.bot_id = bot_id
        self.vaults = vaults
        self.channel_place = channel_place
        self._logged: Dict[str, tuple] = {}   # path -> the file version already logged
        self.skipped: List[Tuple[str, str, str]] = []   # newly logged: (path, reason, home channel)

    def in_scope(self, scope: Scope, tree: str) -> List[Project]:
        """Valid projects in one scope's tree (any status)."""
        found = tree_scope(tree, self.vaults)
        if found is None or found[0] != scope:
            return []
        return self._read_tree(tree, scope, found[1])

    def everywhere(self) -> List[Project]:
        """Valid projects in every scope's tree, for the check-in tick."""
        out = []
        for pattern in TREE_GLOBS:
            for folder in sorted(self.root.glob(pattern)):
                tree = folder.relative_to(self.root).as_posix()
                found = tree_scope(tree, self.vaults) if (folder / "projects").is_dir() else None
                if found is not None:
                    out += self._read_tree(tree, *found)
        return out

    def _read_tree(self, tree: str, scope: Scope, server_id: Optional[str]) -> List[Project]:
        folder = self.root / tree / "projects"
        if not folder.is_dir():
            return []
        out = []
        for fs_path in sorted(folder.glob("*.md")):
            if fs_path.is_symlink() or not fs_path.is_file():
                continue
            project = self._read(fs_path, f"/memories/{self.bot_id}/{tree}/projects/{fs_path.name}",
                                 scope, server_id)
            if project is not None:
                out.append(project)
        return out

    def _read(self, fs_path: pathlib.Path, path: str, scope: Scope,
              server_id: Optional[str]) -> Optional[Project]:
        stat = fs_path.stat()
        data = frontmatter(fs_path.read_text(encoding="utf-8", errors="replace"))
        if data is None:
            return self._skip(path, stat, "no frontmatter")
        status = str(data.get("status", "")).strip().lower()
        if status not in STATUSES:
            return self._skip(path, stat, f"status {data.get('status')!r} isn't active, paused or done")
        try:
            next_check = _next_check(data.get("next_check"))
        except ValueError:
            return self._skip(path, stat, f"next_check {data.get('next_check')!r} isn't an ISO date")
        home = str(data.get("home_channel") or "").strip()
        if not home.isdigit():
            return self._skip(path, stat, "no home_channel")
        place = self.channel_place(home)
        if place is None:
            return self._skip(path, stat, f"home channel {home} isn't known", home)
        home_server, parent, is_thread = place
        if home_server != server_id or self.vaults.resolve_scope(
                home_server, home, parent_id=parent, is_thread=is_thread) != scope:
            return self._skip(path, stat, f"home channel {home} is outside the scope that holds the note", home)
        self._logged.pop(path, None)
        return Project(slug=fs_path.stem, path=path, status=status, next_check=next_check,
                       home_channel=home, home_server=home_server, home_parent=parent,
                       scope=scope, mtime=stat.st_mtime)

    def _skip(self, path: str, stat, reason: str, home: str = "") -> None:
        version = (stat.st_mtime_ns, stat.st_size)
        if self._logged.get(path) != version:
            self._logged[path] = version
            self.skipped.append((path, reason, home))
            logger.warning(f"project note skipped ({reason}): {path}")
        return None

    def fs_path(self, path: str) -> pathlib.Path:
        return self.root / path[len(f"/memories/{self.bot_id}/"):]


def manifest(projects: List[Project], tree_path: str) -> str:
    """<projects> for the volatile tail: unfinished projects, newest first."""
    live = sorted((p for p in projects if p.status != "done"), key=lambda p: p.mtime, reverse=True)
    if not live:
        return ""
    lines = ["<projects>", f"Your projects here (notes in {tree_path}/projects/):"]
    for p in live[:MANIFEST_MAX]:
        check = f", next check {p.check_key}" if p.next_check is not None and p.status == "active" else ""
        lines.append(f"- {p.slug}: {p.status}{check}")
    if len(live) > MANIFEST_MAX:
        lines.append(f"... and {len(live) - MANIFEST_MAX} more in that folder.")
    lines += ["Ambient context, not a task: pick one up when it's relevant or its check comes round.",
              "</projects>"]
    return "\n".join(lines)


def checkin_note(project: Project) -> str:
    """What a due project tells its home channel (persisted, like a job's note)."""
    return NOTE_HEAD + (
        f'Your project "{project.slug}" is due a look: its next_check ({project.check_key}) '
        f"has come round. Its note: {project.path}\n"
        "See where it stands and carry on if there's something to do. Move next_check to when "
        "you'd like to look in again, or mark it paused or done: it won't come round again "
        "until next_check changes.") + NOTE_TAIL


class CheckIns:
    """The hourly check-in tick (spec 6). An active project whose next_check
    has passed gets a persisted note and one may-stay-silent turn in its home
    channel, once per next_check value: a turn that doesn't move it leaves
    the project waiting, not firing every hour. Quiet hours and the daily
    caps on unprompted turns defer a check-in to a later tick, never drop it.
    Owned by the workshop, not the agentic engine, which exists only when
    agentic.enabled."""

    def __init__(self, book: ProjectBook, workshop, notes, request_turn, config, limits, events=None):
        self.book = book
        self.workshop = workshop
        self.notes = notes
        self.request_turn = request_turn
        self.config = config
        self.limits = limits
        self.events = events
        self._deferred: set = set()   # (path, check, reason) already reported

    async def run(self) -> None:
        await asyncio.sleep(PROJECT_CHECK_FIRST_SECONDS)
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("project check-in tick failed")
            await asyncio.sleep(PROJECT_CHECK_SECONDS)

    async def tick(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now().astimezone()
        state = self.workshop.state
        projects = self.book.everywhere()
        for path, reason, home in self.book.skipped:
            await self._event(home, None, {"outcome": "skipped", "path": path, "reason": reason})
        self.book.skipped.clear()
        state.forget_checks(lambda path: self.book.fs_path(path).exists())
        for project in projects:
            if not project.due(now) or state.fired_check(project.path) == project.check_key:
                continue
            if self.workshop.setting(project.scope, server_id=project.home_server,
                                     channel_id=project.home_channel,
                                     parent_id=project.home_parent) != "container":
                continue
            reason = ("quiet hours" if quiet_now(self.config, now)
                      else None if self.limits.allows(project.home_channel)
                      else "the daily cap on unprompted turns")
            if reason:
                await self._defer(project, reason)
                continue
            if not await self.notes.write(project.home_channel, checkin_note(project)):
                continue
            state.fire_check(project.path, project.check_key)
            self.limits.count(project.home_channel)
            await self._event(project.home_channel, project.home_server, {
                "outcome": "fired", "project": project.slug, "path": project.path,
                "next_check": project.check_key})
            self.request_turn(project.home_channel)

    async def _defer(self, project: Project, reason: str) -> None:
        key = (project.path, project.check_key, reason)
        if key in self._deferred:
            return
        self._deferred.add(key)
        logger.info(f"project check-in deferred ({reason}): {project.path}")
        await self._event(project.home_channel, project.home_server, {
            "outcome": "deferred", "project": project.slug, "path": project.path,
            "next_check": project.check_key, "reason": reason})

    async def _event(self, channel_id: str, server_id, payload: dict) -> None:
        if self.events is not None and channel_id:
            await self.events.add_event("project", server_id, channel_id, payload)
