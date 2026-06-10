"""
Skills Manager

Handles auto-discovery, uploading, and tracking of Claude Skills.
Supports hash-based caching to prevent redundant uploads.
"""

import asyncio
import io
import os
import json
import hashlib
import logging
import zipfile
import yaml
from pathlib import Path
from typing import Optional, Dict, List, Any, Union
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)


class SkillsManager:
    """
    Manages Claude Skills auto-discovery and upload system.

    Supports two skill formats:
    - .zip files: Archive containing SKILL.md and supporting files
    - Folders: Directory containing SKILL.md and supporting files
      (auto-zipped in memory before upload)

    Responsibilities:
    - Scan /skills/ directory for .zip files and skill folders
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
            skills_dir: Directory containing skill .zip files or skill folders
            cache_file: Path to cache file for tracking uploads
            anthropic_api_key: Anthropic API key for uploading skills
        """
        self.skills_dir = skills_dir
        self.cache_file = cache_file
        self.cache: Dict[str, Dict] = {}
        # Async client: skill uploads and list pagination are slow HTTP calls
        # that ran during on_ready - a sync client stalls the gateway heartbeat
        self.anthropic_client = AsyncAnthropic(api_key=anthropic_api_key) if anthropic_api_key else None

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

        # Drop entries whose skill was deleted server-side (a dead skill_id
        # would otherwise 400 every request that activates it, forever)
        await self._reconcile_cache_with_server()

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
            with open(self.cache_file, "w", encoding="utf-8") as f:
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

    def _calculate_directory_hash(self, dir_path: Path) -> str:
        """
        Calculate SHA256 hash of a directory by hashing all file paths and contents.

        Files are sorted alphabetically by relative path to ensure deterministic output.

        Args:
            dir_path: Path to directory

        Returns:
            Hex digest of SHA256 hash
        """
        sha256 = hashlib.sha256()

        # Collect all files sorted by relative path for deterministic hashing
        all_files = sorted(dir_path.rglob("*"))
        for file_path in all_files:
            if not file_path.is_file():
                continue
            # Hash the relative path
            rel_path = file_path.relative_to(dir_path).as_posix()
            sha256.update(rel_path.encode("utf-8"))
            # Hash the file contents
            with open(file_path, "rb") as f:
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

    def _extract_folder_metadata(self, folder_path: Path) -> Optional[Dict[str, str]]:
        """
        Extract metadata from SKILL.md inside a skill folder.

        Args:
            folder_path: Path to skill folder containing SKILL.md

        Returns:
            Dictionary with 'name' and 'description' if found, None otherwise
        """
        skill_md = folder_path / "SKILL.md"
        if not skill_md.exists():
            logger.warning(f"No SKILL.md found in {folder_path.name}/")
            return None

        try:
            content = skill_md.read_text(encoding="utf-8")

            # Parse YAML frontmatter
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    frontmatter = yaml.safe_load(parts[1])
                    if frontmatter:
                        return {
                            'name': frontmatter.get('name', folder_path.name),
                            'description': frontmatter.get('description', '')
                        }

            return {'name': folder_path.name, 'description': ''}

        except Exception as e:
            logger.error(f"Failed to extract metadata from {folder_path.name}/SKILL.md: {e}")
            return None

    def _zip_folder_to_bytes(self, folder_path: Path) -> tuple:
        """
        Create an in-memory zip archive from a skill folder.

        Args:
            folder_path: Path to skill folder

        Returns:
            Tuple of (BytesIO zip archive, filename string)
        """
        buf = io.BytesIO()

        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(folder_path.rglob("*")):
                if not file_path.is_file():
                    continue
                # Include folder name as top-level directory (required by Anthropic Skills API)
                arcname = f"{folder_path.name}/{file_path.relative_to(folder_path).as_posix()}"
                zf.write(file_path, arcname)

        buf.seek(0)
        filename = f"{folder_path.name}.zip"
        return buf, filename

    async def scan_and_upload_skills(self) -> List[Dict]:
        """
        Scan skills directory for .zip files and skill folders, then upload
        new or changed skills.

        Supported formats:
        - .zip files containing SKILL.md
        - Folders containing SKILL.md (auto-zipped in memory before upload)

        Returns:
            List of skill upload results
        """
        results = []

        # Find .zip files
        zip_files = list(self.skills_dir.glob("*.zip"))

        # Find skill folders (subdirectories containing SKILL.md)
        skill_folders = [
            d for d in self.skills_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists()
        ]

        total = len(zip_files) + len(skill_folders)
        if total == 0:
            logger.info("No skills found in skills directory")
            return results

        logger.info(
            f"Found {total} skill(s) in {self.skills_dir} "
            f"({len(zip_files)} zip, {len(skill_folders)} folder)"
        )

        # Process .zip files
        for zip_path in zip_files:
            result = await self._process_zip_skill(zip_path)
            results.append(result)

        # Process skill folders
        for folder_path in skill_folders:
            result = await self._process_folder_skill(folder_path)
            results.append(result)

        return results

    async def _process_zip_skill(self, zip_path: Path) -> Dict:
        """Process a .zip skill file: hash, check cache, upload if needed."""
        try:
            file_hash = await asyncio.to_thread(self._calculate_hash, zip_path)

            cached = self.cache.get(file_hash)
            if cached and cached.get('status') != 'already_exists':
                logger.debug(f"Skill {zip_path.name} already uploaded (hash match)")
                return {
                    'filename': zip_path.name,
                    'status': 'cached',
                    'skill_id': cached['skill_id']
                }
            # already_exists entries are stale placeholders (no usable ID) - retry

            metadata = self._extract_skill_metadata(zip_path)
            if not metadata:
                metadata = {'name': zip_path.stem, 'description': ''}

            skill_id = await self._upload_skill(zip_path, metadata)

            if skill_id:
                self.cache[file_hash] = {
                    'skill_id': skill_id,
                    'filename': zip_path.name,
                    'display_title': metadata['name'],
                    'version': '1.0.0',
                    'uploaded_at': __import__('datetime').datetime.utcnow().isoformat()
                }
                await self._save_cache()
                logger.info(f"Uploaded skill: {zip_path.name} (ID: {skill_id})")
                return {
                    'filename': zip_path.name,
                    'status': 'uploaded',
                    'skill_id': skill_id
                }
            else:
                return {
                    'filename': zip_path.name,
                    'status': 'failed',
                    'skill_id': None
                }

        except Exception as e:
            logger.error(f"Error processing skill {zip_path.name}: {e}", exc_info=True)
            return {
                'filename': zip_path.name,
                'status': 'error',
                'error': str(e)
            }

    async def _process_folder_skill(self, folder_path: Path) -> Dict:
        """Process a folder-based skill: hash directory, check cache, zip in memory, upload."""
        display_name = f"{folder_path.name}/"
        try:
            dir_hash = await asyncio.to_thread(self._calculate_directory_hash, folder_path)

            cached = self.cache.get(dir_hash)
            if cached and cached.get('status') != 'already_exists':
                logger.debug(f"Skill {display_name} already uploaded (hash match)")
                return {
                    'filename': display_name,
                    'status': 'cached',
                    'skill_id': cached['skill_id']
                }
            # already_exists entries are stale placeholders (no usable ID) - retry

            metadata = self._extract_folder_metadata(folder_path)
            if not metadata:
                metadata = {'name': folder_path.name, 'description': ''}

            # Create in-memory zip for API upload
            buf, zip_filename = await asyncio.to_thread(self._zip_folder_to_bytes, folder_path)
            skill_id = await self._upload_skill((buf, zip_filename), metadata)

            if skill_id:
                self.cache[dir_hash] = {
                    'skill_id': skill_id,
                    'filename': display_name,
                    'display_title': metadata['name'],
                    'version': '1.0.0',
                    'uploaded_at': __import__('datetime').datetime.utcnow().isoformat()
                }
                await self._save_cache()
                logger.info(f"Uploaded skill: {display_name} (ID: {skill_id})")
                return {
                    'filename': display_name,
                    'status': 'uploaded',
                    'skill_id': skill_id
                }
            else:
                return {
                    'filename': display_name,
                    'status': 'failed',
                    'skill_id': None
                }

        except Exception as e:
            logger.error(f"Error processing skill {display_name}: {e}", exc_info=True)
            return {
                'filename': display_name,
                'status': 'error',
                'error': str(e)
            }

    async def _upload_skill(
        self,
        source: Union[Path, tuple],
        metadata: Dict[str, str]
    ) -> Optional[str]:
        """
        Upload a skill to Anthropic API.

        Args:
            source: Either a Path to a .zip file on disk, or a tuple of
                    (BytesIO archive, filename) for in-memory uploads
            metadata: Extracted metadata with 'name' and 'description'

        Returns:
            Skill ID if successful, None otherwise
        """
        if not self.anthropic_client:
            logger.error("Cannot upload skill: Anthropic client not initialized")
            return None

        try:
            if isinstance(source, Path):
                # Upload from disk
                filename = source.name
                with open(source, 'rb') as f:
                    skill = await self.anthropic_client.beta.skills.create(
                        display_title=metadata['name'],
                        files=[(filename, f, 'application/zip')],
                        betas=["skills-2025-10-02"]
                    )
            else:
                # Upload from in-memory BytesIO
                buf, filename = source
                skill = await self.anthropic_client.beta.skills.create(
                    display_title=metadata['name'],
                    files=[(filename, buf, 'application/zip')],
                    betas=["skills-2025-10-02"]
                )

            return skill.id

        except Exception as e:
            error_str = str(e)
            # Display-title conflict: skill already on the server, recover its real ID
            if "reuse an existing display_title" in error_str:
                skill_id = await self._find_existing_skill_id(metadata['name'])
                if skill_id:
                    logger.info(
                        f"Skill '{metadata['name']}' already on Anthropic servers, "
                        f"recovered ID {skill_id}"
                    )
                    return skill_id
                logger.error(
                    f"Skill '{metadata['name']}' exists on server but its ID "
                    f"could not be recovered from the skills list"
                )
                return None
            logger.error(f"Failed to upload skill {filename}: {e}")
            return None

    async def _find_existing_skill_id(self, display_title: str) -> Optional[str]:
        """Look up a skill's ID on the server by display title (auto-paginates)."""
        try:
            async for skill in self.anthropic_client.beta.skills.list(betas=["skills-2025-10-02"]):
                if skill.display_title == display_title:
                    return skill.id
        except Exception as e:
            logger.error(f"Failed to list skills while recovering '{display_title}': {e}")
        return None

    async def _reconcile_cache_with_server(self) -> None:
        """
        Drop cache entries whose skill no longer exists on the server.

        Hash-based dedup only re-uploads when file content changes, so a
        skill deleted server-side would otherwise never be re-uploaded.
        """
        if not self.anthropic_client or not self.cache:
            return

        try:
            server_ids = set()
            async for skill in self.anthropic_client.beta.skills.list(betas=["skills-2025-10-02"]):
                server_ids.add(skill.id)
        except Exception as e:
            logger.warning(f"Skills cache reconciliation skipped (list failed): {e}")
            return

        stale = [
            h for h, info in self.cache.items()
            if info.get("skill_id") and info["skill_id"] not in server_ids
        ]
        for h in stale:
            info = self.cache.pop(h)
            logger.info(
                f"Dropping stale skills-cache entry '{info.get('filename')}' "
                f"(skill {info.get('skill_id')} no longer on server)"
            )
        if stale:
            await self._save_cache()

    def get_skills_for_api(self) -> List[Dict[str, str]]:
        """
        Get list of custom skills for Claude API container parameter.

        Returns:
            List of skill definitions in API format
        """
        skills = []
        for file_hash, skill_info in self.cache.items():
            # Stale placeholder from a pre-recovery cache file: no usable ID
            if skill_info.get('status') == 'already_exists':
                continue
            skills.append({
                "type": "custom",
                "skill_id": skill_info['skill_id'],
                "version": "latest"
            })
        return skills

    # Maximum skills that can be loaded at once (empirically determined, see Bug #14)
    # With progressive disclosure, this is less of an issue since Claude requests specific skills
    MAX_SKILLS_PER_REQUEST = 2

    # Anthropic built-in skills with descriptions (for skill catalog)
    ANTHROPIC_SKILLS = {
        "pdf": {
            "description": "Read, analyze, and manipulate PDF documents",
            "type": "anthropic"
        },
        "xlsx": {
            "description": "Create and edit Excel spreadsheets",
            "type": "anthropic"
        },
        "docx": {
            "description": "Create and edit Word documents",
            "type": "anthropic"
        },
        "pptx": {
            "description": "Create and edit PowerPoint presentations",
            "type": "anthropic"
        }
    }

    def get_skill_catalog(self) -> Dict[str, Dict[str, str]]:
        """
        Get metadata for all available skills (for system prompt).

        This provides Claude visibility into ALL skills that can be loaded,
        enabling progressive disclosure - Claude sees the catalog and can
        request specific skills via the request_skill tool.

        Returns:
            Dictionary mapping skill names to metadata (description, type, skill_id)
        """
        catalog = {}

        # Add Anthropic built-in skills
        for name, info in self.ANTHROPIC_SKILLS.items():
            catalog[name] = {
                "description": info["description"],
                "type": "anthropic",
                "skill_id": name
            }

        # Add custom skills from cache
        for file_hash, skill_info in self.cache.items():
            # Stale placeholder from a pre-recovery cache file: no usable ID
            if skill_info.get('status') == 'already_exists':
                continue

            name = skill_info.get('display_title', skill_info.get('filename', 'unknown'))
            # Extract description from metadata if available
            description = skill_info.get('description', f"Custom skill: {name}")

            catalog[name] = {
                "description": description,
                "type": "custom",
                "skill_id": skill_info['skill_id']
            }

        return catalog

    def select_skills(self, skill_names: List[str]) -> List[Dict[str, str]]:
        """
        Select specific skills by name for API request.

        This enables progressive disclosure - Claude requests specific skills
        and only those are loaded into the container.

        Args:
            skill_names: List of skill names to load (max MAX_SKILLS_PER_REQUEST)

        Returns:
            List of skill definitions for container parameter
        """
        catalog = self.get_skill_catalog()
        selected = []

        for name in skill_names[:self.MAX_SKILLS_PER_REQUEST]:
            if name in catalog:
                skill_info = catalog[name]
                selected.append({
                    "type": skill_info["type"],
                    "skill_id": skill_info["skill_id"],
                    "version": "latest"
                })
            else:
                logger.warning(f"Requested skill '{name}' not found in catalog")

        if len(skill_names) > self.MAX_SKILLS_PER_REQUEST:
            logger.warning(
                f"Requested {len(skill_names)} skills but max is {self.MAX_SKILLS_PER_REQUEST}. "
                f"Using: {skill_names[:self.MAX_SKILLS_PER_REQUEST]}"
            )

        logger.debug(f"Selected skills: {[s['skill_id'] for s in selected]}")
        return selected

    def get_default_anthropic_skills(self) -> List[Dict[str, str]]:
        """
        Get list of built-in Anthropic skills.

        Note: Due to Anthropic API limitation (Bug #14), only 2 skills can be used per request.
        Prioritizes pdf and xlsx as most commonly needed document skills.

        Returns:
            List of Anthropic skill definitions (limited to MAX_SKILLS_PER_REQUEST)
        """
        # Full list of available skills (prioritized order)
        all_skills = [
            {"type": "anthropic", "skill_id": "pdf", "version": "latest"},   # Most common
            {"type": "anthropic", "skill_id": "xlsx", "version": "latest"},  # Spreadsheets
            {"type": "anthropic", "skill_id": "docx", "version": "latest"},  # Documents
            {"type": "anthropic", "skill_id": "pptx", "version": "latest"}   # Presentations
        ]

        if len(all_skills) > self.MAX_SKILLS_PER_REQUEST:
            logger.warning(
                f"Anthropic API limits skills to {self.MAX_SKILLS_PER_REQUEST} per request. "
                f"Using: {[s['skill_id'] for s in all_skills[:self.MAX_SKILLS_PER_REQUEST]]}. "
                f"Skipping: {[s['skill_id'] for s in all_skills[self.MAX_SKILLS_PER_REQUEST:]]}"
            )

        return all_skills[:self.MAX_SKILLS_PER_REQUEST]
