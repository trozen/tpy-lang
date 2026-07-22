"""Per-function lowering state: _Prescan, _NarrowScope, and _LowerCtx."""

from __future__ import annotations
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from enum import Enum, auto
from ...parse.nodes import TpyFunction, TpyGlobal
from ...typesys import (
    CallableType,
    OptionalType,
    ReadonlyType,
    TpyType,
    UnionType,
    VoidType,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_send_sync,
)
from ..nodes import THIRFormConvert, THIRNarrowedRead, THIRSelf
from .predicates import (
    _borrow_tuple_return_type,
    _container_storage_return,
    _res_container_return,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_ptr_value,
    _eligible_scalar,
    _eligible_value_union,
    _is_type_param_slot,
    _optional_ptr_borrow,
    _own_storage_union_return,
    _own_storage_viewfam_return,
    _own_type_param_slot,
    _record_borrow_return,
    _record_storage_return,
    _resolved_bytes_value,
    _resolved_str_value,
    _storage_optional_return_type,
    _span_return,
    _value_opt_scalar,
    _value_opt_view,
    _generic_value_tuple_return,
    _value_tuple_return,
)


class _ExprResultUse(Enum):
    VALUE = auto()
    DISCARD = auto()
    CONDITION = auto()
    TRUTHY = auto()
    STORAGE = auto()
    BORROW_BIND = auto()
    ITERABLE = auto()
    RECEIVER = auto()
    # ERASED/BORROWED await operand: the resumable skeleton immediately
    # moves/points the result into the sub-future slot (emplace(std::move..)
    # / &(..)), so a record-family result renders bare -- no value slot.
    SUSPEND = auto()


class _RecordCtorUse(Enum):
    DIRECT = auto()
    NESTED_ARG = auto()
    RECORD_TEMP = auto()


@dataclass(frozen=True, slots=True)
class _ExprUse:
    """How the immediate consumer will use one lowered expression result.

    `allow_temps` applies only to the expression passed to `_lower_expr`;
    recursive operands get the default value use unless their own consumer
    explicitly supplies another use.
    """
    result: _ExprResultUse = _ExprResultUse.VALUE
    allow_temps: bool = False
    record_ctor: _RecordCtorUse = _RecordCtorUse.DIRECT
    # Standalone tuple-unpack SOURCE position only: admit tuple-valued
    # results the `auto __tup_N = <expr>;` capture consumes whole -- an
    # `Own[F1-record]`-element tuple call result (`_owned_tuple_call_ret`)
    # and a value-tuple class constant. Never set at decl/return sinks
    # (their slots gate separately).
    tuple_source: bool = False
    # The make_adapter arg position only: admit an async-def FACTORY call
    # (`asyncio.run(main_coro())`'s inner call) -- the concrete coro frame
    # is consumed whole by the heap adapter, never a typed value slot.
    coro_factory: bool = False
    # The sync `with` manager position only: admit a @native record-returning
    # free call (`with open(path, mode) as f`) -- the result is stored in the
    # `__ctx_N` manager slot, whatever native symbol the overload resolves to.
    ctx_manager: bool = False
    # TARGET-LESS positions only (print args, compare operands): the AST's
    # pure-literal binop fold fires there (`_gen_binop` folds only when
    # target_type is None), so the THIR fold arm may mirror it. Slot-threaded
    # positions (decl init / arg / return) render the FULL operator expr on
    # the AST path and must keep rejecting.
    literal_fold_ok: bool = False

# --- F1 form slice: single-assignment non-value record locals + field reads ---

