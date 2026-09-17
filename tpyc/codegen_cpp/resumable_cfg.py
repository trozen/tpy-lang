"""Control-flow graph for resumable-frame lowering, shared by async defs
and generators.

A resumable frame is a state-machine struct whose body method (`__poll__`
for async, `__next__` for generators) dispatches on a state integer to
the right resume point. To support `await` (or `yield`) inside arbitrary
control flow -- if/while/for/try/with -- we lower the function body to
a CFG of basic blocks, then emit each suspension's resume case body
wrapped in the source-level try/except/finally/with stack that was
active at that suspension point. C++'s rule that case labels inside try
blocks aren't reachable from outside the try forces this "replay the
region stack inside each case" pattern; it's what Roslyn's async
rewriter does for the same reason.

This module owns:
- Basic-block + terminator + region data types.
- The CFG builder that walks a resumable (async def / generator) body.
- (Future task) Conservative cross-suspension liveness.
- (Future task) State assignment.

Emission is in `gen_async.py` (and, later, `gen_generators.py`); the
CFG itself is shape-neutral.

SuspensionPayload is a tagged union with two variants -- AwaitPayload
(async) and YieldPayload (generator). The builder dispatches on the node
it encounters: a top-level `await` shape produces an AwaitPayload, a
`yield` statement produces a YieldPayload, and the decomposition
predicate (`_stmt_has_any_suspension`) treats both uniformly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Union

from ..identity_map import IdentityMap
from ..parse.nodes import (
    TpyAssert, TpyAssign, TpyAwait, TpyBreak, TpyContinue, TpyExceptHandler,
    TpyExpr, TpyExprStmt, TpyForEach, TpyIf, TpyName,
    TpyMatch,
    TpyRaise, TpyReturn, TpyStmt, TpyTry, TpyTupleUnpack, TpyVarDecl,
    TpyWhile, TpyWith,
    TpyWithItem, TpyYield, TryTier,
    stmt_has_any_suspension as _stmt_has_any_suspension,
    stmts_have_any_suspension as _stmts_have_any_suspension,
    stmts_have_any_return as _stmts_have_any_return,
)
from ..typesys import TypeParamRef
from .context import (CodeGenError, module_to_cpp_namespace,
                      resumable_struct_name)

if TYPE_CHECKING:
    from ..parse.nodes import TpyFunction
    from ..typesys import NominalType, TpyType
    from .gen_generators import GeneratorForInfo


@dataclass
class ResumableFuncState:
    """Per-function codegen state for the resumable-frame lowering, shared by
    generators and `async def` coroutines. Attached to a `TpyFunction` as
    `func._resumable_state` (via `resumable_state()`), built lazily by the
    prescan / CFG-build / eligibility passes and consumed by the struct +
    body emit.

    Replaces ~16 individually string-keyed `getattr(func, "_resumable_*" /
    "_async_*" / "_with_*")` side tables with one typed attribute: no
    `getattr(..., None) or {}` ritual, no silent-None-on-typo miscompile, and
    the prescan->emit contract (pass P populates fields A1..An) is visible in
    one place. The `_async_*`-named members keep that prefix because they are
    genuinely async-only (sub-coro struct names from `async for`/`async
    with`, await-arg lifting); the rest are shape-neutral."""
    # CFG build (_build_resumable_cfg)
    cfg: 'CFG | None' = None
    cfg_builder: 'CFGBuilder | None' = None
    # `(name, owner_record | None)` of every SAME-MODULE resumable unit whose
    # struct this frame embeds by value -- an inline await's sub-future, an
    # `async with`/`async for` dunder's sub-coro, and the `__iter__` generator
    # backing an `iter_next` for-source. Recorded where the embedding is
    # decided (the prescans + the CFG builder's await handling), because only
    # there is the callee resolved; `_emit_resumable_structs` reads it to order
    # each callee's definition before its consumer. Cross-module callees are
    # omitted -- their header already supplies a complete type, and recording
    # one would collide with a same-named local unit.
    frame_dep_units: 'list[tuple[str, str | None]]' = field(default_factory=list)
    # await-arg lifting (_effective_body / _lift_nested_awaits)
    lifted_body: 'list[TpyStmt] | None' = None
    next_lift_id: int = 0
    # Names of `__await_lift_*` temps. Each is created by the lift pass and
    # consumed by exactly one following statement (a construction invariant),
    # so it is a one-shot/movable source: a tuple-unpack reading it may bind by
    # rvalue-ref and move its elements out rather than copy the whole tuple.
    one_shot_lift_names: 'set[str]' = field(default_factory=set)
    # borrowed-rvalue-arg lifting (_lift_borrowed_rvalue_args): counter for
    # the `__coro_arg_<n>` hoisted locals that back rvalue temps borrowed by
    # an INLINE sub-coro across a suspension.
    next_arg_lift_id: int = 0
    # for-loop prescan (_prescan_resumable_for_loops). `*_prescanned` flags
    # preserve the original "cache even when the result is empty" semantics
    # (an empty map distinct from "not yet prescanned").
    for_prescanned: bool = False
    for_uid_map: 'dict[int, int]' = field(default_factory=dict)
    for_fields: 'list[tuple[str, str]]' = field(default_factory=list)
    for_loop_info: 'IdentityMap' = field(default_factory=IdentityMap)
    for_info_by_uid: 'dict[int, GeneratorForInfo]' = field(default_factory=dict)
    async_for_struct_names: 'dict[int, str]' = field(default_factory=dict)
    # ptr-slot prescan (_prescan_resumable_ptr_slots): an rvalue write into a
    # pointer-form frame local materializes its backing storage in a
    # `std::optional<T>` FRAME FIELD (one per write site), never a case-block
    # local -- the pointer field outlives the case block, so an inline slot
    # dangles at the first suspension. ptr_slot_map keys the TpyVarDecl/
    # TpyAssign -> field name; the pointer-local reseat lowering
    # consumes it and must find an entry for every slot-needing write (loud
    # internal error otherwise -- silence would be the dangle coming back).
    ptr_slots_prescanned: bool = False
    ptr_slot_fields: 'list[tuple[str, str]]' = field(default_factory=list)
    ptr_slot_map: 'IdentityMap' = field(default_factory=IdentityMap)
    # with-stmt prescan (_prescan_with_stmts)
    with_prescanned: bool = False
    with_uid_map: 'dict[int, list[int]]' = field(default_factory=dict)
    with_fields: 'list[tuple[str, str]]' = field(default_factory=list)
    # `__with_ctx_<n>` field names whose manager is borrowed (reference-type
    # lvalue): the frame field is a `T*` bound to the original, not an owning
    # `frame_slot<T>` copy. See TpyWithItem.manager_borrowed.
    with_borrowed_fields: 'set[str]' = field(default_factory=set)
    with_owning_str_targets: 'set[str]' = field(default_factory=set)
    # `with ... as NAME` target -> the C++ payload its frame field is spelled
    # from (`::tpy::with_enter_t<CM>`), so alias-vs-own is decided at
    # instantiation rather than guessed from the TPy enter type. Recorded for
    # EVERY with in the body, not only the decomposed ones: a target outlives
    # its statement, so it can be read across a suspension that the `with`
    # itself does not contain.
    with_target_payloads: 'dict[str, str]' = field(default_factory=dict)
    # TpyWith -> per-item `__with_ctx_<n>` number (None where not promoted),
    # for a NON-decomposed region whose OWNED manager backs an aliasing target.
    # The target's frame field points into `__enter__()`'s result, so the manager
    # must outlive the frame rather than the statement.
    with_owned_ctx_map: 'IdentityMap' = field(default_factory=IdentityMap)
    # `__with_ctx_<n>` numbering shared by the region prescan and the later
    # manager-home pass, which appends fields after the frame layout is built.
    with_ctx_counter: int = 0
    with_manager_homes_prescanned: bool = False
    async_with_struct_names: 'dict[int, tuple[str, str]]' = field(default_factory=dict)
    # borrow-alias prescan (_classify_pointer_alias_locals): non-value
    # statement-level locals -- single-assign aliases (`a = items[0]`) and
    # tuple-unpack borrow targets (`a, b = first_two(items)`) -- that alias
    # existing storage. Stored as `T*` frame slots so the alias (and the
    # mutation visibility CPython guarantees) survives a suspension, instead
    # of a value-copying `frame_slot<T>`. Mirrors the for-loop pointer_form
    # classification (pointer_form_loop_var / pointer_form_unpack_targets).
    pointer_alias_prescanned: bool = False
    pointer_alias_locals: 'set[str]' = field(default_factory=set)
    # THE frame's const bindings: every name the frame binds to const storage --
    # a const-borrow capture (the Phase-2 verdict or an explicit `readonly[T]`),
    # the receiver of a `@readonly` method, a loop var iterating a const source,
    # and a borrow alias rooted at one of those. One set so the field spelling,
    # the derived slot spellings (iterator, view, element-pointer lift) and the
    # THIR lowering cannot answer the same question differently; read through
    # `CodeGenContext.frame_binding_is_const`.
    const_frame_bindings: 'set[str]' = field(default_factory=set)
    # Borrow edges, keyed by SOURCE ROOT -> the names bound out of it, recorded
    # while classifying the bindings above: constness flows along them, and the
    # source's own verdict can be decided after the binding was classified (a
    # loop var is proven const by the for prescan, which runs after the alias
    # pass). Keyed by root so a newly-const name walks only its own dependents.
    const_source_edges: 'dict[str, list[str]]' = field(default_factory=dict)
    # try/finally prescan (_prescan_resumable_try_finally)
    try_finally_prescanned: bool = False
    try_finally_uid_map: 'dict[int, int]' = field(default_factory=dict)
    try_finally_fields: 'list[tuple[str, str]]' = field(default_factory=list)
    # frame-local placement plan (gen_async._frame_layout): one verdict per
    # hoisted local, consumed by the struct field-decl emit, the body-context
    # seeding, and THIR admission -- the single source of truth for the
    # frame-field form. Built lazily after the prescans have populated the
    # binding sets above.
    frame_layout: 'FrameLayoutPlan | None' = None
    # resumable-generator eligibility memoization (generator.py gate)
    gen_eligible: 'bool | None' = None


def resumable_state(func: 'TpyFunction') -> ResumableFuncState:
    """Get-or-create the `ResumableFuncState` attached to `func`."""
    state = getattr(func, "_resumable_state", None)
    if state is None:
        state = ResumableFuncState()
        func._resumable_state = state
    return state


def mark_frame_const(state: ResumableFuncState, *names: 'str | None') -> None:
    """Record `names` as const bindings of this frame and close the borrow
    edges over the result."""
    fresh = [n for n in names if n and n not in state.const_frame_bindings]
    if not fresh:
        return
    state.const_frame_bindings.update(fresh)
    _close_frame_const(state, fresh)


def record_const_source_edge(state: ResumableFuncState, target: str,
                             root: 'str | None') -> None:
    """Record that `target` borrows out of `root`, so `target` is const
    whenever `root` is -- whichever of the two is classified first."""
    if not root or target == root:
        return
    state.const_source_edges.setdefault(root, []).append(target)
    if root in state.const_frame_bindings:
        mark_frame_const(state, target)


def _close_frame_const(state: ResumableFuncState,
                       roots: 'list[str]') -> None:
    """Close `const_frame_bindings` over the borrow edges out of `roots`.

    A worklist over the newly-const names, not a re-scan of every edge on
    every mark: the verdict is consumed DURING the prescan window (a nested
    for-loop's slot is spelled off an alias whose root the enclosing loop
    was just proved const), so the closure cannot be deferred to the end of
    the window -- but only the edges out of a name that just turned const
    can fire, so the whole prescan costs one pass over the edges."""
    work = list(roots)
    while work:
        for target in state.const_source_edges.get(work.pop(), ()):
            if target not in state.const_frame_bindings:
                state.const_frame_bindings.add(target)
                work.append(target)


def same_module_dep_unit(owner: 'NominalType | None', method: str,
                         current_module: 'str | None',
                         ) -> 'tuple[str, str | None] | None':
    """The `frame_dep_units` entry for embedding `owner.method`'s frame, or
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


