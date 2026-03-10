"""Broadcast Campaign Engine with Smart Segmentation.

Production-grade broadcast system for WhatsApp automation:
- Campaign creation with AI-personalized messages per contact
- Smart segmentation (auto-segments + custom filters)
- Scheduled delivery with rate limiting (avoid WhatsApp ban)
- Delivery tracking (pending -> sent -> delivered -> replied)
- Reply attribution within time window
- Conversion tracking

All data stored in SQLite. No external dependencies beyond what the bot already uses.
"""

import asyncio
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Coroutine

try:
    import httpx
except ImportError:
    httpx = None


# ── Data Classes ──


@dataclass
class Segment:
    """A contact segment for targeting broadcasts."""
    id: str = ""
    name: str = ""
    description: str = ""
    segment_type: str = "auto"  # "auto" (built-in) or "custom"
    filter_json: str = "{}"  # JSON filter criteria
    contact_count: int = 0
    created_at: str = ""
    updated_at: str = ""

    @property
    def filters(self) -> dict:
        try:
            return json.loads(self.filter_json)
        except (json.JSONDecodeError, TypeError):
            return {}


@dataclass
class Campaign:
    """A broadcast campaign."""
    id: str = ""
    name: str = ""
    message_template: str = ""  # Template with {name}, {custom} placeholders
    segment_id: str = ""  # Target segment
    segment_name: str = ""
    status: str = "draft"  # draft, scheduled, sending, paused, completed, cancelled
    personalize: bool = True  # AI-personalize per contact
    personalization_prompt: str = ""  # Extra instructions for AI personalization
    scheduled_at: float = 0  # UTC epoch, 0 = immediate
    started_at: float = 0
    completed_at: float = 0
    total_recipients: int = 0
    sent_count: int = 0
    delivered_count: int = 0
    read_count: int = 0
    replied_count: int = 0
    failed_count: int = 0
    created_at: str = ""
    created_by: str = ""  # admin JID
    # Rate limiting
    send_interval_s: float = 3.0  # Seconds between messages (WhatsApp safety)
    batch_size: int = 50  # Max messages per batch before pause
    batch_pause_s: float = 60.0  # Pause between batches


@dataclass
class CampaignMessage:
    """Individual message within a campaign."""
    id: str = ""
    campaign_id: str = ""
    recipient_jid: str = ""
    recipient_name: str = ""
    original_text: str = ""  # Template-rendered text
    personalized_text: str = ""  # AI-personalized final text
    status: str = "pending"  # pending, sending, sent, delivered, read, replied, failed
    error: str = ""
    sent_at: float = 0
    delivered_at: float = 0
    read_at: float = 0
    replied_at: float = 0
    reply_content: str = ""  # First reply content (for conversion tracking)


# ── Auto-Segment Definitions ──

AUTO_SEGMENTS = {
    "all_contacts": {
        "name": "All Contacts",
        "description": "Every contact with a known name",
        "filters": {"min_messages": 0},
    },
    "active": {
        "name": "Active (last 7 days)",
        "description": "Contacts who messaged in the last 7 days",
        "filters": {"active_within_days": 7},
    },
    "recent": {
        "name": "Recent (last 30 days)",
        "description": "Contacts who messaged in the last 30 days",
        "filters": {"active_within_days": 30},
    },
    "dormant": {
        "name": "Dormant (30+ days inactive)",
        "description": "Contacts with no messages in 30+ days",
        "filters": {"inactive_beyond_days": 30},
    },
    "new_contacts": {
        "name": "New Contacts",
        "description": "Contacts with fewer than 10 messages",
        "filters": {"max_messages": 10},
    },
    "repeat_contacts": {
        "name": "Repeat / Regular",
        "description": "Contacts with 20+ messages (loyal customers)",
        "filters": {"min_messages": 20},
    },
    "high_engagement": {
        "name": "High Engagement",
        "description": "Contacts with 50+ messages (VIP / power users)",
        "filters": {"min_messages": 50},
    },
}


# ── Campaign Store (SQLite) ──


