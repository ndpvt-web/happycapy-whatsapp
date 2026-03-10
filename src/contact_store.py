"""Persistent contact card system for context-relevant replies.

Stores per-contact profiles in SQLite with:
- Communication style (tone, formality, emoji usage, message length)
- Relationship context (relationship type, topics, interaction frequency)
- Conversation samples for profile generation
- Profile evolution tracking (changelog)

Profiles are generated/updated by LLM analysis of conversation samples.
Injected into system prompt for personalized, context-aware replies.
"""

import asyncio
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

# Strip <reply> tags from assistant samples before storage.
# Without this, FTS5 indexes tag noise and profile generation sees markup.
_REPLY_TAG_RE = re.compile(r"</?reply(?:\s[^>]*)?>", re.IGNORECASE)

try:
    import httpx
except ImportError:
    httpx = None


@dataclass
class ContactProfile:
    """Per-contact communication profile."""
    jid: str = ""
    display_name: str = ""
    tone: str = "neutral"  # casual, formal, mixed, neutral
    formality: float = 0.5  # 0.0 = very casual, 1.0 = very formal
    emoji_usage: str = "moderate"  # none, rare, moderate, frequent
    avg_message_length: str = "medium"  # short, medium, long
    language: str = "en"
    languages_used: list[str] = field(default_factory=lambda: ["en"])
    relationship: str = "unknown"  # friend, family, colleague, acquaintance, unknown
    topics: list[str] = field(default_factory=list)
    interaction_frequency: str = "unknown"  # daily, weekly, monthly, rare, unknown
    response_style: str = ""
    sample_phrases: list[str] = field(default_factory=list)
    summary: str = ""  # LLM-generated summary of this contact
    total_messages_analyzed: int = 0
    last_updated: str = ""
    profile_version: int = 0


