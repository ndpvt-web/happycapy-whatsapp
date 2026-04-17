"""Scheduler integration: exposes CronService as LLM-accessible tools.

The CronService already exists and handles SQLite-backed reminders and
recurring jobs. This integration wraps it so the AI can:
- Set one-shot reminders (WhatsApp and/or email delivery)
- Set recurring tasks
- List and cancel reminders

Delivery routing: reminder message stores a JSON payload with delivery
preferences. The enhanced cron callback in main.py parses this and routes
to WhatsApp, email, or both.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult


_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": (
                "Schedule a one-shot reminder. The reminder will be delivered via "
                "WhatsApp message, email, or both at the specified time. "
                "Use for meeting prep, follow-ups, deadlines, or any timed notification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The reminder message to deliver when triggered.",
                    },
                    "remind_at": {
                        "type": "string",
                        "description": (
                            "When to send the reminder. ISO 8601 datetime "
                            "(e.g. '2026-03-29T14:00:00'). Use the current timezone context."
                        ),
                    },
                    "label": {
                        "type": "string",
                        "description": "Short human-readable name for this reminder.",
                    },
                    "delivery": {
                        "type": "string",
                        "enum": ["whatsapp", "email", "both"],
                        "description": "Where to deliver the reminder. Default: whatsapp.",
                        "default": "whatsapp",
                    },
                    "email_address": {
                        "type": "string",
                        "description": (
                            "Email address for email delivery. "
                            "Defaults to owner's email if not specified."
                        ),
                    },
                },
                "required": ["message", "remind_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_recurring",
            "description": (
                "Schedule a recurring task that repeats at a fixed interval. "
                "Use for daily check-ins, periodic reports, or regular reminders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message to deliver on each occurrence.",
                    },
                    "interval_minutes": {
                        "type": "integer",
                        "description": "Interval in minutes between each delivery.",
                        "minimum": 1,
                    },
                    "label": {
                        "type": "string",
                        "description": "Short human-readable name for this recurring task.",
                    },
                },
                "required": ["message", "interval_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List all scheduled reminders and recurring tasks.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reminder",
            "description": "Cancel a scheduled reminder or recurring task by its job ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The job ID from set_reminder, set_recurring, or list_reminders.",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
]


class Integration(BaseIntegration):
    """Scheduler integration wrapping CronService for LLM access."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._cron = kwargs.get("cron")  # CronService instance from main.py
        self._sender_jid = ""

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="scheduler",
            display_name="Scheduler",
            description="Set reminders, recurring tasks, and timed notifications",
        )

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        tz = config.get("booking_policy", {}).get("timezone", "Asia/Hong_Kong")
        return (
            "## Reminders & Scheduling\n"
            "You can schedule reminders and recurring tasks using set_reminder and set_recurring.\n"
            "- When someone asks to be reminded about something, use set_reminder\n"
            "- When booking a meeting, schedule a prep reminder 30 minutes before\n"
            "- Reminders can be delivered via WhatsApp, email, or both\n"
            "- Use list_reminders to check existing schedules\n"
            "- Use cancel_reminder to remove a scheduled reminder\n"
            f"- TIMEZONE: The owner is in {tz}. When generating remind_at datetimes, "
            f"use {tz} local time (naive datetimes are interpreted as {tz}, not UTC).\n"
            "- For email delivery: you don't need to specify email_address -- it defaults to the owner's email."
        )

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if not self._cron:
            return ToolResult(False, tool_name, "Scheduler not available (CronService not initialized).")

        dispatch = {
            "set_reminder": self._set_reminder,
            "set_recurring": self._set_recurring,
            "list_reminders": self._list_reminders,
            "cancel_reminder": self._cancel_reminder,
        }

        handler = dispatch.get(tool_name)
        if not handler:
            return ToolResult(False, tool_name, f"Unknown scheduler tool: {tool_name}")

        try:
            return await handler(arguments)
        except Exception as e:
            return ToolResult(False, tool_name, f"Scheduler error: {type(e).__name__}: {e}")

    async def _set_reminder(self, args: dict[str, Any]) -> ToolResult:
        message = args.get("message", "").strip()
        remind_at_str = args.get("remind_at", "").strip()
        label = args.get("label", "Reminder").strip()
        delivery = args.get("delivery", "whatsapp")
        email_address = args.get("email_address", "") or self.config.get("owner_email", "")

        if not message:
            return ToolResult(False, "set_reminder", "Reminder message is required.")
        if not remind_at_str:
            return ToolResult(False, "set_reminder", "remind_at datetime is required.")

        # Parse ISO 8601 datetime
        try:
            dt = datetime.fromisoformat(remind_at_str)
            if dt.tzinfo is None:
                # Use config timezone (default: Asia/Hong_Kong), NOT UTC
                tz_name = self.config.get("booking_policy", {}).get("timezone", "Asia/Hong_Kong")
                try:
                    local_tz = ZoneInfo(tz_name)
                except Exception:
                    local_tz = ZoneInfo("Asia/Hong_Kong")
                dt = dt.replace(tzinfo=local_tz)
            run_at = dt.timestamp()
        except ValueError:
            return ToolResult(False, "set_reminder", f"Invalid datetime format: {remind_at_str}")

        if run_at <= time.time():
            return ToolResult(False, "set_reminder", "Reminder time must be in the future.")

        # Build JSON payload for smart delivery
        payload = json.dumps({
            "text": message,
            "delivery": delivery,
            "email": email_address,
        })

        # Always deliver to admin's escalation number if sender JID is empty
        target_chat = self._sender_jid or ""
        if not target_chat:
            from src.config_manager import get_escalation_target
            esc = get_escalation_target(self.config)
            if esc:
                target_chat = f"{esc}@s.whatsapp.net"
        job = self._cron.add_reminder(label, payload, run_at, target_chat)

        dt_display = dt.strftime("%Y-%m-%d %H:%M")
        return ToolResult(
            True, "set_reminder",
            f"Reminder '{label}' scheduled for {dt_display} (ID: {job['id']}). "
            f"Delivery: {delivery}.",
        )

    async def _set_recurring(self, args: dict[str, Any]) -> ToolResult:
        message = args.get("message", "").strip()
        interval_minutes = int(args.get("interval_minutes", 0))
        label = args.get("label", "Recurring task").strip()

        if not message:
            return ToolResult(False, "set_recurring", "Message is required.")
        if interval_minutes < 1:
            return ToolResult(False, "set_recurring", "Interval must be at least 1 minute.")

        interval_s = interval_minutes * 60
        target_chat = self._sender_jid or ""
        if not target_chat:
            from src.config_manager import get_escalation_target
            esc = get_escalation_target(self.config)
            if esc:
                target_chat = f"{esc}@s.whatsapp.net"
        job = self._cron.add_recurring(label, message, interval_s, target_chat)

        return ToolResult(
            True, "set_recurring",
            f"Recurring task '{label}' set every {interval_minutes} minutes (ID: {job['id']}).",
        )

    async def _list_reminders(self, args: dict[str, Any]) -> ToolResult:
        formatted = self._cron.format_job_list()
        return ToolResult(True, "list_reminders", formatted)

    async def _cancel_reminder(self, args: dict[str, Any]) -> ToolResult:
        job_id = args.get("job_id", "").strip()
        if not job_id:
            return ToolResult(False, "cancel_reminder", "Job ID is required.")

        removed = self._cron.remove_job(job_id)
        if removed:
            return ToolResult(True, "cancel_reminder", f"Reminder {job_id} cancelled.")
        else:
            return ToolResult(False, "cancel_reminder", f"No reminder found with ID: {job_id}")
