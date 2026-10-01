"""Comprehension classification and lowering."""

from __future__ import annotations
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from dataclasses import dataclass, replace
from ...parse.nodes import (
    TpyArrayLiteral,
    TpyDictLiteral,
    TpyTupleLiteral,
    TpyCall,
    TpyCoerce,
    TpyDictComprehension,
    TpyFieldAccess,
    TpyGeneratorExpression,
    TpyIntLiteral,
    TpyListComprehension,
    TpyListRepeat,
    TpyMethodCall,
    TpyName,
    TpySetComprehension,
    collect_name_refs,
)
from ...typesys import (
    IntLiteralType,
    NominalType,
    OptionalType,
    OwnType,
    TpyType,
    TupleType,
    peel_value_readonly,
    resolve_int_literals,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
    is_dyn_protocol,
)
from ...type_def_registry import (
    is_array,
    is_big_int_type,
    is_bytes_type,
    is_dict,
    is_dict_view,
    is_list,
    is_set,
    is_span_iter,
)
from ...modules.type_resolution import get_iterable_element_type, is_native_iterable
from ...codegen_cpp.context import (
    contains_named_expr, free_callee_cpp, is_lvalue_iterable)
from ...codegen_cpp.types import resolve_pending_container
from ..reject import ThirUnsupported
from ..faces import witness as _witness
from ..nodes import (
    THIRComprehension, THIRContainerLiteral, THIRExpr, THIRGenExpr, THIRMove,
    THIRName)
from .checks import (_combinator_pins_source, _container_storage_call_rvalue,
                     _user_iterator_iterable,
                     _native_iter_combinator)
from .predicates import (
    _mixed_own_storage_source,
    storage_tuple_name_source,
    _storage_opt_ternary_elem,
    _call_iterable_lvalue,
    _dict_view_iterable_ok,
    _genfac_like_call,
    _eligible_char,
    _eligible_enum,
    _eligible_scalar,
    _callable_value,
    _f1_record,
    record_like,
    _field_decl_type,
    _field_receiver_ok,
    _for_each_elem_binding_ok,
    _foreach_storage_opt_elem,
    _is_range_call,
    _nonvalue_container_ret,
    _optional_ptr_borrow,
    _owned_str_slot,
    _range_counter_type,
    _resolved_bytes_value,
    _resolved_str_value,
    _resolved_viewfam_value,
    _value_tuple,
)
from .context import (
    _ONLY_INDIRECT_READ,
    _ExprResultUse,
    _ExprUse,
    _LowerCtx,
    SinkPos,
    SlotPlacement,
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
    gen_factory: bool = False        # value-yielding generator-call source
    native_combinator: bool = False  # zip/map/filter/... rvalue source
    iter_protocol: bool = False      # driven by __iter__/__next__ (iter_range)


@dataclass(frozen=True)
class _SourceRoute:
    """The SOURCE verdict a comprehension and a generator expression share:
    what the iterable is, how it is driven, whether the driver borrows it
    (`iterable_lvalue`) or owns the rvalue, and the element type the loop
    var binds. The two forms differ only past this point -- the comp adds
    its result-slot / unpack-target gates and the reserve fact, the genexpr
    its yield-slot / binding gates -- so the classification lives once and a
    source the comp iterates, the genexpr iterates too."""
    loop: str                        # "range" | "begin_end"
    counter_type: 'TpyType | None'   # range counter type
    it_type: 'TpyType | None'        # begin_end iterable type
    et: 'TpyType | None'             # element type (int literals resolved)
    iterable_lvalue: bool
    owns_elements: bool = False      # source yields Own[T]
    gen_factory: bool = False        # value-yielding generator-call source
    native_combinator: bool = False  # zip/map/filter/... rvalue source
    # A source with no begin()/end() of its own -- a user iterable, an
    # Iterable/Iterator param -- is iterated through the iterator its
    # `__iter__()` returns (`::tpy::iter_range`), the same family the for
    # statement drives with `__iter__`/`__next__`.
    iter_protocol: bool = False


def _comp_synth_begin_end(it_type: TpyType, analyzer) -> bool:
    """A user record the record emitter gives synthesized begin()/end()
    (records.py: no explicit begin/end member, has `__span__`, any `__iter__`
    returns SpanIter): the comp loop calls `.begin()`/`.end()` on it
    unconditionally. Records outside the synthesis condition keep rejecting
    -- their comp C++ has no corpus witness."""
    ri = analyzer.registry.get_record_for_type(it_type)
    if ri is None or ri.is_native:
        return False
    if "begin" in ri.methods or "end" in ri.methods:
        return False
    if any(f.name in ("begin", "end") for f in ri.fields):
        return False
    if "__span__" not in ri.methods:
        return False
    iter_ovl = ri.methods.get("__iter__")
    if iter_ovl and not is_span_iter(iter_ovl[0].return_type):
        return False
    return True

def _iter_rvalue_source(it, analyzer) -> bool:
    """`iter(<rvalue>)`: `::tpy::__iter__` has no owning overload, so the
    `auto __obj_N = ::tpy::__iter__(mk());` capture iterates a destroyed
    temporary. The other combinators own their rvalue argument through a
    dedicated overload."""
    fi = getattr(it, "resolved_function_info", None)
    if fi is None or fi.native_name != "tpy::__iter__" or len(it.args) != 1:
        return False
    return not is_lvalue_iterable(it.args[0], analyzer.registry.get_record,
                                  analyzer.get_expr_type)

def _comp_sized_iterable(t: TpyType) -> bool:
    # The admitted iterable families whose size is known up front, so the
    # comp can reserve (Span / varargs never reach this comprehension route;
    # str/bytes are not sized, so no reserve fires for them).
    t = unwrap_readonly(t)
    return (is_array(t) or is_list(t) or is_dict(t) or is_set(t)
            or is_dict_view(t))

def _peel_value_readonly(t: 'TpyType | None') -> 'TpyType | None':
    """A `readonly[V]` VALUE element (`d.values()` / `d.items()` on a
    `readonly[dict]` receiver: the view spells `auto_readonly[V]`) binds a
    copy, so the readonly says nothing about the loop var -- the for-statement
    binds `int32_t v = *__beg;` there too. The same peel a parameter gets
    (`peel_value_readonly`), None-tolerant for the unpack targets."""
    return None if t is None else peel_value_readonly(t)


def _user_iterable_source(gen, declared: dict[str, TpyType], analyzer,
                          lc: '_LowerCtx | None') -> '_SourceRoute | None':
    """The `__iter__`/`__next__` family as a comprehension source
    (`_user_iterator_iterable`, the verdict the for statement's universal
    loop keys on): a user record iterable or iterator read as a name, `self`,
    a field or an OWNED call result, or an `Iterable[T]` / `Iterator[T]`-typed
    NAME. None when the source is outside the family, or belongs to a route
    of its own (a native iterable, a record with synthesized begin()/end())."""
    it = gen.iterable
    # An unpack head over this family is rejected by the for statement too
    # (BUGS.md#unpack-over-user-iterable); an owned element has no move sink
    # on this loop.
    if gen.unpack_vars is not None or gen.owns_elements:
        return None
    if isinstance(it, TpyName):
        if it.name == "self":
            t = analyzer.get_expr_type(it)
        elif it.name in declared:
            t = declared[it.name]
        else:
            return None
        lvalue = True
    elif isinstance(it, TpyFieldAccess):
        if not _field_receiver_ok(it, declared, analyzer):
            return None
        t = _field_decl_type(it, declared, analyzer)
        lvalue = True
    elif isinstance(it, (TpyCall, TpyMethodCall)):
        t = analyzer.get_expr_type(it)
        # A borrow the call returns is only as durable as what it lends from,
        # and that can be a temporary any number of calls down
        # (BUGS.md#borrow-call-comp-source).
        if is_lvalue_iterable(it, analyzer.registry.get_record,
                              analyzer.get_expr_type):
            return None
        lvalue = False
    else:
        return None
    if t is None:
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType):
        u = unwrap_readonly(u.wrapped)
    if (not isinstance(u, NominalType)
            or is_native_iterable(u, analyzer.registry)
            or not _user_iterator_iterable(u, analyzer)):
        return None
    if u.is_protocol:
        # A protocol-typed NAME (a param, an iterator-object local); a
        # protocol-returning call is a generator factory, its own route.
        if (not isinstance(it, TpyName) or is_dyn_protocol(u)
                or lc is None
                or not _statements.protocol_iterable_ok(lc, it)):
            return None
    else:
        if _comp_synth_begin_end(u, analyzer):
            return None
        rec = analyzer.registry.get_record_for_type(u)
        if rec is not None and any(
                fi.is_consuming for fi in
                analyzer.registry.get_method_overloads_with_parents(
                    rec, "__iter__")):
            return None
    et = get_iterable_element_type(u, registry=analyzer.registry)
    if et is None:
        return None
    et_b = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
    # Elements that need a storage-form loop-var registration, which only a
    # native-iterable source gets (the synthesized begin()/end() rule).
    if (isinstance(et_b, (OptionalType, OwnType))
            or (isinstance(et_b, TupleType)
                and et_b.has_pointer_repr_element())):
        return None
    et = _peel_value_readonly(resolve_int_literals(
        unwrap_ref_type(et), analyzer.ctx.default_int_for_literal))
    return _SourceRoute(loop="begin_end", counter_type=None, it_type=u,
                        et=et, iterable_lvalue=lvalue, iter_protocol=True)


