"""Simple-generator (lambda peephole) lowering: the leaf half of the seam.

The peephole SKELETON -- signature, capture list, `make_generator` +
mutable-lambda scaffolding, iterator-slot types, loop-var decl, the `__val`
binding and the per-pull optional return -- is shared machinery in
`gen_generators`, like the resumable frame skeleton. What THIR lowers is
every user-source LEAF the skeleton delegates to it: the pre-loop init
statements, the while condition, the pre-/post-yield loop-body statements,
the yield value, and the for-branch iterable / range-bound expressions.

`lower_simple_generator` re-walks the same body split the emitter performs
(`split_at_yield` over the trailing while/for -- `is_simple_generator`
already routed the body here, so the shape holds), lowers every leaf through
the shared statement/expression lowering, and returns a `THIRSimpleGenBody`
-- or None (with an `sgen.*` / composed `stmt.*` reject reason) when any
leaf or scaffolding-adjacent feature falls outside the slice.

Slice currently lowered:
value-scalar yield values and for-loop element types (the skeleton's
`__val` slot / loop-var decl and the position-blind yield render are only
proven for those families -- the resumable yield precedent); locals behave
exactly like sync-body locals (ordinary C++ locals in the lambda), so
params/locals/statements gate at the shared arms, not here.
"""

from __future__ import annotations

from ..reject import ThirUnsupported, note
from ..faces import witness as _witness
from ..validate import validate_simple_gen_body
from ..nodes import Form, THIRFormConvert, THIRSimpleGenBody
from ...parse.nodes import (
    SourceLocation,
    TpyArrayLiteral,
    TpyFieldAccess,
    TpyForEach,
    TpyFunction,
    TpyName,
    TpySubscript,
    TpyTupleLiteral,
    TpyWhile,
)
from ...typesys import (
    IntLiteralType,
    collapse_tuple_own_elements,
    NominalType,
    OwnType,
    ReadonlyType,
    TpyType,
    TupleType,
    TypeParamRef,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...codegen_cpp import emit_prims
from ...codegen_cpp.gen_generators import (
    for_range_uses_counter_loop,
    owned_view_frame_params,
    split_at_yield,
)
from .context import _ExprResultUse, _ExprUse, _LowerCtx
from .checks import _narrow_cond_info
from .expressions import (_lower_expr, _lower_truthy,
                          _slot_literal_retype,
                          _lower_yield_tuple_literal, _lower_copy_record)
from .functions import (_check_callable_structure, _seed_global_scope,
                        cross_scope_rebind_site)
from ...type_def_registry import is_dict, is_list, is_set
from ...typesys import is_protocol_type
from ...modules.type_resolution import is_native_iterable
from .predicates import (
    _f1_record,
    _field_receiver_ok,
    _resolved_bytes_value,
    _resolved_str_value,
    _wrap_view_owned_sink,
)
from .resumable import _res_value_ok
from .statements import _lower_stmts


def _sgen_yield_ok(yt: 'TpyType | None', analyzer) -> bool:
    """Simple-generator yield-slot families whose leaf VALUE render is
    position-blind (the skeleton owns the slot type, `__val` binding and
    move-out): value scalars/Char/enums (`_res_value_ok`), str/bytes (the
    bare source render into the owned `std::optional<std::string>` slot),
    F1 records (the `val_or_ref<T>` borrow slot -- a bare name/field
    render), `Own[F1 record]` (the bare value slot; the skeleton's
    `std::move(__val)`), and TUPLE slots (deferred to the per-yield tuple
    arm in `_lower_loop_body`, mirroring the resumable Yield tuple arm's
    literal/borrow-local sources). Excluded, each its own rung: readonly
    (const-borrow slot), Optional/Union (pointer/storage machinery),
    TypeParamRef (substituted slots)."""
    if _res_value_ok(yt, analyzer):
        return True
    if yt is None or isinstance(unwrap_ref_type(yt), ReadonlyType):
        return False
    if (_resolved_str_value(yt, analyzer) is not None
            or _resolved_bytes_value(yt, analyzer) is not None):
        return True
    u = unwrap_ref_type(unwrap_send_sync(yt))
    if isinstance(u, TupleType):
        return True
    if isinstance(u, TypeParamRef):
        # A T-typed yield slot: the skeleton owns `std::optional<V>` and
        # the leaf's `auto __val = <source>;` bind is type-neutral -- the
        # source's own arm decides (a bare name / self-field renders
        # position-blind).
        return True
    if isinstance(u, OwnType):
        u = unwrap_readonly(u.wrapped)
        if isinstance(u, TypeParamRef):
            # An `Own[T]` yield slot (`Iterator[Own[T]]`, `yield copy(x)`):
            # the skeleton's slot is the same `std::optional<T>` a bare-T
            # yield gets (Own resolves to plain T by value) and the leaf
            # bind stays type-neutral -- the Own-ness lives entirely in the
            # skeleton's `std::move(__val)` move-out, shared with the
            # Own[F1-record] family below.
            return bool(_witness("sgen.yield_own_tparam"))
    return _f1_record(u, analyzer)


def _sgen_loop_var_ok(iter_elem: 'TpyType | None', analyzer) -> bool:
    """For-branch loop-var families: value scalars (the skeleton's typed
    copy) and F1 records (the skeleton's `auto&&` borrow; the leaf reads
    the var through the same borrow-classified forms as a sync for-each
    body). Pointer-repr tuple elements (which flip
    `storage_form_tuple_locals`) and the remaining families stay their own
    rungs."""
    if _res_value_ok(iter_elem, analyzer):
        return True
    if iter_elem is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(iter_elem)))
    # A str/bytes element: the skeleton declares the element type by
    # `type_to_cpp`, so the binding is OWNED where a sync for-each binds the
    # usage-resolved view -- but that decl is shared skeleton machinery,
    # not rendered here. The leaf reads consult the same sema form
    # resolution either way, which is why the yield slot already rides this
    # reasoning.
    if (_resolved_str_value(u, analyzer) is not None
            or _resolved_bytes_value(u, analyzer) is not None):
        return True
    # A CONTAINER element binds the same skeleton `auto&& v = *__beg++;`
    # as an F1 record, and the leaf reads it through the container-name
    # arms (`v.push_back(9)` / `len(v)`) exactly like a sync for-each var.
    if is_list(u) or is_dict(u) or is_set(u):
        return True
    # A POINTER-REPR tuple element (dict items / list[tuple[.., Ref]]):
    # the skeleton advances via the tuple_to_pointer proxy-ref holder and
    # the body reads ride the borrow-tuple/unpack arms like a sync
    # for-each var. NB the storage_form registration below keys the RAW
    # iterable type, so a BOUNDED-T iterable (whose bound is resolved at
    # that registration site) must not reach this leg -- the
    # caller rejects TypeParamRef iterables for the tuple flavor.
    if isinstance(u, TupleType) and u.has_pointer_repr_element():
        return True
    if isinstance(u, TypeParamRef):
        # A T-typed element binds the same type-neutral
        # `auto&& x = *__beg++;`; the body reads gate at their own arms.
        return True
    return _f1_record(u, analyzer)


