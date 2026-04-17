"""Todoist integration for HappyCapy WhatsApp bot.

Manage tasks, projects, and to-do lists via the Todoist REST API v2.
Admin-only. Token read from TODOIST_API_TOKEN env var.

Tools:
  todoist_add_task      — create a task (with optional due date/priority/project)
  todoist_list_tasks    — list tasks with optional filter
  todoist_complete_task — mark a task as done (by ID or title search)
  todoist_get_projects  — list all projects (useful to know valid project names)
"""

import os
from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult
from src.config_manager import is_admin

TODOIST_API = "https://api.todoist.com/rest/v2"


# ── Tool Definitions ──

_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "todoist_add_task",
            "description": (
                "Create a new task in Todoist. "
                "Admin-only. Supports due dates, priority levels, and project assignment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Task name/content (required).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional notes or details for the task.",
                    },
                    "due_string": {
                        "type": "string",
                        "description": (
                            "Natural language due date, e.g. 'tomorrow', 'next Monday', "
                            "'Jan 15', 'every Friday'. Leave blank for no due date."
                        ),
                    },
                    "priority": {
                        "type": "integer",
                        "enum": [1, 2, 3, 4],
                        "description": "Priority: 1=normal, 2=medium, 3=high, 4=urgent (p1 in Todoist UI).",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project name to add the task to (default: Inbox).",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Label names to apply (e.g. ['work', 'whatsapp']).",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todoist_list_tasks",
            "description": (
                "List tasks from Todoist. "
                "Admin-only. Can filter by project or use Todoist's filter query language."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": (
                            "Todoist filter query, e.g. 'today', 'overdue', "
                            "'p1', '#Work', '@label', 'due before: +7 days'. "
                            "Leave blank to list all active tasks."
                        ),
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Filter by project name (alternative to filter query).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max tasks to return (default 20).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todoist_complete_task",
            "description": (
                "Mark a Todoist task as completed. "
                "Admin-only. Find by task ID or title search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Exact Todoist task ID (from list results). Use this if you know it.",
                    },
                    "title_search": {
                        "type": "string",
                        "description": "Partial task title to search for (case-insensitive). "
                                       "Completes the first matching task.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todoist_get_projects",
            "description": "List all Todoist projects. Admin-only. Useful to get project names/IDs.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ── Integration class ──

class Integration(BaseIntegration):
    """Todoist task management — admin-only."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._sender_jid = ""
        self._token = os.environ.get("TODOIST_API_TOKEN", "")

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    def _is_admin(self) -> bool:
        sid = self._sender_jid.split("@")[0] if self._sender_jid else ""
        return is_admin(self.config, sid)

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="todoist",
            display_name="Todoist",
            description="Add, list, and complete Todoist tasks directly from WhatsApp",
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
            "## Todoist Integration (Admin Only)\n\n"
            "Manage your Todoist tasks:\n"
            "- todoist_add_task: create tasks with due dates, priority, project\n"
            "- todoist_list_tasks: view tasks (filter: 'today', 'overdue', '#Project', 'p1')\n"
            "- todoist_complete_task: mark done by task ID or title search\n"
            "- todoist_get_projects: see all project names\n\n"
            "Priority levels: 1=normal, 2=medium, 3=high, 4=urgent.\n"
            "Tip: use 'tomorrow 9am' or 'every Monday' for due_string.\n"
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self._is_admin():
            return ToolResult(False, tool_name, "Todoist tools are admin-only.")
        if not self._token:
            return ToolResult(False, tool_name,
                "TODOIST_API_TOKEN not set. Get it from todoist.com/app/settings/integrations.")
        handlers = {
            "todoist_add_task": self._add_task,
            "todoist_list_tasks": self._list_tasks,
            "todoist_complete_task": self._complete_task,
            "todoist_get_projects": self._get_projects,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(False, tool_name, f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return ToolResult(False, tool_name, f"Error: {type(e).__name__}: {e}")

    # ── HTTP helper ──

    async def _req(self, method: str, path: str, **kwargs) -> tuple[bool, Any]:
        """Make a Todoist API request."""
        import httpx
        url = f"{TODOIST_API}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await getattr(client, method.lower())(url, headers=headers, **kwargs)
        if resp.status_code in (200, 204):
            try:
                return True, resp.json()
            except Exception:
                return True, {}
        return False, {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    # ── Project lookup helper ──

    async def _get_project_id(self, name: str) -> str | None:
        """Find a project ID by name (case-insensitive). Returns None if not found."""
        ok, data = await self._req("GET", "/projects")
        if not ok or not isinstance(data, list):
            return None
        name_lower = name.lower()
        for p in data:
            if p.get("name", "").lower() == name_lower:
                return p["id"]
        return None

    # ── Tool handlers ──

    async def _add_task(self, args: dict) -> ToolResult:
        content = args.get("content", "").strip()
        if not content:
            return ToolResult(False, "todoist_add_task", "Missing task content.")

        payload: dict[str, Any] = {"content": content}

        if desc := args.get("description", "").strip():
            payload["description"] = desc

        if due := args.get("due_string", "").strip():
            payload["due_string"] = due

        priority = args.get("priority")
        if priority and isinstance(priority, int) and 1 <= priority <= 4:
            payload["priority"] = priority

        if labels := args.get("labels", []):
            if isinstance(labels, list):
                payload["labels"] = [str(l) for l in labels]

        project_name = args.get("project_name", "").strip()
        if project_name:
            project_id = await self._get_project_id(project_name)
            if project_id:
                payload["project_id"] = project_id
            else:
                # Project not found — still add to inbox but warn
                content_note = f" (note: project '{project_name}' not found, added to Inbox)"
            # else just proceed without project_id

        ok, data = await self._req("POST", "/tasks", json=payload)
        if not ok:
            return ToolResult(False, "todoist_add_task",
                              f"Failed to create task: {data.get('error', data)}")

        task_id = data.get("id", "?")
        due_info = ""
        if data.get("due"):
            due_info = f"\nDue: {data['due'].get('string', data['due'].get('date', ''))}"
        prio_labels = {1: "", 2: " (medium)", 3: " (high)", 4: " (urgent!)"}
        prio_str = prio_labels.get(data.get("priority", 1), "")

        return ToolResult(
            True, "todoist_add_task",
            f"Task added{prio_str}: {content}{due_info}\nID: {task_id}"
        )

    async def _list_tasks(self, args: dict) -> ToolResult:
        limit = min(int(args.get("limit", 20)), 100)
        params: dict[str, Any] = {}

        project_name = args.get("project_name", "").strip()
        if project_name:
            project_id = await self._get_project_id(project_name)
            if project_id:
                params["project_id"] = project_id
            else:
                return ToolResult(False, "todoist_list_tasks",
                                  f"Project '{project_name}' not found.")

        filter_q = args.get("filter", "").strip()
        if filter_q:
            params["filter"] = filter_q

        ok, data = await self._req("GET", "/tasks", params=params)
        if not ok:
            return ToolResult(False, "todoist_list_tasks",
                              f"Failed: {data.get('error', data)}")

        if not isinstance(data, list) or not data:
            return ToolResult(True, "todoist_list_tasks", "No tasks found.")

        tasks = data[:limit]
        prio_icons = {1: "○", 2: "◔", 3: "◑", 4: "●"}
        lines = []
        for t in tasks:
            icon = prio_icons.get(t.get("priority", 1), "○")
            due = ""
            if t.get("due"):
                due = f" [{t['due'].get('string', t['due'].get('date', ''))}]"
            project = f" #{t.get('project_id', '')}" if t.get("project_id") else ""
            lines.append(f"{icon} {t['content']}{due} (id:{t['id']}){project}")

        return ToolResult(
            True, "todoist_list_tasks",
            f"Tasks ({len(tasks)}):\n" + "\n".join(lines)
        )

    async def _complete_task(self, args: dict) -> ToolResult:
        task_id = args.get("task_id", "").strip()
        title_search = args.get("title_search", "").strip()

        if not task_id and not title_search:
            return ToolResult(False, "todoist_complete_task",
                              "Provide task_id or title_search.")

        if not task_id and title_search:
            # Search for the task
            ok, data = await self._req("GET", "/tasks")
            if not ok or not isinstance(data, list):
                return ToolResult(False, "todoist_complete_task", "Failed to fetch tasks.")
            query = title_search.lower()
            matches = [t for t in data if query in t.get("content", "").lower()]
            if not matches:
                return ToolResult(False, "todoist_complete_task",
                                  f"No task found matching '{title_search}'.")
            task_id = matches[0]["id"]
            task_name = matches[0]["content"]
        else:
            task_name = task_id  # fallback display

        ok, _ = await self._req("POST", f"/tasks/{task_id}/close")
        if not ok:
            return ToolResult(False, "todoist_complete_task",
                              f"Failed to complete task {task_id}.")

        return ToolResult(True, "todoist_complete_task",
                          f"Completed: {task_name} ✓")

    async def _get_projects(self, args: dict) -> ToolResult:
        ok, data = await self._req("GET", "/projects")
        if not ok:
            return ToolResult(False, "todoist_get_projects",
                              f"Failed: {data.get('error', data)}")
        if not isinstance(data, list) or not data:
            return ToolResult(True, "todoist_get_projects", "No projects found.")

        lines = [f"- {p['name']} (id:{p['id']})" for p in data]
        return ToolResult(
            True, "todoist_get_projects",
            f"Projects ({len(data)}):\n" + "\n".join(lines)
        )
