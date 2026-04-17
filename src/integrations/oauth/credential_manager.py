"""Credential Manager — the transparent pipe.

This is the single point of truth: "give me a working token."
All OAuth complexity lives here. Tool authors write zero auth logic.

Usage in any integration:
    token = await credential_manager.get_valid_token("google")
    headers = {"Authorization": f"Bearer {token}"}
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from .base import (
    ConnectionState, IntegrationNotConnected,
    OAuthProvider, TokenBundle, TokenRefreshFailed,
)
from .token_store import OAuthTokenStore


class CredentialManager:
    """Manages token lifecycle: retrieval, auto-refresh, expiry detection.

    Efficient cause: this is what moves the system forward without user action.
    """

    REFRESH_BUFFER_MINUTES = 15  # refresh if < 15 min remaining

    def __init__(self, store: OAuthTokenStore, providers: dict[str, OAuthProvider]):
        self._store = store
        self._providers = providers
        self._refresh_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, provider_id: str) -> asyncio.Lock:
        """Get or create a per-provider lock to prevent concurrent refreshes."""
        if provider_id not in self._refresh_locks:
            self._refresh_locks[provider_id] = asyncio.Lock()
        return self._refresh_locks[provider_id]

    async def get_valid_token(self, provider_id: str) -> str:
        """Return a non-expired access token, refreshing automatically if needed.

        Raises IntegrationNotConnected if:
          - provider was never connected
          - refresh token is missing/expired
          - provider doesn't support refresh and token has expired
        """
        bundle = self._store.get(provider_id)
        if bundle is None:
            raise IntegrationNotConnected(provider_id, reason="not_connected")

        if not bundle.needs_refresh:
            return bundle.access_token

        # Token near expiry — try to refresh
        provider = self._providers.get(provider_id)
        if not provider or not provider.supports_refresh:
            if bundle.is_expired:
                raise IntegrationNotConnected(provider_id, reason="token_expired")
            # Still valid, just return it (e.g. Notion — tokens never expire)
            return bundle.access_token

        if not bundle.refresh_token:
            if bundle.is_expired:
                raise IntegrationNotConnected(provider_id, reason="no_refresh_token")
            return bundle.access_token

        # Use per-provider lock to avoid concurrent refreshes
        async with self._lock_for(provider_id):
            # Re-check after acquiring lock (another coroutine may have refreshed)
            bundle = self._store.get(provider_id)
            if bundle and not bundle.needs_refresh:
                return bundle.access_token

            try:
                new_bundle = await provider.refresh(bundle.refresh_token)
                self._store.put(new_bundle)
                print(f"[oauth] Refreshed token for {provider_id}")
                return new_bundle.access_token
            except TokenRefreshFailed as e:
                self._store.mark_needs_reauth(provider_id)
                raise IntegrationNotConnected(
                    provider_id, reason=f"refresh_failed: {e}"
                ) from e

    def is_connected(self, provider_id: str) -> bool:
        """Quick check: is this provider connected with a usable token?"""
        bundle = self._store.get(provider_id)
        if bundle is None:
            return False
        provider = self._providers.get(provider_id)
        if bundle.is_expired:
            # Only consider expired if no way to refresh
            if not provider or not provider.supports_refresh or not bundle.refresh_token:
                return False
        return True

    def connection_info(self, provider_id: str) -> dict:
        """Get connection status info for dashboard display."""
        all_conns = {c["provider_id"]: c for c in self._store.list_all()}
        if provider_id not in all_conns:
            return {"state": ConnectionState.DISCONNECTED, "provider_id": provider_id}
        info = all_conns[provider_id]
        info["connected"] = info["state"] == ConnectionState.CONNECTED
        return info

    def all_status(self) -> dict[str, dict]:
        """Return status for all known providers (connected + disconnected)."""
        connected = {c["provider_id"]: c for c in self._store.list_all()}
        result = {}
        for pid in self._providers:
            if pid in connected:
                info = connected[pid]
                result[pid] = {
                    "state": info["state"],
                    "connected": info["state"] == ConnectionState.CONNECTED,
                    "workspace_name": info.get("workspace_name", ""),
                    "connected_at": info.get("connected_at", ""),
                    "last_refreshed": info.get("last_refreshed", ""),
                }
            else:
                result[pid] = {
                    "state": ConnectionState.DISCONNECTED,
                    "connected": False,
                }
        return result

    async def start_refresh_loop(self, interval_seconds: int = 300) -> None:
        """Background loop that proactively refreshes tokens before expiry.

        Run as an asyncio task: asyncio.create_task(manager.start_refresh_loop())
        """
        import asyncio
        print("[oauth] Token refresh loop started")
        while True:
            await asyncio.sleep(interval_seconds)
            await self._refresh_all_near_expiry()

    async def _refresh_all_near_expiry(self) -> None:
        """Proactively refresh any tokens that will expire within 15 minutes."""
        for conn in self._store.list_all():
            pid = conn["provider_id"]
            if conn["state"] not in (ConnectionState.CONNECTED, ConnectionState.REFRESH_PENDING):
                continue
            try:
                await self.get_valid_token(pid)
            except IntegrationNotConnected:
                pass  # already marked as needs_reauth in get_valid_token
            except Exception as e:
                print(f"[oauth] Proactive refresh error for {pid}: {e}")


# ── Singleton factory ──

_manager: Optional[CredentialManager] = None


def get_credential_manager(base_dir: Optional[Path] = None) -> CredentialManager:
    """Get or create the singleton CredentialManager."""
    global _manager
    if _manager is None:
        from pathlib import Path as P
        if base_dir is None:
            base_dir = P.home() / ".happycapy-whatsapp"
        db_path = base_dir / "oauth_tokens.db"
        store = OAuthTokenStore(db_path)
        # Import all providers
        from .providers import REGISTRY
        _manager = CredentialManager(store, REGISTRY)
    return _manager
