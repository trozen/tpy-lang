"""Unit tests for tier-ranked overload resolution helpers.

Covers the pure building blocks in ``tpyc.sema.overloads`` that don't
need a full ``SemanticContext``:

* ``MatchTier`` ordering (the invariant that drives ranking).
* ``_scalar_widening_cost`` / ``_type_args_widening_cost`` (cost model).
* ``_score`` (aggregate ranking key).
* ``_classify_strict_match`` for non-protocol shapes.
* ``_bool_checker_to_classifier`` adapter semantics.
* ``OverloadAmbiguityError`` payload.

End-to-end overload dispatch is covered by the snippet tests under
``tests/cases/``; this file locks down the algebra they depend on.
"""
from __future__ import annotations

import pytest

from tpyc.sema.overloads import (
    ContainerLeaf,
    ContainerMeet,
    MatchTier,
    OverloadAmbiguityError,
    _bool_checker_to_classifier,
    _classify_strict_match,
    _container_fit,
    _scalar_widening_cost,
    _undecided_container_arm,
    call_resolves_params,
    coercion_pass_rank,
    resolve_overload,
    type_matches_with_coercion,
    _score,
    _type_args_widening_cost,
)
from tpyc import get_lib_dir
from tpyc.coercions import CoercionContext
from tpyc.compiler import Compiler
from tpyc.diagnostics import Scope, SemanticError
from tpyc.parse import TpyArrayLiteral
from tpyc.sema.compatibility import TypeCompatibility
from tpyc.sema.context import SemanticContext
from tpyc.sema.pending_num import PendingNums
from tpyc.sema.protocols import ProtocolConformanceKind
from tpyc.sema.type_ops import TypeOperations
from tpyc.typesys import (
    BIGINT,
    BOOL,
    CHAR,
    FLOAT,
    FLOAT32,
    INT8,
    INT16,
    INT32,
    INT64,
    NONE,
    NominalType,
    PendingListType,
    PendingDictType,
    STR,
    TupleType,
    UINT8,
    UINT32,
    UINT64,
    UNKNOWN_ELEMENT,
    VOID,
    IntLiteralType,
    OptionalType,
    TypeParamRef,
    FunctionInfo,
    ParamInfo,
    ListLiteralInfo,
    TypeRegistry,
    make_array,
    make_span,
)


# --------------------------------------------------------------------------
# MatchTier ordering
# --------------------------------------------------------------------------


def test_match_tier_is_total_ordered_lowest_most_specific():
    # Declaration order must match increasing .value so that the counts-array
    # index lines up with the "lower is more specific" ranking.
    tiers = [
        MatchTier.EXACT_CONCRETE,
        MatchTier.EXACT_GENERIC_SHAPE,
        MatchTier.PROTOCOL_EXPLICIT,
        MatchTier.PROTOCOL_STRUCTURAL,
        MatchTier.GENERIC_PROTOCOL_EXPLICIT,
        MatchTier.GENERIC_PROTOCOL_STRUCTURAL,
        MatchTier.GENERIC_WILDCARD,
    ]
    values = [t.value for t in tiers]
    assert values == sorted(values)
    assert all(a < b for a, b in zip(values, values[1:]))


def test_match_tier_values_start_at_one():
    # Score vector is 1-indexed; if this ever changes the _score helper
    # must be reconciled with the new base.
    assert MatchTier.EXACT_CONCRETE.value == 1


# --------------------------------------------------------------------------
# _scalar_widening_cost
# --------------------------------------------------------------------------


def test_scalar_widening_cost_equal_zero():
    assert _scalar_widening_cost(INT32, INT32) == 0
    assert _scalar_widening_cost(FLOAT, FLOAT) == 0
    assert _scalar_widening_cost(STR, STR) == 0


