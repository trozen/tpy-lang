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

from collections.abc import (Callable, Iterable, Iterator, Mapping,
                             Set as AbstractSet)
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
from ...identity_map import IdentityMap, IdentitySet
from ...modules.type_resolution import is_native_iterable
from ...parse.nodes import (RebindStorage, TpyArrayLiteral,
                            TpyAsPattern,
                            TpyAssign, TpyAwait, TpyCall, TpyCapturePattern,
                            TpyClassPattern, TpyCoerce,
                            TpyComprehensionGenerator, TpyDictComprehension,
                            TpyDelVar, TpyDictLiteral, TpyExpr, TpyFieldAccess,
                            TpyForEach, TpyFunction, TpyGeneratorExpression,
                            TpyGlobal,
                            TpyIf, TpyIfExpr, TpyLambda, TpyListComprehension,
                            TpyListRepeat, TpyLiteralPattern, TpyMatch,
                            TpyMatchCase, TpyMethodCall, TpyName, TpyNestedDef,
                            TpyNoneLiteral, TpyNonlocal, TpyOrPattern,
                            TpySetComprehension,
                            TpySetLiteral, TpySlice, TpyStmt, TpySubscript,
                            TpyTry, TpyTupleLiteral, TpyTupleUnpack,
                            TpyVarDecl, TpyWhile, TpyWildcardPattern, TpyWith,
                            TpyWithItem,
                            TpyYield, TupleElemCapture,
                            iter_capture_bindings, program_names,
                            walk_body_stmts, walk_expr_tree, walrus_bindings,
                            written_names)
from ...prescan import scan_first_bindings, scope_bound_names
from ...sema.alias_rebind import (BindKind, bind_kind_of,
                                  final_pointer_tuple_names, leaf_paths,
                                  pointer_tuple_owned, tuple_name_bindings)
from ...sema.binding_ownership import (NOT_OWNED, Acquired, BindingOwnership,
                                       by_value_optional,
                                       frame_field_ownership,
                                       generator_object,
                                       lambda_param_ownership,
                                       local_ownership,
                                       param_ownership,
                                       receiver_ownership, site_ownership)
from ...sema.storage_policy import StorageVerdict, first_binding_storage
from ...sema.storage_walk import pointee_root
from ...sema.own_copy import contains_reference_type
from ...type_def_registry import (is_array, is_bytes_view_type,
                                  is_owned_in_coro_frame,
                                  is_str_view_type)
from ...typesys import (ConcreteGenType, FloatLiteralType, IntLiteralType,
                        NominalType, OptionalType, OwnType, PendingListType,
                        peel_binding,
                        PendingViewType, ReadonlyType, TpyType, TupleType,
                        RecursiveAliasInstanceType, TypeParamRef, UnionType,
                        collapse_tuple_own_elements,
                        contains_pending_leaf, is_dyn_protocol,
                        is_own_pointer_repr_optional,
                        pointer_repr_optional, resolve_int_literals,
                        unwrap_optional_own, unwrap_own, unwrap_readonly,
                        unwrap_ref_type, unwrap_send_sync, ValueForm,
                        view_family_for_type)
from ...value_category import (call_hands_back_value, call_returns_cpp_ref,
                               is_rvalue_source)
from ..nodes import Form
from ..reject import ThirUnsupported, note_detail, stmt_reject_reason
from ..source import UNBOUND, BindingRepr, Held, SlotKey, slot_key
from ..source import BindingConstruct as BC
from ..source import BindingForm as F
from ..source import BindingRefinement as R
from ..source import MoveRow as M
from . import checks as _checks
from . import predicates as _p
from ...sema.call_lends import MayLend
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
    from ...sema.analyzer import SemanticAnalyzer
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
    "const_storage_tuple", "borrow_tuple_mixed",
    "tuple_layout", "inline_elems", "const", "const_loop_var",
    "movable", "owned",
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
    # The root of an inner function's scope (a nested def, a lambda): a
    # binding found past it is the enclosing body's, captured.
    function: bool = False

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

    def captures(self, name: str) -> bool:
        """Whether `name` resolves to an enclosing body's binding, past the
        root of the inner function this node sits in."""
        node: ScopeNode | None = self
        crossed = False
        while node is not None:
            if name in node.bindings:
                return crossed
            if name in node.shadow:
                return False
            crossed = crossed or node.function
            node = node.parent
        return False

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
    __slots__ = ("root", "inp", "scope_at", "sites", "stmt_expr_scope",
                 "entered", "frame_members")

    def __init__(self, root: ScopeNode, inp: 'PlanInputs') -> None:
        self.root = root
        # The inputs the body was planned with: a lambda the walk never met
        # classifies its parameters with them.
        self.inp = inp
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
            node = ScopeNode(parent, lambda_param_records(lam, self.inp),
                             frozenset(lam.param_names), function=True)
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
    # The slots every source bound into which is a const borrow
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
    borrow their pointee, and why a field the frame owns is no move
    source (`exceptions`)."""
    classes: Mapping[str, FrameLocalClass]
    loops: 'FrameLoopFacts'
    borrowed: frozenset[str]
    exceptions: Mapping[str, 'M']


def _frame_facts(inp: PlanInputs) -> _FrameFacts:
    from . import resumable as _res
    func = inp.func
    classes, loops = inp.frame.classes, inp.frame.loops
    # A frame-promoted STORAGE slot moves with no value-type filter: a
    # last-use read moves out of the slot rather than copying it, which is
    # how a BigInt frame local moves at an async return. A pointer-form
    # local moves only when the frame owns its pointee (the Optional-ptr
    # local's `__ptr_slot_fN` storage, as the plain body's OPT_PTR_SLOT
    # decl row); loop-var, alias and unpack pointers borrow. Only names a
    # `TpyVarDecl` (or an unpack) binds: a frame-promoted loop variable
    # moves only off a consuming iterable (the loop row), and a value
    # record copies wherever it lives.
    borrowed = frozenset(loops.ptr_loop_vars | loops.unpack_ptr_targets
                         | {n for n, c in classes.items()
                            if c is FrameLocalClass.ALIAS_PTR})
    decl_names = _res._var_decl_names(list(func.body))
    walrus_names = {w.target for s in _all_stmts(func.body)
                    for w in walrus_bindings(s)}
    locals_ = dict(func.generator_locals or [])
    exceptions: dict[str, M] = {}
    for n in inp.frame.fields:
        t = locals_.get(n)
        if n in borrowed or n not in locals_:
            exceptions[n] = M.FRAME_LAYOUT
        elif n not in decl_names and n not in walrus_names:
            exceptions[n] = M.FRAME_LAYOUT
        elif _value_record_slot(t):
            exceptions[n] = _value_exception(t)
    return _FrameFacts(classes, loops, borrowed, exceptions)


def _all_stmts(body: list[TpyStmt]) -> list[TpyStmt]:
    out: list[TpyStmt] = []
    walk_body_stmts(body, lambda e: None, out.append)
    return out


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
    if rec is not None and rec.frame_owned_view:
        # The frame's own buffer (or its optional), read whole or through
        # the unwrap.
        return Held.STORAGE
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
    if _is_string_owned(rtype) or (rec is not None and (
            rec.storage_tuple or rec.owned
            # An owned step materialized into its storage type.
            or rec.loop_binding is LoopBinding.OWNED_STORAGE)):
        return Held.STORAGE
    if rec is not None and (rec.borrow_tuple_mixed
                            or rec.form is F.BORROW_TUPLE):
        # A borrow tuple whose type cannot say so (an open-T element's
        # borrow form is `val_or_ptr_t<T>`) holds the form its record took.
        return Held.BORROWED
    return Held.BORROWED if _is_borrow_form_name(rtype) else Held.VALUE


def form_of(held: Held):
    """The `Form` a read held as `held` renders in (`Held`'s mapping)."""
    if held is Held.BORROWED:
        return Form.BORROW
    if held in (Held.STORAGE, Held.FRESH):
        return Form.STORAGE
    return Form.VALUE


def tuple_select(arms: tuple[TpyExpr, TpyExpr], helds: tuple[Held, Held],
                 t: 'TupleType') -> 'Held | None':
    """The held form a one-level select of two tuple arms of type `t`
    takes: two NAMES holding the borrow form, of a tuple whose borrow form
    copies nothing (`has_copy_free_borrow_form`), are that borrow (`auto y
    = ((c) ? (x) : (z));`). None for every other select -- a storage, call
    or nested arm, an owning element -- which keeps the value rule. The one
    decision for both the binding planner (helds from the arms' records)
    and the select's lowering (helds from the lowered arms)."""
    if (all(isinstance(a, TpyName) for a in arms)
            and helds == (Held.BORROWED, Held.BORROWED)
            and t.has_copy_free_borrow_form()):
        return Held.BORROWED
    return None


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


def ptr_tuple_element_message(name: str, index: int, et: TpyType, *,
                              per_branch: bool = False) -> str:
    """The sentence for a rebound tuple whose owned element is no plain
    reference: such an element has no reference local of its own yet.
    `per_branch`: every binding of the name is part of its first one (one
    per arm of an `if` or `try`), so none rebinds it."""
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(unwrap_own(et))))
    kind = ("an Optional" if isinstance(bare, OptionalType)
            else "a union" if isinstance(bare, UnionType)
            else "a generic type parameter" if isinstance(bare, TypeParamRef)
            else "a nested tuple" if isinstance(bare, TupleType)
            else f"a {bare}")
    if per_branch:
        return (f"binding '{name}' in more than one branch while it owns "
                f"element {index}, {kind}, is not yet supported here; bind "
                f"each branch's value to a name of its own")
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
            firsts = scan_first_bindings(
                fn.body, [p for p, _ in fn.params]).get(name, ())
            per_branch = len(bindings) > 1 and all(
                any(b is f for f in firsts) for b in bindings)
            return refuse(first_owner, "ptr_tuple.owned_element_form",
                          ptr_tuple_element_message(
                              shown(), i, bt.element_types[i],
                              per_branch=per_branch))
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
    # In a nested def's body, whose standalone unpacks promote nothing.
    nested: bool = False
    # Facts the walk computed for the site: a comprehension clause's route
    # (`_clause_route`), a match arm's capture facts (`_match_captures`).
    route: object | None = None
    # A comprehension clause's comprehension, and (a clause head's target)
    # the clause.
    comp: TpyExpr | None = None
    clause: TpyComprehensionGenerator | None = None
    # A for head's unpack: its for statement, and the records visible at
    # it (the iteration source's scope).
    loop: TpyForEach | None = None
    loop_visible: object | None = None
    # The storage policy's verdict for the name (`first_binding_storage`)
    # and what the binding owns (`sema.binding_ownership`), filled by
    # `classify_binding` before the rows run.
    storage: StorageVerdict = StorageVerdict()
    ownership: BindingOwnership = NOT_OWNED


def _scope_rebinds(site: _Site, pre) -> AbstractSet[str]:
    """The names rebound within the scope a binding at `site` lives in: a
    for statement's variable and its head's unpack targets live in the
    loop body (each loop binds them afresh), every other binding in the
    whole body."""
    node = site.node
    if site.kind == "loop" and isinstance(node, TpyForEach):
        assert node.loop_body_rebinds is not None, "sema stamps every loop"
        return node.loop_body_rebinds
    if site.kind == "unpack" and node.is_loop_head:
        return node.loop_body_rebinds
    return pre.reassigned


def body_rebound_names(body: list[TpyStmt],
                       reassigned: AbstractSet[str]) -> frozenset[str]:
    """The names some binding of which sees them rebound within its scope
    (`_scope_rebinds`): the per-name answer for a body whose locals each
    hold one slot whatever binds them (a resumable frame's fields). A name
    only loop heads bind is rebound only by what their loop bodies
    rebind."""
    out: set[str] = set()

    def on_stmt(s: TpyStmt) -> None:
        if isinstance(s, TpyForEach):
            if s.var in _scope_rebinds(_Site("loop", s), None):
                out.add(s.var)
        elif isinstance(s, TpyTupleUnpack) and s.is_loop_head:
            out.update(n for n in s.targets
                       if n is not None and n in s.loop_body_rebinds)
    walk_body_stmts(body, lambda e: None, on_stmt)
    for site in binding_source_sites(body, all_binders=True,
                                     unpack_targets=True):
        head = (isinstance(site.binder, TpyTupleUnpack)
                and site.binder.is_loop_head)
        if not head and site.target in reassigned:
            out.add(site.target)
    return frozenset(out)


