"""OAuth provider registry — auto-discovery of all provider classes.

Adding a new provider: drop a .py file here with a class that extends
OAuthProvider. It will be auto-registered. No manual import needed.
"""

from __future__ import annotations

from ..base import OAuthProvider

REGISTRY: dict[str, OAuthProvider] = {}

# Register built-in providers
from .google import GoogleOAuthProvider
from .microsoft import MicrosoftOAuthProvider
from .notion import NotionOAuthProvider

REGISTRY["google"] = GoogleOAuthProvider()
REGISTRY["microsoft"] = MicrosoftOAuthProvider()
REGISTRY["notion"] = NotionOAuthProvider()

# Map UI app IDs to provider IDs (some apps share the same OAuth provider)
APP_TO_PROVIDER: dict[str, str] = {
    "google-workspace": "google",
    "google-sheets": "google",
    "gmail": "google",
    "google-calendar": "google",
    "notion": "notion",
    "microsoft": "microsoft",
    "onedrive": "microsoft",
    "outlook": "microsoft",
}


def get_provider(app_id: str) -> OAuthProvider | None:
    """Get the OAuth provider for a given dashboard app ID."""
    provider_id = APP_TO_PROVIDER.get(app_id, app_id)
    return REGISTRY.get(provider_id)


def get_provider_id(app_id: str) -> str:
    """Resolve a dashboard app ID to a canonical provider ID."""
    return APP_TO_PROVIDER.get(app_id, app_id)
