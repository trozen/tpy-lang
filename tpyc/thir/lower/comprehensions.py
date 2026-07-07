"""Comprehension gate + lowering: the _CompRoute routing fact shared by
_comp_decl_ok (gate) and _lower_comprehension (emit mirror).
"""

from __future__ import annotations
from dataclasses import dataclass
from ...parse.nodes import (
    TpyDictComprehension,
    TpyFieldAccess,
    TpyIntLiteral,
    TpyListComprehension,
    TpyMethodCall,
    TpyName,
    TpySetComprehension,
    TpyVarDecl,
)
from ...typesys import (
    IntLiteralType,
    TpyType,
    TupleType,
    resolve_int_literals,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...type_def_registry import (
    is_array,
    is_big_int_type,
    is_dict,
    is_dict_view,
    is_list,
    is_set,
)
from ...modules.type_resolution import get_iterable_element_type, is_native_iterable
from ..faces import witness as _witness
from ..nodes import THIRCall, THIRComprehension
from .predicates import (
    _dict_view_iterable_ok,
    _eligible_char,
    _eligible_scalar,
    _f1_record,
    _field_decl_type,
    _field_receiver_ok,
    _owned_str_slot,
    _resolved_str_value,
    _resolved_viewfam_value,
    _var_decl_type,
)
from .context import (
    _LowerCtx,
    _Prescan,
    _WalkState,
)
from .expr_gates import (
    _condition_eligible,
    _expr_eligible,
)
from .expressions import (
    _lower_container_elem,
    _lower_expr,
    _lower_field_source,
    _lower_truthy,
    _slot_literal_retype,
)
from . import statements as _statements

_COMP_KINDS = {TpyListComprehension: "list", TpySetComprehension: "set",
               TpyDictComprehension: "dict"}

@dataclass(frozen=True)
class _CompRoute:
    """The shared gate/lowering routing fact for a comprehension decl-init
    (discipline #6): everything both sides must agree on. `unpack_types` is
    None for a plain loop var, else the per-target tuple element types
    (None entries = `_` discards)."""
    kind: str                        # "list" | "set" | "dict"
    loop: str                        # "range" | "begin_end"
    counter_type: 'TpyType | None'   # range counter type
    it_type: 'TpyType | None'        # begin_end iterable type
    et: 'TpyType | None'             # loop-var type (resolved; tuple for unpack)
    iterable_lvalue: bool
    sized_reserve: bool
    unpack_types: 'tuple | None'

def _comp_sized_iterable(t: TpyType) -> bool:
    # Mirror of `_is_sized_type` over the admitted iterable families (Span /
    # varargs never reach the comprehension gate; str/bytes are not sized on
    # the AST side either, so no reserve fires for them).
    t = unwrap_readonly(t)
    return (is_array(t) or is_list(t) or is_dict(t) or is_set(t)
            or is_dict_view(t))

def _comp_route(init, declared: dict[str, TpyType], narrowed: 'set[str]',
                analyzer) -> '_CompRoute | None':
    """Classify a comprehension init into the C1+C2(+C3 range3) slice, or
    None. Slice: range1/range2 counter loops (eligible-scalar counter), 3-arg
    range as a begin/end loop over the Range object, bare-name container
    iterables the container gates admit, and `d.values()`/`d.keys()` dict
    views (`d.items()` for the tuple-unpack form). Owned-move element sources
    (`owns_elements`), field/subscript/call iterables, and narrowed-Optional
    iterables stay on the AST path (C3/C4 rows)."""
    kind = _COMP_KINDS.get(type(init))
    if kind is None:
        return None
    gen = init.generator
    if gen.owns_elements:
        return None
    it = gen.iterable
    if _statements._is_range_call(it):
        if gen.unpack_vars is not None or len(it.args) not in (1, 2, 3):
            return None
        counter = _range_counter_type(it, analyzer)
        if not _eligible_scalar(counter):
            return None
        if len(it.args) == 3:
            # 3-arg range: the AST's _gen_comp_range_loop falls back to a
            # begin/end loop over the Range object (an rvalue capture, never
            # sized). Bounds render against the counter slot like the 1/2-arg
            # arms, so they gate the same way.
            if not all(_expr_eligible(a, declared, analyzer)
                       for a in it.args):
                return None
            return _CompRoute(kind=kind, loop="begin_end", counter_type=None,
                              it_type=analyzer.get_expr_type(it), et=counter,
                              iterable_lvalue=False, sized_reserve=False,
                              unpack_types=None)
        return _CompRoute(kind=kind, loop="range", counter_type=counter,
                          it_type=None, et=counter, iterable_lvalue=True,
                          sized_reserve=False, unpack_types=None)
    if isinstance(it, TpyMethodCall):
        methods = (("items",) if gen.unpack_vars is not None
                   else ("values", "keys"))
        if not _dict_view_iterable_ok(it, declared, analyzer, methods=methods):
            return None
        it_type = analyzer.get_expr_type(it)
        lvalue = False  # a view call result is an rvalue (owning capture)
    elif isinstance(it, (TpyName, TpyFieldAccess)):
        if isinstance(it, TpyName):
            if it.name not in declared or it.name in narrowed:
                return None
            base = declared[it.name]
        else:
            # C3 field iterable: `recv.items` off an F1-record / proven
            # Optional-ptr receiver. The route types on the field's DECLARED
            # type (codegen's get_resolved_type source), so a narrowed
            # Optional/union field (the AST's `(*...)` unwrap) is not
            # iterable at that type and rejects below.
            if not _field_receiver_ok(it, declared, analyzer):
                return None
            ft = _field_decl_type(it, declared, analyzer)
            if ft is None:
                return None
            base = ft
        it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(base)))
        it_view = _resolved_viewfam_value(it_type, analyzer)
        if it_view is not None:
            it_type = it_view
        if gen.unpack_vars is not None and not _statements._container_scalar_tuple_iter(
                it_type, analyzer):
            return None
        # A name is an lvalue; a field chain off one inherits it
        # (is_lvalue_iterable recurses to the Name arm).
        lvalue = True
    else:
        return None
    if it_type is None or not is_native_iterable(it_type, analyzer.registry):
        return None
    et = get_iterable_element_type(it_type, registry=analyzer.registry)
    if et is None:
        return None
    et = resolve_int_literals(unwrap_ref_type(et),
                              analyzer.ctx.default_int_for_literal)
    sized = kind == "list" and _comp_sized_iterable(it_type)
    if gen.unpack_vars is not None:
        elem = unwrap_readonly(et)
        if not isinstance(elem, TupleType):
            return None
        if len(elem.element_types) != len(gen.unpack_vars):
            return None  # defensive: sema errors on arity mismatch
        types: list = []
        for name, ett in zip(gen.unpack_vars, elem.element_types):
            tt = unwrap_ref_type(ett)
            if name is None:
                types.append(None)
                continue
            if not _eligible_scalar(tt):
                return None
            types.append(tt)
        return _CompRoute(kind=kind, loop="begin_end", counter_type=None,
                          it_type=it_type, et=et, iterable_lvalue=lvalue,
                          sized_reserve=sized, unpack_types=tuple(types))
    str_et = _resolved_str_value(et, analyzer)
    if str_et is not None:
        et = str_et
    elif not (_eligible_scalar(et) or _eligible_char(et)
              or _f1_record(et, analyzer)):
        return None
    return _CompRoute(kind=kind, loop="begin_end", counter_type=None,
                      it_type=it_type, et=et, iterable_lvalue=lvalue,
                      sized_reserve=sized, unpack_types=None)