def classify_binding(site: _Site, name: str, typ: TpyType | None,
                     inp: PlanInputs, visible) -> BindingRepr:
    """The ONE classifier: the record a binding gets, from its declared
    type, its initializer's shape, sema's per-function facts and the
    records `visible` resolves (the bindings declared before the site)."""
    if site.prescan is not None:
        inp = replace(inp, prescan=site.prescan)
    pre = inp.prescan
    own = _ownership(site, name, typ, inp)
    site = replace(site, storage=first_binding_storage(
        site.kind, typ, rebound=name in _scope_rebinds(site, pre),
        hoisted=name in pre.escape_hoisted), ownership=own)
    rec = None
    for row in _ROWS.get(site.kind, ()):
        rec = row(site, name, typ, inp, visible)
        if rec is not None:
            break
    else:
        rec = BindingRepr(name=name, site=site.node, type=typ,
                          form=F.NONE, site_kind=site.kind,
                          is_param=site.kind in ("param", "lambda",
                                                 "nested_param"),
                          move_exception=_no_move(typ))
    # A lambda's parameters live in the lambda's scope, never the frame's.
    if (inp.frame is not None and name in inp.frame.const_bindings
            and site.kind != "lambda"
            and (site.func is None or site.func is inp.func)):
        rec = replace(rec, frame_const=True)
    return with_movable(rec, own)


# The rows no read moves out of in place, which stand only where fact 1
# says the binding owns: an alias of other storage (owned, it is an
# all-fresh Optional select the planner plans as a call passthrough, which
# the lowering refuses at `expr.ifexpr`), a const pointer (over an owning
# slot, a readonly result is still owned), and module storage (where the
# planner's module-global test and sema's declared globals disagree, the
# name stays unmoved).
_OWNED_ONLY_ROWS = frozenset({M.ALIAS, M.CONST_SLOT, M.MODULE_STORAGE})


def with_movable(rec: BindingRepr, own: BindingOwnership) -> BindingRepr:
    """`rec` with its `movable`: a forced row moves; otherwise the binding
    moves where it owns what it holds and no exception row stands."""
    if rec.move_exception in _OWNED_ONLY_ROWS and not own:
        rec = replace(rec, move_exception=None)
    movable = rec.move_forced is not None or (
        bool(own) and rec.move_exception is None)
    return rec if rec.movable == movable else replace(rec, movable=movable)


def _ownership(site: '_Site', name: str, typ: TpyType | None,
               inp: PlanInputs) -> BindingOwnership:
    """What the binding a site declares owns (`sema.binding_ownership`):
    a parameter (a nested def's included), a lambda's parameter and the
    receiver by their signature, a frame field and a local by their
    function's entry, an iteration binder, a `with` item, an `except`
    handler and a `match` case by its site's."""
    ctx = inp.analyzer.ctx
    kind = site.kind
    if kind == "param":
        return param_ownership(inp.func, name, typ)
    if kind == "nested_param":
        return param_ownership(site.func, name, typ)
    if kind == "lambda":
        return lambda_param_ownership(typ)
    if kind == "receiver":
        return (receiver_ownership(inp.func) if name == inp.self_receiver
                else NOT_OWNED)
    if kind == "frame":
        return frame_field_ownership(ctx, inp.func, name)
    if kind in ("loop", "comp", "with", "except", "match") or (
            kind == "unpack" and site.node.is_loop_head):
        return site_ownership(ctx, site.node, name)
    if kind in ("decl", "assign", "hoist", "walrus", "unpack"):
        func = site.func or inp.func
        # A module's names are module storage.
        if inp.top_level and func is inp.func:
            return NOT_OWNED
        return local_ownership(ctx, func, name)
    return NOT_OWNED


def _owned_reference_elements(own: BindingOwnership,
                              tt: TupleType) -> tuple[bool, ...]:
    """Per top-level element of a binding of tuple `tt`, whether it is a
    reference element every leaf of which the binding owns (`own`)."""
    return tuple(
        TupleType._element_is_pointer_repr(et)
        and own.owns(tt, [p for p in leaf_paths(tt) if p[:1] == (i,)])
        for i, et in enumerate(tt.element_types))


def _value_exception(t: TpyType | None) -> M:
    """Why a value-typed binding is no move source: moving equals copying,
    or its render copies a buffered value at its last use."""
    bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        _unwrap_own(t)))) if isinstance(t, TpyType) else None)
    if bare is None or not bare.is_expensive_copy():
        return M.TRIVIAL_VALUE
    if view_family_for_type(bare) is not None or _is_string_owned(bare):
        return M.VIEWFAM_VALUE
    return M.OWNED_VALUE


def _no_move(t: TpyType | None) -> M:
    """The exception of a binding no row gives a move: a value's, else no
    form of its own."""
    if isinstance(t, TpyType) and t.is_value_type():
        return _value_exception(t)
    return M.NO_FORM


