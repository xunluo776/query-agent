"""Load and check server.yaml with Pydantic, so a bad config fails at startup with a clear message."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, PositiveFloat, PositiveInt, StrictBool, ValidationInfo, field_validator, model_validator

CONFIG_PATH = Path(__file__).resolve().parents[2] / "server.yaml"  # mcp-server/server.yaml


class YamlModel(BaseModel):
    """Base for every yaml model: read-only, unknown keys are errors (catches typos), text is trimmed."""
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


def read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"file not found: {path}")
    return yaml.safe_load(path.read_text()) or {}


def resolve_path(value: str, info: ValidationInfo) -> Path:
    """Relative paths in a yaml are relative to that yaml file (passed in as context)."""
    return (info.context["base_dir"] / value).resolve()


class Settings(YamlModel):
    db_dir: Path            # folder with the SQLite files
    row_limit: PositiveInt  # safety cap: max rows one query can return
    timeout_sec: PositiveFloat

    @field_validator("db_dir", mode="before")
    @classmethod
    def _db_dir(cls, value: str, info: ValidationInfo) -> Path:
        path = resolve_path(value, info)
        if not path.is_dir():
            raise ValueError(f"folder not found: {path}")
        return path


class ToolEntry(YamlModel):
    name: str               # display name, e.g. for agent steps in the UI
    path: Path              # tool yaml
    enabled: StrictBool     # true/false only, not "yes"

    @field_validator("path", mode="before")
    @classmethod
    def _path(cls, value: str, info: ValidationInfo) -> Path:
        path = resolve_path(value, info)
        if not path.is_file():
            raise ValueError(f"file not found: {value}")
        return path


class ServerConfig(YamlModel):
    settings: Settings
    tools: list[ToolEntry] = []  # the registry, in server.yaml order

    @model_validator(mode="after")
    def _unique_names(self):
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            raise ValueError(f"tool names must be unique, got {names}")
        return self


def load_config(path: Path = CONFIG_PATH) -> ServerConfig:
    return ServerConfig.model_validate(read_yaml(path), context={"base_dir": path.parent})
