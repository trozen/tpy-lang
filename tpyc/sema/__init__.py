"""
TurboPython Semantic Analysis Package

Provides type checking and semantic validation:
- Type inference and checking
- Record field validation
- @noalloc constraint enforcement
- Pointer semantics validation
"""

from ..diagnostics import DiagnosticLevel, Diagnostic, SemanticError, Scope, TypedExpr
from .analyzer import SemanticAnalyzer

__all__ = [
    'SemanticAnalyzer',
    'SemanticError',
    'Diagnostic',
    'DiagnosticLevel',
    'Scope',
    'TypedExpr',
]