def _param_row(site, name, typ, inp: PlanInputs, visible):
    """A parameter's record, from its declared type."""
    func, analyzer = inp.func, inp.analyzer
    f: dict = {}
    u = _uw(typ)
    ptype = next((t for n, t in inp.params if n == name), None)
    if inp.frame is not None and name in inp.frame.fields:
        f["frame_class"] = inp.frame.classes.get(name, FrameLocalClass.PLAIN)
        # A genexpr's written capture is the enclosing variable's storage,
        # never a copy the frame owns.
        if (name not in func.genexpr_nonlocals
                and isinstance(typ, TpyType) and is_owned_in_coro_frame(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(typ))))):
            f["frame_owned_view"] = True
    # The const verdicts of the function whose parameter it is; `THIRParam`
    # and every reader in the body read them off this record.
    fi = _p._owning_fi(func, analyzer, inp.record_name)
    f["const"] = _p._param_is_const_at(name, func, fi)
    f["deep_const"] = (
        _p._param_is_deep_const(name, func, analyzer, inp.record_name)
        or _p._vararg_pack_is_const(name, func)
        or _p._ptr_pointee_is_readonly(name, func))
    if name in func.genexpr_nonlocals:
        # A generator expression's written capture is the enclosing scope's
        # variable, held by mutable reference: the record of that variable's
        # local slot, never an owned copy and never const.
        f["const"] = f["deep_const"] = False
        vo = (_declared_value_opt_kind(typ, analyzer)
              if isinstance(typ, TpyType) else None)
        if vo is not None:
            f["value_opt"] = vo
        return BindingRepr(name=name, site=None, type=typ,
                           form=_seeded_form(f), site_kind=site.kind,
                           is_param=True,
                           param_type=ptype if isinstance(ptype, TpyType)
                           else None, **f)
    # A generator expression's capture of its creator's pointer tuple
    # (sema's final set of the frame) is that pointer tuple.
    ptt = (pointer_tuple_of(typ) if typ is not None and name
           in final_pointer_tuple_names(analyzer.ctx, func) else None)
    if ptt is not None:
        return BindingRepr(name=name, site=None, type=typ,
                           form=F.BORROW_TUPLE,
                           site_kind=site.kind, is_param=True,
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
    open_borrow = False
    if isinstance(u, OwnType):
        inner = unwrap_readonly(u.wrapped)
        if (isinstance(inner, TupleType)
                and inner.has_pointer_or_copy_free_borrow_form()):
            f["storage_tuple"] = True
    elif isinstance(u, TupleType) and u.is_owned_movable():
        f["storage_tuple"] = True
    elif (isinstance(u, TupleType) and not u.has_pointer_repr_element()
          and _p._binding_form_tuple(u, analyzer) is not None):
        # The signature passes an open-T tuple in its borrow form
        # (`val_or_ptr_t<T>`), which its type alone does not say.
        open_borrow = True
    if isinstance(typ, TpyType) and unwrap_optional_own(
            unwrap_readonly(unwrap_send_sync(typ))) is not None:
        f["owned"] = True
    own = unwrap_optional_own(u) if isinstance(typ, TpyType) else None
    if (not isinstance(u, TupleType) and own is not None
            and own.wrapped.is_value_type()
            and not (isinstance(unwrap_readonly(own.wrapped), TupleType)
                     and contains_reference_type(own.wrapped))):
        # An ownership-transfer tuple moves with no value filter.
        f["move_exception"] = _value_exception(own.wrapped)
    if isinstance(u, TupleType) and u.is_mixed_own():
        # A capture holds whatever the enclosing variable holds: a
        # parameter, a call result and a module global all hold the mixed
        # render a mixed call result binds.
        f["borrow_tuple_mixed"] = True
    if by_value_optional(typ) is not None:
        f["move_forced"] = M.BY_VALUE_OPTIONAL
    if open_borrow and _seeded_form(f) is F.NONE:
        return BindingRepr(name=name, site=None, type=typ,
                           form=F.BORROW_TUPLE, site_kind=site.kind,
                           is_param=True, effective_type=u,
                           param_type=ptype if isinstance(ptype, TpyType)
                           else None, **f)
    return BindingRepr(name=name, site=None, type=typ, form=_seeded_form(f),
                       site_kind=site.kind,
                       is_param=True,
                       param_type=ptype if isinstance(ptype, TpyType)
                       else None, **f)


def _seeded_form(f: dict) -> F:
    """The form of a parameter or a seeded global, from the facts its row
    decided: the owned storage optional of an `Own[P | None]` parameter,
    a pointer, a pointer union, a value-repr Optional, a storage tuple, a
    mixed own / borrow tuple or a module tuple global's pointer-slot
    layout; none of them the type's own C++."""
    if f.get("optional_storage"):
        return F.OWNED_SLOT
    if f.get("pointer"):
        return F.POINTER
    if f.get("ptr_variant"):
        return F.POINTER_UNION
    if f.get("value_opt") is not None:
        return F.VALUE_OPT
    if f.get("storage_tuple"):
        return F.STORAGE_TUPLE
    if f.get("borrow_tuple_mixed") or f.get("tuple_layout") is not None:
        return F.BORROW_TUPLE
    return F.NONE


def _except_row(site, name, typ, inp: PlanInputs, visible):
    """An `except` target: the handler's `const E&` alias of the caught
    exception, which holds nothing of its own."""
    return BindingRepr(name=name, site=site.node, type=typ, form=F.NONE,
                       site_kind=site.kind, ref_alias=True,
                       move_exception=_no_move(typ))


def _receiver_row(site, name, typ, inp: PlanInputs, visible):
    """The method receiver: const for a readonly method (functions.py,
    resumable.py), movable for a consuming one (context.py)."""
    if name != inp.self_receiver and not (name == "self" and inp.readonly_self):
        return None
    return BindingRepr(
        name=name, site=None, type=typ, form=F.NONE, site_kind=site.kind,
        const=inp.readonly_self)


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
    return BindingRepr(name=name, site=None, type=typ, form=_seeded_form(f),
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
    return BindingRepr(name=name, site=None, type=typ, form=_seeded_form(f),
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
    if (cls in (C.OWNING_TUPLE_SLOT, C.OPT_TUPLE_HOLDER)
            or name in ff.loops.ptr_storage_tuple_loop_vars):
        # The field holds the storage tuple itself: its Optional elements
        # are storage optionals.
        f["storage_tuple"] = True
    if cls is C.VALUE_OPT_RECORD:
        f["value_opt"] = ValueOptKind.RECORD
    else:
        kind = _frame_value_opt(name, inp)
        if kind is not None:
            f["value_opt"] = kind
    own = site.ownership
    if own and own.acquired.from_iteration:
        # A field only loops bind moves out of the step it owns; a copied
        # step is a value's.
        if own.acquired is Acquired.COPIED_STEP:
            f["move_exception"] = _value_exception(typ)
        elif own.acquired not in (Acquired.OWN_ELEMENT,
                                  Acquired.CONSUMING_LOOP,
                                  Acquired.OWN_ITER_LOOP):
            f["move_exception"] = M.FRAME_LAYOUT
    elif name in ff.exceptions:
        f["move_exception"] = ff.exceptions[name]
    lay = fr.layout.get(name)
    if lay is not None:
        f["effective_type"] = getattr(lay, "effective_type", None)
        f["payload"] = getattr(lay, "payload", None)
    # The pointer tuple a frame field is, as a plain body's declaration is
    # (`_btuple_rebound_decl`).
    ptt = ptr_tuple_form(name, inp, inp.func)
    if ptt is not None:
        f["ptr_tuple"] = ptt
    return BindingRepr(name=name, site=None, type=typ, form=_frame_form(cls),
                       site_kind=site.kind, **f)


_FRAME_POINTER_CLASSES = frozenset({
    FrameLocalClass.LOOP_PTR, FrameLocalClass.UNPACK_PTR,
    FrameLocalClass.ALIAS_PTR, FrameLocalClass.OPT_PTR,
    FrameLocalClass.REBIND_PTR})


def _frame_form(cls: 'FrameLocalClass | None') -> F:
    """A frame field's form, from its class: a pointer field, a frame
    slot, the value Optional record field, the borrow tuple field, the
    erased handle (a `@dynamic` local); any other field holds its value."""
    if cls in _FRAME_POINTER_CLASSES:
        return F.POINTER
    if cls is not None and cls.is_frame_slot:
        return F.FRAME_SLOT
    if cls is FrameLocalClass.VALUE_OPT_RECORD:
        return F.VALUE_OPT
    if cls is FrameLocalClass.BORROW_TUPLE:
        return F.BORROW_TUPLE
    if cls is FrameLocalClass.ERASED_HANDLE:
        return F.DYN
    return F.VALUE


def _declared_value_opt_kind(vtype: TpyType, analyzer
                             ) -> 'ValueOptKind | None':
    """The value-repr Optional kind of a binding of `vtype` by its type
    alone (a scalar, an owned view): a narrowed read derefs through it."""
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
        self.const_slots = inp.const_slots

    def binding(self, name: str) -> BindingRepr:
        rec = self._visible(name) if isinstance(name, str) else None
        return self.with_fixpoint(UNBOUND if rec is None else rec)

    def with_fixpoint(self, rec: BindingRepr) -> BindingRepr:
        """`rec` with its slot's const from the fixpoint, as the records
        carry it once planning ends (`_mark_const`)."""
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


# The C++ local shape a first declaration spells, by its form and how its
# initializer constructs it.
_DECL_SHAPE: dict[tuple[F, BC], LocalBinding] = {
    (F.REFERENCE, BC.REF_ALIAS): LocalBinding.REF_ALIAS,
    (F.POINTER, BC.POINTER): LocalBinding.POINTER,
    (F.POINTER, BC.REBIND_SLOT): LocalBinding.REBIND_SLOT,
    (F.POINTER, BC.OPTIONAL_TO_PTR): LocalBinding.OPTIONAL_TO_PTR,
    (F.POINTER, BC.OPT_CALL_PASSTHROUGH): LocalBinding.OPTIONAL_TO_PTR,
    (F.POINTER, BC.STORAGE_OPT_LIFT): LocalBinding.OPTIONAL_TO_PTR,
    (F.POINTER, BC.OPT_OWN_PARAM_LIFT): LocalBinding.OPTIONAL_TO_PTR,
    (F.POINTER, BC.MIXED_ELEM_PTR): LocalBinding.OPTIONAL_TO_PTR,
    (F.STORAGE_TUPLE, BC.STORAGE_TUPLE_ALIAS):
        LocalBinding.STORAGE_TUPLE_ALIAS,
    (F.STORAGE_TUPLE, BC.STORAGE_TUPLE_CHAIN):
        LocalBinding.STORAGE_TUPLE_ALIAS,
    (F.POINTER_UNION, BC.PTR_VARIANT): LocalBinding.PTR_VARIANT,
}


def decl_shape(rec: BindingRepr) -> 'LocalBinding | None':
    """The C++ local shape a first declaration's record spells
    (`THIRVarDecl.cpp_local_representation`)."""
    if rec.site_kind != "decl" or rec.construct is None:
        return None
    return _DECL_SHAPE.get((rec.form, rec.construct))


# The form and construction each borrow-local shape `_borrow_local_binding`
# returns declares (OPT_PTR_SLOT takes its own row; OTHER declares no
# borrow local).
_BORROW_LOCAL_FORM: dict[LocalBinding, tuple[F, BC]] = {
    LocalBinding.REF_ALIAS: (F.REFERENCE, BC.REF_ALIAS),
    LocalBinding.POINTER: (F.POINTER, BC.POINTER),
    LocalBinding.REBIND_SLOT: (F.POINTER, BC.REBIND_SLOT),
    LocalBinding.OPTIONAL_TO_PTR: (F.POINTER, BC.OPTIONAL_TO_PTR),
}


def _mk(site: '_Site', name: str, typ, form: F, construct: 'BC | None',
        **f) -> BindingRepr:
    return BindingRepr(name=name, site=site.node, type=typ, form=form,
                       construct=construct, site_kind=site.kind, **f)


def _decl_row(site, name, typ, inp: 'PlanInputs', visible):
    """A first VarDecl's record: the declaration ladder's form, then whether
    it is a second name for an iterator object, which it holds the same way
    whatever form the ladder picked."""
    bind = _decl_local_binding(site, typ, inp, visible)
    rec = _decl_ladder(site, name, typ, inp, visible, bind)
    init, pre = site.init, inp.prescan
    if (isinstance(init, TpyName) and _protocol_auto_slot(typ)
            and name not in pre.reassigned and name not in pre.hoisted):
        # A structural-protocol or Self slot is never a value type, so the
        # ladder's reference-type tail always decides a form for it.
        if rec is None:
            raise BindingTableError(
                f"protocol-typed declaration of {name!r} got no form")
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
            ctx.names_with("pointer"), ctx, rebound=site.storage.pointer)
    except ThirUnsupported:  # a refusal is no binding
        return None


def _decl_ladder(site, name, typ, inp: 'PlanInputs', visible, bind):
    """A first VarDecl's form -- the representation its declaration holds,
    which `_lower_first_decl` builds -- and its facts."""
    from . import statements as _st
    an, pre = inp.analyzer, inp.prescan
    init = site.init
    fn = site.func or inp.func
    vtype = typ
    if init is None:
        # An annotation-only decl: a default-constructed value slot of the
        # try-hoist family (`_try_hoist_type_ok`), else the null pointer of
        # a pointer-repr Optional.
        if (pointer_repr_optional(vtype) is not None
                and not _try_hoist_type_ok(vtype, an)):
            return _mk(site, name, typ, F.POINTER, BC.NO_INIT_OPT_PTR,
                       pointer=True,
                       rebind_slot=site.storage.pointer)
        if vtype is not None and _try_hoist_type_ok(vtype, an):
            slot_t = _st._value_slot_render_type(vtype, an)
            rec = _mk(site, name, typ, F.VALUE, BC.NO_INIT_VALUE,
                      value_opt=_declared_value_opt_kind(typ, an),
                      move_exception=_value_exception(typ))
            return (replace(rec, effective_type=slot_t)
                    if slot_t is not vtype else rec)
        return None
    # A plain function's generator local a `del` closes is an
    # OPTIONAL_STORAGE slot, movable whatever it owns, and the decl lowers
    # as a reassign of it.
    if (not (fn.is_generator or fn.is_async)
            and name in an.function_closed_frames.get(fn, ())):
        if isinstance(peel_binding(unwrap_ref_type(typ)),
                      ConcreteGenType):
            return _mk(site, name, typ, F.OWNED_SLOT, BC.CLOSED_FRAME,
                       pointer=True,
                       move_forced=M.CLOSED_FRAME, optional_storage=True)
    # An @error_return call's unwrap: an alias of a borrow-aliasing result,
    # else a bind.
    er_fi = _st._error_return_stmt_fi(init, an)
    if er_fi is not None:
        if call_returns_cpp_ref(an, er_fi):
            return _mk(site, name, typ, F.POINTER, BC.ER_ALIAS, pointer=True)
        bare = (unwrap_readonly(unwrap_ref_type(er_fi.return_type))
                if isinstance(er_fi.return_type, TpyType) else None)
        inner = bare.wrapped if isinstance(bare, OwnType) else bare
        ptr = (site.storage.pointer and _p._f1_record(inner, an)
               and is_plain_nonvalue(inner)
               and not (fn.is_generator or fn.is_async))
        return _mk(site, name, typ, F.POINTER if ptr else F.VALUE,
                   BC.ER_BIND, pointer=ptr,
                   rebind_slot=ptr)
    if _open_t_escaping_capture(site, name, typ, inp):
        return _mk(site, name, typ, F.NONE, None, open_t_storage_copy=True,
                   move_exception=_no_move(typ))
    rec = _decl_value_rows(site, name, typ, inp, visible, bind)
    if rec is None and _open_t_storage_copy_decl(site, name, typ, inp,
                                                 visible):
        return _mk(site, name, typ, F.NONE, None, open_t_storage_copy=True,
                   move_exception=_no_move(typ))
    if (isinstance(vtype, TupleType) and vtype.has_own_element()
            and not vtype.has_pointer_repr_element()
            and name not in pre.reassigned):
        # An Own-element value tuple holds its elements in storage, whatever
        # declaration builds it: reads stay storage and it moves like an
        # owned local.
        if rec is None:
            return _mk(site, name, typ, F.STORAGE_TUPLE, BC.OWN_ELEM_TUPLE,
                       storage_tuple=True)
        return replace(rec, storage_tuple=True, move_exception=None,
                       move_forced=None)
    return rec


def _open_t_escaping_capture(site, name, typ, inp: 'PlanInputs') -> bool:
    """An open-T tuple local an escaping closure captures keeps the
    storage copy whatever row its source would take: the closure outlives
    what a borrow form points at
    (BUGS.md#escaping-closure-borrow-tuple-capture)."""
    an = inp.analyzer
    fn = site.func or inp.func
    return (name in an.function_escaping_captured_locals.get(fn, ())
            and _p._open_t_tuple_slot(typ, an) is not None
            and _p._value_bounded_tuple_slot(typ, an) is None)


def _open_t_storage_copy_decl(site, name, typ, inp: 'PlanInputs', visible
                              ) -> bool:
    """An open-T tuple local no binding row took keeps the storage copy it
    compiles to at a value T where the borrow form is not decided for it: a
    reassigned local, a select (only the borrow name pair takes the borrow
    form, `tuple_select`), a copy of such a local, or a tuple whose borrow
    form could copy (`has_copy_free_borrow_form`)."""
    an = inp.analyzer
    ot = _p._open_t_tuple_slot(typ, an)
    if ot is None or _p._value_bounded_tuple_slot(typ, an) is not None:
        return False
    init = site.init
    if (not ot.has_copy_free_borrow_form() or name in inp.prescan.reassigned
            or isinstance(init, TpyIfExpr)):
        return True
    if isinstance(init, TpyName):
        src = visible(init.name)
        return src is not None and src.open_t_storage_copy
    return False


def _decl_value_rows(site, name, typ, inp: 'PlanInputs', visible, bind):
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
    storage = site.storage
    if (isinstance(vtype, NominalType) and _p.record_like(vtype, an)
            and is_plain_nonvalue(vtype)
            and (storage.hoisted
                 or (storage.pointer and bind is not LocalBinding.REBIND_SLOT))
            and (_checks.copy_construct_source(
                init, an, ctx.names_with("pointer"))
                 is not None
                 or (name in pre.move_through and isinstance(init, TpyName))
                 or _checks._record_rvalue_source_shape(init, an))):
        return _mk(site, name, typ, F.POINTER,
                   (BC.RECORD_HOISTED_SLOT if storage.hoisted
                    else BC.RECORD_SLOT), pointer=True,
                   rebind_slot=storage.pointer)
    # A name-reassigned container literal: a pointer slot. An unresolved
    # pending list is this row too: its builder rejects it.
    vr = _p.resolve_pending_container(vtype, an) or vtype
    vru = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vr)))
    if (isinstance(init, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral,
                          TpyListRepeat))
            and storage.pointer and bind is not LocalBinding.REBIND_SLOT
            and name not in pre.hoisted and name not in pre.move_through
            and (isinstance(vru, PendingListType)
                 or _p._f1_container_ref(vru))):
        return _mk(site, name, typ, F.POINTER, BC.CONTAINER_SLOT, pointer=True,
                   effective_type=vru,
                   rebind_slot=storage.pointer)
    fixed = (name not in pre.reassigned and name not in pre.hoisted
             and name not in pre.move_through)
    # `decl.storage_opt_name_lift`: a storage-opt loop / comprehension var
    # lifted to `T*`.
    if (isinstance(init, TpyName) and fixed
            and pointer_repr_optional(vtype) is not None
            and _optional_ptr_borrow_wide(vtype, an) is not None):
        src = visible(init.name)
        if src is not None and src.storage_opt:
            return _mk(site, name, typ, F.POINTER, BC.STORAGE_OPT_LIFT,
                       pointer=True)
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
            and _checks._dyn_own_forward_call_arg(init, svt, an) is not None):
        return _mk(site, name, typ, F.VALUE, BC.DYN_OWN_ERASED,
                   effective_type=svt)
    if (not site.in_branch and isinstance(vtype, NominalType)
            and is_dyn_protocol(vtype)):
        if isinstance(svt_u, OwnType):
            # The deferred-erasure coroutine frame local is a value
            # `std::optional<frame>`, read as a pointer local like every
            # @dynamic local.
            return _mk(site, name, typ, F.DYN, BC.CORO_FRAME_LOCAL,
                       pointer=True, coro_frame=True,
                       move_exception=M.DYN)
        return _mk(site, name, typ, F.DYN, BC.DYN, pointer=True,
                   move_exception=M.DYN)
    # The nullable borrow tuple.
    if (not site.in_branch and _p._optional_borrow_tuple(vtype, an) is not None
            and name not in pre.hoisted and name not in pre.move_through
            and not (fn.is_generator or fn.is_async)):
        # A nullable-tuple call binds whatever its const-ness; past it a
        # const binding, and an init its builder cannot lower, take the
        # rows below.
        opt = _mk(site, name, typ, F.OPT_BORROW_TUPLE, BC.OPT_BTUPLE)
        if (_p._nullable_borrow_tuple_call(init, an) is not None
                and name not in pre.reassigned):
            return opt
        if not _slot_const(opt, ctx.const_slots, an):
            own_call = _p._btuple_owning_call_init(init, an)
            if (isinstance(init, TpyNoneLiteral) or own_call
                    or (isinstance(init, (TpyFieldAccess, TpySubscript))
                        and _st._borrow_tuple_source_ok(init, ctx))):
                return replace(opt, rebind_slot=bool(own_call)
                               and not isinstance(init, TpyNoneLiteral))
    # The borrow-local rows (`_decl_local_binding`): the form and the
    # construction that spell the row's shape.
    if bind is LocalBinding.OPT_PTR_SLOT:
        return _opt_ptr_slot_decl(site, name, typ, inp, ctx)
    shape = _BORROW_LOCAL_FORM.get(bind)
    if shape is not None:
        form, construct = shape
        ref = bind is LocalBinding.REF_ALIAS
        # A pointer is a slot, whose const is the fixpoint's
        # (`_slot_const`); a reference has its initializer alone.
        return _mk(site, name, typ, form, construct,
                   ref_alias=ref, pointer=not ref,
                   move_exception=M.ALIAS if ref else None,
                   const=ref and _p._walrus_src_is_const(init, ctx),
                   rebind_slot=not ref and site.storage.pointer)
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
            return _mk(site, name, typ, F.POINTER, BC.OPT_OWN_PARAM_LIFT,
                       pointer=True, move_exception=M.ALIAS)
    if (not site.in_branch and pointer_repr_optional(vtype) is not None
            and fixed
            and _p._tuple_local_ptr_elem_subscript(
                init, ctx.declared_view, ctx.names_with("borrow_tuple_mixed"), an)):
        # `decl.mixed_elem_ptr`: `e = p[1]` off a mixed own-borrow tuple
        # local.
        return _mk(site, name, typ, F.POINTER, BC.MIXED_ELEM_PTR, pointer=True,
                   move_exception=M.ALIAS)
    # The `auto&&` storage-tuple alias (and its chain).
    rec = _storage_tuple_alias(site, name, typ, inp, visible, ctx)
    if rec is not None:
        return rec
    # A comprehension local.
    if type(init) in _COMP_KINDS and not vtype.is_value_type():
        return _mk(site, name, typ, F.VALUE, BC.COMPREHENSION)
    # `decl.tparam_call`: an open-T local off a T-returning call binds the
    # `val_or_ref_t` trait and is never promoted, unlike the generic tail
    # every other non-value local reaches.
    if site.node in an.ctx.val_or_ref_decls:
        return _mk(site, name, typ, F.VALUE, BC.TPARAM_CALL,
                   const=_p._walrus_src_is_const(init, ctx),
                   move_exception=M.ALIAS)
    # A pointer-variant union local (`decl.union_addr`,
    # `decl.union_slot_rvalue`).
    ptr_u = _p._eligible_ptr_union_wide(vtype, an)
    if ptr_u is not None:
        if site.in_branch and site.storage.pointer:
            # A branch-first rebind slot: its builder rejects it.
            return _mk(site, name, typ, F.POINTER_UNION, BC.PTR_VARIANT_SLOT,
                       ptr_variant=True, rebind_slot=True)
        if not isinstance(init, TpyNoneLiteral) and not _checks._ptr_union_source_ok(
                init, ctx.declared_view, an, ptr_u,
                allow_field=name not in pre.reassigned):
            # The slot-hoist rows -- a rebind slot when the name is
            # rebound.
            return _mk(site, name, typ, F.POINTER_UNION, BC.PTR_VARIANT_SLOT,
                       ptr_variant=True, rebind_slot=site.storage.pointer)
        return _mk(site, name, typ, F.POINTER_UNION, BC.PTR_VARIANT,
                   ptr_variant=True)
    # `decl.storage_call_tuple`: an owning tuple call (an Own-declared
    # return, the flat
    # per-element-Own family, or a tuple of owned tuples).
    ct = an.get_expr_type(init)
    ctu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        _p._unwrap_own(ct)))) if ct is not None else None)
    nested_own = _p._nested_owned_tuple_call_ret(ct, an) is not None
    if (_checks._storage_decl_src(init, an) and isinstance(ctu, TupleType)
            and (ctu.has_pointer_or_copy_free_borrow_form() or nested_own)
            and (call_hands_back_value(init)
                 or _p._owned_tuple_call_ret(ct, an) is not None
                 or nested_own)
            and fixed and is_rvalue_source(an, init)):
        return _mk(site, name, ctu, F.STORAGE_TUPLE, BC.STORAGE_CALL_TUPLE,
                   storage_tuple=True)
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
        return _mk(site, name, typ, F.VALUE, BC.ITERATOR_OBJECT,
                   iterator_object=True, move_exception=M.GENERATOR_OBJECT)
    # The rebound borrow tuple asks whether a `t = t` source is a borrow
    # tuple, which is the record this site gets when the row does not
    # apply: the rows below decide it first.
    rest = _decl_tail_rows(site, name, typ, inp, visible, ctx, fixed)
    rec = _btuple_rebound_decl(site, name, typ, inp, ctx, fn, rest)
    return rec if rec is not None else rest


