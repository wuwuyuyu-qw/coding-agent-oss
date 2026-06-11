"""Patch engine errors."""

from __future__ import annotations


class PatchEngineError(Exception):
    """Base class for patch engine failures."""


class PatchParseError(PatchEngineError):
    """Raised when raw patch output cannot be parsed."""


class PatchValidationError(PatchEngineError):
    """Raised when a patch plan violates validation rules."""


class PatchApplyError(PatchEngineError):
    """Raised when applying a patch fails."""