def _source_route(gen, declared: dict[str, TpyType],
                  analyzer,
                  lc: '_LowerCtx | None' = None) -> '_SourceRoute | None':
    """Classify a comprehension / genexpr SOURCE (`gen` is the
    TpyComprehensionGenerator), or None. Slice: range1/range2 counter loops
    (eligible-scalar counter), 3-arg range as a begin/end loop over the Range
    object, bare-name / field container iterables the container classifiers
    admit, `d.values()`/`d.keys()` dict views (`d.items()` for the
    tuple-unpack form), native iterator combinators, container-returning and
    generator-factory calls, container literals, and Own[T]-yielding
    generator calls (`owns_elements`). A NARROWED name is in the slice: the
    branch retyped `declared`, and the reads the route feeds rename to the
    extraction alias like any other. Subscript iterables are outside it:
    None, and the caller rejects."""
    owns = gen.owns_elements
    it = gen.iterable
    genfac = False
    if _is_range_call(it):
        if owns:
            return None  # defensive: range yields scalars, never Own[T]
        if gen.unpack_vars is not None or len(it.args) not in (1, 2, 3):
            return None
        counter = _range_counter_type(it, analyzer)
        if not _eligible_scalar(counter):
            return None
        if len(it.args) == 3:
            # 3-arg range is a begin/end loop over the Range object (an
            # rvalue capture, never sized). Bounds render against the
            # counter slot like the 1/2-arg
            # arms, so they classify the same way.
            return _SourceRoute(loop="begin_end", counter_type=None,
                                it_type=analyzer.get_expr_type(it),
                                et=counter, iterable_lvalue=False)
        return _SourceRoute(loop="range", counter_type=counter,
                            it_type=None, et=counter, iterable_lvalue=True)
    user = _user_iterable_source(gen, declared, analyzer, lc)
    if user is not None:
        return user
    combinator = False
    if not owns and _native_iter_combinator(it, analyzer):
        # The combinator rvalue is captured owning (`auto __obj_N =
        # ::tpy::builtin_zip(xs, ys);`) and iterated begin/end; its own
        # lowering re-validates callee and args. Never sized -- an Iterator
        # has no len().
        if _iter_rvalue_source(it, analyzer):
            return None
        it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(it))))
        if it_type is None:
            return None
        combinator = True
        lvalue = False
    elif isinstance(it, TpyMethodCall):
        methods = (("items",) if gen.unpack_vars is not None
                   else ("values", "keys"))
        # field_recv_ok: the FIELD-receiver flavor (`dict_items(h.m)`)
        # is admitted HERE and at the method-call arm's iterable_override
        # (both needed; the render is receiver-blind past admission). The
        # for-head call sites keep the False default -- their
        # storage-tuple loop-var registration is name-keyed, and routing
        # without it was caught by the binding-fact join
        # (tplib/json_model_nested).
        if _dict_view_iterable_ok(it, declared, analyzer,
                                  methods=methods, field_recv_ok=True):
            it_type = analyzer.get_expr_type(it)
            lvalue = False  # a view call result is an rvalue (owning capture)
        elif gen.unpack_vars is None and not owns:
            # A container-returning METHOD call iterable (`[n for n in
            # os.listdir(tmp) if ..]` -- module-qualified calls parse as
            # method calls): the comp twin of the for-each catch-all
            # method-call arm. The call renders inside the `__obj_N`
            # capture and its own lowering re-validates callee/args; a
            # borrow return is a C++ lvalue (`auto&` capture), an Own
            # return an owning rvalue.
            ret = analyzer.get_expr_type(it)
            if not _nonvalue_container_ret(ret):
                # The module-qualified generator-factory twin of the
                # TpyCall genfac leg (`[x for x in itertools.islice(
                # itertools.count(), 4)]`): the owning `auto __obj_N`
                # capture of the frame rvalue, begin/end iteration --
                # the comp loop is unconditionally begin/end,
                # callee-kind-blind, so the overload-seam-aware verdict
                # serves (a stub fi carries is_generator=False).
                if not _genfac_like_call(it, analyzer):
                    return None
                it_type = unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(ret)))
                if (it_type is None
                        or _resolved_str_value(it_type,
                                               analyzer) is not None):
                    return None
                genfac = True
                lvalue = is_lvalue_iterable(
                    it, analyzer.registry.get_record,
                    analyzer.get_expr_type)
            else:
                mfi = it.resolved_function_info
                it_type = unwrap_readonly(unwrap_send_sync(ret))
                lvalue = not (mfi is not None
                              and isinstance(mfi.return_type, OwnType))
        else:
            return None
    elif isinstance(it, (TpyName, TpyFieldAccess)):
        if isinstance(it, TpyName):
            if it.name not in declared:
                return None
            base = declared[it.name]
        else:
            # C3 field iterable: `recv.items` off an F1-record / proven
            # Optional-ptr receiver. The route types on the field's DECLARED
            # type (codegen's get_resolved_type source), so a narrowed
            # Optional/union field (which would need a `(*...)` unwrap) is not
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
        if isinstance(it, TpyName):
            nv = _statements._narrowed_value_opt_view_iter(it, declared,
                                                           analyzer)
            if nv is not None:
                # A proven-narrowed value-repr `str|None` / `bytes|None`
                # NAME source: the same begin/end stmt-expr over the deref
                # capture (`auto& __obj_N = (*b);`) as the for-each leg.
                _witness("comp.narrowed_value_opt_view")
                it_type = nv
        if gen.unpack_vars is not None and not _statements._container_scalar_tuple_iter(
                it_type, analyzer, allow_record=True,
                allow_storage_opt=True):
            return None
        # A name is an lvalue; a field chain off one inherits it
        # (is_lvalue_iterable recurses to the Name arm).
        lvalue = True
    elif isinstance(it, TpyArrayLiteral) and not owns and it.elements:
        # A LIST-LITERAL iterable (`[len(x) for x in ["a", "bb"]]`): the
        # capture is the BRACED init-list itself
        # (`auto __obj_N = {"a", "bb"};` -- no container spelling, so the
        # element type comes from sema) and iterates begin/end; the
        # init-list is sized, so the reserve fires like a container's. An
        # unpack head destructures the (const) init-list element like any
        # other tuple element.
        it_type = analyzer.get_expr_type(it)
        it_type = resolve_pending_container(it_type, analyzer) or it_type
        if it_type is None:
            return None
        lvalue = False
    elif isinstance(it, TpyCall) and not owns:
        # A plain container-returning CALL iterable (`[n for n in
        # os.listdir(tmp) if ..]`): the for-each TpyCall arm's comp twin.
        # The call renders inside the `__obj_N` capture (owning for an
        # rvalue return, `auto&` for a borrow) and its own lowering
        # re-validates callee/args -- a shape outside the slice falls the
        # body back. Str/bytes/view returns stay on their own arms
        # (`_nonvalue_container_ret` excludes them).
        ret = analyzer.get_expr_type(it)
        if not _nonvalue_container_ret(ret):
            # A VALUE-yielding generator-factory source (`[v for v in
            # wrap(3)]`): the owned-move arm's begin/end iteration with a
            # plain (non-moving) element read -- the owning `auto __obj_N`
            # capture of the frame rvalue. The overload-seam-aware verdict
            # keeps the free spelling in lockstep with the qualified twin.
            # An UNPACK head over this source rides the shared element
            # rungs below (`auto& __tup_N = *__beg;` + per-element binds).
            if not _genfac_like_call(it, analyzer):
                return None
            it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
            if (it_type is None
                    or _resolved_str_value(it_type, analyzer) is not None):
                return None
            genfac = True
            lvalue = is_lvalue_iterable(it, analyzer.registry.get_record,
                                        analyzer.get_expr_type)
        else:
            # A CONTAINER-returning call with an unpack head is unwitnessed
            # (the storage-form registration differs) -- keep rejecting.
            if gen.unpack_vars is not None:
                return None
            it_type = unwrap_readonly(unwrap_send_sync(ret))
            lvalue = _call_iterable_lvalue(it, analyzer)
    elif isinstance(it, TpyCall) and owns:
        # An Own[T]-yielding generator source (`widgets(3)`), the owned-move
        # comprehension. The generator is iterated via begin/end (the comp
        # loop emits them unconditionally -- so `is_native_iterable`
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
    # requires a NativeIterable source -- or a user record with SYNTHESIZED
    # begin/end (a Spannable conformer), which the unconditional begin/end
    # comp loop serves the same way. The synth family carries no
    # storage-form registration (`register_loop_var_storage_form`
    # early-returns on non-native-iterable sources), so elements that would
    # need one -- and unpack heads -- keep rejecting.
    synth_src = False
    if not owns and not genfac and not combinator \
            and not is_native_iterable(it_type, analyzer.registry):
        if (gen.unpack_vars is not None
                or not _comp_synth_begin_end(it_type, analyzer)):
            return None
        synth_src = True
    et = get_iterable_element_type(it_type, registry=analyzer.registry)
    if et is None:
        return None
    if synth_src:
        et_b = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
        if (isinstance(et_b, OptionalType)
                or (isinstance(et_b, TupleType)
                    and et_b.has_pointer_repr_element())):
            return None
    et = _peel_value_readonly(resolve_int_literals(
        unwrap_ref_type(et), analyzer.ctx.default_int_for_literal))
    return _SourceRoute(loop="begin_end", counter_type=None, it_type=it_type,
                        et=et, iterable_lvalue=lvalue, owns_elements=owns,
                        gen_factory=genfac, native_combinator=combinator)


