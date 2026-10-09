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

from ...codegen_cpp import resumable_cfg as rcfg
from ...codegen_cpp.forms import (LocalBinding, is_plain_nonvalue,
                                  is_ptr_variant_union,
                                  reads_storage_form_optional)
from ...codegen_cpp.type_resolution import resolve_stmt_binding_type
from ...identity_map import IdentityMap, IdentitySet
from ...modules.type_resolution import is_native_iterable
from ...parse.nodes import (ResultForm, TpyArrayLiteral, TpyAsPattern,
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
                            TpyYield, TupleElemCapture,
                            is_property_getter_read, iter_capture_bindings,
                            walk_body_stmts, walk_expr_tree, walrus_bindings)
from ...prescan import scope_bound_names
from ...sema.own_copy import contains_reference_type
from ...type_def_registry import is_array
from ...typesys import (ConcreteGenType, NominalType, OptionalType, OwnType,
                        ReadonlyType, TpyType, TupleType, TypeParamRef,
                        UnionType, contains_pending_leaf, is_dyn_protocol,
                        is_own_pointer_repr_optional, pointer_repr_optional,
                        resolve_int_literals,
                        unwrap_optional_own, unwrap_readonly, unwrap_ref_type,
                        unwrap_send_sync)
from ...value_category import (call_hands_back_value, call_returns_cpp_ref,
                               is_rvalue_source, receiver_const)
