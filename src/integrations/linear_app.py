"""Linear integration for HappyCapy WhatsApp bot.

Manage issues in Linear (modern issue tracker for software teams).
Admin-only. Token read from LINEAR_API_KEY env var.
Uses Linear's GraphQL API.

Tools:
  linear_create_issue — create an issue in a team
  linear_list_issues  — list issues with filters
  linear_get_teams    — list all teams (needed for creating issues)
  linear_update_issue — update status, priority, or assignee
"""

import os
from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult
from src.config_manager import is_admin

LINEAR_API = "https://api.linear.app/graphql"


# ── Tool Definitions ──

_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "linear_create_issue",
            "description": (
                "Create a new issue in Linear. "
                "Admin-only. Specify team, title, description, and priority."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Issue title (required).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Issue description (markdown supported).",
                    },
                    "team_name": {
                        "type": "string",
                        "description": "Team name to create the issue in. Use linear_get_teams if unsure.",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["urgent", "high", "medium", "low", "no_priority"],
                        "description": "Issue priority (default: medium).",
                    },
                    "label_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Label names to apply (e.g. ['bug', 'frontend']).",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "linear_list_issues",
            "description": (
                "List issues from Linear. Admin-only. "
                "Filter by team, status, or assignee."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team_name": {
                        "type": "string",
                        "description": "Filter by team name.",
                    },
                    "status": {
                        "type": "string",
                        "description": "Filter by status name, e.g. 'In Progress', 'Todo', 'Done'.",
                    },
                    "mine": {
                        "type": "boolean",
                        "description": "Show only issues assigned to me (default: false).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max issues to return (default 15).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "linear_get_teams",
            "description": "List all Linear teams. Admin-only. Use this to get valid team names.",
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
            "name": "linear_update_issue",
            "description": (
                "Update a Linear issue — change status, priority, title, or description. "
                "Admin-only. Requires the issue ID from list results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_id": {
                        "type": "string",
                        "description": "The Linear issue ID (e.g. 'abc123...').",
                    },
                    "title": {
                        "type": "string",
                        "description": "New title (optional).",
                    },
                    "description": {
                        "type": "string",
                        "description": "New description (optional).",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["urgent", "high", "medium", "low", "no_priority"],
                        "description": "New priority (optional).",
                    },
                    "state_name": {
                        "type": "string",
                        "description": "New workflow state name, e.g. 'In Progress', 'Done' (optional).",
                    },
                },
                "required": ["issue_id"],
            },
        },
    },
]

# Priority name → Linear numeric value
_PRIORITY_MAP = {
    "urgent": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
    "no_priority": 0,
}


# ── Integration class ──

