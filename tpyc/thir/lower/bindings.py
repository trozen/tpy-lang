"""The per-function binding table: one record per C++ binding a body emits.

`plan_bindings` walks a body before it is lowered, with the lowering's block
scoping, and decides for every binding -- a parameter, a seeded global, a
resumable frame field, a local declaration site, a loop / with / except /
match / walrus / comprehension / lambda target -- how its C++ slot holds the
value (`BindingRepr`), one record per binding.

Every lowered body plans its table before its walk (`plan_and_install`)
and reads every per-name representation fact the table models from it:
`_LowerCtx.binding(name)` is the record where the walk is (`enter` / `at`
/ `scoped` move the walk's cursor into every scope the planner keyed), a
NAME read's held is `project_held` of it; the arm that declares a name
reads its own record through `_LowerCtx.declaring`.
Nothing in the walk writes a record. The facts still kept outside the
table are listed in TODO.md ("STILL OUTSIDE THE TABLE").
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Set as AbstractSet
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, NamedTuple

from ...codegen_cpp import resumable_cfg as rcfg
from ...codegen_cpp.gen_async import collect_frame_nested_defs
from ...codegen_cpp.forms import (LocalBinding, LoopBinding,
                                  is_plain_nonvalue, loop_binding_kind,
                                  is_ptr_variant_union,
                                  is_storage_tuple_alias_decl,
                                  reads_storage_form_optional)
from ...codegen_cpp.type_resolution import resolve_stmt_binding_type
from ...identity_map import IdentityMap, IdentitySet
from ...modules.type_resolution import is_native_iterable
from ...parse.nodes import (RebindStorage, ResultForm, TpyArrayLiteral,
                            TpyAsPattern,
                            TpyAssign, TpyAwait, TpyCall, TpyCapturePattern,
                            TpyClassPattern, TpyCoerce,
                            TpyComprehensionGenerator, TpyDictComprehension,
                            TpyDictLiteral, TpyExpr, TpyFieldAccess,
                            TpyForEach, TpyFunction, TpyGeneratorExpression,
                            TpyIf, TpyIfExpr, TpyLambda, TpyListComprehension,
                            TpyListRepeat, TpyLiteralPattern, TpyMatch,
                            TpyMatchCase, TpyMethodCall, TpyName, TpyNestedDef,
                            TpyNoneLiteral, TpyOrPattern, TpySetComprehension,
                            TpySetLiteral, TpySlice, TpyStmt, TpySubscript,
                            TpyTry, TpyTupleLiteral, TpyTupleUnpack,
                            TpyVarDecl, TpyWhile, TpyWildcardPattern, TpyWith,
                            TpyWithItem,
                            TpyYield, TupleElemCapture,
                            iter_capture_bindings, program_names,
                            walk_body_stmts, walk_expr_tree, walrus_bindings)
from ...prescan import scope_bound_names
from ...sema.alias_rebind import (final_pointer_tuple_names,
                                  pointer_tuple_owned, tuple_name_bindings)
from ...sema.context import receiver_root
from ...sema.own_copy import contains_reference_type
from ...type_def_registry import (is_array, is_bytes_view_type,
                                  is_str_view_type)
from ...typesys import (ConcreteGenType, FloatLiteralType, IntLiteralType,
                        NominalType, OptionalType, OwnType, PendingListType,
                        PendingViewType, ReadonlyType, TpyType, TupleType,
                        RecursiveAliasInstanceType, TypeParamRef, UnionType,
                        collapse_tuple_own_elements,
                        contains_pending_leaf, is_dyn_protocol,
                        is_own_pointer_repr_optional,
                        pointer_repr_optional, resolve_int_literals,
                        unwrap_optional_own, unwrap_own, unwrap_readonly,
                        unwrap_ref_type, unwrap_send_sync, ValueForm)
from ...value_category import (call_hands_back_value, call_returns_cpp_ref,
                               is_rvalue_source)
from ..nodes import Form
from ..reject import ThirUnsupported, note_detail, stmt_reject_reason
from ..source import UNBOUND, BindingRepr, Held, SlotKey, slot_key
from ..source import BindingKind as K
from ..source import BindingRefinement as R
from . import checks as _checks
from . import predicates as _p
from .context import (BindingTableError,
                      FrameLocalClass, ValueOptKind, _LowerCtx,
                      _without_names,
                      binding_source_sites, names_with, nested_def_prescan,
                      prescan_without)
from .predicates import (_bytes_name_form, _is_borrow_form_name,
                         _is_string_owned, _nullable_static_protocol_param,
                         _optional_ptr_borrow_wide, _own_opt_storage_binding,
                         _protocol_auto_slot,
                         _resolved_bytes_value, _resolved_str_value,
                         _str_name_form, _unwrap_own,
                         _value_opt_owned_view, _value_opt_record,
                         _value_opt_scalar, _value_opt_string_owned,
                         _value_opt_tuple, _value_opt_view, _value_record_slot,
                         _var_decl_type)
from .storage import tuple_layout

if TYPE_CHECKING:
    from .resumable import FrameLoopFacts
# statements, expressions, comprehensions, match and resumable import this
# module (they lower under the table it plans), so the planner imports what
# it reads of them inside the reading function.

# The shadow collector, a hook for the review script: None leaves every
# check off; `scripts/thir_migration/review/binding_shadow.py` sets a list
# here and reads the entries the lowering appends to it.
SHADOW_SINK: 'list | None' = None


# The record's per-name representation facts (the fields a loop variable
# inherits from the outer binding it shadows).
REPR_FIELDS: tuple[str, ...] = (
    "pointer", "rebind_slot", "ref_alias", "ptr_variant", "value_opt",
    "storage_opt", "const_storage_opt", "storage_tuple",
    "const_storage_tuple", "borrow_tuple_mixed", "optional_borrow_tuple",
    "tuple_layout", "inline_elems", "const", "const_loop_var",
    "const_borrow_tuple", "const_opt_borrow_tuple", "movable", "owned",
    "optional_storage", "coro_frame", "opt_storage_call",
    "iterator_object",
)


@dataclass(frozen=True, eq=False)
class ScopeNode:
    """One immutable link of a body's scope chain: the bindings a block
    (or a statement in it) adds, over its parent. `shadow` holds the names
    an inner FUNCTION scope (a nested def, a lambda) owns: a lookup that
    reaches such a node without finding the name stops there, as Python
    scoping does, instead of reading the enclosing binding."""
    parent: 'ScopeNode | None'
    bindings: Mapping[str, BindingRepr] = field(default_factory=dict)
    shadow: frozenset[str] = frozenset()

    def lookup(self, name: str) -> BindingRepr | None:
        node: ScopeNode | None = self
        while node is not None:
            b = node.bindings.get(name)
            if b is not None:
                return b
            if name in node.shadow:
                return None
            node = node.parent
        return None

    def lookup_before(self, name: str, site: object) -> BindingRepr | None:
        """`lookup`, passing over the records `site` declares (a statement's
        head hoists sit in the node in effect at the statement)."""
        node: ScopeNode | None = self
        while node is not None:
            b = node.bindings.get(name)
            if b is not None and (site is None or b.site is not site):
                return b
            if name in node.shadow:
                return None
            node = node.parent
        return None

    def shadowed(self, name: str) -> BindingRepr | None:
        """The binding the visible `name` shadows: the next one outward."""
        node: ScopeNode | None = self
        found = False
        while node is not None:
            b = node.bindings.get(name)
            if b is not None:
                if found:
                    return b
                found = True
            node = node.parent
        return None

    def child(self, bindings: Mapping[str, BindingRepr]) -> 'ScopeNode':
        return ScopeNode(self, dict(bindings)) if bindings else self


class BindingTable:
    """The records of one body, keyed by declaration site, and the scope
    node in effect at every statement and scoping expression."""
    __slots__ = ("root", "scope_at", "sites", "stmt_expr_scope", "entered",
                 "frame_members")

    def __init__(self, root: ScopeNode) -> None:
        self.root = root
        # The root's resumable-frame records, by name: the root also holds
        # every seeded module global, too many to scan per question.
        self.frame_members: tuple[str, ...] = ()
        self.scope_at: IdentityMap = IdentityMap()
        self.sites: IdentityMap = IdentityMap()
        # A statement's own expressions (a condition, an iterable, a return
        # value) -> the statement's node: a resumable body lowers some of
        # them apart from their statement (a CFG terminator, a loop setup).
        self.stmt_expr_scope: IdentityMap = IdentityMap()
        # The keys the walk entered, kept only under the shadow collector
        # (`report_unentered`).
        self.entered: IdentitySet | None = None

    def lambda_scope(self, lam: TpyLambda, parent: ScopeNode) -> ScopeNode:
        """The scope of a lambda the walk never met (one a lowering arm
        builds in place of a callable name): its params over `parent`."""
        node = self.scope_at.get(lam)
        if node is None:
            types = list(lam.inferred_param_types)
            node = ScopeNode(parent, {
                n: BindingRepr(name=n, site=lam,
                               type=types[i] if i < len(types) else None,
                               kind=K.LAMBDA_PLAIN, site_kind="lambda",
                               is_param=True,
                               param_type=types[i] if i < len(types)
                               else None)
                for i, n in enumerate(lam.param_names)},
                frozenset(lam.param_names))
            self.scope_at[lam] = node
        return node

    def visible(self, at: object, name: str) -> BindingRepr | None:
        node = self.scope_at.get(at)
        return node.lookup(name) if node is not None else None

    def declared_by(self, site: object, name: str) -> BindingRepr | None:
        """The one record `site` declares for `name`, or None; a site with
        two records of one name is a planner defect, never first-wins."""
        found = [rec for rec in self.sites.get(site, ()) if rec.name == name]
        if len(found) > 1:
            raise BindingTableError(
                f"{len(found)} records for {name!r} at one site "
                f"({type(site).__name__} {loc_of(site)})")
        return found[0] if found else None

    def records(self) -> Iterable[BindingRepr]:
        seen: set[int] = set()
        node: ScopeNode | None
        for node in [self.root]:
            for rec in node.bindings.values():
                if id(rec) not in seen:
                    seen.add(id(rec))
                    yield rec
        for site in self.sites:
            for rec in self.sites[site]:
                if id(rec) not in seen:
                    seen.add(id(rec))
                    yield rec


@dataclass(frozen=True)
class FrameSeed:
    """A resumable body's frame: its fields and the layout plan that placed
    them (`lower_resumable`); the planner classifies each field itself
    (`_frame_facts`)."""
    fields: frozenset[str]
    layout: Mapping[str, object]
    # The fields the frame setup declares before the walk, in its order:
    # (name, declared type, kind) -- a field's first initializing decl in
    # CFG block order ('first_decl'), a top-level unpack or `with` target
    # ('hoist') and a suspending loop's variable ('loop_var'); never an
    # await-bind target, whatever its type.
    setup_decls: tuple[tuple[str, TpyType, str], ...] = ()
    # The names the walk starts with in `declared` (a hoist over a field
    # outside it gives the field its value-opt kind at the hoist), and the
    # frame's slot types (`lc.frame_local_types`).
    entry_declared: frozenset[str] = frozenset()
    local_types: Mapping[str, TpyType] = field(default_factory=dict)
    # Each hoisted local's class (`frame_local_class`, which the resumable
    # admission read first), the skeleton's loop families, and the names
    # the frame binds to const storage.
    classes: Mapping[str, FrameLocalClass] = field(default_factory=dict)
    loops: 'FrameLoopFacts | None' = None
    const_bindings: frozenset[str] = frozenset()


@dataclass
class PlanInputs:
    """What the planner reads besides the body: the lowering context's
    pre-walk facts (no walk state)."""
    func: TpyFunction
    analyzer: object
    prescan: object
    params: tuple
    # The lowering's `declared` at entry: params, receiver, seeded globals,
    # stub-default locals, frame fields.
    seeds: Mapping[str, TpyType]
    self_receiver: str | None = None
    record_name: str | None = None
    readonly_self: bool = False
    top_level: bool = False
    module_globals: frozenset[str] = frozenset()
    frame: FrameSeed | None = None
    sema_movable: frozenset[str] = frozenset()
    # The borrow-tuple const fixpoint's (plain, nullable) names a planning
    # round reads; empty on the first round.
    btuple_const: tuple = (frozenset(), frozenset())
    # The pointer slots every source bound into which is a const borrow
    # (`_plan_const`), as the previous planning round left them.
    const_slots: frozenset = frozenset()
    # A constant initializer's scope (`lower_constant`): the seeds are the
    # constants it may read, bound plain.
    const_scope: bool = False
    # The frame fields' classes, derived once by `plan_bindings`.
    frame_facts: '_FrameFacts | None' = None
    # An @overload specialization's stub narrowing and literal facts: the
    # lowering folds an `if` they decide statically.
    overload_narrowing: 'Mapping[str, TpyType] | None' = None
    overload_literal_facts: Mapping[str, TpyType] = field(
        default_factory=dict)


@dataclass(frozen=True)
class _FrameFacts:
    """A resumable frame's fields as the planner classifies them: each
    hoisted local's class off its layout kind (`frame_local_class`), the
    loop families of the skeleton's for-prescan, the pointer fields that
    borrow their pointee, and the fields a sink may move from."""
    classes: Mapping[str, FrameLocalClass]
    loops: 'FrameLoopFacts'
    borrowed: frozenset[str]
    movable: frozenset[str]


def _frame_facts(inp: PlanInputs) -> _FrameFacts:
    from . import resumable as _res
    func = inp.func
    classes, loops = inp.frame.classes, inp.frame.loops
    # A frame-promoted STORAGE slot is movable with no value-type filter: a
    # last-use read moves out of the slot rather than copying it, which is
    # how a BigInt frame local moves at an async return. A pointer-form
    # local is movable only when the frame owns its pointee (the
    # Optional-ptr local's `__ptr_slot_fN` storage, as the plain body's
    # OPT_PTR_SLOT decl row); loop-var, alias and unpack pointers borrow.
    # Only names a `TpyVarDecl` binds: a frame-promoted loop variable is
    # movable only off a consuming iterable (the loop row), and a value
    # record copies wherever it lives.
    borrowed = frozenset(loops.ptr_loop_vars | loops.unpack_ptr_targets
                         | {n for n, c in classes.items()
                            if c is FrameLocalClass.ALIAS_PTR})
    decl_names = _res._var_decl_names(list(func.body))
    movable = frozenset(
        n for n, t in (func.generator_locals or [])
        if n not in borrowed and n in decl_names
        and not _value_record_slot(t) and n in inp.sema_movable)
    return _FrameFacts(classes, loops, borrowed, movable)


def _uw(t):
    return unwrap_readonly(unwrap_send_sync(t)) if isinstance(t, TpyType) else t


# --- held projection -----------------------------------------------------

def _value_opt_kind(rec: BindingRepr | None, analyzer
                    ) -> ValueOptKind | None:
    if rec is None:
        return None
    if rec.value_opt is not None:
        return rec.value_opt
    if rec.param_type is not None:
        t = rec.param_type
        if _value_opt_scalar(t, analyzer) is not None:
            return ValueOptKind.SCALAR
        if _value_opt_view(t, analyzer) is not None:
            return ValueOptKind.VIEW
        if _value_opt_record(t) is not None:
            return ValueOptKind.RECORD
    return None


def project_held(rec: BindingRepr | None, rtype: TpyType | None,
                 deref: bool, analyzer, param_names, owned_viewfam_params, *,
                 name: str | None = None, renamed: bool = False) -> Held:
    """How a read of the binding is held, from the record: read whole or
    through its narrowed unwrap. `rtype` is the read's type (a narrowed
    read's is narrower than the record's). The one projection: the name
    arm's `form` and the stamp's `held` both come from it.

    `renamed` is a read the arm spelled as something other than the
    binding (a narrowing alias, a cast pointer, an imported global's fixed
    spelling): it is held as its type. A name with no record (one an arm
    synthesized) reads the type rows under `name`."""
    if renamed:
        return Held.BORROWED if _is_borrow_form_name(rtype) else Held.VALUE
    if rec is not None:
        name = rec.name
    bt = rec.type if rec is not None else None
    kind = _value_opt_kind(rec, analyzer)
    if ((rec is not None and rec.optional_borrow_tuple)
            or _value_opt_tuple(bt, analyzer) is not None
            or kind is ValueOptKind.RECORD
            or _value_opt_record(bt) is not None):
        # The whole optional is its storage; the narrowed unwrap reads
        # through it.
        return Held.BORROWED if deref else Held.STORAGE
    if rec is not None and rec.storage_opt:
        return Held.STORAGE
    if rec is not None and rec.ptr_tuple is not None:
        # Every element a pointer, whatever `Own` the declared type carries.
        return Held.BORROWED
    if kind is ValueOptKind.SCALAR:
        return Held.VALUE
    if kind is ValueOptKind.VIEW:
        if not deref:
            return Held.VALUE
        # A param's unwrap is a view; a local's is the owned buffer, and so
        # is an `Optional[String]` param's (`std::optional<std::string>` by
        # value at the param too).
        if name in param_names and _value_opt_string_owned(bt) is None:
            return Held.BORROWED
        return Held.STORAGE
    # The view / owned axis of a str or bytes read follows the READ's type,
    # while pointer-ness, storage and const follow the record. The two
    # disagree where sema resolves a pending str read through an earlier
    # binding while the declaration fell back to owned `str`
    # (BUGS.md#str-rebind-after-block-local-owns); the view-family TypeDef
    # fact is what makes the record's type and the read's type agree.
    str_t = _resolved_str_value(rtype, analyzer)
    if str_t is not None:
        return _held_of(_str_name_form(name, str_t, param_names,
                                       owned_viewfam_params), Form)
    bytes_t = _resolved_bytes_value(rtype, analyzer)
    if bytes_t is not None:
        return _held_of(_bytes_name_form(name, bytes_t, param_names,
                                         owned_viewfam_params), Form)
    if _is_string_owned(rtype) or (rec is not None and (rec.storage_tuple
                                                        or rec.owned)):
        return Held.STORAGE
    if rec is not None and rec.borrow_tuple_mixed:
        return Held.BORROWED
    return Held.BORROWED if _is_borrow_form_name(rtype) else Held.VALUE


def form_of(held: Held):
    """The `Form` a read held as `held` renders in (`Held`'s mapping)."""
    if held is Held.BORROWED:
        return Form.BORROW
    if held in (Held.STORAGE, Held.FRESH):
        return Form.STORAGE
    return Form.VALUE


def read_type(t: TpyType | None) -> TpyType | None:
    """The type a read of a binding declared `t` has: ownership is the
    binding's fact, so an `Own[T]` binding reads as `T` (sema types the
    read so)."""
    if isinstance(t, OwnType):
        return t.wrapped
    return t


def _held_of(form, Form) -> Held:
    if form is Form.BORROW:
        return Held.BORROWED
    if form is Form.STORAGE:
        return Held.STORAGE
    return Held.VALUE


# --- the rebound pointer tuple ---------------------------------------------

def bare_ptr_element(et: TpyType) -> bool:
    """An element whose borrow form is a bare non-null `T*`: the scalar
    reference local's shape, which an owned element of the pointer tuple
    takes. A pointer-repr Optional, a union and a wrapper are not it."""
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(unwrap_own(et))))
    return (bare.value_form() is ValueForm.BORROW_REF
            and not bare.is_value_type()
            and TupleType._element_is_pointer_repr(bare))


