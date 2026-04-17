"""OAuth base — the formal cause of the integration hub.

Every provider implements OAuthProvider. Every token is a TokenBundle.
The calling code never knows which provider it's talking to.

State machine:
  DISCONNECTED → AUTHORIZING → CONNECTED → REFRESH_PENDING → CONNECTED
                                         → NEEDS_REAUTH
"""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ── State constants ──

class ConnectionState:
    DISCONNECTED    = "disconnected"
    AUTHORIZING     = "authorizing"
    CONNECTED       = "connected"
    REFRESH_PENDING = "refresh_pending"
    NEEDS_REAUTH    = "needs_reauth"


# ── Data objects ──

@dataclass
class TokenBundle:
    """Everything needed to make authenticated API calls for one provider."""
    provider_id: str
    access_token: str
    refresh_token: Optional[str]
    expires_at: datetime              # always UTC-aware
    scopes: list[str]
    workspace_name: str = ""          # human-readable label (e.g. workspace name)
    provider_user_id: str = ""        # provider's user/bot ID
    raw: dict = field(default_factory=dict)  # full provider response

    @property
    def is_expired(self) -> bool:
        """True if token has expired."""
        return datetime.now(tz=timezone.utc) >= self.expires_at

    @property
    def needs_refresh(self) -> bool:
        """True if token expires within 15 minutes."""
        from datetime import timedelta
        return datetime.now(tz=timezone.utc) >= (self.expires_at - timedelta(minutes=15))

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
            "scopes": self.scopes,
            "workspace_name": self.workspace_name,
            "provider_user_id": self.provider_user_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TokenBundle":
        expires_at = d["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return cls(
            provider_id=d["provider_id"],
            access_token=d["access_token"],
            refresh_token=d.get("refresh_token"),
            expires_at=expires_at,
            scopes=d.get("scopes", []),
            workspace_name=d.get("workspace_name", ""),
            provider_user_id=d.get("provider_user_id", ""),
        )


class IntegrationNotConnected(Exception):
    """Raised when a tool tries to use an integration that has no valid token."""
    def __init__(self, provider_id: str, reason: str = "not_connected"):
        self.provider_id = provider_id
        self.reason = reason
        super().__init__(f"Integration '{provider_id}' not connected (reason: {reason})")


class TokenRefreshFailed(Exception):
    """Raised when a refresh_token exchange fails at the provider."""
    pass


# ── Provider ABC ──

class OAuthProvider(ABC):
    """Abstract base for all OAuth providers.

    Subclass this for each external service. The calling code only ever
    sees this interface — provider differences are fully hidden here.
    """

    provider_id: str          # e.g. "google", "microsoft", "notion"
    display_name: str         # e.g. "Google Workspace"
    default_scopes: list[str] # scopes requested during authorization
    supports_refresh: bool = True   # Notion = False (tokens never expire)

    @abstractmethod
    def authorize_url(self, state: str, redirect_uri: str) -> str:
        """Build the full authorization URL to redirect the user to."""
        ...

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> TokenBundle:
        """Exchange an authorization code for a TokenBundle."""
        ...

    async def refresh(self, refresh_token: str) -> TokenBundle:
        """Refresh an expired access token. Override in providers that support it."""
        raise TokenRefreshFailed(f"{self.provider_id} does not support token refresh")

    def get_auth_header(self, access_token: str) -> dict[str, str]:
        """Return the Authorization header dict for API calls."""
        return {"Authorization": f"Bearer {access_token}"}

    def generate_state(self) -> str:
        """Generate a cryptographically random state nonce for CSRF protection."""
        return secrets.token_urlsafe(32)
