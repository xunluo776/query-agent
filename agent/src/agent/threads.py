"""The chat list for the UI sidebar: one row per chat (thread) in chats.db, next to the checkpointer's tables.

The checkpointer saves each chat's messages; this table only records which chats exist and when they were
last used, so the sidebar can list them without loading every saved step. Room for a title column later.
"""

from datetime import UTC, datetime

import aiosqlite


class ThreadStore:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    async def setup(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS threads ("
            "thread_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        await self.conn.commit()

    async def touch(self, thread_id: str) -> None:
        """Add the chat, or move it to the top of the list if it exists."""
        now = datetime.now(UTC).isoformat(timespec="seconds")
        await self.conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?) "
            "ON CONFLICT(thread_id) DO UPDATE SET updated_at = excluded.updated_at",
            (thread_id, now, now),
        )
        await self.conn.commit()

    async def list(self) -> list[dict]:
        """All chats, most recently used first."""
        async with self.conn.execute(
            "SELECT thread_id, created_at, updated_at FROM threads ORDER BY updated_at DESC"
        ) as cursor:
            return [{"thread_id": t, "created_at": c, "updated_at": u} for t, c, u in await cursor.fetchall()]
