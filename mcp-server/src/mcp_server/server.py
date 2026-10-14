"""MCP server that lets an agent query the datasets in data/db/.

Run:
    uv run mcp-server                                              # stdio
    uv run uvicorn mcp_server.server:app --port 8000               # HTTP at http://127.0.0.1:8000/mcp
"""

from mcp.server import MCPServer

from mcp_server.config import load_config
from mcp_server.tool_impl_connection import register_tools

config = load_config()  # runs at startup: a bad server.yaml stops the server here
mcp = MCPServer("query-agent-mcp")

register_tools(mcp, config)  # tools come from the yamls

# ASGI app for HTTP; uvicorn serves it, and host/port are set on the uvicorn command line.
# Default security: only requests addressed to localhost are accepted.
app = mcp.streamable_http_app()


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
