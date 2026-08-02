"""Comprehension classification and lowering."""

from __future__ import annotations
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from ...parse.nodes import (
    TpyArrayLiteral,
    TpyTupleLiteral,
    TpyCall,
    TpyCoerce,
    TpyDictComprehension,
    TpyFieldAccess,
    TpyGeneratorExpression,
    TpyIntLiteral,
    TpyListComprehension,
    TpyMethodCall,
    TpyName,
    TpySetComprehension,
    collect_name_refs,
)
from ...typesys import (
    IntLiteralType,
    TpyType,
    TupleType,
    resolve_int_literals,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
    yield_uses_borrow_slot,
)
from ...type_def_registry import (
    is_array,
    is_big_int_type,
    is_bytes_type,
    is_dict,
    is_dict_view,
    is_list,
    is_set,
    is_span,
)
from ...modules.type_resolution import get_iterable_element_type, is_native_iterable
from ...codegen_cpp.context import (
    escape_cpp_name, is_lvalue_iterable, loop_var_binding)
from ..fallback import ThirUnsupported
from ..faces import witness as _witness
from ..nodes import (
    THIRComprehension, THIRContainerLiteral, THIRExpr, THIRGenExpr, THIRMove)
from .predicates import (
    _dict_view_iterable_ok,
    _eligible_char,
    _eligible_enum,
    _eligible_scalar,
    _f1_record,
    _field_decl_type,
    _field_receiver_ok,
    _for_each_elem_binding_ok,
    _is_range_call,
    _owned_str_slot,
    _range_counter_type,
    _resolved_bytes_value,
    _resolved_str_value,
    _resolved_viewfam_value,
    _value_tuple,
)
from .context import (
    _ExprResultUse,
    _ExprUse,
    _LowerCtx,
)
from .expressions import (
    _lower_checked_container_elem,
    _lower_container_elem,
    _lower_expr,
    _lower_field_source,
    _lower_range_object,
    _lower_truthy,
    _slot_literal_retype,
)
from . import statements as _statements

_COMP_KINDS = {TpyListComprehension: "list", TpySetComprehension: "set",
               TpyDictComprehension: "dict"}

