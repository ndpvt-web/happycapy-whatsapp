"""Google OAuth2 provider — Gmail, Calendar, Drive, Sheets.

Scopes:
  - gmail.send + gmail.readonly
  - calendar (read + write)
  - drive.readonly
  - spreadsheets (read + write)

Tokens expire in 1 hour. Refresh tokens valid indefinitely
(unless unused for 6 months or user revokes).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

try:
    import httpx
except ImportError:
    httpx = None

from ..base import OAuthProvider, TokenBundle, TokenRefreshFailed


AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Scopes that cover all Google integrations in the system
DEFAULT_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


class GoogleOAuthProvider(OAuthProvider):
    provider_id = "google"
    display_name = "Google Workspace"
    default_scopes = DEFAULT_SCOPES
    supports_refresh = True

    def __init__(self):
        self._client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        self._client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        """Build Google OAuth2 authorization URL."""
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(DEFAULT_SCOPES),
            "state": state,
            "access_type": "offline",     # get refresh_token
            "prompt": "consent",           # always show consent (ensures refresh_token)
            "include_granted_scopes": "true",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenBundle:
        """Exchange authorization code for tokens."""
        if not httpx:
            raise TokenRefreshFailed("httpx not installed")

        async with httpx.AsyncClient() as client:
            resp = await client.post(TOKEN_URL, data={
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })

        if resp.status_code != 200:
            raise TokenRefreshFailed(f"Google token exchange failed: {resp.text}")

        data = resp.json()
        return self._parse_token_response(data)

    async def refresh(self, refresh_token: str) -> TokenBundle:
        """Refresh an expired access token."""
        if not httpx:
            raise TokenRefreshFailed("httpx not installed")

        async with httpx.AsyncClient() as client:
            resp = await client.post(TOKEN_URL, data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            })

        if resp.status_code != 200:
            raise TokenRefreshFailed(f"Google token refresh failed: {resp.text}")

        data = resp.json()
        # Refresh response may not include refresh_token — keep the existing one
        if "refresh_token" not in data:
            data["refresh_token"] = refresh_token
        return self._parse_token_response(data)

    def _parse_token_response(self, data: dict) -> TokenBundle:
        expires_in = data.get("expires_in", 3600)
        expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)
        scopes = data.get("scope", "").split()

        return TokenBundle(
            provider_id=self.provider_id,
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            scopes=scopes,
            workspace_name=data.get("email", "Google Account"),
            raw=data,
        )