def lower_simple_generator(func: TpyFunction, analyzer, render_type,
                           self_type: 'TpyType | None' = None,
                           native_globals=None,
                           render_type_stored=None,
                           render_resolve=None) -> 'THIRSimpleGenBody | None':
    """Lower a simple-generator body's leaves, or None on a lowering
    reject."""
    try:
        body = _lower_simple_generator(
            func, analyzer, render_type, self_type=self_type,
            native_globals=native_globals or {},
            render_type_stored=render_type_stored,
            render_resolve=render_resolve)
    except ThirUnsupported as ex:
        return _reject(ex.reason, ex.loc)
    return body


def _lower_simple_generator(func: TpyFunction, analyzer, render_type,
                            self_type: 'TpyType | None',
                            native_globals,
                            render_type_stored,
                            render_resolve) -> 'THIRSimpleGenBody | None':
    # Method peepholes emit inside the record with `self` spelled `(*this)`
    # (`generator_self_ref`), a `Record&`-like deref -- `.` member access,
    # same self machinery as the resumable `__self` (R2). Static / property
    # kinds keep their own dispatch shapes; defer them.
    # Keyed on the flag alone, not on `is_method`: a macro-authored static
    # carries only the flag (see `_check_callable_structure`) and must defer
    # here too.
    if func.is_staticmethod:
        return _reject("sgen.static_method")
    if func.is_method:
        if func.is_property_getter or func.is_property_setter:
            return _reject("sgen.property")
        if self_type is None:
            return _reject("sgen.method")
    try:
        _check_callable_structure(
            func, analyzer, self_type, allow_resumable=True)
    except ThirUnsupported as ex:
        return _reject(ex.reason, ex.loc)
    # Generic FUNCTION peepholes admit like the generic-record flavor:
    # the template header is skeleton, the `auto`/`auto&&` leaf binds are
    # type-param-neutral, and each leaf's own arm gates any T-typed shape
    # it cannot render.
    # A GENERIC-record receiver is fine: the template header is skeleton
    # and the leaf renders are type-param-neutral (`auto __val =
    # (*this).v;` -- the value bind absorbs the T spelling; the yield
    # slot's optional<V> is the skeleton's). The per-leaf arms gate any
    # T-typed shape they cannot render, exactly like a generic FUNCTION
    # body's leaves.
    if func.forwarded_locals:
        return _reject("sgen.forwarded_local")
    yt = func.generator_yield_type
    if not _sgen_yield_ok(yt if isinstance(yt, TpyType) else None, analyzer):
        # Families whose leaf value render is position-blind: value scalars
        # (the sema-baked coerce carries the yield-type target -- the
        # resumable yield precedent), str/bytes (the bare source render; the
        # skeleton's optional<owned> ctor converts), F1 records (val_or_ref
        # borrow slot, bare render), and Own[F1 record] (owned value slot;
        # the skeleton's `yld` moves __val out). Tuple yields interact with
        # the yield slot's tuple_to_pointer bridge and the borrow-form
        # literal builder; readonly (const-borrow slot), Optional/Union
        # (pointer/storage machinery) and generics stay their own rungs.
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
            cond = _lower_truthy(last.condition, lc, declared, temps_ok=True)
        except ThirUnsupported:
            return _reject("sgen.cond")
        _witness("sgen.while_cond")
        pre_l, yv, post_l = _lower_loop_body(last, lc, declared, loop_depth=1)
        # Statements only: reserving and consuming a rebind slot are both
        # statement nodes, and no THIR expression owns a statement subtree --
        # so the head's `cond` / `iterable` / `range_args` carry nothing the
        # predicate can see, at this seam and at the for-each one below.
        _reject_cross_scope_rebind(init, pre_l, post_l)
        _witness("sgen.body")
        sg = THIRSimpleGenBody(init=init, pre_yield=pre_l,
                               post_yield=post_l, yield_value=yv, cond=cond)
        validate_simple_gen_body(func.name, sg)
        return sg

    assert isinstance(last, TpyForEach)
    iter_elem = last.elem_type
    if iter_elem is not None and isinstance(iter_elem, IntLiteralType):
        iter_elem = analyzer.ctx.default_int_type
    if not _sgen_loop_var_ok(iter_elem, analyzer):
        # Value elements bind a typed copy, an F1-record element `auto&&`
        # into the live source -- both skeleton-side, and the leaf reads the
        # record var through the same borrow-classified forms as a sync
        # for-each body. Pointer-repr tuple elements (which flip
        # `storage_form_tuple_locals`), str/bytes and the remaining
        # families stay their own rungs.
        return _reject("sgen.loop_var_type")
    _ie_tup = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(iter_elem))) \
        if isinstance(iter_elem, TpyType) else None
    if (isinstance(_ie_tup, TupleType)
            and _ie_tup.has_pointer_repr_element()
            and isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                analyzer.get_expr_type(last.iterable)))), TypeParamRef)):
        # A BOUNDED-T iterable with a pointer-repr tuple element: the
        # storage_form registration resolves the T BOUND at its site; the
        # raw-type check below cannot, so the flavor rejects until a
        # bound-following witness lands (dot-vs-arrow channel otherwise).
        return _reject("sgen.loop_var_type")
    is_range = for_range_uses_counter_loop(last)
    range_args: tuple = ()
    iterable = None
    if is_range:
        # Position-blind bound renders: the skeleton's `static_cast<elem>`
        # scaffolding supplies the conversion (a plain render, unlike the
        # sync range arm's literal retype).
        range_args = tuple(_lower_expr(a, lc, declared)
                           for a in last.iterable.args)
        if range_args:
            _witness("sgen.range_arg")
    else:
        # A pointer-slot GLOBAL iterable: the lambda captures the bare
        # slot name and calls `.begin()` on the pointer -- uncompilable C++
        # (pre-existing, see BUGS.md); reject rather than emit it.
        if (isinstance(last.iterable, TpyName)
                and last.iterable.name in lc.prescan.global_slots):
            return _reject("sgen.iterable_global_slot")
        # The skeleton picks its iteration strategy off the DECLARED binding
        # and does not unwrap `Own`, so an Own-bound CONTAINER name is not a
        # native iterable there and it captures the universal `__iter__`
        # object -- never the begin/end pair this leaf would spell off the
        # payload. The sync for-head's container route declines the same
        # binding for the same reason. An Own-bound PROTOCOL name is not this
        # shape: it iterates through the protocol either way.
        if isinstance(last.iterable, TpyName):
            _ib = declared.get(last.iterable.name)
            _ibu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_ib)))
                    if _ib is not None else None)
            if isinstance(_ibu, OwnType):
                _ibw = unwrap_readonly(_ibu.wrapped)
                if is_list(_ibw) or is_dict(_ibw) or is_set(_ibw):
                    return _reject("sgen.iterable_own_binding")
        # A field iterable (`for h in self.items:`) mirrors the sync
        # container route's validation: receiver admission via
        # _field_receiver_ok, then the bare field-read render (the result
        # gate has no ITERABLE arm for container fields; the skeleton owns
        # the iteration strategy).
        iterable = _lower_expr(
            last.iterable, lc, declared,
            use=_ExprUse(result=_ExprResultUse.ITERABLE),
            field_prechecked=(isinstance(last.iterable, TpyFieldAccess)
                              and _field_receiver_ok(last.iterable, declared,
                                                     analyzer)),
            container_threaded=not isinstance(last.iterable, TpyArrayLiteral))
        _witness("sgen.iterable")
    # The while / for-range branches wrap the body in a real C++ loop
    # (break/continue bind); the pull branches run one element per call with
    # no loop, so loop_depth stays 0 there (`is_simple_generator` already
    # excluded loop control on those shapes).
    body_declared = dict(declared)
    body_declared[last.var] = iter_elem
    _it_t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                analyzer.get_expr_type(last.iterable))))
             if last.iterable is not None else None)
    _it_rec = (analyzer.registry.get_record_for_type(_it_t)
               if _it_t is not None else None)
    _direct_iter = (is_protocol_type(_it_t)
                    and _it_t.qualified_name() == "typing.Iterator"
                    if _it_t is not None else False)
    if (isinstance(iter_elem, TupleType)
            and iter_elem.has_pointer_repr_element()
            and not _direct_iter
            and _it_rec is not None and _it_rec.is_native
            and is_native_iterable(_it_t, registry=analyzer.registry)):
        # The skeleton registers storage_form_tuple_locals in its
        # builtin-NativeIterable branch (list/dict/set/views AND
        # Span/Array/varargs): the element binds a proxy-ref or stored
        # tuple, so member reads render value-form (`std::get<1>(kv).v`,
        # dot not arrow). Direct-iterator sources yield the BORROW tuple
        # (pointer elements, arrow reads) and stay out -- the same two
        # branch predicates the skeleton dispatches on, checked on the RAW
        # element like its registration site.
        lc.storage_tuple_locals.add(last.var)
    pre_l, yv, post_l = _lower_loop_body(last, lc, body_declared,
                                         loop_depth=1 if is_range else 0)
    _reject_cross_scope_rebind(init, pre_l, post_l)
    _witness("sgen.body")
    sg = THIRSimpleGenBody(init=init, pre_yield=pre_l, post_yield=post_l,
                           yield_value=yv, iterable=iterable,
                           range_args=range_args)
    validate_simple_gen_body(func.name, sg)
    return sg


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
    body_declared = dict(declared)
    with lc.branch_scope():
        pre_l = _lower_stmts(pre, lc, body_declared, in_branch=True,
                             branch_decls_ok=True, loop_depth=loop_depth)
        yt = lc.func.generator_yield_type
        yt_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(yt)))
                   if isinstance(yt, TpyType) else None)
        owned_view_params = owned_view_frame_params(lc.func.params)
        if isinstance(yt_bare, TupleType):
            # Tuple yield slot -- the resumable Yield tuple arm's mirror:
            # a LITERAL takes the borrow or value builder per the slot's
            # element forms; other sources (storage lifts, names) stay
            # their own rung.
            yv_src = yield_stmt.value
            if (isinstance(yv_src, TpyName)
                    and yt_bare.has_pointer_repr_element()
                    and yv_src.name not in lc.storage_tuple_locals
                    and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        body_declared.get(yv_src.name)))) == yt_bare):
                # A BORROW-form tuple NAME (the loop var relayed whole,
                # `yield pair`): the pointer tuple copies by value into
                # the skeleton's `__val` slot -- the bare name render.
                yv = _lower_expr(yv_src, lc, body_declared)
                _witness("sgen.tuple_yield_name")
            elif (isinstance(yv_src, TpyName)
                  and yv_src.name in lc.storage_tuple_locals
                  # STORAGE yield slots only: a ptr-repr slot needs the
                  # storage->borrow lift a bare name cannot carry (the
                  # loop-var relay fences pin that boundary).
                  and not yt_bare.has_pointer_repr_element()
                  and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                      body_declared.get(yv_src.name))))
                  == collapse_tuple_own_elements(yt_bare)):
                # A STORAGE-form Own-element tuple NAME (`yield t` off
                # `t = (i, Box(...))`) at the matching STORAGE slot: the
                # binding already holds the form the slot spells -- the
                # bare name render (`auto __val = t;`).
                yv = _lower_expr(yv_src, lc, body_declared)
                _witness("sgen.tuple_yield_storage_name")
            elif (isinstance(yv_src, TpySubscript)
                  and yt_bare.has_pointer_repr_element()
                  and isinstance(yv_src.obj, TpyName)
                  and yv_src.obj.name in body_declared
                  and is_list(_sub_ct := unwrap_readonly(unwrap_ref_type(
                      unwrap_send_sync(
                          body_declared[yv_src.obj.name]))))
                  and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                      _sub_ct.type_args[0]))) == yt_bare):
                # A container-ELEMENT source (`yield items[0]`): the
                # storage element lifts to the slot's borrow form --
                # `tuple_to_pointer<std::tuple<int32_t, Box*>>(
                # ::tpy::__getitem__(items, 0))` (the F3 BORROW convert).
                yv = THIRFormConvert(
                    result_type=yt_bare,
                    value=_lower_expr(yv_src, lc, body_declared,
                                      subscript_prechecked=True),
                    form=Form.BORROW, move=False,
                    loc=getattr(yv_src, "loc", None))
                _witness("sgen.tuple_yield_elem_lift")
            elif not isinstance(yv_src, TpyTupleLiteral):
                raise ThirUnsupported("sgen.tuple_yield_source")
            else:
                # The literal-vs-builder selection shared with the
                # resumable tuple-yield ladder (borrow / generic / spelled
                # value literal incl. Own-record storage elements).
                yv = _lower_yield_tuple_literal(
                    yv_src, yt_bare, lc, body_declared,
                    generic_face="sgen.tuple_yield_generic",
                    reject="sgen.tuple_yield_source")
            _witness("sgen.tuple_yield")
        else:
            # `yield copy(p)` -- the shared copy-construct row (`Point(p)`)
            # binding the skeleton's `__val` slot. The slot's own type is the
            # SOURCE record's, so no slot_type override: an `Own[T]` yield
            # slot spells T, which is what the copy already renders.
            copy_row = _lower_copy_record(yield_stmt.value, lc, body_declared)
            if copy_row is not None:
                _witness("sgen.yield_copy_record")
                yv = copy_row
            else:
                # The yield type is threaded into the render
                # (`yield 1` at an `Iterator[int]` -> `::tpy::BigInt(1)`).
                yv = _lower_expr(yield_stmt.value, lc, body_declared)
                # The lambda's `__val` slot owns its str/bytes payload just
                # like the frame's, so a still-view source takes the same
                # explicit copy the resumable arm applies -- except a param
                # the init-capture already copied owned.
                if not (isinstance(yield_stmt.value, TpyName)
                        and yield_stmt.value.name in owned_view_params):
                    yv = _wrap_view_owned_sink(yv, yt_bare, yield_stmt.loc)
                yv = _slot_literal_retype(yv, yt, lc)
        _witness("sgen.yield_value")
        post_l = _lower_stmts(post, lc, body_declared, in_branch=True,
                              branch_decls_ok=True, loop_depth=loop_depth)
    if lc.unhandled_hoists:
        raise ThirUnsupported("body.hoisted_vars")
    return pre_l, yv, post_l


def _reject_cross_scope_rebind(init, pre_l, post_l) -> None:
    """Raise on a loop-body rebind of a name whose slot the pre-loop prologue
    reserved.

    The peephole renders the loop into a lambda while the prologue stays in the
    enclosing function, so the slot has no sound home either side of the capture
    list. Unlike the nested-def seam, the two operands here are one Python scope,
    so a name reserving a slot in the prologue reserves none inside the loop:
    the predicate matches the diagnostic's condition exactly and is safe to
    raise on rather than reject the body."""
    site = cross_scope_rebind_site(init, (*pre_l, *post_l))
    if site is not None:
        emit_prims.reject_rebind_slot_crosses_scope(*site)


def _reject(reason: str, loc: 'SourceLocation | None' = None) -> None:
    note(reason, loc)
    return None
