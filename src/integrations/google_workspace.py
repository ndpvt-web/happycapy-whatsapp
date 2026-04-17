"""Google Workspace integration for HappyCapy WhatsApp bot.

Wraps the gws CLI to give the LLM access to Gmail, Calendar, Sheets, and Drive.
All outputs pass through the Privacy Projection layer so contacts never see
private details (event titles, email subjects, etc.) -- only the owner gets full access.

Tools: check_calendar, create_event, check_availability,
       send_gmail, read_inbox, read_google_sheet, append_google_sheet
"""

import asyncio
import json
import os

from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult
from src.privacy_projection import PrivacyProjection

GWS_BIN = os.path.expanduser("~/.cargo/bin/gws")
GWS_TIMEOUT = 30


# ── Tool Definitions (OpenAI format) ──

_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "check_calendar",
            "description": (
                "Check the owner's Google Calendar for upcoming events. "
                "Returns a privacy-projected view (contacts see busy/free only, owner sees full details)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": "Number of days ahead to check (default 1, max 7).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": (
                "Create a Google Calendar event, optionally with a Google Meet link. "
                "Use for scheduling meetings, reminders, or any calendar event."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Event title/name."},
                    "start": {"type": "string", "description": "Start datetime (ISO 8601, e.g. '2026-03-25T14:00:00+05:30')."},
                    "end": {"type": "string", "description": "End datetime (ISO 8601, e.g. '2026-03-25T15:00:00+05:30')."},
                    "attendee": {"type": "string", "description": "Attendee email address (optional)."},
                    "with_meet": {"type": "boolean", "description": "Create a Google Meet link for the event (default false)."},
                },
                "required": ["summary", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": (
                "Check if the owner is free at a specific date/time. "
                "Returns only busy/free status to contacts -- never event details."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date to check (YYYY-MM-DD)."},
                    "time": {"type": "string", "description": "Time to check (HH:MM, 24h format)."},
                },
                "required": ["date", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_gmail",
            "description": "Send an email via the owner's Gmail account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Email body text."},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_inbox",
            "description": (
                "Check the owner's Gmail inbox for recent emails. "
                "Privacy-projected: contacts see email count only, owner sees full details."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "description": "Max emails to return (default 5, max 10)."},
                    "query": {"type": "string", "description": "Gmail search query (optional, e.g. 'is:unread from:boss')."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_google_sheet",
            "description": "Read data from a Google Sheets spreadsheet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheets spreadsheet ID (from the URL)."},
                    "range": {"type": "string", "description": "Cell range to read (e.g. 'Sheet1!A1:C10')."},
                },
                "required": ["spreadsheet_id", "range"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_google_sheet",
            "description": "Append a row of data to a Google Sheets spreadsheet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheets spreadsheet ID (from the URL)."},
                    "values": {"type": "string", "description": "Comma-separated values to append (e.g. 'Alice,100,true')."},
                },
                "required": ["spreadsheet_id", "values"],
            },
        },
    },
]


# ── Helper: run gws CLI ──