def ptr_tuple_element_message(name: str, index: int, et: TpyType) -> str:
    """The sentence for a rebound tuple whose owned element is no plain
    reference: such an element has no reference local of its own yet."""
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(unwrap_own(et))))
    kind = ("an Optional" if isinstance(bare, OptionalType)
            else "a union" if isinstance(bare, UnionType)
            else "a generic type parameter" if isinstance(bare, TypeParamRef)
            else "a nested tuple" if isinstance(bare, TupleType)
            else f"a {bare}")
    return (f"rebinding '{name}' while it owns element {index}, {kind}, is "
            f"not yet supported here; bind the new value to a name of its "
            f"own")


def pointer_tuple_of(t: TpyType) -> 'TupleType | None':
    """The pointer tuple a tuple type is held as when its elements' ownership
    is per binding: each reference element a pointer whatever `Own` the
    declared type carries; None for a type that is no such tuple."""
    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(bt, TupleType):
        return None
    bt = TupleType(tuple(et.wrapped if isinstance(et, OwnType) else et
                         for et in bt.element_types))
    if contains_pending_leaf(bt) or any(
            isinstance(x, (PendingViewType, IntLiteralType, FloatLiteralType))
            for x in bt.inner_types()):
        return None
    return bt


class PtrTupleRefusal(NamedTuple):
    """Why C++ cannot spell a pointer tuple: the binding refused, the
    reject detail and its sentence (None: the generic one)."""
    at: TpyStmt
    detail: str
    message: str | None = None


class PtrTupleDecision(NamedTuple):
    """A pointer tuple's form (`ptr_tuple_decision`): the pointer tuple its
    declared type is held as (None: no render), and the refusal raised for
    it when C++ cannot spell it."""
    form: 'TupleType | None'
    refusal: PtrTupleRefusal | None


def ptr_tuple_decision(analyzer, fn: TpyFunction,
                       name: str) -> 'PtrTupleDecision | None':
    """The one answer to "is `name` a pointer tuple of `fn`, and in what
    form": None when sema does not name it one (`pointer_tuple_names`).
    Sema's facts alone decide it, so it is kept per function: the binding
    planner of a plain body and of a frame, and the frame's field layout
    before either, read the same one."""
    memo = analyzer.ptr_tuple_decisions.get(fn)
    if memo is None:
        memo = analyzer.ptr_tuple_decisions[fn] = {}
    if name not in memo:
        memo[name] = _decide_ptr_tuple_form(name, analyzer, fn)
    return memo[name]


def ptr_tuple_form(name: str, inp: 'PlanInputs',
                   fn: TpyFunction) -> 'TupleType | None':
    """The pointer tuple a local of `fn` is at every binding when sema names
    it one (`ptr_tuple_decision`), else None; raises the refusal of one C++
    cannot spell."""
    if inp.top_level:
        return None
    got = ptr_tuple_decision(inp.analyzer, fn, name)
    if got is None:
        return None
    r = got.refusal
    if r is not None:
        # Noted at the raise: the detail rides the statement being gated.
        note_detail(r.detail)
        raise ThirUnsupported(stmt_reject_reason(r.at), message=r.message,
                              loc=getattr(r.at, "loc", None))
    return got.form


def ptr_tuple_elem_storage(verdict: 'tuple | None', i: int, et: TpyType, *,
                           first: bool, const: bool
                           ) -> 'RebindStorage | None':
    """Where an owned element `i` (type `et`) of a pointer tuple's binding
    puts its object: None at the first binding (the declaration's own
    slot), else sema's per-element verdict (`tuple_elem_storage`), OWN
    where it has none. A const element pointer -- a `readonly` element, or
    a binding whose element pointers are const (`const`) -- has no
    in-place write: like the scalar's const local, its object takes a slot
    of its own. The plain body's lowering and the frame's slot prescan
    read this one answer."""
    if first:
        return None
    if const or isinstance(unwrap_send_sync(unwrap_ref_type(et)),
                           ReadonlyType):
        return RebindStorage.OWN
    v = verdict[i] if verdict is not None and i < len(verdict) else None
    return v if v is not None else RebindStorage.OWN


def ptr_tuple_route_message(name: str, what: str) -> str:
    """The sentence for a binding route of a pointer tuple that has no
    per-element placement yet."""
    return (f"binding '{name}' to {what} is not yet supported here: "
            f"'{name}' keeps objects of its own element by element, and "
            f"this binding does not place them one by one")


def _decide_ptr_tuple_form(name: str, an,
                           fn: TpyFunction) -> 'PtrTupleDecision | None':
    """Sema's decision read as is; what is left here is whether C++ can
    spell it, refused at the first owning binding when it cannot: a
    declared type with no pointer-tuple render, an owned element whose
    borrow form is no bare pointer, a binding route that places no
    element (a direct `@error_return` call's unwrap, an await's result,
    which the resume binds whole)."""
    from . import statements as _st
    if name not in final_pointer_tuple_names(an.ctx, fn):
        return None

    def shown() -> str:
        return program_names(name, fn.body)[0] or name

    bindings = tuple_name_bindings(an.ctx, fn, name) or []
    if not bindings:
        # An enclosing body's pointer tuple a nested def reads: the
        # enclosing record carries it.
        return None
    vtype = (_var_decl_type(bindings[0], an)
             if isinstance(bindings[0], TpyVarDecl) else None)
    bt = pointer_tuple_of(vtype) if vtype is not None else None

    def refuse(at, detail: str, message: 'str | None' = None
               ) -> PtrTupleDecision:
        return PtrTupleDecision(bt, PtrTupleRefusal(at, detail, message))

    for b in bindings:
        value = b.init if isinstance(b, TpyVarDecl) else b.value
        if value is not None and _st._error_return_stmt_fi(value, an):
            return refuse(b, "ptr_tuple.error_return_bind",
                          ptr_tuple_route_message(
                              shown(), "the result of an @error_return call"))
        if isinstance(_st._peel_tpy_coerce(value), TpyAwait):
            return refuse(b, "ptr_tuple.await_bind",
                          ptr_tuple_route_message(
                              shown(), "the result of an await"))
    owned = pointer_tuple_owned(an.ctx, fn, name)
    first_owner = next((b for b in bindings
                        if any(an.ctx.tuple_elem_owned.get(b) or ())),
                       bindings[0])
    if bt is None:
        return refuse(first_owner, "ptr_tuple.declared_type")
    for i in sorted(owned):
        if i >= len(bt.element_types) or not bare_ptr_element(
                bt.element_types[i]):
            return refuse(first_owner, "ptr_tuple.owned_element_form",
                          ptr_tuple_element_message(
                              shown(), i, bt.element_types[i]))
    return PtrTupleDecision(bt, None)


# --- the classifier --------------------------------------------------------

@dataclass(frozen=True)
class _Site:
    """What a declaring site gives the classifier."""
    kind: str                 # 'param' 'global' 'frame' 'decl' 'assign'
                              # 'unpack' 'loop' 'with' 'except' 'match'
                              # 'walrus' 'comp' 'lambda' 'hoist' 'def'
                              # 'nested_param'
    node: object | None = None
    init: TpyExpr | None = None
    # Inside a branch / loop / handler body (the lowering's `in_branch`).
    in_branch: bool = False
    # The function whose scope the site is in (a nested def's own body),
    # with the facts the lowering swaps in for it.
    func: TpyFunction | None = None
    prescan: object | None = None
    sema_movable: frozenset[str] | None = None
    # Facts the walk computed for the site: a comprehension clause's route
    # (`_clause_route`), a match arm's capture facts (`_match_captures`).
    route: object | None = None
    # A comprehension clause's comprehension.
    comp: TpyExpr | None = None
    # A for head's unpack: its for statement, and the records visible at
    # it (the iteration source's scope).
    loop: TpyForEach | None = None
    loop_visible: object | None = None


def classify_binding(site: _Site, name: str, typ: TpyType | None,
                     inp: PlanInputs, visible) -> BindingRepr:
    """The ONE classifier: the record a binding gets, from its declared
    type, its initializer's shape, sema's per-function facts and the
    records `visible` resolves (the bindings declared before the site)."""
    if site.prescan is not None:
        inp = replace(inp, prescan=site.prescan,
                      sema_movable=site.sema_movable or frozenset())
    rec = None
    for row in _ROWS.get(site.kind, ()):
        rec = row(site, name, typ, inp, visible)
        if rec is not None:
            break
    else:
        rec = BindingRepr(name=name, site=site.node, type=typ,
                          kind=K.plain(site.kind), site_kind=site.kind,
                          is_param=site.kind in ("param", "lambda",
                                                 "nested_param"))
    if (inp.frame is not None and name in inp.frame.const_bindings
            and (site.func is None or site.func is inp.func)):
        rec = replace(rec, frame_const=True)
    return rec


def _param_row(site, name, typ, inp: PlanInputs, visible):
    """A parameter's record, from its declared type."""
    func, analyzer = inp.func, inp.analyzer
    f: dict = {}
    u = _uw(typ)
    ptype = next((t for n, t in inp.params if n == name), None)
    if inp.frame is not None and name in inp.frame.fields:
        f["frame_class"] = inp.frame.classes.get(name, FrameLocalClass.PLAIN)
    # The const verdicts of the function whose parameter it is; `THIRParam`
    # and every reader in the body read them off this record.
    fi = _p._owning_fi(func, analyzer, inp.record_name)
    f["const"] = _p._param_is_const_at(name, func, fi)
    f["deep_const"] = (
        _p._param_is_deep_const(name, func, analyzer, inp.record_name)
        or _p._vararg_pack_is_const(name, func)
        or _p._ptr_pointee_is_readonly(name, func))
    # A generator expression's capture of its creator's pointer tuple
    # (sema's final set of the frame) is that pointer tuple.
    ptt = (pointer_tuple_of(typ) if typ is not None and name
           in final_pointer_tuple_names(analyzer.ctx, func) else None)
    if ptt is not None:
        return BindingRepr(name=name, site=None, type=typ,
                           kind=K.PARAM_SEED, site_kind=site.kind,
                           is_param=True,
                           param_type=ptype if isinstance(ptype, TpyType)
                           else None, ptr_tuple=ptt, **f)
    if _optional_ptr_borrow_wide(typ, analyzer) is not None:
        f["pointer"] = True
    elif _nullable_static_protocol_param(typ) is not None:
        f["pointer"] = True
    elif is_own_pointer_repr_optional(u):
        f["pointer"] = True
        f["optional_storage"] = True
    if is_ptr_variant_union(u):
        f["ptr_variant"] = True
    if _own_opt_storage_binding(typ):
        f["value_opt"] = ValueOptKind.RECORD
    elif _value_opt_string_owned(typ) is not None:
        f["value_opt"] = ValueOptKind.VIEW
    if isinstance(u, OwnType):
        inner = unwrap_readonly(u.wrapped)
        if isinstance(inner, TupleType) and inner.has_pointer_repr_element():
            f["storage_tuple"] = True
    elif isinstance(u, TupleType) and u.is_owned_movable():
        f["storage_tuple"] = True
    if isinstance(typ, TpyType) and unwrap_optional_own(
            unwrap_readonly(unwrap_send_sync(typ))) is not None:
        f["owned"] = True
    owns = func.takes_ownership_of(name, typ)
    own = unwrap_optional_own(u) if isinstance(typ, TpyType) else None
    if owns and own is not None and (
            not own.wrapped.is_value_type()
            or (isinstance(unwrap_readonly(own.wrapped), TupleType)
                and contains_reference_type(own.wrapped))):
        f["movable"] = True
    if owns and isinstance(u, TupleType):
        f["movable"] = True
    if isinstance(u, TupleType) and u.is_mixed_own():
        # A capture holds whatever the enclosing variable holds: a
        # parameter, a call result and a module global all hold the mixed
        # render a mixed call result binds.
        f["borrow_tuple_mixed"] = True
    if (isinstance(u, OptionalType) and not u.uses_pointer_repr()
            and not isinstance(typ, ReadonlyType)
            and u.inner.is_expensive_copy()):
        f["movable"] = True
    return BindingRepr(name=name, site=None, type=typ, kind=K.PARAM_SEED,
                       site_kind=site.kind, is_param=True,
                       param_type=ptype if isinstance(ptype, TpyType)
                       else None, **f)


def _receiver_row(site, name, typ, inp: PlanInputs, visible):
    """The method receiver: const for a readonly method (functions.py,
    resumable.py), movable for a consuming one (context.py)."""
    if name != inp.self_receiver and not (name == "self" and inp.readonly_self):
        return None
    return BindingRepr(
        name=name, site=None, type=typ, kind=K.PARAM_RECEIVER,
        site_kind=site.kind,
        const=inp.readonly_self,
        movable=bool(name == inp.self_receiver and inp.func.is_consuming))


def _global_row(site, name, typ, inp: PlanInputs, visible):
    """The seeded globals of a function body (`_seed_global_scope`)."""
    pre, analyzer = inp.prescan, inp.analyzer
    f: dict = {"global_cpp": pre.global_cpp.get(name),
               "global_slot": name in pre.global_slots}
    if name in pre.global_slots:
        f["pointer"] = True
    if ((name in pre.global_seeded or name in pre.global_readonly)
            and _value_opt_scalar(typ, analyzer) is not None):
        f["value_opt"] = ValueOptKind.SCALAR
    return BindingRepr(name=name, site=None, type=typ, kind=K.GLOBAL_SEED,
                       site_kind=site.kind, **f)


def _module_global_row(site, name, typ, inp: PlanInputs, visible):
    """The module-init carrier's globals (`lower_top_level`)."""
    pre, analyzer = inp.prescan, inp.analyzer
    own = name in inp.module_globals
    f: dict = {"global_cpp": pre.global_cpp.get(name),
               "global_slot": name in pre.global_slots,
               "own_global": own}
    if name in pre.global_slots:
        f["pointer"] = True
    if own and typ is not None:
        if _value_opt_scalar(typ, analyzer) is not None:
            f["value_opt"] = ValueOptKind.SCALAR
        bare = _uw(unwrap_ref_type(typ))
        if isinstance(bare, TupleType) and bare.has_pointer_repr_element():
            # A tuple global is a tuple of scalar globals: each reference
            # element is the pointer slot a reference global is, whatever
            # backing a write parks (`std::tuple<int32_t, Cell*> M`).
            f["tuple_layout"] = tuple_layout(bare, analyzer, borrow=True)
    return BindingRepr(name=name, site=None, type=typ, kind=K.GLOBAL_MODULE,
                       site_kind=site.kind, **f)


def _frame_row(site, name, typ, inp: PlanInputs, visible):
    """A resumable frame field, from its class (`_frame_facts`) and the
    value-opt kind of the frame's first decl of it."""
    C = FrameLocalClass
    fr, ff = inp.frame, inp.frame_facts
    f: dict = {"frame_class": ff.classes.get(name),
               "source_typed_holder": name in ff.loops.source_typed_holders,
               "opt_ptr_loop_var": name in ff.loops.opt_ptr_loop_vars}
    cls = f["frame_class"]
    if (name in ff.loops.ptr_loop_vars or name in ff.loops.unpack_ptr_targets
            or cls in (C.OPT_PTR, C.REBIND_PTR, C.ALIAS_PTR)):
        f["pointer"] = True
    if (cls is C.OWNING_TUPLE_SLOT
            or name in ff.loops.ptr_storage_tuple_loop_vars):
        f["storage_tuple"] = True
    if cls is C.VALUE_OPT_RECORD:
        f["value_opt"] = ValueOptKind.RECORD
    else:
        kind = _frame_value_opt(name, inp)
        if kind is not None:
            f["value_opt"] = kind
    if name in ff.borrowed:
        f["frame_borrowed"] = True
    if name in ff.movable:
        f["movable"] = True
    lay = fr.layout.get(name)
    if lay is not None:
        f["effective_type"] = getattr(lay, "effective_type", None)
        f["payload"] = getattr(lay, "payload", None)
    # The pointer tuple a frame field is, as a plain body's declaration is
    # (`_btuple_rebound_decl`).
    ptt = ptr_tuple_form(name, inp, inp.func)
    if ptt is not None:
        f["ptr_tuple"] = ptt
        f["ptr_tuple_owned"] = pointer_tuple_owned(
            inp.analyzer.ctx, inp.func, name)
    return BindingRepr(name=name, site=None, type=typ, kind=K.FRAME_LAYOUT,
                       site_kind=site.kind, **f)


def _declared_value_opt_kind(vtype: TpyType, analyzer
                             ) -> 'ValueOptKind | None':
    """The value-repr Optional kind of a slot declared WITHOUT its value (a
    hoist predecl, an annotation-only decl, a frame field): a narrowed read
    derefs through it."""
    if _value_opt_scalar(vtype, analyzer) is not None:
        return ValueOptKind.SCALAR
    if _value_opt_owned_view(vtype, analyzer) is not None:
        return ValueOptKind.VIEW
    return None



def _frame_value_opt(name: str, inp: PlanInputs) -> ValueOptKind | None:
    """A frame field's value-opt kind: its parameter's, then the frame
    setup's declarations of it in order, the last one with a kind winning
    -- a first initializing decl or a loop variable of `Optional[scalar]`,
    a hoisted target of any value-repr Optional."""
    an = inp.analyzer
    kind = None
    for pname, ptype in inp.params:
        if pname != name:
            continue
        if _own_opt_storage_binding(ptype):
            kind = ValueOptKind.RECORD
        elif _value_opt_string_owned(ptype) is not None:
            kind = ValueOptKind.VIEW
    for dname, vtype, how in inp.frame.setup_decls:
        if dname != name:
            continue
        if how == "hoist":
            got = _declared_value_opt_kind(vtype, an)
        else:
            got = (ValueOptKind.SCALAR
                   if _value_opt_scalar(vtype, an) is not None else None)
        if got is not None:
            kind = got
    return kind


class _VisibleDeclared(Mapping):
    """`declared` as the visible records' types, with the names a narrowing
    retypes at the site at their narrowed type (the lowering's narrowing
    arms retype `declared` for the narrowed region; sema types the
    occurrence the same)."""

    def __init__(self, visible, narrowed: Mapping | None = None) -> None:
        self._visible = visible
        self._narrowed = narrowed or {}

    def __getitem__(self, name):
        t = self._narrowed.get(name)
        if t is not None:
            return t
        rec = self._visible(name)
        if rec is None or rec.type is None:
            raise KeyError(name)
        return rec.type

    def __contains__(self, name) -> bool:
        return isinstance(name, str) and self._visible(name) is not None

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0


class _PlanCtx:
    """The pre-walk stand-in for `_LowerCtx` that the shared lowering
    predicates read: `binding` answers from the visible records."""

    def __init__(self, inp: 'PlanInputs', visible, func, exprs=()) -> None:
        self.func = func
        self.analyzer = inp.analyzer
        self.prescan = inp.prescan
        self.record_name = inp.record_name
        self.self_receiver = inp.self_receiver
        self.frame_local_types = (
            {n: inp.seeds.get(n) for n in inp.frame.fields}
            if inp.frame is not None else {})
        self.resumable_leaf_mode = bool(func.is_generator or func.is_async)
        self.params = tuple(inp.params)
        self._visible = visible
        self.declared_view = _VisibleDeclared(
            visible, _narrowed_names(exprs, visible, inp.analyzer))
        # The whole-body const fixpoint (`_plan_const`), as the previous
        # planning round left it; the records carry it only once planning
        # ends.
        self.btuple_const = inp.btuple_const
        self.const_slots = inp.const_slots

    def binding(self, name: str) -> BindingRepr:
        rec = self._visible(name) if isinstance(name, str) else None
        return self.with_fixpoint(UNBOUND if rec is None else rec, name)

    def with_fixpoint(self, rec: BindingRepr, name: str) -> BindingRepr:
        """`rec` with the const fixpoint's bits for `name` and its slot, as
        the records carry them once planning ends (`_mark_const`)."""
        plain, nullable = self.btuple_const
        if name in plain or name in nullable:
            rec = replace(rec, const_borrow_tuple=(rec.const_borrow_tuple
                                                   or name in plain),
                          const_opt_borrow_tuple=(rec.const_opt_borrow_tuple
                                                  or name in nullable))
        if _is_slot(rec):
            const = _slot_const(rec, self.const_slots, self.analyzer)
            if const != rec.const:
                rec = replace(rec, const=const)
        return rec

    def names_with(self, fact: str):
        return names_with(self.binding, fact)

    def any_frame_slot(self) -> bool:
        return any(self.binding(n).frame_slot for n in self.frame_local_types)


