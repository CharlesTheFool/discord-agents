# Memory Tool - API Reference

**Source:** https://docs.claude.com/en/docs/agents-and-tools/tool-use/memory-tool

**Beta Feature:** Requires `context-management-2025-06-27` beta header

---

## Overview

The Memory Tool enables Claude to maintain persistent knowledge across conversations by reading and writing to a local `/memories` directory. Claude automatically manages its own knowledge base without manual intervention.

### Use Cases

- **Project Context:** Maintain context across multiple agent executions
- **Learning:** Learn from past interactions, decisions, and feedback
- **Knowledge Base:** Build domain knowledge over time
- **Workflow Improvement:** Cross-conversation learning for recurring tasks

### How It Works

1. **Automatic Check:** Claude checks `/memories` directory before starting tasks
2. **Tool Calls:** Claude makes tool calls to create/read/update/delete memory files
3. **Client-Side Execution:** Your application executes memory operations locally
4. **Full Control:** You control where and how memories are stored

---

## Supported Models

- Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`)
- Claude Sonnet 4 (`claude-sonnet-4-20250514`)
- Claude Opus 4.1 (`claude-opus-4-1-20250805`)
- Claude Opus 4 (`claude-opus-4-20250514`)

---

## Getting Started

### Basic Setup

```python
from anthropic import Anthropic

client = Anthropic(api_key="your-api-key")

response = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    betas=["context-management-2025-06-27"],  # Required beta header
    tools=[{"type": "memory"}],                # Enable memory tool
    messages=[
        {"role": "user", "content": "Remember that I prefer Python for coding"}
    ],
    max_tokens=1024
)
```

### Implementation Options

**Python:**
- Use `AbstractMemoryTool` base class
- Implement your own backend (file-based, database, cloud storage, etc.)
- Example: `examples/memory/basic.py` in anthropic-sdk-python

**TypeScript:**
- Use `betaMemoryTool` helper
- Flexible backend implementation
- Example: `examples/tools-helpers-memory.ts` in anthropic-sdk-typescript

---

## Memory Tool Commands

### 1. `view` - Show Contents

View directory contents or file contents with optional line ranges.

**View Directory:**
```python
# Claude makes this tool call
{
    "type": "tool_use",
    "name": "memory",
    "input": {
        "command": "view",
        "path": "/memories"
    }
}
```

**View File:**
```python
{
    "type": "tool_use",
    "name": "memory",
    "input": {
        "command": "view",
        "path": "/memories/user_preferences.md"
    }
}
```

**View File with Line Range:**
```python
{
    "type": "tool_use",
    "name": "memory",
    "input": {
        "command": "view",
        "path": "/memories/chat_history.md",
        "view_range": [10, 50]  # Lines 10-50
    }
}
```

### 2. `create` - Create/Overwrite File

Create new file or overwrite existing file.

```python
{
    "type": "tool_use",
    "name": "memory",
    "input": {
        "command": "create",
        "path": "/memories/server_123/users/user_456.md",
        "file_text": "# User: Alice\n\n**Timezone:** PST\n**Interests:** Python, ML"
    }
}
```

### 3. `str_replace` - Replace Text

Replace text in an existing file.

```python
{
    "type": "tool_use",
    "name": "memory",
    "input": {
        "command": "str_replace",
        "path": "/memories/project.md",
        "old_str": "Status: In Progress",
        "new_str": "Status: Completed"
    }
}
```

### 4. `insert` - Insert at Line

Insert text at a specific line number.

```python
{
    "type": "tool_use",
    "name": "memory",
    "input": {
        "command": "insert",
        "path": "/memories/notes.md",
        "insert_line": 5,
        "new_str": "New note added on 2025-09-30"
    }
}
```

### 5. `delete` - Delete File/Directory

Delete a file or directory.

```python
{
    "type": "tool_use",
    "name": "memory",
    "input": {
        "command": "delete",
        "path": "/memories/old_project"
    }
}
```

### 6. `rename` - Rename/Move

Rename or move a file/directory.

```python
{
    "type": "tool_use",
    "name": "memory",
    "input": {
        "command": "rename",
        "path": "/memories/draft.md",
        "new_path": "/memories/final.md"
    }
}
```

---

## Automatic System Prompt

When memory tool is enabled, this instruction is automatically added to the system prompt:

> **Note:** when editing your memory folder, always try to keep its content up-to-date, coherent and organized. You can rename or delete files that are no longer relevant. Do not create new files unless necessary.

### Custom Guidance

You can guide what Claude writes to memory:

```python
system_prompt = """
You are a helpful assistant.

Memory guidance: Only write down information relevant to user preferences 
and project context in your memory system. Don't store temporary conversation 
details or one-off questions.
"""
```

---

## Security Considerations

### 1. Sensitive Information

Claude typically refuses to write sensitive information (passwords, API keys, etc.), but you should implement additional validation:

```python
def validate_memory_content(content: str) -> bool:
    """Strip potentially sensitive information before writing"""
    sensitive_patterns = [
        r'password[:\s]+\S+',
        r'api[_-]?key[:\s]+\S+',
        r'\d{3}-\d{2}-\d{4}',  # SSN pattern
        r'\d{16}',              # Credit card pattern
    ]
    # Implement your validation logic
    return True
