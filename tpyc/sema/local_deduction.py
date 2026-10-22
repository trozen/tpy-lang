"""Unified local type deduction for TurboPython semantic analysis.

Unified local type deduction: reassignment inference, view-type tracking
(str/bytes), and list/dict/set literal tracking, with post-body resolution
for function-local variables.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable

from ..coercions import CoercionContext
from ..parse import TpyExpr, TpyStmt, TpyName, TpyCall, TpyMethodCall, TpyCoerce, TpyFunction, TpyListRepeat, TpyAugAssign
from ..parse.nodes import (TpyStrLiteral, TpySubscript, TpyArrayLiteral,
                           TpyVarDecl, TpyNoneLiteral, TpyTupleLiteral, TpySlice)
from ..typesys import (
    contains_pending_num,

    collapse_tuple_own_elements,
    DictLiteralInfo,
    make_dict,
    FloatLiteralType,
    is_float_type,
    is_integer_type,
    is_protocol_type,
    TypeParamRef,
    IntLiteralType,
    ListLiteralInfo,
    make_list,
    ListRepeatType,
    LiteralType,
    make_array,
    NoneType,
    OptionalType,
    OwnType,
    PendingDictType,
    PendingNumType,
    PendingListType,
    PendingContainerType,
    ContainerLiteralInfo,
    KEY,
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
from .call_lends import TempLend, ViewLend
from .context import (
    PENDING_CONTAINER_TYPES, MODULE_INIT_CONTEXT, ProbeSlot,
    contains_pending_leaf,
)
from ..namespace import BindingKind
from .receiver_calls import method_writes_receiver
from ..storage_trace import StepKind
from .storage_walk import (Leaf, LeafKind, StorageKey, borrow_key, leaves,
                           receiver_leaves, ternary_operands, view_leaves)
from ..diagnostics import Scope, SemanticError
from .type_join import (InferredJoin, JoinOutcome, descend,
                        find_int_float_mix, flipped, join_inferred_value_types,
                        numeric_kind, peel_value,
                        annotate_first_binding, operand_spelling,
                        python_type_name, rebind_mix_message, usage_mix_message,
                        wider_store_message)
from .numeric_lattice import numeric_info, same_width_family, widen_numeric_types
from .pending_num import (Entry, container_parts, is_pending_num,
                          numeric_container, pairs_as, pending_leaves,
                          pending_list_of)
from ..parse.nodes import is_parse_node
from .alias_rebind import BindKind
from ..type_def_registry import (
    is_set, is_dict, is_array, is_span, is_list, is_fixed_int_type, is_big_int_type,
    is_str_type, is_str_view_type, is_bytes_type, is_bytes_view_type,
    is_string_type, is_bytearray_type,
    is_borrowing_view_type,
    int_traits_of,
)

if TYPE_CHECKING:
    from .compatibility import TypeCompatibility
    from .context import SemanticContext
    from .pending_num import PendingNums


def _view_family_is_str(t: 'TpyType | None') -> bool | None:
    """Whether `t` is of the `str` view family (a `Literal[str]` included:
    every value a compile-time literal), False for the `bytes` family, None
    for neither."""
    if (is_str_type(t) or is_str_view_type(t) or isinstance(t, PendingStrType)
            or (isinstance(t, LiteralType) and t.is_str_base())):
        return True
    if (is_bytes_type(t) or is_bytes_view_type(t)
            or isinstance(t, PendingBytesType)):
        return False
    return None


def _view_leaf_source_key(leaf: Leaf,
                          params: set[str]) -> StorageKey | None:
    """The key of the storage a view's leaf reads that the view's source
    storages track: the first operand on the leaf's way that is no call
    the climb went into -- a field or element read's loan key (one hop), a
    call operand's NAME, or a parameter the value itself names -- or None
    (a call's own result, a local the value names, anything unkeyed)."""
    stretches = leaf.stretches()
    for n, st in enumerate(stretches):
        i = st.skip()
        if i < len(st.steps):
            return borrow_key(st.node_at(i))
        if n < len(stretches) - 1:
            continue
        if leaf.kind is LeafKind.PLACE:
            return (StorageKey.of_name(leaf.name)
                    if n > 0 or leaf.name in params else None)
        # A temp-backed element is a copy in a tuple temporary, no storage
        # of the caller's.
        return (None if leaf.at_call or leaf.temp_backed
                else borrow_key(leaf.node))
    return None


def view_source_is_temporary(ctx: 'SemanticContext', expr: TpyExpr) -> bool:
    """True if a view bound to `expr` would reference end-of-statement
    temporary storage: a leaf of its storage under `TempLend` is a
    temporary, an unclassified view node or unplaceable.

    Unlike is_dangling_return, a local name or field is treated as STABLE
    -- it outlives a same-scope binding -- and so is a module or class
    variable; only genuine temporaries (fresh calls, f-strings, operators,
    and views borrowing them) lack durable storage. Used to reject an
    explicit `StrView`/`BytesView` annotation bound to a temporary, while
    leaving pinned-view aliases of stable locals/fields valid.
    """
    return any(leaf.kind in (LeafKind.TEMPORARY, LeafKind.VIEWNODE,
                             LeafKind.UNPLACEABLE)
               for leaf in leaves(expr, TempLend(ctx), split=False))


def view_slot_source_is_temporary(ctx: 'SemanticContext', slot: 'TpyType',
                                  expr: TpyExpr) -> bool:
    """`view_source_is_temporary` asked of a slot that HOLDS a view
    (`holds_borrowing_view`), for the part of the slot the value lands in:
    a `None` holds no view, a select answers per arm, a union member is
    chosen by the value's type (`lands_in_view_member`), and a tuple literal
    answers per element against the element slot."""
    if not holds_borrowing_view(slot):
        return False
    operands = ternary_operands(expr)
    if len(operands) > 1:
        return any(view_slot_source_is_temporary(ctx, slot, e)
                   for e in operands)
    inner = operands[0]
    if isinstance(inner, TpyNoneLiteral):
        return False
    t = unwrap_readonly(slot)
    if isinstance(t, OptionalType):
        t = unwrap_readonly(t.inner)
    if not lands_in_view_member(t, ctx.get_expr_type(inner)):
        return False
    if (isinstance(t, TupleType) and isinstance(inner, TpyTupleLiteral)
            and len(inner.elements) == len(t.element_types)):
        return any(view_slot_source_is_temporary(ctx, et, e)
                   for et, e in zip(t.element_types, inner.elements))
    return view_source_is_temporary(ctx, expr)


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
    return [t for e in receiver_leaves(expr) for t in leaf(e)]


def _mentions(t: TpyType, part: TpyType) -> bool:
    """Whether `part` is `t` or one of its inner types."""
    return t == part or any(_mentions(i, part) for i in t.inner_types())


def _is_number(t: TpyType) -> bool:
    return is_integer_type(t) or is_float_type(t)


def _leaf_mismatch(a: TpyType, b: TpyType, leaf) -> bool:
    """Whether two element types of one shape differ at a numeric place.
    `leaf(a, b)` answers a pair it takes for a leaf and returns None to
    descend. Qualifiers and a nested container's representation are not
    this check's."""
    a = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a)))
    b = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(b)))
    verdict = leaf(a, b)
    if verdict is not None:
        return verdict
    a_inner, b_inner = a.inner_types(), b.inner_types()
    if type(a) is not type(b) or len(a_inner) != len(b_inner):
        return False
    return any(_leaf_mismatch(x, y, leaf) for x, y in zip(a_inner, b_inner))