async def _run_gws(*args: str, timeout: int = GWS_TIMEOUT) -> tuple[bool, str]:
    """Run a gws CLI command and return (success, output)."""
    env = os.environ.copy()
    env["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + env.get("PATH", "")

    try:
        proc = await asyncio.create_subprocess_exec(
            GWS_BIN, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode == 0:
            return True, output
        # Auth errors
        if "OAuth client was disabled" in err or "token" in err.lower():
            return False, "Google auth expired. Owner needs to run: gws auth login"
        return False, err or output or f"gws exited with code {proc.returncode}"

    except asyncio.TimeoutError:
        return False, f"gws command timed out after {timeout}s"
    except FileNotFoundError:
        return False, f"gws binary not found at {GWS_BIN}"
    except Exception as e:
        return False, f"gws error: {type(e).__name__}: {e}"


# ── Integration class ──

class Integration(BaseIntegration):
    """Google Workspace integration via gws CLI with Privacy Projection.

    Fully self-contained plugin. All privacy rules, tool definitions,
    system prompt additions, and execution logic live in this single file.
    Drop this file in src/integrations/ to enable. Delete it to disable.
    No core file changes needed.
    """

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._privacy = PrivacyProjection(config)
        self._sender_jid: str = ""

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        """Set who is asking (for privacy projection per-request)."""
        self._sender_jid = sender_jid

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="google_workspace",
            display_name="Google Workspace",
            description="Gmail, Calendar, Sheets, Drive via gws CLI with privacy projection",
        )

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        """Self-contained system prompt: tools + privacy + booking workflow.

        All instructions live HERE in the plugin, not in context_builder.py.
        This is the Aristotelian formal cause -- the plugin defines its own shape.
        The booking workflow is CONFIG-DRIVEN: change config.json to change behavior.
        """
        # ── Base tools and privacy (always present) ──
        parts = [
            "## Google Workspace Integration\n"
            "You have access to the owner's Google Calendar, Gmail, and Sheets.\n"
            "Tools: check_calendar, create_event, check_availability, send_gmail, "
            "read_inbox, read_google_sheet, append_google_sheet\n\n"
            "USAGE GUIDELINES:\n"
            "- Use check_calendar to see the owner's schedule\n"
            "- Use check_availability to check if a specific time slot is free\n"
            "- Use create_event to schedule meetings (add with_meet=true for Google Meet)\n"
            "- Use send_gmail to send emails from the owner's Gmail\n"
            "- Use read_inbox to check recent emails\n"
            "- Use read_google_sheet / append_google_sheet for spreadsheet operations",

            "## Privacy Projection (MANDATORY)\n"
            "You use Google data to act intelligently. You NEVER reveal raw details to contacts.\n"
            "- Availability: say 'available' or 'has a commitment'. NEVER what it is.\n"
            "- NEVER mention event titles, attendees, email subjects, or document titles.\n"
            "- NEVER confirm/deny specific guesses ('is he in a meeting?' -> 'let me check').\n"
            "- Use generic language: 'busy', 'has something scheduled', 'prior commitment'.\n"
            "- If pressed for details: escalate to owner via ask_owner.\n"
            "- The ONLY person who gets full details is the owner (admin number).",
        ]

        # ── Booking workflow (config-driven) ──
        bp = config.get("booking_policy", {})
        if bp.get("auto_book", False):
            duration = bp.get("default_duration_minutes", 30)
            max_days = bp.get("max_advance_days", 14)
            h_start = bp.get("allowed_hours_start", "09:00")
            h_end = bp.get("allowed_hours_end", "18:00")
            tz = bp.get("timezone", "UTC")
            need_approval = bp.get("require_owner_approval", False)
            collect_email = bp.get("collect_email", True)
            send_meet = bp.get("send_meet_invite", True)

            workflow = (
                "## Meeting Booking Workflow (FOLLOW THIS EXACTLY)\n"
                "When a contact asks to book/schedule a meeting with the owner:\n\n"
                "STEP 1 - GATHER: Ask for preferred date and time if not specified.\n"
                f"  Constraints: {h_start}-{h_end} ({tz}), up to {max_days} days ahead.\n"
                "  If the time is outside these bounds, politely suggest valid times.\n\n"
                "STEP 2 - CHECK: Use check_availability with the requested date and time.\n"
                "  If BUSY: say 'that time is not available' and suggest checking another time.\n"
                "    Do NOT reveal what the conflict is. Just say 'not available'.\n"
                "  If FREE: proceed to Step 3.\n\n"
            )

            if need_approval:
                workflow += (
                    "STEP 3 - APPROVE: Use ask_owner to confirm the booking.\n"
                    "  Message: '[Contact name] wants to meet at [date/time]. Approve?'\n"
                    "  Wait for owner response before proceeding.\n\n"
                )
                next_step = 4
            else:
                next_step = 3

            workflow += (
                f"STEP {next_step} - BOOK: Use create_event to create the meeting.\n"
                f"  Default duration: {duration} minutes.\n"
                f"  Set with_meet={'true' if send_meet else 'false'} for Google Meet link.\n"
                "  Event title: 'Meeting with [contact name]'.\n\n"
            )
            next_step += 1

            if collect_email:
                workflow += (
                    f"STEP {next_step} - EMAIL: Ask the contact for their email address.\n"
                    "  If they provide it:\n"
                )
                if send_meet:
                    workflow += (
                        "    Use send_gmail to email them the meeting details and Google Meet link.\n"
                        "    Subject: 'Meeting Confirmed - [date] at [time]'\n"
                        "    Include: date, time, duration, and the Google Meet link.\n\n"
                    )
                else:
                    workflow += (
                        "    Use send_gmail to email them the meeting confirmation.\n"
                        "    Subject: 'Meeting Confirmed - [date] at [time]'\n\n"
                    )
                next_step += 1

            workflow += (
                f"STEP {next_step} - CONFIRM: Tell the contact the meeting is booked.\n"
                "  Include the date, time, and duration.\n"
            )
            if send_meet:
                workflow += (
                    "  Share the Google Meet link directly in the WhatsApp message too.\n"
                )

            parts.append(workflow)

        return "\n\n".join(parts)

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Route tool execution to the appropriate handler."""
        handlers = {
            "check_calendar": self._check_calendar,
            "create_event": self._create_event,
            "check_availability": self._check_availability,
            "send_gmail": self._send_gmail,
            "read_inbox": self._read_inbox,
            "read_google_sheet": self._read_google_sheet,
            "append_google_sheet": self._append_google_sheet,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(False, tool_name, f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return ToolResult(False, tool_name, f"Error: {type(e).__name__}: {e}")

    # ── Calendar Tools ──

    async def _check_calendar(self, args: dict[str, Any]) -> ToolResult:
        ok, output = await _run_gws("calendar", "+agenda", "--format", "json")
        if not ok:
            return ToolResult(False, "check_calendar", output)
        projected = self._privacy.project_calendar(output, self._sender_jid)
        return ToolResult(True, "check_calendar", projected)

    async def _create_event(self, args: dict[str, Any]) -> ToolResult:
        summary = args.get("summary", "")
        start = args.get("start", "")
        end = args.get("end", "")
        if not summary or not start or not end:
            return ToolResult(False, "create_event", "Missing required: summary, start, end")

        # Always use +insert workflow command (raw API returns 403 on this project)
        cmd = ["calendar", "+insert", "--summary", summary, "--start", start, "--end", end]

        attendee = args.get("attendee", "")
        if attendee:
            cmd.extend(["--attendee", attendee])

        ok, output = await _run_gws(*cmd)
        if not ok:
            return ToolResult(False, "create_event", output)

        result = f"Event '{summary}' created: {start} to {end}"
        if attendee:
            result += f"\nInvite sent to: {attendee}"
        if args.get("with_meet"):
            result += "\nNote: Google Meet link generation requires additional GCP permissions. Please add a Meet link manually or share your personal Meet room link."
        return ToolResult(True, "create_event", result)

    async def _check_availability(self, args: dict[str, Any]) -> ToolResult:
        date_str = args.get("date", "")
        time_str = args.get("time", "")
        if not date_str or not time_str:
            return ToolResult(False, "check_availability", "Missing required: date, time")

        # Calculate days ahead from today for +agenda --days flag
        from datetime import date as dt_date
        try:
            target = dt_date.fromisoformat(date_str)
            today = dt_date.today()
            days_ahead = max((target - today).days + 1, 1)
        except ValueError:
            days_ahead = 14  # fallback: fetch 2 weeks

        # Use +agenda workflow (works with OAuth) instead of raw API (403)
        ok, output = await _run_gws(
            "calendar", "+agenda", "--days", str(days_ahead), "--format", "json",
        )
        if not ok:
            return ToolResult(False, "check_availability", output)

        # Parse events from +agenda JSON format
        try:
            data = json.loads(output)
            all_events = data.get("events", [])
        except (json.JSONDecodeError, AttributeError):
            all_events = []

        # Filter to events on the target date
        events = [e for e in all_events if e.get("start", "").startswith(date_str)]

        check_hour, check_min = int(time_str.split(":")[0]), int(time_str.split(":")[1])
        check_minutes = check_hour * 60 + check_min
        is_busy = False
        busy_event_summary = ""

        for event in events:
            start = event.get("start", "")
            end = event.get("end", "")
            # Handle timed events (contain "T") vs all-day events (date only)
            if "T" in start and "T" in end:
                sh = int(start.split("T")[1][:2])
                sm = int(start.split("T")[1][3:5])
                eh = int(end.split("T")[1][:2])
                em = int(end.split("T")[1][3:5])
                s_min, e_min = sh * 60 + sm, eh * 60 + em
                if s_min <= check_minutes < e_min:
                    is_busy = True
                    busy_event_summary = event.get("summary", "Untitled")
                    break

        # Privacy projection: contacts only see busy/free
        if self._privacy.is_owner(self._sender_jid):
            if is_busy:
                return ToolResult(True, "check_availability",
                    f"BUSY at {time_str} on {date_str}. Event: {busy_event_summary}")
            return ToolResult(True, "check_availability", f"FREE at {time_str} on {date_str}.")
        else:
            status = "BUSY -- has a prior commitment" if is_busy else "FREE -- available"
            return ToolResult(True, "check_availability", f"{status} at {time_str} on {date_str}.")

    # ── Gmail Tools ──

    async def _send_gmail(self, args: dict[str, Any]) -> ToolResult:
        to = args.get("to", "").strip()
        subject = args.get("subject", "").strip()
        body = args.get("body", "").strip()
        if not to or "@" not in to:
            return ToolResult(False, "send_gmail", "Invalid email address.")
        if not subject:
            return ToolResult(False, "send_gmail", "Subject is required.")
        if not body:
            return ToolResult(False, "send_gmail", "Body is required.")

        ok, output = await _run_gws("gmail", "+send", "--to", to, "--subject", subject, "--body", body)
        if not ok:
            return ToolResult(False, "send_gmail", output)
        return ToolResult(True, "send_gmail", f"Email sent to {to} with subject: '{subject}'")

    async def _read_inbox(self, args: dict[str, Any]) -> ToolResult:
        max_results = min(args.get("max_results", 5), 10)
        cmd = ["gmail", "+triage", "--max", str(max_results), "--format", "json"]
        query = args.get("query", "")
        if query:
            cmd.extend(["--query", query])

        ok, output = await _run_gws(*cmd)
        if not ok:
            return ToolResult(False, "read_inbox", output)
        projected = self._privacy.project_email(output, self._sender_jid)
        return ToolResult(True, "read_inbox", projected)

    # ── Sheets Tools ──

    async def _read_google_sheet(self, args: dict[str, Any]) -> ToolResult:
        sid = args.get("spreadsheet_id", "").strip()
        rng = args.get("range", "").strip()
        if not sid or not rng:
            return ToolResult(False, "read_google_sheet", "Missing: spreadsheet_id and range")

        ok, output = await _run_gws("sheets", "+read", "--spreadsheet", sid, "--range", rng)
        if not ok:
            return ToolResult(False, "read_google_sheet", output)
        projected = self._privacy.project_sheet(output, self._sender_jid)
        return ToolResult(True, "read_google_sheet", projected)

    async def _append_google_sheet(self, args: dict[str, Any]) -> ToolResult:
        sid = args.get("spreadsheet_id", "").strip()
        values = args.get("values", "").strip()
        if not sid or not values:
            return ToolResult(False, "append_google_sheet", "Missing: spreadsheet_id and values")

        ok, output = await _run_gws("sheets", "+append", "--spreadsheet", sid, "--values", values)
        if not ok:
            return ToolResult(False, "append_google_sheet", output)
        return ToolResult(True, "append_google_sheet", f"Row appended: {values}")