def test_scalar_widening_cost_int_to_wider_int_uses_bit_gap():
    # bit gap / 8: int32 -> int64 = (64-32)/8 = 4
    assert _scalar_widening_cost(INT32, INT64) == 4
    # int16 -> int32 = (32-16)/8 = 2
    assert _scalar_widening_cost(INT16, INT32) == 2
    # int8 -> int64 = 7; max(1, 7) + 0 sign penalty
    assert _scalar_widening_cost(INT8, INT64) == 7


def test_scalar_widening_cost_cross_sign_adds_penalty():
    # int32 -> uint32: gap 0, sign penalty 1, max(1, 0)+1 = 2.
    assert _scalar_widening_cost(INT32, UINT32) == 2
    # int32 -> uint64: gap 4, sign penalty 1 -> 5.
    assert _scalar_widening_cost(INT32, UINT64) == 5


def test_scalar_widening_cost_same_width_same_sign_is_minimum_one():
    # Different types with identical traits (bits+signedness) shouldn't
    # free-ride to 0 just because neither wider nor signed-different; the
    # max(1, gap) clamp ensures a positive cost.
    assert _scalar_widening_cost(INT8, UINT8) == 2  # sign flip only


def test_scalar_widening_cost_fixed_to_bigint():
    assert _scalar_widening_cost(INT32, BIGINT) == 8
    assert _scalar_widening_cost(INT64, BIGINT) == 8


def test_scalar_widening_cost_fixed_to_float_is_highest():
    # Float lives at a different semantic level; the higher cost reflects
    # that int-literal calls should prefer an int overload over a float one.
    assert _scalar_widening_cost(INT32, FLOAT) == 16
    assert _scalar_widening_cost(INT64, FLOAT32) == 16


def test_scalar_widening_cost_float_to_float_widening():
    # float32 -> float (f64): generic "1" since both are floats.
    assert _scalar_widening_cost(FLOAT32, FLOAT) == 1


def test_scalar_widening_cost_int_literal_with_default_zero_cost():
    il = IntLiteralType(value=1)
    assert _scalar_widening_cost(il, INT32, default_int_type=INT32) == 0


def test_scalar_widening_cost_int_literal_uses_default_as_proxy():
    il = IntLiteralType(value=1)
    # IntLiteral -> int64 under default=int32 scores like int32 -> int64.
    assert _scalar_widening_cost(il, INT64, default_int_type=INT32) == 4
    assert _scalar_widening_cost(il, BIGINT, default_int_type=INT32) == 8
    assert _scalar_widening_cost(il, FLOAT, default_int_type=INT32) == 16


def test_scalar_widening_cost_int_literal_without_default_is_generic():
    il = IntLiteralType(value=1)
    assert _scalar_widening_cost(il, INT32) == 1


def test_scalar_widening_cost_unknown_element_biases_to_default_int():
    # Empty-list element type. Matches CPython's ``sum([]) == 0`` policy.
    assert _scalar_widening_cost(UNKNOWN_ELEMENT, INT32, default_int_type=INT32) == 0
    assert _scalar_widening_cost(UNKNOWN_ELEMENT, INT64, default_int_type=INT32) == 4
    assert _scalar_widening_cost(UNKNOWN_ELEMENT, FLOAT, default_int_type=INT32) == 16


def test_scalar_widening_cost_unknown_shape_falls_back_to_one():
    # Cost of "I can't tell" is conservative, not 0.
    assert _scalar_widening_cost(STR, CHAR) == 1


# --------------------------------------------------------------------------
# _type_args_widening_cost
# --------------------------------------------------------------------------


def _list(elem):
    return NominalType("list", (elem,), _module_qname="builtins.list")


def _iterable(elem):
    return NominalType("Iterable", (elem,), is_protocol=True, _module_qname="tpy.Iterable")


def test_type_args_cost_matching_element_is_zero():
    assert _type_args_widening_cost(_list(INT32), _iterable(INT32)) == 0


def test_type_args_cost_widening_element_is_positive():
    assert _type_args_widening_cost(_list(INT32), _iterable(INT64)) == 4
    assert _type_args_widening_cost(_list(INT32), _iterable(BIGINT)) == 8
    assert _type_args_widening_cost(_list(INT32), _iterable(FLOAT)) == 16