def _stale_literal_leaf(seen: TpyType, final: TpyType, default_int) -> bool:
    """Whether a literal member of `seen` settles, at its default width, to
    another number than `final` holds in its place."""
    def leaf(s: TpyType, f: TpyType) -> 'bool | None':
        if isinstance(s, FloatLiteralType):
            return _is_number(f) and f != FLOAT
        if isinstance(s, IntLiteralType):
            return _is_number(f) and f != default_int(s)
        return None
    return _leaf_mismatch(seen, final, leaf)


def _numeric_leaf_differs(a: TpyType, b: TpyType) -> bool:
    """Whether two element types hold a different number in the same place."""
    return _leaf_mismatch(
        a, b, lambda x, y: x != y if _is_number(x) and _is_number(y) else None)


class _Form(Enum):
    """The container a list literal resolves to
    (`LocalTypeDeduction._container_form`)."""
    ANNOTATED = auto()
    LIST = auto()
    REPEAT = auto()
    ARRAY = auto()


def _row_ids(t: TpyType) -> list[int]:
    """The literal ids of the rows an element tree holds."""
    if isinstance(t, PendingListType):
        return [t.literal_id, *_row_ids(t.element_type)]
    if isinstance(t, TupleType):
        return [lid for m in t.element_types for lid in _row_ids(m)]
    return []


def pending_elem_read(ctx: 'SemanticContext', receiver: 'TpyType | None',
                      result: TpyType, node: 'TpyExpr') -> TpyType:
    """The type of a read built from the element of a container literal
    whose type is not decided yet (`xs[i]`, `xs.pop()`, `xs.copy()`). It is
    compiled at the element as the literal has it so far: a bare literal
    stays one, resolved by its consumer (`note_elem_binding` when that is an
    unannotated binding), and anything composite takes the default width
    for its literal members, so its consumers see a concrete type -- which
    the list literal remembers (`note_pending_elem_read`)."""
    if isinstance(result, (IntLiteralType, FloatLiteralType)):
        return result
    if pending_leaves(result):
        # The element of a list whose cells decide it, or a part of one:
        # the read follows the cells.
        return result
    if (isinstance(receiver, PendingListType)
            and _mentions(result, receiver.element_type)):
        note_pending_elem_read(ctx, receiver, node)
    return resolve_int_literals(result, ctx.default_int_for_literal)


def note_elem_binding(ctx: 'SemanticContext', value: 'TpyExpr | None') -> None:
    """An unannotated binding takes a literal-typed `value` at its default
    width. When `value` reads the element of a pending list literal, that
    is a width the list may not end up with: the literal remembers it."""
    if isinstance(value, TpyCoerce):
        value = value.expr
    if not isinstance(value, (TpySubscript, TpyMethodCall)):
        return
    receiver = ctx.expr_types.get(value.obj)
    if receiver is not None:
        receiver = unwrap_readonly(unwrap_ref_type(receiver))
    note_pending_elem_read(ctx, receiver, value)


def note_pending_elem_read(ctx: 'SemanticContext', receiver: 'TpyType | None',
                           node: 'TpyExpr') -> None:
    """Remember that `node` took an element of the list literal behind
    `receiver` at the width the literal has so far. Another use may give
    the list another element type (a typed parameter, a wider append); the
    read was then compiled at the wrong one, and `_check_elem_reads`
    refuses it."""
    if not isinstance(receiver, PendingListType):
        return
    info = ctx.list_literal(receiver.literal_id)
    # A list whose element cells decide has no read to go stale.
    if info is not None and info.elem_cells is None:
        info.elem_reads.append((receiver.element_type, node))


