"""Access controls for a loopback-only Tailscale Serve backend.

Tailscale Serve removes client-supplied identity headers and adds its own before
proxying to the local service.  This module deliberately trusts that header
only when the direct peer is loopback; the Gunicorn deployment must never bind
this application to a LAN or Tailscale address while this mode is enabled.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


IDENTITY_HEADER = "Tailscale-User-Login"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    raise ValueError("BEHAVIOR_TAILSCALE_AUTH must be a boolean value")


def _canonical_https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("BEHAVIOR_TAILSCALE_CANONICAL_ORIGIN must be an HTTPS origin")
    return f"https://{parsed.netloc}"


@dataclass(frozen=True)
class TailscaleAccess:
    """Fail-closed authorization for requests arriving through Tailscale Serve."""

    enabled: bool
    allowed_login: str | None = None
    canonical_origin: str | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "TailscaleAccess":
        enabled = _enabled(config.get("BEHAVIOR_TAILSCALE_AUTH", os.getenv("BEHAVIOR_TAILSCALE_AUTH", "0")))
        if not enabled:
            return cls(enabled=False)
        login = str(
            config.get(
                "BEHAVIOR_TAILSCALE_ALLOWED_LOGIN",
                os.getenv("BEHAVIOR_TAILSCALE_ALLOWED_LOGIN", ""),
            )
        ).strip()
        if not login or not login.isascii() or any(ch.isspace() for ch in login):
            raise ValueError("BEHAVIOR_TAILSCALE_ALLOWED_LOGIN must be one ASCII login name")
        origin = str(
            config.get(
                "BEHAVIOR_TAILSCALE_CANONICAL_ORIGIN",
                os.getenv("BEHAVIOR_TAILSCALE_CANONICAL_ORIGIN", ""),
            )
        ).strip()
        if not origin:
            raise ValueError("BEHAVIOR_TAILSCALE_CANONICAL_ORIGIN is required when auth is enabled")
        return cls(enabled=True, allowed_login=login, canonical_origin=_canonical_https_origin(origin))

    @staticmethod
    def _is_loopback(remote_addr: str | None) -> bool:
        try:
            return bool(remote_addr) and ipaddress.ip_address(remote_addr).is_loopback
        except ValueError:
            return False

    def denial_reason(self, request: Any) -> str | None:
        """Return a generic-denial reason or ``None`` when the request is allowed."""
        if not self.enabled:
            return None
        if not self._is_loopback(request.remote_addr):
            return "untrusted upstream"
        login = request.headers.get(IDENTITY_HEADER, "")
        if not hmac.compare_digest(login, self.allowed_login or ""):
            return "identity is not permitted"
        if request.method in MUTATING_METHODS:
            origin = request.headers.get("Origin")
            if origin is not None and not hmac.compare_digest(origin, self.canonical_origin or ""):
                return "origin is not permitted"
        return None