def _decl_tail_rows(site, name, typ, inp: 'PlanInputs', visible, ctx,
                    fixed) -> 'BindingRepr | None':
    """The declaration ladder past the rebound borrow tuple."""
    from . import statements as _st
    an, pre = inp.analyzer, inp.prescan
    stmt, init = site.node, site.init
    vtype = typ
    # A borrow-tuple alias of a name, a ternary of names or mixed calls, or
    # a call (the mixed own / borrow tuple among them). A block-local one is
    # the same `auto` copy of the source's pointers when that copies nothing
    # else; a mixed render or an inline element is aliased, which only a
    # straight-line one may be.
    if (fixed and isinstance(init, (TpyName, TpyCall, TpyMethodCall,
                                    TpyIfExpr))):
        rec = _btuple_alias_decl(site, name, typ, inp, visible, ctx)
        if rec is not None and (
                not site.in_branch
                or (rec.construct is BC.BTUPLE_ALIAS
                    and not rec.borrow_tuple_mixed
                    and rec.effective_type.has_copy_free_borrow_form())):
            return rec
    # `decl.optview_shim`: the Optional-view param shim.
    if (name not in pre.reassigned and isinstance(init, TpyName)
            and _value_opt_owned_view(vtype, an) is not None):
        from .expressions import _param_declared_type
        if _p._opt_view_arg_shim(_param_declared_type(init.name, ctx),
                                 vtype, an):
            return _mk(site, name, typ, F.VALUE_OPT, BC.OPTVIEW_SHIM,
                       value_opt=ValueOptKind.VIEW,
                       move_exception=_value_exception(typ))
    # A tuple-literal first decl, by the literal's captures.
    if isinstance(init, TpyTupleLiteral):
        rec = _tuple_literal_decl(site, name, typ, inp)
        if rec is not None:
            return rec
    # The generic tail (`decl.opt_record_call`, the value-opt slots).
    if not fixed or not isinstance(init, (TpyCall, TpyMethodCall)):
        pass
    elif _st._own_opt_record_call_slot(stmt, vtype, ctx, an):
        return _mk(site, name, typ, F.VALUE_OPT, BC.OPT_RECORD_CALL,
                   value_opt=ValueOptKind.RECORD)
    vo = _declared_value_opt_kind(typ, an)
    if vo is not None:
        return _mk(site, name, typ, F.VALUE_OPT, BC.VALUE_OPT,
                   value_opt=vo, move_exception=_value_exception(typ))
    if _p._value_opt_record(vtype) is not None:
        return _mk(site, name, typ, F.VALUE_OPT, BC.OPT_VALUE_RECORD,
                   value_opt=ValueOptKind.RECORD,
                   move_exception=_value_exception(typ))
    if not vtype.is_value_type():
        return _mk(site, name, typ, F.VALUE, BC.OWNED)
    return None


def _btuple_rebound_decl(site, name, typ, inp: 'PlanInputs', ctx, fn,
                         rest: 'BindingRepr | None'
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
        return _mk(site, name, typ, F.BORROW_TUPLE, BC.BTUPLE_REBOUND,
                   effective_type=ptt,
                   ptr_tuple=ptt)
    if site.in_branch and not isinstance(init, TpyName):
        return None
    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        collapse_tuple_own_elements(typ))))
    if not (isinstance(bt, TupleType)
            and (bt.has_pointer_repr_element()
                 or _p._binding_form_tuple(bt, an) is not None)
            and not an.function_stmt_borrow_decls.get(fn, {}).get(name, False)
            and not contains_pending_leaf(bt)
            and not any(isinstance(t, (PendingViewType, IntLiteralType,
                                       FloatLiteralType))
                        for t in bt.inner_types())):
        return None
    srcs = _st._borrow_tuple_binding_sources(name, ctx)
    own = (name, ctx.with_fixpoint(rest if rest is not None else UNBOUND))
    if srcs is None or not all(_st._borrow_tuple_source_ok(s, ctx, own)
                               for s in srcs):
        return None
    return _mk(site, name, typ, F.BORROW_TUPLE, BC.BTUPLE_REBOUND,
               effective_type=bt)