def _comp_slot_ok(slot: 'TpyType | None', analyzer) -> bool:
    # Char slots ride the same targeted element render as scalars
    # (gen_expr_deref(elem, Char) -- the comp elements ARE target-typed,
    # unlike list-literal elements).
    return (_eligible_scalar(slot) or _eligible_char(slot)
            or _owned_str_slot(slot, analyzer))

def _comp_decl_ok(stmt: TpyVarDecl, ws: _WalkState, prescan: _Prescan,
                  analyzer) -> bool:
    """First decl of a comprehension local (`xs = [f(i) for i in ...]`), the
    C1+C2 slice. Same decl-position rules as the container-literal cell (a
    reassigned local is a pointer-local on the AST path); the result must be
    the sema-RESOLVED vector/set/dict (an Array demotion takes the
    `array_from_index` lambda emit -- the C3 row)."""
    init = stmt.init
    if type(init) not in _COMP_KINDS:
        return False
    if (stmt.name in prescan.reassigned or stmt.name in prescan.hoisted
            or stmt.name in prescan.move_through):
        return False
    t = _var_decl_type(stmt, analyzer)
    return _comp_expr_ok(init, t, ws, analyzer)

def _comp_print_arg_ok(a, ws: _WalkState, analyzer) -> bool:
    """A comprehension print arg (C3): wraps in its container printer
    (`::tpy::ListPrinter` / `SetPrinter` / `DictPrinter`); admissibility is
    the position-independent gate over the expr's own resolved result (an
    Array-demoted result takes the array_from_index emit -- deferred row).
    NB the return position stays deferred: container RETURN types reject at
    the signature gate (`_eligible_return`), and widening that is the
    signature axis, not a comprehension row."""
    return _comp_expr_ok(a, analyzer.get_expr_type(a), ws, analyzer)

