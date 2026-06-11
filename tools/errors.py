"""Tool framework exceptions."""

from __future__ import annotations


class ToolError(Exception):
    """Base class for tool framework errors."""


class ToolNotFoundError(ToolError):
    """Raised when a requested tool is not registered."""


class ToolDisabledError(ToolError):
    """Raised when a requested tool is disabled."""


class ToolSafetyError(ToolError):
    """Raised when a tool request violates safety policy."""


class ToolTimeoutError(ToolError):
    """Raised when a tool call exceeds its timeout."""

