"""Email Sender integration for HappyCapy WhatsApp bot.

Sends emails via Gmail (gws CLI) which is already authenticated.
Fallback: capymail Worker API if AGENT_WORKER_BASE_URL is set.

Use cases: invoices, order confirmations, reports, notifications,
meeting prep briefings, follow-up reminders.
"""

import asyncio
import json
import os
from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult

try:
    import httpx
except ImportError:
    httpx = None


GWS_PATH = os.path.expanduser("~/.cargo/bin/gws")


# ── Tool Definition (OpenAI format) ──

_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Send an email to any email address. Use for invoices, receipts, "
                "order confirmations, reports, or any notification that should go via email."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address (e.g. 'john@example.com').",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body text. Use plain text. Use \\n for line breaks.",
                    },
                    "cc": {
                        "type": "string",
                        "description": "CC recipient email address (optional).",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
]


# ── Email Integration ──


class Integration(BaseIntegration):
    """Email sending integration. Primary: Gmail via gws CLI. Fallback: capymail."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._client = kwargs.get("client")  # Shared httpx client (for capymail fallback)

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="email",
            display_name="Email Sender",
            description="Send emails via Gmail (or capymail fallback)",
        )

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        return (
            "## Email Sending\n"
            "You can send emails to any address via Gmail. Use for invoices, confirmations, reports, notifications.\n"
            "Tool: send_email\n"
            "- When someone asks to email something, use send_email with proper subject and body\n"
            "- Format emails professionally with clear subject lines\n"
            "- For invoices, include item details, totals, and payment instructions"
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute an email tool."""
        if tool_name != "send_email":
            return ToolResult(False, tool_name, f"Unknown email tool: {tool_name}")
        try:
            return await self._send_email(arguments)
        except Exception as e:
            return ToolResult(False, tool_name, f"Email error: {type(e).__name__}: {e}")

    async def _send_email(self, args: dict[str, Any]) -> ToolResult:
        """Send email. Try Gmail (gws) first, fall back to capymail."""
        # Validate required fields
        to_addr = args.get("to", "").strip()
        subject = args.get("subject", "").strip()
        body = args.get("body", "").strip()

        if not to_addr or "@" not in to_addr:
            return ToolResult(False, "send_email", "Invalid email address. Provide a valid 'to' address.")
        if not subject:
            return ToolResult(False, "send_email", "Email subject is required.")
        if not body:
            return ToolResult(False, "send_email", "Email body is required.")

        # Primary: Gmail via gws CLI
        if os.path.exists(GWS_PATH):
            return await self._send_via_gmail(to_addr, subject, body)

        # Fallback: capymail Worker API
        return await self._send_via_capymail(to_addr, subject, body, args.get("cc", ""))

    async def _send_via_gmail(self, to: str, subject: str, body: str) -> ToolResult:
        """Send email via Gmail using the gws CLI."""
        try:
            proc = await asyncio.create_subprocess_exec(
                GWS_PATH, "gmail", "+send",
                "--to", to,
                "--subject", subject,
                "--body", body,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)

            if proc.returncode == 0:
                return ToolResult(
                    True, "send_email",
                    f"Email sent via Gmail to {to} with subject: '{subject}'",
                )
            else:
                error = stderr.decode().strip() or stdout.decode().strip()
                return ToolResult(
                    False, "send_email",
                    f"Gmail send failed (exit {proc.returncode}): {error[:200]}",
                )
        except asyncio.TimeoutError:
            return ToolResult(False, "send_email", "Gmail send timed out (30s).")
        except Exception as e:
            return ToolResult(False, "send_email", f"Gmail send error: {type(e).__name__}: {e}")

    async def _send_via_capymail(self, to: str, subject: str, body: str, cc: str) -> ToolResult:
        """Fallback: send email via capymail Worker API."""
        if not httpx:
            return ToolResult(False, "send_email", "Email unavailable (no Gmail, no httpx)")

        worker_url = os.environ.get("AGENT_WORKER_BASE_URL", "")
        worker_secret = os.environ.get("AGENT_WORKER_SECRET", "")

        if not worker_url or not worker_secret:
            return ToolResult(
                False, "send_email",
                "Email not available. Gmail (gws) not found and capymail not configured.",
            )

        from_addr = os.environ.get("CAPY_USER_EMAIL_ALIAS", "capy@capymail.ai")
        sandbox_id = os.environ.get("FLY_APP_NAME", "unknown")

        payload: dict[str, Any] = {
            "to": [to],
            "subject": subject,
            "body": body,
            "from": from_addr,
        }
        if cc and "@" in cc:
            payload["cc"] = [cc]

        api_url = f"{worker_url.rstrip('/')}/api/email/send"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {worker_secret}",
            "X-Sandbox-Id": sandbox_id,
        }

        try:
            if self._client:
                resp = await self._client.post(api_url, headers=headers, json=payload, timeout=30.0)
            else:
                async with httpx.AsyncClient() as c:
                    resp = await c.post(api_url, headers=headers, json=payload, timeout=30.0)

            if resp.status_code == 200:
                return ToolResult(True, "send_email", f"Email sent via capymail to {to}")
            else:
                error_text = resp.text[:200] if resp.text else "Unknown error"
                return ToolResult(False, "send_email", f"Capymail HTTP {resp.status_code}: {error_text}")
        except httpx.TimeoutException:
            return ToolResult(False, "send_email", "Email sending timed out (30s).")
