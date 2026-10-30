"""Whether a call's mutation edge is lent (`MutationCallEdge.lent`: only
what the callee writes through the slot's elements reaches the caller): the
main edge only when every place reaching it is a handle, in either order the
roots are seen; an extra parameter edge decided for its own places alone.
The shapes are sema-level (a select mixing a frame with the iterator it
drains does not lower), so the edges are read off the recorder."""

import pytest

from ..parse.nodes import TpyFunction
from ..thir.testutil import _compile
from ..typesys import MutationCallEdge
from .calls import CallAnalyzer

_SRC = """
from typing import Iterator
from tpy import int32


def drain(src: Iterator[int32]) -> Iterator[int32]:
    for v in src:
        yield v


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return 0


def handle_then_direct(it: Iterator[int32], c: bool) -> int32:
    return first(drain(it) if c else it)


def direct_then_handle(it: Iterator[int32], c: bool) -> int32:
    return first(it if c else drain(it))


def two_handles(it: Iterator[int32], c: bool) -> int32:
    return first(drain(it) if c else drain(it))


def binding_handle_then_param(it: Iterator[int32], c: bool) -> int32:
    its = [it]
    for row in its:
        return first(drain(row) if c else it)
    return 0


def param_then_binding_handle(it: Iterator[int32], c: bool) -> int32:
    its = [it]
    for row in its:
        return first(it if c else drain(row))
    return 0


def binding_direct_param_handle(it: Iterator[int32], c: bool) -> int32:
    its = [drain(it)]
    for row in its:
        return first(row if c else drain(it))
    return 0


def main_handle_extra_direct(a: Iterator[int32], b: Iterator[int32],
                             c: bool) -> int32:
    return first(drain(a) if c else b)


def main_direct_extra_handle(a: Iterator[int32], b: Iterator[int32],
                             c: bool) -> int32:
    return first(a if c else drain(b))


def main() -> None:
    xs = [1, 2]
    print(handle_then_direct(iter(xs), True),
          direct_then_handle(iter(xs), True),
          two_handles(iter(xs), True),
          binding_handle_then_param(iter(xs), True),
          param_then_binding_handle(iter(xs), True),
          binding_direct_param_handle(iter(xs), True),
          main_handle_extra_direct(iter(xs), iter(xs), True),
          main_direct_extra_handle(iter(xs), iter(xs), True))


main()
"""


@pytest.fixture(scope="module")
def first_edges() -> dict[str, list[MutationCallEdge]]:
    """The edges each caller records for its call of `first`."""
    edges: dict[str, list[MutationCallEdge]] = {}
    orig = CallAnalyzer._record_mutation_call_edges

    def record(self: CallAnalyzer, *args: object, **kwargs: object) -> None:
        fn = self.ctx.func
        before = len(fn.current_call_edges)
        orig(self, *args, **kwargs)
        caller = fn.current_function
        if isinstance(caller, TpyFunction):
            edges.setdefault(caller.name, []).extend(
                e for e in fn.current_call_edges[before:]
                if e.callee_fi.name == "first")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(CallAnalyzer, "_record_mutation_call_edges", record)
        _compile(_SRC)
    return edges


def _lent_by_param(edges: list[MutationCallEdge]) -> dict[int, bool]:
    """Caller param -> whether the edge reaching it from `first`'s slot is
    lent."""
    out: dict[int, bool] = {}
    for e in edges:
        for slot, caller in e.param_map.items():
            assert caller not in out
            out[caller] = slot in e.lent
    return out


@pytest.mark.parametrize("caller", ["handle_then_direct",
                                    "direct_then_handle"])
def test_handle_and_direct_place_is_not_lent(
        first_edges: dict[str, list[MutationCallEdge]], caller: str) -> None:
    # `first` may write `it` through the direct arm, whatever arm comes first.
    assert _lent_by_param(first_edges[caller]) == {0: False}


def test_two_handle_places_are_lent(
        first_edges: dict[str, list[MutationCallEdge]]) -> None:
    assert _lent_by_param(first_edges["two_handles"]) == {0: True}


@pytest.mark.parametrize("caller", ["binding_handle_then_param",
                                    "param_then_binding_handle"])
def test_binding_handle_and_direct_param_is_not_lent(
        first_edges: dict[str, list[MutationCallEdge]], caller: str) -> None:
    # The main edge carries the loop binding and the param together: a
    # binding reached only across a handle does not make the direct `it` lent.
    (edge,) = first_edges[caller]
    assert edge.bindings and edge.param_map == {0: 0}
    assert edge.lent == frozenset()


def test_direct_binding_and_param_handle_is_not_lent(
        first_edges: dict[str, list[MutationCallEdge]]) -> None:
    (edge,) = first_edges["binding_direct_param_handle"]
    assert edge.bindings and edge.param_map == {0: 0}
    assert edge.lent == frozenset()


@pytest.mark.parametrize("caller, expected", [
    ("main_handle_extra_direct", {0: True, 1: False}),
    ("main_direct_extra_handle", {0: False, 1: True}),
])
def test_extra_param_edge_is_decided_on_its_own(
        first_edges: dict[str, list[MutationCallEdge]], caller: str,
        expected: dict[int, bool]) -> None:
    # Each caller param gets its own edge, lent as its own places are.
    assert len(first_edges[caller]) == 2
    assert _lent_by_param(first_edges[caller]) == expected