class _Prescan:
    """Per-function prescan facts the binding classifier reads -- the same sets
    codegen seeds into ctx (see setup_body_scope), recomputed here from the
    analyzer so lowering classifies identically without a CodeGenContext."""
    __slots__ = ("reassigned", "rvalue_reassigned", "hoisted", "move_through",
                 "alias_sources", "alias_born",
                 "ret_storage_opt", "ret_ptr_opt", "ret_borrow_tuple",
                 "ret_record_borrow", "ret_record_storage",
                 "ret_container_storage", "ret_res_container",
                 "ret_value_tuple", "ret_generic_tuple",
                 "ret_str", "ret_bytes",
                 "ret_char", "ret_union", "ret_ptr_union", "ret_own_union",
                 "ret_supported", "ret_callable",
                 "ret_value_opt", "ret_value_opt_view",
                 "value_opt_params", "param_names",
                 "has_self", "is_constructor", "global_seeded", "global_readonly",
                 "global_cpp", "global_write_cpp", "native_globals")

    def __init__(self, func: TpyFunction, analyzer) -> None:
        # Param names, for checks that must tell a param from a local (a str
        # param's aug-assign would need the owned-copy prologue -- see
        # _str_aug_append_ok).
        self.param_names = {n for n, _t in func.params}
        # Value-repr Optional[cheap scalar] params (`Int32 | None`): a
        # `return <param>` into a value-optional return slot passes the WHOLE
        # optional bare (deref-on-narrow stripped), so return lowering keys on
        # this to admit a narrowed param name the generic tail would deref.
        self.value_opt_params = {
            n for n, t in func.params
            if _value_opt_scalar(t, analyzer) is not None}
        # Whether the callable has a `self` receiver (instance method) -- the
        # lowering arms that treat the name `self` specially (the return-self arm,
        # the self-rebind rejects) key on this so a free function's local or
        # param that merely SHARES the name is not misclassified.
        self.has_self = bool(func.is_method and not func.is_staticmethod)
        # A constructor body: field writes here interact with the ctor MIL /
        # non-default-constructible-field emit (a separate deletion target),
        # so the plain-record field-write rung stays a method/function-body
        # shape and rejects in this position.
        self.is_constructor = bool(func.is_method and func.name == "__init__")
        # `global`-declared names lower_function seeded into scope (eligible
        # same-module scalar globals); the TpyGlobal lowering arm keys on it.
        self.global_seeded: frozenset[str] = frozenset()
        # Same-module value globals seeded READ-ONLY (never assigned in this
        # body -- see _seed_readonly_globals); the name-read witness keys on it.
        self.global_readonly: frozenset[str] = frozenset()
        # Read-only-seeded native/imported value globals: name -> the
        # PRE-RENDERED spelling THIRName.cpp carries (qualify_native_name /
        # imported_variable_cpp). Disjoint from global_readonly (those
        # render bare).
        self.global_cpp: dict[str, str] = {}
        # WRITE-seeded native-linkage globals: name -> the BARE C-name
        # target spelling of the AST's global-write arm
        # (`native_global_names.get(name, name)`, unqualified).
        self.global_write_cpp: dict[str, str] = {}
        # Module native-linkage globals (name -> C/C++ symbol,
        # module_native_global_names; lower_function threads them through);
        # the try hoist arm rejects a colliding predecl name, and the
        # spelled-read witness keys native vs imported on membership.
        self.native_globals: 'Mapping[str, str] | frozenset[str]' = frozenset()
        scan = analyzer.function_scan_results.get(id(func))
        global_decls = analyzer.function_global_decls.get(id(func), set())
        self.reassigned = (scan.reassigned - global_decls) if scan else set()
        # F2d: the subset reassigned with an rvalue source (the rebind-slot
        # trigger -- mirrors codegen's `ctx.rvalue_reassigned_vars` seeding).
        self.rvalue_reassigned = (
            (scan.rvalue_reassigned - global_decls) if scan else set())
        self.hoisted = analyzer.function_hoisted_vars.get(id(func), set())
        self.move_through = analyzer.function_move_through_vars.get(id(func), set())
        # Alias facts for the `del x` move-sink skips: names some alias binds
        # to (codegen's `ctx.aliased_vars` -- moving from them would gut the
        # alias) and names whose FIRST binding was an alias (`ctx.alias_names`
        # -- such a pointer-local may point at another local's storage).
        self.alias_sources = set(scan.alias_sources.values()) if scan else set()
        self.alias_born = scan.initial_alias_names if scan else set()
        # F2c: the function's storage-form Optional[F1-record] return slot, if any
        # (`Own[T] | None` -> `std::optional<T>`), so a `return None` /
        # `return <borrow T*>` lowers to `std::nullopt` / `ptr_to_optional`. None
        # for every other return type (the value-scalar/pointer-repr paths).
        rt = func.return_type if isinstance(func.return_type, TpyType) else None
        self.ret_storage_opt = _storage_optional_return_type(rt, analyzer)
        # The pointer-repr Optional[F1-record] return slot, if any
        # (`A | None` -> a borrow `A*` returned by value): `None` ->
        # `nullptr`, an already-pointer name -> bare, an F1-record name ->
        # `&(name)` (_optional_pointer_form_value's admitted subset).
        self.ret_ptr_opt = _optional_ptr_borrow(rt, analyzer)
        # The value-repr Optional[cheap scalar] return slot (`-> Int32 | None`
        # -> `std::optional<T>`): `return None` -> `std::nullopt`, a value-opt
        # param name passes the whole optional bare, every other scalar source
        # rides the generic return tail (its type-exact / coerce-wrapped
        # rendering matches the AST's `gen_expr_deref`).
        self.ret_value_opt = _value_opt_scalar(rt, analyzer)
        # The value-repr Optional[view] return slot -- str (`-> str | None` ->
        # `std::optional<std::string>`) OR bytes (`-> bytes | None` ->
        # `std::optional<std::vector<uint8_t>>`): `return None` -> `std::nullopt`,
        # a value-repr Optional[view] param name takes the view->owned shim
        # (`x ? std::make_optional(<conv>(*x)) : std::nullopt`, THIROptViewArg;
        # `<conv>` = `std::string` / `::tpy::bytes_copy`), and a str/bytes literal
        # lands bare (the owned literal / implicit conversion). The owned-view
        # twin of ret_value_opt.
        self.ret_value_opt_view = _value_opt_view(rt, analyzer)
        # F3: the function's borrow-form pointer-repr tuple return slot, if any
        # (`tuple[..., Ref]` -> `std::tuple<..., T*>`), so a `return <storage tuple
        # lvalue>` lifts via `tuple_to_pointer`. None for every other return type.
        self.ret_borrow_tuple = _borrow_tuple_return_type(rt, analyzer)
        # The borrow-form F1-record return slot (`-> Box` -> `Box&`): a bare
        # record borrow name (`return name;`), `self` (`return (*this);`), a
        # plain field read (`return recv.field;`), or -- value-type records
        # only, where the slot actually returns by value -- a record rvalue;
        # the return-stmt arm rejects every other source shape.
        self.ret_record_borrow = _record_borrow_return(rt, analyzer)
        # The storage-form F1-record return slot (`-> Own[Box]` -> `Box` by
        # value): bare names and record-rvalue ctor / by-value calls return
        # bare; everything else stays on the AST path.
        self.ret_record_storage = _record_storage_return(rt, analyzer)
        # The storage-form container return slot (`-> Own[list[T]]` -> a
        # by-value vector/map/set): bare owned container names and container
        # literals return bare (the decl-init renders, position-independent);
        # every other source shape stays on the AST path.
        self.ret_container_storage = _container_storage_return(rt, analyzer)
        # The RESUMABLE container return slot -- wider at the `Own` axis
        # (a coro's slot holds T by value either way); read only by the
        # resumable return arm's empty-literal guard.
        self.ret_res_container = _res_container_return(rt, analyzer)
        # The value-tuple return slot (`-> tuple[int, str]` -> a by-value
        # `std::tuple<...>`): a tuple literal renders the spelled brace-init
        # (THIRTupleLiteral) recursively -- the return element set is widened
        # over the narrow `_value_tuple` (nested value-tuple / value-Optional[
        # scalar] elements). A bare value-tuple name return stays on the narrow
        # arm (no bare-copy read arm for a widened-element receiver).
        self.ret_value_tuple = _value_tuple_return(rt, analyzer)
        # The GENERIC tuple return slot (>=1 TypeParamRef element): consumed
        # only by the RESUMABLE return arm (the async `val_or_ptr_t` bridge);
        # the sync return arm does not read it, so sync generic-tuple returns
        # stay on the AST path (not in ret_supported).
        self.ret_generic_tuple = _generic_value_tuple_return(rt, analyzer)
        # S1 str slice: the resolved str-family return type (owned `str` or
        # `StrView`), so a `return <view-form source>` into an owned `std::string`
        # return copies via the view->owned THIRFormConvert. None otherwise.
        # An `Own[str]` slot spells the same owned return; only the unwrap
        # differs (`_own_storage_viewfam_return`).
        self.ret_str = _resolved_str_value(rt, analyzer)
        # S6: the resolved bytes-family return type -- an owned `bytes` return
        # copies a view-form source via `::tpy::bytes_copy`; a `BytesView`
        # return renders a literal in its span form. `Own[bytes]` rides the
        # same arm via the unwrap.
        self.ret_bytes = _resolved_bytes_value(rt, analyzer)
        own_viewfam = _own_storage_viewfam_return(rt, analyzer)
        if own_viewfam is not None:
            if _resolved_str_value(own_viewfam, analyzer) is not None:
                self.ret_str = own_viewfam
            else:
                self.ret_bytes = own_viewfam
        # S4: a Char return slot -- `return "x"` renders a target-typed char
        # literal (`'x'`) on the AST path, a shape the return arm rejects.
        self.ret_char = _eligible_char(rt)
        # F4 U1: a value-union return slot -- `return None` renders
        # `std::monostate{}` (target-typed); other sources return bare.
        self.ret_union = _eligible_value_union(rt)
        # F4 U2: a pointer-variant return slot -- only same-union borrow
        # names return bare; a MEMBER record name takes the AST's `&(...)`
        # address-of lift, which the slice does not reproduce.
        self.ret_ptr_union = _eligible_ptr_union(rt, analyzer)
        # An `Own[A | B]` record-member union return slot (a by-value
        # storage `std::variant<A, B>`): a member-record ctor rvalue
        # returns bare (the converting ctor absorbs it).
        self.ret_own_union = _own_storage_union_return(rt, analyzer)
        # A value-bearing return must select one of the representations the
        # return arm consumes. Signatures remain AST-emitted; this fact is
        # checked only when lowering reaches an actual return value.
        # A non-template Callable return slot (`std::function<...>` by
        # value): the only admitted source is a closure local's bare name
        # (`return add;` -- the lambda converts implicitly).
        self.ret_callable = bool(
            isinstance(rt, CallableType) and not rt.is_template)
        self.ret_supported = bool(
            rt is None or isinstance(rt, VoidType)
            or self.ret_callable
            or _eligible_scalar(rt) or _eligible_char(rt)
            or _is_type_param_slot(rt) or _own_type_param_slot(rt)
            or _eligible_enum(rt, analyzer) is not None
            or _eligible_ptr_value(rt, analyzer)
            or _span_return(rt)
            or self.ret_storage_opt is not None
            or self.ret_ptr_opt is not None
            or self.ret_value_opt is not None
            or self.ret_value_opt_view is not None
            or self.ret_borrow_tuple is not None
            or self.ret_record_borrow is not None
            or self.ret_record_storage is not None
            or self.ret_container_storage is not None
            or self.ret_value_tuple is not None
            or self.ret_str is not None or self.ret_bytes is not None
            or self.ret_union is not None or self.ret_ptr_union is not None
            or self.ret_own_union is not None)