def _narrowed_names(exprs, visible, analyzer) -> dict:
    """The union-typed names read in `exprs` at a narrower type, at the
    read's type: the narrowing arms retype a union's `declared` entry for
    the narrowed region, while an Optional keeps its declared type (its
    narrowed reads deref in place) as does a union narrowed by an
    assignment."""
    out: dict = {}

    def visit(x) -> bool:
        if isinstance(x, TpyName) and x.name not in out:
            rec = visible(x.name)
            t = analyzer.get_expr_type(x)
            b = _uw(rec.type) if rec is not None else None
            if (isinstance(b, UnionType) and t is not None
                    and _uw(t) != b):
                out[x.name] = t
        return True
    for e in exprs:
        if isinstance(e, TpyExpr):
            walk_expr_tree(e, visit)
    return out


# The C++ local shape a first-declaration kind spells
# (`THIRVarDecl.cpp_local_representation`).
DECL_SHAPE: dict[K, LocalBinding] = {
    K.DECL_REF_ALIAS: LocalBinding.REF_ALIAS,
    K.DECL_POINTER: LocalBinding.POINTER,
    K.DECL_REBIND_SLOT: LocalBinding.REBIND_SLOT,
    K.DECL_OPTIONAL_TO_PTR: LocalBinding.OPTIONAL_TO_PTR,
    K.DECL_OPT_CALL_PASSTHROUGH: LocalBinding.OPTIONAL_TO_PTR,
    K.DECL_STORAGE_OPT_LIFT: LocalBinding.OPTIONAL_TO_PTR,
    K.DECL_OPT_OWN_PARAM_LIFT: LocalBinding.OPTIONAL_TO_PTR,
    K.DECL_MIXED_ELEM_PTR: LocalBinding.OPTIONAL_TO_PTR,
    K.DECL_STORAGE_TUPLE_ALIAS: LocalBinding.STORAGE_TUPLE_ALIAS,
    K.DECL_STORAGE_TUPLE_CHAIN: LocalBinding.STORAGE_TUPLE_ALIAS,
    K.DECL_PTR_VARIANT: LocalBinding.PTR_VARIANT,
}
# The kind each borrow-local shape `_borrow_local_binding` returns declares
# (OPT_PTR_SLOT takes its own row; OTHER declares no borrow local).
_BORROW_LOCAL_KIND: dict[LocalBinding, K] = {
    LocalBinding.REF_ALIAS: K.DECL_REF_ALIAS,
    LocalBinding.POINTER: K.DECL_POINTER,
    LocalBinding.REBIND_SLOT: K.DECL_REBIND_SLOT,
    LocalBinding.OPTIONAL_TO_PTR: K.DECL_OPTIONAL_TO_PTR,
}


def _mk(site: '_Site', name: str, typ, kind: K, **f) -> BindingRepr:
    return BindingRepr(name=name, site=site.node, type=typ, kind=kind,
                       site_kind=site.kind, **f)


def _decl_row(site, name, typ, inp: 'PlanInputs', visible):
    """A first VarDecl's record: the declaration ladder's kind, then whether
    it is a second name for an iterator object, which it holds the same way
    whatever kind the ladder picked."""
    bind = _decl_local_binding(site, typ, inp, visible)
    rec = _decl_ladder(site, name, typ, inp, visible, bind)
    init, pre = site.init, inp.prescan
    if (isinstance(init, TpyName) and _protocol_auto_slot(typ)
            and name not in pre.reassigned and name not in pre.hoisted):
        # A structural-protocol or Self slot is never a value type, so the
        # ladder's reference-type tail always decides a kind for it.
        if rec is None:
            raise BindingTableError(
                f"protocol-typed declaration of {name!r} got no kind")
        src = visible(init.name)
        if src is not None and src.iterator_object:
            rec = replace(rec, iterator_object=True)
    return rec


def _decl_local_binding(site, typ, inp: 'PlanInputs', visible
                        ) -> 'LocalBinding | None':
    """The borrow-local shape of a first non-value or Optional declaration
    (checks.py `_borrow_local_binding`), None outside its rows."""
    if (site.init is None or typ is None
            or not (is_plain_nonvalue(typ) or isinstance(typ, OptionalType))):
        return None
    ctx = _PlanCtx(inp, visible, site.func or inp.func, (site.init,))
    try:
        return _checks._borrow_local_binding(
            site.node, typ, ctx.declared_view, inp.prescan, inp.analyzer,
            ctx.names_with("pointer"), ctx)
    except ThirUnsupported:  # a refusal is no binding
        return None


def _decl_ladder(site, name, typ, inp: 'PlanInputs', visible, bind):
    """A first VarDecl's kind -- the representation its declaration holds,
    which `_lower_first_decl` builds -- and its facts."""
    from . import statements as _st
    an, pre = inp.analyzer, inp.prescan
    init = site.init
    fn = site.func or inp.func
    vtype = typ
    movable = name in inp.sema_movable
    if init is None:
        # An annotation-only decl: a default-constructed value slot of the
        # try-hoist family (`_try_hoist_type_ok`), else the null pointer of
        # a pointer-repr Optional.
        if (pointer_repr_optional(vtype) is not None
                and not _try_hoist_type_ok(vtype, an)):
            return _mk(site, name, typ, K.DECL_NO_INIT_OPT_PTR, pointer=True,
                       movable=movable,
                       rebind_slot=name in pre.rvalue_reassigned)
        if vtype is not None and _try_hoist_type_ok(vtype, an):
            slot_t = _st._value_slot_render_type(vtype, an)
            rec = (_value_opt_of(site, name, typ, an, K.DECL_NO_INIT_VALUE)
                   or _mk(site, name, typ, K.DECL_NO_INIT_VALUE))
            return (replace(rec, effective_type=slot_t)
                    if slot_t is not vtype else rec)
        return None
    # A plain function's generator local a `del` closes is an
    # OPTIONAL_STORAGE slot, movable whatever sema's verdict, and the decl
    # lowers as a reassign of it.
    if (not (fn.is_generator or fn.is_async)
            and name in an.function_closed_frames.get(fn, ())):
        if isinstance(_p._binding_peel(unwrap_ref_type(typ)),
                      ConcreteGenType):
            return _mk(site, name, typ, K.DECL_CLOSED_FRAME, pointer=True,
                       movable=True, optional_storage=True)
    # An @error_return call's unwrap: an alias of a borrow-aliasing result,
    # else a bind; never movable.
    er_fi = _st._error_return_stmt_fi(init, an)
    if er_fi is not None:
        if call_returns_cpp_ref(an, er_fi):
            return _mk(site, name, typ, K.DECL_ER_ALIAS, pointer=True)
        bare = (unwrap_readonly(unwrap_ref_type(er_fi.return_type))
                if isinstance(er_fi.return_type, TpyType) else None)
        inner = bare.wrapped if isinstance(bare, OwnType) else bare
        ptr = (name in pre.rvalue_reassigned and _p._f1_record(inner, an)
               and is_plain_nonvalue(inner)
               and not (fn.is_generator or fn.is_async))
        return _mk(site, name, typ, K.DECL_ER_BIND, pointer=ptr,
                   rebind_slot=ptr)
    rec = _decl_value_rows(site, name, typ, inp, visible, bind, movable)
    if (isinstance(vtype, TupleType) and vtype.has_own_element()
            and not vtype.has_pointer_repr_element()
            and name not in pre.reassigned):
        # An Own-element value tuple holds its elements in storage, whatever
        # declaration builds it: reads stay storage and it moves like an
        # owned local.
        if rec is None:
            return _mk(site, name, typ, K.DECL_OWN_ELEM_TUPLE,
                       storage_tuple=True, movable=movable)
        return replace(rec, storage_tuple=True, movable=movable)
    return rec


def _decl_value_rows(site, name, typ, inp: 'PlanInputs', visible, bind,
                     movable):
    """The declaration ladder past the frame and error-return rows."""
    from . import statements as _st
    from .comprehensions import _COMP_KINDS
    an, pre = inp.analyzer, inp.prescan
    stmt, init = site.node, site.init
    fn = site.func or inp.func
    vtype = typ
    if vtype is None:
        return None
    ctx = _PlanCtx(inp, visible, fn, (init,))
    # An escape-hoisted / name-reassigned record: a pointer slot, an
    # optional pre-declared at function top for a hoisted name.
    if (isinstance(vtype, NominalType) and _p.record_like(vtype, an)
            and is_plain_nonvalue(vtype)
            and (name in pre.hoisted
                 or (name in pre.reassigned
                     and name not in pre.rvalue_reassigned))
            and (_checks.copy_construct_source(
                init, an, ctx.names_with("pointer"))
                 is not None
                 or (name in pre.move_through and isinstance(init, TpyName))
                 or _checks._record_rvalue_source_shape(init, an))):
        return _mk(site, name, typ,
                   (K.DECL_RECORD_HOISTED_SLOT if name in pre.hoisted
                    else K.DECL_RECORD_SLOT), pointer=True,
                   movable=movable,
                   rebind_slot=name in pre.rvalue_reassigned)
    # A name-reassigned container literal: a pointer slot. An unresolved
    # pending list is this kind too: its builder rejects it.
    vr = _p.resolve_pending_container(vtype, an) or vtype
    vru = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vr)))
    if (isinstance(init, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral,
                          TpyListRepeat))
            and name in pre.reassigned
            and name not in pre.rvalue_reassigned
            and name not in pre.hoisted and name not in pre.move_through
            and (isinstance(vru, PendingListType)
                 or _p._f1_container_ref(vru))):
        return _mk(site, name, typ, K.DECL_CONTAINER_SLOT, pointer=True,
                   movable=movable, effective_type=vru)
    fixed = (name not in pre.reassigned and name not in pre.hoisted
             and name not in pre.move_through)
    # `decl.storage_opt_name_lift`: a storage-opt loop / comprehension var
    # lifted to `T*`.
    if (isinstance(init, TpyName) and fixed
            and pointer_repr_optional(vtype) is not None
            and _optional_ptr_borrow_wide(vtype, an) is not None):
        src = visible(init.name)
        if src is not None and src.storage_opt:
            return _mk(site, name, typ, K.DECL_STORAGE_OPT_LIFT,
                       pointer=True, movable=movable)
    # The already-erased Own[dyn] call, then @dynamic protocol locals,
    # outside a branch only.
    svt = an.var_types.get(stmt)
    svt_u = (unwrap_readonly(unwrap_send_sync(svt))
             if isinstance(svt, TpyType) else None)
    gen_or_async = fn.is_generator or fn.is_async
    if (not site.in_branch and isinstance(svt_u, OwnType)
            and isinstance(init, (TpyCall, TpyMethodCall))
            and not gen_or_async and name not in pre.hoisted
            and name not in pre.reassigned
            and name not in pre.rvalue_reassigned
            and _checks._dyn_own_forward_call_arg(init, svt, an) is not None):
        return _mk(site, name, typ, K.DECL_DYN_OWN_ERASED, movable=movable,
                   effective_type=svt)
    if (not site.in_branch and isinstance(vtype, NominalType)
            and is_dyn_protocol(vtype)):
        if isinstance(svt_u, OwnType):
            # The deferred-erasure coroutine frame local is a value
            # `std::optional<frame>`, read as a pointer local like every
            # @dynamic local.
            return _mk(site, name, typ, K.DECL_CORO_FRAME_LOCAL,
                       pointer=True, coro_frame=True)
        return _mk(site, name, typ, K.DECL_DYN, pointer=True)
    # The nullable borrow tuple.
    if (not site.in_branch and _p._optional_borrow_tuple(vtype, an) is not None
            and name not in pre.hoisted and name not in pre.move_through
            and not (fn.is_generator or fn.is_async)):
        # A nullable-tuple call binds whatever its const-ness; past it a
        # const binding, and an init its builder cannot lower, take the
        # rows below.
        if (_p._nullable_borrow_tuple_call(init, an) is not None
                and name not in pre.reassigned):
            return _mk(site, name, typ, K.DECL_OPT_BTUPLE,
                       optional_borrow_tuple=True)
        if not ctx.binding(name).const_opt_borrow_tuple:
            own_call = _p._btuple_owning_call_init(init, an)
            if (isinstance(init, TpyNoneLiteral) or own_call
                    or (isinstance(init, (TpyFieldAccess, TpySubscript))
                        and _st._borrow_tuple_source_ok(init, ctx))):
                return _mk(site, name, typ, K.DECL_OPT_BTUPLE,
                           optional_borrow_tuple=True,
                           rebind_slot=bool(own_call) and not isinstance(
                               init, TpyNoneLiteral))
    # The borrow-local rows (`_decl_local_binding`): the kind that spells
    # the row's shape.
    if bind is LocalBinding.OPT_PTR_SLOT:
        return _opt_ptr_slot_decl(site, name, typ, inp, ctx, movable)
    kind = _BORROW_LOCAL_KIND.get(bind)
    if kind is not None:
        ref = bind is LocalBinding.REF_ALIAS
        slot = bind is LocalBinding.REBIND_SLOT
        # A pointer is a slot, whose const is the fixpoint's
        # (`_slot_const`); a reference has its initializer alone.
        return _mk(site, name, typ, kind, ref_alias=ref, pointer=not ref,
                   movable=movable and not ref,
                   const=ref and (_p._walrus_src_is_const(init, ctx)
                                  or _const_btuple_element(init, ctx)),
                   rebind_slot=slot or (
                       bind is LocalBinding.POINTER
                       and name in pre.rvalue_reassigned
                       and _pointer_decl_addresses(init, vtype, inp, visible,
                                                   fn)))
    if (not site.in_branch and isinstance(init, TpyName)
            and pointer_repr_optional(vtype) is not None
            and name not in pre.reassigned):
        # `decl.opt_own_param_lift`: `y = x` off an `Own[P | None]` param
        # (the
        # param is the only `optional_storage` binding whose declared type
        # is the owning storage optional).
        src = visible(init.name)
        if (src is not None and src.optional_storage
                and _p._own_storage_opt_param(
                    ctx.declared_view.get(init.name), an) == vtype):
            return _mk(site, name, typ, K.DECL_OPT_OWN_PARAM_LIFT,
                       pointer=True)
    if (not site.in_branch and pointer_repr_optional(vtype) is not None
            and fixed
            and _p._tuple_local_ptr_elem_subscript(
                init, ctx.declared_view, ctx.names_with("borrow_tuple_mixed"), an)):
        # `decl.mixed_elem_ptr`: `e = p[1]` off a mixed own-borrow tuple
        # local.
        return _mk(site, name, typ, K.DECL_MIXED_ELEM_PTR, pointer=True)
    # The `auto&&` storage-tuple alias (and its chain).
    rec = _storage_tuple_alias(site, name, typ, inp, visible, ctx)
    if rec is not None:
        return rec
    # A comprehension local.
    if type(init) in _COMP_KINDS and not vtype.is_value_type():
        return _mk(site, name, typ, K.DECL_COMPREHENSION, movable=movable)
    # `decl.tparam_call`: an open-T local off a T-returning call binds the
    # `val_or_ref_t` trait and is never promoted, unlike the generic tail
    # every other non-value local reaches.
    if (isinstance(vtype, TypeParamRef) and not vtype.is_value_type()
            and isinstance(init, (TpyCall, TpyMethodCall))
            and init.result_form is not ResultForm.COPY
            and name not in pre.reassigned and name not in pre.hoisted
            and name not in pre.move_through):
        t_fi = init.resolved_function_info
        if (t_fi is not None and t_fi.cpp_template is None
                and isinstance(unwrap_ref_type(t_fi.return_type),
                               TypeParamRef)):
            return _mk(site, name, typ, K.DECL_TPARAM_CALL,
                       const=_p._walrus_src_is_const(init, ctx))
    # A pointer-variant union local (`decl.union_addr`,
    # `decl.union_slot_rvalue`).
    ptr_u = _p._eligible_ptr_union_wide(vtype, an)
    if ptr_u is not None:
        if site.in_branch and name in pre.rvalue_reassigned:
            # A branch-first rebind slot: its builder rejects it.
            return _mk(site, name, typ, K.DECL_PTR_VARIANT_SLOT,
                       ptr_variant=True, rebind_slot=True)
        if not isinstance(init, TpyNoneLiteral) and not _checks._ptr_union_source_ok(
                init, ctx.declared_view, an, ptr_u,
                allow_field=name not in pre.reassigned):
            # The slot-hoist kinds -- a rebind slot when the name is
            # rvalue-reassigned.
            return _mk(site, name, typ, K.DECL_PTR_VARIANT_SLOT,
                       ptr_variant=True,
                       rebind_slot=name in pre.rvalue_reassigned)
        return _mk(site, name, typ, K.DECL_PTR_VARIANT, ptr_variant=True)
    # `decl.storage_call_tuple`: an owning tuple call (an Own-declared
    # return, the flat
    # per-element-Own family, or a tuple of owned tuples).
    ct = an.get_expr_type(init)
    ctu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        _p._unwrap_own(ct)))) if ct is not None else None)
    nested_own = _p._nested_owned_tuple_call_ret(ct, an) is not None
    if (_checks._storage_decl_src(init, an) and isinstance(ctu, TupleType)
            and (ctu.has_pointer_repr_element() or nested_own)
            and (call_hands_back_value(init)
                 or _p._owned_tuple_call_ret(ct, an) is not None
                 or nested_own)
            and fixed and is_rvalue_source(an, init)):
        return _mk(site, name, ctu, K.DECL_STORAGE_CALL_TUPLE,
                   storage_tuple=True, movable=movable)
    # `decl.iterator_object`: an iterator object off a generator call; a
    # concrete generator record the owned-record decl takes first
    # (`_owned_record_decl_ok`) is a plain local.
    fi = getattr(init, "resolved_function_info", None)
    if (not site.in_branch and fixed
            and isinstance(init, (TpyCall, TpyMethodCall))
            and fi is not None and fi.is_generator
            and not _st._owned_record_decl_ok(stmt, vtype, pre, {}, an)
            and _st._user_iterator_iterable(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vtype))), an)):
        return _mk(site, name, typ, K.DECL_ITERATOR_OBJECT,
                   iterator_object=True)
    # The rebound borrow tuple asks whether a `t = t` source is a borrow
    # tuple, which is the record this site gets when the row does not
    # apply: the rows below decide it first.
    rest = _decl_tail_rows(site, name, typ, inp, visible, ctx, movable, fixed)
    rec = _btuple_rebound_decl(site, name, typ, inp, ctx, fn, movable,
                               rest)
    return rec if rec is not None else rest


