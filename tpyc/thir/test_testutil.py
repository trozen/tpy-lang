"""Units for the reject-matching helpers in testutil.py.

They exist so a boundary pin cannot go vacuous when a gate starts recording
its blocking shape -- an unpinned guard moves that failure mode one level up,
where nothing at all watches it.
"""

from __future__ import annotations

import pytest

from .testutil import _assert_rejects_at, _rejects_at


class TestRejectsAt:
    def test_matches_the_bare_landmark(self):
        assert _rejects_at({"body:expr.call": 1}, "body:expr.call")

    def test_matches_a_shape_suffixed_landmark(self):
        assert _rejects_at({"body:expr.call:call.arg_shape.union": 1},
                           "body:expr.call")

    def test_does_not_match_an_absent_landmark(self):
        assert not _rejects_at({"body:expr.method_call:method.arg_shape": 1},
                               "body:expr.call")

    def test_does_not_match_a_longer_sibling_landmark(self):
        # The `:` boundary is the whole point: `expr.call` must not answer for
        # a distinct `expr.call_thing` landmark that merely shares a prefix.
        assert not _rejects_at({"body:expr.call_thing": 1}, "body:expr.call")

    def test_matches_a_bare_reason_without_a_position_prefix(self):
        assert _rejects_at(["expr.method_call:method.ret_type"],
                           "expr.method_call")


class TestAssertRejectsAt:
    def test_accepts_the_bare_landmark(self):
        _assert_rejects_at({"body:expr.call": 1}, "body:expr.call")

    def test_accepts_a_shape_suffixed_landmark(self):
        _assert_rejects_at({"body:expr.call:call.arg_shape.span": 1},
                           "body:expr.call")

    def test_fails_when_the_landmark_is_absent(self):
        with pytest.raises(AssertionError):
            _assert_rejects_at({"body:stmt.return:return.slot_type": 1},
                               "body:expr.call")

    def test_fails_on_an_empty_tally(self):
        # The routing case: nothing fell back, so every boundary claim is void.
        with pytest.raises(AssertionError):
            _assert_rejects_at({}, "body:expr.call")

    def test_accepts_the_named_shape(self):
        _assert_rejects_at({"body:expr.call:call.arg_shape.span": 1},
                           "body:expr.call", "call.arg_shape.span")

    def test_fails_on_a_different_shape_at_the_same_landmark(self):
        with pytest.raises(AssertionError):
            _assert_rejects_at({"body:expr.call:call.arg_shape.union": 1},
                               "body:expr.call", "call.arg_shape.span")

    def test_fails_when_the_shape_is_named_but_the_reject_is_bare(self):
        with pytest.raises(AssertionError):
            _assert_rejects_at({"body:expr.call": 1},
                               "body:expr.call", "call.arg_shape.span")

    def test_fails_when_a_second_shape_shares_the_landmark(self):
        with pytest.raises(AssertionError):
            _assert_rejects_at({"body:expr.call:call.arg_shape.span": 1,
                                "body:expr.call:call.arg_shape.union": 1},
                               "body:expr.call", "call.arg_shape.span")

    def test_ignores_rejects_at_other_landmarks(self):
        _assert_rejects_at({"body:expr.call:call.ret_type.tuple": 1,
                            "body:stmt.return:return.slot_type": 1},
                           "body:expr.call", "call.ret_type.tuple")

    def test_accepts_the_matching_count(self):
        _assert_rejects_at({"final_global:expr.call:call.type_ctor": 1},
                           "final_global:expr.call", count=1)

    def test_fails_on_a_mismatched_count(self):
        with pytest.raises(AssertionError):
            _assert_rejects_at({"body:expr.call:call.arg_shape.span": 2},
                               "body:expr.call", count=1)

    def test_counts_across_shapes_sharing_the_landmark(self):
        _assert_rejects_at({"body:expr.call:call.arg_shape.span": 1,
                            "body:expr.call:call.arg_shape.union": 2},
                           "body:expr.call", count=3)

    def test_accepts_a_plain_collection_of_reasons(self):
        _assert_rejects_at({"call.inst_shape", "expr.call:call.inst_shape"},
                           "expr.call", "call.inst_shape", count=1)

    def test_accepts_a_single_reason_string(self):
        # A lone reason must not be iterated character by character.
        _assert_rejects_at("expr.call:call.expr_callee_shape", "expr.call",
                           "call.expr_callee_shape")

    def test_fails_on_a_single_reason_string_at_another_landmark(self):
        with pytest.raises(AssertionError):
            _assert_rejects_at("expr.method_call:method.ret_type", "expr.call")
