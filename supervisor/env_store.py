"""
EnvStore - the daemon owns the install's .env so secrets can be set from
the UI instead of a text editor. Values are write-only through the API:
endpoints report booleans ("is it set?"), never the secret itself.

Writes are upserts: other keys and comments are preserved, the file lands
atomically (temp + rename), always utf-8. Bot subprocesses pick changes up
on their next spawn via load_dotenv().
"""

import os
import re
import tempfile
from pathlib import Path

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class EnvStore:
    def __init__(self, env_path: Path):
        self.path = Path(env_path)

    def _lines(self) -> list:
        if not self.path.exists():
            return []
        return self.path.read_text(encoding="utf-8-sig").splitlines()

    def get(self, name: str) -> str:
        """Read a value from the FILE (not the process env)."""
        for line in self._lines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            k, _, v = stripped.partition("=")
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
        return ""

    def is_set(self, name: str) -> bool:
        return bool(self.get(name) or os.getenv(name))

    def set(self, name: str, value: str) -> None:
        """Upsert one key; preserves everything else in the file."""
        if not _KEY_RE.match(name):
            raise ValueError(f"invalid env var name: {name}")
        if "\n" in value or "\r" in value:
            raise ValueError("env values cannot contain newlines")
        lines = self._lines()
        new_line = f"{name}={value}"
        replaced = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith("#") and stripped.partition("=")[0].strip() == name:
                lines[i] = new_line
                replaced = True
                break
        if not replaced:
            lines.append(new_line)
        content = "\n".join(lines) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".env.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        # The daemon validates configs in-process (Config.validate reads
        # os.environ), so mirror the new value immediately.
        os.environ[name] = value
