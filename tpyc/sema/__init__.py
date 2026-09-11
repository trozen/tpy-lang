"""
TurboPython Semantic Analysis Package

Provides type checking and semantic validation:
- Type inference and checking
- Record field validation
- @noalloc constraint enforcement
- Pointer semantics validation
"""

from ..diagnostics import (DiagnosticLevel, Diagnostic, SemanticError, Scope,
                           TypedExpr, format_diagnostics)
from .analyzer import SemanticAnalyzer

__all__ = [
    'SemanticAnalyzer',
    'SemanticError',
    'Diagnostic',
    'DiagnosticLevel',
    'format_diagnostics',
    'Scope',
    'TypedExpr',
]