@dataclass(frozen=True)
class _CompRoute:
    """The routing fact consumed by comprehension lowering.

    `unpack_types` is
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
    owns_elements: bool = False      # source yields Own[T]: sinks move


def _comp_sized_iterable(t: TpyType) -> bool:
    # Mirror of `_is_sized_type` over the admitted iterable families (Span /
    # varargs never reach this comprehension route; str/bytes are not sized on
    # the AST side either, so no reserve fires for them).
    t = unwrap_readonly(t)
    return (is_array(t) or is_list(t) or is_dict(t) or is_set(t)
            or is_dict_view(t))

def _comp_route(init, declared: dict[str, TpyType], narrowed: 'set[str]',
                analyzer) -> '_CompRoute | None':
    """Classify a comprehension init into the C1+C2(+C3 range3) slice, or
    None. Slice: range1/range2 counter loops (eligible-scalar counter), 3-arg
    range as a begin/end loop over the Range object, bare-name container
    iterables the container classifiers admit, and `d.values()`/`d.keys()` dict
    views (`d.items()` for the tuple-unpack form). Owned-move element sources
    (`owns_elements`), field/subscript/call iterables, and narrowed-Optional
    iterables stay on the AST path (C3/C4 rows)."""
    kind = _COMP_KINDS.get(type(init))
    if kind is None:
        return None
    gen = init.generator
    owns = gen.owns_elements
    it = gen.iterable
    if _is_range_call(it):
        if owns:
            return None  # defensive: range yields scalars, never Own[T]
        if gen.unpack_vars is not None or len(it.args) not in (1, 2, 3):
            return None
        counter = _range_counter_type(it, analyzer)
        if not _eligible_scalar(counter):
            return None
        if len(it.args) == 3:
            # 3-arg range: the AST's _gen_comp_range_loop falls back to a
            # begin/end loop over the Range object (an rvalue capture, never
            # sized). Bounds render against the counter slot like the 1/2-arg
            # arms, so they classify the same way.
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
                it_type, analyzer, allow_record=True):
            return None
        # A name is an lvalue; a field chain off one inherits it
        # (is_lvalue_iterable recurses to the Name arm).
        lvalue = True
    elif isinstance(it, TpyCall) and owns:
        # An Own[T]-yielding generator source (`widgets(3)`), the owned-move
        # comprehension. The generator is iterated via begin/end (the AST's
        # comprehension loop emits them unconditionally -- so `is_native_iterable`
        # is bypassed for this arm; `get_iterable_element_type` below is the
        # iterability gate). The lvalue verdict rides the shared
        # `is_lvalue_iterable` (a protocol/generator return is a by-value rvalue
        # -> owning `auto __obj_N =` capture).
        ret = analyzer.get_expr_type(it)
        if ret is None or gen.unpack_vars is not None:
            return None
        it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
        if _resolved_str_value(it_type, analyzer) is not None:
            return None
        lvalue = is_lvalue_iterable(it, analyzer.registry.get_record,
                                    analyzer.get_expr_type)
    else:
        return None
    if it_type is None:
        return None
    # The owned-generator call arm iterates via begin/end; every other arm
    # requires a NativeIterable source.
    if not owns and not is_native_iterable(it_type, analyzer.registry):
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
            # A str element target COPIES the stored element (`std::string k =
            # std::get<i>(tup);` -- the AST's is_value_type branch over the
            # tuple's OWNED element spelling), unlike the for-each unpack's
            # view binding; the owned declared type keeps the target's reads
            # STORAGE-form (bare inserts). An F1-record target BORROWS with
            # the ref binding keyed on const_loop_var alone (`auto& r =` /
            # `const auto& p =` -- the AST's `_emit_inline_tuple_unpack`
            # spells ref_binding for every non-value target).
            if not (_eligible_scalar(tt) or _owned_str_slot(tt, analyzer)
                    or _f1_record(
                        unwrap_readonly(unwrap_send_sync(tt)),
                        analyzer)):
                return None
            types.append(tt)
        return _CompRoute(kind=kind, loop="begin_end", counter_type=None,
                          it_type=it_type, et=et, iterable_lvalue=lvalue,
                          sized_reserve=sized, unpack_types=tuple(types),
                          owns_elements=owns)
    str_et = _resolved_str_value(et, analyzer)
    if str_et is not None:
        et = str_et
    # Compositional loop-var classifier (the for-each twin): the begin/end comp loop
    # binds the loop var through the SAME shared loop_var_binding, so any
    # resolved element renders identically; the element/key/value/filter reads
    # of the var route recursively through expression lowering. Only an unresolved
    # pending element (spelled before the AST's resolve_type concretizes it)
    # stays on the AST path.
    if not _for_each_elem_binding_ok(et):
        return None
    return _CompRoute(kind=kind, loop="begin_end", counter_type=None,
                      it_type=it_type, et=et, iterable_lvalue=lvalue,
                      sized_reserve=sized, unpack_types=None,
                      owns_elements=owns)

def _comp_slot_ok(slot: 'TpyType | None', analyzer) -> bool:
    # The NARROW slot predicate, kept for dict KEYS (the hashable-key axis:
    # widening keys to enum/bytes/record is the container-literal cell's
    # separate key-family concern -- a record key isn't hashable, an enum/bytes
    # key rides a later row). Char slots ride the same targeted element render
    # as scalars (gen_expr_deref(elem, Char) -- comp elements ARE target-typed,
    # unlike list-literal elements).
    return (_eligible_scalar(slot) or _eligible_char(slot)
            or _owned_str_slot(slot, analyzer))

def _comp_elem_slot_ok(slot: 'TpyType | None', analyzer, *,
                       allow_container: bool = False) -> bool:
    """The list/set element + dict VALUE result slot whose
    `_lower_container_elem` render is element-SHAPE-independent, so ANY routed
    element expr into it is byte-identical -- the compositional twin of the
    for-each loop-var classifier on the append/insert side. The wrap keys on the
    element's FORM, not its node kind:

    - value scalar / Char -- bare / target-typed literal retype;
    - owned str / bytes slot -- a BORROW source copies (`std::string(x)` /
      `::tpy::bytes_copy`), a STORAGE/literal source lands bare;
    - enum -- a value type, bare;
    - F1-record -- a name derefs/moves off the same movable_locals facts the
      AST reads, an rvalue lands bare (no owned-slot wrap for records).

    The SHAPE-sensitive families stay on the AST path (`_lower_container_elem`
    branches on the element NODE for them, so a general routed element could
    diverge): Optional (`None` -> `std::nullopt` vs a scalar value), value-tuple
    (a tuple LITERAL only), nested container (an Array LITERAL only), union, and
    a str/bytes VIEW slot (owned-only here).

    `allow_container` (the dict VALUE slot only) admits a list/Array container
    slot -- SHAPE-sensitive, so `_lower_comp_container_elem` gates the element
    NODE (a container literal / nested comprehension), mirroring
    `_container_lit_elem_ok`'s `fam == "container"` arm."""
    if _comp_slot_ok(slot, analyzer):
        return True
    bt = _resolved_bytes_value(slot, analyzer)
    if bt is not None and is_bytes_type(bt):
        return True
    if allow_container and _container_family_slot(slot):
        return True
    return (_eligible_enum(slot, analyzer) is not None
            or _f1_record(slot, analyzer))