class CampaignStore:
    """SQLite-backed store for campaigns, messages, and segments."""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._write_lock = asyncio.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """Create broadcast tables."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS broadcast_campaigns (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                message_template TEXT NOT NULL DEFAULT '',
                segment_id TEXT NOT NULL DEFAULT 'all_contacts',
                segment_name TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                personalize INTEGER DEFAULT 1,
                personalization_prompt TEXT DEFAULT '',
                scheduled_at REAL DEFAULT 0,
                started_at REAL DEFAULT 0,
                completed_at REAL DEFAULT 0,
                total_recipients INTEGER DEFAULT 0,
                sent_count INTEGER DEFAULT 0,
                delivered_count INTEGER DEFAULT 0,
                read_count INTEGER DEFAULT 0,
                replied_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                send_interval_s REAL DEFAULT 3.0,
                batch_size INTEGER DEFAULT 50,
                batch_pause_s REAL DEFAULT 60.0,
                created_at TEXT DEFAULT (datetime('now')),
                created_by TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS broadcast_messages (
                id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                recipient_jid TEXT NOT NULL,
                recipient_name TEXT DEFAULT '',
                original_text TEXT DEFAULT '',
                personalized_text TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT DEFAULT '',
                sent_at REAL DEFAULT 0,
                delivered_at REAL DEFAULT 0,
                read_at REAL DEFAULT 0,
                replied_at REAL DEFAULT 0,
                reply_content TEXT DEFAULT '',
                FOREIGN KEY (campaign_id) REFERENCES broadcast_campaigns(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_bcast_msg_campaign
                ON broadcast_messages(campaign_id);
            CREATE INDEX IF NOT EXISTS idx_bcast_msg_recipient
                ON broadcast_messages(recipient_jid);
            CREATE INDEX IF NOT EXISTS idx_bcast_msg_status
                ON broadcast_messages(campaign_id, status);
            CREATE INDEX IF NOT EXISTS idx_bcast_msg_sent
                ON broadcast_messages(sent_at)
                WHERE sent_at > 0;

            CREATE TABLE IF NOT EXISTS broadcast_segments (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                segment_type TEXT NOT NULL DEFAULT 'custom',
                filter_json TEXT DEFAULT '{}',
                contact_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)
        self._conn.commit()

        # Restrict DB file permissions
        try:
            os.chmod(self._db_path, 0o600)
        except OSError:
            pass

    # ── Campaign CRUD ──

    async def create_campaign(self, campaign: Campaign) -> Campaign:
        """Create a new campaign."""
        if not campaign.id:
            campaign.id = f"BC-{uuid.uuid4().hex[:8].upper()}"
        campaign.created_at = datetime.now().isoformat()

        async with self._write_lock:
            self._conn.execute("""
                INSERT INTO broadcast_campaigns
                (id, name, message_template, segment_id, segment_name, status,
                 personalize, personalization_prompt, scheduled_at, total_recipients,
                 send_interval_s, batch_size, batch_pause_s, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                campaign.id, campaign.name, campaign.message_template,
                campaign.segment_id, campaign.segment_name, campaign.status,
                1 if campaign.personalize else 0, campaign.personalization_prompt,
                campaign.scheduled_at, campaign.total_recipients,
                campaign.send_interval_s,
                campaign.batch_size, campaign.batch_pause_s, campaign.created_by,
            ))
            self._conn.commit()

        return campaign

    def get_campaign(self, campaign_id: str) -> Campaign | None:
        """Get a campaign by ID."""
        row = self._conn.execute(
            "SELECT * FROM broadcast_campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_campaign(row)

    def list_campaigns(self, status: str = "", limit: int = 20) -> list[Campaign]:
        """List campaigns, optionally filtered by status."""
        if status:
            rows = self._conn.execute(
                "SELECT * FROM broadcast_campaigns WHERE status = ? ORDER BY rowid DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM broadcast_campaigns ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_campaign(r) for r in rows]

    async def update_campaign_status(self, campaign_id: str, status: str,
                                      **extra_fields: Any) -> bool:
        """Update campaign status and optional extra fields."""
        sets = ["status = ?"]
        vals: list[Any] = [status]
        for k, v in extra_fields.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(campaign_id)

        async with self._write_lock:
            cursor = self._conn.execute(
                f"UPDATE broadcast_campaigns SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
            self._conn.commit()
        return cursor.rowcount > 0

    async def increment_campaign_counter(self, campaign_id: str, field: str,
                                          amount: int = 1) -> None:
        """Atomically increment a campaign counter (sent_count, etc)."""
        valid = {"sent_count", "delivered_count", "read_count", "replied_count", "failed_count"}
        if field not in valid:
            return
        async with self._write_lock:
            self._conn.execute(
                f"UPDATE broadcast_campaigns SET {field} = {field} + ? WHERE id = ?",
                (amount, campaign_id),
            )
            self._conn.commit()

    async def delete_campaign(self, campaign_id: str) -> bool:
        """Delete a campaign and all its messages."""
        async with self._write_lock:
            self._conn.execute(
                "DELETE FROM broadcast_messages WHERE campaign_id = ?", (campaign_id,)
            )
            cursor = self._conn.execute(
                "DELETE FROM broadcast_campaigns WHERE id = ?", (campaign_id,)
            )
            self._conn.commit()
        return cursor.rowcount > 0

    # ── Message CRUD ──

    async def add_messages(self, messages: list[CampaignMessage]) -> int:
        """Bulk insert campaign messages. Returns count inserted."""
        if not messages:
            return 0
        async with self._write_lock:
            for msg in messages:
                if not msg.id:
                    msg.id = uuid.uuid4().hex[:12]
                self._conn.execute("""
                    INSERT INTO broadcast_messages
                    (id, campaign_id, recipient_jid, recipient_name, original_text,
                     personalized_text, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    msg.id, msg.campaign_id, msg.recipient_jid,
                    msg.recipient_name, msg.original_text,
                    msg.personalized_text, msg.status,
                ))
            self._conn.commit()
        return len(messages)

    def get_pending_messages(self, campaign_id: str, limit: int = 50) -> list[CampaignMessage]:
        """Get pending messages for a campaign (next batch to send)."""
        rows = self._conn.execute(
            """SELECT * FROM broadcast_messages
               WHERE campaign_id = ? AND status = 'pending'
               ORDER BY rowid ASC LIMIT ?""",
            (campaign_id, limit),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    async def update_message_status(self, message_id: str, status: str,
                                     **extra: Any) -> None:
        """Update a message's delivery status."""
        sets = ["status = ?"]
        vals: list[Any] = [status]
        for k, v in extra.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(message_id)

        async with self._write_lock:
            self._conn.execute(
                f"UPDATE broadcast_messages SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
            self._conn.commit()

    def get_campaign_stats(self, campaign_id: str) -> dict[str, int]:
        """Get message status breakdown for a campaign."""
        rows = self._conn.execute(
            """SELECT status, COUNT(*) as cnt
               FROM broadcast_messages WHERE campaign_id = ?
               GROUP BY status""",
            (campaign_id,),
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def get_campaign_messages(self, campaign_id: str, status: str = "",
                               limit: int = 100) -> list[CampaignMessage]:
        """Get messages for a campaign, optionally filtered by status."""
        if status:
            rows = self._conn.execute(
                """SELECT * FROM broadcast_messages
                   WHERE campaign_id = ? AND status = ?
                   ORDER BY sent_at DESC LIMIT ?""",
                (campaign_id, status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM broadcast_messages
                   WHERE campaign_id = ? ORDER BY rowid ASC LIMIT ?""",
                (campaign_id, limit),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    # ── Reply Attribution ──

    def find_recent_broadcast_message(self, sender_jid: str,
                                       window_hours: int = 48) -> CampaignMessage | None:
        """Find a broadcast message sent to this contact within the attribution window.

        Used to attribute incoming replies to a specific campaign. Returns the
        most recently sent broadcast message to this JID within window_hours.
        """
        cutoff = time.time() - (window_hours * 3600)
        row = self._conn.execute(
            """SELECT * FROM broadcast_messages
               WHERE recipient_jid = ? AND status IN ('sent', 'delivered', 'read')
               AND sent_at > ?
               ORDER BY sent_at DESC LIMIT 1""",
            (sender_jid, cutoff),
        ).fetchone()
        if row:
            return self._row_to_message(row)
        return None

    async def record_reply(self, message_id: str, campaign_id: str,
                            reply_content: str) -> None:
        """Record a reply to a broadcast message (conversion tracking)."""
        now = time.time()
        await self.update_message_status(
            message_id, "replied",
            replied_at=now, reply_content=reply_content[:500],
        )
        await self.increment_campaign_counter(campaign_id, "replied_count")

    # ── Segment CRUD ──

    async def save_segment(self, segment: Segment) -> Segment:
        """Create or update a custom segment."""
        if not segment.id:
            segment.id = f"SEG-{uuid.uuid4().hex[:6].upper()}"
        segment.updated_at = datetime.now().isoformat()
        if not segment.created_at:
            segment.created_at = segment.updated_at

        async with self._write_lock:
            self._conn.execute("""
                INSERT INTO broadcast_segments
                (id, name, description, segment_type, filter_json, contact_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    filter_json = excluded.filter_json,
                    contact_count = excluded.contact_count,
                    updated_at = excluded.updated_at
            """, (
                segment.id, segment.name, segment.description,
                segment.segment_type, segment.filter_json,
                segment.contact_count, segment.created_at, segment.updated_at,
            ))
            self._conn.commit()
        return segment

    def get_segment(self, segment_id: str) -> Segment | None:
        """Get a segment by ID."""
        # Check auto-segments first
        if segment_id in AUTO_SEGMENTS:
            auto = AUTO_SEGMENTS[segment_id]
            return Segment(
                id=segment_id,
                name=auto["name"],
                description=auto["description"],
                segment_type="auto",
                filter_json=json.dumps(auto["filters"]),
            )
        row = self._conn.execute(
            "SELECT * FROM broadcast_segments WHERE id = ?", (segment_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_segment(row)

    def list_segments(self) -> list[Segment]:
        """List all segments (auto + custom)."""
        segments = []
        # Auto-segments first
        for seg_id, auto in AUTO_SEGMENTS.items():
            segments.append(Segment(
                id=seg_id,
                name=auto["name"],
                description=auto["description"],
                segment_type="auto",
                filter_json=json.dumps(auto["filters"]),
            ))
        # Custom segments
        rows = self._conn.execute(
            "SELECT * FROM broadcast_segments ORDER BY name ASC"
        ).fetchall()
        for r in rows:
            segments.append(self._row_to_segment(r))
        return segments

    async def delete_segment(self, segment_id: str) -> bool:
        """Delete a custom segment."""
        if segment_id in AUTO_SEGMENTS:
            return False  # Can't delete auto-segments
        async with self._write_lock:
            cursor = self._conn.execute(
                "DELETE FROM broadcast_segments WHERE id = ?", (segment_id,)
            )
            self._conn.commit()
        return cursor.rowcount > 0

    # ── Row mappers ──

    @staticmethod
    def _row_to_campaign(row: sqlite3.Row) -> Campaign:
        return Campaign(
            id=row["id"],
            name=row["name"],
            message_template=row["message_template"],
            segment_id=row["segment_id"],
            segment_name=row["segment_name"] or "",
            status=row["status"],
            personalize=bool(row["personalize"]),
            personalization_prompt=row["personalization_prompt"] or "",
            scheduled_at=row["scheduled_at"] or 0,
            started_at=row["started_at"] or 0,
            completed_at=row["completed_at"] or 0,
            total_recipients=row["total_recipients"],
            sent_count=row["sent_count"],
            delivered_count=row["delivered_count"],
            read_count=row["read_count"],
            replied_count=row["replied_count"],
            failed_count=row["failed_count"],
            send_interval_s=row["send_interval_s"],
            batch_size=row["batch_size"],
            batch_pause_s=row["batch_pause_s"],
            created_at=row["created_at"] or "",
            created_by=row["created_by"] or "",
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> CampaignMessage:
        return CampaignMessage(
            id=row["id"],
            campaign_id=row["campaign_id"],
            recipient_jid=row["recipient_jid"],
            recipient_name=row["recipient_name"] or "",
            original_text=row["original_text"] or "",
            personalized_text=row["personalized_text"] or "",
            status=row["status"],
            error=row["error"] or "",
            sent_at=row["sent_at"] or 0,
            delivered_at=row["delivered_at"] or 0,
            read_at=row["read_at"] or 0,
            replied_at=row["replied_at"] or 0,
            reply_content=row["reply_content"] or "",
        )

    @staticmethod
    def _row_to_segment(row: sqlite3.Row) -> Segment:
        return Segment(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            segment_type=row["segment_type"],
            filter_json=row["filter_json"] or "{}",
            contact_count=row["contact_count"],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()


# ── Segmentation Engine ──


class SegmentationEngine:
    """Resolves segments to lists of target JIDs using ContactStore data."""

    def __init__(self, contact_store: Any):
        self._contact_store = contact_store

    def resolve_segment(self, segment: Segment) -> list[dict]:
        """Resolve a segment to a list of contacts matching the filters.

        Returns list of dicts: {jid, name, messages, last_active}
        """
        filters = segment.filters
        contacts = self._get_all_contacts_with_stats()

        results = []
        for c in contacts:
            if self._matches_filters(c, filters):
                results.append(c)

        return results

    def _get_all_contacts_with_stats(self) -> list[dict]:
        """Get all contacts with message counts and activity timestamps."""
        contacts = []

        # Get all WhatsApp contacts with names
        wa_contacts = self._contact_store.get_all_whatsapp_contacts()
        for wc in wa_contacts:
            jid = wc.get("jid", "")
            if not jid or jid.endswith("@g.us"):
                continue

            name = wc.get("saved_name") or wc.get("push_name") or wc.get("verified_name") or ""
            if not name:
                continue

            # Get message stats from conversation_samples
            full_jid = jid if "@" in jid else f"{jid}@s.whatsapp.net"
            msg_count = self._contact_store.get_sample_count(jid)
            last_active = self._get_last_active(jid)

            contacts.append({
                "jid": jid,
                "full_jid": full_jid,
                "name": name,
                "messages": msg_count,
                "last_active": last_active,
                "profile": self._contact_store.get_profile(jid),
            })

        return contacts

    def _get_last_active(self, jid: str) -> float:
        """Get epoch timestamp of most recent message from a contact."""
        try:
            row = self._contact_store._conn.execute(
                "SELECT timestamp FROM conversation_samples WHERE jid = ? ORDER BY timestamp DESC LIMIT 1",
                (jid,),
            ).fetchone()
            if row and row["timestamp"]:
                dt = datetime.fromisoformat(row["timestamp"])
                return dt.timestamp()
        except Exception:
            pass
        return 0

    def _matches_filters(self, contact: dict, filters: dict) -> bool:
        """Check if a contact matches segment filter criteria."""
        msg_count = contact.get("messages", 0)
        last_active = contact.get("last_active", 0)
        now = time.time()

        # min_messages filter
        if "min_messages" in filters:
            if msg_count < filters["min_messages"]:
                return False

        # max_messages filter
        if "max_messages" in filters:
            if msg_count > filters["max_messages"]:
                return False

        # active_within_days filter
        if "active_within_days" in filters:
            cutoff = now - (filters["active_within_days"] * 86400)
            if last_active < cutoff:
                return False

        # inactive_beyond_days filter
        if "inactive_beyond_days" in filters:
            cutoff = now - (filters["inactive_beyond_days"] * 86400)
            # Must have at least 1 message but not active recently
            if msg_count == 0 or last_active > cutoff:
                return False

        # relationship filter (from ContactProfile)
        if "relationship" in filters:
            profile = contact.get("profile")
            if profile:
                if profile.relationship != filters["relationship"]:
                    return False
            else:
                return False

        # language filter
        if "language" in filters:
            profile = contact.get("profile")
            if profile:
                if profile.language != filters["language"]:
                    return False
            else:
                return False

        # topic filter (contact must have at least one matching topic)
        if "topics" in filters:
            required_topics = filters["topics"]
            profile = contact.get("profile")
            if profile and profile.topics:
                contact_topics_lower = [t.lower() for t in profile.topics]
                if not any(t.lower() in contact_topics_lower for t in required_topics):
                    return False
            else:
                return False

        return True

    def get_segment_preview(self, segment: Segment, max_show: int = 10) -> str:
        """Get a preview of contacts in a segment for admin display."""
        contacts = self.resolve_segment(segment)
        total = len(contacts)

        if total == 0:
            return f"*{segment.name}*: 0 contacts"

        lines = [f"*{segment.name}* ({total} contacts)\n"]
        for c in contacts[:max_show]:
            msg_info = f"{c['messages']} msgs"
            lines.append(f"  - {c['name']} ({msg_info})")
        if total > max_show:
            lines.append(f"  ... and {total - max_show} more")

        return "\n".join(lines)


# ── Broadcast Engine ──


class BroadcastEngine:
    """Core broadcast execution engine.

    Handles campaign lifecycle:
    1. Create campaign + resolve segment -> queue messages
    2. Execute send loop with rate limiting
    3. AI-personalize each message using per-contact context
    4. Track delivery status
    5. Attribute replies to campaigns
    """

    # Safety limits
    MAX_RECIPIENTS_PER_CAMPAIGN = 1000
    MAX_CONCURRENT_CAMPAIGNS = 3
    REPLY_ATTRIBUTION_WINDOW_HOURS = 48

    def __init__(
        self,
        store: CampaignStore,
        segmentation: SegmentationEngine,
        config: dict[str, Any],
        channel: Any = None,  # WhatsAppChannel
        contact_store: Any = None,  # ContactStore
        memory_store: Any = None,  # MemoryStore
        knowledge_graph: Any = None,  # KnowledgeGraph
        http_client: Any = None,  # httpx.AsyncClient
    ):
        self.store = store
        self.segmentation = segmentation
        self.config = config
        self._channel = channel
        self._contact_store = contact_store
        self._memory = memory_store
        self._kg = knowledge_graph
        self._client = http_client
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._paused_campaigns: set[str] = set()

    async def create_campaign(
        self,
        name: str,
        message_template: str,
        segment_id: str = "all_contacts",
        personalize: bool = True,
        personalization_prompt: str = "",
        scheduled_at: float = 0,
        created_by: str = "",
    ) -> Campaign:
        """Create a new broadcast campaign and queue recipient messages.

        Args:
            name: Campaign display name.
            message_template: Message template (supports {name} placeholder).
            segment_id: Target segment ID.
            personalize: Whether to AI-personalize per contact.
            personalization_prompt: Extra instructions for AI.
            scheduled_at: UTC epoch for scheduled send (0 = needs manual start).
            created_by: Admin JID who created this.

        Returns:
            Created Campaign with queued messages.
        """
        # Resolve segment
        segment = self.store.get_segment(segment_id)
        if not segment:
            raise ValueError(f"Unknown segment: {segment_id}")

        contacts = self.segmentation.resolve_segment(segment)
        if not contacts:
            raise ValueError(f"Segment '{segment.name}' has no matching contacts")

        if len(contacts) > self.MAX_RECIPIENTS_PER_CAMPAIGN:
            raise ValueError(
                f"Too many recipients ({len(contacts)}). "
                f"Max is {self.MAX_RECIPIENTS_PER_CAMPAIGN}. Use a narrower segment."
            )

        # Create campaign
        campaign = Campaign(
            name=name,
            message_template=message_template,
            segment_id=segment_id,
            segment_name=segment.name,
            status="scheduled" if scheduled_at > time.time() else "draft",
            personalize=personalize,
            personalization_prompt=personalization_prompt,
            scheduled_at=scheduled_at,
            total_recipients=len(contacts),
            created_by=created_by,
        )
        campaign = await self.store.create_campaign(campaign)

        # Queue individual messages
        messages = []
        for c in contacts:
            rendered = self._render_template(message_template, c)
            msg = CampaignMessage(
                campaign_id=campaign.id,
                recipient_jid=c["full_jid"],
                recipient_name=c["name"],
                original_text=rendered,
                status="pending",
            )
            messages.append(msg)

        await self.store.add_messages(messages)
        print(f"[broadcast] Campaign {campaign.id} created: '{name}' -> {len(messages)} recipients ({segment.name})")

        return campaign

    async def start_campaign(self, campaign_id: str) -> str:
        """Start sending a campaign. Returns status message."""
        campaign = self.store.get_campaign(campaign_id)
        if not campaign:
            return f"Campaign {campaign_id} not found"

        if campaign.status not in ("draft", "scheduled", "paused"):
            return f"Campaign {campaign_id} cannot be started (status: {campaign.status})"

        if len(self._active_tasks) >= self.MAX_CONCURRENT_CAMPAIGNS:
            return f"Too many active campaigns ({len(self._active_tasks)}). Wait for one to finish."

        if not self._channel:
            return "WhatsApp channel not available"

        # Mark as sending
        await self.store.update_campaign_status(
            campaign_id, "sending", started_at=time.time()
        )
        self._paused_campaigns.discard(campaign_id)

        # Launch async send loop
        task = asyncio.create_task(self._send_loop(campaign_id))
        self._active_tasks[campaign_id] = task

        pending = campaign.total_recipients - campaign.sent_count - campaign.failed_count
        return f"Campaign {campaign_id} started. Sending to {pending} recipients..."

    async def pause_campaign(self, campaign_id: str) -> str:
        """Pause a sending campaign."""
        campaign = self.store.get_campaign(campaign_id)
        if not campaign or campaign.status != "sending":
            return f"Campaign {campaign_id} is not currently sending"

        self._paused_campaigns.add(campaign_id)
        await self.store.update_campaign_status(campaign_id, "paused")

        # Cancel the send loop task
        task = self._active_tasks.pop(campaign_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        stats = self.store.get_campaign_stats(campaign_id)
        sent = stats.get("sent", 0) + stats.get("delivered", 0) + stats.get("read", 0) + stats.get("replied", 0)
        pending = stats.get("pending", 0)
        return f"Campaign {campaign_id} paused. {sent} sent, {pending} remaining."

    async def cancel_campaign(self, campaign_id: str) -> str:
        """Cancel a campaign permanently."""
        campaign = self.store.get_campaign(campaign_id)
        if not campaign:
            return f"Campaign {campaign_id} not found"

        # Stop send loop if running
        self._paused_campaigns.add(campaign_id)
        task = self._active_tasks.pop(campaign_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await self.store.update_campaign_status(
            campaign_id, "cancelled", completed_at=time.time()
        )
        return f"Campaign {campaign_id} cancelled."

    def get_campaign_report(self, campaign_id: str) -> str:
        """Get a formatted campaign status report."""
        campaign = self.store.get_campaign(campaign_id)
        if not campaign:
            return f"Campaign {campaign_id} not found"

        stats = self.store.get_campaign_stats(campaign_id)
        total = campaign.total_recipients
        sent = stats.get("sent", 0)
        delivered = stats.get("delivered", 0)
        read = stats.get("read", 0)
        replied = stats.get("replied", 0)
        failed = stats.get("failed", 0)
        pending = stats.get("pending", 0)

        total_sent = sent + delivered + read + replied
        reply_rate = (replied / total_sent * 100) if total_sent > 0 else 0

        lines = [
            f"*Campaign: {campaign.name}*",
            f"ID: {campaign.id}",
            f"Status: {campaign.status.upper()}",
            f"Segment: {campaign.segment_name}",
            f"",
            f"*Delivery Stats*",
            f"  Total: {total}",
            f"  Sent: {total_sent}",
            f"  Pending: {pending}",
            f"  Failed: {failed}",
            f"",
            f"*Engagement*",
            f"  Replied: {replied} ({reply_rate:.1f}%)",
        ]

        if campaign.personalize:
            lines.append(f"  AI Personalized: Yes")

        if campaign.started_at:
            start_dt = datetime.fromtimestamp(campaign.started_at)
            lines.append(f"\nStarted: {start_dt.strftime('%Y-%m-%d %H:%M')}")
        if campaign.completed_at:
            end_dt = datetime.fromtimestamp(campaign.completed_at)
            duration = campaign.completed_at - campaign.started_at
            lines.append(f"Completed: {end_dt.strftime('%Y-%m-%d %H:%M')} ({duration:.0f}s)")

        # Show top replies
        replied_msgs = self.store.get_campaign_messages(campaign_id, status="replied", limit=5)
        if replied_msgs:
            lines.append(f"\n*Recent Replies*")
            for rm in replied_msgs:
                preview = rm.reply_content[:80] + ("..." if len(rm.reply_content) > 80 else "")
                lines.append(f"  {rm.recipient_name}: {preview}")

        return "\n".join(lines)

    async def check_reply_attribution(self, sender_jid: str,
                                       message_content: str) -> bool:
        """Check if an incoming message is a reply to a broadcast.

        Called on every incoming message. If the sender recently received
        a broadcast, attributes the reply to that campaign.

        Returns True if attributed (so caller can log it), False otherwise.
        """
        bare_jid = sender_jid.split("@")[0] if "@" in sender_jid else sender_jid

        # Check both bare and full JID formats
        msg = self.store.find_recent_broadcast_message(
            f"{bare_jid}@s.whatsapp.net",
            window_hours=self.REPLY_ATTRIBUTION_WINDOW_HOURS,
        )
        if not msg:
            msg = self.store.find_recent_broadcast_message(
                bare_jid,
                window_hours=self.REPLY_ATTRIBUTION_WINDOW_HOURS,
            )

        if msg and msg.status != "replied":
            await self.store.record_reply(msg.id, msg.campaign_id, message_content)
            campaign = self.store.get_campaign(msg.campaign_id)
            cname = campaign.name if campaign else msg.campaign_id
            print(f"[broadcast] Reply attributed: {bare_jid} -> campaign '{cname}'")
            return True

        return False

    # ── Internal send loop ──

    async def _send_loop(self, campaign_id: str) -> None:
        """Main send loop for a campaign. Sends in batches with rate limiting."""
        try:
            campaign = self.store.get_campaign(campaign_id)
            if not campaign:
                return

            batch_num = 0
            total_sent = 0

            while True:
                # Check for pause/cancel
                if campaign_id in self._paused_campaigns:
                    print(f"[broadcast] Campaign {campaign_id} paused during send loop")
                    return

                # Get next batch
                pending = self.store.get_pending_messages(
                    campaign_id, limit=campaign.batch_size
                )
                if not pending:
                    # All done
                    break

                batch_num += 1
                print(f"[broadcast] {campaign_id} batch #{batch_num}: {len(pending)} messages")

                for msg in pending:
                    if campaign_id in self._paused_campaigns:
                        return

                    await self._send_single_message(campaign, msg)
                    total_sent += 1

                    # Rate limit between messages
                    await asyncio.sleep(campaign.send_interval_s)

                # Pause between batches (only if more messages remain)
                remaining = self.store.get_pending_messages(campaign_id, limit=1)
                if remaining and campaign.batch_pause_s > 0:
                    print(f"[broadcast] {campaign_id} batch pause ({campaign.batch_pause_s}s)...")
                    await asyncio.sleep(campaign.batch_pause_s)

            # Campaign complete
            await self.store.update_campaign_status(
                campaign_id, "completed", completed_at=time.time()
            )
            print(f"[broadcast] Campaign {campaign_id} completed. {total_sent} messages sent.")

            # Notify admin
            admin = self.config.get("admin_number", "")
            if admin and self._channel:
                report = self.get_campaign_report(campaign_id)
                admin_jid = f"{admin}@s.whatsapp.net"
                try:
                    await self._channel.send_text(admin_jid, f"Broadcast complete!\n\n{report}")
                except Exception:
                    pass

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[broadcast] Campaign {campaign_id} error: {type(e).__name__}: {e}")
            await self.store.update_campaign_status(campaign_id, "paused")
        finally:
            self._active_tasks.pop(campaign_id, None)

    async def _send_single_message(self, campaign: Campaign,
                                    msg: CampaignMessage) -> None:
        """Send a single broadcast message, optionally AI-personalized."""
        try:
            # AI personalization if enabled
            final_text = msg.original_text
            if campaign.personalize:
                personalized = await self._personalize_message(
                    msg.original_text,
                    msg.recipient_jid,
                    msg.recipient_name,
                    campaign.personalization_prompt,
                )
                if personalized:
                    final_text = personalized
                    await self.store.update_message_status(
                        msg.id, "sending", personalized_text=personalized
                    )
                else:
                    await self.store.update_message_status(
                        msg.id, "sending", personalized_text=msg.original_text
                    )
            else:
                await self.store.update_message_status(msg.id, "sending")

            # Send via WhatsApp channel
            await self._channel.send_text(msg.recipient_jid, final_text)

            # Mark as sent
            await self.store.update_message_status(
                msg.id, "sent", sent_at=time.time()
            )
            await self.store.increment_campaign_counter(campaign.id, "sent_count")

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)[:100]}"
            await self.store.update_message_status(
                msg.id, "failed", error=error_msg
            )
            await self.store.increment_campaign_counter(campaign.id, "failed_count")
            print(f"[broadcast] Failed to send to {msg.recipient_name}: {error_msg}")

    async def _personalize_message(
        self,
        template_text: str,
        recipient_jid: str,
        recipient_name: str,
        extra_instructions: str = "",
    ) -> str | None:
        """AI-personalize a broadcast message for a specific contact.

        Uses per-contact memory, knowledge graph, and profile to create
        a unique, personalized version of the broadcast message.
        """
        if not httpx:
            return None

        api_key = os.environ.get("AI_GATEWAY_API_KEY", "")
        if not api_key:
            return None

        # Gather per-contact context
        context_parts = []
        bare_jid = recipient_jid.split("@")[0] if "@" in recipient_jid else recipient_jid

        if self._memory:
            mem_ctx = self._memory.get_memory_context(jid=bare_jid) or ""
            if mem_ctx:
                context_parts.append(f"Memory about this contact:\n{mem_ctx}")

        if self._kg:
            try:
                kg_ctx, _ = self._kg.retrieve(bare_jid, template_text)
                if kg_ctx:
                    context_parts.append(f"Knowledge about this contact:\n{kg_ctx}")
            except Exception:
                pass

        if self._contact_store:
            profile_ctx = self._contact_store.format_profile_for_prompt(bare_jid) or ""
            if profile_ctx:
                context_parts.append(profile_ctx)

        context = "\n\n".join(context_parts) if context_parts else "No prior context available."

        system_prompt = (
            "You are a message personalization assistant. Your job is to take a broadcast "
            "message template and personalize it for a specific contact based on what you know "
            "about them. Keep the core message and intent identical. Only adjust the greeting, "
            "tone, and add small personal touches based on the context provided.\n\n"
            "Rules:\n"
            "- Keep the message SHORT (WhatsApp-appropriate, 1-4 lines max)\n"
            "- Preserve the core offer/information from the template\n"
            "- Add personal touches ONLY if you have real context (don't fabricate)\n"
            "- Match the contact's communication style if known\n"
            "- If no useful context is available, return the template with just the name personalized\n"
            "- Return ONLY the final message text, nothing else\n"
            "- Do NOT add quotes, labels, or explanations\n"
        )
        if extra_instructions:
            system_prompt += f"\nAdditional instructions: {extra_instructions}\n"

        user_prompt = (
            f"Contact name: {recipient_name}\n\n"
            f"Contact context:\n{context}\n\n"
            f"Broadcast template:\n{template_text}\n\n"
            f"Personalize this message for {recipient_name}. Return ONLY the final message."
        )

        gateway_url = self.config.get("ai_gateway_url", "https://ai-gateway.happycapy.ai/api/v1/openai/v1")
        # Use fast model for personalization (runs per-recipient)
        model = self.config.get("profile_model", "gpt-4.1-mini")
        url = f"{gateway_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 300,
            "temperature": 0.7,
        }

        try:
            if self._client:
                resp = await self._client.post(url, headers=headers, json=payload, timeout=15.0)
            else:
                async with httpx.AsyncClient() as c:
                    resp = await c.post(url, headers=headers, json=payload, timeout=15.0)

            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"].strip()
                # Strip any surrounding quotes the model might add
                if text.startswith('"') and text.endswith('"'):
                    text = text[1:-1]
                return text
        except Exception as e:
            print(f"[broadcast] Personalization failed for {recipient_name}: {type(e).__name__}")

        return None

    def _render_template(self, template: str, contact: dict) -> str:
        """Render a message template with contact data."""
        text = template
        text = text.replace("{name}", contact.get("name", ""))
        text = text.replace("{first_name}", contact.get("name", "").split()[0] if contact.get("name") else "")
        return text

    # ── Scheduled campaign checker (called from heartbeat) ──

    async def check_scheduled_campaigns(self) -> None:
        """Check for campaigns that are due to send. Called by heartbeat."""
        now = time.time()
        scheduled = self.store.list_campaigns(status="scheduled")
        for campaign in scheduled:
            if campaign.scheduled_at > 0 and campaign.scheduled_at <= now:
                print(f"[broadcast] Scheduled campaign {campaign.id} is due, starting...")
                result = await self.start_campaign(campaign.id)
                print(f"[broadcast] {result}")


# ── LLM Tool Definitions (OpenAI format) ──

BROADCAST_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "create_broadcast",
            "description": (
                "Create and send a broadcast message to a group of WhatsApp contacts. "
                "Use when the user/owner wants to send a promotion, announcement, or update "
                "to multiple contacts at once. Supports AI personalization per contact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Campaign name (e.g. 'Weekend Sale', 'New Menu Alert')",
                    },
                    "message": {
                        "type": "string",
                        "description": (
                            "Message template to send. Use {name} for contact's name, "
                            "{first_name} for first name only. Example: "
                            "'Hey {first_name}! We have a special 20% off this weekend.'"
                        ),
                    },
                    "segment": {
                        "type": "string",
                        "description": (
                            "Target audience. Use segment ID: 'all_contacts', 'active', "
                            "'recent', 'dormant', 'new_contacts', 'repeat_contacts', "
                            "'high_engagement', or a custom segment ID."
                        ),
                    },
                    "personalize": {
                        "type": "boolean",
                        "description": (
                            "Whether to AI-personalize each message using contact's history "
                            "and preferences. Default true. Set false for uniform messages."
                        ),
                    },
                    "start_now": {
                        "type": "boolean",
                        "description": "Whether to start sending immediately. Default true.",
                    },
                },
                "required": ["name", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "campaign_status",
            "description": (
                "Get the status and delivery report for a broadcast campaign. "
                "Use when the user asks about a campaign's progress, delivery stats, "
                "or replies received."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {
                        "type": "string",
                        "description": "Campaign ID (e.g. 'BC-A1B2C3D4'). If not provided, shows the most recent campaign.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_segments",
            "description": (
                "List available contact segments for broadcasting. Shows both built-in "
                "auto-segments (active, dormant, new, repeat, etc.) and custom segments."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ── Integration as LLM Tools ──

class BroadcastIntegration:
    """Exposes broadcast operations as LLM-callable tools.

    Instantiated by the tool executor and receives tool calls from the AI.
    """

    def __init__(self, engine: "BroadcastEngine"):
        self._engine = engine

    @staticmethod
    def tool_definitions() -> list[dict]:
        return BROADCAST_TOOL_DEFINITIONS

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a broadcast tool call."""
        from src.tool_executor import ToolResult

        if tool_name == "create_broadcast":
            return await self._handle_create_broadcast(arguments)
        elif tool_name == "campaign_status":
            return await self._handle_campaign_status(arguments)
        elif tool_name == "list_segments":
            return await self._handle_list_segments(arguments)
        else:
            return ToolResult(False, tool_name, f"Unknown broadcast tool: {tool_name}")

    async def _handle_create_broadcast(self, args: dict) -> Any:
        from src.tool_executor import ToolResult

        name = args.get("name", "").strip()
        message = args.get("message", "").strip()
        segment = args.get("segment", "all_contacts").strip()
        personalize = args.get("personalize", True)
        start_now = args.get("start_now", True)

        if not name or not message:
            return ToolResult(False, "create_broadcast", "Campaign name and message are required")

        try:
            campaign = await self._engine.create_campaign(
                name=name,
                message_template=message,
                segment_id=segment,
                personalize=personalize,
            )

            result_msg = (
                f"Campaign '{campaign.name}' created ({campaign.id}).\n"
                f"Target: {campaign.segment_name} ({campaign.total_recipients} contacts)\n"
                f"Personalization: {'enabled' if personalize else 'disabled'}"
            )

            if start_now:
                start_result = await self._engine.start_campaign(campaign.id)
                result_msg += f"\n{start_result}"

            return ToolResult(True, "create_broadcast", result_msg)

        except ValueError as e:
            return ToolResult(False, "create_broadcast", str(e))
        except Exception as e:
            return ToolResult(False, "create_broadcast", f"Failed: {type(e).__name__}: {e}")

    async def _handle_campaign_status(self, args: dict) -> Any:
        from src.tool_executor import ToolResult

        campaign_id = args.get("campaign_id", "").strip()

        if not campaign_id:
            # Show most recent campaign
            campaigns = self._engine.store.list_campaigns(limit=1)
            if not campaigns:
                return ToolResult(True, "campaign_status", "No campaigns found.")
            campaign_id = campaigns[0].id

        report = self._engine.get_campaign_report(campaign_id)
        return ToolResult(True, "campaign_status", report)

    async def _handle_list_segments(self, args: dict) -> Any:
        from src.tool_executor import ToolResult

        segments = self._engine.store.list_segments()
        if not segments:
            return ToolResult(True, "list_segments", "No segments available.")

        lines = ["*Available Segments*\n"]
        for seg in segments:
            seg_type = "auto" if seg.segment_type == "auto" else "custom"
            contacts = self._engine.segmentation.resolve_segment(seg)
            lines.append(f"  [{seg.id}] {seg.name} ({len(contacts)} contacts) [{seg_type}]")
            if seg.description:
                lines.append(f"    {seg.description}")

        return ToolResult(True, "list_segments", "\n".join(lines))


# ── Factory ──

def create_broadcast_engine(
    config: dict[str, Any],
    contact_store: Any = None,
    channel: Any = None,
    memory_store: Any = None,
    knowledge_graph: Any = None,
    http_client: Any = None,
) -> tuple[BroadcastEngine, CampaignStore]:
    """Factory to create a fully wired BroadcastEngine.

    Returns (engine, store) tuple. Store is returned separately for
    direct access in admin commands and heartbeat cleanup.
    """
    from src.config_manager import get_config_dir

    db_path = get_config_dir() / "broadcast.db"
    store = CampaignStore(db_path)

    segmentation = SegmentationEngine(contact_store) if contact_store else None

    engine = BroadcastEngine(
        store=store,
        segmentation=segmentation,
        config=config,
        channel=channel,
        contact_store=contact_store,
        memory_store=memory_store,
        knowledge_graph=knowledge_graph,
        http_client=http_client,
    )

    return engine, store
