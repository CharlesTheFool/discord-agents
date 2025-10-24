"""
MCP (Model Context Protocol) Manager

Handles connections to remote HTTP MCP servers, tool discovery, and execution.
Supports environment variable substitution and graceful degradation.
"""

import os
import json
import logging
import httpx
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class MCPManager:
    """
    Manages Model Context Protocol server connections and tool execution.

    Responsibilities:
    - Load MCP server configuration from mcp_servers.json
    - Discover available tools from each server
    - Execute tool calls via HTTP
    - Handle errors and timeouts gracefully
    """

    def __init__(self, config_path: Path = Path("mcp_servers.json")):
        """
        Initialize MCP Manager.

        Args:
            config_path: Path to mcp_servers.json configuration file
        """
        self.config_path = config_path
        self.servers: Dict[str, Dict] = {}
        self.tools_cache: Dict[str, List[Dict]] = {}  # server_name -> tools
        self.http_client: Optional[httpx.AsyncClient] = None

        logger.info(f"MCPManager initialized with config: {config_path}")

    async def initialize(self) -> None:
        """
        Load configuration and discover tools from all enabled servers.

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        # Load configuration
        if not self.config_path.exists():
            logger.warning(f"MCP config not found: {self.config_path}. MCP disabled.")
            return

        with open(self.config_path) as f:
            config = json.load(f)

        # Initialize HTTP client
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            follow_redirects=True
        )

        # Load enabled servers
        for server_config in config.get("servers", []):
            if not server_config.get("enabled", False):
                logger.debug(f"MCP server '{server_config['name']}' is disabled, skipping")
                continue

            server_name = server_config["name"]
            self.servers[server_name] = self._process_server_config(server_config)

            # Discover tools from this server
            try:
                tools = await self._discover_tools(server_name)
                self.tools_cache[server_name] = tools
                logger.info(f"MCP server '{server_name}' loaded with {len(tools)} tools")
            except Exception as e:
                logger.error(f"Failed to discover tools from '{server_name}': {e}")
                # Continue with other servers (graceful degradation)

        logger.info(f"MCP initialization complete. {len(self.servers)} servers loaded.")

    def _process_server_config(self, config: Dict) -> Dict:
        """
        Process server configuration, substituting environment variables in headers.

        Args:
            config: Raw server configuration

        Returns:
            Processed configuration with environment variables substituted
        """
        processed = config.copy()

        # Substitute environment variables in headers
        if "headers" in processed:
            processed["headers"] = self._substitute_env_vars(processed["headers"])

        return processed

    def _substitute_env_vars(self, headers: Dict[str, str]) -> Dict[str, str]:
        """
        Substitute ${VAR_NAME} patterns with environment variable values.

        Args:
            headers: Dictionary with potentially templated values

        Returns:
            Dictionary with substituted values
        """
        result = {}
        for key, value in headers.items():
            if isinstance(value, str) and "${" in value:
                # Extract variable name
                import re
                matches = re.findall(r'\$\{(\w+)\}', value)
                for var_name in matches:
                    env_value = os.getenv(var_name)
                    if env_value:
                        value = value.replace(f"${{{var_name}}}", env_value)
                    else:
                        logger.warning(f"Environment variable {var_name} not found")
            result[key] = value
        return result

    async def _discover_tools(self, server_name: str) -> List[Dict]:
        """
        Discover available tools from an MCP server.

        Args:
            server_name: Name of the server

        Returns:
            List of tool definitions

        Raises:
            httpx.HTTPError: If request fails
        """
        server_config = self.servers[server_name]
        url = f"{server_config['url']}/mcp/tools"
        headers = server_config.get("headers", {})
        timeout = server_config.get("timeout_seconds", 30)

        response = await self.http_client.post(
            url,
            headers=headers,
            timeout=timeout
        )
        response.raise_for_status()

        tools = response.json().get("tools", [])

        # Prefix tool names with server name to avoid conflicts
        for tool in tools:
            original_name = tool["name"]
            tool["name"] = f"{server_name}_{original_name}"
            tool["_original_name"] = original_name
            tool["_server_name"] = server_name

        return tools

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        Execute an MCP tool call.

        Args:
            tool_name: Prefixed tool name (e.g., "github_get_commits")
            arguments: Tool arguments

        Returns:
            Tool execution result

        Raises:
            ValueError: If tool or server not found
            httpx.HTTPError: If request fails
        """
        # Extract server name from prefixed tool name
        if "_" not in tool_name:
            raise ValueError(f"Invalid MCP tool name (missing server prefix): {tool_name}")

        server_name, original_tool_name = tool_name.split("_", 1)

        if server_name not in self.servers:
            raise ValueError(f"MCP server not found: {server_name}")

        server_config = self.servers[server_name]
        url = f"{server_config['url']}/mcp/call_tool"
        headers = server_config.get("headers", {})
        timeout = server_config.get("timeout_seconds", 30)

        payload = {
            "name": original_tool_name,
            "arguments": arguments
        }

        logger.info(f"Executing MCP tool: {tool_name} with arguments: {arguments}")

        response = await self.http_client.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout
        )
        response.raise_for_status()

        result = response.json().get("result")
        logger.debug(f"MCP tool {tool_name} executed successfully")

        return result

    def get_tools_for_api(self) -> List[Dict]:
        """
        Get all discovered tools formatted for Claude API.

        Returns:
            List of tool definitions in Claude API format
        """
        all_tools = []

        for server_name, tools in self.tools_cache.items():
            for tool in tools:
                # Format for Claude API
                all_tools.append({
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {})
                })

        return all_tools

    async def shutdown(self) -> None:
        """Clean up resources (close HTTP client)."""
        if self.http_client:
            await self.http_client.aclose()
            logger.info("MCP HTTP client closed")
