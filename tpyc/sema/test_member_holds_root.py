"""Whether a write through a member path writes its root
(`member_holds_root`), and how loans carry that fact: joined by OR where
either of two loans may be live (`combine_loan_info`, the walk over several
loans on one key), composed by AND where one loan is reached through
another (`retarget_storage_borrows`) -- each in both operand orders."""

import pytest

from ..thir.testutil import _compile, _entry
from ..typesys import TpyType
from .call_lends import key_holds_root, member_holds_root
from .context import (BorrowKind, BorrowTracker, LoanInfo, combine_loan_info,
                      holds_both, holds_either)
from .storage_walk import StorageKey

_SRC = """
from tpy import int32, Own, Ptr, readonly, take_ptr


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class P:
    cell: Cell
    kid: list[int32]
    ptr: Ptr[Cell]

    def __init__(self, c: Cell) -> None:
        self.cell = Cell(1)
        self.kid = [1]
        self.ptr = take_ptr(c)

    @property
    def borrowed(self) -> Cell:
        return self.cell

    @property
    def fresh(self) -> Own[Cell]:
        return Cell(self.cell.n)

    @property
    def count(self) -> int32:
        return self.cell.n

    @property
    def handle(self) -> Ptr[Cell]:
        return self.ptr

    @property
    def ro(self) -> readonly[Cell]:
        return self.cell


class Q(P):
    pass


class H:
    p: P

    def __init__(self, c: Cell) -> None:
        self.p = P(c)


def probe(p: P, o: P | None, q: Q, h: H) -> None:
    pass


def main() -> None:
    c = Cell(0)
    probe(P(c), None, Q(c), H(c))


main()
"""


Decls = tuple[object, dict[str, TpyType]]


@pytest.fixture(scope="module")
def decls() -> Decls:
    _, modules = _compile(_SRC)
    registry = _entry(modules).analyzer.registry
    fi = registry.get_function("probe")[0]
    return registry, {p.name: p.type for p in fi.params}


@pytest.mark.parametrize("member, holds", [
    ("cell", True),        # a record field is the root's storage
    ("kid", True),         # so is a container field
    ("ptr", False),        # a Ptr field points at storage elsewhere
    ("borrowed", True),    # a getter lending a writable reference
    ("fresh", False),      # a getter returning Own[...] hands out a new object
    ("count", False),      # a pointer-free value borrows nothing
    ("handle", False),     # a handle's referent is not the receiver
    ("ro", False),         # a readonly result grants no write
    ("nonexistent", True), # unknown holds: a lost const, never a build failure
])
def test_member_fact(decls: Decls, member: str, holds: bool) -> None:
    registry, types = decls
    assert member_holds_root(registry, types["p"], member) is holds


def test_narrowed_optional_root_is_its_record(decls: Decls) -> None:
    registry, types = decls
    assert member_holds_root(registry, types["o"], "cell") is True
    assert member_holds_root(registry, types["o"], "ptr") is False


def test_inherited_members(decls: Decls) -> None:
    registry, types = decls
    assert member_holds_root(registry, types["q"], "cell") is True
    assert member_holds_root(registry, types["q"], "fresh") is False


def test_unknown_owner_holds(decls: Decls) -> None:
    registry, _ = decls
    assert member_holds_root(registry, None, "cell") is True


def test_path_holds_only_when_every_hop_does(decls: Decls) -> None:
    registry, types = decls
    assert key_holds_root(registry, types["h"], ("p", "cell")) is True
    assert key_holds_root(registry, types["h"], ("p", "ptr")) is False
    assert key_holds_root(registry, types["h"], ("p", "fresh")) is False


def test_borrow_reaches_the_root_past_any_member_but_a_handle(
        decls: Decls) -> None:
    # A borrow (a returned reference) through a readonly or fresh getter
    # still comes from the root; only a handle field points elsewhere.
    registry, types = decls
    for member in ("ro", "fresh", "cell"):
        assert key_holds_root(registry, types["h"], ("p", member),
                              writes=False) is True
    assert key_holds_root(registry, types["h"], ("p", "ptr"),
                          writes=False) is False