def _comp_expr_ok(init, t: 'TpyType | None', ws: _WalkState, analyzer) -> bool:
    """Position-independent comprehension admissibility against result type
    `t`: the route fact, result slots, comp-var shadow checks, and the
    element/filter gates. Elements/filters gate through the PLAIN
    `_expr_eligible` / `_condition_eligible` (no temp_args opt-in), so no
    arg-temp can arise inside the loop -- the scoped `_emit_iter_temps`
    flush seam stays un-mirrored until the owned-move/genexpr rows."""
    if t is None:
        return False
    if is_array(t):
        return _comp_array_ok(init, t, ws, analyzer)
    route = _comp_route(init, ws.declared, ws.narrowed, analyzer)
    if route is None:
        return False
    args = getattr(t, "type_args", None)
    if not args:
        return False
    if route.kind == "list" and not (is_list(t)
                                     and _comp_slot_ok(args[0], analyzer)):
        return False
    if route.kind == "set" and not (is_set(t)
                                    and _comp_slot_ok(args[0], analyzer)):
        return False
    if route.kind == "dict" and not (
            is_dict(t) and len(args) == 2
            and _comp_slot_ok(args[0], analyzer)
            and _comp_slot_ok(args[1], analyzer)):
        return False
    gen = init.generator
    # The comp vars shadow same-named outer locals for the element/filter
    # walk; a var shadowing a specially-classified local (pointer / rebind /
    # tuple-alias / narrowed) would need render-state save-restore the slice
    # does not carry -- reject.
    special = (ws.pointers | ws.rebind_slots | ws.storage_tuple_locals
               | ws.narrowed)
    declared2 = dict(ws.declared)
    if route.unpack_types is not None:
        for name, tt in zip(gen.unpack_vars, route.unpack_types):
            if name is None:
                continue
            if name in special:
                return False
            declared2[name] = tt
    else:
        if gen.var in special:
            return False
        declared2[gen.var] = route.et
    if route.loop == "range":
        # Bounds evaluate outside the loop-var scope.
        if not all(_expr_eligible(a, ws.declared, analyzer)
                   for a in gen.iterable.args):
            return False
    if not all(_condition_eligible(c, declared2, analyzer)
               for c in gen.conditions):
        return False
    exprs = ((init.key_expr, init.value_expr) if route.kind == "dict"
             else (init.element_expr,))
    return all(_expr_eligible(x, declared2, analyzer) for x in exprs)

def _range_counter_type(call, analyzer) -> 'TpyType | None':
    """The range call's counter type -- get_iterable_element_type over the
    Range object type, IntLiteral resolved to the module default."""
    counter = get_iterable_element_type(analyzer.get_expr_type(call),
                                        registry=analyzer.registry)
    if counter is None or isinstance(counter, IntLiteralType):
        counter = analyzer.ctx.default_int_type
    return counter