def _container_family_slot(slot: 'TpyType | None') -> bool:
    if slot is None:
        return False
    su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    return is_list(su) or is_array(su)

def _unpack_target_cpps(unpack_types, lc: '_LowerCtx',
                        const_loop_var: bool = True) -> tuple:
    """The per-target decl spellings of a comp unpack head: None for `_`
    discards, the ref binding for F1-record targets (`const auto&` /
    `auto&`, keyed on const_loop_var exactly like the AST's
    `_emit_inline_tuple_unpack` ref_binding), the rendered type otherwise.
    Shared by the begin_end and array_source arms."""
    ref = "const auto&" if const_loop_var else "auto&"
    return tuple(
        None if tt is None
        else (ref if _f1_record(
                  unwrap_readonly(unwrap_send_sync(tt)), lc.analyzer)
              else lc.render_type(tt))
        for tt in unpack_types)


def _comp_lowering_route(
        init, t: 'TpyType | None', declared: dict[str, TpyType],
        pointers: AbstractSet[str], rebind_slots: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str],
        narrowed: AbstractSet[str], analyzer) -> '_CompRoute | None':
    """Resolve the route data consumed while lowering a comprehension."""
    if t is None:
        return None
    if is_array(t):
        return _comp_array_route(
            init, t, declared, pointers, rebind_slots,
            storage_tuple_locals, narrowed, analyzer)
    route = _comp_route(init, declared, narrowed, analyzer)
    if route is None:
        return None
    args = getattr(t, "type_args", None)
    if not args:
        return None
    if route.kind == "list" and not (is_list(t)
                                     and (_comp_elem_slot_ok(
                                              args[0], analyzer,
                                              allow_container=True)
                                          # A NON-VALUE tuple element slot,
                                          # node-gated like the other
                                          # shape-sensitive families: a tuple
                                          # LITERAL rides the storage-direct/
                                          # borrow-ladder rows, a bare NAME
                                          # the whole tuple_to_storage copy.
                                          or (isinstance(
                                                  init.element_expr,
                                                  (TpyTupleLiteral, TpyName))
                                              and isinstance(
                                                  unwrap_readonly(
                                                      unwrap_ref_type(
                                                          unwrap_send_sync(
                                                              args[0]))),
                                                  TupleType)))):
        return None
    if route.kind == "set" and not (is_set(t)
                                    and _comp_elem_slot_ok(args[0], analyzer,
                                                           allow_container=True)):
        return None
    if route.kind == "dict":
        # An owned-move source admits a hashable F1-record KEY (sema validated
        # hashability by giving the dict a record key type); every other dict
        # comp keeps the narrow key slice.
        key_ok = (_comp_slot_ok(args[0], analyzer)
                  or (route.owns_elements and _f1_record(args[0], analyzer)))
        if not (is_dict(t) and len(args) == 2 and key_ok
                and _comp_elem_slot_ok(args[1], analyzer,     # value: widened
                                       allow_container=True)):
            return None
    gen = init.generator
    # The comp vars shadow same-named outer locals for the element/filter
    # walk; a var shadowing a specially-classified local (pointer / rebind /
    # tuple-alias / narrowed) would need render-state save-restore the slice
    # does not carry -- reject.
    special = (pointers | rebind_slots | storage_tuple_locals | narrowed)
    if route.unpack_types is not None:
        for name in gen.unpack_vars:
            if name is None:
                continue
            if name in special:
                return None
    else:
        if gen.var in special:
            return None
    return route