@dataclass
class _NarrowScope:
    """Lowering's branch/loop-scoped isinstance-narrowing state.

    `narrowed` maps each U3 isinstance-narrowed source var to its live
    extraction alias
    (`ctx.narrowed_vars`); reads rename, the isinstance condition keeps the
    original variant. `persistent_aliases` mirrors
    `ctx.declared_persistent_aliases` for the post-if statement-level
    extraction's collision bump (`__v` -> `__v_2`). `subject_union` records
    each persistently narrowed subject's ORIGINAL union (assert / post-if),
    read by the U4 re-assert bump to render the replacement alias after
    `declared` was retyped to the member. `persistent_narrowed` is the
    var-level subset whose live alias is statement-level.
    A branch/loop body lowers under a `snapshot()`, restored at the closing
    brace (the AST's scope-snapshot semantics). NB `_LowerCtx.inline_narrowed`
    stays outside the bundle: it is condition-scoped (its own snapshot in
    `_lower_narrow_cond`), never live across statements."""
    narrowed: dict[str, str] = field(default_factory=dict)
    persistent_aliases: set[str] = field(default_factory=set)
    subject_union: dict[str, UnionType] = field(default_factory=dict)
    persistent_narrowed: set[str] = field(default_factory=set)
    # Vars narrowed from an ANY subject (D15): consumers that mirror the
    # AST's declared-type classification (print args) key on this to pick
    # the Any fall-through render instead of the union reject.
    any_narrowed: set[str] = field(default_factory=set)
    # Vars narrowed via the polymorphic if-init cast: var -> the pre-spelled
    # C++ read (`(*__p_ptr)`), rendered verbatim through THIRName.cpp. No
    # alias statement exists -- the if-init pre-binds the cast pointer.
    spelled: dict[str, str] = field(default_factory=dict)

    def snapshot(self) -> '_NarrowScope':
        # Field-generic so a new container can't be silently shared: every
        # field is a dict/set, shallow-copied per snapshot.
        return _NarrowScope(**{f.name: getattr(self, f.name).copy()
                               for f in fields(self)})