def _tuple_literal_decl(site, name, typ, inp: 'PlanInputs'
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
        # The reference elements the binding owns (fact 1, per path) are
        # the ones it holds inline. A module's names are module storage,
        # which owns nothing: there the literal's own elements are its
        # write's (`tuple_elem_owned`).
        inline = (an.ctx.tuple_elem_owned.get(site.node) if inp.top_level
                  else _owned_reference_elements(
                      _ownership(site, name, typ, inp), slot_bt))
        return _mk(site, name, typ, F.BORROW_TUPLE, BC.BTUPLE_LITERAL,
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
            return _mk(site, name, typ,
                       F.STORAGE_TUPLE if storage else F.VALUE,
                       BC.TUPLE_LITERAL_WIDE,
                       storage_tuple=storage,
                       move_exception=(None if storage
                                       else _value_exception(typ)),
                       effective_type=wide)
    if not storage_record:
        return None
    # The binding holds the literal's owned layout when the literal is its
    # only write.
    return _mk(site, name, typ, F.STORAGE_TUPLE, BC.TUPLE_LITERAL_RECORD,
               storage_tuple=True, effective_type=tuple_t,
               tuple_layout=(_checks.owned_literal_layout(init, tuple_t, an)
                             if name not in inp.prescan.reassigned
                             else None))


def _btuple_alias_decl(site, name, typ, inp: 'PlanInputs', visible, ctx
                       ) -> 'BindingRepr | None':
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
        bt = _p._binding_form_tuple(src.effective_type or src.type, an)
        if (bt is not None and not bt.has_pointer_repr_element()
                and src.form is not F.BORROW_TUPLE):
            # An open-T tuple's type does not say which form it holds; only
            # a source whose record took the borrow form is one.
            return None
        return bt

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
        if tb is not None and tb == eb and (
                tb.has_pointer_repr_element()
                or _name_pair_select(init, tb, inp, visible) is not None):
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
            src_bt = (_p._binding_form_tuple(an.get_expr_type(init), an)
                      or _p._wrapper_ref_tuple_return(
                          an.get_expr_type(init), an))
            mixed = promoted = (src_bt is not None and src_bt.is_mixed_own())
    if src_bt is None or not (src_bt.has_ref_elements()
                               or src_bt.has_pointer_or_proxy_element()):
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
        construct = BC.BTUPLE_INLINE_ARMS
    elif ref:
        construct = BC.BTUPLE_REF_ALIAS
    else:
        construct = BC.BTUPLE_ALIAS
    # A copy of a pointer-tuple name is that pointer tuple, an alias of
    # every element as `Box& u = *t` is.
    ptt = ptr_tuple_form(name, inp, ctx.func)
    return _mk(site, name, typ, F.BORROW_TUPLE, construct,
               borrow_tuple_mixed=mixed,
               inline_elems=inline_elems, const=const, effective_type=src_bt,
               ptr_tuple=ptt)


def _opt_ptr_slot_decl(site, name, typ, inp: 'PlanInputs', ctx
                       ) -> BindingRepr:
    """The OPT_PTR_SLOT binding's three constructions -- the
    borrow-returning call passthrough (const per its source), the storage
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
        return _mk(site, name, typ, F.POINTER, BC.OPT_CALL_PASSTHROUGH,
                   pointer=True, move_exception=M.ALIAS)
    if (not site.in_branch and isinstance(init, calls)
            and _p._call_storage_optional_return(init, an) is not None
            and _optional_ptr_borrow_wide(vtype, an) is not None):
        return _mk(site, name, typ, F.POINTER, BC.OPT_STORAGE_CALL,
                   pointer=True,
                   opt_storage_call=site.storage.pointer)
    return _mk(site, name, typ, F.POINTER, BC.OPT_PTR, pointer=True,
               rebind_slot=site.storage.pointer)


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


def _storage_tuple_alias(site, name, typ, inp, visible, ctx):
    """`decl.storage_tuple_alias*`: a pointer-repr tuple local bound `auto&&`
    to a storage
    tuple lvalue, or to a name that is one -- the chain reads the source's
    record."""
    init = site.init
    pre, an = inp.prescan, inp.analyzer
    # A block-local alias of a storage name is the same `auto&&`; a field
    # or element alias stays straight-line.
    if ((site.in_branch and not isinstance(init, TpyName))
            or not is_storage_tuple_alias_decl(
                typ, init, name=name, reassigned=pre.reassigned,
                hoisted=pre.hoisted, move_through=pre.move_through,
                storage_tuple_locals=ctx.names_with("storage_tuple"))):
        return None
    # The source must be a const-spellable storage tuple whose elements the
    # alias can re-bind (pointer-repr, or records held inline).
    src_t = an.get_expr_type(init)
    # A field or element source names storage by its type; an open-T tuple
    # name is storage only by its record (the chain arm below).
    src_form_t = (_p._binding_form_tuple(src_t, an)
                  if isinstance(init, TpyName) else _p._f1_tuple(src_t, an))
    if not (_p._storage_tuple_alias_src_ok(init, ctx.declared_view, an)
            and (src_form_t is not None
                 or _p._own_record_tuple(src_t, an) is not None)):
        return None
    if isinstance(init, TpyFieldAccess):
        return _mk(site, name, typ, F.STORAGE_TUPLE, BC.STORAGE_TUPLE_ALIAS,
                   storage_tuple=True,
                   const=_p._walrus_src_is_const(init, ctx),
                   move_exception=M.ALIAS)
    if isinstance(init, TpySubscript):
        return _mk(site, name, typ, F.STORAGE_TUPLE, BC.STORAGE_TUPLE_ALIAS,
                   storage_tuple=True,
                   const_storage_tuple=_p._walrus_src_is_const(init, ctx),
                   move_exception=M.ALIAS)
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
            return _mk(site, name, typ, F.STORAGE_TUPLE,
                       BC.STORAGE_TUPLE_CHAIN, storage_tuple=True,
                       tuple_layout=layout,
                       const_storage_tuple=_p._walrus_src_is_const(init, ctx),
                       move_exception=M.ALIAS)
    return None


def _name_pair_select(init: TpyIfExpr, t: 'TupleType', inp: 'PlanInputs',
                      visible) -> 'Held | None':
    """`tuple_select` over a select's two arms as their records hold
    them."""
    an = inp.analyzer
    arms = (init.then_expr, init.else_expr)
    helds = tuple(
        project_held(visible(a.name), an.get_expr_type(a), False, an,
                     inp.prescan.param_names, frozenset(), name=a.name)
        if isinstance(a, TpyName) else Held.VALUE
        for a in arms)
    return tuple_select(arms, (helds[0], helds[1]), t)


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
    predeclare, no form. The hoist builders (statements.py
    `_hoist_builder`) read nothing else to pick the declaration."""
    an, pre = inp.analyzer, inp.prescan
    if pre.binds_global(name):
        # The body reads the name as a module global: no local predecl.
        return _mk(site, name, typ, F.NONE, None,
                   move_exception=M.MODULE_STORAGE)
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
    return _mk(site, name, typ, F.VALUE, None,
               value_opt=_declared_value_opt_kind(typ, an),
               move_exception=_value_exception(typ))


def _nonvalue_hoist(site, name, typ, vt, inp: 'PlanInputs', ctx
                    ) -> 'BindingRepr | None':
    """The record of a non-value hoist, by `_nonvalue_hoist_rung`. A
    pointer an if chain's or a match's rvalue reseats re-fill has the
    statement's rebind slot; any other pointer hoist takes an rvalue reseat
    through a slot the reseat allocates at function top."""
    rung = _nonvalue_hoist_rung(site, name, vt, inp, ctx)
    if rung is None:
        return None
    form = rung.form
    stmt = site.node
    if form is F.BORROW_TUPLE:
        # A pointer tuple (`ptr_tuple_form`): each binding owning an element
        # backs it at its own site; an alias of one owns none.
        from . import statements as _st
        srcs = _st._borrow_tuple_binding_sources(name, ctx) or ()
        ptt = ptr_tuple_form(name, inp, ctx.func)
        if ptt is not None:
            return _mk(site, name, typ, form, None, ptr_tuple=ptt,
                       effective_type=ptt)
        # Otherwise an owning tuple-call source (a walrus's) fills a rebind
        # slot the if chain's head declares, and a mixed call's binding
        # holds its hybrid render.
        return _mk(site, name, typ, form, None,
                   borrow_tuple_mixed=any(
                       _p._mixed_own_btuple_call(s, ctx.analyzer)
                       for s in srcs),
                   rebind_slot=(isinstance(stmt, TpyIf)
                                and any(_p._btuple_owning_call_init(
                                    s, ctx.analyzer) for s in srcs)))
    if form is F.DYN:
        return _mk(site, name, typ, form, None, pointer=True,
                   move_exception=M.DYN)
    if form is F.POINTER and rung.head_target:
        return _mk(site, name, typ, form, None, pointer=True,
                   lazy_rebind_slot=True, head_target=True,
                   move_exception=M.PASS_SLOT)
    if form is F.POINTER:
        const = rung.const
        nullable = pointer_repr_optional(vt) is not None
        slot = (not const and isinstance(stmt, (TpyIf, TpyMatch))
                and site.storage.pointer and not nullable)
        return _mk(site, name, typ, form, None, pointer=True,
                   const=const,
                   move_exception=M.CONST_SLOT if const else None,
                   rebind_slot=slot, lazy_rebind_slot=not slot,
                   borrowed_capture=rung.borrowed_capture)
    if form is F.OWNED_SLOT:
        return _mk(site, name, typ, form, None, pointer=True,
                   move_forced=M.OWNED_SLOT, optional_storage=True)
    return _mk(site, name, typ, form, None,
               move_exception=(None if form is F.OPT_BORROW_TUPLE
                               else M.NO_FORM))


class _HoistRung(NamedTuple):
    """A non-value hoist's form, and for a pointer whether it is the for
    head's, const, and a match's borrow-only capture."""
    form: F
    head_target: bool = False
    const: bool = False
    borrowed_capture: bool = False


def _nonvalue_hoist_rung(site, name: str, var_type: TpyType,
                         inp: 'PlanInputs', ctx) -> '_HoistRung | None':
    """How a branch-first non-value hoist predecls, for every statement:
    an if chain's nullable borrow tuple, the borrow tuple a branch (if /
    try) or a loop variable binds, an if chain's `@dynamic` base pointer, a
    for head's container unpack target (the pointer the head re-points); a
    plain non-value bound once is the owned slot (`std::optional<T>`);
    reassigned or borrow-only, or a pointer-repr Optional (the nullable
    pointer over its inner), the pointer local; a borrow-decl const bit or
    a readonly type the const pointer, unless rvalue reseats would write
    through it (an if chain's reseats write the owned slot's address
    through it). A resumable body has no function-top drain for the line
    (a resumable loop head's plain non-value takes the owned slot), and a
    move-through payload outside the owned-slot family no form. None: no
    non-value hoist of this type. A pointer's facts say whether it is the
    head's, const, and a match's borrow-only capture (the pointer a nested
    match re-seats by address)."""
    an, pre, func = ctx.analyzer, ctx.prescan, ctx.func
    stmt = site.node
    at_if = isinstance(stmt, TpyIf)
    at_match = isinstance(stmt, TpyMatch)
    if at_if and _p._optional_borrow_tuple(var_type, an) is not None:
        return _HoistRung(F.OPT_BORROW_TUPLE)
    var_opt = pointer_repr_optional(var_type)
    ptr_opt = var_opt is not None and not _readonly_opt_pointee(var_type)
    btuple = (isinstance(stmt, (TpyIf, TpyTry, TpyForEach))
              and isinstance(var_type, TupleType)
              and (var_type.has_pointer_repr_element()
                   or (_p._binding_form_tuple(var_type, an) is not None
                       and not _open_t_escaping_capture(site, name, var_type,
                                                        inp))))
    dyn = (at_if and isinstance(var_type, NominalType)
           and is_dyn_protocol(var_type))
    if not (btuple or dyn) and _ptr_null_hoist(stmt, name, var_type, inp,
                                               ctx):
        return _HoistRung(F.POINTER, head_target=True)
    if not (btuple or dyn or ptr_opt or is_plain_nonvalue(var_type)):
        return None
    owned_slot = _HoistRung(F.OWNED_SLOT)
    no_form = _HoistRung(F.NONE)
    if func.is_generator or func.is_async:
        # A loop-head variable of a resumable leaf is a leaf local in every
        # constructed shape, so its plain non-value takes the owned slot.
        if (isinstance(stmt, (TpyWhile, TpyForEach))
                and is_plain_nonvalue(var_type)):
            return owned_slot
        return no_form
    if btuple:
        return _HoistRung(F.BORROW_TUPLE)
    if dyn:
        return _HoistRung(F.DYN)
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
            return (_HoistRung(F.POINTER, const=True) if readonly
                    else owned_slot)
        return no_form
    const_pointer = _HoistRung(F.POINTER, const=True,
                               borrowed_capture=borrowed)
    if const_borrow:
        # An if chain's or a match's rvalue reseats fill a slot the const
        # pointer then points at; any other statement's would write
        # through it.
        return (no_form if rebound and not (at_if or at_match)
                else const_pointer)
    if readonly:
        return const_pointer
    if ptr_opt or site.storage.pointer or borrow_only:
        return _HoistRung(F.POINTER, borrowed_capture=borrowed)
    return owned_slot


def _ptr_null_hoist(stmt, name, vt, inp: 'PlanInputs', ctx) -> bool:
    """A hoisted container ref-target of a sync for head's unpack: a
    pointer into the element of a container the loop iterates, which
    outlives the loop (an iterator's step does not) -- the loop's own
    route says which."""
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
    try:
        route = _st._select_for_each_route(stmt, inp.analyzer,
                                           ctx.declared_view, frozenset(),
                                           ctx.names_with("pointer"))
    except ThirUnsupported:
        return False
    return (route.route == "container"
            and route.consuming_native_name is None)


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
        if fld in scrubbed or fld in ("owned", "movable"):
            continue
        ov = getattr(outer, fld)
        if fld in ("value_opt", "tuple_layout", "inline_elems"):
            if f.get(fld) is None and ov is not None:
                f[fld] = ov
        elif ov:
            f[fld] = True
    if outer.owned:
        f["owned"] = True
    if outer.movable:
        f["move_forced"] = M.INHERITED_OUTER
    return f


_REFERENCE_LOOP_BINDINGS = frozenset({
    LoopBinding.REFERENCE, LoopBinding.CONST_REFERENCE,
    LoopBinding.CONST_TYPED_REFERENCE, LoopBinding.OWNED})


def _loop_form(loop: 'LoopBinding | None') -> F:
    """A loop or clause variable's form, from how its loop binds it: the
    `auto&&` / `const auto&` reference to the element (an owned step's
    included), else the variable's own value (a copy, the hoisted slot an
    assign writes, the owned storage materialization)."""
    if loop is None:
        return F.NONE
    return F.REFERENCE if loop in _REFERENCE_LOOP_BINDINGS else F.VALUE


@dataclass(frozen=True)
class TupleStep:
    """What a binding a loop hands a tuple step holds of its elements."""
    # Every element inline (an owned step bound as its storage tuple).
    inline_elems: 'tuple[bool, ...] | None' = None
    # The mixed result form (`std::tuple<A, B*>` for `tuple[Own[A], B]`):
    # the non-`Own` elements are lent.
    borrow_tuple_mixed: bool = False


def tuple_step_facts(et: 'TpyType | None', loop: 'LoopBinding | None', *,
                     stored: bool) -> TupleStep:
    """What a binding a loop hands a tuple step of type `et` holds of its
    elements -- for a `for` statement's variable (a generator expression's
    included) and a comprehension clause's alike. `stored`: the step is
    the container's stored tuple itself, every element inline whatever
    its type says. Otherwise an owned step (`Iterator[Own[tuple[A,
    int32]]]`) bound as the storage tuple it is handed holds every element
    inline, as its per-element `Own` twin's, and a step yielded in the
    mixed result form lends its non-`Own` elements."""
    u = _uw(unwrap_ref_type(et)) if isinstance(et, TpyType) else None
    if isinstance(u, OwnType):
        tt = unwrap_readonly(u.wrapped)
        if loop is LoopBinding.OWNED_STORAGE and isinstance(tt, TupleType):
            return TupleStep(inline_elems=(True,) * len(tt.element_types))
        return TupleStep()
    if not stored and isinstance(u, TupleType) and u.is_mixed_own():
        return TupleStep(borrow_tuple_mixed=True)
    return TupleStep()


def owned_optional_step(et: 'TpyType | None',
                        loop: 'LoopBinding | None') -> bool:
    """An owned optional step (`Iterator[Own[A | None]]`) a loop binds as
    the storage optional it is handed, which every read of the variable
    spells -- for a `for` statement's variable and a comprehension
    clause's alike."""
    u = _uw(unwrap_ref_type(et)) if isinstance(et, TpyType) else None
    return (isinstance(u, OwnType) and loop is LoopBinding.OWNED_STORAGE
            and pointer_repr_optional(unwrap_readonly(u.wrapped)) is not None)


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
    if isinstance(elem, OwnType) and not stmt.step_handover:
        # An `Own` element the iteration does not hand over (a lent
        # container's) is the container's: the variable refers to it.
        elem = elem.wrapped
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
    # A container's element, or the step a consuming loop moves out of
    # its container, is the stored tuple itself.
    storage_tuple = (isinstance(etu, TupleType)
                     and etu.has_pointer_or_copy_free_borrow_form()
                     and it_t is not None
                     and (consuming
                          or is_native_iterable(unwrap_readonly(it_t),
                                                an.registry))
                     and not (outer is not None and outer.storage_tuple)
                     and _p._borrow_tuple_local_type(
                         name, ctx.declared_view, ctx.names_with("storage_tuple"))
                     is None)
    if storage_tuple:
        f["storage_tuple"] = True
        f["const_storage_tuple"] = yields_const
    step = tuple_step_facts(et, f.get("loop_binding"), stored=storage_tuple)
    if step.inline_elems is not None:
        f["inline_elems"] = step.inline_elems
    if step.borrow_tuple_mixed:
        f["borrow_tuple_mixed"] = True
    if outer is not None and outer.is_param:
        # A loop that rebinds a parameter reads as that parameter where a
        # use asks "is this the caller's" (a view field's admission); sema
        # refuses a loop rebinding of another type, so the type carries.
        f["is_param"] = True
        f["param_type"] = outer.param_type
    if stmt.is_tuple_unpack:
        # The head's step: its targets take, element by element, what the
        # loop owns.
        return _mk(site, name, typ, _loop_form(f.get("loop_binding")), None,
                   **_inherit_outer(f, outer))
    if _p._foreach_value_opt_elem(et) is not None:
        f["value_opt"] = ValueOptKind.SCALAR
    elif _p._foreach_storage_opt_elem(et, an):
        f["storage_opt"] = True
        src = (stmt.iterable.obj if isinstance(stmt.iterable, TpyMethodCall)
               else stmt.iterable)
        f["const_storage_opt"] = _st._iteration_yields_const(src, ctx, an)
    elif owned_optional_step(et, f.get("loop_binding")):
        f["storage_opt"] = True
    # What the variable owns is its site's (`site.ownership`): the pass's
    # last use moves it where a copy allocates. A hoisted variable is the
    # pass slot the loop writes, unless a consuming loop hands it over; a
    # copied step that does not allocate moves as it copies.
    acq = site.ownership.acquired
    if stmt.hoist_loop_var and acq not in (Acquired.CONSUMING_LOOP,
                                           Acquired.OWN_ITER_LOOP):
        f["move_exception"] = M.PASS_SLOT
    elif (acq is Acquired.COPIED_STEP
          and not (elem is not None and elem.is_expensive_copy())):
        f["move_exception"] = _value_exception(elem)
    elif (acq is Acquired.OWN_ELEMENT and elem is not None
          and _unwrap_own(elem).is_value_type()
          and not _unwrap_own(elem).is_expensive_copy()):
        # A handed-over scalar moves as it copies.
        f["move_exception"] = M.TRIVIAL_VALUE
    form = _loop_form(f.get("loop_binding"))
    if isinstance(et, OwnType) and site.storage.pointer:
        # A rebound variable that owns its step points at it: a rebind
        # re-points it and an alias keeps the step's object (sema checked
        # the rebind against the pointee on the same verdict). Whether a use
        # may move out of what it points at is sema's verdict, as for the
        # declaration twin's pointer. One that refers to its element points
        # at the element.
        if f.get("loop_binding") not in (LoopBinding.OWNED,
                                         LoopBinding.REFERENCE,
                                         LoopBinding.CONST_REFERENCE):
            raise ThirUnsupported(
                stmt_reject_reason(stmt, "loop.rebound_own_form"),
                loc=getattr(stmt, "loc", None))
        form = F.POINTER
        f.update(pointer=True, rebind_slot=True)
    return _mk(site, name, typ, form, None, **_inherit_outer(f, outer))


def _with_row(site, name, typ, inp: 'PlanInputs', visible):
    """A sync `with ... as` target's first binding (`_with_target_arm`): an
    `auto` copy of a value enter result, the `T* name = &(...)` pointer of
    a reassigned record (a later `with` re-points it), the `auto&` alias
    of any other record, const as the enter result it deduces."""
    func = site.func or inp.func
    an = inp.analyzer
    if func.is_generator or func.is_async or not isinstance(typ, TpyType):
        return _mk(site, name, typ, F.NONE, None)
    resolved = unwrap_readonly(unwrap_send_sync(typ))
    if typ.is_value_type():
        if not (_p._eligible_scalar(resolved) or _p._eligible_char(resolved)
                or _p._eligible_enum(resolved, an) is not None
                or _resolved_str_value(resolved, an) is not None
                or _resolved_bytes_value(resolved, an) is not None
                or _p._value_tuple(resolved, an) is not None
                or _p._eligible_value_union(resolved) is not None):
            return _mk(site, name, typ, F.NONE, None)
        return _mk(site, name, typ, F.VALUE, None)
    if not _p.record_like(resolved, an):
        return _mk(site, name, typ, F.NONE, None)
    if site.storage.pointer:
        # A rebound target re-points at each rebind; the reassign site's own
        # gates keep out the sources it cannot take.
        return _mk(site, name, typ, F.POINTER, None, pointer=True,
                   rebind_slot=True)
    return _mk(site, name, typ, F.REFERENCE, None, const=_readonly_type(typ))


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
    if vtu is None:
        return _mk(site, name, typ, F.NONE, None)
    if not (func.is_generator or func.is_async):
        rec = _walrus_sync_kind(site, name, typ, vtu, inp, visible)
        # An escape-hoisted binder has a form only as the pointer over a
        # slot of its own, which the slot's placement before the loop makes
        # the hoist.
        if rec is not None and (name not in pre.hoisted or rec.rebind_slot):
            return rec
    if name in pre.hoisted:
        return _mk(site, name, typ, F.NONE, None)
    if ((_p._eligible_scalar(vtu) or _p._eligible_char(vtu)
         or _value_record_slot(vtu)
         or _p._eligible_enum(vtu, an) is not None)
            and not isinstance(vtu, OptionalType)):
        return _mk(site, name, typ, F.VALUE, None)
    return _mk(site, name, typ, F.NONE, None)


def _walrus_sync_kind(site, name, typ, vtu, inp: 'PlanInputs', visible
                      ) -> 'BindingRepr | None':
    """The sync-only walrus rungs; None past the last of them. A pointer is
    a record's (`POINTER`) or a pointer-repr Optional's (`OPT_PTR`); a
    borrow tuple the name rebinds is held over its owning slot
    (`BTUPLE_REBOUND`)."""
    an, pre = inp.analyzer, inp.prescan
    func = site.func or inp.func
    borrow_decls = an.function_stmt_borrow_decls.get(func, {})
    ever_owned = an.function_ever_owned_locals.get(func, set())
    if (name in borrow_decls and name not in ever_owned
            and is_plain_nonvalue(vtu)):
        if not _p.record_like(vtu, an):
            # The alias of a non-record has no form.
            return _mk(site, name, typ, F.NONE, None)
        return _mk(site, name, typ, F.POINTER, BC.POINTER, pointer=True)
    if _p._optional_ptr_borrow(vtu, an) is not None:
        return _mk(site, name, typ, F.POINTER, BC.OPT_PTR, pointer=True)
    if isinstance(vtu, TupleType) and name in pre.reassigned:
        return _mk(site, name, typ, F.BORROW_TUPLE, BC.BTUPLE_REBOUND)
    owned_tuple = (_p._owned_tuple_call_source(site.init, an)
                   and not contains_pending_leaf(vtu))
    if (isinstance(vtu, TupleType) and vtu.has_pointer_repr_element()
            and not owned_tuple):
        return _mk(site, name, typ, F.BORROW_TUPLE, None)
    if is_plain_nonvalue(vtu) and name in ever_owned and _p.record_like(
            vtu, an):
        # A rebound record keeps its object in a slot of its own, which the
        # pointer re-points away from at a rebind.
        pointer = site.storage.pointer
        return _mk(site, name, typ, F.OWNED_SLOT, None, pointer=pointer,
                   rebind_slot=pointer)
    if owned_tuple:
        return _mk(site, name, typ, F.OWNED_SLOT, None)
    vo = _declared_value_opt_kind(vtu, an)
    if vo is not None:
        return _mk(site, name, typ, F.VALUE_OPT, None,
                   value_opt=vo)
    s = _resolved_str_value(vtu, an)
    b = _resolved_bytes_value(vtu, an)
    if ((s is not None and not is_str_view_type(s))
            or (b is not None and not is_bytes_view_type(b))):
        return _mk(site, name, typ, F.VALUE, None)
    return None


def fresh_record(table: 'BindingTable', site: object, name: str
                 ) -> 'BindingRepr | None':
    """The record `site` declares for `name` as a fresh binding, None where
    the site writes a binding in scope (it may key that binding's
    refinement, as a standalone unpack's move promotion does)."""
    rec = table.declared_by(site, name)
    return rec if rec is not None and rec.site is site else None


def unpack_target_type(stmt: TpyTupleUnpack, i: int,
                       an) -> 'TpyType | None':
    """Unpack target `i`'s type, a pending container leaf resolved to the
    container its literal's uses decided (`stack = [(["x"], 1)]; a, b =
    stack.pop()` types `a` `PendingList[str, 1]`). The target's record is
    built from it; the readers without a record read it here."""
    raw = stmt.target_types[i] if i < len(stmt.target_types) else None
    if not isinstance(raw, TpyType):
        return None
    return _p.resolve_pending_container(unwrap_ref_type(raw), an) or raw


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
    return not stmt.owns_target(i)


def _unpack_row(site, name, typ, inp: 'PlanInputs', visible):
    """A fresh tuple-unpack target (a statement's, a for head's): its form,
    and its facts over those of the same-named outer binding a loop head's
    fresh target shadows (`_inherit_outer`); the const of a head pointer or
    reference is the iteration's, never the outer name's."""
    rec, shape = _unpack_target(site, name, typ, inp, visible)
    outer = visible(name)
    if not site.node.is_loop_head or outer is None:
        return rec
    own = {fld: getattr(rec, fld) for fld in REPR_FIELDS}
    scrubbed = (_HEAD_TARGET_SCRUBBED | {"const"}
                if shape.elem_pointer and not rec.rebind_slot
                else _HEAD_TARGET_SCRUBBED)
    return replace(rec, **_inherit_outer(dict(own), outer, scrubbed))


class _TargetShape(NamedTuple):
    """How a fresh unpack target holds its element: its form, and whether
    it refers through the element's pointer (const as the unpack's source),
    takes an owned element's object, or copies a value element."""
    form: F
    elem_pointer: bool = False
    owned: bool = False
    copies: bool = False


_NO_SHAPE = _TargetShape(F.NONE)


def _unpack_target(site, name, typ, inp: 'PlanInputs', visible
                   ) -> 'tuple[BindingRepr, _TargetShape]':
    """A fresh tuple-unpack target's record and its shape."""
    an = inp.analyzer
    stmt = site.node
    i = stmt.targets.index(name)
    flag = (lambda xs: bool(xs[i]) if i < len(xs) else False)
    is_ref = flag(stmt.is_ref)
    tt = unwrap_ref_type(typ) if isinstance(typ, TpyType) else None
    storage = site.storage
    shape = _unpack_target_shape(stmt, i, tt, an)
    # The statement's rvalue holder, or a loop head's step, is an owned
    # target's first storage; a module's names are module storage, which no
    # init temporary is.
    in_holder = (shape.owned
                 and (stmt.is_loop_head or unpack_holder_is_rvalue(stmt))
                 and not (inp.top_level
                          and (site.func or inp.func) is inp.func))
    f: dict = {}
    if in_holder and storage.pointer and not storage.hoisted:
        # Storage standing before a loop outlives the holder, so a hoisted
        # target keeps its slot: it points at the element in place.
        shape = _TargetShape(F.POINTER, elem_pointer=True)
        f.update(pointer=True, rebind_slot=True)
    elif (shape.elem_pointer and shape.form in (F.REFERENCE, F.POINTER)
          and storage.pointer):
        # A reassigned name rebinds: an alias would write the new value
        # through into the element's referent, and a fresh value needs a
        # slot of its own to point at.
        shape = shape._replace(form=F.POINTER)
        f.update(pointer=True, rebind_slot=True)
    acq = site.ownership.acquired
    if (stmt.is_loop_head and acq is not None
            and acq is not Acquired.OWN_ELEMENT
            and shape.copies and not (
                isinstance(tt, TpyType)
                and (_p._resolve_pending_view(tt, an)
                     or tt).is_expensive_copy())):
        # A head target that takes its own copy each pass moves at its last
        # use in the pass where the copy allocates.
        f["move_exception"] = _value_exception(tt)
    if f.get("rebind_slot"):
        pass
    elif shape.form is F.POINTER:
        f["pointer"] = True
    elif shape.form is F.POINTER_UNION:
        f["ptr_variant"] = True
    elif shape.owned and storage.pointer:
        # A rebound target keeps the element it takes over in a slot of its
        # own, which the pointer re-points away from at a rebind.
        f.update(pointer=True, rebind_slot=True,
                 storage_hoisted=storage.hoisted)
    elif in_holder:
        # The target holds the element in place as an owned loop step
        # does: one move at a consuming read, none at a read-only one.
        f["loop_binding"] = LoopBinding.OWNED
    if not (is_ref or f.get("pointer") or f.get("ptr_variant")):
        # A value-repr Optional is read through its value wherever the
        # target holds it.
        vo = _declared_value_opt_kind(typ, an)
        if vo is not None:
            f["value_opt"] = vo
        elif not stmt.is_loop_head and _own_opt_storage_binding(tt):
            f["value_opt"] = ValueOptKind.RECORD
    if shape.elem_pointer:
        f["const"] = _unpack_source_const(site, i, inp, visible)
    form = (F.POINTER if f.get("rebind_slot")
            else _loop_form(f["loop_binding"]) if "loop_binding" in f
            else shape.form)
    return _mk(site, name, typ, form, None, **f), shape


def unpack_holder_is_rvalue(stmt: TpyTupleUnpack) -> bool:
    """Whether a standalone unpack may bind its source to the by-value
    `auto __tup_N = <expr>;` holder: any source but a name. An
    over-approximation -- the lowering reads a place in place and holds an
    lvalue by reference -- that the lowering's refusal of an owned target
    off any other holder keeps sound."""
    return not stmt.is_loop_head and not isinstance(stmt.value, TpyName)


def _unpack_target_shape(stmt: TpyTupleUnpack, i: int, tt, an
                         ) -> _TargetShape:
    """How fresh unpack target `i` holds its element: a recursive wrapper
    element by reference to its live `X&`; a reference element by
    reference into it (a pointer-repr Optional element as its nullable
    pointer); an owned element by taking its object (an `Own[P | None]`
    as the pointer into the holder's storage optional, an `Own[A | B]` as
    the pointer variant, an `Own[P] | None` element as its storage
    optional); a value element by copy, or by const reference
    where sema says the target never takes its own copy. No shape: the
    unpack refuses the target."""
    flag = (lambda xs: bool(xs[i]) if i < len(xs) else False)
    is_ref, is_owned, is_new = (flag(stmt.is_ref), flag(stmt.is_owned),
                                flag(stmt.is_new))
    if not is_owned and is_new and _p._wrapper_union_like(tt, an) is not None:
        return _TargetShape(F.REFERENCE)
    if not is_new:
        return _NO_SHAPE
    if is_ref:
        if _p.record_like(tt, an):
            return _TargetShape(F.REFERENCE, elem_pointer=True)
        if _p._optional_ptr_borrow(tt, an) is not None:
            return _TargetShape(F.POINTER, elem_pointer=True)
        return _NO_SHAPE
    if is_owned:
        if _p._optional_ptr_borrow(tt, an) is not None:
            return _TargetShape(F.POINTER, elem_pointer=True)
        if _p._eligible_ptr_union(tt, an) is not None:
            return _TargetShape(F.POINTER_UNION)
        if (_p.record_like(tt, an)
                or isinstance(unwrap_readonly(tt), RecursiveAliasInstanceType)
                or _p._owned_container_slot(tt)
                or _own_opt_storage_binding(tt)):
            return _TargetShape(F.VALUE, owned=True)
        return _NO_SHAPE
    # A nested value tuple copies like a scalar; any value element of a
    # loop's step does, as the step's own element.
    from . import statements as _st
    if not (stmt.is_loop_head or _st._scalar_or_str_unpack_elem(tt, an)
            or _p._value_tuple_global(tt, an) is not None):
        return _NO_SHAPE
    # A target sema stamps as taking its own copy copies.
    if flag(stmt.is_const_ref) and not flag(stmt.target_copies):
        return _TargetShape(F.REFERENCE)
    return _TargetShape(F.VALUE, copies=True)


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
        rec = _mk(site, name, typ, F.NONE, None)
    own = {fld: getattr(rec, fld) for fld in REPR_FIELDS}
    return replace(rec, **_inherit_outer(own, outer, _COMP_SCRUBBED))


def _comp_clause_row(site, name, typ, inp: 'PlanInputs', visible):
    """The facts a comprehension clause decides for its variable, from the
    clause's route (`_clause_route`), and for its head's targets from the
    head's per-target record."""
    from . import statements as _st
    an = inp.analyzer
    gen, route = site.clause or site.node, site.route
    if route is None:
        return None
    if route.unpack_types is not None and name != gen.var:
        # A target the step hands its element to holds it in place in the
        # step, as an owned head target does; a record element and a
        # pointer-repr Optional element (its storage optional) bind a
        # reference into the element, const as the clause's loop variable;
        # any other element its value.
        # `x, x`: the last target of a name is the one bound.
        i = len(gen.unpack_vars) - 1 - gen.unpack_vars[::-1].index(name)
        tt = route.unpack_types[i]
        const = bool(gen.const_loop_var)
        if gen.head.is_owned[i]:
            # An owned pointer-repr Optional holds the storage optional, as
            # the clause variable of an owned Optional step does.
            return _mk(site, name, typ, _loop_form(LoopBinding.OWNED), None,
                       loop_binding=LoopBinding.OWNED,
                       storage_opt=_p._optional_ptr_borrow(tt, an)
                       is not None)
        if _p._optional_ptr_borrow(tt, an) is not None:
            return _mk(site, name, typ, F.STORAGE_OPT, None, storage_opt=True,
                       const=const)
        if _p.record_like(unwrap_readonly(unwrap_send_sync(tt)), an):
            return _mk(site, name, typ, F.REFERENCE, None, const=const)
        return _mk(site, name, typ, F.VALUE, None)
    # The clause's loop binds its variable (an unpack clause, its holder)
    # as the for statement's loop binds an element of the same type; an
    # `Own` element the iteration does not hand over (a lent container's)
    # is the container's, which the variable refers to.
    et = route.et
    if isinstance(et, OwnType) and not gen.step_handover:
        et = et.wrapped
    loop = (loop_binding_kind(et, bool(gen.const_loop_var),
                              copies=gen.copies_var)
            if et is not None else None)
    # A container's element is its stored tuple; a generator's step is the
    # form it is yielded in.
    it_t = route.it_type
    step = tuple_step_facts(
        route.et, loop,
        stored=(it_t is not None and is_native_iterable(
            unwrap_readonly(it_t), an.registry)))
    if step.inline_elems is not None:
        return _mk(site, name, typ, _loop_form(loop), None,
                   loop_binding=loop, inline_elems=step.inline_elems)
    if step.borrow_tuple_mixed:
        return _mk(site, name, typ, _loop_form(loop), None,
                   loop_binding=loop, borrow_tuple_mixed=True)
    if owned_optional_step(route.et, loop):
        return _mk(site, name, typ, _loop_form(loop), None,
                   loop_binding=loop, storage_opt=True)
    etp = unwrap_readonly(route.et) if route.et is not None else None
    if isinstance(etp, TupleType) and etp.has_pointer_repr_element():
        return _mk(site, name, typ, F.STORAGE_TUPLE, None, storage_tuple=True,
                   loop_binding=loop)
    if _p._foreach_storage_opt_elem(route.et, an):
        ctx = _PlanCtx(inp, visible, site.func or inp.func)
        src = (gen.iterable.obj if isinstance(gen.iterable, TpyMethodCall)
               else gen.iterable)
        return _mk(site, name, typ, F.STORAGE_OPT, None, storage_opt=True,
                   const_storage_opt=_st._iteration_yields_const(src, ctx,
                                                                 an),
                   loop_binding=loop)
    # An unpack clause's variable is the step its head reads, in the form
    # its loop binds it.
    return _mk(site, name, typ,
               _loop_form(loop) if route.unpack_types is not None else F.NONE,
               None, loop_binding=loop)


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
        return _mk(site, name, typ, F.POINTER, None, pointer=True,
                   const=facts.subject_const)
    if name in facts.by_value:
        return _mk(site, name, typ, F.VALUE, None, value_opt=value_opt)
    return _mk(site, name, typ, F.REFERENCE, None, value_opt=value_opt,
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
        out[name] = (ft, _declared_value_opt_kind(ft, an), False)

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
    return _mk(site, name, typ, F.VALUE, None, nested_def=True)


def _nested_param_row(site, name, typ, inp: 'PlanInputs', visible):
    """A nested def's parameter: no form of its own."""
    return BindingRepr(name=name, site=None, type=typ, form=F.NONE,
                       site_kind=site.kind, is_param=True)


def _lambda_param_row(site, name, typ, inp: 'PlanInputs', visible):
    """A lambda's parameter: no form of its own, its type the one the
    lambda was analyzed at."""
    return BindingRepr(name=name, site=site.node, type=typ, form=F.NONE,
                       site_kind=site.kind, is_param=True, param_type=typ)


def _generator_frame(row):
    """`row`, where a concrete generator object owns its frame but is
    never moved out of: a started frame cannot move."""
    def frame(site, name, typ, inp: 'PlanInputs', visible):
        rec = row(site, name, typ, inp, visible)
        if (rec is not None and rec.move_exception is None
                and rec.move_forced is None and generator_object(
                    inp.analyzer.ctx, site.func or inp.func, name)):
            rec = replace(rec, move_exception=M.GENERATOR_OBJECT)
        return rec
    return frame


def _copied_value(row):
    """`row`, whose binding owns only the copy of a value it takes (a
    value enter result's `auto` copy, a by-value capture): no read moves
    out of it, for that value's reason."""
    def copied(site, name, typ, inp: 'PlanInputs', visible):
        rec = row(site, name, typ, inp, visible)
        if rec is not None and site.ownership:
            rec = replace(rec, move_exception=_value_exception(typ))
        return rec
    return copied


def _excepted(row, why: M):
    """`row`, whose records no read moves out of, for `why`."""
    def excepted(site, name, typ, inp: 'PlanInputs', visible):
        rec = row(site, name, typ, inp, visible)
        return None if rec is None else replace(rec, move_exception=why)
    return excepted


_ROWS: dict[str, tuple] = {
    "param": (_param_row,),
    "receiver": (_receiver_row,),
    "global": (_global_row,),
    "module_global": (_module_global_row,),
    "frame": (_generator_frame(_frame_row),),
    # A concrete generator object owns its frame, which never moves.
    "decl": (_generator_frame(_decl_row),),
    "hoist": (_generator_frame(_hoist_row),),
    "loop": (_loop_row,),
    # A with target refers to the `__enter__` result, a capture to the
    # subject, unless it copies a value.
    "with": (_copied_value(_with_row),),
    "walrus": (_walrus_row,),
    "unpack": (_unpack_row,),
    "comp": (_excepted(_comp_row, M.COMP_CLAUSE),),
    "def": (_def_row,),
    "match": (_copied_value(_match_row),),
    "nested_param": (_nested_param_row,),
    "lambda": (_excepted(_lambda_param_row, M.LAMBDA_PARAM),),
    "except": (_except_row,),
}


def lambda_param_records(lam: TpyLambda,
                         inp: PlanInputs) -> dict[str, BindingRepr]:
    """A lambda's parameter records, at the types it was analyzed at."""
    types = list(lam.inferred_param_types)
    return {n: classify_binding(_Site("lambda", lam), n,
                                types[i] if i < len(types) else None, inp,
                                None)
            for i, n in enumerate(lam.param_names)}


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
        # Planning a nested def's body.
        self.nested = False
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
        # Each planned scope's `_non_fresh_names`, by function.
        self.non_fresh: IdentityMap = IdentityMap()

    def _site(self, kind: str, node=None, init=None) -> _Site:
        return _Site(kind, node, init, in_branch=self.in_branch,
                     func=self.func, prescan=self.prescan,
                     nested=self.nested)

    def _record(self, site: _Site, name: str, typ, node: ScopeNode
                ) -> BindingRepr:
        rec = self._fresh(classify_binding(site, name, typ, self.inp,
                                           node.lookup), site.func)
        self._key(site.node, rec)
        return rec

    def _fresh(self, rec: BindingRepr, func: TpyFunction | None
               ) -> BindingRepr:
        """`rec` with `fresh_only` from the scope of `func` that binds it
        (`_non_fresh_names`, once per function)."""
        if rec.site_kind in ("comp", "lambda"):
            # A comprehension clause's or a lambda's own scope: never the
            # target of a `del`.
            return rec
        func = func or self.inp.func
        names = self.non_fresh.get(func)
        if names is None:
            names = self.non_fresh[func] = _non_fresh_names(func,
                                                            self.analyzer)
        return rec if rec.name in names else replace(rec, fresh_only=True)

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
                tt = unpack_target_type(s, i, an)
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
            kind = (_declared_value_opt_kind(vt, self.analyzer)
                    if vt is not None else None)
            if kind is not None:
                out[n] = replace(prev, value_opt=kind,
                                 refinements=prev.refinements + (R.FRAME_HOIST,))
                self._key(s, out[n])
        return out

    def _unpack_promotions(self, s: TpyTupleUnpack, node: ScopeNode
                           ) -> dict[str, BindingRepr]:
        """The TpyTupleUnpack arm: the standalone unpack promotes EVERY target
        its body owns, a reused one too (a reassigning VarDecl never
        promotes the name it rebinds), and before its source lowers -- so
        the reused binding is movable from the statement's own source read
        on. A hoisted slot and a nested def's local are never promoted."""
        out: dict[str, BindingRepr] = {}
        func = self.func
        if self.nested or (self.inp.top_level and func is self.inp.func):
            return out
        pre = self.prescan or self.inp.prescan
        ctx = self.analyzer.ctx
        for n in s.targets:
            if (n is None or n in out or n in pre.hoisted
                    or not local_ownership(ctx, func, n)):
                continue
            prev = node.lookup(n)
            if prev is not None and not prev.movable:
                out[n] = replace(prev, movable=True,
                                 move_forced=M.UNPACK_PROMOTE,
                                 refinements=prev.refinements + (R.UNPACK_PROMOTE,))
                self._key(s, out[n])
        return out

    def function_scope(self, func: TpyFunction, parent: ScopeNode,
                       nonlocal_names: 'AbstractSet[str]') -> None:
        params = [n for n, _t in func.params]
        own = frozenset(scope_bound_names(func.body, params))
        outer_pre = self.prescan or self.inp.prescan
        site = _Site("nested_param", in_branch=self.in_branch, func=func,
                     prescan=outer_pre, nested=True)
        recs = {n: classify_binding(site, n, t, self.inp, parent.lookup)
                for n, t in func.params}
        # The nested body's prescan carries no pointer-slot globals
        # (`nested_def_prescan`): an enclosing one reads there as the
        # pointer its record says it is, not as a global slot.
        for n in outer_pre.global_slots:
            outer = parent.lookup(n)
            if n not in own and outer is not None and outer.global_slot:
                recs[n] = replace(outer, global_slot=False,
                                  refinements=outer.refinements + (R.NESTED_DEF,))
        root = ScopeNode(parent, recs, own, function=True)
        self.table.scope_at[func] = root
        saved = (self.func, self.in_branch, self.prescan, self.nested,
                 self.overload_narrowing, self.overload_literal_facts)
        self.prescan = nested_def_prescan(
            func, prescan_without(outer_pre, own), self.analyzer,
            nonlocal_names)
        self.nested = True
        self.func, self.in_branch = func, False
        self.overload_narrowing = _without_names(self.overload_narrowing, own)
        self.overload_literal_facts = _without_names(
            self.overload_literal_facts, own)
        try:
            self.block(func.body, root, branch=False)
        finally:
            (self.func, self.in_branch, self.prescan, self.nested,
             self.overload_narrowing, self.overload_literal_facts) = saved

    def expr(self, e, node: ScopeNode) -> None:
        if not isinstance(e, TpyExpr):
            return
        if isinstance(e, TpyLambda):
            lnode = ScopeNode(node, lambda_param_records(e, self.inp),
                              frozenset(e.param_names), function=True)
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
                recs: dict[str, BindingRepr] = {}
                tsite = site
                if route is not None and route.unpack_types is not None:
                    recs[gen.var] = self._record(site, gen.var, route.et, cur)
                    # The head binds the targets off the clause variable,
                    # the step, as a for head binds them off its loop's.
                    assert gen.head is not None
                    self.table.scope_at[gen.head] = cur.child(dict(recs))
                    tsite = replace(site, node=gen.head, clause=gen)
                # `x, x`: the last target of a name binds it.
                for n, t in dict(_clause_types(e, gen, route,
                                               self.analyzer)).items():
                    recs[n] = self._record(tsite, n, t, cur)
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
        if fix.const_slots == inp.const_slots:
            break
        inp = replace(inp, const_slots=fix.const_slots)
        table = _plan_once(inp, body)
    else:
        raise BindingTableError(
            f"const fixpoint of {inp.func.name} did not settle in "
            f"{_CONST_ROUNDS} planning rounds")
    _reject_mixed_slot(table, fix)
    _mark_const(table, fix, inp.analyzer)
    return table


# Each planning round starts from the closure of the last, so a body
# settles in two; more means the rows and the fixpoint disagree.
_CONST_ROUNDS = 8


# The families whose pointer record is the name's one C++ slot: a
# declaration's, a hoist's, a walrus's and a with target's (a loop, unpack
# or capture binding is a fresh C++ variable of its own).
_SLOT_FAMILIES = frozenset({"decl", "hoist", "walrus", "with"})


def _slot_family(rec: BindingRepr) -> bool:
    # Judged by the declaring site, whatever form the record's row picks:
    # the form (a pointer or not) itself reads the verdict, so keying on it
    # would not settle.
    return rec.site_kind in _SLOT_FAMILIES


_BORROW_TUPLE_FORMS = frozenset({F.BORROW_TUPLE, F.OPT_BORROW_TUPLE})


def _is_slot(rec: BindingRepr) -> bool:
    # A pointer-variant union (`::tpy::Union<A*, B*>`) is a pointer slot
    # too, and so is a borrow tuple: its element pointers are one const.
    return ((rec.pointer or rec.ptr_variant or rec.ptr_tuple is not None
             or rec.form in _BORROW_TUPLE_FORMS)
            and not rec.optional_storage and _slot_family(rec))


def _slot_const(rec: BindingRepr, const_slots: AbstractSet, an) -> bool:
    """A slot's const: every source bound into it is const (the
    fixpoint's `const_slots`), or its declared type is read-only."""
    return slot_key(rec) in const_slots or _declared_const(rec, an)


@dataclass(frozen=True)
class _ConstFix:
    """The whole-body const fixpoint: the slots every source bound into
    which is const, and per record the statements binding a const source
    into it."""
    const_slots: frozenset
    const_sources: Mapping[SlotKey, tuple]
    # The matches capturing a const loop variable into a pointer slot:
    # sema does not credit a write through the capture to the loop
    # variable, so its const is unproven
    # (BUGS.md#match-capture-loop-var-write-not-credited).
    unproven: tuple = ()


def _answers(pred, src: TpyExpr, ctx: '_PlanCtx') -> bool:
    try:
        return pred(src, ctx)
    except ThirUnsupported:  # unanswerable
        return False


def _plan_const(inp: PlanInputs, table: BindingTable) -> _ConstFix:
    """The const fixpoint over the table, each source judged against the
    records visible at its own statement. A slot (a pointer, a pointer
    union, a borrow tuple's element pointers) is const when every source
    bound into it is (a match capture's source is its subject, a with
    target's its `__enter__` result; a None points at nothing): the
    largest such set, so a chain or a cycle
    of slots (`d2 = d1`, `n = n.next`) rooted in const sources is const.
    The candidates are the slots some source of which is const once the
    slots it reads are."""
    func = inp.func
    binds = []
    loop_var_captures = []
    for tgt, src, stmt, _w, _b in binding_source_sites(func.body,
                                                         all_binders=True):
        # A with target's record is its item's.
        at = src if isinstance(src, TpyWithItem) else stmt
        rec = table.declared_by(at, tgt) or table.visible(at, tgt)
        if rec is not None and not isinstance(src, TpyNoneLiteral):
            binds.append((slot_key(rec), _slot_family(rec), src, stmt))
            if (isinstance(stmt, TpyMatch) and src is stmt.subject
                    and _is_slot(rec)):
                loop_var_captures.append(stmt)
    slots: set[SlotKey] = set()

    def ctx_at(stmt) -> _PlanCtx:
        node = table.scope_at.get(stmt) or table.root
        ctx = _PlanCtx(inp, node.lookup, func)
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
    return _ConstFix(frozenset(slots),
                     {k: tuple(v) for k, v in const_sources.items()},
                     unproven)


def _rooted_in_const_loop_var(expr: TpyExpr, ctx: _PlanCtx) -> bool:
    # Asked with final facts: sema marked a loop variable a call lends
    # writable on the same climb, widening a pending callee's guess, so a
    # variable still const here is one no write through `expr` reaches.
    root = pointee_root(expr, ctx.analyzer.get_expr_type,
                        MayLend(ctx.analyzer.ctx))
    return root is not None and ctx.binding(root).const_loop_var


def _reject_mixed_slot(table: BindingTable, fix: _ConstFix) -> None:
    """A slot holding both a const and a mutable source has no C++ form:
    `T*` cannot take the const one, and `const T*` refuses a write a
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


def _mark_const(table: BindingTable, fix: _ConstFix, an) -> None:
    """Write the fixpoint's bits on the records: a slot's const
    (`_slot_const`) and every record's const-source bit, in every scope
    node and site entry that holds them."""
    new: dict[int, BindingRepr] = {}

    def swap(rec: BindingRepr) -> BindingRepr:
        key = slot_key(rec)
        sourced = key in fix.const_sources
        slot = _is_slot(rec)
        if not sourced and not slot:
            return rec
        got = new.get(id(rec))
        if got is None:
            got = replace(
                rec,
                const=(_slot_const(rec, fix.const_slots, an) if slot
                       else rec.const),
                const_source=sourced)
            new[id(rec)] = got
        return got

    _swap_records(table, swap)


def _swap_records(table: BindingTable,
                  swap: Callable[[BindingRepr], BindingRepr]) -> None:
    """Replace every record by `swap(record)`, in every scope node and
    site entry that holds it."""
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


def _non_fresh_names(func: TpyFunction, an: 'SemanticAnalyzer'
                     ) -> frozenset[str]:
    """The names of `func`'s own scope some binding of which is not a fresh
    value (`BindingRepr.fresh_only`): every name a statement of the body
    binds (`written_names`) other than at a declaration or assignment
    whose bind kind is RVALUE, a walrus target that is not, a parameter,
    a `global` / `nonlocal` name and a name a nested def rebinds through
    `nonlocal` (sema's `nonlocal_names`, any depth). A nested def's body
    is a scope of its own. Read off the whole body, so the order of the
    bindings does not matter."""
    out: set[str] = {n for n, _t in func.params}

    def fresh(binder: object, value: TpyExpr | None) -> bool:
        owned = an.ctx.tuple_elem_owned.get(binder)
        return bind_kind_of(an.ctx, value, owned) is BindKind.RVALUE

    def on_stmt(s: TpyStmt) -> None:
        if isinstance(s, TpyNestedDef):
            out.add(s.func.name)
            out.update(s.nonlocal_names)
            return
        if isinstance(s, (TpyGlobal, TpyNonlocal)):
            out.update(s.names)
        elif isinstance(s, TpyVarDecl):
            # A bare annotation binds nothing.
            if s.init is not None and not fresh(s, s.init):
                out.add(s.name)
        elif isinstance(s, TpyAssign) and isinstance(s.target, TpyName):
            if not fresh(s, s.value):
                out.add(s.target.name)
        elif not isinstance(s, TpyDelVar):
            out.update(written_names(s, walrus=False))
        for w in walrus_bindings(s):
            if not fresh(w, w.value):
                out.add(w.target)

    walk_body_stmts(func.body, lambda e: None, on_stmt)
    return frozenset(out)


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
    table = BindingTable(root, inp)
    planner = _Planner(inp, table)
    for name, rec in root_recs.items():
        if rec.site_kind != "receiver":
            root_recs[name] = planner._fresh(rec, inp.func)
    table.frame_members = tuple(n for n, rec in root_recs.items()
                                if rec.frame_class is not None)
    planner.block(body, root, branch=False, top_level=True)
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
        sink.append(("_row:" + rec.label + "".join(
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
        frame=frame,
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
                           reason: str, *,
                           storage: 'RebindStorage | None') -> 'THIRExpr':
    """The rvalue a write to a REBIND_PTR frame local of type `vtype`
    stores (`storage`: the write's rebind verdict, None for a first write),
    at any binding site (assignment, walrus). The source fills the SLOT,
    so it is admitted and lowered against what the slot holds -- the
    pointee for a pointer-repr Optional local."""
    from .statements import (_lower_storage_value, _rebind_rvalue_source_ok,
                             _rebind_rvalue_use, _rebind_slot_target,
                             _slot_fill_value)
    slot_t = _rebind_slot_target(vtype, lc.analyzer)
    if not _rebind_rvalue_source_ok(init, slot_t, lc.analyzer,
                                    frame_roots_ok=True):
        note_detail("res.rebind_ptr_source")
        raise ThirUnsupported(reason)
    value = _lower_storage_value(init, slot_t, lc, declared, loc,
                                 use=_rebind_rvalue_use(init, slot_t))
    return _slot_fill_value(value, slot_t, storage, lc)


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
