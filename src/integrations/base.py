"""Base integration interface for HappyCapy WhatsApp bot.

Each integration is a single Python file that provides:
- Tool definitions (OpenAI format) for the LLM
- Tool execution handlers
- System prompt additions (optional)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class IntegrationInfo:
    """Metadata for an integration."""

    name: str  # e.g. "spreadsheet"
    display_name: str  # e.g. "Spreadsheet Tracker"
    description: str  # User-facing one-liner


class BaseIntegration(ABC):
    """Abstract base class for all integrations.

    Plugin Contract (Aristotelian Formal Cause):
    - info(): Who am I? (name, display_name, description)
    - tool_definitions(): What tools do I provide? (OpenAI format)
    - system_prompt_addition(): What should the LLM know about me? (instructions)
    - execute(): How do I handle tool calls?
    - set_request_context(): What do I need to know per-request? (sender identity)
    """

    @classmethod
    @abstractmethod
    def info(cls) -> IntegrationInfo:
        """Return integration metadata."""
        ...

    @classmethod
    @abstractmethod
    def tool_definitions(cls) -> list[dict]:
        """Return OpenAI-format tool definitions."""
        ...

    @abstractmethod
    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool by name. Returns a ToolResult."""
        ...

    @classmethod
    def system_prompt_addition(cls, config: dict[str, Any]) -> str:
        """Return text to inject into the LLM system prompt. Override in subclass."""
        return ""

    @classmethod
    def visibility(cls) -> str:
        """Who can see this integration's tools in the LLM tool list?

        Returns:
            "all" -- visible to every contact (default)
            "admin" -- visible only when sender is admin
            "elevated" -- visible only when admin is in elevated mode (/break-chains)

        This is a PROGRAMMATIC guard. The LLM never sees tools it shouldn't use.
        Override in subclass to restrict visibility.
        """
        return "all"

    def set_request_context(self, *, sender_jid: str = "", **kwargs: Any) -> None:
        """Set per-request context before execute(). Override if needed.

        Called by tool_executor before each execute() so integrations
        can access request-scoped data (e.g., who is asking, for privacy).
        """
        pass
