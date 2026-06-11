"""
SupervisorRoot - every filesystem path the daemon touches derives from one
root (the install it manages, --root), and every user-influenced path goes
through the jail. There is no second way to build a path.

v0.10: code_root separates the framework source (read-only, replaced by the
installer's updater) from the data root (bots/, persistence/, memories/...).
In the git-checkout model both are the same directory and behavior is
byte-identical to before.
"""

from pathlib import Path
from typing import List, Optional


class PathJailError(Exception):
    """A requested path tried to leave its root."""


class SupervisorRoot:
    def __init__(self, root: Path, code_root: Optional[Path] = None):
        self.root = Path(root).resolve()
        self.code_root = Path(code_root).resolve() if code_root else self.root

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
        return self.code_root / "skills"

    def bot_manager_script(self) -> Path:
        return self.code_root / "bot_manager.py"

    def env_file(self) -> Path:
        return self.root / ".env"

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
