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
    ReadonlyType,
    TpyType,
    TupleType,
    peel_value_readonly,
    resolve_int_literals,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
    yield_borrow_slot_cpp,
    yield_uses_borrow_slot,
)
from ...type_def_registry import (
    is_array,
    is_big_int_type,
    is_bytes_type,
    is_dict,
    is_dict_view,
    is_fixed_int_type,
    is_list,
    is_set,
    is_span,
    is_span_iter,
)
from ...modules.type_resolution import get_iterable_element_type, is_native_iterable
from ...codegen_cpp.context import (
    contains_named_expr, escape_cpp_name, is_lvalue_iterable,
    loop_var_binding)
from ...codegen_cpp.types import resolve_pending_container
from ..reject import ThirUnsupported
from ..faces import witness as _witness
from ..nodes import (
    THIRComprehension, THIRContainerLiteral, THIRExpr, THIRGenExpr, THIRMove)
from .checks import _container_storage_call_rvalue
from .predicates import (
    _mixed_own_storage_source,
    _storage_opt_ternary_elem,
    _call_iterable_lvalue,
    _dict_view_iterable_ok,
    _genfac_like_call,
    _eligible_char,
    _eligible_enum,
    _eligible_scalar,
    _callable_value,
    _f1_record,
    _field_decl_type,
    _field_receiver_ok,
    _for_each_elem_binding_ok,
    _foreach_storage_opt_elem,
    _foreach_value_opt_elem,
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
    _ExprResultUse,
    _ExprUse,
    _LowerCtx,
    SinkPos,
)
from .expressions import (
    _capture_entry_cpp,
    _is_move_source,
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

def _native_iter_combinator(it, analyzer) -> bool:
    """A builtin iterator COMBINATOR call (`zip`/`map`/`filter`/`reversed`/
    `enumerate`/`iter`): a @native or @cpp_template callee whose result is the
    structural `typing.Iterator` protocol over a C++ object that has
    begin()/end(), which is what the comp loop calls unconditionally. The
    complement of `_genfac_like_call`, which excludes exactly these callees so
    the frame rows stay theirs; both verdicts key on the same fi flags so the
    two arms cannot claim one call."""
    if not isinstance(it, (TpyCall, TpyMethodCall)):
        return False
    fi = it.resolved_function_info
    if fi is None or fi.is_generator:
        return False
    if not (fi.native_name or fi.native_function or fi.cpp_template):
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(it))))
    return (isinstance(rt, NominalType) and rt.is_protocol
            and rt.qualified_name() == "typing.Iterator")

def _reference_typed_elem(t: 'TpyType | None') -> bool:
    """The yielded element is a reference type (a tuple counts if ANY member
    is), so a combinator that hands back a COPY is observably wrong -- a
    mutation through the loop var never reaches the source."""
    if t is None:
        return False
    b = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(b, TupleType):
        return any(_reference_typed_elem(m) for m in b.element_types)
    return not b.is_value_type()

# Only `zip` lacks an owning DIRECT flavor: `builtin_zip`'s rvalue overload
# gives `owning_zip_iter`, whose tuple members are spelled `T` where
# `zip_direct_iter` spells `val_or_ref_t<T>`, so one rvalue argument costs
# every argument its reference. `enumerate` has
# `owning_enumerate_direct_iter`, so an owned container lends through it
# (BUGS.md#nested-combinator-yields-element-copies).
_NAMED_ARG_COMBINATORS = frozenset({"tpy._builtins._funcs.zip"})

# These lend for a begin()/end() container of either value category:
# `enumerate` / `filter` through their direct flavors, `map` because codegen
# spells its element `val_or_ref<T>`.
_CONTAINER_ARG_COMBINATORS = frozenset({
    "tpy._builtins._funcs.enumerate",
    "tpy._builtins._funcs.map",
    "tpy._builtins._funcs.filter",
    "tpy._builtins._funcs.iter",
})