# Every mutable per-name classification set on _LowerCtx, by scoping rule.
# The mirror of the AST's LocalScopeSnap (codegen_cpp/context.py): a branch
# body lowers over a per-branch `declared` COPY, so the lc-set entries its
# decls register must pop with the branch too -- a same-named sibling-branch
# or post-scope decl would otherwise classify against state its scope never
# saw (the missed-restore bug class).
#
# BRANCH-SCOPED: snapshotted and restored by `branch_scope()`. `narrow` joins
# the snapshot via its own `snapshot()`; `frame_slots` is here because the
# for-each shadow REMOVES names for the body -- the same symmetric restore
# re-adds them at the pop.
_BRANCH_SCOPED_SETS = (
    "const_locals", "pointers", "rebind_slot_locals", "dyn_protocol_locals",
    "optional_locals", "branch_hoisted", "iterator_object_locals",
    "ref_alias_locals", "value_opt_locals", "value_opt_view_locals",
    "value_opt_record_locals",
    "movable_locals", "storage_tuple_locals", "const_storage_tuple_locals",
    "frame_slots", "forbidden_reads", "forbidden_writes",
)
# DELIBERATELY NOT branch-scoped. Registration that must survive a scope
# (with-targets, match full-binds, a nested def's name) is done by ORDERING:
# the caller registers in its own frame, outside the inner push/pop.
#   unhandled_hoists -- function-scoped residue ledger; branch drains ARE the
#       accounting, restoring them would fake un-lowered hoists.
#   nested_def_locals -- the bound name outlives its block (the AST re-adds
#       it after its scope restore; Python names are function-scoped).
#   nested_returns -- function-scoped resumable accumulator (the seam table).
#   inline_narrowed -- condition-scoped: saved/restored by _lower_narrow_cond
#       within a single condition, never live across statements.
#   tparam_bounds -- init-only per-function fact.
_FUNCTION_SCOPED_STATE = (
    "unhandled_hoists", "nested_def_locals", "nested_returns",
    "inline_narrowed", "tparam_bounds",
)


