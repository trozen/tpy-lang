"""Unified local type deduction for TurboPython semantic analysis.

Unified local type deduction: reassignment inference, view-type tracking
(str/bytes), and list/dict/set literal tracking, with post-body resolution
for function-local variables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..coercions import CoercionContext
from ..parse import TpyExpr, TpyStmt, TpyName, TpyCall, TpyMethodCall, TpyCoerce, TpyFunction, TpyListRepeat
from ..parse.nodes import TpyStrLiteral, TpyBytesLiteral, TpySubscript, TpyFieldAccess, TpyBinOp, TpyIfExpr
from ..typesys import (

    DictLiteralInfo,
    make_dict,
    FloatLiteralType,
    IntLiteralType,
    ListLiteralInfo,
    make_list,
    ListRepeatType,
    make_array,
    NoneType,
    OptionalType,
    OwnType,
    PendingDictType,
    PendingListType,
    PendingSetType,
    PendingStrType,
    PendingBytesType,
    PendingViewType,
    ViewTypeFamily,
    ViewVarInfo,
    VIEW_TYPE_FAMILIES,
    SetLiteralInfo,
    make_set,
    TpyType,
    TupleType,
    FLOAT,
    UnknownElementType,
    resolve_int_literals,
    unwrap_ref_type,
)
from .context import PENDING_CONTAINER_TYPES
from .diagnostics import SemanticError
from .numeric_lattice import merge_literal_seed_target, numeric_info, widen_numeric_types
from ..type_def_registry import (
    is_set, is_dict, is_array, is_span, is_list, is_fixed_int_type, is_big_int_type,
    is_str_type, is_str_view_type, is_bytes_type, is_bytes_view_type,
)

if TYPE_CHECKING:
    from .compatibility import TypeCompatibility
    from .context import SemanticContext


def _contains_literal_type(typ: TpyType) -> bool:
    """Check if a composite type contains unresolved IntLiteralType/FloatLiteralType."""
    if isinstance(typ, TupleType):
        return any(
            isinstance(e, (IntLiteralType, FloatLiteralType)) or _contains_literal_type(e)
            for e in typ.element_types
        )
    return False


def collect_pending_source_types(ctx: 'SemanticContext', expr: 'TpyExpr') -> 'list[TpyType]':
    """Collect all leaf pending-type nodes from a logical/ternary expression tree.

    Walks and/or (TpyBinOp &&/||) and ternary (TpyIfExpr) subtrees, returning
    every leaf whose sema type is PendingViewType, PendingListType, PendingDictType,
    or PendingSetType.  Callers filter by type for their specific purpose.

    Returns type objects (PendingViewType / PendingListType / etc.), not IDs.
    Callers filter the returned list by type for their specific purpose:
    - View tracking (statements.py): filter by family's pending_type_class, read .var_id
    - List type forcing (expressions.py): filter PendingListType, set .needs_list_type
    """
    if isinstance(expr, TpyCoerce):
        expr = expr.expr
    if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
        return (collect_pending_source_types(ctx, expr.left)
                + collect_pending_source_types(ctx, expr.right))
    if isinstance(expr, TpyIfExpr):
        return (collect_pending_source_types(ctx, expr.then_expr)
                + collect_pending_source_types(ctx, expr.else_expr))
    t = ctx.get_expr_type(expr)
    if isinstance(t, (PendingViewType, PendingListType, PendingDictType, PendingSetType)):
        return [t]
    return []


class LocalTypeDeduction:
    """Unified tracker for local variable type deduction.

    Collects facts during function body analysis and resolves pending types
    (list literal Array-vs-list, string StrView-vs-str, reassignment inference)
    in a single resolve_all() call after the body is fully analyzed.
    """

    def __init__(self, ctx: SemanticContext, compat: TypeCompatibility):
        self.ctx = ctx
        self.compat = compat

    # ------------------------------------------------------------------
    # Reassignment inference (moved from ReassignmentInference)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_inferred_base(typ: TpyType) -> TpyType:
        """Normalize expression type to variable base type for inference."""
        if isinstance(typ, OwnType):
            return typ.wrapped
        return typ

    def record_write(self, name: str, rhs_expr: TpyExpr, rhs_type: TpyType) -> None:
        """Record assignment history for potential later retro-validation."""
        self.ctx.func.write_history.setdefault(name, []).append((rhs_type, rhs_expr))
        if isinstance(rhs_type, IntLiteralType) and rhs_type.value is not None:
            self.ctx.func.literal_values.setdefault(name, []).append(rhs_type.value)
        elif name in self.ctx.func.literal_values:
            # Non-literal write ends literal-only tracking for narrowing decisions.
            self.ctx.func.literal_values.pop(name, None)

    def retro_validate_against_annotation(
        self,
        name: str,
        annotated_type: TpyType,
        annotation_line: int | None = None,
    ) -> None:
        """Validate earlier writes against a newly provided explicit annotation."""
        for prev_type, prev_expr in self.ctx.func.write_history.get(name, []):
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

    def resolve_reassignment_target_type(
        self,
        name: str,
        existing_type: TpyType,
        init_type: TpyType,
        init_expr: TpyExpr | None = None,
    ) -> TpyType:
        """Resolve target type for an unannotated reassignment write."""
        if name in self.ctx.func.authoritative_types:
            return self.ctx.func.authoritative_types[name]

        # Resolve float literals to float64 before any widening logic
        if isinstance(init_type, FloatLiteralType):
            init_type = FLOAT

        # None-seeded inference: None + T => Optional[T]
        if isinstance(existing_type, NoneType):
            base = self._normalize_inferred_base(init_type)
            if isinstance(base, IntLiteralType):
                base = self.ctx.default_int_for_literal(base, warn_node=init_expr)
            if isinstance(base, NoneType):
                return existing_type
            self.ctx.func.unresolved_none_vars.discard(name)
            return OptionalType(base)

        # If this var was seeded by None and already optional, keep it.
        if isinstance(existing_type, OptionalType):
            return existing_type

        # Literal-seeded default (ctx.default_int_type) may be refined by
        # explicit later writes (e.g., BigInt/Int64/Float anchors).
        if name in self.ctx.func.literal_default_vars:
            merged = merge_literal_seed_target(existing_type, init_type, self.ctx.func.literal_values.get(name, []))
            if merged is not None:
                if (
                    is_fixed_int_type(existing_type)
                    and isinstance(init_type, IntLiteralType)
                    and is_big_int_type(merged)
                    and init_expr is not None
                    and init_type.value is not None
                ):
                    self.ctx.warning(
                        f"Integer literal {init_type.value} is outside default {existing_type} range; "
                        "promoting variable to int (BigInt).",
                        init_expr,
                    )
                if not isinstance(init_type, IntLiteralType):
                    self.ctx.func.literal_default_vars.discard(name)
                return merged
            self.ctx.func.literal_default_vars.discard(name)
            return existing_type

        # Numeric widening: different numeric types widen to the wider type.
        widened = widen_numeric_types(existing_type, init_type)
        if widened is not None:
            return widened

        return existing_type

    def check_conflicting_annotation(
        self,
        name: str,
        new_type: TpyType,
        node: TpyStmt,
        new_line: int | None = None,
    ) -> None:
        """Reject conflicting explicit annotation writes for the same variable."""
        prev_annot = self.ctx.func.authoritative_types.get(name)
        if prev_annot is None or prev_annot == new_type:
            return
        prev_line = self.ctx.func.authoritative_type_lines.get(name)
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
        self.ctx.func.authoritative_types[name] = typ
        if line is not None:
            self.ctx.func.authoritative_type_lines[name] = line
        self.ctx.func.unresolved_none_vars.discard(name)
        self.ctx.func.literal_default_vars.discard(name)

    # ------------------------------------------------------------------
    # List literal deduction (moved from ListLiteralTracker)
    # ------------------------------------------------------------------

    def mark_list_mutated(self, obj_expr: TpyExpr) -> None:
        """Mark a list literal as mutated if it can be traced to one."""
        if isinstance(obj_expr, TpyName):
            var_name = obj_expr.name
            if var_name in self.ctx.func.variable_to_literal:
                literal_id = self.ctx.func.variable_to_literal[var_name]
                if literal_id in self.ctx.list_literals:
                    self.ctx.list_literals[literal_id].is_mutated = True

    def infer_empty_list_element_type(self, obj_expr: TpyExpr, value_type: TpyType) -> None:
        """Infer element type for an empty list literal from usage (e.g. .append(v)).

        If the list's element type is still UNKNOWN_ELEMENT, set it from value_type.
        If already set, widen using the numeric lattice (same rules as variable
        reassignment widening).
        """
        if not isinstance(obj_expr, TpyName):
            return
        var_name = obj_expr.name
        literal_id = self.ctx.func.variable_to_literal.get(var_name)
        if literal_id is None:
            return
        info = self.ctx.list_literals.get(literal_id)
        if info is None:
            return
        self._update_list_element_type(info, value_type)
        # Propagate up the entire alias chain (zs = ys = xs; zs.append(v))
        visited: set[int] = {info.literal_id}
        current = info
        while current.source_literal_id is not None:
            if current.source_literal_id in visited:
                break
            visited.add(current.source_literal_id)
            source = self.ctx.list_literals.get(current.source_literal_id)
            if source is None:
                break
            self._update_list_element_type(source, value_type)
            current = source

    @staticmethod
    def _widen_inferred_type(current: TpyType, new_type: TpyType) -> Optional[TpyType]:
        """Widen an inferred container element type with a new observation.

        Returns the updated type, or None if unchanged (same type, or
        incompatible types that normal type checking will catch).
        """
        # Resolve float literals to float64 before comparisons
        if isinstance(current, FloatLiteralType):
            current = FLOAT
        if isinstance(new_type, FloatLiteralType):
            new_type = FLOAT
        if isinstance(current, UnknownElementType):
            return new_type
        widened = widen_numeric_types(current, new_type)
        if widened is not None:
            return widened
        if current == new_type:
            return None
        if isinstance(current, IntLiteralType) and not isinstance(new_type, IntLiteralType):
            if numeric_info(new_type) is not None:
                return new_type
        if isinstance(new_type, IntLiteralType) and not isinstance(current, IntLiteralType):
            return None  # keep existing concrete type
        return None  # incompatible -- let normal type checking catch it

    def _update_list_element_type(self, info: ListLiteralInfo, value_type: TpyType) -> None:
        """Update element type for a ListLiteralInfo, widening if needed."""
        result = self._widen_inferred_type(info.element_type, value_type)
        if result is not None:
            info.element_type = result

    def mark_container_param_context(self, arg_expr: TpyExpr, arg_type: TpyType, param_type: TpyType) -> None:
        """Track parameter context for list/dict/set literal inference.

        Single entry point for all container types -- handles list (passed_to_list_param,
        passed_to_span_param), dict (key/value widening), and set (element widening).
        """
        if isinstance(arg_expr, TpyCoerce):
            arg_expr = arg_expr.expr
        if not isinstance(arg_expr, TpyName):
            return
        param_type = unwrap_ref_type(param_type)

        if isinstance(arg_type, PendingListType):
            literal_id = self.ctx.func.variable_to_literal.get(arg_expr.name)
            if literal_id is not None and literal_id in self.ctx.list_literals:
                info = self.ctx.list_literals[literal_id]
                if is_list(param_type):
                    info.passed_to_list_param = True
                    info.coerced_element_type = param_type.type_args[0]
                elif is_span(param_type):
                    info.passed_to_span_param = True
                    info.coerced_element_type = param_type.type_args[0]

        elif isinstance(arg_type, PendingDictType) and is_dict(param_type):
            literal_id = self.ctx.func.variable_to_dict_literal.get(arg_expr.name)
            if literal_id is not None:
                info = self.ctx.dict_literals.get(literal_id)
                if info:
                    result = self._widen_inferred_type(info.key_type, param_type.type_args[0])
                    if result is not None:
                        info.key_type = result
                    result = self._widen_inferred_type(info.value_type, param_type.type_args[1])
                    if result is not None:
                        info.value_type = result

        elif isinstance(arg_type, PendingSetType) and is_set(param_type):
            literal_id = self.ctx.func.variable_to_set_literal.get(arg_expr.name)
            if literal_id is not None:
                info = self.ctx.set_literals.get(literal_id)
                if info:
                    result = self._widen_inferred_type(info.element_type, param_type.type_args[0])
                    if result is not None:
                        info.element_type = result

    def mark_list_different_size(self, literal_id: int) -> None:
        """Mark a pending list literal as needing list (different-size reassignment)."""
        if literal_id in self.ctx.list_literals:
            self.ctx.list_literals[literal_id].is_mutated = True

    def link_list_literals(self, target_id: int, value_id: int) -> None:
        """Link target literal to value literal for alias-based promotion.

        Sets a one-directional edge; the resolution pass propagates promotion
        in both directions (Array->List transitivity) via the while-changed loop.
        """
        target = self.ctx.list_literals.get(target_id)
        if target is None:
            return
        if target.source_literal_id is None:
            target.source_literal_id = value_id
        else:
            # Target already linked to a different source -- it can hold multiple
            # distinct list literals, so all three must be promoted to list.
            target.is_mutated = True
            if target.source_literal_id in self.ctx.list_literals:
                self.ctx.list_literals[target.source_literal_id].is_mutated = True
            if value_id in self.ctx.list_literals:
                self.ctx.list_literals[value_id].is_mutated = True

    def mark_list_return_context(self, return_expr: TpyExpr, return_type: TpyType) -> None:
        """Track return-type context for list literal inference."""
        return_type = unwrap_ref_type(return_type)
        if isinstance(return_type, OwnType):
            return_type = return_type.wrapped

        if isinstance(return_expr, TpyCoerce):
            return_expr = return_expr.expr

        if isinstance(return_expr, TpyName):
            var_name = return_expr.name
            literal_id = self.ctx.func.variable_to_literal.get(var_name)
            if literal_id is not None and literal_id in self.ctx.list_literals:
                info = self.ctx.list_literals[literal_id]
                if is_list(return_type):
                    info.passed_to_list_param = True
                    info.coerced_element_type = return_type.type_args[0]

    def register_list_alias(self, var_name: str, init_type: PendingListType, decl_line: int | None = None) -> PendingListType:
        """Register alias relationship when b = a where a is a PendingListType.

        Creates a new ListLiteralInfo for the alias variable with source_literal_id
        pointing to the original. Returns a new PendingListType for the alias.
        """
        source_literal_id = init_type.literal_id
        source_info = self.ctx.list_literals.get(source_literal_id)
        if source_info is None:
            return init_type

        new_id = self.ctx.literal_counter
        self.ctx.literal_counter += 1
        info = ListLiteralInfo(
            literal_id=new_id,
            expr=source_info.expr,
            element_type=init_type.element_type,
            size=init_type.size,
            variable_name=var_name,
            decl_line=decl_line,
            source_literal_id=source_literal_id,
        )
        self.ctx.list_literals[new_id] = info
        self.ctx.func.pending_resolutions.append(new_id)
        self.ctx.func.variable_to_literal[var_name] = new_id
        return PendingListType(init_type.element_type, init_type.size, new_id)

    def _resolve_alias_element_type(self, info: ListLiteralInfo) -> TpyType | None:
        """Walk alias chain to find an inferred element type from a linked literal."""
        visited: set[int] = {info.literal_id}
        current = info
        while current.source_literal_id is not None:
            if current.source_literal_id in visited:
                break
            visited.add(current.source_literal_id)
            source = self.ctx.list_literals.get(current.source_literal_id)
            if source is None:
                break
            if not isinstance(source.element_type, UnknownElementType):
                return source.element_type
            current = source
        return None

    def _apply_container_resolution(
        self,
        info: ListLiteralInfo | DictLiteralInfo | SetLiteralInfo,
        resolved: TpyType,
    ) -> None:
        """Apply a resolved type to a container literal info.

        Shared epilogue for list, dict, and set resolution -- updates
        resolved_type, expr_types, call_type, scope binding, and
        declared_var_types in a single code path.
        """
        info.resolved_type = resolved
        self.ctx.set_expr_type(info.expr, resolved)

        if isinstance(info.expr, TpyCall):
            info.expr.call_type = resolved

        if info.variable_name and self.ctx.func.current_scope:
            current_type = self.ctx.func.current_scope.lookup(info.variable_name)
            if isinstance(current_type, PENDING_CONTAINER_TYPES):
                self.ctx.func.current_scope.define(info.variable_name, resolved)

        if info.variable_name and info.decl_line is not None:
            self.ctx.declared_var_types[(info.decl_line, info.variable_name)] = resolved

    def _resolve_pending_list_types(self) -> None:
        """Resolve all pending list types after function analysis.

        Resolution rules (in priority order):
        1. Explicit annotation -> use it
        2. is_mutated -> ListType
        3. passed_to_list_param -> ListType
        4. Otherwise -> ArrayType

        Element type resolution:
        - If passed to typed param (list[T] or Span[T]), use T
        - IntLiteralType defaults to ctx.default_int_type for containers
        """
        for literal_id in self.ctx.func.pending_resolutions:
            if literal_id not in self.ctx.list_literals:
                continue

            info = self.ctx.list_literals[literal_id]

            # Resolve element type
            # Priority: coerced type from param > inferred from usage > resolved inner PendingListType > default
            elem_type = info.element_type

            # Empty list with unknown element type -- check param/return context first
            if isinstance(elem_type, UnknownElementType):
                if info.coerced_element_type is not None:
                    elem_type = info.coerced_element_type
                else:
                    # Check source literal for alias chains (ys = xs; ys.append(v))
                    source_elem = self._resolve_alias_element_type(info)
                    if source_elem is not None:
                        elem_type = source_elem
                    else:
                        var_desc = f"list '{info.variable_name}'" if info.variable_name else "empty list literal"
                        raise self.ctx.error(
                            f"Cannot infer element type for {var_desc}; "
                            f"add a type annotation (e.g., {info.variable_name or 'x'}: list[T] = []) "
                            f"or use the list so the type can be inferred",
                            info.expr,
                        )

            # If element type is a PendingListType, look up its resolved type
            if isinstance(elem_type, PendingListType):
                inner_info = self.ctx.list_literals.get(elem_type.literal_id)
                if inner_info and inner_info.resolved_type:
                    elem_type = inner_info.resolved_type

            # Coerced type from param/return context overrides inferred type
            if info.coerced_element_type is not None and not isinstance(elem_type, UnknownElementType):
                elem_type = info.coerced_element_type

            if isinstance(elem_type, IntLiteralType):
                # Use configured integer default when no stronger context exists.
                elem_type = self.ctx.default_int_for_literal(elem_type)
            elif _contains_literal_type(elem_type):
                elem_type = resolve_int_literals(elem_type, self.ctx.default_int_for_literal)
            if isinstance(elem_type, PendingViewType):
                # Container elements are owned -- views can't be stored in a list.
                elem_type = elem_type.family.owned_type

            # Determine resolved type
            is_repeat = isinstance(info.expr, TpyListRepeat)
            if info.has_explicit_annotation and info.explicit_type:
                resolved = info.explicit_type
            elif info.is_mutated:
                resolved = make_list(elem_type)
            elif info.needs_list_type:
                # Both ternary branches must have the same C++ type; Array sizes may differ.
                resolved = make_list(elem_type)
            elif info.passed_to_list_param:
                resolved = make_list(elem_type)
            elif info.is_global:
                # Globals can be imported and mutated by other modules
                resolved = make_list(elem_type)
            elif info.passed_to_span_param and is_repeat and info.size < 0:
                # Variable count repeat to Span -- needs contiguous memory, materialize
                resolved = make_list(elem_type)
            elif info.size < 0 and info.needs_indexing:
                # Variable count repeat with subscript access -- materialize for operator[]
                resolved = make_list(elem_type)
            elif info.size < 0:
                # Variable count (repeat with non-constant N) -- stays lazy
                resolved = ListRepeatType(elem_type)
            else:
                # Default: Array (stack-allocated, no mutation detected)
                resolved = make_array(elem_type, info.size)

            self._apply_container_resolution(info, resolved)

        # Second pass: bidirectional alias propagation.
        # If either side of an alias pair resolved to list, the other must too
        # (they share identity in Python semantics).
        # Each node transitions at most once (Array->List), so this terminates
        # in at most len(pending_resolutions) iterations.
        changed = True
        while changed:
            changed = False
            for literal_id in self.ctx.func.pending_resolutions:
                info = self.ctx.list_literals.get(literal_id)
                if info is None or info.source_literal_id is None:
                    continue
                source = self.ctx.list_literals.get(info.source_literal_id)
                if source is None:
                    continue
                # Forward: source became list -> alias must too
                if is_array(info.resolved_type) and is_list(source.resolved_type):
                    info.resolved_type = make_list(info.resolved_type.type_args[0])
                    self._update_resolved_binding(info)
                    changed = True
                # Reverse: alias became list -> source must too
                elif is_array(source.resolved_type) and is_list(info.resolved_type):
                    source.resolved_type = make_list(source.resolved_type.type_args[0])
                    self._update_resolved_binding(source)
                    changed = True

    def _update_resolved_binding(self, info: 'ListLiteralInfo') -> None:
        """Update scope and var_types after alias propagation changes a resolved type."""
        resolved = info.resolved_type
        if resolved is None:
            return
        self.ctx.set_expr_type(info.expr, resolved)
        if info.variable_name and self.ctx.func.current_scope:
            current_type = self.ctx.func.current_scope.lookup(info.variable_name)
            if current_type is not None:
                self.ctx.func.current_scope.define(info.variable_name, resolved)
        if info.variable_name and info.decl_line is not None:
            self.ctx.declared_var_types[(info.decl_line, info.variable_name)] = resolved
        if info.variable_name:
            var_decl = self.ctx.func.var_decl_by_name.get(info.variable_name)
            if var_decl:
                self.ctx.var_types[id(var_decl)] = resolved

    # ------------------------------------------------------------------
    # Dict literal deduction
    # ------------------------------------------------------------------

    def infer_dict_key_value_types(self, obj_expr: TpyExpr, key_type: TpyType, value_type: TpyType) -> None:
        """Infer key/value types for an empty dict literal from subscript assignment (d[k] = v)."""
        if not isinstance(obj_expr, TpyName):
            return
        var_name = obj_expr.name
        literal_id = self.ctx.func.variable_to_dict_literal.get(var_name)
        if literal_id is None:
            return
        info = self.ctx.dict_literals.get(literal_id)
        if info is None:
            return
        self._update_dict_type_param(info, "key", key_type)
        self._update_dict_type_param(info, "value", value_type)

    def _update_dict_type_param(self, info: DictLiteralInfo, which: str, new_type: TpyType) -> None:
        """Update key or value type for a DictLiteralInfo, widening if needed."""
        current = info.key_type if which == "key" else info.value_type
        result = self._widen_inferred_type(current, new_type)
        if result is not None:
            if which == "key":
                info.key_type = result
            else:
                info.value_type = result

    def _resolve_pending_dict_and_set_types(self) -> None:
        """Resolve all pending dict and set types after function analysis.

        Dict and set share the same resolution logic: check for unresolved UNKNOWN
        element types (error), resolve IntLiteralType defaults, then apply resolution.
        Merged into a single method to guarantee identical handling.
        """
        # Process dicts
        for literal_id in self.ctx.func.pending_dict_resolutions:
            info = self.ctx.dict_literals.get(literal_id)
            if info is None:
                continue

            key_type = info.key_type
            value_type = info.value_type

            if isinstance(key_type, UnknownElementType) or isinstance(value_type, UnknownElementType):
                var_desc = f"dict '{info.variable_name}'" if info.variable_name else "empty dict literal"
                raise self.ctx.error(
                    f"Cannot infer types for {var_desc}; "
                    f"add a type annotation (e.g., {info.variable_name or 'd'}: dict[K, V] = {{}}) "
                    f"or use the dict so the types can be inferred",
                    info.expr,
                )

            if isinstance(key_type, IntLiteralType):
                key_type = self.ctx.default_int_for_literal(key_type)
            if isinstance(value_type, IntLiteralType):
                value_type = self.ctx.default_int_for_literal(value_type)
            if isinstance(key_type, PendingViewType):
                key_type = key_type.family.owned_type
            if isinstance(value_type, PendingViewType):
                value_type = value_type.family.owned_type

            self._apply_container_resolution(info, make_dict(key_type, value_type))

        # Process sets
        for literal_id in self.ctx.func.pending_set_resolutions:
            info = self.ctx.set_literals.get(literal_id)
            if info is None:
                continue

            elem_type = info.element_type

            if isinstance(elem_type, UnknownElementType):
                var_desc = f"set '{info.variable_name}'" if info.variable_name else "empty set"
                raise self.ctx.error(
                    f"Cannot infer element type for {var_desc}; "
                    f"add a type annotation (e.g., {info.variable_name or 's'}: set[T] = set()) "
                    f"or use the set so the type can be inferred",
                    info.expr,
                )

            if isinstance(elem_type, IntLiteralType):
                elem_type = self.ctx.default_int_for_literal(elem_type)
            if isinstance(elem_type, PendingViewType):
                elem_type = elem_type.family.owned_type

            self._apply_container_resolution(info, make_set(elem_type))

    # ------------------------------------------------------------------
    # Set literal deduction
    # ------------------------------------------------------------------

    def infer_set_element_type(self, obj_expr: TpyExpr, value_type: TpyType) -> None:
        """Infer element type for an empty set from .add() usage."""
        if not isinstance(obj_expr, TpyName):
            return
        var_name = obj_expr.name
        literal_id = self.ctx.func.variable_to_set_literal.get(var_name)
        if literal_id is None:
            return
        info = self.ctx.set_literals.get(literal_id)
        if info is None:
            return
        result = self._widen_inferred_type(info.element_type, value_type)
        if result is not None:
            info.element_type = result

    # ------------------------------------------------------------------
    # String variable deduction (moved from StrVarTracker)
    # ------------------------------------------------------------------

    def is_view_compatible_source(self, init_expr: TpyExpr, init_type: TpyType) -> bool:
        """Check if init_expr produces a view-safe value (no owned copy needed).

        View-safe sources:
        - String literal (static lifetime)
        - A str/bytes parameter (already view type in C++)
        - Another PendingViewType or view-type local
        - A Final[str] constant (constexpr string_view)
        - A function returning a view type
        - Subscript on lvalue tuple (immutable, stable element storage)

        Note: bytes literals are NOT view-safe (temporary vectors, unlike string
        literals which have static storage).
        """
        is_str = is_str_type(init_type) or is_str_view_type(init_type) or isinstance(init_type, PendingStrType)
        is_bytes = is_bytes_type(init_type) or is_bytes_view_type(init_type) or isinstance(init_type, PendingBytesType)
        if not (is_str or is_bytes):
            return False

        if isinstance(init_expr, TpyCoerce):
            init_expr = init_expr.expr

        # String/bytes literal -> static lifetime, always view-safe.
        # Codegen emits bytes literals as static constexpr uint8_t[] arrays
        # when the target type is BytesView, giving them static storage.
        if isinstance(init_expr, (TpyStrLiteral, TpyBytesLiteral)):
            return True

        # Named variable reference
        if isinstance(init_expr, TpyName):
            name = init_expr.name
            # Check if it's a str/bytes parameter (C++ already passes as view)
            func = self.ctx.func.current_function
            if isinstance(func, TpyFunction):
                for pname, ptype in func.params:
                    if pname == name:
                        if is_str and (is_str_type(ptype) or is_str_view_type(ptype)):
                            return True
                        if is_bytes and (is_bytes_type(ptype) or is_bytes_view_type(ptype)):
                            return True

            # Another pending or view local (must match the same family)
            scope_type = self.ctx.func.current_scope.lookup(name) if self.ctx.func.current_scope else None
            if is_str and (isinstance(scope_type, PendingStrType) or is_str_view_type(scope_type)):
                return True
            if is_bytes and (isinstance(scope_type, PendingBytesType) or is_bytes_view_type(scope_type)):
                return True

            # Final[str] global constant
            if is_str and name in self.ctx.final_globals:
                return True

        # Function/method call returning a view type
        if isinstance(init_expr, (TpyCall, TpyMethodCall)):
            if is_str_view_type(init_type) or is_bytes_view_type(init_type):
                return True

        # Subscript on lvalue container -- source-mutation tracking
        # (source_mutated flag on ViewVarInfo) falls back to the owned type
        # if the source is mutated, so view is safe while source is live.
        # Only single-level access (container[i] where container is a name)
        # to ensure _borrow_storage_root can track the source.
        if isinstance(init_expr, TpySubscript) and self.compat.is_lvalue(init_expr):
            obj_type = self.ctx.get_expr_type(init_expr.obj)
            if isinstance(obj_type, TupleType):
                return True
            if obj_type.subscript_borrows() and isinstance(init_expr.obj, TpyName):
                return True

        # Field access on lvalue record -- field storage is stable unless
        # the record field is reassigned (tracked by source_mutated).
        # Only single-level access (obj.field where obj is a name) is safe;
        # nested chains (p.inner.name) cannot be tracked by _borrow_storage_root.
        if isinstance(init_expr, TpyFieldAccess) and self.compat.is_lvalue(init_expr):
            if isinstance(init_expr.obj, TpyName):
                return True

        # and/or: view-safe if both operands are view-safe. Pending operands
        # are excluded because they go through the chained-pending branch in
        # _infer_new_local_type and never reach is_view_compatible_source.
        if isinstance(init_expr, TpyBinOp) and init_expr.op in ("&&", "||"):
            left_type = self.ctx.get_expr_type(init_expr.left)
            right_type = self.ctx.get_expr_type(init_expr.right)
            if isinstance(left_type, PendingViewType):
                return False
            if isinstance(right_type, PendingViewType):
                return False
            return (self.is_view_compatible_source(init_expr.left, left_type)
                    and self.is_view_compatible_source(init_expr.right, right_type))

        # Ternary: same logic as and/or above.
        if isinstance(init_expr, TpyIfExpr):
            then_type = self.ctx.get_expr_type(init_expr.then_expr)
            else_type = self.ctx.get_expr_type(init_expr.else_expr)
            if isinstance(then_type, PendingViewType):
                return False
            if isinstance(else_type, PendingViewType):
                return False
            return (self.is_view_compatible_source(init_expr.then_expr, then_type)
                    and self.is_view_compatible_source(init_expr.else_expr, else_type))

        return False

    # --- View-type local tracking (generic across str/bytes families) ---

    def mark_view_augassign(self, var_name: str, family: ViewTypeFamily) -> None:
        """Mark a pending view-type variable as used in augmented assignment (+=)."""
        var_id = self.ctx.view_var_map(family).get(var_name)
        if var_id is not None and var_id in self.ctx.view_vars(family):
            self.ctx.view_vars(family)[var_id].used_in_augassign = True

    def mark_view_param_context(self, arg_expr: TpyExpr, param_type: TpyType, family: ViewTypeFamily) -> None:
        """Track when a pending view-type var is passed to a promote-param type."""
        if not family.promote_param_match(param_type):
            return
        if isinstance(arg_expr, TpyCoerce):
            arg_expr = arg_expr.expr
        if isinstance(arg_expr, TpyName):
            var_id = self.ctx.view_var_map(family).get(arg_expr.name)
            if var_id is not None and var_id in self.ctx.view_vars(family):
                self.ctx.view_vars(family)[var_id].passed_to_promote_param = True

    def mark_view_reassigned_from_owned(self, var_name: str, family: ViewTypeFamily) -> None:
        """Mark a pending view-type variable as reassigned from an owned source."""
        var_id = self.ctx.view_var_map(family).get(var_name)
        if var_id is not None and var_id in self.ctx.view_vars(family):
            self.ctx.view_vars(family)[var_id].reassigned_from_owned = True

    def track_view_reassign_source(self, var_name: str, source_type: TpyType, family: ViewTypeFamily) -> None:
        """Track source relationship when reassigning from another pending view-type."""
        if not isinstance(source_type, family.pending_type_class):
            return
        var_id = self.ctx.view_var_map(family).get(var_name)
        if var_id is not None and var_id in self.ctx.view_vars(family):
            self.ctx.view_vars(family)[var_id].source_var_ids = [source_type.var_id]

    def _resolve_pending_view_types(self, family: ViewTypeFamily) -> None:
        """Resolve all pending view types for the given family.

        Resolution rules:
        - Any owned flag set -> resolve to owned type (str / bytes)
        - Otherwise -> resolve to view type (StrView / BytesView)

        After the first pass, aliases whose source resolved to owned are
        retroactively promoted (a view of a buffer that may reallocate
        would dangle).
        """
        pending = self.ctx.view_pending_resolutions(family)
        vars_reg = self.ctx.view_vars(family)

        # First pass: resolve based on direct usage flags
        for var_id in pending:
            info = vars_reg.get(var_id)
            if info is None:
                continue

            needs_owned = (
                info.initialized_from_owned
                or info.used_in_augassign
                or info.passed_to_promote_param
                or info.reassigned_from_owned
                or info.source_mutated
            )

            info.resolved_type = family.owned_type if needs_owned else family.view_type

        # Second pass: promote aliases whose source resolved to owned.
        # An or/ternary result may have multiple sources; if ANY resolves to
        # owned, the result must too (it might point to that buffer at runtime).
        changed = True
        while changed:
            changed = False
            for var_id in pending:
                info = vars_reg.get(var_id)
                if info is None or info.resolved_type != family.view_type:
                    continue
                for src_id in info.source_var_ids:
                    source = vars_reg.get(src_id)
                    if source and source.resolved_type == family.owned_type:
                        info.resolved_type = family.owned_type
                        changed = True
                        break

        # Update scope bindings and var_types
        for var_id in pending:
            info = vars_reg.get(var_id)
            if info is None:
                continue
            resolved = info.resolved_type

            if info.variable_name and self.ctx.func.current_scope:
                current_type = self.ctx.func.current_scope.lookup(info.variable_name)
                if isinstance(current_type, family.pending_type_class):
                    self.ctx.func.current_scope.define(info.variable_name, resolved)

            var_decl = self.ctx.func.var_decl_by_name.get(info.variable_name)
            if var_decl:
                self.ctx.var_types[id(var_decl)] = resolved
            if info.decl_line is not None:
                self.ctx.declared_var_types[(info.decl_line, info.variable_name)] = resolved

    # ------------------------------------------------------------------
    # Unified resolution entry point
    # ------------------------------------------------------------------

    def _check_unresolved_none_inference(self) -> None:
        """Reject variables left as bare None without an inferred or annotated type."""
        if not self.ctx.func.unresolved_none_vars:
            return
        name = sorted(self.ctx.func.unresolved_none_vars)[0]
        # Emit at the first None write location.
        for typ, expr in self.ctx.func.write_history.get(name, []):
            if isinstance(typ, NoneType):
                raise self.ctx.error(
                    f"Cannot infer type for '{name}': assigned None but never assigned a concrete value; "
                    f"add a type annotation (e.g., {name}: T | None = None)",
                    expr
                )
        raise self.ctx.error(
            f"Cannot infer type for '{name}': assigned None but never assigned a concrete value; "
            f"add a type annotation (e.g., {name}: T | None = None)"
        )

    def _check_unresolved_pending_generics(self) -> None:
        """Reject pending generic instances that were never fully resolved."""
        if not self.ctx.func.pending_generic_instances:
            return
        # Report the first unresolved instance.
        info = next(iter(self.ctx.func.pending_generic_instances.values()))
        unresolved = [tp for tp in info.type_params if tp not in info.inferred]
        raise self.ctx.error(
            f"Cannot infer type argument{'s' if len(unresolved) != 1 else ''} "
            f"{', '.join(unresolved)} for '{info.variable_name}'; "
            f"add explicit type args (e.g., {info.record_name}"
            f"[{', '.join(info.type_params)}]()) "
            f"or call a method that constrains the type parameters",
            info.expr,
        )

    def resolve_all(self) -> None:
        """Resolve all pending local type deductions after function body analysis.

        1. Check unresolved None vars (error if any)
        2. Resolve pending list types (Array vs list)
        3. Resolve pending dict and set types
        4. Resolve pending str types (StrView vs str)
        5. Check unresolved pending generic instances (error if any)
        """
        self._check_unresolved_none_inference()
        self._resolve_pending_list_types()
        self._resolve_pending_dict_and_set_types()
        for family in VIEW_TYPE_FAMILIES:
            self._resolve_pending_view_types(family)
        self._check_unresolved_pending_generics()
