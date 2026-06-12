"""
Integrations surface: skills enable/disable (= membership in
skills.default_skills - the bot has no per-skill flag and none is added)
and MCP server management (mcp_servers.json + live health).
"""

import io
import json
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import List, Optional

import yaml

from .paths import SupervisorRoot

logger = logging.getLogger(__name__)

SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def _skill_description(skill_dir: Path) -> str:
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return ""
    for line in md.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("description:"):
            return line.split("description:", 1)[1].strip()
    return ""


def skills_catalog(root: SupervisorRoot, config: dict) -> dict:
    skills_config = config.get("skills") or {}
    enabled = set(skills_config.get("default_skills") or [])
    items = []
    skills_dir = root.skills_dir()
    if skills_dir.exists():
        for p in sorted(skills_dir.iterdir()):
            name = p.stem if p.suffix == ".zip" else p.name
            if p.name.startswith("."):
                continue
            if p.is_dir() or p.suffix == ".zip":
                items.append({
                    "name": name,
                    "description": _skill_description(p) if p.is_dir() else "",
                    "source": "custom",
                    "enabled": name in enabled,
                })
    return {
        "include_anthropic_skills": skills_config.get("include_anthropic_skills", True),
        "items": items,
    }


def apply_skills(root: SupervisorRoot, bot_id: str,
                 default_skills: List[str], include_anthropic: bool) -> None:
    """Rewrite the bot YAML's skills section (toggles = membership)."""
    path = root.bot_yaml(bot_id)
    with open(path, encoding="utf-8-sig") as f:
        config = yaml.safe_load(f) or {}
    config.setdefault("skills", {})
    config["skills"]["default_skills"] = list(default_skills)
    config["skills"]["include_anthropic_skills"] = bool(include_anthropic)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def add_skill(root: SupervisorRoot, name: str, zip_bytes: bytes) -> str:
    """Unpack an uploaded skill zip into skills/{name}/; must carry SKILL.md."""
    if not SKILL_NAME_RE.match(name or ""):
        raise ValueError("skill name must be alphanumeric/dash/underscore")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        if not any(Path(n).name == "SKILL.md" for n in names):
            raise ValueError("archive has no SKILL.md - not a skill")
        if any(Path(n).is_absolute() or ".." in Path(n).parts for n in names):
            raise ValueError("archive paths escape their folder")
        dest = root.skills_dir() / name
        if dest.exists():
            raise ValueError(f"skill {name} already exists")
        dest.mkdir(parents=True)
        zf.extractall(dest)
    logger.info(f"Skill added: {name}")
    return name


def remove_skill(root: SupervisorRoot, name: str) -> bool:
    """Delete a custom skill (folder or .zip) from the shared skills/ dir.
    This is global — every bot's catalog draws from the same folder. Returns
    False if no such skill exists."""
    if not SKILL_NAME_RE.match(name or ""):
        raise ValueError("invalid skill name")
    base = root.skills_dir()
    folder = root.jailed(base, name)
    if folder.is_dir():
        shutil.rmtree(folder)
        logger.info(f"Skill removed: {name}")
        return True
    archive = root.jailed(base, f"{name}.zip")
    if archive.is_file():
        archive.unlink()
        logger.info(f"Skill removed: {name}.zip")
        return True
    return False


def load_mcp_servers(root: SupervisorRoot) -> List[dict]:
    path = root.mcp_servers_json()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("servers", [])
    except (json.JSONDecodeError, OSError):
        logger.error("Unreadable mcp_servers.json")
        return []


def save_mcp_servers(root: SupervisorRoot, servers: List[dict]) -> None:
    root.mcp_servers_json().write_text(
        json.dumps({"servers": servers}, indent=2, ensure_ascii=False),
        encoding="utf-8")