def _comp_array_ok(init, t: TpyType, ws: _WalkState, analyzer) -> bool:
    """An Array-demoted comprehension -- the `array_from_index` RANGE arm
    only (a filter-less, unpack-less list comp over a literal-proven range;
    sema's _try_comp_array_size did the proving, so the bounds are
    compile-time). The Array-SOURCE indexing arm (`__obj_N[__i_N]`) is a
    deferred row. No conditions can appear (a filtered comp has no static
    size, so sema never demotes one) -- rejected defensively anyway."""
    if not isinstance(init, TpyListComprehension):
        return False
    gen = init.generator
    if gen.owns_elements or gen.conditions or gen.unpack_vars is not None:
        return False
    it = gen.iterable
    if not _statements._is_range_call(it) or len(it.args) not in (1, 2, 3):
        return False
    counter = _range_counter_type(it, analyzer)
    if not _eligible_scalar(counter):
        return False
    args_t = getattr(t, "type_args", None)
    if not args_t or not _comp_slot_ok(args_t[0], analyzer):
        return False
    # Bounds render untargeted (the AST's bare gen_expr_deref) -- only the
    # start/step spellings reach the emit; the stop is encoded in N.
    if not all(_expr_eligible(a, ws.declared, analyzer) for a in it.args):
        return False
    special = (ws.pointers | ws.rebind_slots | ws.storage_tuple_locals
               | ws.narrowed)
    if gen.var in special:
        return False
    declared2 = dict(ws.declared)
    declared2[gen.var] = counter
    return _expr_eligible(init.element_expr, declared2, analyzer)

def _lower_array_comprehension(init, lc: '_LowerCtx',
                               declared: dict[str, TpyType]) -> THIRComprehension:
    """The array_from_index range arm: a per-index lambda constructs each
    slot (`E var = start + E(__i_N) * (step); return elem;`). Elements gate
    through the plain `_expr_eligible`, so `_emit_iter_temps` has nothing to
    flush and the lambda body is exactly the binding + return."""
    analyzer = lc.analyzer
    t = analyzer.get_expr_type(init)
    gen = init.generator
    it = gen.iterable
    _witness("comp.array_range")
    counter = _range_counter_type(it, analyzer)
    elem_t = _comp_result_type(init.result_elem_type, analyzer)
    args = it.args
    return THIRComprehension(
        result_type=t,
        kind="list",
        var=gen.var,
        loop="array_range",
        elem_type=counter,
        counter_cpp=counter.to_cpp(),
        array_elem_cpp=lc.render_type(elem_t),
        array_size_cpp=str(t.type_args[1]),
        range_start=_lower_expr(args[0], lc) if len(args) >= 2 else None,
        range_step=_lower_expr(args[2], lc) if len(args) == 3 else None,
        element=_lower_container_elem(init.element_expr, elem_t, lc),
        loc=getattr(init, "loc", None),
    )

def _lower_range_object(call, lc: '_LowerCtx') -> THIRCall:
    """The 3-arg-range iterable: the resolved range overload's cpp_template
    (`::tpy::Range<{T}>({0}, {1}, {2})`) with its type param substituted the
    way gen_call_from_fi does (`to_cpp_stored`); bounds render against the
    counter slot exactly like the 1/2-arg range-arm bounds."""
    fi = call.resolved_function_info
    template = fi.cpp_template
    targs = getattr(call, "inferred_type_args", None)
    if targs and fi.type_params:
        for name, typ in zip(fi.type_params, targs):
            template = template.replace(f"{{{name}}}", typ.to_cpp_stored())
    counter = _range_counter_type(call, lc.analyzer)
    args = tuple(_slot_literal_retype(_lower_expr(a, lc), counter)
                 for a in call.args)
    return THIRCall(result_type=lc.analyzer.get_expr_type(call),
                    callee=call.func_name, args=args, cpp_template=template,
                    loc=getattr(call, "loc", None))

def _comp_result_type(t: 'TpyType | None', analyzer) -> TpyType:
    # `_resolve_int_literal`'s mirror: sema stamps the result element/key/value
    # types on the node; a still-literal int resolves to the default int.
    assert t is not None
    if isinstance(t, IntLiteralType):
        return analyzer.ctx.default_int_type
    return t

