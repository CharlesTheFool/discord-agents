"""
Repository tool definition (v0.6.1) - the bot's per-server file drive.

Write/management surface only: reading repository files goes through the
existing discord_tools get_attachment action (repo files are attachment rows).
"""


def get_repository_tool() -> dict:
    return {
        "name": "repository",
        "description": (
            "Your persistent file repository for this server - a local folder that survives "
            "restarts and that the user can also drop files into directly. Save anything worth "
            "keeping: notes you author (save_file), Discord attachments worth preserving "
            "(save_attachment), or files you created via code execution (save_output with the "
            "output's file_id). Keep it organized with delete/rename and nested folders. "
            "To READ a repository file, use discord_tools get_attachment with the attachment_id "
            "shown in <repository> or the list action. Current contents appear in <repository> "
            "in your context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "save_file", "save_attachment", "save_output", "delete", "rename"],
                    "description": "Repository action to execute",
                },
                "path": {
                    "type": "string",
                    "description": "[save_file/save_attachment/save_output/delete] Relative path inside the repository, e.g. 'notes/summary.md'. Optional for the save_* copy actions (defaults to the source filename).",
                },
                "content": {
                    "type": "string",
                    "description": "[save_file] Full text content to write (UTF-8). For binary files use save_output via code execution instead.",
                },
                "attachment_id": {
                    "type": "string",
                    "description": "[save_attachment] ID of an existing Discord attachment to copy into the repository",
                },
                "file_id": {
                    "type": "string",
                    "description": "[save_output] Files API id of a code-execution output to download into the repository",
                },
                "old_path": {
                    "type": "string",
                    "description": "[rename] Current relative path",
                },
                "new_path": {
                    "type": "string",
                    "description": "[rename] New relative path (must not exist)",
                },
            },
            "required": ["action"],
        },
    }
