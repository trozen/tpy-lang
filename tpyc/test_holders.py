"""Loan holders: a binding is a name, the running iterator of one loop or
comprehension clause an `IterHolder`, and no spelling turns one into the
other."""

from copy import copy, deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from .holders import Holder, IterHolder
from .identity_map import IdentityMap
from .parse import TpyForEach, TpyName
from .sema.alias_rebind import _Replay
from .sema.context import (
    BorrowKind, BorrowTracker, IndexRelation, element_loan_mutation_warning)
from .sema.storage_walk import StorageKey
from .thir.testutil import _compile


def _filed(monkeypatch: pytest.MonkeyPatch, source: str) -> list[object]:
    """Every holder a compile of `source` files a loan for, in order."""
    holders: list[object] = []
    add = BorrowTracker.add_borrow

    def record(self: BorrowTracker, storage: StorageKey, borrower: Holder,
               *args: Any, **kwargs: Any) -> None:
        holders.append(borrower)
        return add(self, storage, borrower, *args, **kwargs)

    monkeypatch.setattr(BorrowTracker, "add_borrow", record)
    _compile(source)
    return holders


def _iters(holders: list[object]) -> list[IterHolder]:
    return list(dict.fromkeys(h for h in holders if isinstance(h, IterHolder)))


def test_holder_is_its_node() -> None:
    a = TpyForEach("x", TpyName("xs"), [])
    b = TpyForEach("x", TpyName("xs"), [])
    assert a == b  # structurally equal nodes ...
    assert IterHolder(a) != IterHolder(b)  # ... are two loops
    assert IterHolder(a) == IterHolder(a)
    assert hash(IterHolder(a)) == hash(IterHolder(a))
    assert IterHolder(a) != "x"
    h = IterHolder(a)
    assert copy(h) is h and deepcopy(h) is h
    assert deepcopy({h: 1})[IterHolder(a)] == 1
    with pytest.raises(AttributeError):
        h.node = b  # type: ignore[misc]


def test_nested_loops_hold_distinct_holders(
        monkeypatch: pytest.MonkeyPatch) -> None:
    iters = _iters(_filed(monkeypatch, (
        "def f(xs: list[list[int]]) -> None:\n"
        "    for row in xs:\n"
        "        for v in row:\n"
        "            print(v)\n"
        "f([[1]])\n")))
    assert len(iters) == 2
    assert all(isinstance(h.node, TpyForEach) for h in iters)
    assert [h.node.var for h in iters] == ["row", "v"]


def test_comprehension_holder_is_not_its_for(
        monkeypatch: pytest.MonkeyPatch) -> None:
    iters = _iters(_filed(monkeypatch, (
        "def f(xs: list[list[int]]) -> None:\n"
        "    for row in xs:\n"
        "        print([y for y in row])\n"
        "f([[1]])\n")))
    assert [type(h.node).__name__ for h in iters] == [
        "TpyForEach", "TpyComprehensionGenerator"]


@pytest.mark.parametrize("ident", ["__for_iter", "__for_iteral", "__escaped"])
def test_user_name_is_a_binding_holder(
        monkeypatch: pytest.MonkeyPatch, ident: str) -> None:
    holders = _filed(monkeypatch, (
        "class C:\n"
        "    def __init__(self, n: int) -> None:\n"
        "        self.n = n\n"
        "def f(xs: list[C]) -> None:\n"
        f"    {ident} = xs[0]\n"
        f"    print({ident}.n)\n"
        "f([C(1)])\n"))
    assert ident in holders
    assert not _iters(holders)


def test_locationless_loops_order_by_walk() -> None:
    warned: list[str] = []
    ctx = SimpleNamespace(warning=lambda msg, stmt: warned.append(msg))
    first = TpyForEach("x", TpyName("xs"), [], iter_text="first")
    second = TpyForEach("x", TpyName("ys"), [], iter_text="second")
    replay = SimpleNamespace(ctx=ctx, iter_ordinals=IdentityMap())
    # The replay numbers the loops as its walk binds their iterators; the
    # clobbered map's own order (second first) must not decide.
    first_h = _Replay.iter_holder(replay, first)
    second_h = _Replay.iter_holder(replay, second)
    site = SimpleNamespace(stmt=None, name="p", kept=frozenset())
    _Replay.warn(replay, site, None,
                 {second_h: BorrowKind.ITER, first_h: BorrowKind.ITER},
                 whole_object=True)
    assert len(warned) == 1 and "`first`" in warned[0]


_ROWS = StorageKey.of_name("rows")
_LOOP = TpyForEach("row", TpyName("rows"), [])


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "loans, holder, relation, wording",
    [
        # A certain hit by a named holder beats a possible hit by an
        # iterator: an unproven index may not be claimed as the iteration.
        ([(IterHolder(_LOOP), BorrowKind.ITER, ("name", "i")),
          ("r", BorrowKind.ELEMENT, ("lit", 0))],
         "r", IndexRelation.SAME,
         "Mutation of 'rows[...]' while borrowed ('append' may invalidate"
         " references)"),
        # At equal certainty the iterator's loan is the one reported.
        ([("r", BorrowKind.ELEMENT, ("lit", 0)),
          (IterHolder(_LOOP), BorrowKind.ITER, ("lit", 0))],
         IterHolder(_LOOP), IndexRelation.SAME,
         "Mutation of 'rows[...]' while iterating over it ('append'"
         " invalidates the iterator)"),
    ])
def test_element_hop_loan_picks_strongest(
        loans: list[tuple[Holder, BorrowKind, tuple[str, int | str]]],
        holder: Holder, relation: IndexRelation, wording: str,
        reverse: bool) -> None:
    bt = BorrowTracker()
    for h, kind, index in (reversed(loans) if reverse else loans):
        bt.add_borrow(_ROWS, h, kind, on_element=True, elem_index=index)

    def check() -> None:
        hit = bt.element_hop_loan(_ROWS, ("lit", 0))
        assert hit is not None
        assert hit.holder == holder and hit.relation is relation
        assert element_loan_mutation_warning(
            "rows[...]", "'append'", hit) == wording

    check()
    # A flow join rebuilds the loans from a frozenset, in no set order.
    bt.restore_from_frozen(bt.freeze())
    check()
