"""Payment integration for HappyCapy WhatsApp bot.

Supports Stripe (global) and Razorpay (India).
Auto-detects provider from environment variables:
  STRIPE_API_KEY         → Stripe
  RAZORPAY_KEY_ID +
  RAZORPAY_KEY_SECRET    → Razorpay

Admin-only. Tools:
  create_payment_link  — generate a payment link and send to contact
  check_payment_status — check if a contact has paid
  list_recent_payments — overview of recent transactions

Webhook handlers live in dashboard/api.py:
  POST /webhooks/stripe   — Stripe signature-verified callback
  POST /webhooks/razorpay — Razorpay HMAC-verified callback
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult
from src.config_manager import is_admin

PAYMENTS_DB = Path.home() / ".happycapy-whatsapp" / "payments.db"


# ── Database helpers ──

def _get_db() -> sqlite3.Connection:
    PAYMENTS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(PAYMENTS_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_links (
            id          TEXT PRIMARY KEY,
            provider    TEXT NOT NULL,
            contact_phone TEXT NOT NULL,
            contact_jid TEXT NOT NULL DEFAULT '',
            contact_name TEXT NOT NULL DEFAULT '',
            amount      REAL NOT NULL,
            currency    TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            url         TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TEXT NOT NULL,
            paid_at     TEXT
        )
    """)
    conn.commit()
    return conn


def _store_link(provider: str, link_id: str, contact_phone: str,
                contact_name: str, amount: float, currency: str,
                description: str, url: str) -> None:
    jid = f"{contact_phone.lstrip('+').replace(' ', '')}@s.whatsapp.net"
    with _get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO payment_links
            (id, provider, contact_phone, contact_jid, contact_name,
             amount, currency, description, url, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (link_id, provider, contact_phone, jid, contact_name,
              amount, currency, description, url,
              datetime.now(tz=timezone.utc).isoformat()))


def mark_paid(link_id: str) -> dict | None:
    """Mark a payment link as paid. Called by webhook handlers. Returns row or None."""
    with _get_db() as conn:
        conn.execute(
            "UPDATE payment_links SET status='paid', paid_at=? WHERE id=?",
            (datetime.now(tz=timezone.utc).isoformat(), link_id)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM payment_links WHERE id=?", (link_id,)
        ).fetchone()
        return dict(row) if row else None


def get_link_by_id(link_id: str) -> dict | None:
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM payment_links WHERE id=?", (link_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Tool Definitions ──

_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "create_payment_link",
            "description": (
                "Create a payment link and return it to include in a message. "
                "Admin-only. Use for bookings, services, or any payment request. "
                "Supports Stripe (USD/global) or Razorpay (INR/India) depending on what's configured."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_phone": {
                        "type": "string",
                        "description": "Contact's phone number with country code, e.g. +919876543210.",
                    },
                    "amount": {
                        "type": "number",
                        "description": "Amount in the local currency (INR for Razorpay, USD for Stripe).",
                    },
                    "description": {
                        "type": "string",
                        "description": "What the payment is for, e.g. 'Consultation booking - March 15'.",
                    },
                    "contact_name": {
                        "type": "string",
                        "description": "Contact's name (optional, improves Razorpay UX).",
                    },
                },
                "required": ["contact_phone", "amount", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_payment_status",
            "description": (
                "Check if a contact has paid or see status of a specific payment link. "
                "Admin-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_phone": {
                        "type": "string",
                        "description": "Contact's phone number to look up their most recent payment.",
                    },
                    "payment_link_id": {
                        "type": "string",
                        "description": "Specific payment link ID (optional — use instead of contact_phone).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_payments",
            "description": "List recent payment links and their statuses. Admin-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent payments to show (default 10).",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["all", "pending", "paid"],
                        "description": "Filter by status (default 'all').",
                    },
                },
                "required": [],
            },
        },
    },
]


# ── Integration class ──

