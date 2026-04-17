"""Notion integration for HappyCapy WhatsApp bot.

Full workspace control -- never open Notion again.
Admin-only. All tools gated by is_admin().

Tools:
  READ:   notion_search, notion_read_page, notion_query_database, notion_get_database_schema
  CREATE: notion_create_page, notion_create_database, notion_add_database_entry
  UPDATE: notion_update_page, notion_append_blocks
  DELETE: notion_delete_page, notion_delete_block
"""

import json
import os
from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult
from src.config_manager import is_admin

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


# ── Tool Definitions ──

_TOOL_DEFINITIONS: list[dict] = [
    # --- READ ---
    {
        "type": "function",
        "function": {
            "name": "notion_search",
            "description": (
                "Search the owner's Notion workspace for pages and databases by keyword. "
                "Admin-only. Returns titles, IDs, and types."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. 'tasks', 'meeting notes').",
                    },
                    "filter_type": {
                        "type": "string",
                        "enum": ["page", "database"],
                        "description": "Optional: only pages or databases.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_read_page",
            "description": (
                "Read a Notion page's properties and block content. "
                "Admin-only. Returns text representation of the page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "The Notion page ID (UUID).",
                    },
                },
                "required": ["page_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_query_database",
            "description": (
                "Query a Notion database to list entries with properties. "
                "Admin-only. Supports filters and sorting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "database_id": {
                        "type": "string",
                        "description": "The database ID to query.",
                    },
                    "filter_json": {
                        "type": "string",
                        "description": "Optional Notion filter as JSON string.",
                    },
                    "sort_property": {
                        "type": "string",
                        "description": "Optional: property name to sort by.",
                    },
                    "sort_direction": {
                        "type": "string",
                        "enum": ["ascending", "descending"],
                        "description": "Sort direction (default ascending).",
                    },
                    "page_size": {
                        "type": "integer",
                        "description": "Max results (default 10, max 100).",
                    },
                },
                "required": ["database_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_get_database_schema",
            "description": (
                "Get a database's schema (column names, types, select options). "
                "Use this before creating entries to know the exact property names and types."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "database_id": {
                        "type": "string",
                        "description": "The database ID.",
                    },
                },
                "required": ["database_id"],
            },
        },
    },
    # --- CREATE ---
    {
        "type": "function",
        "function": {
            "name": "notion_create_page",
            "description": (
                "Create a new page in Notion under a parent page. "
                "For adding entries to a database, use notion_add_database_entry instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the new page.",
                    },
                    "parent_id": {
                        "type": "string",
                        "description": "Parent page ID. Omit to create at workspace root.",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Page body using simple markup. Each line becomes a block. "
                            "Prefixes: '# ' heading1, '## ' heading2, '### ' heading3, "
                            "'- ' bullet, '1. ' numbered, '[] ' todo, '[x] ' done todo, "
                            "'> ' callout, '--- ' divider, '```lang\\ncode\\n``` ' code block. "
                            "Plain text = paragraph."
                        ),
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_create_database",
            "description": (
                "Create a new database in Notion. Defines columns (properties) with types. "
                "Admin-only. Returns the database ID for adding entries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Database title.",
                    },
                    "parent_id": {
                        "type": "string",
                        "description": "Parent page ID to create the database under.",
                    },
                    "columns_json": {
                        "type": "string",
                        "description": (
                            "JSON array of column definitions. Each: "
                            '{\"name\": \"Status\", \"type\": \"select\", \"options\": [\"Todo\", \"In Progress\", \"Done\"]}. '
                            "Supported types: title, rich_text, number, select, multi_select, "
                            "date, checkbox, url, email, phone_number, status. "
                            "A 'title' column (Name) is always added automatically."
                        ),
                    },
                },
                "required": ["title", "parent_id", "columns_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_add_database_entry",
            "description": (
                "Add a new entry (row) to a Notion database with typed properties. "
                "Use notion_get_database_schema first to know exact property names and types."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "database_id": {
                        "type": "string",
                        "description": "The database ID to add an entry to.",
                    },
                    "properties_json": {
                        "type": "string",
                        "description": (
                            "JSON object of property values. Keys = property names (exact match). "
                            "Values depend on type: "
                            'title/rich_text: string, number: number, checkbox: boolean, '
                            'select/status: string (option name), multi_select: [\"opt1\", \"opt2\"], '
                            'date: \"2024-01-15\" or {\"start\": \"2024-01-15\", \"end\": \"2024-01-16\"}, '
                            'url/email/phone_number: string. '
                            'Example: {\"Name\": \"Buy groceries\", \"Status\": \"Todo\", \"Due\": \"2024-03-31\"}'
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Optional page body content (same markup as notion_create_page).",
                    },
                },
                "required": ["database_id", "properties_json"],
            },
        },
    },
    # --- UPDATE ---
    {
        "type": "function",
        "function": {
            "name": "notion_update_page",
            "description": (
                "Update properties on an existing Notion page or database entry. "
                "Change status, dates, text fields, checkboxes, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "The page/entry ID to update.",
                    },
                    "properties_json": {
                        "type": "string",
                        "description": (
                            "JSON object of properties to update. Same format as notion_add_database_entry. "
                            'Example: {\"Status\": \"Done\", \"Priority\": \"High\"}'
                        ),
                    },
                },
                "required": ["page_id", "properties_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_append_blocks",
            "description": (
                "Append rich content blocks to an existing Notion page. "
                "Supports headings, bullets, todos, code, callouts, dividers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "The page ID to append to.",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Content using simple markup (same as notion_create_page content). "
                            "Each line becomes a block."
                        ),
                    },
                },
                "required": ["page_id", "content"],
            },
        },
    },
    # --- DELETE ---
    {
        "type": "function",
        "function": {
            "name": "notion_delete_page",
            "description": (
                "Archive (soft-delete) a Notion page or database entry. "
                "Can be restored from Notion's trash."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "The page/entry ID to archive.",
                    },
                },
                "required": ["page_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_delete_block",
            "description": "Delete a specific block from a Notion page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "block_id": {
                        "type": "string",
                        "description": "The block ID to delete.",
                    },
                },
                "required": ["block_id"],
            },
        },
    },
]


