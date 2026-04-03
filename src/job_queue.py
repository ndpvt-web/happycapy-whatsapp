"""Proactive Job Queue -- async task tracking for deferred agent work.

Aristotelian Foundation:
- Material: SQLite table of pending jobs with context, status, target contact.
- Formal: JobQueue class with create/complete/get_pending/get_for_contact.
- Efficient: Agent creates job when it needs admin input or async processing.
  Cron-like checker fires completed jobs back to the original contact.
- Final: Agent follows up proactively. "I'll get back to you" becomes real.

Job Lifecycle:
  1. CREATED: Agent encounters question it can't answer, creates job.
  2. WAITING_ADMIN: Job needs admin input (linked to escalation code).
  3. PROCESSING: Admin responded or info gathered, agent is composing reply.
  4. READY: Reply composed, waiting for delivery.
  5. DELIVERED: Proactive reply sent to original contact.
  6. CANCELLED: Job cancelled by admin or auto-expired.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class JobQueue:
    """Proactive job queue for deferred agent work.

    SQLite-backed. Designed for async delivery: agent creates a job,
    and a background loop picks up completed jobs and delivers replies.
    """

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path.home() / ".happycapy-whatsapp" / "job_queue.db"
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_jid TEXT NOT NULL,
                contact_name TEXT DEFAULT '',
                job_type TEXT NOT NULL DEFAULT 'general',
                description TEXT NOT NULL,
                context_json TEXT DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'created',
                escalation_code TEXT DEFAULT NULL,
                admin_response TEXT DEFAULT NULL,
                composed_reply TEXT DEFAULT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                delivered_at REAL DEFAULT NULL,
                expires_at REAL DEFAULT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_contact ON jobs(contact_jid);
            CREATE INDEX IF NOT EXISTS idx_jobs_escalation ON jobs(escalation_code);
        """)
        self._conn.commit()

    def create_job(
        self,
        contact_jid: str,
        contact_name: str,
        description: str,
        job_type: str = "general",
        context: dict | None = None,
        escalation_code: str | None = None,
        expires_hours: float = 48,
    ) -> int:
        """Create a new job. Returns the job ID."""
        now = time.time()
        expires_at = now + (expires_hours * 3600) if expires_hours > 0 else None
        cur = self._conn.execute(
            """INSERT INTO jobs
               (contact_jid, contact_name, job_type, description, context_json,
                status, escalation_code, created_at, updated_at, expires_at)
               VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?, ?)""",
            (
                contact_jid,
                contact_name,
                job_type,
                description[:500],
                json.dumps(context or {}),
                escalation_code,
                now,
                now,
                expires_at,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_status(self, job_id: int, status: str, **kwargs: Any) -> bool:
        """Update job status and optional fields."""
        sets = ["status = ?", "updated_at = ?"]
        vals: list[Any] = [status, time.time()]

        for field in ("admin_response", "composed_reply", "escalation_code"):
            if field in kwargs:
                sets.append(f"{field} = ?")
                vals.append(kwargs[field])

        if status == "delivered":
            sets.append("delivered_at = ?")
            vals.append(time.time())

        vals.append(job_id)
        self._conn.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", vals
        )
        self._conn.commit()
        return True

    def get_job(self, job_id: int) -> dict | None:
        """Get a single job by ID."""
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def get_by_escalation(self, code: str) -> dict | None:
        """Find job linked to an escalation code."""
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE escalation_code = ? AND status != 'cancelled'",
            (code,),
        ).fetchone()
        return dict(row) if row else None

    def get_pending(self, limit: int = 20) -> list[dict]:
        """Get jobs that are created or waiting for admin."""
        rows = self._conn.execute(
            """SELECT * FROM jobs
               WHERE status IN ('created', 'waiting_admin', 'processing')
               ORDER BY created_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_ready_to_deliver(self, limit: int = 10) -> list[dict]:
        """Get jobs ready for proactive delivery."""
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE status = 'ready' ORDER BY updated_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_for_contact(self, jid: str, include_delivered: bool = False) -> list[dict]:
        """Get all jobs for a specific contact."""
        if include_delivered:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE contact_jid = ? ORDER BY created_at DESC",
                (jid,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM jobs WHERE contact_jid = ?
                   AND status NOT IN ('delivered', 'cancelled')
                   ORDER BY created_at DESC""",
                (jid,),
            ).fetchall()
        return [dict(r) for r in rows]

    def expire_old_jobs(self, now: float | None = None) -> int:
        """Cancel expired jobs. Returns count of expired jobs."""
        now = now or time.time()
        cur = self._conn.execute(
            """UPDATE jobs SET status = 'cancelled', updated_at = ?
               WHERE expires_at IS NOT NULL AND expires_at < ?
               AND status NOT IN ('delivered', 'cancelled')""",
            (now, now),
        )
        self._conn.commit()
        return cur.rowcount

    def stats(self) -> dict:
        """Get queue statistics."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["cnt"] for r in rows}
        total = sum(by_status.values())
        return {"total": total, "by_status": by_status}

    def format_job_list(self, jobs: list[dict] | None = None) -> str:
        """Format jobs for WhatsApp display."""
        if jobs is None:
            jobs = self.get_pending()
        if not jobs:
            return "No pending jobs."

        lines = [f"*Pending Jobs ({len(jobs)})*\n"]
        for j in jobs:
            age_min = int((time.time() - j["created_at"]) / 60)
            esc = f" [ESC: {j['escalation_code']}]" if j.get("escalation_code") else ""
            lines.append(
                f"#{j['id']} | {j['contact_name'] or j['contact_jid'][:12]} | "
                f"{j['status']} | {j['description'][:40]}{esc} | {age_min}m ago"
            )
        return "\n".join(lines)

    def close(self) -> None:
        self._conn.close()