def _comp_route(init, declared: dict[str, TpyType],
                analyzer,
                lc: '_LowerCtx | None' = None) -> '_CompRoute | None':
    """Classify a comprehension init: the shared `_source_route` verdict plus
    the comp's own result-slot / unpack-target gates and the reserve fact, or
    None (the caller rejects)."""
    kind = _COMP_KINDS.get(type(init))
    if kind is None:
        return None
    gen = init.generator
    src = _source_route(gen, declared, analyzer, lc)
    if src is None:
        return None
    if _is_range_call(gen.iterable):
        return _CompRoute(kind=kind, loop=src.loop,
                          counter_type=src.counter_type, it_type=src.it_type,
                          et=src.et, iterable_lvalue=src.iterable_lvalue,
                          sized_reserve=False, unpack_types=None)
    it_type = src.it_type
    et = src.et
    lvalue = src.iterable_lvalue
    owns = src.owns_elements
    genfac = src.gen_factory
    combinator = src.native_combinator
    sized = kind == "list" and _comp_sized_iterable(it_type)
    if gen.unpack_vars is not None:
        elem = unwrap_readonly(et)
        if not isinstance(elem, TupleType):
            return None
        if len(elem.element_types) != len(gen.unpack_vars):
            return None  # defensive: sema errors on arity mismatch
        types: list = []
        for name, ett in zip(gen.unpack_vars, elem.element_types):
            tt = _peel_value_readonly(unwrap_ref_type(ett))
            if name is None:
                types.append(None)
                continue
            # A str element target COPIES the stored element (`std::string k =
            # std::get<i>(tup);` -- the value-type branch over the
            # tuple's OWNED element spelling), unlike the for-each unpack's
            # view binding; the owned declared type keeps the target's reads
            # STORAGE-form (bare inserts). An F1-record target BORROWS with
            # the ref binding keyed on const_loop_var alone (`auto& r =` /
            # `const auto& p =` -- the inline tuple unpack spells a ref
            # binding for every non-value target).
            if not (_eligible_scalar(tt) or _owned_str_slot(tt, analyzer)
                    or record_like(
                        unwrap_readonly(unwrap_send_sync(tt)),
                        analyzer)
                    # A ptr-repr Optional[F1-record] element binds the
                    # STORAGE optional by reference (`auto& p =
                    # std::get<0>(t);` -- the storage_opt_locals family);
                    # its `T*`-slot consumers lift via optional_to_ptr.
                    or _optional_ptr_borrow(tt, analyzer) is not None):
                return None
            types.append(tt)
        return _CompRoute(kind=kind, loop="begin_end", counter_type=None,
                          it_type=it_type, et=et, iterable_lvalue=lvalue,
                          sized_reserve=sized, unpack_types=tuple(types),
                          owns_elements=owns,
                          native_combinator=combinator)
    str_et = _resolved_str_value(et, analyzer)
    if str_et is not None:
        et = str_et
    # Compositional loop-var classifier (the for-each twin): the begin/end comp loop
    # binds the loop var through the SAME shared loop_var_binding, so any
    # resolved element renders identically; the element/key/value/filter reads
    # of the var route recursively through expression lowering. Only an unresolved
    # pending element (spelled before `resolve_type` concretizes it)
    # rejects. A ptr-repr Optional[F1-record] element rides the
    # storage-opt registration in _lower_comprehension (the for-STATEMENT
    # container leg's comp twin).
    if not (_for_each_elem_binding_ok(et)
            or _foreach_storage_opt_elem(et, analyzer)):
        return None
    return _CompRoute(kind=kind, loop="begin_end", counter_type=None,
                      it_type=it_type, et=et, iterable_lvalue=lvalue,
                      sized_reserve=sized, unpack_types=None,
                      owns_elements=owns, gen_factory=genfac,
                      iter_protocol=src.iter_protocol,
                      native_combinator=combinator)

