"""
send_message tool (v0.12.0 phase 1) - native Discord messaging.

The model sends messages as deliberate tool calls instead of (only) having
its final text captured. Each call is one message; the tool result reports
exactly what went out - message id, attached files - so the model always
has ground truth about its own I/O.

Execution lives in ReactiveEngine._execute_send_message (it needs the
channel and the conversation's workshop).
"""

SEND_MESSAGE_TOOL = {
    "name": "send_message",
    "description": (
        "This is how you talk in Discord - not just another tool, but your "
        "messaging interface to the channel. Each call posts exactly one "
        "message, immediately, mid-turn. Call it several times in one turn "
        "to send several messages; they arrive in the order you call them, "
        "so you can compose like a real person texting: reply to one "
        "specific message (reply_to_message_id - ids are listed in "
        "<recent_messages> in the context update), then follow with a "
        "separate general message for the rest of the conversation, for "
        "example. Attach files from your workshop with attach_outputs (their "
        "/workspace paths) - writing 'attached' in your text never attaches "
        "anything; files only ride a message through attach_outputs. Keep "
        "each message texting-sized, no blank lines. The result reports the "
        "sent message's id and exactly which "
        "files actually attached - trust it over your own intentions. If "
        "you end your turn right after a send_message call, any extra text "
        "is not posted, so say everything through the tool. Text you write "
        "after other work, without a send right before it, still goes out "
        "as a normal message."
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
                    "Files in your workshop to attach, by path (e.g. "
                    "[\"/workspace/chart.png\"])."
                ),
            },
        },
        "required": ["content"],
    },
}


def get_send_message_tool() -> dict:
    return SEND_MESSAGE_TOOL