@pytest.mark.parametrize("a, b, either, both", [
    (True, True, True, True),
    (True, False, True, False),
    (False, False, False, False),
    (None, True, True, None),
    (None, False, None, False),
    (None, None, None, None),
])
def test_join_and_compose_in_both_orders(a: bool | None, b: bool | None,
                                         either: bool | None,
                                         both: bool | None) -> None:
    assert holds_either(a, b) is either and holds_either(b, a) is either
    assert holds_both(a, b) is both and holds_both(b, a) is both


@pytest.mark.parametrize("a, b, merged", [
    (False, True, True),
    (False, False, False),
    (None, False, None),
])
def test_merged_loan_holds_when_either_leg_does(a: bool | None,
                                                b: bool | None,
                                                merged: bool | None) -> None:
    left = LoanInfo(BorrowKind.FIELD, holds_root=a)
    right = LoanInfo(BorrowKind.ELEMENT, holds_root=b)
    assert combine_loan_info(left, right).holds_root is merged
    assert combine_loan_info(right, left).holds_root is merged


_SNAP = StorageKey("s", ("snap",))


@pytest.mark.parametrize("up", [True, False])
def test_retargeted_alias_holds_only_when_its_source_does(up: bool) -> None:
    # `q = s.snap; r = q; q = ...`: r now borrows s.snap through q's loan.
    bt = BorrowTracker()
    bt.add_borrow(_SNAP, "q", BorrowKind.FIELD, holds_root=up)
    bt.add_borrow(StorageKey.of_name("q"), "r", BorrowKind.ALIAS)
    bt.retarget_storage_borrows("q")
    assert bt.loans[_SNAP]["r"].holds_root is up


def test_name_key_loan_holds_unless_told_otherwise() -> None:
    # A name key has no member to cross: its loan writes it unless the
    # entrance says not (a receiver the call grants no write through,
    # `grants_write` -- a readonly getter among them).
    bt = BorrowTracker()
    p = StorageKey.of_name("p")
    bt.add_borrow(p, "q", BorrowKind.ALIAS)
    bt.add_borrow(p, "r", BorrowKind.OPAQUE, holds_root=False)
    assert bt.loans[p]["q"].holds_root is True
    assert bt.loans[p]["r"].holds_root is False


@pytest.mark.parametrize("first", [True, False])
def test_walk_composes_hops(first: bool) -> None:
    # `r` borrows `q`'s storage through a loan that writes nothing of it:
    # a write through `r` stops there, whichever hop says so.
    bt = BorrowTracker()
    bt.add_borrow(StorageKey("p", ("cell",)), "q", BorrowKind.FIELD,
                  holds_root=first)
    bt.add_borrow(StorageKey.of_name("q"), "r", BorrowKind.OPAQUE,
                  holds_root=not first)
    [root] = bt.sited_storage_through_borrows("r")
    assert root.holds is False


@pytest.mark.parametrize("first", [True, False])
def test_walk_joins_the_loans_on_one_root(first: bool) -> None:
    # `r` may alias either `q1` (lends `p.cell` writable) or `q2` (does
    # not): a write through `r` writes `p`, whichever loan came first.
    bt = BorrowTracker()
    key = StorageKey("p", ("cell",))
    bt.add_borrow(key, "q1", BorrowKind.FIELD, holds_root=first)
    bt.add_borrow(key, "q2", BorrowKind.FIELD, holds_root=not first)
    bt.add_borrow(StorageKey.of_name("q1"), "r", BorrowKind.ALIAS)
    bt.add_borrow(StorageKey.of_name("q2"), "r", BorrowKind.ALIAS)
    [root] = bt.sited_storage_through_borrows("r")
    assert root.key == key and root.holds is True