def _comp_slot_ok(slot: 'TpyType | None', analyzer) -> bool:
    # The NARROW slot predicate, kept for dict KEYS (the hashable-key axis:
    # widening keys to enum/bytes/record is the container-literal cell's
    # separate key-family concern -- a record key isn't hashable, an enum/bytes
    # key rides a later row). char slots ride the same targeted element render
    # as scalars (comp elements ARE target-typed, unlike list-literal
    # elements).
    return (_eligible_scalar(slot) or _eligible_char(slot)
            or _owned_str_slot(slot, analyzer))

def _comp_elem_slot_ok(slot: 'TpyType | None', analyzer, *,
                       allow_container: bool = False) -> bool:
    """The list/set element + dict VALUE result slot whose
    `_lower_container_elem` render is element-SHAPE-independent, so ANY
    element expr lowered into it renders the same -- the compositional twin
    of the for-each loop-var classifier on the append/insert side. The wrap
    keys on the element's FORM, not its node kind:

    - value scalar / char -- bare / target-typed literal retype;
    - owned str / bytes slot -- a BORROW source copies (`std::string(x)` /
      `::tpy::Bytes`), a STORAGE/literal source lands bare;
    - enum / callable -- a value type, bare;
    - F1-record -- a name derefs/moves off the same movable_locals facts,
      an rvalue lands bare (no owned-slot wrap for records).

    The SHAPE-sensitive families reject (`_lower_container_elem` branches on
    the element NODE for them, so a shape-blind element render would be
    wrong): Optional (`None` -> `std::nullopt` vs a scalar value), value-tuple
    (a tuple LITERAL only), nested container (an Array LITERAL only), union, and
    a str/bytes VIEW slot (owned-only here).

    `allow_container` (the dict VALUE slot only) admits a list/Array container
    slot -- SHAPE-sensitive, so `_lower_comp_container_elem` gates the element
    NODE against its own vetted set (see there), mirroring
    `_container_lit_elem_ok`'s `fam == "container"` arm."""
    if _comp_slot_ok(slot, analyzer):
        return True
    bt = _resolved_bytes_value(slot, analyzer)
    if bt is not None and is_bytes_type(bt):
        return True
    if allow_container and _container_family_slot(slot):
        return True
    return (_eligible_enum(slot, analyzer) is not None
            or _callable_value(slot)
            or record_like(slot, analyzer))

def _comp_container_name_elem(e, vt: 'TpyType | None', lc: '_LowerCtx',
                              body_declared: dict[str, TpyType]) -> bool:
    """A plain declared NAME whose type IS the container slot, so the slot
    init copies it with no shape-dependent wrap. A pointer or narrowed name is
    out -- both need an unwrap the bare read does not spell.

    The copy is a CPython divergence (CPython aliases one container into every
    slot), so the name is admitted only where sema's "copies ... into owned
    storage" warning reaches the user. That warning is suppressed at a move
    source -- a last-use read of an owned local, which sema believes moves --
    but the element renders once per iteration, so this arm reads it bare and
    copies N times instead. Unwarned, that is silent; reject it."""
    if not isinstance(e, TpyName):
        return False
    if (e.name not in body_declared or e.name in lc.pointers
            or e.name in lc.narrow.narrowed):
        return False
    # Sema's move verdict for this read: its last use (as sema left it) of
    # a name the frame owns -- the element body itself moves nothing.
    if (lc.analyzer.ctx.is_last_use(e)
            and (e.name in lc.sema_movable_locals or e.name in lc.own_params)):
        return False
    peel = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        body_declared[e.name])))
    return peel == unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vt)))

def _container_family_slot(slot: 'TpyType | None') -> bool:
    if slot is None:
        return False
    su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    return is_list(su) or is_array(su) or is_dict(su)

def _unpack_target_cpps(unpack_types, lc: '_LowerCtx',
                        const_loop_var: bool = True) -> tuple:
    """The per-target decl spellings of a comp unpack head: None for `_`
    discards, the ref binding for F1-record targets (`const auto&` /
    `auto&`, keyed on const_loop_var alone, like the inline tuple unpack's
    ref binding), the rendered type otherwise.
    Shared by the begin_end and array_source arms."""
    ref = "const auto&" if const_loop_var else "auto&"
    return tuple(
        None if tt is None
        else (ref if (record_like(
                          unwrap_readonly(unwrap_send_sync(tt)), lc.analyzer)
                      # A storage-optional element binds the same reference
                      # (`auto& p = std::get<0>(t);`).
                      or _optional_ptr_borrow(tt, lc.analyzer) is not None)
              else lc.render_type(tt))
        for tt in unpack_types)


