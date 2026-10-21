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
import logging
import pathlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Tuple, Union

import yaml

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
            return self._skip(path, stat, f"home channel {home} isn't known")
        home_server, parent, is_thread = place
        if home_server != server_id or self.vaults.resolve_scope(
                home_server, home, parent_id=parent, is_thread=is_thread) != scope:
            return self._skip(path, stat, f"home channel {home} is outside the scope that holds the note")
        self._logged.pop(path, None)
        return Project(slug=fs_path.stem, path=path, status=status, next_check=next_check,
                       home_channel=home, home_server=home_server, scope=scope, mtime=stat.st_mtime)

    def _skip(self, path: str, stat, reason: str) -> None:
        version = (stat.st_mtime_ns, stat.st_size)
        if self._logged.get(path) != version:
            self._logged[path] = version
            logger.warning(f"project note skipped ({reason}): {path}")
        return None


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