def _comp_array_route(
        init, t: TpyType, declared: dict[str, TpyType],
        pointers: AbstractSet[str], rebind_slots: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str],
        narrowed: AbstractSet[str], analyzer) -> '_CompRoute | None':
    """An Array-demoted comprehension -- the `array_from_index` RANGE arm
    (a filter-less, unpack-less list comp over a literal-proven range;
    sema's _try_comp_array_size did the proving, so the bounds are
    compile-time) or the Array-SOURCE indexing arm (`__obj_N[__i_N]`, a
    random-access loop over a sized Array source). No conditions can appear
    (a filtered comp has no static size, so sema never demotes one) --
    rejected defensively anyway."""
    if not isinstance(init, TpyListComprehension):
        return None
    gen = init.generator
    if gen.owns_elements or gen.conditions:
        return None
    args_t = getattr(t, "type_args", None)
    if not args_t or not (
            _comp_elem_slot_ok(args_t[0], analyzer, allow_container=True)
            # A VALUE-tuple element slot with a tuple-LITERAL element
            # (`[(i, i * 10) for i in range(3)]` demoted to an Array of
            # tuples): the spelled value render, shape-gated on the node
            # like the other shape-sensitive families.
            or (isinstance(init.element_expr, TpyTupleLiteral)
                and _value_tuple(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        args_t[0]))), analyzer) is not None)):
        return None
    special = (pointers | rebind_slots | storage_tuple_locals | narrowed)
    if gen.unpack_vars is not None:
        # Unpack heads ride the SOURCE arm only (the range arm has no tuple
        # to destructure); the shadow check covers each head var.
        if any(name is not None and name in special
               for name in gen.unpack_vars):
            return None
    elif gen.var in special:
        return None
    it = gen.iterable
    if gen.unpack_vars is None and _is_range_call(it) \
            and len(it.args) in (1, 2, 3):
        counter = _range_counter_type(it, analyzer)
        if not _eligible_scalar(counter):
            return None
        return _CompRoute(
            kind="list", loop="array_range", counter_type=counter,
            it_type=None, et=counter, iterable_lvalue=True,
            sized_reserve=False, unpack_types=None)
    # Array-SOURCE indexing arm: `_gen_array_comprehension`'s non-range branch
    # borrows the source once (`__obj_N`) and indexes it per slot. Reuse the
    # begin/end name/field classifier for the loop-var binding fact, then
    # require a statically-sized Array source (the only shape sema demotes to
    # Array). A view result off the source rebinds it_type in _comp_route.
    base = _comp_route(init, declared, narrowed, analyzer)
    if base is None or base.loop != "begin_end":
        return None
    src = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(base.it_type)))
    if not is_array(src):
        return None
    # UNPACK heads ride along (`[a + b for a, b in ps]` over an Array of
    # tuples): the per-index lambda binds `auto& __tup_N = __obj_N[__i_N];`
    # and per-var `std::get` decls, the begin_end arm's prologue at the
    # indexed read.
    return _CompRoute(
        kind="list", loop="array_source", counter_type=None,
        it_type=base.it_type, et=base.et, iterable_lvalue=base.iterable_lvalue,
        sized_reserve=False, unpack_types=base.unpack_types)

def _lower_array_comprehension(
        init, result_type: TpyType, route: _CompRoute, lc: '_LowerCtx',
        declared: dict[str, TpyType]) -> THIRComprehension:
    """The array_from_index range arm: a per-index lambda constructs each
    slot (`E var = start + E(__i_N) * (step); return elem;`)."""
    analyzer = lc.analyzer
    t = result_type
    gen = init.generator
    it = gen.iterable
    _witness("comp.array_range")
    counter = route.counter_type
    assert counter is not None
    elem_t = _comp_result_type(init.result_elem_type, analyzer)
    args = it.args
    body_declared = dict(declared)
    body_declared[gen.var] = counter
    return THIRComprehension(
        result_type=t,
        kind="list",
        var=gen.var,
        loop="array_range",
        elem_type=counter,
        counter_cpp=counter.to_cpp(),
        array_elem_cpp=lc.render_type(elem_t),
        array_size_cpp=str(t.type_args[1]),
        range_start=_lower_expr(args[0], lc, declared) if len(args) >= 2 else None,
        range_step=_lower_expr(args[2], lc, declared) if len(args) == 3 else None,
        element=_lower_comp_container_elem(
            init.element_expr, elem_t, lc, body_declared),
        loc=getattr(init, "loc", None),
    )

