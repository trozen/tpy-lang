"""The resumable frame builder: a generator / coroutine frame's decisions --
parameter capture, the await-lift and argument-seat pre-pass, the loop /
with / pointer-slot / try-finally prescans, the frame layout plan and the
coarse CFG -- made once per body, before any of its leaves is lowered, and
published as `THIRResumableFrame`.

The builder runs where the frame is first needed (the forward-declaration
gate for generators, the struct-order sort for coroutines, the source
genexpr pass); its diagnostics keep that timing. It reads the analyzer,
spells C++ only through the type renderer it is handed (the `render_type`
seam the leaf lowering also uses), and answers name questions from its own
environment (`FrameBuildEnv`), never from a body emitter's scope.
"""
from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace as dc_replace
from functools import partial
from typing import TYPE_CHECKING, NoReturn

from ...identity_map import IdentityMap
from ...prescan import loop_bindings_of
from ...parse.nodes import (
    RebindStorage, TpyFunction, TpyAwait, TpyStmt, TpyAssign, TpyVarDecl,
    TpyExprStmt, TpyName, TpyExpr, TpyTry, TpyExceptHandler, TpyCall,
    TpyMethodCall, TpyFieldAccess, TpyForEach, TpyWhile, TpyWith, TpyWithItem,
    TpyTupleUnpack, TpySubscript,
    TpyNoneLiteral, TpyCoerce,
    TpyMatch, is_stable_address_lvalue, iter_capture_bindings,
    walrus_bindings, TpyGeneratorExpression,
)
from ...typesys import (
    IntLiteralType, NominalType, TpyType, OptionalType, OwnType,
    ReadonlyType, TupleType, TypeParamRef, VoidType, unwrap_readonly, unwrap_ref_type, unwrap_own, unwrap_send_sync,
    varargs_is_readonly, is_readonly_ptr, is_fn_type, is_dyn_protocol,
    param_takes_ownership, body_function_info, body_method_info,
    pointer_repr_optional, is_protocol_type, ConcreteFrameType,
)
from ...value_category import (
    const_indirect_local, const_storage_source, unpack_source_const_slots,
    is_rvalue_source, is_temporary_expr, call_value_optional,
    declared_call_const, for_source_is_rvalue, frame_factory_callee,
    iterator_source_callee, materializing_temp_source, peel_coerce,
    call_returns_cpp_ref, property_access_returns_cpp_ref,
    is_value_emit_rvalue,
)
from ... import modules as builtin_modules
from ...modules import get_error_return_next_element_type
from ...compilation_context import get_current_compiler
from ...symbol_binding import SymbolKind, lookup_imported
from ...sema.storage_policy import first_binding_storage
from ...type_def_registry import (
    is_str_type, is_str_category, is_big_int_type, is_bytes_type,
    is_bytes_category, is_owned_in_coro_frame, is_str_view_type,
    is_bytes_view_type, is_free_copy_scalar, iter_yields_ref_tuple_proxies,
)
from ...typesys import (polymorphic_subclass_into_optional,
                        returns_cpp_reference_shape)
from ...codegen_cpp.context import (CodeGenError, FrameSourceUnnameable,
                                    ternary_iterable_lends_storage)
from ...codegen_cpp.emit_prims import placeholder_init
from ...codegen_cpp.forms import (is_plain_nonvalue, is_ptr_variant_union,
                                  reads_storage_form_optional)
from ...codegen_cpp.frame_names import (recursive_delegation_error,
                                        sub_struct_qualname)
from ..frame import (
    AwaitMode, FrameCaptureKind, FrameLoopOwner,
    FrameLocalKind, FrameLocalLayout, GeneratorForInfo, HolderRole,
    POINTER_LOCAL_KINDS, THIRFrameHolder, WITH_POINTER_KINDS,
    THIRAwaitSuspension, THIRFrameLocal,
    THIRLeafSlot, LeafRole, THIRResumableFrame, await_operand_slots,
    region_cleanups,
)
from ...resumable_shape import ResumableShape
from ..nodes import THIRBorrowedRecord, THIRParam
from .storage import record_receiver
from .functions import method_self_type_by_name
from ..validate import validate_resumable_frame
from .predicates import frame_temp_arg_slot, frame_temp_elem_slots
from .context import binding_source_sites
from .checks import gen_recv_ctor_temp
from .bindings import (body_rebound_names, ptr_tuple_decision,
                       ptr_tuple_elem_storage)
from .frame_cfg import (CFGBuilder, _CFGNotYetSupported, _top_level_await_in,
                        case_entry_order, collect_frame_nested_defs,
                        no_throw_blocks,
                        _stmts_have_any_suspension, _stmts_have_any_return)

if TYPE_CHECKING:
    from ...typesys import FunctionInfo


@dataclass
class FrameBuildEnv:
    """The name environment a frame's decisions are made in.

    The body emitter answers name questions from its scope (`var_types`, the
    const name sets); the frame is decided before any body scope exists, so
    it carries its own: the parameter types, the parameter const verdicts
    (`const_ref_params` / `deep_const_borrow_params`, the frame-capture
    reading of `FunctionInfo.const_borrow_params`) and the frame's const
    bindings, which the alias and loop prescans grow as they classify (a
    nested loop over a const loop var reads what the outer one decided).
    The rules themselves are the shared ones in `value_category`; only the
    names they are asked about come from here."""
    analyzer: object
    var_types: Mapping[str, TpyType]
    const_ref_params: set[str]
    deep_const_borrow_params: set[str]
    # Shared by reference with the frame's own record, so a binding marked
    # const by one prescan is const for every later question.
    const_bindings: set[str]
    resolved_type_in: Callable[..., TpyType]
    _no_narrowings: Mapping[str, TpyType] = field(default_factory=dict)

    def binds_const(self, name: str) -> bool:
        return name in self.const_ref_params or name in self.const_bindings

    def is_const_storage_source(self, expr: TpyExpr) -> bool:
        return const_storage_source(self.analyzer, expr,
                                    root_const=self.binds_const,
                                    name_const=self.binds_const)

    def is_const_indirect(self, target_type: TpyType | None,
                          init: TpyExpr | None, stmt: object = None) -> bool:
        # The alias arm asks the parameter verdicts only, not the frame's
        # const bindings: the body emitter's twin reads its param sets plus
        # its const locals, of which a frame being decided has none.
        return const_indirect_local(
            self.analyzer, target_type, init, stmt,
            storage_const=self.is_const_storage_source,
            name_const=lambda n: (n in self.const_ref_params
                                  or n in self.deep_const_borrow_params))

    def unpack_source_has_const_slots(self, stmt: TpyTupleUnpack) -> bool:
        return unpack_source_const_slots(
            stmt.value, lambda n: n in self.deep_const_borrow_params)

    def get_resolved_type(self, expr: TpyExpr,
                          target_type: TpyType | None = None) -> TpyType:
        return self.resolved_type_in(expr, self.var_types,
                                     self._no_narrowings, target_type)


@dataclass
class FrameLayoutPlan:
    """Frame-local placement for one resumable body: `bindings` maps every
    `func.generator_locals` name to its verdict; `pointer_tuples` names the
    ones sema made pointer tuples (`_FrameFormInputs.pointer_tuples`).
    The builder's own record: the frame publishes each verdict on its local
    (`THIRFrameLocal.placement`)."""
    bindings: 'dict[str, FrameLocalLayout]'
    pointer_tuples: 'frozenset[str]' = frozenset()


@dataclass(frozen=True)
class FrameBuildDeps:
    """What the builder needs from the module being emitted, as the
    analyzer-only queries it asks: the type renderers (the `render_type`
    seam), the pending-type resolution and its expression form, the
    static-protocol classifier, the same-module generator table, the
    import-cycle peers, and the Poll / next payload spelling of a body's
    result."""
    analyzer: object
    type_to_cpp: 'Callable[[TpyType], str]'
    tuple_borrow_cpp: 'Callable[..., str]'
    resolve_type: 'Callable[[TpyType], TpyType]'
    resolved_type_in: 'Callable[..., TpyType]'
    is_static_protocol_param: 'Callable[[TpyType], bool]'
    same_module_generators: 'Mapping[tuple[str, str | None], TpyFunction]'
    cycle_peers: 'frozenset[str] | set[str]'
    ret_cpp: 'Callable[[TpyFunction], str]'


@dataclass
class FrameBuildState:
    """The builder's working record while a frame is decided: what each
    pass produces for the later ones. `publish` turns it into the frame."""
    frame_dep_units: 'list[tuple[str, str | None]]' = field(default_factory=list)
    lifted_body: 'list[TpyStmt] | None' = None
    next_lift_id: int = 0
    # Names of the await lifts bound exactly once from an awaited result.
    one_shot_lift_names: 'set[str]' = field(default_factory=set)
    next_arg_lift_id: int = 0
    # Argument lifts: holder name -> (the expression it holds, the local
    # the call binds, if any).
    arg_lifts: 'dict[str, tuple[TpyExpr, str | None]]' = field(
        default_factory=dict)
    for_prescanned: bool = False
    for_uid_map: IdentityMap = field(default_factory=IdentityMap)
    for_fields: 'tuple[THIRFrameHolder, ...]' = ()
    for_loop_info: IdentityMap = field(default_factory=IdentityMap)
    for_info_by_uid: 'dict[int, GeneratorForInfo]' = field(default_factory=dict)
    # Non-decomposed loop -> the LOOP holder of its source, or None when the
    # source stays in its block.
    for_src_fields: IdentityMap = field(default_factory=IdentityMap)
    async_for_struct_names: 'dict[int, str]' = field(default_factory=dict)
    ptr_slots_prescanned: bool = False
    ptr_slot_fields: 'tuple[THIRFrameHolder, ...]' = ()
    ptr_slot_map: IdentityMap = field(default_factory=IdentityMap)
    with_prescanned: bool = False
    with_uid_map: IdentityMap = field(default_factory=IdentityMap)
    with_fields: 'tuple[THIRFrameHolder, ...]' = ()
    with_borrowed_fields: 'set[str]' = field(default_factory=set)
    with_owning_str_targets: 'set[str]' = field(default_factory=set)
    with_target_payloads: 'dict[str, str]' = field(default_factory=dict)
    with_generic_targets: 'set[str]' = field(default_factory=set)
    with_owned_ctx_map: IdentityMap = field(default_factory=IdentityMap)
    with_ctx_counter: int = 0
    with_manager_homes_prescanned: bool = False
    async_with_struct_names: 'dict[int, tuple[str, str]]' = field(default_factory=dict)
    pointer_alias_prescanned: bool = False
    pointer_alias_locals: 'set[str]' = field(default_factory=set)
    const_frame_bindings: 'set[str]' = field(default_factory=set)
    # root -> the bindings borrowing out of it (const whenever it is).
    const_source_edges: 'dict[str, list[str]]' = field(default_factory=dict)
    try_finally_prescanned: bool = False
    try_finally_uid_map: IdentityMap = field(default_factory=IdentityMap)
    try_finally_fields: 'tuple[THIRFrameHolder, ...]' = ()
    frame_layout: 'FrameLayoutPlan | None' = None
    loop_close_fields: IdentityMap = field(default_factory=IdentityMap)
    rebound_ref_names: 'frozenset[str] | None' = None


def mark_frame_const(state: FrameBuildState, *names: 'str | None') -> None:
    """Record `names` as const bindings of this frame and close the borrow
    edges over the result."""
    fresh = [n for n in names if n and n not in state.const_frame_bindings]
    if not fresh:
        return
    state.const_frame_bindings.update(fresh)
    _close_frame_const(state, fresh)


def record_const_source_edge(state: FrameBuildState, target: str,
                             root: 'str | None') -> None:
    """Record that `target` borrows out of `root`, so `target` is const
    whenever `root` is -- whichever of the two is classified first."""
    if not root or target == root:
        return
    state.const_source_edges.setdefault(root, []).append(target)
    if root in state.const_frame_bindings:
        mark_frame_const(state, target)


def _close_frame_const(state: FrameBuildState,
                       roots: 'list[str]') -> None:
    """Close `const_frame_bindings` over the borrow edges out of `roots`.

    A worklist over the newly-const names, not a re-scan of every edge on
    every mark: the verdict is consumed DURING the prescans (a nested
    for-loop's slot is spelled off an alias whose root the enclosing loop
    was just proved const), so the closure cannot be deferred to the end --
    but only the edges out of a name that just turned const can fire, so
    the whole build costs one pass over the edges."""
    work = list(roots)
    while work:
        for target in state.const_source_edges.get(work.pop(), ()):
            if target not in state.const_frame_bindings:
                state.const_frame_bindings.add(target)
                work.append(target)


def same_module_dep_unit(owner: 'NominalType | None', method: str,
                         current_module: 'str | None',
                         ) -> 'tuple[str, str | None] | None':
    """The embedding-edge entry for embedding `owner.method`'s frame, or
    None when it is not an ordering edge.

    Only a callee defined in the module being emitted is one. A cross-module
    struct is already complete via its included header, and the unit index is
    keyed on the BARE `(method, record)` pair -- so recording a cross-module
    callee matches a same-named LOCAL unit instead and fabricates a cycle out
    of valid code. Membership in a same-module map cannot stand in for this
    check for the same reason: those maps are bare-name keyed too.

    An owner whose module qname is unstamped is not matched. Every
    user-declared record carries one, so an unknown owner is not a local unit,
    and guessing would risk the false edge this exists to prevent.
    """
    if owner is None or current_module is None:
        return None
    qname = owner.qualified_name()
    if not qname or "." not in qname:
        return None
    if qname.rsplit(".", 1)[0] != current_module:
        return None
    return (method, owner.name)


def returns_void(func: TpyFunction) -> bool:
    """True iff func's declared return is `None` -- i.e. the top-level
    `-> None` annotation that lowers to C++ `void`. Distinguished from
    `Task[None]` / `Poll[None]` consumers, where the inner None is at
    a type-arg position and routes to `std::monostate`."""
    return isinstance(unwrap_ref_type(func.return_type), VoidType)




def _bound_local(stmt: TpyStmt) -> str | None:
    """The local a statement binds, when it binds a plain name."""
    return stmt.name if isinstance(stmt, TpyVarDecl) else None


@dataclass
class _FrameFormInputs:
    """The name sets one frame local's field FORM is read off.

    `_frame_form_inputs` fills the half that is settled before the for and
    with prescans run; `_frame_layout` adds what those decide. Split so both
    the layout and the for prescan read one classifier
    (`_frame_local_form`)."""
    owning_tuple_locals: 'dict[str, TpyType | None]'
    # The locals the storage policy holds as a pointer over storage of
    # their own (`first_binding_storage`): the REBIND_PTR form.
    rebound_ref_names: 'frozenset[str]'
    pointer_form_names: 'set[str]'
    # The pointer tuples (`CodeGenContext.frame_pointer_tuples`).
    pointer_tuples: 'dict[str, TpyType | None]' = field(default_factory=dict)
    borrow_tuple_names: 'set[str]' = field(default_factory=set)
    owning_str: 'set[str]' = field(default_factory=set)
    source_form_fields: 'dict[str, str]' = field(default_factory=dict)
    pointer_payloads: 'dict[str, str]' = field(default_factory=dict)
    # The `with ... as` targets among `source_form_fields`, and those of them
    # whose enter type is a type parameter.
    with_targets: 'set[str]' = field(default_factory=set)
    with_generic_targets: 'set[str]' = field(default_factory=set)


def _cannot_hold_a_borrow(t: 'TpyType') -> bool:
    """Whether a value of `t` is PROVABLY self-contained: it can carry no
    reference to storage outside itself.

    Only the scalar family and the owned string buffers qualify. Everything
    else -- a view, a `Ptr`, a record (whose fields may be pointers), a
    container, a tuple, a union, anything unknown -- is treated as able to
    hold a borrow, because the answer here decides how long the storage a
    binding may point into is kept alive."""
    bare = unwrap_readonly(unwrap_own(unwrap_ref_type(t)))
    return (is_free_copy_scalar(bare) or is_big_int_type(bare)
            or is_str_type(bare) or is_bytes_type(bare))


def _loop_bound_names(analyzer, loop: 'TpyForEach') -> 'frozenset[str]':
    """Every name `loop` can BIND -- its own target, and what a pass binds.
    An over-approximation on purpose: the caller turns "this name could hold
    an element" into a lifetime decision, so a missed binding is a dangling
    read while a spurious one only costs a frame field."""
    facts = loop_bindings_of(analyzer.loop_bindings, loop,
                             analyzer.ctx.write_summaries)
    return facts.body | facts.target


def _same_elements(a: list, b: list) -> bool:
    """True iff `a` and `b` have identical length and element-by-element
    identity. Used by the async lift pre-pass to decide whether a
    sub-body actually changed -- distinct list objects with all-`is`
    elements are treated as unchanged so the parent stmt's id stays
    stable."""
    return len(a) == len(b) and all(x is y for x, y in zip(a, b))


@dataclass(frozen=True)
class _DelegatedGenerator:
    """A resolved `for x in gen(...)` whose callee frame the enclosing
    resumable frame embeds by value in a `__for_src` field."""
    func: TpyFunction
    # Receiver type for a method callee; None for a free function. Carries
    # both the callee's namespace and its owner type args, so a generic
    # owner needs nothing extra.
    owner: 'NominalType | None'
    # Namespace qualifier for a cross-module FREE function. None for a
    # method (the owner supplies it) and for a same-module callee.
    module_qual: str | None
    # Where the callee is defined -- the axis the residual rejects key on,
    # not a spelling input.
    defining_module: str
    call: TpyExpr


def elem_wants_borrow_form(elem_type: 'TpyType | None') -> bool:
    """Whether a frame-resident loop var over elements of this type must be a
    borrow-form `T*` aliasing the source rather than an owning `frame_slot<T>`.
    The owning copy hides loop-var mutations from the source elements -- a
    silent divergence from CPython's aliasing.

    For the `begin_end` and `next` strategies only, and deliberately so: their
    sources always lend a genuine lvalue -- a container element, a producer's
    live yield slot -- so the answer is decidable from the element type alone
    whatever it turns out to be, GENERICS INCLUDED (`*it` on a `vector<T>` is an
    lvalue for every `T`). `iter_next` cannot use this and does not: an
    arbitrary `__next__` may hand back a fresh value that looks identical to a
    lent one here, so its field defers to `for_elem_next_t` and lets C++ decide.

    Value elements copy by their own semantics. A `readonly[T]` element rides
    too: it is the DEFAULT element of a non-mutated `*args` pack (sema's
    `varargs_as_const` flip), so excluding it would make the silent copy the
    normal case rather than a corner. Its pointer is spelled `const T*`: the
    two `GeneratorForInfo` construction sites below decide that with
    `pointer_form_is_const` -- from the iteration source's const verdict or a
    `readonly` element -- and the frame layout consumes it through the same
    const-alias set the statement-level const aliases use.
    """
    elem = unwrap_ref_type(elem_type) if elem_type is not None else None
    return (elem is not None
            and not unwrap_readonly(elem).is_value_type())


def whole_tuple_loop_var_aliases(elem_type: 'TpyType | None') -> bool:
    """Whether a NON-unpack tuple loop var is a POINTER to the source element
    tuple (`std::tuple<..., T>*`) rather than a field of its own.

    A tuple with a reference element has two C++ shapes, and a frame field in
    either one disagrees with the advance: the storage tuple `*it` yields
    cannot assign into a borrow-form field, and a borrow-form field rebuilt
    per advance would still have to name the storage the pointers point at.
    Pointing at the source element -- exactly what the tuple-UNPACK sibling's
    `__for_tup` holder does -- has one shape, needs no runtime helper, and
    aliases the source like CPython's `auto&&` sync twin.

    An ALL-VALUE element tuple takes the same field rather than the value copy
    it could get away with: one form answers the question for both element
    shapes, so there is no second classification to keep in step.

    For `begin_end` sources only -- the ones that lend a genuine lvalue.
    An `__iter__`/`__next__` source hands the step result back BY VALUE for a
    tuple element (`val_or_ref_t` treats a tuple as a value type), so there
    is no source element to point at; its own defect is that the sync emitter
    reads such an element in borrow form regardless (see BUGS.md).
    """
    elem = (unwrap_readonly(unwrap_ref_type(elem_type))
            if elem_type is not None else None)
    return isinstance(elem, TupleType)


def elem_is_known_value(elem_type: 'TpyType | None') -> bool:
    """Whether the element is a CONCRETE value type, so a plain frame field is
    right and the storage-form trait is not needed.

    For a scalar this is decidable and cannot be silently wrong, unlike
    borrow-vs-own for a reference element: copying is the whole semantics, so
    there is no aliasing outcome to lose. A generic or protocol-typed element is
    NOT known -- it instantiates either way -- and `readonly[T]` defers too, so
    the trait can settle its constness.

    A TUPLE element also lands here, but for a weaker reason: `is_value_type()`
    is unconditionally True for TupleType even when the tuple holds borrows, so
    "copying is correct" does NOT follow. Returning True is still right, because
    a tuple loop var has its own established classification (borrow-form tuple
    fields, tuple-unpack alias targets) and must reach it rather than be
    rerouted through this trait.
    """
    elem = unwrap_ref_type(elem_type) if elem_type is not None else None
    if elem is None or isinstance(elem, (TypeParamRef, ReadonlyType)):
        return False
    if is_protocol_type(elem):
        return False
    return elem.is_value_type()