class _LowerCtx:
    """Per-function lowering state threaded through `_lower_stmt`.

    `render_type` renders a decl C++ type the way codegen does
    (`TypeResolver.type_to_cpp`): it qualifies cross-module records and resolves
    the live module, which `TpyType.to_cpp()` does not, so it -- not `to_cpp()` --
    is the byte-identical source for an F1 borrow local's cpp_type. The default
    (`to_cpp`) is for analyzer-only callers (dump / standalone lowering) that
    never hit a non-value local.

    `render_type_stored` is the STORED-form sibling (`TypeResolver.
    type_to_cpp_stored`): the AST spells explicit template args on generic
    free-fn calls with it (val_or_ref<T> for Ref types, PendingView
    resolution), so any THIR arm mirroring that spelling must use this --
    not `render_type` -- for those slots."""
    __slots__ = ("analyzer", "func", "prescan", "render_type",
                 "render_type_stored", "render_resolve", "tparam_bounds",
                 "const_locals",
                 "pointers", "rebind_slot_locals", "dyn_protocol_locals",
                 "optional_locals", "branch_hoisted",
                 "iterator_object_locals",
                 "ref_alias_locals",
                 "value_opt_locals", "value_opt_view_locals",
                 "value_opt_record_locals", "movable_locals",
                 "self_receiver", "self_cpp", "self_is_pointer",
                 "record_name", "storage_tuple_locals",
                 "const_storage_tuple_locals", "frame_slots",
                 "resumable_leaf_mode", "nested_returns", "in_finally_helper",
                 "plain_frame_fields", "value_tuple_frame_locals",
                 "oneshot_lift_locals", "alias_ptr_locals",
                 "unpack_ptr_targets",
                 "unhandled_hoists", "narrow",
                 "inline_narrowed", "forbidden_reads", "forbidden_writes",
                 "nested_def_locals", "error_return_cpp")

    def __init__(self, func: TpyFunction, analyzer, render_type,
                 self_receiver: str | None = None,
                 record_name: str | None = None,
                 render_type_stored=None,
                 self_cpp: str = "this",
                 self_is_pointer: bool = True,
                 render_resolve=None) -> None:
        self.analyzer = analyzer
        self.func = func
        self.prescan = _Prescan(func, analyzer)
        self.render_type = render_type or (lambda t: t.to_cpp())
        self.render_type_stored = (render_type_stored
                                   or (lambda t: t.to_cpp_stored()))
        # `TypeResolver.resolve_type` (Pending view/container resolution) --
        # the nested-def lambda header spells params/returns through it
        # (`resolve_type(t).to_cpp_param(name)`), exactly like _gen_nested_def.
        # Identity for analyzer-only callers, whose value-scalar fixtures
        # never carry a Pending type.
        self.render_resolve = render_resolve or (lambda t: t)
        # The receiver name (`self`) when `func` is an instance method, else
        # None: it lowers to a THIRSelf and, unlike `pointers`, is not a
        # liftable borrow source (`_is_borrow_ptr_local` must never treat it
        # as one). `self_cpp` is its C++ spelling (`this` for a plain method,
        # `__self` for a resumable method coro) and `self_is_pointer` selects
        # `->` vs `.` field/method access -- a plain method's `this` is a
        # pointer (`->`), a coro's `__self` is a `Record&` reference (`.`).
        self.self_receiver = self_receiver
        self.self_cpp = self_cpp
        self.self_is_pointer = self_is_pointer
        # The owning record's name when `func` is a method: `_param_is_const`
        # resolves a record param's const verdict from the method's FunctionInfo
        # on this record, not the free-function registry.
        self.record_name = record_name
        # Type-param bounds in scope for this body -- the mirror of the AST's
        # `ctx.current_type_param_bounds` (record bounds, then the function's
        # own overriding them). Sema does not stamp bounds on the
        # TypeParamRef instances in expression types, so bounded-receiver
        # dispatch resolves them by name through this dict.
        self.tparam_bounds: dict = {}
        if record_name:
            ri = analyzer.registry.get_record(record_name)
            if ri is not None and ri.type_param_bounds:
                self.tparam_bounds.update(ri.type_param_bounds)
        if getattr(func, "type_param_bounds", None):
            self.tparam_bounds.update(func.type_param_bounds)
        self.const_locals: set[str] = set()
        # Names whose C++ binding is a bare `T*` -- F2 pointer-locals
        # (reseatable, recorded at first decl so a later reseat lowers
        # correctly) plus the pointer-repr Optional borrow names (Optional-ptr
        # params seeded below, OPTIONAL_TO_PTR locals added at their decl;
        # never reseated -- sema rejects the param rebind and a reassigned
        # Optional local classifies OTHER). Mirrors codegen's
        # `ctx.pointer_locals` for every render that keys on it: `->` field/
        # method access, the `(*p)` value deref, and the bare pass into `T*`
        # slots. The GATE side has no pointer-set analog for the Optional
        # names -- its faces key on the declared type in `ws.declared`.
        self.pointers: set[str] = set()
        for pname, ptype in func.params:
            if _optional_ptr_borrow(ptype, analyzer) is not None:
                self.pointers.add(pname)
        # F2d rebind-slot subset of `pointers`: their reseats lower as rvalue
        # rebinds (`p = &*(__slot_N = ...)`), not lvalue `&(...)` reseats.
        self.rebind_slot_locals: set[str] = set()
        # First-declared @dynamic protocol locals that are reassigned: their
        # reseat statements take the rebind emit (hoisted optional slot).
        self.dyn_protocol_locals: set[str] = set()
        # OPTIONAL_STORAGE branch-hoisted locals (`std::optional<T> name;`
        # if-head predecl, the AST's ctx.optional_locals): a subset of
        # `pointers` for the read side (deref reads, `->` access); assigns
        # write PLAIN into the optional (`name = <storage rvalue>;`).
        self.optional_locals: set[str] = set()
        # Branch-hoisted `T*` pointer-locals WITHOUT an if-head rebind slot
        # (reassigned but not rvalue-reassigned): an rvalue reseat allocates
        # its slot lazily at function top (PtrSlotKind.BRANCH_RVALUE); the
        # AST's ctx.branch_hoisted_vars.
        self.branch_hoisted: set[str] = set()
        # Iterator-object locals (`it = g()`, the `auto` decl off a
        # generator/iterator factory): the for-head's name arm admits one as
        # a plain lvalue iterable despite its protocol declared type (a
        # protocol PARAM stays deferred -- its C++ spelling is deduced).
        self.iterator_object_locals: set[str] = set()
        # REF_ALIAS-bound locals (`T& name = ...`) -- codegen's
        # `ctx.ref_bound_locals`. Consumed by the `del x` skip ladder: the
        # alias does not own the value, so no move-sink is emitted for it.
        self.ref_alias_locals: set[str] = set()
        # Value-repr Optional[scalar] LOCALS beyond the params: for-each LOOP
        # VARS over `list[T | None]` (`std::optional<T> item = *__beg_N;`,
        # registered by the for-each lowering for the loop's scope) and
        # chain-optional match captures binding the full subject (sema's
        # binds_full_optional; mirrors the AST's `ctx.var_types` registration
        # in `_emit_optional_arm_bindings` -- never removed, the binding leaks
        # function-wide like Python match scoping). The value-opt param render
        # sites (deref-on-narrow, the whole-optional arg/None-test/truthiness
        # renders) key on the declared binding shape, identical for a param
        # and these locals, so all ride the same arms via
        # `_value_opt_scalar_binding`; the movable-seeded last-use moves stay
        # param-only through the `_is_move_source` movable guard.
        self.value_opt_locals: set[str] = set()
        # Value-repr `Optional[view]` LOCALS (str/bytes) -- the view twin of
        # value_opt_locals, kept SEPARATE because a view binding's narrowed
        # deref is an OWNED `std::string`/`vector` (STORAGE) where the scalar
        # deref is a VALUE and a param's is a BORROW view; overloading the
        # scalar set would misfire the scalar-keyed read/move/reassign arms.
        # Consulted only by `_value_opt_view_binding` (None-test + narrowed
        # read); every other view-local position defers.
        self.value_opt_view_locals: set[str] = set()
        # STORAGE `std::optional<T>` RECORD locals -- an owned-optional-
        # returning call bound whole (`upgraded = w.upgrade()` ->
        # `std::optional<Rc<T>> upgraded = ...;`). The record twin of the
        # scalar/view sets: a NARROWED read derefs `(*upgraded)` (a record
        # lvalue consumed as a receiver), the None-test reads has_value.
        # Single-assignment only (the decl gate enforces it).
        self.value_opt_record_locals: set[str] = set()
        # Resumable frame_slot locals (R1c): a non-value coro/generator local
        # stored as `tpy::frame_slot<T>`. Reads render `(*name)` (deref=True on
        # the THIRName; member access is `.` since the slot is not a pointer),
        # writes render `name.emplace(value)` (THIRFrameSlotWrite). Populated
        # only by `lower_resumable`; empty for every sync body.
        self.frame_slots: set[str] = set()
        # Resumable CFG leaves reuse statement lowering for compound bodies.
        # Their nested frame writes and async-return shapes reject at the
        # statement arm rather than through a predictive leaf-tree scan.
        self.resumable_leaf_mode = False
        # Returns nested in leaf compounds (THIRResumableReturn), collected
        # here so `lower_resumable` can register their values into the body's
        # `return_values` table (the seam's id(ast)-keyed lookup).
        self.nested_returns: list = []
        # True while lowering a helper-based finally body: a `return` there
        # needs the helper's Poll-replay / __finally_stop renders, a named
        # rung -- reject instead of routing through the leaf-return hook
        # (generator bare returns route; see the dispatch return arm).
        self.in_finally_helper = False
        # Frame fields whose decl/reassign renders as the plain position-
        # blind `name = expr;` member assign (frame_fields minus the
        # frame_slot / borrow-tuple / coro-handle families, whose renders
        # differ). The branch-nested decl arm keys on it; populated only by
        # `lower_resumable`, empty for every sync body.
        self.plain_frame_fields: frozenset = frozenset()
        # Value/storage tuple frame fields (`std::tuple<...>` bare members):
        # the unpack arm ref-binds one as a name source
        # (`const auto& __tup_N = <name>;`). Populated only by
        # `lower_resumable`, empty for every sync body.
        self.value_tuple_frame_locals: frozenset = frozenset()
        # One-shot `__await_lift_*` frame temps (the skeleton's
        # `one_shot_lift_names`): the unpack arm rvalue-ref-binds one as a
        # consumable source (`auto&& __tup_N = (*<name>);`) and moves its
        # owned elements out. Populated only by `lower_resumable`, empty
        # for every sync body.
        self.oneshot_lift_locals: frozenset = frozenset()
        # Pointer-alias frame locals (the skeleton's pointer_alias_locals,
        # minus the synthetic decomposition temps): `T*` fields aliasing
        # live storage. Reads ride lc.pointers; the unpack arm binds one
        # via `= &(unwrap_ref(tuple_elem_ref(...)));` (frame_ptr_elem).
        # Populated only by `lower_resumable`, empty for every sync body.
        self.alias_ptr_locals: frozenset = frozenset()
        # Pointer-form tuple-unpack LOOP targets (the skeleton's
        # pointer_form_unpack_targets): `T*` fields the head unpack
        # re-points via `= &(std::get<i>(__tup_N));` off the deref'd
        # pointer holder. Populated only by `lower_resumable`.
        self.unpack_ptr_targets: frozenset = frozenset()
        self.unhandled_hoists = set(
            analyzer.function_hoisted_vars.get(id(func), ()))
        # F3 storage-tuple alias locals (`auto&& t = <storage tuple field>`): a read
        # off one is STORAGE form, lifted via `tuple_to_pointer` at borrow boundaries.
        self.storage_tuple_locals: set[str] = set()
        # Subset of storage_tuple_locals iterated from a const source (a const
        # loop var): the borrow tuple wrap spells `const T*` element pointers.
        self.const_storage_tuple_locals: set[str] = set()
        # F2e: sema's movable (owned) locals -- a borrow write/return source that
        # is one of these at last use moves (`ptr_to_optional_move`). The set only
        # grows during the body walk, so the final sema set matches the working
        # set at any post-decl write/return (see _is_move_source). Copied: the
        # param seeding below must not mutate the analyzer's set.
        self.movable_locals: set[str] = set(
            analyzer.function_movable_locals.get(id(func), ()))
        # Own[T] / Own[T]|None params of non-value payload are movable (the
        # caller gave up ownership) -- codegen seeds them at body-scope setup
        # (seed_param_locals), not via sema's per-function set, so lowering
        # mirrors that seeding here. Consumed by the Own-slot call-arg row's
        # move-vs-copy pick; the F2b/F2e write/return converts only ever see
        # borrow-pointer sources, which are never Own params. DELIBERATELY
        # PARTIAL: seed_param_locals' owned-movable tuple-param branch is NOT
        # mirrored -- no current consumer can see those names (the Own-slot
        # rows admit scalar/F1-record payloads only); a frontier that reuses
        # lc.movable_locals against tuple sources must extend this.
        # CAVEAT (proven by a corpus divergence): this is the RAW sema set,
        # while codegen registers movables only at NON-VALUE decl arms -- a
        # consumer matching non-record sources must value-type-filter first
        # (see _container_elem_move_source), else a sema-movable VALUE local
        # (a view-resolved promoted str) over-moves. The value-Optional param
        # arm below is the deliberate exception: seed_param_locals adds it to
        # codegen's movable set too, so it moves at its narrowed last-use read.
        for pname, ptype in func.params:
            own = unwrap_optional_own(unwrap_readonly(unwrap_send_sync(ptype)))
            if own is not None and not own.wrapped.is_value_type():
                self.movable_locals.add(pname)
            # A value-repr Optional[expensive-copy] param (`int | None` ->
            # std::optional<BigInt>, `str | None` -> optional<string_view>)
            # is movable at its narrowed last use -- seed_param_locals'
            # value-optional arm, mirrored with its EXACT condition (no
            # scalar/view split there; is_expensive_copy is the filter).
            # readonly params are excluded to honor the no-mutation contract.
            vopt_u = unwrap_readonly(unwrap_send_sync(ptype))
            if (isinstance(vopt_u, OptionalType)
                    and not vopt_u.uses_pointer_repr()
                    and not isinstance(ptype, ReadonlyType)
                    and vopt_u.inner.is_expensive_copy()):
                self.movable_locals.add(pname)
        # U3/U4 isinstance-narrowing scope (see _NarrowScope's docstring),
        # snapshot/restored around branch and loop bodies.
        self.narrow = _NarrowScope()
        # U4 compound conditions: subject -> (member_cpp, is_ptr) for the
        # alias-free THIRNarrowedRead inside the condition. Strictly
        # condition-scoped -- installed and popped by _lower_narrow_cond,
        # never live across statements (deliberately OUTSIDE _NarrowScope).
        self.inline_narrowed: dict[str, tuple[str, bool]] = {}
        self.forbidden_reads: set[str] = set()
        self.forbidden_writes: set[str] = set()
        # Closure locals bound by a lowered TpyNestedDef -- mirrors codegen's
        # `ctx.nested_def_locals`. Sole consumer: the Callable-return arm
        # (`return add;`); closure CALLS key on `fi.frame_captures` in
        # `_free_callee_kind` instead. The name survives the nested scope,
        # exactly like the AST's re-add.
        self.nested_def_locals: set[str] = set()
        # The enclosing function's @error_return error type render
        # (ctx.current_error_return) -- set by lower_function; drives the
        # return pass-through / return-tier raise admissions and rides
        # THIRFunction into the emit state.
        self.error_return_cpp: 'str | None' = None

    @contextmanager
    def branch_scope(self):
        """One scope pop for every branch-scoped lc name-set (plus `narrow`).

        Restore is by whole-set snapshot, so it is symmetric: in-scope adds
        (branch-local decl registrations) AND removals (the for-each shadow)
        both undo at the pop. Registration that must outlive the scope is the
        caller's job -- perform it in the enclosing frame, after (or outside)
        this context."""
        saved = [set(getattr(self, name)) for name in _BRANCH_SCOPED_SETS]
        saved_narrow = self.narrow.snapshot()
        try:
            yield
        finally:
            for name, entries in zip(_BRANCH_SCOPED_SETS, saved):
                setattr(self, name, entries)
            self.narrow = saved_narrow


@dataclass
class _LowerScope:
    """The complete live statement-lowering view.

    Function-wide representation state remains owned by `_LowerCtx`; lexical
    bindings and control position vary per nested body. Keeping both behind
    one carrier keeps lowering as the authoritative sequential walk.
    """
    lc: _LowerCtx
    declared: dict[str, TpyType]
    in_branch: bool = False
    branch_decls_ok: bool = False
    loop_depth: int = 0

    def admission_pointers(self) -> set[str]:
        """Pointer locals whose statement admission follows pointer rules."""
        return {
            name for name in self.lc.pointers
            if _optional_ptr_borrow(self.declared.get(name),
                                    self.lc.analyzer) is None
        }
