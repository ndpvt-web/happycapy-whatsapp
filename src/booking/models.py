"""Booking data models — the essence of a booking.

Aristotle's material cause: what a booking IS, separate from how it flows.

A booking's essence: a bi-party agreement on a time slot, with committed
resources (calendar event, email confirmation, WhatsApp message).

Accidents (contingent properties): which channel, which calendar provider,
which email client. These live in intents.py and are swappable.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class BookingState(str, Enum):
    """All valid states for a booking lifecycle."""
    IDLE                = "IDLE"                # Initial: contact expressed interest
    CALENDAR_FETCHED    = "CALENDAR_FETCHED"    # Free slots retrieved from calendar
    TIMES_PROPOSED      = "TIMES_PROPOSED"      # Slots sent to contact
    CONTACT_REPLIED     = "CONTACT_REPLIED"     # Contact picked a slot
    AWAITING_OWNER      = "AWAITING_OWNER"      # Owner notified, waiting for approval
    BOOKING_CONFIRMED   = "BOOKING_CONFIRMED"   # Owner approved, all resources committed
    REJECTED            = "REJECTED"            # Owner rejected → can re-enter
    RESCHEDULE_PENDING  = "RESCHEDULE_PENDING"  # Contact asked to move confirmed slot
    EXPIRED             = "EXPIRED"             # TTL hit, no contact reply
    CANCELLED           = "CANCELLED"           # Contact cancelled after confirmation
    COMPLETE            = "COMPLETE"            # Meeting happened, brief sent


# All valid (state, event) → new_state transitions.
# Any transition NOT in this map is physically impossible.
VALID_TRANSITIONS: dict[tuple[BookingState, str], BookingState] = {
    (BookingState.IDLE,               "calendar_checked"):       BookingState.CALENDAR_FETCHED,
    (BookingState.CALENDAR_FETCHED,   "times_proposed"):         BookingState.TIMES_PROPOSED,
    (BookingState.TIMES_PROPOSED,     "contact_replied"):        BookingState.CONTACT_REPLIED,
    (BookingState.TIMES_PROPOSED,     "ttl_expired"):            BookingState.EXPIRED,
    (BookingState.CONTACT_REPLIED,    "owner_notified"):         BookingState.AWAITING_OWNER,
    (BookingState.AWAITING_OWNER,     "owner_approved"):         BookingState.BOOKING_CONFIRMED,
    (BookingState.AWAITING_OWNER,     "owner_rejected"):         BookingState.REJECTED,
    # After rejection, owner can re-propose → back to CALENDAR_FETCHED
    (BookingState.REJECTED,           "calendar_checked"):       BookingState.CALENDAR_FETCHED,
    # Confirmed bookings can be cancelled or rescheduled by contact
    (BookingState.BOOKING_CONFIRMED,  "contact_cancelled"):      BookingState.CANCELLED,
    (BookingState.BOOKING_CONFIRMED,  "reschedule_requested"):   BookingState.RESCHEDULE_PENDING,
    # Reschedule loops back into the proposal flow
    (BookingState.RESCHEDULE_PENDING, "calendar_checked"):       BookingState.CALENDAR_FETCHED,
    # Proactive engine advances to COMPLETE after brief is sent
    (BookingState.BOOKING_CONFIRMED,  "meeting_complete"):       BookingState.COMPLETE,
}

# Terminal states — no further transitions possible
TERMINAL_STATES = {BookingState.EXPIRED, BookingState.CANCELLED, BookingState.COMPLETE}


class BookingStateError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class BookingIntent(str, Enum):
    """Transport-agnostic intents emitted by the booking engine.

    The WhatsApp layer (intents.py) subscribes and fulfills these.
    Swapping to email or Slack means writing a new fulfillment layer,
    not touching the booking engine.
    """
    SEND_TIMES_TO_CONTACT   = "send_times_to_contact"
    NOTIFY_OWNER            = "notify_owner_for_approval"
    SEND_CONFIRMATION       = "send_booking_confirmation"
    SEND_REJECTION          = "send_rejection_to_contact"
    SEND_BRIEF              = "send_pre_meeting_brief"
    SEND_EXPIRY_NOTICE      = "send_expiry_notice_to_owner"
    SEND_CANCELLATION_ACK   = "send_cancellation_acknowledgment"


@dataclass
class TimeSlot:
    """A single candidate time slot."""
    start: str          # ISO 8601 with timezone, e.g. "2026-04-18T14:00:00+05:30"
    end: str
    label: str = ""     # Human-readable, e.g. "Friday 2pm–2:30pm"


@dataclass
class AuditEntry:
    """One state transition in the audit log."""
    from_state: str
    event: str
    to_state: str
    actor: str          # "llm" | "contact" | "owner" | "proactive_engine" | "system"
    ts: str             # ISO datetime
    metadata: dict = field(default_factory=dict)


@dataclass
class BookingRecord:
    """The complete booking record — persisted to SQLite.

    Essence (what it IS):
      contact_id + time_slot + duration + purpose

    Accidents (how it manifests):
      channel, calendar provider, email client → handled by intents.py
    """
    id: str                                 # "BKG-001", "BKG-002", ...
    contact_id: str                         # phone number (digits only)
    contact_name: str
    purpose: str                            # "wants to discuss partnership"
    state: BookingState
    created_at: str                         # ISO datetime

    # Proposed slots (set after CALENDAR_FETCHED)
    proposed_slots: list[dict] = field(default_factory=list)   # list of TimeSlot dicts

    # Contact's chosen slot (set after CONTACT_REPLIED)
    contact_choice: dict | None = None      # TimeSlot dict

    # Owner feedback (set after REJECTED)
    owner_rejected_reason: str = ""

    # Proactive engine flags
    brief_sent: bool = False
    brief_scheduled_for: str = ""          # ISO datetime

    # TTL: expire if contact doesn't reply within this many hours
    ttl_hours: int = 48

    # Full audit trail: every state transition recorded
    audit_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "contact_id": self.contact_id,
            "contact_name": self.contact_name,
            "purpose": self.purpose,
            "state": self.state.value,
            "created_at": self.created_at,
            "proposed_slots": self.proposed_slots,
            "contact_choice": self.contact_choice,
            "owner_rejected_reason": self.owner_rejected_reason,
            "brief_sent": self.brief_sent,
            "brief_scheduled_for": self.brief_scheduled_for,
            "ttl_hours": self.ttl_hours,
            "audit_log": self.audit_log,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BookingRecord":
        return cls(
            id=d["id"],
            contact_id=d["contact_id"],
            contact_name=d.get("contact_name", ""),
            purpose=d.get("purpose", ""),
            state=BookingState(d["state"]),
            created_at=d["created_at"],
            proposed_slots=d.get("proposed_slots", []),
            contact_choice=d.get("contact_choice"),
            owner_rejected_reason=d.get("owner_rejected_reason", ""),
            brief_sent=d.get("brief_sent", False),
            brief_scheduled_for=d.get("brief_scheduled_for", ""),
            ttl_hours=d.get("ttl_hours", 48),
            audit_log=d.get("audit_log", []),
        )


@dataclass
class OwnerContext:
    """Rich context bundle for owner notification.

    Assembled by ContextAggregator before emitting NOTIFY_OWNER intent.
    """
    contact_name: str
    contact_id: str
    purpose: str
    chosen_slot: dict | None
    email_threads: list[dict] = field(default_factory=list)
    whatsapp_history: str = ""
    zoom_summary: str = ""
    web_search_results: list[dict] = field(default_factory=list)


@dataclass
class BriefContext:
    """Rich context bundle for pre-meeting brief.

    Assembled by ContextAggregator 60 minutes before the meeting.
    """
    contact_name: str
    contact_id: str
    purpose: str
    meeting_time: str
    email_threads: list[dict] = field(default_factory=list)
    whatsapp_history: str = ""
    zoom_summary: str = ""
    web_search_results: list[dict] = field(default_factory=list)