class Integration(BaseIntegration):
    """Stripe + Razorpay payment links — admin-only."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._sender_jid = ""
        self._stripe_key = os.environ.get("STRIPE_API_KEY", "")
        self._rzp_key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        self._rzp_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    def _is_admin(self) -> bool:
        sid = self._sender_jid.split("@")[0] if self._sender_jid else ""
        return is_admin(self.config, sid)

    def _provider(self) -> str:
        if self._stripe_key:
            return "stripe"
        if self._rzp_key_id and self._rzp_secret:
            return "razorpay"
        return ""

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="payments",
            display_name="Payments",
            description="Stripe and Razorpay payment links — send, track, and confirm payments over WhatsApp",
        )

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def visibility(cls) -> str:
        return "admin"

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        return (
            "## Payment Tools (Admin Only)\n\n"
            "You can create and track payments via create_payment_link.\n"
            "Workflow:\n"
            "1. After confirming a booking/service, call create_payment_link\n"
            "2. Include the returned URL naturally in your reply to the contact\n"
            "3. Payments are confirmed automatically via webhook\n"
            "4. Use check_payment_status to verify if someone has paid\n"
            "Currency: INR for Razorpay, USD for Stripe.\n"
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self._is_admin():
            return ToolResult(False, tool_name, "Payment tools are admin-only.")
        if not self._provider():
            return ToolResult(False, tool_name,
                "No payment provider configured. Set STRIPE_API_KEY or "
                "RAZORPAY_KEY_ID + RAZORPAY_KEY_SECRET.")
        handlers = {
            "create_payment_link": self._create_payment_link,
            "check_payment_status": self._check_payment_status,
            "list_recent_payments": self._list_recent_payments,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(False, tool_name, f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return ToolResult(False, tool_name, f"Error: {type(e).__name__}: {e}")

    # ── Stripe ──

    async def _stripe_create_link(self, contact_phone: str, contact_name: str,
                                   amount: float, description: str) -> tuple[str, str]:
        """Create a Stripe Checkout session. Returns (session_id, url)."""
        try:
            import stripe as _stripe
        except ImportError:
            raise RuntimeError("stripe package not installed. Run: pip install stripe")
        _stripe.api_key = self._stripe_key
        amount_cents = int(amount * 100)
        session = _stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": description},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url="https://happycapy.ai/payment/success",
            cancel_url="https://happycapy.ai/payment/cancel",
            metadata={"contact_phone": contact_phone, "contact_name": contact_name},
        )
        return session.id, session.url

    async def _stripe_check_status(self, link_id: str) -> str:
        """Return Stripe checkout session payment status."""
        try:
            import stripe as _stripe
        except ImportError:
            raise RuntimeError("stripe package not installed.")
        _stripe.api_key = self._stripe_key
        session = _stripe.checkout.Session.retrieve(link_id)
        return session.payment_status  # paid / unpaid / no_payment_required

    # ── Razorpay ──

    async def _rzp_create_link(self, contact_phone: str, contact_name: str,
                                amount: float, description: str) -> tuple[str, str]:
        """Create a Razorpay payment link. Returns (link_id, short_url)."""
        try:
            import razorpay
        except ImportError:
            raise RuntimeError("razorpay package not installed. Run: pip install razorpay")
        client = razorpay.Client(auth=(self._rzp_key_id, self._rzp_secret))
        amount_paise = int(amount * 100)
        phone_digits = "".join(c for c in contact_phone if c.isdigit())
        payload: dict[str, Any] = {
            "amount": amount_paise,
            "currency": "INR",
            "description": description,
            "notes": {
                "contact_phone": contact_phone,
                "contact_name": contact_name,
            },
            "reminder_enable": False,
        }
        if phone_digits:
            payload["customer"] = {
                "name": contact_name or "Customer",
                "contact": phone_digits,
            }
        link = client.payment_link.create(payload)
        return link["id"], link["short_url"]

    async def _rzp_check_status(self, link_id: str) -> str:
        """Return Razorpay payment link status."""
        try:
            import razorpay
        except ImportError:
            raise RuntimeError("razorpay package not installed.")
        client = razorpay.Client(auth=(self._rzp_key_id, self._rzp_secret))
        link = client.payment_link.fetch(link_id)
        return link.get("status", "unknown")  # created / paid / cancelled / expired

    # ── Tool handlers ──

    async def _create_payment_link(self, args: dict) -> ToolResult:
        contact_phone = args.get("contact_phone", "").strip()
        amount = float(args.get("amount", 0))
        description = args.get("description", "Payment").strip()
        contact_name = args.get("contact_name", "").strip()

        if not contact_phone:
            return ToolResult(False, "create_payment_link", "Missing contact_phone.")
        if amount <= 0:
            return ToolResult(False, "create_payment_link", "Amount must be greater than 0.")

        provider = self._provider()
        if provider == "stripe":
            link_id, url = await self._stripe_create_link(contact_phone, contact_name, amount, description)
            currency = "USD"
        else:
            link_id, url = await self._rzp_create_link(contact_phone, contact_name, amount, description)
            currency = "INR"

        _store_link(provider, link_id, contact_phone, contact_name, amount, currency, description, url)

        return ToolResult(
            True, "create_payment_link",
            f"Payment link created ({provider.title()}):\n"
            f"Amount: {currency} {amount:.2f}\n"
            f"For: {description}\n"
            f"URL: {url}\n\n"
            f"Share this link with the contact. Payment will be confirmed automatically."
        )

    async def _check_payment_status(self, args: dict) -> ToolResult:
        contact_phone = args.get("contact_phone", "").strip()
        link_id = args.get("payment_link_id", "").strip()

        with _get_db() as conn:
            if link_id:
                row = conn.execute(
                    "SELECT * FROM payment_links WHERE id=?", (link_id,)
                ).fetchone()
            elif contact_phone:
                row = conn.execute(
                    "SELECT * FROM payment_links WHERE contact_phone=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (contact_phone,)
                ).fetchone()
            else:
                return ToolResult(False, "check_payment_status",
                                  "Provide contact_phone or payment_link_id.")

        if not row:
            return ToolResult(True, "check_payment_status", "No payment link found.")

        row = dict(row)
        # Also check live status from provider
        try:
            if row["provider"] == "stripe":
                live_status = await self._stripe_check_status(row["id"])
                if live_status == "paid" and row["status"] != "paid":
                    mark_paid(row["id"])
                    row["status"] = "paid"
            else:
                live_status = await self._rzp_check_status(row["id"])
                if live_status == "paid" and row["status"] != "paid":
                    mark_paid(row["id"])
                    row["status"] = "paid"
        except Exception:
            pass  # Use stored status as fallback

        currency = row["currency"]
        amount = row["amount"]
        status_emoji = "✅" if row["status"] == "paid" else "⏳"
        result = (
            f"{status_emoji} Payment status: {row['status'].upper()}\n"
            f"Contact: {row['contact_name'] or row['contact_phone']}\n"
            f"Amount: {currency} {amount:.2f}\n"
            f"For: {row['description']}\n"
            f"Created: {row['created_at'][:10]}"
        )
        if row.get("paid_at"):
            result += f"\nPaid at: {row['paid_at'][:16]}"
        return ToolResult(True, "check_payment_status", result)

    async def _list_recent_payments(self, args: dict) -> ToolResult:
        limit = min(int(args.get("limit", 10)), 50)
        status_filter = args.get("status", "all")

        with _get_db() as conn:
            if status_filter == "all":
                rows = conn.execute(
                    "SELECT * FROM payment_links ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM payment_links WHERE status=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status_filter, limit)
                ).fetchall()

        if not rows:
            return ToolResult(True, "list_recent_payments", "No payments found.")

        lines = []
        for r in rows:
            emoji = "✅" if r["status"] == "paid" else "⏳"
            name = r["contact_name"] or r["contact_phone"]
            lines.append(
                f"{emoji} {r['currency']} {r['amount']:.0f} — {name} — "
                f"{r['description'][:40]} [{r['status']}] {r['created_at'][:10]}"
            )
        return ToolResult(True, "list_recent_payments",
                          f"Recent payments ({len(rows)}):\n" + "\n".join(lines))