# -- Frame-local placement plan.

class FrameLocalKind(Enum):
    """The C++ field form backing one hoisted frame local.

    One verdict per local, decided once (gen_async._frame_layout) and
    consumed by every reader of the frame-field form: the struct
    field-decl emit, `setup_resumable_frame_locals`, and THIR admission.
    A consumer re-deriving the form from the local's TYPE is the defect
    class this plan exists to remove -- the form depends on binding facts
    (write sources, loop machinery, prescan sets) the type alone cannot
    recover.

      * VALUE            -- bare `T name;` (value types).
      * OWNED_STR        -- `std::string name;`: a `with ... as` str target
                            whose `__enter__` result would dangle as a view.
      * PTR_ALIAS        -- `T* name = nullptr;` aliasing live storage
                            (pointer-form loop var, unpack target, or
                            statement-level borrow alias).
      * OPT_PTR          -- `T* name = nullptr;` for a pointer-repr
                            Optional local (nullptr doubles as None).
      * REBIND_PTR       -- `T* name = nullptr;` for a plain non-value
                            local some rvalue rebind of which needs storage
                            of its own (sema's alias-rebind OWN verdict):
                            writes materialize in per-site `__ptr_slot_fN`
                            fields like OPT_PTR's, or assign through the
                            pointer where the verdict is IN_PLACE.
      * BORROW_TUPLE     -- `std::tuple<..., T*> name;` borrow-form tuple.
      * OWNING_TUPLE_SLOT-- `::tpy::frame_slot<std::tuple<...storage...>>`:
                            an owning tuple (Own element / owned call
                            result); emplace writes, `(*name)` reads.
      * MIXED_TUPLE_SLOT -- the owning slot of a MIXED tuple
                            (`tuple[Own[A], B]`), whose payload is the mixed
                            render `std::tuple<A, B*>` rather than a
                            fully-owned storage tuple: the owned element is
                            held by value, the borrowed one stays a pointer
                            into the caller's object. Reads split per element
                            (`.` owned, `->` borrowed), which is what
                            separates it from OWNING_TUPLE_SLOT.
      * FRAME_SLOT       -- `::tpy::frame_slot<T> name;` owning slot for
                            plain non-value locals; emplace writes,
                            `(*name)` reads.
      * SOURCE_FORM_SLOT -- a loop var whose alias-vs-own choice belongs to
                            C++: `::tpy::frame_slot<payload>` where the
                            payload spelling comes from the iteration
                            source's protocol (`for_elem_deref_t` /
                            `for_elem_next_t`) and frame_slot's
                            specializations supply either form at
                            instantiation.
      * PROTOCOL         -- a static-protocol-typed local: no concrete C++
                            backing exists for a frame FIELD (rendering one
                            is a user-facing CodeGenError at the struct
                            emit); body-context seeding treats it like
                            FRAME_SLOT, matching the peephole path where no
                            field is ever rendered.
    """
    VALUE = "value"
    OWNED_STR = "owned_str"
    PTR_ALIAS = "ptr_alias"
    OPT_PTR = "opt_ptr"
    REBIND_PTR = "rebind_ptr"
    BORROW_TUPLE = "borrow_tuple"
    OWNING_TUPLE_SLOT = "owning_tuple_slot"
    MIXED_TUPLE_SLOT = "mixed_tuple_slot"
    FRAME_SLOT = "frame_slot"
    SOURCE_FORM_SLOT = "source_form_slot"
    PROTOCOL = "protocol"


@dataclass(frozen=True)
class FrameLocalLayout:
    """Placement verdict for one hoisted local. `const` applies to the
    pointer kinds (PTR_ALIAS / OPT_PTR: `const T*` when the local is one of
    the frame's const bindings). `payload` is the frame_slot field's C++ payload
    spelling for the kinds that cannot re-derive it from the local's type --
    SOURCE_FORM_SLOT (spelled from the iteration source) and
    MIXED_TUPLE_SLOT (the mixed render); None for every other kind.

    `effective_type` is the local's tuple type with per-element ownership made
    explicit (`Own[...]` on the elements the frame owns) for a tuple whose
    declared type cannot say so -- a literal-bound owning local. None whenever
    the declared type is already the whole story."""
    kind: FrameLocalKind
    const: bool = False
    payload: 'str | None' = None
    effective_type: 'TpyType | None' = None


@dataclass
class FrameLayoutPlan:
    """Frame-local placement for one resumable body: `bindings` maps every
    `func.generator_locals` name to its verdict."""
    bindings: 'dict[str, FrameLocalLayout]'


# -- Resumable-frame shape: which state machine the emitter produces.

class ResumableShape(Enum):
    """Which resumable-frame shape the emitter (`gen_async.py`) produces.

    ASYNC -- `async def`: `await` -> `__poll__(Waker) -> Poll<T>`.
    GENERATOR -- generator: `yield` -> `__next__() -> expected<T,
        StopIteration>`.
    """
    ASYNC = "async"
    GENERATOR = "generator"


# -- Frame-struct naming.
#
# One grammar for both shapes: the `__coro_` struct an inline await embeds
# and the `__gen_` struct a delegating `for` embeds are the same kind of
# name over the same kind of callee, so a caller that spells one by hand
# drifts from the other (that drift is what kept cross-module generator
# delegation rejected while cross-module await worked).

_SHAPE_PREFIX = {ResumableShape.ASYNC: "__coro_",
                 ResumableShape.GENERATOR: "__gen_"}
_SHAPE_MEMBER = {ResumableShape.ASYNC: "async method",
                 ResumableShape.GENERATOR: "generator method"}


def frame_struct_name(name: str, owner_record: 'str | None' = None,
                      shape: ResumableShape = ResumableShape.ASYNC) -> str:
    """Resumable-frame struct name from raw strings: `<prefix><name>` for
    free functions, `<prefix><Owner>_<name>` for methods, where the prefix
    is `__coro_` for the async shape and `__gen_` for the generator one.

    Single source of truth for the frame a function emits for itself, for
    the sub-coroutine struct of a statically-resolved await (always
    `__coro_`, since only an `async def` can be awaited), for the delegated
    generator a `__for_src` field embeds, and for the async-with prescan
    (which only has the context manager's `NominalType.name`).
    """
    # The spelling itself (incl. the collision-free form for a nested
    # owner like `Outer.Inner`) is the context helper's; this only picks
    # the prefix from the shape.
    return resumable_struct_name(name, owner_record, _SHAPE_PREFIX[shape])


def frame_struct_qualname(
        types, owner: 'NominalType | None', method: str,
        inferred_type_args: 'tuple[TpyType, ...] | None' = None,
        *, module_qual: str | None = None,
        extra_template_args: 'list[str] | None' = None,
        shape: ResumableShape = ResumableShape.ASYNC,
        loc=None) -> str:
    """The C++ name of the frame struct a call site embeds, qualified so it
    is spellable from the module doing the embedding. `types` is the
    TypeResolver.

    Free function, same module: `<prefix><name>[<inferred_args>]`.
    Free function, cross-module (via `module_qual`):
        `<callee_ns>::<prefix><name>[<inferred_args>]`.
    Method on a non-generic class: `<ns>::<prefix><Record>_<name>
        [<inferred_args>]`, the namespace read off the owner's rendered
        C++ name so an imported owner qualifies itself.
    Method on a generic class: `<ns>::<prefix><Record>_<name>
        <owner_type_args>`. (The class-generic + method-generic case is
        rejected at sema, so the two arg lists are not composed today.)

    `extra_template_args` appends the deduced C++ type for each
    static-protocol param on the callee -- the `T_<pname>` template args
    declared on its struct (see `_extra_template_args_for_await`).
    """
    ns_qual = ""
    owner_name = None
    owner_args_suffix = ""
    if owner is not None:
        # Resolve the record rather than reading the owner's rendered C++
        # name: a nested owner (`Outer.Inner`) renders inside its outer
        # class scope, but its frame lives beside the record at module
        # scope under the record's dotted name.
        record = types.ctx.analyzer.registry.get_record_for_type(owner)
        owner_name = record.name if record is not None else owner.name
        if record is not None:
            owner_module = record.defining_module or record.module
            if owner_module and owner_module != types.ctx.analyzer.ctx.module_name:
                ns_qual = f"::{module_to_cpp_namespace(owner_module)}::"
        if owner.type_args:
            # A call site must name the base frame struct with concrete type
            # args. An unbound TypeParamRef here means the MRO-resolved
            # owner's args were not bound to concrete types -- compute_mro_
            # ancestors records each base with the defining class's own type
            # params, so a generic subclass of a generic base (Child[U](Box[U]))
            # or a multi-level chain (C(B[int32]) where B[U](A[U])) leaves them
            # unbound. Emit a clean diagnostic rather than ill-formed C++.
            if any(isinstance(ta, TypeParamRef) for ta in owner.type_args):
                raise CodeGenError(
                    f"inherited {_SHAPE_MEMBER[shape]} on generic base "
                    f"'{owner.name}' is not yet supported here: its type "
                    "parameters are not bound to concrete types (this happens "
                    "with a generic subclass of a generic base, or a "
                    "multi-level generic inheritance chain); flatten the "
                    "hierarchy or make the base concrete", loc=loc)
            inner_cpps = [types.type_to_cpp(ta)
                          for ta in owner.type_args]
            owner_args_suffix = "<" + ", ".join(inner_cpps) + ">"
    elif module_qual is not None:
        # Cross-module free function: qualify with the callee module's
        # C++ namespace.
        ns_qual = f"::{module_to_cpp_namespace(module_qual)}::"
    bare = frame_struct_name(method, owner_name, shape)
    # Combined template-arg list: callee's explicit `[T1, ...]` from
    # the call's inferred substitution, followed by any
    # `T_<pname>` extras deduced from static-protocol args.
    all_args: list[str] = []
    if inferred_type_args and not (owner is not None and owner.type_args):
        all_args.extend(types.type_to_cpp(ta)
                        for ta in inferred_type_args)
    if extra_template_args:
        all_args.extend(extra_template_args)
    suffix = ("<" + ", ".join(all_args) + ">") if all_args else ""
    return f"{ns_qual}{bare}{owner_args_suffix}{suffix}"


def recursive_delegation_error(name: str, loc=None) -> CodeGenError:
    """The diagnostic for a generator delegation cycle. Raised both by the
    within-module emit-order sort (which detects the cycle by failing to
    order the units) and by the field-type decision for a callee in a
    module that is a cycle peer of this one -- neither module's header is
    complete for the other, and the frames are mutually infinite-size."""
    return CodeGenError(
        f"recursive generator delegation involving '{name}' is not "
        "supported: the delegated generator source is stored by value in "
        "the consumer's frame, so the cycle would be infinite-size. Break "
        "the recursion (e.g. materialize the inner elements with "
        "`list(...)`).", loc=loc)