def _begin_end_container(at, analyzer) -> bool:
    """The argument types the runtime's `detail::has_begin_end` accepts: the
    container families plus `Span` and the records the record emitter gives
    synthesized begin()/end(). Every `next_iter_mixin` iterator is excluded,
    which is why a combinator argument never qualifies."""
    at = unwrap_ref_type(unwrap_send_sync(at))
    at = resolve_pending_container(at, analyzer) or at
    return (_comp_sized_iterable(at) or _lending_span(unwrap_readonly(at))
            or _comp_synth_begin_end(at, analyzer))


def _lending_span(t) -> bool:
    """A `Span[T]` argument lends, but not `Span[readonly[T]]`: the combinator
    then spells its element as `val_or_ref<const T>` and the loop var binds
    the raw proxy (double-wrapped), which no member access can use -- an
    ill-formed render on both paths, so the readonly-element span stays a
    located reject until the element spelling peels the readonly layer."""
    if not is_span(t):
        return False
    args = getattr(t, "type_args", None)
    return not (args and isinstance(args[0], ReadonlyType))

def _source_arg_copies(a, analyzer, *, named_only: bool) -> bool:
    """Whether one combinator ARGUMENT costs the source its references.

    An argument that is not a source at all -- `map`/`filter`'s callable,
    `enumerate`'s start -- never does. Every other argument must be a
    begin()/end() container, and under `named_only` (`zip`, whose rvalue
    overload has no direct flavor) a plain NAME as well. A combinator argument
    is neither: `next_iter_mixin` types have no begin()/end(), so
    `filter(pred, filter(pred, xs))` reaches the by-value
    `owning_filter_iter` -- which is why the rule mirrors factory selection
    per node instead of recursing into the argument's own verdict.

    `named_only` is narrower than the C++ rule, which takes any lvalue: a
    FIELD source is an lvalue and does select `zip_direct_iter`, but inside a
    method whose `self` is inferred readonly the container arrives `const` and
    `val_or_ref_t<T>` cannot bind, which is a build failure on the pre-THIR
    emitter too. Distinguishing the two needs the receiver's const-ness, which
    is not a fact this lowering has."""
    at = analyzer.get_expr_type(a)
    if at is None:
        return True
    if get_iterable_element_type(at, registry=analyzer.registry) is None:
        return False
    if not _begin_end_container(at, analyzer):
        return True
    return named_only and not isinstance(a, TpyName)

def _combinator_copies(it, analyzer) -> bool:
    """Whether the combinator source `it` hands its elements back by value.

    Mirrors the runtime's factory selection one node at a time. An unverified
    callee copies, `reversed_iter` above all
    (BUGS.md#reversed-yields-element-copies). `zip` needs every source
    argument to be a NAMED begin()/end() container; the others need a
    begin()/end() container of either value category. No recursion: a
    combinator argument fails the container test at this level, whatever its
    own verdict would have been."""
    fi = getattr(it, "resolved_function_info", None)
    qn = fi.qualified_name if fi is not None else ""
    if qn in _CONTAINER_ARG_COMBINATORS:
        return any(_source_arg_copies(a, analyzer, named_only=False)
                   for a in it.args)
    if qn in _NAMED_ARG_COMBINATORS:
        return any(_source_arg_copies(a, analyzer, named_only=True)
                   for a in it.args)
    return True

def _combinator_owning_flavor(it, analyzer) -> bool:
    """Whether the combinator source `it` selects the runtime's OWNING
    flavor: one source argument that is not a C++ lvalue (a call result, a
    literal, a genexpr, another combinator) binds the factory's `&&`
    overload, which moves the argument into an `owning_*_iter`. Every owning
    flavor deletes its move ctor (it aliases its own slot), so a holder built
    over it -- a genexpr's `genexpr_state` -- cannot be moved either, not
    even before the first pull. Mirrors overload selection one node at a
    time, like `_combinator_copies`; a non-source argument (`map`/`filter`'s
    callable, `enumerate`'s start) never selects it."""
    for a in it.args:
        at = analyzer.get_expr_type(a)
        if at is None:
            return True
        if get_iterable_element_type(at, registry=analyzer.registry) is None:
            continue
        if not is_lvalue_iterable(a, analyzer.registry.get_record,
                                  analyzer.get_expr_type):
            return True
    return False

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


