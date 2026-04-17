"""Booking Engine — the spine of the booking system.

State machine with SQLite persistence. Every transition is validated
against VALID_TRANSITIONS — invalid moves raise BookingStateError.

The engine emits BookingIntents (transport-agnostic). The WhatsApp
layer (intents.py) subscribes and fulfills them.

Event bus pattern: callers register listeners via on_intent() and
on_state_change(). The engine never calls WhatsApp directly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable, Any

from .models import (
    BookingRecord,
    BookingState,
    BookingIntent,
    BookingStateError,
    VALID_TRANSITIONS,
    TERMINAL_STATES,
)


class BookingEngine:
    """Constraint-based booking lifecycle manager."""

    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._intent_listeners: list[Callable] = []
        self._state_listeners: list[Callable] = []
        self._init_db()

    # ── Database ──

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY,
                contact_id TEXT NOT NULL,
                contact_name TEXT DEFAULT '',
                purpose TEXT DEFAULT '',
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                proposed_slots TEXT DEFAULT '[]',
                contact_choice TEXT,
                owner_rejected_reason TEXT DEFAULT '',
                brief_sent INTEGER DEFAULT 0,
                brief_scheduled_for TEXT DEFAULT '',
                ttl_hours INTEGER DEFAULT 48,
                audit_log TEXT DEFAULT '[]'
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bkg_contact ON bookings(contact_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bkg_state ON bookings(state)"
        )
        self._conn.commit()

    def _next_id(self) -> str:
        cursor = self._conn.execute("SELECT COUNT(*) as cnt FROM bookings")
        row = cursor.fetchone()
        return f"BKG-{(row['cnt'] or 0) + 1:03d}"

    def _save(self, booking: BookingRecord) -> None:
        d = booking.to_dict()
        self._conn.execute("""
            INSERT OR REPLACE INTO bookings
            (id, contact_id, contact_name, purpose, state, created_at,
             proposed_slots, contact_choice, owner_rejected_reason,
             brief_sent, brief_scheduled_for, ttl_hours, audit_log)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            d["id"], d["contact_id"], d["contact_name"], d["purpose"],
            d["state"], d["created_at"],
            json.dumps(d["proposed_slots"]),
            json.dumps(d["contact_choice"]) if d["contact_choice"] else None,
            d["owner_rejected_reason"],
            int(d["brief_sent"]),
            d["brief_scheduled_for"],
            d["ttl_hours"],
            json.dumps(d["audit_log"]),
        ))
        self._conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> BookingRecord:
        d = dict(row)
        d["proposed_slots"] = json.loads(d["proposed_slots"] or "[]")
        d["contact_choice"] = json.loads(d["contact_choice"]) if d.get("contact_choice") else None
        d["brief_sent"] = bool(d["brief_sent"])
        d["audit_log"] = json.loads(d["audit_log"] or "[]")
        return BookingRecord.from_dict(d)

    # ── Event Bus ──

    def on_intent(self, listener: Callable) -> None:
        """Register a listener for BookingIntents.
        Signature: async def listener(intent, booking, context) -> None
        """
        self._intent_listeners.append(listener)

    def on_state_change(self, listener: Callable) -> None:
        """Register a listener for state transitions.
        Signature: async def listener(booking, new_state) -> None
        """
        self._state_listeners.append(listener)

    async def _emit_intent(
        self, intent: BookingIntent, booking: BookingRecord, context: Any = None
    ) -> None:
        for listener in self._intent_listeners:
            try:
                await listener(intent, booking, context)
            except Exception as e:
                print(f"[booking] Intent listener error ({intent}): {e}")

    async def _notify_state_change(
        self, booking: BookingRecord, new_state: BookingState
    ) -> None:
        for listener in self._state_listeners:
            try:
                await listener(booking, new_state)
            except Exception as e:
                print(f"[booking] State listener error ({new_state}): {e}")

    # ── State Machine ──

    def _advance_state(
        self,
        booking: BookingRecord,
        event: str,
        actor: str,
        metadata: dict | None = None,
    ) -> BookingState:
        """Validate and apply a state transition. Raises BookingStateError if invalid.

        Every transition is logged to the audit trail regardless of outcome.
        """
        key = (booking.state, event)
        if key not in VALID_TRANSITIONS:
            raise BookingStateError(
                f"Invalid transition: state={booking.state.value} event={event} "
                f"booking={booking.id}"
            )
        new_state = VALID_TRANSITIONS[key]
        booking.audit_log.append({
            "from": booking.state.value,
            "event": event,
            "to": new_state.value,
            "actor": actor,
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            **(metadata or {}),
        })
        booking.state = new_state
        return new_state

    # ── Public API — called by booking tools ──

    def create(
        self,
        contact_id: str,
        contact_name: str,
        purpose: str,
    ) -> BookingRecord:
        """Create a new booking in IDLE state."""
        booking = BookingRecord(
            id=self._next_id(),
            contact_id=contact_id,
            contact_name=contact_name or contact_id,
            purpose=purpose,
            state=BookingState.IDLE,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        self._save(booking)
        print(f"[booking] Created {booking.id} for {contact_name or contact_id}")
        return booking

    async def calendar_checked(
        self,
        booking: BookingRecord,
        slots: list[dict],
    ) -> BookingRecord:
        """Record that calendar was checked and slots were found.

        Advances: IDLE / REJECTED / RESCHEDULE_PENDING → CALENDAR_FETCHED
        """
        new_state = self._advance_state(
            booking, "calendar_checked", "llm", {"slot_count": len(slots)}
        )
        booking.proposed_slots = slots
        self._save(booking)
        await self._notify_state_change(booking, new_state)
        print(f"[booking] {booking.id} calendar_checked → {new_state.value} ({len(slots)} slots)")
        return booking

    async def times_proposed(
        self,
        booking: BookingRecord,
        context: Any = None,
    ) -> BookingRecord:
        """Record that time slots were sent to the contact.

        Advances: CALENDAR_FETCHED → TIMES_PROPOSED
        Emits: SEND_TIMES_TO_CONTACT intent
        """
        new_state = self._advance_state(booking, "times_proposed", "llm")
        self._save(booking)
        await self._notify_state_change(booking, new_state)
        await self._emit_intent(BookingIntent.SEND_TIMES_TO_CONTACT, booking, context)
        print(f"[booking] {booking.id} times_proposed → {new_state.value}")
        return booking

    async def contact_replied(
        self,
        booking: BookingRecord,
        chosen_slot: dict,
    ) -> BookingRecord:
        """Record that contact chose a slot.

        Advances: TIMES_PROPOSED → CONTACT_REPLIED
        Triggered by: inbound message routing in main.py (not LLM)
        """
        new_state = self._advance_state(
            booking, "contact_replied", "contact", {"slot": chosen_slot}
        )
        booking.contact_choice = chosen_slot
        self._save(booking)
        await self._notify_state_change(booking, new_state)
        print(f"[booking] {booking.id} contact_replied → {new_state.value}")
        return booking

    async def notify_owner(
        self,
        booking: BookingRecord,
        context: Any = None,
    ) -> BookingRecord:
        """Notify owner for approval with full context bundle.

        Advances: CONTACT_REPLIED → AWAITING_OWNER
        Emits: NOTIFY_OWNER intent (with OwnerContext)
        """
        new_state = self._advance_state(booking, "owner_notified", "llm")
        self._save(booking)
        await self._notify_state_change(booking, new_state)
        await self._emit_intent(BookingIntent.NOTIFY_OWNER, booking, context)
        print(f"[booking] {booking.id} owner_notified → {new_state.value}")
        return booking

    async def owner_approved(
        self,
        booking: BookingRecord,
        calendar_event_id: str = "",
    ) -> BookingRecord:
        """Owner approved. Atomically: create calendar + send email + confirm contact.

        Advances: AWAITING_OWNER → BOOKING_CONFIRMED
        Emits: SEND_CONFIRMATION intent
        Triggered by: admin reply routing in main.py (not LLM)
        """
        new_state = self._advance_state(
            booking, "owner_approved", "owner",
            {"calendar_event_id": calendar_event_id}
        )
        self._save(booking)
        await self._notify_state_change(booking, new_state)
        await self._emit_intent(BookingIntent.SEND_CONFIRMATION, booking)
        print(f"[booking] {booking.id} owner_approved → {new_state.value}")
        return booking

    async def owner_rejected(
        self,
        booking: BookingRecord,
        reason: str = "",
    ) -> BookingRecord:
        """Owner rejected the booking request.

        Advances: AWAITING_OWNER → REJECTED
        Emits: SEND_REJECTION intent
        """
        new_state = self._advance_state(
            booking, "owner_rejected", "owner", {"reason": reason}
        )
        booking.owner_rejected_reason = reason
        self._save(booking)
        await self._notify_state_change(booking, new_state)
        await self._emit_intent(BookingIntent.SEND_REJECTION, booking)
        print(f"[booking] {booking.id} owner_rejected → {new_state.value}")
        return booking

    async def expire(self, booking: BookingRecord) -> BookingRecord:
        """TTL expired — contact never replied.

        Advances: TIMES_PROPOSED → EXPIRED
        Emits: SEND_EXPIRY_NOTICE intent to owner
        """
        new_state = self._advance_state(booking, "ttl_expired", "system")
        self._save(booking)
        await self._notify_state_change(booking, new_state)
        await self._emit_intent(BookingIntent.SEND_EXPIRY_NOTICE, booking)
        print(f"[booking] {booking.id} ttl_expired → {new_state.value}")
        return booking

    async def contact_cancelled(self, booking: BookingRecord) -> BookingRecord:
        """Contact cancelled a confirmed booking.

        Advances: BOOKING_CONFIRMED → CANCELLED
        Emits: SEND_CANCELLATION_ACK intent
        """
        new_state = self._advance_state(booking, "contact_cancelled", "contact")
        self._save(booking)
        await self._notify_state_change(booking, new_state)
        await self._emit_intent(BookingIntent.SEND_CANCELLATION_ACK, booking)
        print(f"[booking] {booking.id} contact_cancelled → {new_state.value}")
        return booking

    async def reschedule_requested(self, booking: BookingRecord) -> BookingRecord:
        """Contact asked to reschedule a confirmed booking.

        Advances: BOOKING_CONFIRMED → RESCHEDULE_PENDING
        (proactive_engine or llm then calls calendar_checked to re-enter flow)
        """
        new_state = self._advance_state(booking, "reschedule_requested", "contact")
        self._save(booking)
        await self._notify_state_change(booking, new_state)
        print(f"[booking] {booking.id} reschedule_requested → {new_state.value}")
        return booking

    async def mark_complete(self, booking: BookingRecord) -> BookingRecord:
        """Meeting happened and brief was sent.

        Advances: BOOKING_CONFIRMED → COMPLETE
        """
        new_state = self._advance_state(booking, "meeting_complete", "proactive_engine")
        booking.brief_sent = True
        self._save(booking)
        await self._notify_state_change(booking, new_state)
        print(f"[booking] {booking.id} meeting_complete → {new_state.value}")
        return booking

    # ── Query API ──

    def get(self, booking_id: str) -> BookingRecord | None:
        cursor = self._conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (booking_id.upper(),)
        )
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def get_for_contact(self, contact_id: str) -> list[BookingRecord]:
        """Get all non-terminal bookings for a contact (newest first)."""
        terminal = [s.value for s in TERMINAL_STATES]
        placeholders = ",".join("?" * len(terminal))
        cursor = self._conn.execute(
            f"SELECT * FROM bookings WHERE contact_id = ? AND state NOT IN ({placeholders}) "
            f"ORDER BY created_at DESC",
            (contact_id, *terminal),
        )
        return [self._row_to_record(r) for r in cursor.fetchall()]

    def get_active_for_contact(self, contact_id: str) -> BookingRecord | None:
        """Get the most recent active (non-terminal) booking for a contact."""
        bookings = self.get_for_contact(contact_id)
        return bookings[0] if bookings else None

    def get_awaiting_owner_approval(self) -> list[BookingRecord]:
        """All bookings waiting for owner to approve/reject."""
        cursor = self._conn.execute(
            "SELECT * FROM bookings WHERE state = ? ORDER BY created_at ASC",
            (BookingState.AWAITING_OWNER.value,),
        )
        return [self._row_to_record(r) for r in cursor.fetchall()]

    def get_confirmed_upcoming(self) -> list[BookingRecord]:
        """Confirmed bookings that haven't had their brief sent yet."""
        cursor = self._conn.execute(
            "SELECT * FROM bookings WHERE state = ? AND brief_sent = 0 ORDER BY created_at ASC",
            (BookingState.BOOKING_CONFIRMED.value,),
        )
        return [self._row_to_record(r) for r in cursor.fetchall()]

    def get_awaiting_contact_reply(self) -> list[BookingRecord]:
        """Bookings waiting for contact to pick a slot (for TTL checks)."""
        cursor = self._conn.execute(
            "SELECT * FROM bookings WHERE state = ? ORDER BY created_at ASC",
            (BookingState.TIMES_PROPOSED.value,),
        )
        return [self._row_to_record(r) for r in cursor.fetchall()]

    def set_brief_scheduled(self, booking_id: str, scheduled_for: str) -> None:
        self._conn.execute(
            "UPDATE bookings SET brief_scheduled_for = ? WHERE id = ?",
            (scheduled_for, booking_id),
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