class ContactStore:
    """SQLite-backed contact profile store."""

    MIN_SAMPLES_FOR_PROFILE = 5  # Minimum messages before generating a profile
    PROFILE_UPDATE_INTERVAL = 10  # Re-analyze after this many new messages
    PROFILE_MAX_AGE_HOURS = 48   # Re-analyze if profile older than this (time-based trigger)

    # Theorem T_PROFSAN: Max chars for profile context injected into system prompt.
    # Bounds prompt injection surface from contact-manipulated profile data (P_PROMPTINJ).
    # 500 chars ≈ 150 tokens. Enough for useful context, small enough to limit injection.
    _MAX_PROFILE_CONTEXT_CHARS = 500

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        # Theorem T_FPERM: Restrict DB file permissions to owner-only (P_FPERMS).
        # DB contains full conversation history - highly sensitive PII.
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass
        # Asyncio lock serializes writes from concurrent tasks (e.g., store_sample
        # from multiple contact handlers + background generate_profile).
        # SQLite allows concurrent reads but writes must be serialized.
        self._write_lock = asyncio.Lock()
        # In-memory profile cache (Theorem T_PCACHE).
        # Eliminates repeated SQLite SELECT + JSON parse on every message.
        # Invalidated on save_profile.
        self._profile_cache: dict[str, ContactProfile] = {}
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS contact_profiles (
                jid TEXT PRIMARY KEY,
                display_name TEXT DEFAULT '',
                profile_json TEXT DEFAULT '{}',
                total_messages_analyzed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS conversation_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                jid TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT DEFAULT (datetime('now')),
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_samples_jid ON conversation_samples(jid);
            CREATE INDEX IF NOT EXISTS idx_samples_jid_ts ON conversation_samples(jid, timestamp DESC);

            CREATE TABLE IF NOT EXISTS profile_changelog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                jid TEXT NOT NULL,
                change_summary TEXT NOT NULL,
                changed_at TEXT DEFAULT (datetime('now'))
            );
        """)
        self._conn.commit()

        self._init_group_tables()

    async def store_sample(self, jid: str, role: str, content: str, timestamp: str = "") -> None:
        """Store a conversation sample for future profile analysis.

        Uses _write_lock to serialize SQLite writes across concurrent asyncio tasks.
        Strips <reply> tags from assistant responses to keep FTS5 clean.
        """
        if not content or len(content.strip()) < 2:
            return

        # Strip <reply> tags from assistant responses before storage
        clean = _REPLY_TAG_RE.sub("", content).strip() if role == "assistant" else content

        ts = timestamp or datetime.now().isoformat()
        async with self._write_lock:
            self._conn.execute(
                "INSERT INTO conversation_samples (jid, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (jid, role, clean[:2000], ts),
            )
            self._conn.commit()

    def get_sample_count(self, jid: str) -> int:
        """Get number of stored samples for a contact."""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM conversation_samples WHERE jid = ?", (jid,)
        ).fetchone()
        return row["cnt"] if row else 0

    def get_recent_samples(self, jid: str, limit: int = 30) -> list[dict]:
        """Get recent conversation samples for a contact."""
        rows = self._conn.execute(
            "SELECT role, content, timestamp FROM conversation_samples WHERE jid = ? ORDER BY timestamp DESC LIMIT ?",
            (jid, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]} for r in reversed(rows)]

    def get_recent_samples_all(self, limit: int = 30) -> list[dict]:
        """Get recent conversation samples across ALL contacts (for memory consolidation)."""
        rows = self._conn.execute(
            "SELECT jid, role, content, timestamp FROM conversation_samples ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]}
            for r in reversed(rows)
        ]

    def get_active_jids(self, min_samples: int = 3) -> list[tuple[str, str]]:
        """Get JIDs with recent conversation samples for per-contact consolidation.

        Returns list of (jid, display_name) tuples for contacts with at least
        min_samples conversation samples.
        """
        rows = self._conn.execute(
            """SELECT cs.jid, COALESCE(cp.display_name, cs.jid) as name,
                      COUNT(*) as cnt
               FROM conversation_samples cs
               LEFT JOIN contact_profiles cp ON cs.jid = cp.jid
               GROUP BY cs.jid
               HAVING cnt >= ?
               ORDER BY MAX(cs.timestamp) DESC""",
            (min_samples,),
        ).fetchall()
        return [(r["jid"], r["name"]) for r in rows]

    def get_profile(self, jid: str) -> ContactProfile | None:
        """Get a contact's profile.

        Theorem T_PCACHE: Check in-memory cache first (P_CACHE).
        Cache eliminates SQLite SELECT + JSON parse on the hot path.
        """
        if jid in self._profile_cache:
            return self._profile_cache[jid]

        row = self._conn.execute(
            "SELECT * FROM contact_profiles WHERE jid = ?", (jid,)
        ).fetchone()
        if not row:
            return None

        try:
            data = json.loads(row["profile_json"])
            profile = ContactProfile(**{k: v for k, v in data.items() if k in ContactProfile.__dataclass_fields__})
            profile.jid = jid
            profile.display_name = row["display_name"] or ""
            profile.total_messages_analyzed = row["total_messages_analyzed"]
            self._profile_cache[jid] = profile
            return profile
        except (json.JSONDecodeError, TypeError):
            return ContactProfile(jid=jid)

    async def save_profile(self, profile: ContactProfile) -> None:
        """Save or update a contact profile (write-locked for async safety).

        Theorem T_PCACHE: Updates in-memory cache on write (cache invalidation).
        """
        profile.last_updated = datetime.now().isoformat()
        profile_json = json.dumps(asdict(profile), default=str)

        # Update cache immediately (Theorem T_PCACHE)
        self._profile_cache[profile.jid] = profile

        async with self._write_lock:
            self._conn.execute("""
                INSERT INTO contact_profiles (jid, display_name, profile_json, total_messages_analyzed, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(jid) DO UPDATE SET
                    display_name = excluded.display_name,
                    profile_json = excluded.profile_json,
                    total_messages_analyzed = excluded.total_messages_analyzed,
                    updated_at = datetime('now')
            """, (profile.jid, profile.display_name, profile_json, profile.total_messages_analyzed))
            self._conn.commit()

    def needs_profile_update(self, jid: str) -> bool:
        """Check if a contact needs profile generation/update.

        Triggers on either:
        - Message count: PROFILE_UPDATE_INTERVAL new messages since last analysis
        - Time-based: Profile older than PROFILE_MAX_AGE_HOURS with >= 3 new messages
        """
        sample_count = self.get_sample_count(jid)
        if sample_count < self.MIN_SAMPLES_FOR_PROFILE:
            return False

        profile = self.get_profile(jid)
        if not profile:
            return True

        new_messages = sample_count - profile.total_messages_analyzed

        # Message count trigger
        if new_messages >= self.PROFILE_UPDATE_INTERVAL:
            return True

        # Time-based trigger: stale profile with some new data
        if new_messages >= 3 and profile.last_updated:
            try:
                updated = datetime.fromisoformat(profile.last_updated)
                age_hours = (datetime.now() - updated).total_seconds() / 3600
                if age_hours >= self.PROFILE_MAX_AGE_HOURS:
                    return True
            except (ValueError, TypeError):
                pass

        return False

    async def generate_profile(
        self, jid: str, config: dict, client: "httpx.AsyncClient | None" = None,
    ) -> ContactProfile | None:
        """Generate or update a contact profile using LLM analysis.

        Theorem T_POOL: Reuses shared httpx client when provided (P_POOL).
        Theorem T_PMODEL: Defaults to Haiku for speed (P_HAIKU).
        """
        samples = self.get_recent_samples(jid, limit=40)
        if len(samples) < self.MIN_SAMPLES_FOR_PROFILE:
            return None

        existing = self.get_profile(jid)

        # Format samples for LLM
        formatted = []
        for s in samples:
            prefix = "Contact" if s["role"] == "user" else "You"
            formatted.append(f"[{prefix}] {s['content'][:300]}")

        conversation_text = "\n".join(formatted[-30:])

        prompt = f"""Analyze this WhatsApp conversation and create a contact profile.

Conversation samples:
{conversation_text}

{"Existing profile to update: " + json.dumps(asdict(existing), default=str) if existing else "No existing profile."}

Return a JSON object with these fields:
- display_name: their likely name (if mentioned) or empty string
- tone: "casual", "formal", "mixed", or "neutral"
- formality: 0.0 (very casual) to 1.0 (very formal)
- emoji_usage: "none", "rare", "moderate", or "frequent"
- avg_message_length: "short", "medium", or "long"
- language: primary language code (e.g., "en", "hi", "es")
- languages_used: array of language codes used
- relationship: "friend", "family", "colleague", "acquaintance", or "unknown"
- topics: array of common discussion topics (max 5)
- interaction_frequency: "daily", "weekly", "monthly", "rare", or "unknown"
- response_style: one sentence describing how to match their communication style
- sample_phrases: 3-5 characteristic phrases they use
- summary: 2-3 sentence summary of this contact and conversation pattern

Return ONLY valid JSON, no markdown or explanation."""

        if not httpx:
            return None

        api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
        if not api_key:
            return None

        gateway_url = config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1")
        # Theorem T_PMODEL: Use Haiku for profile gen (faster, non-user-facing).
        model = config.get("profile_model", "claude-haiku-4-5-20251001")

        url = f"{gateway_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a conversation analyst. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1024,
            "temperature": 0.3,
        }

        try:
            # Theorem T_POOL: Reuse shared client if provided (P_POOL).
            if client:
                resp = await client.post(url, headers=headers, json=payload, timeout=30.0)
            else:
                async with httpx.AsyncClient() as _c:
                    resp = await _c.post(url, headers=headers, json=payload, timeout=30.0)

            if resp.status_code == 200:
                    text = resp.json()["choices"][0]["message"]["content"]
                    # Extract JSON from response (handle markdown wrapping)
                    text = text.strip()
                    if text.startswith("```"):
                        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                    data = json.loads(text)
                    profile = ContactProfile(
                        jid=jid,
                        display_name=data.get("display_name", existing.display_name if existing else ""),
                        tone=data.get("tone", "neutral"),
                        formality=data.get("formality", 0.5),
                        emoji_usage=data.get("emoji_usage", "moderate"),
                        avg_message_length=data.get("avg_message_length", "medium"),
                        language=data.get("language", "en"),
                        languages_used=data.get("languages_used", ["en"]),
                        relationship=data.get("relationship", "unknown"),
                        topics=data.get("topics", [])[:5],
                        interaction_frequency=data.get("interaction_frequency", "unknown"),
                        response_style=data.get("response_style", ""),
                        sample_phrases=data.get("sample_phrases", [])[:5],
                        summary=data.get("summary", ""),
                        total_messages_analyzed=self.get_sample_count(jid),
                        profile_version=(existing.profile_version + 1) if existing else 1,
                    )
                    await self.save_profile(profile)

                    # Log change (under write lock)
                    async with self._write_lock:
                        self._conn.execute(
                            "INSERT INTO profile_changelog (jid, change_summary) VALUES (?, ?)",
                            (jid, f"Profile v{profile.profile_version}: {profile.summary[:200]}"),
                        )
                        self._conn.commit()

                    print(f"Profile updated for {jid}: {profile.display_name or 'unnamed'} ({profile.relationship})")
                    return profile
        except Exception as e:
            print(f"Profile generation error for {jid}: {e}")

        return None

    def format_profile_for_prompt(self, jid: str) -> str:
        """Format a contact's profile for injection into the system prompt.

        Theorem T_PROFSAN: Bound total output to _MAX_PROFILE_CONTEXT_CHARS (P_PROMPTINJ).
        Contact-controlled data (display_name, topics, phrases, summary) could contain
        prompt injection attempts. Truncation limits the injection surface area.
        """
        profile = self.get_profile(jid)
        if not profile:
            return ""

        parts = []
        parts.append(f"\n--- Contact Profile ---")

        if profile.display_name:
            parts.append(f"Name: {profile.display_name[:50]}")
        if profile.relationship != "unknown":
            parts.append(f"Relationship: {profile.relationship}")
        if profile.tone != "neutral":
            parts.append(f"Their tone: {profile.tone} (formality: {profile.formality:.1f})")
        if profile.language != "en" or len(profile.languages_used) > 1:
            parts.append(f"Languages: {', '.join(profile.languages_used[:5])}")
        if profile.topics:
            parts.append(f"Common topics: {', '.join(t[:30] for t in profile.topics[:5])}")
        if profile.emoji_usage != "moderate":
            parts.append(f"Emoji usage: {profile.emoji_usage}")
        if profile.response_style:
            parts.append(f"Match their style: {profile.response_style[:100]}")
        if profile.summary:
            parts.append(f"Context: {profile.summary[:150]}")
        if profile.sample_phrases:
            parts.append(f"Their phrases: {', '.join(repr(p[:30]) for p in profile.sample_phrases[:3])}")

        parts.append("--- End Profile ---")

        result = "\n".join(parts)
        # Theorem T_PROFSAN: Hard cap on total profile context length.
        if len(result) > self._MAX_PROFILE_CONTEXT_CHARS:
            result = result[:self._MAX_PROFILE_CONTEXT_CHARS] + "\n--- End Profile ---"
        return result

    def get_all_profiles(self) -> list[ContactProfile]:
        """Get all stored contact profiles."""
        rows = self._conn.execute(
            "SELECT jid FROM contact_profiles ORDER BY updated_at DESC"
        ).fetchall()
        profiles = []
        for row in rows:
            p = self.get_profile(row["jid"])
            if p:
                profiles.append(p)
        return profiles

    # ── Group Intelligence (sampled, lightweight) ──

    # Rate limits to prevent group message floods from consuming storage/CPU.
    # 500 samples per group = ~100KB max. 60s cooldown = max 1 sample/min/group.
    _GROUP_MAX_SAMPLES = 500
    _GROUP_SAMPLE_COOLDOWN = 60  # seconds between stored samples per group

    def _init_group_tables(self) -> None:
        """Create group-specific tables (called from _init_db)."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS group_cards (
                group_jid TEXT PRIMARY KEY,
                group_name TEXT DEFAULT '',
                member_count INTEGER DEFAULT 0,
                active_members TEXT DEFAULT '[]',
                topics TEXT DEFAULT '[]',
                message_rate TEXT DEFAULT 'unknown',
                last_active TEXT DEFAULT '',
                card_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS group_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_jid TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_group_samples_jid
                ON group_samples(group_jid);
            CREATE INDEX IF NOT EXISTS idx_group_samples_sender
                ON group_samples(sender_id);
        """)
        # FTS5 virtual table for group message search
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS group_samples_fts
            USING fts5(content, content=group_samples, content_rowid=id, tokenize='porter')
        """)
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS group_samples_fts_insert AFTER INSERT ON group_samples
            BEGIN INSERT INTO group_samples_fts(rowid, content) VALUES (new.id, new.content); END
        """)
        self._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS group_samples_fts_delete AFTER DELETE ON group_samples
            BEGIN INSERT INTO group_samples_fts(group_samples_fts, rowid, content) VALUES ('delete', old.id, old.content); END
        """)
        self._conn.commit()

    async def store_group_sample(
        self, group_jid: str, sender_id: str, content: str,
        group_name: str = "", timestamp: str = "",
    ) -> bool:
        """Store a sampled group message (rate-limited, capped).

        Returns True if stored, False if rate-limited or skipped.
        Designed to handle hundreds of messages/sec without slowing down.
        """
        if not content or len(content.strip()) < 3:
            return False

        # Rate limit: check last sample time for this group
        now = time.time()
        cache_key = f"_grp_{group_jid}"
        last_ts = getattr(self, cache_key, 0)
        if now - last_ts < self._GROUP_SAMPLE_COOLDOWN:
            return False
        setattr(self, cache_key, now)

        ts = timestamp or datetime.now().isoformat()
        async with self._write_lock:
            # Cap total samples per group (rolling window: delete oldest)
            count = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM group_samples WHERE group_jid = ?",
                (group_jid,),
            ).fetchone()["cnt"]

            if count >= self._GROUP_MAX_SAMPLES:
                # Delete oldest 10% to make room
                delete_count = max(self._GROUP_MAX_SAMPLES // 10, 1)
                self._conn.execute(
                    """DELETE FROM group_samples WHERE id IN (
                        SELECT id FROM group_samples WHERE group_jid = ?
                        ORDER BY id ASC LIMIT ?
                    )""",
                    (group_jid, delete_count),
                )

            # Store with truncated content (200 chars vs 2000 for DMs)
            self._conn.execute(
                "INSERT INTO group_samples (group_jid, sender_id, content, timestamp) VALUES (?, ?, ?, ?)",
                (group_jid, sender_id, content[:200], ts),
            )

            # Upsert group card (touch last_active + name)
            self._conn.execute("""
                INSERT INTO group_cards (group_jid, group_name, last_active)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(group_jid) DO UPDATE SET
                    group_name = CASE WHEN excluded.group_name != '' THEN excluded.group_name ELSE group_cards.group_name END,
                    last_active = datetime('now'),
                    updated_at = datetime('now')
            """, (group_jid, group_name))

            self._conn.commit()

        # Cross-pollinate: also store as DM sample for the sender's contact profile.
        # This way, contacts you know from groups build richer profiles.
        await self.store_sample(sender_id, "user", content, ts)

        return True

    def get_group_card(self, group_jid: str) -> dict | None:
        """Get a group's card info."""
        row = self._conn.execute(
            "SELECT * FROM group_cards WHERE group_jid = ?", (group_jid,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def get_group_active_members(self, group_jid: str, limit: int = 20) -> list[dict]:
        """Get most active members in a group (by message count)."""
        rows = self._conn.execute(
            """SELECT sender_id, COUNT(*) as msg_count
               FROM group_samples WHERE group_jid = ?
               GROUP BY sender_id ORDER BY msg_count DESC LIMIT ?""",
            (group_jid, limit),
        ).fetchall()
        result = []
        for row in rows:
            sid = row["sender_id"]
            profile = self.get_profile(sid)
            result.append({
                "sender_id": sid,
                "msg_count": row["msg_count"],
                "display_name": profile.display_name if profile else "",
                "has_profile": profile is not None,
            })
        return result

    def get_all_group_cards(self) -> list[dict]:
        """Get all group cards sorted by recent activity."""
        rows = self._conn.execute(
            "SELECT * FROM group_cards ORDER BY last_active DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def search_group_messages(self, query: str, group_jid: str = "", limit: int = 20) -> list[dict]:
        """FTS5 keyword search across group messages.

        Args:
            query: Search terms.
            group_jid: Optional - filter to specific group.
            limit: Max results (default 20, max 50).

        Returns:
            List of dicts with: group_jid, sender_id, content, timestamp, rank.
        """
        limit = min(limit, 50)
        try:
            if group_jid:
                rows = self._conn.execute(
                    """SELECT gs.group_jid, gs.sender_id, gs.content, gs.timestamp,
                              rank as relevance
                       FROM group_samples_fts fts
                       JOIN group_samples gs ON gs.id = fts.rowid
                       WHERE group_samples_fts MATCH ? AND gs.group_jid = ?
                       ORDER BY rank LIMIT ?""",
                    (query, group_jid, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT gs.group_jid, gs.sender_id, gs.content, gs.timestamp,
                              rank as relevance
                       FROM group_samples_fts fts
                       JOIN group_samples gs ON gs.id = fts.rowid
                       WHERE group_samples_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (query, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_recent_group_messages(self, group_jid: str = "", limit: int = 20) -> list[dict]:
        """Get recent group messages, optionally filtered by group.

        Args:
            group_jid: Optional - filter to specific group.
            limit: Max results (default 20, max 50).

        Returns:
            List of dicts with: group_jid, sender_id, content, timestamp.
        """
        limit = min(limit, 50)
        try:
            if group_jid:
                rows = self._conn.execute(
                    """SELECT group_jid, sender_id, content, timestamp
                       FROM group_samples WHERE group_jid = ?
                       ORDER BY id DESC LIMIT ?""",
                    (group_jid, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT group_jid, sender_id, content, timestamp
                       FROM group_samples
                       ORDER BY id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def resolve_group_by_name(self, partial_name: str) -> str | None:
        """Resolve a partial group name to a group_jid (case-insensitive).

        Returns the JID if a unique match is found, None otherwise.
        """
        try:
            rows = self._conn.execute(
                "SELECT group_jid, group_name FROM group_cards WHERE LOWER(group_name) LIKE ?",
                (f"%{partial_name.lower()}%",),
            ).fetchall()
            if len(rows) == 1:
                return rows[0]["group_jid"]
            return None
        except Exception:
            return None

    def prune_old_samples(self, keep_last: int = 5000) -> int:
        """Prune old conversation samples, keeping the last N per contact.

        Returns the total number of deleted rows.
        """
        try:
            # Get all distinct JIDs
            jids = [r[0] for r in self._conn.execute(
                "SELECT DISTINCT jid FROM conversation_samples"
            ).fetchall()]

            total_deleted = 0
            for jid in jids:
                count = self.get_sample_count(jid)
                if count > keep_last:
                    # Delete oldest entries beyond keep_last
                    cursor = self._conn.execute(
                        """DELETE FROM conversation_samples WHERE id IN (
                            SELECT id FROM conversation_samples WHERE jid = ?
                            ORDER BY timestamp ASC LIMIT ?
                        )""",
                        (jid, count - keep_last),
                    )
                    total_deleted += cursor.rowcount
            if total_deleted > 0:
                self._conn.commit()
            return total_deleted
        except Exception:
            return 0

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