def _source_route(gen, declared: dict[str, TpyType],
                  analyzer) -> '_SourceRoute | None':
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
    combinator = False
    combinator_copies = False
    if not owns and _native_iter_combinator(it, analyzer):
        # The combinator rvalue is captured owning (`auto __obj_N =
        # ::tpy::builtin_zip<...>(xs, ys);`) and iterated begin/end; its own
        # lowering re-validates callee and args. Never sized -- an Iterator
        # has no len().
        if _iter_rvalue_source(it, analyzer):
            return None
        it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(it))))
        if it_type is None:
            return None
        combinator = True
        combinator_copies = _combinator_copies(it, analyzer)
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
    if combinator_copies and _reference_typed_elem(et):
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
                analyzer) -> '_CompRoute | None':
    """Classify a comprehension init: the shared `_source_route` verdict plus
    the comp's own result-slot / unpack-target gates and the reserve fact, or
    None (the caller rejects)."""
    kind = _COMP_KINDS.get(type(init))
    if kind is None:
        return None
    gen = init.generator
    src = _source_route(gen, declared, analyzer)
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
                    or _f1_record(
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
            or _f1_record(slot, analyzer))

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
    if _is_move_source(e, lc):
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
        else (ref if (_f1_record(
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
        narrowed: AbstractSet[str], analyzer) -> '_CompRoute | None':
    """Resolve the route data consumed while lowering a comprehension."""
    if t is None:
        return None
    if is_array(t):
        return _comp_array_route(
            init, t, declared, pointers, rebind_slots,
            storage_tuple_locals, narrowed, analyzer)
    route = _comp_route(init, declared, analyzer)
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
        # An owned-move source admits a hashable F1-record KEY (sema validated
        # hashability by giving the dict a record key type); every other dict
        # comp keeps the narrow key slice.
        key_ok = (_comp_slot_ok(args[0], analyzer)
                  or (route.owns_elements and _f1_record(args[0], analyzer)))
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
        counter_cpp=counter.to_cpp(),
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
            it, lc, declared, use=_ExprUse(result=_ExprResultUse.ITERABLE))
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
        lc.storage_tuple_locals, lc.narrow.narrowed.keys(), analyzer)
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
            conditions_lowered = tuple(
                _lower_truthy(c, lc, body_declared, temps_ok=True)
                for c in gen.conditions)
            for leaked in set(body_declared) - pre:
                declared[leaked] = body_declared[leaked]
            _witness("comp.filter_walrus_leak")
        value_moved = False
        if route.kind == "dict":
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
                key = _lower_container_elem(init.key_expr, kt, lc,
                                            body_declared)
                value = _lower_comp_container_elem(init.value_expr, vt, lc,
                                                   body_declared,
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
                element = _lower_comp_container_elem(
                    init.element_expr, elem_t, lc, body_declared,
                    allow_temps=True)
            key = value = None
        if conditions_lowered is None:
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
        if route.native_combinator:
            _witness("comp.combinator_source")
        # allow_temps: the source-call's own arg temps (`int32_t __tmp_N =
        # 8;` a generic factory's ref-slot literal) flush BEFORE the comp's
        # enclosing statement.
        iterable = _lower_expr(
            gen.iterable, lc, declared,
            use=_ExprUse(result=_ExprResultUse.ITERABLE, allow_temps=True))
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


def _genexpr_captures(element, conditions, extra_refs, declared, comp_vars,
                      self_receiver, module_globals=frozenset(),
                      frame_lc: '_LowerCtx | None' = None,
                      narrow: '_NarrowScope | None' = None) -> str:
    """The `&local, ` capture prefix for the outer names a genexpr lambda
    reads. Refs come from the element
    (+ filter conditions, + `extra_refs` for the IIFE's iterable refs); keep only
    function locals not shadowed by the comprehension scope; `self` maps to the
    captured `this`. Module globals are seeded into `declared` alongside the
    locals but have static storage duration, so capturing one is ill-formed
    C++ -- `module_globals` takes them back out. `frame_lc` marks the OUTER
    IIFE's list: inside a resumable frame a captured name may be a frame
    member, which `_capture_entry_cpp` names in an init-capture (`&xs = xs`)
    the way a lambda does; the inner lambda captures the IIFE's own
    references and needs no such entry. A name narrowed by an enclosing
    `isinstance` is captured under its extraction ALIAS -- that is the
    binding the lowered body reads, and the union itself is the wrong C++
    type for it. A poly-narrowed subject has no capturable name (its read is
    a spelled deref), so it rejects."""
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
    names = sorted((refs & set(declared)) - comp_vars - set(module_globals))
    aliases = dict(narrow.narrowed) if narrow is not None else {}
    if narrow is not None and any(n in narrow.spelled for n in names):
        # A poly-narrowed subject reads as a spelled deref, not a name.
        raise ThirUnsupported("genexpr.spelled_capture")
    for n in names:
        alias = aliases.get(n)
        if alias is not None:
            # The alias is a plain block-scoped reference, never a frame
            # member, so it takes the by-reference entry on both lists.
            parts.append(f"&{escape_cpp_name(alias)}")
            _witness("genexpr.narrowed_capture")
        elif frame_lc is None:
            parts.append(f"&{escape_cpp_name(n)}")
        else:
            try:
                parts.append(_capture_entry_cpp(n, frame_lc, declared,
                                                by_value=False))
            except ThirUnsupported:
                raise ThirUnsupported("genexpr.frame_capture")
    if not parts:
        return ""
    return ", ".join(parts) + ", "


def _lower_genexpr(expr: TpyGeneratorExpression, lc: '_LowerCtx',
                   declared: dict[str, TpyType]) -> THIRGenExpr:
    """Lower a generator expression to the make_generator render: the
    `_source_route` verdict the comprehension shares (which source, borrowed
    lvalue vs owned rvalue, element type), then the genexpr's own gates --
    scalar/char/str/F1-record/value-Optional loop-var bindings or the
    tuple-unpack head, the yield slot, optional &&-joined filters. A range()
    source takes `_lower_genexpr_range`'s counter lambda. Everything else
    raises ThirUnsupported, rejecting the enclosing body."""
    analyzer = lc.analyzer
    gen = expr.generator
    loc = getattr(expr, "loc", None)
    it = gen.iterable
    if isinstance(it, TpyCall) and it.func_name == "range":
        return _lower_genexpr_range(expr, it, lc, declared)
    route = _source_route(gen, declared, analyzer)
    if route is None:
        raise ThirUnsupported("genexpr.iterable_shape")
    if route.owns_elements:
        # An `Iterator[Own[T]]` source hands each element over by value into
        # the source iterator's own slot, which the next advance overwrites;
        # a genexpr yields a reference element through the `val_or_ref<T>`
        # slot, so the consumer would hold a borrow of that slot. The
        # comprehension's owned-move sinks have no yield twin yet.
        raise ThirUnsupported("genexpr.owned_source", loc=loc)
    owned = not route.iterable_lvalue
    # Decided here, off the route: the owning boundary that would move the
    # closure (a lazy combinator taking this genexpr as its rvalue argument)
    # only reads the verdict.
    nonmovable = (owned and route.native_combinator
                  and _combinator_owning_flavor(it, analyzer))
    it_type = route.it_type
    sema_elem = route.et
    # The owned form binds off the holder's seeded optional and advances on
    # the next pull; the borrowed form keeps the begin/end pair (see
    # THIRGenExpr).
    deref = "*(*__st.beg)" if owned else "*__beg++"
    unpack_targets: tuple = ()
    unpack_cpps: tuple = ()
    genexpr_opt_vars: list[str] = []
    body_declared = dict(declared)
    if gen.unpack_vars is not None:
        # Tuple-unpack head (`n for p, n in items`): per-target binds off
        # `auto& __tup_N = <deref>;`, mirroring the comp unpack. A container
        # LITERAL source rides the same rungs -- the tuple is destructured
        # off the lambda's own `__src` capture -- but only for an all-VALUE
        # element tuple: a pointer-repr element would run the literal
        # through the storage lift, and the capture spelling for that has
        # no witness.
        elem_tup = unwrap_readonly(sema_elem)
        if (not isinstance(elem_tup, TupleType)
                or len(elem_tup.element_types) != len(gen.unpack_vars)
                or (isinstance(it, TpyArrayLiteral)
                    and elem_tup.has_pointer_repr_element())):
            raise ThirUnsupported("genexpr.unpack")
        types: list = []
        for uname, ett in zip(gen.unpack_vars, elem_tup.element_types):
            tt = _peel_value_readonly(unwrap_ref_type(ett))
            if uname is None:
                types.append(None)
                continue
            # A str target COPIES the stored element (`std::string k =
            # std::get<i>(tup);` -- the value-type branch over the
            # tuple's OWNED element spelling), so its reads stay
            # STORAGE-form. An F1-record target BORROWS off the tuple
            # (`auto& p = std::get<0>(t);`) -- the ref binding
            # `_unpack_target_cpps` already spells, so a body mutation through
            # it reaches the source element. The comp unpack head carries both
            # disjuncts.
            if not (_eligible_scalar(tt) or _owned_str_slot(tt, analyzer)
                    or _f1_record(unwrap_readonly(unwrap_send_sync(tt)),
                                  analyzer)
                    or _optional_ptr_borrow(tt, analyzer) is not None):
                raise ThirUnsupported("genexpr.unpack")
            types.append(tt)
            body_declared[uname] = tt
        unpack_targets = tuple(gen.unpack_vars)
        unpack_cpps = _unpack_target_cpps(tuple(types), lc,
                                          gen.const_loop_var)
        for uname, tt in zip(gen.unpack_vars, types):
            if (uname is not None and tt is not None
                    and _optional_ptr_borrow(tt, analyzer) is not None
                    and uname not in lc.storage_opt_locals):
                genexpr_opt_vars.append(uname)
                lc.storage_opt_locals.add(uname)
        binding_cpp = ""
        comp_vars = {n for n in gen.unpack_vars if n is not None}
        _witness("genexpr.unpack")
    else:
        if not (_eligible_scalar(sema_elem) or _eligible_char(sema_elem)
                # A str element binds through the same shared
                # loop_var_binding (`std::string_view w = *__beg++;` -- the
                # view aliases the source element, valid for the lambda's
                # scope like the comp loop var).
                or _resolved_str_value(sema_elem, analyzer) is not None
                # An F1-record element binds the borrow alias
                # (`auto&& n = *__beg++;`) and yields through the
                # `val_or_ref<T>` slot -- reference-preserving, so a body
                # mutation through the loop var reaches the source.
                or _f1_record(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(sema_elem))), analyzer)
                # A value-repr Optional[cheap scalar] element binds the typed
                # copy (`std::optional<int32_t> x = *__beg++;`) and its body
                # reads route off the declared type, exactly as the
                # comprehension loop var over the same container does.
                or _foreach_value_opt_elem(sema_elem) is not None):
            raise ThirUnsupported("genexpr.binding_shape")
        binding_cpp = loop_var_binding(sema_elem, escape_cpp_name(gen.var),
                                       deref, gen.const_loop_var)
        body_declared[gen.var] = sema_elem
        comp_vars = {gen.var}
    elem_type = _comp_result_type(expr.result_elem_type, analyzer)
    if yield_uses_borrow_slot(elem_type):
        # A record element yields through the reference-preserving
        # `::tpy::val_or_ref<T>` slot (`make_generator<val_or_ref<Node>>`,
        # `std::optional<val_or_ref<Node>>(n)`); the loop-var name feeds
        # the wrap bare. The helper's const branch cannot fire from HERE
        # today: a genexpr element type never carries `readonly`, because
        # sema strips the source container's outer readonly before the
        # element is computed (BUGS.md#genexpr-readonly-source-slot). The
        # spelling still comes from the shared helper so the two yield
        # sites cannot disagree once that entry closes.
        slot_cpp = yield_borrow_slot_cpp(elem_type, lc.render_type(elem_type))
    else:
        slot_cpp = lc.render_type(elem_type)
    try:
        with _scrubbed_pointers(lc, _comp_pointer_shadow(gen, lc)):
            # allow_temps: the emit's yield_lines flushes element temps
            # into the lambda body per iteration, so the element is a
            # flush position -- the range flavor's twin.
            # field_owned_str_ok: a view-family FIELD element renders the
            # bare member read and the yield slot's optional constructs
            # from it, the same way it absorbs a view-form loop var.
            element = _lower_expr(expr.element_expr, lc, body_declared,
                                  use=_ExprUse(allow_temps=True),
                                  field_owned_str_ok=isinstance(
                                      expr.element_expr, TpyFieldAccess))
            # Filter conditions render inside the lambda body against the
            # loop vars (the comp slice's truthy lowering; per-iteration
            # temps flush at emit).
            conditions = tuple(
                _lower_truthy(c, lc, body_declared, temps_ok=True)
                for c in gen.conditions)
    finally:
        for uname in genexpr_opt_vars:
            lc.storage_opt_locals.discard(uname)
    if gen.conditions:
        _witness("genexpr.filter")
    # The source renders inside the IIFE, so it has no statement-level flush
    # point: a source whose arg render would hoist a `__tmp_N` (a generic
    # factory's ref-slot literal) rejects rather than reference a temp the
    # IIFE never captured.
    if isinstance(it, TpyFieldAccess):
        iterable = _lower_field_source(it, lc, declared)
    else:
        iterable = _lower_expr(
            it, lc, declared, use=_ExprUse(result=_ExprResultUse.ITERABLE))
    if owned and isinstance(iterable, THIRContainerLiteral):
        # The holder's `src` must own the literal's elements: a bare
        # brace-init would deduce a std::initializer_list, a view of a
        # backing array that dies with the IIFE. Self-describe it with the
        # resolved container type (`std::array<int32_t, 4>{1, 2, 3, 4}`).
        iterable = replace(iterable, typed_brace_cpp=lc.render_type(it_type))
    mod_globals = lc.prescan.namespace_scope_names()
    inner_captures = _genexpr_captures(expr.element_expr, gen.conditions,
                                       None, declared, comp_vars,
                                       lc.self_receiver, mod_globals,
                                       narrow=lc.narrow)
    iife_captures = _genexpr_captures(expr.element_expr, gen.conditions,
                                      collect_name_refs(it), declared,
                                      comp_vars, lc.self_receiver,
                                      mod_globals, frame_lc=lc,
                                      narrow=lc.narrow)
    _witness("genexpr.native_iterable")
    return THIRGenExpr(
        result_type=analyzer.get_expr_type(expr),
        iterable=iterable,
        element=element,
        conditions=conditions,
        slot_cpp=slot_cpp,
        binding_cpp=binding_cpp,
        iife_captures=iife_captures,
        inner_captures=inner_captures,
        owned_source=owned,
        nonmovable_source=nonmovable,
        unpack_targets=unpack_targets,
        unpack_target_cpps=unpack_cpps,
        const_loop_var=gen.const_loop_var,
        loc=loc,
    )


