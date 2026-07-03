"""Exception hierarchy with plain-language messages for the GUI.

Every error that can reach the user carries a ``user_message`` (what happened,
in plain words), a ``suggestion`` (what to try next) and optional technical
``details`` for the collapsible section of the error dialog / the log file.
"""

from __future__ import annotations


class DrainageError(Exception):
    """Base class for all expected application errors."""

    def __init__(self, user_message: str, suggestion: str = "", details: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.suggestion = suggestion
        self.details = details

    def full_text(self) -> str:
        """Combined message used for logging."""
        parts = [self.user_message]
        if self.suggestion:
            parts.append(f"Suggestion: {self.suggestion}")
        if self.details:
            parts.append(f"Details: {self.details}")
        return " | ".join(parts)


class DEMValidationError(DrainageError):
    """The input DEM could not be opened or is unusable."""


class EngineError(DrainageError):
    """A hydrology engine failed or is unavailable."""


class ExportError(DrainageError):
    """Writing an output file failed."""


class MemoryBudgetError(DrainageError):
    """The DEM is too large for the available RAM."""


class PipelineCancelled(Exception):
    """Raised internally when the user cancels a run. Not an error."""