# -- AwaitPayload-specific enums (kept here so resumable_cfg owns the
# CFG-level metadata; gen_async re-imports them).

class AwaitMode(Enum):
    """How an await transition stores its sub-future in the parent frame.

    INLINE -- statically-known async def; sub-coro struct emplaced as a
        sub-future field.
    ERASED -- value awaitable (Task[T] / user value type implementing
        Awaitable[T]); move-stored in an std::optional sub-future.
    BORROWED -- reference-type awaitable (e.g. Future[T]); stored as a
        raw pointer so observers see the same object across suspensions.
    """
    INLINE = "inline"
    ERASED = "erased"
    BORROWED = "borrowed"


class AwaitKind(Enum):
    """How an await expression's result is consumed at its host stmt."""
    ASSIGN = "assign"
    VARDECL = "vardecl"
    RETURN = "return"
    DISCARD = "discard"


class AsyncWithKind(Enum):
    """Which leg of an `async with` a synthetic yield emits.

    The CFG synthesizes two Yield BBs per async-with: one for
    `await __cm.__aenter__()` and one for `await __cm.__aexit__(...)`.
    Emit dispatches on this enum rather than re-parsing the AST.
    """
    AENTER = "aenter"
    AEXIT = "aexit"


# -- Suspension payloads (the shape-specific part of a yield terminator)

@dataclass(frozen=True)
class AwaitPayload:
    """An async-def suspension. Held inside a Yield terminator."""

    @property
    def source_loc(self) -> 'SourceLocation | None':
        """Where the suspension sits in the source: the await node, else the
        statement that hosts it."""
        if self.await_node.loc is not None:
            return self.await_node.loc
        return self.host_stmt.loc if self.host_stmt is not None else None

    mode: AwaitMode
    sub_field_cpp_type: str       # cpp type for std::optional<...> / pointee
    operand_expr: TpyExpr         # the operand of `await ...`
    kind: AwaitKind
    bind_target: str | None       # variable name for ASSIGN / VARDECL
    return_stmt: TpyReturn | None # the original return for RETURN-kind
    host_stmt: TpyStmt | None     # source-level statement containing the await
    # The TpyAwait node itself (sema attaches awaited_async_func_name /
    # awaited_task_inner here; emit consults them).
    await_node: TpyAwait
    # Prebuilt-slot await: the operand is a bound coroutine handle whose
    # frame field IS the slot -- emit declares no __sub field, skips the
    # emplace, and polls/resets that frame field instead.
    prebuilt_slot: str | None = None
    # Async-with internal yields: emit takes a special path that
    # synthesizes `(*__with_ctx_<n>).__aenter__()` or
    # `(*__with_ctx_<n>).__aexit__({}, nullptr, {})` directly rather
    # than rendering a synthesized parse-tree node. Set by
    # `_build_async_with`; None for ordinary user awaits.
    async_with_kind: 'AsyncWithKind | None' = None
    async_with_ctx_n: int | None = None
    # Set by `_build_async_for` so emit can synthesize
    # `__sub_<i>.emplace(*__for_itr_<uid>)` and look up the sub-coro
    # struct name via `func._async_for_struct_names`. None for
    # ordinary user awaits.
    async_for_uid: int | None = None
    # `(name, owner_record | None)` of the SAME-MODULE coro this suspension
    # embeds as a by-value sub-future -- the emit-ordering edge, recorded
    # beside `sub_field_cpp_type` because the same resolution produces both.
    # None when there is nothing to order: a cross-module callee (already
    # complete via its header), or an erased/bound-handle await with no
    # statically-named callee.
    dep_unit: 'tuple[str, str | None] | None' = None
    # Namespace qualifier of a cross-module callee, recorded where the callee
    # is RESOLVED. Struct emit re-spells the sub-coro name when the callee has
    # static-protocol params, and re-deriving the qualifier there would put a
    # second copy of the resolution rules a merge could drift apart.
    sub_struct_module_qual: str | None = None


@dataclass(frozen=True)
class YieldPayload:
    """A generator suspension. Held inside a Yield terminator when the CFG
    is built from a generator body."""

    @property
    def source_loc(self) -> 'SourceLocation | None':
        return self.yield_stmt.loc if self.yield_stmt is not None else None

    value_expr: TpyExpr | None    # the yielded expression (None for bare yield)
    # The source `yield` statement, so emit can reuse the ordinary yield
    # emit (storage->borrow bridging etc.).
    yield_stmt: 'TpyYield | None' = None


SuspensionPayload = Union[AwaitPayload, YieldPayload]


# -- Regions: try/finally/with frames active at a BB

@dataclass(frozen=True)
class TryRegion:
    """A try/except/finally frame active inside the try body.

    `handlers` are the source-level except handlers; emit wraps the
    case body in `catch (Type& e) { handler.body }` per handler.

    Two finally shapes:
      * Helper-based (`finally_helper_name` set): finally body has no
        awaits and is emitted as a member function `void __finally_<n>()`
        called on every throw / return / fall-through exit.
      * CFG-based (`finally_entry_bb` / `captured_exc_field` set):
        finally body has an await and lives in the main state machine.
        On exception in the try body, the catch-all saves
        `std::current_exception()` to the captured-exc frame field and
        transitions state to `finally_entry_bb`. The finally body ends
        with an `AsyncFinallyExit` synthetic stmt that rethrows the
        saved exception (clearing the field) before falling through to
        the post-try BB. Mutually exclusive with the helper-based shape.
    """
    handlers: tuple[TpyExceptHandler, ...]
    finally_helper_name: str | None       # None if try has no finally body OR CFG-based
    # Source-level loc for emit diagnostics.
    loc_source: TpyStmt | None = None
    # CFG-based finally fields (None for helper-based / no finally).
    finally_entry_bb: int | None = None
    captured_exc_field: str | None = None
    # Pending-return slot for CFG-based finally: when set, a `return`
    # inside the try body or any handler stores its value to
    # `pending_return_slot` (when non-None -- void async defs leave it
    # None) and sets `pending_return_flag = true`, then transitions
    # state to the finally entry BB. AsyncFinallyExit checks the flag
    # and emits the deferred Poll::ready after the rethrow check.
    pending_return_flag: str | None = None
    pending_return_slot: str | None = None


@dataclass(frozen=True)
class FinallyRegion:
    """Marker for BBs inside a finally body. Emit treats specially:
    a throw inside a finally re-raises *after* the body completes;
    a `return` inside a finally body parks into the same pending-return
    slot as a return from the try / handler body (`finally_exit_bb`
    drains it via AsyncFinallyExit, Python: return-in-finally wins and
    swallows any in-flight exception).
    """
    helper_name: str
    loc_source: TpyStmt | None = None
    # Parking fields mirror TryRegion's so a `return` inside the finally
    # body sees the same slot as a return inside the try body. None when
    # the try has no reachable returns anywhere in try / handlers /
    # finally (no slot allocated).
    pending_return_flag: str | None = None
    pending_return_slot: str | None = None
    # Dedicated BB whose single stmt is AsyncFinallyExit. Both normal
    # fall-through and a return-in-finally jump here, so AsyncFinallyExit
    # is the single replay site.
    finally_exit_bb: int | None = None


@dataclass(frozen=True)
class ExceptRegion:
    """Marker for BBs inside an except handler body. The corresponding
    try frame is no longer on the stack (we've already caught the
    exception). The sub-future reset happens in the C++ catch wrapper
    emitted around the resume case body whose suspension threw -- so
    the reset is per-yield, not per-handler.

    `parent_finally` mirrors the enclosing TryRegion's
    `finally_helper_name` so emit can run the finally body when control
    leaves the handler normally (Python semantics: finally runs after
    a matched except).

    `parent_finally_entry_bb` is set instead when the parent try has
    a CFG-based finally: an exception raised inside the handler body
    must transition state to that BB (the inner try/catch in the
    handler emit dispatches this via the TryRegion's
    `captured_exc_field`). A `return` inside the handler body routes
    through the same pending-return slot as a return in the try body.
    """
    handler: TpyExceptHandler
    parent_finally: str | None = None
    loc_source: TpyStmt | None = None
    parent_finally_entry_bb: int | None = None
    parent_pending_return_flag: str | None = None
    parent_pending_return_slot: str | None = None


@dataclass(frozen=True)
class WithRegion:
    """A with frame active inside the body. Emit wraps the case body in
    a try/catch that runs __exit__ with the exception args and either
    suppresses or re-raises depending on __exit__'s return.

    `ctx_n` is the uid used to name both the `__with_ctx_<n>` frame
    slot (where the context-manager object lives, as
    `std::optional<T>`) and the `__exc_<n>` BaseException& binding
    inside the catch wrap. `post_with_bb` is the CFG BB to transition
    to when __exit__ suppresses an exception (only meaningful when
    item.exit_can_suppress).
    """
    item: TpyWithItem
    ctx_n: int
    post_with_bb: int
    loc_source: TpyStmt | None = None


Region = Union[TryRegion, FinallyRegion, ExceptRegion, WithRegion]


# -- Terminators: one per BB

@dataclass(frozen=True)
class Fall:
    """Fall through to next_bb. Used for straight-line edges and
    explicit break/continue (which target loop exit / loop top)."""
    next_bb: int


@dataclass(frozen=True)
class Branch:
    """Conditional branch on `cond`."""
    cond: TpyExpr
    then_bb: int
    else_bb: int


@dataclass(frozen=True)
class Yield:
    """Suspension point. Saves state, returns Pending (async) / yield
    value (generator); resume_bb is the entry point on resume."""
    payload: SuspensionPayload
    resume_bb: int
    suspension_index: int         # bijective with resume_bb among yields


@dataclass(frozen=True)
class ReturnT:
    """Return from the function. Emit walks the active finally chain
    before emitting the actual return."""
    value: TpyExpr | None
    return_stmt: TpyReturn        # the original statement (for type info)


@dataclass(frozen=True)
class RaiseT:
    """Raise an exception. Emit walks the active finally chain via
    the exception unwinding mechanism (no explicit chain run)."""
    raise_stmt: TpyRaise


@dataclass(frozen=True)
class Unreachable:
    """BB whose end is statically unreachable (e.g. infinite loop body
    with no break/return)."""
    pass


@dataclass(frozen=True)
class AsyncForAdvance:
    """Terminator for the cond/advance BB of a CFG-decomposed for-loop
    whose body contains await. Emit:
      __for_r_<uid> = (*__for_itr_<uid>).__next__();
      if (!(*__for_r_<uid>).has_value()) -> exhausted_bb
      <loop_var> = ::tpy::unwrap_ref(*(*__for_r_<uid>));
      -> has_value_bb
    Two successors mirror Branch so case-entries pred-counting works."""
    uid: int
    stmt: TpyForEach
    has_value_bb: int
    exhausted_bb: int