def _decl_tail_rows(site, name, typ, inp: 'PlanInputs', visible, ctx,
                    movable, fixed) -> 'BindingRepr | None':
    """The declaration ladder past the rebound borrow tuple."""
    from . import statements as _st
    an, pre = inp.analyzer, inp.prescan
    stmt, init = site.node, site.init
    vtype = typ
    # A borrow-tuple alias of a name, a ternary of names or mixed calls, or
    # a call (the mixed own / borrow tuple among them).
    if (not site.in_branch and fixed
            and isinstance(init, (TpyName, TpyCall, TpyMethodCall,
                                  TpyIfExpr))):
        rec = _btuple_alias_decl(site, name, typ, inp, visible, ctx, movable)
        if rec is not None:
            return rec
    # `decl.optview_shim`: the Optional-view param shim.
    if (name not in pre.reassigned and isinstance(init, TpyName)
            and _value_opt_owned_view(vtype, an) is not None):
        from .expressions import _param_declared_type
        if _p._opt_view_arg_shim(_param_declared_type(init.name, ctx),
                                 vtype, an):
            return _mk(site, name, typ, K.DECL_OPTVIEW_SHIM,
                       value_opt=ValueOptKind.VIEW)
    # A tuple-literal first decl, by the literal's captures.
    if isinstance(init, TpyTupleLiteral):
        rec = _tuple_literal_decl(site, name, typ, inp, movable)
        if rec is not None:
            return rec
    # The generic tail (`decl.opt_record_call`, the value-opt slots).
    if not fixed or not isinstance(init, (TpyCall, TpyMethodCall)):
        pass
    elif _st._own_opt_record_call_slot(stmt, vtype, ctx, an):
        return _mk(site, name, typ, K.DECL_OPT_RECORD_CALL,
                   value_opt=ValueOptKind.RECORD,
                   movable=movable and not vtype.is_value_type())
    rec = _value_opt_of(site, name, typ, an, K.DECL_VALUE_OPT)
    if rec is not None:
        if rec.value_opt is None and _p._value_opt_record(vtype) is not None:
            return replace(rec, value_opt=ValueOptKind.RECORD,
                           kind=K.DECL_OPT_VALUE_RECORD)
        return rec
    if _p._value_opt_record(vtype) is not None:
        return _mk(site, name, typ, K.DECL_OPT_VALUE_RECORD,
                   value_opt=ValueOptKind.RECORD)
    if not vtype.is_value_type():
        return _mk(site, name, typ, K.DECL_OWNED, movable=movable)
    return None


def _btuple_rebound_decl(site, name, typ, inp: 'PlanInputs', ctx, fn,
                         movable, rest: 'BindingRepr | None'
                         ) -> 'BindingRepr | None':
    """A reassigned pointer-repr tuple local declares the one borrow shape
    every binding of it holds (`std::tuple<.., T*>`, its owned elements
    collapsed to pointers) when every binding source can produce that shape:
    a borrow-tuple storage lvalue, a REF-capture literal of plain names or a
    borrow-tuple local. A branch-first declaration is one only off a name. A
    source naming the declared name (`t = t`) reads `rest`, the record the
    site gets otherwise.

    The pointer tuple (`ptr_tuple_form`) is this shape whatever its
    bindings' sources: a binding owning an element backs it with a slot at
    its own site. So is a local bound off a pointer-tuple name, which owns
    nothing of it."""
    from . import statements as _st
    an, pre, init = inp.analyzer, inp.prescan, site.init
    if not (name in pre.reassigned and name not in pre.hoisted
            and name not in pre.move_through
            and name not in pre.param_names
            and not (fn.is_generator or fn.is_async)):
        return None
    ptt = ptr_tuple_form(name, inp, fn)
    owned = pointer_tuple_owned(an.ctx, fn, name) if ptt is not None else ()
    if site.in_branch and owned:
        # An unhoisted block-first binding of the pointer tuple: its
        # element slots would die with the block while an alias of an
        # element may outlive it (TODO.md "Storage placement from loans
        # (scalar and tuple)"), whatever the right-hand side.
        raise ThirUnsupported(
            stmt_reject_reason(site.node, "decl.branch_slot_type"),
            loc=getattr(site.node, "loc", None))
    if ptt is not None:
        return _mk(site, name, typ, K.DECL_BTUPLE_REBOUND, effective_type=ptt,
                   ptr_tuple=ptt, movable=movable,
                   ptr_tuple_owned=frozenset(owned))
    if site.in_branch and not isinstance(init, TpyName):
        return None
    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        collapse_tuple_own_elements(typ))))
    if not (isinstance(bt, TupleType) and bt.has_pointer_repr_element()
            and not an.function_stmt_borrow_decls.get(fn, {}).get(name, False)
            and not contains_pending_leaf(bt)
            and not any(isinstance(t, (PendingViewType, IntLiteralType,
                                       FloatLiteralType))
                        for t in bt.inner_types())):
        return None
    srcs = _st._borrow_tuple_binding_sources(name, ctx)
    own = (name, ctx.with_fixpoint(rest if rest is not None
                                   else UNBOUND, name))
    if srcs is None or not all(_st._borrow_tuple_source_ok(s, ctx, own)
                               for s in srcs):
        return None
    return _mk(site, name, typ, K.DECL_BTUPLE_REBOUND, effective_type=bt)


def _pointer_decl_addresses(init, vtype, inp: 'PlanInputs', visible,
                            fn) -> bool:
    """Whether a POINTER decl takes its initializer's address
    (`_lower_borrow_local`'s PTR_ADDR arms), the shape that pre-declares a
    rebind slot for a later rvalue reseat: a call, a deref coerce, a
    container element, or a same-typed borrow-form NAME outside a
    resumable body. A field source converts in place and takes none."""
    if isinstance(init, (TpyCall, TpyMethodCall, TpyCoerce, TpySubscript)):
        return True
    if not isinstance(init, TpyName) or fn.is_generator or fn.is_async:
        return False
    rtype = inp.analyzer.get_expr_type(init)
    if rtype != vtype:
        return False
    if init.name == inp.self_receiver:
        return True
    rec = visible(init.name)
    pre = inp.prescan
    return project_held(rec, rtype, False, inp.analyzer, pre.param_names,
                        pre.owned_viewfam_params) is Held.BORROWED


def _tuple_literal_decl(site, name, typ, inp: 'PlanInputs', movable
                        ) -> 'BindingRepr | None':
    """A tuple-literal first decl: the borrow form of a REF-capture literal
    (inline elements noted), the widened storage family, the storage-record
    tuple with its owned layout; a plain value tuple continues to the
    generic tail (None)."""
    an, init = inp.analyzer, site.init
    vtype = typ
    caps = list(init.elem_capture or ())
    VALUE, REF = TupleElemCapture.VALUE, TupleElemCapture.REF
    all_value = bool(caps) and all(c is VALUE for c in caps)
    tuple_t = _p._value_tuple_nested(vtype, an)
    storage_record = False
    if tuple_t is None and all_value:
        tuple_t = _p._storage_record_tuple(vtype, an)
        storage_record = tuple_t is not None
    slot_bt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vtype)))
               if isinstance(vtype, TpyType) else None)
    if (tuple_t is None and isinstance(slot_bt, TupleType)
            and slot_bt.has_pointer_repr_element() and caps
            and all(c in (VALUE, REF) for c in caps)
            and any(c is REF for c in caps)):
        # The elements the binding owns (sema's `tuple_elem_owned`) are
        # the ones it holds inline.
        inline = an.ctx.tuple_elem_owned.get(site.node)
        return _mk(site, name, typ, K.DECL_BTUPLE_LITERAL,
                   effective_type=slot_bt,
                   inline_elems=(inline if inline is not None
                                 and len(inline) == len(slot_bt.element_types)
                                 and any(inline) else None))
    if tuple_t is None and (not caps or all_value):
        wide = _p._decl_tuple_nested(vtype, an)
        if wide is not None:
            storage = (wide.has_pointer_repr_element()
                       or wide.has_own_element()
                       or any(_p._wrapper_union_like(
                                  unwrap_readonly(unwrap_ref_type(
                                      unwrap_send_sync(we))), an) is not None
                              for we in wide.element_types))
            return _mk(site, name, typ, K.DECL_TUPLE_LITERAL_WIDE,
                       storage_tuple=storage, movable=storage and movable,
                       effective_type=wide)
    if not storage_record:
        return None
    # The binding holds the literal's owned layout when the literal is its
    # only write.
    return _mk(site, name, typ, K.DECL_TUPLE_LITERAL_RECORD,
               storage_tuple=True, movable=movable, effective_type=tuple_t,
               tuple_layout=(_checks.owned_literal_layout(init, tuple_t, an)
                             if name not in inp.prescan.reassigned
                             else None))


def _btuple_alias_decl(site, name, typ, inp: 'PlanInputs', visible, ctx,
                       movable) -> 'BindingRepr | None':
    """A borrow-tuple alias of a borrow-form tuple name, a ternary of two
    such names (or of two mixed own-borrow calls) or a call returning one:
    an `auto&` alias where a source holds elements inline, else an `auto`
    copy; the mixed calls hold the mixed render."""
    an, init = inp.analyzer, site.init
    src_bt = None
    mixed = promoted = False

    def name_bt(n: str):
        src = visible(n)
        if src is None or src.pointer or src.storage_tuple:
            return None
        return _p._f1_tuple(src.effective_type or src.type, an)

    if isinstance(init, TpyName):
        src_bt = name_bt(init.name)
    elif isinstance(init, TpyIfExpr):
        def arm(a):
            if (isinstance(a, (TpyCall, TpyMethodCall))
                    and _p._renders_own_borrow_tuple(
                        a, ctx.names_with("borrow_tuple_mixed"), an)):
                abt = _p._f1_tuple(an.get_expr_type(a), an)
                return abt if abt is not None and abt.is_mixed_own() else None
            return name_bt(a.name) if isinstance(a, TpyName) else None
        tb, eb = arm(init.then_expr), arm(init.else_expr)
        if tb is not None and tb == eb:
            src_bt = tb
            mixed = tb.is_mixed_own()
    else:
        cfi = init.resolved_function_info
        crt = getattr(cfi, "return_type", None) if cfi else None
        cru = (unwrap_readonly(unwrap_send_sync(crt))
               if crt is not None else None)
        owning = isinstance(cru, OwnType)
        owned_only = (isinstance(cru, TupleType) and cru.has_own_element()
                      and not cru.has_ref_elements())
        if cru is not None and not owning and not owned_only:
            src_bt = (_p._f1_tuple(an.get_expr_type(init), an)
                      or _p._wrapper_ref_tuple_return(
                          an.get_expr_type(init), an))
            mixed = promoted = (src_bt is not None and src_bt.is_mixed_own())
    if src_bt is None or not src_bt.has_ref_elements():
        return None
    if isinstance(init, TpyName):
        src = visible(init.name)
        mixed = mixed or (src is not None and src.borrow_tuple_mixed)
        arm_names = [init.name]
    elif isinstance(init, TpyIfExpr):
        arm_names = [a.name for a in (init.then_expr, init.else_expr)
                     if isinstance(a, TpyName)]
    else:
        arm_names = []
    recs = [visible(n) for n in arm_names]
    inline = [r.inline_elems if r is not None else None for r in recs]
    any_inline = any(i is not None for i in inline)
    inline_elems = inline[0] if any_inline else None
    const = bool(arm_names) and _p._walrus_src_is_const(init, ctx)
    # A source holding an element inline is aliased (`auto&`), since a copy
    # would duplicate the element; so is a mixed render whose owned elements
    # sit inline when no source is ever reassigned (a reassignment would
    # overwrite them in place under the alias). A reassigned source binds
    # `auto`, which copies the owned elements:
    # BUGS.md#mixed-tuple-local-copy-silent.
    arms = 2 if isinstance(init, TpyIfExpr) else 1
    ref = any_inline or (
        len(arm_names) == arms
        and all(r is not None and r.borrow_tuple_mixed
                and n not in inp.prescan.reassigned
                for n, r in zip(arm_names, recs)))
    if ref and (len(arm_names) < arms
                or any(i != inline_elems for i in inline)):
        # The alias holds the elements its source holds inline; a ternary
        # whose arms hold different ones (or a call arm) has no one alias.
        kind = K.DECL_BTUPLE_INLINE_ARMS
    else:
        kind = K.DECL_BTUPLE_REF_ALIAS if ref else K.DECL_BTUPLE_ALIAS
    # A copy of a pointer-tuple name is that pointer tuple, an alias of
    # every element as `Box& u = *t` is.
    ptt = ptr_tuple_form(name, inp, ctx.func)
    return _mk(site, name, typ, kind,
               borrow_tuple_mixed=mixed, movable=promoted and movable,
               inline_elems=inline_elems, const=const, effective_type=src_bt,
               ptr_tuple=ptt)


def _const_btuple_element(init, ctx) -> bool:
    """An element of a borrow tuple whose element pointers are const (the
    whole-body fixpoint's bit): a reference to it is a const referent."""
    return (isinstance(init, TpySubscript) and isinstance(init.obj, TpyName)
            and ctx.binding(init.obj.name).const_borrow_tuple)


def _opt_ptr_slot_decl(site, name, typ, inp: 'PlanInputs', ctx, movable
                       ) -> BindingRepr:
    """The OPT_PTR_SLOT binding's three kinds -- the borrow-returning call
    passthrough (const per its source), the storage
    optional call lift, the slot-hoist pointer local (a rebind slot when
    rvalue-reassigned)."""
    an, pre, init = inp.analyzer, inp.prescan, site.init
    vtype = typ
    calls = (TpyCall, TpyMethodCall)
    if (not site.in_branch
            and isinstance(init, (TpyCall, TpyMethodCall, TpyIfExpr,
                                  TpySubscript))
            and not (isinstance(init, TpySubscript)
                     and isinstance(init.index, TpySlice))
            and name not in pre.reassigned
            and _optional_ptr_borrow_wide(vtype, an) is not None
            and _optional_ptr_borrow_wide(an.get_expr_type(init), an)
            is not None
            and not reads_storage_form_optional(an, init)
            and not (isinstance(init, calls)
                     and call_hands_back_value(init))):
        # The lent `T*` is a borrow, never a move source.
        return _mk(site, name, typ, K.DECL_OPT_CALL_PASSTHROUGH,
                   pointer=True)
    if (not site.in_branch and isinstance(init, calls)
            and _p._call_storage_optional_return(init, an) is not None
            and _optional_ptr_borrow_wide(vtype, an) is not None):
        return _mk(site, name, typ, K.DECL_OPT_STORAGE_CALL, pointer=True,
                   movable=movable,
                   opt_storage_call=name in pre.reassigned)
    return _mk(site, name, typ, K.DECL_OPT_PTR, pointer=True,
               movable=movable, rebind_slot=name in pre.rvalue_reassigned)


def _declared_const(rec: BindingRepr, an) -> bool:
    """A slot whose declared type is read-only points at const whatever
    is bound into it: a `readonly[T]` type or Optional inner, or sema
    holding `Optional[readonly[T]]` for a plain-annotated declaration (a
    readonly-seeded local). A type fact, not a source's, so it seeds the
    slot's const beside the fixpoint (`_slot_const`)."""
    if _readonly_type(rec.type) or _readonly_opt_pointee(rec.type):
        return True
    svt = (an.var_types.get(rec.site) if isinstance(rec.site, TpyVarDecl)
           else None)
    return isinstance(svt, OptionalType) and isinstance(svt.inner,
                                                         ReadonlyType)


def _readonly_type(t: 'TpyType | None') -> bool:
    """`readonly[T]` (a Send / Sync wrapper peeled)."""
    return t is not None and isinstance(unwrap_send_sync(t), ReadonlyType)


def _readonly_opt_pointee(t: 'TpyType | None') -> bool:
    """A pointer-repr Optional over `readonly[T]`."""
    opt = (pointer_repr_optional(unwrap_send_sync(t)) if t is not None
           else None)
    return opt is not None and isinstance(opt.inner, ReadonlyType)


def _value_opt_of(site, name, typ, an, kind) -> 'BindingRepr | None':
    if _value_opt_scalar(typ, an) is not None:
        return _mk(site, name, typ, kind, value_opt=ValueOptKind.SCALAR)
    if _value_opt_owned_view(typ, an) is not None:
        return _mk(site, name, typ, kind, value_opt=ValueOptKind.VIEW)
    return None


def _storage_tuple_alias(site, name, typ, inp, visible, ctx):
    """`decl.storage_tuple_alias*`: a pointer-repr tuple local bound `auto&&`
    to a storage
    tuple lvalue, or to a name that is one -- the chain reads the source's
    record."""
    init = site.init
    pre, an = inp.prescan, inp.analyzer
    if site.in_branch or not is_storage_tuple_alias_decl(
            typ, init, name=name, reassigned=pre.reassigned,
            hoisted=pre.hoisted, move_through=pre.move_through,
            storage_tuple_locals=ctx.names_with("storage_tuple")):
        return None
    # The source must be a const-spellable storage tuple whose elements the
    # alias can re-bind (pointer-repr, or records held inline).
    src_t = an.get_expr_type(init)
    if not (_p._storage_tuple_alias_src_ok(init, ctx.declared_view, an)
            and (_p._f1_tuple(src_t, an) is not None
                 or _p._own_record_tuple(src_t, an) is not None)):
        return None
    if isinstance(init, TpyFieldAccess):
        return _mk(site, name, typ, K.DECL_STORAGE_TUPLE_ALIAS,
                   storage_tuple=True,
                   const=_p._walrus_src_is_const(init, ctx))
    if isinstance(init, TpySubscript):
        return _mk(site, name, typ, K.DECL_STORAGE_TUPLE_ALIAS,
                   storage_tuple=True,
                   const_storage_tuple=_p._walrus_src_is_const(init, ctx))
    if isinstance(init, TpyName):
        src = visible(init.name)
        if src is not None and src.storage_tuple:
            # The alias names the source's own constructed elements when it
            # holds the source's type and the source is never rebound.
            src_t = (src.effective_type if src.effective_type is not None
                     else src.type)
            layout = (src.tuple_layout
                      if (src.tuple_layout is not None
                          and init.name not in pre.reassigned
                          and src_t == typ)
                      else None)
            return _mk(site, name, typ, K.DECL_STORAGE_TUPLE_CHAIN,
                       storage_tuple=True, tuple_layout=layout,
                       const_storage_tuple=_p._walrus_src_is_const(init, ctx))
    return None


def _try_hoist_type_ok(vtype: TpyType, analyzer) -> bool:
    """A hoisted predecl type the slice renders -- the plain-value tail arm
    (`{cpp_type} {name};`), restricted to the same value family a first
    var-decl admits. Readonly wrappers reject: they need the const/pointer
    arms. A VALUE-repr Optional[scalar]
    predecls the bare `std::optional<T> r;` slot (default-empty), its later
    assigns riding the value-opt rows; pointer-repr Optionals stay out."""
    if isinstance(vtype, ReadonlyType):
        return False
    if isinstance(vtype, OptionalType):
        # The owned-inner `Optional[str]`/`Optional[bytes]` twin binds
        # `std::optional<std::string>` like a first var-decl does (the VIEW
        # kind); a view-INNER optional keeps rejecting -- its slot would
        # alias whatever each branch bound.
        return (_value_opt_scalar(vtype, analyzer) is not None
                or _value_opt_owned_view(vtype, analyzer) is not None)
    return (_p._eligible_scalar(vtype) or _p._eligible_char(vtype)
            # A `Ptr[T]` local is a first-class T* VALUE (never the
            # pointer-local deref machinery): plain `T* p;` predecl,
            # branch assigns and reads render bare.
            or _p._eligible_ptr_value(vtype, analyzer)
            or _p._eligible_enum(vtype, analyzer) is not None
            or _resolved_str_value(vtype, analyzer) is not None
            or _resolved_bytes_value(vtype, analyzer) is not None
            or _is_string_owned(vtype)
            or _p._eligible_value_union(vtype) is not None
            # A VALUE tuple predecls the same default-constructed slot
            # (`std::tuple<std::string, int32_t> peer;`); its later assigns
            # ride the value-tuple reassign rows. Pointer-repr tuples alias
            # their sources and take the borrow-tuple hoist instead.
            or _p._value_tuple(vtype, analyzer) is not None
            or _p._slice_object_type(vtype)
            # A VALUE-record hoist (`z = ZoneInfo(key)` inside a try):
            # the plain-value arm predecls the default-constructed
            # record (`::tpystd::datetime::ZoneInfo z;`), the body assign
            # riding the value-record reassign rows.
            or _value_record_slot(vtype))