def test_type_args_cost_pending_list_uses_element_type():
    pl = PendingListType(element_type=IntLiteralType(value=1), size=0, literal_id=0)
    # PendingListType.get_element_type() returns element_type, which flows
    # through the single-element fallback.
    assert _type_args_widening_cost(pl, _iterable(INT32), default_int_type=INT32) == 0
    assert _type_args_widening_cost(pl, _iterable(INT64), default_int_type=INT32) == 4


def test_type_args_cost_empty_pending_list_biases_to_default_int():
    pl = PendingListType(element_type=UNKNOWN_ELEMENT, size=0, literal_id=0)
    assert _type_args_widening_cost(pl, _iterable(INT32), default_int_type=INT32) == 0
    assert _type_args_widening_cost(pl, _iterable(INT64), default_int_type=INT32) == 4
    assert _type_args_widening_cost(pl, _iterable(FLOAT), default_int_type=INT32) == 16


def test_type_args_cost_pending_dict_is_not_ranked_by_its_value():
    # A dict iterates its keys: ranking `Iterable[K]` candidates by the
    # value type would pick a specialization for the wrong part.
    pd = PendingDictType(key_type=INT32, value_type=INT64, literal_id=0)
    assert _type_args_widening_cost(pd, _iterable(INT64), default_int_type=INT32) == 0
    assert _type_args_widening_cost(pd, _iterable(INT32), default_int_type=INT32) == 0


class _Ops:
    """A stand-in for TypeOperations carrying only the undecided-container
    query: `leaves[param]` is what the argument meets there (None: a call
    to that candidate alone refuses it), as (cell, held, wanted)."""
    def __init__(self, leaves):
        self.leaves = leaves
        self.asked = []
        self.pending_wants_fit = None

    def pending_arg_leaves(self, arg, param, view):
        self.asked.append(param)
        return self.leaves.get(param)


_OPEN_LIST = PendingListType(element_type=IntLiteralType(value=1), size=2, literal_id=7)
_CELL = object()


def test_undecided_container_is_a_strict_match_its_widening_ranks():
    # The widening is the candidate's first key (`_container_fit`), counted
    # once; the argument's own strict cost is nothing.
    ops = _Ops({_list(INT32): [(None, INT32, INT32)], _list(INT64): [(None, INT32, INT64)]})
    for slot, widening in ((_list(INT32), 0), (_list(INT64), 4)):
        assert _classify_strict_match(_OPEN_LIST, slot, type_ops=ops,
                                      default_int_type=INT32) == (MatchTier.EXACT_CONCRETE, 0)
        meet = _undecided_container_arm(_OPEN_LIST, slot, ops)
        assert _container_fit([meet], INT32) == widening


def test_undecided_container_widening_sums_type_positions():
    tuple_list = _list(TupleType((INT64, FLOAT)))
    ops = _Ops({tuple_list: [(None, INT32, INT64), (None, FLOAT, FLOAT)]})
    meet = _undecided_container_arm(_OPEN_LIST, tuple_list, ops)
    assert _container_fit([meet], INT32) == 4


def test_undecided_container_refused_by_the_query_is_no_match():
    ops = _Ops({})
    assert _classify_strict_match(_OPEN_LIST, _list(INT8), type_ops=ops) is None
    assert not type_matches_with_coercion(_OPEN_LIST, _list(INT8), type_ops=ops)


def test_undecided_container_arm_leaves_protocols_and_type_params_alone():
    ops = _Ops({})
    _classify_strict_match(_OPEN_LIST, _iterable(INT32), type_ops=ops)
    _classify_strict_match(_OPEN_LIST, _list(TypeParamRef("T")), type_ops=ops)
    assert ops.asked == []


