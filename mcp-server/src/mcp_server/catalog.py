"""Load the tool yamls enabled in server.yaml, and only the table yamls they list.

    server.yaml (tools: videos: {enabled: true})
      -> tools/videos.yaml   (description, tables, MCP tools)
        -> tables/videos.yaml (table, database, columns)

Everything is checked at startup, so a bad yaml stops the server with a clear message.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from mcp_server.config import ServerConfig
from mcp_server.db import Database, QueryError

PACKAGE_DIR = Path(__file__).resolve().parent
TOOL_TYPES = {"query"}  # implementations in tool_impl_connection.py


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str


@dataclass(frozen=True)
class Table:
    table: str          # name used in SQL, e.g. "main.videos"
    database: str       # SQLite file in settings.db_dir
    description: str
    columns: list[Column]

    @property
    def bare_name(self) -> str:
        """'main.videos' -> 'videos' (SQLite's authorizer reports bare names)."""
        return self.table.split(".")[-1]


@dataclass(frozen=True)
class ToolDef:
    name: str           # MCP tool name the LLM sees, e.g. "videos_query"
    type: str           # one of TOOL_TYPES
    description: str


@dataclass(frozen=True)
class ToolFile:
    name: str
    description: str
    tables: list[Table]
    tools: list[ToolDef]

    @property
    def database(self) -> str:
        return self.tables[0].database  # all tables share one database (checked on load)

    def tables_text(self) -> str:
        """Tables and columns as text for the LLM, e.g. for the query tool's description."""
        lines = []
        for t in self.tables:
            lines.append(f"Table {t.table}: {t.description}")
            lines += [f"  - {c.name} ({c.type}): {c.description}" for c in t.columns]
        return "\n".join(lines)


def load_enabled_tools(config: ServerConfig) -> list[ToolFile]:
    return [load_tool_file(name, config) for name, entry in config.tools.items() if entry["enabled"]]


def load_tool_file(name: str, config: ServerConfig) -> ToolFile:
    path = PACKAGE_DIR / "tools" / f"{name}.yaml"
    raw = _read_yaml(path)
    where = f"tools/{name}.yaml"

    if raw.get("name") != name:
        raise ValueError(f"{where}: name must be {name!r} (the key in server.yaml), got {raw.get('name')!r}")
    _require(raw, ["description", "tables", "tools"], where)

    tables = [_load_table((path.parent / p).resolve()) for p in raw["tables"]]
    if len({t.database for t in tables}) > 1:
        raise ValueError(f"{where}: all tables must be in the same database, got {sorted({t.database for t in tables})}")

    tools = []
    for i, entry in enumerate(raw["tools"]):
        _require(entry, ["name", "type", "description"], f"{where} tools[{i}]")
        if entry["type"] not in TOOL_TYPES:
            raise ValueError(f"{where}: tool {entry['name']!r} has unknown type {entry['type']!r}; use one of {sorted(TOOL_TYPES)}")
        tools.append(ToolDef(entry["name"], entry["type"], entry["description"].strip()))

    tool_file = ToolFile(name, raw["description"].strip(), tables, tools)
    _check_against_database(tool_file, config, where)
    return tool_file


def _load_table(path: Path) -> Table:
    raw = _read_yaml(path)
    where = f"tables/{path.name}"
    _require(raw, ["table", "database", "description", "columns"], where)
    if not raw["table"].startswith("main.") or raw["table"].count(".") != 1:
        raise ValueError(f"{where}: table must look like 'main.<name>' for SQLite, got {raw['table']!r}")

    columns = []
    for i, c in enumerate(raw["columns"]):
        _require(c, ["name", "type", "description"], f"{where} columns[{i}]")
        columns.append(Column(c["name"], c["type"], str(c["description"])))
    return Table(raw["table"], raw["database"], raw["description"].strip(), columns)


def _check_against_database(tool_file: ToolFile, config: ServerConfig, where: str) -> None:
    """The yaml must match the real tables, or the LLM would write SQL for columns that don't exist."""
    db = Database(config, tool_file.database, {t.bare_name for t in tool_file.tables})
    for t in tool_file.tables:
        try:
            real = db.run_query(f"SELECT * FROM {t.table} LIMIT 0")["columns"]
        except QueryError as e:
            raise ValueError(f"{where}: table {t.table} can't be read: {e}") from None
        described = [c.name for c in t.columns]
        if set(described) != set(real):
            missing, extra = sorted(set(real) - set(described)), sorted(set(described) - set(real))
            raise ValueError(f"tables for {t.table}: columns don't match the database (not described: {missing}, not in database: {extra})")


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"file not found: {path.relative_to(PACKAGE_DIR) if path.is_relative_to(PACKAGE_DIR) else path}")
    return yaml.safe_load(path.read_text()) or {}


def _require(raw: dict, keys: list[str], where: str) -> None:
    missing = [k for k in keys if not raw.get(k)]
    if missing:
        raise ValueError(f"{where}: missing {missing}")
