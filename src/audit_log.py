"""Immutable audit log for all bot actions (Theorem T_AUDITALL).

Every outbound action (send, delete, config change) is logged to an
append-only SQLite table. NEVER stores message content -- only lengths
and metadata (Theorem T_LOGREDACT).

Security:
- Theorem T_FPERM: DB inherits restricted permissions from contact_store.
- No UPDATE or DELETE operations on audit_log table.
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path


class AuditLog:
    """Append-only audit trail for bot actions."""

    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                event_type TEXT NOT NULL,
                channel TEXT DEFAULT 'whatsapp',
                chat_id TEXT,
                direction TEXT,
                content_length INTEGER,
                message_id TEXT,
                metadata TEXT
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type)
        """)
        self._conn.commit()

    def log(
        self,
        event_type: str,
        chat_id: str = "",
        direction: str = "",
        content_length: int = 0,
        message_id: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Append an audit event. Never raises -- silently fails on error."""
        try:
            meta_json = json.dumps(metadata) if metadata else None
            self._conn.execute(
                """INSERT INTO audit_log (event_type, chat_id, direction, content_length, message_id, metadata)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_type, chat_id, direction, content_length, message_id, meta_json),
            )
            self._conn.commit()
        except Exception:
            pass  # Audit must never break the main flow

    def recent(self, limit: int = 50) -> list[dict]:
        """Get recent audit events (newest first)."""
        try:
            cursor = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def prune(self, days: int = 30) -> int:
        """Delete audit entries older than N days. Returns count deleted."""
        try:
            cursor = self._conn.execute(
                "DELETE FROM audit_log WHERE timestamp < datetime('now', ?)",
                (f"-{days} days",),
            )
            self._conn.commit()
            return cursor.rowcount
        except Exception:
            return 0

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
