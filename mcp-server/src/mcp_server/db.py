"""All access to the source databases (SQLite). The only file that changes for a Postgres swap.

Generic: one function, SQL string in -> result out. It knows nothing about datasets or
descriptions; each tool creates a Database with its own file and allowed tables (from its yaml).
Every query gets the safety rules:
  1. read-only connection (mode=ro)
  2. allowlist: only SELECT, and only the allowed tables (set_authorizer)
  3. timeout (set_progress_handler)
  4. row cap: the SQL is wrapped in SELECT * FROM (...) LIMIT row_limit + 1; the extra row
     tells us if the result was cut off. The agent's SQL must not end with ';' or a -- comment
  5. clean errors: QueryError with SQLite's message, no stack trace
"""

import sqlite3
import time
from contextlib import closing

from mcp_server.config import ServerConfig

# Authorizer actions a read-only SELECT needs; everything else (INSERT, PRAGMA, ATTACH...) is denied
ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}


class QueryError(Exception):
    """A query failed. The message is safe to show to the LLM, so it can fix its SQL."""


class Database:
    def __init__(self, config: ServerConfig, db: str, tables: set[str]):
        """db = file name in settings.db_dir (e.g. "videos.db"); tables = the only tables queries may read."""
        path = (config.settings.db_dir / db).resolve()
        if path.parent != config.settings.db_dir or not path.is_file():  # also blocks "../../etc/x"
            raise ValueError(f"database {db!r} not found in {config.settings.db_dir}")
        self.path = path
        self.tables = set(tables)
        self.row_limit = config.settings.row_limit
        self.timeout_sec = config.settings.timeout_sec

    def run_query(self, sql: str) -> dict:
        """Run one read-only SELECT. Returns {columns, rows, row_count, truncated}."""
        with closing(sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)) as conn:
            conn.set_authorizer(self._authorizer)
            deadline = time.monotonic() + self.timeout_sec
            # Called every 10,000 SQLite steps; returning True stops the query
            conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
            try:
                cursor = conn.execute(f"SELECT * FROM ({sql}) LIMIT {self.row_limit + 1}")
                rows = cursor.fetchall()
            except sqlite3.OperationalError as e:
                if str(e) == "interrupted":
                    raise QueryError(f"query stopped after {self.timeout_sec:g}s timeout") from None
                raise QueryError(str(e)) from None
            except sqlite3.Error as e:  # any other SQLite error
                raise QueryError(str(e)) from None

        truncated = len(rows) > self.row_limit
        rows = [list(r) for r in rows[: self.row_limit]]
        return {
            "columns": [d[0] for d in cursor.description],
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }

    def _authorizer(self, action, arg1, arg2, db_name, trigger):
        if action not in ALLOWED_ACTIONS:
            return sqlite3.SQLITE_DENY
        # arg1 = table name, e.g. sqlite_master. Real tables come with db_name "main";
        # CTE names (WITH c AS ...) come with None and are only views of allowed tables
        if action == sqlite3.SQLITE_READ and db_name is not None and arg1 not in self.tables:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