def test_coercion_pass_rank_leads_with_container_widening():
    ops = _Ops({_list(INT32): [(None, INT32, INT32)], _list(INT64): [(None, INT32, INT64)]})
    narrow = coercion_pass_rank([_OPEN_LIST], [_list(INT32)], None, None, None, ops, INT32)
    wide = coercion_pass_rank([_OPEN_LIST], [_list(INT64)], None, None, None, ops, INT32)
    assert narrow is not None and wide is not None
    assert narrow.widening < wide.widening
    assert coercion_pass_rank([_OPEN_LIST], [_list(INT8)], None, None, None, ops, INT32) is None


def test_coercion_pass_rank_asks_each_argument_once():
    ops = _Ops({_list(INT64): [(None, INT32, INT64)]})
    coercion_pass_rank([_OPEN_LIST], [_list(INT64)], None, None, None, ops, INT32)
    assert ops.asked == [_list(INT64)]


def test_container_widening_outranks_the_scalar_keys():
    # `f(1, ms)` at `(int, list[int64])` / `(int, Iterable[int64])`: the
    # literal converts to `int` at both, and the candidate that widens the
    # container loses to the one that takes it as it is.
    ops = _Ops({_list(INT64): [(None, INT32, INT64)]})
    widens = _fn("f", BIGINT, _list(INT64))
    view = _fn("f", BIGINT, _iterable(INT64))
    for overloads in ([widens, view], [view, widens]):
        assert resolve_overload(overloads, [IntLiteralType(value=1), _OPEN_LIST],
                                protocol_checker=lambda a, p: True,
                                default_int_type=INT32, type_ops=ops) is view


def test_a_cell_passed_twice_must_be_wanted_at_one_type():
    # `f(ms, ms)` at `(list[int32], list[int64])`: no call to that candidate
    # alone accepts `ms` at both, so it is not applicable.
    ops = _Ops({_list(INT32): [(_CELL, INT32, INT32)],
                _list(INT64): [(_CELL, INT32, INT64)]})
    assert coercion_pass_rank([_OPEN_LIST, _OPEN_LIST], [_list(INT32), _list(INT64)],
                              None, None, None, ops, INT32) is None
    same = coercion_pass_rank([_OPEN_LIST, _OPEN_LIST], [_list(INT64), _list(INT64)],
                              None, None, None, ops, INT32)
    # One cell widened once.
    assert same is not None and same.widening == 4


def test_cells_a_store_ties_are_asked_together():
    # `b = a` ties the cells of two locals: `f(a, b)` at
    # `(list[int32], list[int64])` wants them at types they cannot hold
    # together, which the cells' owner answers (`PendingNums.wants_fit`).
    ops = _Ops({_list(INT32): [(3, INT32, INT32)],
                _list(INT64): [(4, INT32, INT64)]})
    meets = [_undecided_container_arm(_OPEN_LIST, _list(INT32), ops),
             _undecided_container_arm(_OPEN_LIST, _list(INT64), ops)]
    asked = []
    ops.pending_wants_fit = lambda wants: asked.append(dict(wants))
    assert _container_fit(meets, INT32, ops) is None
    assert asked == [{3: INT32, 4: INT64}]
    ops.pending_wants_fit = lambda wants: {c: c for c in wants}
    assert _container_fit(meets, INT32, ops) == 4


def test_cells_tied_both_ways_widen_as_one():
    # `mb = ma`: one list under two names, wanted at int64 by both
    # parameters, is one widening -- as the same name passed twice is.
    ops = _Ops({})
    meets = [ContainerMeet(True, (ContainerLeaf(3, INT32, INT64),)),
             ContainerMeet(True, (ContainerLeaf(4, INT32, INT64),))]
    ops.pending_wants_fit = lambda wants: {3: 3, 4: 3}
    assert _container_fit(meets, INT32, ops) == 4
    ops.pending_wants_fit = lambda wants: {3: 3, 4: 4}
    assert _container_fit(meets, INT32, ops) == 8


