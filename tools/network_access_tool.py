"""`request_network_access` (spec 5.7/5.8): ask the operator to let this
workshop reach one more host. The turn doesn't wait; the decision arrives
later as a note in this channel."""
from core.workshop.service import WorkshopUnavailable

NETWORK_ACCESS_TOOL = {
    "name": "request_network_access",
    "description": (
        "Ask the operator to let your workshop reach a host it can't reach yet "
        "(it gets package downloads only by default). Name one host, like "
        "github.com, and say briefly why. You don't wait for the answer: it "
        "arrives here as a note, and if it's a yes, requests to that host just "
        "start working."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "A host name, e.g. github.com"},
            "reason": {"type": "string", "description": "One or two sentences on why"},
        },
        "required": ["host", "reason"],
    },
}


async def run_request_network_access(approvals, workshop, tool_input: dict, *, server_id,
                                     channel_id, parent_id=None, is_thread=False) -> str:
    if approvals is None:
        return "Network requests aren't available right now."
    try:
        scope = workshop.scope_for_turn(server_id, channel_id, parent_id=parent_id, is_thread=is_thread)
    except WorkshopUnavailable as e:
        return f"The workshop isn't available: {e}."
    return await approvals.request(scope, tool_input.get("host", ""), tool_input.get("reason", ""),
                                   server_id=server_id, channel_id=channel_id)