def _comp_lowering_route(
        init, t: 'TpyType | None', declared: dict[str, TpyType],
        pointers: AbstractSet[str], rebind_slots: AbstractSet[str],
        storage_tuple_locals: AbstractSet[str],
        narrowed: AbstractSet[str], analyzer,
        lc: '_LowerCtx | None' = None) -> '_CompRoute | None':
    """Resolve the route data consumed while lowering a comprehension."""
    if t is None:
        return None
    if is_array(t):
        return _comp_array_route(
            init, t, declared, pointers, rebind_slots,
            storage_tuple_locals, narrowed, analyzer)
    route = _comp_route(init, declared, analyzer, lc)
    if route is None:
        return None
    args = getattr(t, "type_args", None)
    if not args:
        return None
    # A `readonly[V]` VALUE slot (the readonly-receiver dict view's element,
    # copied into the result) gates as the plain value it renders as.
    args = tuple(_peel_value_readonly(a) for a in args)
    if route.kind == "list" and not (is_list(t)
                                     and (_comp_elem_slot_ok(
                                              args[0], analyzer,
                                              allow_container=True)
                                          # A NON-VALUE tuple element slot,
                                          # node-gated like the other
                                          # shape-sensitive families: a tuple
                                          # LITERAL rides the storage-direct/
                                          # borrow-ladder rows, a NAME (bare
                                          # or under copy()) the whole
                                          # tuple_to_storage copy.
                                          or ((isinstance(init.element_expr,
                                                          TpyTupleLiteral)
                                               or storage_tuple_name_source(
                                                   init.element_expr,
                                                   analyzer) is not None)
                                              and isinstance(
                                                  unwrap_readonly(
                                                      unwrap_ref_type(
                                                          unwrap_send_sync(
                                                              args[0]))),
                                                  TupleType))
                                          # ... an Optional-result TERNARY
                                          # element (the asdict/astuple
                                          # recursion into a
                                          # `list[Optional[DC]]` field),
                                          # node-gated like the other
                                          # shape-sensitive families.
                                          or _storage_opt_ternary_elem(
                                              init.element_expr, analyzer)
                                          # ... and the MIXED-own-tuple CALL
                                          # source (the non-move
                                          # tuple_to_storage elem row).
                                          or (isinstance(
                                                  _mcl_b := unwrap_readonly(
                                                      unwrap_ref_type(
                                                          unwrap_send_sync(
                                                              args[0]))),
                                                  TupleType)
                                              and _mixed_own_storage_source(
                                                  init.element_expr, _mcl_b,
                                                  frozenset(), analyzer)
                                              is not None))):
        return None
    if route.kind == "set" and not (is_set(t)
                                    and _comp_elem_slot_ok(args[0], analyzer,
                                                           allow_container=True)):
        return None
    if route.kind == "dict":
        # An owned-move source admits a hashable record-like KEY (sema validated
        # hashability by giving the dict a record key type); every other dict
        # comp keeps the narrow key slice.
        key_ok = (_comp_slot_ok(args[0], analyzer)
                  or (route.owns_elements and record_like(args[0], analyzer)))
        # The list leg's node-gated tuple row, on the dict's VALUE slot: a
        # value `TupleType` slot fails the elem-slot ladder, but a tuple
        # LITERAL value_expr rides the storage-direct row the same way (a
        # bare NAME value_expr keeps rejecting -- it would need the whole
        # tuple_to_storage copy the list leg's NAME half carries).
        value_ok = (_comp_elem_slot_ok(args[1], analyzer, allow_container=True)
                    or (isinstance(init.value_expr, TpyTupleLiteral)
                        and isinstance(
                            unwrap_readonly(unwrap_ref_type(
                                unwrap_send_sync(args[1]))), TupleType))
                    # The list leg's Optional-TERNARY row on the VALUE slot
                    # (the asdict recursion into a `dict[str, Optional[DC]]`
                    # field).
                    or _storage_opt_ternary_elem(init.value_expr, analyzer)
                    # The list leg's mixed-own CALL row on the VALUE slot.
                    or (isinstance(
                            _mcd_b := unwrap_readonly(unwrap_ref_type(
                                unwrap_send_sync(args[1]))), TupleType)
                        and _mixed_own_storage_source(
                            init.value_expr, _mcd_b, frozenset(), analyzer)
                        is not None))
        if not (is_dict(t) and len(args) == 2 and key_ok and value_ok):
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
                        args_t[0]))), analyzer) is not None)
            # A MIXED-own-tuple CALL element (`[make_mixed(b) for _ in
            # range(1)]` demoted to an Array): the non-move
            # tuple_to_storage elem row, node-gated like the literal one.
            or (isinstance(
                    _mca_b := unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(args_t[0]))), TupleType)
                and _mixed_own_storage_source(
                    init.element_expr, _mca_b, frozenset(), analyzer)
                is not None)):
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
    # Array-SOURCE indexing arm: the non-range Array comprehension borrows
    # the source once (`__obj_N`) and indexes it per slot. Reuse the
    # begin/end name/field classifier for the loop-var binding fact, then
    # require a statically-sized Array source (the only shape sema demotes to
    # Array). A view result off the source rebinds it_type in _comp_route.
    base = _comp_route(init, declared, analyzer)
    if base is None or base.loop != "begin_end":
        return None
    # A storage-opt element under the INDEXED read (`__obj_N[__i_N]`) has no
    # witness -- only the begin/end loop registers the storage-opt loop var.
    if _foreach_storage_opt_elem(base.et, analyzer):
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
        declared: dict[str, TpyType],
        ptr_shadow: frozenset = frozenset()) -> THIRComprehension:
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
    # The element renders inside the per-index lambda, where the comp var
    # is a fresh local -- pointer-shadowed globals scrub for that walk
    # only; the range bounds evaluate in the enclosing scope. allow_temps:
    # the emit flushes element temps into the lambda body before the
    # `return` (`auto __tmp_N = i;` ahead of `Box(std::move(__tmp_N))`),
    # so the element is a flush position.
    with _scrubbed_pointers(lc, ptr_shadow):
        element = _lower_comp_container_elem(
            init.element_expr, elem_t, lc, body_declared, allow_temps=True)
    return THIRComprehension(
        result_type=t,
        kind="list",
        var=gen.var,
        loop="array_range",
        elem_type=counter,
        counter_cpp=lc.render_type(counter),
        array_elem_cpp=lc.render_type(elem_t),
        array_size_cpp=str(t.type_args[1]),
        range_start=_lower_expr(args[0], lc, declared) if len(args) >= 2 else None,
        range_step=_lower_expr(args[2], lc, declared) if len(args) == 3 else None,
        element=element,
        loc=getattr(init, "loc", None),
    )

def _lower_array_source_comprehension(
        init, result_type: TpyType, route: _CompRoute, lc: '_LowerCtx',
        declared: dict[str, TpyType],
        ptr_shadow: frozenset = frozenset()) -> THIRComprehension:
    """The array_from_index SOURCE arm: borrow a sized Array source once
    (`__obj_N`, lvalue-verdict binding) inside a `({...})` prelude, then index
    it per slot (`E var = __obj_N[__i_N];` value binding / `auto&& var = ...`
    borrow) -- the non-range Array comprehension. The element renders
    through the shared per-slot wrap after the loop-var binding."""
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
            it, lc, declared, use=_ExprUse(result=_ExprResultUse.ITERABLE,
                         pos=SinkPos.ITER_SOURCE))
    with _scrubbed_pointers(lc, ptr_shadow):
        element = _lower_comp_container_elem(
            init.element_expr, elem_t, lc, body_declared)
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
        element=element,
        loc=getattr(init, "loc", None),
    )

def _comp_result_type(t: 'TpyType | None', analyzer) -> TpyType:
    # Sema stamps the result element/key/value types on the node; a
    # still-literal int resolves to the default int.
    assert t is not None
    if isinstance(t, IntLiteralType):
        return analyzer.ctx.default_int_type
    return t

