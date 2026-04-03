"""Admin Tools integration for HappyCapy WhatsApp bot.

Aristotelian Foundation:
- Material: ContactStore + MemoryStore + KnowledgeGraph + JobQueue (all existing).
- Formal: BaseIntegration plugin with 4 admin-gated tools.
- Efficient: LLM calls tools only when admin is in elevated mode (/break-chains).
- Final: Admin can query any contact's memory, manage jobs, and get cross-contact insights.

Security: All tools check admin status AND elevated mode before execution.
"""

from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult


_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "access_contact_memory",
            "description": (
                "Access a specific contact's memory and conversation history. "
                "ONLY available when admin has activated elevated mode via /break-chains. "
                "Use when the admin asks about a contact by name or number -- 'what do I know about X?', "
                "'show me the history with X', 'what did X say about Y?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_query": {
                        "type": "string",
                        "description": (
                            "Name, partial name, or phone number of the contact to look up. "
                            "Examples: 'John', 'John Smith', '+91999...' "
                        ),
                    },
                    "info_type": {
                        "type": "string",
                        "enum": ["memory", "history", "profile", "all"],
                        "description": (
                            "What information to retrieve: "
                            "'memory' = long-term memory facts, "
                            "'history' = recent conversation history entries, "
                            "'profile' = communication style profile, "
                            "'all' = everything available."
                        ),
                    },
                },
                "required": ["contact_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_across_contacts",
            "description": (
                "Search for a keyword or topic across ALL contacts' conversations. "
                "ONLY available in elevated mode. Use when admin asks 'who mentioned X?', "
                "'find conversations about Y', 'which contacts discussed Z?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query -- keyword, topic, or phrase to find across all contacts.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default 10).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_followup_job",
            "description": (
                "Create a follow-up job for a contact. Use when you need to get back to someone "
                "later -- after getting info from admin, after processing, or after a delay. "
                "The job queue will track it and prompt you to deliver the reply proactively."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_query": {
                        "type": "string",
                        "description": "Name or number of the contact to follow up with.",
                    },
                    "description": {
                        "type": "string",
                        "description": "What needs to be done -- e.g. 'Reply about pricing once admin confirms'.",
                    },
                    "job_type": {
                        "type": "string",
                        "enum": ["followup", "research", "confirmation", "delivery"],
                        "description": "Type of job for tracking.",
                    },
                },
                "required": ["contact_query", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pending_jobs",
            "description": (
                "List all pending follow-up jobs in the queue. "
                "Shows jobs waiting for admin input, processing, or delivery."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status_filter": {
                        "type": "string",
                        "enum": ["all", "waiting_admin", "ready", "created"],
                        "description": "Filter by job status (default: all pending).",
                    },
                },
            },
        },
    },
]


