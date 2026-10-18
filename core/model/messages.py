"""
The persisted message vocabulary: what conversation state stores and every
driver reads.

In 0.13.0 this is exactly what the bot already persisted, written down, so
requests stay byte-identical. It is structurally the Anthropic Messages
vocabulary; an adapter for another provider translates to and from it.

Message dict:
    role            "user" | "assistant"
    content         str, or a list of blocks (PERSISTED_BLOCK_TYPES)
    message_type    internal: discord_user | discord_assistant | tool_use |
                    tool_result (the message cap counts discord_* only)
    attachment_ids  internal: attachments carried by this message
    model           internal, assistant messages from 0.13.0 on: the model
                    that produced it (see message_model)

Internal fields are stripped before a request is built.
"""

ROLES = ("user", "assistant")

PERSISTED_BLOCK_TYPES = frozenset({
    "text", "image", "document", "container_upload",
    "tool_use", "tool_result", "thinking", "redacted_thinking",
})

INTERNAL_FIELDS = frozenset({"message_type", "attachment_ids", "model"})


def message_model(message: dict, default: str) -> str:
    """The model that produced an assistant message. Rows from before
    0.13.0 carry no stamp and count as the configured main model, so their
    thinking is never dropped by mistake."""
    return message.get("model") or default
