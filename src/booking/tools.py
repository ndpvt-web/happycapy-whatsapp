"""Booking Tools — the LLM's gated interface to the booking engine.

Each tool has a REQUIRED_STATE precondition. If the booking is not in that
state, the tool rejects with a clear error message. The LLM physically
cannot skip steps or take wrong actions.

Tool surface by state:
  IDLE                 → check_meeting_availability (only)
  CALENDAR_FETCHED     → propose_meeting_times (only)
  CONTACT_REPLIED      → notify_owner_meeting_request (only)
  AWAITING_OWNER       → (no LLM tools — system waits for owner reply)
  REJECTED             → check_meeting_availability (re-enter loop)

The LLM never sees state — it only sees which tools are available.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from src.tool_executor import ToolResult

GWS_BIN = os.path.expanduser("~/.cargo/bin/gws")
GWS_TIMEOUT = 30


# ── OpenAI-format tool definitions exposed to the LLM ──

BOOKING_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "check_meeting_availability",
            "description": (
                "Check the owner's calendar for available meeting slots. "
                "Call this FIRST when someone requests a meeting. "
                "Returns free slots that can be proposed to the contact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_id": {
                        "type": "string",
                        "description": "Phone number of the contact requesting the meeting (digits only).",
                    },
                    "contact_name": {
                        "type": "string",
                        "description": "Name of the contact.",
                    },
                    "purpose": {
                        "type": "string",
                        "description": "What the meeting is about (from the contact's message).",
                    },
                    "preferred_days_ahead": {
                        "type": "integer",
                        "description": "How many days ahead to check for availability. Default 7.",
                        "default": 7,
                    },
                },
                "required": ["contact_id", "purpose"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_meeting_times",
            "description": (
                "Send available meeting slots to the contact for them to choose. "
                "Only call AFTER check_meeting_availability has been called. "
                "The system will message the contact with the options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {
                        "type": "string",
                        "description": "The booking ID returned by check_meeting_availability.",
                    },
                    "message_to_contact": {
                        "type": "string",
                        "description": (
                            "Friendly message to send to the contact with the available slots. "
                            "Include the slot options clearly numbered."
                        ),
                    },
                },
                "required": ["booking_id", "message_to_contact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_owner_meeting_request",
            "description": (
                "Notify the owner that a contact has chosen a meeting slot and needs approval. "
                "Only call AFTER the contact has replied with their slot choice. "
                "The system will gather full context (email history, chat history, etc.) "
                "and present it to the owner for approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {
                        "type": "string",
                        "description": "The booking ID.",
                    },
                },
                "required": ["booking_id"],
            },
        },
    },
]


class BookingTools:
    """Executes booking tool calls. Gate-checks state before every action.

    Implements the integration protocol so it can be registered with ToolExecutor:
      - set_request_context(sender_jid, chat_id)
      - execute(tool_name, arguments) -> ToolResult-compatible dict
    """

    def set_request_context(self, sender_jid: str = "", chat_id: str = "") -> None:
        """Called by ToolExecutor before each tool call. No-op for booking tools."""
        pass

    def __init__(self, engine, context_aggregator, channel=None):
        """
        engine: BookingEngine instance
        context_aggregator: ContextAggregator instance
        channel: WhatsAppChannel (for send_text, optional — intents layer handles delivery)
        """
        self._engine = engine
        self._ctx = context_aggregator
        self._channel = channel

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        """Dispatch tool call. Returns ToolResult for ToolExecutor compatibility."""
        if tool_name == "check_meeting_availability":
            r = await self._check_availability(arguments)
        elif tool_name == "propose_meeting_times":
            r = await self._propose_times(arguments)
        elif tool_name == "notify_owner_meeting_request":
            r = await self._notify_owner(arguments)
        else:
            r = {"success": False, "result": f"Unknown booking tool: {tool_name}"}
        return ToolResult(
            success=r["success"],
            tool_name=tool_name,
            content=r["result"],
        )

    # ── Tool 1: check_meeting_availability ──

    async def _check_availability(self, args: dict) -> dict:
        """Check calendar and create/update booking record.

        State gate: IDLE or REJECTED or RESCHEDULE_PENDING (create/re-enter)
        Effect: CALENDAR_FETCHED
        """
        contact_id = args.get("contact_id", "").strip().replace("+", "").replace(" ", "")
        contact_name = args.get("contact_name", contact_id)
        purpose = args.get("purpose", "meeting")
        days_ahead = min(int(args.get("preferred_days_ahead", 7)), 14)

        if not contact_id:
            return {"success": False, "result": "contact_id is required."}

        # Get or create booking
        booking = self._engine.get_active_for_contact(contact_id)
        if booking is None:
            booking = self._engine.create(contact_id, contact_name, purpose)

        # State gate: must be in a state that allows calendar check
        from .models import BookingState, BookingStateError
        allowed = {BookingState.IDLE, BookingState.REJECTED, BookingState.RESCHEDULE_PENDING}
        if booking.state not in allowed:
            return {
                "success": False,
                "result": (
                    f"Cannot check calendar: booking {booking.id} is in state "
                    f"{booking.state.value}. "
                    f"{'Contact has not yet replied with their choice.' if booking.state == BookingState.TIMES_PROPOSED else ''}"
                    f"{'Waiting for owner approval.' if booking.state == BookingState.AWAITING_OWNER else ''}"
                ),
            }

        # Fetch free slots from Google Calendar
        slots = await self._fetch_free_slots(days_ahead)
        if not slots:
            return {
                "success": False,
                "result": "No available slots found in the calendar for the next "
                          f"{days_ahead} days. Try asking about a longer window or "
                          "check the calendar manually.",
            }

        await self._engine.calendar_checked(booking, slots)

        # Format slots for LLM to present to contact
        slot_lines = []
        for i, s in enumerate(slots[:5], 1):
            slot_lines.append(f"  {i}. {s['label']} ({s['start']})")

        return {
            "success": True,
            "result": (
                f"Booking {booking.id} created. Calendar checked. "
                f"Found {len(slots)} available slots:\n"
                + "\n".join(slot_lines)
                + f"\n\nNow call propose_meeting_times with booking_id={booking.id} "
                f"to send these options to the contact."
            ),
            "booking_id": booking.id,
            "slots": slots,
        }

    # ── Tool 2: propose_meeting_times ──

    async def _propose_times(self, args: dict) -> dict:
        """Record that slots were proposed to the contact.

        State gate: CALENDAR_FETCHED
        Effect: TIMES_PROPOSED
        Emits: SEND_TIMES_TO_CONTACT intent
        """
        booking_id = args.get("booking_id", "").strip().upper()
        message = args.get("message_to_contact", "").strip()

        if not booking_id:
            return {"success": False, "result": "booking_id is required."}
        if not message:
            return {"success": False, "result": "message_to_contact is required."}

        booking = self._engine.get(booking_id)
        if not booking:
            return {"success": False, "result": f"Booking {booking_id} not found."}

        from .models import BookingState
        if booking.state != BookingState.CALENDAR_FETCHED:
            return {
                "success": False,
                "result": (
                    f"Cannot propose times: booking {booking_id} is in state "
                    f"{booking.state.value}. "
                    f"You must call check_meeting_availability first."
                    if booking.state == BookingState.IDLE
                    else f"Booking is already in state {booking.state.value}."
                ),
            }

        # Emit intent with the message payload — WhatsApp layer sends it
        await self._engine.times_proposed(booking, context={"message": message})

        return {
            "success": True,
            "result": (
                f"Booking {booking_id}: time slots sent to {booking.contact_name}. "
                f"Now waiting for them to reply with their choice. "
                f"When they reply, the system will automatically route their response "
                f"and you'll be prompted to call notify_owner_meeting_request."
            ),
        }

    # ── Tool 3: notify_owner_meeting_request ──

    async def _notify_owner(self, args: dict) -> dict:
        """Notify owner with full context bundle.

        State gate: CONTACT_REPLIED
        Effect: AWAITING_OWNER
        Emits: NOTIFY_OWNER intent (with OwnerContext)
        """
        booking_id = args.get("booking_id", "").strip().upper()

        if not booking_id:
            return {"success": False, "result": "booking_id is required."}

        booking = self._engine.get(booking_id)
        if not booking:
            return {"success": False, "result": f"Booking {booking_id} not found."}

        from .models import BookingState
        if booking.state != BookingState.CONTACT_REPLIED:
            return {
                "success": False,
                "result": (
                    f"Cannot notify owner: booking {booking_id} is in state "
                    f"{booking.state.value}. "
                    f"The contact must first reply with their slot choice."
                ),
            }

        # Assemble full context (this is the expensive I/O call)
        owner_ctx = await self._ctx.for_owner_notification(booking)
        await self._engine.notify_owner(booking, context=owner_ctx)

        return {
            "success": True,
            "result": (
                f"Booking {booking_id}: owner notified with full context "
                f"(email history, WhatsApp history, web search, Zoom summaries). "
                f"Waiting for owner to approve or reject."
            ),
        }

    # ── Calendar helper ──

    async def _fetch_free_slots(self, days_ahead: int) -> list[dict]:
        """Call gws to get free slots from Google Calendar."""
        if not os.path.exists(GWS_BIN):
            # Fallback: return sample slots (development mode)
            return _sample_slots(days_ahead)
        try:
            proc = await asyncio.create_subprocess_exec(
                GWS_BIN, "calendar", "free-slots",
                "--days", str(days_ahead),
                "--duration", "30",   # 30-min slots by default
                "--format", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=GWS_TIMEOUT
            )
            if not stdout:
                return []
            data = json.loads(stdout.decode())
            slots = data if isinstance(data, list) else data.get("slots", [])
            # Normalize to our TimeSlot shape
            result = []
            for s in slots[:8]:  # max 8 slots
                result.append({
                    "start": s.get("start", ""),
                    "end": s.get("end", ""),
                    "label": s.get("label", s.get("start", "")),
                })
            return result
        except Exception as e:
            print(f"[booking/tools] fetch_free_slots error: {e}")
            return []


def _sample_slots(days_ahead: int) -> list[dict]:
    """Development fallback: return plausible-looking slots."""
    from datetime import datetime, timedelta, timezone
    base = datetime.now(tz=timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    slots = []
    for i in range(1, min(days_ahead, 4) + 1):
        day = base + timedelta(days=i)
        if day.weekday() < 5:  # weekday only
            start = day.isoformat()
            end = (day + timedelta(minutes=30)).isoformat()
            slots.append({
                "start": start,
                "end": end,
                "label": day.strftime("%A %b %d, %I:%M %p"),
            })
    return slots
