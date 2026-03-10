"""Integration loader for HappyCapy WhatsApp bot."""

from typing import Any

from .base import BaseIntegration, IntegrationInfo

# Direct imports with graceful fallback if dependencies missing
_INTEGRATIONS: dict[str, type[BaseIntegration]] = {}

try:
    from .spreadsheet import Integration as _SpreadsheetIntegration
    _INTEGRATIONS["spreadsheet"] = _SpreadsheetIntegration
except ImportError:
    pass

try:
    from .email import Integration as _EmailIntegration
    _INTEGRATIONS["email"] = _EmailIntegration
except ImportError:
    pass


def load_integrations(
    enabled_names: list[str],
    config: dict[str, Any],
    **kwargs: Any,
) -> dict[str, BaseIntegration]:
    """Load and instantiate enabled integrations."""
    loaded: dict[str, BaseIntegration] = {}

    for name in enabled_names:
        cls = _INTEGRATIONS.get(name)
        if not cls:
            print(f"[integrations] Unknown integration: {name}")
            continue
        try:
            instance = cls(config=config, **kwargs)
            loaded[name] = instance
            print(f"[integrations] Loaded: {cls.info().display_name}")
        except Exception as e:
            print(f"[integrations] Failed to load {name}: {type(e).__name__}: {e}")

    return loaded