```

### 2. File Storage Size

Track memory file sizes to prevent unbounded growth:

```python
def check_memory_size(path: str, max_size_mb: int = 10) -> bool:
    """Ensure memory files don't grow too large"""
    file_size = os.path.getsize(path) / (1024 * 1024)
    return file_size < max_size_mb

def limit_view_output(content: str, max_chars: int = 100000) -> str:
    """Limit characters returned by view command"""
    if len(content) > max_chars:
        return content[:max_chars] + f"\n\n[...truncated {len(content) - max_chars} chars]"
    return content
```

### 3. Memory Expiration

Clear stale memories periodically:

```python
from datetime import datetime, timedelta

def cleanup_old_memories(memory_dir: Path, days: int = 90):
    """Remove memories not accessed in X days"""
    cutoff = datetime.now() - timedelta(days=days)
    
    for file_path in memory_dir.rglob("*.md"):
        if datetime.fromtimestamp(file_path.stat().st_atime) < cutoff:
            file_path.unlink()
            print(f"Cleaned up old memory: {file_path}")
```

### 4. Path Traversal Protection ⚠️

**CRITICAL:** Always validate paths to prevent directory traversal attacks.

```python
from pathlib import Path

def validate_memory_path(path: str, base_dir: Path) -> bool:
    """
    Validate that path stays within /memories directory.
    Prevents directory traversal attacks.
    
    Args:
        path: Requested path from tool call
        base_dir: Base memory directory (e.g., Path("/memories"))
    
    Returns:
        True if safe, False if potential attack
    """
    try:
        # Resolve to canonical form
        requested = Path(path).resolve()
        base = base_dir.resolve()
        
        # Check if requested path is within base directory
        requested.relative_to(base)
        return True
        
    except ValueError:
        # Path is outside base directory
        return False

# Example usage
def handle_memory_command(tool_input: dict):
    path = tool_input["path"]
    base_memory_dir = Path("/memories")
    
    if not validate_memory_path(path, base_memory_dir):
        return {
            "error": "Invalid path: must be within /memories directory",
            "blocked_path": path
        }
    
    # Safe to proceed with operation
    command = tool_input["command"]
    # ... execute command
