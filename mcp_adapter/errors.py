"""Errors used by the MCP adapter layer."""

from __future__ import annotations


class MCPAdapterError(RuntimeError):
    """Base class for adapter failures."""


class MCPDependencyError(MCPAdapterError):
    """Raised when the optional MCP SDK is required but missing."""


class MCPConfigError(MCPAdapterError):
    """Raised when server configuration is invalid."""