from ..nodes import Form
from ..reject import ThirUnsupported
from ..scalar_leaves import storage_leaf
from ..source import UNBOUND, BindingRepr, Held
from . import checks as _checks
from . import predicates as _p
from .context import (BindingTableError, ValueOptKind, _LowerCtx,
                      _btuple_const_storage, _without_names,
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
    "const_borrow_tuple", "const_opt_borrow_tuple", "movable", "frame_slot",
    "owned", "optional_storage", "coro_frame", "opt_storage_call",
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
    __slots__ = ("root", "scope_at", "sites", "stmt_expr_scope", "entered")

    def __init__(self, root: ScopeNode) -> None:
        self.root = root
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
                               row="lambda.plain", is_param=True,
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
    loop families of the skeleton's for-prescan, and the fields a sink may
    move from."""
    classes: Mapping[str, object]
    loops: object
    movable: frozenset[str]


def _frame_facts(inp: PlanInputs) -> _FrameFacts:
    from . import resumable as _res
    func, an = inp.func, inp.analyzer
    rstate = rcfg.resumable_state(func)
    loops = _res.frame_loop_facts(func, rstate)
    classes = {}
    for lname, ltype in (func.generator_locals or []):
        lay = inp.frame.layout.get(lname)
        if lay is not None:
            classes[lname] = _res.frame_local_class(
                lname, ltype, lay.kind, an, loops,
                rstate.one_shot_lift_names)
    # A frame-promoted STORAGE slot is movable with no value-type filter: a
    # last-use read moves out of the slot rather than copying it, which is
    # how a BigInt frame local moves at an async return. A pointer-form
    # local is movable only when the frame owns its pointee (the
    # Optional-ptr local's `__ptr_slot_fN` storage, as the plain body's
    # OPT_PTR_SLOT decl row); loop-var, alias and unpack pointers borrow.
    # Only names a `TpyVarDecl` binds: a frame-promoted loop variable is
    # movable only off a consuming iterable (the loop row), and a value
    # record copies wherever it lives.
    borrowed = (loops.ptr_loop_vars | loops.unpack_ptr_targets
                | {n for n, c in classes.items()
                   if c is _res.FrameLocalClass.ALIAS_PTR})
    decl_names = _res._var_decl_names(list(func.body))
    movable = frozenset(
        n for n, t in (func.generator_locals or [])
        if n not in borrowed and n in decl_names
        and not _value_record_slot(t) and n in inp.sema_movable)
    return _FrameFacts(classes, loops, movable)


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


def classify_binding(site: _Site, name: str, typ: TpyType | None,
                     inp: PlanInputs, visible) -> BindingRepr:
    """The ONE classifier: the record a binding gets, from its declared
    type, its initializer's shape, sema's per-function facts and the
    records `visible` resolves (the bindings declared before the site)."""
    if site.prescan is not None:
        inp = replace(inp, prescan=site.prescan,
                      sema_movable=site.sema_movable or frozenset())
    for row in _ROWS.get(site.kind, ()):
        rec = row(site, name, typ, inp, visible)
        if rec is not None:
            return rec
    return BindingRepr(name=name, site=site.node, type=typ,
                       row=f"{site.kind}.plain",
                       is_param=site.kind in ("param", "lambda",
                                              "nested_param"))


def _param_row(site, name, typ, inp: PlanInputs, visible):
    """A parameter's record, from its declared type."""
    func, analyzer = inp.func, inp.analyzer
    f: dict = {}
    u = _uw(typ)
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
    ptype = next((t for n, t in inp.params if n == name), None)
    return BindingRepr(name=name, site=None, type=typ, row="param.seed",
                       is_param=True,
                       param_type=ptype if isinstance(ptype, TpyType)
                       else None, **f)


def _receiver_row(site, name, typ, inp: PlanInputs, visible):
    """The method receiver: const for a readonly method (functions.py,
    resumable.py), movable for a consuming one (context.py)."""
    if name != inp.self_receiver and not (name == "self" and inp.readonly_self):
        return None
    return BindingRepr(
        name=name, site=None, type=typ, row="param.receiver",
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
    return BindingRepr(name=name, site=None, type=typ, row="global.seed", **f)


def _module_global_row(site, name, typ, inp: PlanInputs, visible):
    """The module-init carrier's globals (`lower_top_level`)."""
    pre, analyzer = inp.prescan, inp.analyzer
    f: dict = {"global_cpp": pre.global_cpp.get(name),
               "global_slot": name in pre.global_slots}
    if name in inp.module_globals and typ is not None:
        if _value_opt_scalar(typ, analyzer) is not None:
            f["value_opt"] = ValueOptKind.SCALAR
        if not typ.is_value_type() and not typ.needs_wrapper():
            f["pointer"] = True
        bare = _uw(unwrap_ref_type(typ))
        if isinstance(bare, TupleType) and bare.has_pointer_repr_element():
            # A tuple global is a tuple of scalar globals: each reference
            # element is the pointer slot a reference global is, whatever
            # backing a write parks (`std::tuple<int32_t, Cell*> M`).
            f["tuple_layout"] = tuple_layout(bare, analyzer, borrow=True)
    elif name in pre.global_slots:
        f["pointer"] = True
    return BindingRepr(name=name, site=None, type=typ, row="global.module",
                       **f)


def _frame_row(site, name, typ, inp: PlanInputs, visible):
    """A resumable frame field, from its class (`_frame_facts`) and the
    value-opt kind of the frame's first decl of it."""
    from .resumable import _FRAME_SLOT_CLASSES, FrameLocalClass as C
    fr, ff = inp.frame, inp.frame_facts
    f: dict = {}
    cls = ff.classes.get(name)
    if cls in _FRAME_SLOT_CLASSES:
        f["frame_slot"] = True
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
    if name in ff.movable:
        f["movable"] = True
    lay = fr.layout.get(name)
    if lay is not None:
        f["effective_type"] = getattr(lay, "effective_type", None)
        f["payload"] = getattr(lay, "payload", None)
    return BindingRepr(name=name, site=None, type=typ, row="frame.layout",
                       **f)


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
        self.capture_funcs = () if func is inp.func else (inp.func,)
        self.frame_local_types = (
            {n: inp.seeds.get(n) for n in inp.frame.fields}
            if inp.frame is not None else {})
        self.resumable_leaf_mode = bool(func.is_generator or func.is_async)
        self.params = tuple(inp.params)
        self._visible = visible
        self.declared_view = _VisibleDeclared(
            visible, _narrowed_names(exprs, visible, inp.analyzer))
        # The whole-body borrow-tuple const fixpoint, as the previous
        # planning round left it (`_plan_btuple_const`); the records carry
        # it only once planning ends.
        self.btuple_const = inp.btuple_const

    def binding(self, name: str) -> BindingRepr:
        rec = self._visible(name) if isinstance(name, str) else None
        rec = UNBOUND if rec is None else rec
        plain, nullable = self.btuple_const
        if name in plain or name in nullable:
            rec = replace(rec, const_borrow_tuple=(rec.const_borrow_tuple
                                                   or name in plain),
                          const_opt_borrow_tuple=(rec.const_opt_borrow_tuple
                                                  or name in nullable))
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


def _mk(site: '_Site', name: str, typ, row: str, **f) -> BindingRepr:
    return BindingRepr(name=name, site=site.node, type=typ, row=row, **f)


def _decl_row(site, name, typ, inp: 'PlanInputs', visible):
    """A first VarDecl: the declaration ladder's rows, then the second name
    for an iterator object (statements.py's `auto&` alias arm), which
    whatever row the ladder picked holds the same way."""
    bind = _decl_local_binding(site, typ, inp, visible)
    rec = _decl_ladder(site, name, typ, inp, visible, bind)
    if rec is not None and bind is not None:
        rec = replace(rec, local_binding=bind)
    init, pre = site.init, inp.prescan
    if (isinstance(init, TpyName) and _protocol_auto_slot(typ)
            and name not in pre.reassigned and name not in pre.hoisted):
        src = visible(init.name)
        if src is not None and src.iterator_object:
            rec = (replace(rec, iterator_object=True) if rec is not None
                   else _mk(site, name, typ, "decl.iterator_alias",
                            iterator_object=True))
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
    """A first VarDecl: the declaration ladder's registering arms, in the
    order `_lower_stmt_dispatch` tries them (statements.py)."""
    from . import statements as _st
    from .comprehensions import _COMP_KINDS
    an, pre = inp.analyzer, inp.prescan
    stmt, init = site.node, site.init
    fn = site.func or inp.func
    vtype = typ
    movable = name in inp.sema_movable
    if init is None:
        # An annotation-only decl: the try-hoist arms' types
        # (`_try_hoist_type_ok`), else `decl.no_init_value`.
        if (pointer_repr_optional(vtype) is not None
                and not _st._try_hoist_type_ok(vtype, an)):
            return _mk(site, name, typ, "decl.no_init_opt_ptr", pointer=True,
                       movable=movable,
                       rebind_slot=name in pre.rvalue_reassigned)
        if vtype is not None and _st._try_hoist_type_ok(vtype, an):
            return _value_opt_of(site, name, typ, an, "decl.no_init_value")
        return None
    # `_closed_frame_first_decl`: a plain function's generator local a
    # `del` closes is an OPTIONAL_STORAGE slot, movable whatever sema's
    # verdict, and the decl lowers as a reassign of it.
    if (not (fn.is_generator or fn.is_async)
            and name in an.function_closed_frames.get(fn, ())):
        if isinstance(_p._binding_peel(unwrap_ref_type(typ)),
                      ConcreteGenType):
            return _mk(site, name, typ, "decl.closed_frame", pointer=True,
                       movable=True, optional_storage=True)
    # `_lower_error_return_bind`: the error-return bind's first binding,
    # never movable.
    er_fi = _st._error_return_stmt_fi(init, an)
    if er_fi is not None:
        if call_returns_cpp_ref(an, er_fi):
            return _mk(site, name, typ, "decl.er_alias", pointer=True)
        bare = (unwrap_readonly(unwrap_ref_type(er_fi.return_type))
                if isinstance(er_fi.return_type, TpyType) else None)
        inner = bare.wrapped if isinstance(bare, OwnType) else bare
        ptr = (name in pre.rvalue_reassigned and _p._f1_record(inner, an)
               and is_plain_nonvalue(inner)
               and not (fn.is_generator or fn.is_async))
        return _mk(site, name, typ, "decl.er_bind", pointer=ptr,
                   rebind_slot=ptr)
    # An Own-element value tuple, storage form.
    if (isinstance(vtype, TupleType) and vtype.has_own_element()
            and not vtype.has_pointer_repr_element()
            and name not in pre.reassigned):
        return _mk(site, name, typ, "decl.own_elem_tuple",
                   storage_tuple=True, movable=movable)
    if vtype is None:
        return None
    ctx = _PlanCtx(inp, visible, fn, (init,))
    # `_lower_record_ptr_slot_decl`: the escape-hoist / name-reassigned
    # record pointer slot.
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
        return _mk(site, name, typ, "decl.record_slot", pointer=True,
                   movable=movable,
                   rebind_slot=name in pre.rvalue_reassigned)
    # `_lower_container_ptr_slot_decl`: a name-reassigned container literal.
    vr = _p.resolve_pending_container(vtype, an) or vtype
    if (isinstance(init, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral,
                          TpyListRepeat))
            and name in pre.reassigned
            and name not in pre.rvalue_reassigned
            and name not in pre.hoisted and name not in pre.move_through
            and _p._f1_container_ref(unwrap_readonly(unwrap_send_sync(vr)))):
        return _mk(site, name, typ, "decl.container_slot", pointer=True,
                   movable=movable)
    fixed = (name not in pre.reassigned and name not in pre.hoisted
             and name not in pre.move_through)
    # `decl.storage_opt_name_lift`: a storage-opt loop / comprehension var
    # lifted to `T*`.
    if (isinstance(init, TpyName) and fixed
            and pointer_repr_optional(vtype) is not None
            and _optional_ptr_borrow_wide(vtype, an) is not None):
        src = visible(init.name)
        if src is not None and src.storage_opt:
            return _mk(site, name, typ, "decl.storage_opt_lift",
                       pointer=True, movable=movable,
                       const=src.const_storage_opt)
    # The already-erased Own[dyn] call, then @dynamic protocol locals
    # (`_lower_dyn_own_erased_call_decl`, `_lower_dyn_protocol_decl`),
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
        return _mk(site, name, typ, "decl.dyn_own_erased", movable=movable)
    if (not site.in_branch and isinstance(vtype, NominalType)
            and is_dyn_protocol(vtype)):
        if isinstance(svt_u, OwnType):
            # The deferred-erasure coroutine frame local is a value
            # `std::optional<frame>`, yet the @dynamic decl arm's caller
            # binds every decl it returns as a pointer local.
            return _mk(site, name, typ, "decl.coro_frame_local",
                       pointer=True, coro_frame=True)
        if init is not None and name not in pre.hoisted:
            return _mk(site, name, typ, "decl.dyn", pointer=True)
        return None
    # `_lower_opt_btuple_decl`: the nullable borrow tuple.
    if (not site.in_branch and _p._optional_borrow_tuple(vtype, an) is not None
            and name not in pre.hoisted and name not in pre.move_through
            and not (fn.is_generator or fn.is_async)):
        # `_lower_opt_btuple_decl`'s rows: the nullable-tuple call binds
        # whatever its const-ness; past it a const binding, and an init no
        # row lowers, fall through to the cascade below.
        if (_p._nullable_borrow_tuple_call(init, an) is not None
                and name not in pre.reassigned):
            return _mk(site, name, typ, "decl.opt_btuple",
                       optional_borrow_tuple=True)
        if not ctx.binding(name).const_opt_borrow_tuple:
            own_call = _p._btuple_owning_call_init(init, an)
            if (isinstance(init, TpyNoneLiteral) or own_call
                    or (isinstance(init, (TpyFieldAccess, TpySubscript))
                        and _st._borrow_tuple_source_ok(init, ctx))):
                return _mk(site, name, typ, "decl.opt_btuple",
                           optional_borrow_tuple=True,
                           rebind_slot=bool(own_call) and not isinstance(
                               init, TpyNoneLiteral))
    # The borrow-local rows (`_decl_local_binding`).
    if bind is not None:
        const = False
        if bind in (LocalBinding.REF_ALIAS, LocalBinding.POINTER,
                    LocalBinding.OPTIONAL_TO_PTR):
            const = _p._f1_is_const(bind, vtype, stmt, ctx)
        if bind is LocalBinding.REF_ALIAS:
            return _mk(site, name, typ, "decl.ref_alias", ref_alias=True,
                       const=const)
        if bind is LocalBinding.POINTER:
            return _mk(site, name, typ, "decl.pointer", pointer=True,
                       movable=movable, const=const,
                       rebind_slot=(name in pre.rvalue_reassigned
                                    and _pointer_decl_addresses(
                                        init, vtype, inp, visible, fn)))
        if bind is LocalBinding.REBIND_SLOT:
            return _mk(site, name, typ, "decl.rebind_slot", pointer=True,
                       rebind_slot=True, movable=movable)
        if bind is LocalBinding.OPT_PTR_SLOT:
            return _opt_ptr_slot_decl(site, name, typ, inp, ctx, movable)
        if bind is LocalBinding.OPTIONAL_TO_PTR:
            return _mk(site, name, typ, "decl.optional_to_ptr", pointer=True,
                       movable=movable, const=const)
    if (not site.in_branch and isinstance(init, TpyName)
            and pointer_repr_optional(vtype) is not None
            and name not in pre.reassigned):
        # `decl.opt_own_param_lift`: `y = x` off an `Own[P | None]` param
        # (the
        # param is the only `optional_storage` binding whose declared type
        # is the owning storage optional).
        src = visible(init.name)
        if (src is not None and src.is_param and src.site is None
                and _p._own_storage_opt_param(src.type, an) == vtype):
            return _mk(site, name, typ, "decl.opt_own_param_lift",
                       pointer=True)
    if (not site.in_branch and pointer_repr_optional(vtype) is not None
            and fixed
            and _p._tuple_local_ptr_elem_subscript(
                init, ctx.declared_view, ctx.names_with("borrow_tuple_mixed"), an)):
        # `decl.mixed_elem_ptr`: `e = p[1]` off a mixed own-borrow tuple
        # local.
        return _mk(site, name, typ, "decl.mixed_elem_ptr", pointer=True)
    # The `auto&&` storage-tuple alias (and its chain).
    rec = _storage_tuple_alias(site, name, typ, inp, visible, ctx)
    if rec is not None:
        return rec
    # A comprehension local.
    if type(init) in _COMP_KINDS and not vtype.is_value_type():
        return _mk(site, name, typ, "decl.comprehension", movable=movable)
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
            return _mk(site, name, typ, "decl.tparam_call")
    # A pointer-variant union local (`decl.union_addr`,
    # `decl.union_slot_rvalue`).
    ptr_u = _p._eligible_ptr_union_wide(vtype, an)
    if ptr_u is not None:
        if site.in_branch and name in pre.rvalue_reassigned:
            return None
        if not isinstance(init, TpyNoneLiteral) and not _checks._ptr_union_source_ok(
                init, ctx.declared_view, an, ptr_u,
                allow_field=name not in pre.reassigned):
            # The slot-hoist kinds -- a rebind slot when the name is
            # rvalue-reassigned, never const.
            return _mk(site, name, typ, "decl.ptr_variant_slot",
                       ptr_variant=True,
                       rebind_slot=name in pre.rvalue_reassigned)
        return _mk(site, name, typ, "decl.ptr_variant", ptr_variant=True,
                   const=_ptr_union_const(init, ctx))
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
        return _mk(site, name, ctu, "decl.storage_call_tuple",
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
        return _mk(site, name, typ, "decl.iterator_object",
                   iterator_object=True)
    # The `decl.btuple_*` arms: a borrow-tuple alias of a name, a ternary of
    # names or
    # mixed calls, or a call (the mixed own / borrow tuple among them).
    if (not site.in_branch and fixed
            and isinstance(init, (TpyName, TpyCall, TpyMethodCall,
                                  TpyIfExpr))):
        rec = _btuple_alias_decl(site, name, typ, inp, visible, ctx, movable)
        if rec is not None:
            return rec
    # `_lower_btuple_reassigned_decl`: a reassigned mixed own-borrow tuple's
    # first decl off a mixed call binds the call's render directly.
    if (not site.in_branch and name in pre.reassigned
            and name not in pre.hoisted and name not in pre.move_through):
        vbt = _uw(unwrap_ref_type(vtype))
        if (isinstance(vbt, TupleType) and vbt.has_pointer_repr_element()
                and not contains_pending_leaf(vbt)
                and _p._mixed_own_btuple_call(init, an)):
            return _mk(site, name, typ, "decl.btuple_reassigned",
                       borrow_tuple_mixed=True, movable=movable)
    # `decl.optview_shim`: the Optional-view param shim.
    if (name not in pre.reassigned and isinstance(init, TpyName)
            and _value_opt_owned_view(vtype, an) is not None):
        from .expressions import _param_declared_type
        if _p._opt_view_arg_shim(_param_declared_type(init.name, ctx),
                                 vtype, an):
            return _mk(site, name, typ, "decl.optview_shim",
                       value_opt=ValueOptKind.VIEW)
    # The `decl.tuple_literal*` arms: a tuple-literal first decl, by the
    # literal's captures.
    if isinstance(init, TpyTupleLiteral):
        rec = _tuple_literal_decl(site, name, typ, inp, movable)
        if rec is not None:
            return rec
    # The generic tail (`decl.opt_record_call`, the value-opt slots).
    if not fixed or not isinstance(init, (TpyCall, TpyMethodCall)):
        pass
    elif _st._own_opt_record_call_slot(stmt, vtype, ctx, an):
        return _mk(site, name, typ, "decl.opt_record_call",
                   value_opt=ValueOptKind.RECORD,
                   movable=movable and not vtype.is_value_type())
    rec = _value_opt_of(site, name, typ, an, "decl.value_opt")
    if rec is not None:
        if rec.value_opt is None and _p._value_opt_record(vtype) is not None:
            return replace(rec, value_opt=ValueOptKind.RECORD,
                           row="decl.opt_value_record")
        return rec
    if _p._value_opt_record(vtype) is not None:
        return _mk(site, name, typ, "decl.opt_value_record",
                   value_opt=ValueOptKind.RECORD)
    if not vtype.is_value_type():
        return _mk(site, name, typ, "decl.owned", movable=movable)
    return None


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
    """statements.py's tuple-literal decl arms (first decls): the borrow
    form of a REF-capture literal (inline elements noted), the widened
    storage family, the storage-record tuple with its owned layout; a plain
    value tuple continues to the generic tail (None)."""
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
        return _mk(site, name, typ, "decl.btuple_literal",
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
            return _mk(site, name, typ, "decl.tuple_literal_wide",
                       storage_tuple=storage, movable=storage and movable)
    if not storage_record:
        return None
    # `decl.storage_record_tuple`: the owned layout keys on the lowered
    # literal's elements: each
    # a scalar leaf or a constructor call (`_tuple_elem_ctor_call`).
    layout = None
    if all(storage_leaf(t) or _tuple_elem_ctor_call(v, an)
           for t, v in zip(tuple_t.element_types, init.elements)):
        layout = tuple_layout(tuple_t, an, captures=init.elem_capture,
                              own_records=True)
    return _mk(site, name, typ, "decl.tuple_literal_record",
               storage_tuple=True, movable=movable,
               tuple_layout=(layout if not site.in_branch
                             and name not in inp.prescan.reassigned
                             else None))


def _btuple_alias_decl(site, name, typ, inp: 'PlanInputs', visible, ctx,
                       movable) -> 'BindingRepr | None':
    """The `decl.btuple_alias` arms: the `auto` alias of a borrow-form tuple
    name, a ternary of two such names (or of two mixed own-borrow calls) or
    a call returning one; the mixed calls hold the mixed render."""
    an, init = inp.analyzer, site.init
    src_bt = None
    mixed = promoted = False

    def name_bt(n: str):
        src = visible(n)
        if src is None or src.pointer or src.storage_tuple:
            return None
        return _p._f1_tuple(src.type, an)

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
    inline = [r.inline_elems if (r := visible(n)) is not None else None
              for n in arm_names]
    inline_elems = inline[0] if any(i is not None for i in inline) else None
    const = bool(arm_names) and all(
        _p._const_borrow_name(n, ctx, const_locals=True) for n in arm_names)
    return _mk(site, name, typ, "decl.btuple_alias",
               borrow_tuple_mixed=mixed, movable=promoted and movable,
               inline_elems=inline_elems, const=const)


def _tuple_elem_ctor_call(member, an) -> bool:
    """Whether a storage-record tuple literal's element lowers to its
    record's constructor call (`THIRCtorCall`): a constructor call sema
    did not declare a copy of (`own_element_copies`)."""
    if not isinstance(member, TpyCall) or member in an.ctx.own_element_copies:
        return False
    if (member.macro_expansion is not None
            or member.cast_target_type is not None
            or member.enum_from_value is not None):
        return False
    fi = member.resolved_function_info
    return fi is not None and fi.is_constructor


def _opt_ptr_slot_decl(site, name, typ, inp: 'PlanInputs', ctx, movable
                       ) -> BindingRepr:
    """`_lower_opt_ptr_slot_decl`: the OPT_PTR_SLOT binding's three arms -- the
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
        return _mk(site, name, typ, "decl.opt_call_passthrough",
                   pointer=True,
                   const=_p._f1_is_const(LocalBinding.OPTIONAL_TO_PTR,
                                         vtype, site.node, ctx))
    if (not site.in_branch and isinstance(init, calls)
            and _p._call_storage_optional_return(init, an) is not None
            and _optional_ptr_borrow_wide(vtype, an) is not None):
        return _mk(site, name, typ, "decl.opt_storage_call", pointer=True,
                   movable=movable,
                   opt_storage_call=name in pre.reassigned)
    return _mk(site, name, typ, "decl.opt_ptr", pointer=True,
               movable=movable, rebind_slot=name in pre.rvalue_reassigned)


def _ptr_union_const(init, ctx) -> bool:
    """A pointer-variant local bound off a field of const storage (the
    place walk, which stops at a step through a `Ptr` / `Span`) or off a
    const receiver's subscript is const."""
    if is_property_getter_read(init):
        return receiver_const(init.obj, ctx.analyzer.get_expr_type,
                              lambda r: _p._f1_const_rooted_source(r, ctx))
    if isinstance(init, TpyFieldAccess):
        return _p._f1_const_rooted_source(init, ctx)
    if isinstance(init, TpySubscript) and isinstance(init.obj, TpyName):
        return (ctx.binding(init.obj.name).const
                or _p._param_is_const(init.obj.name, ctx.func, ctx.analyzer,
                                      ctx.record_name))
    return False


def _mixed_call(init, an) -> bool:
    fi = getattr(init, "resolved_function_info", None)
    if fi is None or not isinstance(fi.return_type, TpyType):
        return False
    cru = unwrap_readonly(unwrap_ref_type(fi.return_type))
    if isinstance(cru, OwnType):
        return False
    if (isinstance(cru, TupleType) and cru.has_own_element()
            and not cru.has_ref_elements()):
        return False
    t = (_p._f1_tuple(an.get_expr_type(init), an)
         or _p._wrapper_ref_tuple_return(fi.return_type, an))
    return t is not None and t.is_mixed_own()


def _value_opt_of(site, name, typ, an, row) -> 'BindingRepr | None':
    if _value_opt_scalar(typ, an) is not None:
        return _mk(site, name, typ, row, value_opt=ValueOptKind.SCALAR)
    if _value_opt_owned_view(typ, an) is not None:
        return _mk(site, name, typ, row, value_opt=ValueOptKind.VIEW)
    return None


def _storage_tuple_alias(site, name, typ, inp, visible, ctx):
    """`decl.storage_tuple_alias*`: a pointer-repr tuple local bound `auto&&`
    to a storage
    tuple lvalue, or to a name that is one -- the chain reads the source's
    record."""
    init = site.init
    pre = inp.prescan
    if (site.in_branch or init is None or typ is None
            or name in pre.reassigned or name in pre.hoisted
            or name in pre.move_through):
        return None
    t = _uw(typ)
    # An owned-element tuple holds its records inline; the alias re-binds
    # that storage as it re-binds a pointer slot.
    if not (isinstance(t, TupleType)
            and (t.has_pointer_repr_element() or t.has_own_element())):
        return None
    if isinstance(init, TpyFieldAccess):
        recv = init.obj
        const = (isinstance(recv, TpyName)
                 and (ctx.binding(recv.name).const
                      or _p._param_is_const(recv.name, ctx.func, ctx.analyzer,
                                            ctx.record_name)))
        return _mk(site, name, typ, "decl.storage_tuple_alias",
                   storage_tuple=True, const=const)
    if isinstance(init, TpySubscript):
        return _mk(site, name, typ, "decl.storage_tuple_alias",
                   storage_tuple=True,
                   const_storage_tuple=_btuple_const_storage(init, ctx))
    if isinstance(init, TpyName):
        src = visible(init.name)
        if src is not None and src.storage_tuple:
            layout = None
            if src.tuple_layout is not None:
                st = inp.analyzer.get_expr_type(init)
                st = _p.resolve_pending_container(st, inp.analyzer) or st
                if st == typ:
                    layout = src.tuple_layout
            return _mk(site, name, typ, "decl.storage_tuple_chain",
                       storage_tuple=True, tuple_layout=layout,
                       const_storage_tuple=_btuple_const_storage(init, ctx))
    return None


def _hoist_row(site, name, typ, inp: 'PlanInputs', visible):
    """A sema branch-first predecl at the head of an if / while / for /
    try / with / match (`if_branch_decls`): the flavor every ladder
    shares (`_nonvalue_hoist_flavor`), registered the way each ladder's
    emitter does (statements.py `_lower_hoist_predecls`,
    `_lower_if_hoist_predecls`)."""
    from . import statements as _st
    an, pre = inp.analyzer, inp.prescan
    stmt = site.node
    ctx = _PlanCtx(inp, visible, site.func or inp.func)
    if typ is None or pre.binds_global(name) or _st._frame_declares_hoist(
            name, ctx):
        return None
    bare = typ
    if _st._try_hoist_type_ok(bare, an):
        return _value_opt_of(site, name, typ, an, "hoist.value")
    vt = _p.resolve_pending_container(bare, an) or bare
    is_if = isinstance(stmt, TpyIf)
    if isinstance(stmt, TpyMatch):
        return _match_hoist(site, name, typ, vt, inp, ctx)
    if is_if and _p._optional_borrow_tuple(vt, an) is not None:
        return _mk(site, name, typ, "hoist.opt_btuple",
                   optional_borrow_tuple=not site.in_branch)
    vtu = _uw(vt)
    if isinstance(vtu, TupleType) and vtu.has_pointer_repr_element():
        srcs = _st._borrow_tuple_binding_sources(name, ctx) or ()
        mixed = any(isinstance(s, (TpyCall, TpyMethodCall))
                    and _mixed_call(s, an) for s in srcs)
        own_call = is_if and any(_p._btuple_owning_call_init(s, an)
                                 for s in srcs)
        return _mk(site, name, typ, "hoist.btuple",
                   borrow_tuple_mixed=mixed, rebind_slot=own_call)
    if is_if and isinstance(vt, NominalType) and is_dyn_protocol(vt):
        return _mk(site, name, typ, "hoist.dyn", pointer=True)
    if _ptr_null_hoist(stmt, name, vt, inp, ctx):
        # A hoisted container ref-target of a sync for head's unpack
        # is a null pointer predecl the head re-points.
        return _mk(site, name, typ, "hoist.ptr_null", pointer=True)
    flavor = _st._nonvalue_hoist_flavor(name, vt, ctx)
    H = _st.HoistFlavor
    if (flavor is H.RESUMABLE and not is_if
            and isinstance(stmt, (TpyWhile, TpyForEach))):
        flavor = H.OPT_STORAGE
    if flavor in (H.CONST_POINTER, H.CONST_REBOUND):
        return _mk(site, name, typ, "hoist.const_pointer", pointer=True,
                   const=True)
    if flavor is H.POINTER:
        return _mk(site, name, typ, "hoist.pointer", pointer=True,
                   movable=name in inp.sema_movable,
                   rebind_slot=(is_if and name in pre.rvalue_reassigned
                                and pointer_repr_optional(vt) is None))
    if flavor is H.OPT_STORAGE:
        return _mk(site, name, typ, "hoist.opt_storage", pointer=True,
                   movable=True, optional_storage=True)
    return None


def _ptr_null_hoist(stmt, name, vt, inp: 'PlanInputs', ctx) -> bool:
    """The for-each arm's PTR_NULL head flavor's gate (`HoistFlavor.
    PTR_NULL`)."""
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


def _match_hoist(site, name, typ, vt, inp: 'PlanInputs', ctx):
    """match.py `_route_hoists`: a match's arm-declared hoist, by the kind
    its admission picks."""
    from . import statements as _st
    an, pre = inp.analyzer, inp.prescan
    func = site.func or inp.func
    if site.in_branch or func.is_generator or func.is_async:
        return None
    vopt = pointer_repr_optional(vt)
    if (vopt is not None
            and not isinstance(vopt.inner, ReadonlyType)
            and _p.record_like(vopt.inner, an)):
        if name in pre.move_through or name in pre.rvalue_reassigned:
            return None
        return _mk(site, name, typ, "hoist.match_opt_ptr", pointer=True)
    if not is_plain_nonvalue(vt):
        return None
    borrow_decls = an.function_stmt_borrow_decls.get(func, {})
    if borrow_decls.get(name, False):
        return None
    if (name in borrow_decls
            and name not in an.function_ever_owned_locals.get(func, set())):
        if name in pre.rvalue_reassigned:
            return None
        return _mk(site, name, typ, "hoist.match_ptr", pointer=True)
    if _st._nonvalue_hoist_flavor(name, vt, ctx) is _st.HoistFlavor.OPT_STORAGE:
        return _mk(site, name, typ, "hoist.match_opt_storage", pointer=True,
                   movable=True, optional_storage=True)
    if name in pre.rvalue_reassigned:
        return _mk(site, name, typ, "hoist.match_ptr_slot", pointer=True,
                   rebind_slot=True, movable=name in inp.sema_movable)
    return None


# The facts a loop head decides for its variable whatever the same-named
# outer binding says; the variable inherits every other fact from that
# binding (`_inherit_outer`).
_LOOP_SCRUBBED = frozenset({"pointer", "rebind_slot", "frame_slot",
                            "const_loop_var"})


# A loop-head unpack target decides the same facts, except const, which it
# inherits.
_HEAD_TARGET_SCRUBBED = frozenset({"pointer", "rebind_slot", "frame_slot"})


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
    f["const_loop_var"] = bool(stmt.const_loop_var or yields_const)
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
        return _mk(site, name, typ, "loop.unpack_holder",
                   **_inherit_outer(f, outer))
    if _p._foreach_value_opt_elem(et) is not None:
        f["value_opt"] = ValueOptKind.SCALAR
    elif _p._foreach_storage_opt_elem(et, an):
        f["storage_opt"] = True
        src = (stmt.iterable.obj if isinstance(stmt.iterable, TpyMethodCall)
               else stmt.iterable)
        f["const_storage_opt"] = _st._iteration_yields_const(src, ctx, an)
    # A consuming loop's variable owns its element: movable whatever
    # sema's verdict says.
    if (isinstance(et, OwnType) and not stmt.hoist_loop_var) or (
            _st._for_consuming_route(stmt, an, ctx.declared_view)
            is not None):
        f["movable"] = True
    return _mk(site, name, typ, "loop.var", **_inherit_outer(f, outer))


def _with_row(site, name, typ, inp: 'PlanInputs', visible):
    """`_lower_with`: a sync `with ... as` target of a reassigned record."""
    func = site.func or inp.func
    if (func.is_generator or func.is_async or not isinstance(typ, TpyType)
            or typ.is_value_type()
            or not _p.record_like(_uw(typ), inp.analyzer)
            or name not in inp.prescan.reassigned):
        return None
    return _mk(site, name, typ, "with.pointer", pointer=True)


def _walrus_row(site, name, typ, inp: 'PlanInputs', visible):
    """A walrus target's first binding (`_lower_expr_impl`'s TpyNamedExpr
    arm)."""
    an, pre = inp.analyzer, inp.prescan
    func = site.func or inp.func
    if (func.is_generator or func.is_async or name in pre.hoisted
            or typ is None):
        return None
    vtu = _uw(unwrap_ref_type(resolve_int_literals(
        an.get_expr_type(site.node), an.ctx.default_int_for_literal)
        or typ))
    ctx = _PlanCtx(inp, visible, func)
    borrow_decls = an.function_stmt_borrow_decls.get(func, {})
    ever_owned = an.function_ever_owned_locals.get(func, set())
    if (name in borrow_decls and name not in ever_owned
            and is_plain_nonvalue(vtu) and _p.record_like(vtu, an)):
        const = bool(borrow_decls.get(name)) or _p._walrus_src_is_const(
            site.init, ctx)
        return _mk(site, name, typ, "walrus.ptr_alias", pointer=True,
                   const=const)
    if _p._optional_ptr_borrow(vtu, an) is not None:
        return _mk(site, name, typ, "walrus.opt_ptr", pointer=True,
                   const=_p._walrus_src_is_const(site.init, ctx))
    return _value_opt_of(site, name, typ, an, "walrus.value_opt")


def _unpack_row(site, name, typ, inp: 'PlanInputs', visible):
    """A tuple-unpack target; a loop head's target shadows a same-named
    outer binding the way the loop variable does (`_inherit_outer`)."""
    rec = _unpack_target(site, name, typ, inp, visible)
    outer = visible(name)
    if not site.node.is_loop_head or outer is None:
        return rec
    if rec is None:
        rec = _mk(site, name, typ, "unpack.plain")
    own = {fld: getattr(rec, fld) for fld in REPR_FIELDS}
    merged = _inherit_outer(dict(own), outer, _HEAD_TARGET_SCRUBBED)
    if outer.row == "hoist.ptr_null":
        # The head re-adds its null-pointer hoists after the scrub.
        merged["pointer"] = True
    return replace(rec, **merged)


def _unpack_target(site, name, typ, inp: 'PlanInputs', visible):
    """A tuple-unpack target (`_lower_stmt_dispatch`'s TpyTupleUnpack arm;
    the for-head unpack of its for-each arm): every standalone target is
    promoted, the flags pick the bind."""
    an = inp.analyzer
    stmt = site.node
    i = stmt.targets.index(name)
    flag = (lambda xs: bool(xs[i]) if i < len(xs) else False)
    is_ref = flag(stmt.is_ref)
    is_owned, is_rebound = flag(stmt.is_owned), flag(stmt.is_rebound)
    tt = unwrap_ref_type(typ) if isinstance(typ, TpyType) else None
    f: dict = {}
    if not stmt.is_loop_head:
        f["movable"] = name in inp.sema_movable
    if _p._optional_ptr_borrow(tt, an) is not None:
        if stmt.is_loop_head or (is_ref and not _p.record_like(tt, an)) \
                or (not is_ref and is_owned):
            f["pointer"] = True
        return _mk(site, name, typ, "unpack.opt_ptr", **f)
    if (not stmt.is_loop_head and not is_ref and is_owned
            and _p._eligible_ptr_union(tt, an) is not None):
        return _mk(site, name, typ, "unpack.ptr_variant", ptr_variant=True,
                   **f)
    if is_ref and is_rebound and _p.record_like(tt, an):
        return _mk(site, name, typ, "unpack.ptr", pointer=True,
                   rebind_slot=name in inp.prescan.rvalue_reassigned, **f)
    if not is_ref:
        rec = _value_opt_of(site, name, typ, an, "unpack.value_opt")
        if rec is not None:
            return replace(rec, **f)
        if (not stmt.is_loop_head
                and _own_opt_storage_binding(tt)):
            return _mk(site, name, typ, "unpack.value_opt_record",
                       value_opt=ValueOptKind.RECORD, **f)
    return _mk(site, name, typ, "unpack.plain", **f) if f else None


# A comprehension variable is never the pointer its same-named outer binding
# may be. The route refuses a variable hiding a rebind slot, a storage tuple
# or a narrowed name; every other fact is inherited, which is wrong for a
# value Optional and a frame slot (BUGS.md#comp-loop-var-shadows-optional-ptr-param,
# BUGS.md#async-comp-target-named-like-frame-loop-var).
_COMP_SCRUBBED = frozenset({"pointer"})


def _comp_row(site, name, typ, inp: 'PlanInputs', visible):
    """A comprehension variable: the facts its clause decides over those of
    the same-named outer binding it shadows (`_inherit_outer`)."""
    rec = _comp_clause_row(site, name, typ, inp, visible)
    outer = visible(name)
    if outer is None:
        return rec
    if rec is None:
        rec = _mk(site, name, typ, "comp.plain")
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
    if route.unpack_types is not None:
        # A pointer-repr Optional element of the unpacked tuple binds its
        # storage optional.
        tt = route.unpack_types[gen.unpack_vars.index(name)]
        if _p._optional_ptr_borrow(tt, an) is not None:
            return _mk(site, name, typ, "comp.unpack_storage_opt",
                       storage_opt=True)
        return None
    etp = unwrap_readonly(route.et) if route.et is not None else None
    if isinstance(etp, TupleType) and etp.has_pointer_repr_element():
        return _mk(site, name, typ, "comp.storage_tuple", storage_tuple=True)
    if _p._foreach_storage_opt_elem(route.et, an):
        ctx = _PlanCtx(inp, visible, site.func or inp.func)
        src = (gen.iterable.obj if isinstance(gen.iterable, TpyMethodCall)
               else gen.iterable)
        return _mk(site, name, typ, "comp.storage_opt", storage_opt=True,
                   const_storage_opt=_st._iteration_yields_const(src, ctx,
                                                                 an))
    return None


def _match_row(site, name, typ, inp: 'PlanInputs', visible):
    """A match capture (match.py: the field captures of
    `_lower_field_subpatterns`, the Optional chain's whole-subject bind)."""
    fact = (site.route or {}).get(name)
    if fact is None:
        return None
    _t, value_opt, pointer = fact
    if value_opt is None and not pointer:
        return None
    return _mk(site, name, typ, "match.capture", value_opt=value_opt,
               pointer=pointer)


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
        rec = _value_opt_of(_Site("match"), name, ft, an, "")
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
    return _mk(site, name, typ, "def.closure_local", nested_def=True)


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
            for i, n in enumerate(s.targets):
                if n is None or n in recs or (
                        not s.is_loop_head and node.lookup(n) is not None):
                    continue
                if (frame_head and n in fr.fields
                        and node.lookup(n) is not None):
                    continue
                tt = s.target_types[i] if i < len(s.target_types) else None
                recs[n] = self._record(self._site("unpack", s), n, tt, node)
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
                    after.setdefault(n, r)
            # Captures outlive the statement.
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
        site = replace(self._site("match", case), route=facts)
        for cap in iter_capture_bindings(case.pattern):
            prev = node.lookup(cap.name)
            fact = facts.get(cap.name)
            if (prev is not None and cap.name not in caps
                    and fact is not None and fact[1] is not None
                    and prev.value_opt is None):
                # `match.optional_full_bind`: a whole-Optional capture
                # into an existing binding (a frame field) gives it the
                # value-opt kind past the statement.
                caps[cap.name] = replace(prev, value_opt=fact[1],
                                         row=prev.row + "+match_bind")
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
            if (prev is None or prev.row != "frame.layout"
                    or n in fr.entry_declared or prev.value_opt is not None):
                continue
            vt = fr.local_types.get(n, raw)
            vt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vt)))
                  if isinstance(vt, TpyType) else None)
            kind = _value_opt_of(_Site("hoist"), n, vt, self.analyzer, "")
            if kind is not None:
                out[n] = replace(prev, value_opt=kind.value_opt,
                                 row=prev.row + "+frame_hoist")
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
                                 row=prev.row + "+unpack_promote")
                self._key(s, out[n])
        return out

    def function_scope(self, func: TpyFunction, parent: ScopeNode,
                       nonlocal_names: 'AbstractSet[str]') -> None:
        params = [n for n, _t in func.params]
        own = frozenset(scope_bound_names(func.body, params))
        recs = {n: BindingRepr(name=n, site=None, type=t,
                               row="nested_param.plain", is_param=True)
                for n, t in func.params}
        # The nested body's prescan carries no pointer-slot globals
        # (`nested_def_prescan`): an enclosing one reads there as the
        # pointer its record says it is, not as a global slot.
        outer_pre = self.prescan or self.inp.prescan
        for n in outer_pre.global_slots:
            outer = parent.lookup(n)
            if n not in own and outer is not None and outer.global_slot:
                recs[n] = replace(outer, global_slot=False,
                                  row=outer.row + "+nested_def")
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
                        row="lambda.plain", is_param=True,
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
                site = replace(self._site("comp", gen), route=route)
                recs = {n: self._record(site, n, t, cur)
                        for n, t in _clause_types(e, gen, route, self.analyzer)}
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
    record per declaring site in source order, then the whole-body
    borrow-tuple const fixpoint over the records. A declaration row reads
    that fixpoint (`_PlanCtx`), so a body where it marks a name is planned
    again with it until the two agree."""
    if inp.frame is not None and inp.frame_facts is None:
        inp = replace(inp, frame_facts=_frame_facts(inp))
    table = _plan_once(inp, body)
    for _round in range(3):
        consts = _plan_btuple_const(inp, table)
        if consts == inp.btuple_const:
            break
        inp = replace(inp, btuple_const=consts)
        table = _plan_once(inp, body)
    _mark_btuple_const(table, *inp.btuple_const)
    return table


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


def _btuple_src_const(src: TpyExpr, ctx: '_PlanCtx') -> bool:
    """Const verdict for a borrow-tuple binding source, over the binding
    records: a ternary is const if either arm is; a const-rooted lvalue
    chain or a const-bound name is const; an explicitly
    `readonly[...]`-typed source is const even without a const binding."""
    if isinstance(src, TpyCoerce):
        return _btuple_src_const(src.expr, ctx)
    if isinstance(src, TpyIfExpr):
        return (_btuple_src_const(src.then_expr, ctx)
                or _btuple_src_const(src.else_expr, ctx))
    if _btuple_const_storage(src, ctx):
        return True
    return isinstance(ctx.analyzer.get_expr_type(src), ReadonlyType)


def _plan_btuple_const(inp: PlanInputs, table: BindingTable) -> tuple:
    """The borrow-tuple const fixpoint over the table: OR const over every
    binding source of a reassigned / hoisted pointer-repr-tuple local to a
    fixpoint over name chains, each source judged against the records
    visible at its own statement."""
    func = inp.func
    plain, nullable = _borrow_tuple_const_sources(
        func, inp.analyzer, inp.prescan.reassigned, inp.prescan.hoisted)
    if not plain and not nullable:
        return (frozenset(), frozenset())
    const_set: set[str] = set()
    opt_set: set[str] = set()
    pairs = ((plain, const_set), (nullable, opt_set))
    changed = True
    while changed:
        changed = False
        for binds, cs in pairs:
            for name, srcs in binds.items():
                if name in cs:
                    continue
                for src, stmt in srcs:
                    node = table.scope_at.get(stmt) or table.root
                    ctx = _PlanCtx(inp, node.lookup, func)
                    ctx.btuple_const = (const_set, opt_set)
                    try:
                        is_const = _btuple_src_const(src, ctx)
                    except ThirUnsupported:  # unanswerable
                        is_const = False
                    if is_const:
                        cs.add(name)
                        changed = True
                        break
    return (frozenset(const_set), frozenset(opt_set))


def _mark_btuple_const(table: BindingTable, plain: frozenset,
                       nullable: frozenset) -> None:
    """Write the fixpoint's bits on every record of a marked name, in every
    scope node and site entry that holds it."""
    if not plain and not nullable:
        return
    new: dict[int, BindingRepr] = {}

    def swap(rec: BindingRepr) -> BindingRepr:
        if rec.name not in plain and rec.name not in nullable:
            return rec
        got = new.get(id(rec))
        if got is None:
            got = replace(rec, const_borrow_tuple=rec.name in plain,
                          const_opt_borrow_tuple=rec.name in nullable)
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
    table = BindingTable(root)
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
        sink.append(("_row:" + rec.row, "", "", "", ""))


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
    """Build the body's binding table and attach it. A planning failure is
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
    except Exception as ex:
        raise RuntimeError(
            f"binding table: planning {_qualname(lc)} failed: "
            f"{type(ex).__name__}: {ex}") from ex
    install(lc, table)