class Integration(BaseIntegration):
    """Linear issue tracking — admin-only."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._sender_jid = ""
        self._token = os.environ.get("LINEAR_API_KEY", "")

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    def _is_admin(self) -> bool:
        sid = self._sender_jid.split("@")[0] if self._sender_jid else ""
        return is_admin(self.config, sid)

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="linear",
            display_name="Linear",
            description="Create and manage Linear issues directly from WhatsApp",
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
            "## Linear Integration (Admin Only)\n\n"
            "Manage software issues in Linear:\n"
            "- linear_get_teams: see all teams\n"
            "- linear_create_issue: create issue (title, team_name, priority, description)\n"
            "- linear_list_issues: list issues (filter by team/status/mine)\n"
            "- linear_update_issue: change status, priority, or title\n\n"
            "Priority: urgent > high > medium > low > no_priority.\n"
            "Always use linear_get_teams first if you don't know the team name.\n"
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self._is_admin():
            return ToolResult(False, tool_name, "Linear tools are admin-only.")
        if not self._token:
            return ToolResult(False, tool_name,
                "LINEAR_API_KEY not set. Get it from linear.app/settings/api.")
        handlers = {
            "linear_create_issue": self._create_issue,
            "linear_list_issues": self._list_issues,
            "linear_get_teams": self._get_teams,
            "linear_update_issue": self._update_issue,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(False, tool_name, f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return ToolResult(False, tool_name, f"Error: {type(e).__name__}: {e}")

    # ── GraphQL helper ──

    async def _gql(self, query: str, variables: dict | None = None) -> tuple[bool, dict]:
        """Execute a GraphQL query against Linear API."""
        import httpx
        headers = {
            "Authorization": self._token,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(LINEAR_API, json=payload, headers=headers)

        if resp.status_code != 200:
            return False, {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

        data = resp.json()
        if "errors" in data:
            msgs = "; ".join(e.get("message", str(e)) for e in data["errors"])
            return False, {"error": msgs}
        return True, data.get("data", {})

    # ── Team lookup helper ──

    async def _resolve_team_id(self, team_name: str) -> tuple[str | None, str]:
        """Return (team_id, team_name) for the given name, or (None, error_msg)."""
        ok, data = await self._gql("""
            query { teams { nodes { id name } } }
        """)
        if not ok:
            return None, data.get("error", "Failed to fetch teams")
        teams = data.get("teams", {}).get("nodes", [])
        name_lower = team_name.lower()
        for t in teams:
            if t["name"].lower() == name_lower:
                return t["id"], t["name"]
        team_names = [t["name"] for t in teams]
        return None, f"Team '{team_name}' not found. Available: {', '.join(team_names)}"

    # ── Tool handlers ──

    async def _get_teams(self, args: dict) -> ToolResult:
        ok, data = await self._gql("""
            query {
                teams {
                    nodes { id name description memberCount }
                }
            }
        """)
        if not ok:
            return ToolResult(False, "linear_get_teams", data.get("error", "Failed"))
        teams = data.get("teams", {}).get("nodes", [])
        if not teams:
            return ToolResult(True, "linear_get_teams", "No teams found.")
        lines = [f"- {t['name']} ({t.get('memberCount', '?')} members, id:{t['id'][:8]}...)"
                 for t in teams]
        return ToolResult(True, "linear_get_teams",
                          f"Teams ({len(teams)}):\n" + "\n".join(lines))

    async def _create_issue(self, args: dict) -> ToolResult:
        title = args.get("title", "").strip()
        if not title:
            return ToolResult(False, "linear_create_issue", "Missing title.")

        variables: dict[str, Any] = {"title": title}

        if desc := args.get("description", "").strip():
            variables["description"] = desc

        priority_name = args.get("priority", "medium")
        variables["priority"] = _PRIORITY_MAP.get(priority_name, 3)

        team_name = args.get("team_name", "").strip()
        if team_name:
            team_id, err = await self._resolve_team_id(team_name)
            if not team_id:
                return ToolResult(False, "linear_create_issue", err)
            variables["teamId"] = team_id
        else:
            # Use first team as default
            ok, data = await self._gql("query { teams { nodes { id name } } }")
            if not ok or not data.get("teams", {}).get("nodes"):
                return ToolResult(False, "linear_create_issue",
                                  "No teams found. Specify team_name.")
            first_team = data["teams"]["nodes"][0]
            variables["teamId"] = first_team["id"]
            team_name = first_team["name"]

        mutation = """
            mutation CreateIssue($title: String!, $description: String,
                                 $teamId: String!, $priority: Int) {
                issueCreate(input: {
                    title: $title,
                    description: $description,
                    teamId: $teamId,
                    priority: $priority
                }) {
                    success
                    issue {
                        id
                        identifier
                        url
                        title
                    }
                }
            }
        """
        ok, data = await self._gql(mutation, variables)
        if not ok:
            return ToolResult(False, "linear_create_issue", data.get("error", "Failed"))

        result = data.get("issueCreate", {})
        if not result.get("success"):
            return ToolResult(False, "linear_create_issue", "Issue creation failed.")

        issue = result.get("issue", {})
        return ToolResult(
            True, "linear_create_issue",
            f"Issue created: [{issue.get('identifier', '?')}] {title}\n"
            f"Team: {team_name} | Priority: {priority_name}\n"
            f"URL: {issue.get('url', '')}"
        )

    async def _list_issues(self, args: dict) -> ToolResult:
        limit = min(int(args.get("limit", 15)), 50)
        filters: list[str] = []
        variables: dict[str, Any] = {"first": limit}

        team_name = args.get("team_name", "").strip()
        if team_name:
            team_id, err = await self._resolve_team_id(team_name)
            if not team_id:
                return ToolResult(False, "linear_list_issues", err)
            filters.append("team: { id: { eq: $teamId } }")
            variables["teamId"] = team_id

        status = args.get("status", "").strip()
        if status:
            filters.append("state: { name: { eq: $stateName } }")
            variables["stateName"] = status

        mine = args.get("mine", False)
        if mine:
            filters.append("assignee: { isMe: { eq: true } }")

        filter_str = ""
        if filters:
            filter_str = f"filter: {{ {', '.join(filters)} }}"

        query = f"""
            query ListIssues($first: Int, $teamId: ID, $stateName: String) {{
                issues({filter_str} first: $first orderBy: updatedAt) {{
                    nodes {{
                        id identifier title
                        state {{ name }}
                        priority
                        assignee {{ name }}
                        team {{ name }}
                        updatedAt
                    }}
                }}
            }}
        """
        ok, data = await self._gql(query, variables)
        if not ok:
            return ToolResult(False, "linear_list_issues", data.get("error", "Failed"))

        issues = data.get("issues", {}).get("nodes", [])
        if not issues:
            return ToolResult(True, "linear_list_issues", "No issues found.")

        prio_names = {0: "—", 1: "🚨", 2: "🔴", 3: "🟡", 4: "🟢"}
        lines = []
        for iss in issues:
            prio = prio_names.get(iss.get("priority", 0), "")
            state = iss.get("state", {}).get("name", "?")
            assignee = iss.get("assignee", {}).get("name", "") if iss.get("assignee") else ""
            team = iss.get("team", {}).get("name", "")
            assignee_str = f" @{assignee}" if assignee else ""
            lines.append(
                f"{prio} [{iss.get('identifier', '?')}] {iss['title']} "
                f"[{state}] {team}{assignee_str} (id:{iss['id'][:8]}...)"
            )

        return ToolResult(True, "linear_list_issues",
                          f"Issues ({len(issues)}):\n" + "\n".join(lines))

    async def _update_issue(self, args: dict) -> ToolResult:
        issue_id = args.get("issue_id", "").strip()
        if not issue_id:
            return ToolResult(False, "linear_update_issue", "Missing issue_id.")

        update: dict[str, Any] = {}
        if title := args.get("title", "").strip():
            update["title"] = title
        if desc := args.get("description", "").strip():
            update["description"] = desc
        if priority_name := args.get("priority", "").strip():
            update["priority"] = _PRIORITY_MAP.get(priority_name, 3)

        state_name = args.get("state_name", "").strip()
        if state_name:
            # Resolve state ID for this issue's team
            state_query = """
                query GetIssueStates($id: String!) {
                    issue(id: $id) {
                        team {
                            states { nodes { id name } }
                        }
                    }
                }
            """
            ok, data = await self._gql(state_query, {"id": issue_id})
            if ok:
                states = (data.get("issue", {}).get("team", {})
                          .get("states", {}).get("nodes", []))
                state_name_lower = state_name.lower()
                for s in states:
                    if s["name"].lower() == state_name_lower:
                        update["stateId"] = s["id"]
                        break
                if "stateId" not in update:
                    state_names = [s["name"] for s in states]
                    return ToolResult(False, "linear_update_issue",
                                      f"State '{state_name}' not found. "
                                      f"Available: {', '.join(state_names)}")

        if not update:
            return ToolResult(False, "linear_update_issue",
                              "Nothing to update — provide title, description, priority, or state_name.")

        mutation = """
            mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
                issueUpdate(id: $id, input: $input) {
                    success
                    issue { identifier title state { name } }
                }
            }
        """
        ok, data = await self._gql(mutation, {"id": issue_id, "input": update})
        if not ok:
            return ToolResult(False, "linear_update_issue", data.get("error", "Failed"))

        result = data.get("issueUpdate", {})
        if not result.get("success"):
            return ToolResult(False, "linear_update_issue", "Update failed.")

        issue = result.get("issue", {})
        changes = []
        if "title" in update:
            changes.append(f"title → '{update['title']}'")
        if "stateId" in update:
            changes.append(f"state → '{state_name}'")
        if "priority" in update:
            changes.append(f"priority → '{args.get('priority')}'")

        return ToolResult(
            True, "linear_update_issue",
            f"Updated [{issue.get('identifier', '?')}] {issue.get('title', '')}\n"
            f"Changes: {', '.join(changes)}"
        )