def _lower_comp_container_elem(e, vt: TpyType, lc: '_LowerCtx',
                               body_declared: dict[str, TpyType],
                               *, typed_brace: bool = False,
                               allow_temps: bool = False) -> 'THIRExpr':
    """Lower a comprehension element/value against a possibly-container slot
    (dict VALUE, list/set element, Array slot). A list/Array container slot
    is element-SHAPE-sensitive, so only vetted sources route: a container
    LITERAL -- rendered self-describing (`std::array<int32_t, 2>{...}`, via the
    `typed_brace_init` prefix: the insert/brace target is a template, a bare
    brace-init cannot deduce) -- a nested COMPREHENSION or list-REPEAT (already
    self-describing, so typed_brace_init no-ops on them), a plain NAME whose
    copy sema warned about, and a CALL whose prvalue IS the slot's storage
    container. Every other slot family keeps the shape-independent element
    render (`_lower_container_elem`).

    `typed_brace` (the dict VALUE only) spells the container literal
    self-describing (`std::array<int32_t, 2>{...}`) because `insert_or_assign`
    is a template that cannot deduce a bare brace. A list/set element
    (`push_back`) and an array-lambda return already have a declared target
    type, so their brace stays bare."""
    if not _container_family_slot(vt):
        return _lower_container_elem(e, vt, lc, body_declared,
                                     allow_temps=allow_temps)
    if type(e) in _COMP_KINDS:
        value = _lower_container_elem(e, vt, lc, body_declared)
    elif isinstance(e, TpyArrayLiteral):
        value = _lower_checked_container_elem(
            e, vt, lc, body_declared, threaded=True, forced=True,
            allow_nested=True)
        if typed_brace and isinstance(value, THIRContainerLiteral):
            value = replace(value, typed_brace_cpp=lc.render_type(
                unwrap_readonly(unwrap_ref_type(vt))))
    elif isinstance(e, TpyDictLiteral):
        # A dict-literal element at a dict element slot (the asdict list
        # recursion's per-member `{"x": m.x, ...}`): self-describing
        # ordered_map render, pushed bare -- typed_brace no-ops on it.
        value = _lower_checked_container_elem(
            e, vt, lc, body_declared, threaded=True, forced=True,
            allow_nested=True)
    elif isinstance(e, TpyListRepeat):
        # A list-REPEAT element (`[[0.0] * n for _ in range(k)]`): the slot is
        # its materialization target, so the repeat renders self-describing
        # (`from_range<std::vector<double>>(..)`) and pushes bare like a nested
        # comprehension does. Without the target it would stay the LAZY
        # `repeat_range` the annotated slot cannot take.
        value = _lower_expr(e, lc, body_declared,
                            use=_ExprUse(slot_target=vt))
    elif _comp_container_name_elem(e, vt, lc, body_declared):
        # A bare NAME at a container slot (`[xs for i in range(3)]` at
        # `list[list[int32]]`): the slot init copies the container by value,
        # so the read lands bare -- sema already warned about the copy.
        value = _lower_expr(e, lc, body_declared)
    elif _container_storage_call_rvalue(e, vt, lc.analyzer):
        # A CALL that already yields the slot's storage container
        # (`[make_row(i) for i in range(n)]`): the prvalue lands bare, the
        # same verdict the `Own[container]` argument slot reads for the
        # `out.append(make_row(i))` spelling of the identical store.
        value = _lower_container_elem(e, vt, lc, body_declared)
    else:
        raise ThirUnsupported("comp.container_value", detail=True)
    _witness("comp.container_value")
    return value

def _comp_pointer_shadow(gen, lc) -> frozenset:
    """The comp vars that shadow a POINTER-SLOT GLOBAL at module scope --
    the subset scrubbed from `lc.pointers` for the element/filter walk.
    The comp binds a fresh C++-scoped local (the stmt-expr / lambda), so
    reads of the var inside the walk are bare; the ITERABLE and range
    bounds evaluate in the OUTER scope and keep the deref. SPELLED
    globals (imported / native, `global_cpp` / `global_write_cpp`) are
    not shadowable -- their read is a fixed qualified name, so the body
    would read the shadowed global -- and stay out of this set (the
    route's special check keeps rejecting them only if they are also
    pointers; a non-pointer spelled global never reaches the special
    check). Empty outside module scope: a comp var
    shadowing a pointer LOCAL keeps the special-check reject.

    A shadow name the ITERABLE itself references (`[x + 1 for x in x]`)
    stays OUT of the set, so the route's special check keeps rejecting it:
    the iterable read would land BARE against the pointer-slot global
    there (`auto& __obj_N = x;` on a `std::vector<T>*` -- ill-formed
    C++, see the BUGS.md comprehension self-shadow entry)."""
    if not lc.top_level_scope:
        return frozenset()
    names = (set(gen.unpack_vars) if gen.unpack_vars is not None
             else {gen.var})
    it_refs = collect_name_refs(gen.iterable)
    return frozenset(
        n for n in names
        if n is not None and n in lc.pointers
        and n not in it_refs
        and n not in lc.prescan.global_cpp
        and n not in lc.prescan.global_write_cpp)


@contextmanager
def _scrubbed_pointers(lc: '_LowerCtx', names: frozenset):
    """Scoped removal of comp-shadowed names from the pointer
    classification (the comp twin of the for-each `shadow_names` scrub):
    reads inside the walk render bare, the restore re-adds at exit."""
    removed = frozenset(n for n in names if n in lc.pointers)
    lc.pointers -= removed
    try:
        yield
    finally:
        lc.pointers |= removed


def _lower_owned_comp_sink(e, slot: 'TpyType | None', lc: '_LowerCtx',
                           body_declared: dict[str, TpyType], gen,
                           *, is_last_sink: bool) -> 'THIRExpr':
    """Lower one sink (list/set element, dict value) of an owned-move
    comprehension. The move set is restricted to THIS comp's loop var
    (the loop var is rebound each iteration, so moving it is sound; an outer
    movable would multi-move). A bare-loop-var LAST sink moves UNCONDITIONALLY
    (structurally the last use, sequenced after any earlier read); an
    earlier/derived sink defers to the ordinary last-use move gate."""
    with lc.moves_only({gen.var}):
        lowered = _lower_container_elem(e, slot, lc, body_declared)
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
    global_scope = lc.global_binding_scope
    lc.global_binding_scope = False
    try:
        return _lower_comprehension_impl(init, result_type, lc, declared, pointers)
    finally:
        lc.global_binding_scope = global_scope


def _lower_comprehension_impl(
        init, result_type: 'TpyType | None', lc: '_LowerCtx',
        declared: dict[str, TpyType],
        pointers: AbstractSet[str]) -> THIRComprehension:
    """Build the THIRComprehension node from its classified route. The
    container spelling composes from the node's sema-stamped result types;
    elements/keys/values lower through the S5 per-slot owned-str wrap
    (`_lower_container_elem`), target-typed against the slot."""
    analyzer = lc.analyzer
    loc = getattr(init, "loc", None)
    ptr_shadow = _comp_pointer_shadow(init.generator, lc)
    if ptr_shadow:
        # The shadowed pointer-slot globals leave the route's special
        # check (the walk scrubs them scoped); every other special class
        # keeps its reject.
        _witness("comp.global_shadow")
        pointers = frozenset(pointers) - ptr_shadow
    route = _comp_lowering_route(
        init, result_type, declared, pointers, lc.rebind_slot_locals,
        lc.storage_tuple_locals, lc.narrow.narrowed.keys(), analyzer, lc)
    if route is None or result_type is None:
        raise ThirUnsupported("comp.route", detail=True)
    if route.loop == "array_range":
        return _lower_array_comprehension(
            init, result_type, route, lc, declared, ptr_shadow=ptr_shadow)
    if route.loop == "array_source":
        return _lower_array_source_comprehension(
            init, result_type, route, lc, declared, ptr_shadow=ptr_shadow)
    gen = init.generator
    body_declared = dict(declared)
    if route.unpack_types is not None:
        for name, tt in zip(gen.unpack_vars, route.unpack_types):
            if name is not None:
                body_declared[name] = tt
    else:
        body_declared[gen.var] = route.et
    # A comprehension loop var over a container of pointer-repr tuples reads
    # STORAGE form, exactly as the for-statement loop var does -- registered
    # at comp-scope entry (`register_loop_var_storage_form`) so the element
    # consumer sees it. Without the registration the element wrap re-lifts an
    # already-storage read.
    #
    # Membership makes EVERY read of the name in the body STORAGE form (the Form
    # verdict in `expressions.py`), not just the element wrap -- filters and
    # subscripts included.
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
    # A storage-optional unpack TARGET registers for the body walk (the
    # `storage_form_optional_locals` counterpart -- its reads render the bare
    # storage optional, `T*` slots lift via optional_to_ptr), and pops
    # with it exactly like the loop-var storage-form registration above.
    comp_opt_vars: list[str] = []
    comp_const_opt_vars: list[str] = []
    if route.unpack_types is not None:
        for uname, utt in zip(gen.unpack_vars, route.unpack_types):
            if (uname is not None
                    and _optional_ptr_borrow(utt, analyzer) is not None
                    and uname not in lc.storage_opt_locals):
                comp_opt_vars.append(uname)
                lc.storage_opt_locals.add(uname)
    elif (_foreach_storage_opt_elem(route.et, analyzer)
          and gen.var not in lc.storage_opt_locals):
        # The storage-opt LOOP VAR (`[v.x if v is not None else -1 for v in
        # items]` over `list[P | None]`): binds the STORAGE-form
        # `std::optional<P>` and registers like the for-STATEMENT container
        # leg, including the const twin (a const-bound source's consumers
        # spell `const P*`). A dict-VIEW iterable tracks its RECEIVER's
        # const-ness. The hop is taken HERE, for ANY method call, where the
        # predicate's own arm takes it only for a borrowing-view return --
        # BUGS.md#comp-storage-opt-const-any-method-recv.
        _cs_src = (gen.iterable.obj
                   if isinstance(gen.iterable, TpyMethodCall)
                   else gen.iterable)
        _witness("comp.storage_opt_elem")
        comp_opt_vars.append(gen.var)
        lc.storage_opt_locals.add(gen.var)
        if _statements._iteration_yields_const(_cs_src, lc, analyzer):
            _witness("comp.storage_opt_const_elem")
            comp_const_opt_vars.append(gen.var)
            lc.const_storage_opt_locals.add(gen.var)
    try:
        return _build_comprehension_body(
            init, result_type, route, lc, declared, body_declared, gen,
            analyzer, loc, pointers, ptr_shadow)
    finally:
        if comp_storage_var is not None:
            lc.storage_tuple_locals.discard(comp_storage_var)
        for uname in comp_opt_vars:
            lc.storage_opt_locals.discard(uname)
        for uname in comp_const_opt_vars:
            lc.const_storage_opt_locals.discard(uname)