def _lower_array_source_comprehension(
        init, result_type: TpyType, route: _CompRoute, lc: '_LowerCtx',
        declared: dict[str, TpyType]) -> THIRComprehension:
    """The array_from_index SOURCE arm: borrow a sized Array source once
    (`__obj_N`, lvalue-verdict binding) inside a `({...})` prelude, then index
    it per slot (`E var = __obj_N[__i_N];` value binding / `auto&& var = ...`
    borrow) -- `_gen_array_comprehension`'s non-range branch. The element
    renders through the shared per-slot wrap after the loop-var binding."""
    analyzer = lc.analyzer
    gen = init.generator
    it = gen.iterable
    _witness("comp.array_source")
    elem_t = _comp_result_type(init.result_elem_type, analyzer)
    body_declared = dict(declared)
    unpack_targets: tuple = ()
    unpack_cpps: tuple = ()
    if route.unpack_types is not None:
        _witness("comp.unpack")
        for name, tt in zip(gen.unpack_vars, route.unpack_types):
            if name is not None:
                body_declared[name] = tt
        unpack_targets = tuple(gen.unpack_vars)
        unpack_cpps = _unpack_target_cpps(route.unpack_types, lc,
                                  gen.const_loop_var)
    else:
        body_declared[gen.var] = route.et
    if isinstance(it, TpyFieldAccess):
        iterable = _lower_field_source(it, lc, declared)
    else:
        iterable = _lower_expr(
            it, lc, declared, use=_ExprUse(result=_ExprResultUse.ITERABLE))
    return THIRComprehension(
        result_type=result_type,
        kind="list",
        var=gen.var,
        loop="array_source",
        elem_type=route.et,
        const_loop_var=gen.const_loop_var,
        array_elem_cpp=lc.render_type(elem_t),
        array_size_cpp=str(result_type.type_args[1]),
        iterable=iterable,
        iterable_lvalue=route.iterable_lvalue,
        unpack_targets=unpack_targets,
        unpack_target_cpps=unpack_cpps,
        element=_lower_comp_container_elem(
            init.element_expr, elem_t, lc, body_declared),
        loc=getattr(init, "loc", None),
    )

def _comp_result_type(t: 'TpyType | None', analyzer) -> TpyType:
    # `_resolve_int_literal`'s mirror: sema stamps the result element/key/value
    # types on the node; a still-literal int resolves to the default int.
    assert t is not None
    if isinstance(t, IntLiteralType):
        return analyzer.ctx.default_int_type
    return t

def _lower_comp_container_elem(e, vt: TpyType, lc: '_LowerCtx',
                               body_declared: dict[str, TpyType],
                               *, typed_brace: bool = False) -> 'THIRExpr':
    """Lower a comprehension element/value against a possibly-container slot
    (dict VALUE, list/set element, Array slot). A list/Array container slot
    is element-SHAPE-sensitive, so only the two vetted sources route: a
    container LITERAL -- rendered self-describing (`std::array<int32_t, 2>{...}`,
    mirroring the AST's `typed_brace_init`: the insert/brace target is a
    template, a bare brace-init cannot deduce) -- and a nested COMPREHENSION
    (`_lower_container_elem`'s comp arm; its `({...})` stmt-expr is already
    self-describing, the AST's typed_brace_init no-ops on it). Every other slot
    family keeps the shape-independent element render (`_lower_container_elem`).

    `typed_brace` (the dict VALUE only) spells the container literal
    self-describing (`std::array<int32_t, 2>{...}`) because `insert_or_assign`
    is a template that cannot deduce a bare brace. A list/set element
    (`push_back`) and an array-lambda return already have a declared target
    type, so their brace stays bare -- matching the AST."""
    if not _container_family_slot(vt):
        return _lower_container_elem(e, vt, lc, body_declared)
    if type(e) in _COMP_KINDS:
        value = _lower_container_elem(e, vt, lc, body_declared)
    elif isinstance(e, TpyArrayLiteral):
        value = _lower_checked_container_elem(
            e, vt, lc, body_declared, threaded=True, forced=True,
            allow_nested=True)
        if typed_brace and isinstance(value, THIRContainerLiteral):
            value = replace(value, typed_brace_cpp=lc.render_type(
                unwrap_readonly(unwrap_ref_type(vt))))
    else:
        raise ThirUnsupported("comp.container_value", detail=True)
    _witness("comp.container_value")
    return value

