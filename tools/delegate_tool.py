"""The `delegate` tool (spec 7.2, 0.15.0): hand work to a worker that runs on
its own in the workshop and reports back through a completion note."""

DELEGATE_TOOL = {
    "name": "delegate",
    "description": (
        "Hand a self-contained piece of work to a helper that runs on its own in your "
        "workshop, so the conversation can go on meanwhile. It shares /workspace with you "
        "(its shell is its own), can read your memory and search the message history, and "
        "can't send messages or change memory. When it's done, a note with its report lands "
        "here. Worth it for work that takes many steps; a quick command, just run yourself."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "What to do, and what to report back"},
            "context": {"type": "string",
                        "description": "Anything it needs that isn't in the files: who asked, "
                                       "constraints, what's been tried"},
        },
        "required": ["task"],
    },
}
