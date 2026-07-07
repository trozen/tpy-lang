"""Per-function lowering state: _Prescan, _WalkState, _NarrowScope, _LowerCtx."""

from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from ...parse.nodes import TpyFunction, TpyGlobal
from ...typesys import (
    TpyType,
    UnionType,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_send_sync,
)
from ..nodes import THIRFormConvert, THIRNarrowedRead, THIRSelf
from .predicates import (
    _borrow_tuple_return_type,
    _container_storage_return,
    _eligible_char,
    _eligible_ptr_union,
    _eligible_value_union,
    _optional_ptr_borrow,
    _own_storage_viewfam_return,
    _record_borrow_return,
    _record_storage_return,
    _resolved_bytes_value,
    _resolved_str_value,
    _storage_optional_return_type,
    _value_tuple,
)

# --- F1 form slice: single-assignment non-value record locals + field reads ---

class _Prescan:
    """Per-function prescan facts the binding classifier reads -- the same sets
    codegen seeds into ctx (see setup_body_scope), recomputed here from the
    analyzer so lowering classifies identically without a CodeGenContext."""
    __slots__ = ("reassigned", "rvalue_reassigned", "hoisted", "move_through",
                 "ret_storage_opt", "ret_ptr_opt", "ret_borrow_tuple",
                 "ret_record_borrow", "ret_record_storage",
                 "ret_container_storage", "ret_value_tuple",
                 "ret_str", "ret_bytes",
                 "ret_char", "ret_union", "ret_ptr_union", "param_names",
                 "has_self", "is_constructor", "global_seeded", "global_readonly",
                 "global_cpp", "native_globals")

    def __init__(self, func: TpyFunction, analyzer) -> None:
        # Param names, for gates that must tell a param from a local (a str
        # param's aug-assign would need the owned-copy prologue -- see
        # _str_aug_append_ok).
        self.param_names = {n for n, _t in func.params}
        # Whether the callable has a `self` receiver (instance method) -- the
        # gate arms that treat the name `self` specially (the return-self arm,
        # the self-rebind rejects) key on this so a free function's local or
        # param that merely SHARES the name is not misclassified.
        self.has_self = bool(func.is_method and not func.is_staticmethod)
        # A constructor body: field writes here interact with the ctor MIL /
        # non-default-constructible-field emit (a separate deletion target),
        # so the plain-record field-write rung stays a method/function-body
        # shape and rejects in this position.
        self.is_constructor = bool(func.is_method and func.name == "__init__")
        # `global`-declared names lower_function seeded into scope (eligible
        # same-module scalar globals); the TpyGlobal gate arm keys on it.
        self.global_seeded: frozenset[str] = frozenset()
        # Same-module value globals seeded READ-ONLY (never assigned in this
        # body -- see _seed_readonly_globals); the name-read witness keys on it.
        self.global_readonly: frozenset[str] = frozenset()
        # Read-only-seeded native/imported value globals: name -> the
        # PRE-RENDERED spelling THIRName.cpp carries (qualify_native_name /
        # imported_variable_cpp). Disjoint from global_readonly (those
        # render bare).
        self.global_cpp: dict[str, str] = {}
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
        # The value-tuple return slot (`-> tuple[int, str]` -> a by-value
        # `std::tuple<...>`): a tuple literal renders the spelled brace-init
        # (THIRTupleLiteral) and a bare value-tuple name returns bare (a
        # value-type copy -- no move/copy() machinery arises).
        self.ret_value_tuple = _value_tuple(rt, analyzer)
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

@dataclass
class _WalkState:
    """The eligibility walk's copy-per-branch scope state, mirroring lowering's
    scope growth. `declared` maps each in-scope name to its resolved (possibly
    narrowed) type; `pointers` carries the F2 pointer-local names a reseat
    reads, `rebind_slots` the F2d subset whose reseats are rvalue rebinds,
    `storage_tuple_locals` the F3 `auto&&` tuple aliases a borrow read lifts,
    `narrowed` the U3 isinstance-narrowed names whose reads rename to the
    extraction alias (writes to them are out of the slice),
    `persistent_narrowed` the subset whose live alias is statement-level
    (assert / early-return post-if -- registered in the AST's
    `declared_persistent_aliases`): only those admit the U4 re-assert bump;
    a branch/loop-scoped alias would collide instead (the BUGS.md
    `_gen_while` redeclaration). Walk arms mutate the active state in place
    (a POINTER decl extends `pointers`, a post-if fact retypes in
    `declared`); each branch/loop body walks a `branch_copy()` so siblings
    don't see each other."""
    declared: dict[str, TpyType]
    pointers: set[str] = field(default_factory=set)
    rebind_slots: set[str] = field(default_factory=set)
    storage_tuple_locals: set[str] = field(default_factory=set)
    narrowed: set[str] = field(default_factory=set)
    persistent_narrowed: set[str] = field(default_factory=set)

    def branch_copy(self) -> '_WalkState':
        # Field-generic so a new container can't be silently shared: every
        # field is a dict/set, shallow-copied per branch.
        return _WalkState(**{f.name: getattr(self, f.name).copy()
                             for f in fields(self)})

