"""
TurboPython String Variable Tracking

Tracks string locals and resolves their types (StrView vs str) based on usage.
Analogous to ListLiteralTracker for Array vs list inference.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, StrType, StringType, StrViewType, PendingStrType, StrVarInfo,
    STR, STRVIEW,
)
from ..parse import TpyExpr, TpyName, TpyCall, TpyMethodCall, TpyCoerce, TpyFunction
from ..parse.nodes import TpyStrLiteral

if TYPE_CHECKING:
    from .context import SemanticContext


class StrVarTracker:
    """Tracks string locals and resolves StrView vs str based on usage."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def is_view_compatible_source(self, init_expr: TpyExpr, init_type: TpyType) -> bool:
        """Check if init_expr produces a view-safe value (no owned string needed).

        View-safe sources:
        - String literal (static lifetime)
        - A str parameter (already string_view in C++)
        - Another PendingStrType or StrViewType local
        - A Final[str] constant (constexpr string_view)
        - A function returning StrView
        """
        if isinstance(init_expr, TpyCoerce):
            init_expr = init_expr.expr

        # String literal -> static lifetime, always view-safe
        if isinstance(init_expr, TpyStrLiteral):
            return True

        # Named variable reference
        if isinstance(init_expr, TpyName):
            name = init_expr.name
            # Check if it's a str parameter (C++ already passes as string_view)
            func = self.ctx.current_function
            if isinstance(func, TpyFunction):
                for pname, ptype in func.params:
                    if pname == name and isinstance(ptype, (StrType, StrViewType)):
                        return True

            # Another PendingStrType or StrViewType local
            scope_type = self.ctx.current_scope.lookup(name) if self.ctx.current_scope else None
            if isinstance(scope_type, (PendingStrType, StrViewType)):
                return True

            # Final[str] global constant
            if name in self.ctx.final_globals:
                return True

        # Function/method call returning StrView
        if isinstance(init_expr, (TpyCall, TpyMethodCall)):
            if isinstance(init_type, StrViewType):
                return True

        return False

    def mark_str_augassign(self, var_name: str) -> None:
        """Mark a PendingStrType variable as used in augmented assignment (+=)."""
        str_var_id = self.ctx.variable_to_str_var.get(var_name)
        if str_var_id is not None and str_var_id in self.ctx.str_vars:
            self.ctx.str_vars[str_var_id].used_in_augassign = True

    def mark_str_param_context(self, arg_expr: TpyExpr, param_type: TpyType) -> None:
        """Track when a PendingStrType var is passed to a String param."""
        if not isinstance(param_type, StringType):
            return

        if isinstance(arg_expr, TpyCoerce):
            arg_expr = arg_expr.expr

        if isinstance(arg_expr, TpyName):
            str_var_id = self.ctx.variable_to_str_var.get(arg_expr.name)
            if str_var_id is not None and str_var_id in self.ctx.str_vars:
                self.ctx.str_vars[str_var_id].passed_to_string_param = True

    def mark_str_reassigned_from_owned(self, var_name: str) -> None:
        """Mark a PendingStrType variable as reassigned from an owned source."""
        str_var_id = self.ctx.variable_to_str_var.get(var_name)
        if str_var_id is not None and str_var_id in self.ctx.str_vars:
            self.ctx.str_vars[str_var_id].reassigned_from_owned = True

    def track_reassign_source(self, var_name: str, source_type: TpyType) -> None:
        """Track source relationship when reassigning from another PendingStrType."""
        if not isinstance(source_type, PendingStrType):
            return
        str_var_id = self.ctx.variable_to_str_var.get(var_name)
        if str_var_id is not None and str_var_id in self.ctx.str_vars:
            self.ctx.str_vars[str_var_id].source_str_var_id = source_type.str_var_id

    def resolve_pending_str_types(self) -> None:
        """Resolve all pending str types after function analysis.

        Resolution rules:
        - Any owned flag set -> resolve to STR (std::string)
        - Otherwise -> resolve to STRVIEW (std::string_view)

        After the first pass, aliases whose source resolved to STR are
        retroactively promoted (a string_view of a std::string that may
        reallocate would dangle).
        """
        # First pass: resolve based on direct usage flags
        for str_var_id in self.ctx.pending_str_resolutions:
            info = self.ctx.str_vars.get(str_var_id)
            if info is None:
                continue

            needs_owned = (
                info.initialized_from_owned
                or info.used_in_augassign
                or info.passed_to_string_param
                or info.reassigned_from_owned
            )

            info.resolved_type = STR if needs_owned else STRVIEW

        # Second pass: promote aliases whose source resolved to STR
        changed = True
        while changed:
            changed = False
            for str_var_id in self.ctx.pending_str_resolutions:
                info = self.ctx.str_vars.get(str_var_id)
                if info is None or info.resolved_type != STRVIEW:
                    continue
                if info.source_str_var_id is not None:
                    source = self.ctx.str_vars.get(info.source_str_var_id)
                    if source and source.resolved_type == STR:
                        info.resolved_type = STR
                        changed = True

        # Update scope bindings and var_types
        for str_var_id in self.ctx.pending_str_resolutions:
            info = self.ctx.str_vars.get(str_var_id)
            if info is None:
                continue
            resolved = info.resolved_type

            if info.variable_name and self.ctx.current_scope:
                current_type = self.ctx.current_scope.lookup(info.variable_name)
                if isinstance(current_type, PendingStrType):
                    self.ctx.current_scope.define(info.variable_name, resolved)

            var_decl = self.ctx.var_decl_by_name.get(info.variable_name)
            if var_decl:
                self.ctx.var_types[id(var_decl)] = resolved
            if info.decl_line is not None:
                self.ctx.declared_var_types[(info.decl_line, info.variable_name)] = resolved
