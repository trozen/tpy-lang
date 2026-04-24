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
    MatchTier,
    OverloadAmbiguityError,
    _bool_checker_to_classifier,
    _classify_strict_match,
    _scalar_widening_cost,
    _score,
    _type_args_widening_cost,
)
from tpyc.sema.protocols import ProtocolConformanceKind
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
    # bit gap / 8: Int32 -> Int64 = (64-32)/8 = 4
    assert _scalar_widening_cost(INT32, INT64) == 4
    # Int16 -> Int32 = (32-16)/8 = 2
    assert _scalar_widening_cost(INT16, INT32) == 2
    # Int8 -> Int64 = 7; max(1, 7) + 0 sign penalty
    assert _scalar_widening_cost(INT8, INT64) == 7


def test_scalar_widening_cost_cross_sign_adds_penalty():
    # Int32 -> UInt32: gap 0, sign penalty 1, max(1, 0)+1 = 2.
    assert _scalar_widening_cost(INT32, UINT32) == 2
    # Int32 -> UInt64: gap 4, sign penalty 1 -> 5.
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
    # Float32 -> float (f64): generic "1" since both are floats.
    assert _scalar_widening_cost(FLOAT32, FLOAT) == 1


def test_scalar_widening_cost_int_literal_with_default_zero_cost():
    il = IntLiteralType(value=1)
    assert _scalar_widening_cost(il, INT32, default_int_type=INT32) == 0


def test_scalar_widening_cost_int_literal_uses_default_as_proxy():
    il = IntLiteralType(value=1)
    # IntLiteral -> Int64 under default=Int32 scores like Int32 -> Int64.
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
    assert _classify_strict_match(il, INT32) == (MatchTier.EXACT_CONCRETE, 0)
    # Value out of range for Int8 -> not a match.
    big = IntLiteralType(value=1000)
    assert _classify_strict_match(big, INT8) is None


def test_classify_strict_int_literal_unknown_value_matches_any_fixed_int():
    # value=None: compiler can't range-check, accept.
    il = IntLiteralType(value=None)
    assert _classify_strict_match(il, INT8) == (MatchTier.EXACT_CONCRETE, 0)
    assert _classify_strict_match(il, UINT64) == (MatchTier.EXACT_CONCRETE, 0)


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
    assert cost == 4  # Int32 -> Int64 widening


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
    assert "f(Int32)" in msg
    assert "f(Int64)" in msg