def _hoist_row(site, name, typ, inp: 'PlanInputs', visible):
    """A sema branch-first predecl at the head of an if / while / for /
    try / with / match (`if_branch_decls`): the representation its
    statement predeclares -- or, for a hoist the statement cannot
    predeclare, `hoist.plain`. The hoist builders (statements.py
    `_HOIST_BUILDERS`) read nothing else to pick the declaration."""
    an, pre = inp.analyzer, inp.prescan
    if pre.binds_global(name):
        # The body reads the name as a module global: no local predecl.
        return _mk(site, name, typ, K.HOIST_PLAIN)
    if typ is None:
        return None
    ctx = _PlanCtx(inp, visible, site.func or inp.func)
    vt = _p.resolve_pending_container(typ, an) or typ
    if isinstance(site.node, TpyIf):
        # An if chain tries its non-value flavors before the value family.
        rec = _nonvalue_hoist(site, name, typ, vt, inp, ctx)
        if rec is None and _try_hoist_type_ok(vt, an):
            rec = _value_hoist(site, name, typ, an)
        return rec
    if _try_hoist_type_ok(typ, an):
        return _value_hoist(site, name, typ, an)
    return _nonvalue_hoist(site, name, typ, vt, inp, ctx)


def _value_hoist(site, name, typ, an) -> BindingRepr:
    """The default-constructed value predecl (`T name;`), a value-repr
    Optional by its value-opt kind."""
    return (_value_opt_of(site, name, typ, an, K.HOIST_VALUE)
            or _mk(site, name, typ, K.HOIST_VALUE))


def _nonvalue_hoist(site, name, typ, vt, inp: 'PlanInputs', ctx
                    ) -> 'BindingRepr | None':
    """The record of a non-value hoist, by `_nonvalue_hoist_kind`. A
    pointer an if chain's or a match's rvalue reseats re-fill has the
    statement's rebind slot; any other pointer hoist takes an rvalue reseat
    through a slot the reseat allocates at function top."""
    kind, borrowed = _nonvalue_hoist_kind(site, name, vt, inp, ctx)
    if kind is None:
        return None
    stmt = site.node
    if kind in (K.HOIST_BTUPLE_BRANCH, K.HOIST_BTUPLE_LOOP):
        # A pointer tuple (`ptr_tuple_form`): each binding owning an element
        # backs it at its own site; an alias of one owns none.
        from . import statements as _st
        srcs = _st._borrow_tuple_binding_sources(name, ctx) or ()
        ptt = ptr_tuple_form(name, inp, ctx.func)
        if ptt is not None:
            return _mk(site, name, typ, kind, ptr_tuple=ptt,
                       effective_type=ptt,
                       ptr_tuple_owned=pointer_tuple_owned(
                           ctx.analyzer.ctx, ctx.func, name))
        # Otherwise an owning tuple-call source (a walrus's) fills a rebind
        # slot the if chain's head declares, and a mixed call's binding
        # holds its hybrid render.
        return _mk(site, name, typ, kind,
                   borrow_tuple_mixed=any(
                       _p._mixed_own_btuple_call(s, ctx.analyzer)
                       for s in srcs),
                   rebind_slot=(kind is K.HOIST_BTUPLE_BRANCH
                                and isinstance(stmt, TpyIf)
                                and any(_p._btuple_owning_call_init(
                                    s, ctx.analyzer) for s in srcs)))
    if kind is K.HOIST_OPT_BTUPLE:
        return _mk(site, name, typ, kind,
                   optional_borrow_tuple=not site.in_branch)
    if kind in (K.HOIST_DYN, K.HOIST_PTR_NULL):
        return _mk(site, name, typ, kind, pointer=True,
                   lazy_rebind_slot=kind is K.HOIST_PTR_NULL)
    if kind in (K.HOIST_POINTER, K.HOIST_CONST_POINTER):
        nullable = pointer_repr_optional(vt) is not None
        slot = (kind is K.HOIST_POINTER
                and isinstance(stmt, (TpyIf, TpyMatch))
                and name in inp.prescan.rvalue_reassigned and not nullable)
        movable = kind is K.HOIST_POINTER and name in inp.sema_movable
        return _mk(site, name, typ, kind, pointer=True,
                   const=kind is K.HOIST_CONST_POINTER, movable=movable,
                   rebind_slot=slot, lazy_rebind_slot=not slot,
                   borrowed_capture=borrowed)
    if kind is K.HOIST_OPT_STORAGE:
        return _mk(site, name, typ, kind, pointer=True, movable=True,
                   optional_storage=True)
    return _mk(site, name, typ, kind)


def _nonvalue_hoist_kind(site, name: str, var_type: TpyType,
                         inp: 'PlanInputs', ctx) -> 'tuple[K | None, bool]':
    """How a branch-first non-value hoist predecls, for every statement:
    an if chain's nullable borrow tuple, the borrow tuple a branch (if /
    try) or a loop variable binds, an if chain's `@dynamic` base pointer, a
    for head's container unpack target; a plain non-value bound once is
    the owned slot (`std::optional<T>`); reassigned or borrow-only, or a
    pointer-repr Optional (the nullable pointer over its inner), the
    pointer local; a borrow-decl const bit or a readonly type the const
    pointer, unless rvalue reseats would write through it (an if chain's
    reseats write the owned slot's address through it). A resumable body
    has no function-top drain for the line (a resumable loop head's plain
    non-value takes the owned slot), and a move-through payload outside
    the owned-slot family no form. None: no non-value hoist of this type.
    The flag is a match's borrow-only capture, the pointer a nested match
    re-seats by address."""
    got = _nonvalue_hoist_rung(site, name, var_type, inp, ctx)
    return got if isinstance(got, tuple) else (got, False)


def _nonvalue_hoist_rung(site, name: str, var_type: TpyType,
                         inp: 'PlanInputs', ctx):
    """`_nonvalue_hoist_kind`'s ladder: a kind, or a kind with its
    borrowed-capture flag."""
    an, pre, func = ctx.analyzer, ctx.prescan, ctx.func
    stmt = site.node
    at_if = isinstance(stmt, TpyIf)
    at_match = isinstance(stmt, TpyMatch)
    if at_if and _p._optional_borrow_tuple(var_type, an) is not None:
        return K.HOIST_OPT_BTUPLE
    var_opt = pointer_repr_optional(var_type)
    ptr_opt = var_opt is not None and not _readonly_opt_pointee(var_type)
    btuple = (isinstance(stmt, (TpyIf, TpyTry, TpyForEach))
              and isinstance(var_type, TupleType)
              and var_type.has_pointer_repr_element())
    dyn = (at_if and isinstance(var_type, NominalType)
           and is_dyn_protocol(var_type))
    if not (btuple or dyn) and _ptr_null_hoist(stmt, name, var_type, inp,
                                               ctx):
        return K.HOIST_PTR_NULL
    if not (btuple or dyn or ptr_opt or is_plain_nonvalue(var_type)):
        return None
    if func.is_generator or func.is_async:
        # A loop-head variable of a resumable leaf is a leaf local in every
        # constructed shape, so its plain non-value takes the owned slot.
        if (isinstance(stmt, (TpyWhile, TpyForEach))
                and is_plain_nonvalue(var_type)):
            return K.HOIST_OPT_STORAGE
        return K.HOIST_PLAIN
    if btuple:
        return (K.HOIST_BTUPLE_LOOP if isinstance(stmt, TpyForEach)
                else K.HOIST_BTUPLE_BRANCH)
    if dyn:
        return K.HOIST_DYN
    borrow_decls = an.function_stmt_borrow_decls.get(func, {})
    borrow_only = (name in borrow_decls
                   and name not in an.function_ever_owned_locals.get(
                       func, set()))
    # Const when every source bound into this slot is a const borrow
    # (`_plan_const`, a capture's subject included).
    const_borrow = SlotKey.of(stmt, name) in inp.const_slots
    rebound = name in pre.rvalue_reassigned
    # A readonly-TYPED hoist (`v = r.view()` at a `readonly[T]` return) is
    # the same const borrow sema's stmt-borrow bit names.
    readonly = _readonly_type(var_type)
    # A match capture into a borrow-only pointer assigns its address.
    borrowed = at_match and borrow_only
    if name in pre.move_through:
        # Sema's ordinary move bindings consume owned, unreassigned sources.
        if _p.record_like(var_type, an):
            return K.HOIST_CONST_POINTER if readonly else K.HOIST_OPT_STORAGE
        return K.HOIST_PLAIN
    if const_borrow:
        # An if chain's or a match's rvalue reseats fill a slot the const
        # pointer then points at; any other statement's would write
        # through it.
        return (K.HOIST_PLAIN if rebound and not (at_if or at_match)
                else (K.HOIST_CONST_POINTER, borrowed))
    if readonly:
        return K.HOIST_CONST_POINTER, borrowed
    if ptr_opt or name in pre.reassigned or borrow_only:
        return K.HOIST_POINTER, borrowed
    return K.HOIST_OPT_STORAGE


def _ptr_null_hoist(stmt, name, vt, inp: 'PlanInputs', ctx) -> bool:
    """A hoisted container ref-target of a sync for head's unpack."""
    from . import statements as _st
    if (not isinstance(stmt, TpyForEach) or not stmt.is_tuple_unpack
            or ctx.resumable_leaf_mode or not stmt.body
            or not isinstance(stmt.body[0], TpyTupleUnpack)
            or not _p._f1_container_ref(unwrap_readonly(vt))):
        return False
    up = stmt.body[0]
    if name not in up.targets:
        return False
    i = up.targets.index(name)
    if i >= len(up.is_ref) or not up.is_ref[i]:
        return False
    route = _st._for_tuple_unpack_route(stmt, inp.analyzer,
                                        ctx.declared_view, frozenset())
    return route is not None and not route.iter_proto


def capture_const(subject, ctx) -> bool:
    """Whether a match capture aliasing into `subject` is const: the one
    const-source verdict (`_walrus_src_is_const`) on the subject, as the
    const fixpoint judges a capture's source. Every capture's record reads
    it -- the `auto&` alias, the whole pointer subject's copy."""
    return _p._walrus_src_is_const(subject, ctx)


# The facts a loop head decides for its variable whatever the same-named
# outer binding says; the variable inherits every other fact from that
# binding (`_inherit_outer`). The outer declaration's tuple layout and
# inline elements are facts of its own write, never the loop's.
_LOOP_SCRUBBED = frozenset({"pointer", "rebind_slot",
                            "const_loop_var", "tuple_layout",
                            "inline_elems"})


# A loop-head unpack target decides the same facts, except const, which it
# inherits.
_HEAD_TARGET_SCRUBBED = frozenset({"pointer", "rebind_slot"})


def _inherit_outer(f: dict, outer: 'BindingRepr | None',
                   scrubbed: frozenset = _LOOP_SCRUBBED) -> dict:
    """A loop variable's fields over the same-named outer binding it shadows:
    the head decides its own facts and inherits every other one from the
    outer name -- an asymmetry with a comprehension variable or a nested
    def's parameter, whose scopes start clean."""
    if outer is None:
        return f
    for fld in REPR_FIELDS:
        if fld in scrubbed or fld == "owned":
            continue
        ov = getattr(outer, fld)
        if fld in ("value_opt", "tuple_layout", "inline_elems"):
            if f.get(fld) is None and ov is not None:
                f[fld] = ov
        elif ov:
            f[fld] = True
    if outer.owned:
        f["owned"] = True
    return f


def _loop_row(site, name, typ, inp: 'PlanInputs', visible):
    """A for-each loop variable (`_lower_stmt_dispatch`'s for-each arm): a
    fresh C++ binding of the body."""
    from . import statements as _st
    an = inp.analyzer
    stmt = site.node
    ctx = _PlanCtx(inp, visible, site.func or inp.func, (stmt.iterable,))
    f: dict = {}
    yields_const = _st._iteration_yields_const(stmt.iterable, ctx, an)
    consuming = (_st._for_consuming_route(stmt, an, ctx.declared_view)
                 is not None)
    elem = (stmt.enum_iterable if stmt.enum_iterable is not None
            else _st._resolved_loop_elem_type(stmt, an))
    if elem is not None:
        kind = loop_binding_kind(elem, stmt.const_loop_var,
                                 hoisted=stmt.hoist_loop_var,
                                 consuming=consuming,
                                 copies=stmt.copies_var)
        f["loop_binding"] = kind
        # A reference over a const iteration deduces const (`auto&&`); a
        # copy or the hoisted slot is the loop's own mutable value.
        f["const_loop_var"] = (
            kind in (LoopBinding.CONST_REFERENCE,
                     LoopBinding.CONST_TYPED_REFERENCE)
            or (kind is LoopBinding.REFERENCE and yields_const))
    outer = visible(name)
    et = stmt.elem_type
    etu = _uw(unwrap_ref_type(et)) if isinstance(et, TpyType) else None
    it_t = an.get_expr_type(stmt.iterable)
    storage_tuple = (isinstance(etu, TupleType)
                     and etu.has_pointer_repr_element()
                     and it_t is not None
                     and is_native_iterable(unwrap_readonly(it_t),
                                            an.registry)
                     and not (outer is not None and outer.storage_tuple)
                     and _p._borrow_tuple_local_type(
                         name, ctx.declared_view, ctx.names_with("storage_tuple"))
                     is None)
    if storage_tuple:
        f["storage_tuple"] = True
        f["const_storage_tuple"] = yields_const
    if outer is not None and outer.is_param:
        # A loop that rebinds a parameter reads as that parameter where a
        # use asks "is this the caller's" (a view field's admission); sema
        # refuses a loop rebinding of another type, so the type carries.
        f["is_param"] = True
        f["param_type"] = outer.param_type
    if stmt.is_tuple_unpack:
        return _mk(site, name, typ, K.LOOP_UNPACK_HOLDER,
                   **_inherit_outer(f, outer))
    if _p._foreach_value_opt_elem(et) is not None:
        f["value_opt"] = ValueOptKind.SCALAR
    elif _p._foreach_storage_opt_elem(et, an):
        f["storage_opt"] = True
        src = (stmt.iterable.obj if isinstance(stmt.iterable, TpyMethodCall)
               else stmt.iterable)
        f["const_storage_opt"] = _st._iteration_yields_const(src, ctx, an)
    # A consuming loop's variable owns its element: movable whatever
    # sema's verdict says; so does one that takes its own copy of an
    # element a copy allocates for (`copies_var`): the pass's last use
    # moves it.
    owns_copy = (stmt.copies_var and elem is not None
                 and elem.is_expensive_copy())
    if ((isinstance(et, OwnType) or owns_copy)
            and not stmt.hoist_loop_var) or consuming:
        f["movable"] = True
    return _mk(site, name, typ, K.LOOP_VAR, **_inherit_outer(f, outer))


def _with_row(site, name, typ, inp: 'PlanInputs', visible):
    """A sync `with ... as` target's first binding (`_with_target_arm`): an
    `auto` copy of a value enter result, the `T* name = &(...)` pointer of
    a reassigned record (a later `with` re-points it), the `auto&` alias
    of any other record, const as the enter result it deduces."""
    func = site.func or inp.func
    an = inp.analyzer
    if func.is_generator or func.is_async or not isinstance(typ, TpyType):
        return _mk(site, name, typ, K.WITH_PLAIN)
    resolved = unwrap_readonly(unwrap_send_sync(typ))
    if typ.is_value_type():
        if not (_p._eligible_scalar(resolved) or _p._eligible_char(resolved)
                or _p._eligible_enum(resolved, an) is not None
                or _resolved_str_value(resolved, an) is not None
                or _resolved_bytes_value(resolved, an) is not None
                or _p._value_tuple(resolved, an) is not None
                or _p._eligible_value_union(resolved) is not None):
            return _mk(site, name, typ, K.WITH_PLAIN)
        return _mk(site, name, typ, K.WITH_VALUE)
    if not _p.record_like(resolved, an):
        return _mk(site, name, typ, K.WITH_PLAIN)
    if name in inp.prescan.reassigned:
        # The scan counts the with-rebind itself in `rvalue_reassigned`, so
        # a mixed plain-rvalue reassign is kept out by the reassign site's
        # own gates.
        return _mk(site, name, typ, K.WITH_POINTER, pointer=True)
    return _mk(site, name, typ, K.WITH_REF, const=_readonly_type(typ))


def walrus_value_type(w, analyzer) -> 'TpyType | None':
    """The type a walrus binds its target at: the named expression's type,
    pending containers / views and literals resolved, wrappers peeled."""
    t = analyzer.get_expr_type(w)
    t = _p.resolve_pending_container(t, analyzer) or t
    t = _p._resolve_pending_view(t, analyzer) or t
    if t is None:
        return None
    t = resolve_int_literals(t, analyzer.ctx.default_int_for_literal)
    return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))


def _walrus_row(site, name, typ, inp: 'PlanInputs', visible):
    """A walrus target's first binding (`_lower_expr_impl`'s TpyNamedExpr
    arm), in the order its rungs try: a borrow-only record's pointer, a
    pointer-repr Optional's, the reassigned borrow tuple over its owning
    slot, the borrow tuple, an owned non-value's deferred-init slot, a
    value-repr Optional, an owned str / bytes or a scalar-shaped value;
    sync only but the last."""
    an, pre = inp.analyzer, inp.prescan
    func = site.func or inp.func
    vtu = walrus_value_type(site.node, an)
    if name in pre.hoisted or vtu is None:
        return _mk(site, name, typ, K.WALRUS_PLAIN)
    if not (func.is_generator or func.is_async):
        rec = _walrus_sync_kind(site, name, typ, vtu, inp, visible)
        if rec is not None:
            return rec
    if ((_p._eligible_scalar(vtu) or _p._eligible_char(vtu)
         or _value_record_slot(vtu)
         or _p._eligible_enum(vtu, an) is not None)
            and not isinstance(vtu, OptionalType)):
        return _mk(site, name, typ, K.WALRUS_VALUE)
    return _mk(site, name, typ, K.WALRUS_PLAIN)


