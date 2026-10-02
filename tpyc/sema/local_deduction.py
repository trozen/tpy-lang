"""Unified local type deduction for TurboPython semantic analysis.

Unified local type deduction: reassignment inference, view-type tracking
(str/bytes), and list/dict/set literal tracking, with post-body resolution
for function-local variables.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Callable

from ..coercions import CoercionContext
from ..parse import TpyExpr, TpyStmt, TpyName, TpyCall, TpyMethodCall, TpyCoerce, TpyFunction, TpyListRepeat, TpyAugAssign
from ..parse.nodes import (TpyStrLiteral, TpyBytesLiteral, TpySubscript, TpyFieldAccess,
                           TpyBinOp, TpyIfExpr, TpyNamedExpr, TpyArrayLiteral,
                           TpyVarDecl, TpyNoneLiteral, TpyTupleLiteral, TpySlice)
from ..typesys import (
    recorded_return_borrow_sources,

    collapse_tuple_own_elements,
    DictLiteralInfo,
    make_dict,
    FloatLiteralType,
    is_float_type,
    IntLiteralType,
    ListLiteralInfo,
    make_list,
    ListRepeatType,
    LiteralType,
    make_array,
    NoneType,
    is_bufferless_scalar,
    OptionalType,
    OwnType,
    PendingDictType,
    PendingNumType,
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
    holds_borrowing_view,
    lands_in_view_member,
    unwrap_ref_type,
    unwrap_send_sync,
)
from .context import (PENDING_CONTAINER_TYPES, MODULE_INIT_CONTEXT,
                      _borrow_storage_root, canonical_storage_key,
                      call_borrow_operands, call_lend_sources,
                      contains_pending_leaf)
from ..namespace import BindingKind
from .receiver_calls import method_writes_receiver
from ..diagnostics import SemanticError
from .type_join import (InferredJoin, JoinOutcome, descend,
                        find_int_float_mix, flipped, join_inferred_value_types,
                        numeric_kind, peel_value,
                        annotate_first_binding, operand_spelling,
                        python_type_name, rebind_mix_message, usage_mix_message,
                        wider_store_message)
from .numeric_lattice import numeric_info, same_width_family, widen_numeric_types
from .pending_num import contains_pending_num
from .alias_rebind import BindKind
from ..type_def_registry import (
    is_set, is_dict, is_array, is_span, is_list, is_fixed_int_type, is_big_int_type,
    is_str_type, is_str_view_type, is_bytes_type, is_bytes_view_type,
    is_string_type, is_bytearray_type,
    is_enum_type,
    is_borrowing_view_type,
    int_traits_of,
)

if TYPE_CHECKING:
    from .compatibility import TypeCompatibility
    from .context import SemanticContext
    from .pending_num import PendingNums


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
            ops = call_borrow_operands(expr)
            return ops is not None and any(
                src.temp_backed or view_source_is_temporary(src.expr)
                for src in call_lend_sources(ops, sources, expr_type=None,
                                              temp_backing=True)
                if src.idx >= 0)
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


def view_slot_source_is_temporary(
        slot: 'TpyType', expr: TpyExpr,
        type_of: 'Callable[[TpyExpr], TpyType | None]') -> bool:
    """`view_source_is_temporary` asked of a slot that HOLDS a view
    (`holds_borrowing_view`), for the part of the slot the value lands in:
    a `None` holds no view, a select answers per arm, a union member is
    chosen by the value's type (`lands_in_view_member`), and a tuple literal
    answers per element against the element slot."""
    if not holds_borrowing_view(slot):
        return False
    inner = expr.expr if isinstance(expr, TpyCoerce) else expr
    if isinstance(inner, TpyNoneLiteral):
        return False
    if isinstance(inner, TpyIfExpr):
        return (view_slot_source_is_temporary(slot, inner.then_expr, type_of)
                or view_slot_source_is_temporary(slot, inner.else_expr,
                                                 type_of))
    t = unwrap_readonly(slot)
    if isinstance(t, OptionalType):
        t = unwrap_readonly(t.inner)
    if not lands_in_view_member(t, type_of(inner)):
        return False
    if (isinstance(t, TupleType) and isinstance(inner, TpyTupleLiteral)
            and len(inner.elements) == len(t.element_types)):
        return any(view_slot_source_is_temporary(et, e, type_of)
                   for et, e in zip(t.element_types, inner.elements))
    return view_source_is_temporary(expr)


def temporary_view_bind_message(view_type: 'TpyType', subject: str,
                                 name: str) -> str:
    """The one diagnostic for a view slot -- a local or a field -- bound to a
    source `view_source_is_temporary` calls temporary."""
    owned = "list" if is_span(unwrap_readonly(view_type)) else "str/bytes"
    return (f"Cannot bind {view_type} {subject} to a temporary view source; "
            f"the backing storage is destroyed at end-of-statement -- "
            f"annotate '{name}' as an owned {owned} to keep a copy")


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
        # Set after construction (it is built from compat)
        self.pend: 'PendingNums'

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
        aug_op: str | None = None,
        site: TpyStmt | None = None,
    ) -> TpyType:
        """Resolve target type for a reassignment write.

        A reassigned per-element-`Own` tuple local takes the collapsed borrow
        type (it aliases its elements, it does not own them). Collapse the
        resolved target once here so every reassignment caller -- the compat
        check, the bare-assign scope update, aug-assign -- agrees on the borrow
        shape codegen emits; no-op for any other type.
        """
        resolved = self._resolve_reassignment_target_type_raw(
            name, existing_type, init_type, init_expr, aug_op, site)
        return collapse_tuple_own_elements(resolved)

    def _resolve_reassignment_target_type_raw(
        self,
        name: str,
        existing_type: TpyType,
        init_type: TpyType,
        init_expr: TpyExpr | None = None,
        aug_op: str | None = None,
        site: TpyStmt | None = None,
    ) -> TpyType:
        """Resolve target type for an unannotated reassignment write."""
        declared = self.declared_slot_type(name, existing_type)
        if declared is not None:
            if self.is_annotated_slot(name):
                self.refuse_wider_rebind(name, declared, init_type,
                                         init_expr, aug_op, site)
            return declared

        # A float literal converts into a float local as an int literal
        # converts into an int one; anywhere else it is a `float`.
        if (isinstance(init_type, FloatLiteralType)
                and not is_float_type(existing_type)):
            init_type = FLOAT

        self.refuse_int_float_rebind(name, existing_type, init_type,
                                     init_expr, aug_op, site)

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

        # One type per local: a function local keeps the type of its first
        # binding and a narrower value converts into it. (A local whose
        # first binding is a bare literal is a pending local and never gets
        # here.)
        # Module-level variables still join their bindings.
        module_level = (self.ctx.func.current_function is None
                        or self.ctx.is_top_level)
        if module_level and self._literal_outgrows(existing_type, init_type):
            return self.ctx.default_int_for_literal(
                init_type, warn_node=init_expr, rebinding=True)
        widened = widen_numeric_types(existing_type, init_type)
        if widened is not None and widened != existing_type:
            if module_level:
                return widened
            self.refuse_wider_rebind(name, existing_type, init_type,
                                     init_expr, aug_op, site)

        return existing_type

    def _literal_outgrows(self, existing_type: TpyType,
                          init_type: TpyType) -> bool:
        """An int literal the default-int variable it is bound to cannot
        hold."""
        if (not isinstance(init_type, IntLiteralType) or init_type.value is None
                or existing_type != self.ctx.default_int_type):
            return False
        tr = int_traits_of(existing_type)
        return tr is not None and not tr.min_value <= init_type.value <= tr.max_value

    def refuse_wider_rebind(
        self, name: str, existing_type: TpyType, value_type: TpyType,
        value_expr: TpyExpr | None, aug_op: str | None,
        site: TpyStmt | TpyExpr | None,
    ) -> None:
        """Refuse a binding whose value is wider than the numeric type the
        local took from its first binding; a narrower or equal one passes.
        Widening the local would retype every read already analyzed."""
        widened = widen_numeric_types(existing_type, value_type)
        if widened is None or widened == existing_type:
            return
        func = self.ctx.func
        if aug_op is not None and value_expr is None and isinstance(site, TpyAugAssign):
            value_expr = site.value
        if self.is_annotated_slot(name):
            # A declared slot takes what converts into it: an `int` by the
            # range-checked conversion, never a wider value of its own family.
            if self.compat.is_type_compatible(value_type, existing_type):
                return
            line = self.annotation_line(name)
            at = f" (line {line})" if line is not None else ""
            wide = python_type_name(widened)
            declare = f"annotate it {wide} there: {name}: {wide} = ..."
            spelled = (operand_spelling(value_expr)
                       if value_expr is not None and aug_op is None
                       and same_width_family(existing_type, value_type)
                       else None)
            fix = (f"write {python_type_name(existing_type)}({spelled}) to "
                   f"narrow it, or {declare}" if spelled is not None
                   else declare)
            raise self.ctx.error(
                wider_store_message(name, existing_type, widened, fix, at=at,
                                    declared=True, aug_op=aug_op,
                                    aug_value=value_expr),
                site if site is not None else value_expr)
        decl = func.var_decl_by_name.get(name)
        line = getattr(getattr(decl, "loc", None), "line", None)
        at = f" (line {line})" if line is not None else ""
        there = ""
        if name in func.current_nonlocal_names:
            at, there = " in the enclosing function", " there"
        if name in func.loop_vars:
            what = "result" if aug_op is not None else "value"
            fix = f"bind the {python_type_name(widened)} {what} to a new name"
        else:
            fix = annotate_first_binding(name, widened, there=there)
        raise self.ctx.error(
            wider_store_message(name, existing_type, widened, fix, at=at,
                                aug_op=aug_op, aug_value=value_expr),
            site if site is not None else value_expr)

    def is_annotated_slot(self, name: str) -> bool:
        """Whether an annotation declares the slot a store into `name`
        reaches -- this function's, or the enclosing function's or the
        module's through `nonlocal` / `global` -- rather than a parameter
        or the slot's bindings."""
        func = self.ctx.func
        if name in func.authoritative_types:
            return True
        if name in func.current_nonlocal_names:
            return name in func.enclosing_annotation_lines
        if name in func.global_declarations:
            return name in self.ctx.preregistered_globals
        return False

    def annotation_line(self, name: str) -> int | None:
        """The line of the annotation `is_annotated_slot` found."""
        func = self.ctx.func
        if name in func.authoritative_types:
            return func.authoritative_type_lines.get(name)
        if name in func.current_nonlocal_names:
            return func.enclosing_annotation_lines.get(name)
        return self.ctx.top_level_decls.get(name)

    def declared_slot_type(self, name: str,
                           existing_type: TpyType) -> TpyType | None:
        """The type a declaration gives `name`'s one slot -- its annotation,
        its parameter's, or the declaration a `nonlocal` / `global` write
        reaches in the scope that owns the name -- or None when the slot's
        type is inferred from its bindings. A declared slot converts what is
        bound to it; widening it instead would store a wider value into the
        narrower C++ declaration."""
        func = self.ctx.func
        if name in func.authoritative_types:
            return func.authoritative_types[name]
        if name in func.current_nonlocal_names:
            return (existing_type if name in func.enclosing_declared_names
                    else None)
        if name in func.global_declarations:
            return (existing_type if name in self.ctx.preregistered_globals
                    else None)
        if name in func.current_param_names:
            return existing_type
        return None

    def refuse_int_float_rebind(
        self, name: str, existing_type: TpyType, init_type: TpyType,
        init_expr: TpyExpr | None, aug_op: str | None = None,
        site: TpyStmt | None = None,
    ) -> None:
        """Refuse a binding that makes an inferred local's integer bindings
        meet float ones. The local has one C++ type, so widening it to float
        would print an int binding as `3.0` (and an int storage would
        truncate a float), where CPython keeps each value's own type."""
        if self.declared_slot_type(name, existing_type) is not None:
            return
        mix = self.int_float_mix(existing_type, init_type)
        if mix is None:
            return
        func = self.ctx.func
        earlier = []
        for t, e in func.write_history.get(name, []):
            m = self.int_float_mix(t, init_type)
            if e is not None and m is not None and m.int_first == mix.int_first:
                earlier.append(e)
        line = next((e.loc.line for e in earlier if e.loc is not None), None)
        # A loop variable, a `nonlocal` or a `global` name is not bound by an
        # annotatable statement here.
        bound_elsewhere = (name in func.current_nonlocal_names
                           or name in func.global_declarations)
        annotatable = not (name in func.loop_vars or bound_elsewhere)
        raise self.ctx.error(
            rebind_mix_message(mix, name, init_expr, earlier, line, aug_op,
                               annotatable, bound_elsewhere),
            init_expr if init_expr is not None else site)

    def int_float_mix(self, current: TpyType,
                      new: TpyType) -> InferredJoin | None:
        """The int/float mix between a local's type so far and a new binding,
        asked in that order. A pending container on one side is joined with
        the concrete container on the other element by element, also below
        a tuple element or a container's type argument."""
        return find_int_float_mix(current, new, leaf=self._pending_mix_leaf)

    def _pending_mix_leaf(self, current: TpyType,
                          new: TpyType) -> InferredJoin | None:
        """`int_float_mix` at a pending container beside the concrete one it
        would pin to, which the type walk does not descend itself."""
        pending = PENDING_CONTAINER_TYPES
        if isinstance(new, pending) and not isinstance(current, pending):
            verdict = self.pin_pending_container(new, current, commit=False)
            return (flipped(verdict)
                    if verdict.outcome is JoinOutcome.INT_FLOAT_MIX else None)
        if isinstance(current, pending) and not isinstance(new, pending):
            verdict = self.pin_pending_container(current, new, commit=False)
            return (verdict if verdict.outcome is JoinOutcome.INT_FLOAT_MIX
                    else None)
        return None

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

    # ------------------------------------------------------------------
    # List literal deduction (moved from ListLiteralTracker)
    # ------------------------------------------------------------------

    def infer_empty_list_element_type(self, obj_expr: TpyExpr, value_type: TpyType,
                                      value: TpyExpr | None = None) -> None:
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
        self.update_list_element_type(info, value_type, obj_expr, value)
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
            self.update_list_element_type(source, value_type, obj_expr, value)
            current = source

    @staticmethod
    def _widen_inferred_type(current: TpyType, new_type: TpyType) -> Optional[TpyType]:
        """Widen an inferred container element type with a new observation.

        Returns the joined type -- `current` itself when the observation adds
        nothing -- or None when the two are incompatible, which normal type
        checking reports at the use.
        """
        original = current
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
            return original
        if isinstance(current, IntLiteralType) and not isinstance(new_type, IntLiteralType):
            if numeric_info(new_type) is not None:
                return new_type
        if isinstance(new_type, IntLiteralType) and not isinstance(current, IntLiteralType):
            # The literal takes the existing concrete numeric type.
            if numeric_info(current) is not None:
                return original
        return None

    @staticmethod
    def _initializer_spelling(expr: TpyExpr | None, empty: str,
                              nonempty: str) -> str:
        """How an annotation hint spells a container's initializer: `empty`
        for an empty literal or a no-argument constructor call."""
        if isinstance(expr, TpyCall):
            return empty if not expr.args else nonempty
        items = getattr(expr, "keys", getattr(expr, "elements", None))
        return empty if items == [] else nonempty

    def update_list_element_type(self, info: ListLiteralInfo, value_type: TpyType,
                                 site: TpyExpr | None = None,
                                 value: TpyExpr | None = None) -> None:
        """Update element type for a ListLiteralInfo from a use, widening if
        needed; `site` is the use the diagnostic points at, `value` the node
        it adds."""
        name = info.variable_name or "xs"
        init = self._initializer_spelling(info.expr, "[]", "[...]")
        result = self._join_observed_type(
            info.element_type, value_type, site or info.expr,
            f"list '{name}'" if info.variable_name else "this list",
            lambda f: f"{name}: list[{f}] = {init}", value)
        if result is not None:
            info.element_type = result

    def _join_observed_type(self, current: TpyType, new_type: TpyType,
                            site: TpyExpr, container: str,
                            annotation: Callable[[str], str],
                            value: TpyExpr | None = None) -> Optional[TpyType]:
        """Join a use's type into the type an unannotated container learned
        from its earlier uses -- an inferred join, so an int meeting a float
        is refused. Returns the joined type, or None when the use is
        incompatible (normal type checking reports it at the use)."""
        verdict = join_inferred_value_types(current, new_type,
                                            self._widen_inferred_type)
        if verdict.outcome is JoinOutcome.INT_FLOAT_MIX:
            raise self.ctx.error(usage_mix_message(
                verdict, container,
                annotation(python_type_name(verdict.float_side)), value), site)
        return verdict.joined

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

    def pin_pending_container(self, pending: TpyType, target: TpyType,
                              commit: bool = True) -> InferredJoin:
        """Resolve a pending container toward the concrete container `target`
        it must share one C++ type with (the other operand of a select).

        Not JOINED when an element type the container already committed to
        disagrees with `target`'s -- the two cannot share a type then. A
        literal element is an inferred join with `target`'s element type: an
        int meeting a float is refused (`[5]` beside a `list[float]` is a
        list of ints under CPython), and a value must fit (`[300]` has no
        `list[uint8]` spelling). `commit=False` asks for the verdict alone,
        leaving the container unpinned."""
        joined = InferredJoin(JoinOutcome.JOINED, target)
        incompatible = InferredJoin(JoinOutcome.INCOMPATIBLE)

        def literal_join(lit: TpyType, want: TpyType) -> TpyType | None:
            # A `T | None` element is no numeric one to pin a literal to.
            bare, nullable = peel_value(want)
            numeric = numeric_kind(bare) is not None and not nullable
            return (want if numeric and self.compat.is_type_compatible(lit, want)
                    else None)

        def pin(current: TpyType, want: TpyType,
                leaves: list[TpyType] | None = None) -> InferredJoin:
            if isinstance(current, UnknownElementType):
                return InferredJoin(JoinOutcome.JOINED, want)
            if not isinstance(current, (IntLiteralType, FloatLiteralType)):
                if current == want:
                    return InferredJoin(JoinOutcome.JOINED, current)
                # A nested container or tuple element keeps its ints too.
                return self.int_float_mix(current, want) or incompatible
            # The recorded element type keeps one literal's value only, so
            # each element's own literal is joined.
            for leaf in leaves or [current]:
                verdict = join_inferred_value_types(leaf, want, literal_join)
                if verdict.outcome is JoinOutcome.INT_FLOAT_MIX:
                    return verdict
                if verdict.outcome is not JoinOutcome.JOINED:
                    return incompatible
            return InferredJoin(JoinOutcome.JOINED, want)

        def below(verdict: InferredJoin, index: int) -> InferredJoin:
            # A mix under the container's `index`-th type argument.
            return (descend(verdict, index)
                    if verdict.outcome is JoinOutcome.INT_FLOAT_MIX
                    else verdict)

        if isinstance(pending, PendingListType):
            info = self.ctx.list_literals.get(pending.literal_id)
            to_array = is_array(target)
            if info is None or not (is_list(target) or to_array):
                return joined
            elem = target.type_args[0]
            if info.coerced_element_type is not None:
                if to_array:
                    return joined
                return joined if info.coerced_element_type == elem else incompatible
            leaves = None
            if isinstance(info.expr, (TpyArrayLiteral, TpyListRepeat)):
                leaves = [unwrap_own(self.ctx.get_expr_type(x) or info.element_type)
                          for x in info.expr.elements]
            verdict = pin(info.element_type, elem, leaves)
            if to_array:
                # An Array target pins the elements itself while it matches
                # the literal's size (`pending_list_matches_array`); an int
                # meeting its float is still a mix.
                return (below(verdict, 0)
                        if verdict.outcome is JoinOutcome.INT_FLOAT_MIX
                        else joined)
            if verdict.outcome is JoinOutcome.JOINED:
                if commit:
                    info.coerced_element_type = elem
                return joined
            return below(verdict, 0)
        if isinstance(pending, PendingDictType) and is_dict(target):
            info = self.ctx.dict_literals.get(pending.literal_id)
            if info is None:
                return joined
            key = pin(info.key_type, target.type_args[0])
            value = pin(info.value_type, target.type_args[1])
            for index, verdict in enumerate((key, value)):
                if verdict.outcome is not JoinOutcome.JOINED:
                    return below(verdict, index)
            if commit:
                info.key_type, info.value_type = key.joined, value.joined
            return joined
        if isinstance(pending, PendingSetType) and is_set(target):
            info = self.ctx.set_literals.get(pending.literal_id)
            if info is None:
                return joined
            verdict = pin(info.element_type, target.type_args[0])
            if verdict.outcome is not JoinOutcome.JOINED:
                return below(verdict, 0)
            if commit:
                info.element_type = verdict.joined
            return joined
        return incompatible

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
            self.ctx.func.pending_loop_vars[name] = dc_replace(
                entry, var_type=resolved)

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

    def infer_dict_key_value_types(self, obj_expr: TpyExpr, key_type: TpyType,
                                   value_type: TpyType,
                                   key: TpyExpr | None = None,
                                   value: TpyExpr | None = None) -> None:
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
        self._update_dict_type_param(info, "key", key_type, obj_expr, key)
        self._update_dict_type_param(info, "value", value_type, obj_expr,
                                     value)

    def _update_dict_type_param(self, info: DictLiteralInfo, which: str,
                                new_type: TpyType, site: TpyExpr,
                                value: TpyExpr | None = None) -> None:
        """Update key or value type for a DictLiteralInfo from a use,
        widening if needed."""
        current = info.key_type if which == "key" else info.value_type
        name = info.variable_name or "d"
        init = self._initializer_spelling(info.expr, "{}", "{...}")

        def annotation(f: str) -> str:
            other = info.value_type if which == "key" else info.key_type
            o = ("..." if isinstance(other, UnknownElementType)
                 else python_type_name(other))
            k, v = (f, o) if which == "key" else (o, f)
            return f"{name}: dict[{k}, {v}] = {init}"
        result = self._join_observed_type(
            current, new_type, site,
            (f"dict '{name}'" if info.variable_name else "this dict")
            + (" keys" if which == "key" else ""),
            annotation, value)
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

    def infer_set_element_type(self, obj_expr: TpyExpr, value_type: TpyType,
                               value: TpyExpr | None = None) -> None:
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
        name = info.variable_name or "s"
        init = self._initializer_spelling(info.expr, "set()", "{...}")
        result = self._join_observed_type(
            info.element_type, value_type, obj_expr,
            f"set '{name}'" if info.variable_name else "this set",
            lambda f: f"{name}: set[{f}] = {init}", value)
        if result is not None:
            info.element_type = result

    # ------------------------------------------------------------------
    # String variable deduction (moved from StrVarTracker)
    # ------------------------------------------------------------------

    def is_view_compatible_source(self, init_expr: TpyExpr, init_type: TpyType) -> bool:
        """Whether an inferred str/bytes local bound to `init_expr` may be a
        VIEW (no owned copy). The one rule, the same for both families: every
        source leaf is
        - static storage: a str/bytes literal, a `Final` constant, a
          `Literal[str]` value, an enum member's `.name`, a slice of a `str`
          literal (a `bytes` literal's slice renders over a temporary span);
        - a parameter of the view family (a narrowed `str | None` too; a
          narrowed `bytes | None` OWNS its buffer and is not a source);
        - another str/bytes local (the alias pass owns it when that local
          owns, or when it does not lend -- `ViewVarInfo.lends_to_aliases`);
        - a slice, an element or a view-returning method over a str, bytes,
          `String`, `bytearray` or tuple NAME this function binds or takes
          (`name_lends_buffer`);
        - a view-returning call, free function or method, whose every
          operand it may point into (`_call_lent_operands`) is itself a
          lending leaf.
        A record field, a container element and a loop variable's buffer
        bind OWNED storage: nothing tracks a write through those while the
        view lives.
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

        if isinstance(init_expr, TpyName):
            return self._name_lends_view(init_expr.name, is_str)

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

        return self.leaf_lends_view(init_expr)

    def _name_lends_view(self, name: str, is_str: bool) -> bool:
        """A bare NAME as an inferred view's source (`v = s`)."""
        if self.binding_withholds_view(name):
            return False
        func = self.ctx.func.current_function
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                if pname == name:
                    # A narrowed `str | None` param dereferences to the
                    # contained view: `str | None` lowers to a by-value
                    # std::optional<std::string_view>, so *a is a string_view.
                    # `bytes | None`, by contrast, lowers to
                    # std::optional<std::vector<uint8_t>> (the param OWNS the
                    # buffer), so a span into *a would borrow it -- only a
                    # plain `bytes` param (std::span) qualifies.
                    str_base = ptype.inner if isinstance(ptype, OptionalType) else ptype
                    lit_str_param = isinstance(str_base, LiteralType) and str_base.is_str_base()
                    if is_str and (is_str_type(str_base) or is_str_view_type(str_base) or lit_str_param):
                        return True
                    if not is_str and (is_bytes_type(ptype) or is_bytes_view_type(ptype)):
                        return True

        # Another pending or view local (must match the same family).
        # LiteralType[str] locals get view storage end-to-end (see
        # _resolve_literal_view_storage in codegen).
        scope_type = self.ctx.func.current_scope.lookup(name) if self.ctx.func.current_scope else None
        lit_str_scope = isinstance(scope_type, LiteralType) and scope_type.is_str_base()
        if is_str and (isinstance(scope_type, PendingStrType) or is_str_view_type(scope_type) or lit_str_scope):
            return True
        if not is_str and (isinstance(scope_type, PendingBytesType) or is_bytes_view_type(scope_type)):
            return True
        return is_str and name in self.ctx.final_globals

    def binding_withholds_view(self, name: str) -> bool:
        """`name` is a loop variable, or a binding marked as not lending
        (`ViewVarInfo.lends_to_aliases`): its storage is reused by the next
        step of its producer, so a local bound off it must own."""
        if name in self.ctx.func.loop_vars:
            return True
        for fam in VIEW_TYPE_FAMILIES:
            var_id = self.ctx.view_var_map(fam).get(name)
            info = self.ctx.view_vars(fam).get(var_id) if var_id is not None else None
            if info is not None and not info.lends_to_aliases:
                return True
        return False

    def name_lends_buffer(self, name: str) -> bool:
        """A NAME a slice, an element read or a view-returning method may
        lend a view of: a parameter or local of this function holding an
        str/bytes value (or view), a `String` / `bytearray`, or a tuple.
        Every other reference type is excluded."""
        if self.binding_withholds_view(name):
            return False
        bound = None
        from_param = False
        func = self.ctx.func.current_function
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                if pname == name:
                    bound = ptype
                    from_param = True
                    break
        if bound is None:
            if name not in self.ctx.func.var_scope_depth:
                return name in self.ctx.final_globals
            bound = (self.ctx.func.current_scope.lookup(name)
                     if self.ctx.func.current_scope else None)
        if bound is None:
            return False
        bare = unwrap_readonly(unwrap_ref_type(unwrap_own(bound)))
        if isinstance(bare, OptionalType) and from_param:
            # A narrowed `str | None` PARAM dereferences to the contained
            # view, as the bare-name arm says; a `bytes | None` param owns
            # its buffer and stays out, as there.
            inner = unwrap_readonly(bare.inner)
            return is_str_type(inner) or is_str_view_type(inner)
        if isinstance(bare, TupleType):
            return from_param or self.name_holds_storage(name)
        if isinstance(bare, (PendingViewType, LiteralType)):
            return not isinstance(bare, LiteralType) or bare.is_str_base()
        # The mutable siblings lend too: a subscript store, a mutating
        # method or `+=` on the NAME is a write the body sees, and it
        # demotes the view exactly as a rebind of a str name does.
        if is_string_type(bare) or is_bytearray_type(bare):
            return from_param or self.name_holds_storage(name)
        return (is_str_type(bare) or is_str_view_type(bare)
                or is_bytes_type(bare) or is_bytes_view_type(bare))

    def name_holds_storage(self, name: str) -> bool:
        """Whether local `name` holds its own object -- a tuple, a `String`
        or a `bytearray` -- so a view over it is storage this body binds
        and every write the body can make to it is visible on the name.
        A reference-typed local (`bytearray`) holds its object only when its
        binding's kind is `BindKind.RVALUE` -- a fresh object the name owns
        (a constructor, an owned-returning call); a borrow of a field, an
        element, a global or a reference-returning call (`t = h.ba`,
        `t = gg()`) is LVALUE, and a name bound any other way (an unpack or
        loop target, a hoisted declaration) has no recorded kind: a write
        through the other spelling never reaches the name, so a view over
        it is that storage's read, which owns. A value-typed local (a
        tuple, a `String`) copies its object unless the binding borrows it
        (a tuple holding a record element bound off a field, `ub = p.ub`),
        which the borrow tracker records."""
        bound = self._local_or_param_type(name)
        bare = (unwrap_readonly(unwrap_ref_type(unwrap_own(bound)))
                if bound is not None else None)
        if bare is not None and not bare.is_value_type():
            decl = self.ctx.func.var_decl_by_name.get(name)
            kind = (self.ctx.func.bind_kinds.get(decl)
                    if decl is not None else None)
            return kind is BindKind.RVALUE
        return self.ctx.func.borrow_tracker.borrow_source(name) is None

    def leaf_lends_view(self, e: TpyExpr) -> bool:
        """One non-compound leaf of an inferred view's source (see
        `is_view_compatible_source`)."""
        if isinstance(e, TpyCoerce):
            return self.leaf_lends_view(e.expr)
        if isinstance(e, (TpyStrLiteral, TpyBytesLiteral)):
            return True
        if is_enum_name_read(e, self.ctx):
            return True
        if isinstance(e, TpyName):
            return self.name_lends_buffer(e.name)
        if isinstance(e, TpyFieldAccess):
            # A module or class qualifier: static only for a `Final`
            # constant (a constexpr view).
            if isinstance(e.obj, TpyName) and self.ctx.get_expr_type(e.obj) is None:
                et = self.ctx.get_expr_type(e)
                return et is not None and is_borrowing_view_type(unwrap_readonly(et))
            return False
        if isinstance(e, TpySubscript):
            if isinstance(e.obj, TpyStrLiteral):
                # A `str` slice views the literal's static storage, as
                # `"lit".strip()` does; a `bytes` literal's slice renders
                # over a temporary span, so it owns.
                return isinstance(e.index, TpySlice)
            return isinstance(e.obj, TpyName) and self.name_lends_buffer(e.obj.name)
        if isinstance(e, (TpyCall, TpyMethodCall)):
            rt = self.ctx.get_expr_type(e)
            if isinstance(rt, LiteralType) and rt.is_str_base():
                return True
            if rt is None or not is_borrowing_view_type(unwrap_readonly(rt)):
                return False
            # The binding question, not the return one: a local root the
            # view-escape check calls dangling AT A RETURN outlives a local
            # bound in the same body. A temporary operand (an owned call
            # result, a concat, an f-string) fails closed through this walk.
            return all(self.leaf_lends_view(op) for op in self._call_lent_operands(e))
        return False

    def _call_lent_operands(self, e: 'TpyCall | TpyMethodCall') -> list[TpyExpr]:
        """The operands a view-returning call's result may point into: the
        recorded borrow sources, or every operand that can hold a buffer
        when the callee recorded none. A module or class qualifier lends
        nothing."""
        ops = call_borrow_operands(e)
        if ops is not None:
            sources = recorded_return_borrow_sources(ops.fi)
            if sources:
                return [s.expr for s in call_lend_sources(ops, sources, expr_type=None)]
        operands: list[TpyExpr] = list(e.args)
        operands.extend((e.kwargs or {}).values())
        if isinstance(e, TpyMethodCall) and self.ctx.get_expr_type(e.obj) is not None:
            operands.append(e.obj)
        out = []
        for a in operands:
            at = self.ctx.get_expr_type(a)
            bare = unwrap_readonly(unwrap_ref_type(at)) if at is not None else None
            if not is_bufferless_scalar(bare):
                out.append(a)
        return out

    def mark_view_non_lending(self, family: ViewTypeFamily, var_id: int) -> None:
        """A loop variable or loop-head unpack target: it may stay a view of
        the current step, but never lends one to a local."""
        info = self.ctx.view_vars(family).get(var_id)
        if info is not None:
            info.lends_to_aliases = False

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
        bound = self._local_or_param_type(name)
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
        storage whose root the body reseats ANYWHERE counts as reseated
        already (`root_reseated_in_body`): which of the write and the view's
        read runs first is not a source-order question once a loop's back
        edge or a closure is involved."""
        info = self.ctx.view_vars(family).get(var_id)
        if info is None:
            return
        if storage not in info.source_storages:
            info.source_storages.append(storage)
        self.ctx.view_source_borrows_map(family).setdefault(storage, set()).add(var_id)
        root = storage.split(".", 1)[0]
        if root != info.variable_name and self.root_reseated_in_body(root):
            info.source_mutated = True

    def root_reseated_in_body(self, root: str) -> bool:
        """Whether this body may replace or rewrite, anywhere, the buffer
        `root` holds: a rebind of the name (assignment, walrus, unpack, for /
        with target, match capture, nested def), `+=`, `del`, a nested def's
        `nonlocal` rebind, or -- for a `String` / `bytearray`, whose methods
        write -- a store, a writing method call or a call argument whose
        operand may hold its object, through any alias the body binds
        anywhere (`prescan.InPlaceWrites`). A write through a field path
        demotes where it is analyzed instead. A view's own name is not asked: `s = s[1:]` re-views what
        `s` read."""
        func = self.ctx.func
        if self.ctx.is_reseated(root) or root in func.current_aug_assigned_vars:
            return True
        writes = func.in_place_writes.writes_through(root)
        if not writes:
            return False
        bound = self._local_or_param_type(root)
        if bound is None:
            return False
        bare = unwrap_readonly(unwrap_ref_type(unwrap_own(bound)))
        if not (is_string_type(bare) or is_bytearray_type(bare)):
            return False
        # "<arg>": the name reached a callee that may write it; the pre-scan
        # has no signatures, so a mutable sibling passed anywhere owns.
        return any(m is None or m == "<arg>"
                   or method_writes_receiver(self.ctx, bare, m)
                   for m in writes)

    def _local_or_param_type(self, name: str) -> 'TpyType | None':
        func = self.ctx.func.current_function
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                if pname == name:
                    return ptype
        scope = self.ctx.func.current_scope
        return scope.lookup(name) if scope is not None else None

    def register_view_source_storages(self, family: ViewTypeFamily, var_id: int,
                                      expr: TpyExpr) -> None:
        """Register every owned-storage root `expr` borrows as a source of
        view `var_id`. A compound source (ternary / and-or) borrows every
        root reachable through its arms."""
        bt = self.ctx.func.borrow_tracker
        func = self.ctx.func.current_function
        params = ({p for p, _ in func.params}
                  if isinstance(func, TpyFunction) else set())

        def _key(root: str) -> list[str]:
            return [canonical_storage_key(bt, bt.effective_storage(root))]

        def _leaf_root(leaf: TpyExpr, operand: bool = False) -> list[str]:
            if isinstance(leaf, TpyCoerce):
                return _leaf_root(leaf.expr, operand)
            if isinstance(leaf, (TpySubscript, TpyFieldAccess)):
                root = _borrow_storage_root(leaf)
                if root is not None:
                    return _key(root)
            if isinstance(leaf, TpyName):
                # A view of a parameter reads the caller's buffer until the
                # body rebinds the name, which makes it an owned local; a
                # call's receiver or argument is the buffer itself.
                if operand or leaf.name in params:
                    return _key(leaf.name)
            if isinstance(leaf, (TpyCall, TpyMethodCall)):
                rt = self.ctx.get_expr_type(leaf)
                if rt is not None and is_borrowing_view_type(unwrap_readonly(rt)):
                    return [k for op in self._call_lent_operands(leaf)
                            for k in _leaf_root(op, True)]
            return []
        for storage in walk_view_source_leaves(expr, _leaf_root):
            self.register_view_source_storage(family, var_id, storage)

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
                operands = self._call_lent_operands(e)
                return bool(operands) and all(of(a) for a in operands)
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
        """Record what one binding of view `var_id` reads: the owned storages
        `expr` borrows, so a mutation of any of them demotes the view, and,
        for the hoist rule, the roots of `expr`, or `roots` a caller resolved
        itself; neither is a source this site cannot name. `replace` sets the
        entry's first binding for a caller that registered it with no
        expression."""
        info = self.ctx.view_vars(family).get(var_id)
        if info is None:
            return
        if expr is not None:
            self.register_view_source_storages(family, var_id, expr)
            roots = self.view_hoist_roots(expr)
        if replace:
            info.hoist_roots = set()
            info.hoist_unknown = False
        if roots is None:
            info.hoist_unknown = True
        else:
            info.hoist_roots |= roots - {info.variable_name}
            if self._root_dies_before_view(info.variable_name, roots):
                info.reassigned_from_owned = True

    def _root_dies_before_view(self, name: str, roots: 'set[str]') -> bool:
        """A root declared in a block nested inside the one view `name` is
        declared in dies at that block's closing brace, while the view is
        still in scope (`decl_block_depth`). A loop variable's roots are its
        iterable's, declared at the loop's depth or further out, so a loop
        variable never fails this. A name this function does not bind (a
        capture, a global) outlives the body; a local of it with no depth on
        record is unknown, so it owns the view rather than lend it."""
        func = self.ctx.func
        depths = func.decl_block_depth
        view_depth = depths.get(name, 0)
        for r in roots:
            if r == name:
                continue
            depth = depths.get(r)
            if depth is None:
                if self._binds_locally(r):
                    return True
            elif depth > view_depth:
                return True
        return False

    def _binds_locally(self, name: str) -> bool:
        """Python's local test: a parameter (the receiver too) or a name the
        body binds anywhere."""
        func = self.ctx.func
        fn = func.current_function
        if isinstance(fn, TpyFunction):
            if any(pname == name for pname, _ in fn.params):
                return True
            if (name == "self" and fn.is_method
                    and not fn.is_staticmethod):
                return True
        return name in func.body_bound_names

    def note_view_rebind(self, name: str, value_expr: TpyExpr,
                         family: ViewTypeFamily) -> None:
        """A view-compatible rebind of pending view `name` is one more binding
        the hoist rule has to see."""
        var_id = self.ctx.view_var_map(family).get(name)
        if var_id is not None:
            self.note_view_binding(family, var_id, value_expr)

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
                    if source and (source.resolved_type == family.owned_type
                                   or not source.lends_to_aliases):
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
        func = self.ctx.func.current_function
        self.pend.settle_all(getattr(func, "body", None))
        self._check_unresolved_none_inference()
        self._resolve_pending_list_types()
        self._resolve_pending_dict_and_set_types()
        for family in VIEW_TYPE_FAMILIES:
            self._resolve_pending_view_types(family)
        self._finalize_pending_elem_type_fields()
        self._finalize_pending_in_bindings()
        self._finalize_arm_decl_types()
        self._check_unresolved_pending_generics()
        self.pend.assert_settled(getattr(func, "body", None))
        self.pend.drop_cells()

    def _finalize_arm_decl_types(self) -> None:
        """Give the binding that declares a name in each arm of a
        per-arm-declared `if` (`arm_decl_sites`) the one joined type,
        settled. A binding records no type of its own unless something set
        one, so an arm's declaration would take its value's type (`"lit"` in
        one arm, `a + "!"` in the next); it is one Python local, so every arm
        declares it at the type a predecl in front of the `if` would have
        had. Outer links run last, so every arm of a chain takes the type of
        the outermost link recording the name. A walrus, unpack or `def`
        declaration keeps its own type."""
        for if_stmt, name, decl in self.ctx.func.arm_decl_sites:
            if (not isinstance(decl, TpyVarDecl) or decl.type is not None
                    or decl.init is None):
                continue
            target = self._settled_decl_type(
                self.ctx.arm_branch_decls[if_stmt][name])
            if target is None:
                continue
            own = self.ctx.var_types.get(decl)
            if own is None:
                own = self.ctx.expr_types.get(decl.init)
            settled = self._settled_decl_type(own)
            if settled is None or settled == target:
                continue
            self.ctx.var_types[decl] = target
            if decl.loc is not None:
                self.ctx.declared_var_types[(decl.loc.line, name)] = target

    def _settled_decl_type(self, typ: TpyType | None) -> TpyType | None:
        """A binding type as a declaration spells it once deduction settled:
        qualifiers off, pending views and containers resolved, literals at
        their default width. Own-carrying bindings are not settled here."""
        if typ is None or isinstance(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(typ))), OwnType):
            return None
        return resolve_int_literals(
            self._deep_resolve_pending(unwrap_qualifiers(typ),
                                       settle_views=True),
            self.ctx.default_int_for_literal)

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
        # A pending number is bound in the function's own scope too (its
        # parents are the enclosing function's, settled on their own).
        scope = self.ctx.func.current_scope
        if scope is not None and scope is not self.ctx.global_scope:
            for name, typ in scope.bindings.items():
                if isinstance(typ, TpyType) and contains_pending_num(typ):
                    scope.bindings[name] = self.pend.finalize(typ)

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
        for name, entry in list(loop_vars.items()):
            if entry.var_type is not None:
                resolved = self._deep_resolve_pending(entry.var_type)
                if resolved is not entry.var_type:
                    loop_vars[name] = dc_replace(entry, var_type=resolved)

        # Branch-decl snapshots capture binding types before the deferred
        # container resolution; codegen renders them directly (predecl /
        # frame-slot init), so finalize each map recorded by this function.
        for decls in self.ctx.func.pending_branch_decl_maps:
            for name, vtype in decls.items():
                if vtype is not None:
                    decls[name] = self._deep_resolve_pending(vtype)

    def _deep_resolve_pending(self, typ: TpyType, *,
                              settle_views: bool = False) -> TpyType:
        """Replace every Pending* container leaf in a (possibly composite) type
        with its registry-resolved type, recursing through wrapper/composite
        types via `map_inner_types`. A genuinely-unresolved Pending (no resolved
        type yet) is left as-is for the downstream unresolved-type diagnostic.
        `settle_views` also resolves a PendingViewType leaf, to its family's
        owned type when unresolved -- for callers running after view
        resolution."""
        if isinstance(typ, PendingNumType):
            return self.pend.concrete(typ)
        if settle_views and isinstance(typ, PendingViewType):
            info = self.ctx.view_vars(typ.family).get(typ.var_id)
            return (info.resolved_type if info is not None and info.resolved_type
                    else typ.family.owned_type)
        resolved = self._resolved_container_type(typ)
        if resolved is not None:
            return resolved
        return typ.map_inner_types(
            lambda t: self._deep_resolve_pending(t, settle_views=settle_views))

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