def _build_comprehension_body(init, result_type, route, lc, declared,
                              body_declared, gen, analyzer, loc, pointers,
                              ptr_shadow: frozenset = frozenset()):
    """The comprehension body build, split out so the loop-var storage-form
    registration above can scope itself symmetrically around the comp
    scope's enter/exit."""
    _witness(f"comp.{route.kind}")
    _witness(f"comp.{route.loop}")
    if gen.conditions:
        _witness("comp.filter")
    if route.sized_reserve:
        _witness("comp.reserve")
    conditions_lowered = None
    # The element/filter walk runs under the pointer-shadow scrub (the
    # comp vars bind fresh C++ locals, so their reads are bare); the
    # iterable / range-bound lowering below stays OUTSIDE it -- those
    # evaluate in the enclosing scope where the global's deref applies.
    with _scrubbed_pointers(lc, ptr_shadow):
        if any(contains_named_expr(c) for c in gen.conditions):
            # PEP 572: a comp-filter walrus binds in the ENCLOSING scope --
            # the predecl flushes before the statement-expr and the target
            # stays readable after the comprehension. Conditions lower FIRST
            # here so the element's read of the leaked name resolves (Python
            # evaluates the filter before the element each iteration).
            pre = set(body_declared)
            with lc.moves_only(()):
                conditions_lowered = tuple(
                    _lower_truthy(c, lc, body_declared, temps_ok=True)
                    for c in gen.conditions)
            for leaked in set(body_declared) - pre:
                declared[leaked] = body_declared[leaked]
            _witness("comp.filter_walrus_leak")
        # The element and the filters render inside the loop body, which
        # flushes their temps per iteration, so they sit in a statement
        # even when the comprehension itself does not (a ctor member-init).
        # A dict's key and value have no such flush (the emit drains
        # nothing around `insert_or_assign`), and a filter walrus above
        # leaks its decl to the enclosing statement: both stay under the
        # enclosing placement. A filter walrus never reaches a member-init:
        # it binds a body local, so the readiness demote
        # (`CTOR_DEMOTE_BINDS_LOCAL`) has already moved such an init to the
        # body.
        outer_placement = lc.placement
        with lc.placement_scope(SlotPlacement.STATEMENT):
            value_moved = False
            if route.kind == "dict":
                with lc.placement_scope(outer_placement):
                    kt = _comp_result_type(init.result_key_type, analyzer)
                    vt = _comp_result_type(init.result_value_type, analyzer)
                    container = (f"::tpy::ordered_map<{lc.render_type(kt)}, "
                                 f"{lc.render_type(vt)}>")
                    element = None
                    if route.owns_elements:
                        # The value is the last sink (a bare owned loop var moves
                        # unconditionally); the key is earlier, so it moves only when
                        # it is itself the last use (the ordinary last-use gate). When
                        # the value moves, the key is sequenced into `__dk_N` first at
                        # emit.
                        key = _lower_owned_comp_sink(init.key_expr, kt, lc,
                                                     body_declared, gen,
                                                     is_last_sink=False)
                        value = _lower_owned_comp_sink(init.value_expr, vt, lc,
                                                       body_declared, gen,
                                                       is_last_sink=True)
                        value_moved = isinstance(value, THIRMove)
                    else:
                        # The body runs once per iteration: a name bound outside it
                        # is never moved there, whatever its last use says.
                        with lc.moves_only(()):
                            key = _lower_container_elem(init.key_expr, kt, lc,
                                                        body_declared)
                            value = _lower_comp_container_elem(
                                init.value_expr, vt, lc, body_declared,
                                typed_brace=True)
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
                    # Element temps flush PER-ITERATION into the loop body (the
                    # emit's element checkpoint/flush_since window), so the
                    # list/set element is a flushable position:
                    # `take(Probe(c, i))`-style arg temps hoist right above
                    # push_back.
                    # Dict key/value sinks keep the default (no emit window
                    # there).
                    with lc.moves_only(()):
                        element = _lower_comp_container_elem(
                            init.element_expr, elem_t, lc, body_declared,
                            allow_temps=True)
                key = value = None
            if conditions_lowered is None:
                with lc.moves_only(()):
                    conditions_lowered = tuple(
                        _lower_truthy(c, lc, body_declared, temps_ok=True)
                        for c in gen.conditions)
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
        # The non-value field read (storage form) -- the plain
        # `recv.field` / `recv->field` render.
        _witness("comp.field_iter")
        iterable = _lower_field_source(gen.iterable, lc, declared)
    else:
        if route.gen_factory:
            _witness("comp.genfac_source")
        if route.iter_protocol:
            _witness("comp.iter_protocol_source")
        if route.native_combinator:
            _witness("comp.combinator_source")
        # allow_temps: the source-call's own arg temps (`int32_t __tmp_N =
        # 8;` a generic factory's ref-slot literal) flush BEFORE the comp's
        # enclosing statement.
        # The `__iter__` family hands on the OBJECT: a reassigned record
        # local's pointer read derefs (`iter_range(*c)`), as the for
        # statement's capture does.
        iterable = _lower_expr(
            gen.iterable, lc, declared,
            use=_ExprUse(result=_ExprResultUse.ITERABLE,
                         pos=SinkPos.ITER_SOURCE, allow_temps=True,
                         forms=(_ONLY_INDIRECT_READ if route.iter_protocol
                                else None)))
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
        counter_cpp=(lc.render_type(route.counter_type)
                     if route.counter_type is not None else ""),
        counter_bigint=(route.counter_type is not None
                        and is_big_int_type(route.counter_type)),
        range_start=range_start,
        range_stop=range_stop,
        range_start_literal=start_lit,
        range_stop_literal=stop_lit,
        iterable=iterable,
        iterable_lvalue=route.iterable_lvalue,
        iter_protocol=route.iter_protocol,
        sized_reserve=route.sized_reserve,
        unpack_targets=unpack_targets,
        unpack_target_cpps=unpack_cpps,
        # temps_ok: a filter's per-iteration temps (an owned-move ctor arg)
        # render at loop-body indent before the `if` -- the emit flushes them
        # inside the loop scope, where the loop var is declared.
        conditions=conditions_lowered,
        element=element,
        key=key,
        value=value,
        value_moved=value_moved,
        loc=loc,
    )


