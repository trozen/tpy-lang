"""The frame field order: stable, pulled only by edges, rejecting what it
cannot lay out."""
import pytest

from tpyc.codegen_cpp.context import CodeGenError
from tpyc.codegen_cpp.frame_fields import FrameField, order_frame_fields


def names(fields):
    return [f.name for f in fields]


def test_no_edges_keeps_the_given_order():
    fields = [FrameField("a", ""), FrameField("b", ""), FrameField("c", "")]
    assert names(order_frame_fields(fields)) == ["a", "b", "c"]


def test_lifetime_edge_orders_only_a_field_whose_drop_runs_code():
    # A pointer's borrows constrain nothing; a generator's do.
    ptr = FrameField("p", "", borrows=frozenset({"slot"}))
    gen = FrameField("g", "", borrows=frozenset({"slot"}),
                     drop_runs_user_code=True)
    slot = FrameField("slot", "")
    assert names(order_frame_fields([ptr, gen, slot])) == ["p", "slot", "g"]


def test_spelling_pull_brings_what_the_pulled_field_borrows_transitively():
    alias = FrameField("A", "", spelled_from=frozenset({"g"}), is_alias=True)
    x = FrameField("x", "", borrows=frozenset({"s1"}))
    s1 = FrameField("s1", "")
    g = FrameField("g", "", borrows=frozenset({"x"}),
                   drop_runs_user_code=True)
    assert names(order_frame_fields([x, alias, s1, g])) == [
        "x", "s1", "g", "A"]


def test_untraced_generator_pulled_by_a_spelling_edge_rejects():
    alias = FrameField("A", "", spelled_from=frozenset({"g"}), is_alias=True)
    g = FrameField("g", "", drop_runs_user_code=True, borrows_untraced=True,
                   local="g")
    with pytest.raises(CodeGenError, match="what it borrows cannot be traced"):
        order_frame_fields([alias, FrameField("w", ""), g])


def test_lifetime_edge_to_an_untraced_field_is_not_followed():
    src = FrameField("src", "", borrows=frozenset({"g"}),
                     drop_runs_user_code=True)
    g = FrameField("g", "", drop_runs_user_code=True, borrows_untraced=True)
    assert names(order_frame_fields([src, g])) == ["src", "g"]


def test_cycle_through_an_alias_keeps_the_alias_diagnostic():
    alias = FrameField("A", "", spelled_from=frozenset({"g"}), is_alias=True)
    v = FrameField("v", "", spelled_from=frozenset({"A"}))
    g = FrameField("g", "", borrows=frozenset({"v"}),
                   drop_runs_user_code=True)
    with pytest.raises(CodeGenError, match="reads the variable the loop"):
        order_frame_fields([v, alias, g])
