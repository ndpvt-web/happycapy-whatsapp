"""Voice Dump integration for HappyCapy WhatsApp bot.

Admin-only. When the owner sends a voice note, the LLM classifies the
transcribed content and routes each item to the right destination:
Calendar, Google Docs (person notes), CRM Sheet, Gmail, or Sheets.

Tools: create_person_doc, append_person_doc, log_crm_interaction, search_person_docs
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult

GWS_BIN = os.path.expanduser("~/.cargo/bin/gws")
GWS_TIMEOUT = 30
REGISTRY_PATH = os.path.expanduser("~/.happycapy-whatsapp/voice_dump_registry.json")


# ── Tool Definitions (OpenAI format) ──

_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "create_person_doc",
            "description": (
                "Create a new Google Doc for a person to store CRM notes. "
                "Admin-only. Returns the document ID. Use when you first "
                "encounter a person the owner mentions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "person_name": {
                        "type": "string",
                        "description": "Full name of the person (e.g. 'John Smith').",
                    },
                    "initial_notes": {
                        "type": "string",
                        "description": "Optional initial notes to add to the doc.",
                    },
                },
                "required": ["person_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_person_doc",
            "description": (
                "Append dated notes to an existing person's Google Doc. "
                "Admin-only. Auto-creates the doc if the person doesn't have one yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "person_name": {
                        "type": "string",
                        "description": "Full name of the person.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Notes to append (meeting notes, conversation summary, etc.).",
                    },
                },
                "required": ["person_name", "notes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_crm_interaction",
            "description": (
                "Log an interaction to the master CRM Google Sheet. "
                "Admin-only. Adds a row: Date | Person | Type | Summary | Follow-up | Doc Link."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "person_name": {
                        "type": "string",
                        "description": "Full name of the person.",
                    },
                    "interaction_type": {
                        "type": "string",
                        "enum": ["meeting", "call", "chat", "note", "email"],
                        "description": "Type of interaction.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of the interaction.",
                    },
                    "follow_up": {
                        "type": "string",
                        "description": "Optional follow-up action or reminder.",
                    },
                },
                "required": ["person_name", "interaction_type", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_person_docs",
            "description": (
                "Search Google Drive for existing person CRM docs by name. "
                "Admin-only. Returns matching doc names and IDs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Person name or partial name to search for.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


# ── Helper: run gws CLI (self-contained copy) ──

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
        return False, err or output or f"gws exited with code {proc.returncode}"
    except asyncio.TimeoutError:
        return False, f"gws command timed out after {timeout}s"
    except FileNotFoundError:
        return False, f"gws binary not found at {GWS_BIN}"
    except Exception as e:
        return False, f"gws error: {type(e).__name__}: {e}"


# ── Registry: person name -> doc ID mapping ──

def _load_registry() -> dict:
    try:
        with open(REGISTRY_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"people": {}, "crm_sheet_id": None}


def _save_registry(reg: dict) -> None:
    with open(REGISTRY_PATH, "w") as f:
        json.dump(reg, f, indent=2)


# ── Integration class ──

class Integration(BaseIntegration):
    """Voice Dump integration -- admin-only tools for routing voice note content."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._sender_jid: str = ""

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    def _is_admin(self) -> bool:
        admin = self.config.get("admin_number", "")
        return admin and admin in self._sender_jid

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="voice_dump",
            display_name="Voice Dump Assistant",
            description="Admin-only voice note routing: Calendar, Docs, CRM, Gmail",
        )

    @classmethod
    def visibility(cls) -> str:
        return "admin"

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        return (
            "## Voice Dump Assistant (Admin Only)\n\n"
            "When the admin sends a voice note, the transcribed text often contains "
            "MULTIPLE pieces of information that need routing to different destinations.\n\n"
            "VOICE DUMP CLASSIFICATION PROTOCOL:\n"
            "1. Parse the transcribed text and identify ALL discrete information items.\n"
            "2. Classify each item:\n"
            "   - CALENDAR: meetings, appointments, time-specific events -> use create_event\n"
            "   - PERSON_NOTES: 'I met X', 'talked to X about Y' -> use append_person_doc + log_crm_interaction\n"
            "   - TASK: 'remind me to', 'I need to', 'don't forget' -> use create_event (as timed reminder)\n"
            "   - EMAIL: 'send email to X about Y' -> use send_gmail\n"
            "   - SHEET: 'add this to spreadsheet', 'log this data' -> use append_google_sheet\n\n"
            "3. Present a STRUCTURED PLAN (do NOT call any tools yet):\n"
            "   Format each line as:\n"
            "   1. [CALENDAR] Meeting with John -- 27 Mar 2pm-2:30pm\n"
            "   2. [PERSON] John Smith -- discussed partnership (-> Doc + CRM)\n"
            "   3. [TASK] Follow up on contract by Friday (-> Calendar: 28 Mar 9am)\n"
            "   4. [EMAIL] Draft to john@example.com re: partnership\n\n"
            "   Then say: Reply 'y' to execute all, or tell me what to change.\n\n"
            "4. On admin reply 'y', 'yes', 'ok', or thumbs-up emoji: execute ALL planned actions using tools.\n"
            "5. On any other reply: treat as edits to the plan, revise and re-present.\n\n"
            "IMPORTANT: This protocol is ONLY for the admin's voice notes.\n"
            "IMPORTANT: For calendar/email/sheet routes, use the EXISTING tools "
            "(create_event, send_gmail, append_google_sheet). Only use voice_dump tools "
            "(create_person_doc, append_person_doc, log_crm_interaction, search_person_docs) "
            "for person-related routing.\n"
            "IMPORTANT: When creating calendar reminders for tasks, set the event summary "
            "to the task description and schedule it at the appropriate time."
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self._is_admin():
            return ToolResult(False, tool_name, "Voice dump tools are admin-only.")
        handlers = {
            "create_person_doc": self._create_person_doc,
            "append_person_doc": self._append_person_doc,
            "log_crm_interaction": self._log_crm_interaction,
            "search_person_docs": self._search_person_docs,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(False, tool_name, f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return ToolResult(False, tool_name, f"Error: {type(e).__name__}: {e}")

    # ── Tool Handlers (placeholders -- filled via Edit) ──

    async def _create_person_doc(self, args: dict) -> ToolResult:
        name = args.get("person_name", "").strip()
        if not name:
            return ToolResult(False, "create_person_doc", "Missing person_name")

        reg = _load_registry()
        key = name.lower()
        if key in reg["people"]:
            doc_id = reg["people"][key]["doc_id"]
            return ToolResult(True, "create_person_doc",
                              f"Doc already exists for '{name}' (ID: {doc_id})")

        # Create blank doc
        ok, output = await _run_gws(
            "docs", "documents", "create",
            "--json", json.dumps({"title": f"CRM - {name}"}),
            "--format", "json",
        )
        if not ok:
            return ToolResult(False, "create_person_doc", f"Failed to create doc: {output}")

        try:
            doc_data = json.loads(output)
            doc_id = doc_data.get("documentId", "")
        except (json.JSONDecodeError, KeyError):
            return ToolResult(False, "create_person_doc", f"Bad response: {output[:200]}")

        if not doc_id:
            return ToolResult(False, "create_person_doc", "No documentId in response")

        # Write initial header
        today = datetime.now().strftime("%Y-%m-%d")
        header = f"CRM Notes: {name}\nCreated: {today}\n"
        initial_notes = args.get("initial_notes", "")
        if initial_notes:
            header += f"\n--- {today} ---\n{initial_notes}\n"

        await _run_gws("docs", "+write", "--document", doc_id, "--text", header)

        # Save to registry
        reg["people"][key] = {"doc_id": doc_id, "name": name, "created": today}
        _save_registry(reg)

        return ToolResult(True, "create_person_doc",
                          f"Created doc 'CRM - {name}' (ID: {doc_id})")

    async def _append_person_doc(self, args: dict) -> ToolResult:
        name = args.get("person_name", "").strip()
        notes = args.get("notes", "").strip()
        if not name or not notes:
            return ToolResult(False, "append_person_doc", "Missing person_name or notes")

        reg = _load_registry()
        key = name.lower()

        # Auto-create if not found
        if key not in reg["people"]:
            create_result = await self._create_person_doc({"person_name": name})
            if not create_result.success:
                return create_result
            reg = _load_registry()  # Reload after creation

        doc_id = reg["people"][key]["doc_id"]
        today = datetime.now().strftime("%Y-%m-%d %H:%M")
        text = f"\n\n--- {today} ---\n{notes}"

        ok, output = await _run_gws("docs", "+write", "--document", doc_id, "--text", text)
        if not ok:
            return ToolResult(False, "append_person_doc", f"Failed to append: {output}")

        return ToolResult(True, "append_person_doc",
                          f"Notes appended to '{name}' doc (ID: {doc_id})")

    async def _log_crm_interaction(self, args: dict) -> ToolResult:
        name = args.get("person_name", "").strip()
        itype = args.get("interaction_type", "note")
        summary = args.get("summary", "").strip()
        follow_up = args.get("follow_up", "")
        if not name or not summary:
            return ToolResult(False, "log_crm_interaction", "Missing person_name or summary")

        reg = _load_registry()

        # Auto-create CRM sheet if needed
        sheet_id = reg.get("crm_sheet_id")
        if not sheet_id:
            ok, output = await _run_gws(
                "sheets", "spreadsheets", "create",
                "--json", json.dumps({"properties": {"title": "CRM Master"}}),
                "--format", "json",
            )
            if not ok:
                return ToolResult(False, "log_crm_interaction",
                                  f"Failed to create CRM sheet: {output}")
            try:
                sheet_data = json.loads(output)
                sheet_id = sheet_data.get("spreadsheetId", "")
            except (json.JSONDecodeError, KeyError):
                return ToolResult(False, "log_crm_interaction",
                                  f"Bad response: {output[:200]}")

            if not sheet_id:
                return ToolResult(False, "log_crm_interaction", "No spreadsheetId in response")

            # Write header row
            header_values = [["Date", "Person", "Type", "Summary", "Follow-up", "Doc Link"]]
            await _run_gws(
                "sheets", "spreadsheets", "values", "append",
                "--params", json.dumps({
                    "spreadsheetId": sheet_id,
                    "range": "Sheet1!A:F",
                    "valueInputOption": "USER_ENTERED",
                }),
                "--json", json.dumps({"values": header_values}),
            )
            reg["crm_sheet_id"] = sheet_id
            _save_registry(reg)

        # Look up doc link for this person
        key = name.lower()
        doc_link = ""
        if key in reg["people"]:
            doc_id = reg["people"][key]["doc_id"]
            doc_link = f"https://docs.google.com/document/d/{doc_id}"

        today = datetime.now().strftime("%Y-%m-%d")
        row = [[today, name, itype, summary, follow_up, doc_link]]

        ok, output = await _run_gws(
            "sheets", "spreadsheets", "values", "append",
            "--params", json.dumps({
                "spreadsheetId": sheet_id,
                "range": "Sheet1!A:F",
                "valueInputOption": "USER_ENTERED",
            }),
            "--json", json.dumps({"values": row}),
        )
        if not ok:
            return ToolResult(False, "log_crm_interaction", f"Failed to log: {output}")

        return ToolResult(True, "log_crm_interaction",
                          f"Logged {itype} with {name} to CRM sheet")

    async def _search_person_docs(self, args: dict) -> ToolResult:
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(False, "search_person_docs", "Missing query")

        # Search Drive for CRM docs matching the name
        ok, output = await _run_gws(
            "drive", "files", "list",
            "--params", json.dumps({
                "q": f"name contains 'CRM - {query}' and mimeType='application/vnd.google-apps.document'",
                "fields": "files(id,name)",
            }),
            "--format", "json",
        )
        if not ok:
            return ToolResult(False, "search_person_docs", f"Search failed: {output}")

        try:
            data = json.loads(output)
            files = data.get("files", [])
        except (json.JSONDecodeError, KeyError):
            files = []

        if not files:
            # Also check local registry
            reg = _load_registry()
            matches = [
                {"name": v["name"], "id": v["doc_id"]}
                for k, v in reg["people"].items()
                if query.lower() in k
            ]
            if matches:
                lines = [f"- {m['name']} (ID: {m['id']})" for m in matches]
                return ToolResult(True, "search_person_docs",
                                  f"Found in local registry:\n" + "\n".join(lines))
            return ToolResult(True, "search_person_docs",
                              f"No person docs found matching '{query}'")

        lines = [f"- {f.get('name', '?')} (ID: {f.get('id', '?')})" for f in files]
        return ToolResult(True, "search_person_docs",
                          f"Found {len(files)} doc(s):\n" + "\n".join(lines))