def loop_field_drops(fname: str, owner: FrameLoopOwner,
                     resolve: 'Callable[[TpyExpr], TpyType | None]') -> bool:
    """Whether destroying this loop field may run user code: the source
    holds the iterable, the step result an element, and a derived
    iterator is whatever `__iter__` returned -- not known here. `resolve`
    is the builder environment's source-type resolution."""
    stmt, info = owner.stmt, owner.info
    if info is None or fname == f"__for_src_{info.uid}":
        src_t = resolve(stmt.iterable)
        return (src_t is None
                or unwrap_ref_type(src_t).drop_runs_user_code())
    if info.strategy in ("range", "begin_end"):
        return False
    if fname == f"__for_r_{info.uid}":
        elem = stmt.elem_type
        if isinstance(elem, IntLiteralType):
            return False
        return elem is None or unwrap_ref_type(elem).drop_runs_user_code()
    return True


def frame_deep_const_verdict(
        analyzer, func: TpyFunction,
        record_name: 'str | None') -> 'frozenset[int] | None':
    """The per-param deep-const verdict off the RAW fi of `func`'s body."""
    fi = frame_function_info(analyzer, func, record_name)
    return fi.const_borrow_params if fi is not None else None


def frame_function_info(analyzer, func: TpyFunction,
                        record_name: 'str | None') -> 'FunctionInfo | None':
    """The registry FunctionInfo of the generator / coroutine `func`, or
    None for a nested def (it lives in its enclosing body's namespace)."""
    if record_name:
        return body_method_info(analyzer.registry.get_record(record_name), func)
    return body_function_info(analyzer.registry, func)


def frame_capture_const_names(
        analyzer, is_static_protocol_param: 'Callable[[TpyType], bool]',
        func: TpyFunction, record_name: 'str | None') -> 'tuple[set[str], set[str], set[str]]':
    """The frame's per-capture const verdict by NAME: `(const_ref, deep)`.

    The ONE reading of the const-borrow verdict for a resumable frame: the
    captured field spelling, the const sets the prescan types alias locals
    against, and the frame's const bindings all take it from here, so one
    capture cannot come out const in one of them and mutable in another.
    `readonly[T]` is the explicit half of the same verdict. A capture that
    holds no borrow (`Own[T]`, an owned view copy, a deduced template arg)
    is excluded whatever the verdict says; the pointer-repr forms carry
    their const inside the spelling, so they join `deep` only.

    The third set is the `*args` packs bound to const storage. The builder
    records all three as the frame's const bindings (the loop and alias
    prescans add theirs as they classify).
    """
    const_ref: set[str] = set()
    deep: set[str] = set()
    # A `*args` pack binds const storage without the CAPTURE being a const
    # borrow: sema flips an unmutated pack to `varargs[readonly[T]]` and
    # the pack's own spelling carries it, so the field stays as it was and
    # only the lifts rooted at it turn const.
    pack: set[str] = set()
    if func.is_readonly and (record_name is not None or func.is_method):
        const_ref.add("self")
    verdict = frame_deep_const_verdict(analyzer, func, record_name)
    for pidx, (pname, ptype) in enumerate(func.params):
        if varargs_is_readonly(unwrap_ref_type(ptype)):
            pack.add(pname)
            continue
        outer = unwrap_send_sync(unwrap_ref_type(ptype))
        if is_readonly_ptr(outer):
            # `Ptr[readonly[T]]` captures as `const T*` whatever the param
            # verdict says -- its const is on the POINTEE, so like the
            # other pointer-repr forms it joins `deep` only.
            deep.add(pname)
            continue
        explicit = isinstance(unwrap_ref_type(ptype), ReadonlyType)
        if not explicit and (verdict is None or pidx not in verdict):
            continue
        kind = classify_param_kind(ptype, is_static_protocol_param)
        if explicit or kind is FrameCaptureKind.REF:
            const_ref.add(pname)
            deep.add(pname)
            continue
        bare = unwrap_readonly(unwrap_ref_type(ptype))
        if ((isinstance(bare, TupleType) and bare.has_pointer_repr_element())
                or is_ptr_variant_union(bare)):
            deep.add(pname)
    return const_ref, deep, pack


def classify_param_kind(
        ptype: 'TpyType',
        is_static_protocol_param: 'Callable[[TpyType], bool]',
) -> FrameCaptureKind:
    """Decide the coro-frame storage kind for an async-def param.

    Single source of truth for the kind cascade: `_classify_params`
    derives the C++ spelling from it, and `param_borrows` reads it to
    decide whether an rvalue-temporary arg bound to this param must be
    hoisted into the awaiter's frame (see `_lift_borrowed_rvalue_args`).
    """
    ptype_inner = unwrap_ref_type(ptype)
    actual = unwrap_readonly(ptype_inner)
    # str / bytes are captured owned in the frame (copied at construction)
    # so the view doesn't dangle across a suspension when the arg is a
    # temporary -- see is_owned_in_coro_frame (the sema borrow set excludes
    # the same set). Explicit view params (StrView/Span) stay borrow-form.
    if is_owned_in_coro_frame(ptype_inner):
        return FrameCaptureKind.OWNED_COPY
    # `readonly[T]` is the same slot seen through the const wrapper, so it
    # takes the same per-instantiation traits: a literal `const T&` field
    # would hold a reference at a VALUE instantiation too, where the frame
    # outlives the temporary the factory call materialized.
    if isinstance(actual, TypeParamRef):
        return FrameCaptureKind.TYPE_PARAM
    # Checked before the OptionalType-pointer-repr branch so
    # `Own[Awaitable[T]] | None` isn't mis-routed to POINTER --
    # `_protocol_template_parts` rejects the nullable shape instead.
    if is_static_protocol_param(ptype):
        return FrameCaptureKind.STATIC_PROTOCOL
    # Fn callable: concrete type is deduced as an `F_<pname>` template arg
    # (like a static protocol). Checked before is_value_type() -- a
    # CallableType would otherwise route through type_to_cpp(), which a
    # template-mode Fn cannot answer.
    if is_fn_type(actual):
        return FrameCaptureKind.FN
    # A generic `T | None` slot lands here too, and the arm's spelling is
    # what it wants: `to_cpp_param_type()` renders the runtime's
    # per-instantiation form, which owns at a value T and points at a
    # reference one. Only the KIND's name is a pointer -- the borrow
    # question is answered per instantiation, on the substituted slot the
    # call site passes to `param_borrows`.
    if pointer_repr_optional(actual) is not None:
        return FrameCaptureKind.POINTER
    if is_ptr_variant_union(actual):
        return FrameCaptureKind.POINTER
    # A borrow-form tuple param (std::tuple<..., T*>) is held in the frame
    # by value-of-pointers so it aliases the caller across suspensions; a
    # VALUE-kind field would store the storage form (std::tuple<..., T>)
    # and silently copy the element where CPython shares it.
    if isinstance(actual, TupleType) and actual.has_pointer_repr_element():
        return (FrameCaptureKind.OWNED_TUPLE if param_takes_ownership(ptype)
                else FrameCaptureKind.POINTER)
    # Own[T] checked before is_value_type(): `OwnType.is_value_type()` is
    # True (Own[T] uses T&& at param boundaries) but the move-only
    # semantics need explicit forwarding, so it can't ride the VALUE path.
    if isinstance(ptype_inner, OwnType):
        return FrameCaptureKind.OWNED_VALUE
    if ptype_inner.is_value_type():
        return FrameCaptureKind.VALUE
    return FrameCaptureKind.REF


def capture_kind(func: TpyFunction, pname: str, ptype: 'TpyType',
                 is_static_protocol_param: 'Callable[[TpyType], bool]',
                 ) -> FrameCaptureKind:
    """How the frame of `func` holds parameter `pname`: a lexical capture
    of a genexpr's function is the deduced-type forwarding slot whatever
    its type; any other parameter takes `classify_param_kind`."""
    if func.is_capture(pname):
        return FrameCaptureKind.CAPTURE
    return classify_param_kind(ptype, is_static_protocol_param)


def param_borrows(ptype: 'TpyType',
                  is_static_protocol_param: 'Callable[[TpyType], bool]',
                  ) -> bool:
    """True iff a param of this type is captured in the coro frame as a
    borrow (a reference/pointer into the arg) rather than by value -- the
    REF / POINTER / OWNED_TUPLE kinds. An rvalue-temporary arg bound to
    such a param dangles once the suspending `case` block exits unless it
    is hoisted into a frame field that outlives the sub-coro.

    TYPE_PARAM (generic-async) is intentionally not treated as borrowing
    here: its value-vs-reference form resolves only at instantiation, so
    the borrowing rvalue-arg dangle for an object-typed `T` is left to the
    generic-async dangle tracked in BUGS.md (#292 M7).

    A generic `T | None` slot needs no such carve-out: its only caller
    (`_hoist_borrowed_args`) asks about the resolved callee's SUBSTITUTED
    slot, which is a value Optional at a value T (no hoist) and a
    pointer-repr one at a reference T (hoisted), so the answer is already
    per instantiation."""
    return classify_param_kind(ptype, is_static_protocol_param) in (
        FrameCaptureKind.REF, FrameCaptureKind.POINTER,
        FrameCaptureKind.OWNED_TUPLE)


def bound_frame_targets(
        func: TpyFunction) -> list[tuple[str, str | None]]:
    """`(function_name, owner_record_name | None)` for every bound
    generator or coroutine object promoted into `func`'s resumable
    frame -- each is embedded by value, the same infinite-size
    constraint as an inline await's sub-future."""
    targets: list[tuple[str, str | None]] = []
    for _lname, ltype in (func.generator_locals or []):
        inner = unwrap_readonly(unwrap_own(unwrap_ref_type(ltype)))
        if isinstance(inner, ConcreteFrameType):
            owner = inner.frame_owner
            targets.append((inner.frame_func_name,
                            owner.name if owner is not None else None))
    return targets


def build_resumable_frame(func: TpyFunction, record_name: 'str | None',
                          shape: ResumableShape,
                          deps: FrameBuildDeps) -> THIRResumableFrame:
    """Decide `func`'s frame and publish it. A shape the resumable lowering
    cannot handle is a located `CodeGenError` raised here, at the first
    build -- the authoritative backstop, so no unsupported shape silently
    miscompiles."""
    frame = FrameBuilder(func, record_name, shape, deps).build()
    validate_resumable_frame(frame)
    return frame