def reject_nonmovable_genexpr_arg(call, arg, lowered, analyzer) -> None:
    """The owning boundary: a lazy native combinator (`enumerate` / `zip` /
    `filter` / `map` / `reversed` -- the `_native_iter_combinator` verdict)
    stores a non-lvalue argument by moving it into the iterator it returns
    (`builtin_enumerate(Iterable&&)`), so a genexpr argument's closure is
    moved before its first pull. A movable closure survives that; one over
    a non-movable source has no move ctor, and the C++ build fails deep in
    the runtime -- so it is a located reject here instead, until producers
    are movable while unstarted (TODO.md)."""
    if (isinstance(lowered, THIRGenExpr) and lowered.nonmovable_source
            and _native_iter_combinator(call, analyzer)):
        raise ThirUnsupported("genexpr.nonmovable_into_owning",
                              loc=getattr(arg, "loc", None))


def _lower_genexpr_range(expr: TpyGeneratorExpression, it: 'TpyCall',
                         lc: '_LowerCtx',
                         declared: dict[str, TpyType]) -> THIRGenExpr:
    """The RANGE-source counter lambda: the bounds move into
    init-captures cast to the counter type, the loop var
    binds `{counter} {var} = __i++;` (2-arg) / `= __i;` + `__i += __step;`
    (3-arg, with the step-nonzero and fixed-int overflow checks). Bounds
    lower target-typed against the counter type; non-scalar counters
    reject."""
    analyzer = lc.analyzer
    gen = expr.generator
    loc = getattr(expr, "loc", None)
    if gen.unpack_vars is not None or len(it.args) not in (1, 2, 3):
        raise ThirUnsupported("genexpr.range")
    it_type = analyzer.get_expr_type(it)
    sema_elem = (get_iterable_element_type(it_type,
                                           registry=analyzer.registry)
                 if it_type is not None else None)
    if sema_elem is None or isinstance(sema_elem, IntLiteralType):
        sema_elem = analyzer.ctx.default_int_type
    if not _eligible_scalar(sema_elem):
        raise ThirUnsupported("genexpr.range")
    counter_cpp = sema_elem.to_cpp()
    range_args = tuple(
        _lower_expr(a, lc, declared,
                    use=_ExprUse(pos=SinkPos.CALL_ARG,
                                 slot_target=sema_elem))
        for a in it.args)
    body_declared = dict(declared)
    body_declared[gen.var] = sema_elem
    comp_vars = {gen.var}
    elem_type = _comp_result_type(expr.result_elem_type, analyzer)
    if yield_uses_borrow_slot(elem_type):
        raise ThirUnsupported("genexpr.borrow_slot")
    slot_cpp = lc.render_type(elem_type)
    # allow_temps: the emit's yield_lines flushes element (and condition)
    # temps into the lambda body per iteration (`Box<int32_t> __tmp_N =
    # ...;` ahead of the yield), so both are flush positions.
    with _scrubbed_pointers(lc, _comp_pointer_shadow(gen, lc)):
        element = _lower_expr(expr.element_expr, lc, body_declared,
                              use=_ExprUse(allow_temps=True))
        conditions = tuple(_lower_truthy(c, lc, body_declared, temps_ok=True)
                           for c in gen.conditions)
    if gen.conditions:
        _witness("genexpr.filter")
    inner_captures = _genexpr_captures(expr.element_expr, gen.conditions,
                                       None, declared, comp_vars,
                                       lc.self_receiver,
                                       lc.prescan.namespace_scope_names(),
                                       narrow=lc.narrow)
    var_cpp = escape_cpp_name(gen.var)
    binding_cpp = (f"{counter_cpp} {var_cpp} = __i++;" if len(it.args) <= 2
                   else f"{counter_cpp} {var_cpp} = __i;")
    _witness("genexpr.range")
    return THIRGenExpr(
        result_type=analyzer.get_expr_type(expr),
        element=element,
        conditions=conditions,
        slot_cpp=slot_cpp,
        binding_cpp=binding_cpp,
        inner_captures=inner_captures,
        range_args=range_args,
        counter_cpp=counter_cpp,
        range_overflow_check=(len(it.args) == 3
                              and is_fixed_int_type(sema_elem)),
        loc=loc,
    )