def test_a_declared_view_in_a_generic_candidate_is_not_resolved():
    # `g[T](xs: Iterable[int64], t: T)`: only `t` is resolved by the call,
    # so only a view naming `T` decides an undecided container.
    g = FunctionInfo(name="g", params=(ParamInfo("xs", _iterable(INT64)),
                                       ParamInfo("t", TypeParamRef("T")),
                                       ParamInfo("ys", _iterable(TypeParamRef("T")))),
                     return_type=VOID, type_params=["T"])
    assert call_resolves_params(g) == (False, True, True)
    h = _fn("h", _iterable(INT64), INT32)
    assert call_resolves_params(h) == (False, False)


def test_type_args_cost_tuple_all_same_uses_that_element():
    t = TupleType((INT32, INT32, INT32))
    assert _type_args_widening_cost(t, _iterable(INT32)) == 0
    assert _type_args_widening_cost(t, _iterable(INT64)) == 4


def test_type_args_cost_tuple_mixed_elements_returns_zero():
    # Mixed-element tuples have no well-defined "the element type"; the
    # cost model gives up rather than pick arbitrarily.
    t = TupleType((INT32, FLOAT))
    assert _type_args_widening_cost(t, _iterable(INT32)) == 0


def test_type_args_cost_mismatched_arity_returns_zero():
    # dict[K, V] vs Sequence[T] -- the cost model only scores when shapes
    # align. Downstream relies on the protocol match itself filtering.
    pair = NominalType("dict", (INT32, STR), _module_qname="builtins.dict")
    seq = NominalType("Sequence", (INT32,), is_protocol=True, _module_qname="tpy.Sequence")
    assert _type_args_widening_cost(pair, seq) == 0


def test_type_args_cost_empty_protocol_type_args_zero():
    marker = NominalType("Sized", (), is_protocol=True, _module_qname="tpy.Sized")
    assert _type_args_widening_cost(_list(INT32), marker) == 0


# --------------------------------------------------------------------------
# _score
# --------------------------------------------------------------------------


def test_score_higher_tier_count_wins_primary_key():
    exact = ((MatchTier.EXACT_CONCRETE, 0),)
    structural = ((MatchTier.PROTOCOL_STRUCTURAL, 0),)
    # Ascending sort on score: exact must come first.
    assert _score(exact) < _score(structural)


def test_score_mixed_tier_vector_orders_by_counts_lex():
    # A = (exact, structural), B = (structural, structural).
    # A has 1 EXACT_CONCRETE count > B's 0. A wins primary.
    a = ((MatchTier.EXACT_CONCRETE, 0), (MatchTier.PROTOCOL_STRUCTURAL, 0))
    b = ((MatchTier.PROTOCOL_STRUCTURAL, 0), (MatchTier.PROTOCOL_STRUCTURAL, 0))
    assert _score(a) < _score(b)


def test_score_secondary_key_is_total_widening_cost():
    a = ((MatchTier.PROTOCOL_EXPLICIT, 0),)
    b = ((MatchTier.PROTOCOL_EXPLICIT, 4),)
    # Same primary key, smaller total cost wins.
    assert _score(a) < _score(b)


def test_score_primary_beats_secondary():
    # Structural with cost=0 still loses to Explicit with cost=100 because
    # primary key (tier counts) dominates.
    explicit_expensive = ((MatchTier.PROTOCOL_EXPLICIT, 100),)
    structural_cheap = ((MatchTier.PROTOCOL_STRUCTURAL, 0),)
    assert _score(explicit_expensive) < _score(structural_cheap)


def test_score_exact_generic_shape_beats_protocol_explicit():
    # Key invariant for concrete-vs-generic dispatch.
    generic_shape = ((MatchTier.EXACT_GENERIC_SHAPE, 0),)
    protocol = ((MatchTier.PROTOCOL_EXPLICIT, 0),)
    assert _score(generic_shape) < _score(protocol)


