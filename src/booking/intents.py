"""Booking Intent Fulfillment — the WhatsApp transport layer.

Aristotle's accident vs essence: how the booking manifests on a specific channel.

The booking engine emits BookingIntents (transport-agnostic).
This module subscribes and fulfills them via WhatsApp.

To add a new channel (email, Telegram, Slack): write a new fulfillment
module with the same on_intent() signature. The booking engine never changes.

Also handles inbound routing:
  - Admin replies to AWAITING_OWNER bookings → owner_approved / owner_rejected
  - Contact replies to TIMES_PROPOSED bookings → contact_replied
"""

from __future__ import annotations

import re
from typing import Any

from .models import BookingRecord, BookingIntent, BookingState
from .context import format_owner_context


# Pattern to detect booking approval in admin messages
_APPROVE_RE = re.compile(r"\b(yes|approve|confirm|ok|sure|go ahead|book it)\b", re.IGNORECASE)
_REJECT_RE = re.compile(r"\b(no|reject|decline|cancel|not now|don't book)\b", re.IGNORECASE)
_SLOT_RE = re.compile(r"\b([1-5])\b")  # Contact picks slot 1-5


class BookingIntentFulfiller:
    """Fulfills BookingIntents via WhatsApp channel.

    Register as an intent listener on BookingEngine:
      engine.on_intent(fulfiller.on_intent)
    """

    def __init__(self, engine, config: dict, channel=None):
        self._engine = engine
        self._config = config
        self._channel = channel  # WhatsAppChannel

    async def on_intent(
        self,
        intent: BookingIntent,
        booking: BookingRecord,
        context: Any = None,
    ) -> None:
        """Route intent to the appropriate fulfillment method."""
        try:
            if intent == BookingIntent.SEND_TIMES_TO_CONTACT:
                await self._send_times_to_contact(booking, context)
            elif intent == BookingIntent.NOTIFY_OWNER:
                await self._notify_owner(booking, context)
            elif intent == BookingIntent.SEND_CONFIRMATION:
                await self._send_confirmation(booking)
            elif intent == BookingIntent.SEND_REJECTION:
                await self._send_rejection(booking)
            elif intent == BookingIntent.SEND_BRIEF:
                pass  # Handled by proactive.py directly
            elif intent == BookingIntent.SEND_EXPIRY_NOTICE:
                await self._send_expiry_notice(booking)
            elif intent == BookingIntent.SEND_CANCELLATION_ACK:
                await self._send_cancellation_ack(booking)
        except Exception as e:
            print(f"[booking/intents] Intent fulfillment error ({intent}): {e}")

    # ── Intent Fulfillment ──

    async def _send_times_to_contact(
        self, booking: BookingRecord, context: Any
    ) -> None:
        """Send the slot options to the contact."""
        if not self._channel:
            return

        # context carries the LLM-generated message with slots listed
        message = ""
        if isinstance(context, dict):
            message = context.get("message", "")

        if not message:
            # Fallback: generate from slots
            lines = [f"Hi {booking.contact_name}! Here are some available times:"]
            for i, s in enumerate(booking.proposed_slots[:5], 1):
                label = s.get("label", s.get("start", ""))
                lines.append(f"{i}. {label}")
            lines.append("\nJust reply with the number of your preferred slot!")
            message = "\n".join(lines)

        contact_jid = _to_jid(booking.contact_id)
        await self._channel.send_text(contact_jid, message)
        print(f"[booking/intents] Sent times to {booking.contact_name}")

    async def _notify_owner(
        self, booking: BookingRecord, context: Any
    ) -> None:
        """Send owner notification with full context."""
        if not self._channel:
            return

        owner_number = self._config.get("escalation_notify_number") or \
                       self._config.get("admin_number", "")
        if not owner_number:
            return

        # Format context block
        ctx_text = ""
        if context:
            try:
                ctx_text = format_owner_context(context)
            except Exception:
                ctx_text = str(context)[:300]

        chosen_label = ""
        if booking.contact_choice:
            chosen_label = booking.contact_choice.get("label") or \
                           booking.contact_choice.get("start", "")

        msg = (
            f"*[{booking.id}] Meeting Request*\n\n"
            f"{ctx_text}\n\n"
            f"Chosen slot: *{chosen_label}*\n\n"
            f"Reply *yes* to confirm or *no [reason]* to reject."
        )

        owner_jid = _to_jid(owner_number)
        await self._channel.send_text(owner_jid, msg)
        print(f"[booking/intents] Owner notified for {booking.id}")

    async def _send_confirmation(self, booking: BookingRecord) -> None:
        """Confirm the booking to the contact."""
        if not self._channel:
            return

        chosen_label = ""
        if booking.contact_choice:
            chosen_label = booking.contact_choice.get("label") or \
                           booking.contact_choice.get("start", "")

        msg = (
            f"Great news! Your meeting has been confirmed for *{chosen_label}*.\n"
            f"You'll receive a calendar invite shortly. Looking forward to it!"
        )
        contact_jid = _to_jid(booking.contact_id)
        await self._channel.send_text(contact_jid, msg)
        print(f"[booking/intents] Confirmation sent for {booking.id}")

    async def _send_rejection(self, booking: BookingRecord) -> None:
        """Notify contact that the meeting request was not confirmed."""
        if not self._channel:
            return

        reason = booking.owner_rejected_reason
        if reason:
            msg = f"Thanks for reaching out! Unfortunately I'm not able to meet at that time. {reason}"
        else:
            msg = (
                "Thanks for reaching out! Unfortunately I'm not available for a meeting "
                "right now. I'll get back to you to reschedule soon."
            )

        contact_jid = _to_jid(booking.contact_id)
        await self._channel.send_text(contact_jid, msg)
        print(f"[booking/intents] Rejection sent for {booking.id}")

    async def _send_expiry_notice(self, booking: BookingRecord) -> None:
        """Notify owner that a booking expired (contact never replied)."""
        if not self._channel:
            return

        owner_number = self._config.get("escalation_notify_number") or \
                       self._config.get("admin_number", "")
        if not owner_number:
            return

        msg = (
            f"*[{booking.id}] Meeting request expired*\n"
            f"{booking.contact_name} ({booking.contact_id}) didn't reply to the "
            f"slot options within {booking.ttl_hours} hours. Booking archived."
        )
        owner_jid = _to_jid(owner_number)
        await self._channel.send_text(owner_jid, msg)

    async def _send_cancellation_ack(self, booking: BookingRecord) -> None:
        """Acknowledge cancellation to contact."""
        if not self._channel:
            return

        msg = "Your meeting request has been cancelled. Feel free to reach out whenever you'd like to reschedule!"
        contact_jid = _to_jid(booking.contact_id)
        await self._channel.send_text(contact_jid, msg)

    # ── Inbound Routing ──

    async def route_admin_reply(
        self, content: str, sender_id: str
    ) -> bool:
        """Try to route an admin reply to a pending booking approval.

        Returns True if handled (caller should not process further).
        """
        # Find bookings awaiting owner approval
        pending = self._engine.get_awaiting_owner_approval()
        if not pending:
            return False

        # If admin explicitly references a booking ID, use that
        bkg_match = re.search(r"\[?(BKG-\d+)\]?", content, re.IGNORECASE)
        if bkg_match:
            booking_id = bkg_match.group(1).upper()
            booking = next((b for b in pending if b.id == booking_id), None)
        else:
            # Use the oldest pending booking (FIFO)
            booking = pending[0] if len(pending) == 1 else None

        if not booking:
            return False

        content_lower = content.strip().lower()

        if _REJECT_RE.search(content_lower):
            # Extract reason after "no"
            reason = re.sub(r"^(no|reject|decline|cancel|not now|don't book)[,\s]*", "", content_lower, flags=re.IGNORECASE).strip()
            await self._engine.owner_rejected(booking, reason=reason)
            return True

        if _APPROVE_RE.search(content_lower):
            await self._engine.owner_approved(booking)
            return True

        return False

    async def route_contact_reply(
        self, contact_id: str, content: str
    ) -> bool:
        """Try to route a contact's reply to a booking in TIMES_PROPOSED state.

        Returns True if handled.
        """
        booking = self._engine.get_active_for_contact(contact_id)
        if not booking or booking.state != BookingState.TIMES_PROPOSED:
            return False

        # Check if contact is picking a slot number (1-5)
        slot_match = _SLOT_RE.search(content.strip())
        if slot_match:
            slot_idx = int(slot_match.group(1)) - 1
            if 0 <= slot_idx < len(booking.proposed_slots):
                chosen = booking.proposed_slots[slot_idx]
                await self._engine.contact_replied(booking, chosen)
                return True

        # Check for cancellation keywords
        cancel_re = re.compile(r"\b(cancel|never mind|forget it|don't bother)\b", re.IGNORECASE)
        if cancel_re.search(content) and booking.state == BookingState.BOOKING_CONFIRMED:
            await self._engine.contact_cancelled(booking)
            return True

        return False


def _to_jid(phone: str) -> str:
    """Convert phone number to WhatsApp JID."""
    phone = phone.replace("+", "").replace(" ", "").replace("-", "")
    if "@" not in phone:
        return f"{phone}@s.whatsapp.net"
    return phone
