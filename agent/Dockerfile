# Agent service image (FastAPI, api.py). Build and run from the repo root with Compose:
#   docker compose up -d --build
# The API key is NOT in the image (.env is in .dockerignore); Compose passes it in at runtime (env_file).

FROM python:3.12-slim

# uv from its official image, pinned to the local version so it reads uv.lock the same way
COPY --from=ghcr.io/astral-sh/uv:0.9.3 /uv /bin/

WORKDIR /app

# Workspace root + lock file, plus every member's pyproject (uv needs them to read the workspace)
COPY pyproject.toml uv.lock ./
COPY mcp-server/pyproject.toml mcp-server/
COPY ui/pyproject.toml ui/
COPY data/pyproject.toml data/
COPY agent/ agent/

# Install exactly the locked versions, only for the agent
RUN uv sync --frozen --package agent --no-dev

EXPOSE 8001

# 0.0.0.0 so traffic from outside the container can reach the service
CMD ["uv", "run", "--frozen", "--package", "agent", "uvicorn", "agent.api:app", "--host", "0.0.0.0", "--port", "8001"]
