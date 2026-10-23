"""Connect to the MCP server and get its tools as LangChain tools. Only a connection; the tools are the server's job.

    MCP server (mcp_url) --list tools--> LangChain tools (name, description, args schema)

Each tool reconnects when it is called, so no connection is held open. The tools are async only (use ainvoke).
A tool call returns a ToolMessage: status "error" if the server raised ToolError (bad SQL),
otherwise .content holds the result as JSON text ({columns, rows, row_count, truncated}); graph.py parses it.
"""

import warnings

from langchain_core._api import LangChainBetaWarning
from langchain_core.tools import BaseTool

# langchain.mcp is beta (works with MCP SDK v2; see dev log); hide the warning it prints on import
warnings.filterwarnings("ignore", category=LangChainBetaWarning)
from langchain.mcp import MCPAdapter  # noqa: E402

from agent.setting_reader import load_settings  # noqa: E402


async def get_tools() -> list[BaseTool]:
    """List the server's tools once, from mcp_url in settings.yaml (or the MCP_URL env variable)."""
    mcp_url = load_settings().mcp_url
    return await MCPAdapter(mcp_url).list_tools()
