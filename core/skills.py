"""Skills (0.14): local folders under skills/, each with a SKILL.md.

The system prompt lists them (with any default skills' instructions
preloaded); request_skill returns one's instructions on demand; the
workshop copies the folders into every sandbox at /skills, where their
scripts and templates run. Nothing is uploaded anywhere.

Where the workshop is off, only skills marked `metadata: {text-only: true}`
in their frontmatter are offered: guides that need nothing but reading.
Unmarked skills are assumed to need the workshop (most carry scripts).
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    rel: str          # its folder under skills/ - and so under /skills in the workshop
    text_only: bool   # usable without the workshop


def _split_frontmatter(text: str) -> Tuple[dict, str]:
    """(frontmatter, body) of a SKILL.md; no frontmatter means an empty dict."""
    if text.startswith("---"):
        _, _, rest = text.partition("\n")
        header, sep, body = rest.partition("\n---")
        if sep:
            try:
                meta = yaml.safe_load(header) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), body.lstrip("-").lstrip("\n")
    return {}, text


class SkillCatalog:
    def __init__(self, skills_dir: Path, default_skills: Iterable[str] = ()):
        self.skills_dir = Path(skills_dir)
        self.default_skills = list(default_skills)

    def skills(self) -> Dict[str, Skill]:
        found = {}
        if not self.skills_dir.exists():
            return found
        for folder in sorted(p for p in self.skills_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            home = self._skill_home(folder)
            if home is None:
                continue
            meta, _ = _split_frontmatter((home / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
            extra = meta.get("metadata")
            text_only = isinstance(extra, dict) and extra.get("text-only") is True
            found[folder.name] = Skill(folder.name, str(meta.get("description", "")).strip(), home,
                                       home.relative_to(self.skills_dir).as_posix(), text_only)
        return found

    def available(self, workshop: bool) -> Dict[str, Skill]:
        """The skills a conversation can use: all of them in the workshop,
        the text-only ones elsewhere."""
        return {n: s for n, s in self.skills().items() if workshop or s.text_only}

    @staticmethod
    def _skill_home(folder: Path) -> Optional[Path]:
        """The folder holding SKILL.md. Unzipping often adds a wrapper
        (skills/foo/foo/SKILL.md): one level down is accepted (0.12.3)."""
        if (folder / "SKILL.md").is_file():
            return folder
        nested = [d for d in folder.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()]
        if len(nested) == 1:
            logger.warning(f"skill {folder.name}/ has its SKILL.md one level down - using it")
            return nested[0]
        return None

    def instructions(self, name: str) -> Optional[str]:
        skill = self.skills().get(name)
        if skill is None:
            return None
        _, body = _split_frontmatter((skill.path / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
        return body.strip()

    def prompt(self, workshop: bool = True) -> str:
        """The <available_skills> section: stable unless the folder changes,
        so it can sit in the cached system prefix."""
        skills = self.available(workshop)
        if not skills:
            return ""
        where = ", with their files in /skills/<name>/ in your workshop" if workshop else ""
        lines = ["<available_skills>",
                 f"Guides for kinds of work{where}. Read one with request_skill when it "
                 "fits what you're doing."]
        lines += [f"  - {s.name}: {s.description}" for s in skills.values()]
        lines.append("</available_skills>")
        for name in self.default_skills:
            if name not in skills:   # missing, or needs the workshop this conversation lacks
                if name not in self.skills():
                    logger.warning(f"default skill {name!r} not found in {self.skills_dir}")
                continue
            text = self.instructions(name)
            if text:
                lines += [f'<skill name="{name}">', text, "</skill>"]
        return "\n".join(lines)
