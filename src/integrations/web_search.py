"""Web Search integration for HappyCapy WhatsApp bot.

Exposes a search_web tool to the LLM via the BaseIntegration plugin system.
The actual search backend is abstracted via src.search_provider (adapter pattern).

Swapping providers: change config["web_search_provider"] from "ai_gateway" to
"tavily" or "brave". The integration never knows which backend runs.
"""

from typing import Any

from .base import BaseIntegration, IntegrationInfo
from src.tool_executor import ToolResult


_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web for current information. Use when you need to look up "
                "people, companies, recent news, events, or any topic you don't have "
                "reliable information about. Be specific in your query for better results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The search query. Be specific. For people: include full name "
                            "plus company or location if known."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (1-10). Default 5.",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["query"],
            },
        },
    },
]


class Integration(BaseIntegration):
    """Web search integration with swappable provider backend."""

    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self._provider = None  # Lazy-init to avoid import at module load

    def _get_provider(self):
        """Lazy-load search provider to avoid circular imports."""
        if self._provider is None:
            from src.search_provider import get_provider
            self._provider = get_provider(self.config)
        return self._provider

    @classmethod
    def info(cls) -> IntegrationInfo:
        return IntegrationInfo(
            name="web_search",
            display_name="Web Search",
            description="Search the web for people, companies, news, and current information",
        )

    @classmethod
    def tool_definitions(cls) -> list[dict]:
        return _TOOL_DEFINITIONS

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        if not config.get("web_search_enabled", True):
            return ""
        return (
            "## Web Search\n"
            "You have access to web search via the search_web tool.\n"
            "Use it when:\n"
            "- Someone you don't recognize messages you -- research who they are\n"
            "- You're asked about current events, news, or recent information\n"
            "- You need to verify facts you're unsure about\n"
            "- Someone mentions a company, person, or topic you need context on\n"
            "Be specific in your queries for better results."
        )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if tool_name != "search_web":
            return ToolResult(False, tool_name, f"Unknown web search tool: {tool_name}")

        if not self.config.get("web_search_enabled", True):
            return ToolResult(False, tool_name, "Web search is disabled in configuration.")

        query = arguments.get("query", "").strip()
        if not query:
            return ToolResult(False, tool_name, "Search query is required.")

        max_results = min(max(int(arguments.get("max_results", 5)), 1), 10)

        try:
            provider = self._get_provider()
            results = await provider.search(query, max_results)

            if not results:
                return ToolResult(True, tool_name, f"No results found for: {query}")

            # Format results for the LLM
            lines = [f"Web search results for: {query}\n"]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. **{r.title}**")
                if r.url:
                    lines.append(f"   URL: {r.url}")
                if r.snippet:
                    lines.append(f"   {r.snippet}")
                if r.published:
                    lines.append(f"   Published: {r.published}")
                lines.append("")

            return ToolResult(True, tool_name, "\n".join(lines))

        except Exception as e:
            return ToolResult(False, tool_name, f"Search failed: {type(e).__name__}: {e}")
