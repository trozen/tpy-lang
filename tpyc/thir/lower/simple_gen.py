"""Simple-generator (lambda peephole) lowering: the leaf half of the seam.

The peephole SKELETON -- signature, capture list, `make_generator` +
mutable-lambda scaffolding, iterator-slot types, loop-var decl, the `__val`
binding and the per-pull optional return -- is shared machinery in
`gen_generators` (the structural-emission precedent, like the resumable
frame skeleton). What routes through THIR is every user-source LEAF the
skeleton would otherwise delegate to the AST emitters: the pre-loop init
statements, the while condition, the pre-/post-yield loop-body statements,
the yield value, and the for-branch iterable / range-bound expressions.

`lower_simple_generator` re-walks the same body split the emitter performs
(`split_at_yield` over the trailing while/for -- `is_simple_generator`
already routed the body here, so the shape holds), lowers every leaf through
the shared statement/expression lowering, and returns a `THIRSimpleGenBody`
-- or None (with an `sgen.*` / composed `stmt.*` fallback reason) when any
leaf or scaffolding-adjacent feature falls outside the slice.

Foundation slice (deliberately tight; widening cells follow the metrics):
value-scalar yield values and for-loop element types (the skeleton's
`__val` slot / loop-var decl and the position-blind yield render are only
proven for those families -- the resumable yield precedent); locals behave
exactly like sync-body locals (ordinary C++ locals in the lambda), so
params/locals/statements gate at the shared arms, not here.
"""

from __future__ import annotations

from ..fallback import ThirUnsupported, note
from ..faces import witness as _witness
from ..nodes import THIRSimpleGenBody
from ...parse.nodes import (
    TpyArrayLiteral,
    TpyFieldAccess,
    TpyForEach,
    TpyFunction,
    TpyWhile,
)
from ...typesys import IntLiteralType, NominalType, TpyType
from ...codegen_cpp.gen_generators import (
    for_range_uses_counter_loop,
    split_at_yield,
)
from .context import _ExprResultUse, _ExprUse, _LowerCtx
from .checks import _narrow_cond_info
from .expressions import _lower_expr, _lower_truthy
from .functions import _check_callable_structure, _seed_global_scope
from .resumable import _res_value_ok
from .statements import _lower_stmts


def lower_simple_generator(func: TpyFunction, analyzer, render_type,
                           self_type: 'TpyType | None' = None,
                           native_globals=None,
                           render_type_stored=None,
                           render_resolve=None) -> 'THIRSimpleGenBody | None':
    """Lower a simple-generator body's leaves, falling back cleanly on a
    lowering reject."""
    try:
        return _lower_simple_generator(
            func, analyzer, render_type, self_type=self_type,
            native_globals=native_globals or {},
            render_type_stored=render_type_stored,
            render_resolve=render_resolve)
    except ThirUnsupported as ex:
        return _reject(ex.reason)