# ── Helpers ──

def _extract_title(obj: dict) -> str:
    """Extract title from a Notion page or database object."""
    if obj.get("object") == "database":
        t = obj.get("title", [])
        return t[0].get("plain_text", "Untitled database") if t else "Untitled database"
    props = obj.get("properties", {})
    for _key, val in props.items():
        if val.get("type") == "title":
            titles = val.get("title", [])
            if titles:
                return titles[0].get("plain_text", "?")
    return "Untitled"


def _rt(text: str) -> list[dict]:
    """Build a rich_text array from a plain string."""
    return [{"type": "text", "text": {"content": text}}]


def _blocks_to_text(blocks: list[dict]) -> str:
    """Convert Notion block objects to readable text."""
    lines = []
    for b in blocks:
        btype = b.get("type", "")
        content = b.get(btype, {})
        if isinstance(content, dict):
            rich = content.get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich)
            if btype.startswith("heading"):
                level = btype[-1] if btype[-1].isdigit() else "1"
                lines.append(f"{'#' * int(level)} {text}")
            elif btype == "bulleted_list_item":
                lines.append(f"- {text}")
            elif btype == "numbered_list_item":
                lines.append(f"1. {text}")
            elif btype == "to_do":
                checked = content.get("checked", False)
                lines.append(f"[{'x' if checked else ' '}] {text}")
            elif btype == "code":
                lang = content.get("language", "")
                lines.append(f"```{lang}\n{text}\n```")
            elif btype == "callout":
                icon = content.get("icon", {}).get("emoji", ">")
                lines.append(f"{icon} {text}")
            elif text:
                lines.append(text)
        elif btype == "divider":
            lines.append("---")
    return "\n".join(lines)


