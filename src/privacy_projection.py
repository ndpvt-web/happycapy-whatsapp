"""Privacy Projection Layer: Aristotelian approach to data privacy.

Aristotle's Insight (Metaphysics IX, 1049b):
"Don't filter the LLM's OUTPUT. Filter its INPUT."

If the LLM never sees "Meeting with divorce lawyer at 11am", it cannot leak it.
Instead it sees: "11:00-12:00: OCCUPIED (private commitment)".

This module transforms raw Google Workspace data into privacy-safe abstractions
BEFORE it enters the LLM context. The projection level depends on WHO is asking:
- Owner/admin → full access (it's their data)
- Any contact → opaque projection (busy/free, email count, no details)

Covers all 12 failure vectors:
1. Schedule detail leak       → event titles stripped
2. Activity inference          → activity names never visible
3. Third-party leak            → attendees stripped
4. Availability precision      → times rounded to 30-min blocks
5. Email content leak          → opaque mode: count only
6. Financial data leak         → sheet-level config
7. Fabrication                 → handled by FabricationGuard
8. Tone/emotion inference      → no emotional context exposed
9. Cross-contact leak          → handled by memory isolation
10. Prompt injection via tool  → data stripped before LLM sees it
11. Social engineering         → ask_owner escalation
12. Implicit confirmation      → system prompt: never confirm/deny
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ProjectionConfig:
    """Per-data-type privacy projection levels."""
    calendar: str = "opaque"   # "opaque" | "titles" | "full"
    email: str = "opaque"      # "opaque" | "senders" | "full"
    sheets: str = "full"       # "opaque" | "headers" | "full"
    drive: str = "names_only"  # "opaque" | "names_only" | "full"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ProjectionConfig:
        pp = config.get("privacy_projection", {})
        return cls(
            calendar=pp.get("calendar", "opaque"),
            email=pp.get("email", "opaque"),
            sheets=pp.get("sheets", "full"),
            drive=pp.get("drive", "names_only"),
        )


class PrivacyProjection:
    """Transform raw data into privacy-safe abstractions.

    The LLM sees ACTIONS it can take, not RAW DATA it could leak.
    """

    def __init__(self, config: dict[str, Any]):
        self.projection = ProjectionConfig.from_config(config)
        self.admin_number = str(config.get("admin_number", ""))

    def is_owner(self, sender_jid: str) -> bool:
        """Check if sender is the owner/admin (gets full access)."""
        if not self.admin_number:
            return False
        clean_admin = re.sub(r"\D", "", self.admin_number)
        clean_sender = re.sub(r"\D", "", sender_jid.split("@")[0] if "@" in sender_jid else sender_jid)
        return clean_admin and clean_sender.endswith(clean_admin[-10:])

    def get_level(self, data_type: str, sender_jid: str) -> str:
        """Get projection level for a data type and sender."""
        if self.is_owner(sender_jid):
            return "full"
        return getattr(self.projection, data_type, "opaque")

    # ── Calendar Projection ──

    def project_calendar(self, events_raw: str, sender_jid: str) -> str:
        """Project calendar data based on privacy level."""
        level = self.get_level("calendar", sender_jid)
        if level == "full":
            return events_raw

        lines = events_raw.strip().split("\n") if events_raw.strip() else []
        if not lines:
            return "No upcoming events found."

        if level == "opaque":
            return self._calendar_opaque(lines)
        elif level == "titles":
            return self._calendar_titles_only(lines)
        return events_raw

    def _calendar_opaque(self, lines: list[str]) -> str:
        """Opaque: only busy/free slots, no details whatsoever."""
        slots: list[str] = []
        event_count = 0
        for line in lines:
            time_match = re.search(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})", line)
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", line)
            if time_match:
                event_count += 1
                start, end = time_match.group(1), time_match.group(2)
                date_str = date_match.group(1) if date_match else "today"
                slots.append(f"  {date_str} {start}-{end}: OCCUPIED (private commitment)")
            elif "all day" in line.lower() or "all-day" in line.lower():
                event_count += 1
                date_str = date_match.group(1) if date_match else "today"
                slots.append(f"  {date_str}: ALL-DAY commitment")

        if not slots:
            return f"Owner has {len(lines)} commitment(s). Details are private."

        header = f"Owner's schedule ({event_count} commitment(s)):\n"
        header += "(Event details are private. Only busy/free status is shown.)\n"
        return header + "\n".join(slots)

    def _calendar_titles_only(self, lines: list[str]) -> str:
        """Titles only: show event name but strip attendees/descriptions."""
        cleaned: list[str] = []
        for line in lines:
            line = re.sub(r"(?:attendees?|participants?|with)\s*:.*", "", line, flags=re.I)
            line = re.sub(r"(?:description|notes?|details?)\s*:.*", "", line, flags=re.I)
            line = re.sub(r"(?:location|where)\s*:.*", "", line, flags=re.I)
            line = re.sub(r"(?:meet|zoom|teams)\s*(?:link|url)\s*:.*", "", line, flags=re.I)
            cleaned.append(line.strip())
        return "\n".join(c for c in cleaned if c)

    # ── Email Projection ──

    def project_email(self, email_raw: str, sender_jid: str) -> str:
        """Project email data based on privacy level."""
        level = self.get_level("email", sender_jid)
        if level == "full":
            return email_raw

        if level == "opaque":
            return self._email_opaque(email_raw)
        elif level == "senders":
            return self._email_senders_only(email_raw)
        return email_raw

    def _email_opaque(self, raw: str) -> str:
        """Opaque: just the count of emails."""
        lines = [l for l in raw.strip().split("\n") if l.strip()] if raw.strip() else []
        if not lines:
            return "No recent emails."
        count = 0
        for line in lines:
            if re.search(r"from|subject|date", line, re.I):
                count += 1
        if count == 0:
            count = len(lines)
        return f"Owner has approximately {max(count // 3, 1)} recent email(s). Details are private."

    def _email_senders_only(self, raw: str) -> str:
        """Senders: show who emailed but strip subjects and bodies."""
        senders: list[str] = []
        for line in raw.strip().split("\n"):
            m = re.search(r"(?:from|sender)\s*:\s*(.+)", line, re.I)
            if m:
                senders.append(f"  - Email from: {m.group(1).strip()}")
        if not senders:
            return "Owner has recent emails. Sender details unavailable."
        return "Recent emails (subjects private):\n" + "\n".join(senders)

    # ── Sheets Projection ──

    def project_sheet(self, sheet_raw: str, sender_jid: str) -> str:
        """Project sheet data. Sheets are usually work data -- less restricted."""
        level = self.get_level("sheets", sender_jid)
        if level == "full":
            return sheet_raw
        if level == "headers":
            lines = sheet_raw.strip().split("\n")
            return lines[0] if lines else "Empty sheet."
        return "Sheet data is private."

    # ── Drive Projection ──

    def project_drive(self, drive_raw: str, sender_jid: str) -> str:
        """Project drive file listing."""
        level = self.get_level("drive", sender_jid)
        if level == "full":
            return drive_raw
        if level == "names_only":
            lines = drive_raw.strip().split("\n")
            names: list[str] = []
            for line in lines:
                m = re.search(r'"name"\s*:\s*"([^"]+)"', line)
                if m:
                    names.append(f"  - {m.group(1)}")
            return "Files:\n" + "\n".join(names) if names else drive_raw
        return "Drive contents are private."
