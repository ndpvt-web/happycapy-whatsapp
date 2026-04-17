"""Microsoft OAuth2 provider — Outlook, Calendar, OneDrive.

Uses the common (multi-tenant) endpoint so both personal and
work Microsoft accounts can connect.

Tokens expire in 1 hour. Refresh tokens expire after 90 days
of inactivity or on password change.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

try:
    import httpx
except ImportError:
    httpx = None

from ..base import OAuthProvider, TokenBundle, TokenRefreshFailed


TENANT = "common"  # accepts any Microsoft account (personal + work)
AUTHORIZE_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"

DEFAULT_SCOPES = [
    "offline_access",       # required for refresh token
    "openid",
    "email",
    "profile",
    "Mail.Read",
    "Mail.Send",
    "Calendars.ReadWrite",
    "Files.Read",
]


class MicrosoftOAuthProvider(OAuthProvider):
    provider_id = "microsoft"
    display_name = "Microsoft / Outlook"
    default_scopes = DEFAULT_SCOPES
    supports_refresh = True

    def __init__(self):
        self._client_id = os.environ.get("MICROSOFT_CLIENT_ID", "")
        self._client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        """Build Microsoft OAuth2 authorization URL."""
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": " ".join(DEFAULT_SCOPES),
            "state": state,
            "prompt": "consent",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenBundle:
        """Exchange authorization code for tokens."""
        if not httpx:
            raise TokenRefreshFailed("httpx not installed")

        async with httpx.AsyncClient() as client:
            resp = await client.post(TOKEN_URL, data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": " ".join(DEFAULT_SCOPES),
            })

        if resp.status_code != 200:
            raise TokenRefreshFailed(f"Microsoft token exchange failed: {resp.text}")

        return self._parse_token_response(resp.json())

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
                "scope": " ".join(DEFAULT_SCOPES),
            })

        if resp.status_code != 200:
            raise TokenRefreshFailed(f"Microsoft token refresh failed: {resp.text}")

        data = resp.json()
        if "refresh_token" not in data:
            data["refresh_token"] = refresh_token
        return self._parse_token_response(data)

    def _parse_token_response(self, data: dict) -> TokenBundle:
        expires_in = data.get("expires_in", 3600)
        expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)
        scopes = data.get("scope", "").split()

        # Extract email from id_token claims if available
        workspace_name = data.get("email", "")
        if not workspace_name and "id_token" in data:
            try:
                import base64, json as _json
                payload = data["id_token"].split(".")[1]
                padded = payload + "=" * (4 - len(payload) % 4)
                claims = _json.loads(base64.urlsafe_b64decode(padded))
                workspace_name = claims.get("email", claims.get("preferred_username", "Microsoft Account"))
            except Exception:
                workspace_name = "Microsoft Account"

        return TokenBundle(
            provider_id=self.provider_id,
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            scopes=scopes,
            workspace_name=workspace_name or "Microsoft Account",
            raw=data,
        )