def _parse_markup_to_blocks(text: str) -> list[dict]:
    """Parse simple markup text into Notion block objects.

    Supported:
      # heading1, ## heading2, ### heading3
      - bullet, 1. numbered
      [] todo, [x] done todo
      > callout
      --- divider
      ```lang ... ``` code block
      plain text = paragraph
    """
    blocks: list[dict] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Code block (multi-line)
        if stripped.startswith("```"):
            lang = stripped[3:].strip() or "plain text"
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            blocks.append({
                "object": "block", "type": "code",
                "code": {"rich_text": _rt("\n".join(code_lines)), "language": lang},
            })
            continue

        # Divider
        if stripped in ("---", "---\n", "***", "___"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue

        # Headings
        if stripped.startswith("### "):
            blocks.append({
                "object": "block", "type": "heading_3",
                "heading_3": {"rich_text": _rt(stripped[4:])},
            })
        elif stripped.startswith("## "):
            blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": _rt(stripped[3:])},
            })
        elif stripped.startswith("# "):
            blocks.append({
                "object": "block", "type": "heading_1",
                "heading_1": {"rich_text": _rt(stripped[2:])},
            })
        # Bullet
        elif stripped.startswith("- ") or stripped.startswith("* "):
            blocks.append({
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": _rt(stripped[2:])},
            })
        # Numbered list
        elif len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in (".", ")") and stripped[2] == " ":
            blocks.append({
                "object": "block", "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": _rt(stripped[3:])},
            })
        # Todo
        elif stripped.startswith("[x] ") or stripped.startswith("[X] "):
            blocks.append({
                "object": "block", "type": "to_do",
                "to_do": {"rich_text": _rt(stripped[4:]), "checked": True},
            })
        elif stripped.startswith("[] ") or stripped.startswith("[ ] "):
            text_start = 3 if stripped.startswith("[] ") else 4
            blocks.append({
                "object": "block", "type": "to_do",
                "to_do": {"rich_text": _rt(stripped[text_start:]), "checked": False},
            })
        # Callout
        elif stripped.startswith("> "):
            blocks.append({
                "object": "block", "type": "callout",
                "callout": {
                    "rich_text": _rt(stripped[2:]),
                    "icon": {"type": "emoji", "emoji": "💡"},
                },
            })
        # Paragraph (default)
        else:
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": _rt(stripped)},
            })

        i += 1

    return blocks[:100]  # Notion API max 100 blocks per request


def _prop_to_text(prop: dict) -> str:
    """Convert a single Notion property value to readable text."""
    ptype = prop.get("type", "")
    val = prop.get(ptype)
    if ptype == "title" and isinstance(val, list):
        return "".join(r.get("plain_text", "") for r in val)
    if ptype == "rich_text" and isinstance(val, list):
        return "".join(r.get("plain_text", "") for r in val)
    if ptype == "number":
        return str(val) if val is not None else ""
    if ptype == "select" and isinstance(val, dict):
        return val.get("name", "")
    if ptype == "multi_select" and isinstance(val, list):
        return ", ".join(v.get("name", "") for v in val)
    if ptype == "date" and isinstance(val, dict):
        start = val.get("start", "")
        end = val.get("end", "")
        return f"{start} -> {end}" if end else start
    if ptype == "checkbox":
        return "Yes" if val else "No"
    if ptype in ("url", "email", "phone_number"):
        return str(val or "")
    if ptype == "status" and isinstance(val, dict):
        return val.get("name", "")
    if ptype == "relation" and isinstance(val, list):
        return ", ".join(r.get("id", "?") for r in val)
    if ptype == "formula":
        inner = val or {}
        return str(inner.get(inner.get("type", ""), ""))
    if ptype == "rollup":
        inner = val or {}
        return str(inner.get(inner.get("type", ""), ""))
    return str(val)[:100] if val else ""


