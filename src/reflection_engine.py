"""Reflection & Learning Engine for HappyCapy WhatsApp skill.

The bot learns from its own mistakes and owner corrections over time.
Three learning signals:

1. **Owner corrections**: When the admin replies to a contact to fix/override
   what the bot said, the bot recognizes the pattern and learns.
2. **Escalation feedback**: When ask_owner is used and the owner responds,
   the answer is stored as a lesson for similar future questions.
3. **Self-reflection**: Periodic LLM-powered review of recent interactions
   to identify mistakes, missed context, and improvement areas.

Storage: SQLite database with lessons indexed by category and keywords.
Lessons are injected into the system prompt via context_builder as a
"## Lessons Learned" section.
"""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class ReflectionEngine:
    """Learn from mistakes and owner corrections."""

    MAX_LESSONS_IN_PROMPT = 10  # Max lessons injected into system prompt
    MAX_LESSON_AGE_DAYS = 90   # Expire old lessons

    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                lesson TEXT NOT NULL,
                context TEXT DEFAULT '',
                contact_id TEXT DEFAULT '',
                source TEXT DEFAULT 'correction',
                relevance_score REAL DEFAULT 1.0,
                times_applied INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_lessons_category
            ON lessons(category)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_lessons_contact
            ON lessons(contact_id)
        """)
        # Escalation answer cache: store owner answers for reuse
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS escalation_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_pattern TEXT NOT NULL,
                answer TEXT NOT NULL,
                contact_id TEXT DEFAULT '',
                times_reused INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self._conn.commit()

    # ── Learning from corrections ──

    def record_correction(
        self,
        bot_said: str,
        owner_correction: str,
        contact_id: str = "",
        contact_name: str = "",
    ) -> int:
        """Record when the owner corrected or overrode the bot's response.

        Returns the lesson ID.
        """
        lesson = (
            f"When talking to {contact_name or 'a contact'}, "
            f"I said: \"{bot_said[:200]}\" but the owner corrected with: "
            f"\"{owner_correction[:200]}\". "
            f"In future similar situations, follow the owner's style/approach."
        )
        context = json.dumps({
            "bot_response": bot_said[:500],
            "correction": owner_correction[:500],
            "contact": contact_name or contact_id,
        })

        cursor = self._conn.execute(
            """INSERT INTO lessons (category, lesson, context, contact_id, source)
               VALUES (?, ?, ?, ?, 'correction')""",
            ("tone_correction", lesson, context, contact_id),
        )
        self._conn.commit()
        return cursor.lastrowid

    def record_escalation_answer(
        self,
        question: str,
        answer: str,
        contact_id: str = "",
    ) -> int:
        """Store an owner's answer to an escalated question for future reuse."""
        cursor = self._conn.execute(
            """INSERT INTO escalation_answers (question_pattern, answer, contact_id)
               VALUES (?, ?, ?)""",
            (question[:500], answer[:1000], contact_id),
        )
        self._conn.commit()
        return cursor.lastrowid

    def record_lesson(
        self,
        category: str,
        lesson: str,
        context: str = "",
        contact_id: str = "",
        source: str = "reflection",
    ) -> int:
        """Record a general lesson learned."""
        cursor = self._conn.execute(
            """INSERT INTO lessons (category, lesson, context, contact_id, source)
               VALUES (?, ?, ?, ?, ?)""",
            (category, lesson[:1000], context[:2000], contact_id, source),
        )
        self._conn.commit()
        return cursor.lastrowid

    # ── Retrieving lessons for prompt injection ──

    def get_lessons_for_prompt(
        self, contact_id: str = "", limit: int | None = None,
    ) -> str:
        """Get formatted lessons for system prompt injection.

        Returns both global lessons and contact-specific lessons,
        prioritized by relevance and recency.
        """
        limit = limit or self.MAX_LESSONS_IN_PROMPT

        # Get global lessons (high relevance) + contact-specific lessons
        rows = self._conn.execute(
            """SELECT lesson, category, source, relevance_score, times_applied
               FROM lessons
               WHERE (contact_id = '' OR contact_id = ?)
               AND created_at > datetime('now', ? || ' days')
               ORDER BY relevance_score DESC, created_at DESC
               LIMIT ?""",
            (contact_id, f"-{self.MAX_LESSON_AGE_DAYS}", limit),
        ).fetchall()

        if not rows:
            return ""

        lines = ["## Lessons Learned (from past mistakes)"]
        lines.append("These are patterns learned from previous errors. Apply them:")
        for row in rows:
            lines.append(f"- [{row['category']}] {row['lesson']}")

        return "\n".join(lines)

    def get_similar_escalation_answers(
        self, question: str, contact_id: str = "", limit: int = 3,
    ) -> list[dict]:
        """Find previously answered similar questions.

        Uses keyword overlap to find similar past escalations.
        """
        # Extract keywords from question
        keywords = set(re.findall(r"\b\w{3,}\b", question.lower()))
        stop_words = {"the", "and", "for", "are", "but", "not", "you", "all",
                      "can", "had", "her", "was", "one", "our", "out", "has",
                      "what", "when", "where", "how", "who", "why", "this", "that"}
        keywords -= stop_words

        if not keywords:
            return []

        rows = self._conn.execute(
            """SELECT question_pattern, answer, contact_id, times_reused
               FROM escalation_answers
               WHERE (contact_id = '' OR contact_id = ?)
               ORDER BY created_at DESC
               LIMIT 50""",
            (contact_id,),
        ).fetchall()

        # Score by keyword overlap
        results = []
        for row in rows:
            q_keywords = set(re.findall(r"\b\w{3,}\b", row["question_pattern"].lower()))
            q_keywords -= stop_words
            if not q_keywords:
                continue
            overlap = len(keywords & q_keywords)
            if overlap >= 2 or (overlap >= 1 and len(keywords) <= 3):
                results.append({
                    "question": row["question_pattern"],
                    "answer": row["answer"],
                    "contact_id": row["contact_id"],
                    "score": overlap / max(len(keywords), len(q_keywords)),
                })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    # ── Self-reflection (LLM-powered) ──

    async def reflect(
        self,
        recent_interactions: list[dict],
        api_url: str,
        api_key: str,
        model: str = "gpt-4.1-mini",
    ) -> list[dict]:
        """LLM-powered self-reflection on recent interactions.

        Analyzes recent bot responses for mistakes, tone issues,
        and areas for improvement. Returns list of new lessons.
        """
        try:
            import httpx
        except ImportError:
            return []

        if not recent_interactions:
            return []

        # Format interactions for analysis
        interaction_text = []
        for item in recent_interactions[-20:]:  # Last 20 interactions
            role = item.get("role", "?")
            content = item.get("content", "")[:300]
            contact = item.get("contact_name", "unknown")
            interaction_text.append(f"[{contact}] {role}: {content}")

        system_prompt = (
            "You are a self-improvement analyst for a WhatsApp bot. "
            "Review these recent interactions and identify SPECIFIC mistakes or improvements.\n\n"
            "Look for:\n"
            "1. Tone mismatches (too formal when contact is casual, or vice versa)\n"
            "2. Information the bot should have deflected instead of answering\n"
            "3. Missed context from conversation history\n"
            "4. Responses that were too long or too short for WhatsApp\n"
            "5. Cases where the bot should have used ask_owner but didn't\n"
            "6. Factual claims that look fabricated\n\n"
            "Return a JSON array of lessons. Each lesson:\n"
            '{"category": "tone|privacy|fabrication|context|length|escalation", '
            '"lesson": "Specific actionable lesson", '
            '"severity": "low|medium|high"}\n\n'
            "If no issues found, return an empty array: []\n"
            "ONLY output the JSON array, nothing else."
        )

        url = api_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": "\n".join(interaction_text)},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 1500,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            reply = data["choices"][0]["message"]["content"].strip()
            if reply.startswith("```"):
                reply = re.sub(r"^```(?:json)?\s*", "", reply)
                reply = re.sub(r"\s*```$", "", reply)

            lessons = json.loads(reply)
            if not isinstance(lessons, list):
                return []

            # Store lessons
            new_lessons = []
            for item in lessons:
                if not isinstance(item, dict):
                    continue
                cat = item.get("category", "general")
                lesson_text = item.get("lesson", "")
                severity = item.get("severity", "medium")
                if lesson_text:
                    relevance = {"high": 2.0, "medium": 1.0, "low": 0.5}.get(severity, 1.0)
                    self._conn.execute(
                        """INSERT INTO lessons
                           (category, lesson, context, source, relevance_score)
                           VALUES (?, ?, ?, 'reflection', ?)""",
                        (cat, lesson_text, json.dumps(item), relevance),
                    )
                    new_lessons.append(item)

            if new_lessons:
                self._conn.commit()
                print(f"[reflection] Learned {len(new_lessons)} lessons from self-reflection")

            return new_lessons

        except Exception as e:
            print(f"[reflection] Error: {e}")
            return []

    # ── Maintenance ──

    def expire_old_lessons(self, days: int | None = None) -> int:
        """Remove old, low-relevance lessons."""
        days = days or self.MAX_LESSON_AGE_DAYS
        cursor = self._conn.execute(
            """DELETE FROM lessons
               WHERE created_at < datetime('now', ? || ' days')
               AND relevance_score < 1.0""",
            (f"-{days}",),
        )
        self._conn.commit()
        return cursor.rowcount

    def boost_lesson(self, lesson_id: int) -> None:
        """Boost a lesson's relevance when it's applied."""
        self._conn.execute(
            """UPDATE lessons
               SET times_applied = times_applied + 1,
                   relevance_score = MIN(relevance_score * 1.1, 5.0),
                   updated_at = datetime('now')
               WHERE id = ?""",
            (lesson_id,),
        )
        self._conn.commit()

    def get_stats(self) -> dict:
        """Get reflection engine statistics."""
        row = self._conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN source='correction' THEN 1 ELSE 0 END) as corrections, "
            "SUM(CASE WHEN source='reflection' THEN 1 ELSE 0 END) as reflections "
            "FROM lessons"
        ).fetchone()
        esc_count = self._conn.execute(
            "SELECT COUNT(*) as total FROM escalation_answers"
        ).fetchone()
        return {
            "total_lessons": row["total"] or 0,
            "from_corrections": row["corrections"] or 0,
            "from_reflections": row["reflections"] or 0,
            "escalation_answers_cached": esc_count["total"] or 0,
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
