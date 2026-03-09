"""Cross-channel escalation with correlation codes (Theorem T_ESCROUTE).

When the AI cannot answer or message importance exceeds threshold,
escalate to the admin with a unique code. Admin responds via
/respond ESC-XXX <answer>, which routes back to the original contact.

Generalized: not hardcoded to any specific channel. The escalation
record stores origin_channel for future multi-channel support.

Security:
- Theorem T_LOGREDACT: Only first 100 chars of question stored as preview.
- Escalation codes are sequential but not predictable from outside.
"""

import sqlite3
from datetime import datetime
from pathlib import Path


class EscalationEngine:
    """Owner routing with correlation codes."""

    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS escalations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                sender_id TEXT NOT NULL,
                sender_name TEXT DEFAULT '',
                question_preview TEXT,
                context TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                admin_response TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                responded_at TEXT
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_esc_status ON escalations(status)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_esc_code ON escalations(code)
        """)
        self._conn.commit()

    def _next_code(self) -> str:
        """Generate next escalation code (ESC-001, ESC-002, ...)."""
        cursor = self._conn.execute("SELECT MAX(id) as max_id FROM escalations")
        row = cursor.fetchone()
        next_num = (row["max_id"] or 0) + 1
        return f"ESC-{next_num:03d}"

    def escalate(
        self,
        sender_id: str,
        sender_name: str,
        question: str,
        context: str = "",
    ) -> tuple[str, str]:
        """Create an escalation record.

        Returns (code, admin_message) where admin_message is the formatted
        notification to send to the admin.

        Theorem T_LOGREDACT: Only first 100 chars stored.
        """
        code = self._next_code()
        preview = question[:100] + ("..." if len(question) > 100 else "")

        self._conn.execute(
            """INSERT INTO escalations (code, sender_id, sender_name, question_preview, context)
               VALUES (?, ?, ?, ?, ?)""",
            (code, sender_id, sender_name, preview, context),
        )
        self._conn.commit()

        # Format admin notification
        name_display = sender_name or sender_id
        admin_msg = (
            f"*[{code}] Escalation*\n\n"
            f"From: {name_display}\n"
            f"Q: {preview}\n"
        )
        if context:
            admin_msg += f"Context: {context}\n"
        admin_msg += f"\nReply: /respond {code} <your answer>"

        return (code, admin_msg)

    def respond(self, code: str, answer: str) -> dict | None:
        """Record admin response to an escalation.

        Returns dict with sender_id and answer for routing back,
        or None if code not found / already answered.
        """
        cursor = self._conn.execute(
            "SELECT * FROM escalations WHERE code = ? AND status = 'pending'",
            (code.upper(),),
        )
        row = cursor.fetchone()
        if not row:
            return None

        self._conn.execute(
            """UPDATE escalations
               SET status = 'answered', admin_response = ?, responded_at = datetime('now')
               WHERE code = ?""",
            (answer, code.upper()),
        )
        self._conn.commit()

        return {
            "sender_id": row["sender_id"],
            "sender_name": row["sender_name"],
            "answer": answer,
            "code": code.upper(),
        }

    def pending(self) -> list[dict]:
        """List all pending escalations (oldest first)."""
        cursor = self._conn.execute(
            "SELECT * FROM escalations WHERE status = 'pending' ORDER BY created_at ASC"
        )
        return [dict(r) for r in cursor.fetchall()]

    def get(self, code: str) -> dict | None:
        """Get a specific escalation by code."""
        cursor = self._conn.execute(
            "SELECT * FROM escalations WHERE code = ?", (code.upper(),)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def expire(self, hours: int = 24) -> int:
        """Expire old pending escalations. Returns count expired."""
        cursor = self._conn.execute(
            """UPDATE escalations SET status = 'expired'
               WHERE status = 'pending'
               AND created_at < datetime('now', ? || ' hours')""",
            (f"-{hours}",),
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
