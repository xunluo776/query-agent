"""Connects each MCP tool in the tool yamls to its implementation.

    tool yaml `type: query`  ->  IMPLEMENTATIONS["query"]  ->  db.run_query(sql)

Each implementation is built once per tool yaml, bound to that yaml's database and tables.
"""

from collections.abc import Callable

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server.catalog import load_enabled_tools
from mcp_server.config import ServerConfig
from mcp_server.db import Database, QueryError


def make_query(db: Database) -> Callable:
    def query(sql: str) -> dict:
        """Run one read-only SELECT."""
        try:
            return db.run_query(sql)
        except QueryError as e:
            # ToolError: the SDK shows this message to the model (it hides other exceptions)
            raise ToolError(str(e)) from None

    return query


IMPLEMENTATIONS = {"query": make_query}  # keys = catalog.TOOL_TYPES


def register_tools(mcp: MCPServer, config: ServerConfig) -> None:
    """Add every MCP tool from the enabled tool yamls. Adding a dataset = yaml only, no code."""
    for tool_file in load_enabled_tools(config):
        db = Database(config, tool_file.database, {t.bare_name for t in tool_file.tables})
        for tool in tool_file.tools:
            # The model sees every table and column up front, so it can write the SQL
            description = f"{tool.description}\n\n{tool_file.description}\n\n{tool_file.tables_text()}"
            mcp.add_tool(IMPLEMENTATIONS[tool.type](db), name=tool.name, description=description)
