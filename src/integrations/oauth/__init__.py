"""OAuth integration hub.

Public API:
    from src.integrations.oauth import get_credential_manager

    manager = get_credential_manager()
    token = await manager.get_valid_token("google")   # raises if not connected
    is_ok = manager.is_connected("notion")
    status = manager.all_status()                     # for dashboard
"""

from .base import (
    ConnectionState,
    IntegrationNotConnected,
    OAuthProvider,
    TokenBundle,
    TokenRefreshFailed,
)
from .credential_manager import CredentialManager, get_credential_manager
from .token_store import OAuthTokenStore

__all__ = [
    "ConnectionState",
    "CredentialManager",
    "IntegrationNotConnected",
    "OAuthProvider",
    "OAuthTokenStore",
    "TokenBundle",
    "TokenRefreshFailed",
    "get_credential_manager",
]