def test_score_concrete_beats_generic_protocol():
    concrete = ((MatchTier.PROTOCOL_EXPLICIT, 0),)
    generic = ((MatchTier.GENERIC_PROTOCOL_EXPLICIT, 0),)
    assert _score(concrete) < _score(generic)


def test_score_equal_inputs_equal_outputs():
    a = ((MatchTier.PROTOCOL_EXPLICIT, 2), (MatchTier.EXACT_CONCRETE, 0))
    b = ((MatchTier.EXACT_CONCRETE, 0), (MatchTier.PROTOCOL_EXPLICIT, 2))
    # Aggregate counts + total cost are equal; score is too (matches the
    # dedup-then-ambiguity semantics in resolve_overload).
    assert _score(a) == _score(b)


# --------------------------------------------------------------------------
# _classify_strict_match (non-protocol paths; protocol paths require a
# classifier callable and are exercised end-to-end via snippet tests)
# --------------------------------------------------------------------------


def test_classify_strict_exact_concrete_match():
    assert _classify_strict_match(INT32, INT32) == (MatchTier.EXACT_CONCRETE, 0)


def test_classify_strict_no_match_returns_none():
    assert _classify_strict_match(INT32, FLOAT) is None


def test_classify_strict_int_literal_fits_fixed_int():
    il = IntLiteralType(value=1)
    # Cost 0 for exact-default match, larger for narrower/wider widths so
    # str(IntLiteralType) picks the int32_t overload over int8_t.
    assert _classify_strict_match(il, INT32, default_int_type=INT32) == (MatchTier.EXACT_CONCRETE, 0)
    int8_cost = _classify_strict_match(il, INT8, default_int_type=INT32)
    assert int8_cost is not None and int8_cost[0] == MatchTier.EXACT_CONCRETE and int8_cost[1] > 0
    # Value out of range for int8 -> not a match.
    big = IntLiteralType(value=1000)
    assert _classify_strict_match(big, INT8) is None


def test_classify_strict_int_literal_unknown_value_matches_any_fixed_int():
    # value=None: compiler can't range-check, accept.
    il = IntLiteralType(value=None)
    assert _classify_strict_match(il, INT8, default_int_type=INT32) == (MatchTier.EXACT_CONCRETE, 1)
    assert _classify_strict_match(il, INT32, default_int_type=INT32) == (MatchTier.EXACT_CONCRETE, 0)
    assert _classify_strict_match(il, UINT64, default_int_type=INT32)[0] == MatchTier.EXACT_CONCRETE


def test_classify_strict_none_matches_void():
    assert _classify_strict_match(NONE, VOID) == (MatchTier.EXACT_CONCRETE, 0)


def test_classify_strict_optional_unwrap_delegates():
    opt = OptionalType(INT32)
    # None -> Optional[T].
    assert _classify_strict_match(NONE, opt) == (MatchTier.EXACT_CONCRETE, 0)
    # T -> Optional[T] via recursion.
    assert _classify_strict_match(INT32, opt) == (MatchTier.EXACT_CONCRETE, 0)
    # Mismatch inside Optional also propagates.
    assert _classify_strict_match(FLOAT, opt) is None


def test_classify_strict_protocol_classifies_explicit_via_classifier():
    def cls(a, p):
        return ProtocolConformanceKind.EXPLICIT
    proto = NominalType("Iterable", (INT32,), is_protocol=True, _module_qname="tpy.Iterable")
    tier, _ = _classify_strict_match(_list(INT32), proto, cls)
    assert tier == MatchTier.PROTOCOL_EXPLICIT


def test_classify_strict_protocol_classifies_structural_via_classifier():
    def cls(a, p):
        return ProtocolConformanceKind.STRUCTURAL
    proto = NominalType("Iterable", (INT32,), is_protocol=True, _module_qname="tpy.Iterable")
    tier, cost = _classify_strict_match(_list(INT32), proto, cls)
    assert tier == MatchTier.PROTOCOL_STRUCTURAL
    # Structural is opaque: widening info is not computed.
    assert cost == 0


