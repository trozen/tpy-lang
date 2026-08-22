"""Per-function lowering state: _Prescan, _NarrowScope, and _LowerCtx."""

from __future__ import annotations
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from enum import Enum, auto
from ...parse.nodes import (TpyAssign, TpyCoerce, TpyExpr, TpyFieldAccess,
                            TpyFunction, TpyGlobal, TpyIfExpr, TpyName,
                            TpyNamedExpr, TpySubscript, TpyVarDecl)
from ...codegen_cpp.type_resolution import resolve_stmt_binding_type
from ...typesys import (
    CallableType,
    OptionalType,
    OwnType,
    ReadonlyType,
    TpyType,
    TupleType,
    UnionType,
    VoidType,
    is_own_pointer_repr_optional,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...codegen_cpp.forms import is_ptr_variant_union
from ...binding_audit import (enabled as _binding_audit_on,
                              fresh_record as _binding_fresh,
                              capture_thir as _binding_capture,
                              acknowledge_binding_partial as _binding_ack)
from ..nodes import THIRFormConvert, THIRNarrowedRead, THIRSelf
from .predicates import (
    _borrow_tuple_return_type,
    _container_borrow_return,
    _container_storage_return,
    _res_container_return,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_ptr_value,
    _eligible_scalar,
    _eligible_value_union,
    _is_type_param_slot,
    _native_iter_value_slot,
    _optional_ptr_borrow,
    _optional_ptr_borrow_wide,
    _nullable_static_protocol_param,
    _storage_optional_return_wide,
    _own_opt_storage_binding,
    _value_opt_string_owned,
    _own_viewfam_param,
    _param_is_const,
    _protocol_auto_slot,
    _dyn_borrow_return,
    _own_dyn_return,
    _own_genrec_return,
    _own_wrapper_return,
    _wrapper_borrow_return,
    _own_storage_union_return,
    _own_storage_viewfam_return,
    _own_type_param_slot,
    _record_borrow_return,
    _record_storage_return,
    _resolved_bytes_value,
    _resolved_str_value,
    _span_return,
    _value_opt_scalar,
    _value_opt_view,
    _generic_value_tuple_return,
    _own_storage_tuple_return,
    _wrapper_ref_tuple_return,
    _value_tuple_return,
    _union_elem_value_tuple,
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
    # An `Own[...]` value-variant ARG slot consuming a same-union
    # Own[A | B]-returning call rvalue whole -- the prvalue moves through
    # the `&&` slot bare (`describe(pick(True))`). Never threaded at
    # decl/return sinks (their slots gate separately).
    OWN_SLOT = auto()


class _RecordCtorUse(Enum):
    DIRECT = auto()
    NESTED_ARG = auto()
    RECORD_TEMP = auto()


class ValueOptKind(Enum):
    """The value-repr `std::optional<T>` LOCAL binding families -- one
    kind-tagged registration where three parallel sets used to live. The
    kinds differ only in the narrowed-deref FORM verdict (see the
    `value_opt_bindings` field comment)."""
    SCALAR = auto()
    VIEW = auto()
    RECORD = auto()


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
    # The record FIELD-WRITE copy sink only: admit a borrow-returning
    # record call source (`h.p = identity(pt);` -- the C++ copy-assign
    # absorbs the `T&`). Other STORAGE sinks (decls) bind REF_ALIAS off
    # the same result and must keep rejecting.
    record_copy_sink: bool = False
    # The BORROW-record RETURN sink only: admit a T&-returning call's
    # bare passthrough (`return get_first(items);`). A decl bind off
    # the same result is the REF_ALIAS frontier and must keep
    # rejecting -- this flag never leaves the return arm.
    borrow_ret_passthrough: bool = False
    # The FIELD-READ receiver position only: admit a T&-returning record
    # call composing transiently under the member read
    # (`ret_param_ref(shared).n` -- the temp lives to the end of the full
    # expression; nothing binds). Decl binds keep rejecting (REF_ALIAS
    # frontier) -- this flag never leaves the field-read receiver slot.
    field_recv: bool = False
    # The pointer-repr-tuple LAMBDA-return body only: admit a generic
    # call whose monomorphized val_or_ptr_t tuple IS the closure's
    # borrow-form trailing return -- the direct `return body;` needs no
    # element conversion. This flag never leaves the lambda arm.
    lambda_btuple_ret: bool = False
    # TARGET-LESS positions only (print args, compare operands): the AST's
    # pure-literal binop fold fires there (`_gen_binop` folds only when
    # target_type is None), so the THIR fold arm may mirror it. Slot-threaded
    # positions (decl init / arg / return) render the FULL operator expr on
    # the AST path and must keep rejecting.
    literal_fold_ok: bool = False
    # Positions where the AST threads a slot target into the render while
    # THIR lowers target-less: resolved-binop OPERAND slots
    # (`gen_expr_deref(left, receiver_type)`) and fixed-int FREE-call/ctor
    # arg slots (`gen_call_arg` threads the param). A both-literal
    # sub-binop renders the full operator there, never the target-less
    # fold -- so the nested binop lowers `slot_threaded` regardless of the
    # outer's sink. User-record METHOD args stay unflagged (the AST's
    # record loop passes target_type=None, so those FOLD).
    slot_threaded: bool = False
    # The module-init pointer-slot-global pass-through write
    # (`g = find(xs, k);`) and the bare ptr-opt call DECL bind
    # (`Point* r1 = get_or_none(true, p);`): admit a BORROW-returning
    # ptr-repr Optional result, which is already the `T*` the slot or
    # binding holds. Every other consumer of such a result materializes a
    # slot or lifts through `optional_to_ptr`, so they keep gating on
    # their own rows.
    ptr_opt_passthrough: bool = False
    # The pointer-repr `Optional[record]` FIELD-write sink only
    # (`h.value = find_point(pts, 1);`): admit a BORROW-returning ptr-repr
    # Optional result, which the sink then lifts through `ptr_to_optional`.
    # Distinct from `ptr_opt_passthrough`, whose result lands BARE -- one flag
    # cannot stand for both renders.
    ptr_opt_lift: bool = False
    # The value-variant UNION field-write sink only (`z.pet =
    # identity(new_pet);`): admit a ptr-variant union call result, which
    # the sink consumes whole through the `to_value_variant` lift.
    union_value_lift: bool = False
    # The borrow-tuple local decl sink only (`auto p = pair_of(b);`): admit a
    # pointer-repr tuple result, which the `auto` slot binds whole. Every other
    # consumer of such a result converts form, so they keep their own rows.
    btuple_slot: bool = False
    # The global ptr-slot's address-of catch-all only
    # (`p = &(get_item<Point>((*points), 0));`): admit a BORROW-returning
    # record call, whose T& result the position takes the address of. A bare
    # RECEIVER admission would also open field reads off such calls
    # (`shared(a).x`) -- the REF_ALIAS place/loan frontier, design-stopped.
    addr_call: bool = False
    # An ALWAYS_TRUE truthiness operand only: the wrap renders it inside
    # `static_cast<void>(...)`, so the result is discarded exactly as at
    # statement position -- the same widened return set applies.
    truthy_discard: bool = False
    # A gen_expr_deref position (a value-consuming read: call/element slots,
    # raise/decl name sources, indirect-coerce inners): ANY pointer-local
    # name fully derefs there (`(*p)`), where the default value use derefs
    # only the always-indirect bindings. Consumed by `_name_read_deref` --
    # the single resolution point for a name read's indirection.
    indirect_read: bool = False
    # The union-switch CALL-subject position only (`match p.choose(d):`):
    # admit a non-wrapper ptr-variant UNION call result, consumed whole by
    # the by-value dispatch local (`auto __match_subject_N = <call>;`).
    # Every other consumer of a union result converts or narrows, so they
    # keep their own rows.
    match_union_subject: bool = False

# --- F1 form slice: single-assignment non-value record locals + field reads ---

class _Prescan:
    """Per-function prescan facts the binding classifier reads -- the same sets
    codegen seeds into ctx (see setup_body_scope), recomputed here from the
    analyzer so lowering classifies identically without a CodeGenContext."""
    __slots__ = ("reassigned", "rvalue_reassigned", "hoisted", "move_through",
                 "alias_sources", "alias_born", "owned_viewfam_params",
                 "ret_storage_opt", "ret_ptr_opt", "ret_borrow_tuple",
                 "ret_record_borrow", "ret_record_storage",
                 "ret_container_storage", "ret_container_borrow",
                 "ret_res_container",
                 "ret_value_tuple", "ret_generic_tuple",
                 "ret_own_storage_tuple", "ret_wrapper_ref_tuple",
                 "ret_str", "ret_bytes",
                 "ret_char", "ret_union", "ret_ptr_union", "ret_union_borrow",
                 "ret_own_union",
                 "ret_genrec", "ret_own_wrapper", "ret_wrapper_borrow",
                 "ret_dyn_borrow", "ret_dyn_own",
                 "ret_supported", "ret_callable",
                 "ret_value_opt", "ret_value_opt_view",
                 "value_opt_params", "param_names",
                 "own_tuple_params",
                 "has_self", "is_constructor", "global_seeded", "global_readonly",
                 "global_cpp", "global_write_cpp", "native_globals",
                 "global_slots")

    def __init__(self, func: TpyFunction, analyzer,
                 params_override=None, return_type_override=None,
                 scan_override=None, hoisted_override=None,
                 move_through_override=None) -> None:
        # `params_override` / `return_type_override` carry a per-@overload
        # STUB's signature: the AST emits the impl body against the stub's
        # types, so every signature-derived fact here must key on them.
        src_params = func.params if params_override is None else params_override
        # Param names, for checks that must tell a param from a local (a str
        # param's aug-assign would need the owned-copy prologue -- see
        # _str_aug_append_ok).
        self.param_names = {n for n, _t in src_params}
        # Own[str]/Own[bytes] params: the signature spells the OWNED type by
        # value, so their name reads are STORAGE -- carved out of the
        # param-implies-view verdicts (`_str_name_form`/`_bytes_name_form`).
        self.owned_viewfam_params = {
            n for n, t in src_params if _own_viewfam_param(t) is not None}
        # `Own[tuple[...]]` params: the signature binds the STORAGE tuple by
        # value, so an element read is a `T&`/value -- never the borrow
        # param's deref-flagged `(*std::get<i>(p))` (the alias-decl
        # predicate keys on this; the expr type strips Own and cannot tell).
        self.own_tuple_params = {
            n for n, t in src_params
            if isinstance((_otp := unwrap_readonly(unwrap_send_sync(t))),
                          OwnType)
            and isinstance(unwrap_readonly(_otp.wrapped), TupleType)}
        # Value-repr Optional[cheap scalar] params (`Int32 | None`): a
        # `return <param>` into a value-optional return slot passes the WHOLE
        # optional bare (deref-on-narrow stripped), so return lowering keys on
        # this to admit a narrowed param name the generic tail would deref.
        self.value_opt_params = {
            n for n, t in src_params
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
        # POINTER-SLOT globals seeded read-only (non-value record/container
        # `T* g{};` slots): also in lc.pointers, so reads take the slot
        # renders (`(*g)`, `g->`, `&(*g)`); imported ones carry their
        # qualified spelling in global_cpp.
        self.global_slots: frozenset[str] = frozenset()
        # Module native-linkage globals (name -> C/C++ symbol,
        # module_native_global_names; lower_function threads them through);
        # the try hoist arm rejects a colliding predecl name, and the
        # spelled-read witness keys native vs imported on membership.
        self.native_globals: 'Mapping[str, str] | frozenset[str]' = frozenset()
        # The module-init walk has no per-function scan entry -- its facts come
        # from the analyzer's top_level_* results, passed in as overrides
        # (gen_module_init seeds ctx from exactly those three).
        scan = (analyzer.function_scan_results.get(id(func))
                if scan_override is None else scan_override)
        global_decls = analyzer.function_global_decls.get(id(func), set())
        self.reassigned = (scan.reassigned - global_decls) if scan else set()
        # F2d: the subset reassigned with an rvalue source (the rebind-slot
        # trigger -- mirrors codegen's `ctx.rvalue_reassigned_vars` seeding).
        self.rvalue_reassigned = (
            (scan.rvalue_reassigned - global_decls) if scan else set())
        self.hoisted = (analyzer.function_hoisted_vars.get(id(func), set())
                        if hoisted_override is None else hoisted_override)
        self.move_through = (
            analyzer.function_move_through_vars.get(id(func), set())
            if move_through_override is None else move_through_override)
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
        rt = (return_type_override
              if return_type_override is not None
              else (func.return_type
                    if isinstance(func.return_type, TpyType) else None))
        self.ret_storage_opt = _storage_optional_return_wide(rt, analyzer)
        # The pointer-repr Optional return slot over the WIDE pointee class
        # (`A | None` / `T | None` / `W | None` -> a borrow `A*` returned by
        # value): `None` -> `nullptr`, an already-pointer name -> bare, a
        # pointee-typed name -> `&(name)` (_optional_pointer_form_value's
        # admitted subset; every render is pointee-shape-blind).
        self.ret_ptr_opt = _optional_ptr_borrow_wide(rt, analyzer)
        # The value-repr Optional[cheap scalar] return slot (`-> Int32 | None`
        # -> `std::optional<T>`): `return None` -> `std::nullopt`, a value-opt
        # param name passes the whole optional bare, every other scalar source
        # rides the generic return tail (its type-exact / coerce-wrapped
        # rendering matches the AST's `gen_expr_deref`).
        self.ret_value_opt = _value_opt_scalar(rt, analyzer)
        if self.ret_value_opt is None and rt is not None:
            # A value-BOUND `Optional[T]` return in a generic body
            # (`-> T | None` under `T: ValueType` -> `std::optional<T>`)
            # renders like the scalar family: `return None` -> nullopt,
            # a member/field source rides the generic tail's implicit
            # optional conversion. Return-slot only -- bindings/args keep
            # the scalar-keyed classification.
            _rvb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
            if (isinstance(_rvb, OptionalType)
                    and not _rvb.uses_pointer_repr()
                    and _is_type_param_slot(unwrap_readonly(_rvb.inner))):
                self.ret_value_opt = _rvb
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
        # The BORROW-form container return slot (`-> list[T]` -> C++
        # `std::vector<T>&`): a bare container name / plain field read
        # returns bare; every other source shape stays on the AST path.
        self.ret_container_borrow = _container_borrow_return(rt)
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
        # The union-element widening: a VALUE tuple with a value-union
        # element returns the same spelled brace-init (the variant
        # element's converting ctor absorbs the member render).
        self.ret_value_tuple = (_value_tuple_return(rt, analyzer)
                                or _union_elem_value_tuple(rt, analyzer))
        # The GENERIC tuple return slot (>=1 TypeParamRef element): the
        # RESUMABLE return arm's `val_or_ptr_t` bridge AND the sync return
        # arm's literal row (`return (tag, val)` -> the spelled brace-init
        # with per-element `to_val_or_ptr` wraps) both consume it.
        self.ret_generic_tuple = _generic_value_tuple_return(rt, analyzer)
        # The Own[tuple] STORAGE return slot with a non-value member
        # (`-> Own[tuple[str, Resource]]`): a tuple LITERAL of storage-direct
        # members returns the spelled brace-init.
        self.ret_own_storage_tuple = _own_storage_tuple_return(rt, analyzer)
        # The REFERENCE-element tuple return slot (`-> tuple[Tree, Int32]`
        # -> `std::tuple<Tree&, int32_t>`): literal-of-lvalue-names sources
        # only, gated at the return arm.
        self.ret_wrapper_ref_tuple = _wrapper_ref_tuple_return(rt, analyzer)
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
        # A PROPERTY GETTER's ptr-variant union return renders the STORAGE
        # variant by reference (`std::variant<Circle, Square>&` -- the
        # signature layer's is_property_getter arm), NOT the ptr-variant
        # convention -- so this fact is derived first and excludes the
        # flavor from ret_ptr_union below. A bare same-union self-FIELD
        # returns bare (`return this->_shape;`).
        self.ret_union_borrow = (
            _eligible_ptr_union(rt, analyzer)
            if getattr(func, "is_property_getter", False) else None)
        # F4 U2: a pointer-variant return slot -- only same-union borrow
        # names return bare; a MEMBER record name takes the AST's `&(...)`
        # address-of lift, which the slice does not reproduce.
        self.ret_ptr_union = (None if self.ret_union_borrow is not None
                              else _eligible_ptr_union(rt, analyzer))
        # An `Own[A | B]` record-member union return slot (a by-value
        # storage `std::variant<A, B>`): a member-record ctor rvalue
        # returns bare (the converting ctor absorbs it).
        self.ret_own_union = _own_storage_union_return(rt, analyzer)
        # The generic-instance sibling: an `Own[Tree[Int32]]` slot returns
        # the wrapper struct by value; source rows gate at the return arm.
        self.ret_genrec = _own_genrec_return(rt)
        # The non-generic wrapper-union sibling (`-> Own[V]` -> `V` by
        # value): None -> monostate, scalar literals bare, container
        # literals via the ru render, member container names bare.
        self.ret_own_wrapper = _own_wrapper_return(rt, analyzer)
        # The wrapper BORROW return slot (`-> Expr` -> `Expr&`): NAME
        # sources only, gated at the return arm.
        self.ret_wrapper_borrow = _wrapper_borrow_return(rt, analyzer)
        # The @dynamic-protocol BORROW return slot (`-> P` -> `P&`,
        # `-> readonly[P]` -> `const P&`): NAME sources only (a borrow
        # param bare, a pointer-local/global deref) -- the return arm gates.
        self.ret_dyn_borrow = _dyn_borrow_return(rt)
        # The `Own[@dynamic P]` return slot (`std::unique_ptr<P>`):
        # verdict-keyed source rows at the return arm ('forward' names and
        # Own[P]-returning calls bare, conformer ctor rvalues wrapped).
        self.ret_dyn_own = _own_dyn_return(rt)
        # A value-bearing return must select one of the representations the
        # return arm consumes. Signatures remain AST-emitted; this fact is
        # checked only when lowering reaches an actual return value.
        # A non-template Callable return slot (`std::function<...>` by
        # value): the only admitted source is a closure local's bare name
        # (`return add;` -- the lambda converts implicitly).
        self.ret_callable = bool(
            isinstance(rt, CallableType) and not rt.is_template)
        # `Own[T]` on a VALUE scalar is a no-op spelling (resolves to plain
        # T -- `auto_own[Int32]` returns `int32_t`); unwrap for the scalar
        # rows only, the non-value Own families keep their own fields.
        own_v = (unwrap_readonly(rt.wrapped)
                 if isinstance(rt, OwnType) else None)
        own_value_scalar = (own_v is not None
                            and (_eligible_scalar(own_v)
                                 or _eligible_char(own_v)
                                 # `Own[<structural protocol>]` returns the
                                 # same `auto` slot as the bare protocol
                                 # (`auto_own[Iterator[T]]`); @dynamic Own
                                 # keeps the adapter machinery out.
                                 or _protocol_auto_slot(own_v)))
        self.ret_supported = bool(
            rt is None or isinstance(rt, VoidType)
            or self.ret_callable
            or _eligible_scalar(rt) or _eligible_char(rt)
            or own_value_scalar
            or _is_type_param_slot(rt) or _own_type_param_slot(rt)
            or _eligible_enum(rt, analyzer) is not None
            or _eligible_ptr_value(rt, analyzer)
            or _span_return(rt)
            # A native-iterator value return (`def __iter__ -> SpanIter[T]`)
            # and a structural-protocol return (`-> Iterator[T]`, the C++
            # signature already spells the concrete/auto type): the value
            # renders bare through its own call arms.
            or _native_iter_value_slot(rt, analyzer)
            or _protocol_auto_slot(rt)
            or self.ret_storage_opt is not None
            or self.ret_ptr_opt is not None
            or self.ret_value_opt is not None
            or self.ret_value_opt_view is not None
            or self.ret_borrow_tuple is not None
            or self.ret_record_borrow is not None
            or self.ret_union_borrow is not None
            or self.ret_record_storage is not None
            or self.ret_container_storage is not None
            or self.ret_container_borrow is not None
            or self.ret_value_tuple is not None
            or self.ret_generic_tuple is not None
            or self.ret_own_storage_tuple is not None
            or self.ret_wrapper_ref_tuple is not None
            or self.ret_str is not None or self.ret_bytes is not None
            or self.ret_union is not None or self.ret_ptr_union is not None
            or self.ret_own_union is not None
            or self.ret_genrec is not None
            or self.ret_own_wrapper is not None
            or self.ret_wrapper_borrow is not None
            or self.ret_dyn_borrow is not None
            or self.ret_dyn_own is not None)

@dataclass
class _NarrowScope:
    """Lowering's branch/loop-scoped isinstance-narrowing state.

    `narrowed` maps each U3 isinstance-narrowed source var to its live
    extraction alias
    (`ctx.narrowed_vars`); reads rename, the isinstance condition keeps the
    original variant. `persistent_aliases` mirrors
    `ctx.declared_persistent_aliases` for the post-if statement-level
    extraction's collision bump (`__v` -> `__v_2`). `subject_union` records
    each narrowed subject's ORIGINAL union (assert / post-if / branch);
    consumers key it to mirror gen_print/gen_subscript's DECLARED-type
    classification (ctx.var_types) -- NB that classification has ONE
    override, `protocol_narrowings`, so a future arm routing
    protocol-narrowed subjects must not reuse the union-keyed renders.
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
    # Poly post-if/assert-narrowed vars: var -> the ORIGINAL declared type
    # (the narrow retypes `declared` to the member, but every later cast in
    # a re-narrowing chain must anchor to the source decl -- the AST reads
    # `lookup_var_type`, which narrowing never retypes: the pointer-vs-`&`
    # cast-arg spelling, the adapter-vs-dynamic_cast pick, and the readonly
    # const verdict all key on it).
    poly_source: dict[str, TpyType] = field(default_factory=dict)

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
    "const_locals", "pointers", "ptr_variant_locals", "rebind_slot_locals",
    "dyn_protocol_locals", "coro_frame_locals", "opt_storage_call_locals",
    "optional_locals", "branch_hoisted", "match_ptr_hoists",
    "iterator_object_locals",
    "ref_alias_locals", "value_opt_bindings", "storage_opt_locals",
    "const_storage_opt_locals",
    "movable_locals", "storage_tuple_locals", "own_borrow_tuple_locals",
    "optional_borrow_tuple_locals",
    "const_storage_tuple_locals",
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
#   walrus_predeclared -- mirrors the AST's walrus_pre_declared asymmetry:
#       the named pre-decl is function-scoped, so a sibling-branch walrus
#       re-bind must see the first branch's decl and assign in place.
#   walrus_slot_locals -- the owned-slot walrus targets: the decl is
#       function-scoped (named row) and the binding outlives its branch
#       (Python scoping), so reads after the branch keep the `(*n)` render.
#   frame_local_types -- the frame's resolved slot types, seeded once by
#       lower_resumable (the AST frame ctx's var_types); a frame field's
#       slot is one type for the whole body, position-blind.
_FUNCTION_SCOPED_STATE = (
    "unhandled_hoists", "nested_def_locals", "nested_returns",
    "inline_narrowed", "tparam_bounds", "walrus_predeclared",
    "walrus_slot_locals",
    "frame_local_types",
    # Forwarded proto-param aliases (`xs = it` in a resumable): a
    # compile-time name->param map seeded once at entry, never mutated.
    "forwarded_map",
    # Per-@overload-stub literal facts: seeded once at lower_function setup
    # for a literal-only group's stub, immutable across the walk.
    "overload_literal_facts",
    # Module-init facts: seeded once from the module's globals / import list
    # and never branch-scoped -- a global's slot identity and an import's
    # chain do not change inside a branch. `global_slot_assigned` is the one
    # that GROWS during the walk, and deliberately does not restore: a slot
    # emitted inside a branch is still emitted after it (the second write
    # rejects either way).
    "global_ptr_slots", "global_slot_assigned", "import_calls",
    "pre_decl_import_cpp",
    # The binding-audit union ledger (binding_audit.py): monotonic by design
    # -- branch pops UNION into it rather than restoring it, since it records
    # every name the sets ever held.
    "binding_union",
    # The borrow-tuple const fixpoint (ensure_borrow_tuple_const): computed
    # once over the whole body on first demand, immutable after -- the
    # per-function fact discipline, not walk-order state.
    "const_borrow_tuple_locals", "const_opt_borrow_tuple_locals",
    "_btuple_const_computed",
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
                 "pointers", "ptr_variant_locals", "rebind_slot_locals",
                 "dyn_protocol_locals", "coro_frame_locals",
                 "opt_storage_call_locals",
                 "optional_locals", "branch_hoisted", "match_ptr_hoists",
                 "iterator_object_locals",
                 "ref_alias_locals",
                 "value_opt_bindings", "storage_opt_locals",
                 "const_storage_opt_locals",
                 "deref_view_spelled", "forwarded_map",
                 "movable_locals",
                 "sema_movable_locals",
                 "params",
                 "self_receiver", "self_cpp", "self_is_pointer",
                 "record_name", "storage_tuple_locals",
                 "own_borrow_tuple_locals", "optional_borrow_tuple_locals",
                 "const_storage_tuple_locals", "frame_slots",
                 "resumable_leaf_mode", "nested_returns", "in_finally_helper",
                 "plain_frame_fields", "borrow_tuple_frame_locals",
                 "coro_handle_slots", "frame_local_types",
                 "value_tuple_frame_locals",
                 "opt_tuple_holders", "opt_ptr_frame_locals",
                 "oneshot_lift_locals", "alias_ptr_locals",
                 "unpack_ptr_targets",
                 "unhandled_hoists", "narrow", "literal_facts",
                 "inline_narrowed", "forbidden_reads", "forbidden_writes",
                 "nested_def_locals", "error_return_cpp",
                 "walrus_predeclared", "walrus_slot_locals",
                 "overload_narrowing", "overload_stub_return",
                 "overload_literal_facts",
                 "overload_terminated", "render_concept",
                 "top_level_scope", "global_ptr_slots", "global_slot_assigned",
                 "in_for_body",
                 "import_calls", "pre_decl_import_cpp", "top_level_line",
                 "binding_union",
                 "const_borrow_tuple_locals", "const_opt_borrow_tuple_locals",
                 "_btuple_const_computed")

    def __init__(self, func: TpyFunction, analyzer, render_type,
                 self_receiver: str | None = None,
                 record_name: str | None = None,
                 render_type_stored=None,
                 self_cpp: str = "this",
                 self_is_pointer: bool = True,
                 render_resolve=None,
                 params_override=None,
                 return_type_override=None,
                 render_concept=None,
                 scan_override=None,
                 hoisted_override=None,
                 move_through_override=None,
                 top_level_scope: bool = False) -> None:
        self.analyzer = analyzer
        self.func = func
        # Per-lowering union ledger for the cross-path binding-fact check
        # (binding_audit.py); None keeps every capture seam a no-op when the
        # audit is off.
        self.binding_union = _binding_fresh() if _binding_audit_on() else None
        # The SIGNATURE params this body is lowered against: a per-@overload
        # stub's when one is being specialized (the AST binds the stub's
        # types), the impl's otherwise. Every param-type lookup must read
        # this, not `func.params` -- a stub-narrowed param is concrete here
        # while the impl declares it Optional.
        self.params = tuple(func.params if params_override is None
                            else params_override)
        self.prescan = _Prescan(func, analyzer,
                                params_override=params_override,
                                return_type_override=return_type_override,
                                scan_override=scan_override,
                                hoisted_override=hoisted_override,
                                move_through_override=move_through_override)
        # Module-init walk: names live at NAMESPACE scope (pre-declared by the
        # generator, so every write is an assignment), slot temps are `static
        # __global_slot_N`, and non-value globals are writable pointer slots --
        # the global variable model, not a function-local one.
        self.top_level_scope = top_level_scope
        # Inside a for-each BODY with an element type -- the AST's sole
        # namespace-push site, which is what drops `static` on in-branch
        # global-slot decls (if/while/with/try bodies keep global_ns).
        self.in_for_body = False
        # Non-value module globals (`std::vector<T>* g{}` at namespace scope):
        # their initializing write emits the static-slot pair, their reads ride
        # the pointer-local arms. Empty outside the module-init walk.
        self.global_ptr_slots: set[str] = set()
        # Pointer-slot globals whose static slot has already been emitted -- a
        # SECOND rvalue write would have to reuse that slot (the AST's
        # rebind_slots lookup), a render this slice does not reproduce.
        self.global_slot_assigned: set[str] = set()
        # Per-import `__tpy_init()` chain, keyed by statement id and resolved
        # up-front by `lower_top_level` (the AST arm's dedup is order- and
        # state-dependent, so it is replayed once over the whole list rather
        # than re-derived mid-walk).
        self.import_calls: dict[int, tuple[str, ...]] = {}
        # Imported names this module redefines at top level: name ->
        # (decl_line, qualified import spelling). Reads BEFORE decl_line
        # take the import; `top_level_line` is the module-init walk's
        # position (the AST's ctx.current_stmt_line).
        self.pre_decl_import_cpp: dict[str, tuple[int, str]] = {}
        self.top_level_line: int = 0
        # Arm-scoped LiteralType facts (a Literal-subject match narrows the
        # subject per arm). The AST consumes these for dead-branch FOLDS
        # (compare / membership / bool-chain); THIR does not mirror the
        # folds yet, so expression lowering RAISES on a fact-name compare
        # instead of silently diverging -- the fence at the consumer.
        self.literal_facts: dict[str, TpyType] = {}
        self.render_type = render_type or (lambda t: t.to_cpp())
        self.render_type_stored = (render_type_stored
                                   or (lambda t: t.to_cpp_stored()))
        # `TypeResolver.resolve_type` (Pending view/container resolution) --
        # the nested-def lambda header spells params/returns through it
        # (`resolve_type(t).to_cpp_param(name)`), exactly like _gen_nested_def.
        # Identity for analyzer-only callers, whose value-scalar fixtures
        # never carry a Pending type.
        self.render_resolve = render_resolve or (lambda t: t)
        # The codegen concept renderer for `if constexpr` conditions
        # (protocol-isinstance): a (var_name, check_type, declared_type) ->
        # cpp callable built at the driver seam from the SAME helpers the
        # AST arm calls. None for analyzer-only callers -- the constexpr
        # arm rejects without it.
        self.render_concept = render_concept
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
        # Per-@overload-stub lowering: the dead-branch-elim narrowing map
        # (build_overload_narrowing's stub-name -> concrete type) and the
        # stub's return type (overriding func.return_type at the return
        # arms). Both None for an ordinary single-signature body.
        # `overload_terminated` mirrors ctx.overload_terminated: a folded
        # True branch ending in return/raise truncates every enclosing
        # statement list and suppresses the trailing-comment scan.
        self.overload_narrowing: 'dict[str, TpyType] | None' = None
        self.overload_stub_return: 'TpyType | None' = None
        # A literal-only group's per-stub fact map (the AST's
        # literal_overload_facts): enables the if-chain dead-branch fold
        # exactly like overload_narrowing does for the isinstance families.
        self.overload_literal_facts: dict[str, TpyType] = {}
        self.overload_terminated: bool = False
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
        # Mirrors all three `seed_param_locals` pointer arms: the wide
        # ptr-repr Optional class, the nullable-static-protocol param, and
        # the `Own[Opt[T_ref]]` storage param (also an `optional_locals`
        # seed, like the AST).
        self.pointers: set[str] = set()
        # Initialized here (full docstring below) -- the param loop seeds it.
        self.optional_locals: set[str] = set()
        for pname, ptype in self.params:
            # WIDE pointee class: an Optional[wrapper] / `T | None` /
            # Optional[dyn-protocol] param binds the same `T*` shape as the
            # F1 slice, and every read/test render is pointee-blind.
            if _optional_ptr_borrow_wide(ptype, analyzer) is not None:
                self.pointers.add(pname)
            elif _nullable_static_protocol_param(ptype) is not None:
                # seed_param_locals' nullable-static-protocol arm: the param
                # is the monomorphized `const T_x*` (always const-indirect),
                # narrowed reads deref `(*x)`, and the None test is the
                # nullproto constexpr swap.
                self.pointers.add(pname)
            elif is_own_pointer_repr_optional(
                    unwrap_readonly(unwrap_send_sync(ptype))):
                # The AST's `Own[Opt[T_ref]]` param seed: BOTH pointer_locals
                # (arrow reads via optional<P>::operator->) and
                # optional_locals (the None test picks has_value over
                # `!= nullptr`). The binding stays declared Own[Optional[P]]
                # -- consumers key their rows on that type, not on a
                # var_types rebind (`_own_storage_opt_param`); only the
                # F1-record pointee routes reads (_unrouted_binding_read).
                self.pointers.add(pname)
                self.optional_locals.add(pname)
        # Names BOUND as `std::variant<A*, B*>` -- codegen's
        # `ctx.ptr_variant_locals`, which is a BINDING set, not a type
        # verdict: a ptr-variant-typed union reaching a name through a
        # container element / loop variable still binds the value variant.
        # The narrow arms key their `std::get<T*>` render on this.
        # Codegen registers from four producers; the two unmirrored ones cannot
        # reach a routed body -- a union-typed tuple-unpack target is not in the
        # unpack classifier's admitted shapes, and a short @overload stub whose
        # omitted params need a prologue local rejects at admission.
        self.ptr_variant_locals: set[str] = set()
        for pname, ptype in self.params:
            if is_ptr_variant_union(unwrap_readonly(unwrap_send_sync(ptype))):
                self.ptr_variant_locals.add(pname)
        # Walrus targets already pre-declared this FUNCTION -- the AST's
        # walrus_pre_declared asymmetry (function-scoped, never
        # branch-restored, unlike the branch-copied `declared` dict): a
        # sibling-branch re-bind assigns in place, no second decl.
        self.walrus_predeclared: set[str] = set()
        # Owned-slot walrus targets (`std::optional<T> n;` + walrus-deref
        # reads): every read renders `(*n)` with DOT member access -- the
        # AST's register_walrus_deref substitution, NOT the pointer-local
        # arrow model. Function-scoped like the decl itself.
        self.walrus_slot_locals: set[str] = set()
        # F2d rebind-slot subset of `pointers`: their reseats lower as rvalue
        # rebinds (`p = &*(__slot_N = ...)`), not lvalue `&(...)` reseats.
        self.rebind_slot_locals: set[str] = set()
        # First-declared @dynamic protocol locals that are reassigned: their
        # reseat statements take the rebind emit (hoisted optional slot).
        # Async-factory locals holding the CONCRETE coro frame in optional
        # storage (`std::optional<__coro_f> c = f(..);` -- erasure deferred
        # to the Own[dyn] consumer arg). name -> the declared dyn proto.
        self.coro_frame_locals: dict[str, object] = {}
        self.dyn_protocol_locals: set[str] = set()
        # OPT_STORAGE_CALL-declared names (an Own[P|None]-returning call
        # materialized in a `std::optional<P> __slot_N`): a reseat re-fills
        # THAT slot and re-lifts (`__slot_N = make(43); z =
        # optional_to_ptr(__slot_N);` -- the AST's rebind_slots reuse); any
        # other reseat shape for such a name rejects (the generic THIRAssign
        # rebind emit would hijack it into the `&*(__slot = ...)` render).
        self.opt_storage_call_locals: set[str] = set()
        # OPTIONAL_STORAGE bindings (`std::optional<T>`), the AST's
        # ctx.optional_locals: branch-hoisted locals (if-head predecl) and
        # `Own[Opt[T_ref]]` params (`std::optional<P>&&`, seeded above). A
        # subset of `pointers` for the read side (deref reads, `->` access);
        # assigns write PLAIN into the optional (`name = <storage rvalue>;`)
        # and the None test picks has_value over the pointer compare. Its
        # SIBLING set
        # `storage_form_optional_locals` (the storage-optional loop var /
        # unpack target -- storage form like these, but NOT pointer-accessed,
        # so it lifts via `optional_to_ptr` at a `T*` slot) has no mirror at
        # all; the for-each Optional element family rejects before one can be
        # bound. (Initialized above the param-seed loop.)
        # Storage-optional comp/genexpr UNPACK targets -- the mirror of
        # codegen's `storage_form_optional_locals` (a ptr-repr
        # Optional[F1-record] tuple element bound `auto& p = std::get<i>(t)`:
        # storage form, NOT pointer-accessed, so a `T*` slot lifts it via
        # `optional_to_ptr`). Registered by the comp/genexpr heads for the
        # body walk and discarded with it; the for-STATEMENT producer and
        # the const twin (`const_storage_form_optional_locals`) stay
        # unmirrored until witnessed.
        self.storage_opt_locals: set[str] = set()
        # The CONST subset of the above (a `const optional<P>&` loop var off
        # a const-bound source): its consumers spell `const P*`. Mirrors the
        # AST's `const_storage_form_optional_locals`.
        self.const_storage_opt_locals: set[str] = set()
        # Branch-hoisted `T*` pointer-locals WITHOUT an if-head rebind slot
        # (reassigned but not rvalue-reassigned): an rvalue reseat allocates
        # its slot lazily at function top (PtrSlotKind.BRANCH_RVALUE); the
        # AST's ctx.branch_hoisted_vars.
        self.branch_hoisted: set[str] = set()
        # Captures a routed match hoisted as `T*` pointer-locals. A NESTED
        # match may re-seat one (`q = &(__match_subject_2.inner);`); every
        # other pointer-local reuse keeps rejecting, so the admission keys
        # on this set rather than on `pointers`.
        self.match_ptr_hoists: set[str] = set()
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
        # One kind-tagged map (name -> ValueOptKind) covers all three
        # value-repr `std::optional<T>` LOCAL families; the kinds differ
        # only in the narrowed-deref FORM verdict: SCALAR derefs a VALUE,
        # VIEW an OWNED `std::string`/`vector` (STORAGE -- a param's deref
        # is a BORROW view instead, keyed on `param_names`), RECORD a
        # record lvalue consumed as a receiver. The None-test/truthiness
        # read the whole optional (`.has_value()`) for every kind.
        self.value_opt_bindings: dict[str, ValueOptKind] = {}
        # Deref-view narrowed subjects (the if-init cast local): branch-body
        # member calls carrying deref_narrowed_to read the wrapper var via
        # this spelling (`(*__b_ptr)`), never the deref chain. Registered
        # per branch by the deref-view narrow-if arm.
        self.deref_view_spelled: dict[str, str] = {}
        # Forwarded proto-param aliases (`xs = it`): the local's decl emits
        # nothing and every read renders the BACKING param's name (the
        # AST's generator_storage_name substitution). Resumable-only.
        self.forwarded_map: dict[str, str] = {}
        # The `Optional[Own[T_ref]]` PARAM seed (`Own[Point] | None` -- a
        # by-value `std::optional<Point>`; the reverse `Own[Optional[T]]`
        # nesting keeps its pointer_locals fence): the binding IS the
        # RECORD kind -- has_value None-test, `(*p)` narrowed deref -- so
        # it rides the same kind-tagged registration as the
        # owned-optional-call decls.
        for pname, ptype in self.params:
            if _own_opt_storage_binding(ptype):
                self.value_opt_bindings[pname] = ValueOptKind.RECORD
            # The `Optional[String]` PARAM seed (`std::optional<std::string>`
            # by value at the param too): the VIEW kind's owned-deref
            # semantics -- has_value None-test, STORAGE `(*x)` narrowed
            # deref (the name arm's owned-inner form override), the
            # expensive-copy movable move at owned sinks.
            elif _value_opt_string_owned(ptype) is not None:
                self.value_opt_bindings[pname] = ValueOptKind.VIEW
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
        # Borrow-form tuple frame fields (`std::tuple<..., T*>` bare members)
        # and concrete-coro handle slots -- the two frame_slot-adjacent
        # families whose write render differs from both the plain assign and
        # the bare emplace. The branch-nested decl arm keys on them;
        # populated only by `lower_resumable`, empty for every sync body.
        self.borrow_tuple_frame_locals: frozenset = frozenset()
        self.coro_handle_slots: frozenset = frozenset()
        # Resolved frame-local types (the AST frame ctx's `var_types`): the
        # slot's declared C++ shape, which is NOT the first decl's expression
        # type -- a branch-declared container literal can resolve to a sized
        # Array at one decl site while the frame slot is the merged list.
        # Renders that spell the SLOT (the frame_slot brace-init prefix) must
        # read this, not `declared`. Populated only by `lower_resumable`.
        self.frame_local_types: dict = {}
        # Value/storage tuple frame fields (`std::tuple<...>` bare members):
        # the unpack arm ref-binds one as a name source
        # (`const auto& __tup_N = <name>;`). Populated only by
        # `lower_resumable`, empty for every sync body.
        self.value_tuple_frame_locals: frozenset = frozenset()
        self.opt_tuple_holders: frozenset = frozenset()
        self.opt_ptr_frame_locals: frozenset = frozenset()
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
        # The residue ledger must read the SAME facts `prescan.hoisted` does:
        # the module-init walk's carrier is synthetic, so its `id(func)` has no
        # analyzer entry and only the override carries its hoisted set. Left
        # unseeded, this reject backstop silently admits a body it should
        # reject (top-level hoisting is reachable -- `module_init_local` temps
        # are locals of `__tpy_init`, not globals).
        self.unhandled_hoists = set(
            analyzer.function_hoisted_vars.get(id(func), ())
            if hoisted_override is None else hoisted_override)
        # F3 storage-tuple alias locals (`auto&& t = <storage tuple field>`): a read
        # off one is STORAGE form, lifted via `tuple_to_pointer` at borrow boundaries.
        # PARTIAL against codegen's `storage_form_tuple_locals`: the owned-tuple
        # PARAM seeds and the generator/resumable frame-local registrations are
        # not mirrored, so a name from either would read BORROW here off
        # `_is_borrow_form_name`'s type verdict. Both stay unreachable via the
        # call/subscript arms rejecting an owned-tuple source.
        self.storage_tuple_locals: set[str] = set()
        # The owned-MOVABLE tuple PARAM seed is the documented partial above:
        # acknowledge it so the binding-fact join stays honest until an
        # arm-widening change seeds it for real. The `Own[tuple-with-
        # pointer-repr-element]` param seeds FOR REAL: the signature spells
        # the storage tuple by value, so its name reads are STORAGE -- the
        # unpack lift (`tuple_to_pointer<std::tuple<P*, P*>>(t)`), the
        # optional-element decl lift, and the storage-name arg row all key
        # on this membership (mirrors the AST's seed_param_locals arm).
        for pname, ptype in self.params:
            actual = unwrap_readonly(unwrap_send_sync(ptype))
            if (isinstance(actual, TupleType) and actual.is_owned_movable()
                    and not isinstance(ptype, ReadonlyType)):
                _binding_ack(self, "storage_tuple_locals", pname)
            elif isinstance(actual, OwnType):
                inner_t = unwrap_readonly(actual.wrapped)
                if (isinstance(inner_t, TupleType)
                        and inner_t.has_pointer_repr_element()):
                    self.storage_tuple_locals.add(pname)
        # Nullable borrow-form tuple locals (`tuple[.., ref] | None` ->
        # `std::optional<std::tuple<.., T*>>` -- the OPTIONAL_BORROW_TUPLE
        # LocalCppForm): None-tests read `.has_value()`, narrowed reads
        # deref `(*t)` and take the borrow-tuple element arrows, reseats
        # re-wrap through the optional (storage lift / owning-slot
        # emplace / nullopt).
        self.optional_borrow_tuple_locals: set[str] = set()
        # Locals holding the MIXED borrow render of a per-element-Own tuple
        # (`auto p = make_mixed(b)` -- owned elements by value, ref elements
        # as pointers): the NAME leg of `_renders_own_borrow_tuple`, the
        # mirror of codegen's `own_borrow_tuple_locals`. Deliberately NOT in
        # `storage_tuple_locals` here (the AST holds them in both): THIR's
        # element-read arrow (`_subscript_yields_borrow_ptr`) treats a
        # storage name as `.`-access, so the pair must stay split until the
        # read arms key on this set directly.
        self.own_borrow_tuple_locals: set[str] = set()
        # Subset of storage_tuple_locals bound from a const source (a const loop
        # var, or an alias off a const receiver chain): the borrow tuple wrap
        # spells `const T*` element pointers. Mirrors the AST's
        # `const_storage_form_tuple_locals`.
        self.const_storage_tuple_locals: set[str] = set()
        # Borrow-form tuple locals whose element pointers spell `const T*`
        # because SOME binding source reads const storage -- the mirror of
        # codegen's `const_borrow_form_tuple_locals` (+ the nullable sibling),
        # populated by `ensure_borrow_tuple_const`'s whole-body fixpoint on
        # first demand (the AST computes both in setup_body_scope; here most
        # bodies never ask, so the walk is lazy).
        self.const_borrow_tuple_locals: set[str] = set()
        self.const_opt_borrow_tuple_locals: set[str] = set()
        self._btuple_const_computed = False
        # F2e: sema's RAW owned-locals fact, the mirror of codegen's
        # `ctx.sema_movable_locals`. It means "sema proved this local owned",
        # NOT "movable" -- a name becomes movable only by joining the working
        # set below, at a decl arm that promotes. Read ONLY through
        # `promote_movable`; a consumer reading it directly re-introduces the
        # conflation that made a value-typed local (a view-promoted `str`, a
        # BigInt) and a ptr-variant alias move where the AST copies.
        self.sema_movable_locals: frozenset[str] = frozenset(
            analyzer.function_movable_locals.get(id(func), ()))
        # The WORKING set the move sites read -- codegen's `ctx.movable_locals`.
        # Starts at the param seeds below (codegen's seed_param_locals) and
        # grows during the body walk at exactly the arms whose codegen twin
        # calls `StatementGenerator.promote_movable`.
        self.movable_locals: set[str] = set()
        # Own[T] / Own[T]|None params of non-value payload are movable (the
        # caller gave up ownership) -- codegen seeds them at body-scope setup
        # (seed_param_locals), not via sema's per-function set, so lowering
        # mirrors that seeding here. Consumed by the Own-slot call-arg row's
        # move-vs-copy pick and the own-tuple unpack's NAME_MOVE holder bind;
        # the F2b/F2e write/return converts only ever see borrow-pointer
        # sources, which are never Own params. The owned-movable TUPLE-param
        # branch is mirrored below (the unpack's move-vs-copy pick reads it).
        # The resumable RETURN leaf's direct-ready move reads codegen's full
        # working set on its AST twin, so any remaining seeding gap is
        # masked only by the return-shape gate -- widening that gate to
        # force-seeded shapes must extend this seeding in lockstep.
        for pname, ptype in self.params:
            own = unwrap_optional_own(unwrap_readonly(unwrap_send_sync(ptype)))
            if own is not None and not own.wrapped.is_value_type():
                self.movable_locals.add(pname)
            # An owned-movable TUPLE param (`tuple[Own[A], Own[B]]` --
            # `std::tuple<A, B>&&`): seed_param_locals' tuple branch.
            pu = unwrap_readonly(unwrap_send_sync(ptype))
            if (isinstance(pu, TupleType) and pu.is_owned_movable()
                    and not isinstance(ptype, ReadonlyType)):
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

    def promote_movable(self, name: str) -> None:
        """Mirror of `StatementGenerator.promote_movable`: a sema-owned local
        joins the working movable set when its decl reaches a promoting arm.

        Call this from a decl arm exactly when the AST twin calls its version,
        and NOWHERE else -- the arms that stay silent (ptr-variant unions,
        non-Own @dynamic locals, `val_or_ref_t` TypeParamRef locals, REF_ALIAS
        borrows, frame-promoted pointer locals) hand back an alias, so a
        last-use read there copies. Over-promoting is invisible to the
        byte-diff until some later arm starts trusting the verdict, then
        renders a `std::move` the AST does not."""
        if name in self.sema_movable_locals:
            self.movable_locals.add(name)

    def ensure_borrow_tuple_const(self) -> None:
        """Lazy mirror of `_compute_borrow_tuple_const`: OR const over every
        binding source of a reassigned/hoisted ptr-repr-tuple local, to a
        fixpoint over name chains (a bare-name source feeding from another
        borrow-tuple local carries that local's verdict), populating BOTH
        const sets in one pass. The declared const must be at least as const
        as every source (mutable->const lift compiles, const->mutable does
        not). Nullable targets are split by their DECLARED type
        (`tuple[..] | None`), source-blind, exactly like the AST."""
        if self._btuple_const_computed:
            return
        self._btuple_const_computed = True
        analyzer = self.analyzer
        reassigned = self.prescan.reassigned
        hoisted = self.prescan.hoisted
        bindings: dict[str, list] = {}
        opt_bindings: dict[str, list] = {}
        optional_targets: set[str] = set()

        def record(tgt: str, src) -> None:
            if tgt not in reassigned and tgt not in hoisted:
                return
            st = analyzer.get_expr_type(src)
            stb = (unwrap_readonly(unwrap_ref_type(st))
                   if st is not None else None)
            is_pr_tuple = (
                (isinstance(stb, TupleType)
                 and stb.has_pointer_repr_element())
                or (isinstance(stb, OptionalType)
                    and stb.wraps_pointer_repr_tuple()))
            if not is_pr_tuple:
                return
            (opt_bindings if tgt in optional_targets
             else bindings).setdefault(tgt, []).append(src)

        def collect(stmts) -> None:
            for stmt in stmts:
                if isinstance(stmt, TpyVarDecl) and stmt.init is not None:
                    tt = resolve_stmt_binding_type(
                        stmt, analyzer, include_global_binding=False)
                    if tt is None:
                        tt = analyzer.get_expr_type(stmt.init)
                    if tt is not None:
                        tt = unwrap_readonly(
                            unwrap_ref_type(unwrap_send_sync(tt)))
                    if (isinstance(tt, OptionalType)
                            and tt.wraps_pointer_repr_tuple()):
                        optional_targets.add(stmt.name)
                    record(stmt.name, stmt.init)
                elif (isinstance(stmt, TpyAssign)
                      and isinstance(stmt.target, TpyName)):
                    record(stmt.target.name, stmt.value)
                for e in stmt.exprs():
                    for tgt, src in _walrus_pairs(e):
                        record(tgt, src)
                for body in stmt.sub_bodies():
                    collect(body)

        collect(self.func.body)
        if not bindings and not opt_bindings:
            return
        pairs = [(bindings, self.const_borrow_tuple_locals),
                 (opt_bindings, self.const_opt_borrow_tuple_locals)]
        changed = True
        while changed:
            changed = False
            for binds, const_set in pairs:
                for name, srcs in binds.items():
                    if name in const_set:
                        continue
                    if any(_btuple_src_const(s, self) for s in srcs):
                        const_set.add(name)
                        changed = True

    @contextmanager
    def branch_scope(self):
        """One scope pop for every branch-scoped lc name-set (plus `narrow`).

        Restore is by whole-set snapshot, so it is symmetric: in-scope adds
        (branch-local decl registrations) AND removals (the for-each shadow)
        both undo at the pop. Registration that must outlive the scope is the
        caller's job -- perform it in the enclosing frame, after (or outside)
        this context."""
        # `.copy()` covers both shapes here: plain name-sets and the
        # kind-tagged `value_opt_bindings` dict.
        saved = [getattr(self, name).copy() for name in _BRANCH_SCOPED_SETS]
        saved_narrow = self.narrow.snapshot()
        try:
            yield
        finally:
            # Branch-scoped binding adds are still live -- the audit's one
            # chance to see them before the restore discards them.
            _binding_capture(self)
            for name, entries in zip(_BRANCH_SCOPED_SETS, saved):
                setattr(self, name, entries)
            self.narrow = saved_narrow


def _walrus_pairs(expr):
    """(target, value) for every walrus node in `expr` -- the generic
    dataclass-field recursion of the AST's `_walrus_bindings`."""
    if expr is None or not isinstance(expr, TpyExpr):
        return
    if isinstance(expr, TpyNamedExpr):
        yield expr.target, expr.value
    for f in fields(expr):
        val = getattr(expr, f.name)
        if isinstance(val, TpyExpr):
            yield from _walrus_pairs(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, TpyExpr):
                    yield from _walrus_pairs(item)


def _btuple_src_const(src, lc: _LowerCtx) -> bool:
    """`_tuple_source_is_const` mirror over lc verdicts: a ternary is const
    if either arm is; a const-rooted lvalue chain or const-bound name is
    const; an explicitly `readonly[...]`-typed source is const even without
    a const binding."""
    if isinstance(src, TpyCoerce):
        return _btuple_src_const(src.expr, lc)
    if isinstance(src, TpyIfExpr):
        return (_btuple_src_const(src.then_expr, lc)
                or _btuple_src_const(src.else_expr, lc))
    if _btuple_const_storage(src, lc):
        return True
    st = lc.analyzer.get_expr_type(src)
    return isinstance(st, ReadonlyType)


def _btuple_const_root(name: str, lc: _LowerCtx) -> bool:
    # `const_ref_params` | `const_indirect_locals` mirror: the inferred
    # const-borrow param verdict plus the const-classified locals (which
    # carry the readonly-method receiver).
    return (name in lc.const_locals
            or _param_is_const(name, lc.func, lc.analyzer, lc.record_name))


def _btuple_const_storage(expr, lc: _LowerCtx) -> bool:
    # `is_const_storage_source` mirror (including the
    # `is_const_union_source` lvalue-chain half).
    if isinstance(expr, TpyCoerce):
        return _btuple_const_storage(expr.expr, lc)
    if isinstance(expr, (TpyFieldAccess, TpySubscript)):
        obj = expr.obj
        if isinstance(obj, TpyName):
            return _btuple_const_root(obj.name, lc)
        return _btuple_const_storage(obj, lc)
    if isinstance(expr, TpyName):
        return (expr.name in lc.const_storage_tuple_locals
                or expr.name in lc.const_borrow_tuple_locals
                or expr.name in lc.const_opt_borrow_tuple_locals
                or _btuple_const_root(expr.name, lc))
    return False


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
