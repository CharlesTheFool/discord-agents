"""
send_message tool (v0.12.0 phase 1) - native Discord messaging.

The model sends messages as deliberate tool calls instead of (only) having
its final text captured. Each call is one message; the tool result reports
exactly what went out - message id, attached files - so the model always
has ground truth about its own I/O.

Execution lives in ReactiveEngine._execute_send_message (it needs the
channel, the turn's container outputs, and the Files API client).
"""

SEND_MESSAGE_TOOL = {
    "name": "send_message",
    "description": (
        "Send one Discord message to the current channel, immediately, "
        "mid-turn. Use it when you want to anchor your reply to a specific "
        "message (reply_to_message_id - ids are listed in <recent_messages> "
        "in the context update), attach files you created with code "
        "execution this turn (attach_outputs), or pace out a multi-message "
        "response. Each call is exactly one message - keep it texting-sized, "
        "no blank lines. Plain text you output at the end of your turn is "
        "still sent as ordinary messages too, so for a simple reply you "
        "don't need this tool; never send the same content both ways. The "
        "result tells you the sent message's id and exactly which files "
        "actually attached."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The message text. One message, texting-sized.",
            },
            "reply_to_message_id": {
                "type": "string",
                "description": (
                    "Anchor this message as a Discord reply to that message "
                    "id. Use when answering one specific message in a busy "
                    "conversation."
                ),
            },
            "attach_outputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Filenames of files created via code execution during "
                    "THIS turn to attach (e.g. [\"chart.png\"]). Files from "
                    "previous turns no longer exist - regenerate them."
                ),
            },
        },
        "required": ["content"],
    },
}


def get_send_message_tool() -> dict:
    return SEND_MESSAGE_TOOL