def _lower_owned_comp_sink(e, slot: 'TpyType | None', lc: '_LowerCtx',
                           body_declared: dict[str, TpyType], gen,
                           *, is_last_sink: bool) -> 'THIRExpr':
    """Lower one sink (list/set element, dict value) of an owned-move
    comprehension -- `_gen_comp_owned_elem` + `_move_comp_sink` under
    `_comp_owned_move_scope`. The move set is restricted to THIS comp's loop var
    (the loop var is rebound each iteration, so moving it is sound; an outer
    movable would multi-move). A bare-loop-var LAST sink moves UNCONDITIONALLY
    (structurally the last use, sequenced after any earlier read); an
    earlier/derived sink defers to the ordinary `_maybe_move` last-use gate."""
    # Whole-set REPLACEMENT for this one sink render -- an expression-position
    # override, not a lexical scope, so it stays a manual single-set swap
    # rather than a branch_scope (context.py's _BRANCH_SCOPED_SETS).
    saved = lc.movable_locals
    lc.movable_locals = {gen.var}
    try:
        lowered = _lower_container_elem(e, slot, lc, body_declared)
    finally:
        lc.movable_locals = saved
    inner = e
    while isinstance(inner, TpyCoerce):
        inner = inner.expr
    if (is_last_sink and isinstance(inner, TpyName) and inner.name == gen.var
            and not isinstance(lowered, THIRMove)):
        lowered = THIRMove(result_type=lowered.result_type, value=lowered,
                           form=lowered.form, loc=getattr(e, "loc", None))
    return lowered


def _lower_comprehension(
        init, result_type: 'TpyType | None', lc: '_LowerCtx',
        declared: dict[str, TpyType],
        pointers: AbstractSet[str]) -> THIRComprehension:
    """Build the THIRComprehension node from its classified route. The
    container spelling composes from the node's sema-stamped result types
    exactly like `_gen_list/dict/set_comprehension`; elements/keys/values
    lower through the S5 per-slot owned-str wrap (`_lower_container_elem`,
    target-typed like the AST's `gen_expr_deref(elem, elem_type)`)."""
    analyzer = lc.analyzer
    loc = getattr(init, "loc", None)
    route = _comp_lowering_route(
        init, result_type, declared, pointers, lc.rebind_slot_locals,
        lc.storage_tuple_locals, lc.narrow.narrowed.keys(), analyzer)
    if route is None or result_type is None:
        raise ThirUnsupported("comp.route", detail=True)
    if route.loop == "array_range":
        return _lower_array_comprehension(
            init, result_type, route, lc, declared)
    if route.loop == "array_source":
        return _lower_array_source_comprehension(
            init, result_type, route, lc, declared)
    gen = init.generator
    body_declared = dict(declared)
    if route.unpack_types is not None:
        for name, tt in zip(gen.unpack_vars, route.unpack_types):
            if name is not None:
                body_declared[name] = tt
    else:
        body_declared[gen.var] = route.et
    # A comprehension loop var over a container of pointer-repr tuples reads
    # STORAGE form, exactly as the for-statement loop var does -- the AST
    # registers it at comp-scope entry (`register_loop_var_storage_form`) so the
    # element consumer sees it. Without the mirror the element wrap re-lifts an
    # already-storage read.
    #
    # Membership makes EVERY read of the name in the body STORAGE form (the Form
    # verdict in `expressions.py`), not just the element wrap -- filters and
    # subscripts included, which is what the AST does too.
    #
    # PARTIAL against `register_loop_var_storage_form`, each part unreachable
    # today via a gate elsewhere rather than anything here, so widening any of
    # those routes must revisit this: no `is_native_iterable` gate (a generator
    # yields borrow-form tuples; the owns-arm keeps `et` an OwnType), no
    # `borrow_form_tuple_locals` exclusion (those shapes reject at the outer
    # decl), no `const_storage_tuple_locals` channel, no storage-Optional
    # family, and this sits AFTER the array_range/array_source early returns
    # (those routes admit only value-tuple slots with literal elements).
    comp_storage_var = None
    et_peeled = unwrap_readonly(route.et) if route.et is not None else None
    if (route.unpack_types is None and isinstance(et_peeled, TupleType)
            and et_peeled.has_pointer_repr_element()
            and gen.var not in lc.storage_tuple_locals):
        comp_storage_var = gen.var
        lc.storage_tuple_locals.add(gen.var)
    try:
        return _build_comprehension_body(
            init, result_type, route, lc, declared, body_declared, gen,
            analyzer, loc, pointers)
    finally:
        if comp_storage_var is not None:
            lc.storage_tuple_locals.discard(comp_storage_var)


