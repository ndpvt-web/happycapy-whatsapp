"""Proactive Babloo -- Hyper-personalized study companion engine.

Transforms the bot from passive Q&A to an active learning companion with:
- SM-2 spaced repetition (concept mastery tracking per student)
- Affective model (emotional state detection: frustrated/bored/anxious/confident)
- Feedback loop (message effectiveness tracking + adaptive frequency)
- 13 decision types: REMINDER, CHECKIN, NUDGE, COUNTDOWN, ACHIEVEMENT,
  REVIEW, CHALLENGE, RECOVERY, CURIOSITY, SCAFFOLDING, CELEBRATION_SPECIFIC,
  AUTONOMY_CHECK, DELOAD
- SDT-aware messaging (Autonomy, Competence, Relatedness)

Database: proactive.db (SQLite WAL, per-instance isolation)
Heartbeat: schedule_check() runs every 30 minutes via HeartbeatService.
All times in PKT (UTC+5).
"""

import asyncio
import json
import math
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import httpx
except ImportError:
    httpx = None


# ── Constants ──

PKT = timezone(timedelta(hours=5))
ACHIEVEMENT_MILESTONES = {3, 5, 7, 14, 21, 30}

TYPE_INSTRUCTIONS = {
    "reminder": "Remind them it's study time. Reference their focus subject if known. Be energizing, not nagging.",
    "checkin": "Ask how their study session went. Encourage them to log what they covered.",
    "nudge": "They haven't been active. Gently encourage them to get back on track. No guilt-tripping.",
    "countdown": "Share how many days until their exam. Create urgency without panic.",
    "achievement": "Celebrate their study streak! Be genuinely excited and proud.",
    "review": "A concept is due for spaced repetition review. Ask them to recall it briefly. Make it feel like a fun quiz, not a test.",
    "challenge": "They've mastered the current topic. Introduce the next difficulty level with excitement. Make them feel ready.",
    "recovery": "They seem frustrated or down. Show empathy, offer to help, suggest a break. NO academic pressure.",
    "curiosity": "Connect their personal interests to the curriculum. Make learning feel fun and relevant.",
    "scaffolding": "They've struggled with this concept multiple times. Break it into smaller steps. Offer a different explanation angle.",
    "celebration_specific": "Celebrate a SPECIFIC academic milestone (accuracy improvement, new topic mastered). Be precise, not generic.",
    "autonomy_check": "Offer them a CHOICE of what to study next. Respect their preferences. Use invitational language.",
    "deload": "They've been studying hard (high streak, long hours). Suggest rest. Normalize breaks. No pressure.",
}

# ── Affective Model: Sentiment Signals ──

FRUSTRATION_SIGNALS = [
    "i don't get it", "too hard", "i give up", "confused", "can't understand",
    "samajh nahi aa raha", "nahi hota", "mushkil hai", "samajh ni ata",
    "ye nahi hoga", "haar gaya", "boht mushkil", "kuch samajh nahi",
    "pata nahi kaise", "stuck", "impossible", "ugh", "kya karoon",
]
BOREDOM_SIGNALS = [
    "easy", "boring", "next", "kuch aur", "ye toh aasan hai",
    "already know", "done this before", "too simple", "yawn",
    "bore ho raha", "kuch naya batao", "ye pata hai",
]
ANXIETY_SIGNALS = [
    "what if i fail", "scared", "nervous", "dar lag raha", "tension",
    "exam tension", "pass ho jaunga?", "fail ho gaya toh", "worried",
    "will i pass", "kya hoga", "bahut dar", "panic", "stress",
]
CONFIDENCE_SIGNALS = [
    "let me try", "i understand", "got it", "samajh aa gaya",
    "aur do", "easy hai ye", "mujhe aata hai", "i can do this",
    "next topic", "aage chalo", "bring it on", "ye toh easy tha",
]

# Quiet hours (local time for student)
QUIET_NIGHT_START = 23
QUIET_NIGHT_END = 7
QUIET_SCHOOL_START = 7
QUIET_SCHOOL_END = 14
QUIET_JUMMAH_START = 13
QUIET_JUMMAH_END = 14


# ── Database Schema ──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS student_plans (
    jid TEXT PRIMARY KEY,
    display_name TEXT DEFAULT '',
    board TEXT DEFAULT '',
    class TEXT DEFAULT '',
    exam_date TEXT DEFAULT '',
    study_time TEXT DEFAULT '20:00',
    study_days TEXT DEFAULT '1,2,3,4,5,6',
    focus_subjects TEXT DEFAULT '[]',
    weekly_plan_json TEXT DEFAULT '{}',
    daily_target_hours REAL DEFAULT 2.0,
    reminders_enabled INTEGER DEFAULT 1,
    checkins_enabled INTEGER DEFAULT 1,
    nudges_enabled INTEGER DEFAULT 1,
    countdown_enabled INTEGER DEFAULT 1,
    max_daily_messages INTEGER DEFAULT 3,
    nudge_after_days INTEGER DEFAULT 2,
    timezone TEXT DEFAULT 'Asia/Karachi',  -- IANA timezone (e.g., Asia/Karachi, Asia/Dubai)
    current_streak INTEGER DEFAULT 0,
    longest_streak INTEGER DEFAULT 0,
    last_activity_date TEXT DEFAULT '',
    last_streak_date TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS study_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jid TEXT NOT NULL,
    subject TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    duration_minutes INTEGER DEFAULT 0,
    source TEXT DEFAULT 'self_reported',
    confidence REAL DEFAULT 1.0,
    notes TEXT DEFAULT '',
    logged_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_progress_jid_date ON study_progress(jid, logged_at);

CREATE TABLE IF NOT EXISTS proactive_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jid TEXT NOT NULL,
    message_type TEXT NOT NULL,
    message_text TEXT NOT NULL,
    sent_date TEXT NOT NULL,
    jitter_seconds INTEGER DEFAULT 0,
    status TEXT DEFAULT 'sent',
    error TEXT DEFAULT '',
    sent_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_log_jid_date ON proactive_log(jid, sent_date);

CREATE TABLE IF NOT EXISTS exam_calendar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board TEXT NOT NULL,
    class TEXT NOT NULL,
    exam_start TEXT NOT NULL,
    exam_end TEXT NOT NULL,
    year INTEGER NOT NULL,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exam_timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board TEXT NOT NULL,
    class TEXT NOT NULL,
    subject TEXT NOT NULL,
    exam_date TEXT NOT NULL,
    year INTEGER NOT NULL,
    is_practical INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_exam_timetable_board_class ON exam_timetable(board, class);
CREATE INDEX IF NOT EXISTS idx_exam_timetable_date ON exam_timetable(exam_date);

-- GAP 1: Cognitive Model -- per-concept spaced repetition (SM-2)
CREATE TABLE IF NOT EXISTS concept_mastery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jid TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    mastery_level REAL DEFAULT 0.0,
    ease_factor REAL DEFAULT 2.5,
    interval_days REAL DEFAULT 1.0,
    repetition_count INTEGER DEFAULT 0,
    next_review_date TEXT,
    last_reviewed TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(jid, concept_id)
);
CREATE INDEX IF NOT EXISTS idx_mastery_jid ON concept_mastery(jid);
CREATE INDEX IF NOT EXISTS idx_mastery_review ON concept_mastery(next_review_date);