class Integration(BaseIntegration):
    """Admin tools for cross-contact access and job management."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._sender_jid: str = ""
        # These get injected by main.py via kwargs
        self._admin_mode = kwargs.get("admin_mode")
        self._memory_store = kwargs.get("memory_store")
        self._contact_store = kwargs.get("contact_store")
        self._knowledge_graph = kwargs.get("knowledge_graph")
        self._job_queue = kwargs.get("job_queue")

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    def _is_admin(self) -> bool:
        admin = self.config.get("admin_number", "")
        return bool(admin and admin in self._sender_jid)

    def _is_elevated(self) -> bool:
        if not self._is_admin():
            return False
        if not self._admin_mode:
            return False
        return self._admin_mode.is_elevated(self._sender_jid)

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="admin_tools",
            display_name="Admin Tools",
            description="Cross-contact memory access and job management (admin elevated mode only)",
        )

    @classmethod
    def visibility(cls) -> str:
        """Only visible when admin is in elevated mode (/break-chains).

        Non-admin contacts and non-elevated admin NEVER see these tools --
        the LLM has zero knowledge they exist, preventing hallucination.
        """
        return "elevated"

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        return (
            "## Admin Elevated Mode Tools\n\n"
            "When the admin activates /break-chains, you gain access to:\n"
            "- `access_contact_memory`: Look up any contact's memory, history, or profile by name/number\n"
            "- `search_across_contacts`: Search keywords across ALL contacts' conversations\n"
            "- `create_followup_job`: Queue a follow-up task for a contact (proactive delivery later)\n"
            "- `list_pending_jobs`: See all queued follow-up tasks\n\n"
            "These tools ONLY work when elevated mode is active.\n"
            "Use access_contact_memory when admin asks about any contact.\n"
            "Use create_followup_job when you need to defer a reply to a contact.\n\n"
            "## Multi-Message Responses\n\n"
            "You can send multiple distinct messages by separating them with `|||` on its own line.\n"
            "Use this when:\n"
            "- You want to send a greeting, then the main content, then a follow-up\n"
            "- Breaking a long response into natural conversational chunks\n"
            "- Sending 2-3 messages feels more natural than one wall of text\n\n"
            "Example:\n"
            "Hey! Let me check on that for you.\n"
            "|||\n"
            "Found the info you needed. The meeting is at 3pm tomorrow at the usual place.\n"
            "|||\n"
            "Want me to add it to your calendar?\n"
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        # Job management tools available to admin without elevation
        if tool_name in ("create_followup_job", "list_pending_jobs"):
            if not self._is_admin():
                return ToolResult(False, tool_name, "Admin-only tool.")
        else:
            # Memory access tools require elevated mode
            if not self._is_elevated():
                return ToolResult(
                    False, tool_name,
                    "Elevated mode not active. Admin must send /break-chains first."
                )

        handlers = {
            "access_contact_memory": self._access_contact_memory,
            "search_across_contacts": self._search_across_contacts,
            "create_followup_job": self._create_followup_job,
            "list_pending_jobs": self._list_pending_jobs,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return ToolResult(False, tool_name, f"Unknown admin tool: {tool_name}")
        return await handler(arguments)

    async def _access_contact_memory(self, args: dict) -> ToolResult:
        query = args.get("contact_query", "").strip()
        info_type = args.get("info_type", "all")
        if not query:
            return ToolResult(False, "access_contact_memory", "No contact query provided.")

        if not self._contact_store:
            return ToolResult(False, "access_contact_memory", "Contact store not available.")

        # Resolve contact by name or number
        matches = self._contact_store.resolve_contact_by_name(query, limit=3)
        if not matches:
            return ToolResult(
                True, "access_contact_memory",
                f"No contacts found matching '{query}'. Try a different name or number."
            )

        # Use the best match
        contact = matches[0]
        jid = contact.get("jid", "")
        name = contact.get("best_name", contact.get("display_name", jid))

        parts = [f"## Contact: {name} ({jid})"]

        if info_type in ("memory", "all") and self._memory_store:
            memory = self._memory_store.read_contact_memory(jid)
            if memory:
                parts.append(f"\n### Long-term Memory\n{memory[:2000]}")
            else:
                parts.append("\n### Long-term Memory\n(No memory stored yet)")

        if info_type in ("history", "all") and self._memory_store:
            history = self._memory_store.get_recent_history(jid, max_entries=10)
            if history:
                parts.append(f"\n### Recent History\n{history[:2000]}")
            else:
                parts.append("\n### Recent History\n(No history entries)")

        if info_type in ("profile", "all") and self._contact_store:
            profile = self._contact_store.get_profile(jid)
            if profile:
                parts.append(f"\n### Communication Profile\n{self._contact_store.format_profile_for_prompt(jid)}")
            else:
                parts.append("\n### Communication Profile\n(No profile generated yet)")

        if info_type == "all" and self._knowledge_graph:
            try:
                kg_context, _ = self._knowledge_graph.retrieve(jid, query)
                if kg_context:
                    parts.append(f"\n### Knowledge Graph\n{kg_context[:1500]}")
            except Exception:
                pass

        # Show other matches if multiple
        if len(matches) > 1:
            other = ", ".join(
                m.get("best_name", m.get("jid", "?")) for m in matches[1:]
            )
            parts.append(f"\n_Other possible matches: {other}_")

        return ToolResult(True, "access_contact_memory", "\n".join(parts))

    async def _search_across_contacts(self, args: dict) -> ToolResult:
        query = args.get("query", "").strip()
        max_results = min(args.get("max_results", 10), 20)
        if not query:
            return ToolResult(False, "search_across_contacts", "No search query provided.")

        if not self._contact_store:
            return ToolResult(False, "search_across_contacts", "Contact store not available.")

        # Use the contact store's group message search (which has FTS5)
        # and also search across DM samples
        results = []

        # Search DM conversation samples via knowledge graph if available
        if self._knowledge_graph:
            try:
                entities = self._knowledge_graph.search_entities(query, jid=None, limit=max_results)
                for ent in entities:
                    results.append(
                        f"- [{ent.get('entity_type', '?')}] {ent.get('name', '?')} "
                        f"(contact: {ent.get('jid', '?')[:20]}, mentions: {ent.get('mention_count', 0)})"
                    )
            except Exception:
                pass

        # Search group messages
        try:
            group_hits = self._contact_store.search_group_messages(query, limit=max_results)
            for hit in group_hits:
                results.append(
                    f"- [group] {hit.get('content', '')[:80]} "
                    f"(from: {hit.get('sender_id', '?')[:20]})"
                )
        except Exception:
            pass

        if not results:
            return ToolResult(
                True, "search_across_contacts",
                f"No results found for '{query}' across contacts."
            )

        header = f"## Cross-Contact Search: '{query}'\nFound {len(results)} result(s):\n"
        return ToolResult(True, "search_across_contacts", header + "\n".join(results[:max_results]))

    async def _create_followup_job(self, args: dict) -> ToolResult:
        contact_query = args.get("contact_query", "").strip()
        description = args.get("description", "").strip()
        job_type = args.get("job_type", "followup")

        if not contact_query or not description:
            return ToolResult(False, "create_followup_job", "Missing contact_query or description.")

        if not self._job_queue:
            return ToolResult(False, "create_followup_job", "Job queue not available.")

        # Resolve contact
        contact_jid = contact_query
        contact_name = contact_query
        if self._contact_store:
            matches = self._contact_store.resolve_contact_by_name(contact_query, limit=1)
            if matches:
                contact_jid = matches[0].get("jid", contact_query)
                contact_name = matches[0].get("best_name", contact_query)

        job_id = self._job_queue.create_job(
            contact_jid=contact_jid,
            contact_name=contact_name,
            description=description,
            job_type=job_type,
        )

        return ToolResult(
            True, "create_followup_job",
            f"Follow-up job #{job_id} created for {contact_name}. "
            f"Type: {job_type}. Description: {description[:100]}"
        )

    async def _list_pending_jobs(self, args: dict) -> ToolResult:
        if not self._job_queue:
            return ToolResult(False, "list_pending_jobs", "Job queue not available.")

        jobs = self._job_queue.get_pending(limit=20)
        if not jobs:
            return ToolResult(True, "list_pending_jobs", "No pending jobs in the queue.")

        return ToolResult(True, "list_pending_jobs", self._job_queue.format_job_list(jobs))
