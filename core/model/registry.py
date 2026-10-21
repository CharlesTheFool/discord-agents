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
from tools.bash_tool import BASH_TOOL
from tools.delegate_tool import DELEGATE_TOOL
from tools.discord_tools import get_discord_tools
from tools.editor_tool import EDITOR_DECLARATION, EDITOR_DESCRIPTION, EDITOR_NAME, EDITOR_SCHEMA
from tools.network_access_tool import NETWORK_ACCESS_TOOL
from tools.repository_tool import get_repository_tool
from tools.send_message import SEND_MESSAGE_TOOL
from tools.skills_tool import SKILL_READ_TOOL, SKILL_REQUEST_TOOL
from tools.web_search import get_web_search_tools

FILES_BETA = "files-api-2025-04-14"


@dataclass(frozen=True)
class ToolDef:
    name: str
    mode: str                         # "client": we run it; "server": the provider does
    anthropic: dict                   # Anthropic declaration, sent as is
    requires: Tuple[str, ...] = ()    # ModelSpec flags a server tool needs
    schema: Optional[dict] = None     # our own input schema, for a typed declaration
    description: str = ""             # ...and the words that go with it

    @property
    def json_schema(self) -> Optional[dict]:
        """Input schema for wire APIs without Anthropic's typed tools."""
        return self.schema or self.anthropic.get("input_schema")

    def plain(self) -> dict:
        """A typed tool as a plain client tool, for models without typed tools."""
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


def client_tool(declaration: dict) -> ToolDef:
    return ToolDef(declaration["name"], "client", declaration)


MEMORY = ToolDef("memory", "client", {"type": "memory_20250818", "name": "memory"})
# The workshop (0.14): ours to run, on every driver; the editor rides
# Anthropic's typed text editor where the model has it
BASH = client_tool(BASH_TOOL)
EDITOR = ToolDef(EDITOR_NAME, "client", EDITOR_DECLARATION, schema=EDITOR_SCHEMA,
                 description=EDITOR_DESCRIPTION)
NETWORK_ACCESS = client_tool(NETWORK_ACCESS_TOOL)
REQUEST_SKILL = client_tool(SKILL_REQUEST_TOOL)
READ_SKILL = client_tool(SKILL_READ_TOOL)   # request_skill without the workshop
DELEGATE = client_tool(DELEGATE_TOOL)      # workers (0.15.0)


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
    workshop: bool = False            # this conversation's scope runs a sandbox
    text_skills: bool = False         # text-only skills exist (offered without the workshop)
    files: bool = False               # documents may ride as Files API ids
    role: str = "resident"            # "worker" (0.15.0): reads and works, never writes


def assemble_tools(ctx: ToolContext, spec: ModelSpec) -> Tuple[List[ToolDef], list]:
    """(tools, betas) for one request, in the order the model sees them.
    The order is part of the cached prompt prefix - don't reshuffle it."""
    if ctx.role == "worker":
        return _worker_tools(ctx, spec), []
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

    if ctx.workshop:
        tools += [BASH, EDITOR, NETWORK_ACCESS, REQUEST_SKILL, DELEGATE]
    elif ctx.text_skills:
        tools.append(READ_SKILL)

    betas = []
    if ctx.files:
        betas.append(FILES_BETA)

    return _capable(tools, spec), betas


def _capable(tools: List[ToolDef], spec: ModelSpec) -> List[ToolDef]:
    return [t for t in tools if all(getattr(spec, flag) for flag in t.requires)]


def _worker_tools(ctx: ToolContext, spec: ModelSpec) -> List[ToolDef]:
    """A worker's set (spec 7.2): memory (the executor refuses its writes),
    message and file reads, web tools where the provider runs them, the
    workshop. Never send_message, the repository, ask_prime, MCP tools,
    network approvals or delegate - and the worker's dispatch refuses those
    names too, whatever the model emits."""
    tools = [MEMORY]
    if ctx.discord_tools:
        tools += [client_tool(t) for t in get_discord_tools()]
    if ctx.web_search:
        tools += [ToolDef(t["name"], "server", t, requires=("server_web_tools",))
                  for t in get_web_search_tools(citations_enabled=ctx.web_citations, worker=True)]
    tools += [BASH, EDITOR, REQUEST_SKILL]
    return _capable(tools, spec)