def _build_property_value(ptype: str, value: Any) -> dict | None:
    """Build a Notion API property value from a user-provided value and schema type."""
    if ptype == "title":
        return {"title": [{"text": {"content": str(value)}}]}
    if ptype == "rich_text":
        return {"rich_text": [{"text": {"content": str(value)}}]}
    if ptype == "number":
        try:
            return {"number": float(value)}
        except (ValueError, TypeError):
            return None
    if ptype == "select":
        return {"select": {"name": str(value)}}
    if ptype == "status":
        return {"status": {"name": str(value)}}
    if ptype == "multi_select":
        if isinstance(value, list):
            return {"multi_select": [{"name": str(v)} for v in value]}
        return {"multi_select": [{"name": str(value)}]}
    if ptype == "date":
        if isinstance(value, dict):
            return {"date": value}
        return {"date": {"start": str(value)}}
    if ptype == "checkbox":
        if isinstance(value, bool):
            return {"checkbox": value}
        return {"checkbox": str(value).lower() in ("true", "yes", "1")}
    if ptype == "url":
        return {"url": str(value)}
    if ptype == "email":
        return {"email": str(value)}
    if ptype == "phone_number":
        return {"phone_number": str(value)}
    return None


def _build_db_column(col: dict) -> tuple[str, dict]:
    """Build a Notion database property schema from a column definition."""
    name = col.get("name", "Column")
    ctype = col.get("type", "rich_text")
    options = col.get("options", [])

    if ctype == "select":
        return name, {"select": {"options": [{"name": o} for o in options]}}
    if ctype == "multi_select":
        return name, {"multi_select": {"options": [{"name": o} for o in options]}}
    if ctype == "status":
        return name, {"status": {"options": [{"name": o} for o in options]}} if options else {"status": {}}
    if ctype == "number":
        fmt = col.get("format", "number")
        return name, {"number": {"format": fmt}}
    if ctype == "checkbox":
        return name, {"checkbox": {}}
    if ctype == "date":
        return name, {"date": {}}
    if ctype == "url":
        return name, {"url": {}}
    if ctype == "email":
        return name, {"email": {}}
    if ctype == "phone_number":
        return name, {"phone_number": {}}
    if ctype == "rich_text":
        return name, {"rich_text": {}}
    # fallback
    return name, {"rich_text": {}}


# ── Integration class ──

