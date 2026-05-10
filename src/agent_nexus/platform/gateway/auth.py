"""GatewayAuth + ToolAccessPolicy — authentication and authorization for MCP Gateway.

Phase 2-3 of the Gateway Security roadmap (P0-2).

Design decisions:
- D9:  API key stored as SHA256 hash, loaded from env var.
- D11: Default disabled, explicit enable via config.
- D12: Per-client rate limiting at 100/min default.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import time
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class GatewayAuthConfig(BaseModel):
    """Gateway authentication configuration."""

    enabled: bool = False  # D11: default off, explicit enable
    method: Literal["api_key", "bearer_token", "mtls"] = "api_key"
    keys: list[str] = Field(default_factory=list)  # SHA256 hashed keys
    token_issuer: str | None = None
    token_audience: str | None = None
    mtls_ca_cert: str | None = None


class AuthenticatedClient(BaseModel):
    """Represents an authenticated client."""

    client_id: str
    roles: list[str] = Field(default_factory=lambda: ["default"])
    permissions: list[str] = Field(default_factory=list)
    authenticated_at: float = Field(default_factory=time.time)


class ToolAccessPolicy(BaseModel):
    """Role-based tool access control."""

    client_roles: list[str]
    tools_allowed: list[str]  # glob patterns
    tools_denied: list[str]  # higher priority
    rate_limit: int | None = None  # per-minute cap
    require_confirmation: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GatewayAuthenticator
# ---------------------------------------------------------------------------


class GatewayAuthenticator:
    """Authenticates clients against GatewayAuthConfig.

    D9:  API key stored as SHA256 hash, loaded from env var.
    D11: Default disabled, explicit enable via config.
    """

    def __init__(self, config: GatewayAuthConfig) -> None:
        self._config = config

    def authenticate(self, api_key: str) -> AuthenticatedClient:
        """Verify API key against configured hashes.

        Raises:
            PermissionError: if auth is enabled and key doesn't match.
        """
        if not self._config.enabled:
            # D11: default disabled returns a default client
            return AuthenticatedClient(client_id="anonymous")

        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        for stored_hash in self._config.keys:
            if key_hash == stored_hash:
                return AuthenticatedClient(client_id=f"key-{key_hash[:8]}")

        raise PermissionError("Invalid API key")

    @staticmethod
    def hash_key(key: str) -> str:
        """Hash an API key for storage."""
        return hashlib.sha256(key.encode()).hexdigest()

    @classmethod
    def from_env(cls, prefix: str = "GATEWAY") -> GatewayAuthenticator:
        """Create authenticator from environment variables.

        Reads {PREFIX}_AUTH_ENABLED, {PREFIX}_AUTH_KEYS (comma-separated SHA256 hashes).
        """
        enabled = os.environ.get(f"{prefix}_AUTH_ENABLED", "false").lower() == "true"
        keys_str = os.environ.get(f"{prefix}_AUTH_KEYS", "")
        keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        config = GatewayAuthConfig(enabled=enabled, keys=keys)
        return cls(config)


# ---------------------------------------------------------------------------
# ToolAccessChecker
# ---------------------------------------------------------------------------


class ToolAccessChecker:
    """Checks tool access against policies.

    D12: per-client rate limiting at 100/min default.
    """

    def __init__(self, policies: list[ToolAccessPolicy] | None = None) -> None:
        self._policies = policies or []
        self._call_counts: dict[str, list[float]] = {}  # client_id -> timestamps

    def is_tool_allowed(self, client: AuthenticatedClient, tool_name: str) -> bool:
        """Check if client roles allow access to tool.

        Logic: deny list takes priority, then check allow list.
        If no policies are configured, allow all (open by default).
        """
        if not self._policies:
            return True

        for policy in self._policies:
            if not any(role in policy.client_roles for role in client.roles):
                continue
            # Check deny first (higher priority)
            for pattern in policy.tools_denied:
                if fnmatch.fnmatch(tool_name, pattern):
                    return False
            # Check allow
            for pattern in policy.tools_allowed:
                if fnmatch.fnmatch(tool_name, pattern):
                    return True
        return False  # no matching policy -> deny

    def check_rate_limit(self, client_id: str) -> bool:
        """Check if client is within rate limit. Returns True if OK."""
        now = time.time()
        # Find applicable rate limit
        limit = 100  # D12: default 100/min
        for policy in self._policies:
            if policy.rate_limit is not None:
                limit = policy.rate_limit

        if client_id not in self._call_counts:
            self._call_counts[client_id] = []

        # Prune timestamps older than 60s
        self._call_counts[client_id] = [
            t for t in self._call_counts[client_id] if now - t < 60
        ]

        if len(self._call_counts[client_id]) >= limit:
            return False
        self._call_counts[client_id].append(now)
        return True