def test_classify_strict_protocol_no_match_without_classifier():
    # Protocol param with no classifier callable -> can't confirm
    # conformance, no match.
    proto = NominalType("Iterable", (INT32,), is_protocol=True, _module_qname="tpy.Iterable")
    assert _classify_strict_match(_list(INT32), proto) is None


def test_classify_strict_propagates_widening_cost_from_type_args():
    def cls(a, p):
        return ProtocolConformanceKind.EXPLICIT
    proto = NominalType("Iterable", (INT64,), is_protocol=True, _module_qname="tpy.Iterable")
    tier, cost = _classify_strict_match(_list(INT32), proto, cls, default_int_type=INT32)
    assert tier == MatchTier.PROTOCOL_EXPLICIT
    assert cost == 4  # int32 -> int64 widening


# --------------------------------------------------------------------------
# _bool_checker_to_classifier
# --------------------------------------------------------------------------


def test_bool_checker_to_classifier_none_passes_through():
    assert _bool_checker_to_classifier(None) is None


def test_bool_checker_to_classifier_true_means_structural():
    # The adapter has to fabricate a tier; it picks STRUCTURAL so it never
    # silently upgrades an unverified hit to EXPLICIT.
    cls = _bool_checker_to_classifier(lambda a, p: True)
    assert cls(INT32, FLOAT) == ProtocolConformanceKind.STRUCTURAL


def test_bool_checker_to_classifier_false_means_none():
    cls = _bool_checker_to_classifier(lambda a, p: False)
    assert cls(INT32, FLOAT) is None


# --------------------------------------------------------------------------
# OverloadAmbiguityError
# --------------------------------------------------------------------------


def _fn(name: str, *param_types) -> FunctionInfo:
    return FunctionInfo(
        name=name,
        params=tuple(ParamInfo(f"p{i}", t) for i, t in enumerate(param_types)),
        return_type=VOID,
    )


def test_overload_ambiguity_error_carries_candidates():
    a = _fn("f", INT32)
    b = _fn("f", INT64)
    err = OverloadAmbiguityError((a, b))
    assert err.candidates == (a, b)
    msg = str(err)
    assert "f(int32)" in msg
    assert "f(int64)" in msg


# --------------------------------------------------------------------------
# The overload query writes nothing (undecided containers)
# --------------------------------------------------------------------------


def _literal_compat():
    """A compatibility checker and the record of a list literal `[1, 2]`
    no cell decides."""
    ctx = SemanticContext(registry=TypeRegistry(), global_scope=Scope())
    compat = TypeCompatibility(ctx)
    compat.pend = PendingNums(ctx, compat)
    compat.type_ops = TypeOperations(ctx)
    info = ListLiteralInfo(literal_id=0, expr=TpyArrayLiteral([]),
                           element_type=IntLiteralType(value=1), size=2)
    ctx.container_literals[0] = info
    return compat, info, PendingListType(IntLiteralType(value=1), 2, 0)


@pytest.mark.parametrize("slot", [make_span(INT64), make_array(INT64, 2)],
                         ids=["span", "array"])
def test_overload_query_leaves_the_literal_record_alone(slot):
    # A candidate asked and then refused must leave no element or storage
    # fact on the literal; only the committing check of the winner writes.
    compat, info, lit = _literal_compat()
    assert compat.is_type_compatible(lit, slot, CoercionContext.ARG)
    assert info.coerced_element_type is None
    assert not info.passed_to_span_param
    compat._check_compat(lit, slot, "", coercion_ctx=CoercionContext.ARG,
                         commit=True)
    assert info.coerced_element_type == INT64


_SEMA_PRELUDE = """from tpy import dispatch, int8, int32, int64
def a64() -> int64: return 1099511627776
"""