class FrameBuilder:
    """One frame's decisions, made in order: the await-lift and argument
    seat pre-pass, the capture const verdicts that seed the environment,
    the borrow-alias classification, the loop / with / manager-home /
    pointer-slot / try-finally prescans (the layout plan is built from the
    for / with outputs), the loop release points, and the CFG."""

    # Frame-field kinds whose storage is entirely its own: nothing in the field
    # points at the manager, so an owned manager may stay a case-block local.
    _SELF_CONTAINED_FRAME_KINDS = frozenset({
        FrameLocalKind.OWNED_STR,
        FrameLocalKind.OWNING_TUPLE_SLOT,
        FrameLocalKind.FRAME_SLOT,
    })

    def __init__(self, func: TpyFunction, record_name: 'str | None',
                 shape: ResumableShape, deps: FrameBuildDeps) -> None:
        self.func = func
        self.record_name = record_name
        self.shape = shape
        self.deps = deps
        self.analyzer = deps.analyzer
        self.state = FrameBuildState()
        self.env: 'FrameBuildEnv | None' = None
        self.frame_locals: 'tuple[THIRFrameLocal, ...]' = ()

    def get_expr_type(self, expr: TpyExpr) -> 'TpyType | None':
        """The sema type of `expr`, readonly stripped (C++ carries const in
        the signatures)."""
        typ = self.analyzer.get_expr_type(expr)
        return unwrap_readonly(typ) if typ is not None else None

    def _sub_struct_qualname(self, owner, method, inferred_type_args=None,
                             *, module_qual=None, extra_template_args=None,
                             loc=None) -> str:
        return sub_struct_qualname(
            owner, method, inferred_type_args,
            analyzer=self.analyzer, type_to_cpp=self.deps.type_to_cpp,
            module_qual=module_qual,
            extra_template_args=extra_template_args, loc=loc)

    def build(self) -> THIRResumableFrame:
        func, record_name, state = self.func, self.record_name, self.state
        body = self._effective_body(func)
        # The frame is decided in its own name environment, not a body
        # emitter's scope: its params' types, its capture const verdicts,
        # and its const bindings, which the alias and for prescans below
        # grow as they classify -- each one's slot spelling reads what the
        # previous ones decided (a nested loop over a const loop var, an
        # alias off one). Every capture whose field the factory
        # const-qualifies is in the const sets; a capture that keeps its
        # mutable spelling (a pointer-repr Optional, an owned value) is
        # not, or alias locals would type against a field that isn't const.
        crp, dcbp, pack = frame_capture_const_names(
            self.analyzer, self.deps.is_static_protocol_param, func,
            record_name)
        mark_frame_const(state, *crp, *dcbp, *pack)
        self.env = FrameBuildEnv(
            analyzer=self.analyzer,
            var_types={pname: unwrap_ref_type(ptype)
                       for pname, ptype in func.params},
            const_ref_params=crp,
            deep_const_borrow_params=dcbp,
            const_bindings=state.const_frame_bindings,
            resolved_type_in=self.deps.resolved_type_in,
        )
        # The alias classification is memoized and the prescans read it, so
        # it runs first, under the environment just built.
        self._classify_pointer_alias_locals(func)
        try:
            for_uid_map = self._prescan_resumable_for_loops(
                func, body, record_name)
            with_uid_map = self._prescan_with_stmts(func, body)
            # Both of these build the frame layout, so they must follow the
            # for/with prescans whose outputs it reads (for_loop_info,
            # with_owning_str_targets, with_target_payloads). Neither's own
            # output feeds the layout, so building it here is not circular.
            self._prescan_with_manager_homes(func, body)
            self._prescan_resumable_ptr_slots(func, body)
            try_finally_uid_map = self._prescan_resumable_try_finally(func, body)
            state.loop_close_fields = self._loop_close_fields(func, body)
            # The locals are final once the prescans have hoisted theirs; a
            # bound-handle await names the local it polls.
            self.frame_locals = self._decide_frame_locals(func)
            builder = CFGBuilder(
                payload_factory=self._make_await_payload,
                for_uid_map=for_uid_map,
                with_uid_map=with_uid_map,
                try_finally_uid_map=try_finally_uid_map,
                func_returns_void=returns_void(func),
                loop_close_fields=state.loop_close_fields,
                for_strategies={uid: info.strategy for uid, info
                                in state.for_info_by_uid.items()},
                lent_step_vars=self._lent_step_vars(func),
            )
            cfg = builder.build(body)
        except _CFGNotYetSupported as e:
            raise CodeGenError(e.msg, loc=e.loc)
        # Inline awaits resolve their callee in the payload factory during the
        # build, so their edges ride the payload; the `async with` / `async for`
        # and `iter_next` embeddings are recorded by the prescans above. Between
        # them the frame's by-value embeddings are covered without re-walking
        # the AST, which cannot see the `async with`/`async for` sub-futures at
        # all -- they carry no TpyAwait node.
        for _y in cfg.yield_sites:
            _dep = getattr(_y.payload, "dep_unit", None)
            if _dep is not None:
                state.frame_dep_units.append(_dep)
        return self.publish(cfg)

    def _lent_step_vars(self, func: TpyFunction) -> frozenset[str]:
        """The loop vars bound from a lent step (a borrow-form tuple): a
        proxy-reference loop element, or a local the layout places as a
        borrow tuple."""
        layout = self._frame_layout(func)
        return frozenset(
            {info.borrow_tuple_loop_var
             for info in self.state.for_info_by_uid.values()
             if info.borrow_tuple_loop_var is not None}
            | {n for n, v in layout.bindings.items()
               if v.kind is FrameLocalKind.BORROW_TUPLE})

    def _decide_frame_locals(self, func: TpyFunction
                             ) -> 'tuple[THIRFrameLocal, ...]':
        layout = self._frame_layout(func)
        return tuple(
            THIRFrameLocal(name=n, type=t, placement=layout.bindings[n],
                           one_shot=n in self.state.one_shot_lift_names)
            for n, t in (func.generator_locals or [])
            if n in layout.bindings)

    def _receiver(self, func: TpyFunction) -> 'THIRBorrowedRecord | None':
        if self.record_name is None:
            return None
        self_type = method_self_type_by_name(self.record_name, self.analyzer)
        return (record_receiver(self_type, bool(func.is_readonly),
                                self.analyzer)
                if self_type is not None else None)

    def publish(self, cfg) -> THIRResumableFrame:
        func, state = self.func, self.state
        const_params = frozenset(
            pname for pname, _ in func.params
            if pname in self.env.const_ref_params
            or pname in self.env.deep_const_borrow_params)
        params = tuple(THIRParam(name=pname, type=ptype)
                       for pname, ptype in func.params)
        captures = tuple(capture_kind(func, pname, ptype, self.deps.is_static_protocol_param)
                         for pname, ptype in func.params)
        frame_locals = self.frame_locals
        edges = (list(state.frame_dep_units)
                 + self._for_src_generator_targets(func)
                 + bound_frame_targets(func))
        return THIRResumableFrame(
            func=func,
            record_name=self.record_name,
            shape=self.shape,
            params=params,
            captures=captures,
            const_params=const_params,
            receiver=self._receiver(func),
            locals=frame_locals,
            entry_block=cfg.entry_bb,
            blocks=cfg.blocks,
            suspensions=cfg.yield_sites,
            finally_helpers=cfg.finally_helpers,
            nested_defs=tuple(THIRLeafSlot(LeafRole.NESTED_DEF, nd)
                              for nd in collect_frame_nested_defs(func.body)),
            decomposed=cfg.decomposed,
            handler_entries=cfg.handler_entries,
            case_entries=case_entry_order(cfg),
            lifted_body=state.lifted_body,
            for_loop_info=state.for_loop_info,
            for_info_by_uid=state.for_info_by_uid,
            for_uid_map=state.for_uid_map,
            for_fields=state.for_fields,
            for_src_fields=state.for_src_fields,
            async_for_struct_names=state.async_for_struct_names,
            with_uid_map=state.with_uid_map,
            with_fields=state.with_fields,
            with_owned_ctx_map=state.with_owned_ctx_map,
            async_with_struct_names=state.async_with_struct_names,
            ptr_slot_fields=state.ptr_slot_fields,
            ptr_slot_map=state.ptr_slot_map,
            try_finally_fields=state.try_finally_fields,
            arg_lifts=state.arg_lifts,
            const_source_edges=state.const_source_edges,
            const_bindings=frozenset(state.const_frame_bindings),
            embedding_edges=tuple(edges),
            abandonment_cleanup=tuple(
                (y.suspension_index, actions)
                for y in cfg.yield_sites
                if (actions := tuple(reversed(region_cleanups(
                    cfg.blocks[y.resume_bb].region_stack))))),
            no_throw_cases=frozenset(case_entry_order(cfg)) & no_throw_blocks(cfg),
            finally_helpers_return=any(
                _stmts_have_any_return([s.source for s in body])
                for _, body in cfg.finally_helpers),
        )


    def _frame_temp_slot(self, ptype: 'TpyType',
                         arg: 'TpyExpr') -> 'TpyType | None':
        """The OWNED type a TEMPORARY argument of the awaited sub-coro hoists
        into a slot of THIS frame, or None when nothing is owed.

        A frame never receives a temporary, so this is the same question the
        sync call sites ask and it is the same predicate:
        `frame_temp_arg_slot` decides. `param_borrows` cannot answer for a
        borrowing-VIEW slot: the view's frame field is a VALUE-kind capture,
        so the kind cascade says by-value while the aliasing says borrow."""
        return frame_temp_arg_slot(arg, ptype, self.analyzer)

    def _seat_frame_temp_elems(self, func: TpyFunction, fi, i: int,
                               arg: TpyExpr, pre: list[TpyStmt], *,
                               shared: bool, binds: str | None) -> None:
        """Seat every temporary ELEMENT of a tuple-literal argument `i` that
        the callee's frame would borrow on a local of THIS frame, rewriting
        the element to the seat's name -- the element half of the argument
        seat, from the same plan the lowering row hoists (`frame_temp_elem_
        slots`). The tuple stays in place: the callee's frame copies its
        pointers, and what they point at is now storage this frame holds."""
        for entry in frame_temp_elem_slots(arg, fi.params[i].type,
                                           self.analyzer):
            k = entry.path[-1]
            source = entry.holder.elements[k]
            if shared:
                self._reject_shared_loop_seat(
                    fi, source,
                    f"an element of argument '{fi.params[i].name}'",
                    ", or declare the parameter 'Own[...]' so the callee "
                    "owns its copy")
            entry.holder.elements[k] = self._frame_local_for(
                func, source, entry.owned, pre, binds=binds)

    def _view_backing_coerce(self, ptype: 'TpyType',
                             arg: 'TpyExpr') -> 'TpyCoerce | None':
        """The coerce node the lift re-seats when a hoisted temporary argument
        arrived through one (`::tpy::as_mut_span(<source>)`): the coerce keeps
        its render and only its SOURCE moves onto the frame slot.

        A STACKED coerce is safe by the lift's own shape rather than by
        witness: `_hoist_borrowed_args` captures `host.expr` BEFORE re-seating,
        so the hoisted decl's init is the inner coerce whole and only the
        outermost source name is replaced. No two-layer coerce at a view
        argument could be built to observe it, so the claim is reasoned from
        that capture, not pinned by a case."""
        if not isinstance(arg, TpyCoerce):
            return None
        return arg if self._frame_temp_slot(ptype, arg) is not None else None

    def _effective_body(self, func: TpyFunction) -> list[TpyStmt]:
        """Return the function body with the await-lift pass applied so
        awaits embedded in expressions become top-level vardecls. The
        CFG builder treats compound statements (including any
        wrapping try/finally) uniformly, so no unwrap is needed here.

        Cached on the TpyFunction node so multiple codegen passes
        (struct emission + poll body) see the same rewrite.
        """
        state = self.state
        if state.lifted_body is not None:
            return state.lifted_body
        lifted = self._lift_nested_awaits(func, func.body)
        # Runs on the await-normalized body: every await now sits in a
        # top-level statement, so each INLINE await's call args are in a
        # known position to hoist (a nested-await arg has already become a
        # stable hoisted-local name and is left alone).
        lifted = self._lift_borrowed_rvalue_args(func, lifted)
        state.lifted_body = lifted
        return lifted

    def _lift_nested_awaits(self, func: TpyFunction,
                             body: list[TpyStmt]) -> list[TpyStmt]:
        """Rewrite each statement that has awaits buried in expressions
        into a sequence of statements where every await is at top level.

        Recurses into compound statement bodies (if/while/for/try/with)
        so awaits buried inside loop/branch bodies are also lifted.

        Example:
            x = (await a()) + (await b())
        becomes:
            __await_lift_0 = await a()
            __await_lift_1 = await b()
            x = __await_lift_0 + __await_lift_1

        Each lifted name is registered as a hoisted local so the bind
        slot is a frame field.
        """
        out: list[TpyStmt] = []
        for stmt in body:
            top_await = _top_level_await_in(stmt)
            new_stmts = self._lift_awaits_in_stmt(func, stmt, top_await)
            # Recurse compound bodies AFTER the surface lift so a
            # substituted TpyName isn't re-scanned. The surface lift
            # mutates `stmt` in place (see BUGS.md "Await lifter
            # mutates parse AST"); the compound recurse returns a
            # shallow copy so nested bodies don't.
            new_stmts = [
                self._lift_compound_subbodies(func, s) for s in new_stmts
            ]
            out.extend(new_stmts)
        return out

    def _lift_compound_subbodies(self, func: TpyFunction,
                                    stmt: TpyStmt) -> TpyStmt:
        """If `stmt` is a compound (if/while/for/try/with) and any of
        its sub-bodies needs lifting, return a shallow copy with the
        sub-body lists replaced by lifted versions. Otherwise return
        `stmt` unchanged. Does not mutate the input."""
        return self._map_compound_subbodies(func, stmt, self._lift_nested_awaits)

    def _map_compound_subbodies(
            self, func: TpyFunction, stmt: TpyStmt,
            transform: 'Callable[[TpyFunction, list[TpyStmt]], list[TpyStmt]]',
    ) -> TpyStmt:
        """Apply `transform(func, body)` to each statement-list sub-body of a
        compound `stmt` (its dataclass fields plus try-handler bodies). Return
        `stmt` unchanged when nothing moved -- preserving its id for the
        analyzer's id(stmt)-keyed tables -- or a shallow copy with the
        rewritten sub-bodies otherwise. Does not mutate the input."""
        if not hasattr(stmt, "sub_bodies"):
            return stmt
        if not is_dataclass(stmt):
            return stmt
        # Preserve the original stmt object (and its id) when sub-body
        # lifting produced no actual changes -- any analyzer-side
        # id(stmt)-keyed dict (e.g. if_branch_decls) would otherwise be
        # orphaned by a spurious clone.
        replacements: dict[str, list[TpyStmt]] = {}
        for f in fields(stmt):
            v = getattr(stmt, f.name, None)
            if isinstance(v, list) and v and isinstance(v[0], TpyStmt):
                lifted = transform(func, v)
                if not _same_elements(lifted, v):
                    replacements[f.name] = lifted
        # TpyTry: handlers list isn't TpyStmt-typed but each handler
        # carries its own body. Build new handler instances if any
        # handler body needed lifting.
        new_handlers: list[TpyExceptHandler] | None = None
        if isinstance(stmt, TpyTry) and stmt.handlers:
            rebuilt: list[TpyExceptHandler] = []
            any_changed = False
            for h in stmt.handlers:
                lifted_body = transform(func, h.body) if h.body else h.body
                if not _same_elements(lifted_body, h.body):
                    rebuilt.append(dc_replace(h, body=lifted_body))
                    any_changed = True
                else:
                    rebuilt.append(h)
            if any_changed:
                new_handlers = rebuilt
        if not replacements and new_handlers is None:
            return stmt
        new_stmt = copy.copy(stmt)
        for fname, lifted in replacements.items():
            setattr(new_stmt, fname, lifted)
        if new_handlers is not None:
            new_stmt.handlers = new_handlers
        return new_stmt

    def _lift_awaits_in_stmt(self, func: TpyFunction, stmt: TpyStmt,
                              top_await: 'TpyAwait | None') -> list[TpyStmt]:
        """Lift any TpyAwait inside `stmt`'s expressions out into preceding
        VarDecls. The top-level await (if any) stays in place. Returns the
        new statement list; if no rewrite needed, returns [stmt].
        """
        # Collect nested awaits (excluding the top-level one).
        lifts: list[TpyAwait] = []
        self._collect_nested_awaits(stmt, top_await, lifts)
        if not lifts:
            return [stmt]
        new_stmts: list[TpyStmt] = []
        for await_node in lifts:
            name = f"__await_lift_{self._next_lift_id(func)}"
            self._bump_lift_id(func)
            # One-shot by construction: created here, consumed by `stmt` (the
            # single replacement below). Records it as a movable source so a
            # tuple-unpack reading it moves elements out instead of copying.
            # INVARIANT: only OWNED results may be added here -- an await
            # result is always owned, and `owning_generator_tuple_locals`
            # relies on that to give a reference-element lift tuple owning
            # (not borrow) frame storage. Do not add a borrow/aliasing source.
            self.state.one_shot_lift_names.add(name)
            await_t = self.get_expr_type(await_node)
            if await_t is None:
                raise CodeGenError(
                    "await result has no analyzed type (lift)", loc=stmt.loc)
            await_t = unwrap_ref_type(await_t)
            new_decl = TpyVarDecl(
                name=name, type=await_t, init=await_node, loc=stmt.loc)
            new_stmts.append(new_decl)
            # Register as hoisted local; codegen emits a frame field.
            # Append in place to avoid an O(N) copy per lift (which,
            # paired with _next_lift_id's linear scan, would otherwise
            # be O(N^2) over nested awaits in one function).
            if func.generator_locals is None:
                func.generator_locals = []
            func.generator_locals.append((name, await_t))
            # Replace the await in `stmt`'s expression tree with a
            # TpyName referring to the lifted local. Use the analyzer's
            # set_expr_type so codegen's get_expr_type sees it.
            replacement = TpyName(name=name, loc=await_node.loc)
            self.analyzer.ctx.set_expr_type(replacement, await_t)
            self._replace_expr_in_stmt(stmt, await_node, replacement)
        new_stmts.append(stmt)
        return new_stmts

    def _collect_nested_awaits(self, stmt: TpyStmt, top_await: 'TpyAwait | None',
                                out: list[TpyAwait]) -> None:
        """Walk stmt's expressions; collect TpyAwait nodes that are NOT the
        top-level await (which is handled by region partitioning). Skips
        nested defs / sub_bodies."""

        def walk_expr(e):
            if e is None:
                return
            if isinstance(e, TpyAwait):
                if e is not top_await:
                    out.append(e)
                # Don't recurse into the await's own value; that becomes
                # the call processed by the lifted VarDecl.
                return
            for c in (e.children() if hasattr(e, "children") else ()):
                walk_expr(c)

        if hasattr(stmt, "exprs"):
            for e in stmt.exprs():
                walk_expr(e)

    def _next_lift_id(self, func: TpyFunction) -> int:
        """Return the next free index for __await_lift_<n> names.
        Tracked as a per-function counter so the lift pass is linear
        in the total number of lifts."""
        return self.state.next_lift_id

    def _bump_lift_id(self, func: TpyFunction) -> None:
        self.state.next_lift_id += 1

    def _lift_borrowed_rvalue_args(self, func: TpyFunction,
                                    body: list[TpyStmt],
                                    in_loop: bool = False) -> list[TpyStmt]:
        """Hoist rvalue-temporary arguments of INLINE-mode await calls into
        frame-backed hoisted locals so the sub-coro's borrow doesn't dangle
        across the suspension.

            await f(Dog("x"))          # f(a: Dog | Cat)
        becomes
            __coro_arg_0 = Dog("x")
            await f(__coro_arg_0)

        The sub-coro frame's `variant<Cat*, Dog*>` (or `T*` for pointer-form
        Optional, or `T&` for a plain non-value ref param) then points at
        `__coro_arg_0` -- a frame field outliving the sub-coro -- instead of
        a `__tmp` flushed inside the suspending `case` block, which dies at
        the `continue`. Without the lift the pointer/variant shapes are
        silent UB and the plain-ref shape is a hard C++ error.

        A stable-lvalue arg (a name or field chain) is left in place: its
        address already persists across the suspension, and copying it would
        break mutation-through-borrow and reject @nocopy elements.

        Runs after `_lift_nested_awaits`, so every await is already in a
        top-level statement; recursion into compound sub-bodies mirrors that
        pass. The await half is self-gating for generators (no awaits -> no
        INLINE calls); `_hoist_frame_factory_args` then covers every OTHER
        generator/coro factory call in the body -- a `for` head, a handle bound
        to a local, the escaping `create_task(f(rvalue))` form -- whose
        argument the uniform frame-temp rule would otherwise have named in the
        state's `case` block, which the handle outlives.

        A `Task` that outlives the whole enclosing frame is a further hazard
        this pass cannot answer (`BUGS.md#create-task-ref-param-escape`): a
        frame field is as long-lived as this body, no longer.

        `in_loop` says whether the statements can RE-EXECUTE within one run of
        this body. One field per call SITE is only enough while at most one
        handle built there is alive at a time, so a site whose handle outlives
        the iteration is rejected instead of seated (see
        `_hoist_frame_factory_args`)."""
        out: list[TpyStmt] = []
        for stmt in body:
            hoisted = self._hoist_borrowed_args(func, stmt)
            # The factory hoist runs on the await hoist's OUTPUT: an inline
            # await's arguments are already frame-local names by then, so the
            # two passes never name the same argument twice.
            out.extend(hoisted[:-1])
            out.extend(
                self._hoist_frame_factory_args(func, hoisted[-1], in_loop))
            # Recurse compound bodies AFTER the surface hoists: they mutate
            # only the host statement's own call arg lists, never sub-bodies,
            # so the passes never touch the same node. A loop's OWN head was
            # hoisted at the enclosing depth just above; only its sub-bodies
            # re-execute.
            sub_in_loop = in_loop or isinstance(out[-1], (TpyForEach, TpyWhile))
            out[-1] = self._map_compound_subbodies(
                func, out[-1],
                partial(self._lift_borrowed_rvalue_args, in_loop=sub_in_loop))
        return out

    def _hoist_borrowed_args(self, func: TpyFunction,
                              stmt: TpyStmt) -> list[TpyStmt]:
        """If `stmt` hosts a top-level INLINE await call, hoist its borrowed
        rvalue args into preceding vardecls (mutating the call's arg list in
        place to reference the hoisted names). Returns [preceding vardecls...,
        stmt]; [stmt] when nothing to hoist."""
        await_node = _top_level_await_in(stmt)
        if await_node is None or await_node.awaited_async_func_name is None:
            return [stmt]
        call = await_node.value
        if not isinstance(call, (TpyCall, TpyMethodCall)):
            return [stmt]
        fi = call.resolved_function_info
        if fi is None:
            return [stmt]
        pre: list[TpyStmt] = []
        for i, arg in enumerate(call.args):
            # Varargs (arg_index past the declared params) are a separate
            # dangle class tracked in BUGS.md; leave them for the *args path.
            if i >= len(fi.params):
                break
            self._seat_frame_temp_elems(func, fi, i, arg, pre, shared=False,
                                        binds=_bound_local(stmt))
            # A BORROWING-VIEW slot is passed by value, but the value IS a
            # borrow the sub-coro's frame keeps for its whole life -- the same
            # standing the REF / POINTER kinds have. What must outlive the
            # suspension is then the STORAGE the view aliases, so the lift
            # targets the coerce's source and leaves the view built over the
            # hoisted local (`::tpy::as_mut_span((*__coro_arg_0))`).
            host = self._view_backing_coerce(fi.params[i].type, arg)
            temp_slot = self._frame_temp_slot(fi.params[i].type, arg)
            if (host is None and temp_slot is None
                    and not param_borrows(fi.params[i].type,
                                          self.deps.is_static_protocol_param)):
                continue
            source = host.expr if host is not None else arg
            # `None` lowers to nullptr (pointer-form Optional) or
            # `std::monostate` (pointer-variant union) -- a by-value slot, not
            # a borrow, so there is no temp to outlive the suspension.
            if isinstance(source, TpyNoneLiteral):
                continue
            if not (is_rvalue_source(self.analyzer, source)
                    or is_temporary_expr(self.analyzer, source)):
                continue
            # The frame slot takes the source's OWNED type where the uniform
            # frame-temp rule named one (a str/bytes argument owns its buffer
            # in the slot however the param spells it).
            arg_t = (temp_slot if temp_slot is not None
                     else self.get_expr_type(source))
            if arg_t is None:
                raise CodeGenError(
                    "await arg has no analyzed type (borrowed-arg lift)",
                    loc=stmt.loc)
            self._seat_arg_on_frame_local(func, call, i, fi.params[i].type,
                                          source, host, arg_t, pre,
                                          binds=_bound_local(stmt))
        pre.append(stmt)
        return pre

    def _seat_arg_on_frame_local(self, func: TpyFunction, call, i: int,
                                 ptype: 'TpyType', source: TpyExpr,
                                 host: 'TpyCoerce | None', arg_t: 'TpyType',
                                 pre: list[TpyStmt], *,
                                 binds: str | None) -> None:
        """Move `source` onto a `__coro_arg_N` local of THIS frame
        (`_frame_local_for`) and point argument `i` of `call` at it.

        A coerce-shaped argument keeps its coerce and only its SOURCE moves
        (`::tpy::as_mut_span(__coro_arg_0)`); an argument with no coerce
        becomes the local itself."""
        arg_t = unwrap_ref_type(arg_t)
        # A tuple arg's analyzed type can carry IntLiteral elements and
        # per-element Own provenance, which would demote the hoisted frame
        # field to a value tuple (or fail to render). The hoisted local
        # exists to back the param's borrow, so give it the declared param
        # slot shape -- the frame field then takes the borrow form and the
        # sub-coro's pointer slots stay valid across the suspension.
        lift_ptype = unwrap_readonly(unwrap_ref_type(ptype))
        if isinstance(arg_t, TupleType) and isinstance(lift_ptype, TupleType):
            arg_t = lift_ptype
        replacement = self._frame_local_for(func, source, arg_t, pre,
                                            binds=binds)
        if host is not None:
            host.expr = replacement
        else:
            call.args[i] = replacement

    def _frame_local_for(self, func: TpyFunction, source: TpyExpr,
                         local_t: 'TpyType', pre: list[TpyStmt], *,
                         binds: str | None) -> TpyName:
        """Declare `source` as a fresh `__coro_arg_N` local of THIS frame and
        return the name that reads it back.

        The local is registered in `generator_locals`, so its storage is a
        field of the enclosing frame and lives as long as the frame does --
        which is what a hoist inside a resumable body has to mean. A local of
        the enclosing BLOCK would be a `case`-block local, and every handle
        this hoist exists to feed (a sub-coro field, a `__for_src` slot, a
        `Task`) outlives that block."""
        name = f"__coro_arg_{self.state.next_arg_lift_id}"
        self.state.next_arg_lift_id += 1
        # loc=None: the hoisted decl is a synthesized sub-step of the host
        # statement, not its own source line, so it must not re-emit the
        # host's source comment (which the host statement still emits).
        pre.append(TpyVarDecl(name=name, type=local_t, init=source, loc=None))
        if func.generator_locals is None:
            func.generator_locals = []
        # Declared with its statement: ahead of the local the statement binds,
        # else after every local. The field order proper follows the borrow
        # edges recorded here (`_frame_fields`): the lift borrows what its
        # expression reads, and the handle `binds` names borrows the lift.
        names = [n for n, _ in func.generator_locals]
        if binds is not None and binds in names:
            func.generator_locals.insert(names.index(binds), (name, local_t))
        else:
            func.generator_locals.append((name, local_t))
        self.state.arg_lifts[name] = (source, binds)
        replacement = TpyName(name=name, loc=source.loc)
        self.analyzer.ctx.set_expr_type(replacement, local_t)
        return replacement

    def _frame_factory_calls(
            self, func: TpyFunction,
            stmt: TpyStmt) -> 'list[tuple[TpyExpr, bool]]':
        """The generator/coroutine FACTORY calls in `stmt` that run
        unconditionally and exactly once when control reaches the statement,
        each paired with whether its HANDLE can outlive the statement.

        That is the whole admission for the hoist below: a preceding decl
        preserves evaluation order only where the call was going to run anyway,
        once. So the descent starts at the statement slots evaluated on entry
        (a `for` head, an init, an assigned value, a discarded expression) and
        walks only through coercions, `await` operands and ARGUMENT lists --
        never into a ternary arm, a `bool` operator's right side, a
        comprehension body or a nested def, where the sub-expression is
        conditional or repeated. A `return` / `yield` value is out for a
        different reason: the handle LEAVES this frame, so seating its argument
        here would pin the argument to a frame that dies first.

        The callee test is the one the lowering row uses (`frame_factory_
        callee`, plus an async def), so the hoist and the row cannot disagree
        about which callee keeps an argument past the statement.

        The paired flag is what the ONE-FIELD-PER-SITE seat below needs: the
        field is safe to re-emplace only where no handle built at this site is
        still alive. The positions that consume the handle where it stands are
        a `for` head (the loop statement drains and destroys the iterator), an
        `await` operand (the statement runs the coroutine to completion), a
        bare discard (the temporary dies at the semicolon), and a bind whose
        name is only ever drained -- one slot, re-assigned, so at most one
        handle is live. Every other position -- an argument of another call
        (`create_task(...)`, and through it `tasks.append(...)`), a bind whose
        name is copied elsewhere -- hands the handle on."""
        # The BIND answer walks the whole body, so it is a thunk: only a root
        # that turns out to host a factory call ever asks for it.
        root: 'TpyExpr | None' = None
        bind_escapes: 'Callable[[], bool]' = lambda: False
        # At the head of a loop whose source the FRAME holds -- one that
        # suspends, or one that lends an element out of a fresh source (the
        # prescan's `non_suspending` verdict, asked the same way here) -- a
        # lazy combinator keeps its arguments exactly as a factory keeps its
        # own (`iterator_source_callee`). A loop the frame does not hold
        # keeps its source in its own block, and so its arguments' lifetimes
        # as CPython has them.
        retains = frame_factory_callee
        if isinstance(stmt, TpyForEach):
            root = stmt.iterable
            if (stmt.is_async or _stmts_have_any_suspension(stmt.body)
                    or _stmts_have_any_suspension(stmt.orelse)
                    or (for_source_is_rvalue(stmt, self.analyzer)
                        and self._loop_lends_element_out(
                            func, _loop_bound_names(self.analyzer,
                                                    stmt)))):
                retains = iterator_source_callee
        elif isinstance(stmt, TpyVarDecl):
            root = stmt.init
            bind_escapes = partial(self._handle_name_escapes, func, stmt.name)
        elif isinstance(stmt, TpyAssign):
            root = stmt.value
            target = peel_coerce(stmt.target)
            bind_escapes = (
                partial(self._handle_name_escapes, func, target.name)
                if isinstance(target, TpyName) else (lambda: True))
        elif isinstance(stmt, TpyTupleUnpack):
            root = stmt.value
            targets = stmt.targets
            bind_escapes = lambda: any(
                t is None or self._handle_name_escapes(func, t)
                for t in targets)
        elif isinstance(stmt, TpyExprStmt):
            root = stmt.expr
        # `None` in a pair means "whatever the bind answers"; a nested position
        # has already decided (an argument hands the handle on, an `await`
        # consumes it) and overrides the bind either way.
        found: 'list[tuple[TpyExpr, bool | None]]' = []

        def visit(e: 'TpyExpr | None', escapes: 'bool | None') -> None:
            if e is None:
                return
            if isinstance(e, TpyCoerce):
                visit(e.expr, escapes)
                return
            if isinstance(e, TpyAwait):
                visit(e.value, False)
                return
            if not isinstance(e, (TpyCall, TpyMethodCall)):
                return
            fi = e.resolved_function_info
            if fi is not None and (retains(fi) or fi.is_async):
                found.append((e, escapes))
            if isinstance(e, TpyMethodCall):
                visit(e.obj, escapes)
            for a in e.args:
                visit(a, True)

        visit(root, None)
        if not any(e is None for _, e in found):
            return [(c, bool(e)) for c, e in found]
        bound = bind_escapes()
        return [(c, bound if e is None else e) for c, e in found]

    def _handle_name_escapes(self, func: TpyFunction, name: str) -> bool:
        """Whether a generator/coroutine handle bound to `name` can be carried
        out of the statement that builds it.

        The bind itself is not: `name` is ONE slot, so a rebind on the next
        iteration destroys the previous handle and at most one is ever live --
        which is exactly what one hoisted field per call site can serve. What
        carries a handle out is a use that copies or moves it somewhere
        longer-lived (an argument, a container element, a return). The two
        DRAINING uses -- `await name` and `for ... in name` -- consume the
        handle where it stands, so they are not escapes; a rebind of `name`
        itself is not one either."""

        def expr_escapes(e: 'TpyExpr | None') -> bool:
            if e is None:
                return False
            if isinstance(e, TpyName):
                return e.name == name
            if isinstance(e, TpyAwait):
                inner = peel_coerce(e.value)
                if isinstance(inner, TpyName) and inner.name == name:
                    return False
            return any(expr_escapes(c)
                       for c in (e.children() if hasattr(e, "children") else ()))

        def body_escapes(body: 'list[TpyStmt]') -> bool:
            for s in body:
                skip: 'TpyExpr | None' = None
                if isinstance(s, TpyForEach):
                    head = peel_coerce(s.iterable)
                    if isinstance(head, TpyName) and head.name == name:
                        skip = s.iterable
                elif isinstance(s, TpyAssign):
                    target = peel_coerce(s.target)
                    if isinstance(target, TpyName) and target.name == name:
                        skip = s.target
                for e in (s.exprs() if hasattr(s, "exprs") else ()):
                    if e is not skip and expr_escapes(e):
                        return True
                if any(body_escapes(b) for b in s.sub_bodies()):
                    return True
                if isinstance(s, TpyTry):
                    for h in s.handlers:
                        if body_escapes(h.body):
                            return True
            return False

        return body_escapes(func.body)

    def _hoist_frame_factory_args(self, func: TpyFunction, stmt: TpyStmt,
                                  in_loop: bool = False) -> list[TpyStmt]:
        """Seat every temporary argument of a generator/coro factory call in
        `stmt` on a frame local, and return [decls..., stmt].

        The uniform frame-temp rule names such an argument at the enclosing
        statement's flush point; inside a resumable body that flush point is
        the state's `case` block, which the handle the factory returns
        outlives -- so the name has to be a frame field instead. Same shape
        predicate as the lowering row (`frame_temp_arg_slot`), so an argument
        named here is exactly one the row would otherwise have flushed into the
        `case` block, and the row then sees a plain name and does nothing.

        There is one field per call SITE, so a site that can re-execute while
        an earlier handle is still alive would hand every one of those handles
        the LAST value written. That is the `create_task(f(temp))`-in-a-loop
        shape, and no per-site field can serve it -- the seat is refused
        instead of silently aliasing (`_reject_shared_loop_seat`)."""
        pre: list[TpyStmt] = []
        for call, escapes in self._frame_factory_calls(func, stmt):
            fi = call.resolved_function_info
            shared = in_loop and escapes
            recv = self._factory_receiver_to_seat(func, call)
            if recv is not None:
                if shared:
                    self._reject_shared_loop_seat(
                        fi, recv, "the receiver",
                        ", or call the method on a named receiver")
                recv_t = self._frame_seat_type(recv)
                call.obj = self._frame_local_for(func, recv, recv_t, pre,
                                                 binds=_bound_local(stmt))
            for i, arg in enumerate(call.args):
                # Varargs (past the declared params) are a separate dangle
                # class tracked in BUGS.md; leave them for the *args path.
                if i >= len(fi.params):
                    break
                ptype = fi.params[i].type
                self._seat_frame_temp_elems(func, fi, i, arg, pre,
                                            shared=shared,
                                            binds=_bound_local(stmt))
                temp_slot = self._frame_temp_slot(ptype, arg)
                if temp_slot is None:
                    continue
                inner = peel_coerce(arg)
                if (not frame_factory_callee(fi)
                        and isinstance(inner, (TpyCall, TpyMethodCall))
                        and iterator_source_callee(
                            inner.resolved_function_info)):
                    # A combinator OWNS an iterator handed to it as an rvalue
                    # (`enumerate(reversed(xs))`); seating the inner one
                    # would hand the outer a reference instead. The inner's
                    # own arguments were visited on their own.
                    continue
                host = self._view_backing_coerce(ptype, arg)
                source = host.expr if host is not None else arg
                if shared:
                    self._reject_shared_loop_seat(
                        fi, source, f"argument '{fi.params[i].name}'",
                        ", or declare the parameter 'Own[...]' so the callee "
                        "owns its copy")
                self._seat_arg_on_frame_local(func, call, i, ptype, source,
                                              host, temp_slot, pre,
                                              binds=_bound_local(stmt))
        pre.append(stmt)
        return pre

    def _reject_shared_loop_seat(self, fi, source: TpyExpr, what: str,
                                 remedy: str) -> 'NoReturn':
        """Refuse a temporary whose seat would be shared by several live
        handles of the same loop.

        The seat is a field of the enclosing frame, one per call site, so the
        next iteration overwrites it while the handle the previous iteration
        handed on (a `Task`, a stored generator) still borrows it. Naming the
        temporary in the loop body does not help -- a local of a resumable
        body is a frame field too -- so the remedy the message gives is
        storage that outlives the loop."""
        raise CodeGenError(
            f"cannot use a temporary as {what} of '{fi.name}' here: the "
            f"handle this call creates outlives the loop iteration, so every "
            f"handle the loop creates would borrow one shared slot of the "
            f"enclosing frame and see the last iteration's value. Keep each "
            f"value in storage that outlives the loop (append it to a list "
            f"declared before the loop and pass that element)" + remedy,
            loc=getattr(source, "loc", None))

    def _roots_at_temp(self, e: TpyExpr) -> bool:
        """Whether `e` reads a field / element out of a TEMPORARY -- storage
        the enclosing statement destroys. A chain rooted at a name (or at a
        subscript of one) is not: only the root's own lifetime is in
        question, and the access itself borrows into it."""
        root = peel_coerce(e)
        while isinstance(root, (TpyFieldAccess, TpySubscript)):
            root = peel_coerce(root.obj)
        return (root is not peel_coerce(e)
                and materializing_temp_source(root, self.analyzer))

    def _frame_seat_type(self, recv: TpyExpr) -> 'TpyType':
        """The frame-field type a seated factory receiver takes."""
        recv_t = self.analyzer.get_expr_type(recv)
        if recv_t is None:
            raise CodeGenError(
                "factory receiver has no analyzed type (receiver lift)",
                loc=getattr(recv, "loc", None))
        return unwrap_own(unwrap_readonly(
            unwrap_ref_type(unwrap_send_sync(recv_t))))

    def _factory_receiver_to_seat(self, func: TpyFunction,
                                  call) -> 'TpyExpr | None':
        """The TEMPORARY RECEIVER of a generator/coro factory method that must
        be seated on a frame local -- the receiver twin of the argument hoist
        above. `None` when the receiver already outlives the frame.

        The frame keeps the receiver as `<Class>&` for the handle's whole
        life, exactly as it keeps a borrowed argument, so one invariant covers
        both: a frame never receives a temporary. Inside a resumable body the
        receiver would otherwise be named in the state's `case` block
        (`Summer __tmp_1 = Summer(200);` -- the `method.gen_recv_temp` row,
        which materializes at the CONSUMING position) or not named at all (the
        async route binds `const Summer&` straight to the prvalue), and the
        handle outlives both.

        Admission is the shape the CONSUMING route already accepts, so the
        hoist removes a dangle without moving the accepted set: the ctor-rvalue
        slice for a generator factory -- hoisting a broader shape would make
        `for v in make_summer(1).pair(xs)` compile here while the identical
        line still rejects in a sync caller -- and any materializing rvalue for
        an async one, whose route gates no receiver shape at all (every rvalue
        receiver there compiles today and dangles). The hoisted receiver is a
        NAME, so the `gen_recv_temp` row no longer fires and no second temp is
        built.

        A receiver the seat cannot take but that BORROWS INTO a temporary --
        `make_pair(a, b).left`, a field or element read out of an owner the
        statement destroys -- is rejected on the async route rather than left
        to bind `const Summer&` to the dying owner. Seating the owner instead
        would widen what the language accepts (the generator route rejects the
        same receiver at lowering, `method.fi_kind`, and sema rejects it at the
        bound and awaited positions), so the routes are made to agree on the
        reject. A chain rooted at a NAME is untouched: its storage already
        outlives the statement."""
        if not isinstance(call, TpyMethodCall):
            return None
        analyzer = self.analyzer
        recv = call.obj
        fi = call.resolved_function_info
        is_async = fi is not None and fi.is_async
        if not materializing_temp_source(recv, analyzer):
            if is_async and self._roots_at_temp(recv):
                raise CodeGenError(
                    f"receiver of the async method '{fi.name}' must be a "
                    f"stable lvalue (a local, parameter, or field chain "
                    f"rooted at one) or a temporary this frame can seat -- "
                    f"the coroutine captures it by reference for the handle's "
                    f"whole life, and this one reads out of a temporary that "
                    f"dies at the end of the statement. Bind the owner to a "
                    f"local first: `r = <expr>; ... r.field.{fi.name}(...)`",
                    loc=getattr(recv, "loc", None))
            return None
        if not is_async:
            if not gen_recv_ctor_temp(recv, analyzer):
                return None
        return recv

    def _replace_expr_in_stmt(self, stmt: TpyStmt, old_expr,
                                new_expr) -> None:
        """Walk stmt's dataclass fields and replace `old_expr` with
        `new_expr` (by identity). Catches both primary-expression slots
        (init/value/expr/condition for if/while/etc.) and nested
        expression children."""
        if not is_dataclass(stmt):
            return
        for f in fields(stmt):
            v = getattr(stmt, f.name, None)
            if v is old_expr:
                setattr(stmt, f.name, new_expr)
                return
            if isinstance(v, list):
                for i, item in enumerate(v):
                    if item is old_expr:
                        v[i] = new_expr
                        return
                    if hasattr(item, "children"):
                        self._replace_in_expr(item, old_expr, new_expr)
            elif hasattr(v, "children"):
                self._replace_in_expr(v, old_expr, new_expr)

    def _replace_in_expr(self, expr, old_expr, new_expr) -> None:
        """Recursively replace `old_expr` with `new_expr` inside `expr`."""
        if not hasattr(expr, "children") or not is_dataclass(expr):
            return
        for f in fields(expr):
            v = getattr(expr, f.name, None)
            if v is old_expr:
                setattr(expr, f.name, new_expr)
                return
            if isinstance(v, list):
                for i, item in enumerate(v):
                    if item is old_expr:
                        v[i] = new_expr
                        return
                    if hasattr(item, "children"):
                        self._replace_in_expr(item, old_expr, new_expr)
            elif hasattr(v, "children"):
                self._replace_in_expr(v, old_expr, new_expr)

    def _rebound_ref_names(self, func: TpyFunction) -> 'frozenset[str]':
        """The frame locals the storage policy holds as a pointer over
        storage of their own, from the name facts a plain body's bindings
        read (`first_binding_storage`)."""
        state = self.state
        if state.rebound_ref_names is None:
            scan = self.analyzer.function_scan_results.get(func)
            rebound = body_rebound_names(
                func.body, scan.reassigned if scan is not None else set())
            state.rebound_ref_names = frozenset(
                n for n, t in (func.generator_locals or [])
                if first_binding_storage("frame", t, rebound=n in rebound,
                                         hoisted=False).pointer)
        return state.rebound_ref_names

    def _frame_form_inputs(self, func: TpyFunction) -> '_FrameFormInputs':
        """The name sets a frame local's field FORM is read off, minus the
        ones the for and with prescans fill in.

        Split out of `_frame_layout` so the for prescan -- which runs before
        those -- can ask the SAME classifier whether a local's field lends
        storage instead of growing a second answer to that question."""
        return _FrameFormInputs(
            owning_tuple_locals=self.owning_generator_tuple_locals(func),
            pointer_tuples=self.frame_pointer_tuples(func),
            rebound_ref_names=self._rebound_ref_names(func),
            # Statement-level borrow aliases (single-assign / tuple-unpack)
            # get a `T*` field rather than an owning frame_slot<T>: a `T*`
            # field kept live across suspensions.
            pointer_form_names=set(self._classify_pointer_alias_locals(func)),
        )

    def _loop_lends_element_out(self, func: TpyFunction,
                                bound_names: 'frozenset[str]') -> bool:
        """Whether this loop can bind something out of its elements into
        frame storage that may point back at an element.

        Such a binding is read after the loop's own block is gone, so
        whatever it may point into has to outlive that block. The question is
        answered off the bound name's TYPE, conservatively: only a provably
        self-contained type (`_cannot_hold_a_borrow`) is a copy that needs
        nothing kept alive. A name that is not a frame local at all dies with
        the block and cannot be read later.

        Leans on the rejects that stop a borrow from travelling FURTHER than
        a frame local: into a field (`assign.field_write_shape`) or into a
        container (`method.arg_shape`), and on the borrow checker's
        "reference to 'x' may outlive its storage". Without those this
        question would have to be asked of the whole body, not of its
        bindings.
        """
        frame_types = dict(func.generator_locals or [])
        for name in bound_names:
            if name not in frame_types:
                continue
            ltype = frame_types[name]
            if ltype is None or not _cannot_hold_a_borrow(ltype):
                return True
        return False

    def _holds_rebound_ref(self, lname: str, ltype_inner: 'TpyType',
                           inp: '_FrameFormInputs') -> bool:
        """Whether the storage policy holds frame local `lname` as a
        pointer over storage of its own (`rebound_ref_names`) and its type
        has that form: a plain reference type, not a `@dynamic` handle (an
        erased coroutine, a single-use object whose rebind drops the old
        one)."""
        bare = unwrap_own(ltype_inner)
        return (lname in inp.rebound_ref_names
                and is_plain_nonvalue(bare)
                and not is_dyn_protocol(unwrap_readonly(bare)))

    def _frame_local_form(
            self, lname: str, ltype_inner: 'TpyType',
            inp: '_FrameFormInputs',
    ) -> 'tuple[FrameLocalKind, str | None, TpyType | None]':
        """One frame local's field form, plus the payload spelling and
        effective type the form needs.

        Arm PRECEDENCE is load-bearing: pointer-form is checked before
        `is_value_type` because a pointer-form `__for_tup` holder is itself a
        value tuple; borrow-tuple excludes `__for_tup_*` holders (their form
        is the loop machinery's call) unless the loop classified them
        borrow-tuple explicitly.
        """
        source_form_fields = inp.source_form_fields
        pointer_form_names = inp.pointer_form_names
        borrow_tuple_names = inp.borrow_tuple_names
        owning_str = inp.owning_str
        owning_tuple_locals = inp.owning_tuple_locals
        pointer_payloads = inp.pointer_payloads
        payload: str | None = None
        effective_type: 'TpyType | None' = None
        # A protocol-typed local has no concrete C++ backing -- only
        # captured params carry the deduced template arg `T_<pname>`. A
        # *single-assignment* alias of a bare protocol param (`xs = it`)
        # is forwarded to that param by sema (see
        # `_extract_proto_param_forwarding`) and never reaches here.
        # What lands here is the unforwarded remainder -- chiefly a
        # *reassigned* alias -- which has no single backing param. The
        # verdict is a kind (not an error) so non-frame consumers can
        # classify; rendering a frame FIELD for it raises at the struct
        # emit.
        if self.deps.is_static_protocol_param(ltype_inner):
            kind = FrameLocalKind.PROTOCOL
        elif inp.pointer_tuples.get(lname) is not None:
            # A pointer tuple: each owned element is the scalar reference
            # local, its object in a per-site field of its own, so the
            # field is the borrow-form tuple of the plain body's local.
            kind = FrameLocalKind.BORROW_TUPLE
            effective_type = inp.pointer_tuples[lname]
        elif (lname in source_form_fields
                and lname in inp.with_targets
                and lname not in inp.with_generic_targets
                and self._holds_rebound_ref(lname, ltype_inner, inp)):
            # A rebound `with ... as` target (the storage policy): a pointer
            # its rebinds re-point, at a lent `__enter__()` result or at the
            # item's own field over a by-value one; const over a `readonly`
            # result (`_record_with_target_payloads`).
            kind = FrameLocalKind.REBIND_PTR
        elif lname in source_form_fields:
            # Loop var whose alias-vs-own choice belongs to C++: the
            # payload is spelled from the source's iteration protocol,
            # and frame_slot's specializations supply either form. Comes
            # before the type-directed arms below -- they would re-derive
            # a form from the TPy element type, which is exactly what
            # this field exists to stop doing.
            kind = FrameLocalKind.SOURCE_FORM_SLOT
            payload = source_form_fields[lname]
        elif lname in owning_str:
            # `with X() as label:` -- `__enter__` returns by value;
            # storing the view across suspensions would dangle. Use
            # owning storage. See `_prescan_with_stmts`.
            kind = FrameLocalKind.OWNED_STR
        elif lname in pointer_form_names:
            # Pointer-form alias: a for-loop var (non-value element
            # over a stable source) or a tuple-unpack target bound to
            # a non-value container member. Stored as `T*` (alias the
            # live element) rather than `frame_slot<T>` (value-copy),
            # so mutations propagate and resume preserves aliasing;
            # also works for @nocopy / move-only elements. Bound by
            # address in `_emit_async_for_advance` / the tuple-unpack
            # emit.
            kind = FrameLocalKind.PTR_ALIAS
            payload = pointer_payloads.get(lname)
        elif lname in owning_tuple_locals:
            # OWNING pointer-repr tuple local: the frame must hold the
            # element storage (emplace writes, `(*name)` reads) -- a
            # borrow `std::tuple<..., T*>` field can't own, and the
            # owning rvalue can't be address-taken into it. A MIXED tuple
            # has no fully-owned storage form to hold: its borrowed
            # element must keep pointing at the caller's object, so its
            # payload is the mixed render.
            #
            # The ownership axis reads off the EFFECTIVE type: a literal
            # init's per-element verdict carries no `Own` in the declared
            # type (there is no user spelling for it), so the oracle hands
            # the Own-wrapped image back and every arm below keys on that.
            eff = owning_tuple_locals[lname] or ltype_inner
            effective_type = eff
            if isinstance(eff, TupleType) and eff.is_mixed_own():
                kind = FrameLocalKind.MIXED_TUPLE_SLOT
                payload = self.deps.tuple_borrow_cpp(eff)
            else:
                kind = FrameLocalKind.OWNING_TUPLE_SLOT
        elif (isinstance(ltype_inner, TupleType)
                and ltype_inner.has_pointer_repr_element()
                and (not lname.startswith("__for_tup_")
                     or lname in borrow_tuple_names)):
            # Borrow-form tuple local (std::tuple<..., T*>): a value-form
            # field would copy the element across the suspension (the
            # silent-copy divergence). Pointers default-construct to
            # null, so no frame_slot wrapper is needed.
            kind = FrameLocalKind.BORROW_TUPLE
        elif self._holds_rebound_ref(lname, ltype_inner, inp):
            # A rebound reference local keeps each object for its aliases,
            # which an owning `frame_slot<T>` cannot: the local goes
            # pointer-form over per-site materialization fields, and its
            # IN_PLACE rebinds assign through the pointer. A `@dynamic`
            # handle (an erased coroutine) is a single-use object whose
            # rebind drops the old one: its own arm below.
            kind = FrameLocalKind.REBIND_PTR
        elif (isinstance(ltype_inner, OwnType)
                and not unwrap_own(ltype_inner).is_value_type()
                and not is_dyn_protocol(
                    unwrap_readonly(ltype_inner.wrapped))):
            # `Own[T]` over a REFERENCE type reports as a value here, but the
            # object it names is built at the BINDING, later than the frame.
            # A bare field would default-construct at frame creation and then
            # assign -- which is not the object's real constructor, and is
            # deleted outright once T holds a non-default-constructible
            # member (a `Box`, `Rc`, `Mutex`). The slot's placement-new runs
            # the actual constructor at the actual construction point.
            #
            # A coroutine/adapter handle is the exception: `Own[@dynamic P]`
            # renders as a `std::optional<coro>` / `unique_ptr` that already
            # default-constructs empty and takes the value by assignment, so
            # a slot around it would just double-wrap.
            kind = FrameLocalKind.FRAME_SLOT
        elif ltype_inner.is_value_type():
            kind = FrameLocalKind.VALUE
        elif pointer_repr_optional(ltype_inner) is not None:
            # Pointer-repr Optional: bare `T* = nullptr` aliases the
            # source and uses nullptr as both "uninitialized" and
            # "None"; no outer `std::optional<...>` wrap. A `readonly`
            # element (a loop var over a readonly container) is the same
            # pointer, const through the frame's const bindings.
            kind = FrameLocalKind.OPT_PTR
        else:
            kind = FrameLocalKind.FRAME_SLOT
        return (kind, payload, effective_type)

    def _frame_layout(self, func: TpyFunction) -> 'FrameLayoutPlan':
        """Get-or-build the frame-local placement plan for this body: one
        `FrameLocalLayout` verdict per `func.generator_locals` name.

        Must run after the for/with/alias prescans and the await/arg lifting
        have populated the build state (every caller sits downstream of
        them in `build`) -- but only the LOOP-VAR and with-target arms
        depend on those; the rest is `_frame_form_inputs`.
        """
        state = self.state
        if state.frame_layout is not None:
            return state.frame_layout

        inp = self._frame_form_inputs(func)
        inp.owning_str.update(state.with_owning_str_targets)
        # Loop vars whose field payload is spelled from the iteration
        # source; the trait decides alias-vs-own, so no TPy-side form.
        # A name bound by several loops has one field: its pointee is the
        # loops' payloads joined, const if any of them is.
        payloads: dict[str, list[str]] = {}
        for info in state.for_loop_info.values():
            if info.pointer_form_loop_var is not None:
                inp.pointer_form_names.add(info.pointer_form_loop_var)
            inp.pointer_form_names.update(info.pointer_form_unpack_targets)
            for name, payload in info.pointer_payloads.items():
                if payload not in payloads.setdefault(name, []):
                    payloads[name].append(payload)
            if info.loop_var_field is not None:
                name, payload = info.loop_var_field
                inp.source_form_fields[name] = payload
            if info.borrow_tuple_loop_var is not None:
                inp.borrow_tuple_names.add(info.borrow_tuple_loop_var)
        for name, forms in payloads.items():
            inp.pointer_payloads[name] = (
                forms[0] if len(forms) == 1
                else f"::tpy::const_join_t<{', '.join(forms)}>")
        # A `with ... as t` target joins the same family for the same reason: its
        # payload is spelled from `__enter__()` (`with_enter_t<CM>`) because
        # alias-vs-own is not decidable here either.
        inp.source_form_fields.update(state.with_target_payloads)
        inp.with_targets.update(state.with_target_payloads)
        inp.with_generic_targets.update(state.with_generic_targets)

        bindings: dict[str, FrameLocalLayout] = {}
        for lname, ltype in (func.generator_locals or []):
            kind, payload, effective_type = self._frame_local_form(
                lname, unwrap_ref_type(ltype), inp)
            bindings[lname] = FrameLocalLayout(
                kind=kind,
                # A pointer into const storage needs `const T*`. The binding's
                # const-ness is the frame's own verdict (`const_frame_bindings`,
                # recorded where each binding is classified); only the pointer
                # kinds can carry it in their spelling (a borrow tuple carries
                # it inside its element pointers).
                const=(kind in (FrameLocalKind.PTR_ALIAS,
                                FrameLocalKind.OPT_PTR,
                                FrameLocalKind.REBIND_PTR,
                                FrameLocalKind.BORROW_TUPLE)
                       and lname in state.const_frame_bindings),
                payload=payload,
                effective_type=effective_type)

        state.frame_layout = FrameLayoutPlan(
            bindings=bindings, pointer_tuples=frozenset(inp.pointer_tuples))
        return state.frame_layout

    def _loop_close_fields(
            self, func: TpyFunction,
            body: 'list[TpyStmt]') -> IdentityMap:
        """Per decomposed loop, the fields it releases when control leaves it
        (`THIRLoopSourceRegion`), in release order: the ones whose
        destruction may run user code -- a source generator whose `finally`
        CPython runs as the loop exits, an iterator `__iter__` built.

        None for a loop that lends an element out: a name bound in it may
        still point into the source after the loop, so the source lives on
        with the frame. None for the body's last statement too: leaving it
        ends the body, and the frame keeps its locals to its destruction
        anyway (BUGS.md#finished-frame-keeps-locals), so a release there
        would only add states and a destructor."""
        state = self.state
        holders = {h.name: h for h in state.for_fields}
        out: IdentityMap = IdentityMap()
        for s, info in state.for_loop_info.items():
            if s.is_async or (body and body[-1] is s):
                continue
            released = [h for fn, _ in info.fields
                        if (h := holders.get(fn)) is not None
                        and h.loop is not None and h.drop_runs_user_code]
            if not released or self._loop_lends_element_out(
                    func, released[0].loop.bound_names):
                continue
            out[s] = tuple(reversed([h.name for h in released]))
        return out

    def _prescan_resumable_for_loops(
            self, func: TpyFunction,
            body: list[TpyStmt],
            record_name: str | None = None) -> IdentityMap:
        """Find for-loops whose body contains await OR are `async for`
        (M6). For each, allocate a uid, register frame-field declarations
        and register the loop variable as a hoisted local so it persists
        across suspensions.

        Sync for-with-await (M3.1): two slots,
        `(__for_itr_N: iter_type_t<S>, __for_r_N: iter_result_t<S>)`.

        Async-for (M6): one slot, `__for_itr_N: aiter_type_t<S>`. No `__for_r_N` slot --
        the advance is a Yield(await __anext__()) and the unwrapped
        value goes straight to the loop var via the standard resume-bind
        path. Also populates `state.async_for_struct_names[uid]` with
        the C++ name of the `__anext__` sub-coro struct so the Yield
        emit can size `__sub_<i>` and emplace it.

        Returns {TpyForEach -> uid} for the CFG builder. Frame fields
        live on `state.for_fields` as `[(name, cpp_type)]` consumed
        by gen_coro_struct (see `THIRResumableFrame`).
        """
        state = self.state
        if state.for_prescanned:
            return state.for_uid_map
        # Storage that outlives the frame: the params the frame BORROWS (a
        # reference, a pointer, a capture aliasing an enclosing variable) and
        # the receiver. A param stored in the frame by value (`Own[T]`, a
        # deduced slot, an owned `str` copy) is a sibling field, which a frame
        # move would leave behind. An iterator source reached from a borrowed
        # root can be held by reference across advances.
        # A `val_or_ref_t<T>` slot is caller storage at every reference
        # instantiation, which is what its monomorphic twin is; the value
        # instantiation shares the latent frame-move hazard the `begin_end`
        # iterators into such a field already carry.
        borrowed_kinds = (FrameCaptureKind.REF, FrameCaptureKind.POINTER,
                          FrameCaptureKind.OWNED_TUPLE, FrameCaptureKind.CAPTURE,
                          FrameCaptureKind.TYPE_PARAM)
        pinned_roots = frozenset(
            [pname for pname, ptype in func.params
             if capture_kind(func, pname, ptype, self.deps.is_static_protocol_param)
             in borrowed_kinds]
            + (["self"] if func.is_method else []))
        # A genexpr's deduced source slot has one instantiation, its creation
        # site's; only the nested-def stand-in for an unsettled literal type
        # is open.
        deduced_concrete = (func.is_genexpr
                            and not func.genexpr_container_by_protocol)
        uid_map: IdentityMap = IdentityMap()
        fields_out: list[tuple[str, str]] = []
        struct_names_out: dict[int, str] = {}
        # Same-module frame structs this loop machinery embeds by value; like
        # local_hoists below, committed to the state only after the walk
        # completes so a mid-walk `_CFGNotYetSupported` leaves nothing behind.
        dep_units_out: list[tuple[str, str | None]] = []
        # {TpyForEach -> GeneratorForInfo}: carries pointer_form_loop_var
        # so `setup_resumable_frame_locals` + the struct field emit render a
        # non-value loop var as an aliasing `T*` rather than a `frame_slot<T>`
        # value copy.
        for_loop_info: IdentityMap = IdentityMap()
        info_by_uid: dict[int, GeneratorForInfo] = {}
        counter = [0]
        # Loop-var hoists are accumulated here and applied to
        # func.generator_locals only after the walk completes, so a mid-walk
        # `_CFGNotYetSupported` (an unsupported for-loop shape) leaves no
        # partially-appended loop-var entries behind -- they would emit as
        # duplicate frame fields.
        local_hoists: list[tuple[str, "TpyType"]] = []
        # Non-suspending loops whose FRESH source the frame owns:
        # {TpyForEach -> (field name, its C++ type)}. Separate from `uid_map`,
        # which the CFG builder reads as "decompose this loop".
        src_fields: IdentityMap = IdentityMap()

        def mark_loop_var_const(s: TpyForEach, info: GeneratorForInfo) -> None:
            # A loop var bound into const storage joins the frame's const
            # bindings BEFORE this loop's body is walked, so a nested loop, an
            # alias or a view slot inside it spells itself against the
            # constness its source actually has. The unpack targets ride the
            # same holder -- a `T*` loop var or the proxy-ref borrow tuple
            # (`d.items()`), which carries the const inside its element
            # pointers.
            holder = (info.pointer_form_loop_var
                      or info.borrow_tuple_loop_var
                      or info.opt_ptr_loop_var)
            if holder is not None:
                if info.pointer_form_is_const:
                    mark_frame_const(state, holder,
                                          *info.pointer_form_unpack_targets)
            elif info.loop_var_field is not None and info.source_is_const:
                # The slot's payload is spelled from the const source, so the
                # element it lends is const too.
                mark_frame_const(state, s.var)

        def non_suspending(s: TpyForEach) -> None:
            """A loop whose body does not suspend keeps its block-local
            iterators and every peephole the sync route gives it. The frame
            takes its source only where the source is FRESH and the body can
            bind something out of an element into frame storage that is not
            provably self-contained: a temporary iterable lives as long as
            anything bound from its elements might point into it, and no
            longer.

            Where the frame can name no slot type for such a source (a
            delegated generator in a module that imports this one back),
            the holder stays in the block, as on the sync route.

            This is also the only pass that sees such a loop at all, so the
            element's constness reaches the frame's const bindings from here
            (`docs/PITFALLS.md#const-source-const-loop-var`); that half is
            about the ELEMENT and applies to an lvalue source too."""
            try:
                info = self._analyze_for_strategy(
                    s, counter[0], self.env, pinned_roots=pinned_roots,
                    deduced_source_concrete=deduced_concrete)
            except FrameSourceUnnameable:
                # Discriminated from the strategy analysis by its own type so
                # an unrelated located diagnostic is not swallowed here.
                info = None
            if info is not None:
                mark_loop_var_const(s, info)
            if info is None or not (
                    for_source_is_rvalue(s, self.analyzer)
                    and self._loop_lends_element_out(
                        func, _loop_bound_names(self.analyzer, s))):
                # Existing storage outlives the loop by itself, and a loop
                # that lends nothing out needs nothing kept: either way the
                # holder stays where the sync route puts it, in the block.
                src_fields[s] = None
                return
            src = next((f for f in info.fields
                        if f[0] == f"__for_src_{counter[0]}"), None)
            if src is not None:
                counter[0] += 1
                fields_out.append(src)
                # Only the source is embedded, so only its own emit-ordering
                # deps are.
                dep_units_out.extend(info.dep_units)
            src_fields[s] = src

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                suspends = isinstance(s, TpyForEach) and (
                    s.is_async
                    or _stmts_have_any_suspension(s.body)
                    or _stmts_have_any_suspension(s.orelse))
                if isinstance(s, TpyForEach) and not suspends:
                    non_suspending(s)
                if suspends:
                    cur_uid = counter[0]
                    counter[0] += 1
                    uid_map[s] = cur_uid
                    if s.is_async:
                        # async-for (M6): one `__aiter__` iterator slot; the
                        # advance is a Yield(await __anext__), not a strategy.
                        iter_t = self.env.get_resolved_type(s.iterable)
                        if iter_t is None:
                            raise CodeGenError(
                                "for-loop iterable has no resolved type "
                                "(async for pre-scan)", loc=s.loc)
                        src_cpp = self.deps.type_to_cpp(unwrap_ref_type(iter_t))
                        iter_field_type = f"::tpy::aiter_type_t<{src_cpp}>"
                        fields_out.append(
                            (f"__for_itr_{cur_uid}", iter_field_type))
                        if s.async_aiter_type is None:
                            raise CodeGenError(
                                "async-for missing resolved aiter type "
                                "(internal: sema didn't populate "
                                "stmt.async_aiter_type)", loc=s.loc)
                        struct_names_out[cur_uid] = self._sub_struct_qualname(
                            s.async_aiter_type, "__anext__", loc=s.loc)
                        _anext_dep = same_module_dep_unit(
                            s.async_aiter_type, "__anext__",
                            self.analyzer.ctx.module_name)
                        if _anext_dep is not None:
                            dep_units_out.append(_anext_dep)
                        info = GeneratorForInfo(
                            uid=cur_uid, strategy="async_for", fields=[])
                    else:
                        if (ternary_iterable_lends_storage(
                                    s.iterable,
                                    self.analyzer.registry.get_record,
                                    self.env.get_resolved_type)
                                and any(is_rvalue_source(self.analyzer, arm)
                                        for arm in (s.iterable.then_expr,
                                                    s.iterable.else_expr))):
                            # One arm lends storage the frame must not own and
                            # the other is fresh storage it must: no single
                            # capture holds both. Asked of the ARMS, since the
                            # whole head reads as an lvalue once one arm is.
                            # Two arms that agree are fine -- both lend, or
                            # both are fresh.
                            raise CodeGenError(
                                "iterating a conditional whose arms are an "
                                "existing object and a fresh one is not "
                                "supported: the loop would have to borrow "
                                "and own the same source. Write the loop "
                                "once in each branch of an `if` instead",
                                loc=s.loc)
                        # Sync for-loop: reuse the legacy strategy analysis so
                        # range / begin_end peepholes (a plain counter / begin-
                        # end iterators) carry over instead of the slower
                        # universal `__iter__`/`__next__` shape. The fields it
                        # returns are emitted as `tpy::frame_slot<T>` in
                        # gen_coro_struct (matching hoisted user locals).
                        info = self._analyze_for_strategy(
                            s, cur_uid, self.env,
                            pinned_roots=pinned_roots,
                            deduced_source_concrete=deduced_concrete)
                        if info is None:
                            raise _CFGNotYetSupported(
                                "yield inside this for-loop shape is not yet "
                                "supported on the resumable path.", loc=s.loc)
                        fields_out.extend(info.fields)
                        dep_units_out.extend(info.dep_units)
                    for_loop_info[s] = info
                    info_by_uid[cur_uid] = info
                    mark_loop_var_const(s, info)
                    elem_t = (unwrap_ref_type(s.elem_type)
                              if s.elem_type else None)
                    if elem_t is None:
                        raise CodeGenError(
                            "for-with-suspension: loop var has no "
                            "resolved elem_type", loc=s.loc)
                    local_hoists.append((s.var, elem_t))
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        # Walk succeeded: commit the loop-var hoists.
        if local_hoists:
            if func.generator_locals is None:
                func.generator_locals = []
            existing = {n for n, _ in func.generator_locals}
            for name, elem_t in local_hoists:
                if name not in existing:
                    func.generator_locals.append((name, elem_t))
                    existing.add(name)
        state.frame_dep_units.extend(dep_units_out)
        state.for_uid_map = uid_map
        owners: dict[str, FrameLoopOwner] = {}
        for s, info in for_loop_info.items():
            owner = FrameLoopOwner(s, info, info.src_alias,
                                   _loop_bound_names(self.analyzer, s))
            for fname, _ in info.fields:
                owners[fname] = owner
        for s, held in src_fields.items():
            if held is not None:
                # A kept-whole loop's field is spelled as the alias itself.
                owners[held[0]] = FrameLoopOwner(
                    s, None, held[1], _loop_bound_names(self.analyzer, s))
        state.for_fields = tuple(
            THIRFrameHolder(
                HolderRole.LOOP, n, cpp, loop=owners.get(n),
                drop_runs_user_code=(
                    n not in owners
                    or loop_field_drops(n, owners[n],
                                        self.env.get_resolved_type)))
            for n, cpp in fields_out)
        holder_of = {h.name: h for h in state.for_fields}
        state.async_for_struct_names = struct_names_out
        state.for_loop_info = for_loop_info
        state.for_info_by_uid = info_by_uid
        state.for_src_fields = IdentityMap()
        for s, held in src_fields.items():
            state.for_src_fields[s] = (holder_of[held[0]]
                                       if held is not None else None)
        state.for_prescanned = True
        return uid_map

    def _classify_pointer_alias_locals(self, func: TpyFunction) -> 'set[str]':
        """Frame locals that are borrow ALIASES of existing storage, so their
        frame field must be a `T*` (aliasing the source) rather than an owning
        `frame_slot<T>` (which would copy across the suspension -- a silent
        reference-copy divergence from CPython).

        Three binding shapes, mirroring how sync codegen renders them as
        references (`Box& a = items[0]` / `auto&& a = tuple_elem_ref(...)`):
          - single-assign `a = <lvalue>` of a plain non-value type,
          - tuple-unpack borrow targets (`stmt.is_ref[i]`), and
          - a walrus `(a := <lvalue>)`, which sema already classified as a
            statement-level borrow.

        Const sources (readonly params, const tuple elements) join the frame's
        `const_frame_bindings` so the field is `const T*`; a source whose own
        const verdict is settled later (a loop var, proven const by the for
        prescan that runs after this pass) is reached through the borrow edge
        recorded for the binding. Exception-
        handler bindings are excluded: the caught exception is only live inside
        the handler, so aliasing it across a suspension could dangle -- they
        keep the safe owning copy. Owning bindings (`a = Box(1)`, Own[T]
        elements, value types) also keep the owning frame_slot path.

        Cached on the resumable state; consumed by `gen_coro_struct` (field
        type) and `setup_resumable_frame_locals` (pointer_locals membership).
        Idempotent across the struct + body passes.
        """
        state = self.state
        if state.pointer_alias_prescanned:
            return state.pointer_alias_locals
        state.pointer_alias_prescanned = True
        if not func.generator_locals:
            return state.pointer_alias_locals
        frame_local_types = {n: t for n, t in func.generator_locals}
        aliases = state.pointer_alias_locals
        env = self.env
        body = self._effective_body(func)
        # Read per-function off the analyzer, not off ctx: this prescan runs at
        # struct-emit time, before the body scope that would seed ctx with it.
        analyzer = self.analyzer
        borrow_decls = analyzer.function_stmt_borrow_decls.get(func, {})

        exc_bindings: set[str] = set()

        def collect_exc(stmts: 'list[TpyStmt]') -> None:
            for s in stmts:
                if isinstance(s, TpyTry):
                    for h in s.handlers:
                        if h.binding is not None:
                            exc_bindings.add(h.binding)
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        collect_exc(b)

        collect_exc(body)

        def root_name(expr: 'TpyExpr | None') -> 'str | None':
            # Walk `.obj` (field access / subscript) down to the base name.
            cur = expr
            while cur is not None and not isinstance(cur, TpyName):
                cur = getattr(cur, "obj", None)
            return cur.name if isinstance(cur, TpyName) else None

        def borrows_from(target: str, src: 'TpyExpr | None') -> None:
            # A binding whose storage comes out of `src` is const whenever
            # `src` is, however late that is settled (a loop var's own verdict
            # lands in the for prescan, which runs after this pass). A
            # borrow-declared call's const-ness is its lenders', which sema
            # already folded into the result type -- its receiver may not lend.
            if declared_call_const(self.analyzer, src) is not None:
                return
            record_const_source_edge(state, target, root_name(src))

        def aliases_live_storage(ltype_bare: 'TpyType | None',
                                 init: 'TpyExpr') -> bool:
            # The one rule every binding form takes: a plain reference local
            # bound to an lvalue is a `T*` alias of it, whether or not sema
            # moved a last-use name source -- the frame keeps the source
            # alive, so nothing is copied. A source rooted at an
            # exception-handler binding (`e`, `e.inner`, ...) is handler-
            # scoped and may not survive a suspension stably, so the frame
            # keeps the owning copy; an owned-erased (Own[dyn P]) local moves
            # on binding.
            return (root_name(init) not in exc_bindings
                    and not isinstance(ltype_bare, OwnType)
                    and is_plain_nonvalue(ltype_bare)
                    and not is_rvalue_source(self.analyzer, init))

        def walk(stmts: 'list[TpyStmt]') -> None:
            for s in stmts:
                if isinstance(s, TpyVarDecl):
                    ltype = frame_local_types.get(s.name)
                    # Exclude any source rooted at an exception-handler binding
                    # (`e`, `e.inner`, ...): the caught exception is handler-
                    # scoped and may not survive a suspension stably, so a frame
                    # pointer into it could dangle -- keep the owning copy.
                    src_is_exc = root_name(s.init) in exc_bindings
                    # Owned-erased (Own[dyn P]) locals move on binding --
                    # never a borrow alias of the source.
                    ltype_bare = unwrap_ref_type(ltype) if ltype is not None else None
                    # is_const_storage_source: a field chain rooted at a
                    # const receiver (self.field in a readonly method)
                    # renders const; sync locals pick that up via auto
                    # deduction, but the frame field must spell it. An
                    # element read off a deep-const-verdict param (the
                    # seeded name set) is a const source too -- the frame
                    # field spells the verdict, so the alias must match.
                    def init_is_const(s=s, ltype_bare=ltype_bare):
                        if (env.is_const_indirect(ltype_bare, s.init, s)
                                or env.is_const_storage_source(s.init)):
                            return True
                        src = s.init
                        if isinstance(src, TpyCoerce):
                            src = src.expr
                        if not (isinstance(src, TpySubscript)
                                and isinstance(src.obj, TpyName)
                                and src.obj.name
                                in env.deep_const_borrow_params):
                            return False
                        root_t = env.var_types.get(src.obj.name)
                        return isinstance(
                            unwrap_readonly(unwrap_ref_type(root_t)),
                            TupleType) if root_t is not None else False
                    if (ltype is not None and s.init is not None
                            and aliases_live_storage(ltype_bare, s.init)):
                        aliases.add(s.name)
                        borrows_from(s.name, s.init)
                        if init_is_const():
                            mark_frame_const(state, s.name)
                    elif (ltype is not None and s.init is not None
                            and not src_is_exc
                            and pointer_repr_optional(ltype_bare) is not None):
                        # Pointer-repr Optional locals get their bare `T*`
                        # frame field on their own emission branch (not via
                        # `aliases`); only the const fact is recorded here,
                        # where the initializing decl is in hand. A value
                        # that fills the frame's own slot borrows nothing
                        # from its receiver.
                        if not self.ptr_write_fills_slot(s.init):
                            borrows_from(s.name, s.init)
                        if init_is_const():
                            mark_frame_const(state, s.name)
                elif isinstance(s, TpyMatch):
                    # A capture is bound out of the SUBJECT's storage, so a
                    # const subject makes every pointer-form capture a
                    # `const T*`. The binding is the arm's pattern, not a
                    # VarDecl, so the arm above never sees it -- and the
                    # subject is only in hand here. Captures bound by value
                    # snapshot the element and carry no pointer to qualify.
                    for case in s.cases:
                        for cap in iter_capture_bindings(case.pattern):
                            if cap.bind_by_value:
                                continue
                            cap_t = frame_local_types.get(cap.name)
                            if cap_t is None:
                                continue
                            cap_bare = unwrap_ref_type(cap_t)
                            if (cap.name in aliases
                                    or pointer_repr_optional(cap_bare) is not None):
                                borrows_from(cap.name, s.subject)
                                if env.is_const_storage_source(s.subject):
                                    mark_frame_const(state, cap.name)
                elif isinstance(s, TpyWith) and s.is_async:
                    # An `async with ... as t` whose __aenter__ result is a
                    # borrow (pointer Poll payload): the as-binding must
                    # alias, not copy -- same rule as the await-init
                    # VarDecl arm above. Gated on the item's own
                    # enter_type, not frame_local_types: the with-prescan
                    # appends the as-target to generator_locals only
                    # later, at struct emission.
                    for it in s.items:
                        if (it.target is not None
                                and it.aenter_result_is_borrow
                                and it.enter_type is not None
                                and is_plain_nonvalue(unwrap_ref_type(it.enter_type))):
                            aliases.add(it.target)
                            if it.aenter_result_is_const:
                                mark_frame_const(state, it.target)
                elif isinstance(s, TpyTupleUnpack):
                    # For-loop element unpacks (`idx, it = __for_tup`) already
                    # get pointer-form slots via the loop machinery's
                    # pointer_form_unpack_targets; skip them here so they keep
                    # that path (and the `&std::get` emit) unchanged.
                    src_is_loop_holder = (
                        isinstance(s.value, TpyName)
                        and s.value.name.startswith("__for_tup_"))
                    # A frame `T*` alias is sound only when the source resolves
                    # to storage outliving the frame: a stable lvalue (name /
                    # field, bound by-ref) or a call/method-call returning a
                    # borrow tuple (`std::tuple<T*, ...>` -- pointers to external
                    # storage). A value-tuple rvalue temp (a tuple literal ->
                    # `std::tuple<T, ...>`) would leave the alias pointing into
                    # a destroyed stack temporary across the suspension, so it
                    # falls back to the owning-copy path instead.
                    src_safe_to_alias = (
                        not is_rvalue_source(self.analyzer, s.value)
                        or isinstance(s.value, (TpyCall, TpyMethodCall)))
                    if not src_is_loop_holder and src_safe_to_alias:
                        # The element pointers are taken off the SOURCE, so
                        # they are const exactly when it is -- a borrow-tuple
                        # capture, a const-bound holder, or (the lvalue lift)
                        # a chain rooted at a const frame binding. The lift
                        # spelling in the THIR lowering reads the same answer.
                        src_const = (
                            env.unpack_source_has_const_slots(s)
                            or env.is_const_storage_source(s.value))
                        for i, tname in enumerate(s.targets):
                            if (tname is not None and tname in frame_local_types
                                    and i < len(s.is_ref) and s.is_ref[i]
                                    and is_plain_nonvalue(unwrap_ref_type(frame_local_types[tname]))):
                                aliases.add(tname)
                                borrows_from(tname, s.value)
                                elem_const = src_const or (
                                    i < len(s.is_const_ref) and s.is_const_ref[i])
                                if elem_const:
                                    mark_frame_const(state, tname)
                # A walrus binds in expression position, so it is invisible to
                # the statement arms above; it takes the declaration's rule.
                for ne in walrus_bindings(s):
                    ltype = frame_local_types.get(ne.target)
                    if ltype is None:
                        continue
                    if not aliases_live_storage(unwrap_ref_type(ltype),
                                                ne.value):
                        continue
                    aliases.add(ne.target)
                    borrows_from(ne.target, ne.value)
                    if borrow_decls.get(ne.target, False):
                        mark_frame_const(state, ne.target)
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        return aliases

    def _record_with_target_payloads(self, func: TpyFunction, stmt: TpyWith,
                                     state: 'FrameBuildState') -> None:
        """Record the C++ payload each `as`-target's frame field is spelled
        from: `::tpy::with_enter_t<CM>`, the element form of `CM::__enter__()`.

        Whether the target aliases the manager or owns a fresh value is not
        decidable here -- a generic or inherited manager instantiates either way
        -- so the choice goes to C++, exactly as a for-loop element's does.

        Skipped when the enter type answers the question by itself, leaving the
        plain field its sibling value locals use: a value type has no aliasing
        question; `Own[T]` (which `is_value_type` reports as a value) returns by
        value, so the target owns; and a view-family (`str` / `bytes`) target is
        given owning storage by the `OWNED_STR` arm instead, which a source-form
        payload would override (it is tested first).

        A target the storage policy holds as a pointer over storage of its own
        takes the pointer form at the payload's row (`_frame_local_form`),
        const over a `readonly` result, unless its enter type is a type
        parameter, which only the payload's instantiation can answer.

        Sync `with` only: an `async with` target is bound from `__aenter__`'s Poll
        payload, not from `__enter__` (which such a manager need not even
        define), and its field already takes the aliasing pointer form.
        """
        if stmt.is_async:
            return
        for item in stmt.items:
            if item.target is None or item.enter_type is None:
                continue
            enter_t = unwrap_ref_type(item.enter_type)
            resolved_enter = self.deps.resolve_type(enter_t)
            if (resolved_enter.is_value_type()
                    or is_str_category(resolved_enter)
                    or is_bytes_category(resolved_enter)):
                # `with_owning_str_targets` already claims the whole view family
                # for owning storage, keyed on the str/bytes CATEGORY rather than
                # on what `__enter__` actually lends -- so a `-> StrView` target
                # owns as much as a `-> str` one, and nothing is left to defer.
                continue
            if pointer_repr_optional(resolved_enter) is not None:
                # A pointer-repr Optional answers alias-vs-own by itself (the
                # null pointer doubles as None) and has its own field kind; a
                # source-form payload would win the earlier arm and render
                # `frame_slot<T*>`, whose reads deref one level short.
                continue
            ctx_t = self.env.get_resolved_type(item.context_expr)
            if ctx_t is None:
                continue
            ctx_cpp = self.deps.type_to_cpp(unwrap_ref_type(ctx_t))
            state.with_target_payloads[item.target] = (
                f"::tpy::with_enter_t<{ctx_cpp}>")
            if isinstance(resolved_enter, TypeParamRef):
                state.with_generic_targets.add(item.target)
            elif (item.target in self._rebound_ref_names(func)
                    and isinstance(unwrap_send_sync(enter_t), ReadonlyType)):
                mark_frame_const(state, item.target)

    def _prescan_with_manager_homes(self, func: TpyFunction,
                                    body: list[TpyStmt]) -> None:
        """Give an OWNED manager a frame field when its `as`-target's field points
        INTO it.

        Such a field holds a pointer or view into whatever `__enter__()` returned,
        so the manager has to outlive the FRAME, not the statement. A borrowed
        manager already does -- it aliases a frame-resident local -- but an owned
        one is a per-invocation local, so a read of the target after any later
        suspension would dangle. Only for a non-decomposed region: a decomposed
        one allocates the field regardless.

        Runs after `_prescan_with_stmts` so it can ask the frame-layout PLAN what
        each target's field actually is, rather than re-deriving it from the enter
        type's spelling -- an enumeration of spellings cannot close that set (a
        borrow-form tuple and a `Span` are value types holding pointers), and
        every miss is a use-after-free. The plan never reads this pass's output,
        so building it here is not circular.
        """
        state = self.state
        if state.with_manager_homes_prescanned:
            return
        state.with_manager_homes_prescanned = True
        layout = self._frame_layout(func)
        local_types = dict(func.generator_locals or [])
        counter = [state.with_ctx_counter]
        fields_out: list[THIRFrameHolder] = []

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if (isinstance(s, TpyWith) and not s.is_async
                        and not _stmts_have_any_suspension(s.body)):
                    per_item: list[THIRFrameHolder | None] = []
                    for item in s.items:
                        ctx_t = (self.env.get_resolved_type(item.context_expr)
                                 if item.target is not None
                                 and not item.manager_borrowed
                                 and item.manager_owns_enter_result
                                 and self._with_field_points_into_manager(
                                     item.target, layout, local_types)
                                 else None)
                        if ctx_t is None:
                            per_item.append(None)
                            continue
                        cur_n = counter[0]
                        counter[0] += 1
                        home = THIRFrameHolder(
                            HolderRole.WITH_CONTEXT, f"__with_ctx_{cur_n}",
                            self.deps.type_to_cpp(unwrap_ref_type(ctx_t)))
                        fields_out.append(home)
                        per_item.append(home)
                    if any(n is not None for n in per_item):
                        state.with_owned_ctx_map[s] = per_item
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        state.with_fields = state.with_fields + tuple(fields_out)
        state.with_ctx_counter = counter[0]

    def _with_field_points_into_manager(
            self, target: str, layout: 'FrameLayoutPlan',
            local_types: 'dict[str, TpyType]') -> bool:
        """Does the target's frame field hold a pointer or view INTO the manager?

        Defaults to YES for any kind not provably self-contained: a miss in this
        direction is a use-after-free, while a false positive only keeps the
        manager alive longer than strictly needed.
        """
        verdict = layout.bindings.get(target)
        if verdict is None:
            return False  # not frame-resident: no field to point with
        if verdict.kind in self._SELF_CONTAINED_FRAME_KINDS:
            return False
        if verdict.kind is not FrameLocalKind.VALUE:
            # PTR_ALIAS / OPT_PTR (pointer), BORROW_TUPLE / MIXED_TUPLE_SLOT
            # (pointer elements), SOURCE_FORM_SLOT (alias when the trait picks
            # the borrow form).
            return True
        # A bare VALUE field is self-contained only for the owning value
        # families; the rest (views, `Span`, `Ptr`, a user ValueType holding
        # either) carry a pointer inside a value type.
        ltype = local_types.get(target)
        if ltype is None:
            return True
        t = self.deps.resolve_type(unwrap_ref_type(ltype))
        if is_free_copy_scalar(t) or is_big_int_type(t) or isinstance(t, OwnType):
            return False
        if is_str_category(t) and not is_str_view_type(t):
            return False
        if is_bytes_category(t) and not is_bytes_view_type(t):
            return False
        return True

    def _prescan_resumable_ptr_slots(
            self, func: TpyFunction, body: list[TpyStmt]) -> None:
        """Reserve one `std::optional<T>` frame field per rvalue write into
        a pointer-form frame local (decl init or rebind; a walrus of a
        rebound reference local), and per owned element of a pointer
        tuple's binding or of a rebound reference local's unpack. The
        pointer field outlives every case block, so the materialization slot
        must too -- an inline or hoisted per-call slot dies at the next
        suspension (or the next `__next__` call) while the pointer still
        aims at it.

        Which writes need a slot is decided here once, per (write, element)
        -- None for the scalar: a scalar's is `ptr_slot_field_type`'s call,
        an element's `ptr_tuple_elem_storage`'s. Fields land on
        `state.ptr_slot_fields`; the site map (`stmt -> {element ->
        field}`) on `state.ptr_slot_map`, which the lowering reads
        (`THIRResumableFrame.ptr_slot_field`) and rejects a reseat it finds no field for.
        One field per write sema's alias-rebind verdict does not send in
        place: an alias holding the previous value keeps a live target.
        """
        state = self.state
        if state.ptr_slots_prescanned:
            return
        state.ptr_slots_prescanned = True
        layout = self._frame_layout(func)
        ptr_names = {n for n, v in layout.bindings.items()
                     if v.kind in POINTER_LOCAL_KINDS}
        ptr_tuples = {n: v.effective_type for n, v in layout.bindings.items()
                      if v.kind is FrameLocalKind.BORROW_TUPLE
                      and n in layout.pointer_tuples
                      and v.effective_type is not None}
        if not ptr_names and not ptr_tuples:
            return
        local_types = dict(func.generator_locals or [])
        owned_masks = self.analyzer.ctx.tuple_elem_owned
        elem_verdicts = self.analyzer.ctx.tuple_elem_storage
        fields: list[THIRFrameHolder] = []
        uid_map: IdentityMap = IdentityMap()

        def reserve(stmt: 'TpyStmt | TpyExpr', elem: 'int | None',
                    field_cpp: str, local: str, value: TpyExpr) -> None:
            slot = THIRFrameHolder(HolderRole.PTR_SLOT,
                                   f"__ptr_slot_f{len(fields)}", field_cpp,
                                   local=local, value=value)
            fields.append(slot)
            uid_map.setdefault(stmt, {})[elem] = slot

        def visit_tuple(stmt: TpyStmt, name: str, init: TpyExpr) -> None:
            ptt = ptr_tuples[name]
            # Visited even where every element is written in place: the
            # lowering refuses a binding the prescan never saw.
            uid_map.setdefault(stmt, {})
            mask = owned_masks.get(stmt) or ()
            const = name in state.const_frame_bindings
            for i, et in enumerate(ptt.element_types):
                if not (i < len(mask) and mask[i]):
                    continue
                if ptr_tuple_elem_storage(
                        elem_verdicts.get(stmt), i, et, first=False,
                        const=const) is RebindStorage.IN_PLACE:
                    continue
                pointee = unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(unwrap_own(et))))
                reserve(stmt, i,
                        f"std::optional<{self.deps.type_to_cpp(pointee)}>",
                        name, init)

        def visit(stmt: 'TpyStmt | TpyExpr', name: str,
                  init: 'TpyExpr | None') -> None:
            if init is None:
                return
            if name in ptr_tuples:
                visit_tuple(stmt, name, init)
                return
            if name not in ptr_names:
                return
            # An IN_PLACE rebind assigns through the pointer into the
            # storage it already aims at: no field of its own. The reseat
            # lowering keys on the same verdict.
            if getattr(stmt, "rebind_storage", None) is RebindStorage.IN_PLACE:
                return
            target_type = local_types.get(name)
            inner = (target_type.inner
                     if isinstance(target_type, OptionalType) else target_type)
            cpp_type = (self.deps.type_to_cpp(unwrap_ref_type(inner))
                        if inner is not None else "auto")
            field_cpp = self.ptr_slot_field_type(init, target_type, cpp_type)
            if field_cpp is None:
                return
            reserve(stmt, None, field_cpp, name, init)

        rebind_names = {n for n, v in layout.bindings.items()
                        if v.kind is FrameLocalKind.REBIND_PTR}

        def visit_unpack(stmt: TpyTupleUnpack) -> None:
            # A rebound reference local taking an element over (sema's
            # `is_owned`, the verdict that makes a rebind of it a site) lands
            # it in a field of this site's own, as its assignments do; the
            # site is never stamped IN_PLACE. The lowering binds exactly the
            # targets reserved here, moving each out of the holder.
            for i, name in enumerate(stmt.targets):
                if (name not in rebind_names
                        or not stmt.owns_target(i)):
                    continue
                pointee = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    unwrap_own(stmt.target_types[i]))))
                reserve(stmt, i,
                        f"std::optional<{self.deps.type_to_cpp(pointee)}>",
                        name, stmt.value)

        with_ptr_names = {n for n, v in layout.bindings.items()
                          if v.kind in WITH_POINTER_KINDS}

        def visit_with(stmt: TpyWith, item: TpyWithItem, name: str) -> None:
            # A pointer-form target over a by-value `__enter__` result: the
            # object lands in this item's field, the pointer aims at it.
            if (stmt.is_async or name not in with_ptr_names
                    or not isinstance(item.enter_type, TpyType)
                    or returns_cpp_reference_shape(item.enter_type)):
                return
            pointee = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                unwrap_own(item.enter_type))))
            reserve(item, None,
                    f"std::optional<{self.deps.type_to_cpp(pointee)}>",
                    name, item.context_expr)

        unpacks_seen: IdentityMap = IdentityMap()
        for site in binding_source_sites(body, all_binders=True,
                                         unpack_targets=True):
            if isinstance(site.binder, TpyTupleUnpack):
                if site.binder not in unpacks_seen:
                    unpacks_seen[site.binder] = None
                    visit_unpack(site.binder)
            elif isinstance(site.source, TpyWithItem):
                visit_with(site.stmt, site.source, site.target)
            elif isinstance(site.binder, TpyMatch):
                # A capture aliases the subject (sema refuses its rebind):
                # no object of its own to land.
                continue
            elif not site.is_walrus or site.target in rebind_names:
                visit(site.binder, site.target, site.source)
        state.ptr_slot_fields = tuple(fields)
        state.ptr_slot_map = uid_map

    def _prescan_with_stmts(
            self, func: TpyFunction,
            body: list[TpyStmt]) -> IdentityMap:
        """Find `with` stmts whose body contains await. For each, allocate
        a ctx_n per WithItem, declare a `__with_ctx_<n>` frame field of
        the context-manager's C++ type, and register any `as` target
        names as hoisted locals.

        Returns {TpyWith -> [ctx_n_per_item]}; frame fields land on
        `state.with_fields` as `[(name, cpp_type)]`.
        """
        state = self.state
        if state.with_prescanned:
            return state.with_uid_map
        uid_map: IdentityMap = IdentityMap()
        fields_out: list[tuple[str, str]] = []
        counter = [0]

        # Async-with sub-coro struct names by ctx_n. Populated alongside
        # the per-ctx frame field. Emit uses this to size __sub_<i>
        # slots and to write the inline factory call.
        struct_names_out: dict[int, tuple[str, str]] = {}
        # The `__aenter__`/`__aexit__` units whose structs those slots embed.
        dep_units_out: list[tuple[str, str | None]] = []

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if isinstance(s, TpyWith):
                    # A TARGET's frame field is NOT gated on the region being
                    # decomposed: the target outlives its statement (Python
                    # scoping), so it can be read across a later suspension the
                    # `with` itself does not contain. Record the payload its
                    # field is spelled from for every `with`; the manager field
                    # and state numbering below stay gated.
                    self._record_with_target_payloads(func, s, state)
                    # A DECOMPOSED region needs the manager in the frame anyway
                    # (its states are disjoint). Whether a NON-decomposed one
                    # needs it depends on the target's field kind, which the frame
                    # layout has not decided yet -- see
                    # `_prescan_with_manager_homes`, which runs once this pass has
                    # fed it the payloads it reads.
                # The MANAGER needs a frame field only when the region is
                # decomposed into states: a sync `with` whose body contains an
                # await (M3.2), or any `async with` (its `__aenter__` /
                # `__aexit__` are themselves the suspensions).
                if (isinstance(s, TpyWith)
                        and (s.is_async
                             or _stmts_have_any_suspension(s.body))):
                    per_item: list[int] = []
                    for item in s.items:
                        cur_n = counter[0]
                        counter[0] += 1
                        per_item.append(cur_n)
                        ctx_t = self.env.get_resolved_type(item.context_expr)
                        if ctx_t is None:
                            raise CodeGenError(
                                "with-stmt context manager has no resolved "
                                "type (async with-with-await pre-scan)",
                                loc=s.loc)
                        ctx_cpp = self.deps.type_to_cpp(unwrap_ref_type(ctx_t))
                        fields_out.append((f"__with_ctx_{cur_n}", ctx_cpp))
                        if item.manager_borrowed:
                            state.with_borrowed_fields.add(f"__with_ctx_{cur_n}")
                        # For async-with, compute the qualified C++ names
                        # of the __aenter__ / __aexit__ coro structs so
                        # struct-emit and _emit_suspend can look them up
                        # by ctx_n without needing the types helper at
                        # the CFG layer.
                        if s.is_async:
                            ctx_inner = unwrap_ref_type(ctx_t)
                            if not isinstance(ctx_inner, NominalType):
                                raise CodeGenError(
                                    "async-with context manager type is "
                                    "not a record (internal)",
                                    loc=s.loc)
                            # Build the aenter/aexit sub-coro struct
                            # names using the canonical struct namer
                            # (same shape as M4 async-method coro
                            # structs). The C++ namespace is derived
                            # from the CM's qualified type by
                            # stripping any template args first (so
                            # `::ns::Foo<T>` yields a `::ns::` prefix,
                            # not `::ns::Foo<T>::`). Type args are
                            # re-attached as a suffix on the coro
                            # struct reference -- the struct itself is
                            # templated over the same T as the CM.
                            # Generic CMs are rejected at sema today (see
                            # registration.py); the helper handles type_args
                            # so this prescan stays robust if that lifts.
                            # Name each sub-coro struct from the method's
                            # DEFINING record (sema-stamped; an ancestor when
                            # the manager inherits __aenter__/__aexit__), not
                            # the manager's subclass type -- the struct exists
                            # only for the definer. Falls back to the manager
                            # type for own methods (owner == ctx_inner).
                            aenter_owner = item.aenter_owner_type or ctx_inner
                            aexit_owner = item.aexit_owner_type or ctx_inner
                            struct_names_out[cur_n] = (
                                self._sub_struct_qualname(
                                    aenter_owner, "__aenter__", loc=s.loc),
                                self._sub_struct_qualname(
                                    aexit_owner, "__aexit__", loc=s.loc),
                            )
                            for _dunder, _owner in (("__aenter__", aenter_owner),
                                                    ("__aexit__", aexit_owner)):
                                _dep = same_module_dep_unit(
                                    _owner, _dunder,
                                    self.analyzer.ctx.module_name)
                                if _dep is not None:
                                    dep_units_out.append(_dep)
                        if item.target is not None:
                            enter_t = (unwrap_ref_type(item.enter_type)
                                       if item.enter_type else None)
                            if enter_t is None:
                                raise CodeGenError(
                                    "with-stmt target has no resolved "
                                    "enter_type", loc=s.loc)
                            if func.generator_locals is None:
                                func.generator_locals = []
                            if not any(n == item.target
                                       for n, _ in func.generator_locals):
                                func.generator_locals.append(
                                    (item.target, enter_t))
                            # For str-typed as-targets, the C++
                            # `__enter__()` / `__aenter__()` returns
                            # `std::string` by value but sema's `str`
                            # lowers to `std::string_view`. Tracking
                            # the name here promotes the frame field's
                            # storage type to `std::string` (owning)
                            # so the field doesn't alias a temporary
                            # that dies at the assignment's semicolon.
                            # Applies to both sync `with` and async
                            # `with`: the Poll<std::string>::value()
                            # extraction in async-with's resume case
                            # produces the same temporary shape as the
                            # sync __enter__() return.
                            resolved_enter_t = self.deps.resolve_type(enter_t)
                            if is_str_category(resolved_enter_t):
                                state.with_owning_str_targets.add(item.target)
                    uid_map[s] = per_item
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        state.frame_dep_units.extend(dep_units_out)
        state.with_uid_map = uid_map
        state.with_fields = tuple(
            THIRFrameHolder(HolderRole.WITH_CONTEXT, n, cpp,
                            borrowed=n in state.with_borrowed_fields)
            for n, cpp in fields_out)
        state.with_ctx_counter = counter[0]
        state.async_with_struct_names = struct_names_out
        state.with_prescanned = True
        return uid_map

    def _prescan_resumable_try_finally(
            self, func: TpyFunction,
            body: list[TpyStmt]) -> IdentityMap:
        """Find try-stmts whose finally body contains an await. For
        each, allocate a uid and declare:
          * `__finally_exc_<n>` -- `std::exception_ptr` (always).
          * `__finally_pending_<n>` -- `bool` (only when try / handler /
             finally body contains a reachable `return`).
          * `__finally_ret_<n>` -- function's return type, for the
             same reason and only when the async def is non-void.
        Returns {TpyTry -> uid}; field declarations land on
        `state.try_finally_fields`."""
        state = self.state
        if state.try_finally_prescanned:
            return state.try_finally_uid_map
        uid_map: IdentityMap = IdentityMap()
        fields_out: list[tuple[str, str, str]] = []
        counter = [0]
        is_void = returns_void(func)
        ret_cpp = self.deps.ret_cpp(func) if not is_void else None
        # The pending return is a slot declared before its value.
        ret_init = (placeholder_init(
            unwrap_ref_type(func.return_type), ret_cpp)
            if ret_cpp is not None else "")

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                if (isinstance(s, TpyTry)
                        and s.finally_body
                        and _stmts_have_any_suspension(s.finally_body)):
                    cur_uid = counter[0]
                    counter[0] += 1
                    uid_map[s] = cur_uid
                    fields_out.append(
                        (f"__finally_exc_{cur_uid}", "std::exception_ptr", ""))
                    has_return = (_stmts_have_any_return(s.try_body)
                                   or any(_stmts_have_any_return(h.body)
                                           for h in s.handlers)
                                   or _stmts_have_any_return(s.finally_body))
                    if has_return:
                        fields_out.append(
                            (f"__finally_pending_{cur_uid}", "bool", ""))
                        # Generator pending returns are always StopIteration;
                        # no value slot needed (_ret_cpp would give Iterator[T]).
                        if ret_cpp is not None and not (self.shape is ResumableShape.GENERATOR):
                            fields_out.append(
                                (f"__finally_ret_{cur_uid}", ret_cpp,
                                 ret_init))
                # `async with` desugars to a try/finally where the
                # finally body is `await __cm.__aexit__(...)`. The
                # synthetic finally needs the same set of frame slots
                # as a user-written try/finally-with-await; allocate
                # them in the same shared uid pool here.
                if isinstance(s, TpyWith) and s.is_async:
                    cur_uid = counter[0]
                    counter[0] += 1
                    uid_map[s] = cur_uid
                    fields_out.append(
                        (f"__finally_exc_{cur_uid}", "std::exception_ptr", ""))
                    if _stmts_have_any_return(s.body):
                        fields_out.append(
                            (f"__finally_pending_{cur_uid}", "bool", ""))
                        if ret_cpp is not None:
                            fields_out.append(
                                (f"__finally_ret_{cur_uid}", ret_cpp,
                                 ret_init))
                if hasattr(s, "sub_bodies"):
                    for b in s.sub_bodies():
                        walk(b)

        walk(body)
        state.try_finally_uid_map = uid_map
        state.try_finally_fields = tuple(
            THIRFrameHolder(HolderRole.TRY_FINALLY, n, cpp, init=init)
            for n, cpp, init in fields_out)
        state.try_finally_prescanned = True
        return uid_map

    def _make_await_payload(self, await_node: TpyAwait, host_stmt: TpyStmt,
                             kind, bind_target, return_stmt) -> 'THIRAwaitSuspension':
        """CFGBuilder payload factory: derives mode + sub_field_cpp_type
        from the await's sema annotations."""
        if await_node.awaited_prebuilt_slot is not None:
            # Bound-coroutine handle: poll the handle's own frame slot in
            # place (INLINE flavors for reset/poll; no field, no emplace).
            operand_type = self.get_expr_type(await_node.value)
            inner = unwrap_readonly(unwrap_own(unwrap_ref_type(operand_type)))
            return THIRAwaitSuspension(
                mode=AwaitMode.INLINE,
                sub_field_cpp_type=self.deps.type_to_cpp(inner),
                operand_expr=await_node.value,
                kind=kind,
                bind_target=bind_target,
                return_stmt=return_stmt,
                host_stmt=host_stmt,
                await_node=await_node,
                prebuilt_slot=next(
                    loc for loc in self.frame_locals
                    if loc.name == await_node.awaited_prebuilt_slot),
            )
        dep_unit: 'tuple[str, str | None] | None' = None
        module_qual: str | None = None
        if await_node.awaited_async_func_name is not None:
            mode = AwaitMode.INLINE
            inferred_type_args = getattr(
                await_node.value, "inferred_type_args", None)
            # Cross-module free-function await spelled `mod.func(...)`:
            # operand is a TpyMethodCall whose receiver is the module
            # (no class owner). Use the module qualifier to namespace
            # the sub-coro struct name.
            if (isinstance(await_node.value, TpyMethodCall)
                    and await_node.awaited_method_owner_type is None):
                module_qual = (await_node.value.user_module_call
                               or await_node.value.builtin_module_call)
            elif (isinstance(await_node.value, TpyCall)
                    and await_node.awaited_method_owner_type is None):
                # Bare `from mod import f; await f()`: qualify the coro struct
                # with the resolved function's defining module. originating_module
                # is chain-flattened + shadow-correct (unlike imported_names, which
                # maps the direct import); same module -> None -> bare name.
                fi = getattr(await_node.value, "resolved_function_info", None)
                cur = self.analyzer.ctx.module_name
                if (fi is not None and fi.originating_module is not None
                        and fi.originating_module != cur):
                    module_qual = fi.originating_module
            # The `T_<pname>` args for a static-protocol callee are NOT added
            # here: they must be spelled from the same render the emplace
            # passes to the ctor, and that render is only decidable under the
            # body scope (narrowing, move-out) which does not exist yet at CFG
            # build. Struct emit re-spells the name with them.
            sub_cpp = self._sub_struct_qualname(
                await_node.awaited_method_owner_type,
                await_node.awaited_async_func_name,
                inferred_type_args,
                module_qual=module_qual,
                loc=await_node.loc)
            # Same-module callee only -- a cross-module struct is already
            # complete via its header, and the bare-name unit key would match a
            # same-named local unit. The two forms carry that fact differently:
            # a method's owner type names its defining module, while for a free
            # function `module_qual` is the signal (it is only computed when
            # there is no owner type).
            _owner = await_node.awaited_method_owner_type
            if _owner is not None:
                dep_unit = same_module_dep_unit(
                    _owner, await_node.awaited_async_func_name,
                    self.analyzer.ctx.module_name)
            elif module_qual is None:
                dep_unit = (await_node.awaited_async_func_name, None)
        elif await_node.awaited_task_inner is not None:
            operand_type = self.get_expr_type(await_node.value)
            if operand_type is None:
                raise CodeGenError(
                    "await operand has no analyzed type",
                    loc=host_stmt.loc)
            operand_inner = unwrap_own(unwrap_ref_type(operand_type))
            sub_cpp = self.deps.type_to_cpp(operand_inner)
            if operand_inner.is_value_type():
                mode = AwaitMode.ERASED
            elif is_stable_address_lvalue(await_node.value):
                mode = AwaitMode.BORROWED
            else:
                mode = AwaitMode.ERASED
        else:
            raise CodeGenError(
                "await reached codegen without sema-resolved sub-future shape",
                loc=host_stmt.loc)
        return THIRAwaitSuspension(
            mode=mode,
            sub_field_cpp_type=sub_cpp,
            operand_expr=await_node.value,
            kind=kind,
            bind_target=bind_target,
            return_stmt=return_stmt,
            host_stmt=host_stmt,
            await_node=await_node,
            dep_unit=dep_unit,
            sub_struct_module_qual=module_qual,
            **await_operand_slots(mode, await_node.value),
        )

    def _analyze_for_strategy(self, stmt: TpyForEach, uid: int,
                              env: FrameBuildEnv,
                              pinned_roots: frozenset[str] = frozenset(),
                              deduced_source_concrete: bool = False
                              ) -> GeneratorForInfo | None:
        """Determine the iteration strategy and struct fields for a for-loop
        with yield.

        Every field of the loop is spelled off ONE alias, `__for_src_<uid>_t`,
        which the struct emit defines as `decltype((<source render>))` -- the
        source expression's own C++ type, reference and const included (an
        rvalue's is stripped to the value the frame owns). A `T&`-returning
        method on a const receiver, a view of a readonly dict, a conditional
        of two const params, a lazy combinator, a generator expression: each
        spells its iterator and result slots through that alias without this
        analysis naming the type, so it cannot disagree with what the setup
        actually stores. What IS decided here: the strategy, which fields
        exist, whether the frame holds the source, and the loop var's form.

        `pinned_roots` are the names (params, captures, `self`) whose storage
        outlives the frame. An lvalue ITERATOR source rooted at one of them
        is held by reference in the frame so it is evaluated once.
        `deduced_source_concrete` says a protocol-typed source is a genexpr's
        deduced slot, instantiated by its one creation site -- not an open
        protocol param that may be driven at a container of value tuples.
        """

        elem_type = stmt.elem_type
        if elem_type and isinstance(elem_type, IntLiteralType):
            elem_type = self.analyzer.ctx.default_int_type
        alias = f"__for_src_{uid}_t"
        # Sema's answer for "is this source const here", recorded for the loop
        # var's BINDING (the frame's const bindings, which the rest of the body
        # reads); the loop's own fields carry their const through the alias.
        src_is_const = env.is_const_storage_source(stmt.iterable)
        holds_source = for_source_is_rvalue(stmt, self.analyzer)

        # Range counter optimization
        if isinstance(stmt.iterable, TpyCall) and stmt.iterable.func_name == "range":
            elem_cpp = self.deps.type_to_cpp(elem_type) if elem_type else "int32_t"
            fields: list[tuple[str, str]] = [
                (f"__for_i_{uid}", elem_cpp),
                (f"__for_stop_{uid}", elem_cpp),
            ]
            nargs = len(stmt.iterable.args)
            if nargs == 3:
                fields.append((f"__for_step_{uid}", elem_cpp))
            return GeneratorForInfo(uid=uid, strategy="range", fields=fields)

        # A narrowed value-Optional iterable (`str|None`/`bytes|None`) is still
        # `std::optional<V>` in the frame -- dispatch on the contained `V` (the
        # source render derefs `(*v)` to match).
        iterable_type = self.narrowed_value_optional_iter_type(stmt.iterable, env.get_resolved_type(stmt.iterable))

        # The source IS the iterator: `typing.Iterator` (a generator frame, a
        # combinator, a protocol param) or a user type whose `__next__` is
        # `@error_return`.
        is_iterator = (is_protocol_type(iterable_type)
                       and iterable_type.qualified_name() == "typing.Iterator")
        if is_iterator or get_error_return_next_element_type(
                iterable_type, registry=self.analyzer.registry) is not None:
            fields: list[tuple[str, str]] = []
            if holds_source or self._lvalue_source_pinned(stmt, pinned_roots):
                # The source iterator lives in the frame -- owned when fresh,
                # by reference when it is existing storage reached through a
                # call: re-rendering the call per advance would run it once
                # per element. The frame struct it embeds must be complete.
                self._check_delegated_source(stmt)
                fields.append((f"__for_src_{uid}", alias))
                holds_source = True
            fields.append((f"__for_r_{uid}", f"::tpy::iter_next_t<{alias}>"))
            step_elem = f"::tpy::step_elem_t<::tpy::iter_next_t<{alias}>>"
            # Non-value elements alias the producer's live yield slot (T*),
            # mirroring begin_end's pointer-form loop var -- a frame_slot
            # copy would hide loop-var mutations from the source elements.
            # An unpack head aliases its reference members the same way.
            pointer_payloads = self._step_unpack_payloads(stmt, elem_type,
                                                          step_elem)
            pointer_form_var = (
                stmt.var
                if (not stmt.is_tuple_unpack
                    and elem_wants_borrow_form(elem_type))
                else None
            )
            if pointer_form_var is not None:
                pointer_payloads[pointer_form_var] = step_elem
            elif pointer_payloads:
                pointer_form_var = stmt.var
            return GeneratorForInfo(
                uid=uid, strategy="next", fields=fields, src_alias=alias,
                source_is_const=src_is_const,
                pointer_form_loop_var=pointer_form_var,
                pointer_form_is_const=isinstance(
                    unwrap_ref_type(elem_type) if elem_type else None,
                    ReadonlyType),
                pointer_form_unpack_targets=frozenset(
                    n for n in pointer_payloads if n != stmt.var),
                pointer_payloads=pointer_payloads)

        # Built-in NativeIterable containers (list, dict, set, Array, Span, str) -- begin/end
        record = self.analyzer.registry.get_record_for_type(iterable_type)
        is_builtin_ni = (record is not None and record.is_native
                         and builtin_modules.is_native_iterable(iterable_type, registry=self.analyzer.registry))
        native_elem = builtin_modules.get_iterable_element_type(iterable_type, registry=self.analyzer.registry) if is_builtin_ni else None
        if native_elem is not None:
            iter_type = f"::tpy::begin_iter_t<{alias}>"
            fields = [
                (f"__for_it_{uid}", iter_type),
                (f"__for_end_{uid}", iter_type),
            ]
            # A fresh source needs a frame field; existing storage (a name, a
            # field path, a reference-returning call) is borrowed instead --
            # copying it into the frame would hide loop-var mutations from the
            # source (CPython aliasing semantics).
            if holds_source:
                fields.insert(0, (f"__for_src_{uid}", alias))
            # Non-value element type with stable lvalue source: the loop var
            # becomes T* (aliasing the container element) instead of
            # std::optional<T> (value-copy). Preserves CPython aliasing
            # semantics across yield/resume.
            # `&(*iter)` is a `const T*` whenever the source iterates const --
            # a const-rooted container or a `readonly[E]` element -- so the
            # slot takes the const spelling rather than falling back to a
            # value copy.
            elem_for_form = unwrap_ref_type(native_elem) if native_elem else None
            # Proxy-ref tuple iterators (dict_items): `&(*it)` is ill-formed
            # on the prvalue proxy, so the loop element binds as a borrow-form
            # tuple lent element by element instead of a `T*` to the
            # element.
            yields_proxy = iter_yields_ref_tuple_proxies(iterable_type)
            pointer_form_targets: frozenset[str] = frozenset()
            target_payloads: dict[str, str] = {}
            opt_ptr_var: str | None = None
            if (stmt.is_tuple_unpack and isinstance(elem_for_form, TupleType)
                    and stmt.body and isinstance(stmt.body[0], TpyTupleUnpack)):
                # Tuple-unpack over a stable lvalue container: alias the
                # reference members so mutating an unpacked record propagates
                # to the source element (CPython semantics, matching the plain
                # pointer-form loop var). `__for_tup` becomes T* and the
                # aliased targets become T* too via `&std::get<i>(...)`; a
                # holder with nothing to alias stays a value copy.
                aliased = self._unpack_alias_members(stmt, elem_for_form)
                pointer_form_var = (stmt.var
                                    if aliased and not yields_proxy else None)
                pointer_form_targets = frozenset(aliased.values())
                if pointer_form_var is not None:
                    target_payloads = {
                        tname: f"std::tuple_element_t<{i}, "
                               f"::tpy::begin_elem_t<{alias}>>"
                        for i, tname in aliased.items()}
            else:
                # A pointer-repr `Optional[T]` element is NOT a `T*` to the
                # container's slot: its loop var is the nullable `T*` every
                # other position spells (`optional_to_ptr` at the advance), so
                # a `None` test on it asks the element, not the slot's address.
                opt_ptr_var = (
                    stmt.var
                    if (not yields_proxy
                        and pointer_repr_optional(elem_for_form) is not None)
                    else None)
                pointer_form_var = (
                    stmt.var
                    if (not yields_proxy and opt_ptr_var is None
                        and (elem_wants_borrow_form(native_elem)
                             or whole_tuple_loop_var_aliases(native_elem)))
                    else None
                )
            borrow_tuple_var = (
                stmt.var
                if (yields_proxy and isinstance(elem_for_form, TupleType)
                    and elem_for_form.has_pointer_repr_element())
                else None)
            pointer_payloads = dict(target_payloads)
            if pointer_form_var is not None:
                pointer_payloads[pointer_form_var] = (
                    f"::tpy::begin_elem_t<{alias}>")
            return GeneratorForInfo(
                uid=uid, strategy="begin_end", fields=fields, src_alias=alias,
                source_is_const=src_is_const,
                pointer_form_loop_var=pointer_form_var,
                pointer_form_is_const=(src_is_const
                                       or isinstance(elem_for_form,
                                                     ReadonlyType)),
                pointer_form_unpack_targets=pointer_form_targets,
                pointer_payloads=pointer_payloads,
                borrow_tuple_loop_var=borrow_tuple_var,
                opt_ptr_loop_var=opt_ptr_var,
            )

        # Universal default: ::tpy::__iter__() + __next__() loop.
        # Handles Iterable[T]/NativeIterable[T] protocol params, user types
        # with __iter__(), and any remaining iterable types.
        fields = [
            (f"__for_itr_{uid}", f"::tpy::iter_type_t<{alias}>"),
            (f"__for_r_{uid}", f"::tpy::iter_result_t<{alias}>"),
        ]
        if holds_source or self._lvalue_source_pinned(stmt, pinned_roots):
            # `tpy::__iter__` borrows its argument, so a fresh source is stored
            # in the frame first or the iterator dangles; existing storage
            # reached through a call is held by reference, so the call runs
            # once rather than at every advance of a self-iterator.
            fields.insert(0, (f"__for_src_{uid}", alias))
            holds_source = True
        # The source's `__next__` may lend an element or hand back a fresh one,
        # and for a protocol-typed or generic source that is settled only at
        # instantiation -- so the field's payload comes from the trait, not from
        # the TPy element type. A CONCRETE value element opts out: copy is the
        # only correct storage for it, so it keeps the plain field its sibling
        # value locals use rather than paying a slot for a choice that is not
        # open. A tuple-unpack loop keeps its own target classification, which
        # is a separate axis.
        #
        # A tuple-unpack head whose tuple has reference members points the
        # HOLDER at the step result in the frame's own result slot, and its
        # reference targets alias the members through it (`p =
        # &std::get<0>(*__for_tup)`). The result stays there until the next
        # advance, so the alias holds for the iteration, and nothing is copied
        # to take it -- a proxy tuple of references (`zip`, `enumerate`,
        # `dict.items()`) has no value form to copy into. Not for an OPEN
        # protocol-typed source: it may instantiate at a native container of
        # value tuples, whose adaptor hands back a COPY of the element, and a
        # mutation through the target would then miss the source silently.
        elem_bare = unwrap_ref_type(elem_type) if elem_type is not None else None
        source_open = (
            self.deps.is_static_protocol_param(iterable_type)
            and not deduced_source_concrete)
        pointer_payloads: dict[str, str] = (
            {} if source_open else self._step_unpack_payloads(
                stmt, elem_type, f"::tpy::for_step_elem_t<{alias}>"))
        aliased_targets = frozenset(n for n in pointer_payloads
                                    if n != stmt.var)
        # A pointer-repr `Optional[T]` element is the nullable `T*` here as
        # under `begin_end`: the step result stays in the frame's result slot
        # until the next advance, lent or fresh, so the pointer holds for the
        # iteration.
        elem_opt = unwrap_readonly(elem_bare) if elem_bare is not None else None
        opt_ptr_var = (
            stmt.var if (not stmt.is_tuple_unpack
                         and pointer_repr_optional(elem_opt) is not None)
            else None)
        loop_var_field = (
            None if (stmt.is_tuple_unpack or opt_ptr_var is not None
                     or elem_is_known_value(elem_type))
            else (stmt.var, f"::tpy::for_elem_next_t<{alias}>"))
        return GeneratorForInfo(uid=uid, strategy="iter_next", fields=fields,
                                src_alias=alias,
                                loop_var_field=loop_var_field,
                                opt_ptr_loop_var=opt_ptr_var,
                                pointer_form_is_const=(
                                    src_is_const
                                    or isinstance(elem_bare, ReadonlyType)),
                                pointer_form_loop_var=(
                                    stmt.var if aliased_targets else None),
                                pointer_payloads=pointer_payloads,
                                pointer_form_unpack_targets=aliased_targets,
                                source_is_const=src_is_const,
                                dep_units=self._iter_source_dep_units(
                                    iterable_type))

    @staticmethod
    def _unpack_alias_members(stmt: TpyForEach,
                              elem_bare: 'TpyType | None') -> dict[int, str]:
        """The members a tuple-unpack head ALIASES, by tuple index -> target
        name: the plain reference members, and the ones the head owns
        (`is_owned`: the step hands them over and holds them until the next
        advance, as the sync head's reference into the step). A value or
        `readonly` member is copied, and an Optional member takes its own
        pointer-repr path (`T* = nullptr`, `optional_to_ptr`). Empty for a
        non-unpack loop."""
        if not (stmt.is_tuple_unpack and isinstance(elem_bare, TupleType)
                and stmt.body and isinstance(stmt.body[0], TpyTupleUnpack)):
            return {}
        head = stmt.body[0]
        return {
            i: tname
            for i, (tname, etype) in enumerate(zip(
                head.targets, elem_bare.element_types))
            if tname is not None
            and (not unwrap_ref_type(etype).is_value_type()
                 or head.owns_target(i))
            and not isinstance(unwrap_own(unwrap_ref_type(etype)),
                               (ReadonlyType, OptionalType))}

    def _step_unpack_payloads(self, stmt: TpyForEach,
                              elem_type: 'TpyType | None',
                              step_elem: str) -> dict[str, str]:
        """Pointee spellings for a tuple-unpack head over an ITERATOR
        source: the holder points at the step result in the frame's own
        result slot (`step_elem`), and each aliased member's target points
        through it. The result stays there until the next advance, so the
        alias holds for the iteration, and nothing is copied to take it -- a
        proxy tuple of references (`zip`, `enumerate`, `dict.items()`) has
        no value form to copy into."""
        elem_bare = unwrap_ref_type(elem_type) if elem_type is not None else None
        payloads = {
            tname: f"::tpy::step_elem_member_t<{i}, {step_elem}>"
            for i, tname in self._unpack_alias_members(stmt, elem_bare).items()}
        if payloads:
            payloads[stmt.var] = step_elem
        return payloads

    def _lvalue_source_pinned(self, stmt: TpyForEach,
                              pinned_roots: frozenset[str]) -> bool:
        """An lvalue ITERATOR source the frame holds by reference: a method
        call (which may be impure, so it must run once) whose receiver chain
        is rooted at storage that outlives the frame. A chain rooted at a
        frame local of the frame's own is not held and keeps re-rendering the
        call (`BUGS.md#frame-iter-next-source-reevaluated`).
        """
        e = peel_coerce(stmt.iterable)
        if not isinstance(e, TpyMethodCall):
            return False
        # Every hop below the root must LEND: a method returning by value
        # mints a temporary the reference would dangle into. A getter's
        # convention is the property predicate's (a storage-ref return, an
        # open `T` settled at the instantiation).
        analyzer = self.analyzer
        root: TpyExpr = e
        while isinstance(root, (TpyMethodCall, TpyFieldAccess, TpySubscript)):
            if isinstance(root, TpyMethodCall) and not (
                    property_access_returns_cpp_ref(analyzer, root)
                    or call_returns_cpp_ref(analyzer,
                                            root.resolved_function_info)):
                return False
            root = root.obj
        return isinstance(root, TpyName) and root.name in pinned_roots

    def _iter_source_dep_units(
            self, iterable_type: 'TpyType | None',
    ) -> tuple[tuple[str, str | None], ...]:
        """The `("__iter__", record)` emit-ordering dep for an `iter_next`
        source, or empty when the source has no same-module generator
        `__iter__`.

        A protocol-typed source resolves to no record and yields nothing; its
        field renders against a deduced template arg, so completeness is
        settled at instantiation rather than at the field declaration.

        The same-module test is the OWNER'S module, not membership in
        `same_module_generators`: that map is keyed on the bare
        `(method, record)` pair, so a cross-module type whose name collides
        with a local record matches the local entry and fabricates a cycle.
        """
        inner = unwrap_readonly(unwrap_own(unwrap_ref_type(iterable_type)))
        dep = same_module_dep_unit(inner, "__iter__",
                                   self.analyzer.ctx.module_name)
        if dep is None or dep not in self.deps.same_module_generators:
            return ()
        return (dep,)

    @staticmethod
    def _module_generator(module: str, name: str,
                          owner_record: str | None) -> TpyFunction | None:
        """The `TpyFunction` for a generator defined in `module`, or None
        when the name does not resolve there or is not a generator. Every
        module is fully analyzed before any of them emits, so a callee's
        AST is available whatever the emit order."""
        compiler = get_current_compiler()
        compiled = compiler.modules.get(module) if compiler else None
        if compiled is None:
            return None
        mod_ast = compiled.ast
        if owner_record is not None:
            for record in mod_ast.all_records():
                if record.name != owner_record:
                    continue
                for m in record.methods:
                    if m.name == name and m.is_generator:
                        return m
            return None
        for f in mod_ast.functions:
            if f.name == name and f.is_generator:
                return f
        return None

    def _resolve_generator_call(
            self, expr: TpyExpr,
    ) -> '_DelegatedGenerator | None':
        """Resolve a direct call to a generator whose frame a `__for_src`
        field can embed by value, wherever that generator is defined. None
        for any other shape (non-call expression, unknown callee, a call
        through a variable).

        The same-module map is consulted first because it is the pre-scan's
        own view of this module; anything else is resolved through the
        binding that named the callee, which is what makes the callee's
        defining module -- and so its C++ namespace -- known.
        """
        e = expr
        while isinstance(e, TpyCoerce):
            e = e.expr
        cur = self.analyzer.ctx.module_name
        if isinstance(e, TpyCall) and isinstance(e.func, TpyName):
            f = self.deps.same_module_generators.get((e.func_name, None))
            if f is not None:
                return _DelegatedGenerator(f, None, None, cur, e)
            # `from m import g; ... for x in g():` -- the attribute table's
            # binding is chain-flattened and shadow-correct, and carries the
            # name the callee is defined under in its own module.
            qual = lookup_imported(self.analyzer.ctx.module_attributes,
                                   e.func_name, SymbolKind.FUNCTION)
            if qual is None or qual[0] == cur:
                return None
            f = self._module_generator(qual[0], qual[1], None)
            return (_DelegatedGenerator(f, None, qual[0], qual[0], e)
                    if f is not None else None)
        if isinstance(e, TpyMethodCall):
            mod = e.user_module_call or e.builtin_module_call
            if mod is not None:
                # `import m; ... for x in m.g():` -- a free function reached
                # through the module object, not a method.
                f = self._module_generator(mod, e.method, None)
                if f is None:
                    return None
                return _DelegatedGenerator(
                    f, None, None if mod == cur else mod, mod, e)
            recv = unwrap_ref_type(self.env.get_resolved_type(e.obj))
            owner_name = getattr(recv, "name", None)
            if owner_name is None:
                return None
            f = self.deps.same_module_generators.get((e.method, owner_name))
            if f is not None:
                return _DelegatedGenerator(f, recv, None, cur, e)
            qname = recv.qualified_name() if isinstance(recv, NominalType) \
                else None
            if not qname or "." not in qname:
                return None
            owner_mod = qname.rsplit(".", 1)[0]
            if owner_mod == cur:
                return None
            f = self._module_generator(owner_mod, e.method, owner_name)
            return (_DelegatedGenerator(f, recv, None, owner_mod, e)
                    if f is not None else None)
        return None

    def _check_delegated_source(self, stmt: TpyForEach) -> None:
        """Reject a `typing.Iterator` source the frame cannot hold: a direct
        call to a generator defined in a module that imports this one back.
        Its frame would be embedded by value in this one and vice versa, so
        neither header can complete the other's struct (mutually
        infinite-size). Any other source needs no check here: the field's
        type is the source expression's own, deduced by C++."""
        resolved = self._resolve_generator_call(stmt.iterable)
        if resolved is None:
            return
        # `cycle_peers` includes this module itself, so the membership test
        # only means "mutually infinite-size" for a callee defined elsewhere.
        if (resolved.defining_module != self.analyzer.ctx.module_name
                and resolved.defining_module in self.deps.cycle_peers):
            raise recursive_delegation_error(resolved.func.name, loc=stmt.loc)

    def _for_src_generator_targets(
            self, func: TpyFunction) -> list[tuple[str, str | None]]:
        """(name, owner_record) of same-module generator callees whose struct
        `func`'s resumable frame embeds by value via a `__for_src` field --
        emit-ordering dependencies (the embedded struct must be complete
        first)."""
        state = self.state
        targets: list[tuple[str, str | None]] = []

        def frame_holds_source(s: TpyForEach) -> bool:
            info = state.for_loop_info.get(s)
            if info is not None:
                return any(n.startswith("__for_src_") for n, _ in info.fields)
            return state.for_src_fields.get(s) is not None

        def walk(stmts: list[TpyStmt]) -> None:
            for s in stmts:
                # Whether the loop SUSPENDS does not enter it; whether the
                # frame ends up HOLDING the source does. A loop whose holder
                # stays in its block names no field, so it orders nothing.
                if (isinstance(s, TpyForEach) and not s.is_async
                        and frame_holds_source(s)):
                    src = peel_coerce(s.iterable)
                    if (isinstance(src, TpyGeneratorExpression)
                            and src.frame_func is not None):
                        # A genexpr's frame is a unit of this module too.
                        targets.append((src.frame_func.name, None))
                    t = self.env.get_resolved_type(s.iterable)
                    if (t is not None and is_protocol_type(t)
                            and t.qualified_name() == "typing.Iterator"):
                        r = self._resolve_generator_call(s.iterable)
                        # Same-module only: the keys are bare `(name, owner)`
                        # pairs, so a cross-module callee would match a
                        # same-named local unit and fabricate an edge -- and
                        # it needs no ordering (its header is complete).
                        if (r is not None and r.defining_module ==
                                self.analyzer.ctx.module_name):
                            owner = r.owner.name if r.owner is not None \
                                else None
                            targets.append((r.func.name, owner))
                for b in (s.sub_bodies() if hasattr(s, "sub_bodies") else ()):
                    walk(b)

        # The prescan's verdicts are keyed on the nodes of the await-lifted
        # body, so the walk must see those same nodes.
        walk(state.lifted_body or func.body)
        return targets

    def frame_pointer_tuples(
            self, func: 'TpyFunction') -> dict[str, 'TpyType | None']:
        """The frame locals sema names pointer tuples, each with the form
        the lowering decided (`ptr_tuple_decision`; None: one C++ cannot
        spell, which the binding planner refuses)."""
        out: dict[str, 'TpyType | None'] = {}
        for lname, _ in (func.generator_locals or []):
            got = ptr_tuple_decision(self.analyzer, func, lname)
            if got is not None:
                out[lname] = got.form
        return out

    def owning_generator_tuple_locals(
            self, func: 'TpyFunction') -> dict[str, 'TpyType | None']:
        """Generator/async frame locals that OWN tuple element storage, mapped
        to the EFFECTIVE tuple type when it differs from the declared one.

        A frame field for such a local is `frame_slot<std::tuple<..., T>>`, not
        the borrow-form `std::tuple<..., T*>`: a borrow field can't hold owned
        elements and the owning rvalue can't be address-taken into it. Aliasing
        tuple locals (every element bound from a storage lvalue) stay borrow
        form.

        Two sources qualify:
          - typed as a tuple with an `OWN` element (e.g. an await-result lift
            temp `tuple[Own[socket], ...]`). `is_value_type()` reports such a
            tuple True, so without this it would take the raw-field path and
            make the frame default ctor ill-formed (the owned element by value
            is not default-constructible);
          - an init that hands the frame element storage: a tuple LITERAL that
            VALUE-captures a reference element (`t = (A(1), 2)` -- a fresh
            rvalue no one else owns, so a borrow field would dangle at the end
            of the statement), or an owning call (`t = make_pair()`, whose
            result is equally the frame's).

        Ownership is decided PER ELEMENT by sema for each init
        (`tuple_elem_owned`) and joined here across every init of the name:
        element i is owned iff every init hands it over. The verdict is
        expressed as an effective TupleType with the owned reference
        elements wrapped in `Own[...]`, so
        `is_owned_movable` / `is_mixed_own` / `tuple_borrow_cpp` route it into
        the frame kinds that already exist. An element one init hands over and
        another only borrows has no single form and is rejected at the decl --
        the borrow would have to point into the owning init's dead temporary.
        """
        one_shot = self.state.one_shot_lift_names
        scan = self.analyzer.function_scan_results.get(func)
        reassigned = scan.reassigned if scan is not None else set()
        # A pointer tuple's elements own per site, never in the field.
        pointer_tuples = self.frame_pointer_tuples(func)
        gen_types = {n: t for n, t in (func.generator_locals or [])
                     if n not in pointer_tuples}
        owning: dict[str, 'TpyType | None'] = {}

        # Type/provenance-driven owning tuple locals:
        #  - a tuple with an OWN element held by value owns its storage
        #    regardless of how it is bound;
        #  - an `__await_lift_*` temp is ALWAYS an owned result, so even a
        #    reference-element tuple (e.g. `tuple[list[T], int]`) needs owning
        #    `frame_slot<std::tuple<...>>` rather than the borrow-form
        #    `std::tuple<T*, ...>` field a non-await borrow tuple would get --
        #    the owned result can't be assigned into a pointer-element field.
        for lname, ltype in gen_types.items():
            inner = unwrap_ref_type(ltype)
            if not (isinstance(inner, TupleType) and lname not in reassigned):
                continue
            if (inner.needs_frame_slot()
                    or (lname in one_shot and inner.has_pointer_repr_element())):
                owning[lname] = None

        # Per-name join of the per-element ownership each init implies. A
        # missing entry means "no init said anything", which is the borrow
        # default.
        elem_owned: dict[str, list[bool]] = {}
        # An element ANY init wants owned: the join alone cannot tell "no init
        # owned it" (plain borrow, fine) from "one did and another did not"
        # (no single form -- the diagnostic below).
        elem_wanted: dict[str, list[bool]] = {}
        conflicts: dict[str, TpyStmt] = {}

        def record(name: str | None, binding: object,
                   at: 'TpyStmt | None') -> None:
            """Join one init's per-element ownership (sema's
            `tuple_elem_owned` for the binding) into the name's verdict."""
            ltype = gen_types.get(name) if name is not None else None
            inner = unwrap_ref_type(ltype) if ltype is not None else None
            if name is None or not isinstance(inner, TupleType):
                return
            n = len(inner.element_types)
            owned = self.analyzer.ctx.tuple_elem_owned.get(binding) or ()
            verdict = list(owned[:n]) + [False] * (n - len(owned[:n]))
            prev = elem_owned.get(name)
            if prev is None:
                elem_owned[name] = verdict
                elem_wanted[name] = list(verdict)
                return
            want = elem_wanted[name]
            for i, (a, b) in enumerate(zip(prev, verdict)):
                if a != b and at is not None:
                    conflicts.setdefault(name, at)
                prev[i] = a and b
                want[i] = want[i] or b

        def visit(stmts: list[TpyStmt]) -> None:
            for stmt in stmts:
                if isinstance(stmt, TpyVarDecl) and stmt.init is not None:
                    record(stmt.name, stmt, stmt)
                elif (isinstance(stmt, TpyAssign)
                      and isinstance(stmt.target, TpyName)):
                    record(stmt.target.name, stmt, stmt)
                # A walrus binds in expression position: without this row its
                # owning-call source is invisible here and the frame gets a
                # borrow-form tuple field aliasing the dead result temporary.
                for ne in walrus_bindings(stmt):
                    record(ne.target, ne, stmt)
                for body in stmt.sub_bodies():
                    visit(body)

        visit(func.body)
        for lname, flags in elem_owned.items():
            if not any(elem_wanted[lname]):
                continue
            if lname in conflicts:
                at = conflicts[lname]
                raise CodeGenError(
                    f"'{lname}' is bound both to a tuple holding a fresh "
                    f"object and to one borrowing an existing object, and "
                    f"lives across a suspension; the two need different "
                    f"storage. Bind the two forms to separate names",
                    loc=getattr(at, "loc", None))
            inner = unwrap_ref_type(gen_types[lname])
            eff = TupleType(tuple(
                OwnType(et) if own and not isinstance(et, OwnType) else et
                for et, own in zip(inner.element_types, flags)))
            owning[lname] = eff
        return owning

    def ptr_write_fills_slot(self, init: 'TpyExpr') -> bool:
        """Whether a write of `init` into a pointer-form frame local
        materializes storage of its own (a frame slot the pointer then aims
        at), rather than aliasing storage that already exists (None literal,
        alias, optional_to_ptr lift forms, a borrowed `T*`). The routing
        order mirrors the emit arms."""
        if isinstance(init, TpyNoneLiteral):
            return False
        if call_value_optional(init) is not None:
            return True
        # A name never fills a slot: every arm below answers False for one
        # (a name is no rvalue), so the body emitter's name-keyed lift
        # tables need not be asked.
        if isinstance(init, TpyName):
            return False
        if pointer_repr_optional(self.get_expr_type(init)) is not None:
            if (isinstance(init, (TpyFieldAccess, TpySubscript))
                    and reads_storage_form_optional(self.analyzer, init)
                    and not is_rvalue_source(self.analyzer, init)):
                return False
            if not is_value_emit_rvalue(self.analyzer, init):
                return False
        return is_rvalue_source(self.analyzer, init)

    def ptr_slot_field_type(self, init: 'TpyExpr',
                            target_type: TpyType | None,
                            cpp_type: str) -> str | None:
        """The frame-field C++ type an rvalue write into a pointer-form
        frame local needs for its materialization slot, or None when the
        write materializes no storage (`ptr_write_fills_slot`).

        Single source of truth shared by the resumable ptr-slot prescan
        (which reserves the field) and the pointer-local reseat lowering
        (which consumes it): the two MUST agree
        on which writes need a slot, or a frame write lands back in a
        dying case-block local -- the reseat lowering rejects a missing
        entry rather than guessing.
        """
        if not self.ptr_write_fills_slot(init):
            return None
        if call_value_optional(init) is not None:
            # Own[Optional[T]]-returning call: the slot holds the returned
            # optional<T> whole; the pointer is lifted via optional_to_ptr.
            return f"std::optional<{cpp_type}>"
        # Polymorphic refinement: a per-site slot can hold the subclass
        # whole (the shared-slot slicing reject still fires upstream for
        # the sync-shared shapes; this only spells the site's storage).
        slot_cpp = cpp_type
        sub = polymorphic_subclass_into_optional(
            target_type, self.get_expr_type(init), self.analyzer.registry)
        if sub is not None:
            slot_cpp = sub.name
        return f"std::optional<{slot_cpp}>"

    def narrowed_value_optional_iter_type(self, expr: TpyExpr,
                                          declared: TpyType | None) -> TpyType | None:
        """For a for-loop iterable: if `declared` is a narrowed value-Optional
        (`str|None`/`bytes|None` proven non-None, value-repr -- still
        `std::optional<V>` in C++), return its narrowed inner so dispatch and
        frame-field types use the contained value `V`. Pointer-repr Optionals
        (e.g. `list|None`) already deref via the pointer-narrowing path, so they
        pass through unchanged. The matching `(*v)` render is applied separately
        (`THIRResumableLeaves.unwrapped_sources`).

        Not `_for_iterable_narrowed_optional` (thir/lower/resumable.py),
        though both look at a narrowed Optional source: this one answers the
        TYPE the loop strategy dispatches on, for any iterable expression,
        against the builder environment's resolution, and only for the
        value-repr form; that one answers whether the source LEAF hands the
        printer a bare read to unwrap, for a name or field read only, against
        the declared slot types of the leaf lowering, and for either repr
        (its caller tells them apart by the leaf's own deref)."""
        analyzed = self.get_expr_type(expr)
        if (analyzed is not None
                and isinstance(declared, OptionalType)
                and not declared.uses_pointer_repr()
                and not isinstance(analyzed, OptionalType)):
            return unwrap_ref_type(analyzed)
        return declared
