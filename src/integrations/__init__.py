"""Auto-discovery integration loader for HappyCapy WhatsApp bot.

Plugin Architecture (Aristotelian):
- Material: Each plugin is a single .py file in this directory
- Formal: Must export an `Integration` class extending BaseIntegration
- Efficient: This loader scans the directory, imports matching files, registers them
- Final: Drop a file = it works. Delete a file = everything keeps working.

NO manual imports. NO hardcoded integration names. Pure auto-discovery.
"""

import importlib
import pkgutil
from pathlib import Path
from typing import Any

from .base import BaseIntegration, IntegrationInfo

# Auto-discover all integration modules in this directory.
# Each module that exports an `Integration` class (extending BaseIntegration)
# is registered automatically. No manual import lines needed.
_INTEGRATIONS: dict[str, type[BaseIntegration]] = {}

_pkg_dir = Path(__file__).parent
for _finder, _module_name, _is_pkg in pkgutil.iter_modules([str(_pkg_dir)]):
    if _module_name.startswith("_") or _module_name == "base":
        continue
    try:
        _mod = importlib.import_module(f".{_module_name}", package=__name__)
        _cls = getattr(_mod, "Integration", None)
        if _cls and isinstance(_cls, type) and issubclass(_cls, BaseIntegration):
            _info = _cls.info()
            _INTEGRATIONS[_info.name] = _cls
    except Exception:
        # Graceful: if a plugin file has import errors, skip it silently.
        # The plugin simply doesn't exist until its dependencies are met.
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
            # Not an error -- the plugin file simply doesn't exist (yet)
            continue
        try:
            instance = cls(config=config, **kwargs)
            loaded[name] = instance
            print(f"[integrations] Loaded: {cls.info().display_name}")
        except Exception as e:
            print(f"[integrations] Failed to load {name}: {type(e).__name__}: {e}")

    return loaded