class Integration(BaseIntegration):
    """Full Notion workspace control -- never open Notion again."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._sender_jid: str = ""
        self._client = kwargs.get("client")
        self._token = os.environ.get("NOTION_API_TOKEN", "")

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    def _is_admin(self) -> bool:
        sender_id = self._sender_jid.split("@")[0] if self._sender_jid else ""
        return is_admin(self.config, sender_id)

    def _get_token(self) -> str:
        """Return active Notion token: env var first, then OAuth store fallback."""
        if self._token:
            return self._token
        try:
            from pathlib import Path
            from .oauth.token_store import OAuthTokenStore
            db_path = Path.home() / ".happycapy-whatsapp" / "oauth_tokens.db"
            if db_path.exists():
                bundle = OAuthTokenStore(db_path).get("notion")
                if bundle and bundle.access_token:
                    return bundle.access_token
        except Exception:
            pass
        return ""

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="notion",
            display_name="Notion",
            description="Full Notion workspace control: search, read, create, update, delete pages and databases",
        )

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        return (
            "## Notion Integration (Admin Only)\n\n"
            "You have FULL control of the owner's Notion workspace. Tools:\n"
            "READ: notion_search, notion_read_page, notion_query_database, notion_get_database_schema\n"
            "CREATE: notion_create_page, notion_create_database, notion_add_database_entry\n"
            "UPDATE: notion_update_page, notion_append_blocks\n"
            "DELETE: notion_delete_page, notion_delete_block\n\n"
            "Workflow tips:\n"
            "- Always search first if you don't have an ID\n"
            "- Use notion_get_database_schema before adding entries (to get exact property names/types)\n"
            "- Content supports markup: # heading, - bullet, [] todo, > callout, ``` code, --- divider\n"
            "- For database entries, use notion_add_database_entry (not notion_create_page)\n"
            "- Properties in updates/entries must match the exact property name from the schema\n"
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self._is_admin():
            return ToolResult(False, tool_name, "Notion tools are admin-only.")
        if not self._get_token():
            return ToolResult(False, tool_name, "Notion not connected. Set NOTION_API_TOKEN or connect via the dashboard Apps section.")
        handlers = {
            "notion_search": self._search,
            "notion_read_page": self._read_page,
            "notion_query_database": self._query_database,
            "notion_get_database_schema": self._get_database_schema,
            "notion_create_page": self._create_page,
            "notion_create_database": self._create_database,
            "notion_add_database_entry": self._add_database_entry,
            "notion_update_page": self._update_page,
            "notion_append_blocks": self._append_blocks,
            "notion_delete_page": self._delete_page,
            "notion_delete_block": self._delete_block,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(False, tool_name, f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return ToolResult(False, tool_name, f"Error: {type(e).__name__}: {e}")

    # ── HTTP helper ──

    async def _api(self, method: str, path: str, body: dict | None = None) -> tuple[bool, dict]:
        """Make a Notion API call. Returns (success, json_response)."""
        import httpx
        url = f"{NOTION_API}{path}"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                if method == "GET":
                    resp = await client.get(url, headers=self._headers())
                elif method == "POST":
                    resp = await client.post(url, headers=self._headers(), json=body or {})
                elif method == "PATCH":
                    resp = await client.patch(url, headers=self._headers(), json=body or {})
                elif method == "DELETE":
                    resp = await client.delete(url, headers=self._headers())
                else:
                    return False, {"error": f"Unsupported method: {method}"}
            data = resp.json()
            if resp.status_code >= 400:
                msg = data.get("message", str(resp.status_code))
                return False, {"error": msg}
            return True, data
        except Exception as e:
            return False, {"error": f"{type(e).__name__}: {e}"}

    # ── READ handlers ──

    async def _search(self, args: dict) -> ToolResult:
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(False, "notion_search", "Missing query")
        body: dict[str, Any] = {"query": query, "page_size": 10}
        ft = args.get("filter_type")
        if ft in ("page", "database"):
            body["filter"] = {"value": ft, "property": "object"}
        ok, data = await self._api("POST", "/search", body)
        if not ok:
            return ToolResult(False, "notion_search", f"Search failed: {data.get('error')}")
        results = data.get("results", [])
        if not results:
            return ToolResult(True, "notion_search", f"No results for '{query}'")
        lines = []
        for r in results[:15]:
            obj_type = r.get("object", "?")
            title = _extract_title(r)
            rid = r.get("id", "?")
            lines.append(f"- [{obj_type}] {title} (id: {rid})")
        return ToolResult(True, "notion_search", f"Found {len(results)} result(s):\n" + "\n".join(lines))

    async def _read_page(self, args: dict) -> ToolResult:
        page_id = args.get("page_id", "").strip()
        if not page_id:
            return ToolResult(False, "notion_read_page", "Missing page_id")
        ok, page = await self._api("GET", f"/pages/{page_id}")
        if not ok:
            return ToolResult(False, "notion_read_page", f"Failed: {page.get('error')}")
        title = _extract_title(page)
        # Get blocks
        ok2, blocks_data = await self._api("GET", f"/blocks/{page_id}/children?page_size=100")
        content = ""
        if ok2:
            content = _blocks_to_text(blocks_data.get("results", []))
        # Format properties
        props_lines = []
        for key, val in page.get("properties", {}).items():
            text = _prop_to_text(val)
            if text:
                props_lines.append(f"  {key}: {text}")
        props_str = "\n".join(props_lines) if props_lines else "(no properties)"
        result = f"Page: {title}\nID: {page_id}\n\nProperties:\n{props_str}"
        if content:
            result += f"\n\nContent:\n{content}"
        return ToolResult(True, "notion_read_page", result[:4000])

    async def _query_database(self, args: dict) -> ToolResult:
        db_id = args.get("database_id", "").strip()
        if not db_id:
            return ToolResult(False, "notion_query_database", "Missing database_id")
        page_size = min(int(args.get("page_size", 10)), 100)
        body: dict[str, Any] = {"page_size": page_size}
        filter_json = args.get("filter_json", "").strip()
        if filter_json:
            try:
                body["filter"] = json.loads(filter_json)
            except json.JSONDecodeError:
                return ToolResult(False, "notion_query_database", "Invalid filter_json")
        # Sorting
        sort_prop = args.get("sort_property", "").strip()
        if sort_prop:
            direction = args.get("sort_direction", "ascending")
            body["sorts"] = [{"property": sort_prop, "direction": direction}]
        ok, data = await self._api("POST", f"/databases/{db_id}/query", body)
        if not ok:
            return ToolResult(False, "notion_query_database", f"Failed: {data.get('error')}")
        results = data.get("results", [])
        if not results:
            return ToolResult(True, "notion_query_database", "Database is empty")
        lines = []
        for r in results:
            rid = r.get("id", "?")
            title = _extract_title(r)
            props = []
            for key, val in r.get("properties", {}).items():
                if val.get("type") == "title":
                    continue
                text = _prop_to_text(val)
                if text:
                    props.append(f"{key}={text}")
            prop_str = " | ".join(props) if props else ""
            entry = f"- {title} [id:{rid[:8]}]: {prop_str}" if prop_str else f"- {title} [id:{rid[:8]}]"
            lines.append(entry)
        return ToolResult(True, "notion_query_database",
                          f"Found {len(results)} entries:\n" + "\n".join(lines))

    async def _get_database_schema(self, args: dict) -> ToolResult:
        db_id = args.get("database_id", "").strip()
        if not db_id:
            return ToolResult(False, "notion_get_database_schema", "Missing database_id")
        ok, data = await self._api("GET", f"/databases/{db_id}")
        if not ok:
            return ToolResult(False, "notion_get_database_schema", f"Failed: {data.get('error')}")
        title_parts = data.get("title", [])
        db_title = title_parts[0].get("plain_text", "Untitled") if title_parts else "Untitled"
        props = data.get("properties", {})
        lines = [f"Database: {db_title}", f"ID: {db_id}", "", "Columns:"]
        for name, schema in props.items():
            ptype = schema.get("type", "?")
            detail = ""
            if ptype == "select":
                opts = [o.get("name", "?") for o in schema.get("select", {}).get("options", [])]
                detail = f" options=[{', '.join(opts)}]" if opts else ""
            elif ptype == "multi_select":
                opts = [o.get("name", "?") for o in schema.get("multi_select", {}).get("options", [])]
                detail = f" options=[{', '.join(opts)}]" if opts else ""
            elif ptype == "status":
                opts = [o.get("name", "?") for o in schema.get("status", {}).get("options", [])]
                groups = [g.get("name", "?") for g in schema.get("status", {}).get("groups", [])]
                detail = f" options=[{', '.join(opts)}]"
                if groups:
                    detail += f" groups=[{', '.join(groups)}]"
            elif ptype == "number":
                fmt = schema.get("number", {}).get("format", "number")
                detail = f" format={fmt}"
            lines.append(f"  {name}: {ptype}{detail}")
        return ToolResult(True, "notion_get_database_schema", "\n".join(lines))

    # ── CREATE handlers ──

    async def _create_page(self, args: dict) -> ToolResult:
        title = args.get("title", "").strip()
        if not title:
            return ToolResult(False, "notion_create_page", "Missing title")
        parent_id = args.get("parent_id", "").strip()
        content = args.get("content", "").strip()
        parent = {"page_id": parent_id} if parent_id else {"workspace": True}
        children = _parse_markup_to_blocks(content) if content else []
        body: dict[str, Any] = {
            "parent": parent,
            "properties": {"title": {"title": [{"text": {"content": title}}]}},
        }
        if children:
            body["children"] = children
        ok, data = await self._api("POST", "/pages", body)
        if not ok:
            return ToolResult(False, "notion_create_page", f"Failed: {data.get('error')}")
        pid = data.get("id", "?")
        url = data.get("url", "")
        return ToolResult(True, "notion_create_page", f"Created page '{title}' (id: {pid})\nURL: {url}")

    async def _create_database(self, args: dict) -> ToolResult:
        title = args.get("title", "").strip()
        parent_id = args.get("parent_id", "").strip()
        columns_json = args.get("columns_json", "").strip()
        if not title or not parent_id or not columns_json:
            return ToolResult(False, "notion_create_database", "Missing title, parent_id, or columns_json")
        try:
            columns = json.loads(columns_json)
        except json.JSONDecodeError:
            return ToolResult(False, "notion_create_database", "Invalid columns_json")
        if not isinstance(columns, list):
            return ToolResult(False, "notion_create_database", "columns_json must be a JSON array")
        # Build properties schema -- title column is always "Name"
        properties: dict[str, Any] = {"Name": {"title": {}}}
        for col in columns:
            if not isinstance(col, dict):
                continue
            cname, cschema = _build_db_column(col)
            if cname != "Name":  # Don't overwrite title column
                properties[cname] = cschema
        body = {
            "parent": {"page_id": parent_id},
            "title": [{"text": {"content": title}}],
            "properties": properties,
        }
        ok, data = await self._api("POST", "/databases", body)
        if not ok:
            return ToolResult(False, "notion_create_database", f"Failed: {data.get('error')}")
        db_id = data.get("id", "?")
        url = data.get("url", "")
        col_names = list(properties.keys())
        return ToolResult(True, "notion_create_database",
                          f"Created database '{title}' (id: {db_id})\n"
                          f"Columns: {', '.join(col_names)}\nURL: {url}")

    async def _add_database_entry(self, args: dict) -> ToolResult:
        db_id = args.get("database_id", "").strip()
        props_json = args.get("properties_json", "").strip()
        content = args.get("content", "").strip()
        if not db_id or not props_json:
            return ToolResult(False, "notion_add_database_entry", "Missing database_id or properties_json")
        try:
            user_props = json.loads(props_json)
        except json.JSONDecodeError:
            return ToolResult(False, "notion_add_database_entry", "Invalid properties_json")
        # Get database schema to know property types
        ok, db_data = await self._api("GET", f"/databases/{db_id}")
        if not ok:
            return ToolResult(False, "notion_add_database_entry", f"Can't read schema: {db_data.get('error')}")
        schema = db_data.get("properties", {})
        # Build Notion properties
        notion_props: dict[str, Any] = {}
        errors = []
        for key, value in user_props.items():
            if key not in schema:
                errors.append(f"Unknown property '{key}'")
                continue
            ptype = schema[key].get("type", "rich_text")
            built = _build_property_value(ptype, value)
            if built is None:
                errors.append(f"Can't set '{key}' ({ptype}) to {value!r}")
            else:
                notion_props[key] = built
        if not notion_props:
            return ToolResult(False, "notion_add_database_entry",
                              f"No valid properties. Errors: {'; '.join(errors)}")
        children = _parse_markup_to_blocks(content) if content else []
        body: dict[str, Any] = {
            "parent": {"database_id": db_id},
            "properties": notion_props,
        }
        if children:
            body["children"] = children
        ok, data = await self._api("POST", "/pages", body)
        if not ok:
            return ToolResult(False, "notion_add_database_entry", f"Failed: {data.get('error')}")
        pid = data.get("id", "?")
        entry_title = _extract_title(data)
        msg = f"Added entry '{entry_title}' (id: {pid})"
        if errors:
            msg += f"\nWarnings: {'; '.join(errors)}"
        return ToolResult(True, "notion_add_database_entry", msg)

    # ── UPDATE handlers ──

    async def _update_page(self, args: dict) -> ToolResult:
        page_id = args.get("page_id", "").strip()
        props_json = args.get("properties_json", "").strip()
        if not page_id or not props_json:
            return ToolResult(False, "notion_update_page", "Missing page_id or properties_json")
        try:
            user_props = json.loads(props_json)
        except json.JSONDecodeError:
            return ToolResult(False, "notion_update_page", "Invalid properties_json")
        # Get current page to determine property types
        ok, page = await self._api("GET", f"/pages/{page_id}")
        if not ok:
            return ToolResult(False, "notion_update_page", f"Can't read page: {page.get('error')}")
        current_props = page.get("properties", {})
        # Build update
        notion_props: dict[str, Any] = {}
        errors = []
        for key, value in user_props.items():
            if key not in current_props:
                errors.append(f"Unknown property '{key}'")
                continue
            ptype = current_props[key].get("type", "rich_text")
            built = _build_property_value(ptype, value)
            if built is None:
                errors.append(f"Can't set '{key}' ({ptype}) to {value!r}")
            else:
                notion_props[key] = built
        if not notion_props:
            return ToolResult(False, "notion_update_page",
                              f"No valid properties. Errors: {'; '.join(errors)}")
        ok, data = await self._api("PATCH", f"/pages/{page_id}", {"properties": notion_props})
        if not ok:
            return ToolResult(False, "notion_update_page", f"Failed: {data.get('error')}")
        updated_keys = list(notion_props.keys())
        msg = f"Updated {len(updated_keys)} properties: {', '.join(updated_keys)}"
        if errors:
            msg += f"\nWarnings: {'; '.join(errors)}"
        return ToolResult(True, "notion_update_page", msg)

    async def _append_blocks(self, args: dict) -> ToolResult:
        page_id = args.get("page_id", "").strip()
        content = args.get("content", "").strip()
        if not page_id or not content:
            return ToolResult(False, "notion_append_blocks", "Missing page_id or content")
        children = _parse_markup_to_blocks(content)
        if not children:
            return ToolResult(False, "notion_append_blocks", "No valid blocks parsed from content")
        ok, data = await self._api("PATCH", f"/blocks/{page_id}/children", {"children": children})
        if not ok:
            return ToolResult(False, "notion_append_blocks", f"Failed: {data.get('error')}")
        types = [c["type"] for c in children]
        return ToolResult(True, "notion_append_blocks",
                          f"Appended {len(children)} block(s) to page {page_id}: {', '.join(types)}")

    # ── DELETE handlers ──

    async def _delete_page(self, args: dict) -> ToolResult:
        page_id = args.get("page_id", "").strip()
        if not page_id:
            return ToolResult(False, "notion_delete_page", "Missing page_id")
        ok, data = await self._api("PATCH", f"/pages/{page_id}", {"archived": True})
        if not ok:
            return ToolResult(False, "notion_delete_page", f"Failed: {data.get('error')}")
        title = _extract_title(data)
        return ToolResult(True, "notion_delete_page", f"Archived '{title}' (id: {page_id}). Can be restored from Notion trash.")

    async def _delete_block(self, args: dict) -> ToolResult:
        block_id = args.get("block_id", "").strip()
        if not block_id:
            return ToolResult(False, "notion_delete_block", "Missing block_id")
        ok, data = await self._api("DELETE", f"/blocks/{block_id}")
        if not ok:
            return ToolResult(False, "notion_delete_block", f"Failed: {data.get('error')}")
        return ToolResult(True, "notion_delete_block", f"Deleted block {block_id}")