def _build_comprehension_body(init, result_type, route, lc, declared,
                              body_declared, gen, analyzer, loc, pointers):
    """The comprehension body build, split out so the loop-var storage-form
    registration above can scope itself symmetrically (mirroring the AST's
    comp-scope enter/exit)."""
    _witness(f"comp.{route.kind}")
    _witness(f"comp.{route.loop}")
    if gen.conditions:
        _witness("comp.filter")
    if route.sized_reserve:
        _witness("comp.reserve")
    value_moved = False
    if route.kind == "dict":
        kt = _comp_result_type(init.result_key_type, analyzer)
        vt = _comp_result_type(init.result_value_type, analyzer)
        container = (f"::tpy::ordered_map<{lc.render_type(kt)}, "
                     f"{lc.render_type(vt)}>")
        element = None
        if route.owns_elements:
            # The value is the last sink (a bare owned loop var moves
            # unconditionally); the key is earlier, so it moves only when it is
            # itself the last use (the ordinary last-use gate). When the value
            # moves, the key is sequenced into `__dk_N` first at emit.
            key = _lower_owned_comp_sink(init.key_expr, kt, lc, body_declared,
                                         gen, is_last_sink=False)
            value = _lower_owned_comp_sink(init.value_expr, vt, lc,
                                           body_declared, gen, is_last_sink=True)
            value_moved = isinstance(value, THIRMove)
        else:
            key = _lower_container_elem(init.key_expr, kt, lc, body_declared)
            value = _lower_comp_container_elem(init.value_expr, vt, lc,
                                               body_declared, typed_brace=True)
    else:
        elem_t = _comp_result_type(init.result_elem_type, analyzer)
        cpp_elem = lc.render_type(elem_t)
        container = (f"std::vector<{cpp_elem}>" if route.kind == "list"
                     else f"::tpy::ordered_set<{cpp_elem}>")
        if route.owns_elements:
            element = _lower_owned_comp_sink(
                init.element_expr, elem_t, lc, body_declared, gen,
                is_last_sink=True)
        else:
            element = _lower_comp_container_elem(
                init.element_expr, elem_t, lc, body_declared)
        key = value = None
    range_start = range_stop = None
    start_lit = stop_lit = False
    iterable = None
    if route.loop == "range":
        a = gen.iterable.args
        if len(a) == 2:
            range_start = _slot_literal_retype(_lower_expr(a[0], lc, declared),
                                               route.counter_type, lc)
            start_lit = isinstance(a[0], TpyIntLiteral)
        stop_arg = a[1] if len(a) == 2 else a[0]
        range_stop = _slot_literal_retype(_lower_expr(stop_arg, lc, declared),
                                          route.counter_type, lc)
        stop_lit = isinstance(stop_arg, TpyIntLiteral)
    elif _is_range_call(gen.iterable):
        _witness("comp.range3")
        iterable = _lower_range_object(gen.iterable, lc, declared)
    elif isinstance(gen.iterable, TpyFieldAccess):
        # The non-value field read (storage form) -- gen_expr_deref's plain
        # `recv.field` / `recv->field` render.
        _witness("comp.field_iter")
        iterable = _lower_field_source(gen.iterable, lc, declared)
    else:
        iterable = _lower_expr(
            gen.iterable, lc, declared,
            use=_ExprUse(result=_ExprResultUse.ITERABLE))
    unpack_targets: tuple = ()
    unpack_cpps: tuple = ()
    if route.unpack_types is not None:
        _witness("comp.unpack")
        unpack_targets = tuple(gen.unpack_vars)
        unpack_cpps = _unpack_target_cpps(route.unpack_types, lc,
                                  gen.const_loop_var)
    return THIRComprehension(
        result_type=result_type,
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
        conditions=tuple(
            _lower_truthy(c, lc, body_declared) for c in gen.conditions),
        element=element,
        key=key,
        value=value,
        value_moved=value_moved,
        loc=loc,
    )


def _genexpr_captures(element, conditions, extra_refs, declared, comp_vars,
                      self_receiver) -> str:
    """Mirror ExpressionGenerator._genexpr_outer_captures: the `&local, ` prefix
    for the outer names a genexpr lambda reads. Refs come from the element
    (+ filter conditions, + `extra_refs` for the IIFE's iterable refs); keep only
    function locals not shadowed by the comprehension scope; `self` maps to the
    captured `this`."""
    refs = collect_name_refs(element)
    for c in conditions:
        refs |= collect_name_refs(c)
    if extra_refs:
        refs |= extra_refs
    needs_this = "self" in refs and self_receiver is not None
    refs.discard("self")
    parts: list[str] = []
    if needs_this:
        parts.append("this")
    parts.extend(f"&{escape_cpp_name(n)}"
                 for n in sorted((refs & set(declared)) - comp_vars))
    if not parts:
        return ""
    return ", ".join(parts) + ", "