def _lower_genexpr(expr: TpyGeneratorExpression, lc: '_LowerCtx',
                   declared: dict[str, TpyType]) -> THIRGenExpr:
    global_scope = lc.global_binding_scope
    lc.global_binding_scope = False
    try:
        return _lower_genexpr_impl(expr, lc, declared)
    finally:
        lc.global_binding_scope = global_scope


def _lower_genexpr_impl(expr: TpyGeneratorExpression, lc: '_LowerCtx',
                       declared: dict[str, TpyType]) -> THIRGenExpr:
    """Lower the CREATION of a generator expression: the `_source_route`
    verdict the comprehension shares (which source, borrowed lvalue vs owned
    rvalue) decides how the frame sema built takes its source. The body is
    that function's, lowered like any generator body."""
    analyzer = lc.analyzer
    gen = expr.generator
    loc = getattr(expr, "loc", None)
    it = gen.iterable
    if expr.frame_range_args:
        # The bounds go in by value; the frame loops over a range of them.
        return _lower_genexpr_frame(expr, None, False, False, lc, declared)
    route = _source_route(gen, declared, analyzer, lc)
    if route is None:
        raise ThirUnsupported("genexpr.iterable_shape")
    if route.owns_elements:
        # An `Iterator[Own[T]]` source hands each element over by value into
        # the source iterator's own slot, which the next advance overwrites;
        # a genexpr yields a reference element through the `val_or_ref<T>`
        # slot, so the consumer would hold a borrow of that slot. The
        # comprehension's owned-move sinks have no yield twin yet.
        raise ThirUnsupported("genexpr.owned_source", loc=loc)
    if route.iter_protocol:
        # The `__iter__`/`__next__` family is a comprehension source only: a
        # frame cannot yet hold what its `__iter__` lends
        # (BUGS.md#genexpr-over-user-iterable).
        raise ThirUnsupported("genexpr.iterable_shape", loc=loc)
    owned = not route.iterable_lvalue
    # Decided here, off the route: the owning boundary that would move the
    # frame (a lazy combinator taking this genexpr as its rvalue argument)
    # only reads the verdict.
    pinned = (owned and route.native_combinator
              and _combinator_pins_source(it, analyzer))
    return _lower_genexpr_frame(expr, route, owned, pinned, lc, declared)


def _lower_genexpr_frame(expr: TpyGeneratorExpression, route: '_SourceRoute | None',
                         owned: bool, pinned: bool, lc: '_LowerCtx',
                         declared: dict[str, TpyType]) -> THIRGenExpr:
    """The creation site of a genexpr: the source, admitted by the route the
    comprehensions share, and the reads of the captured names, handed to the
    frame factory of the function sema built."""
    analyzer = lc.analyzer
    func = expr.frame_func
    it = expr.generator.iterable
    fis = analyzer.ctx.registry.get_function(func.name)
    factory = free_callee_cpp(analyzer.ctx.module_attributes,
                              analyzer.ctx.module_name,
                              analyzer.ctx.cpp_module_name, func.name,
                              fis[-1] if fis else None)
    if factory is None:
        raise ThirUnsupported("genexpr.frame_factory")
    if func.type_params:
        # The enclosing function's type params lead the factory's template
        # header and nothing deduces them; they are in scope by name here.
        factory += f"<{', '.join(func.type_params)}>"
    range_args = tuple(_lower_expr(a, lc, declared) for a in expr.frame_range_args)
    if range_args:
        iterable = None
    elif isinstance(it, TpyFieldAccess):
        iterable = _lower_field_source(it, lc, declared)
    else:
        iterable = _lower_expr(
            it, lc, declared, use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                           pos=SinkPos.ITER_SOURCE))
    if owned and route is not None and isinstance(iterable, THIRContainerLiteral):
        # The factory returns the literal, and a bare brace-init would deduce a
        # `std::initializer_list` -- a view of a backing array that dies with
        # the factory call. The resolved container type makes it own them.
        iterable = replace(iterable, typed_brace_cpp=lc.render_type(route.it_type))
    param_types = dict(func.params)
    captures = tuple(
        _capture_lvalue(read, param_types.get(read.name), expr, lc, declared)
        for read in expr.frame_captures)
    _witness("genexpr.frame")
    return THIRGenExpr(
        result_type=analyzer.get_expr_type(expr),
        iterable=iterable,
        owned_source=owned,
        pinned_source=pinned,
        frame_factory_cpp=factory,
        range_args=range_args,
        frame_captures=captures,
        loc=getattr(expr, "loc", None),
    )


def _capture_lvalue(read: TpyName, ptype: 'TpyType | None',
                    expr: TpyGeneratorExpression, lc: '_LowerCtx',
                    declared: dict[str, TpyType]) -> THIRExpr:
    """The lvalue a genexpr's frame binds one capture to.

    The frame's slot is a reference to whatever this renders. A local the
    enclosing body keeps behind a pointer it RE-SEATS on a rebind reads bare as
    that pointer, while the genexpr's body was analyzed against the object: the
    capture is the pointee. That reference cannot follow a later re-seat, so
    where something can rebind the name between two pulls there is nothing
    sound to bind it to."""
    bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if isinstance(ptype, TpyType) else None)
    # The function's param type says what it was handed: an Optional param
    # takes the whole value, whatever this function's flow knows about it here.
    lowered = _lower_expr(read, lc, declared,
                          allow_whole_optional=isinstance(bare, OptionalType))
    if (isinstance(lowered, THIRName) and not lowered.deref
            and read.name in lc.pointers and bare is not None
            and not isinstance(bare, OptionalType)):
        if read.name in expr.frame_rebindable:
            raise ThirUnsupported("genexpr.capture_rebound",
                                  loc=getattr(expr, "loc", None))
        lowered = replace(lowered, deref=True)
    return lowered


def reject_pinned_genexpr_arg(call, arg, lowered, analyzer) -> None:
    """The owning boundary: a lazy native combinator stores a non-lvalue
    argument by moving it into the iterator it returns
    (`builtin_enumerate(Iterable&&)`), so a genexpr argument's frame is
    moved before its first pull. A frame over a PINNED source has no move
    ctor, and the C++ build fails deep in the runtime -- so it is a located
    reject here instead (BUGS.md#separate-iter-temp-no-flush-slot)."""
    if (isinstance(lowered, THIRGenExpr) and lowered.pinned_source
            and _native_iter_combinator(call, analyzer)):
        raise ThirUnsupported("genexpr.pinned_into_owning",
                              loc=getattr(arg, "loc", None))