def _walrus_sync_kind(site, name, typ, vtu, inp: 'PlanInputs', visible
                      ) -> 'BindingRepr | None':
    """The sync-only walrus rungs; None past the last of them."""
    an, pre = inp.analyzer, inp.prescan
    func = site.func or inp.func
    borrow_decls = an.function_stmt_borrow_decls.get(func, {})
    ever_owned = an.function_ever_owned_locals.get(func, set())
    if (name in borrow_decls and name not in ever_owned
            and is_plain_nonvalue(vtu)):
        if not _p.record_like(vtu, an):
            # The alias of a non-record has no form.
            return _mk(site, name, typ, K.WALRUS_PLAIN)
        return _mk(site, name, typ, K.WALRUS_PTR_ALIAS, pointer=True)
    if _p._optional_ptr_borrow(vtu, an) is not None:
        return _mk(site, name, typ, K.WALRUS_OPT_PTR, pointer=True)
    if isinstance(vtu, TupleType) and name in pre.reassigned:
        return _mk(site, name, typ, K.WALRUS_BTUPLE_SLOT)
    owned_tuple = (_p._owned_tuple_call_source(site.init, an)
                   and not contains_pending_leaf(vtu))
    if (isinstance(vtu, TupleType) and vtu.has_pointer_repr_element()
            and not owned_tuple):
        return _mk(site, name, typ, K.WALRUS_BTUPLE)
    if ((is_plain_nonvalue(vtu) and name in ever_owned
         and _p.record_like(vtu, an)) or owned_tuple):
        return _mk(site, name, typ, K.WALRUS_OWNED_SLOT)
    vo = _value_opt_of(site, name, vtu, an, K.WALRUS_VALUE_OPT)
    if vo is not None:
        return replace(vo, type=typ)
    s = _resolved_str_value(vtu, an)
    b = _resolved_bytes_value(vtu, an)
    if ((s is not None and not is_str_view_type(s))
            or (b is not None and not is_bytes_view_type(b))):
        return _mk(site, name, typ, K.WALRUS_VALUE)
    return None


def fresh_record(table: 'BindingTable', site: object, name: str
                 ) -> 'BindingRepr | None':
    """The record `site` declares for `name` as a fresh binding, None where
    the site writes a binding in scope (it may key that binding's
    refinement, as a standalone unpack's move promotion does)."""
    rec = table.declared_by(site, name)
    return rec if rec is not None and rec.site is site else None


def head_target_writes_outer(stmt: TpyTupleUnpack, i: int, typ,
                             outer: 'BindingRepr | None', analyzer, *,
                             walk_declared: bool = False) -> bool:
    """Whether a for head's unpack target `i` writes the same-named binding
    in scope rather than declaring a fresh one: a pointer-repr Optional
    element and an owned element always declare a fresh local, any other
    target over a visible binding is that binding (assigned, re-pointed
    when it is the loop's null-pointer hoist, else refused). A resumable
    frame's field is shadowed by a fresh head local unless the walk
    declared it already (`walk_declared`, the for-each arm's flow state,
    which the planner cannot see: it keys the fresh record, and the arm
    asks again)."""
    if outer is None or (outer.site_kind == "frame" and not walk_declared):
        return False
    tt = unwrap_ref_type(typ) if isinstance(typ, TpyType) else None
    if _p._optional_ptr_borrow(tt, analyzer) is not None:
        return False
    return not (i < len(stmt.is_owned) and stmt.is_owned[i])


def _unpack_row(site, name, typ, inp: 'PlanInputs', visible):
    """A fresh tuple-unpack target (`_lower_stmt_dispatch`'s TpyTupleUnpack
    arm, the for-each arm's head): its kind, and its facts over those of
    the same-named outer binding a loop head's fresh target shadows
    (`_inherit_outer`); the const of a head pointer or reference is the
    iteration's, never the outer name's."""
    rec = _unpack_target(site, name, typ, inp, visible)
    outer = visible(name)
    if not site.node.is_loop_head or outer is None:
        return rec
    own = {fld: getattr(rec, fld) for fld in REPR_FIELDS}
    scrubbed = (_HEAD_TARGET_SCRUBBED | {"const"}
                if rec.kind in (K.UNPACK_OPT_PTR, K.UNPACK_REF)
                else _HEAD_TARGET_SCRUBBED)
    return replace(rec, **_inherit_outer(dict(own), outer, scrubbed))


def _unpack_target(site, name, typ, inp: 'PlanInputs', visible
                   ) -> BindingRepr:
    """A fresh tuple-unpack target's kind, in the order the unpack's
    target loop tries its binds, and its facts."""
    an = inp.analyzer
    stmt = site.node
    i = stmt.targets.index(name)
    flag = (lambda xs: bool(xs[i]) if i < len(xs) else False)
    is_ref = flag(stmt.is_ref)
    tt = unwrap_ref_type(typ) if isinstance(typ, TpyType) else None
    kind = (_head_unpack_kind if stmt.is_loop_head
            else _standalone_unpack_kind)(stmt, i, tt, an)
    f: dict = {}
    if not stmt.is_loop_head:
        f["movable"] = name in inp.sema_movable
    elif (kind is K.UNPACK_VALUE and flag(stmt.target_copies)
          and isinstance(tt, TpyType)
          and (_p._resolve_pending_view(tt, an) or tt).is_expensive_copy()):
        # A head target that takes its own copy each pass: its last use in
        # the pass moves it.
        f["movable"] = True
    if kind is K.UNPACK_OPT_PTR:
        f["pointer"] = True
    elif kind is K.UNPACK_PTR_VARIANT:
        f["ptr_variant"] = True
    elif kind is K.UNPACK_PTR:
        f["pointer"] = True
        f["rebind_slot"] = name in inp.prescan.rvalue_reassigned
    elif not is_ref:
        vo = _value_opt_of(site, name, typ, an, kind)
        if vo is not None:
            f["value_opt"] = vo.value_opt
        elif not stmt.is_loop_head and _own_opt_storage_binding(tt):
            f["value_opt"] = ValueOptKind.RECORD
    if kind in UNPACK_ELEM_POINTER_KINDS:
        f["const"] = _unpack_source_const(site, i, inp, visible)
    return _mk(site, name, typ, kind, **f)


# The unpack targets bound to an element pointer of the `tuple_to_pointer`
# wrap (`auto&&`, `auto*`, `T*`): const as the unpack's source, which the
# wrap spells (`statements.unpack_wrap_const`).
UNPACK_ELEM_POINTER_KINDS = frozenset({K.UNPACK_REF, K.UNPACK_PTR,
                                       K.UNPACK_OPT_PTR})


def _standalone_unpack_kind(stmt: TpyTupleUnpack, i: int, tt, an) -> K:
    """The bind a standalone unpack's fresh target `i` takes
    (`_lower_stmt_dispatch`'s TpyTupleUnpack arm)."""
    from . import statements as _st
    flag = (lambda xs: bool(xs[i]) if i < len(xs) else False)
    is_ref, is_owned, is_new = (flag(stmt.is_ref), flag(stmt.is_owned),
                                flag(stmt.is_new))
    if not is_owned and is_new and _p._wrapper_union_like(tt, an) is not None:
        # A recursive wrapper element's capture slot is a live `X&`.
        return K.UNPACK_UNWRAP_REF
    if is_ref:
        if not is_new:
            return K.UNPACK_PLAIN
        if _p.record_like(tt, an):
            # A reassigned name rebinds: an alias would write the new value
            # through into the element's referent.
            return K.UNPACK_PTR if flag(stmt.is_rebound) else K.UNPACK_REF
        if _p._optional_ptr_borrow(tt, an) is not None:
            return K.UNPACK_OPT_PTR
        return K.UNPACK_PLAIN
    if not is_new:
        return K.UNPACK_PLAIN
    if is_owned:
        # The pointer-lift capture already holds an `Own[P | None]`
        # element as `P*`; an `Own[A | B]` one as the value variant, lifted
        # per element. A record, a recursive wrapper or a container moves
        # out of the holder.
        if _p._optional_ptr_borrow(tt, an) is not None:
            return K.UNPACK_OPT_PTR
        if _p._eligible_ptr_union(tt, an) is not None:
            return K.UNPACK_PTR_VARIANT
        if (_p.record_like(tt, an)
                or isinstance(unwrap_readonly(tt), RecursiveAliasInstanceType)
                or _p._owned_container_slot(tt)):
            return K.UNPACK_MOVE
        return K.UNPACK_PLAIN
    # A nested value tuple and an `Own[P] | None` element (storage form)
    # copy like a scalar.
    if not (_st._scalar_or_str_unpack_elem(tt, an)
            or _p._value_tuple_global(tt, an) is not None
            or _own_opt_storage_binding(tt)):
        return K.UNPACK_PLAIN
    return K.UNPACK_CREF if flag(stmt.is_const_ref) else K.UNPACK_VALUE


def _head_unpack_kind(stmt: TpyTupleUnpack, i: int, tt, an) -> K:
    """The bind a for head's fresh unpack target `i` takes (the for-each
    arm's head; a target writing the outer binding keys no record,
    `head_target_writes_outer`). A target sema stamps as taking its own
    copy (`target_copies`) copies instead of a const reference into the
    holder."""
    flag = (lambda xs: bool(xs[i]) if i < len(xs) else False)
    if _p._optional_ptr_borrow(tt, an) is not None:
        return K.UNPACK_OPT_PTR
    if flag(stmt.is_owned):
        return K.UNPACK_MOVE
    if flag(stmt.is_ref):
        return K.UNPACK_PTR if flag(stmt.is_rebound) else K.UNPACK_REF
    if flag(stmt.is_const_ref) and not flag(stmt.target_copies):
        return K.UNPACK_CREF
    return K.UNPACK_VALUE


def _unpack_source_const(site, i: int, inp: 'PlanInputs', visible) -> bool:
    """The const of the element pointer an unpack's pointer or reference
    target binds: a readonly element type (a fact of element `i`, not of
    the source), else a for head's iteration source
    (`_iteration_yields_const`), a standalone unpack's source by the one
    source verdict (`_walrus_src_is_const`)."""
    from . import statements as _st
    stmt = site.node
    an = inp.analyzer
    raw = stmt.target_types[i] if i < len(stmt.target_types) else None
    if isinstance(unwrap_ref_type(unwrap_send_sync(raw))
                  if isinstance(raw, TpyType) else None, ReadonlyType):
        return True
    if stmt.is_loop_head:
        loop = site.loop
        if loop is None:
            return False
        ctx = _PlanCtx(inp, site.loop_visible, site.func or inp.func,
                       (loop.iterable,))
        return _st._iteration_yields_const(loop.iterable, ctx, an)
    ctx = _PlanCtx(inp, visible, site.func or inp.func, (stmt.value,))
    return _p._walrus_src_is_const(stmt.value, ctx)


# A comprehension variable is never the pointer its same-named outer binding
# may be. The route refuses a variable hiding a rebind slot, a storage tuple
# or a narrowed name; every other fact in REPR_FIELDS is inherited, which is
# wrong for a value Optional (BUGS.md#comp-loop-var-shadows-optional-ptr-param).
# A frame member's `frame_class`, and the frame slot it decides, is not.
_COMP_SCRUBBED = frozenset({"pointer"})


def _comp_row(site, name, typ, inp: 'PlanInputs', visible):
    """A comprehension variable: the facts its clause decides over those of
    the same-named outer binding it shadows (`_inherit_outer`)."""
    rec = _comp_clause_row(site, name, typ, inp, visible)
    outer = visible(name)
    if outer is None:
        return rec
    if rec is None:
        rec = _mk(site, name, typ, K.COMP_PLAIN)
    own = {fld: getattr(rec, fld) for fld in REPR_FIELDS}
    return replace(rec, **_inherit_outer(own, outer, _COMP_SCRUBBED))


def _comp_clause_row(site, name, typ, inp: 'PlanInputs', visible):
    """The facts a comprehension clause decides for its variable, from the
    clause's route (`_clause_route`)."""
    from . import statements as _st
    an = inp.analyzer
    gen, route = site.node, site.route
    if route is None:
        return None
    # The clause's loop binds its variable (an unpack clause, its holder)
    # as the for statement's loop binds an element of the same type.
    loop = (loop_binding_kind(route.et, bool(gen.const_loop_var),
                              copies=gen.copies_var)
            if route.et is not None else None)
    if route.unpack_types is not None and name == gen.var:
        return _mk(site, name, typ, K.COMP_UNPACK_HOLDER, loop_binding=loop)
    if route.unpack_types is not None:
        # A record element and a pointer-repr Optional element (its
        # storage optional) bind a reference into the element, const as
        # the clause's loop variable; any other element its value.
        tt = route.unpack_types[gen.unpack_vars.index(name)]
        const = bool(gen.const_loop_var)
        if _p._optional_ptr_borrow(tt, an) is not None:
            return _mk(site, name, typ, K.COMP_UNPACK_STORAGE_OPT,
                       storage_opt=True, const=const)
        if _p.record_like(unwrap_readonly(unwrap_send_sync(tt)), an):
            return _mk(site, name, typ, K.COMP_UNPACK_REF, const=const)
        return _mk(site, name, typ, K.COMP_UNPACK_VALUE)
    etp = unwrap_readonly(route.et) if route.et is not None else None
    if isinstance(etp, TupleType) and etp.has_pointer_repr_element():
        return _mk(site, name, typ, K.COMP_STORAGE_TUPLE, storage_tuple=True,
                   loop_binding=loop)
    if _p._foreach_storage_opt_elem(route.et, an):
        ctx = _PlanCtx(inp, visible, site.func or inp.func)
        src = (gen.iterable.obj if isinstance(gen.iterable, TpyMethodCall)
               else gen.iterable)
        return _mk(site, name, typ, K.COMP_STORAGE_OPT, storage_opt=True,
                   const_storage_opt=_st._iteration_yields_const(src, ctx,
                                                                 an),
                   loop_binding=loop)
    return _mk(site, name, typ, K.COMP_PLAIN, loop_binding=loop)


@dataclass(frozen=True)
class _CaseFacts:
    """What a match case gives its captures' rows: `_match_captures`'s per
    name (type, value_opt, pointer), the names sema binds by value, and
    whether the subject the captures alias is const."""
    captures: Mapping[str, tuple]
    by_value: frozenset[str]
    subject_const: bool


def _match_row(site, name, typ, inp: 'PlanInputs', visible):
    """A fresh match capture (the match tiers' bind modes): the `auto` copy
    of a pointer subject an Optional chain binds whole, else the `auto`
    copy sema binds by value or the `auto&` alias, const as the subject it
    aliases into."""
    facts: _CaseFacts = site.route
    _t, value_opt, pointer = facts.captures.get(name, (None, None, False))
    if pointer:
        return _mk(site, name, typ, K.MATCH_SUBJECT_PTR, pointer=True,
                   const=facts.subject_const)
    if name in facts.by_value:
        return _mk(site, name, typ, K.MATCH_COPY, value_opt=value_opt)
    return _mk(site, name, typ, K.MATCH_REF, value_opt=value_opt,
               const=facts.subject_const)


def _match_captures(stmt, case, an, pre) -> dict:
    """name -> (type, value_opt, pointer) for one arm's captures as the
    match tiers bind them: a keyword capture at its field's type (with the
    value-opt kind `_lower_field_subpatterns` binds it with), the Optional
    chain tiers' whole-subject capture at the full subject (binds full) or
    its inner type."""
    from . import match as _m
    out: dict = {}

    def field_capture(name, ft) -> None:
        if name in out or ft is None:
            return
        rec = _value_opt_of(_Site("match"), name, ft, an, K.MATCH_REF)
        out[name] = (ft, rec.value_opt if rec is not None else None, False)

    def walk(p) -> None:
        if isinstance(p, TpyAsPattern):
            walk(p.pattern)
        elif isinstance(p, TpyOrPattern):
            for alt in p.patterns:
                walk(alt)
        elif isinstance(p, TpyClassPattern):
            for fname, sub in p.keywords:
                as_node = sub if isinstance(sub, TpyAsPattern) else None
                inner = sub.pattern if as_node is not None else sub
                ft = _m._match_record_field_type(p, fname, an)
                if isinstance(inner, TpyLiteralPattern):
                    if as_node is not None:
                        field_capture(as_node.name,
                                      unwrap_readonly(ft) if ft else None)
                elif isinstance(inner, TpyCapturePattern):
                    field_capture(inner.name,
                                  unwrap_readonly(ft) if ft else None)
                elif isinstance(inner, TpyClassPattern):
                    walk(inner)
            for sub in p.positional:
                walk(sub)

    walk(case.pattern)
    strategy = _m._match_strategy(stmt, an)
    if strategy not in ("if_elif_optional", "if_elif_optional_guarded"):
        return out
    parts = _m._match_arm_parts(case)
    if parts is None or parts[1] is None:
        return out
    test, bnode = parts
    subj = unwrap_readonly(stmt.subject_type)
    is_always = test is None or isinstance(test, TpyWildcardPattern)
    binds_full = is_always and getattr(case.pattern, "binds_full_optional",
                                       False)
    if not binds_full:
        out[bnode.name] = (subj.inner, None, False)
        return out
    if not subj.uses_pointer_repr():
        out[bnode.name] = (subj, ValueOptKind.SCALAR, False)
        return out
    # A pointer subject bound whole is an arm-local `T*` copy unless the
    # match hoisted the name (the hoist's record) or the subject is a
    # pointer-slot global read through its pointee.
    derefs = (isinstance(stmt.subject, TpyName)
              and stmt.subject.name in pre.global_slots)
    out[bnode.name] = (subj, None, not derefs)
    return out


def _def_row(site, name, typ, inp: 'PlanInputs', visible):
    """A nested def binds its name as a closure local (statements.py
    `_lower_nested_def`, resumable.py's frame member pass)."""
    return _mk(site, name, typ, K.DEF_CLOSURE_LOCAL, nested_def=True)


_ROWS: dict[str, tuple] = {
    "param": (_param_row,),
    "receiver": (_receiver_row,),
    "global": (_global_row,),
    "module_global": (_module_global_row,),
    "frame": (_frame_row,),
    "decl": (_decl_row,),
    "hoist": (_hoist_row,),
    "loop": (_loop_row,),
    "with": (_with_row,),
    "walrus": (_walrus_row,),
    "unpack": (_unpack_row,),
    "comp": (_comp_row,),
    "def": (_def_row,),
    "match": (_match_row,),
}


# --- the walk --------------------------------------------------------------