def _lower_genexpr(expr: TpyGeneratorExpression, lc: '_LowerCtx',
                   declared: dict[str, TpyType]) -> THIRGenExpr:
    """Lower a generator expression to the make_generator render
    (`_gen_generator_expression`). Current slice: single loop var, no filter,
    scalar element/binding, over an LVALUE bare-name container OR a NON-LVALUE
    container literal. Everything else raises ThirUnsupported so the enclosing
    body falls back to AST."""
    analyzer = lc.analyzer
    gen = expr.generator
    loc = getattr(expr, "loc", None)
    if gen.unpack_vars is not None:
        raise ThirUnsupported("genexpr.unpack")
    if gen.conditions:
        raise ThirUnsupported("genexpr.filter")
    it = gen.iterable
    if isinstance(it, TpyCall) and it.func_name == "range":
        raise ThirUnsupported("genexpr.range")
    if isinstance(it, TpyName):
        if it.name not in declared or it.name in lc.narrow.narrowed:
            raise ThirUnsupported("genexpr.iterable_shape")
        it_type = declared[it.name]
        moved = False
    elif isinstance(it, TpyArrayLiteral):
        # A container literal is a prvalue: it moves into the lambda's storage.
        it_type = analyzer.get_expr_type(it)
        moved = True
    else:
        raise ThirUnsupported("genexpr.iterable_shape")
    it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it_type)))
    # dict deferred: its `*__beg` yields a key/value pair, so the scalar
    # loop-var binding would need the AST's key-extraction shape (a later rung).
    if not (is_list(it_type) or is_set(it_type)
            or is_array(it_type) or is_span(it_type)):
        raise ThirUnsupported("genexpr.iterable_shape")
    if not is_native_iterable(it_type, analyzer.registry):
        raise ThirUnsupported("genexpr.iterable_shape")
    et = get_iterable_element_type(it_type, registry=analyzer.registry)
    if et is None:
        raise ThirUnsupported("genexpr.elem_type")
    sema_elem = resolve_int_literals(unwrap_ref_type(et),
                                     analyzer.ctx.default_int_for_literal)
    if not (_eligible_scalar(sema_elem) or _eligible_char(sema_elem)):
        raise ThirUnsupported("genexpr.binding_shape")
    elem_type = _comp_result_type(expr.result_elem_type, analyzer)
    if yield_uses_borrow_slot(elem_type):
        raise ThirUnsupported("genexpr.borrow_slot")
    slot_cpp = lc.render_type(elem_type)
    binding_cpp = loop_var_binding(sema_elem, escape_cpp_name(gen.var),
                                   "*__beg++", gen.const_loop_var)
    body_declared = dict(declared)
    body_declared[gen.var] = sema_elem
    element = _lower_expr(expr.element_expr, lc, body_declared)
    comp_vars = {gen.var}
    inner_captures = _genexpr_captures(expr.element_expr, gen.conditions,
                                       None, declared, comp_vars,
                                       lc.self_receiver)
    _witness("genexpr.native_iterable")
    if moved:
        elements = tuple(_lower_container_elem(el, sema_elem, lc, declared)
                         for el in it.elements)
        return THIRGenExpr(
            result_type=analyzer.get_expr_type(expr),
            element=element,
            slot_cpp=slot_cpp,
            binding_cpp=binding_cpp,
            inner_captures=inner_captures,
            moved_source=True,
            cpp_iterable=lc.render_type(it_type),
            iterable_elements=elements,
            loc=loc,
        )
    iife_captures = _genexpr_captures(expr.element_expr, gen.conditions,
                                      {it.name}, declared, comp_vars,
                                      lc.self_receiver)
    return THIRGenExpr(
        result_type=analyzer.get_expr_type(expr),
        iterable=_lower_expr(it, lc, declared),
        element=element,
        slot_cpp=slot_cpp,
        binding_cpp=binding_cpp,
        iife_captures=iife_captures,
        inner_captures=inner_captures,
        loc=loc,
    )
