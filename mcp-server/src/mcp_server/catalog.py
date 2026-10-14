"""Load the tool yamls enabled in server.yaml, and only the table yamls they list (Pydantic models).

    server.yaml (tools: - {name: videos, path: .../tools/videos.yaml, enabled: true})
      -> tools/videos.yaml   (description, tables, MCP tools)
        -> tables/videos.yaml (table, database, columns)

Everything is checked at startup, so a bad yaml stops the server with a clear message.
"""

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from mcp_server.config import ServerConfig, ToolEntry, YamlModel, read_yaml, resolve_path
from mcp_server.db import Database, QueryError


class Column(YamlModel):
    name: str
    type: str
    description: str


class Table(YamlModel):
    table: str = Field(pattern=r"^main\.\w+$")  # name used in SQL; SQLite takes schema.table, schema = main
    database: str                              # SQLite file in settings.db_dir
    description: str
    columns: list[Column]

    @property
    def bare_name(self) -> str:
        """'main.videos' -> 'videos' (SQLite's authorizer reports bare names)."""
        return self.table.split(".")[-1]


class ToolDef(YamlModel):
    name: str                # MCP tool name the LLM sees, e.g. "videos_query"
    type: Literal["query"]   # keys of IMPLEMENTATIONS in tool_impl_connection.py
    input: list[str]         # parameter names; checked against the implementation at startup
    description: str


class ToolFile(YamlModel):
    name: str
    description: str
    tables: list[Table]      # the yaml lists paths; each is loaded into a Table below
    tools: list[ToolDef]

    @field_validator("tables", mode="before")
    @classmethod
    def _load_tables(cls, paths: list[str], info: ValidationInfo) -> list[dict]:
        return [read_yaml(resolve_path(p, info)) for p in paths]

    @model_validator(mode="after")
    def _one_database(self):
        databases = {t.database for t in self.tables}
        if len(databases) > 1:
            raise ValueError(f"all tables must be in the same database, got {sorted(databases)}")
        return self

    @property
    def database(self) -> str:
        return self.tables[0].database

    def tables_text(self) -> str:
        """Tables and columns as text for the LLM, e.g. for the tool descriptions."""
        lines = []
        for t in self.tables:
            lines.append(f"Table {t.table}: {t.description}")
            lines += [f"  - {c.name} ({c.type}): {c.description}" for c in t.columns]
        return "\n".join(lines)


def load_enabled_tools(config: ServerConfig) -> list[ToolFile]:
    return [load_tool_file(entry, config) for entry in config.tools if entry.enabled]


def load_tool_file(entry: ToolEntry, config: ServerConfig) -> ToolFile:
    tool_file = ToolFile.model_validate(read_yaml(entry.path), context={"base_dir": entry.path.parent})
    if tool_file.name != entry.name:
        raise ValueError(f"{entry.path.name}: name must be {entry.name!r} (its name in server.yaml), got {tool_file.name!r}")
    _check_against_database(tool_file, config)
    return tool_file


def _check_against_database(tool_file: ToolFile, config: ServerConfig) -> None:
    """The yaml must match the real tables, or the LLM would write SQL for columns that don't exist."""
    db = Database(config, tool_file.database, {t.bare_name for t in tool_file.tables})
    for t in tool_file.tables:
        try:
            real = db.run_query(f"SELECT * FROM {t.table} LIMIT 0")["columns"]
        except QueryError as e:
            raise ValueError(f"{tool_file.name}: table {t.table} can't be read: {e}") from None
        described = {c.name for c in t.columns}
        if described != set(real):
            raise ValueError(f"{tool_file.name}: columns of {t.table} don't match the database "
                             f"(not described: {sorted(set(real) - described)}, not in database: {sorted(described - set(real))})")