def mark_pending_list_mutated(ctx: 'SemanticContext', expr: 'TpyExpr | None',
                              expr_type: 'TpyType | None') -> None:
    """Mark the pending list literal behind `expr` as mutated so it resolves
    to list, not Array. Covers a directly PendingListType-typed expression
    and a name bound to a tracked literal (alias). One home for the
    expression-rooted mutation routes: mutating method receivers, binary
    concat operands, and aug-assign targets."""
    if isinstance(expr_type, PendingListType):
        info = ctx.list_literal(expr_type.literal_id)
        if info:
            info.is_mutated = True
        return
    if isinstance(expr, TpyName):
        lit_id = ctx.func.variable_to_literal.get(expr.name)
        if lit_id is not None:
            info = ctx.list_literal(lit_id)
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

    def join_rebind(self, name: str, existing_type: TpyType,
                    var_type: TpyType, scope: Scope, line: int) -> None:
        """A rebind whose value widened `name` to `var_type` (a module
        variable first bound to a literal joins its bindings, until module
        code is analyzed as one body: TODO "Module scope is the body of
        __tpy_init"): the variable takes the new type wherever it is
        recorded, so a store of any spelling widens it alike."""
        if var_type == existing_type:
            return
        self.retype_declaration(name, var_type)
        scope.define(name, var_type)
        # The binding's owner, not the current level: inside a comprehension
        # or a block the current namespace is a temporary child that a
        # module variable's later reads never see.
        ns = self.ctx.func.current_ns
        if ns is not None and not ns.update_variable_type_recursive(
                name, var_type):
            ns.bind_variable(name, var_type)
        if (self.ctx.is_top_level
                and self.ctx.global_scope.lookup(name) is not None):
            self.ctx.define_module_global(name, var_type, line)

    def retype_declaration(self, name: str, var_type: TpyType) -> None:
        """A rebind widened `name` (a module variable joins its bindings):
        its first declaration, and every type recorded for it, take the
        new type."""
        resolved = unwrap_readonly(var_type)
        orig_decl = self.ctx.func.var_decl_by_name.get(name)
        if orig_decl:
            self.ctx.var_types[orig_decl] = resolved
        for key in self.ctx.declared_var_types:
            if key[1] == name:
                self.ctx.declared_var_types[key] = resolved

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
        module_level = self.module_level()
        if module_level and self._literal_outgrows(existing_type, init_type):
            if self.keeps_first_type(name):
                self.refuse_wider_rebind(
                    name, existing_type,
                    self.ctx.default_int_for_literal(init_type,
                                                     rebinding=True),
                    init_expr, aug_op, site)
            return self.ctx.default_int_for_literal(
                init_type, warn_node=init_expr, rebinding=True)
        widened = widen_numeric_types(existing_type, init_type)
        if widened is not None and widened != existing_type:
            if module_level and not (is_integer_type(existing_type)
                                     and self.keeps_first_type(name)):
                return widened
            self.refuse_wider_rebind(name, existing_type, init_type,
                                     init_expr, aug_op, site)

        return existing_type

    def module_level(self) -> bool:
        """Whether the code being analyzed is module code, whose variables
        are the module's globals."""
        return (self.ctx.func.current_function is None
                or self.ctx.is_top_level)

    def keeps_first_type(self, name: str) -> bool:
        """Whether an integer store into `name` at module level keeps the
        type of its first binding, refusing a wider value, as a function
        local does. A module variable first bound to a literal still joins
        its bindings (and an annotated one converts what it is given),
        until module code is analyzed as one body (TODO "Module scope is
        the body of __tpy_init")."""
        return (self.module_level()
                and name not in self.ctx.func.literal_seeded
                and name not in self.ctx.func.authoritative_types)

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
            with self.ctx.probe_slot_scope(ProbeSlot.QUERY):
                verdict = self.pin_pending_container(new, current)
            return (flipped(verdict)
                    if verdict.outcome is JoinOutcome.INT_FLOAT_MIX else None)
        if isinstance(current, pending) and not isinstance(new, pending):
            with self.ctx.probe_slot_scope(ProbeSlot.QUERY):
                verdict = self.pin_pending_container(current, new)
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

    def observe_named_store(self, obj: TpyExpr, t: TpyType,
                            entry: Entry) -> None:
        """`entry` stored into the container literal local `obj` names, of
        container type `t`, through a method or a subscript
        (`observe_store`); each name the list was bound to before (`ys =
        xs`) learns the joined element too."""
        if not isinstance(obj, TpyName):
            return
        info = self._named_record(obj.name, t)
        if info is None:
            return
        site = entry.parts[-1][1] or obj
        if self.observe_store(info, entry, site, obj):
            # Every name for the container shares the cells the store
            # went to.
            return
        visited: set[int] = {info.literal_id}
        current = info
        while current.source_literal_id is not None:
            if current.source_literal_id in visited:
                break
            visited.add(current.source_literal_id)
            source = self.ctx.container_record(current.source_literal_id)
            if source is None:
                break
            self.observe_store(source, entry, site, obj)
            current = source

    def rebind_pending(self, obj: TpyExpr, t: TpyType) -> None:
        """The container literal `obj` reads as `t` from here on: the parts
        its record learned."""
        self.ctx.set_expr_type(obj, t)
        if isinstance(obj, TpyName):
            if self.ctx.func.current_scope:
                self.ctx.func.current_scope.define(obj.name, t)
            if self.ctx.func.current_ns:
                self.ctx.func.current_ns.bind_variable(obj.name, t)

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
        self.observe_store(info, Entry(((value_type, value),)),
                           value or site or info.expr, site or info.expr)

    def observe_store(self, info: 'ContainerLiteralInfo', entry: Entry,
                      store_site: TpyStmt | TpyExpr, site: TpyExpr) -> bool:
        """An entry stored into container literal `info`. The one switch
        between the cells and the joins of the containers no cell decides:
        the cells take it (`PendingNums.store_value`, which seeds an empty
        container whose entry holds a number); otherwise each part the
        container learned from its uses joins the entry's -- what reaches
        that join is a module-level container, one stored into from a
        nested body, or one of non-numbers. True when the cells took it."""
        cells, last = self.pend.store_value(info.pending_type(), entry,
                                            store_site)
        if cells is not None:
            return True
        # The value as the store left it (a pending number it settled).
        entry = Entry((*entry.parts[:-1], (last, entry.parts[-1][1])))
        root = info.pending_type()
        kind = root.KIND
        name = info.variable_name or root.HINT_NAME
        init = self._initializer_spelling(info.expr, *root.INITS)
        parts = list(root.parts())
        for i, (step, (new_type, value)) in enumerate(zip(root.STEPS,
                                                         entry.parts)):
            def annotation(f: str, i: int = i) -> str:
                shown = [f if j == i else
                         ("..." if isinstance(p, UnknownElementType)
                          else python_type_name(p))
                         for j, p in enumerate(parts)]
                return f"{name}: {kind}[{', '.join(shown)}] = {init}"
            result = self._join_observed_type(
                parts[i], new_type, site,
                (f"{kind} '{name}'" if info.variable_name else f"this {kind}")
                + (" keys" if step == KEY else ""),
                annotation, value if is_parse_node(value) else None)
            if result is not None:
                parts[i] = result
                info.set_parts(root.with_parts(tuple(parts)))
        return False

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

        Single entry point for all container types: a list's storage facts
        (passed_to_list_param, passed_to_span_param), then -- for a container
        no cell decides; one whose cells decide met the parameter where the
        argument was analyzed (`PendingNums.elem_context`) -- its parts.
        """
        self.ctx.assert_slot_effect("a container's parameter context")
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
        info = self._named_record(arg_expr.name, arg_type)
        if info is None:
            return
        if isinstance(info, ListLiteralInfo):
            if is_list(param_type):
                info.passed_to_list_param = True
            elif is_span(param_type):
                info.passed_to_span_param = True
            else:
                return
        if info.elem_cells is None:
            self._learn_parts(info, param_type)

    def elem_container(self, slot_type: TpyType, marker: TpyType,
                       ) -> tuple[TpyType | None, TpyType | None]:
        """The typed container a slot is to a list literal: the slot's type
        itself when it is a list, a Span or an Array of a number, or -- for
        a protocol whose one type argument is the element a list gives it
        (`Iterable[int]`) -- the list of that number, with the protocol as
        the type to show. (None, None) for any other. `marker` stands for
        the literal's element in the protocol question."""
        container = numeric_container(slot_type)
        if container is not None:
            return container, None
        view = unwrap_qualifiers(slot_type)
        args = getattr(view, "type_args", None)
        if not is_protocol_type(view) or not args or len(args) != 1:
            return None, None
        elem = args[0]
        if isinstance(elem, TpyType):
            elem = unwrap_readonly(unwrap_own(unwrap_ref_type(elem)))
        if not (isinstance(elem, TpyType)
                and (is_integer_type(elem) or is_float_type(elem))):
            return None, None
        # Asked of the protocol, not of its name: the element position is
        # whatever a list binds the type argument to.
        bound: dict[str, TpyType] = {}
        pattern = dc_replace(view, type_args=(TypeParamRef("__elem"),))
        if (self.compat.type_ops._match_protocol_type_args_with_inference(
                pattern, make_list(marker), bound)
                and bound.get("__elem") == marker):
            return make_list(elem), view
        return None, None

    def pin_pending_container(self, pending: TpyType,
                              target: TpyType) -> InferredJoin:
        """Resolve a pending container toward the concrete container `target`
        it must share one C++ type with (the other operand of a select).

        Not JOINED when an element type the container already committed to
        disagrees with `target`'s -- the two cannot share a type then. A
        literal element is an inferred join with `target`'s element type: an
        int meeting a float is refused (`[5]` beside a `list[float]` is a
        list of ints under CPython), and a value must fit (`[300]` has no
        `list[uint8]` spelling). A slot step that does not commit
        (`SemanticContext.slot_step_commits`, a `ProbeSlot.QUERY` scope
        included) asks for the verdict alone, leaving the container
        unpinned."""
        commit = self.ctx.slot_step_commits()
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
            info = self.ctx.list_literal(pending.literal_id)
            to_array = is_array(target)
            if info is None or not (is_list(target) or to_array):
                return joined
            elem = target.type_args[0]
            lc = (self.pend.cells_of(info) if info.elem_cells is not None
                  else None)
            if lc is not None:
                # The element is the cells': the operand was analyzed as a
                # value, which settled it.
                if self.pend.known_as(lc, target):
                    return joined
                mix = self.int_float_mix(
                    self.pend.tree_known(lc).element_type, elem)
                return below(mix, 0) if mix is not None else incompatible
            if (commit and numeric_container(target) is not None
                    and self.pend.seed_by_context(pending, target, None,
                                                  "selected") is not None):
                # An empty list's element cell is seeded by the container
                # it shares one C++ type with.
                return joined
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
            info = self.ctx.container_record(pending.literal_id)
            if info is None:
                return joined
            if info.elem_cells is not None or (
                    commit and self.pend.seed_by_context(
                        pending, target, None, "selected") is not None):
                # The cells decide; the operand was analyzed as a value.
                return self._pin_cells(info, target)
            key = pin(info.key_type, target.type_args[0])
            value = pin(info.value_type, target.type_args[1])
            for index, verdict in enumerate((key, value)):
                if verdict.outcome is not JoinOutcome.JOINED:
                    return below(verdict, index)
            if commit:
                info.key_type, info.value_type = key.joined, value.joined
            return joined
        if isinstance(pending, PendingSetType) and is_set(target):
            info = self.ctx.container_record(pending.literal_id)
            if info is None:
                return joined
            if info.elem_cells is not None or (
                    commit and self.pend.seed_by_context(
                        pending, target, None, "selected") is not None):
                return self._pin_cells(info, target)
            verdict = pin(info.element_type, target.type_args[0])
            if verdict.outcome is not JoinOutcome.JOINED:
                return below(verdict, 0)
            if commit:
                info.element_type = verdict.joined
            return joined
        return incompatible

    def _pin_cells(self, info: 'DictLiteralInfo | SetLiteralInfo',
                   target: TpyType) -> InferredJoin:
        """`pin_pending_container` for a dict or set whose cells decide its
        leaves: JOINED when they are `target`'s at the types known so far,
        else the mix or the incompatibility."""
        lc = self.pend.cells_of(info)
        if self.pend.known_as(lc, target):
            return InferredJoin(JoinOutcome.JOINED, target)
        mix = self.int_float_mix(
            self.pend.declared_form(self.pend.tree_known(lc)), target)
        return mix if mix is not None else InferredJoin(JoinOutcome.INCOMPATIBLE)

    def rebind_elem_cell(self, existing: TpyType, value_type: TpyType,
                         site: TpyStmt | TpyExpr) -> bool:
        """A name whose container literal has cells is rebound to a value
        of `value_type`: one local holds one element type, so another
        container literal shares what each holds with the other, and a typed
        container is one the leaves must agree with. True when the value is
        a container this settled the element question for (for a dict or a
        set, a typed one too: the rebinding then converts nothing)."""
        lc = self.pend.container_cells(existing)
        if lc is None:
            return False
        kind = type(lc.tree)
        other = self.pend.container_cells(value_type)
        if other is not None and type(other.tree) is kind:
            return (other.cids == lc.cids
                    or self.pend.link(lc.tree, other.tree, site))
        empty = self.pend.seedable(value_type)
        if empty is not None and type(empty.pending_type()) is kind:
            # An empty container rebinding one with cells holds what it
            # does: one local, one element type.
            self.pend.share_cell(empty, lc.tree)
            return True
        is_list_cells = isinstance(lc.tree, PendingListType)
        pending = pending_list_of(value_type) if is_list_cells else None
        if not is_list_cells:
            container = numeric_container(value_type, lc.tree)
            if (container is None
                    or not self.pend.fits_container(lc.tree, container)):
                return False
            refusal = self.pend.context_refusal(lc, container, "rebound")
            if refusal is not None:
                raise self.ctx.error(refusal, site)
            self.pend.elem_context(existing, container, site, "rebound")
            return True
        if pending is not None:
            info = self.ctx.list_literal(pending.literal_id)
            if isinstance(pending.element_type, UnknownElementType):
                if info is not None:
                    self.pend.attach(info, lc.tree)
                return True
            container = make_list(pending.element_type)
        else:
            container = numeric_container(value_type)
        if (container is None or numeric_container(container) is None
                or not self.pend.fits_container(lc.tree, container)):
            return False
        refusal = self.pend.context_refusal(lc, container, "rebound")
        if refusal is not None:
            raise self.ctx.error(refusal, site)
        self.pend.elem_context(existing, container, site, "rebound")
        return pending is not None

    def mark_list_different_size(self, literal_id: int) -> None:
        """Mark a pending list literal as needing list (different-size reassignment)."""
        info = self.ctx.list_literal(literal_id)
        if info is not None:
            info.is_mutated = True

    def link_list_literals(self, target_id: int, value_id: int) -> None:
        """Link target literal to value literal for alias-based promotion.

        Sets a one-directional edge; the resolution pass propagates promotion
        in both directions (Array->List transitivity) via the while-changed loop.
        """
        target = self.ctx.list_literal(target_id)
        if target is None:
            return
        if target.source_literal_id is None:
            target.source_literal_id = value_id
        else:
            # Target already linked to a different source -- it can hold multiple
            # distinct list literals, so all three must be promoted to list.
            target.is_mutated = True
            for lid in (target.source_literal_id, value_id):
                info = self.ctx.list_literal(lid)
                if info is not None:
                    info.is_mutated = True

    def mark_container_return_context(self, return_expr: TpyExpr, return_type: TpyType) -> None:
        """Track return context for list/dict/set literal inference.

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
        info = self._named_record(return_expr.name, None, return_type)
        if info is None:
            return
        if isinstance(info, ListLiteralInfo):
            info.passed_to_list_param = True
        # Cells meet the return type where the value is coerced to it.
        if info.elem_cells is None:
            self._learn_parts(info, return_type)

    def _named_record(self, name: str, t: TpyType | None,
                      returned: TpyType | None = None,
                      ) -> 'ContainerLiteralInfo | None':
        """The record of the container literal local `name` names: of
        `t`'s kind when `t` is given, else of the kind a returned
        container `returned` is (a list returned as a list)."""
        info = self.ctx.container_record(
            self.ctx.func.variable_to_literal.get(name))
        if info is None:
            return None
        cls = type(info.pending_type())
        if t is not None and not isinstance(t, cls):
            return None
        if returned is not None and not (
                is_list(returned) if cls is PendingListType
                else pairs_as(cls, returned)):
            return None
        return info

    def _learn_parts(self, info: 'ContainerLiteralInfo',
                     container: TpyType) -> None:
        """A container literal no cell decides meets the typed `container`
        of its kind: a list takes its element from it, a dict or set widens
        each part toward it."""
        root = info.pending_type()
        if not pairs_as(root, container):
            return
        parts = container_parts(container, root)
        if isinstance(info, ListLiteralInfo):
            info.coerced_element_type = parts[0]
            return
        learned = []
        for old, new in zip(root.parts(), parts):
            widened = self._widen_inferred_type(old, new)
            learned.append(widened if widened is not None else old)
        info.set_parts(root.with_parts(tuple(learned)))

    def register_list_alias(self, var_name: str, init_type: PendingListType, decl_line: int | None = None) -> PendingListType:
        """Register alias relationship when b = a where a is a PendingListType.

        Creates a new ListLiteralInfo for the alias variable with source_literal_id
        pointing to the original. Returns a new PendingListType for the alias.
        """
        source_literal_id = init_type.literal_id
        source_info = self.ctx.list_literal(source_literal_id)
        if source_info is None:
            return init_type

        new_id = self.ctx.literal_counter
        self.ctx.literal_counter += 1
        # Two names for one list share the cells that decide its element.
        elem = (source_info.element_type
                if source_info.elem_cells is not None
                else init_type.element_type)
        info = ListLiteralInfo(
            literal_id=new_id,
            expr=source_info.expr,
            element_type=elem,
            size=init_type.size,
            variable_name=var_name,
            decl_line=decl_line,
            source_literal_id=source_literal_id,
            elem_cells=(dict(source_info.elem_cells)
                        if source_info.elem_cells is not None else None),
        )
        self.ctx.container_literals[new_id] = info
        self.ctx.func.pending_resolutions.append(new_id)
        self.ctx.func.variable_to_literal[var_name] = new_id
        return PendingListType(elem, init_type.size, new_id)

    def _resolve_alias_element_type(self, info: ListLiteralInfo) -> TpyType | None:
        """Walk alias chain to find an inferred element type from a linked literal."""
        visited: set[int] = {info.literal_id}
        current = info
        while current.source_literal_id is not None:
            if current.source_literal_id in visited:
                break
            visited.add(current.source_literal_id)
            source = self.ctx.list_literal(current.source_literal_id)
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
        self._close_row_groups()
        for literal_id in self._rows_first():
            info = self.ctx.list_literal(literal_id)
            if info is None:
                continue

            # Resolve element type
            # Priority: coerced type from param > inferred from usage > resolved inner PendingListType > default
            elem_type = info.element_type
            by_cell = info.elem_cells is not None
            if by_cell:
                # The cell settled with the function's numbers; every use
                # of the element was compiled at this type.
                elem_type = self.pend.finalize(elem_type)
                info.element_type = elem_type

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
            if (info.coerced_element_type is not None and not by_cell
                    and not isinstance(elem_type, UnknownElementType)):
                elem_type = info.coerced_element_type

            elem_type = resolve_int_literals(elem_type, self.ctx.default_int_for_literal)
            if isinstance(elem_type, PendingViewType):
                # Container elements are owned -- views can't be stored in a list.
                elem_type = elem_type.family.owned_type

            form = self._container_form(info)
            if form is _Form.ANNOTATED:
                resolved = info.explicit_type
            elif form is _Form.LIST:
                resolved = make_list(elem_type)
            elif form is _Form.REPEAT:
                resolved = ListRepeatType(elem_type)
            else:
                resolved = make_array(elem_type, info.size)

            # The read guard and the context override above serve the lists
            # without a cell: module-level, nested, tuple-element and
            # non-numeric ones. A function-local list of scalar numbers,
            # empty or not, has its element decided by a cell.
            if not info.has_explicit_annotation and not by_cell:
                self._check_elem_reads(info, elem_type)
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
                info = self.ctx.list_literal(literal_id)
                if info is None or info.source_literal_id is None:
                    continue
                source = self.ctx.list_literal(info.source_literal_id)
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

        # An alias and its source are one list, so they hold one element
        # type; a use of only one of the names can decide it differently.
        for literal_id in self.ctx.func.pending_resolutions:
            info = self.ctx.list_literal(literal_id)
            source = (self.ctx.list_literal(info.source_literal_id)
                      if info is not None and info.source_literal_id is not None
                      else None)
            if (source is None or info.resolved_type is None
                    or source.resolved_type is None
                    or info.elem_cells is not None):
                continue
            mine = info.resolved_type.get_element_type()
            theirs = source.resolved_type.get_element_type()
            if (mine is None or theirs is None
                    or not _numeric_leaf_differs(mine, theirs)):
                continue
            decided = mine if info.coerced_element_type is not None else theirs
            raise self.ctx.error(
                f"'{info.variable_name}' and '{source.variable_name}' name one "
                f"list, but it holds {theirs} elements through "
                f"'{source.variable_name}' and {mine} through "
                f"'{info.variable_name}'; annotate its first binding: "
                f"{source.variable_name}: list[{decided}] = ...",
                info.expr)

    def _rows_first(self) -> list[int]:
        """The function's list records in resolution order: a list whose
        element cells name rows after those rows, which a store may have
        created after the list (`g.append([v])`, an empty list seeded by
        a row), since the list embeds their resolved type."""
        order: list[int] = []
        done: set[int] = set()

        def visit(lid: int) -> None:
            if lid in done:
                return
            done.add(lid)
            info = self.ctx.list_literal(lid)
            if info is not None and info.elem_cells is not None:
                for row in _row_ids(info.element_type):
                    visit(row)
            order.append(lid)

        own = [lid for lid in self.ctx.func.pending_resolutions
               if self.ctx.list_literal(lid) is not None]
        for lid in own:
            visit(lid)
        mine = set(own)
        return [lid for lid in order if lid in mine]

    @staticmethod
    def _container_form(info: ListLiteralInfo) -> '_Form':
        """The container list literal `info` resolves to: its annotation; a
        `list` when it is mutated, a ternary branch beside another list
        (whose Array size may differ), passed as a `list`, or a global
        (another module may mutate it); a variable-count repeat a `list`
        where it needs contiguous memory (a repeat passed as a Span) or
        `operator[]`, else a lazy repeat; else an Array."""
        if info.has_explicit_annotation and info.explicit_type:
            return _Form.ANNOTATED
        if (info.is_mutated or info.needs_list_type
                or info.passed_to_list_param or info.is_global):
            return _Form.LIST
        if info.size < 0:
            if ((info.passed_to_span_param
                 and isinstance(info.expr, TpyListRepeat))
                    or info.needs_indexing):
                return _Form.LIST
            return _Form.REPEAT
        return _Form.ARRAY

    def _close_row_groups(self) -> None:
        """The rows at one position of one list are one C++ type
        (`ListLiteralInfo.row_group`): a requirement for a vector on one of
        them -- a mutation, a list context, a different size, an alias
        that needs one -- is one on every row of the group and every name
        for them. Decided before any record resolves, so the list that
        holds them embeds the type all its rows get."""
        lits = {lid: rec for lid in self.ctx.func.pending_resolutions
                if (rec := self.ctx.list_literal(lid)) is not None}
        groups: dict[int, list[ListLiteralInfo]] = {}
        for rec in lits.values():
            if rec.row_group is not None:
                groups[id(rec.row_group)] = [
                    r for g in rec.row_group
                    if (r := self.ctx.list_literal(g)) is not None]
        if not groups:
            return
        changed = True
        while changed:
            changed = False
            for recs in groups.values():
                members = {r.literal_id for r in recs}
                names = [lits[lid] for lid in self.ctx.func.pending_resolutions
                         if lid in lits and lid not in members
                         and lits[lid].source_literal_id in members]
                allof = [r for r in recs + names
                         if not r.has_explicit_annotation]
                if not (any(self._container_form(r) is not _Form.ARRAY
                            for r in allof)
                        or len({r.size for r in recs}) > 1):
                    continue
                for r in allof:
                    if not r.is_mutated:
                        r.is_mutated = True
                        changed = True

    def _check_elem_reads(self, info: 'ListLiteralInfo',
                          elem_type: TpyType) -> None:
        """Refuse a read compiled at an element type the list did not
        resolve to (`pending_elem_read`). A use the read could not see
        decided the element, so the value would be converted silently."""
        default_int = self.ctx.default_int_for_literal
        for seen, node in info.elem_reads:
            if not _stale_literal_leaf(seen, elem_type, default_int):
                continue
            assumed = resolve_int_literals(seen, default_int)
            name = info.variable_name
            subject = f"'{name}'" if name else "this list"
            fix = (f"; annotate its first binding: {name}: list[{elem_type}] = ..."
                   if name else "")
            raise self.ctx.error(
                f"{subject} holds {elem_type} elements, decided by another "
                f"use of the list, but this read takes one as {assumed}{fix}",
                node)

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
    # Dict and set resolution
    # ------------------------------------------------------------------

    def _resolve_pending_dict_and_set_types(self) -> None:
        """Resolve all pending dict and set types after function analysis.

        Dict and set share the same resolution logic: check for unresolved UNKNOWN
        element types (error), resolve IntLiteralType defaults, then apply resolution.
        Merged into a single method to guarantee identical handling.
        """
        # Process dicts
        for literal_id in self.ctx.func.pending_resolutions:
            info = self.ctx.container_record(literal_id)
            if not isinstance(info, DictLiteralInfo):
                continue
            if info.elem_cells is not None:
                # Its cells decided its leaves.
                self._apply_container_resolution(
                    info, self._deep_resolve_pending(self.pend.declared_form(
                        self.pend.finalize(info.pending_type()))))
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
        for literal_id in self.ctx.func.pending_resolutions:
            info = self.ctx.container_record(literal_id)
            if not isinstance(info, SetLiteralInfo):
                continue
            if info.elem_cells is not None:
                self._apply_container_resolution(
                    info, self._deep_resolve_pending(self.pend.declared_form(
                        self.pend.finalize(info.pending_type()))))
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
    # String variable deduction (moved from StrVarTracker)
    # ------------------------------------------------------------------

    def is_view_compatible_source(self, init_expr: TpyExpr, init_type: TpyType) -> bool:
        """Whether an inferred str/bytes local bound to `init_expr` may be a
        VIEW (no owned copy). The one rule, the same for both families: every
        source leaf (`view_leaves`; each select operand of the view's family
        and not pending) is
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
          operand it may point into (`ViewLend`) is itself a lending leaf.
        A record field, a container element and a loop variable's buffer
        bind OWNED storage: nothing tracks a write through those while the
        view lives.
        """
        is_str = _view_family_is_str(init_type)
        if is_str is None:
            return False
        for leaf in view_leaves(init_expr, ViewLend(self.ctx)):
            leaf_str = is_str
            if leaf.arm is not None:
                # A pending operand goes through the chained-pending branch
                # of `_infer_new_local_type` instead.
                arm_t = self.ctx.get_expr_type(leaf.arm)
                if isinstance(arm_t, PendingViewType):
                    return False
                leaf_str = _view_family_is_str(arm_t)
                if leaf_str is None:
                    return False
            if not self.leaf_lends_view(
                    leaf, lambda n, s=leaf_str: self._name_lends_view(n, s)):
                return False
        return True

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

    def leaf_lends_view(self, leaf: Leaf,
                        top_name: 'Callable[[str], bool] | None' = None
                        ) -> bool:
        """One leaf of an inferred view's source (see
        `is_view_compatible_source`): each call on its way is the operand
        itself, and it ends at static storage, at a view call with nothing
        to read, or at a NAME, bare or under one subscript, that lends a
        buffer (`name_lends_buffer`, or `top_name` for the value itself);
        a slice of a `str` literal is static. The binding question, not
        the return one: a local root the view-escape check calls dangling
        AT A RETURN outlives a local bound in the same body. A temporary
        operand (an owned call result, a concat, an f-string) fails
        closed."""
        *calls, last = leaf.stretches()
        if any(st.skip() < len(st.steps) for st in calls):
            return False
        i = last.skip()
        if i == len(last.steps):
            if leaf.kind in (LeafKind.STATIC, LeafKind.CONSTANT,
                             LeafKind.UNNAMED):
                return True
            if leaf.kind is LeafKind.PLACE:
                rule = (top_name if not calls and top_name is not None
                        else self.name_lends_buffer)
                return rule(leaf.name)
            return False
        if i == len(last.steps) - 1 and last.steps[i].kind is StepKind.INDEX:
            if leaf.kind is LeafKind.PLACE:
                return self.name_lends_buffer(leaf.name)
            return (isinstance(leaf.node, TpyStrLiteral)
                    and isinstance(last.steps[i].node.index, TpySlice))
        return False

    def mark_view_non_lending(self, family: ViewTypeFamily, var_id: int) -> None:
        """A loop variable or loop-head unpack target: it may stay a view of
        the current step, but never lends one to a local."""
        info = self.ctx.view_vars(family).get(var_id)
        if info is not None:
            info.lends_to_aliases = False

    # --- View-type local tracking (generic across str/bytes families) ---

    def _is_static_view_leaf(self, leaf: Leaf) -> bool:
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
        `_frame_resident_slice`. Only the value's own leaf counts: a view
        call over static storage, and a CONSTANT (a `Final` view off a
        module or class qualifier), are not counted static here."""
        if leaf.path:
            return (leaf.kind is LeafKind.PLACE and len(leaf.path) == 1
                    and leaf.path[0].kind is StepKind.INDEX
                    and self._frame_resident_slice(leaf.path[0].node))
        if leaf.kind is LeafKind.STATIC:
            return True
        if leaf.kind is not LeafKind.PLACE:
            return False
        func = self.ctx.func.current_function
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                if pname == leaf.name:
                    ptype = unwrap_readonly(ptype)
                    if is_str_view_type(ptype) or is_bytes_view_type(ptype):
                        return True
                    break
        scope_type = (self.ctx.func.current_scope.lookup(leaf.name)
                      if self.ctx.func.current_scope else None)
        if scope_type is not None:
            scope_type = unwrap_readonly(scope_type)
            if isinstance(scope_type, PendingViewType):
                return True
            if isinstance(scope_type, LiteralType) and scope_type.is_str_base():
                return True
        return leaf.name in self.ctx.final_globals

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
        return any(not self._is_static_view_leaf(leaf)
                   for leaf in view_leaves(expr, ViewLend(self.ctx)))

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
                                     storage: StorageKey) -> None:
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
        root = storage.root
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
        view `var_id`: per leaf (`view_leaves`), its key
        (`_view_leaf_source_key`). A view of a parameter reads the caller's
        buffer until the body rebinds the name; another local the value
        names is the alias pass's (`source_var_ids`)."""
        bt = self.ctx.func.borrow_tracker
        func = self.ctx.func.current_function
        params = ({p for p, _ in func.params}
                  if isinstance(func, TpyFunction) else set())
        for leaf in view_leaves(expr, ViewLend(self.ctx)):
            key = _view_leaf_source_key(leaf, params)
            if key is None:
                continue
            name = key.as_name()
            if name is not None:
                key = bt.effective_storage(name)
            self.register_view_source_storage(family, var_id,
                                              bt.canonical_key(key))

    def tuple_target_view_family(self, name: str) -> ViewTypeFamily | None:
        """The pending str/bytes view family of tuple-unpack target `name`, or
        None if it is not a pending-view local."""
        bound = self.ctx.func.current_scope.lookup(name)
        inner = (unwrap_own(unwrap_ref_type(unwrap_readonly(bound)))
                 if bound is not None else None)
        return inner.family if isinstance(inner, PendingViewType) else None

    def view_hoist_roots(self, expr: TpyExpr) -> 'set[str] | None':
        """The storage roots a view bound to `expr` reads, resolved as of this
        binding (`_resolve_hoist_root`), or None when some leaf
        (`view_leaves`) has no root the hoist rule can place: a field or
        element of a call's result, a temporary, a view call that may read
        no operand, an unplaceable leaf. Static leaves contribute nothing; a
        call's result may borrow its receiver or any operand that can lend
        storage, so all of those count."""
        roots: set[str] = set()
        for leaf in view_leaves(expr, ViewLend(self.ctx)):
            *calls, last = leaf.stretches()
            if any(st.steps and st.steps[-1].kind in (StepKind.FIELD,
                                                      StepKind.INDEX)
                   for st in (calls + [last] if leaf.at_call else calls)):
                return None
            if leaf.kind in (LeafKind.STATIC, LeafKind.CONSTANT):
                continue
            if leaf.kind is not LeafKind.PLACE:
                return None
            resolved = self._resolve_hoist_root(leaf.name, set())
            if resolved is None:
                return None
            roots |= resolved
        return roots

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
            sub_roots = self._resolve_hoist_root(st.root, seen)
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
        for elem, record in self.ctx.func.after_list_resolution:
            record(self._deep_resolve_pending(elem))
        self.ctx.func.after_list_resolution = []
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
        """Refresh node-held type snapshots post-resolution (a comprehension's
        element/key/value type, a loop head's or an unpack's targets).

        A snapshot field (result_elem_type etc.) cached before resolution may
        hold a Pending* container type (bare or nested in a composite) whose
        resolved type now lives on its registry info; rewrite the field so
        codegen sees the concrete type."""
        for node, attr in self.ctx.func.pending_elem_type_fields:
            current = getattr(node, attr)
            if isinstance(current, list):
                # One type per target (an unpack's).
                current[:] = [t if t is None else self._deep_resolve_pending(t)
                              for t in current]
                continue
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

        # A read of a list literal whose element a cell decided is recorded
        # at the literal's resolved type, as a read of an annotated list is
        # recorded at its annotation: the lowering keys on the node's type
        # (the member a union slot lifts, for one).
        for node in self.ctx.func.pending_container_exprs:
            current = self.ctx.expr_types.get(node)
            if not isinstance(current, PendingContainerType):
                continue
            info = self.ctx.container_record(current.literal_id)
            unknown = any(isinstance(p, UnknownElementType)
                          for p in current.parts())
            # A container no cell decides keeps the type its own
            # resolution gives every read.
            if unknown and (info is None or info.elem_cells is None):
                continue
            if info is not None and info.resolved_type is not None:
                self.ctx.expr_types[node] = info.resolved_type
            elif info is not None and info.part_of is not None:
                self.ctx.expr_types[node] = self._deep_resolve_pending(
                    self.pend.finalize(current))
            elif contains_pending_num(current):
                self.ctx.expr_types[node] = self.pend.finalize(current)
            elif unknown:
                # A read recorded before its empty list's cell was born has
                # no pending number to finalize: only the literal's own
                # resolution gives it an element.
                raise AssertionError(
                    f"Internal error: list literal "
                    f"'{info.variable_name or info.literal_id}' has an "
                    f"element cell but no resolved type at finalization"
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
        if isinstance(typ, (PendingDictType, PendingSetType)):
            rec = self.ctx.container_record(typ.literal_id)
            if rec is not None and rec.part_of is not None:
                # A dict or set inside another container literal is the
                # container its parts spell; its cells are the outer one's.
                return typ.spelled(tuple(
                    self._deep_resolve_pending(p, settle_views=settle_views)
                    for p in typ.parts()))
        return typ.map_inner_types(
            lambda t: self._deep_resolve_pending(t, settle_views=settle_views))

    def _resolved_container_type(self, typ: TpyType) -> TpyType | None:
        """The registry-resolved type for a Pending* container, else None."""
        if not isinstance(typ, PendingContainerType):
            return None
        info = self.ctx.container_record(typ.literal_id)
        return info.resolved_type if info is not None else None