@dataclass(frozen=True)
class MatchDispatch:
    """Terminator for a `match` whose arm bodies contain a suspension (H1).

    The suspension-free dispatch (subject eval + every arm test + bindings
    + guards) is emitted by reusing the ordinary match dispatch; each arm
    *body* is routed back through the resumable walker (it may suspend),
    so arm bodies live in the state machine while the dispatch keeps its
    type-aware switch / if-elif shape. `arm_bbs[i]` is the entry BB of
    `match_stmt.cases[i].body`; `join_bb` is the post-match continuation
    (None when the match is exhaustive and every arm terminates, so no
    fall-through exists)."""
    match_stmt: TpyMatch
    arm_bbs: tuple[int, ...]
    join_bb: int | None


Terminator = Union[Fall, Branch, Yield, ReturnT, RaiseT, Unreachable,
                   AsyncForAdvance, MatchDispatch]


# -- Synthetic leaf-stmt types for CFG-lowered for-loops ----------------

@dataclass(frozen=True)
class AsyncForIterSetup:
    """Synthetic leaf stmt at the head of a CFG-decomposed for-loop.

    Sync (`is_async=False`, v1.5 M3.1) emits:
      `::tpy::resumable_iter_init(__for_itr_<uid>, <iterable_expr>);`
    Async (`is_async=True`, v1.5 M6) emits:
      `__for_itr_<uid> = (<iterable_expr>).__aiter__();`
    The frame field name is shared since only one of the two paths is
    active per uid."""
    uid: int
    iterable_expr: TpyExpr
    is_async: bool = False
    # Loop element type, used by the range strategy's counter init (the
    # emitter needs the C++ element type for the counter/stop casts).
    elem_type: 'TpyType | None' = None
    # Carried from the source loop's `iter_borrow_unplaceable`: sema could
    # file no ITER loan for this iterable, so the lowering refuses it here
    # exactly as the sync for-each route does.
    iter_borrow_unplaceable: bool = False


@dataclass(frozen=True)
class AsyncFinallyExit:
    """Synthetic leaf stmt at the single replay site of a CFG-decomposed
    finally region (allocated as the finally's dedicated `finally_exit_bb`
    so both normal fall-through and a return-in-finally drain through one
    point). Two-stage emit -- pending-return check FIRST so a return in
    the finally body wins over any in-flight exception (Python
    semantics):

      if (this-><pending_return_flag>) {
          this-><pending_return_flag> = false;
          this-><captured_exc_field> = nullptr;   // swallow in-flight exc
          <walk outer finally chain>
          __state = S_DONE;
          return Poll<T>::ready(std::move(this-><pending_return_slot>));
      }
      if (this-><captured_exc_field>) {
          std::exception_ptr __tmp = this-><captured_exc_field>;
          this-><captured_exc_field> = nullptr;
          std::rethrow_exception(__tmp);
      }

    Stage 1 fires when a `return` originating in the try body, any
    handler body, OR the finally body itself set the pending flag. It
    has two shapes depending on whether an enclosing CFG-finally region
    is active at this exit site (see `_emit_async_finally_exit`):
      * No enclosing CFG finally: replay locally -- walk the outer
        helper chain, then emit Poll<T>::ready / StopIteration as shown
        above (the deferred return is delivered as the coro's exit).
      * Enclosing CFG finally present: forward -- move this exit's
        parked value into the outer's `pending_return_slot`, set the
        outer's `pending_return_flag`, walk helpers between this exit
        and the outer's finally entry, then transition state to the
        outer's `finally_entry_bb`. The outer's AsyncFinallyExit
        eventually performs the local replay (or forwards again, for
        deeper nesting).
    The captured-exc clear-on-its-way-out implements the Python
    return-in-finally-wins-over-raise rule. Stage 2 rethrows a saved
    in-flight exception when no return is pending. Fields are cleared
    after extraction so a re-entry to the same try (e.g. inside a loop)
    doesn't carry over stale state.

    `pending_return_flag` / `pending_return_slot` are None when no
    `return` is reachable in try / handlers / finally (stage 1 omitted).
    `pending_return_slot` is None for void async defs even when the flag
    is set (the deferred return needs no value)."""
    captured_exc_field: str
    pending_return_flag: str | None = None
    pending_return_slot: str | None = None


@dataclass(frozen=True)
class WithEnter:
    """Synthetic leaf stmt at the head of a CFG-decomposed sync `with`
    body (sync `with X:` compiled inside an `async def`, M3.2). Real
    Python `async with` uses its own setup synthetic (see AsyncWithSetup).
    Emit:
      __with_ctx_<n> = <context_expr>;
      (*__with_ctx_<n>).__enter__();     # if target is None
      <target> = (*__with_ctx_<n>).__enter__();   # otherwise
    """
    ctx_n: int
    item: TpyWithItem


@dataclass(frozen=True)
class AsyncWithSetup:
    """Synthetic leaf stmt at the head of a CFG-decomposed `async with`
    region (v1.5 M5). Stores the context manager in a frame slot so the
    subsequent `__aenter__` / `__aexit__` yields can dispatch through it.

    Emit:
      __with_ctx_<n> = <context_expr>;

    (`__with_ctx_<n>` is the frame-hoisted `std::optional<CM>` field
    allocated by the prescan; assigning a CM rvalue constructs in
    place via `operator=`.) The Yield site immediately following
    emplaces `__sub_<i>` with `(*__with_ctx_<n>, ...)` for the
    `__aenter__` call; the finally body's Yield does the same for
    `__aexit__(None, None, None)`.
    """
    ctx_n: int
    item: TpyWithItem


# -- Basic block

@dataclass
class BB:
    """A basic block.

    `stmts` are leaf statements emitted by the ordinary leaf renderer;
    compound statements that *don't* transitively contain a suspension
    stay as single elements here (lazy decomposition). Compound
    statements that *do* contain suspensions are decomposed during CFG
    construction and never appear in `stmts`.

    `region_stack` is the (outermost ... innermost) list of regions
    active when control enters this BB. Emit wraps the BB's emission
    in the region_stack reconstructed as nested C++ try blocks.

    `region_stack` is mutable (set by the builder's annotation pass).
    """
    id: int
    stmts: list[TpyStmt] = field(default_factory=list)
    terminator: Terminator | None = None
    region_stack: tuple[Region, ...] = ()
    # Set by the (future) reachability pass; True iff this BB is reached
    # only as the resume entry of a Yield (i.e. is a state target). Used
    # by emit to decide whether to emit a case label.
    is_resume_entry: bool = False
    # isinstance/`is not None` narrowing facts (var name -> narrowed type)
    # active when control enters this BB. A suspension splits a narrowed
    # region across C++ case scopes, so the narrowed binding (a dynamic_cast
    # / std::get local) does not survive; emit re-establishes it at the
    # resume case from these facts. {} for un-narrowed blocks.
    entry_narrowings: dict[str, "TpyType"] = field(default_factory=dict)


# -- Loop-context tracking (used during construction; not in the final CFG)

@dataclass
class _LoopCtx:
    """Pushed onto a stack while building loop bodies so break/continue
    can resolve to the correct target BB.

    `continue_bb` is the loop's check/step BB (for while: the condition
    check; for for: the iterator advance). `break_bb` is the BB the
    loop exits to. `regions_at_entry` is the region stack snapshot at
    loop entry, used to compute the region delta on break/continue.
    """
    continue_bb: int
    break_bb: int
    regions_at_entry: tuple[Region, ...]


# -- The CFG container

@dataclass
class CFG:
    entry_bb: int
    blocks: dict[int, BB]
    yield_sites: list[Yield] = field(default_factory=list)
    # Finally-body helper functions to emit as private members. Each
    # entry is (helper_name, body_stmts). Order matters for stable
    # output.
    finally_helpers: list[tuple[str, list[TpyStmt]]] = field(default_factory=list)
    # Cached emit-side maps. Populated lazily by the consumer (e.g.
    # AsyncCoroCodegen) on first use; reused across struct-emit + poll-
    # emit passes so we don't pay the O(N) predecessor walk + region-
    # stack equality scan twice per function. The case-entries value
    # type is consumer-defined (async emits StateLabel; future generator
    # port may use a different shape) -- typed `dict[int, object]` to
    # keep this module shape-neutral.
    _case_entries_cache: dict[int, object] | None = field(default=None, repr=False)
    _resume_to_yield_cache: dict[int, Yield] | None = field(default=None, repr=False)

    def bb(self, bb_id: int) -> BB:
        return self.blocks[bb_id]

    def resume_to_yield(self) -> dict[int, Yield]:
        """`resume_bb -> Yield` map, built once and cached."""
        if self._resume_to_yield_cache is None:
            self._resume_to_yield_cache = {
                y.resume_bb: y for y in self.yield_sites
            }
        return self._resume_to_yield_cache


# -- Builder

