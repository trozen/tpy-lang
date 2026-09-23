"""Unified local type deduction for TurboPython semantic analysis.

Unified local type deduction: reassignment inference, view-type tracking
(str/bytes), and list/dict/set literal tracking, with post-body resolution
for function-local variables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ..coercions import CoercionContext, resolve_coercion
from ..parse import TpyExpr, TpyStmt, TpyName, TpyCall, TpyMethodCall, TpyCoerce, TpyFunction, TpyListRepeat
from ..parse.nodes import (TpyStrLiteral, TpyBytesLiteral, TpySubscript, TpyFieldAccess,
                           TpyBinOp, TpyIfExpr, TpyNamedExpr)
from ..typesys import (
    recorded_return_borrow_sources,

    collapse_tuple_own_elements,
    DictLiteralInfo,
    make_dict,
    FloatLiteralType,
    IntLiteralType,
    ListLiteralInfo,
    make_list,
    ListRepeatType,
    LiteralType,
    make_array,
    NoneType,
    is_numeric_type,
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
    STR,
    UnknownElementType,
    resolve_int_literals,
    unwrap_own,
    unwrap_qualifiers,
    unwrap_readonly,
    unwrap_ref_type,
)
from .context import (PENDING_CONTAINER_TYPES, MODULE_INIT_CONTEXT,
                      contains_pending_leaf)
from ..namespace import BindingKind
from ..diagnostics import SemanticError
from .numeric_lattice import (
    fixed_int_range_contains, merge_literal_seed_target,
    numeric_info, widen_numeric_types,
)
from ..type_def_registry import (
    is_set, is_dict, is_array, is_span, is_list, is_fixed_int_type, is_big_int_type,
    is_str_type, is_str_view_type, is_bytes_type, is_bytes_view_type,
    is_enum_type,
    is_borrowing_view_type,
    is_bool_type,
    is_char_type,
)

if TYPE_CHECKING:
    from .compatibility import TypeCompatibility
    from .context import SemanticContext
    from ..parse.nodes import SourceLocation


def _contains_literal_type(typ: TpyType) -> bool:
    """Check if a composite type contains unresolved IntLiteralType/FloatLiteralType."""
    if isinstance(typ, TupleType):
        return any(
            isinstance(e, (IntLiteralType, FloatLiteralType)) or _contains_literal_type(e)
            for e in typ.element_types
        )
    return False


def is_enum_name_read(expr: 'TpyExpr', ctx: 'SemanticContext') -> bool:
    """True for an enum member's `.name`.

    Its value is a view of EnumUtil's STATIC member-name table, not of the
    receiver (sema types it STRVIEW in `sema/expressions.py`), so it has the
    static lifetime a `str` literal has. The fact belongs to the FIELD, not
    to the receiver: `.value` off the same receiver is the underlying value
    and borrows nothing static. Spelled once because both view classifiers
    (`is_view_compatible_source` and `_is_static_view_leaf`) need it and a
    second spelling could answer differently.

    The read's own STRVIEW type is part of the question, not a caller's
    precondition: the claim being made is about a view of that table, so a
    read typed anything else cannot be answered True here and leave a
    caller that forgot the guard asserting static lifetime for it.
    """
    return (isinstance(expr, TpyFieldAccess)
            and expr.field == "name"
            and is_str_view_type(ctx.get_expr_type(expr))
            and is_enum_type(ctx.get_expr_type(expr.obj)))


def _is_bufferless_scalar(t: TpyType) -> bool:
    """A value no view can point into: a number, bool, char, enum or None."""
    return (is_numeric_type(t) or is_bool_type(t) or is_char_type(t)
            or is_enum_type(t) or isinstance(t, NoneType))


def walk_view_source_leaves(expr: 'TpyExpr', leaf_fn: 'Callable[[TpyExpr], list]') -> 'list':
    """Apply leaf_fn to every leaf arm a view-deduced local can borrow at runtime.

    One shared recursion over and/or (TpyBinOp &&/||), ternary (TpyIfExpr), and
    transparent TpyCoerce, so view-source leaf classifiers (pending-source and
    borrow-root collection) can't drift in which arms they consider.
    """
    if isinstance(expr, TpyCoerce):
        return walk_view_source_leaves(expr.expr, leaf_fn)
    if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
        return (walk_view_source_leaves(expr.left, leaf_fn)
                + walk_view_source_leaves(expr.right, leaf_fn))
    if isinstance(expr, TpyIfExpr):
        return (walk_view_source_leaves(expr.then_expr, leaf_fn)
                + walk_view_source_leaves(expr.else_expr, leaf_fn))
    return leaf_fn(expr)


def view_source_is_temporary(expr: TpyExpr) -> bool:
    """True if a view bound to `expr` would reference end-of-statement temporary
    storage (no durable lvalue/static root).

    Unlike is_dangling_return, a local name/field is treated as STABLE -- it
    outlives a same-scope binding -- so only genuine temporaries (fresh calls,
    f-strings, binops, and views borrowing them) lack durable storage. Used to
    reject an explicit `StrView`/`BytesView` annotation bound to a temporary,
    while leaving pinned-view aliases of stable locals/fields valid.
    """
    if isinstance(expr, TpyCoerce):
        return view_source_is_temporary(expr.expr)
    # A walrus hands out its wrapped value: provenance follows it.
    if isinstance(expr, TpyNamedExpr):
        return view_source_is_temporary(expr.value)
    if isinstance(expr, (TpyName, TpyFieldAccess, TpyStrLiteral, TpyBytesLiteral)):
        return False
    # A slice borrows its container: temp iff the container is temp.
    if isinstance(expr, TpySubscript):
        return view_source_is_temporary(expr.obj)
    # A view-returning method borrows its receiver (str.strip etc.); an owned
    # return is a fresh temporary regardless of receiver.
    if isinstance(expr, TpyMethodCall):
        fi = expr.resolved_function_info
        ret = fi.return_type if fi is not None else None
        if ret is not None and is_borrowing_view_type(unwrap_readonly(ret)):
            return view_source_is_temporary(expr.obj)
        return True
    if isinstance(expr, TpyCall):
        # View/pointer constructor borrows its argument (StrView("lit") is static).
        if expr.call_type is not None and (
                is_borrowing_view_type(expr.call_type) or expr.call_type.is_pointer()):
            return not expr.args or view_source_is_temporary(expr.args[0])
        fi = expr.resolved_function_info
        # A view-returning free function that borrows specific args dangles only
        # if a borrowed arg is temporary (pick_view(StrView("lit")) is safe).
        sources = (recorded_return_borrow_sources(fi)
                   if fi is not None else frozenset())
        if sources:
            return any(0 <= i < len(expr.args) and view_source_is_temporary(expr.args[i])
                       for i in sources if i >= 0)
        # Otherwise: a view return is the callee's responsibility (durable),
        # an owned return is a fresh temporary that dangles as a view.
        ret = fi.return_type if fi is not None else None
        if ret is not None and is_borrowing_view_type(unwrap_readonly(ret)):
            return False
        return True
    if isinstance(expr, TpyIfExpr):
        return (view_source_is_temporary(expr.then_expr)
                or view_source_is_temporary(expr.else_expr))
    # f-string / binop / unknown -> temporary (fail closed).
    return True


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
    def leaf(e: TpyExpr) -> list[TpyType]:
        t = ctx.get_expr_type(e)
        if isinstance(t, (PendingViewType, PendingListType, PendingDictType, PendingSetType)):
            return [t]
        return []
    return walk_view_source_leaves(expr, leaf)


def mark_pending_list_mutated(ctx: 'SemanticContext', expr: 'TpyExpr | None',
                              expr_type: 'TpyType | None') -> None:
    """Mark the pending list literal behind `expr` as mutated so it resolves
    to list, not Array. Covers a directly PendingListType-typed expression
    and a name bound to a tracked literal (alias). One home for the
    expression-rooted mutation routes: mutating method receivers, binary
    concat operands, and aug-assign targets."""
    if isinstance(expr_type, PendingListType):
        info = ctx.list_literals.get(expr_type.literal_id)
        if info:
            info.is_mutated = True
        return
    if isinstance(expr, TpyName):
        lit_id = ctx.func.variable_to_literal.get(expr.name)
        if lit_id is not None:
            info = ctx.list_literals.get(lit_id)
            if info:
                info.is_mutated = True


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
        """Resolve target type for a reassignment write.

        A reassigned per-element-`Own` tuple local takes the collapsed borrow
        type (it aliases its elements, it does not own them). Collapse the
        resolved target once here so every reassignment caller -- the compat
        check, the bare-assign scope update, aug-assign -- agrees on the borrow
        shape codegen emits; no-op for any other type.
        """
        resolved = self._resolve_reassignment_target_type_raw(
            name, existing_type, init_type, init_expr)
        return collapse_tuple_own_elements(resolved)

    def _resolve_reassignment_target_type_raw(
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
        # explicit later writes (e.g., BigInt/int64/Float anchors).
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

    def literal_retro_candidate(
        self, name: str, actual: TpyType, expected: TpyType,
    ) -> tuple[TpyType, list[int]] | None:
        """Probe whether `name` is a literal-seeded local that could in
        principle retro-widen to `expected`. Returns (unwrapped target,
        recorded literal values) when the gate passes; None otherwise.
        Callers decide what to do based on whether the recorded values
        fit the target -- promote (try_retro_widen_literal_arg) or raise
        a range error (TypeCompatibility._maybe_raise_literal_local_range).

        Standard fixed-int widening (int32->int64 etc.) already has a
        Coercion entry, so we only step in when the directional COERCIONS
        table has nothing for actual->target. widen_numeric_types is a
        symmetric common-merge predicate (int32+uint8 -> int32 either
        order) and would mis-gate this.
        """
        if name not in self.ctx.func.literal_default_vars:
            return None
        target = unwrap_qualifiers(expected)
        if not is_fixed_int_type(actual) or not is_fixed_int_type(target):
            return None
        if actual == target:
            return None
        if resolve_coercion(actual, target, CoercionContext.ARG) is not None:
            return None
        return target, self.ctx.func.literal_values.get(name, [])

    def try_retro_widen_literal_arg(
        self,
        name: str,
        actual: TpyType,
        expected: TpyType,
        call_loc: 'SourceLocation | None',
    ) -> TpyType | None:
        """Retro-widen a literal-seeded local to a fixed-int target slot.

        Fires when every recorded literal value fits `target`. Mutates the
        var's declared type in place across var_types, declared_var_types,
        scope, and namespace -- subsequent expression analysis in the same
        function sees the new type, and codegen reads it through
        var_types[decl]. Returns the new (unwrapped) target on
        success; None when the gate fails or some recorded value is out
        of range.
        """
        cand = self.literal_retro_candidate(name, actual, expected)
        if cand is None:
            return None
        target, values = cand
        if not values:
            return None
        if not all(fixed_int_range_contains(target, v) for v in values):
            return None
        var_decl = self.ctx.func.var_decl_by_name.get(name)
        if var_decl is None:
            return None
        self.ctx.var_types[var_decl] = target
        for key in list(self.ctx.declared_var_types):
            if key[1] == name:
                self.ctx.declared_var_types[key] = target
        if self.ctx.func.current_scope is not None:
            self.ctx.func.current_scope.set_existing(name, target)
        if self.ctx.func.current_ns is not None:
            self.ctx.func.current_ns.update_variable_type_recursive(name, target)
        self.ctx.func.literal_default_vars.discard(name)
        # Always record the promotion even when no loc is available (synthetic
        # nodes from macros etc.), so the staleness-refresh in _analyze_assign
        # has a consistent signal; the hint formatter guards against None.
        self.ctx.func.retro_widened_locs[name] = call_loc
        return target

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
        self.update_list_element_type(info, value_type)
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
            self.update_list_element_type(source, value_type)
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

    def update_list_element_type(self, info: ListLiteralInfo, value_type: TpyType) -> None:
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
        # Own[list[T]] consumes by value but still requires vector storage --
        # without the unwrap the literal resolves to std::array and the
        # call site fails the C++ build (mirrors mark_container_return_context).
        if isinstance(param_type, OwnType):
            param_type = param_type.wrapped

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

    def mark_container_return_context(self, return_expr: TpyExpr, return_type: TpyType) -> None:
        """Track return-type context for list/dict/set literal inference.

        Mirror of ``mark_container_param_context`` for the return position: an
        empty container returned (`d = {}; return d` with `-> dict[K, V]`)
        resolves its element/key/value from the declared return type, just as
        passing it to a typed parameter does.
        """
        return_type = unwrap_ref_type(return_type)
        if isinstance(return_type, OwnType):
            return_type = return_type.wrapped

        if isinstance(return_expr, TpyCoerce):
            return_expr = return_expr.expr
        if not isinstance(return_expr, TpyName):
            return
        var_name = return_expr.name

        literal_id = self.ctx.func.variable_to_literal.get(var_name)
        if literal_id is not None and literal_id in self.ctx.list_literals and is_list(return_type):
            info = self.ctx.list_literals[literal_id]
            info.passed_to_list_param = True
            info.coerced_element_type = return_type.type_args[0]
            return

        dict_id = self.ctx.func.variable_to_dict_literal.get(var_name)
        if dict_id is not None and is_dict(return_type):
            info = self.ctx.dict_literals.get(dict_id)
            if info is not None:
                widened = self._widen_inferred_type(info.key_type, return_type.type_args[0])
                if widened is not None:
                    info.key_type = widened
                widened = self._widen_inferred_type(info.value_type, return_type.type_args[1])
                if widened is not None:
                    info.value_type = widened
            return

        set_id = self.ctx.func.variable_to_set_literal.get(var_name)
        if set_id is not None and is_set(return_type):
            info = self.ctx.set_literals.get(set_id)
            if info is not None:
                widened = self._widen_inferred_type(info.element_type, return_type.type_args[0])
                if widened is not None:
                    info.element_type = widened

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

    def _sync_resolved_to_ns(self, name: str, resolved: TpyType) -> None:
        """Write a late-resolved local type through to the namespace binding.

        Pending container/view types defer element-type inference until after
        body analysis; the resolution sinks update `current_scope` (and the
        AST / var_types), but `current_ns` is a parallel binding table that
        must stay in sync. Consumers that read the namespace after resolution
        -- notably the resumable-frame local hoist (`generator_locals`) -- would
        otherwise see a stale `Pending*` type and crash in codegen.

        `pending_loop_vars` is a second such table: the generator-local hoist
        reads loop-var types from it directly, so a resolved view-family loop
        var (`for d in list[str]` in a generator) must be synced here too, or
        it reaches the hoist still `Pending*`.
        """
        if self.ctx.func.current_ns is not None:
            self.ctx.func.current_ns.update_variable_type_recursive(name, resolved)
        entry = self.ctx.func.pending_loop_vars.get(name)
        if entry is not None:
            self.ctx.func.pending_loop_vars[name] = (resolved, *entry[1:])

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

        if info.variable_name:
            self._sync_resolved_to_ns(info.variable_name, resolved)

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
                    elif (info.variable_name == "__all__"
                          and self.ctx.func.current_function
                              is MODULE_INIT_CONTEXT):
                        # `__all__ = []` is the Python idiom for "export
                        # nothing". The literal is compile-time metadata
                        # consumed by star-import expansion; the bare
                        # empty form is too common for users to be forced
                        # to write `__all__: list[str] = []`.
                        elem_type = STR
                    else:
                        var_desc = f"list '{info.variable_name}'" if info.variable_name else "empty list literal"
                        raise self.ctx.error(
                            f"Cannot infer element type for {var_desc}; "
                            f"add a type annotation (e.g., {info.variable_name or 'x'}: list[T] = []) "
                            f"or use the list so the type can be inferred",
                            info.expr,
                        )

            # A pending element -- bare (a nested list literal) or nested in a
            # composite (a tuple of list literals) -- resolves to its registry
            # type so the resolved container is fully concrete (every store
            # derived from resolved_type is then pending-free).
            elem_type = self._deep_resolve_pending(elem_type)

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
            # Container elements store the owned value; the Own marker is a
            # boundary annotation, not a storage type (comprehensions reach
            # here with the element expr's `Own[T]` return type intact).
            elem_type = unwrap_own(elem_type)

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
        if info.variable_name:
            self._sync_resolved_to_ns(info.variable_name, resolved)
        if info.variable_name and info.decl_line is not None:
            self.ctx.declared_var_types[(info.decl_line, info.variable_name)] = resolved
        if info.variable_name:
            var_decl = self.ctx.func.var_decl_by_name.get(info.variable_name)
            if var_decl:
                self.ctx.var_types[var_decl] = resolved

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
        - Subscript on a NAME container whose element storage is stable
        - A one-hop read of a record field off a NAME receiver

        The field read's remaining escapes -- a record handed out where a
        write can reach it without the view being demoted -- are
        BUGS.md#field-view-escape-needs-place, which also holds the design
        for re-admitting the deeper chain this arm still copies.

        Note: bytes literals are NOT view-safe (temporary vectors, unlike string
        literals which have static storage).
        """
        # LiteralType over str counts as the family for view-safety purposes:
        # all values are compile-time string literals (static lifetime) and Literal
        # returns are emitted as view storage. (Literal over bytes isn't a thing --
        # LiteralValue tags are str/int/bool only.)
        lit_str = isinstance(init_type, LiteralType) and init_type.is_str_base()
        is_str = is_str_type(init_type) or is_str_view_type(init_type) or isinstance(init_type, PendingStrType) or lit_str
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
            # Check if it's a str/bytes parameter (C++ already passes as view).
            # LiteralType[str] params are also view in C++ (param form delegates to
            # base.to_cpp_param_type() -> std::string_view).
            func = self.ctx.func.current_function
            if isinstance(func, TpyFunction):
                for pname, ptype in func.params:
                    if pname == name:
                        # A narrowed `str | None` param dereferences to the
                        # contained view: `str | None` lowers to a by-value
                        # std::optional<std::string_view>, so *a is a string_view
                        # and a copy into the local is as safe as a plain str
                        # param. The is_str guard above (on the narrowed
                        # init_type) only fires at a non-None use site.
                        # `bytes | None`, by contrast, lowers to
                        # std::optional<std::vector<uint8_t>> (the param OWNS the
                        # buffer), so a span into *a would borrow it -- not
                        # view-safe here; only a plain `bytes` param (std::span)
                        # qualifies.
                        str_base = ptype.inner if isinstance(ptype, OptionalType) else ptype
                        lit_str_param = isinstance(str_base, LiteralType) and str_base.is_str_base()
                        if is_str and (is_str_type(str_base) or is_str_view_type(str_base) or lit_str_param):
                            return True
                        if is_bytes and (is_bytes_type(ptype) or is_bytes_view_type(ptype)):
                            return True

            # Another pending or view local (must match the same family).
            # LiteralType[str] locals get view storage end-to-end (see
            # _resolve_literal_view_storage in codegen).
            scope_type = self.ctx.func.current_scope.lookup(name) if self.ctx.func.current_scope else None
            lit_str_scope = isinstance(scope_type, LiteralType) and scope_type.is_str_base()
            if is_str and (isinstance(scope_type, PendingStrType) or is_str_view_type(scope_type) or lit_str_scope):
                return True
            if is_bytes and (isinstance(scope_type, PendingBytesType) or is_bytes_view_type(scope_type)):
                return True

            # Final[str] global constant
            if is_str and name in self.ctx.final_globals:
                return True

        # Function/method call returning a view type (or a `Literal[str]`,
        # which is emitted as view storage in return position). View-safe only
        # if the view does not borrow a temporary/unsafe source -- otherwise the
        # local must own a copy (is_dangling_return roots the provenance through
        # return_borrows_from, e.g. str.strip borrowing a temporary receiver).
        if isinstance(init_expr, (TpyCall, TpyMethodCall)):
            if is_str_view_type(init_type) or is_bytes_view_type(init_type):
                return not self.compat.is_dangling_return(init_expr)
            if isinstance(init_type, LiteralType) and init_type.is_str_base():
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
            # `str` only. A `bytes` field read did not compile at all before
            # this batch (a THIR reject), so admitting it as a VIEW would
            # newly hand out a span that the escapes in
            # BUGS.md#field-view-escape-needs-place can outlive -- a dangle
            # class master does not have. The owned `::tpy::Bytes` copy
            # admits the same programs with no such window.
            if is_str and isinstance(init_expr.obj, TpyName):
                return True

        # INVARIANT: the compound arms this predicate accepts (and/or, ternary
        # below) must stay the same set `walk_view_source_leaves` recurses into;
        # a compound deemed view-safe here whose leaves that walk does not reach
        # registers no borrow root and yields a stale view (UAF). Any new
        # compound form must be added to both, or to neither.
        #
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

        # Fail closed: a source shape no arm above recognises gets the OWNED
        # copy. Durability alone (`view_source_is_temporary`, which the
        # declaration of a view-typed local seeds from) is too weak a question
        # here -- it calls a name stable without asking what that name's own
        # binding borrows, and answering it at this site put `urllib.parse`
        # locals into views over dead storage. A local whose bindings disagree
        # therefore takes the strictest binding's answer, which is the join a
        # single storage needs; what it costs is that the JOIN is the strictest
        # demand rather than one uniform verdict per source shape.
        return False

    # --- View-type local tracking (generic across str/bytes families) ---

    def _is_static_view_leaf(self, e: TpyExpr) -> bool:
        """A view-source leaf that stays safe across a resumable-frame
        suspension: static storage (str/bytes literal, `Literal[str]`-typed
        name or call -- every value a compile-time literal -- or a
        `Final[str]` constant, or an enum member's `.name` -- see
        `is_enum_name_read`), or an explicit view-typed (StrView/BytesView)
        PARAM -- the caller-side borrow check backs the user's view contract
        for the whole call, so the referent outlives the frame. An explicit
        view-typed LOCAL is NOT safe: its own borrow is unchecked against
        the frame lifetime, so an un-annotated alias of it must not silently
        inherit the view. A pending-view name also counts: its safety is
        inherited through `source_var_ids` (the second resolution pass
        promotes the chain when the source resolves owned). A slice of an
        owning str/bytes name this body binds counts too -- see
        `_frame_resident_slice`.

        Deliberately a SEPARATE, narrower classifier than
        `is_view_compatible_source` (which answers sync view-SAFETY, not
        frame-lifetime staticness) -- but both walk the same leaves via
        `walk_view_source_leaves`; when adding a source shape to either,
        audit the other."""
        if isinstance(e, (TpyStrLiteral, TpyBytesLiteral)):
            return True
        if is_enum_name_read(e, self.ctx):
            return True
        if isinstance(e, TpyName):
            func = self.ctx.func.current_function
            if isinstance(func, TpyFunction):
                for pname, ptype in func.params:
                    if pname == e.name:
                        ptype = unwrap_readonly(ptype)
                        if is_str_view_type(ptype) or is_bytes_view_type(ptype):
                            return True
                        break
            scope_type = (self.ctx.func.current_scope.lookup(e.name)
                          if self.ctx.func.current_scope else None)
            if scope_type is not None:
                scope_type = unwrap_readonly(scope_type)
                if isinstance(scope_type, PendingViewType):
                    return True
                if isinstance(scope_type, LiteralType) and scope_type.is_str_base():
                    return True
            return e.name in self.ctx.final_globals
        if isinstance(e, (TpyCall, TpyMethodCall)):
            t = self.ctx.get_expr_type(e)
            return isinstance(t, LiteralType) and t.is_str_base()
        return self._frame_resident_slice(e)

    def _frame_resident_slice(self, e: TpyExpr) -> bool:
        """A slice of an owning str/bytes NAME bound by this body.

        A resumable body hoists its params and locals into the frame, so such
        a name is a frame FIELD and its buffer lives exactly as long as the
        frame: the suspension the staticness question guards against cannot
        end it, and the view is the same one the sync body gets. Rebinding or
        mutating the name still demotes the view -- through the source-storage
        tracking both bodies share -- so this gives up no demotion, only the
        blanket copy.

        A loop variable is excluded: its binding can be a borrow of another
        producer's frame slot, which the next step overwrites.
        """
        if not (isinstance(e, TpySubscript) and isinstance(e.obj, TpyName)):
            return False
        name = e.obj.name
        if name in self.ctx.func.loop_vars:
            return False
        bound = None
        func = self.ctx.func.current_function
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                if pname == name:
                    bound = ptype
                    break
        if bound is None and self.ctx.func.current_scope is not None:
            bound = self.ctx.func.current_scope.lookup(name)
        if bound is None:
            return False
        bound = unwrap_readonly(unwrap_ref_type(unwrap_own(bound)))
        # A root that is itself an undecided str/bytes local answers through
        # the deduction, not through its "undecided" spelling; one that will
        # be a VIEW is not owning storage and fails closed here.
        bound = self.ctx.view_storage_verdict(bound) or bound
        # The frame field must OWN the buffer the slice points into. A value
        # type (str / bytes / String) is copied into the frame, so it does;
        # a reference type (bytearray) is held as a reference to the CALLER's
        # object, which can reallocate across a suspension.
        if not bound.is_value_type() or is_borrowing_view_type(bound):
            return False
        return any(fam.is_any_member(bound) for fam in VIEW_TYPE_FAMILIES)

    def has_nonstatic_view_source(self, expr: TpyExpr | None) -> bool:
        """True when any view-source leaf of `expr` is not static-lifetime
        (see `_is_static_view_leaf`); None (loop var / tuple unpack -- the
        source is a container element or a per-statement temp) is always
        non-static. Feeds `ViewVarInfo.frame_unsafe_source`."""
        if expr is None:
            return True
        return bool(walk_view_source_leaves(
            expr, lambda e: [] if self._is_static_view_leaf(e) else [e]))

    def mark_view_nonstatic_reassign(self, var_name: str, value_expr: TpyExpr,
                                     family: ViewTypeFamily) -> None:
        """OR `frame_unsafe_source` into an existing pending view local on a
        view-compatible reassign whose source is not static-lifetime."""
        if not self.has_nonstatic_view_source(value_expr):
            return
        var_id = self.ctx.view_var_map(family).get(var_name)
        if var_id is not None and var_id in self.ctx.view_vars(family):
            self.ctx.view_vars(family)[var_id].frame_unsafe_source = True

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

    def _add_view_source(self, family: ViewTypeFamily, var_id: int,
                         source_var_id: int) -> None:
        """Record that view local `var_id` shares storage with `source_var_id`,
        so the alias pass promotes it when the source resolves owned."""
        info = self.ctx.view_vars(family).get(var_id)
        if info is not None and source_var_id not in info.source_var_ids:
            info.source_var_ids.append(source_var_id)

    def register_view_source_storage(self, family: ViewTypeFamily, var_id: int,
                                     storage: str) -> None:
        """Record that view local `var_id` borrows the owned storage `storage`,
        so a reseat or mutation of that storage demotes the view to owned. A
        storage rooted at a name a nested def rebinds via `nonlocal` counts as
        reseated already: the closure can run while the view is live."""
        info = self.ctx.view_vars(family).get(var_id)
        if info is None:
            return
        if storage not in info.source_storages:
            info.source_storages.append(storage)
        self.ctx.view_source_borrows_map(family).setdefault(storage, set()).add(var_id)
        if storage.split(".", 1)[0] in self.ctx.func.nested_nonlocal_rebinds:
            info.source_mutated = True

    def tuple_target_view_family(self, name: str) -> ViewTypeFamily | None:
        """The pending str/bytes view family of tuple-unpack target `name`, or
        None if it is not a pending-view local."""
        bound = self.ctx.func.current_scope.lookup(name)
        inner = (unwrap_own(unwrap_ref_type(unwrap_readonly(bound)))
                 if bound is not None else None)
        return inner.family if isinstance(inner, PendingViewType) else None

    def view_hoist_roots(self, expr: TpyExpr) -> 'set[str] | None':
        """The storage roots a view bound to `expr` reads, resolved as of this
        binding, or None when some arm has no root the hoist rule can place.
        Static leaves (literals, a module or class attribute) contribute
        nothing; a call's result may borrow its receiver or any operand that
        can lend storage, so all of those count."""
        roots: set[str] = set()

        def of(e: TpyExpr) -> bool:
            if isinstance(e, TpyCoerce):
                return of(e.expr)
            if isinstance(e, (TpyStrLiteral, TpyBytesLiteral)) or is_enum_name_read(e, self.ctx):
                return True
            if isinstance(e, TpyName):
                resolved = self._resolve_hoist_root(e.name, set())
                if resolved is None:
                    return False
                roots.update(resolved)
                return True
            if isinstance(e, TpyFieldAccess) and isinstance(e.obj, TpyName) \
                    and self.ctx.get_expr_type(e.obj) is None:
                # A module or class qualifier: static only for a `Final`
                # constant (a constexpr view); a plain module variable can be
                # rebound through `global` in its own module.
                et = self.ctx.get_expr_type(e)
                return et is not None and is_borrowing_view_type(unwrap_readonly(et))
            if isinstance(e, (TpySubscript, TpyFieldAccess)):
                if isinstance(e.obj, (TpyCall, TpyMethodCall)):
                    return False    # an element of a temporary
                return of(e.obj)
            if isinstance(e, (TpyCall, TpyMethodCall)):
                rt = self.ctx.get_expr_type(e)
                if isinstance(rt, LiteralType) and rt.is_str_base():
                    return True
                if rt is None or not is_borrowing_view_type(unwrap_readonly(rt)):
                    return False    # a fresh result: a temporary
                # A view result borrows its receiver or a lending operand;
                # with none of those it reads nothing this rule can place, so
                # it owns (it may be a view of a module-level container).
                operands = list(e.args)
                if (isinstance(e, TpyMethodCall)
                        and self.ctx.get_expr_type(e.obj) is not None):
                    # A module or class qualifier (`util.head(s)`) lends nothing.
                    operands.append(e.obj)
                lending = False
                for a in operands:
                    at = self.ctx.get_expr_type(a)
                    bare = unwrap_readonly(unwrap_ref_type(at)) if at is not None else None
                    if bare is not None and _is_bufferless_scalar(bare):
                        continue
                    lending = True
                    if not of(a):
                        return False
                return lending
            return False

        ok = all(walk_view_source_leaves(expr, lambda leaf: [of(leaf)]))
        return roots if ok else None

    def _resolve_hoist_root(self, name: str, seen: set[str]) -> 'set[str] | None':
        """The storage `name` stands for at this point: a view names itself
        (the hoist rule follows its own entries), a live loop variable its
        iterable's storage as this loop bound it, a borrow its referents, a
        `with` target nothing placeable; anything else is its own storage."""
        if name in seen:
            return set()
        seen = seen | {name}
        func = self.ctx.func
        if name in func.view_ids_by_name:
            return {name}
        if name in func.with_target_names:
            return None
        if name in func.move_through_vars:
            # The binding moved its source in: the object lives in `name`.
            return {name}
        if name in func.loop_vars:
            keys = func.loop_var_iter_roots.get(name)
            if keys is None:
                return None
            out: set[str] = set()
            for k in keys:
                sub_roots = self._resolve_hoist_root(k, seen)
                if sub_roots is None:
                    return None
                out |= sub_roots
            return out
        storages = func.borrow_tracker.all_storage_through_borrows(name)
        if not storages:
            return {name}
        out = set()
        for st in storages:
            sub_roots = self._resolve_hoist_root(st.split(".", 1)[0], seen)
            if sub_roots is None:
                return None
            out |= sub_roots
        return out

    def note_view_binding(self, family: ViewTypeFamily, var_id: int,
                          expr: 'TpyExpr | None', *,
                          roots: 'set[str] | None' = None,
                          replace: bool = False) -> None:
        """Record what one binding of view `var_id` reads, for the hoist rule:
        the roots of `expr`, or `roots` a caller resolved itself; neither is
        a source this site cannot name. `replace` sets the entry's first
        binding for a caller that registered it with no expression."""
        info = self.ctx.view_vars(family).get(var_id)
        if info is None:
            return
        if expr is not None:
            roots = self.view_hoist_roots(expr)
        if replace:
            info.hoist_roots = set()
            info.hoist_unknown = False
        if roots is None:
            info.hoist_unknown = True
        else:
            info.hoist_roots |= roots - {info.variable_name}

    def note_view_rebind(self, name: str, value_expr: TpyExpr,
                         family: ViewTypeFamily) -> None:
        """A view-compatible rebind of pending view `name` is one more binding
        the hoist rule has to see."""
        var_id = self.ctx.view_var_map(family).get(name)
        if var_id is not None:
            self.note_view_binding(family, var_id, value_expr)

    def own_views_borrowing(self, storage: str) -> None:
        """Make every view local that holds a borrow of `storage` (or of a
        path under it) own its buffer."""
        prefix = storage + "."
        for key, holders in self.ctx.func.borrow_tracker.loans.items():
            if key != storage and not key.startswith(prefix):
                continue
            for borrower in holders:
                for info in self._view_entries(borrower):
                    info.source_mutated = True

    def _view_entries(self, name: str) -> 'list[ViewVarInfo]':
        out = []
        for fam, vid in self.ctx.func.view_ids_by_name.get(name, ()):
            info = self.ctx.view_vars(fam).get(vid)
            if info is not None:
                out.append(info)
        return out

    def promote_hoisted_views(self, hoisted: set[str],
                              block_new: 'set[str] | frozenset[str]' = frozenset()
                              ) -> None:
        """Apply the hoist rule to every str/bytes view local pre-declared in
        the enclosing scope of a branch, `with`, `try` or loop-`else` block.
        `block_new` holds the names first bound in the block that were NOT
        hoisted with it: their storage dies with the block. A tuple-unpack
        view target owns whatever its source: conservative, see TODO.md "Keep
        a hoisted str/bytes tuple view target zero-copy"."""
        for name in hoisted:
            if name in self.ctx.func.tuple_unpack_view_targets:
                fam = self.tuple_target_view_family(name)
                if fam is not None:
                    self.mark_view_reassigned_from_owned(name, fam)
                continue
            self.own_hoisted_view(name, block_new)

    def own_hoisted_view(self, name: str,
                         block_new: 'set[str] | frozenset[str]' = frozenset()) -> None:
        """The one hoist rule for a view local: once declared in an enclosing
        scope it stays a view only if every binding reads static storage or
        storage bound in that scope or further out (a param, a global, a
        name visible there and not in `block_new`, a loop variable over such
        storage, or a view that itself qualifies). Anything else owns."""
        entries = self._view_entries(name)
        if not entries:
            return
        if not all(self._view_hoist_safe(i, block_new, {name}) for i in entries):
            for i in entries:
                i.reassigned_from_owned = True

    def _view_hoist_safe(self, info: 'ViewVarInfo',
                         block_new: 'set[str] | frozenset[str]',
                         seen: set[str]) -> bool:
        if info.hoist_unknown:
            return False
        return all(self._root_durable(r, block_new, seen) for r in info.hoist_roots)

    def _root_durable(self, root: str, block_new: 'set[str] | frozenset[str]',
                      seen: set[str]) -> bool:
        if root in seen:
            return True
        seen = seen | {root}
        if root not in block_new and self._bound_in_view_scope(root):
            return True
        if root in self.ctx.func.global_declarations:
            return True
        entries = self._view_entries(root)
        return bool(entries) and all(
            self._view_hoist_safe(i, block_new, seen) for i in entries)

    def _bound_in_view_scope(self, name: str) -> bool:
        """Whether `name` is visible from the current scope as THIS binding.
        A module-scope hit for a name the function binds itself is a
        different variable (the local shadows it), so it does not count."""
        scope = self.ctx.func.current_scope
        while scope is not None:
            if name in scope.bindings:
                return not (scope is self.ctx.global_scope
                            and name in self.ctx.func.var_scope_depth)
            scope = scope.parent
        return False

    def track_view_reassign_source(self, var_name: str, source_type: TpyType, family: ViewTypeFamily) -> None:
        """Track source relationship when reassigning from another pending view-type."""
        if not isinstance(source_type, family.pending_type_class):
            return
        var_id = self.ctx.view_var_map(family).get(var_name)
        if var_id is not None:
            # Append, don't replace: a view local that aliased one source and is
            # later rebound to another must keep BOTH -- if either source resolves
            # to owned (or is mutated), the alias must promote too. Replacing here
            # would drop the earlier source and leave a dangling view.
            self._add_view_source(family, var_id, source_type.var_id)

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

        # First pass: resolve based on direct usage flags. `view_needs_owned`
        # is the shared predicate a mid-body consumer reads through
        # `view_storage_verdict`, so the settled answer and the verdict a use
        # site saw can only differ by facts recorded after that use.
        for var_id in pending:
            info = vars_reg.get(var_id)
            if info is None:
                continue

            info.resolved_type = (family.owned_type
                                  if self.ctx.view_needs_owned(info)
                                  else family.view_type)

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

            if info.variable_name:
                self._sync_resolved_to_ns(info.variable_name, resolved)

            var_decl = self.ctx.func.var_decl_by_name.get(info.variable_name)
            if var_decl:
                self.ctx.var_types[var_decl] = resolved
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
        self._finalize_pending_elem_type_fields()
        self._finalize_pending_in_bindings()
        self._check_unresolved_pending_generics()

    def _finalize_pending_elem_type_fields(self) -> None:
        """Refresh comprehension element/key/value-type snapshots post-resolution.

        A snapshot field (result_elem_type etc.) cached before resolution may
        hold a Pending* container type (bare or nested in a composite) whose
        resolved type now lives on its registry info; rewrite the field so
        codegen sees the concrete type."""
        for node, attr in self.ctx.func.pending_elem_type_fields:
            current = getattr(node, attr)
            resolved = self._deep_resolve_pending(current)
            if resolved is not current:
                setattr(node, attr, resolved)

    def _finalize_pending_in_bindings(self) -> None:
        """Resolve Pending* leaves left in codegen-visible bindings in place.

        A Pending* embedded in a composite bound to a name (a non-array comp's
        `list[Pending]`, a `tuple[Pending,...]`) or bare on a loop var survives
        the deferred resolver and crashes codegen's `to_cpp()`. Only per-function
        stores are touched (namespace locals + loop-var snapshots feeding the
        frame hoist) plus the per-function-recorded composite expr nodes, and --
        for module code only -- the module's global scope; never a sweep over
        the module-wide `expr_types`."""
        ns = self.ctx.func.current_ns
        if ns is not None:
            for binding in ns.all_bindings().values():
                if binding.kind is BindingKind.VARIABLE and binding.type is not None:
                    binding.type = self._deep_resolve_pending(binding.type)

        if self.ctx.is_top_level:
            # An inferred top-level binding is recorded in `global_scope`
            # while its container type is still pending, and the module's
            # export table renders straight off that type.
            gbindings = self.ctx.global_scope.bindings
            for name, gtype in gbindings.items():
                if gtype is not None:
                    gbindings[name] = self._deep_resolve_pending(gtype)

        for node in self.ctx.func.pending_composite_exprs:
            current = self.ctx.expr_types.get(node)
            if current is not None:
                current = self._deep_resolve_pending(current)
                self.ctx.expr_types[node] = current
                # Completeness net for the recorded-composite half of the
                # set_expr_type chokepoint: an explicit raise (like
                # _assert_no_pending_locals, so it is not stripped under -O)
                # turns a missed element resolution into a clear internal error
                # here rather than the opaque `to_cpp()` crash in codegen.
                if contains_pending_leaf(current):
                    raise AssertionError(
                        f"Internal error: composite expr type {current} still "
                        f"has a Pending* leaf after finalization; a tracked "
                        f"literal element was not resolved before resolve_all"
                    )

        loop_vars = self.ctx.func.pending_loop_vars
        for name, (vtype, *rest) in list(loop_vars.items()):
            if vtype is not None:
                resolved = self._deep_resolve_pending(vtype)
                if resolved is not vtype:
                    loop_vars[name] = (resolved, *rest)

        # Branch-decl snapshots capture binding types before the deferred
        # container resolution; codegen renders them directly (predecl /
        # frame-slot init), so finalize each map recorded by this function.
        for decls in self.ctx.func.pending_branch_decl_maps:
            for name, vtype in decls.items():
                if vtype is not None:
                    decls[name] = self._deep_resolve_pending(vtype)

    def _deep_resolve_pending(self, typ: TpyType) -> TpyType:
        """Replace every Pending* container leaf in a (possibly composite) type
        with its registry-resolved type, recursing through wrapper/composite
        types via `map_inner_types`. A genuinely-unresolved Pending (no resolved
        type yet) is left as-is for the downstream unresolved-type diagnostic."""
        resolved = self._resolved_container_type(typ)
        if resolved is not None:
            return resolved
        return typ.map_inner_types(self._deep_resolve_pending)

    def _resolved_container_type(self, typ: TpyType) -> TpyType | None:
        """The registry-resolved type for a Pending* container, else None."""
        if isinstance(typ, PendingListType):
            info = self.ctx.list_literals.get(typ.literal_id)
        elif isinstance(typ, PendingDictType):
            info = self.ctx.dict_literals.get(typ.literal_id)
        elif isinstance(typ, PendingSetType):
            info = self.ctx.set_literals.get(typ.literal_id)
        else:
            return None
        return info.resolved_type if info is not None else None
