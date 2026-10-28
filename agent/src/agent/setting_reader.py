"""Load settings.yaml with Pydantic, so a bad config fails at startup with a clear message."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, PositiveInt

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.yaml"


class Settings(BaseModel):
    """Read-only; unknown keys are errors (catches typos), text is trimmed."""
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    model: str                    # Gemini model name
    mcp_url: str                  # MCP server URL
    api_url: str                  # agent service URL, for the terminal client
    chat_db: Path                 # SQLite file for chat sessions (api.py)
    recursion_limit: PositiveInt  # passed to LangGraph in graph.py
    read_cap: PositiveInt         # max result rows the LLM reads (graph.py)


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    # .env -> environment (GOOGLE_API_KEY for Gemini); searched from this file upward, real env variables win
    load_dotenv()
    data = yaml.safe_load(path.read_text()) or {}
    if url := os.getenv("MCP_URL"):  # e.g. http://mcp-server:8000/mcp in Docker Compose
        data["mcp_url"] = url
    if "chat_db" in data:  # relative to settings.yaml, so the Mac and Docker use the same path
        data["chat_db"] = (path.parent / data["chat_db"]).resolve()
    return Settings.model_validate(data)
