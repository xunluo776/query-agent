# UI image (Streamlit, app.py). Build and run from the repo root with Compose:
#   docker compose up -d --build      then open http://localhost:8501

FROM python:3.12-slim

# uv from its official image, pinned to the local version so it reads uv.lock the same way
COPY --from=ghcr.io/astral-sh/uv:0.9.3 /uv /bin/

WORKDIR /app

# Workspace root + lock file, plus every member's pyproject (uv needs them to read the workspace)
COPY pyproject.toml uv.lock ./
COPY mcp-server/pyproject.toml mcp-server/
COPY agent/pyproject.toml agent/
COPY data/pyproject.toml data/
COPY ui/ ui/

# Install exactly the locked versions, only for the UI
RUN uv sync --frozen --package ui --no-dev

EXPOSE 8501

# 0.0.0.0 so the browser on the Mac can reach it; headless = don't try to open a browser in the container
CMD ["uv", "run", "--frozen", "--package", "ui", "streamlit", "run", "ui/src/ui/app.py", \
     "--server.address", "0.0.0.0", "--server.port", "8501", "--server.headless", "true"]
