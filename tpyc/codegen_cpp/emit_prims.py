"""Emit primitives shared by the structural skeleton and the AST body emitter.

Gate D3 fixed the migration's end state as bodies-only: `expressions`,
`statements`, `match` and `builtins` are deleted in one cutover commit, while
the structural skeleton (module driver, signatures, record/protocol/enum
drivers, the resumable + simple-generator frames) stays as the permanent
printer layer. The resumable frame skeleton needs a handful of small
predicates, type decisions and fragment renders that happened to live in
`statements` / `expressions`. Those survive the deletion, so they cannot live
in a module being deleted -- they live here instead.

Everything in this module is a self-contained predicate, type decision, ctx
mutation or fragment render: none of it dispatches a statement or an
arbitrary expression through a body emitter. (`compute_borrow_tuple_const`
and the per-scope setup around it do read the function body -- a const-ness
prescan over binding sources -- but they emit nothing.) That is the property
that lets the cutover gate hold, and `test_cutover_gate.py` asserts
mechanically that this module imports none of the four body emitters.

The functions take their collaborators (`ctx`, the type resolver, the
protocol generator) explicitly rather than living on a generator object: both
the skeleton and the body emitter call them, and neither owns them.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator, TextIO, TYPE_CHECKING

from .. import binding_audit
from ..namespace import Namespace
from ..parse.nodes import (
    TpyAssign, TpyBinOp, TpyExpr, TpyFieldAccess, TpyFunction, TpyIfExpr,
    TpyMethodCall, TpyName, TpyNoneLiteral, TpyStmt, TpySubscript,
    TpyTupleUnpack, TpyUnaryOp, TpyVarDecl, walrus_bindings,
)
from ..prescan import parse_deref_view_key
from ..sema.literal_utils import fixed_int_literal_value_from_expr
from ..sema.registration import build_record_self_type
from ..type_def_registry import is_fixed_int_type
from ..typesys import (
    FLOAT, AnyType, FloatLiteralType, LiteralType, NominalType, OptionalType,
    OwnType, PendingViewType, PtrType, ReadonlyType, TpyType, TupleType,
    UnionType, VoidType,
    collapse_tuple_own_elements, error_return_to_cpp, is_dyn_protocol,
    is_own_pointer_repr_optional, is_polymorphic_subclass_fact,
    is_protocol_type, is_void_like_type, polymorphic_source_inner,
    polymorphic_subclass_into_optional, resolve_int_literals, unwrap_optional_own,
    unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from .context import INDENT, FinallyContext, escape_cpp_name
from .type_resolution import resolve_stmt_binding_type
from .types import resolve_pending_container
from .variant_access import VariantAccess

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .protocols import ProtocolGenerator
    from .types import TypeResolver


# -- signature rendering ---------------------------------------------------

def nested_def_signature(types: 'TypeResolver',
                         func: TpyFunction) -> 'tuple[str, str | None]':
    """(params_str, ret_cpp-or-None-for-void) for a nested def -- shared
    by the lambda emission and the resumable-frame member emission."""
    params = []
    for pname, ptype in func.params:
        resolved = types.resolve_type(ptype)
        cpp_name = escape_cpp_name(pname)
        params.append(resolved.to_cpp_param(cpp_name))
    return_type = types.resolve_type(func.return_type)
    ret_cpp = (None if isinstance(return_type, VoidType)
               else types.type_to_cpp(return_type))
    return ", ".join(params), ret_cpp


# -- local-form predicates -------------------------------------------------

def is_plain_nonvalue(ctx: 'CodeGenContext', t: TpyType) -> bool:
    # Recursive-union wrappers are reference types like records: a local
    # bound from a reference source (`g = h.get()`) binds `Tree<T>&`, a
    # fresh value (`t = [1, 2]`) stays by value -- the is_rvalue_source
    # rule in _needs_indirection draws that line (mirrors list/dict/record).
    return ctx.is_plain_nonvalue(t)


def is_const_indirect(ctx: 'CodeGenContext', target_type: TpyType | None,
                      init: TpyExpr | None,
                      stmt: 'TpyVarDecl | None' = None) -> bool:
    """Check if a local variable should use const indirection (const T* or const T&).

    Detects when the variable is derived from a ReadonlyType source:
    - Optional inner is ReadonlyType (None-seeded from readonly param)
    - Init expression has ReadonlyType in sema (direct alias of readonly param)
    - Sema var_types holds ReadonlyType for annotated locals that are later
      reassigned from a readonly source
    """
    if isinstance(target_type, OptionalType) and isinstance(target_type.inner, ReadonlyType):
        return True
    if init is not None:
        sema_type = ctx.analyzer.get_expr_type(init)
        if isinstance(sema_type, ReadonlyType):
            return True
    # For annotated Optional locals, sema var_types may hold
    # OptionalType(ReadonlyType(T)) even when stmt.type is plain Optional[T].
    if stmt is not None:
        sema_var_type = ctx.analyzer.var_types.get(id(stmt))
        if (isinstance(sema_var_type, OptionalType)
                and isinstance(sema_var_type.inner, ReadonlyType)):
            return True
    # Readonly method call returns const T& -> variable needs const indirection.
    # (TypeParamRef returns are handled separately via val_or_cref_t in _gen_local_var_decl.)
    if isinstance(init, TpyMethodCall):
        fi = init.resolved_function_info
        if fi is not None and fi.is_readonly and ctx._call_returns_cpp_ref(fi):
            return True
    # Operator dispatch mirrors the method-call arm: a readonly dunder's
    # borrow return is const-projected at emit, so the alias binds const.
    if isinstance(init, TpyBinOp) and init.resolved_binop is not None:
        fi = init.resolved_binop.method
        if fi.is_readonly and ctx._call_returns_cpp_ref(fi):
            return True
    if isinstance(init, TpyUnaryOp) and init.resolved_unaryop is not None:
        fi = init.resolved_unaryop.method
        if fi.is_readonly and ctx._call_returns_cpp_ref(fi):
            return True
    # A subscript / method call on a const-rooted receiver binds const:
    # when the enclosing method's readonly-ness is INFERRED (post
    # body-analysis), sema resolved the MUTABLE twin, so the
    # fi.is_readonly arms above miss -- but C++ overload resolution on
    # the const receiver picks the const twin regardless. Sound for the
    # same reason as the name-alias arm below: a const-rooted source
    # implies inference proved no writes through the result.
    if isinstance(init, (TpySubscript, TpyMethodCall)) \
            and ctx.is_const_storage_source(init.obj):
        return True
    # An alias of a const-inferred source must also bind const, else a
    # mutable reference/pointer would be taken from a const source. Sound
    # because a const source implies the alias is never written through --
    # a write would have marked the source mutated via the borrow chain.
    # Covers the pointer-local (Optional) branch, which the T&-branch
    # call-site propagation does not reach.
    if isinstance(init, TpyName) and (
            init.name in ctx.const_ref_params
            or init.name in ctx.const_indirect_locals
            or init.name in ctx.deep_const_borrow_params):
        return True
    return False


def is_const_borrow_source(ctx: 'CodeGenContext', var_name: str,
                          var_decl: 'TpyType | None') -> bool:
    return (isinstance(var_decl, ReadonlyType)
            or var_name in ctx.deep_const_borrow_params
            or var_name in ctx.const_ref_params)


def unpack_source_has_const_slots(ctx: 'CodeGenContext',
                                  stmt: TpyTupleUnpack) -> bool:
    """True when the unpack source has const-typed borrow slots.

    Triggered when the source is a name referring to either:
    - a const-inferred param (deep_const_borrow_params), or
    - a synthesized for-loop tuple iterating a const-bound source
      (const_storage_form_tuple_locals).
    Drives the unpack codegen to emit `const T*` / `const T&` for
    unpacked locals rather than `T*` / `T&` (which would fail to bind
    from the const slot).
    """
    if not isinstance(stmt.value, TpyName):
        return False
    return (stmt.value.name in ctx.deep_const_borrow_params
            or stmt.value.name in ctx.const_storage_form_tuple_locals)


def ptr_slot_field_type(ctx: 'CodeGenContext', init: 'TpyExpr',
                        target_type: TpyType | None,
                        cpp_type: str) -> str | None:
    """The frame-field C++ type an rvalue write into a pointer-form
    frame local needs for its materialization slot, or None when the
    write materializes no storage (None literal, alias, optional_to_ptr
    lift forms).

    Single source of truth shared by the resumable ptr-slot prescan
    (which reserves the field) and the emit arms in
    `_gen_pointer_local_rebind` (which consume it): the two MUST agree
    on which writes need a slot, or a frame write lands back in a
    dying case-block local -- the emit arms raise on a missing entry
    rather than fall back. The routing order mirrors the emit arms.
    """
    if isinstance(init, TpyNoneLiteral):
        return None
    if ctx.callee_returns_own_ptr_optional(init):
        # Own[Optional[T]]-returning call: the slot holds the returned
        # optional<T> whole; the pointer is lifted via optional_to_ptr.
        return f"std::optional<{cpp_type}>"
    if (isinstance(init, TpyName)
            and ctx.needs_optional_to_ptr_lift(init.name)):
        return None
    init_type = ctx.get_expr_type(init)
    if isinstance(init_type, OptionalType) and init_type.uses_pointer_repr():
        if (ctx.is_storage_form_optional_source(init)
                and not ctx.is_rvalue_source(init)):
            return None
        if not ctx.is_value_emit_rvalue(init):
            return None
    if not ctx.is_rvalue_source(init):
        return None
    # Polymorphic refinement: a per-site slot can hold the subclass
    # whole (the shared-slot slicing reject still fires upstream for
    # the sync-shared shapes; this only spells the site's storage).
    slot_cpp = cpp_type
    sub = polymorphic_subclass_into_optional(
        target_type, init_type, ctx.analyzer.registry)
    if sub is not None:
        slot_cpp = sub.name
    return f"std::optional<{slot_cpp}>"


def promote_movable(ctx: 'CodeGenContext', name: str) -> None:
    """Promote a sema-owned local into the body's working movable set.

    `sema_movable_locals` is the RAW per-function fact ("sema proved this
    local owned"); `movable_locals` is what the move sites actually read,
    and a name joins it only when its declaration reaches a decl arm that
    promotes. The arms that DON'T call this -- ptr-variant unions,
    non-Own @dynamic protocol locals, `val_or_ref_t` TypeParamRef locals,
    REF_ALIAS borrows, frame-promoted POINTER locals -- alias rather than
    own, so a last-use read there must copy, not steal. Grep this function's
    callers for the authoritative promoting-arm set: THIR's `_is_move_source`
    mirrors exactly it, and the two sets silently diverging is a move-vs-copy
    miscompile no byte-diff can see.
    """
    if name in ctx.sema_movable_locals:
        ctx.movable_locals.add(name)


# -- declared-type resolution ----------------------------------------------

def cpp_declared_type(ctx: 'CodeGenContext',
                      expr: TpyExpr) -> TpyType | None:
    """Get the C++ declared type of a variable or field access.

    For names, checks codegen var_types and current_func_params.
    For field access (obj.field), resolves the field's declared type
    on the record, which may be Optional even when sema has narrowed it.

    MUST stay local-only for names: callers like the union-arg
    already-variant check rely on module globals resolving None. The
    narrowed-Optional family uses `declared_type_incl_globals`.
    """
    if isinstance(expr, TpyName):
        return ctx.var_types.get(expr.name) or ctx.current_func_params.get(expr.name)
    if isinstance(expr, TpyFieldAccess):
        return resolve_field_declared_type(ctx, expr)
    return None


def declared_type_incl_globals(ctx: 'CodeGenContext',
                               expr: TpyExpr) -> TpyType | None:
    """`cpp_declared_type` extended to module globals.

    Only the narrowed-Optional unwrap family consults globals: a
    narrowed global read must see its declared (possibly Optional)
    type. Other `cpp_declared_type` callers (e.g. the union-arg
    already-variant check) rely on globals resolving None."""
    declared = cpp_declared_type(ctx, expr)
    if declared is not None:
        return declared
    if isinstance(expr, TpyName) and ctx.is_global_name(expr):
        binding = ctx.analyzer.global_ns.lookup_local(expr.name)
        if binding is not None:
            return binding.type
    return None


def resolve_field_declared_type(ctx: 'CodeGenContext',
                                expr: TpyFieldAccess) -> TpyType | None:
    """Resolve the declared type of a field on its record/object."""
    obj_type = cpp_declared_type(ctx, expr.obj)
    if obj_type is None:
        obj_type = ctx.get_expr_type(expr.obj)
    if obj_type is None:
        return None
    actual_type = unwrap_readonly(obj_type)
    if isinstance(actual_type, PtrType):
        actual_type = actual_type.pointee
    elif isinstance(actual_type, OwnType):
        actual_type = actual_type.wrapped
    elif isinstance(actual_type, OptionalType):
        if actual_type.inner.is_value_type():
            return None
        actual_type = actual_type.inner
    if isinstance(actual_type, NominalType) and actual_type.is_record:
        record = ctx.analyzer.registry.get_record_for_type(actual_type)
        if record:
            for f in record.fields:
                if f.name == expr.field:
                    return f.type
    return None


def maybe_unwrap_narrowed_optional(ctx: 'CodeGenContext', expr_obj: TpyExpr,
                                   obj: str, needs_deref: bool,
                                   target_type: TpyType | None = None) -> str:
    """Unwrap narrowed Optional receivers.

    When sema has proven a std::optional<T> variable holds a value,
    the C++ variable is still optional -- dereference it with (*obj).
    Handles simple names, field accesses, and generator-promoted
    Optional locals (whose rendered `obj` is already the inner
    storage-form Optional after the outer init-tracking deref).
    When `target_type` is given, the unwrap only fires for a
    non-Optional target -- an Optional target keeps the whole copy.
    """
    if needs_deref:
        return obj
    if (target_type is not None
            and isinstance(unwrap_readonly(target_type), OptionalType)):
        return obj
    cpp_decl = declared_type_incl_globals(ctx, expr_obj)
    analyzed = ctx.get_expr_type(expr_obj)
    is_comp_var = isinstance(expr_obj, TpyName) and expr_obj.name in ctx.comp_local_names
    is_storage_optional = ctx.is_storage_form_optional_source(expr_obj)
    if (cpp_decl is not None
            and isinstance(cpp_decl, OptionalType)
            and (isinstance(expr_obj, TpyFieldAccess) or is_comp_var
                 or is_storage_optional
                 or not cpp_decl.uses_pointer_repr())
            and not isinstance(analyzed, OptionalType)):
        return f"(*{obj})"
    return obj


def narrowed_value_optional_iter_type(ctx: 'CodeGenContext', expr: TpyExpr,
                                      declared: TpyType | None) -> TpyType | None:
    """For a for-loop iterable: if `declared` is a narrowed value-Optional
    (`str|None`/`bytes|None` proven non-None, value-repr -- still
    `std::optional<V>` in C++), return its narrowed inner so dispatch and
    frame-field types use the contained value `V`. Pointer-repr Optionals
    (e.g. `list|None`) already deref via the pointer-narrowing path, so they
    pass through unchanged. The matching `(*v)` render is applied separately
    via `maybe_unwrap_narrowed_optional`. Shared by the sync for-loop
    (`_for_iterable_deref` / `_gen_for_each_loop`) and the resumable-frame
    strategy analysis (`_analyze_for_strategy`)."""
    analyzed = ctx.get_expr_type(expr)
    if (analyzed is not None
            and isinstance(declared, OptionalType)
            and not declared.uses_pointer_repr()
            and not isinstance(analyzed, OptionalType)):
        return unwrap_ref_type(analyzed)
    return declared


# -- range-loop literals ---------------------------------------------------

def extract_int_literal(expr: TpyExpr) -> int | None:
    """Extract a compile-time integer value from a range argument.

    Handles bare literals (3), negated literals (-3), and fixed-int
    constructor calls with a literal arg (Int32(3)).
    Returns the integer value or None if not a compile-time constant.
    """
    return fixed_int_literal_value_from_expr(expr)


def gen_range_overflow_check(out: TextIO, indent: str, start_expr: str,
                             stop_expr: str, step_expr: str,
                             elem_type: TpyType) -> None:
    """Emit upfront overflow check for fixed-int range loops with step != +/-1."""
    if is_fixed_int_type(elem_type):
        cpp_t = elem_type.to_cpp()
        out.write(f"{indent}::tpy::range_check_overflow<{cpp_t}>({start_expr}, {stop_expr}, {step_expr});\n")


# -- fragment emitters -----------------------------------------------------

def emit_except_handler_header(ctx: 'CodeGenContext', out: TextIO,
                               handler) -> None:
    """Emit a single ` catch (...) {` clause header for `handler`.
    Caller is responsible for emitting the handler body and the
    closing `}`. Shared by sync try/except codegen and the
    async-await try/except path in `gen_async.py`.
    """
    if handler.exception_type is None:
        out.write(" catch (...) {\n")
        return
    cpp_type = error_return_to_cpp(
        handler.exception_type,
        ctx.analyzer.ctx.module_name,
        ctx.analyzer.registry)
    if handler.binding:
        binding = escape_cpp_name(handler.binding)
        out.write(f" catch (const {cpp_type}& {binding}) {{\n")
    else:
        out.write(f" catch (const {cpp_type}&) {{\n")


def push_finally(ctx: 'CodeGenContext',
                 emit_finally: Callable[[TextIO, str], None],
                 terminates: bool) -> FinallyContext:
    """Push a finally frame onto the active stack.

    loop_depth captures len(loop_else_labels) at push time so
    break/continue can identify finally frames inside the innermost
    active loop body.

    The guard name is allocated eagerly (an exit site inside the body
    needs it while the body emits) but only declared if an exit site
    actually used it -- see FinallyContext.guard_name.
    """
    ctx.finally_guard_counter += 1
    guard = f"__fin_ran_{ctx.finally_guard_counter}"
    fctx = FinallyContext(
        emit_finally=emit_finally,
        terminates=terminates,
        loop_depth=len(ctx.loop_else_labels),
        guard_name=guard,
    )
    ctx.finally_stack.append(fctx)
    return fctx


def emit_finally_chain(ctx: 'CodeGenContext', out: TextIO, indent: str,
                       stop_at: int = 0) -> bool:
    """Emit finally bodies inline from innermost down to stop_at (exclusive).

    Each finally body is emitted with the corresponding frame popped, so
    any return/break/continue inside it redirects through the outer
    frames -- not back through itself. The stack is restored on exit so
    subsequent code in the caller's scope is unaffected (relevant when
    emitting the normal-fall-through finally before popping in the
    caller).

    The finally bodies themselves are opaque `emit_finally` callbacks
    owned by whoever pushed the frame, so this walker holds no body-
    emission knowledge of its own.

    ``indent_level`` is temporarily synced to the ``indent`` string so
    that gen_stmt-based emit_finally callbacks (which read
    ctx.indent_level rather than the ``ind`` argument) emit at the
    correct depth. Callers may pass an indent that doesn't correspond
    to the current emission point (e.g. _gen_propagate_check emits a
    nested return inside an `if` body); the level is restored after.

    Returns True if any finally body terminates (raise/return) -- the
    caller must suppress its own trailing return/break/continue/throw
    in that case, since control already left.
    """
    snapshot = list(ctx.finally_stack)
    prev_indent_level = ctx.indent_level
    target_level = len(indent) // len(INDENT)
    terminated = False
    try:
        ctx.indent_level = target_level
        while len(ctx.finally_stack) > stop_at:
            fctx = ctx.finally_stack.pop()
            # Set before the copy runs: if the copy raises, the frame's
            # own catch must not run it again. Frames further out still
            # have a false guard, so their finallies do run -- Python's
            # unwind semantics.
            if fctx.guard_name is not None:
                # Record the guard live: the emitter (sync catch here, or
                # a resumable region catch, whose frames outlive one
                # emit_finally_chain call) declares and tests it only
                # when its name is present in live_finally_guards.
                ctx.live_finally_guards.add(fctx.guard_name)
                out.write(f"{indent}{fctx.guard_name} = true;\n")
            fctx.emit_finally(out, indent)
            if fctx.terminates:
                terminated = True
                break
    finally:
        ctx.indent_level = prev_indent_level
        ctx.finally_stack = snapshot
    return terminated


def fresh_alias_local(ctx: 'CodeGenContext', var_name: str, *,
                      persistent: bool) -> str:
    # Default is `__{var_name}`. `persistent` is True when the emit is at
    # the same C++ scope as the caller (assert, early-return) -- where a
    # prior alias declared in the same lexical scope would collide. False
    # when the caller opened a fresh `{...}` block (if-body, while-body):
    # shadowing the outer alias is fine and produces cleaner names.
    #
    # Collision check spans the scope-global `declared_persistent_aliases`
    # set rather than `narrowed_vars` (which only holds the most-recent
    # alias per source variable -- earlier aliases like `__p` become
    # invisible after a bump to `__p_2` even though their C++ declaration
    # is still live). The caller records the chosen name in the set; the
    # set is part of LocalScopeSnap so it tracks C++ lexical scope.
    base = f"__{var_name}"
    # In a resumable frame the alias must not shadow a captured frame
    # field (e.g. `self` is the field `__self`): the cast initializer
    # reads the source by name, so a same-named alias would self-reference
    # its own uninitialized storage. Frame-field sets are empty outside
    # resumable bodies, so this is a no-op for sync codegen.
    if (base == ctx.generator_self_ref
            or base in ctx.generator_field_names):
        base = f"{base}_narrowed"
    if not persistent:
        return base
    in_use = ctx.declared_persistent_aliases
    if base not in in_use:
        return base
    n = 2
    while f"{base}_{n}" in in_use:
        n += 1
    return f"{base}_{n}"


def emit_isinstance_extractions(
    ctx: 'CodeGenContext', types: 'TypeResolver',
    protocols: 'ProtocolGenerator',
    out: TextIO, type_facts: dict[str, TpyType],
    *, indent_extra: int = 1, persistent: bool = False,
) -> dict[str, str | None]:
    """Emit std::get extractions for isinstance-narrowed variables.

    Returns saved narrowed_vars entries for later restoration.
    Only emits extraction when the fact is a concrete (non-union) type.
    `indent_extra` controls how many indent levels past the current level
    to emit at: 1 (default) inside an if-block / while-block, 0 after an
    assert or at the implicit-else of an early-returning if.
    `persistent` is True when the alias must outlive the caller's emit
    block (assert / early-return): the alias-name picker bumps the suffix
    if a prior alias of the same shape is in scope. False when the caller
    opened a fresh `{...}` block (if-body / while-body / else-body) --
    shadowing the outer alias is fine.
    """
    saved: dict[str, str | None] = {}
    if not type_facts:
        return saved
    inner_indent = INDENT * (ctx.indent_level + indent_extra)
    for var_name, narrowed_type in type_facts.items():
        # A union fact has no single alternative to extract; a void-like
        # fact (NoneType, or the VoidType `make_union` yields when only the
        # None member remains -- e.g. the else of `isinstance(v, (int, str))`
        # on `int | str | None`) narrows to None, which has no value to bind.
        if isinstance(narrowed_type, UnionType) or is_void_like_type(narrowed_type):
            continue
        # Deref-view facts don't retype the wrapper var -- no extraction
        # local. The narrowed reads route through deref_narrowed_to (and the
        # if-init's deref_view_init_locals); the wrapper stays its own type.
        if parse_deref_view_key(var_name) is not None:
            continue
        # LiteralType narrowing: track for dead branch elimination,
        # no std::get extraction needed.
        if isinstance(narrowed_type, LiteralType):
            ctx.literal_facts[var_name] = narrowed_type
            continue
        # Protocol isinstance narrows the concept constraint, not the value;
        # no std::get extraction needed (the variable is already a T& ref).
        # Track the narrowed type so get_resolved_type surfaces it to
        # downstream dispatch (for-loop peephole, `in` operator, etc.).
        if is_protocol_type(narrowed_type):
            ctx.protocol_narrowings[var_name] = narrowed_type
            continue
        # In @overload context, the param is already the concrete type --
        # no std::get extraction needed.
        if var_name in ctx.overload_param_types:
            continue
        cpp_type = types.type_to_cpp(narrowed_type)
        var_decl = ctx.lookup_var_type(var_name)
        # Polymorphic source + strict-subclass narrowed_type: cast-and-cache
        # extraction. Identity narrowing (`is not None`) keeps the same
        # class and is gated out by the predicate.
        if is_polymorphic_subclass_fact(
                var_decl, narrowed_type, ctx.analyzer.registry):
            # If `_gen_if` pre-bound the cast via C++17 if-init, route reads
            # through `(*__var_ptr)` directly -- no need for a separate
            # reference local that just aliases the deref. Compiler sees the
            # same object either way. For assert/while paths that don't go
            # through if-init, emit the fresh cast into a reference local.
            init_local = ctx.isinstance_init_locals.get(var_name)
            if init_local is not None:
                saved[var_name] = ctx.narrowed_vars.get(var_name)
                ctx.narrowed_vars[var_name] = f"(*{init_local})"
                continue
            local_name = fresh_alias_local(ctx, var_name, persistent=persistent)
            cast_const = "const " if is_const_borrow_source(ctx, var_name, var_decl) else ""
            cast_arg = ctx.polymorphic_cast_arg(var_name, var_decl)
            source_inner = polymorphic_source_inner(
                var_decl, ctx.analyzer.registry)
            cast_rhs = protocols.dynamic_narrow_cast_rhs(
                cpp_type, narrowed_type, source_inner, cast_arg,
                is_const=bool(cast_const))
            out.write(
                f"{inner_indent}{cast_const}{cpp_type}& {local_name} = "
                f"*{cast_rhs};\n"
            )
            saved[var_name] = ctx.narrowed_vars.get(var_name)
            ctx.narrowed_vars[var_name] = local_name
            if persistent:
                ctx.declared_persistent_aliases.add(local_name)
            continue
        # Any narrowing (D15): the source variable is a tpy::Any cell;
        # the narrowed binding is a `const T&` borrow into its
        # contents. The outer Any survives unchanged.
        if isinstance(var_decl, AnyType):
            local_name = fresh_alias_local(ctx, var_name, persistent=persistent)
            if ctx.is_indirect_name(TpyName(var_name)):
                var_ref = f"(*{var_name})"
            else:
                var_ref = var_name
            out.write(
                f"{inner_indent}const {cpp_type}& {local_name} = "
                f"std::any_cast<const {cpp_type}&>({var_ref}.value);\n"
            )
            saved[var_name] = ctx.narrowed_vars.get(var_name)
            ctx.narrowed_vars[var_name] = local_name
            if persistent:
                ctx.declared_persistent_aliases.add(local_name)
            continue
        # std::get needs the underlying variant. Previously-extracted T&
        # aliases in narrowed_vars (from outer if-branch narrowing, match
        # binds, or inline isinstance facts) point at non-variants, so we
        # must target the original variable here.
        # A union local hoisted into the resumable frame is a
        # `frame_slot<variant<...>>`; std::get needs the variant, not the
        # slot wrapper (the same `(*name)` unwrap the name-read path uses).
        fs_deref = ctx.frame_slot_deref(var_name)
        if ctx.is_indirect_name(TpyName(var_name)):
            var_ref = f"(*{var_name})"
        elif fs_deref is not None:
            var_ref = fs_deref
        else:
            var_ref = var_name
        local_name = fresh_alias_local(ctx, var_name, persistent=persistent)
        # Value-type union params are const&, so std::get yields const T&.
        # Non-value union params and locals are mutable.
        var_decl_type = ctx.var_types.get(var_name)
        is_const = (var_name in ctx.current_func_params
                    and var_decl_type is not None
                    and (var_decl_type.is_value_type() or var_decl_type.needs_wrapper()))
        qualifier = "const auto&" if is_const else "auto&"
        # Pointer-variant unions: *std::get<T*>(var) or *std::get<const T*>(var)
        if var_name in ctx.ptr_variant_locals:
            is_const = var_name in ctx.const_indirect_locals
            va = VariantAccess(var_ref, None, is_ptr_variant=True, is_const=is_const)
        else:
            va = VariantAccess(var_ref, var_decl_type, is_ptr_variant=False)
        out.write(f"{inner_indent}{qualifier} {local_name} = {va.get_by_type(cpp_type, lvalue=True)};\n")
        saved[var_name] = ctx.narrowed_vars.get(var_name)
        ctx.narrowed_vars[var_name] = local_name
        if persistent:
            ctx.declared_persistent_aliases.add(local_name)
    return saved


# -- per-body scope setup --------------------------------------------------

# The ctx set-fields `seed_param_locals` mutates -- the single authoritative
# list the ctor member-init save/restore (records._extract_field_inits)
# snapshots AND clears before seeding (the MIL window must classify against
# exactly the ctor's params; stale same-named entries flip name-keyed
# verdicts). Add here when seed_param_locals starts writing a new set.
# (var_types, a dict, is snapshotted and cleared separately by that caller.)
# NB other name-keyed per-body fields (const_ref_params, declared_vars,
# local_scope_names, narrowed_vars) stay stale through the window; none
# has a demonstrated MIL-render consumer.
PARAM_LOCAL_SET_FIELDS = (
    "pointer_locals", "const_indirect_locals", "optional_locals",
    "ptr_variant_locals", "movable_locals", "storage_form_tuple_locals",
)


def seed_param_locals(ctx: 'CodeGenContext', protocols: 'ProtocolGenerator',
                      params: list[tuple[str, TpyType]],
                      local_ns: Namespace,
                      deep_const_borrow_params: set[str]) -> None:
    """Classify params into the pointer-form local sets access dispatch reads
    (`PARAM_LOCAL_SET_FIELDS` + `var_types`) so a pointer-repr param derefs
    with `->` in a ctor member-init initializer as it does in the body."""
    # Optional non-value params are T* / const T* in C++ -- need pointer-local treatment (->)
    for pname, ptype in params:
        # Peel the Send/Sync marker (representationally transparent -- it
        # erases to its inner type in C++) so a Send[Own[T]] param is
        # classified by its Own/pointer/optional shape, not treated as opaque.
        actual = unwrap_readonly(unwrap_send_sync(ptype))
        if protocols.is_static_protocol_param(ptype):
            # Static protocol params: check if nullable (uses pointer repr)
            infos = protocols.get_all_protocol_params([(pname, ptype)])
            if infos and infos[0].has_none:
                ctx.pointer_locals.add(pname)
                ctx.const_indirect_locals.add(pname)
        elif isinstance(actual, OptionalType) and actual.uses_pointer_repr():
            ctx.pointer_locals.add(pname)
            # `const P*` when annotated `readonly[...]` OR when the inferred
            # verdict const-consts it (readonly fn/method whose param address
            # does not escape) -- same addr-escape-aware verdict the signature
            # renders, so borrow-locals off this receiver spell const to match.
            if (isinstance(ptype, ReadonlyType)
                    or pname in deep_const_borrow_params):
                ctx.const_indirect_locals.add(pname)
        # Own[OptionalType[P_ref]]: param renders as `std::optional<P>&&`
        # (storage form), but body access patterns are the same as a
        # storage-form Optional local: arrow for member access (uses
        # optional<P>::operator->), .has_value() for null check, direct
        # std::move into another storage slot. Register as both
        # pointer_local (for arrow access) and optional_local (so the
        # null-check dispatch picks has_value over `!= nullptr`). Rebind
        # the namespace to the bare Optional so type-aware codegen sites
        # match the sibling pointer-repr Optional handling. movable_locals
        # is set below via the generic `unwrap_optional_own + non-value`
        # pass.
        elif is_own_pointer_repr_optional(actual):
            ctx.pointer_locals.add(pname)
            ctx.optional_locals.add(pname)
            ctx.var_types[pname] = actual.wrapped
            local_ns.bind_variable(pname, actual.wrapped)
        # Non-value union params are pointer variants (variant<T*...>)
        elif ctx.is_ptr_variant_union(actual):
            ctx.ptr_variant_locals.add(pname)
            # Deep-const members (`const T*`) when the param is `readonly[...]`
            # OR the const verdict deep-consts it (readonly fn/method whose
            # param address does not escape). `deep_const_borrow_params` is the
            # same addr-escape-aware verdict the signature and call site read,
            # so the body's `std::get<T*>` matches the param decl.
            if (isinstance(ptype, ReadonlyType)
                    or pname in deep_const_borrow_params):
                ctx.const_indirect_locals.add(pname)
        # Own[T] and Own[T] | None params are movable (caller gave up ownership)
        own_actual = unwrap_optional_own(actual)
        if own_actual is not None and not own_actual.wrapped.is_value_type():
            ctx.movable_locals.add(pname)
        if (isinstance(actual, TupleType) and actual.is_owned_movable()
                and not isinstance(ptype, ReadonlyType)):
            ctx.movable_locals.add(pname)
            ctx.storage_form_tuple_locals.add(pname)
        # Own[tuple[T | None, ...]] params are stored in storage form
        # (std::tuple<std::optional<T>, ...>); same C++ shape as the
        # storage-form locals registered for storage-form tuple iteration.
        if isinstance(actual, OwnType):
            inner = unwrap_readonly(actual.wrapped)
            if isinstance(inner, TupleType) and inner.has_pointer_repr_element():
                ctx.storage_form_tuple_locals.add(pname)
        # Value-optional params (std::optional<T> by value) are movable when
        # the inner type has an expensive copy (String, BigInt, etc.).
        # readonly params are excluded to respect the no-mutation contract.
        elif (isinstance(actual, OptionalType) and not actual.uses_pointer_repr()
                and not isinstance(ptype, ReadonlyType)
                and actual.inner.is_expensive_copy()):
            ctx.movable_locals.add(pname)


@contextmanager
def seed_param_locals_scoped(
        ctx: 'CodeGenContext', protocols: 'ProtocolGenerator',
        params: list[tuple[str, TpyType]], local_ns: Namespace,
        deep_const_borrow_params: set[str]) -> Iterator[None]:
    """Seed the param classification (`seed_param_locals`) for the body of
    the with-block, then restore the exact ctx sets it writes. For callers
    that run before `setup_body_scope`/`reset_scope` (the ctor member-init
    extraction) where the full scope snapshot isn't usable yet. Owning the
    save/restore here keeps it from drifting out of sync with what
    seed_param_locals mutates.

    The sets are CLEARED before seeding, not merely added to: the caller
    runs outside any body scope, so whatever the previously emitted body
    left behind is stale -- a same-named binding from it would flip
    name-keyed verdicts (`is_ptr_variant_source`, pointer derefs, moves)
    inside the window."""
    saved = {f: getattr(ctx, f).copy() for f in PARAM_LOCAL_SET_FIELDS}
    saved_var_types = dict(ctx.var_types)
    try:
        for f in PARAM_LOCAL_SET_FIELDS:
            setattr(ctx, f, set())
        ctx.var_types = {}
        seed_param_locals(ctx, protocols, params, local_ns,
                          deep_const_borrow_params)
        yield
    finally:
        for f, prev in saved.items():
            setattr(ctx, f, prev)
        ctx.var_types = saved_var_types


def setup_body_scope(ctx: 'CodeGenContext', protocols: 'ProtocolGenerator',
                     types: 'TypeResolver',
                     params: list[tuple[str, TpyType]],
                     return_type: TpyType, func: TpyFunction,
                     local_ns: Namespace, indent_level: int = 1,
                     is_method: bool = False,
                     record_type_param_bounds: dict[str, TpyType] | None = None,
                     const_ref_params: set[str] | None = None,
                     deep_const_borrow_params: set[str] | None = None,
                     owning_record_name: str | None = None,
                     return_cpp: str | None = None) -> 'ScanResult | None':
    """Reset per-scope ctx state and repopulate it for the given function.

    Shared by gen_body (sync + simple-gen + multi-yield-gen) and async
    body emission (`_resumable_frame_ctx`). Returns the scan result so
    callers can use it for body-emission-specific work
    (reassigned-param copies, etc.).
    """
    ctx.reset_scope()
    # Apply literal overload facts (injected by _gen_literal_specialized_function,
    # survives reset_scope like overload_param_types)
    if ctx.literal_overload_facts:
        ctx.literal_facts.update(ctx.literal_overload_facts)
    ctx.const_ref_params = const_ref_params if const_ref_params is not None else set()
    ctx.deep_const_borrow_params = deep_const_borrow_params if deep_const_borrow_params is not None else set()
    ctx.declared_vars = {pname for pname, _ in params}
    ctx.var_types = {pname: unwrap_ref_type(ptype) for pname, ptype in params}
    ctx.local_scope_names = {pname for pname, _ in params}
    ctx.global_declared_vars = ctx.analyzer.function_global_decls.get(id(func), set())
    scan = ctx.analyzer.function_scan_results.get(id(func))
    if scan:
        ctx.reassigned_vars = scan.reassigned - ctx.global_declared_vars
        ctx.rvalue_reassigned_vars = scan.rvalue_reassigned - ctx.global_declared_vars
        ctx.lvalue_reassigned_vars = scan.lvalue_reassigned - ctx.global_declared_vars
        ctx.aliased_vars = set(scan.alias_sources.values())
        ctx.alias_names = scan.initial_alias_names
    else:
        ctx.reassigned_vars = set()
        ctx.rvalue_reassigned_vars = set()
        ctx.lvalue_reassigned_vars = set()
        ctx.aliased_vars = set()
        ctx.alias_names = set()
    ctx.hoisted_vars = ctx.analyzer.function_hoisted_vars.get(id(func), set())
    ctx.move_through_vars = ctx.analyzer.function_move_through_vars.get(id(func), set())
    ctx.sema_movable_locals = ctx.analyzer.function_movable_locals.get(id(func), set())
    ctx.sema_ever_owned_locals = ctx.analyzer.function_ever_owned_locals.get(id(func), set())
    ctx.sema_stmt_borrow_decls = ctx.analyzer.function_stmt_borrow_decls.get(id(func), {})
    # Classify params into the pointer-form local sets (pointer_locals,
    # ptr_variant_locals, optional_locals, movable_locals, ...) that access
    # dispatch consults so `->` vs `.` / move / variant-form are correct.
    seed_param_locals(ctx, protocols, params, local_ns,
                      ctx.deep_const_borrow_params)
    binding_audit.begin_ast_body(func)
    # Generator-promoted locals are struct fields; pre-seed var_types
    # so codegen sites that consult it (e.g. address-of for tuple
    # slots) see the original TPy type rather than the synthetic
    # outer-optional wrapper used for init tracking.
    if func.generator_locals:
        for lname, ltype in func.generator_locals:
            ctx.var_types[lname] = ltype
        ctx.setup_resumable_frame_locals(func)
    ctx.current_ns = local_ns
    ctx.indent_level = indent_level
    ctx.current_return_type = return_type
    ctx.current_return_cpp = return_cpp
    ctx.current_return_const = bool(getattr(func, 'is_readonly', False))
    # Set current_yield_type for generator bodies so yield-emission sites
    # don't need it threaded through their call signatures. Skipped for
    # sema-errored generators (no resolved yield type) -- leaves the
    # field at its reset_scope() default rather than crashing later.
    if func.is_generator and func.generator_yield_type is not None:
        ctx.current_yield_type = func.generator_yield_type
    raw_error_return = getattr(func, 'error_return', None)
    ctx.current_error_return = error_return_to_cpp(raw_error_return, ctx.analyzer.ctx.module_name, ctx.analyzer.registry) if raw_error_return else None
    ctx.current_func_params = {pname: ptype for pname, ptype in params}
    ctx.in_property_getter = getattr(func, 'is_property_getter', False)
    ctx.current_type_param_bounds = dict(record_type_param_bounds) if record_type_param_bounds else {}
    if func.type_param_bounds:
        ctx.current_type_param_bounds.update(func.type_param_bounds)
    if is_method:
        ctx.in_method = True
        # `self` resolves to the enclosing record's type during the body
        # so `lookup_var_type('self')` can drive `isinstance(self, Sub)`
        # polymorphic dispatch. None for static methods (no self). Use
        # `build_record_self_type` so the NominalType carries the proper
        # qname + generic type-param refs, matching how sema constructs
        # self's type -- avoids future cross-module short-name collision
        # risk if polymorphic-source predicates ever route through qname
        # equality.
        if owning_record_name is not None:
            rec_info = ctx.analyzer.registry.get_record(owning_record_name)
            if rec_info is not None:
                ctx.current_method_record_type = build_record_self_type(
                    rec_info, qname=rec_info.qualified_name())
            else:
                ctx.current_method_record_type = NominalType(owning_record_name)
    compute_borrow_tuple_const(ctx, types, func)
    return scan


def compute_borrow_tuple_const(ctx: 'CodeGenContext', types: 'TypeResolver',
                               func: TpyFunction) -> None:
    """Populate `const_borrow_form_tuple_locals`: borrow-form tuple locals
    whose declared element pointers must be `const T*` because some binding
    source is a const-storage location.

    Codegen runs after Phase-2 const inference, so each source's final
    const-ness is known here. The declared const must be at least as const
    as every source feeding the local (mutable->const lift is safe,
    const->mutable would not compile); we therefore OR const over all
    bindings. A bare-name source feeding from another borrow-form tuple
    carries that local's const, so iterate to a fixpoint over name chains.
    """
    bindings: dict[str, list[TpyExpr]] = {}
    # Nullable-borrow-tuple locals tracked separately: their const set is
    # const_optional_borrow_tuple_locals (the inner tuple sits behind a
    # std::optional, but const-ness is inferred from the same sources).
    opt_bindings: dict[str, list[TpyExpr]] = {}

    # A nullable-borrow-tuple local is identified by its TARGET type
    # (`tuple[..., T] | None`), not the source: a rebind source is often a
    # plain `tuple[..., T]` field, but the local stays the optional form.
    optional_targets: set[str] = set()

    def collect(stmts: list[TpyStmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, TpyVarDecl) and stmt.init is not None:
                tt = resolve_target_type(ctx, types, stmt)
                if (isinstance(tt, OptionalType)
                        and tt.wraps_pointer_repr_tuple()):
                    optional_targets.add(stmt.name)
                record(stmt.name, stmt.init)
            elif (isinstance(stmt, TpyAssign)
                  and isinstance(stmt.target, TpyName)):
                record(stmt.target.name, stmt.value)
            # Walrus (`(t := src)`) binds too -- in conditions, values, etc.
            for ne in walrus_bindings(stmt):
                record(ne.target, ne.value)
            for body in stmt.sub_bodies():
                collect(body)

    def record(tgt: str, src: TpyExpr) -> None:
        if tgt not in ctx.reassigned_vars and tgt not in ctx.hoisted_vars:
            return
        st = ctx.analyzer.get_expr_type(src)
        stb = (unwrap_readonly(unwrap_ref_type(st))
               if st is not None else None)
        is_ptr_repr_tuple = (
            (isinstance(stb, TupleType) and stb.has_pointer_repr_element())
            or (isinstance(stb, OptionalType) and stb.wraps_pointer_repr_tuple()))
        if not is_ptr_repr_tuple:
            return
        if tgt in optional_targets:
            opt_bindings.setdefault(tgt, []).append(src)
        else:
            bindings.setdefault(tgt, []).append(src)

    collect(func.body)
    if not bindings and not opt_bindings:
        return
    # Fixpoint over both kinds together: a name chain can cross between a
    # plain borrow-tuple local and a nullable one, and `is_const_storage_source`
    # (consulted via `tuple_source_is_const`) reads both const sets.
    pairs = [(bindings, ctx.const_borrow_form_tuple_locals),
             (opt_bindings, ctx.const_optional_borrow_tuple_locals)]
    changed = True
    while changed:
        changed = False
        for binds, const_set in pairs:
            for name, srcs in binds.items():
                if name in const_set:
                    continue
                if any(tuple_source_is_const(ctx, s) for s in srcs):
                    const_set.add(name)
                    changed = True


def tuple_source_is_const(ctx: 'CodeGenContext', src: TpyExpr) -> bool:
    """Whether a borrow-tuple binding source reads from const storage.

    A ternary feeds whichever arm runs, so it is const if either arm is.
    An explicit `readonly[...]` source (readonly param / field / return) is
    const even though it is not in `const_ref_params`: its element pointers
    lift as `const T*`, so the borrow local must declare them const.
    """
    inner = ctx.unwrap_copy(src)
    if isinstance(inner, TpyIfExpr):
        return (tuple_source_is_const(ctx, inner.then_expr)
                or tuple_source_is_const(ctx, inner.else_expr))
    if ctx.is_const_storage_source(inner):
        return True
    st = ctx.analyzer.get_expr_type(inner)
    return isinstance(st, ReadonlyType)


def resolve_target_type(ctx: 'CodeGenContext', types: 'TypeResolver',
                        stmt: TpyVarDecl) -> TpyType | None:
    """Resolve the target type for a variable declaration."""
    target_type = resolve_stmt_binding_type(
        stmt,
        ctx.analyzer,
        include_global_binding=(ctx.current_ns is ctx.analyzer.global_ns),
    )
    if target_type is None and stmt.init:
        target_type = ctx.analyzer.get_expr_type(stmt.init)
    if target_type is not None:
        # Strip Ref and ReadonlyType -- C++ reference semantics are
        # handled by codegen binding (T& / auto&), not by the type itself.
        # Send/Sync markers (canonically outermost) have no C++ shape.
        target_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(target_type)))
        # Keep Own[dyn P]: the owned-erased local IS unique_ptr<P>;
        # stripping it would route the decl to the borrow-form
        # pointer-local path (dangling for an owned rvalue init).
        if isinstance(target_type, OwnType) and not is_dyn_protocol(
                unwrap_readonly(target_type.wrapped)):
            target_type = target_type.wrapped
        target_type = resolve_int_literals(target_type, ctx.analyzer.ctx.default_int_for_literal)
        if isinstance(target_type, FloatLiteralType):
            target_type = FLOAT
        resolved = resolve_pending_container(target_type, ctx.analyzer)
        if resolved is not None:
            target_type = resolved
        elif isinstance(target_type, PendingViewType):
            target_type = types._resolve_pending_view(target_type)
        # A reassigned per-element-Own tuple local (including the nullable
        # `tuple[..., Own[T]] | None` form) takes the unified borrow shape
        # so an alias rebind aliases the source instead of copying. Sema
        # records this on inferred decls; an annotated decl reaches codegen
        # with the raw `stmt.type`, so re-apply the collapse here.
        if stmt.name in ctx.reassigned_vars:
            target_type = collapse_tuple_own_elements(target_type)
    return target_type


__all__ = [
    "PARAM_LOCAL_SET_FIELDS",
    "compute_borrow_tuple_const",
    "cpp_declared_type",
    "declared_type_incl_globals",
    "emit_except_handler_header",
    "emit_finally_chain",
    "emit_isinstance_extractions",
    "extract_int_literal",
    "fresh_alias_local",
    "gen_range_overflow_check",
    "is_const_borrow_source",
    "is_const_indirect",
    "is_plain_nonvalue",
    "maybe_unwrap_narrowed_optional",
    "narrowed_value_optional_iter_type",
    "nested_def_signature",
    "promote_movable",
    "ptr_slot_field_type",
    "push_finally",
    "resolve_field_declared_type",
    "resolve_target_type",
    "seed_param_locals",
    "seed_param_locals_scoped",
    "setup_body_scope",
    "tuple_source_is_const",
    "unpack_source_has_const_slots",
]