class CFGBuilder:
    """Walks a TpyFunction body and produces a CFG.

    Usage:
        builder = CFGBuilder()
        cfg = builder.build(func_body)

    The builder maintains a "current BB" while walking statements.
    Compound statements that contain suspensions branch the CFG;
    statements that don't are appended to the current BB as leaf
    statements.
    """

    def __init__(self, payload_factory=None,
                 for_uid_map: 'dict[int, int] | None' = None,
                 with_uid_map: 'dict[int, list[int]] | None' = None,
                 try_finally_uid_map:
                    'dict[int, int] | None' = None,
                 func_returns_void: bool = False) -> None:
        """
        `payload_factory(await_node, host_stmt, kind, bind_target,
        return_stmt) -> AwaitPayload` is called for each top-level await
        the builder encounters. The factory fills in mode +
        sub_field_cpp_type, which are async-specific. If None, a
        placeholder payload is created (suitable for testing only).

        `for_uid_map` maps id(TpyForEach) -> uid for for-loops that the
        caller has pre-scanned and registered with frame fields. Used by
        `_build_for` to attach the correct uid to AsyncForIterSetup /
        AsyncForAdvance. Missing entries cause `_build_for` to raise
        _CFGNotYetSupported (the caller is expected to pre-register every
        for-with-await loop).

        `with_uid_map` maps id(TpyWith) -> list of ctx_n (one per WithItem,
        in source order) for with-stmts pre-scanned and registered. Used
        by `_build_with`. Missing entries cause `_build_with` to raise.
        """
        self._blocks: dict[int, BB] = {}
        self._next_bb_id: int = 0
        self._yield_sites: list[Yield] = []
        self._finally_helpers: list[tuple[str, list[TpyStmt]]] = []
        self._next_finally_id: int = 0
        # Construction-time stacks.
        self._loop_stack: list[_LoopCtx] = []
        self._region_stack: list[Region] = []
        # isinstance/`is not None` narrowing facts active at the current
        # construction point. New BBs are stamped with a snapshot so the
        # emitter can re-establish narrowed bindings at resume cases that
        # land inside a narrowed region (a binding is a C++ local that
        # does not survive the suspension that split the region).
        self._active_narrowings: dict[str, TpyType] = {}
        self._payload_factory = payload_factory
        self._for_uid_map: dict[int, int] = for_uid_map or {}
        self._with_uid_map: dict[int, list[int]] = with_uid_map or {}
        self._try_finally_uid_map: dict[int, int] = try_finally_uid_map or {}
        self._func_returns_void: bool = func_returns_void
        # (id(region), id(handler)) -> handler-entry BB id. Populated
        # by `_record_handler_entry` during try/except construction;
        # read by emitters via `get_handler_entry`.
        self._handler_entries: dict[tuple[int, int], int] = {}

    # -- Public entry point ---------------------------------------------

    def build(self, body: list[TpyStmt]) -> CFG:
        """Build a CFG from a resumable function body (async def or
        generator). Shape-neutral: a `yield` is a suspension point
        alongside `await`."""
        entry = self._new_bb()
        end = self._build_block(entry, body)
        # If the body fell off the end without a terminator, mark the
        # tail BB with Unreachable. The emitter inserts the appropriate
        # "fell-through-without-returning" handling (Ready(unit) for
        # `-> None`, panic otherwise).
        if end is not None and self._blocks[end].terminator is None:
            self._blocks[end].terminator = Unreachable()
        cfg = CFG(
            entry_bb=entry,
            blocks=self._blocks,
            yield_sites=self._yield_sites,
            finally_helpers=self._finally_helpers,
        )
        self._mark_resume_entries(cfg)
        return cfg

    # -- BB helpers -----------------------------------------------------

    def _new_bb(self) -> int:
        bb_id = self._next_bb_id
        self._next_bb_id += 1
        bb = BB(id=bb_id, region_stack=tuple(self._region_stack),
                entry_narrowings=dict(self._active_narrowings))
        self._blocks[bb_id] = bb
        return bb_id

    # -- Narrowing-fact threading ---------------------------------------

    def _push_narrowings(self, facts: 'dict[str, TpyType]') -> dict:
        """Extend the active narrowing set with `facts` (concrete-type
        narrowings only; union/None facts are no-ops at the binding
        level). Returns a token for `_pop_narrowings`."""
        prev = self._active_narrowings
        if facts:
            merged = dict(prev)
            merged.update(facts)
            self._active_narrowings = merged
        else:
            self._active_narrowings = dict(prev)
        return prev

    def _pop_narrowings(self, prev: dict) -> None:
        self._active_narrowings = prev

    def _kill_narrowings(self, stmt: TpyStmt) -> None:
        """Drop a variable's narrowing when a statement reassigns it, so
        BBs built after the reassignment don't re-cast a now-wrong type.
        Assert narrowings (`then_type_facts`) flow forward into the active
        set for the rest of the block."""
        if isinstance(stmt, TpyAssert):
            if stmt.then_type_facts:
                self._active_narrowings = {**self._active_narrowings,
                                           **stmt.then_type_facts}
            return
        killed: set[str] = set()
        if isinstance(stmt, TpyAssign) and isinstance(stmt.target, TpyName):
            killed.add(stmt.target.name)
        elif isinstance(stmt, TpyVarDecl):
            killed.add(stmt.name)
        elif isinstance(stmt, TpyTupleUnpack):
            # `a, b = ...` rebinds each target to a fresh value; any narrowed
            # name among them must lose its narrowing (parser guarantees the
            # targets are simple names, `None` for `_`).
            killed.update(n for n in stmt.targets if n is not None)
        if killed & self._active_narrowings.keys():
            self._active_narrowings = {
                k: v for k, v in self._active_narrowings.items()
                if k not in killed}

    def _finish(self, bb_id: int, terminator: Terminator) -> None:
        """Set the terminator on a BB if it doesn't already have one.
        A BB ending in return/raise/yield/branch is sealed; subsequent
        statements in the source belong to a fresh BB."""
        bb = self._blocks[bb_id]
        if bb.terminator is None:
            bb.terminator = terminator

    def _is_sealed(self, bb_id: int) -> bool:
        return self._blocks[bb_id].terminator is not None

    # -- Body walker ----------------------------------------------------

    def _build_block(self, entry_bb: int, stmts: list[TpyStmt]) -> int | None:
        """Walk a statement list, appending to / forking from entry_bb.
        Returns the BB id where control flows out (None if the block
        terminates unconditionally via return/raise/break/continue)."""
        cur = entry_bb
        for stmt in stmts:
            if self._is_sealed(cur):
                # Source-level dead code after a return/raise/break/
                # continue. Emit into a fresh BB that's only reachable
                # from nothing; it'll be DCE'd by the emitter.
                cur = self._new_bb()
            cur_after = self._build_stmt(cur, stmt)
            if cur_after is None:
                return None
            cur = cur_after
            self._kill_narrowings(stmt)
        return cur

    def _build_stmt(self, cur: int, stmt: TpyStmt) -> int | None:
        """Process one statement. Returns the BB id where control flows
        out of this statement (None for unconditional terminators)."""
        # Statements with explicit terminator semantics.
        if isinstance(stmt, TpyReturn):
            return self._build_return(cur, stmt)
        if isinstance(stmt, TpyRaise):
            self._finish(cur, RaiseT(raise_stmt=stmt))
            return None
        if isinstance(stmt, TpyBreak):
            self._build_break(cur)
            return None
        if isinstance(stmt, TpyContinue):
            self._build_continue(cur)
            return None
        # Statements that may host a top-level await.
        if self._stmt_has_top_level_await(stmt):
            return self._build_top_level_await_stmt(cur, stmt)
        # A `yield` is a generator suspension at statement position.
        if isinstance(stmt, TpyYield):
            return self._build_yield_stmt(cur, stmt)
        # A non-loop compound (if/try/with) inside a decomposed loop
        # must be decomposed if it contains break/continue targeting
        # the outer loop -- otherwise the break/continue would become
        # C++ break/continue inside our state-machine switch instead
        # of a CFG state transition.
        force_loop_decomp = (self._loop_stack
                             and _stmt_has_unbound_loop_transfer(stmt))
        # Compound statements: decompose if they contain a nested
        # suspension OR an unbound break/continue inside a loop.
        if isinstance(stmt, TpyIf):
            if _stmt_has_any_suspension(stmt) or force_loop_decomp:
                return self._build_if(cur, stmt)
        elif isinstance(stmt, TpyWhile):
            if _stmt_has_any_suspension(stmt):
                return self._build_while(cur, stmt)
        elif isinstance(stmt, TpyForEach):
            if stmt.is_async or _stmt_has_any_suspension(stmt):
                return self._build_for(cur, stmt)
        elif isinstance(stmt, TpyTry):
            if _stmt_has_any_suspension(stmt) or force_loop_decomp:
                return self._build_try(cur, stmt)
        elif isinstance(stmt, TpyWith):
            if _stmt_has_any_suspension(stmt) or force_loop_decomp:
                return self._build_with(cur, stmt)
        elif isinstance(stmt, TpyMatch):
            if _stmt_has_any_suspension(stmt) or force_loop_decomp:
                return self._build_match(cur, stmt)
        # Leaf statement (or compound without suspensions). If a statement
        # reaches this point STILL containing a suspension, it's a statement
        # kind the builder doesn't decompose (if/while/for/try/with/match all
        # are). Appending it as a leaf would emit the nested `await`/`yield`
        # as straight-line code -- silently wrong for async, and for a
        # generator it would collide with the resumable state enum. Refuse so
        # the build surfaces a clean CodeGenError instead of a miscompile.
        if _stmt_has_any_suspension(stmt):
            raise _CFGNotYetSupported(
                "a suspension (`await`/`yield`) inside this statement is "
                "not yet supported by the resumable lowering (the statement "
                "kind is not decomposed by the CFG builder).",
                loc=getattr(stmt, "loc", None),
            )
        self._blocks[cur].stmts.append(stmt)
        return cur

    # -- Top-level await on assign/vardecl/return/expr-stmt -------------

    def _stmt_has_top_level_await(self, stmt: TpyStmt) -> bool:
        return _top_level_await_in(stmt) is not None

    def _build_top_level_await_stmt(self, cur: int, stmt: TpyStmt) -> int | None:
        """Convert a statement whose top-level expression slot is a
        TpyAwait into a Yield terminator. The bind / discard / return
        action runs in the resume BB."""
        await_node = _top_level_await_in(stmt)
        assert await_node is not None
        kind, bind_target, return_stmt = _classify_await_position(stmt, await_node)
        if self._payload_factory is not None:
            payload = self._payload_factory(
                await_node, stmt, kind, bind_target, return_stmt)
        else:
            payload = AwaitPayload(
                mode=AwaitMode.INLINE,
                sub_field_cpp_type="",
                operand_expr=await_node.value,
                kind=kind,
                bind_target=bind_target,
                return_stmt=return_stmt,
                host_stmt=stmt,
                await_node=await_node,
            )
        suspension_idx = len(self._yield_sites)
        resume_bb = self._new_bb()
        terminator = Yield(
            payload=payload,
            resume_bb=resume_bb,
            suspension_index=suspension_idx,
        )
        self._finish(cur, terminator)
        self._yield_sites.append(terminator)
        # If the await's host stmt was a TpyReturn, the resume case
        # body runs the finally chain and returns; control does not
        # flow past it.
        if kind is AwaitKind.RETURN:
            self._finish(resume_bb, ReturnT(value=None, return_stmt=return_stmt))
            return None
        return resume_bb

    def _build_yield_stmt(self, cur: int, stmt: TpyYield) -> int | None:
        """Convert a `yield` statement into a Yield terminator. Unlike an
        `await`, a yield pushes its value OUT to the caller and resumes
        in place -- there is no sub-future to poll and (until `send()`)
        no value bound back in, so the resume BB simply continues after
        the yield. The generator emitter reads `value_expr` / `yield_stmt`
        off the payload to produce the yielded value."""
        payload = YieldPayload(value_expr=stmt.value, yield_stmt=stmt)
        suspension_idx = len(self._yield_sites)
        resume_bb = self._new_bb()
        terminator = Yield(
            payload=payload,
            resume_bb=resume_bb,
            suspension_index=suspension_idx,
        )
        self._finish(cur, terminator)
        self._yield_sites.append(terminator)
        return resume_bb

    def _build_return(self, cur: int, stmt: TpyReturn) -> int | None:
        # `return await ...` is the RETURN await kind; routed through
        # _build_top_level_await_stmt.
        if isinstance(stmt.value, TpyAwait):
            return self._build_top_level_await_stmt(cur, stmt)
        self._finish(cur, ReturnT(value=stmt.value, return_stmt=stmt))
        return None

    # -- break / continue -----------------------------------------------

    def _build_break(self, cur: int) -> None:
        if not self._loop_stack:
            # Sema would have rejected; treat as unreachable.
            self._finish(cur, Unreachable())
            return
        target = self._loop_stack[-1].break_bb
        self._finish(cur, Fall(next_bb=target))

    def _build_continue(self, cur: int) -> None:
        if not self._loop_stack:
            self._finish(cur, Unreachable())
            return
        target = self._loop_stack[-1].continue_bb
        self._finish(cur, Fall(next_bb=target))

    # -- if/else --------------------------------------------------------

    def _build_if(self, cur: int, stmt: TpyIf) -> int | None:
        then_bb = self._new_bb()
        else_bb = self._new_bb()
        join_bb = self._new_bb()
        self._finish(cur, Branch(cond=stmt.condition, then_bb=then_bb,
                                  else_bb=else_bb))
        # Each arm carries the condition's narrowing facts so a suspension
        # inside the arm re-establishes the narrowed binding at its resume
        # case. then_bb/else_bb were created in the outer context, so stamp
        # them explicitly; continuation BBs are stamped by `_new_bb` while
        # the arm facts are pushed.
        self._blocks[then_bb].entry_narrowings = {
            **self._active_narrowings, **stmt.then_type_facts}
        prev = self._push_narrowings(stmt.then_type_facts)
        then_end = self._build_block(then_bb, stmt.then_body)
        self._pop_narrowings(prev)
        if then_end is not None:
            self._finish(then_end, Fall(next_bb=join_bb))
        self._blocks[else_bb].entry_narrowings = {
            **self._active_narrowings, **stmt.else_type_facts}
        prev = self._push_narrowings(stmt.else_type_facts)
        else_end = self._build_block(else_bb, stmt.else_body)
        self._pop_narrowings(prev)
        if else_end is not None:
            self._finish(else_end, Fall(next_bb=join_bb))
        # If both branches terminate, the join is unreachable.
        if then_end is None and else_end is None:
            return None
        return join_bb

    # -- while ----------------------------------------------------------

    def _build_while(self, cur: int, stmt: TpyWhile) -> int | None:
        check_bb = self._new_bb()
        body_bb = self._new_bb()
        # `exit_bb` is the NORMAL-exit target (condition false). When there
        # is an `else` clause it runs there; `break` instead targets a
        # separate `after_bb` that skips the else (Python loop-else
        # semantics). With no else, `after_bb` IS `exit_bb` (no extra BB,
        # so non-else loops are structurally unchanged).
        exit_bb = self._new_bb()
        self._finish(cur, Fall(next_bb=check_bb))
        self._finish(check_bb, Branch(cond=stmt.condition, then_bb=body_bb,
                                       else_bb=exit_bb))
        after_bb = self._new_bb() if stmt.orelse else exit_bb
        self._loop_stack.append(_LoopCtx(
            continue_bb=check_bb,
            break_bb=after_bb,
            regions_at_entry=tuple(self._region_stack),
        ))
        # The condition's narrowing applies inside the loop body; a
        # suspension in the body re-establishes the binding at its resume.
        self._blocks[body_bb].entry_narrowings = {
            **self._active_narrowings, **stmt.then_type_facts}
        prev = self._push_narrowings(stmt.then_type_facts)
        try:
            body_end = self._build_block(body_bb, stmt.body)
            if body_end is not None:
                self._finish(body_end, Fall(next_bb=check_bb))
        finally:
            self._loop_stack.pop()
            self._pop_narrowings(prev)
        if stmt.orelse:
            # else runs on the normal-exit edge, then falls to after_bb;
            # break edges (-> after_bb) skip it. The else body is a regular
            # region so it may itself contain suspensions.
            else_end = self._build_block(exit_bb, stmt.orelse)
            if else_end is not None:
                self._finish(else_end, Fall(next_bb=after_bb))
            return after_bb
        return exit_bb

    # -- for ------------------------------------------------------------

    def _build_for(self, cur: int, stmt: TpyForEach) -> int | None:
        if stmt.is_async:
            return self._build_async_for(cur, stmt)
        # Universal iter/next lowering. The for-loop becomes:
        #   iter_init -> cond_advance --(has_value)-> body BBs --(fall)-> cond_advance
        #                            --(exhausted)-> exit
        # The cond/advance BB carries an AsyncForAdvance terminator
        # which expands at emit to next() + has_value check + bind.
        uid = self._for_uid_map.get(id(stmt))
        if uid is None:
            raise _CFGNotYetSupported(
                "for-loop with await reached CFG builder without a "
                "registered uid (internal: pre-scan missed this loop).",
                loc=stmt.loc,
            )
        iter_init_bb = self._new_bb()
        cond_bb = self._new_bb()
        body_bb = self._new_bb()
        # `exit_bb` is the EXHAUSTED (normal-exit) target; the `else` clause
        # runs there. `break` targets `after_bb` (skips else). No else ->
        # after_bb IS exit_bb (non-else for-loops unchanged).
        exit_bb = self._new_bb()
        # Fall into iter init.
        self._finish(cur, Fall(next_bb=iter_init_bb))
        # iter_init_bb: setup, fall to cond.
        self._blocks[iter_init_bb].stmts.append(
            AsyncForIterSetup(
                uid=uid, iterable_expr=stmt.iterable,
                elem_type=stmt.elem_type,
                iter_borrow_unplaceable=stmt.iter_borrow_unplaceable))
        self._finish(iter_init_bb, Fall(next_bb=cond_bb))
        # cond_bb: AsyncForAdvance terminator (advance + branch).
        self._finish(cond_bb, AsyncForAdvance(
            uid=uid, stmt=stmt,
            has_value_bb=body_bb, exhausted_bb=exit_bb))
        after_bb = self._new_bb() if stmt.orelse else exit_bb
        # body: continue=cond_bb, break=after_bb (skips the else).
        self._loop_stack.append(_LoopCtx(
            continue_bb=cond_bb,
            break_bb=after_bb,
            regions_at_entry=tuple(self._region_stack),
        ))
        try:
            body_end = self._build_block(body_bb, stmt.body)
            if body_end is not None:
                self._finish(body_end, Fall(next_bb=cond_bb))
        finally:
            self._loop_stack.pop()
        if stmt.orelse:
            else_end = self._build_block(exit_bb, stmt.orelse)
            if else_end is not None:
                self._finish(else_end, Fall(next_bb=after_bb))
            return after_bb
        return exit_bb

    def _build_async_for(self, cur: int, stmt: TpyForEach) -> int | None:
        """Lower `async for y in ait: <body>` (v1.5 M6).

        Shape:
          iter_init_bb (AsyncForIterSetup(is_async=True))
            -> TryRegion[ExceptRegion(StopAsyncIteration -> break)] {
                 cond_bb: Yield(await __aiter.__anext__(), bind y)
                 resume_bb: (binds y, falls out of try)
               }
            (pop TryRegion)
            body_bb: <user body>, Fall -> cond_bb
            handler -> exit_bb (via the loop's break_bb)

        The TryRegion wraps ONLY the cond/resume pair, NOT the body.
        A StopAsyncIteration from the body must propagate up like any
        other exception, so the body's case bodies must not be wrapped
        in the auto-catch.

        Parser rejects `else:` on async-for, so no orelse handling here.
        """
        uid = self._for_uid_map.get(id(stmt))
        if uid is None:
            raise _CFGNotYetSupported(
                "async-for reached CFG builder without a registered "
                "uid (internal: pre-scan missed this loop).",
                loc=stmt.loc,
            )
        # body_bb / exit_bb are created OUTSIDE the TryRegion push so a
        # StopAsyncIteration thrown from the body propagates rather than
        # being silently caught by the loop's auto-handler. iter_init_bb
        # likewise stays outside (the __aiter__ call shouldn't throw
        # StopAsyncIteration, and a try region around it would distort
        # the case structure). cond_bb / resume_bb / handler body BB are
        # created below INSIDE the push so the emitter wraps their case
        # bodies in `catch (StopAsyncIteration&)`.
        iter_init_bb = self._new_bb()
        body_bb = self._new_bb()
        exit_bb = self._new_bb()

        self._finish(cur, Fall(next_bb=iter_init_bb))
        self._blocks[iter_init_bb].stmts.append(
            AsyncForIterSetup(
                uid=uid, iterable_expr=stmt.iterable, is_async=True,
                iter_borrow_unplaceable=stmt.iter_borrow_unplaceable))

        # The handler body is a synthesized TpyBreak -- _build_block
        # routes it through _loop_stack to the loop's break_bb, so
        # _loop_stack must be active while the handler is built.
        handler = TpyExceptHandler(
            exception_type="StopAsyncIteration",
            binding=None,
            body=[TpyBreak(loc=stmt.loc)],
            loc=stmt.loc,
        )
        try_region = TryRegion(
            handlers=(handler,),
            finally_helper_name=None,
            loc_source=stmt,
        )

        self._region_stack.append(try_region)
        cond_bb = self._new_bb()
        resume_bb = self._new_bb()
        self._loop_stack.append(_LoopCtx(
            continue_bb=cond_bb,
            break_bb=exit_bb,
            regions_at_entry=tuple(self._region_stack),
        ))
        try:
            self._finish(iter_init_bb, Fall(next_bb=cond_bb))
            dummy_await = TpyAwait(value=stmt.iterable, loc=stmt.loc)
            payload = AwaitPayload(
                mode=AwaitMode.INLINE,
                sub_field_cpp_type="",  # filled in by struct emit
                operand_expr=stmt.iterable,
                kind=AwaitKind.ASSIGN,
                bind_target=stmt.var,
                return_stmt=None,
                host_stmt=stmt,
                await_node=dummy_await,
                async_for_uid=uid,
            )
            yield_term = Yield(
                payload=payload,
                resume_bb=resume_bb,
                suspension_index=len(self._yield_sites),
            )
            self._finish(cond_bb, yield_term)
            self._yield_sites.append(yield_term)
            # Build the synthesized except handler body (single TpyBreak)
            # while the TryRegion is still on the stack so the
            # ExceptRegion has the right parent.
            except_region = ExceptRegion(
                handler=handler,
                parent_finally=None,
                loc_source=stmt,
            )
            self._region_stack.append(except_region)
            try:
                handler_entry = self._new_bb()
                handler_end = self._build_block(handler_entry, handler.body)
                assert handler_end is None  # TpyBreak terminates
                self._record_handler_entry(try_region, handler, handler_entry)
            finally:
                self._region_stack.pop()
        finally:
            self._region_stack.pop()

        # body_bb sits outside the TryRegion -- a user `await` in the
        # body whose result throws StopAsyncIteration propagates up
        # rather than being silently swallowed.
        self._finish(resume_bb, Fall(next_bb=body_bb))
        try:
            body_end = self._build_block(body_bb, stmt.body)
            if body_end is not None:
                self._finish(body_end, Fall(next_bb=cond_bb))
        finally:
            self._loop_stack.pop()

        return exit_bb

    # -- try/except/finally ---------------------------------------------

    def _build_try(self, cur: int, stmt: TpyTry) -> int | None:
        if stmt.tier is TryTier.RETURN and stmt.handled_error_return:
            # Sema classified the handlers as ReturnException ones, so the
            # only thing that can enter them is a failing @error_return
            # call -- never a C++ throw. A decomposed try has no way to
            # spell that edge yet (the handler is another basic block, and
            # the unwrap check has no state transition to take), so the
            # region would emit a catch nothing reaches and the calls in
            # its body would take the unhandled disposition. Reject instead
            # of emitting that. A False flag means no EXPLICIT call: those
            # are admitted at one sema chokepoint
            # (`_check_error_return_handled`), and the implicit caller that
            # would otherwise bypass it -- a `with` header's
            # `__enter__`/`__exit__` -- is rejected in `_analyze_with`. The
            # other implicit callers (an `@error_return` `__getitem__` or
            # `__add__`) never reach this function at all: the record's
            # generated operator bridge returns the raw `std::expected` and
            # the C++ build fails at the class definition
            # (BUGS.md#error-return-operator-bridge-unlocated). So no
            # reachable shape leaves the flag False with a failing call in
            # the body: the handler is unreachable, the dead catch the
            # region emits is harmless, and the shape keeps compiling.
            raise _CFGNotYetSupported(
                "'except StopIteration' (or any other ReturnException type) "
                "is not yet supported inside a generator or 'async def' when "
                "the 'try' needs its own states -- it holds an "
                "'await'/'yield', or a 'break'/'continue' that leaves it. "
                "Move the 'try' into a helper function and call that, or "
                "keep the 'try' free of suspensions and of transfers out of "
                "it.",
                loc=stmt.loc,
            )
        finally_name: str | None = None
        finally_entry_bb: int | None = None
        captured_exc_field: str | None = None
        pending_return_flag: str | None = None
        pending_return_slot: str | None = None
        finally_async = bool(stmt.finally_body) and _stmts_have_any_suspension(
            stmt.finally_body)
        if stmt.finally_body and not finally_async:
            finally_name = f"__finally_{self._next_finally_id}"
            self._next_finally_id += 1
            self._finally_helpers.append((finally_name, list(stmt.finally_body)))
        elif finally_async:
            # CFG-based finally. Pre-scan assigns an exception-slot uid
            # and (if returns are reachable in any of try / handler /
            # finally) a pending-return slot. Nesting two CFG-based
            # finally regions is supported: the inner AsyncFinallyExit
            # forwards its pending state into the outer's parking slot
            # (see `_emit_async_finally_exit`).
            uid = self._try_finally_uid_map.get(id(stmt))
            if uid is None:
                raise _CFGNotYetSupported(
                    "try-finally-with-await reached CFG builder "
                    "without a registered exception-slot uid "
                    "(internal: pre-scan missed it).",
                    loc=stmt.loc,
                )
            captured_exc_field = f"__finally_exc_{uid}"
            # Pending-return slot: allocated when there's a reachable
            # `return` anywhere the CFG-based finally can intercept --
            # try body, any handler body, or the finally body itself
            # (the FinallyRegion mirrors the flag/slot so a return inside
            # the finally body parks into the same place).
            # `pending_return_slot` is None for void async defs (the
            # flag alone suffices).
            try_has_return = (_stmts_have_any_return(stmt.try_body)
                               or any(_stmts_have_any_return(h.body)
                                       for h in stmt.handlers)
                               or _stmts_have_any_return(stmt.finally_body))
            if try_has_return:
                pending_return_flag = f"__finally_pending_{uid}"
                # Void async defs need only the flag (no value to park);
                # leave `pending_return_slot` None so emit sites can
                # consult one source of truth (the slot name) instead
                # of also re-deriving void-ness.
                if self._func_returns_void:
                    pending_return_slot = None
                else:
                    pending_return_slot = f"__finally_ret_{uid}"
            else:
                pending_return_flag = None
                pending_return_slot = None

        # Pre-allocate finally_entry_bb + finally_exit_bb (CFG-based
        # finally only) so TryRegion / FinallyRegion can carry them.
        # `finally_exit_bb` is the single replay site holding the
        # AsyncFinallyExit synth -- both normal fall-through from the
        # finally body and a return-in-finally jump here, which gives
        # the deferred-return replay a home outside the body's last BB.
        finally_region: FinallyRegion | None = None
        finally_exit_bb: int | None = None
        if finally_async:
            assert captured_exc_field is not None
            # finally_exit_bb is OUTSIDE the FinallyRegion -- by the time
            # AsyncFinallyExit runs, the finally body is done.
            finally_exit_bb = self._new_bb()
            finally_region = FinallyRegion(
                helper_name=captured_exc_field,
                loc_source=stmt,
                pending_return_flag=pending_return_flag,
                pending_return_slot=pending_return_slot,
                finally_exit_bb=finally_exit_bb,
            )
            self._region_stack.append(finally_region)
            finally_entry_bb = self._new_bb()
            self._region_stack.pop()

        try_region = TryRegion(
            handlers=tuple(stmt.handlers),
            finally_helper_name=finally_name,
            loc_source=stmt,
            finally_entry_bb=finally_entry_bb,
            captured_exc_field=captured_exc_field,
            pending_return_flag=pending_return_flag,
            pending_return_slot=pending_return_slot,
        )
        # join_bb is outside the try frame.
        join_bb = self._new_bb()

        # Target for normal try-body exit: finally_entry when CFG-based
        # finally (so finally body runs before reaching join); join
        # otherwise.
        normal_exit_target = (finally_entry_bb if finally_async
                              else join_bb)

        # Try body BBs live inside the TryRegion. Push the region BEFORE
        # creating them so their region_stack captures correctly.
        self._region_stack.append(try_region)
        try:
            try_body_bb = self._new_bb()
            self._finish(cur, Fall(next_bb=try_body_bb))
            try_end = self._build_block(try_body_bb, stmt.try_body)
            if try_end is not None:
                if stmt.else_body:
                    else_end = self._build_block(try_end, stmt.else_body)
                    if else_end is not None:
                        self._finish(else_end,
                                      Fall(next_bb=normal_exit_target))
                else:
                    self._finish(try_end,
                                  Fall(next_bb=normal_exit_target))
        finally:
            self._region_stack.pop()

        # Except-handler bodies: the corresponding TryRegion is no
        # longer active inside the handler (the throw has been caught);
        # the ExceptRegion takes its place so emit knows we're in a
        # handler body (for sub-future reset, exception-binding scope).
        # When the parent try has a CFG-based finally, the
        # ExceptRegion also carries the parent's captured_exc /
        # finally_entry so emit can route raises and returns inside
        # the handler body through the finally region.
        for handler in stmt.handlers:
            except_region = ExceptRegion(
                handler=handler,
                parent_finally=try_region.finally_helper_name,
                parent_finally_entry_bb=try_region.finally_entry_bb,
                parent_pending_return_flag=try_region.pending_return_flag,
                parent_pending_return_slot=try_region.pending_return_slot,
                loc_source=stmt,
            )
            self._region_stack.append(except_region)
            try:
                handler_entry = self._new_bb()
                handler_end = self._build_block(handler_entry, handler.body)
                if handler_end is not None:
                    # Handler's normal exit: when finally is CFG-based,
                    # route through finally entry so the finally body
                    # runs before reaching the post-try join.
                    self._finish(handler_end,
                                  Fall(next_bb=normal_exit_target))
                self._record_handler_entry(try_region, handler, handler_entry)
            finally:
                self._region_stack.pop()

        # Finally body (CFG-based path).
        if finally_async:
            assert finally_region is not None
            assert finally_entry_bb is not None
            assert finally_exit_bb is not None
            assert captured_exc_field is not None
            self._region_stack.append(finally_region)
            try:
                finally_end = self._build_block(
                    finally_entry_bb, stmt.finally_body)
                if finally_end is not None:
                    # Normal fall-through: route through finally_exit_bb
                    # so AsyncFinallyExit (rethrow + deferred-return
                    # replay) runs at the single replay site. A
                    # return-in-finally inside the body also jumps to
                    # finally_exit_bb (via the FinallyRegion's parking
                    # info -- see gen_async._pending_return_info_for_region_stack).
                    self._finish(finally_end, Fall(next_bb=finally_exit_bb))
            finally:
                self._region_stack.pop()
            # AsyncFinallyExit lives at the dedicated exit BB; fall to
            # join from there.
            self._blocks[finally_exit_bb].stmts.append(
                AsyncFinallyExit(
                    captured_exc_field=captured_exc_field,
                    pending_return_flag=pending_return_flag,
                    pending_return_slot=pending_return_slot,
                ))
            self._finish(finally_exit_bb, Fall(next_bb=join_bb))

        return join_bb

    def _record_handler_entry(self, region: TryRegion,
                               handler: TpyExceptHandler,
                               entry_bb: int) -> None:
        # Stash on a per-builder side map keyed by (id(region), id(handler))
        # so the emit can find handler entries without mutating the
        # frozen region dataclass.
        self._handler_entries[(id(region), id(handler))] = entry_bb

    def get_handler_entry(self, region: TryRegion,
                           handler: TpyExceptHandler) -> int | None:
        return self._handler_entries.get((id(region), id(handler)))

    # -- with -----------------------------------------------------------

    def _build_with(self, cur: int, stmt: TpyWith) -> int | None:
        """Lower a sync `with X as t: body` whose body contains await
        into a CFG region. The context manager lives in a frame-hoisted
        field (`__with_ctx_<n>`); the case-emit wraps each case body in
        try/catch that runs `__exit__` on exception (with optional
        suppression -> transition to post-with state).

        Async `with` (`stmt.is_async`) routes to a separate path: the
        `__aenter__` and `__aexit__` calls are themselves suspensions
        and need Yield BBs, modelled via a synthetic TryRegion whose
        CFG-based finally body holds the `__aexit__` yield. See
        `_build_async_with`."""
        if stmt.is_async:
            return self._build_async_with(cur, stmt)
        ctx_ns = self._with_uid_map.get(id(stmt))
        if ctx_ns is None:
            raise _CFGNotYetSupported(
                "with-stmt with await reached CFG builder without "
                "registered ctx uids (internal: pre-scan missed it).",
                loc=stmt.loc,
            )
        if len(ctx_ns) != len(stmt.items):
            raise _CFGNotYetSupported(
                "with-stmt ctx uid count mismatch (internal).",
                loc=stmt.loc,
            )
        post_with_bb = self._new_bb()
        # For each item, push WithRegion then emit WithEnter into
        # the current BB. Items are processed in source order
        # (outer-most CM enters first).
        regions: list[WithRegion] = []
        for item, ctx_n in zip(stmt.items, ctx_ns):
            region = WithRegion(
                item=item,
                ctx_n=ctx_n,
                post_with_bb=post_with_bb,
                loc_source=stmt,
            )
            self._blocks[cur].stmts.append(
                WithEnter(ctx_n=ctx_n, item=item))
            self._region_stack.append(region)
            regions.append(region)
        try:
            # Body BBs live inside all WithRegions.
            body_entry = self._new_bb()
            self._finish(cur, Fall(next_bb=body_entry))
            body_end = self._build_block(body_entry, stmt.body)
            if body_end is not None:
                # Fall out -> exit BB. Region exit (normal __exit__) is
                # emitted via `_emit_exit_region_finallies` when the
                # transition crosses out of the With regions.
                self._finish(body_end, Fall(next_bb=post_with_bb))
        finally:
            # Pop regions in reverse so the region_stack is clean
            # outside this with.
            for _ in regions:
                self._region_stack.pop()
        return post_with_bb

    # -- async with -----------------------------------------------------

    def _build_async_with(self, cur: int, stmt: TpyWith) -> int | None:
        """Lower Python `async with X1 as a, X2 as b: body` (v1.5 M5).

        Each item is decomposed left-to-right into:
          AsyncWithSetup -> Yield(__aenter__) -> [TryRegion {
              <recurse to next item, or body>
          } finally {
              Yield(__aexit__) ; AsyncFinallyExit
          }]

        The TryRegion's finally body re-uses the M3.3 CFG-based-finally
        machinery: a `__finally_exc_<uid>` frame slot saves any in-flight
        exception via `std::current_exception()`; AsyncFinallyExit at
        the tail rethrows. `return` inside the body parks the value in
        `__finally_pending_<uid>` / `__finally_ret_<uid>` and walks
        through the same finally path (same as M3.3.2).

        v1.5 M5 cleanup-only: `__aexit__` is called with all-None args
        regardless of whether an exception was caught. Inspecting
        `exc_val: Optional[BaseException]` is deferred to the
        polymorphic-exception-storage milestone (E9).
        """
        ctx_ns = self._with_uid_map.get(id(stmt))
        if ctx_ns is None:
            raise _CFGNotYetSupported(
                "async-with reached CFG builder without registered "
                "ctx uids (internal: pre-scan missed it).",
                loc=stmt.loc,
            )
        if len(ctx_ns) != len(stmt.items):
            raise _CFGNotYetSupported(
                "async-with ctx uid count mismatch (internal).",
                loc=stmt.loc,
            )
        finally_uid = self._try_finally_uid_map.get(id(stmt))
        if finally_uid is None:
            raise _CFGNotYetSupported(
                "async-with reached CFG builder without registered "
                "finally-exc uid (internal: pre-scan missed it).",
                loc=stmt.loc,
            )
        # Multi-item `async with X as a, Y as b:`: would left-to-right
        # desugar to nested `async with` using the same finally_uid, so
        # the items would collide on the shared `__finally_exc_<n>` /
        # `__finally_pending_<n>` / `__finally_ret_<n>` fields. Reject
        # for now; users can nest two `async with` statements instead.
        if len(stmt.items) != 1:
            raise _CFGNotYetSupported(
                "multi-item `async with X as a, Y as b:` is not yet "
                "supported -- nest two `async with` statements instead",
                loc=stmt.loc,
            )
        # An `async with` synthesizes its own TryRegion with a CFG-based
        # finally body (the `__aexit__` Yield). Plain try/finally
        # nesting was lifted by extending AsyncFinallyExit to forward
        # parked state outward; the async-with case has not been lifted
        # because the synthesized finally also needs to keep its
        # __aexit__ call ordering correct across forwarding.
        for r in self._region_stack:
            if (isinstance(r, TryRegion)
                    and r.captured_exc_field is not None):
                raise _CFGNotYetSupported(
                    "`async with` nested inside another `await`-in-"
                    "finally region is a planned follow-up.",
                    loc=stmt.loc,
                )

        item = stmt.items[0]
        ctx_n = ctx_ns[0]
        captured_exc_field = f"__finally_exc_{finally_uid}"

        # 1. AsyncWithSetup populates the __with_ctx_<n> frame slot.
        self._blocks[cur].stmts.append(
            AsyncWithSetup(ctx_n=ctx_n, item=item))

        # 2. Yield for `__aenter__()`. Synthesize a minimal TpyAwait so
        # the payload's dataclass invariants hold; emit branches on
        # `async_with_kind` and never reads the AST. The sub-coro
        # struct's C++ name is resolved at codegen time by looking up
        # `_async_with_struct_names[ctx_n]` (populated by the prescan).
        dummy_await = TpyAwait(value=item.context_expr, loc=stmt.loc)
        aenter_resume_bb = self._new_bb()
        aenter_payload = AwaitPayload(
            mode=AwaitMode.INLINE,
            sub_field_cpp_type="",  # emit fills this from the CM type
            operand_expr=item.context_expr,
            kind=(AwaitKind.ASSIGN if item.target is not None
                  else AwaitKind.DISCARD),
            bind_target=item.target,
            return_stmt=None,
            host_stmt=stmt,
            await_node=dummy_await,
            async_with_kind=AsyncWithKind.AENTER,
            async_with_ctx_n=ctx_n,
        )
        aenter_yield = Yield(
            payload=aenter_payload,
            resume_bb=aenter_resume_bb,
            suspension_index=len(self._yield_sites),
        )
        self._finish(cur, aenter_yield)
        self._yield_sites.append(aenter_yield)

        # 3. Build TryRegion with CFG-based finally. The body is
        # stmt.body; the finally body holds the __aexit__ Yield +
        # AsyncFinallyExit.
        pending_return_flag = None
        pending_return_slot = None
        if _stmts_have_any_return(stmt.body):
            pending_return_flag = f"__finally_pending_{finally_uid}"
            if not self._func_returns_void:
                pending_return_slot = f"__finally_ret_{finally_uid}"

        # Build finally body BB (with FinallyRegion on stack).
        finally_region = FinallyRegion(
            helper_name=captured_exc_field,
            loc_source=stmt,
        )
        self._region_stack.append(finally_region)
        finally_entry_bb = self._new_bb()
        self._region_stack.pop()

        try_region = TryRegion(
            handlers=(),
            finally_helper_name=None,
            loc_source=stmt,
            finally_entry_bb=finally_entry_bb,
            captured_exc_field=captured_exc_field,
            pending_return_flag=pending_return_flag,
            pending_return_slot=pending_return_slot,
        )
        join_bb = self._new_bb()

        # Try body lives inside TryRegion.
        self._region_stack.append(try_region)
        try:
            try_body_entry = self._new_bb()
            self._finish(aenter_resume_bb, Fall(next_bb=try_body_entry))
            body_end = self._build_block(try_body_entry, stmt.body)
            if body_end is not None:
                self._finish(body_end, Fall(next_bb=finally_entry_bb))
        finally:
            self._region_stack.pop()

        # Finally body: Yield for __aexit__ + AsyncFinallyExit + Fall
        # to join.
        self._region_stack.append(finally_region)
        try:
            dummy_aexit_await = TpyAwait(
                value=item.context_expr, loc=stmt.loc)
            aexit_resume_bb = self._new_bb()
            aexit_payload = AwaitPayload(
                mode=AwaitMode.INLINE,
                sub_field_cpp_type="",
                operand_expr=item.context_expr,
                kind=AwaitKind.DISCARD,
                bind_target=None,
                return_stmt=None,
                host_stmt=stmt,
                await_node=dummy_aexit_await,
                async_with_kind=AsyncWithKind.AEXIT,
                async_with_ctx_n=ctx_n,
            )
            aexit_yield = Yield(
                payload=aexit_payload,
                resume_bb=aexit_resume_bb,
                suspension_index=len(self._yield_sites),
            )
            self._finish(finally_entry_bb, aexit_yield)
            self._yield_sites.append(aexit_yield)
            # After aexit resume: AsyncFinallyExit (rethrow saved exc if
            # any, deferred return if pending), then Fall to join.
            self._blocks[aexit_resume_bb].stmts.append(
                AsyncFinallyExit(
                    captured_exc_field=captured_exc_field,
                    pending_return_flag=pending_return_flag,
                    pending_return_slot=pending_return_slot,
                ))
            self._finish(aexit_resume_bb, Fall(next_bb=join_bb))
        finally:
            self._region_stack.pop()

        return join_bb

    # -- match -----------------------------------------------------------

    def _build_match(self, cur: int, stmt: TpyMatch) -> int | None:
        """Lower a `match` whose arm bodies contain a suspension. The
        dispatch stays a single suspension-free unit (emitted later by
        reusing the ordinary match dispatch); each arm body becomes its
        own BB chain (recursively built, so nested suspensions decompose),
        joining at `join_bb`. `match` introduces no region, so arm BBs and join
        share `cur`'s region stack -- no finally-chain delta on the
        dispatch->arm or arm->join transitions.

        A guard or the subject expression cannot host a suspension (those
        appear only at statement position), so the dispatch is always
        suspension-free; only arm bodies suspend."""
        join_bb = self._new_bb()
        arm_bbs: list[int] = []
        all_terminate = True
        for case in stmt.cases:
            arm_bb = self._new_bb()
            arm_bbs.append(arm_bb)
            # The arm's pattern/guard narrowing (case.type_facts) applies to
            # the body; stamp the arm BBs so a suspension inside the arm
            # re-establishes the subject narrowing at the resume case (parallel
            # to _build_if). The pattern test + guard run in the suspension-free
            # dispatch, so only case.body -- which executes after they succeed
            # -- is stamped.
            self._blocks[arm_bb].entry_narrowings = {
                **self._active_narrowings, **case.type_facts}
            prev = self._push_narrowings(case.type_facts)
            arm_end = self._build_block(arm_bb, case.body)
            self._pop_narrowings(prev)
            if arm_end is not None:
                self._finish(arm_end, Fall(next_bb=join_bb))
                all_terminate = False
        # When the match is exhaustive AND every arm terminates, no
        # control path reaches past the match: the dispatch has no
        # fall-through and join is unreachable.
        reachable_join = None if (stmt.is_exhaustive and all_terminate) else join_bb
        self._finish(cur, MatchDispatch(
            match_stmt=stmt,
            arm_bbs=tuple(arm_bbs),
            join_bb=reachable_join,
        ))
        return reachable_join

    # -- post-construction passes ---------------------------------------

    def _mark_resume_entries(self, cfg: CFG) -> None:
        for y in cfg.yield_sites:
            cfg.blocks[y.resume_bb].is_resume_entry = True


