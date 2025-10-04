"""
Memory Tool Executor - Client-Side Memory Operations

Implements Anthropic's official Memory Tool API specification.
Reference: docs/api_memory_tool.md

Supported commands:
- view: Show directory contents or file contents
- create: Create or overwrite file
- str_replace: Replace text in file
- insert: Insert text at specific line
- delete: Delete file or directory
- rename: Rename or move file/directory
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional
import shutil

logger = logging.getLogger(__name__)


class MemoryToolExecutor:
    """
    Executes memory tool commands on local filesystem.

    Follows official Anthropic Memory Tool API specification.
    All paths must start with /memories/{bot_id}/
    """

    def __init__(self, memory_base_path: Path, bot_id: str):
        """
        Initialize memory tool executor.

        Args:
            memory_base_path: Base path for memories (e.g., "./memories")
            bot_id: Bot identifier (e.g., "alpha")
        """
        self.bot_id = bot_id
        self.base_path = memory_base_path / bot_id
        self.base_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"MemoryToolExecutor initialized at {self.base_path}")

    def execute(self, tool_input: Dict[str, Any]) -> str:
        """
        Execute memory tool command.

        Args:
            tool_input: Tool use input from Claude (contains command, path, etc.)

        Returns:
            Result string to return to Claude
        """
        command = tool_input.get("command")
        path = tool_input.get("path", "")

        # Validate path (except for rename which uses old_path/new_path)
        if command != "rename":
            if not self._validate_path(path):
                return f"Error: Invalid path '{path}'"

        try:
            if command == "view":
                return self._view(tool_input)
            elif command == "create":
                return self._create(tool_input)
            elif command == "str_replace":
                return self._str_replace(tool_input)
            elif command == "insert":
                return self._insert(tool_input)
            elif command == "delete":
                return self._delete(tool_input)
            elif command == "rename":
                return self._rename(tool_input)
            else:
                return f"Error: Unknown command '{command}'"

        except Exception as e:
            logger.error(f"Error executing memory command '{command}': {e}", exc_info=True)
            return f"Error: {str(e)}"

    def _validate_path(self, path: str) -> bool:
        """
        Validate that path is within allowed memory directory.

        Args:
            path: Memory tool path (e.g., "/memories/alpha/servers/123/users/456.md")

        Returns:
            True if safe, False otherwise
        """
        # Allow root memories directory for exploration
        if path == "/memories":
            return True

        # Allow bot's directory and subdirectories
        bot_prefix = f"/memories/{self.bot_id}"
        if path == bot_prefix or path.startswith(f"{bot_prefix}/"):
            pass  # Valid
        else:
            logger.warning(f"Invalid memory path (must be /memories or under {bot_prefix}): {path}")
            return False

        # Convert to filesystem path and check for traversal
        # Handle special case for root memories directory
        if path == "/memories":
            return True

        # Remove the /memories/alpha prefix to get relative path
        if path == bot_prefix:
            relative_path = ""  # Root of bot's directory
        else:
            relative_path = path.replace(f"{bot_prefix}/", "")

        try:
            if relative_path:
                file_path = (self.base_path / relative_path).resolve()
            else:
                file_path = self.base_path.resolve()

            base_resolved = self.base_path.resolve()

            # Check if file_path is within base_path
            file_path.relative_to(base_resolved)
            return True

        except ValueError:
            logger.warning(f"Invalid memory path (traversal attempt): {path}")
            return False

    def _path_to_filesystem(self, memory_path: str) -> Path:
        """Convert memory tool path to filesystem path"""
        bot_prefix = f"/memories/{self.bot_id}"

        # Handle root memories directory
        if memory_path == "/memories":
            return self.base_path.parent  # Up one level from bot directory

        # Handle exact bot directory path
        if memory_path == bot_prefix:
            return self.base_path

        # Handle paths under bot directory
        relative_path = memory_path.replace(f"{bot_prefix}/", "")
        return self.base_path / relative_path

    def _view(self, tool_input: Dict[str, Any]) -> str:
        """
        View directory contents or file contents.

        Parameters:
            path: Path to view
            view_range: Optional [start_line, end_line] (1-indexed)

        Returns:
            Directory listing or file contents
        """
        path = tool_input["path"]
        view_range = tool_input.get("view_range")

        fs_path = self._path_to_filesystem(path)

        # View directory
        if not fs_path.exists():
            return f"Path does not exist: {path}"

        if fs_path.is_dir():
            # List directory contents
            try:
                items = []
                for item in sorted(fs_path.iterdir()):
                    if item.is_dir():
                        items.append(f"{item.name}/")
                    else:
                        items.append(item.name)

                if not items:
                    return f"Directory is empty: {path}"

                return "\n".join(items)

            except Exception as e:
                return f"Error listing directory: {str(e)}"

        # View file
        try:
            with open(fs_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Handle empty files
            if not content:
                return f"File exists but is empty: {path}"

            # Handle line range if specified
            if view_range:
                lines = content.splitlines()
                start, end = view_range
                # view_range is 1-indexed, convert to 0-indexed
                content = "\n".join(lines[start-1:end])

            logger.debug(f"Viewed memory file: {path} ({len(content)} chars)")
            return content

        except Exception as e:
            return f"Error reading file: {str(e)}"

    def _create(self, tool_input: Dict[str, Any]) -> str:
        """
        Create or overwrite file.

        Parameters:
            path: Path to file
            file_text: Content to write

        Returns:
            Success message or error
        """
        path = tool_input["path"]
        file_text = tool_input.get("file_text", "")

        fs_path = self._path_to_filesystem(path)

        try:
            # Create parent directories
            fs_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file (overwrites if exists)
            with open(fs_path, 'w', encoding='utf-8') as f:
                f.write(file_text)

            logger.info(f"Created memory file: {path} ({len(file_text)} chars)")
            return f"Successfully created {path}"

        except Exception as e:
            return f"Error creating file: {str(e)}"

    def _str_replace(self, tool_input: Dict[str, Any]) -> str:
        """
        Replace text in existing file.

        Parameters:
            path: Path to file
            old_str: String to find
            new_str: String to replace with

        Returns:
            Success message or error
        """
        path = tool_input["path"]
        old_str = tool_input.get("old_str", "")
        new_str = tool_input.get("new_str", "")

        fs_path = self._path_to_filesystem(path)

        # Check if file exists
        if not fs_path.exists():
            return f"Error: File does not exist at {path}. Use create to make a new file."

        try:
            # Read current content
            with open(fs_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Check if old_str exists
            if old_str not in content:
                return f"Error: String not found in file."

            # Replace and write back (only first occurrence)
            new_content = content.replace(old_str, new_str, 1)

            with open(fs_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            logger.info(f"Updated memory file: {path}")
            return f"Successfully updated {path}"

        except Exception as e:
            return f"Error updating file: {str(e)}"

    def _insert(self, tool_input: Dict[str, Any]) -> str:
        """
        Insert text at specific line number.

        Parameters:
            path: Path to file
            insert_line: Line number to insert at (1-indexed)
            new_str: Text to insert

        Returns:
            Success message or error
        """
        path = tool_input["path"]
        insert_line = tool_input.get("insert_line")
        new_str = tool_input.get("new_str", "")

        if insert_line is None:
            return "Error: insert_line parameter required"

        fs_path = self._path_to_filesystem(path)

        # Check if file exists
        if not fs_path.exists():
            return f"Error: File does not exist at {path}. Use create to make a new file."

        try:
            # Read current content
            with open(fs_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Insert at line (convert from 1-indexed to 0-indexed)
            # Insert before the specified line
            lines.insert(insert_line - 1, new_str + "\n")

            # Write back
            with open(fs_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

            logger.info(f"Inserted into memory file: {path} at line {insert_line}")
            return f"Successfully inserted text at line {insert_line} in {path}"

        except Exception as e:
            return f"Error inserting into file: {str(e)}"

    def _delete(self, tool_input: Dict[str, Any]) -> str:
        """
        Delete file or directory.

        Parameters:
            path: Path to delete

        Returns:
            Success message or error
        """
        path = tool_input["path"]
        fs_path = self._path_to_filesystem(path)

        # Check if exists
        if not fs_path.exists():
            return f"Error: Path does not exist: {path}"

        try:
            if fs_path.is_dir():
                # Delete directory recursively
                shutil.rmtree(fs_path)
                logger.info(f"Deleted memory directory: {path}")
                return f"Successfully deleted directory {path}"
            else:
                # Delete file
                fs_path.unlink()
                logger.info(f"Deleted memory file: {path}")
                return f"Successfully deleted {path}"

        except Exception as e:
            return f"Error deleting: {str(e)}"

    def _rename(self, tool_input: Dict[str, Any]) -> str:
        """
        Rename or move file/directory.

        Parameters:
            path: Current path
            new_path: New path

        Returns:
            Success message or error
        """
        old_path = tool_input.get("path")
        new_path = tool_input.get("new_path")

        if not old_path or not new_path:
            return "Error: Both 'path' and 'new_path' required for rename"

        # Validate both paths
        if not self._validate_path(old_path):
            return f"Error: Invalid old path '{old_path}'"
        if not self._validate_path(new_path):
            return f"Error: Invalid new path '{new_path}'"

        old_fs_path = self._path_to_filesystem(old_path)
        new_fs_path = self._path_to_filesystem(new_path)

        # Check if old path exists
        if not old_fs_path.exists():
            return f"Error: Path does not exist: {old_path}"

        # Check if new path already exists
        if new_fs_path.exists():
            return f"Error: Destination already exists: {new_path}"

        try:
            # Create parent directories for new path
            new_fs_path.parent.mkdir(parents=True, exist_ok=True)

            # Rename/move
            old_fs_path.rename(new_fs_path)

            logger.info(f"Renamed memory path: {old_path} -> {new_path}")
            return f"Successfully renamed {old_path} to {new_path}"

        except Exception as e:
            return f"Error renaming: {str(e)}"
