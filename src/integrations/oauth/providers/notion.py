"""Notion OAuth2 provider.

Notion tokens NEVER expire — no refresh token needed.
expires_at is set 100 years in the future so needs_refresh never fires.

Token exchange uses Basic auth (client_id:client_secret), not body params.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

try:
    import httpx
except ImportError:
    httpx = None

from ..base import OAuthProvider, TokenBundle, TokenRefreshFailed


AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"


class NotionOAuthProvider(OAuthProvider):
    provider_id = "notion"
    display_name = "Notion"
    default_scopes = []       # Notion has no scope granularity
    supports_refresh = False  # Tokens never expire

    def __init__(self):
        self._client_id = os.environ.get("NOTION_CLIENT_ID", "")
        self._client_secret = os.environ.get("NOTION_CLIENT_SECRET", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        """Build Notion OAuth2 authorization URL."""
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "owner": "user",
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenBundle:
        """Exchange authorization code for tokens via Basic auth."""
        if not httpx:
            raise TokenRefreshFailed("httpx not installed")

        # Notion requires Basic auth with client_id:client_secret
        credentials = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TOKEN_URL,
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/json",
                },
            )

        if resp.status_code != 200:
            raise TokenRefreshFailed(f"Notion token exchange failed: {resp.text}")

        data = resp.json()
        # Tokens never expire — set a far future date
        far_future = datetime.now(tz=timezone.utc) + timedelta(days=36500)

        workspace_name = data.get("workspace_name", "Notion Workspace")
        bot_id = data.get("bot_id", "")
        owner = data.get("owner", {})
        user_id = ""
        if isinstance(owner, dict) and owner.get("type") == "user":
            user_info = owner.get("user", {})
            user_id = user_info.get("id", "")
            person = user_info.get("person", {})
            email = person.get("email", "")
            if email:
                workspace_name = f"{workspace_name} ({email})"

        return TokenBundle(
            provider_id=self.provider_id,
            access_token=data["access_token"],
            refresh_token=None,         # Notion has no refresh token
            expires_at=far_future,
            scopes=[],
            workspace_name=workspace_name,
            provider_user_id=user_id or bot_id,
            raw=data,
        )