# -- Errors ---------------------------------------------------------------

class _CFGNotYetSupported(Exception):
    """Raised when the CFG builder hits a shape that's known but not yet
    implemented. Caller catches and converts to a CodeGenError with the
    appropriate location.
    """
    def __init__(self, msg: str, loc) -> None:
        super().__init__(msg)
        self.msg = msg
        self.loc = loc


# -- Helpers (top-level, used by gen_async too) ---------------------------

def _top_level_await_in(stmt: TpyStmt) -> TpyAwait | None:
    """Return the TpyAwait if `stmt` is one of the top-level
    statement-position await shapes: assign with await RHS, vardecl
    with await init, return with await value, or expression-statement
    with bare await. Returns None otherwise.
    """
    value: TpyExpr | None
    if isinstance(stmt, TpyAssign):
        value = stmt.value
    elif isinstance(stmt, TpyVarDecl):
        value = stmt.init
    elif isinstance(stmt, TpyReturn):
        value = stmt.value
    elif isinstance(stmt, TpyExprStmt):
        value = stmt.expr
    else:
        value = None
    if isinstance(value, TpyAwait):
        return value
    return None


def _classify_await_position(
    stmt: TpyStmt, await_node: TpyAwait,
) -> tuple[AwaitKind, str | None, TpyReturn | None]:
    """Return (kind, bind_target, return_stmt) for a top-level await."""
    if isinstance(stmt, TpyAssign) and stmt.value is await_node:
        target = stmt.target
        if not isinstance(target, TpyName):
            raise _CFGNotYetSupported(
                "await result can only be bound to a simple name in v1; "
                "field/index targets are not yet supported.",
                loc=stmt.loc,
            )
        return (AwaitKind.ASSIGN, target.name, None)
    if isinstance(stmt, TpyVarDecl) and stmt.init is await_node:
        return (AwaitKind.VARDECL, stmt.name, None)
    if isinstance(stmt, TpyReturn) and stmt.value is await_node:
        return (AwaitKind.RETURN, None, stmt)
    if isinstance(stmt, TpyExprStmt) and stmt.expr is await_node:
        return (AwaitKind.DISCARD, None, None)
    raise _CFGNotYetSupported(
        "internal: _classify_await_position called on unsupported stmt.",
        loc=stmt.loc,
    )






def _stmt_has_unbound_loop_transfer(stmt: TpyStmt) -> bool:
    """True if `stmt` directly or transitively contains a break/continue
    that targets the enclosing loop (not bound by an inner loop in this
    statement). Used to decide whether a leaf compound (if/try/with)
    inside a CFG-decomposed loop needs decomposition so its break /
    continue translate to state transitions.

    Stops at nested loops (while/for): their own break/continue bind
    to them and stay as C++ break/continue (the nested loop is a
    leaf compound)."""
    if isinstance(stmt, (TpyBreak, TpyContinue)):
        return True
    # Nested loop swallows its own break/continue.
    if isinstance(stmt, (TpyWhile, TpyForEach)):
        return False
    if hasattr(stmt, "sub_bodies"):
        for body in stmt.sub_bodies():
            for s in body:
                if _stmt_has_unbound_loop_transfer(s):
                    return True
    return False