@dataclass
class _NarrowScope:
    """Lowering's branch/loop-scoped isinstance-narrowing state, the mirror of
    the eligibility walk's `_WalkState` bundle. `narrowed` maps each U3
    isinstance-narrowed source var to its live extraction alias
    (`ctx.narrowed_vars`); reads rename, the isinstance condition keeps the
    original variant. `persistent_aliases` mirrors
    `ctx.declared_persistent_aliases` for the post-if statement-level
    extraction's collision bump (`__v` -> `__v_2`). `subject_union` records
    each persistently narrowed subject's ORIGINAL union (assert / post-if),
    read by the U4 re-assert bump to render the replacement alias after
    `declared` was retyped to the member. `persistent_narrowed` is the
    var-level subset whose live alias is statement-level -- the 1:1 mirror of
    `_WalkState.persistent_narrowed` (same registration and restore points).
    A branch/loop body lowers under a `snapshot()`, restored at the closing
    brace (the AST's scope-snapshot semantics). NB `_LowerCtx.inline_narrowed`
    stays outside the bundle: it is condition-scoped (its own snapshot in
    `_lower_narrow_cond`), never live across statements."""
    narrowed: dict[str, str] = field(default_factory=dict)
    persistent_aliases: set[str] = field(default_factory=set)
    subject_union: dict[str, UnionType] = field(default_factory=dict)
    persistent_narrowed: set[str] = field(default_factory=set)

    def snapshot(self) -> '_NarrowScope':
        # Field-generic so a new container can't be silently shared: every
        # field is a dict/set, shallow-copied per snapshot.
        return _NarrowScope(**{f.name: getattr(self, f.name).copy()
                               for f in fields(self)})

class _LowerCtx:
    """Per-function lowering state threaded through `_lower_stmt`.

    `render_type` renders a decl C++ type the way codegen does
    (`TypeResolver.type_to_cpp`): it qualifies cross-module records and resolves
    the live module, which `TpyType.to_cpp()` does not, so it -- not `to_cpp()` --
    is the byte-identical source for an F1 borrow local's cpp_type. The default
    (`to_cpp`) is for analyzer-only callers (dump / standalone lowering) that
    never hit a non-value local."""
    __slots__ = ("analyzer", "func", "prescan", "render_type", "const_locals",
                 "pointers", "rebind_slot_locals", "movable_locals",
                 "self_receiver", "record_name", "storage_tuple_locals",
                 "narrow", "inline_narrowed")

    def __init__(self, func: TpyFunction, analyzer, render_type,
                 self_receiver: str | None = None,
                 record_name: str | None = None) -> None:
        self.analyzer = analyzer
        self.func = func
        self.prescan = _Prescan(func, analyzer)
        self.render_type = render_type or (lambda t: t.to_cpp())
        # The receiver name (`self`) when `func` is an instance method, else
        # None: it lowers to a THIRSelf (`this`) and renders `->` field reads
        # like a pointer-local, but unlike `pointers` it is not a liftable
        # borrow source (`_is_borrow_ptr_local` must never treat it as one).
        self.self_receiver = self_receiver
        # The owning record's name when `func` is a method: `_param_is_const`
        # resolves a record param's const verdict from the method's FunctionInfo
        # on this record, not the free-function registry.
        self.record_name = record_name
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
        # F3 storage-tuple alias locals (`auto&& t = <storage tuple field>`): a read
        # off one is STORAGE form, lifted via `tuple_to_pointer` at borrow boundaries.
        self.storage_tuple_locals: set[str] = set()
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
        # PARTIAL: seed_param_locals' sibling movable branches (owned-movable
        # tuple params, expensive-copy value-Optional params) are NOT
        # mirrored -- no current consumer can see those names (the Own-slot
        # rows admit scalar/F1-record payloads only); a frontier that reuses
        # lc.movable_locals against tuple/Optional sources must extend this.
        # CAVEAT (proven by a corpus divergence): this is the RAW sema set,
        # while codegen registers movables only at NON-VALUE decl arms -- a
        # consumer matching non-record sources must value-type-filter first
        # (see _container_elem_move_source), else a sema-movable VALUE local
        # (a view-resolved promoted str) over-moves.
        for pname, ptype in func.params:
            own = unwrap_optional_own(unwrap_readonly(unwrap_send_sync(ptype)))
            if own is not None and not own.wrapped.is_value_type():
                self.movable_locals.add(pname)
        # U3/U4 isinstance-narrowing scope (see _NarrowScope's docstring),
        # snapshot/restored around branch and loop bodies.
        self.narrow = _NarrowScope()
        # U4 compound conditions: subject -> (member_cpp, is_ptr) for the
        # alias-free THIRNarrowedRead inside the condition. Strictly
        # condition-scoped -- installed and popped by _lower_narrow_cond,
        # never live across statements (deliberately OUTSIDE _NarrowScope).
        self.inline_narrowed: dict[str, tuple[str, bool]] = {}
