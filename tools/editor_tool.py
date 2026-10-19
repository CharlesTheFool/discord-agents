"""The workshop editor (spec 5.7): view, create, str_replace, insert on files
in this scope's sandbox, through the backend's file operations.

On Anthropic the declaration is the typed text_editor_20250728 tool (the
model is trained on it); other drivers get EDITOR_SCHEMA (plan C). Paths are
judged after normalization: writes only under /workspace, reads also under
/skills. On microsandbox the file operations run in the guest, so a
symlink resolves inside the VM and never reaches the host.
"""
import posixpath
from typing import Optional

from core.internal_constants import WORKSHOP_TOOL_OUTPUT_CHARS
from core.workshop.backend import FileTooLarge, SandboxUnavailable
from core.workshop.service import WorkshopUnavailable

EDITOR_NAME = "str_replace_based_edit_tool"
EDITOR_DESCRIPTION = (
    "View and edit files in your workshop: view a file (or list a folder), "
    "create a new file, replace one exact snippet with str_replace, or insert "
    "lines after a given line. Paths are absolute, under /workspace; skills "
    "under /skills can be read."
)
EDITOR_DECLARATION = {"type": "text_editor_20250728", "name": EDITOR_NAME,
                      "max_characters": WORKSHOP_TOOL_OUTPUT_CHARS}
EDITOR_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "enum": ["view", "create", "str_replace", "insert"]},
        "path": {"type": "string", "description": "Absolute path, under /workspace (or /skills to read)"},
        "file_text": {"type": "string", "description": "[create] The new file's content"},
        "old_str": {"type": "string", "description": "[str_replace] Text that occurs exactly once"},
        "new_str": {"type": "string", "description": "[str_replace] Its replacement"},
        "insert_line": {"type": "integer", "description": "[insert] Insert after this line (0 = top)"},
        "insert_text": {"type": "string", "description": "[insert] The text to insert"},
        "view_range": {"type": "array", "items": {"type": "integer"},
                       "description": "[view] [first, last] line numbers; last -1 = end of file"},
    },
    "required": ["command", "path"],
}
WRITABLE = ("/workspace",)
READABLE = ("/workspace", "/skills")


class EditorError(Exception):
    pass


def jail(path: Optional[str], *, write: bool) -> str:
    if not path or not path.startswith("/"):
        raise EditorError("the path must be absolute, e.g. /workspace/notes.md")
    norm = posixpath.normpath(path)
    roots = WRITABLE if write else READABLE
    if not any(norm == r or norm.startswith(r + "/") for r in roots):
        where = "/workspace" if write else "/workspace or /skills"
        raise EditorError(f"{norm} is outside {where}")
    return norm


EDITOR_MAX_BYTES = 10 * 1024 * 1024   # past this, bash (head, tail, grep) is the tool


def _text(data: bytes, path: str, *, edit: bool) -> str:
    if b"\x00" in data[:8192]:
        raise EditorError(f"{path} is a binary file ({len(data):,} bytes) - use bash to inspect it")
    if not edit:
        return data.decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:   # writing back replaced characters would corrupt the file
        raise EditorError(f"{path} isn't UTF-8 text, so editing it here would damage it - "
                          f"use bash (iconv converts it)") from None


def _numbered(lines, start: int) -> str:
    return "\n".join(f"{n:6}\t{line}" for n, line in enumerate(lines, start))


class Editor:
    def __init__(self, backend, name: str):
        self.backend = backend
        self.name = name

    async def run(self, tool_input: dict) -> str:
        command = tool_input.get("command")
        handler = {"view": self.view, "create": self.create,
                   "str_replace": self.str_replace, "insert": self.insert}.get(command)
        if handler is None:
            raise EditorError(f"unknown command {command!r} (view, create, str_replace, insert)")
        return await handler(tool_input)

    async def _read(self, path: str, *, edit: bool = True) -> str:
        """The file's text. A directory raises IsADirectoryError for view to
        list; for an edit it's just an error."""
        try:
            data = await self.backend.read_file(self.name, path, max_bytes=EDITOR_MAX_BYTES)
        except FileNotFoundError:
            raise EditorError(f"{path} doesn't exist") from None
        except FileTooLarge as e:
            raise EditorError(f"{path} is {e.size:,} bytes, too big to open here - use bash "
                              f"(head, tail, grep)") from None
        except IsADirectoryError:
            if edit:
                raise EditorError(f"{path} is a directory") from None
            raise
        return _text(data, path, edit=edit)

    async def view(self, tool_input: dict) -> str:
        path = jail(tool_input.get("path"), write=False)
        try:
            text = await self._read(path, edit=False)
        except IsADirectoryError:
            entries = await self.backend.list_dir(self.name, path)
            lines = [f"{e.name}/" if e.kind == "dir" else e.name for e in entries]
            return f"{path}:\n" + ("\n".join(lines) if lines else "(empty)")
        lines = text.split("\n")
        first, last = 1, len(lines)
        if tool_input.get("view_range"):
            first, last = (list(tool_input["view_range"]) + [-1])[:2]
            last = len(lines) if last == -1 else last
            if not (1 <= first <= max(last, 1) <= len(lines)):
                raise EditorError(f"view_range must lie within 1..{len(lines)}")
        out = _numbered(lines[first - 1:last], first)
        if len(out) > WORKSHOP_TOOL_OUTPUT_CHARS:
            out = out[:WORKSHOP_TOOL_OUTPUT_CHARS] + "\n[... truncated - view a smaller view_range]"
        return out

    async def create(self, tool_input: dict) -> str:
        path = jail(tool_input.get("path"), write=True)
        if await self.backend.exists(self.name, path):
            raise EditorError(f"{path} already exists - use str_replace or insert to change it")
        await self.backend.write_file(self.name, path, (tool_input.get("file_text") or "").encode())
        return f"Created {path}."

    async def str_replace(self, tool_input: dict) -> str:
        path = jail(tool_input.get("path"), write=True)
        old = tool_input.get("old_str")
        if not old:
            raise EditorError("old_str is required and can't be empty")
        text = await self._read(path)
        count = text.count(old)
        if count != 1:
            raise EditorError(f"old_str occurs {count} times in {path}; it must occur exactly once")
        await self.backend.write_file(self.name, path, text.replace(old, tool_input.get("new_str") or "").encode())
        return f"Edited {path}."

    async def insert(self, tool_input: dict) -> str:
        path = jail(tool_input.get("path"), write=True)
        new = tool_input.get("insert_text", tool_input.get("new_str"))
        if new is None:
            raise EditorError("insert_text is required")
        lines = (await self._read(path)).split("\n")
        at = tool_input.get("insert_line")
        if not isinstance(at, int) or not 0 <= at <= len(lines):
            raise EditorError(f"insert_line must be between 0 and {len(lines)}")
        lines[at:at] = new.split("\n")
        await self.backend.write_file(self.name, path, "\n".join(lines).encode())
        return f"Inserted {len(new.splitlines()) or 1} line(s) into {path} after line {at}."


async def run_editor(workshop, tool_input: dict, *, server_id, channel_id,
                     parent_id=None, is_thread=False) -> str:
    try:
        scope = workshop.scope_for_turn(server_id, channel_id, parent_id=parent_id, is_thread=is_thread)
        name = await workshop.sandbox(scope)
        return await Editor(workshop.backend, name).run(tool_input)
    except EditorError as e:
        return f"Error: {e}"
    except WorkshopUnavailable as e:
        return f"The workshop isn't available: {e}."
    except SandboxUnavailable as e:
        return f"The workshop's machine isn't reachable right now ({e}); try again in a moment."
