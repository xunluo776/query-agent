"""Query results as Parquet files, like objects in S3, deleted after a TTL, like an S3 lifecycle rule.

    graph.py (tools node): save(thread_id, result_id, result)     one file per result: <thread_id>_<r#>.parquet
    api.py:  csv(thread_id, result_id) -> Download CSV           cleanup() every few minutes + delete_thread()

The files live in data/state/results/ (a Docker volume), next to chats.db, and are never in git.
"""

import io
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq


def unique_names(columns: list[str]) -> list[str]:
    """SELECT a.n, b.n gives two columns named n; files (and DuckDB) need unique names: n, n_2."""
    seen, names = {}, []
    for c in columns:
        seen[c] = seen.get(c, 0) + 1
        names.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
    return names


class ResultStore:
    def __init__(self, folder: Path, ttl_min: int):
        self.folder = folder
        self.ttl_sec = ttl_min * 60
        folder.mkdir(parents=True, exist_ok=True)

    def _path(self, thread_id: str, result_id: str) -> Path:
        return self.folder / f"{thread_id}_{result_id}.parquet"

    def save(self, thread_id: str, result_id: str, result: dict) -> None:
        """Write one full result. Columns are typed by Arrow; a column with mixed types is saved as text."""
        arrays = []
        for i, _ in enumerate(result["columns"]):
            values = [row[i] for row in result["rows"]]
            try:
                arrays.append(pa.array(values))
            except (pa.ArrowInvalid, pa.ArrowTypeError):  # e.g. numbers and text in one column (SQLite allows it)
                arrays.append(pa.array([None if v is None else str(v) for v in values]))
        table = pa.Table.from_arrays(arrays, names=unique_names(result["columns"]))
        pq.write_table(table, self._path(thread_id, result_id))

    def csv(self, thread_id: str, result_id: str) -> bytes | None:
        """The file as CSV, or None if it doesn't exist or has expired."""
        path = self._path(thread_id, result_id)
        if not path.is_file() or self._expired(path):
            return None
        buffer = io.BytesIO()
        pa_csv.write_csv(pq.read_table(path), buffer)
        return buffer.getvalue()

    def cleanup(self) -> int:
        """Delete expired files; returns how many."""
        expired = [p for p in self.folder.glob("*.parquet") if self._expired(p)]
        for p in expired:
            p.unlink(missing_ok=True)
        return len(expired)

    def delete_thread(self, thread_id: str) -> None:
        """Delete a chat's files (when the chat is deleted)."""
        for p in self.folder.glob(f"{thread_id}_*.parquet"):
            p.unlink(missing_ok=True)

    def _expired(self, path: Path) -> bool:
        return time.time() - path.stat().st_mtime > self.ttl_sec
