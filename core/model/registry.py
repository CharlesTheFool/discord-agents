"""
Tool registry: every tool the model can be offered, as a definition, and
the one function that decides which ones a request carries.

A ToolDef says what a tool is and who runs it; each adapter renders it for
its wire API (`anthropic` holds the declaration the Anthropic adapter sends,
verbatim). assemble_tools filters twice before anything is rendered:
policy first (what this context may use), then capability (a server tool
the model's provider doesn't run is never offered). A filtered tool's
schema never reaches the model.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from core.model.catalog import ModelSpec
from tools.ask_prime import ASK_PRIME_TOOL
from tools.discord_tools import get_discord_tools
from tools.repository_tool import get_repository_tool
from tools.send_message import SEND_MESSAGE_TOOL
from tools.skills_tool import get_skill_request_tool
from tools.web_search import get_web_search_tools

SKILLS_BETA = "skills-2025-10-02"
FILES_BETA = "files-api-2025-04-14"


@dataclass(frozen=True)
class ToolDef:
    name: str
    mode: str                         # "client": we run it; "server": the provider does
    anthropic: dict                   # Anthropic declaration, sent as is
    requires: Tuple[str, ...] = ()    # ModelSpec flags a server tool needs

    @property
    def json_schema(self) -> Optional[dict]:
        """Input schema for wire APIs without Anthropic's typed tools."""
        return self.anthropic.get("input_schema")


def client_tool(declaration: dict) -> ToolDef:
    return ToolDef(declaration["name"], "client", declaration)


MEMORY = ToolDef("memory", "client", {"type": "memory_20250818", "name": "memory"})
# Server-side code execution rides the skills container (removed in 0.14.0)
CODE_EXECUTION = ToolDef("code_execution", "server",
                         {"type": "code_execution_20260120", "name": "code_execution"})


@dataclass
class ToolContext:
    """What one request may carry - the engine's policy inputs."""
    is_dm: bool = False
    discord_tools: bool = False
    repository: bool = False
    ask_prime: bool = False           # an executor exists; DMs still drop it
    web_search: bool = False
    web_citations: bool = True
    mcp_tools: list = field(default_factory=list)   # declarations from MCPManager
    skills: bool = False
    files: bool = False               # persisted file ids may ride the request


def assemble_tools(ctx: ToolContext, spec: ModelSpec) -> Tuple[List[ToolDef], list]:
    """(tools, betas) for one request, in the order the model sees them.
    The order is part of the cached prompt prefix - don't reshuffle it."""
    tools = [MEMORY]
    if ctx.discord_tools:
        tools += [client_tool(t) for t in get_discord_tools()]
    if ctx.repository:
        tools.append(client_tool(get_repository_tool()))
    if ctx.ask_prime and not ctx.is_dm:   # a DM already is the Prime
        tools.append(client_tool(ASK_PRIME_TOOL))
    tools.append(client_tool(SEND_MESSAGE_TOOL))
    if ctx.web_search:
        tools += [ToolDef(t["name"], "server", t, requires=("server_web_tools",))
                  for t in get_web_search_tools(citations_enabled=ctx.web_citations)]
    tools += [client_tool(t) for t in ctx.mcp_tools]

    betas = []
    if ctx.skills:
        tools += [CODE_EXECUTION, client_tool(get_skill_request_tool())]
        betas.append(SKILLS_BETA)
    if ctx.files:
        betas.append(FILES_BETA)

    tools = [t for t in tools if all(getattr(spec, flag) for flag in t.requires)]
    return tools, betas
