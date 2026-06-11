"""
SupervisorRoot - every filesystem path the daemon touches derives from one
root (the install it manages, --root), and every user-influenced path goes
through the jail. There is no second way to build a path.
"""

from pathlib import Path
from typing import List


class PathJailError(Exception):
    """A requested path tried to leave its root."""


class SupervisorRoot:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # --- derivations -----------------------------------------------------

    def bots_dir(self) -> Path:
        return self.root / "bots"

    def bot_ids(self) -> List[str]:
        if not self.bots_dir().exists():
            return []
        return sorted(p.stem for p in self.bots_dir().glob("*.yaml")
                      if not p.stem.endswith(".example"))

    def bot_yaml(self, bot_id: str) -> Path:
        return self.bots_dir() / f"{bot_id}.yaml"

    def persistence_dir(self) -> Path:
        return self.root / "persistence"

    def messages_db(self, bot_id: str) -> Path:
        return self.persistence_dir() / f"{bot_id}_messages.db"

    def states_db(self, bot_id: str) -> Path:
        return self.persistence_dir() / f"{bot_id}_conversation_states.db"

    def users_db(self, bot_id: str) -> Path:
        return self.persistence_dir() / f"{bot_id}_users.db"

    def running_flag(self, bot_id: str) -> Path:
        return self.persistence_dir() / f"{bot_id}_running.flag"

    def engagement_stats(self, bot_id: str) -> Path:
        return self.persistence_dir() / f"{bot_id}_engagement_stats.json"

    def supervisor_state(self) -> Path:
        return self.persistence_dir() / "supervisor_state.json"

    def memories_dir(self, bot_id: str) -> Path:
        return self.root / "memories" / bot_id

    def repository_dir(self, bot_id: str) -> Path:
        return self.root / "repository" / bot_id

    def logs_dir(self) -> Path:
        return self.root / "logs"

    def log_file(self, bot_id: str, which: str) -> Path:
        suffix = "_conversations.log" if which == "conversations" else ".log"
        return self.logs_dir() / f"{bot_id}{suffix}"

    def skills_dir(self) -> Path:
        return self.root / "skills"

    def mcp_servers_json(self) -> Path:
        return self.root / "mcp_servers.json"

    def trash_dir(self) -> Path:
        return self.root / "trash"

    # --- the jail --------------------------------------------------------

    def jailed(self, base: Path, relative: str) -> Path:
        """Resolve a user-supplied relative path inside base, or raise.
        Absolute paths, drive letters, and any traversal that escapes the
        base are rejected - this is the API's only defense for file routes."""
        candidate = Path(str(relative).replace("\\", "/"))
        if candidate.is_absolute() or candidate.drive:
            raise PathJailError(f"absolute path refused: {relative}")
        resolved = (Path(base) / candidate).resolve()
        base_resolved = Path(base).resolve()
        if resolved != base_resolved and base_resolved not in resolved.parents:
            raise PathJailError(f"path escapes its root: {relative}")
        return resolved