def _lower_comprehension(init, lc: '_LowerCtx',
                         declared: dict[str, TpyType]) -> THIRComprehension:
    """Build the THIRComprehension node (the gate admitted the shape). The
    container spelling composes from the node's sema-stamped result types
    exactly like `_gen_list/dict/set_comprehension`; elements/keys/values
    lower through the S5 per-slot owned-str wrap (`_lower_container_elem`,
    target-typed like the AST's `gen_expr_deref(elem, elem_type)`)."""
    analyzer = lc.analyzer
    loc = getattr(init, "loc", None)
    if is_array(analyzer.get_expr_type(init)):
        return _lower_array_comprehension(init, lc, declared)
    # The gate rejected narrowed iterables, so the route facts recompute
    # identically with an empty narrowed set here.
    route = _comp_route(init, declared, set(), analyzer)
    assert route is not None
    gen = init.generator
    _witness(f"comp.{route.kind}")
    _witness(f"comp.{route.loop}")
    if gen.conditions:
        _witness("comp.filter")
    if route.sized_reserve:
        _witness("comp.reserve")
    if route.kind == "dict":
        kt = _comp_result_type(init.result_key_type, analyzer)
        vt = _comp_result_type(init.result_value_type, analyzer)
        container = (f"::tpy::ordered_map<{lc.render_type(kt)}, "
                     f"{lc.render_type(vt)}>")
        element = None
        key = _lower_container_elem(init.key_expr, kt, lc)
        value = _lower_container_elem(init.value_expr, vt, lc)
    else:
        elem_t = _comp_result_type(init.result_elem_type, analyzer)
        cpp_elem = lc.render_type(elem_t)
        container = (f"std::vector<{cpp_elem}>" if route.kind == "list"
                     else f"::tpy::ordered_set<{cpp_elem}>")
        element = _lower_container_elem(init.element_expr, elem_t, lc)
        key = value = None
    range_start = range_stop = None
    start_lit = stop_lit = False
    iterable = None
    if route.loop == "range":
        a = gen.iterable.args
        if len(a) == 2:
            range_start = _slot_literal_retype(_lower_expr(a[0], lc),
                                               route.counter_type)
            start_lit = isinstance(a[0], TpyIntLiteral)
        stop_arg = a[1] if len(a) == 2 else a[0]
        range_stop = _slot_literal_retype(_lower_expr(stop_arg, lc),
                                          route.counter_type)
        stop_lit = isinstance(stop_arg, TpyIntLiteral)
    elif _statements._is_range_call(gen.iterable):
        _witness("comp.range3")
        iterable = _lower_range_object(gen.iterable, lc)
    elif isinstance(gen.iterable, TpyFieldAccess):
        # The non-value field read (storage form) -- gen_expr_deref's plain
        # `recv.field` / `recv->field` render.
        _witness("comp.field_iter")
        iterable = _lower_field_source(gen.iterable, lc)
    else:
        iterable = _lower_expr(gen.iterable, lc)
    unpack_targets: tuple = ()
    unpack_cpps: tuple = ()
    if route.unpack_types is not None:
        _witness("comp.unpack")
        unpack_targets = tuple(gen.unpack_vars)
        unpack_cpps = tuple(None if tt is None else lc.render_type(tt)
                            for tt in route.unpack_types)
    return THIRComprehension(
        result_type=analyzer.get_expr_type(init),
        kind=route.kind,
        container_cpp=container,
        var=gen.var,
        loop=route.loop,
        elem_type=route.et,
        const_loop_var=gen.const_loop_var,
        counter_cpp=(route.counter_type.to_cpp()
                     if route.counter_type is not None else ""),
        counter_bigint=(route.counter_type is not None
                        and is_big_int_type(route.counter_type)),
        range_start=range_start,
        range_stop=range_stop,
        range_start_literal=start_lit,
        range_stop_literal=stop_lit,
        iterable=iterable,
        iterable_lvalue=route.iterable_lvalue,
        sized_reserve=route.sized_reserve,
        unpack_targets=unpack_targets,
        unpack_target_cpps=unpack_cpps,
        conditions=tuple(_lower_truthy(c, lc) for c in gen.conditions),
        element=element,
        key=key,
        value=value,
        loc=loc,
    )
