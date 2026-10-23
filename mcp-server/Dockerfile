# MCP server image. Build from the repo root (the workspace files live there):
#   docker build -f mcp-server/Dockerfile -t query-agent-mcp .
#   docker run --rm --name query-agent-mcp -p 8000:8000 -v ./data/db:/app/data/db:ro query-agent-mcp

FROM python:3.12-slim

# uv from its official image, pinned to the local version so it reads uv.lock the same way
COPY --from=ghcr.io/astral-sh/uv:0.9.3 /uv /bin/

WORKDIR /app

# Workspace root + lock file, plus every member's pyproject (uv needs them to read the workspace)
COPY pyproject.toml uv.lock ./
COPY agent/pyproject.toml agent/
COPY ui/pyproject.toml ui/
COPY data/pyproject.toml data/
COPY mcp-server/ mcp-server/

# Install exactly the locked versions, only for mcp-server (no pyspark etc.)
RUN uv sync --frozen --package mcp-server --no-dev

EXPOSE 8000

# 0.0.0.0 so traffic from outside the container can reach the server
CMD ["uv", "run", "--frozen", "--package", "mcp-server", "uvicorn", "mcp_server.server:app", "--host", "0.0.0.0", "--port", "8000"]
