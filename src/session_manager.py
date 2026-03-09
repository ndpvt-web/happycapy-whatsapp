"""Conversation session manager for tracking continuity.

Tracks per-contact conversation sessions with timeout-based freshness detection.
When a contact resumes after inactivity, provides context signals so the AI
knows whether to continue or start fresh.

Premise P_SESSION: Conversations have temporal coherence. Messages within a
session relate to each other; messages after a long gap may not.

Theorem T_TIMEOUT: A session is "fresh" if last_activity + timeout < now.
Theorem T_CONTEXT: Resuming an expired session includes summary context.
"""

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


class SessionManager:
    """Per-contact session tracking with timeout-based freshness."""

    # Default session timeout: 30 minutes of inactivity
    DEFAULT_TIMEOUT_S = 30 * 60

    def __init__(self, db_path: Path, timeout_s: int | None = None):
        self._db_path = db_path
        self._timeout_s = timeout_s or self.DEFAULT_TIMEOUT_S
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                jid TEXT PRIMARY KEY,
                session_start TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                message_count INTEGER DEFAULT 0,
                topic TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(is_active, last_activity);
        """)
        self._conn.commit()

    def touch(self, jid: str) -> dict:
        """Record activity for a contact. Returns session info with freshness state.

        Returns dict with:
            - is_new: True if this is a brand new session (no prior record)
            - is_resumed: True if session was expired and now resumed
            - is_continued: True if session is still active (within timeout)
            - gap_seconds: Seconds since last activity (0 if new)
            - message_count: Total messages in current session
            - session_start: ISO timestamp of session start
            - topic: Current session topic
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE jid = ?", (jid,)
        ).fetchone()

        if row is None:
            # Brand new session
            self._conn.execute(
                "INSERT INTO sessions (jid, session_start, last_activity, message_count, is_active) "
                "VALUES (?, ?, ?, 1, 1)",
                (jid, now, now),
            )
            self._conn.commit()
            return {
                "is_new": True, "is_resumed": False, "is_continued": False,
                "gap_seconds": 0, "message_count": 1,
                "session_start": now, "topic": "",
            }

        # Calculate time gap
        last_activity = row["last_activity"]
        try:
            last_dt = datetime.fromisoformat(last_activity)
            now_dt = datetime.fromisoformat(now)
            gap_s = (now_dt - last_dt).total_seconds()
        except (ValueError, TypeError):
            gap_s = 0

        expired = gap_s > self._timeout_s

        if expired:
            # Session expired -> start fresh session, archive old
            self._conn.execute(
                "UPDATE sessions SET session_start = ?, last_activity = ?, "
                "message_count = 1, is_active = 1, topic = '' WHERE jid = ?",
                (now, now, jid),
            )
            self._conn.commit()
            return {
                "is_new": False, "is_resumed": True, "is_continued": False,
                "gap_seconds": int(gap_s), "message_count": 1,
                "session_start": now, "topic": "",
            }
        else:
            # Active session -> continue
            new_count = row["message_count"] + 1
            self._conn.execute(
                "UPDATE sessions SET last_activity = ?, message_count = ? WHERE jid = ?",
                (now, new_count, jid),
            )
            self._conn.commit()
            return {
                "is_new": False, "is_resumed": False, "is_continued": True,
                "gap_seconds": int(gap_s), "message_count": new_count,
                "session_start": row["session_start"], "topic": row["topic"] or "",
            }

    def set_topic(self, jid: str, topic: str) -> None:
        """Set the topic for a contact's current session."""
        self._conn.execute(
            "UPDATE sessions SET topic = ? WHERE jid = ?",
            (topic[:200], jid),
        )
        self._conn.commit()

    def get_session(self, jid: str) -> dict | None:
        """Get current session info for a contact."""
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE jid = ?", (jid,)
        ).fetchone()
        if not row:
            return None
        return {
            "jid": row["jid"],
            "session_start": row["session_start"],
            "last_activity": row["last_activity"],
            "message_count": row["message_count"],
            "topic": row["topic"] or "",
            "is_active": bool(row["is_active"]),
        }

    def get_active_sessions(self, limit: int = 20) -> list[dict]:
        """Get recently active sessions."""
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE is_active = 1 "
            "ORDER BY last_activity DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "jid": r["jid"],
                "session_start": r["session_start"],
                "last_activity": r["last_activity"],
                "message_count": r["message_count"],
                "topic": r["topic"] or "",
            }
            for r in rows
        ]

    def reset_session(self, jid: str) -> bool:
        """Manually reset a session (admin command)."""
        row = self._conn.execute(
            "SELECT jid FROM sessions WHERE jid = ?", (jid,)
        ).fetchone()
        if not row:
            return False
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE sessions SET session_start = ?, last_activity = ?, "
            "message_count = 0, topic = '' WHERE jid = ?",
            (now, now, jid),
        )
        self._conn.commit()
        return True

    def stats(self) -> dict:
        """Get overall session statistics."""
        total = self._conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
        active = self._conn.execute(
            "SELECT COUNT(*) as c FROM sessions WHERE is_active = 1"
        ).fetchone()["c"]
        total_msgs = self._conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) as s FROM sessions"
        ).fetchone()["s"]
        return {
            "total_sessions": total,
            "active_sessions": active,
            "total_messages": total_msgs,
            "timeout_minutes": self._timeout_s // 60,
        }

    def format_session_list(self, sessions: list[dict]) -> str:
        """Format session list for admin display."""
        if not sessions:
            return "No active sessions."
        lines = []
        for s in sessions:
            topic = f" [{s['topic']}]" if s['topic'] else ""
            lines.append(
                f"  {s['jid']}: {s['message_count']} msgs{topic}\n"
                f"    Last: {s['last_activity'][:16]}"
            )
        return "\n".join(lines)

    def build_resume_context(self, session_info: dict) -> str:
        """Build context string when a conversation is resumed after timeout.

        Returns a string to prepend to the system prompt or conversation,
        informing the AI about the conversation gap.
        """
        gap_s = session_info.get("gap_seconds", 0)
        if gap_s < 3600:
            gap_str = f"{gap_s // 60} minutes"
        elif gap_s < 86400:
            gap_str = f"{gap_s // 3600} hours"
        else:
            gap_str = f"{gap_s // 86400} days"

        return (
            f"[Session resumed after {gap_str} of inactivity. "
            f"This is a fresh conversation - greet naturally and don't reference "
            f"previous conversation unless the user does.]"
        )

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
