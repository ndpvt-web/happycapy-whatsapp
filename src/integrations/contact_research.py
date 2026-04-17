"""Contact Research integration for HappyCapy WhatsApp bot.

Combines web search + knowledge graph + contact store to build
a comprehensive profile/briefing about a person.

Use cases:
- Unknown person messages: who are they?
- Meeting prep: research attendees before a meeting
- Due diligence: background check before business decisions
"""

from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult


_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "research_contact",
            "description": (
                "Research a person using web search and internal knowledge graph. "
                "Returns a structured briefing with known info, conversation history, "
                "and web research results. Use when an unknown person contacts you, "
                "before important meetings, or when you need background on someone."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Full name of the person to research.",
                    },
                    "phone": {
                        "type": "string",
                        "description": (
                            "WhatsApp phone number or JID if known "
                            "(e.g. '85291234567'). Used to look up conversation history."
                        ),
                    },
                    "context_clues": {
                        "type": "string",
                        "description": (
                            "Any context: their company, role, why they're reaching out, "
                            "topics mentioned. Helps narrow web search."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
    },
]


class Integration(BaseIntegration):
    """Contact research integration combining multiple data sources."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._kg = kwargs.get("knowledge_graph")
        self._contact_store = kwargs.get("contact_store")
        self._sender_jid = ""

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="contact_research",
            display_name="Contact Research",
            description="Research people using web search, knowledge graph, and conversation history",
        )

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        return (
            "## Contact Research\n"
            "You can research people using the research_contact tool.\n"
            "Use it when:\n"
            "- An unknown person messages and you want to know who they are\n"
            "- Preparing for a meeting and need attendee background\n"
            "- Someone mentions a name you need context on\n"
            "Provide as many context clues as possible for better results."
        )

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        self._sender_jid = sender_jid

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if tool_name != "research_contact":
            return ToolResult(False, tool_name, f"Unknown tool: {tool_name}")

        try:
            return await self._research(arguments)
        except Exception as e:
            return ToolResult(False, tool_name, f"Research failed: {type(e).__name__}: {e}")

    async def _research(self, args: dict[str, Any]) -> ToolResult:
        name = args.get("name", "").strip()
        phone = args.get("phone", "").strip()
        context_clues = args.get("context_clues", "").strip()

        if not name:
            return ToolResult(False, "research_contact", "Name is required.")

        sections: list[str] = [f"## Contact Research: {name}"]

        # Resolve JID for internal lookups
        jid = ""
        if phone:
            clean_phone = "".join(c for c in phone if c.isdigit())
            jid = f"{clean_phone}@s.whatsapp.net"
        elif self._sender_jid:
            jid = self._sender_jid

        # 1. Knowledge Graph lookup
        kg_section = await self._query_knowledge_graph(name, jid)
        if kg_section:
            sections.append(kg_section)

        # 2. Contact store / conversation history
        contact_section = await self._query_contact_store(name, jid)
        if contact_section:
            sections.append(contact_section)

        # 3. Web search (if enabled)
        web_section = await self._web_search(name, context_clues)
        if web_section:
            sections.append(web_section)

        if len(sections) == 1:
            sections.append("No information found from any source.")

        return ToolResult(True, "research_contact", "\n\n".join(sections))

    async def _query_knowledge_graph(self, name: str, jid: str) -> str:
        """Query KG for entities and relationships related to this person."""
        if not self._kg:
            return ""

        try:
            # retrieve() does FTS5 search + 1-hop relationship traversal
            context = await self._kg.retrieve(name, jid=jid)
            if context and context.strip():
                return f"**Internal Knowledge Graph:**\n{context}"
        except Exception as e:
            print(f"[contact-research] KG query failed: {e}")

        return ""

    async def _query_contact_store(self, name: str, jid: str) -> str:
        """Query contact store for profile and recent conversation samples."""
        if not self._contact_store or not jid:
            return ""

        try:
            # Get formatted profile
            profile = await self._contact_store.format_profile_for_prompt(jid)
            if profile and profile.strip():
                return f"**Contact Profile:**\n{profile}"
        except Exception as e:
            print(f"[contact-research] Contact store query failed: {e}")

        return ""

    async def _web_search(self, name: str, context_clues: str) -> str:
        """Search the web for information about this person."""
        if not self.config.get("web_search_enabled", True):
            return ""

        try:
            from src.search_provider import get_provider

            provider = get_provider(self.config)
            max_results = self.config.get("contact_research_max_results", 5)

            # Build a targeted search query
            query_parts = [name]
            if context_clues:
                query_parts.append(context_clues)
            query = " ".join(query_parts)

            results = await provider.search(query, max_results)

            if not results:
                return ""

            lines = ["**Web Research:**"]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. **{r.title}**")
                if r.url:
                    lines.append(f"   {r.url}")
                if r.snippet:
                    lines.append(f"   {r.snippet}")
                lines.append("")

            return "\n".join(lines)

        except Exception as e:
            print(f"[contact-research] Web search failed: {e}")
            return ""
