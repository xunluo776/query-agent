"""Load and check server.yaml, so a bad config fails at startup with a clear message."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "server.yaml"  # mcp-server/server.yaml


@dataclass(frozen=True)
class ServerConfig:
    db_dir: Path
    row_limit: int
    timeout_sec: float
    tools: dict[str, dict] = field(default_factory=dict)  # tool name -> its registry entry

    def tool_enabled(self, name: str) -> bool:
        return self.tools.get(name, {}).get("enabled", False)


def load_config(path: Path = CONFIG_PATH) -> ServerConfig:
    raw = yaml.safe_load(path.read_text()) or {}
    settings = raw.get("settings") or {}
    tools = raw.get("tools") or {}

    if not settings.get("db_dir"):
        raise ValueError(f"{path.name}: settings.db_dir is missing")
    db_dir = (path.parent / settings["db_dir"]).resolve()
    if not db_dir.is_dir():
        raise ValueError(f"{path.name}: settings.db_dir not found: {db_dir}")

    row_limit = settings.get("row_limit")
    if not isinstance(row_limit, int) or row_limit <= 0:
        raise ValueError(f"{path.name}: settings.row_limit must be a positive integer, got {row_limit!r}")

    timeout_sec = settings.get("timeout_sec")
    if not isinstance(timeout_sec, (int, float)) or timeout_sec <= 0:
        raise ValueError(f"{path.name}: settings.timeout_sec must be a positive number, got {timeout_sec!r}")

    if not isinstance(tools, dict):
        raise ValueError(f"{path.name}: tools must be a mapping of tool name -> settings")
    for name, entry in tools.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("enabled"), bool):
            raise ValueError(f"{path.name}: tools.{name} needs 'enabled: true' or 'enabled: false'")

    return ServerConfig(db_dir=db_dir, row_limit=row_limit, timeout_sec=float(timeout_sec), tools=tools)
