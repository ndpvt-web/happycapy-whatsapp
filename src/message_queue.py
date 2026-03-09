"""SQLite-backed priority message queue (Theorem T_QUEUEFIRST).

Messages enter the queue before processing. Priority is derived from
importance score. Supports deferred handling and status tracking.

Security:
- Theorem T_LOGREDACT: Only stores first 50 chars of content as preview.
- Theorem T_FPERM: DB inherits restricted permissions.
"""

import json
import sqlite3
from pathlib import Path


def _priority_from_score(score: int) -> str:
    """Map importance score (1-10) to priority label."""
    if score >= 9:
        return "urgent"
    elif score >= 7:
        return "high"
    elif score >= 4:
        return "normal"
    else:
        return "low"


class MessageQueue:
    """Priority message queue with deferred handling."""

    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS message_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id TEXT NOT NULL,
                sender_name TEXT DEFAULT '',
                content_preview TEXT,
                importance_score INTEGER DEFAULT 5,
                importance_reasons TEXT,
                priority TEXT DEFAULT 'normal',
                status TEXT DEFAULT 'pending',
                deferred_until TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                replied_at TEXT
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_status ON message_queue(status)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_priority ON message_queue(priority)
        """)
        self._conn.commit()

    def add(
        self,
        sender_id: str,
        sender_name: str,
        content: str,
        score: int,
        reasons: list[str],
    ) -> int:
        """Add message to queue. Returns queue ID.

        Theorem T_LOGREDACT: Only first 50 chars stored as preview.
        """
        preview = content[:50] + ("..." if len(content) > 50 else "")
        priority = _priority_from_score(score)
        reasons_json = json.dumps(reasons)

        cursor = self._conn.execute(
            """INSERT INTO message_queue
               (sender_id, sender_name, content_preview, importance_score, importance_reasons, priority)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sender_id, sender_name, preview, score, reasons_json, priority),
        )
        self._conn.commit()
        return cursor.lastrowid

    def list_pending(self, limit: int = 20) -> list[dict]:
        """List pending messages, highest priority first."""
        cursor = self._conn.execute(
            """SELECT * FROM message_queue
               WHERE status = 'pending'
               ORDER BY CASE priority
                   WHEN 'urgent' THEN 0
                   WHEN 'high' THEN 1
                   WHEN 'normal' THEN 2
                   ELSE 3
               END, created_at ASC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]

    def mark_replied(self, queue_id: int) -> None:
        """Mark a queued message as replied."""
        self._conn.execute(
            "UPDATE message_queue SET status = 'replied', replied_at = datetime('now') WHERE id = ?",
            (queue_id,),
        )
        self._conn.commit()

    def mark_deferred(self, queue_id: int, until: str) -> None:
        """Defer a message for later handling."""
        self._conn.execute(
            "UPDATE message_queue SET status = 'deferred', deferred_until = ? WHERE id = ?",
            (until, queue_id),
        )
        self._conn.commit()

    def mark_escalated(self, queue_id: int) -> None:
        """Mark a message as escalated to admin."""
        self._conn.execute(
            "UPDATE message_queue SET status = 'escalated' WHERE id = ?",
            (queue_id,),
        )
        self._conn.commit()

    def stats(self) -> dict:
        """Get queue statistics by status."""
        cursor = self._conn.execute(
            "SELECT status, COUNT(*) as count FROM message_queue GROUP BY status"
        )
        result = {row["status"]: row["count"] for row in cursor.fetchall()}
        # Total
        result["total"] = sum(result.values())
        return result

    def cleanup(self, older_than_hours: int = 72) -> int:
        """Remove old replied/ignored entries. Returns count removed."""
        cursor = self._conn.execute(
            """DELETE FROM message_queue
               WHERE status IN ('replied', 'ignored')
               AND created_at < datetime('now', ? || ' hours')""",
            (f"-{older_than_hours}",),
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
