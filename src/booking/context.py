"""Context Aggregator — the sensitive soul of the booking system.

Aristotle's three souls:
  Vegetative (engine.py): lifecycle mechanics
  Sensitive  (context.py): perception — reads from all integrations
  Rational   (LLM): generates the words

This module reads from four sources PROGRAMMATICALLY — not via LLM tool calls:
  1. Web search      — recent public info about the contact/company
  2. Email history   — past email threads with this contact
  3. WhatsApp history — message history with this contact (MemoryStore)
  4. Zoom meetings   — past meeting summaries from Zoom AI emails

The LLM receives this pre-assembled context, not individual tool results.
This prevents the LLM from forgetting to gather context or gathering it wrong.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from .models import BookingRecord, OwnerContext, BriefContext


GWS_BIN = os.path.expanduser("~/.cargo/bin/gws")


class ContextAggregator:
    """Assembles rich context from multiple integrations before owner notification or brief."""

    def __init__(
        self,
        config: dict,
        memory_store=None,   # src.memory_store.MemoryStore instance
        http_client=None,    # httpx.AsyncClient for web search
    ):
        self._config = config
        self._memory = memory_store
        self._http = http_client

    # ── Public API ──

    async def for_owner_notification(self, booking: BookingRecord) -> OwnerContext:
        """Assemble full context for owner approval notification."""
        contact_id = booking.contact_id
        contact_name = booking.contact_name

        email_threads, whatsapp_history, zoom_summary, web_results = await asyncio.gather(
            self._get_email_history(contact_id, contact_name),
            self._get_whatsapp_history(contact_id),
            self._get_zoom_summary(contact_name),
            self._get_web_search(contact_name, booking.purpose),
            return_exceptions=True,
        )

        return OwnerContext(
            contact_name=contact_name,
            contact_id=contact_id,
            purpose=booking.purpose,
            chosen_slot=booking.contact_choice,
            email_threads=_safe_list(email_threads),
            whatsapp_history=_safe_str(whatsapp_history),
            zoom_summary=_safe_str(zoom_summary),
            web_search_results=_safe_list(web_results),
        )

    async def for_pre_meeting_brief(self, booking: BookingRecord) -> BriefContext:
        """Assemble full context for T-60min pre-meeting brief."""
        contact_id = booking.contact_id
        contact_name = booking.contact_name
        meeting_time = booking.contact_choice.get("start", "") if booking.contact_choice else ""

        email_threads, whatsapp_history, zoom_summary, web_results = await asyncio.gather(
            self._get_email_history(contact_id, contact_name),
            self._get_whatsapp_history(contact_id),
            self._get_zoom_summary(contact_name),
            self._get_web_search(contact_name, booking.purpose),
            return_exceptions=True,
        )

        return BriefContext(
            contact_name=contact_name,
            contact_id=contact_id,
            purpose=booking.purpose,
            meeting_time=meeting_time,
            email_threads=_safe_list(email_threads),
            whatsapp_history=_safe_str(whatsapp_history),
            zoom_summary=_safe_str(zoom_summary),
            web_search_results=_safe_list(web_results),
        )

    # ── Source 1: Web Search ──

    async def _get_web_search(self, contact_name: str, purpose: str) -> list[dict]:
        """Search for recent public info about the contact."""
        if not self._http or not contact_name:
            return []
        try:
            from src.search_provider import get_provider
            provider = get_provider(self._config)
            query = f"{contact_name}"
            if purpose:
                # Narrow the search to topics relevant to the meeting purpose
                query += f" {purpose[:60]}"
            results = await provider.search(query, max_results=3)
            return [
                {
                    "title": r.title,
                    "url": r.url,
                    "snippet": r.snippet,
                    "published": r.published,
                }
                for r in results
            ]
        except Exception as e:
            print(f"[booking/context] web_search failed: {e}")
            return []

    # ── Source 2: Email History ──

    async def _get_email_history(
        self, contact_id: str, contact_name: str
    ) -> list[dict]:
        """Fetch recent email threads with this contact via gws CLI."""
        if not os.path.exists(GWS_BIN):
            return []

        # Build search query: search by phone (if email unknown) or name
        query_parts = []
        if contact_name and contact_name != contact_id:
            query_parts.append(f'"{contact_name}"')

        if not query_parts:
            return []

        search_query = " OR ".join(query_parts)
        search_query += " in:anywhere"

        try:
            proc = await asyncio.create_subprocess_exec(
                GWS_BIN, "gmail", "search",
                "--query", search_query,
                "--max-results", "5",
                "--format", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            if not stdout:
                return []
            data = json.loads(stdout.decode())
            threads = data if isinstance(data, list) else data.get("threads", [])
            return threads[:5]
        except Exception as e:
            print(f"[booking/context] email_history failed: {e}")
            return []

    # ── Source 3: WhatsApp Chat History ──

    async def _get_whatsapp_history(self, contact_id: str) -> str:
        """Read chat history from MemoryStore (per-contact HISTORY.md)."""
        if not self._memory:
            return ""
        try:
            # Normalize: strip @s.whatsapp.net suffix if present
            jid = contact_id if "@" in contact_id else f"{contact_id}@s.whatsapp.net"
            history = self._memory.read_contact_history(jid)
            if not history:
                return ""
            # Return last ~2000 chars (most recent interactions)
            return history[-2000:] if len(history) > 2000 else history
        except Exception as e:
            print(f"[booking/context] whatsapp_history failed: {e}")
            return ""

    # ── Source 4: Zoom Meeting Summaries ──

    async def _get_zoom_summary(self, contact_name: str) -> str:
        """Search Gmail for Zoom AI meeting summary emails mentioning this contact.

        Zoom sends summary emails with subject 'Meeting summary: <meeting name>'
        after each meeting. We search for these mentioning the contact's name.
        """
        if not os.path.exists(GWS_BIN) or not contact_name:
            return ""

        search_query = f'subject:"Meeting summary" "{contact_name}" from:zoom.us'

        try:
            proc = await asyncio.create_subprocess_exec(
                GWS_BIN, "gmail", "search",
                "--query", search_query,
                "--max-results", "2",
                "--format", "json",
                "--include-body",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            if not stdout:
                return ""
            data = json.loads(stdout.decode())
            threads = data if isinstance(data, list) else data.get("threads", [])
            if not threads:
                return ""

            # Extract and join summaries (most recent first)
            summaries = []
            for thread in threads[:2]:
                body = thread.get("body", "") or thread.get("snippet", "")
                if body:
                    # Truncate long bodies
                    summaries.append(body[:500])

            return "\n---\n".join(summaries) if summaries else ""
        except Exception as e:
            print(f"[booking/context] zoom_summary failed: {e}")
            return ""


# ── Format helpers ──

def format_owner_context(ctx: OwnerContext) -> str:
    """Format OwnerContext into a human-readable text block for the LLM."""
    lines = [
        f"*Booking request from {ctx.contact_name} ({ctx.contact_id})*",
        f"Purpose: {ctx.purpose}",
    ]

    if ctx.chosen_slot:
        label = ctx.chosen_slot.get("label") or ctx.chosen_slot.get("start", "")
        lines.append(f"Requested slot: {label}")

    if ctx.email_threads:
        lines.append("\n*Email history (recent):*")
        for t in ctx.email_threads[:3]:
            subject = t.get("subject", t.get("snippet", "")[:80])
            date = t.get("date", "")
            lines.append(f"  • [{date}] {subject}")

    if ctx.whatsapp_history:
        lines.append("\n*WhatsApp chat history:*")
        lines.append(ctx.whatsapp_history[-500:])  # Last 500 chars

    if ctx.zoom_summary:
        lines.append("\n*Previous meeting summary (Zoom):*")
        lines.append(ctx.zoom_summary[:400])

    if ctx.web_search_results:
        lines.append("\n*Recent web info:*")
        for r in ctx.web_search_results[:2]:
            lines.append(f"  • {r['title']}: {r['snippet'][:100]}")

    return "\n".join(lines)


def format_brief_context(ctx: BriefContext) -> str:
    """Format BriefContext into a pre-meeting brief prompt."""
    lines = [
        f"Prepare a pre-meeting brief for Nivesh.",
        f"Meeting with: {ctx.contact_name} ({ctx.contact_id})",
        f"Meeting time: {ctx.meeting_time}",
        f"Purpose: {ctx.purpose}",
        "",
        "Context gathered:",
    ]

    if ctx.email_threads:
        lines.append("\nEmail threads with this contact:")
        for t in ctx.email_threads[:3]:
            subject = t.get("subject", t.get("snippet", "")[:80])
            date = t.get("date", "")
            lines.append(f"  [{date}] {subject}")

    if ctx.whatsapp_history:
        lines.append("\nWhatsApp conversation history:")
        lines.append(ctx.whatsapp_history[-800:])

    if ctx.zoom_summary:
        lines.append("\nPrevious meeting summary:")
        lines.append(ctx.zoom_summary[:600])

    if ctx.web_search_results:
        lines.append("\nRecent web info about them:")
        for r in ctx.web_search_results[:3]:
            lines.append(f"  {r['title']}: {r['snippet'][:120]}")

    lines.extend([
        "",
        "Generate a concise brief covering:",
        "1. Who they are and what they want",
        "2. History of your interactions",
        "3. Key points to cover or decisions to make",
        "4. Any recent news relevant to this meeting",
        "Keep it under 300 words. WhatsApp-friendly format.",
    ])

    return "\n".join(lines)


def _safe_list(result: Any) -> list:
    if isinstance(result, Exception):
        return []
    return result if isinstance(result, list) else []


def _safe_str(result: Any) -> str:
    if isinstance(result, Exception):
        return ""
    return result if isinstance(result, str) else ""