def _lower_simple_generator(func: TpyFunction, analyzer, render_type,
                            self_type: 'TpyType | None',
                            native_globals,
                            render_type_stored,
                            render_resolve) -> 'THIRSimpleGenBody | None':
    # Method peepholes emit inside the record with `self` spelled `(*this)`
    # (`generator_self_ref`), a `Record&`-like deref -- `.` member access,
    # same self machinery as the resumable `__self` (R2). Static / property
    # kinds keep their own dispatch shapes; defer them.
    if func.is_method:
        if func.is_staticmethod:
            return _reject("sgen.static_method")
        if func.is_property_getter or func.is_property_setter:
            return _reject("sgen.property")
        if self_type is None:
            return _reject("sgen.method")
    try:
        _check_callable_structure(
            func, analyzer, self_type, allow_resumable=True)
    except ThirUnsupported as ex:
        return _reject(ex.reason)
    if func.type_params:
        # Generic peephole: the template header is skeleton, but leaf slots
        # spell TypeParamRefs -- the sync T-value arms are not proven against
        # the lambda's capture-typed scope; a cell.
        return _reject("sgen.generic")
    if isinstance(self_type, NominalType) and self_type.type_args:
        return _reject("sgen.generic_record")
    if func.forwarded_locals:
        return _reject("sgen.forwarded_local")
    yt = func.generator_yield_type
    if not _res_value_ok(yt if isinstance(yt, TpyType) else None, analyzer):
        # Value-scalar yields only: the slot is the bare value type and the
        # value render is position-blind (the sema-baked coerce carries the
        # yield-type target -- the resumable yield precedent). Borrow-slot /
        # tuple / str yields interact with `_iter_slot_for_yield`'s slot
        # forms and `gen_yield_value`'s bridge wraps -- their own rungs.
        return _reject("sgen.yield_type")

    has_self = self_type is not None and func.is_method
    lc = _LowerCtx(func, analyzer, render_type,
                   render_type_stored=render_type_stored,
                   self_receiver="self" if has_self else None,
                   record_name=(self_type.name
                                if has_self and isinstance(self_type,
                                                           NominalType)
                                else None),
                   self_cpp="(*this)", self_is_pointer=False,
                   render_resolve=render_resolve)
    params_set: dict[str, TpyType] = {n: t for n, t in func.params}
    if has_self:
        params_set["self"] = self_type
        if func.is_readonly:
            lc.const_locals.add("self")
    _seed_global_scope(func, analyzer, lc, params_set, native_globals)
    declared: dict[str, TpyType] = dict(params_set)

    last = func.body[-1]
    init_stmts = func.body[:-1]
    init = _lower_stmts(init_stmts, lc, declared)

    if isinstance(last, TpyWhile):
        if analyzer.if_branch_decls.get(id(last)):
            return _reject("sgen.hoist_promoted")
        if _narrow_cond_info(last.condition, declared, analyzer) is not None:
            # A while-isinstance head would need the U4 loop-entry extraction
            # woven between the skeleton's `while (...) {` and the body.
            return _reject("sgen.narrow_cond")
        try:
            cond = _lower_truthy(last.condition, lc, declared)
        except ThirUnsupported:
            return _reject("sgen.cond")
        _witness("sgen.while_cond")
        pre_l, yv, post_l = _lower_loop_body(last, lc, declared, loop_depth=1)
        _witness("sgen.body")
        return THIRSimpleGenBody(init=init, pre_yield=pre_l,
                                 post_yield=post_l, yield_value=yv, cond=cond)

    assert isinstance(last, TpyForEach)
    iter_elem = last.elem_type
    if iter_elem is not None and isinstance(iter_elem, IntLiteralType):
        iter_elem = analyzer.ctx.default_int_type
    if not _res_value_ok(iter_elem, analyzer):
        # A non-value element binds the loop var `auto&&` into the live
        # source (and pointer-repr tuples flip `storage_form_tuple_locals`)
        # -- the borrow-classified loop-var reads are a cell.
        return _reject("sgen.loop_var_type")
    is_range = for_range_uses_counter_loop(last)
    range_args: tuple = ()
    iterable = None
    if is_range:
        # Position-blind bound renders: the skeleton's `static_cast<elem>`
        # scaffolding supplies the conversion (plain `gen_expr`, unlike the
        # sync range arm's literal retype).
        range_args = tuple(_lower_expr(a, lc, declared)
                           for a in last.iterable.args)
        if range_args:
            _witness("sgen.range_arg")
    else:
        iterable = _lower_expr(
            last.iterable, lc, declared,
            use=_ExprUse(result=_ExprResultUse.ITERABLE),
            container_threaded=not isinstance(last.iterable, TpyArrayLiteral))
        _witness("sgen.iterable")
    # The while / for-range branches wrap the body in a real C++ loop
    # (break/continue bind); the pull branches run one element per call with
    # no loop, so loop_depth stays 0 there (`is_simple_generator` already
    # excluded loop control on those shapes).
    body_declared = dict(declared)
    body_declared[last.var] = iter_elem
    pre_l, yv, post_l = _lower_loop_body(last, lc, body_declared,
                                         loop_depth=1 if is_range else 0)
    _witness("sgen.body")
    return THIRSimpleGenBody(init=init, pre_yield=pre_l, post_yield=post_l,
                             yield_value=yv, iterable=iterable,
                             range_args=range_args)


def _lower_loop_body(loop_stmt, lc: _LowerCtx, declared: dict[str, TpyType],
                     *, loop_depth: int):
    """Lower the loop body split around its single yield (pre stmts, yield
    value, post stmts) under one narrowing-scope snapshot -- the three pieces
    share the walk state (a pre-yield narrowing holds across the yield, as it
    does in the emitted single C++ scope) and pop together at the loop's
    closing brace."""
    yield_stmt, pre, post = split_at_yield(loop_stmt.body)
    if yield_stmt.value is None:
        # A bare `yield` never arises for the gated value-scalar yield types;
        # defensive.
        raise ThirUnsupported("sgen.bare_yield")
    saved = lc.narrow.snapshot()
    body_declared = dict(declared)
    try:
        pre_l = _lower_stmts(pre, lc, body_declared, in_branch=True,
                             branch_decls_ok=True, loop_depth=loop_depth)
        yv = _lower_expr(yield_stmt.value, lc, body_declared)
        _witness("sgen.yield_value")
        post_l = _lower_stmts(post, lc, body_declared, in_branch=True,
                              branch_decls_ok=True, loop_depth=loop_depth)
    finally:
        lc.narrow = saved
    if lc.unhandled_hoists:
        raise ThirUnsupported("body.hoisted_vars")
    return pre_l, yv, post_l


def _reject(reason: str):
    note(reason)
    return None