-- GAP 3: Feedback Loop -- track whether proactive messages actually work
CREATE TABLE IF NOT EXISTS message_effectiveness (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proactive_log_id INTEGER NOT NULL,
    jid TEXT NOT NULL,
    message_type TEXT NOT NULL,
    response_received INTEGER DEFAULT 0,
    response_time_minutes INTEGER DEFAULT -1,
    led_to_study_session INTEGER DEFAULT 0,
    sentiment_of_response TEXT DEFAULT '',
    evaluated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_effectiveness_jid ON message_effectiveness(jid);
"""


# ── ProactiveEngine ──

class ProactiveEngine:
    """Core engine for proactive student engagement.

    Manages study plans, progress tracking, and scheduled proactive messages.
    Designed for contract-first integration with the WhatsApp bot.
    """

    def __init__(self, base_dir, channel, memory_store, config):
        self.base_dir = Path(base_dir)
        self.channel = channel
        self.memory_store = memory_store
        self.config = config or {}
        db_path = self.base_dir / "proactive.db"
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        # WAL mode: allows concurrent reads while writing (critical for 300+ students)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA_SQL)
        # Migrate: add columns if missing
        migrations = [
            ("timezone", "ALTER TABLE student_plans ADD COLUMN timezone TEXT DEFAULT 'Asia/Karachi'"),
            ("recent_affect", "ALTER TABLE student_plans ADD COLUMN recent_affect TEXT DEFAULT 'neutral'"),
            ("engagement_score", "ALTER TABLE student_plans ADD COLUMN engagement_score INTEGER DEFAULT 50"),
            ("preferred_send_hour", "ALTER TABLE student_plans ADD COLUMN preferred_send_hour INTEGER DEFAULT -1"),
            ("last_response_to_proactive", "ALTER TABLE student_plans ADD COLUMN last_response_to_proactive TEXT DEFAULT ''"),
        ]
        for col, sql in migrations:
            try:
                self._conn.execute(f"SELECT {col} FROM student_plans LIMIT 1")
            except sqlite3.OperationalError:
                self._conn.execute(sql)
                print(f"[proactive] Migrated: added {col} column")
        self._conn.commit()
        self.seed_exam_calendar()
        self._seed_exam_data()
        print(f"[proactive] Database initialized at {db_path}")

    # ── Plan CRUD ──

    def create_plan(self, jid, display_name="", board="", class_="",
                    exam_date="", study_time="20:00", focus_subjects="[]",
                    timezone="Asia/Karachi"):
        """Create or replace a student plan."""
        subjects_json = json.dumps(focus_subjects) if isinstance(focus_subjects, list) else focus_subjects
        self._conn.execute(
            """INSERT INTO student_plans (jid, display_name, board, class, exam_date,
               study_time, focus_subjects, timezone) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(jid) DO UPDATE SET
               display_name=excluded.display_name, board=excluded.board,
               class=excluded.class, exam_date=excluded.exam_date,
               study_time=excluded.study_time, focus_subjects=excluded.focus_subjects,
               timezone=excluded.timezone, updated_at=datetime('now')""",
            (jid, display_name, board, class_, exam_date, study_time, subjects_json, timezone),
        )
        self._conn.commit()
        return self.get_plan(jid)

    def update_plan(self, jid, **kwargs):
        """Update specific fields of a student plan."""
        if not kwargs:
            return self.get_plan(jid)
        updates = {k: v for k, v in kwargs.items() if v is not None}
        if not updates:
            return self.get_plan(jid)
        # JSON-encode list/dict fields
        if "focus_subjects" in updates and isinstance(updates["focus_subjects"], list):
            updates["focus_subjects"] = json.dumps(updates["focus_subjects"])
        if "weekly_plan_json" in updates and isinstance(updates["weekly_plan_json"], dict):
            updates["weekly_plan_json"] = json.dumps(updates["weekly_plan_json"])
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [jid]
        self._conn.execute(
            f"UPDATE student_plans SET {set_clause}, updated_at=datetime('now') WHERE jid=?",
            values,
        )
        self._conn.commit()
        return self.get_plan(jid)

    def get_plan(self, jid):
        """Get a student's plan."""
        row = self._conn.execute(
            "SELECT * FROM student_plans WHERE jid=?", (jid,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        # Parse JSON fields
        for field in ("focus_subjects", "weekly_plan_json"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    def delete_plan(self, jid):
        """Delete a student's plan."""
        cursor = self._conn.execute("DELETE FROM student_plans WHERE jid=?", (jid,))
        self._conn.commit()
        return cursor.rowcount > 0

    def get_all_active_plans(self):
        """Get all enabled student plans."""
        rows = self._conn.execute(
            "SELECT * FROM student_plans WHERE enabled=1"
        ).fetchall()
        plans = []
        for row in rows:
            d = dict(row)
            for field in ("focus_subjects", "weekly_plan_json"):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            plans.append(d)
        return plans

    def format_plan_for_prompt(self, jid):
        """Format a student's plan status for injection into the AI system prompt.

        Returns a concise context block telling the AI:
        - What info is already known
        - What info is MISSING (so the AI knows what to ask)
        - Current streak and activity status
        - Proactive feature toggles
        """
        plan = self.get_plan(jid)
        if not plan:
            return (
                "\n--- Study Plan Status ---\n"
                "This student has NO study plan yet. They are a new/unregistered student.\n"
                "MISSING INFO (ask naturally during conversation):\n"
                "  - Board (FBISE only - this is the ONLY board supported)\n"
                "  - Class (Grade 9 or Grade 10 ONLY - also called SSC-I and SSC-II. These are the ONLY 2 classes supported)\n"
                "  - Exam date (approximate)\n"
                "  - Focus subjects\n"
                "  - Preferred study time\n"
                "Use update_study_plan and set_study_reminder tools to save info as you learn it.\n"
                "Introduce your proactive features (reminders, streaks, exam countdown).\n"
                "--- End Study Plan Status ---"
            )

        known = []
        missing = []

        if plan.get("board"):
            known.append("Board: " + plan["board"])
        else:
            missing.append("Board (FBISE only)")

        if plan.get("class"):
            known.append("Class: " + plan["class"])
        else:
            missing.append("Class (9th, 10th, 11th, 12th)")

        if plan.get("exam_date"):
            known.append("Exam date: " + plan["exam_date"])
            try:
                exam_dt = datetime.strptime(plan["exam_date"], "%Y-%m-%d")
                days_left = (exam_dt - datetime.now()).days
                if days_left > 0:
                    known.append("Days until exam: " + str(days_left))
            except (ValueError, TypeError):
                pass
        else:
            missing.append("Exam date (approximate)")

        subjects = plan.get("focus_subjects")
        if subjects and subjects != "[]":
            if isinstance(subjects, list):
                known.append("Focus subjects: " + ", ".join(subjects))
            else:
                known.append("Focus subjects: " + str(subjects))
        else:
            missing.append("Focus subjects")

        if plan.get("study_time") and plan["study_time"] != "20:00":
            known.append("Study time: " + plan["study_time"])
        else:
            missing.append("Preferred study time (currently default 8pm)")

        tz = plan.get("timezone", "Asia/Karachi")
        if tz != "Asia/Karachi":
            known.append("Timezone: " + tz)

        streak = plan.get("current_streak", 0)
        longest = plan.get("longest_streak", 0)
        if streak or longest:
            known.append(f"Study streak: {streak} days (longest: {longest})")

        last_active = plan.get("last_activity_date")
        if last_active:
            known.append("Last active: " + last_active)

        parts = ["\n--- Study Plan Status ---"]
        if known:
            parts.append("Known: " + ", ".join(known))
        if missing:
            parts.append("MISSING (ask ONE naturally per message): " + ", ".join(missing))
        else:
            parts.append("Profile is COMPLETE. No onboarding questions needed.")

        features_on = []
        features_off = []
        for feat, key in [
            ("reminders", "reminders_enabled"),
            ("checkins", "checkins_enabled"),
            ("nudges", "nudges_enabled"),
            ("countdown", "countdown_enabled"),
        ]:
            if plan.get(key, 1):
                features_on.append(feat)
            else:
                features_off.append(feat)
        if features_on:
            parts.append("Active features: " + ", ".join(features_on))
        if features_off:
            parts.append("Disabled features: " + ", ".join(features_off))

        # Add system-level context about supported boards/classes
        parts.append("SYSTEM CONTEXT: Only FBISE board is supported. Only Grade 9 (SSC-I) and Grade 10 (SSC-II) are supported.")
        parts.append("Use update_study_plan/set_study_reminder/log_study_progress tools to save info.")
        parts.append("--- End Study Plan Status ---")
        return "\n".join(parts)

    # ── Progress tracking ──

    def log_progress(self, jid, subject="", topic="", duration_minutes=0,
                     source="self_reported", confidence=1.0):
        """Log a study progress entry and update streak."""
        self._conn.execute(
            """INSERT INTO study_progress (jid, subject, topic, duration_minutes, source, confidence)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (jid, subject, topic, duration_minutes, source, confidence),
        )
        self._conn.commit()
        streak_info = self.update_streak(jid)
        return streak_info

    def get_progress(self, jid, days=7):
        """Get recent progress entries for a student."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self._conn.execute(
            """SELECT * FROM study_progress WHERE jid=? AND logged_at >= ?
               ORDER BY logged_at DESC""",
            (jid, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_streak(self, jid):
        """Update streak counters after activity. Returns streak info."""
        today = datetime.now().strftime("%Y-%m-%d")
        plan = self._conn.execute(
            "SELECT current_streak, longest_streak, last_streak_date FROM student_plans WHERE jid=?",
            (jid,),
        ).fetchone()
        if not plan:
            return {"current_streak": 0, "longest_streak": 0}
        last_date = plan["last_streak_date"]
        current = plan["current_streak"] or 0
        longest = plan["longest_streak"] or 0

        if last_date == today:
            return {"current_streak": current, "longest_streak": longest}

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        if last_date == yesterday:
            current += 1
        else:
            current = 1

        if current > longest:
            longest = current

        self._conn.execute(
            """UPDATE student_plans SET current_streak=?, longest_streak=?,
               last_streak_date=?, last_activity_date=?, updated_at=datetime('now')
               WHERE jid=?""",
            (current, longest, today, today, jid),
        )
        self._conn.commit()
        return {"current_streak": current, "longest_streak": longest}

    # ── GAP 1: Cognitive Model (SM-2 Spaced Repetition) ──

    def upsert_concept(self, jid, concept_id, subject, topic):
        """Create or get a concept mastery record."""
        self._conn.execute(
            """INSERT INTO concept_mastery (jid, concept_id, subject, topic,
               next_review_date, last_reviewed)
               VALUES (?, ?, ?, ?, date('now', '+1 day'), date('now'))
               ON CONFLICT(jid, concept_id) DO NOTHING""",
            (jid, concept_id, subject, topic),
        )
        self._conn.commit()

    def update_mastery(self, jid, concept_id, quality):
        """SM-2 algorithm: update mastery after a recall attempt.

        quality: 0-5 (0=blackout, 3=correct with difficulty, 5=perfect recall)
        """
        row = self._conn.execute(
            "SELECT * FROM concept_mastery WHERE jid=? AND concept_id=?",
            (jid, concept_id),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        ef = d["ease_factor"]
        interval = d["interval_days"]
        reps = d["repetition_count"]
        mastery = d["mastery_level"]

        # SM-2 ease factor update
        ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        ef = max(1.3, ef)

        if quality < 3:  # Failed recall
            interval = 1.0
            reps = 0
            mastery = max(0.0, mastery - 0.1)
        else:  # Successful recall
            if reps == 0:
                interval = 1.0
            elif reps == 1:
                interval = 6.0
            else:
                interval = interval * ef
            reps += 1
            mastery = min(1.0, mastery + 0.05 * quality)

        today = datetime.now().strftime("%Y-%m-%d")
        next_review = (datetime.now() + timedelta(days=interval)).strftime("%Y-%m-%d")
        self._conn.execute(
            """UPDATE concept_mastery SET ease_factor=?, interval_days=?,
               repetition_count=?, mastery_level=?, next_review_date=?,
               last_reviewed=?, updated_at=datetime('now')
               WHERE jid=? AND concept_id=?""",
            (ef, interval, reps, mastery, next_review, today, jid, concept_id),
        )
        self._conn.commit()
        return {"mastery": mastery, "ease_factor": ef, "interval": interval,
                "next_review": next_review, "reps": reps}

    def get_concepts_due(self, jid, limit=5):
        """Get concepts due for review (next_review_date <= today)."""
        today = datetime.now().strftime("%Y-%m-%d")
        rows = self._conn.execute(
            """SELECT * FROM concept_mastery WHERE jid=? AND next_review_date<=?
               ORDER BY mastery_level ASC, next_review_date ASC LIMIT ?""",
            (jid, today, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_mastery_summary(self, jid):
        """Get mastery summary for a student (for compose context)."""
        rows = self._conn.execute(
            """SELECT subject, topic, mastery_level, next_review_date
               FROM concept_mastery WHERE jid=?
               ORDER BY mastery_level ASC LIMIT 10""",
            (jid,),
        ).fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            d = dict(r)
            pct = int(d["mastery_level"] * 100)
            lines.append(f"  {d['subject']}/{d['topic']}: {pct}% mastery (review: {d['next_review_date']})")
        return "Concept mastery:\n" + "\n".join(lines)

    def get_highest_mastery_subject(self, jid):
        """Get the subject where student has highest average mastery."""
        row = self._conn.execute(
            """SELECT subject, AVG(mastery_level) as avg_m FROM concept_mastery
               WHERE jid=? GROUP BY subject ORDER BY avg_m DESC LIMIT 1""",
            (jid,),
        ).fetchone()
        return dict(row) if row else None

    def get_concept_count(self, jid):
        """Count total concepts tracked for student."""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM concept_mastery WHERE jid=?", (jid,),
        ).fetchone()
        return row["cnt"] if row else 0

    def get_next_exam(self, class_name: str) -> Optional[dict]:
        """Get the next upcoming exam for the student's class.

        Args:
            class_name: Student's class (e.g., "Grade 9", "Grade 10")

        Returns:
            dict with keys: subject, date, days_left (or None if no upcoming exams)
        """
        today = datetime.now().strftime("%Y-%m-%d")
        row = self._conn.execute(
            """SELECT subject, exam_date
               FROM exam_timetable
               WHERE board='FBISE' AND class=? AND exam_date >= ?
               ORDER BY exam_date ASC
               LIMIT 1""",
            (class_name, today),
        ).fetchone()

        if not row:
            return None

        exam_date = row["exam_date"]
        try:
            exam_dt = datetime.strptime(exam_date, "%Y-%m-%d")
            days_left = (exam_dt - datetime.now()).days
            return {
                "subject": row["subject"],
                "date": exam_date,
                "days_left": days_left,
            }
        except (ValueError, TypeError):
            return None

    # ── GAP 2: Affective Model ──

    def detect_affect(self, text):
        """Detect emotional state from student message text.

        Returns: 'frustrated', 'bored', 'anxious', 'confident', or 'neutral'
        """
        if not text:
            return "neutral"
        text_lower = text.lower()
        # Check signals in priority order (frustration > anxiety > boredom > confidence)
        for signal in FRUSTRATION_SIGNALS:
            if signal in text_lower:
                return "frustrated"
        for signal in ANXIETY_SIGNALS:
            if signal in text_lower:
                return "anxious"
        for signal in BOREDOM_SIGNALS:
            if signal in text_lower:
                return "bored"
        for signal in CONFIDENCE_SIGNALS:
            if signal in text_lower:
                return "confident"
        return "neutral"

    def update_affect(self, jid, text):
        """Update student's emotional state based on incoming message.

        Called from main.py during normal chat handling (not proactive).
        Only updates if a non-neutral affect is detected.
        """
        affect = self.detect_affect(text)
        if affect != "neutral":
            self._conn.execute(
                "UPDATE student_plans SET recent_affect=?, updated_at=datetime('now') WHERE jid=?",
                (affect, jid),
            )
            self._conn.commit()
        return affect

    def get_affect(self, jid):
        """Get student's current emotional state."""
        row = self._conn.execute(
            "SELECT recent_affect FROM student_plans WHERE jid=?", (jid,),
        ).fetchone()
        return row["recent_affect"] if row else "neutral"

    # ── GAP 3: Feedback Loop ──

    def record_proactive_response(self, jid, incoming_text):
        """Record that a student responded after a proactive message.

        Called from main.py when a message arrives from a student.
        Finds the most recent proactive message sent to this student
        in the last 4 hours that hasn't been evaluated yet.
        """
        now = datetime.now()
        cutoff = (now - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")
        # Find most recent unevaluated proactive message to this student
        row = self._conn.execute(
            """SELECT l.id, l.message_type, l.sent_at FROM proactive_log l
               LEFT JOIN message_effectiveness e ON l.id = e.proactive_log_id
               WHERE l.jid=? AND l.status='sent' AND l.sent_at>=? AND e.id IS NULL
               ORDER BY l.sent_at DESC LIMIT 1""",
            (jid, cutoff),
        ).fetchone()
        if not row:
            return None

        log_id = row["id"]
        sent_at_str = row["sent_at"]
        msg_type = row["message_type"]
        # Calculate response time
        try:
            sent_at = datetime.strptime(sent_at_str, "%Y-%m-%d %H:%M:%S")
            response_minutes = int((now - sent_at).total_seconds() / 60)
        except (ValueError, TypeError):
            response_minutes = -1

        sentiment = self.detect_affect(incoming_text)
        sentiment_label = "positive" if sentiment in ("confident",) else (
            "negative" if sentiment in ("frustrated", "anxious") else "neutral"
        )

        self._conn.execute(
            """INSERT INTO message_effectiveness
               (proactive_log_id, jid, message_type, response_received,
                response_time_minutes, sentiment_of_response)
               VALUES (?, ?, ?, 1, ?, ?)""",
            (log_id, jid, msg_type, response_minutes, sentiment_label),
        )
        # Update last_response_to_proactive timestamp
        self._conn.execute(
            "UPDATE student_plans SET last_response_to_proactive=datetime('now') WHERE jid=?",
            (jid,),
        )
        self._conn.commit()
        return {"log_id": log_id, "response_minutes": response_minutes, "sentiment": sentiment_label}

    def check_study_after_proactive(self, jid, proactive_log_id):
        """Check if student logged study progress after a proactive message."""
        row = self._conn.execute(
            "SELECT sent_at FROM proactive_log WHERE id=?", (proactive_log_id,),
        ).fetchone()
        if not row:
            return False
        sent_at = row["sent_at"]
        # Check if study_progress was logged within 4 hours
        progress = self._conn.execute(
            """SELECT COUNT(*) as cnt FROM study_progress
               WHERE jid=? AND logged_at>=? AND logged_at<=datetime(?, '+4 hours')""",
            (jid, sent_at, sent_at),
        ).fetchone()
        if progress and progress["cnt"] > 0:
            self._conn.execute(
                "UPDATE message_effectiveness SET led_to_study_session=1 WHERE proactive_log_id=?",
                (proactive_log_id,),
            )
            self._conn.commit()
            return True
        return False

    def update_engagement_scores(self):
        """Update engagement scores for all active students.

        engagement_score = (responses_in_7_days / proactive_msgs_in_7_days) * 100
        Capped at 0-100.
        """
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        students = self._conn.execute(
            "SELECT jid FROM student_plans WHERE enabled=1"
        ).fetchall()
        for s in students:
            jid = s["jid"]
            sent = self._conn.execute(
                """SELECT COUNT(*) as cnt FROM proactive_log
                   WHERE jid=? AND sent_date>=? AND status='sent'""",
                (jid, cutoff),
            ).fetchone()["cnt"]
            if sent == 0:
                continue
            responded = self._conn.execute(
                """SELECT COUNT(*) as cnt FROM message_effectiveness
                   WHERE jid=? AND evaluated_at>=? AND response_received=1""",
                (jid, cutoff),
            ).fetchone()["cnt"]
            score = min(100, int((responded / max(1, sent)) * 100))
            self._conn.execute(
                "UPDATE student_plans SET engagement_score=? WHERE jid=?",
                (score, jid),
            )
        self._conn.commit()

    def learn_preferred_send_hour(self, jid):
        """Learn student's preferred send hour from response patterns."""
        rows = self._conn.execute(
            """SELECT l.sent_at FROM proactive_log l
               INNER JOIN message_effectiveness e ON l.id = e.proactive_log_id
               WHERE l.jid=? AND e.response_received=1
               ORDER BY l.sent_at DESC LIMIT 20""",
            (jid,),
        ).fetchall()
        if len(rows) < 3:
            return -1
        hours = []
        for r in rows:
            try:
                dt = datetime.strptime(r["sent_at"], "%Y-%m-%d %H:%M:%S")
                hours.append(dt.hour)
            except (ValueError, TypeError):
                pass
        if not hours:
            return -1
        # Mode of response hours
        from collections import Counter
        most_common = Counter(hours).most_common(1)[0][0]
        self._conn.execute(
            "UPDATE student_plans SET preferred_send_hour=? WHERE jid=?",
            (most_common, jid),
        )
        self._conn.commit()
        return most_common

    def get_effectiveness_summary(self, jid, days=7):
        """Get message effectiveness summary for compose context."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self._conn.execute(
            """SELECT message_type, response_received, sentiment_of_response
               FROM message_effectiveness WHERE jid=? AND evaluated_at>=?""",
            (jid, cutoff),
        ).fetchall()
        if not rows:
            return ""
        total = len(rows)
        responded = sum(1 for r in rows if r["response_received"])
        positive = sum(1 for r in rows if r["sentiment_of_response"] == "positive")
        negative = sum(1 for r in rows if r["sentiment_of_response"] == "negative")
        return (f"Message effectiveness (7d): {responded}/{total} responded, "
                f"{positive} positive, {negative} negative")

    # ── Scale constants (Aristotelian golden mean: fast enough to serve, safe enough to survive) ──
    # Ban risk is PATTERN + CONTENT, not raw rate. Our messages are:
    #   - Uniquely AI-composed per student (high entropy, not template spam)
    #   - Sent to existing contacts who initiated chat (not cold outreach)
    #   - Expected by opted-in students (low complaint rate)
    # WhatsApp Business API: 80 msg/sec. Baileys safe zone: 15-20 msg/min to known contacts.
    MAX_CONCURRENT_AI = 5       # AI composition is separate from send rate
    SEND_INTERVAL_SEC = 3.0     # Base interval between sends
    SEND_VARIANCE_MIN = 1.0     # Min random add-on (total: 4-6s, avg 5s = ~12 msg/min)
    SEND_VARIANCE_MAX = 3.0     # Max random add-on (wide variance = human-like)
    BURST_PAUSE_EVERY = 20      # After every N sends, brief pause (human checks phone)
    BURST_PAUSE_SEC = 12        # Short pause (12-20s with jitter) -- natural, not paranoid
    MAX_SENDS_PER_TICK = 150    # Can serve all 300 students if needed
    BATCH_SIZE = 20             # Students per AI batch
    MAX_JITTER_SEC = 30         # Metadata-only jitter logged to DB (not slept)

    # ── Heartbeat: schedule_check ──

    async def schedule_check(self):
        """Main heartbeat entry point. Runs every 30 min.

        Scaled for 200-300 students (Aristotelian golden mean):
        - Phase 1: Synchronous decision pass (fast, ~0.06ms/student)
        - Phase 2: Batched concurrent AI composition (5 concurrent, 20/batch)
        - Phase 3: Human-like send (~12 msgs/min avg, 150 max/tick, brief pauses every 20)

        13-type priority decision tree per student:
        P0. RECOVERY: frustrated/anxious + inactive
        P1. REMINDER: study_time window + correct day
        P2. REVIEW: spaced repetition concepts due
        P3. CHECKIN: 2+ hours after study_time
        P4. DELOAD: high streak (14+) + recent high engagement
        P5. CHALLENGE: mastery > 0.8 in focus subject
        P6. SCAFFOLDING: mastery < 0.3 with 3+ repetitions
        P7. NUDGE: inactive >= nudge_after_days
        P8. CELEBRATION_SPECIFIC: concept count milestone
        P9. COUNTDOWN: Sunday + exam 1-90 days away
        P10. ACHIEVEMENT: streak milestone reached
        P11. AUTONOMY_CHECK: every 7 days
        P12. CURIOSITY: fallback (engagement > 30)

        Returns stats dict.
        """
        forced = self.check_force_flag()
        if forced:
            print("[proactive] Force check triggered via flag file")

        stats = {"checked": 0, "skipped": 0, "errors": 0, "ai_ok": 0, "ai_fallback": 0}
        plans = self.get_all_active_plans()
        print(f"[proactive] Checking {len(plans)} active students...")

        # ── Phase 1: Decision pass (synchronous, fast) ──
        pending = []  # list of (jid, message_type, plan)
        for plan in plans:
            jid = plan["jid"]
            try:
                tz_name = plan.get("timezone", "Asia/Karachi")
                try:
                    from zoneinfo import ZoneInfo
                    stu_tz = ZoneInfo(tz_name)
                except Exception:
                    stu_tz = PKT

                now_stu = datetime.now(stu_tz)
                today_str = now_stu.strftime("%Y-%m-%d")
                today_weekday = now_stu.weekday()

                if not self.can_send(jid, plan, stu_tz):
                    stats["skipped"] += 1
                    continue

                message_type = self._decide_message_type(plan, now_stu, today_str, today_weekday)
                if not message_type:
                    stats["skipped"] += 1
                    continue

                pending.append((jid, message_type, plan, today_str))
            except Exception as e:
                print(f"[proactive] Decision error for {jid}: {e}")
                stats["errors"] += 1

        print(f"[proactive] {len(pending)} students need messages, {stats['skipped']} skipped")
        if not pending:
            print(f"[proactive] Check complete: {stats}")
            return stats

        # ── Phase 2: Batched AI composition ──
        sem = asyncio.Semaphore(self.MAX_CONCURRENT_AI)
        composed = []  # list of (jid, message_type, text, today_str)

        async def compose_one(jid, message_type, plan, today_str):
            async with sem:
                try:
                    text = await self.compose_message(jid, message_type, plan)
                    if text:
                        stats["ai_ok"] += 1
                    else:
                        text = self._static_message(message_type, plan)
                        stats["ai_fallback"] += 1
                except Exception:
                    text = self._static_message(message_type, plan)
                    stats["ai_fallback"] += 1
                return (jid, message_type, text, today_str)

        # Process in batches to avoid overwhelming the event loop
        for i in range(0, len(pending), self.BATCH_SIZE):
            batch = pending[i:i + self.BATCH_SIZE]
            tasks = [compose_one(jid, mt, plan, ts) for jid, mt, plan, ts in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    stats["errors"] += 1
                else:
                    composed.append(r)

        print(f"[proactive] Composed {len(composed)} messages (AI: {stats['ai_ok']}, fallback: {stats['ai_fallback']})")

        # ── Phase 3: Rate-limited send with anti-ban protections ──
        # Shuffle for fairness + randomness (looks less bot-like)
        random.shuffle(composed)

        # Hard cap: never send more than MAX_SENDS_PER_TICK in one heartbeat
        if len(composed) > self.MAX_SENDS_PER_TICK:
            overflow = len(composed) - self.MAX_SENDS_PER_TICK
            composed = composed[:self.MAX_SENDS_PER_TICK]
            print(f"[proactive] WARN: Capped at {self.MAX_SENDS_PER_TICK} sends, deferred {overflow}")

        send_count = 0
        for jid, message_type, text, today_str in composed:
            try:
                # Random jitter before each send (breaks timing pattern)
                jitter = random.randint(0, self.MAX_JITTER_SEC)
                await self._send_proactive(jid, message_type, text, today_str, jitter)
                stats["checked"] += 1
                send_count += 1

                # Human-like interval: base + wide random variance
                variance = random.uniform(self.SEND_VARIANCE_MIN, self.SEND_VARIANCE_MAX)
                await asyncio.sleep(self.SEND_INTERVAL_SEC + variance)

                # Burst pause: brief natural pause like checking phone
                if send_count % self.BURST_PAUSE_EVERY == 0:
                    pause = self.BURST_PAUSE_SEC + random.randint(0, 8)
                    print(f"[proactive] Brief pause ({pause}s) after {send_count} sends")
                    await asyncio.sleep(pause)

            except Exception as e:
                print(f"[proactive] Send error for {jid}: {e}")
                stats["errors"] += 1

        print(f"[proactive] Check complete: {stats}")
        return stats

    def _decide_message_type(self, plan, now_pkt, today_str, today_weekday):
        """Decision tree: 13 message types, priority-ordered.

        Original 5: REMINDER, CHECKIN, NUDGE, COUNTDOWN, ACHIEVEMENT
        New 8: RECOVERY, REVIEW, DELOAD, CHALLENGE, SCAFFOLDING,
               CELEBRATION_SPECIFIC, AUTONOMY_CHECK, CURIOSITY
        """
        jid = plan["jid"]
        affect = plan.get("recent_affect", "neutral")
        engagement = plan.get("engagement_score", 50)
        streak = plan.get("current_streak", 0)

        # Parse study days and time
        study_days_csv = plan.get("study_days", "1,2,3,4,5,6")
        study_days = {int(d.strip()) for d in study_days_csv.split(",") if d.strip()}
        study_time_str = plan.get("study_time", "20:00")
        try:
            sh, sm = map(int, study_time_str.split(":"))
        except (ValueError, AttributeError):
            sh, sm = 20, 0
        study_time = now_pkt.replace(hour=sh, minute=sm, second=0, microsecond=0)

        # ── P0: RECOVERY (frustrated/anxious + inactive) ──
        if affect in ("frustrated", "anxious"):
            last_active = plan.get("last_activity_date") or ""
            if last_active:
                try:
                    days_off = (datetime.now() - datetime.strptime(last_active[:10], "%Y-%m-%d")).days
                    if days_off >= 1 and not self._sent_recently(jid, "recovery", 3):
                        return "recovery"
                except (ValueError, TypeError):
                    pass

        # ── P1: REMINDER ──
        if today_weekday in study_days and plan.get("reminders_enabled", 1):
            window_start = study_time - timedelta(minutes=30)
            window_end = study_time + timedelta(minutes=30)
            if window_start <= now_pkt <= window_end:
                if not self._sent_today(jid, "reminder", today_str):
                    return "reminder"

        # ── P2: REVIEW (spaced repetition concepts due) ──
        concepts_due = self.get_concepts_due(jid, limit=1)
        if concepts_due and not self._sent_today(jid, "review", today_str):
            return "review"

        # ── P3: CHECKIN ──
        if plan.get("checkins_enabled", 1):
            if now_pkt > study_time + timedelta(hours=2):
                if not self._sent_today(jid, "checkin", today_str):
                    return "checkin"

        # ── P4: DELOAD (high streak + burnout risk) ──
        if streak >= 7 and affect in ("frustrated", "anxious", "neutral"):
            if not self._sent_recently(jid, "deload", 7):
                return "deload"

        # ── P5: CHALLENGE (high mastery on current topic) ──
        top = self.get_highest_mastery_subject(jid)
        if top and top["avg_m"] >= 0.85:
            if not self._sent_recently(jid, "challenge", 3):
                return "challenge"

        # ── P6: SCAFFOLDING (same concept failed 2+ times) ──
        if self.get_concept_count(jid) > 0:
            weak = self._conn.execute(
                """SELECT concept_id FROM concept_mastery
                   WHERE jid=? AND mastery_level<0.3 AND repetition_count>=2 LIMIT 1""",
                (jid,),
            ).fetchone()
            if weak and not self._sent_recently(jid, "scaffolding", 3):
                return "scaffolding"

        # ── P7: NUDGE ──
        if plan.get("nudges_enabled", 1):
            nudge_days = plan.get("nudge_after_days", 2)
            last_active = plan.get("last_activity_date") or plan.get("created_at", "")
            if last_active:
                try:
                    last_dt = datetime.strptime(last_active[:10], "%Y-%m-%d")
                    days_inactive = (datetime.now() - last_dt).days
                    if days_inactive >= nudge_days:
                        if not self._sent_recently(jid, "nudge", 2):
                            return "nudge"
                except (ValueError, TypeError):
                    pass

        # ── P8: CELEBRATION_SPECIFIC (mastery jumped significantly) ──
        if self.get_concept_count(jid) >= 3:
            high_mastery = self._conn.execute(
                """SELECT COUNT(*) as cnt FROM concept_mastery
                   WHERE jid=? AND mastery_level>=0.8""",
                (jid,),
            ).fetchone()
            if high_mastery and high_mastery["cnt"] >= 3:
                if not self._sent_recently(jid, "celebration_specific", 5):
                    return "celebration_specific"

        # ── P9: COUNTDOWN ──
        if plan.get("countdown_enabled", 1):
            class_name = plan.get("class", "")
            if class_name in ("Grade 9", "Grade 10"):
                next_exam = self.get_next_exam(class_name)
                if next_exam and next_exam["days_left"] <= 7:
                    if not self._sent_today(jid, "countdown", today_str):
                        return "countdown"

        # ── P10: ACHIEVEMENT ──
        if streak in ACHIEVEMENT_MILESTONES:
            if not self._has_achievement(jid, streak):
                return "achievement"

        # ── P11: AUTONOMY_CHECK (weekly, engaged students) ──
        if today_weekday == 0 and engagement >= 40:  # Monday
            if not self._sent_recently(jid, "autonomy_check", 7):
                return "autonomy_check"

        # ── P12: CURIOSITY (low engagement students, once a week) ──
        if engagement < 30 and not self._sent_recently(jid, "curiosity", 7):
            return "curiosity"

        return None

    # ── Message composition ──

    async def compose_message(self, jid, message_type, plan):
        """AI-generate hyper-personalized message using full student context.

        Context layers injected into prompt:
        1. Identity: name, subjects, streak, exam countdown, today's plan
        2. Memory: full MEMORY.md context from conversation history
        3. Cognitive: concept mastery summary + concepts due for review
        4. Affective: current emotional state (frustrated/bored/anxious/confident/neutral)
        5. Effectiveness: 7-day message response stats (what worked, what didn't)
        6. SDT directives: autonomy/competence/relatedness guidance per message type
        """
        # ── Layer 1: Identity context ──
        display_name = plan.get("display_name", "")
        subjects = plan.get("focus_subjects", [])
        if isinstance(subjects, str):
            try:
                subjects = json.loads(subjects)
            except Exception:
                subjects = []
        streak = plan.get("current_streak", 0)
        exam_date = plan.get("exam_date", "")
        days_left = ""
        exam_schedule = ""

        # Enhanced countdown context with exam timetable
        if message_type == "countdown":
            class_name = plan.get("class", "")
            if class_name in ("Grade 9", "Grade 10"):
                next_exam = self.get_next_exam(class_name)
                if next_exam:
                    days_left = f"{next_exam['days_left']} days until {next_exam['subject']} exam ({next_exam['date']})"

                    # Get all remaining exams for full schedule
                    today = datetime.now().strftime("%Y-%m-%d")
                    remaining_exams = self._conn.execute(
                        """SELECT subject, exam_date FROM exam_timetable
                           WHERE board='FBISE' AND class=? AND exam_date >= ?
                           ORDER BY exam_date ASC""",
                        (class_name, today),
                    ).fetchall()
                    if remaining_exams:
                        schedule_lines = [f"  {row['exam_date']}: {row['subject']}" for row in remaining_exams[:5]]
                        exam_schedule = "Remaining exam schedule:\n" + "\n".join(schedule_lines)
        elif exam_date:
            try:
                exam_dt = datetime.strptime(exam_date, "%Y-%m-%d")
                dl = (exam_dt - datetime.now()).days
                if dl > 0:
                    days_left = f"{dl} days until exam"
            except (ValueError, TypeError):
                pass

        today_plan = ""
        weekly = plan.get("weekly_plan_json", {})
        if isinstance(weekly, str):
            try:
                weekly = json.loads(weekly)
            except Exception:
                weekly = {}
        if weekly:
            day_names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
            today_name = day_names[datetime.now().weekday()]
            today_plan = weekly.get(today_name, "")

        # ── Layer 2: Memory context ──
        memory_context = ""
        if self.memory_store:
            try:
                memory_context = self.memory_store.read_contact_memory(jid) or ""
            except Exception:
                pass

        # ── Layer 3: Cognitive model (concept mastery) ──
        mastery_ctx = self.get_mastery_summary(jid)
        concepts_due = self.get_concepts_due(jid, limit=3)
        due_ctx = ""
        if concepts_due:
            due_lines = [f"  {c['subject']}/{c['topic']} (mastery: {int(c['mastery_level']*100)}%)"
                         for c in concepts_due]
            due_ctx = "Concepts due for review:\n" + "\n".join(due_lines)

        # ── Layer 4: Affective model ──
        affect = self.get_affect(jid)
        engagement = plan.get("engagement_score", 50)

        # ── Layer 5: Effectiveness feedback ──
        effectiveness_ctx = self.get_effectiveness_summary(jid)

        # ── Layer 6: SDT directives per message type ──
        sdt_map = {
            "reminder": "Support COMPETENCE: reference a specific skill they're building.",
            "checkin": "Support RELATEDNESS: show you remember their last session.",
            "nudge": "Support AUTONOMY: offer a choice, don't demand.",
            "countdown": "Support COMPETENCE: highlight what they've already prepared.",
            "achievement": "Support COMPETENCE: name the specific effort behind the streak.",
            "review": "Support COMPETENCE: frame review as strengthening, not testing.",
            "challenge": "Support COMPETENCE + AUTONOMY: present challenge as optional next step.",
            "recovery": "Support RELATEDNESS: empathize first, academics second. Offer help.",
            "curiosity": "Support AUTONOMY: connect to their interests, let them choose.",
            "scaffolding": "Support COMPETENCE: break into tiny wins. Celebrate each micro-step.",
            "celebration_specific": "Support COMPETENCE: name the EXACT milestone. Be precise.",
            "autonomy_check": "Support AUTONOMY: offer 2-3 choices. No wrong answer.",
            "deload": "Support AUTONOMY: normalize rest. No guilt about breaks.",
        }
        sdt_directive = sdt_map.get(message_type, "")

        type_instruction = TYPE_INSTRUCTIONS.get(message_type, "")

        # ── Build enriched prompt ──
        prompt = (
            "You are Babloo, a friendly Feynman-style tutor for Pakistani students.\n"
            f"Generate a SHORT proactive {message_type} message (max 200 chars).\n"
            "Language: Natural Hinglish/Roman Urdu mix. Warm, encouraging, never nagging.\n\n"
            f"── STUDENT PROFILE ──\n"
            f"Name: {display_name}\n"
            f"Focus subjects: {', '.join(subjects) if subjects else 'not set'}\n"
            f"Current streak: {streak} days\n"
            f"Engagement score: {engagement}/100\n"
        )
        if days_left:
            prompt += f"Exam countdown: {days_left}\n"
        if exam_schedule:
            prompt += f"\n{exam_schedule}\n"
        if today_plan:
            prompt += f"Today's plan: {today_plan}\n"

        # Memory context (truncated to avoid token bloat)
        mem_snippet = memory_context[:500] if memory_context else "New student"
        prompt += f"\n── MEMORY (what you know about them) ──\n{mem_snippet}\n"

        # Emotional state
        prompt += f"\n── EMOTIONAL STATE ──\n"
        prompt += f"Current affect: {affect}\n"
        if affect == "frustrated":
            prompt += "IMPORTANT: They are frustrated. Be extra gentle. No academic pressure.\n"
        elif affect == "anxious":
            prompt += "IMPORTANT: They are anxious. Be calming and reassuring.\n"
        elif affect == "bored":
            prompt += "IMPORTANT: They are bored. Make it exciting or introduce something new.\n"
        elif affect == "confident":
            prompt += "Great -- they're feeling confident. Match their energy!\n"

        # Concept mastery
        if mastery_ctx:
            prompt += f"\n── CONCEPT MASTERY ──\n{mastery_ctx}\n"
        if due_ctx:
            prompt += f"{due_ctx}\n"

        # Effectiveness
        if effectiveness_ctx:
            prompt += f"\n── WHAT'S WORKING ──\n{effectiveness_ctx}\n"

        # SDT + type instruction
        prompt += f"\n── MESSAGE TYPE: {message_type.upper()} ──\n"
        prompt += f"Instruction: {type_instruction}\n"
        if sdt_directive:
            prompt += f"SDT focus: {sdt_directive}\n"

        prompt += "\nReply with ONLY the message text, nothing else."

        # ── Call AI Gateway ──
        api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
        url = self.config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1")
        if not api_key:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "anthropic/claude-haiku-4.5",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 350,
            }
            if httpx:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(url + "/chat/completions", json=payload, headers=headers)
                    if resp.status_code != 200:
                        print(f"[proactive] AI Gateway HTTP {resp.status_code}")
                        return None
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"].strip()
                    return text
        except httpx.TimeoutException:
            print(f"[proactive] compose_message timeout for {jid}")
        except Exception as e:
            print(f"[proactive] compose_message error: {e}")
        return None

    def _static_message(self, message_type, plan):
        """Fallback static messages when AI is unavailable."""
        name = plan.get("display_name", "")
        subjects = plan.get("focus_subjects", [])
        if isinstance(subjects, str):
            try:
                subjects = json.loads(subjects)
            except Exception:
                subjects = []
        subject = subjects[0] if subjects else "studies"
        streak = plan.get("current_streak", 0)

        templates = {
            "reminder": [
                f"{name}! Study time hai -- {subject} ka time! Chal shuru karte hain",
                f"{name}, padhne ka time ho gaya! {subject} pe focus karo aaj",
                f"{name}! Ready for {subject}? Lets gooo",
            ],
            "checkin": [
                f"{name}, kaisa raha study session? Kya cover kiya aaj?",
                f"{name}! Aaj ki padhai kaisi rahi? Batao kya seekha",
            ],
            "nudge": [
                f"{name}, miss kar rahe hain tujhe! Wapas aa jao studies pe",
                f"{name}, kab se dikhe nahi. Chal thoda sa padh lete hain",
            ],
            "countdown": [
                f"{name}, exam aa raha hai! Focus mode ON karo ab",
            ],
            "achievement": [
                f"{name}! {streak} din ki streak! Masha'Allah, keep it up!",
            ],
            "review": [
                f"{name}! Quick recall time -- {subject} ka ek concept yaad karo",
                f"{name}, chal ek chhota quiz khelte hain {subject} pe!",
            ],
            "challenge": [
                f"{name}, {subject} mein aage badhne ka time! Next level try karo",
                f"{name}! Ready for a challenge? {subject} mein kuch naya try karte hain",
            ],
            "recovery": [
                f"{name}, sab theek hai? Koi baat nahi, break le lo. Main hoon",
                f"{name}, mushkil lagta hai? Chal mil ke solve karte hain, no rush",
            ],
            "curiosity": [
                f"{name}! Ek interesting fact -- {subject} real life mein kaise kaam aata hai?",
                f"{name}, kya pata {subject} ka ye concept kitna cool hai!",
            ],
            "scaffolding": [
                f"{name}, chal is topic ko chhotey steps mein todte hain. Step 1 se shuru!",
                f"{name}! Ek aur angle se try karte hain -- simple explanation ready hai",
            ],
            "celebration_specific": [
                f"{name}! {subject} mein progress dikha rahi hai! Boht achi baat hai",
                f"Masha'Allah {name}! {subject} pe mehnat rang la rahi hai",
            ],
            "autonomy_check": [
                f"{name}, aaj kya padhna chahte ho? {subject} ya kuch aur? Tum chuno!",
                f"{name}! Aaj tumhari choice -- kya topic karna hai?",
            ],
            "deload": [
                f"{name}, boht mehnat ki hai! Aaj rest le lo, kal phir full power",
                f"{name}! Break lo -- dimagh ko bhi rest chahiye. Proud of you!",
            ],
        }
        options = templates.get(message_type, [f"{name}, study time!"])
        return random.choice(options)

    # ── Sending ──

    async def _send_proactive(self, jid, message_type, text, sent_date, jitter=0):
        """Send a proactive message and log it."""
        status = "sent"
        error = ""
        try:
            chat_jid = jid if "@" in jid else f"{jid}@s.whatsapp.net"
            if self.channel:
                await self.channel.send_text(chat_jid, text)
                print(f"[proactive] Sent {message_type} to {jid}")
            else:
                status = "failed"
                error = "No channel"
        except Exception as e:
            status = "failed"
            error = str(e)
            print(f"[proactive] Send failed for {jid}: {e}")

        self._conn.execute(
            """INSERT INTO proactive_log (jid, message_type, message_text, sent_date,
               jitter_seconds, status, error) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (jid, message_type, text, sent_date, jitter, status, error),
        )
        self._conn.commit()

    # ── Rate limiting ──

    def get_sent_count_today(self, jid):
        """Count proactive messages sent to student today."""
        today = datetime.now().strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM proactive_log WHERE jid=? AND sent_date=? AND status='sent'",
            (jid, today),
        ).fetchone()
        return row["cnt"] if row else 0

    def can_send(self, jid, plan, tz=None):
        """Check if we can send a proactive message (daily cap + quiet hours)."""
        max_msgs = plan.get("max_daily_messages", 3)
        if self.get_sent_count_today(jid) >= max_msgs:
            return False
        if self.is_quiet_hour(tz):
            return False
        return True

    def is_quiet_hour(self, tz=None):
        """Returns True during quiet hours."""
        if tz:
            now = datetime.now(tz)
        else:
            now = datetime.now(PKT)
        h = now.hour
        m = now.minute
        weekday = now.weekday()

        # Night: 23:00 - 07:00
        if h >= QUIET_NIGHT_START or h < QUIET_NIGHT_END:
            return True
        # School hours: 07:00 - 14:00 (Mon-Sat, weekday 0-5)
        if weekday < 6 and QUIET_SCHOOL_START <= h < QUIET_SCHOOL_END:
            return True
        # Jummah: Friday (4) 13:00-14:00
        if weekday == 4 and QUIET_JUMMAH_START <= h < QUIET_JUMMAH_END:
            return True
        return False

    def apply_jitter(self):
        """Returns random delay 0-60 seconds (reduced from 300 for batch mode)."""
        return random.randint(0, self.MAX_JITTER_SEC)

    def check_force_flag(self):
        """Check if a force_check flag file exists (written by dashboard API)."""
        flag_file = self.base_dir / ".proactive_force_check"
        if flag_file.exists():
            try:
                flag_file.unlink()
            except Exception:
                pass
            return True
        return False

    # ── Query helpers ──

    def _sent_today(self, jid, message_type, today_str):
        """Check if a specific message type was sent today."""
        row = self._conn.execute(
            """SELECT COUNT(*) as cnt FROM proactive_log
               WHERE jid=? AND message_type=? AND sent_date=? AND status='sent'""",
            (jid, message_type, today_str),
        ).fetchone()
        return row["cnt"] > 0 if row else False

    def _sent_recently(self, jid, message_type, days=2):
        """Check if a message type was sent in the last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        row = self._conn.execute(
            """SELECT COUNT(*) as cnt FROM proactive_log
               WHERE jid=? AND message_type=? AND sent_date>=? AND status='sent'""",
            (jid, message_type, cutoff),
        ).fetchone()
        return row["cnt"] > 0 if row else False

    def _has_achievement(self, jid, streak):
        """Check if an achievement was already sent for this streak value."""
        row = self._conn.execute(
            """SELECT COUNT(*) as cnt FROM proactive_log
               WHERE jid=? AND message_type='achievement' AND status='sent'
               AND message_text LIKE ?""",
            (jid, f"%{streak}%"),
        ).fetchone()
        return row["cnt"] > 0 if row else False

    # ── Stats / Dashboard API ──

    def get_stats(self):
        """Get overall proactive system stats."""
        today = datetime.now().strftime("%Y-%m-%d")
        active = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM student_plans WHERE enabled=1"
        ).fetchone()["cnt"]
        total = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM student_plans"
        ).fetchone()["cnt"]
        today_msgs = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM proactive_log WHERE sent_date=? AND status='sent'",
            (today,),
        ).fetchone()["cnt"]
        avg_streak_row = self._conn.execute(
            "SELECT AVG(current_streak) as avg FROM student_plans WHERE enabled=1"
        ).fetchone()
        avg_streak = round(avg_streak_row["avg"] or 0, 1)
        total_progress = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM study_progress"
        ).fetchone()["cnt"]
        return {
            "active_students": active,
            "total_students": total,
            "messages_today": today_msgs,
            "avg_streak": avg_streak,
            "total_progress_entries": total_progress,
        }

    def get_student_list(self):
        """Get all students with summary info."""
        rows = self._conn.execute(
            "SELECT jid, display_name, board, class, current_streak, last_activity_date, enabled, exam_date FROM student_plans ORDER BY display_name"
        ).fetchall()
        students = []
        now = datetime.now()
        for r in rows:
            d = dict(r)
            exam_date = d.get("exam_date", "")
            if exam_date:
                try:
                    exam_dt = datetime.strptime(exam_date, "%Y-%m-%d").replace(tzinfo=None)
                    d["exam_days_left"] = (exam_dt - now).days
                except (ValueError, TypeError):
                    d["exam_days_left"] = None
            else:
                d["exam_days_left"] = None
            students.append(d)
        return students

    def get_message_history(self, limit=50):
        """Get recent proactive messages."""
        rows = self._conn.execute(
            """SELECT l.*, sp.display_name FROM proactive_log l
               LEFT JOIN student_plans sp ON l.jid = sp.jid
               ORDER BY l.sent_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_exam_calendar(self):
        """Get all exam calendar entries."""
        rows = self._conn.execute(
            "SELECT * FROM exam_calendar ORDER BY exam_start"
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_exam_date(self, board, class_, exam_start, exam_end, year):
        """Insert or update an exam calendar entry."""
        existing = self._conn.execute(
            "SELECT id FROM exam_calendar WHERE board=? AND class=? AND year=?",
            (board, class_, year),
        ).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE exam_calendar SET exam_start=?, exam_end=? WHERE id=?",
                (exam_start, exam_end, existing["id"]),
            )
        else:
            self._conn.execute(
                "INSERT INTO exam_calendar (board, class, exam_start, exam_end, year) VALUES (?, ?, ?, ?, ?)",
                (board, class_, exam_start, exam_end, year),
            )
        self._conn.commit()

    def get_student_detail(self, jid):
        """Get full student detail including plan, progress, and message history."""
        plan = self.get_plan(jid)
        progress = self.get_progress(jid, days=30)
        messages = self._conn.execute(
            "SELECT * FROM proactive_log WHERE jid=? ORDER BY sent_at DESC LIMIT 20",
            (jid,),
        ).fetchall()
        return {
            "plan": plan,
            "progress": progress,
            "messages": [dict(r) for r in messages],
        }

    def toggle_student(self, jid):
        """Toggle a student's enabled status."""
        plan = self.get_plan(jid)
        if not plan:
            return {"error": "Student not found"}
        new_status = 0 if plan["enabled"] else 1
        self._conn.execute(
            "UPDATE student_plans SET enabled=?, updated_at=datetime('now') WHERE jid=?",
            (new_status, jid),
        )
        self._conn.commit()
        return {"jid": jid, "enabled": new_status}

    def seed_exam_calendar(self):
        """Seed exam calendar with 2026 dates if empty."""
        existing = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM exam_calendar"
        ).fetchone()
        if existing["cnt"] > 0:
            return

        entries = [
            ("FBISE", "9th", "2026-04-01", "2026-04-25", 2026),
            ("FBISE", "10th", "2026-04-01", "2026-04-25", 2026),
            ("FBISE", "11th", "2026-05-15", "2026-06-10", 2026),
            ("FBISE", "12th", "2026-05-15", "2026-06-10", 2026),
            ("Punjab", "9th", "2026-03-15", "2026-04-15", 2026),
            ("Punjab", "10th", "2026-03-15", "2026-04-15", 2026),
        ]
        for board, cls, start, end, year in entries:
            self._conn.execute(
                "INSERT INTO exam_calendar (board, class, exam_start, exam_end, year) VALUES (?, ?, ?, ?, ?)",
                (board, cls, start, end, year),
            )
        self._conn.commit()
        print(f"[proactive] Seeded {len(entries)} exam calendar entries")

    def _seed_exam_data(self):
        """Seed exam_timetable with FBISE SSC 2026 exam dates if empty (idempotent)."""
        existing = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM exam_timetable WHERE board='FBISE' AND year=2026"
        ).fetchone()
        if existing["cnt"] > 0:
            return

        # SSC-I (Grade 9) exam dates (board, class, exam_date, subject, is_practical)
        ssc1_exams = [
            ("FBISE", "Grade 9", "2026-04-01", "Math", 0),
            ("FBISE", "Grade 9", "2026-04-04", "Tarjuma Quran", 0),
            ("FBISE", "Grade 9", "2026-04-10", "Chemistry", 0),
            ("FBISE", "Grade 9", "2026-04-13", "English", 0),
            ("FBISE", "Grade 9", "2026-04-16", "Urdu", 0),
            ("FBISE", "Grade 9", "2026-04-20", "Islamiat", 0),
            ("FBISE", "Grade 9", "2026-04-24", "Physics", 0),
            ("FBISE", "Grade 9", "2026-04-29", "Bio/Computer", 0),
        ]

        # SSC-II (Grade 10) exam dates
        ssc2_exams = [
            ("FBISE", "Grade 10", "2026-03-31", "Physics", 0),
            ("FBISE", "Grade 10", "2026-04-03", "Islamiat", 0),
            ("FBISE", "Grade 10", "2026-04-06", "English", 0),
            ("FBISE", "Grade 10", "2026-04-09", "Urdu", 0),
            ("FBISE", "Grade 10", "2026-04-11", "Pak Studies", 0),
            ("FBISE", "Grade 10", "2026-04-15", "Bio/Computer", 0),
            ("FBISE", "Grade 10", "2026-04-18", "Bio/Comp (Practical)", 1),
            ("FBISE", "Grade 10", "2026-04-21", "Chemistry", 0),
            ("FBISE", "Grade 10", "2026-04-23", "Chemistry (Practical)", 1),
            ("FBISE", "Grade 10", "2026-04-27", "Math", 0),
            ("FBISE", "Grade 10", "2026-04-30", "Physics (Practical)", 1),
        ]

        all_exams = ssc1_exams + ssc2_exams
        for board, cls, exam_date, subject, is_practical in all_exams:
            self._conn.execute(
                "INSERT INTO exam_timetable (board, class, exam_date, subject, is_practical, year) VALUES (?, ?, ?, ?, ?, ?)",
                (board, cls, exam_date, subject, is_practical, 2026),
            )
        self._conn.commit()
        print(f"[proactive] Seeded {len(all_exams)} exam timetable entries for FBISE SSC 2026")


# ── Tool Definitions ──

PROACTIVE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "set_study_reminder",
            "description": "Set a study reminder for the student. Creates or updates their study schedule with preferred time and days. Use when a student says 'remind me to study' or 'set study time'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Subject to study (e.g., Physics, Math, Chemistry)",
                    },
                    "time": {
                        "type": "string",
                        "description": "Study time in HH:MM format (24-hour, PKT). Default 20:00.",
                    },
                    "recurring": {
                        "type": "boolean",
                        "description": "Whether this is a recurring daily reminder. Default true.",
                    },
                    "days": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Days of week (Mon,Tue,Wed,Thu,Fri,Sat,Sun). Default Mon-Sat.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Student's timezone as IANA name (e.g., Asia/Karachi, Asia/Dubai, Europe/London). Default Asia/Karachi. Ask the student to confirm their timezone.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_study_plan",
            "description": "Update a student's study plan with exam info, subjects, and schedule. Use when a student shares their board, class, exam date, or subjects. NOTE: Only FBISE board is supported, and only Grade 9 (SSC-I) and Grade 10 (SSC-II) classes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "board": {
                        "type": "string",
                        "description": "Education board (must be FBISE)",
                        "enum": ["FBISE"],
                    },
                    "class_name": {
                        "type": "string",
                        "description": "Class/grade (Grade 9 for SSC-I or Grade 10 for SSC-II only)",
                        "enum": ["Grade 9", "Grade 10"],
                    },
                    "exam_date": {
                        "type": "string",
                        "description": "Exam date in YYYY-MM-DD format",
                    },
                    "focus_subjects": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of focus subjects",
                    },
                    "daily_hours": {
                        "type": "number",
                        "description": "Target study hours per day. Default 2.",
                    },
                    "weekly_plan": {
                        "type": "object",
                        "description": "Day-wise study plan, e.g. {Monday: 'Physics Ch 5', Tuesday: 'Math'}",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_study_progress",
            "description": "Log a student's study progress. Use when a student reports what they studied, completed a topic, or finished a study session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Subject studied",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Specific topic or chapter covered",
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Study duration in minutes",
                    },
                },
                "required": ["subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_proactive",
            "description": "Toggle proactive messaging features on/off. Use when a student says 'stop reminders', 'pause notifications', or 'turn on nudges'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "feature": {
                        "type": "string",
                        "description": "Which feature to toggle",
                        "enum": ["all", "reminders", "checkins", "nudges", "countdown"],
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "True to enable, False to disable",
                    },
                },
                "required": ["feature", "enabled"],
            },
        },
    },
]