class _Planner:
    """The walk: one scope node per block, a new node after each declaring
    statement, and the node current at every statement and scoping
    expression recorded in `table.scope_at`."""

    def __init__(self, inp: PlanInputs, table: BindingTable) -> None:
        self.inp = inp
        self.table = table
        self.analyzer = inp.analyzer
        self.func = inp.func
        self.in_branch = False
        self.prescan = None
        self.sema_movable = None
        # A statically-true @overload arm ending in a return / raise drops
        # the rest of the function's top-level statements.
        self.terminated = False
        # The stub's facts the fold reads; a nested def's own names leave
        # them, as they leave the lowering's (`shadow_scope`).
        self.overload_narrowing = inp.overload_narrowing
        self.overload_literal_facts = inp.overload_literal_facts
        # The for statement whose head unpack is being planned: a
        # suspending loop's head binds the frame's own fields.
        self.loop: TpyForEach | None = None

    def _site(self, kind: str, node=None, init=None) -> _Site:
        return _Site(kind, node, init, in_branch=self.in_branch,
                     func=self.func, prescan=self.prescan,
                     sema_movable=self.sema_movable)

    def _record(self, site: _Site, name: str, typ, node: ScopeNode
                ) -> BindingRepr:
        rec = classify_binding(site, name, typ, self.inp, node.lookup)
        self._key(site.node, rec)
        return rec

    def _key(self, key: object, rec: BindingRepr) -> None:
        """Key `rec` at the one site that declares it: two records of a
        name under one key would make `declared_by` ambiguous."""
        if key is not None:
            self.table.sites[key] = self.table.sites.get(key, ()) + (rec,)

    def block(self, stmts: list[TpyStmt], node: ScopeNode, *,
              branch: bool = True, top_level: bool = False) -> ScopeNode:
        saved = self.in_branch
        saved_terminated = self.terminated
        self.in_branch = saved or branch
        try:
            for s in stmts:
                if top_level and self.terminated:
                    break
                node = self.stmt(s, node)
        finally:
            self.in_branch = saved
            if not top_level:
                self.terminated = saved_terminated
        return node

    def stmt(self, s: TpyStmt, node: ScopeNode) -> ScopeNode:
        saved = self.loop
        if not (isinstance(s, TpyTupleUnpack) and s.is_loop_head):
            self.loop = None
        try:
            return self._stmt(s, node)
        finally:
            self.loop = saved

    def _stmt(self, s: TpyStmt, node: ScopeNode) -> ScopeNode:
        an = self.analyzer
        # An @overload fold lowers the arm it selects flat in the enclosing
        # block, before (and instead of) the statement's hoists.
        if isinstance(s, TpyIf):
            folded = self._folded_if(s, node)
            if folded is not None:
                return folded
        if isinstance(s, TpyMatch):
            folded = self._folded_match(s, node)
            if folded is not None:
                return folded
        # A statement's sema hoists are predeclared before any of it lowers,
        # its condition's walrus included.
        hoists = an.if_branch_decls.get(s) or {}
        hrec = {n: self._record(self._site("hoist", s), n,
                                unwrap_ref_type(t) if t is not None else None,
                                node)
                for n, t in hoists.items() if node.lookup(n) is None}
        hrec.update(self._frame_hoists(s, hoists, node))
        node = node.child(hrec)
        node = self._walrus_scope(s, node)
        if isinstance(s, TpyTupleUnpack) and not s.is_loop_head:
            node = node.child(self._unpack_promotions(s, node))
        self.table.scope_at[s] = node
        for e in _statement_level_exprs(s):
            self.table.stmt_expr_scope[e] = node
            self.expr(e, node)
        if isinstance(s, TpyVarDecl):
            if node.lookup(s.name) is None:
                rec = self._record(self._site("decl", s, s.init), s.name,
                                   _var_decl_type(s, an), node)
                return node.child({s.name: rec})
            return node
        if isinstance(s, TpyAssign):
            if (isinstance(s.target, TpyName)
                    and node.lookup(s.target.name) is None):
                rec = self._record(self._site("assign", s, s.value),
                                   s.target.name,
                                   an.get_expr_type(s.value), node)
                return node.child({s.target.name: rec})
            return node
        if isinstance(s, TpyTupleUnpack):
            recs = {}
            fr = self.inp.frame
            # A suspending loop's head binds the frame's own fields.
            frame_head = (s.is_loop_head and fr is not None
                          and self.loop is not None
                          and _suspends(self.loop))
            site = self._site("unpack", s)
            if s.is_loop_head and self.loop is not None:
                site = replace(site, loop=self.loop, loop_visible=(
                    self.table.scope_at[self.loop].lookup))
            for i, n in enumerate(s.targets):
                if n is None or n in recs or (
                        not s.is_loop_head and node.lookup(n) is not None):
                    continue
                if (frame_head and n in fr.fields
                        and node.lookup(n) is not None):
                    continue
                tt = s.target_types[i] if i < len(s.target_types) else None
                if s.is_loop_head and head_target_writes_outer(
                        s, i, tt, node.lookup(n), an):
                    continue
                recs[n] = self._record(site, n, tt, node)
            return node.child(recs)
        if isinstance(s, TpyIf):
            self.block(s.then_body, node)
            self.block(s.else_body, node)
            return node
        if isinstance(s, TpyWhile):
            self.block(s.body, node)
            self.block(s.orelse, node)
            return node
        if isinstance(s, TpyForEach):
            # The loop variable is a fresh C++ binding of the body, shadowing
            # any outer one of the same spelling -- except a frame field a
            # suspending loop's skeleton binds, which stays the field.
            fr = self.inp.frame
            if (fr is not None and s.var in fr.fields
                    and node.lookup(s.var) is not None and _suspends(s)):
                self.loop = s
                self.block(s.body, node)
                self.block(s.orelse, node)
                return node
            rec = self._record(self._site("loop", s), s.var,
                               _loop_var_type(s, an), node)
            self.loop = s
            self.block(s.body, node.child({s.var: rec}))
            self.block(s.orelse, node)
            return node
        if isinstance(s, TpyWith):
            cur = node
            for item in s.items:
                self.table.scope_at[item] = cur
                # A later item's manager reads the earlier items' targets.
                self.table.stmt_expr_scope[item.context_expr] = cur
                self.expr(item.context_expr, cur)
                if item.target is not None and cur.lookup(item.target) is None:
                    rec = self._record(self._site("with", item), item.target,
                                       item.enter_type, cur)
                    cur = cur.child({item.target: rec})
            self.block(s.body, cur)
            # The targets outlive the statement.
            return cur
        if isinstance(s, TpyTry):
            self.block(s.try_body, node)
            for h in s.handlers:
                hnode = node
                if h.binding is not None:
                    from .statements import _handler_binding_type
                    rec = self._record(self._site("except", h), h.binding,
                                       _handler_binding_type(h, an), node)
                    hnode = node.child({h.binding: rec})
                self.table.scope_at[h] = hnode
                self.block(h.body, hnode)
            self.block(s.else_body, node)
            self.block(s.finally_body, node)
            return node
        if isinstance(s, TpyMatch):
            after: dict[str, BindingRepr] = {}
            for case in s.cases:
                caps = self._case_captures(s, case, node)
                cnode = node.child(caps)
                self.table.scope_at[case] = cnode
                if case.guard is not None:
                    self.table.stmt_expr_scope[case.guard] = cnode
                    self.expr(case.guard, cnode)
                self.block(case.body, cnode)
                for n, r in caps.items():
                    # A capture the case declares is local to its arm.
                    if r.site_kind != "match":
                        after.setdefault(n, r)
            # A capture into a binding in scope (a refinement of it)
            # outlives the statement; a capture the case declares is local
            # to its arm, so a later binding of the name declares its own.
            return node.child(after)
        if isinstance(s, TpyNestedDef):
            func = s.func
            rec = (self._record(self._site("def", s), func.name, None, node)
                   if node.lookup(func.name) is None else None)
            outer = node.child({func.name: rec} if rec is not None else {})
            self.function_scope(func, outer, s.nonlocal_names)
            return outer
        for sub in s.sub_bodies():
            self.block(sub, node)
        return node

    def _walrus_scope(self, s: TpyStmt, node: ScopeNode) -> ScopeNode:
        """A walrus target is the enclosing block's from its statement on."""
        wal = {}
        for w in walrus_bindings(s):
            if w.target not in wal and node.lookup(w.target) is None:
                wal[w.target] = self._record(
                    self._site("walrus", w, w.value), w.target,
                    _walrus_type(w, self.analyzer), node)
        return node.child(wal)

    def _folded_if(self, s: TpyIf, node: ScopeNode) -> ScopeNode | None:
        """`_lower_overload_folded_if`'s walk over the same plan. None where
        the lowering does not fold (or rejects)."""
        plan = _p.overload_if_fold(s, self.overload_narrowing,
                                   self.overload_literal_facts)
        if plan is None or plan.reject is not None:
            return None
        # A folded-away link is never lowered, so only the statement and
        # the live links are keyed.
        self.table.scope_at[s] = node
        if plan.flat is not None:
            end = self.block(plan.flat, node, branch=False)
            if plan.terminates:
                self.terminated = True
            return end
        # The first live condition lowers at the statement, so its walrus
        # targets outlive it as an unfolded `if`'s do.
        node = self._walrus_scope(plan.live[0], node)
        self.table.scope_at[s] = node
        for link in plan.live:
            self.table.scope_at[link] = node
            for e in link.exprs():
                self.table.stmt_expr_scope[e] = node
                self.expr(e, node)
            self.block(link.then_body, node)
        self.block(plan.else_body, node)
        return node

    def _folded_match(self, s: TpyMatch, node: ScopeNode
                      ) -> ScopeNode | None:
        """`_lower_overload_folded_match`'s walk over the same plan: the
        selected case's captures and body in the enclosing block, so its
        declarations outlive the statement. None where the lowering does
        not fold (or rejects)."""
        plan = _p.overload_match_fold(s, self.overload_narrowing)
        if plan is None or plan.reject is not None:
            return None
        self.table.scope_at[s] = node
        cnode = node.child(self._case_captures(s, plan.case, node))
        self.table.scope_at[plan.case] = cnode
        return self.block(plan.case.body, cnode, branch=False)

    def _case_captures(self, s: TpyMatch, case: TpyMatchCase,
                       node: ScopeNode) -> dict[str, BindingRepr]:
        """The records one case's captures declare, as the match tiers bind
        them."""
        caps: dict[str, BindingRepr] = {}
        facts = _match_captures(s, case, self.analyzer,
                                self.prescan or self.inp.prescan)
        first: dict[str, bool] = {}
        for cap in iter_capture_bindings(case.pattern):
            first.setdefault(cap.name, cap.bind_by_value)
        by_value = {n for n, v in first.items() if v}
        ctx = _PlanCtx(self.inp, node.lookup, self.func, (s.subject,))
        site = replace(self._site("match", case), route=_CaseFacts(
            facts, frozenset(by_value), capture_const(s.subject, ctx)))
        for cap in iter_capture_bindings(case.pattern):
            prev = node.lookup(cap.name)
            fact = facts.get(cap.name)
            if prev is not None and prev.ptr_tuple is not None:
                # The capture writes the pointer tuple's own binding, whose
                # elements no capture places one by one.
                note_detail("ptr_tuple.match_capture_bind")
                raise ThirUnsupported(
                    stmt_reject_reason(s),
                    message=ptr_tuple_route_message(
                        cap.name, "a match capture"),
                    loc=getattr(case, "loc", None) or s.loc)
            if (prev is not None and cap.name not in caps
                    and fact is not None and fact[1] is not None
                    and prev.value_opt is None):
                # `match.optional_full_bind`: a whole-Optional capture
                # into an existing binding (a frame field) gives it the
                # value-opt kind past the statement.
                caps[cap.name] = replace(prev, value_opt=fact[1],
                                         refinements=prev.refinements + (R.MATCH_BIND,))
                self._key(case, caps[cap.name])
                continue
            if cap.name in caps or prev is not None:
                continue
            typ = (facts[cap.name][0] if cap.name in facts
                   else cap.bound_type)
            caps[cap.name] = self._record(site, cap.name, typ, node)
        return caps

    def _clause_route(self, comp, gen, node: ScopeNode):
        """The lowering's route for one clause, computed over the visible
        records' types; None where the lowering rejects the clause (its body
        is then never lowered, so its variables are never read)."""
        from .comprehensions import _COMP_KINDS, _clause_route
        ctx = _PlanCtx(self.inp, node.lookup, self.func, (gen.iterable,))
        try:
            return _clause_route(_COMP_KINDS[type(comp)], gen,
                                 ctx.declared_view, self.analyzer, ctx)
        except ThirUnsupported:  # a route the lowering rejects
            return None

    def _frame_hoists(self, s, hoists, node: ScopeNode
                      ) -> dict[str, BindingRepr]:
        """`_register_frame_hoist`: a sema hoist over a frame field the walk
        has not declared yet gives the field its value-opt kind from the
        hoisting statement on (the frame setup decides it only at a field's
        first initializing decl)."""
        fr = self.inp.frame
        out: dict[str, BindingRepr] = {}
        if fr is None or self.func is not self.inp.func:
            return out
        for n, raw in hoists.items():
            prev = node.lookup(n)
            if (prev is None or prev.site_kind != "frame" or prev.refinements
                    or n in fr.entry_declared or prev.value_opt is not None):
                continue
            vt = fr.local_types.get(n, raw)
            vt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vt)))
                  if isinstance(vt, TpyType) else None)
            kind = _value_opt_of(_Site("hoist"), n, vt, self.analyzer, K.HOIST_VALUE)
            if kind is not None:
                out[n] = replace(prev, value_opt=kind.value_opt,
                                 refinements=prev.refinements + (R.FRAME_HOIST,))
                self._key(s, out[n])
        return out

    def _unpack_promotions(self, s: TpyTupleUnpack, node: ScopeNode
                           ) -> dict[str, BindingRepr]:
        """The TpyTupleUnpack arm: the standalone unpack promotes EVERY target,
        a reused one too (a reassigning VarDecl never promotes the name it
        rebinds), and before its source lowers -- so the reused binding is
        movable from the statement's own source read on."""
        movable = (self.sema_movable if self.sema_movable is not None
                   else self.inp.sema_movable)
        out: dict[str, BindingRepr] = {}
        for n in s.targets:
            if n is None or n in out or n not in movable:
                continue
            prev = node.lookup(n)
            if prev is not None and not prev.movable:
                out[n] = replace(prev, movable=True,
                                 refinements=prev.refinements + (R.UNPACK_PROMOTE,))
                self._key(s, out[n])
        return out

    def function_scope(self, func: TpyFunction, parent: ScopeNode,
                       nonlocal_names: 'AbstractSet[str]') -> None:
        params = [n for n, _t in func.params]
        own = frozenset(scope_bound_names(func.body, params))
        recs = {n: BindingRepr(name=n, site=None, type=t,
                               kind=K.NESTED_PARAM_PLAIN,
                               site_kind="nested_param", is_param=True)
                for n, t in func.params}
        # The nested body's prescan carries no pointer-slot globals
        # (`nested_def_prescan`): an enclosing one reads there as the
        # pointer its record says it is, not as a global slot.
        outer_pre = self.prescan or self.inp.prescan
        for n in outer_pre.global_slots:
            outer = parent.lookup(n)
            if n not in own and outer is not None and outer.global_slot:
                recs[n] = replace(outer, global_slot=False,
                                  refinements=outer.refinements + (R.NESTED_DEF,))
        root = ScopeNode(parent, recs, own)
        self.table.scope_at[func] = root
        saved = (self.func, self.in_branch, self.prescan, self.sema_movable,
                 self.overload_narrowing, self.overload_literal_facts)
        self.prescan = nested_def_prescan(
            func, prescan_without(outer_pre, own), self.analyzer,
            nonlocal_names)
        self.sema_movable = frozenset()
        self.func, self.in_branch = func, False
        self.overload_narrowing = _without_names(self.overload_narrowing, own)
        self.overload_literal_facts = _without_names(
            self.overload_literal_facts, own)
        try:
            self.block(func.body, root, branch=False)
        finally:
            (self.func, self.in_branch, self.prescan, self.sema_movable,
             self.overload_narrowing, self.overload_literal_facts) = saved

    def expr(self, e, node: ScopeNode) -> None:
        if not isinstance(e, TpyExpr):
            return
        if isinstance(e, TpyLambda):
            types = list(e.inferred_param_types)
            recs = {n: BindingRepr(
                        name=n, site=e,
                        type=types[i] if i < len(types) else None,
                        kind=K.LAMBDA_PLAIN, site_kind="lambda",
                        is_param=True,
                        param_type=types[i] if i < len(types) else None)
                    for i, n in enumerate(e.param_names)}
            lnode = ScopeNode(node, recs, frozenset(e.param_names))
            self.table.scope_at[e] = lnode
            self.expr(e.body, lnode)
            return
        if isinstance(e, TpyGeneratorExpression):
            # Its body is a function of its own (`frame_func`), planned when
            # that function is lowered; only the first source and the range
            # bounds are read here, at the enclosing node.
            if e.generators:
                self.expr(e.generators[0].iterable, node)
            for a in e.frame_range_args:
                self.expr(a, node)
            return
        if isinstance(e, (TpyListComprehension, TpySetComprehension,
                          TpyDictComprehension)):
            cur = node
            for gen in e.generators:
                self.expr(gen.iterable, cur)
                route = self._clause_route(e, gen, cur)
                site = replace(self._site("comp", gen), route=route,
                               comp=e)
                recs = {n: self._record(site, n, t, cur)
                        for n, t in _clause_types(e, gen, route, self.analyzer)}
                if route is not None and route.unpack_types is not None:
                    recs[gen.var] = self._record(site, gen.var, route.et, cur)
                cur = cur.child(recs)
                self.table.scope_at[gen] = cur
                for c in gen.conditions:
                    self.expr(c, cur)
            self.table.scope_at[e] = cur
            if isinstance(e, TpyDictComprehension):
                self.expr(e.key_expr, cur)
                self.expr(e.value_expr, cur)
            else:
                self.expr(e.element_expr, cur)
            return
        for c in e.children():
            self.expr(c, node)


def _statement_level_exprs(s: TpyStmt) -> list[TpyExpr]:
    """The expressions a statement lowers at its own scope node: a `with`
    item's manager and a `case` guard are planned (and lowered) under the
    item's / the case's node instead."""
    if isinstance(s, TpyWith):
        return []
    if isinstance(s, TpyMatch):
        return [s.subject]
    return s.exprs()


def _walrus_type(w, analyzer) -> TpyType | None:
    """The type the walrus arms declare their target at: the named
    expression's own type, literals resolved, wrappers peeled."""
    t = analyzer.get_expr_type(w)
    if t is None:
        return None
    t = resolve_int_literals(t, analyzer.ctx.default_int_for_literal)
    return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))


def _loop_var_type(stmt: TpyForEach, analyzer) -> TpyType | None:
    """The type the for statement's body declares its variable at: the
    element, borrowed whatever the source hands over (`_unwrap_own`)."""
    from . import statements as _st
    et = _st._resolved_loop_elem_type(stmt, analyzer)
    return _unwrap_own(et) if et is not None else None


def _suspends(stmt: TpyForEach) -> bool:
    """Whether a loop suspends a resumable frame (an `async for`, or a
    `yield` / `await` in its body): its variable is then the frame's."""
    if stmt.is_async:
        return True
    found = False

    def on_stmt(s) -> None:
        nonlocal found
        found = found or isinstance(s, TpyYield)

    def on_expr(e) -> None:
        nonlocal found

        def visit(x) -> bool:
            nonlocal found
            if isinstance(x, TpyAwait):
                found = True
            return not found
        walk_expr_tree(e, visit)
    walk_body_stmts([*stmt.body, *stmt.orelse], on_expr, on_stmt)
    return found


def _clause_types(comp, gen: TpyComprehensionGenerator, route, analyzer
                  ) -> list[tuple[str, TpyType | None]]:
    """The names a comprehension clause binds with the types its body reads
    them at -- `_clause_bindings`, the producer the lowering's body scope
    reads; an Array result's source arm binds the element unpeeled."""
    from .comprehensions import _clause_bindings
    if route is None:
        return [(n, None) for n in gen.targets]
    if route.unpack_types is None:
        rt = analyzer.get_expr_type(comp)
        if rt is not None and is_array(rt):
            return [(gen.var, route.et)]
    return _clause_bindings(gen, route)


def plan_bindings(inp: PlanInputs, body: list[TpyStmt]) -> BindingTable:
    """The binding table of one body: the seeded bindings at its root, one
    record per declaring site in source order, then the whole-body const
    fixpoint over the records (`_plan_const`). A declaration row reads
    that fixpoint (`_PlanCtx`), so a body where it marks a name is planned
    again with it until the two agree."""
    if inp.frame is not None and inp.frame_facts is None:
        inp = replace(inp, frame_facts=_frame_facts(inp))
    table = _plan_once(inp, body)
    for _round in range(_CONST_ROUNDS):
        fix = _plan_const(inp, table)
        if (fix.btuple == inp.btuple_const
                and fix.const_slots == inp.const_slots):
            break
        inp = replace(inp, btuple_const=fix.btuple,
                      const_slots=fix.const_slots)
        table = _plan_once(inp, body)
    else:
        raise BindingTableError(
            f"const fixpoint of {inp.func.name} did not settle in "
            f"{_CONST_ROUNDS} planning rounds")
    _reject_mixed_slot(table, fix)
    _reject_const_ptr_tuple(table, inp, fix)
    _mark_const(table, *inp.btuple_const, fix, inp.analyzer)
    return table