```

**Watch for:**
- `../` sequences
- `..\\` sequences (Windows)
- URL-encoded traversal: `%2e%2e%2f`
- Absolute paths outside base directory
- Symlink attacks

---

## Error Handling

Memory tool uses standard error patterns:

```python
def handle_memory_error(error_type: str, details: dict) -> dict:
    """
    Common memory tool errors and responses.
    
    Error types:
    - file_not_found: Requested file doesn't exist
    - permission_denied: Can't access file/directory
    - invalid_path: Path validation failed
    - file_exists: Attempting to create existing file (without overwrite)
    """
    error_responses = {
        "file_not_found": {
            "error": "File not found",
            "path": details.get("path"),
            "suggestion": "Use view command to list available files"
        },
        "permission_denied": {
            "error": "Permission denied",
            "path": details.get("path"),
            "suggestion": "Check file permissions"
        },
        "invalid_path": {
            "error": "Invalid path",
            "path": details.get("path"),
            "suggestion": "Path must be within /memories directory"
        }
    }
    return error_responses.get(error_type, {"error": "Unknown error"})
```

---

## Implementation Examples

### File-Based Backend (Recommended for Small Bots)

```python
from pathlib import Path
from typing import Optional
import json

class FileMemoryBackend:
    def __init__(self, base_path: str = "./memories"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def view(self, path: str, view_range: Optional[list] = None) -> str:
        """View directory contents or file contents"""
        full_path = self.base_path / path.lstrip("/memories/")
        
        if not self._validate_path(full_path):
            raise ValueError("Invalid path")
        
        if full_path.is_dir():
            # List directory contents
            items = [item.name for item in full_path.iterdir()]
            return "\n".join(items)
        
        # Read file
        with open(full_path, 'r') as f:
            lines = f.readlines()
        
        if view_range:
            start, end = view_range
            lines = lines[start-1:end]  # Convert to 0-indexed
        
        return "".join(lines)
    
    def create(self, path: str, content: str):
        """Create or overwrite file"""
        full_path = self.base_path / path.lstrip("/memories/")
        
        if not self._validate_path(full_path):
            raise ValueError("Invalid path")
        
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(full_path, 'w') as f:
            f.write(content)
    
    def str_replace(self, path: str, old_str: str, new_str: str):
        """Replace text in file"""
        full_path = self.base_path / path.lstrip("/memories/")
        
        if not self._validate_path(full_path):
            raise ValueError("Invalid path")
        
        with open(full_path, 'r') as f:
            content = f.read()
        
        if old_str not in content:
            raise ValueError(f"String not found: {old_str}")
        
        content = content.replace(old_str, new_str, 1)  # Replace first occurrence
        
        with open(full_path, 'w') as f:
            f.write(content)
    
    def insert(self, path: str, insert_line: int, new_str: str):
        """Insert text at specific line"""
        full_path = self.base_path / path.lstrip("/memories/")
        
        if not self._validate_path(full_path):
            raise ValueError("Invalid path")
        
        with open(full_path, 'r') as f:
            lines = f.readlines()
        
        lines.insert(insert_line - 1, new_str + "\n")
        
        with open(full_path, 'w') as f:
            f.writelines(lines)
    
    def delete(self, path: str):
        """Delete file or directory"""
        full_path = self.base_path / path.lstrip("/memories/")
        
        if not self._validate_path(full_path):
            raise ValueError("Invalid path")
        
        if full_path.is_dir():
            import shutil
            shutil.rmtree(full_path)
        else:
            full_path.unlink()
    
    def rename(self, path: str, new_path: str):
        """Rename or move file/directory"""
        full_path = self.base_path / path.lstrip("/memories/")
        new_full_path = self.base_path / new_path.lstrip("/memories/")
        
        if not self._validate_path(full_path) or not self._validate_path(new_full_path):
            raise ValueError("Invalid path")
        
        new_full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.rename(new_full_path)
    
    def _validate_path(self, path: Path) -> bool:
        """Ensure path is within base directory"""
        try:
            path.resolve().relative_to(self.base_path.resolve())
            return True
        except ValueError:
            return False
```

### Integration with Claude API

```python
async def chat_with_memory(user_message: str, memory: FileMemoryBackend):
    """Chat with Claude using memory tool"""
    
    response = await client.messages.create(
        model="claude-sonnet-4-5-20250929",
        betas=["context-management-2025-06-27"],
        tools=[{"type": "memory"}],
        messages=[{"role": "user", "content": user_message}],
        max_tokens=4096
    )
    
    # Process tool calls
    while response.stop_reason == "tool_use":
        tool_results = []
        
        for block in response.content:
            if block.type == "tool_use" and block.name == "memory":
                # Execute memory command
                result = execute_memory_command(block.input, memory)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })
        
        # Continue conversation with tool results
        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            betas=["context-management-2025-06-27"],
            tools=[{"type": "memory"}],
            messages=[
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_results}
            ],
            max_tokens=4096
        )
    
    return response

