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
    """Abstract base class for all integrations."""

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
