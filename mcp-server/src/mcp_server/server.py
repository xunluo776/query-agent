"""MCP server that lets an agent query the datasets in data/db/.

Run:
    uv run mcp-server                                              # stdio
    uv run uvicorn mcp_server.server:app --port 8000               # HTTP at http://127.0.0.1:8000/mcp
"""

from mcp.server import MCPServer

mcp = MCPServer("query-agent-mcp")


@mcp.tool()
def ping() -> str:
    """Health check: returns 'pong'. Placeholder until the real tools exist."""
    return "pong"


# ASGI app for HTTP; uvicorn serves it, and host/port are set on the uvicorn command line.
# Default security: only requests addressed to localhost are accepted.
app = mcp.streamable_http_app()


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