def execute_memory_command(tool_input: dict, memory: FileMemoryBackend) -> str:
    """Execute memory tool command"""
    command = tool_input["command"]
    
    try:
        if command == "view":
            return memory.view(
                tool_input["path"],
                tool_input.get("view_range")
            )
        elif command == "create":
            memory.create(tool_input["path"], tool_input["file_text"])
            return "File created successfully"
        elif command == "str_replace":
            memory.str_replace(
                tool_input["path"],
                tool_input["old_str"],
                tool_input["new_str"]
            )
            return "Text replaced successfully"
        elif command == "insert":
            memory.insert(
                tool_input["path"],
                tool_input["insert_line"],
                tool_input["new_str"]
            )
            return "Text inserted successfully"
        elif command == "delete":
            memory.delete(tool_input["path"])
            return "Deleted successfully"
        elif command == "rename":
            memory.rename(tool_input["path"], tool_input["new_path"])
            return "Renamed successfully"
        else:
            return f"Unknown command: {command}"
            
    except Exception as e:
        return f"Error: {str(e)}"
```

---

## Best Practices

### 1. Organize Memory Structure

```
/memories/
└── {bot_id}/
    └── servers/
        └── {server_id}/
            ├── culture.md           # Server-wide context
            ├── followups.json       # Pending follow-ups
            ├── channels/
            │   └── {channel_id}.md  # Per-channel context
            └── users/
                └── {user_id}.md     # Per-user profiles
```

### 2. Use Markdown for Profiles

```markdown
# User: Alice

**Timezone:** PST
**Active Hours:** 7pm-11pm weekdays

## Background
- Software engineer at TechCorp
- Interested in Python, ML, distributed systems

## Communication Style
- Direct, appreciates technical depth
- Prefers code examples over explanations

## Recent Topics
- Asked about PyTorch optimization (2025-09-28)
- Building ML rig with RTX 4090
```

### 3. Use JSON for Structured Data

```json
{
  "pending": [
    {
      "id": "followup_001",
      "user_id": "123456",
      "event": "Job interview at TechCorp",
      "mentioned_date": "2025-09-25T14:00:00Z",
      "follow_up_after": "2025-09-27T00:00:00Z",
      "priority": "high"
    }
  ],
  "completed": []
}
```

### 4. Keep Memories Concise

- Focus on persistent, relevant information
- Archive or delete stale content
- Use summaries rather than full transcripts
- Let Claude manage organization

---

## Troubleshooting

### Memory Not Persisting

**Problem:** Changes don't persist between conversations.

**Solution:**
- Ensure memory backend writes to disk
- Check file permissions
- Verify path validation isn't blocking writes

### Path Traversal Errors

**Problem:** Getting "Invalid path" errors.

**Solution:**
- Ensure all paths start with `/memories/`
- Check for `../` sequences
- Use Path.resolve() for validation

### Large Memory Files

**Problem:** Memory files growing too large.

**Solution:**
- Implement pagination for view commands
- Set maximum file sizes
- Archive old content periodically

### Tool Loop Errors

**Problem:** Claude makes repeated tool calls.

**Solution:**
- Check tool result format is correct
- Ensure errors are returned clearly
- Verify memory operations actually execute

---

## Additional Resources

- **Python SDK Examples:** https://github.com/anthropics/anthropic-sdk-python/tree/main/examples/memory
- **TypeScript SDK Examples:** https://github.com/anthropics/anthropic-sdk-typescript/tree/main/examples
- **Feedback Form:** https://forms.gle/YXC2EKGMhjN1c4L88
