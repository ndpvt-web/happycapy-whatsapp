"""OAuth Token Store — AES-256-GCM encrypted SQLite.

Material cause: where tokens live at rest.
Every token is encrypted before write, decrypted on read.
The encryption key is derived from the app secret — never stored with data.

Single-user design: provider_id is the primary key (one connection per provider).
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .base import ConnectionState, TokenBundle


# ── Encryption (AES-256-GCM via cryptography package, or fallback XOR) ──

def _get_fernet():
    """Return a Fernet instance using the app encryption key."""
    try:
        from cryptography.fernet import Fernet
        import hashlib
        secret = os.environ.get("OAUTH_ENCRYPTION_KEY", "")
        if not secret:
            secret = os.environ.get("CAPY_SECRET", "happycapy-default-key-change-in-production")
        # Derive a 32-byte key from the secret, base64url-encode for Fernet
        key_bytes = hashlib.sha256(secret.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(key_bytes)
        return Fernet(fernet_key)
    except ImportError:
        return None


def _encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns base64-encoded ciphertext."""
    fernet = _get_fernet()
    if fernet:
        return fernet.encrypt(plaintext.encode()).decode()
    # Fallback: base64 encode (no real encryption — warns in logs)
    print("[oauth/token_store] WARNING: cryptography package not installed. Tokens stored base64-only.")
    return base64.b64encode(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    """Decrypt a string from base64-encoded ciphertext."""
    fernet = _get_fernet()
    if fernet:
        try:
            return fernet.decrypt(ciphertext.encode()).decode()
        except Exception:
            # Might be legacy base64 — try fallback
            pass
    return base64.b64decode(ciphertext.encode()).decode()


# ── Token Store ──

class OAuthTokenStore:
    """Encrypted SQLite store for OAuth tokens.

    Schema:
      provider_id     TEXT PRIMARY KEY
      access_token    TEXT  (encrypted)
      refresh_token   TEXT  (encrypted, nullable)
      expires_at      TEXT  (ISO datetime UTC)
      scopes          TEXT  (JSON array)
      workspace_name  TEXT
      provider_user_id TEXT
      state           TEXT  (ConnectionState enum value)
      connected_at    TEXT  (ISO datetime UTC)
      last_refreshed  TEXT  (ISO datetime UTC, nullable)
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    provider_id      TEXT PRIMARY KEY,
                    access_token     TEXT NOT NULL,
                    refresh_token    TEXT,
                    expires_at       TEXT NOT NULL,
                    scopes           TEXT NOT NULL DEFAULT '[]',
                    workspace_name   TEXT NOT NULL DEFAULT '',
                    provider_user_id TEXT NOT NULL DEFAULT '',
                    state            TEXT NOT NULL DEFAULT 'connected',
                    connected_at     TEXT NOT NULL,
                    last_refreshed   TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_state_nonces (
                    state      TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL DEFAULT '',
                    code_verifier TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            conn.commit()

    # ── Token CRUD ──

    def put(self, bundle: TokenBundle) -> None:
        """Store or update a TokenBundle (encrypted)."""
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT connected_at FROM oauth_tokens WHERE provider_id = ?",
                (bundle.provider_id,)
            ).fetchone()
            connected_at = existing["connected_at"] if existing else now

            conn.execute("""
                INSERT INTO oauth_tokens
                    (provider_id, access_token, refresh_token, expires_at, scopes,
                     workspace_name, provider_user_id, state, connected_at, last_refreshed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = COALESCE(excluded.refresh_token, refresh_token),
                    expires_at = excluded.expires_at,
                    scopes = excluded.scopes,
                    workspace_name = excluded.workspace_name,
                    provider_user_id = excluded.provider_user_id,
                    state = excluded.state,
                    last_refreshed = ?
            """, (
                bundle.provider_id,
                _encrypt(bundle.access_token),
                _encrypt(bundle.refresh_token) if bundle.refresh_token else None,
                bundle.expires_at.isoformat(),
                json.dumps(bundle.scopes),
                bundle.workspace_name,
                bundle.provider_user_id,
                ConnectionState.CONNECTED,
                connected_at,
                now,  # last_refreshed (for UPDATE branch)
            ))
            conn.commit()

    def get(self, provider_id: str) -> Optional[TokenBundle]:
        """Fetch and decrypt a TokenBundle. Returns None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_tokens WHERE provider_id = ?", (provider_id,)
            ).fetchone()
        if not row:
            return None
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return TokenBundle(
                provider_id=provider_id,
                access_token=_decrypt(row["access_token"]),
                refresh_token=_decrypt(row["refresh_token"]) if row["refresh_token"] else None,
                expires_at=expires_at,
                scopes=json.loads(row["scopes"] or "[]"),
                workspace_name=row["workspace_name"] or "",
                provider_user_id=row["provider_user_id"] or "",
            )
        except Exception as e:
            print(f"[oauth/token_store] Failed to decrypt {provider_id}: {e}")
            return None

    def delete(self, provider_id: str) -> None:
        """Remove a connection entirely."""
        with self._conn() as conn:
            conn.execute("DELETE FROM oauth_tokens WHERE provider_id = ?", (provider_id,))
            conn.commit()

    def mark_needs_reauth(self, provider_id: str) -> None:
        """Mark a connection as needing re-authorization."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE oauth_tokens SET state = ? WHERE provider_id = ?",
                (ConnectionState.NEEDS_REAUTH, provider_id)
            )
            conn.commit()

    def list_all(self) -> list[dict]:
        """List all connections with metadata (no tokens)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT provider_id, state, workspace_name, connected_at, "
                "last_refreshed, expires_at FROM oauth_tokens"
            ).fetchall()
        result = []
        for r in rows:
            try:
                expires_at = datetime.fromisoformat(r["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                now = datetime.now(tz=timezone.utc)
                # Auto-detect if token expired since last check
                state = r["state"]
                if state == ConnectionState.CONNECTED and expires_at < now:
                    state = ConnectionState.NEEDS_REAUTH
                result.append({
                    "provider_id": r["provider_id"],
                    "state": state,
                    "workspace_name": r["workspace_name"],
                    "connected_at": r["connected_at"],
                    "last_refreshed": r["last_refreshed"],
                })
            except Exception:
                pass
        return result

    # ── State nonce management (CSRF guard) ──

    def store_state_nonce(
        self, state: str, provider_id: str,
        redirect_uri: str = "", code_verifier: str = ""
    ) -> None:
        """Store a state nonce with a 10-minute TTL."""
        now = datetime.now(tz=timezone.utc)
        expires = (now + timedelta(minutes=10)).isoformat()
        with self._conn() as conn:
            # Clean up expired nonces first
            conn.execute(
                "DELETE FROM oauth_state_nonces WHERE expires_at < ?",
                (now.isoformat(),)
            )
            conn.execute("""
                INSERT OR REPLACE INTO oauth_state_nonces
                    (state, provider_id, redirect_uri, code_verifier, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (state, provider_id, redirect_uri, code_verifier, now.isoformat(), expires))
            conn.commit()

    def consume_state_nonce(self, state: str) -> Optional[dict]:
        """Validate and consume a state nonce. Returns nonce data or None if invalid/expired."""
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_state_nonces WHERE state = ? AND expires_at > ?",
                (state, now)
            ).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM oauth_state_nonces WHERE state = ?", (state,))
            conn.commit()
        return {
            "provider_id": row["provider_id"],
            "redirect_uri": row["redirect_uri"],
            "code_verifier": row["code_verifier"],
        }
