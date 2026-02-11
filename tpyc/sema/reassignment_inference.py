"""Reassignment-based type inference helpers for semantic analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..coercions import CoercionContext
from ..parse import TpyExpr, TpyStmt
from ..typesys import (
    BIGINT,
    BigIntType,
    IntLiteralType,
    NoneType,
    OptionalType,
    OwnType,
    TpyType,
)
from .diagnostics import SemanticError
from .numeric_lattice import merge_literal_seed_target

if TYPE_CHECKING:
    from .compatibility import TypeCompatibility
    from .context import SemanticContext


class ReassignmentInference:
    """Tracks and resolves inferred variable types across writes."""

    def __init__(self, ctx: SemanticContext, compat: TypeCompatibility):
        self.ctx = ctx
        self.compat = compat

    @staticmethod
    def _normalize_inferred_base(typ: TpyType) -> TpyType:
        """Normalize expression type to variable base type for inference."""
        if isinstance(typ, OwnType):
            return typ.wrapped
        if isinstance(typ, IntLiteralType):
            return BIGINT
        return typ

    def record_write(self, name: str, rhs_expr: TpyExpr, rhs_type: TpyType) -> None:
        """Record assignment history for potential later retro-validation."""
        self.ctx.write_history.setdefault(name, []).append((rhs_type, rhs_expr))
        if isinstance(rhs_type, IntLiteralType):
            self.ctx.literal_values.setdefault(name, []).append(rhs_type.value)
        elif name in self.ctx.literal_values:
            # Non-literal write ends literal-only tracking for narrowing decisions.
            self.ctx.literal_values.pop(name, None)

    def retro_validate_against_annotation(
        self,
        name: str,
        annotated_type: TpyType,
        annotation_line: int | None = None,
    ) -> None:
        """Validate earlier writes against a newly provided explicit annotation."""
        for prev_type, prev_expr in self.ctx.write_history.get(name, []):
            try:
                self.compat.check_type_compatible(
                    prev_type,
                    annotated_type,
                    f"earlier assignment to '{name}'",
                    loc=getattr(prev_expr, "loc", None),
                    source_expr=prev_expr,
                    coercion_ctx=CoercionContext.ASSIGN,
                )
            except SemanticError as e:
                detail = e.message
                mismatch_prefix = f"Type mismatch in earlier assignment to '{name}': "
                if detail.startswith(mismatch_prefix):
                    detail = f"Type mismatch: {detail[len(mismatch_prefix):]}"
                if annotation_line is not None:
                    msg = (
                        f"Assignment to '{name}' is incompatible with later annotation "
                        f"'{annotated_type}' at line {annotation_line}: {detail}"
                    )
                else:
                    msg = (
                        f"Assignment to '{name}' is incompatible with later annotation "
                        f"'{annotated_type}': {detail}"
                    )
                raise SemanticError(msg, e.loc) from e

    def resolve_reassignment_target_type(self, name: str, existing_type: TpyType, init_type: TpyType) -> TpyType:
        """Resolve target type for an unannotated reassignment write."""
        if name in self.ctx.authoritative_types:
            return self.ctx.authoritative_types[name]

        # None-seeded inference: None + T => Optional[T]
        if isinstance(existing_type, NoneType):
            base = self._normalize_inferred_base(init_type)
            if isinstance(base, NoneType):
                return existing_type
            self.ctx.unresolved_none_vars.discard(name)
            return OptionalType(base)

        # If this var was seeded by None and already optional, keep it.
        if isinstance(existing_type, OptionalType):
            return existing_type

        # Literal-seeded default (BigInt) may narrow to Int32 when explicitly anchored.
        if name in self.ctx.literal_default_vars and isinstance(existing_type, BigIntType):
            merged = merge_literal_seed_target(init_type, self.ctx.literal_values.get(name, []))
            if merged is not None:
                if not isinstance(init_type, IntLiteralType):
                    self.ctx.literal_default_vars.discard(name)
                return merged
            self.ctx.literal_default_vars.discard(name)
            return existing_type

        return existing_type

    def check_conflicting_annotation(
        self,
        name: str,
        new_type: TpyType,
        node: TpyStmt,
        new_line: int | None = None,
    ) -> None:
        """Reject conflicting explicit annotation writes for the same variable."""
        prev_annot = self.ctx.authoritative_types.get(name)
        if prev_annot is None or prev_annot == new_type:
            return
        prev_line = self.ctx.authoritative_type_lines.get(name)
        if prev_line is not None and new_line is not None:
            conflict_msg = (
                f"Conflicting explicit annotations for '{name}': "
                f"'{prev_annot}' at line {prev_line} vs '{new_type}' at line {new_line}"
            )
        elif prev_line is not None:
            conflict_msg = (
                f"Conflicting explicit annotations for '{name}': "
                f"'{prev_annot}' at line {prev_line} vs '{new_type}'"
            )
        elif new_line is not None:
            conflict_msg = (
                f"Conflicting explicit annotations for '{name}': "
                f"'{prev_annot}' vs '{new_type}' at line {new_line}"
            )
        else:
            conflict_msg = (
                f"Conflicting explicit annotations for '{name}': "
                f"'{prev_annot}' vs '{new_type}'"
            )
        raise self.ctx.error(conflict_msg, node)

    def set_authoritative_annotation(self, name: str, typ: TpyType, line: int | None = None) -> None:
        """Store explicit annotation as authoritative and clear pending inference seeds."""
        self.ctx.authoritative_types[name] = typ
        if line is not None:
            self.ctx.authoritative_type_lines[name] = line
        self.ctx.unresolved_none_vars.discard(name)
        self.ctx.literal_default_vars.discard(name)