def _reject_const_ptr_tuple(table: BindingTable, inp: PlanInputs,
                            fix: _ConstFix) -> None:
    """A pointer tuple bound from a read-only source -- a `readonly[...]`
    parameter, a read-only receiver's field: its owning binding is a
    mutable source, so the slot is mixed and rejected at its first
    read-only source, as the scalar's mixed pointer slot is
    (`_reject_mixed_slot`, the same detail). A source the body could write
    is no such source: sema marks every pointer-tuple binding's roots
    mutated (`finalize_pointer_tuples`), as the scalar's rebind does. The
    borrow-tuple const bit is per name and any-source (TODO.md "One home
    per binding fact -- slices 4 and 5" unifies it with the slots')."""
    for rec in table.records():
        if (rec.ptr_tuple is None or rec.name not in fix.btuple[0]
                or ptr_tuple_form(rec.name, inp, inp.func) is None):
            continue
        stmts = fix.btuple_sources.get(rec.name, ())
        if not stmts:
            continue
        stmt = min(stmts, key=_line)
        raise ThirUnsupported(
            stmt_reject_reason(stmt, "decl.reseat_source"),
            loc=getattr(stmt, "loc", None))


# Each planning round starts from the closure of the last, so a body
# settles in two; more means the rows and the fixpoint disagree.
_CONST_ROUNDS = 8


def _borrow_tuple_const_sources(func: TpyFunction, analyzer, reassigned,
                                hoisted) -> tuple[dict, dict]:
    """Every binding source of a reassigned / hoisted pointer-repr-tuple
    local, with the statement it sits in: `(plain, nullable)` maps of
    target name -> [(source, statement)], the nullable targets split by
    their DECLARED type (`tuple[..] | None`) -- the const fixpoint's
    input."""
    plain: dict[str, list] = {}
    nullable: dict[str, list] = {}
    optional_targets: set[str] = set()
    for tgt, src, stmt, is_walrus in binding_source_sites(func.body):
        if isinstance(stmt, TpyVarDecl) and not is_walrus:
            tt = resolve_stmt_binding_type(stmt, analyzer,
                                           include_global_binding=False)
            if tt is None:
                tt = analyzer.get_expr_type(stmt.init)
            if tt is not None:
                tt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(tt)))
            if isinstance(tt, OptionalType) and tt.wraps_pointer_repr_tuple():
                optional_targets.add(stmt.name)
        if tgt not in reassigned and tgt not in hoisted:
            continue
        st = analyzer.get_expr_type(src)
        stb = unwrap_readonly(unwrap_ref_type(st)) if st is not None else None
        if not ((isinstance(stb, TupleType)
                 and stb.has_pointer_repr_element())
                or (isinstance(stb, OptionalType)
                    and stb.wraps_pointer_repr_tuple())):
            continue
        (nullable if tgt in optional_targets
         else plain).setdefault(tgt, []).append((src, stmt))
    return plain, nullable


# The families whose pointer record is the name's one C++ slot: a
# declaration's, a hoist's, a walrus's and a with target's (a loop, unpack
# or capture binding is a fresh C++ variable of its own).
_SLOT_FAMILIES = frozenset({"decl", "hoist", "walrus", "with"})


def _slot_family(rec: BindingRepr) -> bool:
    # Judged by the declaring site, whatever kind the record's row picks:
    # the kind (a pointer or not) itself reads the verdict, so keying on it
    # would not settle.
    return rec.site_kind in _SLOT_FAMILIES


def _is_slot(rec: BindingRepr) -> bool:
    # A pointer-variant union (`::tpy::Union<A*, B*>`) is a pointer slot too.
    return ((rec.pointer or rec.ptr_variant) and not rec.optional_storage
            and _slot_family(rec))


def _slot_const(rec: BindingRepr, const_slots: AbstractSet, an) -> bool:
    """A slot's const: every source bound into it is const (the
    fixpoint's `const_slots`), or its declared type is read-only."""
    return slot_key(rec) in const_slots or _declared_const(rec, an)


@dataclass(frozen=True)
class _ConstFix:
    """The whole-body const fixpoint: the borrow-tuple names (plain,
    nullable), the pointer slots every source bound into which is const,
    and per record the statements binding a const source into it."""
    btuple: tuple
    const_slots: frozenset
    const_sources: Mapping[SlotKey, tuple]
    # The matches capturing a const loop variable into a pointer slot:
    # sema does not credit a write through the capture to the loop
    # variable, so its const is unproven
    # (BUGS.md#match-capture-loop-var-write-not-credited).
    unproven: tuple = ()
    # Per const borrow-tuple name, the statements binding a const source
    # into it.
    btuple_sources: Mapping[str, tuple] = field(default_factory=dict)


def _answers(pred, src: TpyExpr, ctx: '_PlanCtx') -> bool:
    try:
        return pred(src, ctx)
    except ThirUnsupported:  # unanswerable
        return False


def _plan_const(inp: PlanInputs, table: BindingTable) -> _ConstFix:
    """The const fixpoint over the table, each source judged against the
    records visible at its own statement. A borrow-tuple local is const
    when some source bound into it is. A pointer slot is const when every
    source bound into it is (a match capture's source is its subject, a
    with target's its `__enter__` result; a None points at nothing): the
    largest such set, so a chain or a cycle
    of slots (`d2 = d1`, `n = n.next`) rooted in const sources is const.
    The candidates are the slots some source of which is const once the
    slots it reads are."""
    func = inp.func
    plain, nullable = _borrow_tuple_const_sources(
        func, inp.analyzer, inp.prescan.reassigned, inp.prescan.hoisted)
    binds = []
    loop_var_captures = []
    for tgt, src, stmt, _w in binding_source_sites(func.body,
                                                     all_binders=True):
        # A with target's record is its item's.
        at = src if isinstance(src, TpyWithItem) else stmt
        rec = table.declared_by(at, tgt) or table.visible(at, tgt)
        if rec is not None and not isinstance(src, TpyNoneLiteral):
            binds.append((slot_key(rec), _slot_family(rec), src, stmt))
            if (isinstance(stmt, TpyMatch) and src is stmt.subject
                    and _is_slot(rec)):
                loop_var_captures.append(stmt)
    const_set: set[str] = set()
    opt_set: set[str] = set()
    slots: set[SlotKey] = set()

    def ctx_at(stmt) -> _PlanCtx:
        node = table.scope_at.get(stmt) or table.root
        ctx = _PlanCtx(inp, node.lookup, func)
        ctx.btuple_const = (const_set, opt_set)
        ctx.const_slots = slots
        return ctx

    def is_const(src, stmt) -> bool:
        if isinstance(src, TpyWithItem):
            # The bound value is the `__enter__` result, const by its
            # declared result access.
            return _readonly_type(src.enter_type)
        return _answers(_p._walrus_src_is_const, src, ctx_at(stmt))

    changed = True
    while changed:
        changed = False
        for srcs_of, cs in ((plain, const_set), (nullable, opt_set)):
            for name, srcs in srcs_of.items():
                if name not in cs and any(is_const(src, stmt)
                                          for src, stmt in srcs):
                    cs.add(name)
                    changed = True
        for key, slot, src, stmt in binds:
            if slot and key not in slots and is_const(src, stmt):
                slots.add(key)
                changed = True
    changed = True
    while changed:
        changed = False
        for key, slot, src, stmt in binds:
            if key in slots and not is_const(src, stmt):
                slots.discard(key)
                changed = True
    const_sources: dict[SlotKey, list] = {}
    for key, _slot, src, stmt in binds:
        if is_const(src, stmt):
            const_sources.setdefault(key, []).append(stmt)
    unproven = tuple(
        stmt for stmt in loop_var_captures
        if _rooted_in_const_loop_var(stmt.subject, ctx_at(stmt)))
    btuple_sources = {
        name: tuple(stmt for src, stmt in plain[name] if is_const(src, stmt))
        for name in const_set}
    return _ConstFix((frozenset(const_set), frozenset(opt_set)),
                     frozenset(slots),
                     {k: tuple(v) for k, v in const_sources.items()},
                     unproven, btuple_sources)


def _rooted_in_const_loop_var(expr: TpyExpr, ctx: _PlanCtx) -> bool:
    root = receiver_root(expr, ctx.analyzer.get_expr_type)
    return root is not None and ctx.binding(root).const_loop_var


def _reject_mixed_slot(table: BindingTable, fix: _ConstFix) -> None:
    """A pointer slot holding both a const and a mutable source has no C++
    form: `T*` cannot take the const one, and `const T*` refuses a write a
    mutable source's alias may make through it. Rejected where it binds
    the const source, or where a capture binds an unproven const loop
    variable (`_ConstFix.unproven`); the first such statement."""
    found = [(stmt, "match.capture_const_loop_var") for stmt in fix.unproven]
    for rec in table.records():
        if _is_slot(rec) and slot_key(rec) not in fix.const_slots:
            found.extend((stmt, "decl.reseat_source")
                         for stmt in fix.const_sources.get(slot_key(rec), ()))
    if found:
        stmt, detail = min(found, key=lambda f: _line(f[0]))
        raise ThirUnsupported(stmt_reject_reason(stmt, detail),
                              loc=getattr(stmt, "loc", None))


def _line(stmt) -> int:
    loc = getattr(stmt, "loc", None)
    return loc.line if loc is not None else 0


def _mark_const(table: BindingTable, plain: frozenset,
                nullable: frozenset, fix: _ConstFix, an) -> None:
    """Write the fixpoint's bits on the records: the borrow-tuple bits on
    every record of a marked name, a slot's const (`_slot_const`) and
    every record's const-source bit, in every scope node and site entry
    that holds them."""
    new: dict[int, BindingRepr] = {}

    def swap(rec: BindingRepr) -> BindingRepr:
        key = slot_key(rec)
        sourced = key in fix.const_sources
        slot = _is_slot(rec)
        if (rec.name not in plain and rec.name not in nullable
                and not sourced and not slot):
            return rec
        got = new.get(id(rec))
        if got is None:
            got = replace(
                rec,
                const_borrow_tuple=rec.const_borrow_tuple or rec.name in plain,
                const_opt_borrow_tuple=(rec.const_opt_borrow_tuple
                                        or rec.name in nullable),
                const=(_slot_const(rec, fix.const_slots, an) if slot
                       else rec.const),
                const_source=sourced)
            new[id(rec)] = got
        return got

    seen: set[int] = set()
    nodes = [table.root, *table.scope_at.values(),
             *table.stmt_expr_scope.values()]
    while nodes:
        node = nodes.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        b = node.bindings
        for n in list(b):
            b[n] = swap(b[n])
        nodes.append(node.parent)
    for site in list(table.sites):
        table.sites[site] = tuple(swap(r) for r in table.sites[site])


def _plan_once(inp: PlanInputs, body: list[TpyStmt]) -> BindingTable:
    """One planning round: the seeded root, then the walk."""
    pre = inp.prescan
    param_names = {n for n, _t in inp.params}
    root_recs: dict[str, BindingRepr] = {}
    root = ScopeNode(None, root_recs)
    for name, typ in inp.seeds.items():
        if inp.const_scope:
            kind = "const"
        elif inp.frame is not None and name in inp.frame.fields \
                and name not in param_names:
            kind = "frame"
        elif name in param_names:
            kind = "param"
        elif name == inp.self_receiver or (name == "self"
                                           and inp.func.is_method):
            kind = "receiver"
        elif inp.top_level:
            kind = "module_global"
        elif pre.binds_global(name) or name in pre.global_write_cpp:
            kind = "global"
        else:
            kind = "stub_default"
        root_recs[name] = classify_binding(_Site(kind), name, typ, inp,
                                           root.lookup)
    if inp.self_receiver is not None and inp.self_receiver not in root_recs:
        root_recs[inp.self_receiver] = classify_binding(
            _Site("receiver"), inp.self_receiver, None, inp, root.lookup)
    if inp.frame is not None:
        # A resumable frame's nested defs are members of the frame, callable
        # from every resume case: bound from the frame's entry, not at their
        # statement.
        for nd in collect_frame_nested_defs(list(body)):
            if nd.func.name not in root_recs:
                root_recs[nd.func.name] = classify_binding(
                    _Site("def", nd), nd.func.name, None, inp, root.lookup)
    table = BindingTable(root)
    table.frame_members = tuple(n for n, rec in root_recs.items()
                                if rec.frame_class is not None)
    _Planner(inp, table).block(body, root, branch=False, top_level=True)
    return table


# --- installation and the walk's cursor ---------------------------------------

def _qualname(lc) -> str:
    mod = getattr(lc.analyzer.ctx, "module_name", "?")
    owner = f"{lc.record_name}." if lc.record_name else ""
    return f"{mod}:{owner}{lc.func.name}"


def loc_of(x) -> str:
    loc = getattr(x, "loc", None)
    if loc is None:
        return "?"
    return f"{getattr(loc, 'line', '?')}:{getattr(loc, 'col', '?')}"


def shadow_emit(lc, cls: str, name: str, loc: str, detail: str = "") -> None:
    """Append one entry to `SHADOW_SINK` (once per body and key)."""
    sink = SHADOW_SINK
    if sink is None:
        return
    key = (cls, name, loc, detail)
    seen = lc.shadow_report
    if key in seen:
        return
    seen.add(key)
    sink.append((cls, _qualname(lc), name, loc, detail))


def install(lc, table: BindingTable) -> None:
    """Attach `table` to `lc` with the cursor at its root; with a collector
    on `SHADOW_SINK`, report the body's records and the rows that decided
    them."""
    lc.table = table
    lc.cursor = (None, table.root)
    lc.shadow_report = set()
    sink = SHADOW_SINK
    if sink is None:
        return
    table.entered = IdentitySet()
    sink.append(("_stat:functions", _qualname(lc), "", "", ""))
    for rec in table.records():
        sink.append(("_stat:records", "", "", "", ""))
        sink.append(("_row:" + rec.kind.value + "".join(
            "+" + r.value for r in rec.refinements), "", "", "", ""))


def enter(lc, key) -> None:
    """Move the cursor to the scope node the planner recorded for `key` (a
    statement, a with item, an except handler, a match case, a
    comprehension clause, a lambda) -- the one way the walk opens a scope.
    A key the planner never saw is an internal error: the two walks
    disagree. The caller restores."""
    table = lc.table
    node = table.scope_at.get(key)
    if node is None:
        node = table.stmt_expr_scope.get(key)
    if node is None and isinstance(key, TpyLambda):
        node = table.lambda_scope(key, lc.cursor[1])
    if node is None:
        shadow_emit(lc, f"cursor_miss:{type(key).__name__}", "", loc_of(key))
        raise BindingTableError(
            f"no scope planned for {type(key).__name__} at {loc_of(key)} "
            f"in {_qualname(lc)}")
    if table.entered is not None:
        table.entered.add(key)
    lc.cursor = (key, node)


def scoped(lc: _LowerCtx, keys: Iterable[object]) -> Iterator[object]:
    """Each of `keys` (a `with` statement's items, a `try`'s handlers, a
    `match`'s cases) with the cursor at its scope while the caller lowers
    it, restored after the last."""
    saved = lc.cursor
    try:
        for key in keys:
            enter(lc, key)
            yield key
    finally:
        lc.cursor = saved


def report_unentered(lc: _LowerCtx,
                     decomposed: AbstractSet[object] = frozenset()
                     ) -> None:
    """With a collector on `SHADOW_SINK`: every scope the planner keyed for
    the body just walked that the walk never entered, by key kind. A
    statement or handler a resumable body's CFG decomposed (`decomposed`,
    the `CFG`'s own record) has no lowering of its own -- its condition,
    items and body leaves enter their own keys -- so it is counted apart,
    as `scope_unentered_cfg`."""
    table = getattr(lc, "table", None)
    if SHADOW_SINK is None or table is None or table.entered is None:
        return
    for key in table.scope_at:
        if key in table.entered:
            continue
        cls = ("scope_unentered_cfg" if key in decomposed
               else "scope_unentered")
        shadow_emit(lc, f"{cls}:{type(key).__name__}", "", loc_of(key))


def require_entered(lc: _LowerCtx, key: object) -> None:
    """A with item, an except handler or a match case is built with the
    cursor at its own scope: its target, binding or captures and every read
    in it resolve there. A route that builds one elsewhere would read the
    enclosing scope's bindings, so it is an internal error."""
    if lc.cursor[0] is not key:
        raise BindingTableError(
            f"{type(key).__name__} at {loc_of(key)} built outside its scope "
            f"(cursor at {type(lc.cursor[0]).__name__}) in {_qualname(lc)}")


@contextmanager
def at(lc, key) -> Iterator[None]:
    """`enter` for a region, restoring the cursor after it."""
    saved = lc.cursor
    enter(lc, key)
    try:
        yield
    finally:
        lc.cursor = saved


def plan_and_install(lc, body: list[TpyStmt], seeds: Mapping[str, TpyType],
                     *, frame: FrameSeed | None = None,
                     top_level: bool = False,
                     module_globals: AbstractSet[str] = frozenset(),
                     readonly_self: bool = False,
                     const_scope: bool = False) -> None:
    """Build the body's binding table and attach it. A body with no form
    for a binding raises `ThirUnsupported`; any other planning failure is
    an internal error: no body lowers without its table."""
    inp = PlanInputs(
        func=lc.func, analyzer=lc.analyzer, prescan=lc.prescan,
        params=lc.params, seeds=dict(seeds),
        self_receiver=lc.self_receiver, record_name=lc.record_name,
        readonly_self=readonly_self,
        top_level=top_level, module_globals=frozenset(module_globals),
        frame=frame, sema_movable=frozenset(lc.sema_movable_locals),
        const_scope=const_scope,
        overload_narrowing=lc.overload_narrowing,
        overload_literal_facts=dict(lc.overload_literal_facts))
    try:
        table = plan_bindings(inp, body)
    except ThirUnsupported:
        raise
    except Exception as ex:
        raise RuntimeError(
            f"binding table: planning {_qualname(lc)} failed: "
            f"{type(ex).__name__}: {ex}") from ex
    install(lc, table)


def rebind_ptr_frame_value(init: TpyExpr, vtype: TpyType, lc: '_LowerCtx',
                           declared: dict[str, TpyType], loc,
                           reason: str) -> 'THIRExpr':
    """The rvalue an OWN or IN_PLACE write to a REBIND_PTR frame local of
    type `vtype` stores, at any binding site (assignment, walrus). The
    source fills the SLOT, so it is admitted and lowered against what the
    slot holds -- the pointee for a pointer-repr Optional local."""
    from .statements import (_lower_storage_value, _rebind_rvalue_source_ok,
                             _rebind_rvalue_use, _rebind_slot_target)
    slot_t = _rebind_slot_target(vtype, lc.analyzer)
    if not _rebind_rvalue_source_ok(init, slot_t, lc.analyzer,
                                    frame_roots_ok=True):
        note_detail("res.rebind_ptr_source")
        raise ThirUnsupported(reason)
    return _lower_storage_value(init, slot_t, lc, declared, loc,
                                use=_rebind_rvalue_use(init, slot_t))


def rebind_ptr_frame_field(lc: '_LowerCtx', site: object,
                           reason: str) -> str:
    """The frame field the ptr-slot prescan reserved for an OWN write to a
    REBIND_PTR frame local at `site`; a site it reserved none for
    rejects."""
    fld = rcfg.ptr_slot_field(lc.func, site)
    if fld is None:
        note_detail("res.rebind_ptr_slot")
        raise ThirUnsupported(reason)
    return fld
