"""Cron/Scheduling Service: reminders and recurring tasks.

Inspired by nanobot CronService. SQLite-backed scheduler with:
- One-shot reminders (at a specific time)
- Recurring interval tasks (every N seconds)
- Admin commands: /remind, /cron list, /cron del
- Asyncio timer-based execution (no external cron needed)
- Automatic cleanup of expired one-shot tasks

All times stored as UTC epoch seconds for simplicity.
"""

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Coroutine


class CronService:
    """SQLite-backed scheduler with asyncio timer execution.

    Jobs are stored in SQLite and checked periodically (every 30s).
    When a job is due, the on_fire callback is invoked with the job dict.
    """

    CHECK_INTERVAL_S = 30  # Check for due jobs every 30 seconds

    def __init__(self, db_path: Path):
        import sqlite3
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        self._running = False
        self._task: asyncio.Task | None = None
        self._on_fire: Callable[[dict], Coroutine] | None = None

    def _init_db(self) -> None:
        """Create the cron_jobs table."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS cron_jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'once',
                message TEXT NOT NULL,
                target_chat TEXT DEFAULT '',
                next_run_at REAL NOT NULL,
                interval_s REAL DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                last_run_at REAL DEFAULT 0,
                last_status TEXT DEFAULT '',
                created_at REAL NOT NULL,
                delete_after_run INTEGER DEFAULT 1
            );
        """)
        self._conn.commit()

    def set_callback(self, on_fire: Callable[[dict], Coroutine]) -> None:
        """Set the callback for when a job fires."""
        self._on_fire = on_fire

    def add_reminder(self, name: str, message: str, run_at: float,
                     target_chat: str = "") -> dict:
        """Add a one-shot reminder.

        Args:
            name: Display name.
            message: Message to deliver.
            run_at: UTC epoch seconds when to fire.
            target_chat: Optional chat_id to deliver to.

        Returns:
            Job dict.
        """
        job_id = uuid.uuid4().hex[:8]
        now = time.time()
        self._conn.execute(
            """INSERT INTO cron_jobs (id, name, kind, message, target_chat,
               next_run_at, interval_s, enabled, created_at, delete_after_run)
               VALUES (?, ?, 'once', ?, ?, ?, 0, 1, ?, 1)""",
            (job_id, name, message, target_chat, run_at, now),
        )
        self._conn.commit()
        return {"id": job_id, "name": name, "kind": "once", "message": message,
                "next_run_at": run_at, "target_chat": target_chat}

    def add_recurring(self, name: str, message: str, interval_s: float,
                      target_chat: str = "") -> dict:
        """Add a recurring task.

        Args:
            name: Display name.
            message: Message to deliver.
            interval_s: Interval in seconds between runs.
            target_chat: Optional chat_id to deliver to.

        Returns:
            Job dict.
        """
        job_id = uuid.uuid4().hex[:8]
        now = time.time()
        next_run = now + interval_s
        self._conn.execute(
            """INSERT INTO cron_jobs (id, name, kind, message, target_chat,
               next_run_at, interval_s, enabled, created_at, delete_after_run)
               VALUES (?, ?, 'every', ?, ?, ?, ?, 1, ?, 0)""",
            (job_id, name, message, target_chat, next_run, interval_s, now),
        )
        self._conn.commit()
        return {"id": job_id, "name": name, "kind": "every", "message": message,
                "next_run_at": next_run, "interval_s": interval_s, "target_chat": target_chat}

    def list_jobs(self, include_disabled: bool = False) -> list[dict]:
        """List all jobs."""
        if include_disabled:
            rows = self._conn.execute(
                "SELECT * FROM cron_jobs ORDER BY next_run_at ASC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM cron_jobs WHERE enabled = 1 ORDER BY next_run_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID."""
        cursor = self._conn.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def get_due_jobs(self) -> list[dict]:
        """Get all jobs that are due to run now."""
        now = time.time()
        rows = self._conn.execute(
            "SELECT * FROM cron_jobs WHERE enabled = 1 AND next_run_at <= ?",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_executed(self, job_id: str) -> None:
        """Mark a job as executed. Handles one-shot vs recurring differently."""
        now = time.time()
        job = self._conn.execute(
            "SELECT * FROM cron_jobs WHERE id = ?", (job_id,)
        ).fetchone()

        if not job:
            return

        if job["kind"] == "once":
            if job["delete_after_run"]:
                self._conn.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
            else:
                self._conn.execute(
                    "UPDATE cron_jobs SET enabled = 0, last_run_at = ?, last_status = 'ok' WHERE id = ?",
                    (now, job_id),
                )
        else:
            # Recurring: advance next_run_at
            next_run = now + job["interval_s"]
            self._conn.execute(
                "UPDATE cron_jobs SET next_run_at = ?, last_run_at = ?, last_status = 'ok' WHERE id = ?",
                (next_run, now, job_id),
            )
        self._conn.commit()

    def mark_error(self, job_id: str, error: str) -> None:
        """Mark a job execution as failed."""
        now = time.time()
        self._conn.execute(
            "UPDATE cron_jobs SET last_run_at = ?, last_status = ? WHERE id = ?",
            (now, f"error: {error[:100]}", job_id),
        )
        self._conn.commit()

    async def start(self) -> None:
        """Start the cron check loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        jobs = self.list_jobs()
        print(f"[cron] Started (check_interval={self.CHECK_INTERVAL_S}s, active_jobs={len(jobs)})")

    async def stop(self) -> None:
        """Stop the cron check loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        print("[cron] Stopped")

    async def _run_loop(self) -> None:
        """Main loop: check for due jobs every CHECK_INTERVAL_S."""
        try:
            while self._running:
                await self._check_and_fire()
                await asyncio.sleep(self.CHECK_INTERVAL_S)
        except asyncio.CancelledError:
            pass

    async def _check_and_fire(self) -> None:
        """Check for due jobs and fire them."""
        if not self._on_fire:
            return

        due_jobs = self.get_due_jobs()
        for job in due_jobs:
            try:
                await self._on_fire(job)
                self.mark_executed(job["id"])
            except Exception as e:
                self.mark_error(job["id"], str(e))

    def format_job_list(self) -> str:
        """Format job list for WhatsApp display."""
        jobs = self.list_jobs(include_disabled=True)
        if not jobs:
            return "No scheduled jobs."

        lines = [f"*Scheduled Jobs ({len(jobs)})*\n"]
        for j in jobs:
            status = "active" if j["enabled"] else "disabled"
            kind = j["kind"]
            if kind == "every":
                interval = j["interval_s"]
                if interval >= 3600:
                    schedule = f"every {interval / 3600:.1f}h"
                elif interval >= 60:
                    schedule = f"every {interval / 60:.0f}m"
                else:
                    schedule = f"every {interval:.0f}s"
            else:
                from datetime import datetime
                try:
                    dt = datetime.fromtimestamp(j["next_run_at"])
                    schedule = f"at {dt.strftime('%Y-%m-%d %H:%M')}"
                except Exception:
                    schedule = "at (invalid)"

            lines.append(f"  [{j['id']}] {j['name']}")
            lines.append(f"    {schedule} | {status}")
            if j["message"]:
                preview = j["message"][:50]
                if len(j["message"]) > 50:
                    preview += "..."
                lines.append(f"    Msg: {preview}")

        return "\n".join(lines)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
