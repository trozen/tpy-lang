"""Diagnostics envelope for the frontend plugin pipeline.

Wraps a regular `Diagnostic` with plugin-frontend-specific category and
metadata. Kept out of `diagnostics.py` so that core diagnostics stays
free of plugin coupling.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .diagnostics import Diagnostic


class FrontendDiagnosticCategory(Enum):
    """Why a frontend-pipeline diagnostic was emitted.

    - PLUGIN_REPORTED:  the plugin returned it in FrontendOutput.diagnostics.
    - PLUGIN_IR_INVALID: lowering caught a structural problem in the IR
      (unknown discriminator, missing required field, etc.). Blames the
      plugin with a clear local error.
    - LOWERING_INTERNAL: lowering hit an impossible-state condition
      after structural validation passed. This is a TPy compiler bug,
      not a plugin bug; the renderer prefixes the message with an
      "internal compiler error" banner.
    """
    PLUGIN_REPORTED = "PLUGIN_REPORTED"
    PLUGIN_IR_INVALID = "PLUGIN_IR_INVALID"
    LOWERING_INTERNAL = "LOWERING_INTERNAL"


@dataclass
class FrontendDiagnostic:
    """Plugin-pipeline envelope around a regular Diagnostic."""
    diagnostic: Diagnostic
    category: FrontendDiagnosticCategory
    plugin_name: str | None = None
    source_language: str | None = None
    plugin_diagnostic_code: str | None = None

    def format(self, filename: str = "<unknown>") -> str:
        base = self.diagnostic.format(filename)
        if self.category == FrontendDiagnosticCategory.LOWERING_INTERNAL:
            return ("internal compiler error: " + base
                    + " (please file an issue)")
        return base
