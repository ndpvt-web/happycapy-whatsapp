"""Integration registry and loader for HappyCapy WhatsApp bot.

Discovers, loads, and aggregates pluggable integrations. Each integration
is a single Python module in this package with a class that extends BaseIntegration.
"""

import importlib
from typing import Any

from .base import BaseIntegration, IntegrationInfo

# Explicit registry: integration name -> module path
REGISTRY: dict[str, str] = {
    "spreadsheet": "src.integrations.spreadsheet",
    "email": "src.integrations.email",
}

# Convention: each module exports a class named Integration
_CLASS_NAME = "Integration"


def load_integrations(
    enabled_names: list[str],
    config: dict[str, Any],
    **kwargs: Any,
) -> dict[str, BaseIntegration]:
    """Load and instantiate enabled integrations.

    Args:
        enabled_names: List of integration names to load (e.g. ["spreadsheet", "email"]).
        config: Bot configuration dict.
        **kwargs: Extra kwargs passed to each integration's __init__ (client, channel, etc.).

    Returns:
        Dict of {name: instance} for successfully loaded integrations.
    """
    loaded: dict[str, BaseIntegration] = {}

    for name in enabled_names:
        module_path = REGISTRY.get(name)
        if not module_path:
            print(f"[integrations] Unknown integration: {name}")
            continue

        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, _CLASS_NAME, None)

            if cls is None or not (isinstance(cls, type) and issubclass(cls, BaseIntegration)):
                print(f"[integrations] {name}: no valid Integration class found")
                continue

            instance = cls(config=config, **kwargs)
            loaded[name] = instance

            meta = cls.info()
            print(f"[integrations] Loaded: {meta.display_name}")

        except Exception as e:
            print(f"[integrations] Failed to load {name}: {type(e).__name__}: {e}")

    return loaded


def get_all_tool_definitions(integrations: dict[str, BaseIntegration]) -> list[dict]:
    """Collect tool definitions from all loaded integrations."""
    defs: list[dict] = []
    for integration in integrations.values():
        defs.extend(integration.tool_definitions())
    return defs


def get_system_prompt_additions(
    integrations: dict[str, BaseIntegration],
    config: dict[str, Any],
) -> str:
    """Collect and join system prompt additions from all loaded integrations."""
    parts: list[str] = []
    for integration in integrations.values():
        addition = integration.system_prompt_addition(config)
        if addition:
            parts.append(addition)
    return "\n\n".join(parts)
