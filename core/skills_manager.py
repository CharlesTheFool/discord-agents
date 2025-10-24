"""
Skills Manager

Handles auto-discovery, uploading, and tracking of Claude Skills.
Supports hash-based caching to prevent redundant uploads.
"""

import os
import json
import hashlib
import logging
import zipfile
import yaml
from pathlib import Path
from typing import Optional, Dict, List, Any
from anthropic import Anthropic

logger = logging.getLogger(__name__)


class SkillsManager:
    """
    Manages Claude Skills auto-discovery and upload system.

    Responsibilities:
    - Scan /skills/ directory for .zip files
    - Calculate SHA256 hashes for change detection
    - Upload new/changed skills to Anthropic API
    - Maintain cache in .skills_cache.json
    - Provide skill IDs for Claude API requests
    """

    def __init__(
        self,
        skills_dir: Path = Path("skills"),
        cache_file: Path = Path(".skills_cache.json"),
        anthropic_api_key: Optional[str] = None
    ):
        """
        Initialize Skills Manager.

        Args:
            skills_dir: Directory containing skill .zip files
            cache_file: Path to cache file for tracking uploads
            anthropic_api_key: Anthropic API key for uploading skills
        """
        self.skills_dir = skills_dir
        self.cache_file = cache_file
        self.cache: Dict[str, Dict] = {}
        self.anthropic_client = Anthropic(api_key=anthropic_api_key) if anthropic_api_key else None

        # Ensure skills directory exists
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"SkillsManager initialized with directory: {skills_dir}")

    async def initialize(self) -> None:
        """
        Load cache and scan for new/changed skills.

        This method should be called on bot startup.
        """
        # Load existing cache
        await self._load_cache()

        # Scan and upload new skills
        await self.scan_and_upload_skills()

        logger.info(f"Skills initialization complete. {len(self.cache)} skills in cache.")

    async def _load_cache(self) -> None:
        """Load skills cache from disk."""
        if not self.cache_file.exists():
            logger.info("No skills cache found, starting fresh")
            self.cache = {}
            return

        try:
            with open(self.cache_file) as f:
                self.cache = json.load(f)
            logger.info(f"Loaded skills cache with {len(self.cache)} entries")
        except Exception as e:
            logger.error(f"Failed to load skills cache: {e}")
            self.cache = {}

    async def _save_cache(self) -> None:
        """Save skills cache to disk."""
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.cache, f, indent=2)
            logger.debug("Skills cache saved")
        except Exception as e:
            logger.error(f"Failed to save skills cache: {e}")

    def _calculate_hash(self, file_path: Path) -> str:
        """
        Calculate SHA256 hash of a file.

        Args:
            file_path: Path to file

        Returns:
            Hex digest of SHA256 hash
        """
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Read in chunks to handle large files
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _extract_skill_metadata(self, zip_path: Path) -> Optional[Dict[str, str]]:
        """
        Extract metadata from SKILL.md inside the zip file.

        Args:
            zip_path: Path to skill .zip file

        Returns:
            Dictionary with 'name' and 'description' if found, None otherwise
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Find SKILL.md (could be in root or subdirectory)
                skill_md_path = None
                for name in zf.namelist():
                    if name.endswith('SKILL.md'):
                        skill_md_path = name
                        break

                if not skill_md_path:
                    logger.warning(f"No SKILL.md found in {zip_path.name}")
                    return None

                # Read SKILL.md
                with zf.open(skill_md_path) as f:
                    content = f.read().decode('utf-8')

                # Parse YAML frontmatter
                if content.startswith('---'):
                    parts = content.split('---', 2)
                    if len(parts) >= 3:
                        frontmatter = yaml.safe_load(parts[1])
                        return {
                            'name': frontmatter.get('name', zip_path.stem),
                            'description': frontmatter.get('description', '')
                        }

                return {'name': zip_path.stem, 'description': ''}

        except Exception as e:
            logger.error(f"Failed to extract metadata from {zip_path.name}: {e}")
            return None

    async def scan_and_upload_skills(self) -> List[Dict]:
        """
        Scan skills directory for .zip files and upload new/changed skills.

        Returns:
            List of skill upload results
        """
        results = []

        # Find all .zip files
        zip_files = list(self.skills_dir.glob("*.zip"))

        if not zip_files:
            logger.info("No skill .zip files found in skills directory")
            return results

        logger.info(f"Found {len(zip_files)} skill file(s) in {self.skills_dir}")

        for zip_path in zip_files:
            try:
                # Calculate hash
                file_hash = self._calculate_hash(zip_path)

                # Check if already uploaded
                if file_hash in self.cache:
                    logger.debug(f"Skill {zip_path.name} already uploaded (hash match)")
                    results.append({
                        'filename': zip_path.name,
                        'status': 'cached',
                        'skill_id': self.cache[file_hash]['skill_id']
                    })
                    continue

                # New or changed skill - extract metadata
                metadata = self._extract_skill_metadata(zip_path)
                if not metadata:
                    # Fallback to filename
                    metadata = {'name': zip_path.stem, 'description': ''}

                # Upload to Anthropic API
                skill_id = await self._upload_skill(zip_path, metadata)

                if skill_id:
                    # Cache the result
                    self.cache[file_hash] = {
                        'skill_id': skill_id,
                        'filename': zip_path.name,
                        'display_title': metadata['name'],
                        'version': '1.0.0',  # Could extract from SKILL.md if present
                        'uploaded_at': __import__('datetime').datetime.utcnow().isoformat()
                    }
                    await self._save_cache()

                    results.append({
                        'filename': zip_path.name,
                        'status': 'uploaded',
                        'skill_id': skill_id
                    })
                    logger.info(f"✅ Uploaded skill: {zip_path.name} (ID: {skill_id})")
                else:
                    results.append({
                        'filename': zip_path.name,
                        'status': 'failed',
                        'skill_id': None
                    })

            except Exception as e:
                logger.error(f"Error processing skill {zip_path.name}: {e}", exc_info=True)
                results.append({
                    'filename': zip_path.name,
                    'status': 'error',
                    'error': str(e)
                })

        return results

    async def _upload_skill(self, zip_path: Path, metadata: Dict[str, str]) -> Optional[str]:
        """
        Upload a skill to Anthropic API.

        Args:
            zip_path: Path to skill .zip file
            metadata: Extracted metadata with 'name' and 'description'

        Returns:
            Skill ID if successful, None otherwise
        """
        if not self.anthropic_client:
            logger.error("Cannot upload skill: Anthropic client not initialized")
            return None

        try:
            # Upload skill using Anthropic SDK
            with open(zip_path, 'rb') as f:
                skill = self.anthropic_client.beta.skills.create(
                    display_title=metadata['name'],
                    files=[(zip_path.name, f, 'application/zip')],
                    betas=["skills-2025-10-02"]
                )

            return skill.id

        except Exception as e:
            logger.error(f"Failed to upload skill {zip_path.name}: {e}")
            return None

    def get_skills_for_api(self) -> List[Dict[str, str]]:
        """
        Get list of custom skills for Claude API container parameter.

        Returns:
            List of skill definitions in API format
        """
        skills = []

        for file_hash, skill_info in self.cache.items():
            skills.append({
                "type": "custom",
                "skill_id": skill_info['skill_id'],
                "version": "latest"
            })

        return skills

    def get_default_anthropic_skills(self) -> List[Dict[str, str]]:
        """
        Get list of built-in Anthropic skills.

        Returns:
            List of Anthropic skill definitions
        """
        return [
            {"type": "anthropic", "skill_id": "xlsx", "version": "latest"},
            {"type": "anthropic", "skill_id": "pptx", "version": "latest"},
            {"type": "anthropic", "skill_id": "docx", "version": "latest"},
            {"type": "anthropic", "skill_id": "pdf", "version": "latest"}
        ]

    def get_all_skills_for_api(self) -> List[Dict[str, str]]:
        """
        Get combined list of Anthropic + custom skills.

        Returns:
            Complete list of skills for API
        """
        return self.get_default_anthropic_skills() + self.get_skills_for_api()