# ── ProactiveIntegration ──

class ProactiveIntegration:
    """Exposes proactive study tools as LLM-callable tools.

    Follows the same pattern as BroadcastIntegration.
    """

    def __init__(self, engine: ProactiveEngine):
        self.engine = engine

    def tool_definitions(self):
        return PROACTIVE_TOOL_DEFINITIONS

    def execute(self, tool_name, arguments):
        """Execute a proactive tool call."""
        from src.tool_executor import ToolResult

        jid = arguments.get("jid", "")
        if not jid:
            return ToolResult("set_study_reminder", "No student context (JID not set)")

        if tool_name == "set_study_reminder":
            return self._handle_set_reminder(jid, arguments)
        elif tool_name == "update_study_plan":
            return self._handle_update_plan(jid, arguments)
        elif tool_name == "log_study_progress":
            return self._handle_log_progress(jid, arguments)
        elif tool_name == "toggle_proactive":
            return self._handle_toggle(jid, arguments)
        else:
            return ToolResult(tool_name, f"Unknown proactive tool: {tool_name}")

    def _handle_set_reminder(self, jid, args):
        from src.tool_executor import ToolResult

        subject = args.get("subject", "")
        study_time = args.get("time", "20:00")
        days = args.get("days", ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat"))
        tz = args.get("timezone", "Asia/Karachi")

        # Convert day names to numbers (Mon=1, ..., Sun=0)
        day_nums = {
            "Sun": 0, "Mon": 1, "Tue": 2, "Wed": 3,
            "Thu": 4, "Fri": 5, "Sat": 6,
        }
        study_days = [str(day_nums.get(d, 1)) for d in days]

        plan = self.engine.get_plan(jid)
        updates = {
            "study_time": study_time,
            "study_days": ",".join(study_days),
            "reminders_enabled": 1,
            "timezone": tz,
        }
        if subject:
            updates["focus_subjects"] = [subject]

        if plan:
            existing = self.engine.update_plan(jid, **updates)
        else:
            display_name = args.get("display_name", "")
            existing = self.engine.create_plan(
                jid=jid, display_name=display_name,
                study_time=study_time, focus_subjects=[subject] if subject else "[]",
                timezone=tz,
            )
            if study_days != ["1", "2", "3", "4", "5", "6"]:
                self.engine.update_plan(jid, study_days=",".join(study_days))

        day_str = ", ".join(days)
        msg = f"Study reminder set for {study_time} PKT on {day_str}"
        if subject:
            msg += f" (focus: {subject})"
        return ToolResult("set_study_reminder", msg)

    def _handle_update_plan(self, jid, args):
        from src.tool_executor import ToolResult

        plan = self.engine.get_plan(jid)
        updates = {}
        if "board" in args:
            updates["board"] = args["board"]
        if "class_name" in args:
            updates["class"] = args["class_name"]
        if "exam_date" in args:
            updates["exam_date"] = args["exam_date"]
        if "focus_subjects" in args:
            updates["focus_subjects"] = args["focus_subjects"]
        if "daily_hours" in args:
            updates["daily_target_hours"] = args["daily_hours"]
        if "weekly_plan" in args:
            updates["weekly_plan_json"] = args["weekly_plan"]
        if "display_name" in args:
            updates["display_name"] = args["display_name"]

        if plan:
            result = self.engine.update_plan(jid, **updates)
        else:
            display_name = args.get("display_name", "")
            result = self.engine.create_plan(
                jid=jid, display_name=display_name,
                board=args.get("board", ""),
                class_=args.get("class_name", ""),
                exam_date=args.get("exam_date", ""),
                focus_subjects=args.get("focus_subjects", []),
            )
            if "weekly_plan_json" in updates:
                self.engine.update_plan(jid, weekly_plan_json=updates["weekly_plan_json"])

        parts = []
        if "board" in updates:
            parts.append("Board: " + updates["board"])
        if "class" in updates:
            parts.append("Class: " + updates["class"])
        if "exam_date" in updates:
            parts.append("Exam: " + updates["exam_date"])
        if "focus_subjects" in updates:
            subj = updates["focus_subjects"]
            parts.append("Subjects: " + (", ".join(subj) if isinstance(subj, list) else str(subj)))
        if "daily_target_hours" in updates:
            parts.append(f"Daily target: {updates['daily_target_hours']}h")

        summary = "Study plan updated: " + "; ".join(parts) if parts else "Study plan created"
        return ToolResult("update_study_plan", summary)

    def _handle_log_progress(self, jid, args):
        from src.tool_executor import ToolResult

        subject = args.get("subject", "")
        topic = args.get("topic", "")
        duration = args.get("duration_minutes", 0)

        if not subject:
            return ToolResult("log_study_progress", "Subject is required")

        result = self.engine.log_progress(
            jid=jid, subject=subject, topic=topic,
            duration_minutes=duration, source="self_reported",
        )
        streak = result.get("current_streak", 0)
        msg = f"Logged: {subject} - {topic}" if topic else f"Logged: {subject}"
        if duration:
            msg += f" ({duration} min)"
        msg += f". Streak: {streak} day"
        if streak != 1:
            msg += "s"
        return ToolResult("log_study_progress", msg)

    def _handle_toggle(self, jid, args):
        from src.tool_executor import ToolResult

        feature = args.get("feature", "all")
        enabled = args.get("enabled", True)
        val = 1 if enabled else 0

        plan = self.engine.get_plan(jid)
        if not plan:
            self.engine.create_plan(jid=jid)

        if feature == "all":
            self.engine.update_plan(
                jid,
                reminders_enabled=val,
                checkins_enabled=val,
                nudges_enabled=val,
                countdown_enabled=val,
                enabled=val,
            )
        elif feature == "reminders":
            self.engine.update_plan(jid, reminders_enabled=val)
        elif feature == "checkins":
            self.engine.update_plan(jid, checkins_enabled=val)
        elif feature == "nudges":
            self.engine.update_plan(jid, nudges_enabled=val)
        elif feature == "countdown":
            self.engine.update_plan(jid, countdown_enabled=val)
        else:
            return ToolResult("toggle_proactive", f"Unknown feature: {feature}")

        state = "enabled" if enabled else "disabled"
        return ToolResult("toggle_proactive", f"Proactive {feature} {state}")