@pytest.mark.parametrize(
    ("body", "message"),
    [
        # The overloaded `raise` constructor: its winner decides the local,
        # which its user exception's codegen cannot show end to end
        # (BUGS.md#dispatch-ctor-group-unlowered).
        pytest.param(
            "class E(Exception):\n    n: int32\n"
            "    @dispatch\n    def __init__(self, xs: list[int32]) -> None:\n"
            "        self.n = len(xs)\n"
            "    @dispatch\n    def __init__(self, s: str) -> None:\n"
            "        self.n = -1\n"
            "def main() -> None:\n    ms = [1, 2]\n    try:\n"
            "        raise E(ms)\n    except E as e:\n        print(e.n)\n"
            "    ms.append(a64())\n",
            r"^'ms' holds int32 elements since line 14 \(passed as "
            r"list\[int32\]\), and this value is int64",
            id="raise-group-then-wider"),
        pytest.param(
            "class E(Exception):\n    n: int32\n"
            "    @dispatch\n    def __init__(self, xs: list[str]) -> None:\n"
            "        self.n = 1\n"
            "    @dispatch\n    def __init__(self, xs: list[int64]) -> None:\n"
            "        self.n = 2\n"
            "def main() -> None:\n    xs = []\n    raise E(xs)\n",
            r"^Ambiguous overload: __init__\(list\[str\]\), "
            r"__init__\(list\[int64\]\)$",
            id="raise-group-ambiguous"),
        # The `typing.overload` form: the stub the call resolves to decides
        # the local (its implementation's narrowing does not lower,
        # BUGS.md#overload-stub-narrows-container-param).
        pytest.param(
            "from typing import overload\n"
            "@overload\ndef t(x: list[int32]) -> int32: ...\n"
            "@overload\ndef t(x: str) -> int32: ...\n"
            "def t(x: list[int32] | str) -> int32:\n    return 7\n"
            "def main() -> None:\n    ms = [5, 6]\n    print(t(ms))\n"
            "    ms.append(a64())\n",
            r"^'ms' holds int32 elements since line 12 \(passed as "
            r"list\[int32\]\), and this value is int64",
            id="typing-overload-then-wider"),
        # The constructor overload a local is passed to decides its element:
        # a later wider value is refused, as after a call to that one
        # constructor.
        pytest.param(
            "class K:\n    n: int32\n"
            "    @dispatch\n    def __init__(self, xs: list[int32]) -> None:\n"
            "        self.n = len(xs)\n"
            "    @dispatch\n    def __init__(self, s: str) -> None:\n"
            "        self.n = -1\n"
            "def main() -> None:\n    ms = [1, 2]\n    k = K(ms)\n"
            "    ms.append(a64())\n    print(k.n)\n",
            r"^'ms' holds int32 elements since line 13 \(passed as "
            r"list\[int32\]\), and this value is int64",
            id="ctor-group-then-wider"),
        # No constructor of the group may narrow the local's int32.
        pytest.param(
            "class K:\n    n: int32\n"
            "    @dispatch\n    def __init__(self, xs: list[int8]) -> None:\n"
            "        self.n = len(xs)\n"
            "    @dispatch\n    def __init__(self, s: str) -> None:\n"
            "        self.n = -1\n"
            "def main() -> None:\n    ms = [1, 2]\n    print(K(ms).n)\n",
            r"^No matching overload for 'K\(list\[int32\]\)'$",
            id="ctor-group-no-match"),
        # An empty container no candidate takes is named by its container.
        pytest.param(
            "@dispatch\ndef fn(x: int) -> str:\n    return 'int'\n"
            "@dispatch\ndef fn(x: str) -> str:\n    return 'str'\n"
            "def main() -> None:\n    print(fn([]))\n    print(fn(set()))\n",
            r"^No matching overload for fn\(list\)$",
            id="empty-container-no-match"),
    ],
)
def test_overload_paths_without_an_end_to_end_case(body: str,
                                                   message: str) -> None:
    with pytest.raises(SemanticError, match=message):
        Compiler.from_source(_SEMA_PRELUDE + body,
                             lib_dirs=[get_lib_dir() / "tpy"]).compile()
