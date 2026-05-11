# User-defined function with Own[tuple[T | None, ...]] param.
#
# Per-element ownership at the call boundary:
#  - Last-use rvalue / fresh constructor / explicit copy() / None: OK,
#    each element is moved into the storage-form param via
#    tuple_to_storage_move (no silent copy of caller's lvalue).
#  - Borrowed lvalue at non-last-use: rejected with a clean diagnostic
#    that points the user at copy() (see sibling error_* test).
#
# Storage-form sources (e.g. list[tuple[...]] elements via subscript) keep
# today's copy semantic via tuple_to_pointer -- the container retains
# ownership of its elements and the call can't move out.
from tpy import Int32, Own, copy


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def take(t: Own[tuple[P | None, P | None]]) -> Int32:
    a, b = t
    if a is not None and b is not None:
        return a.x + b.x
    if a is not None:
        return a.x
    return Int32(0)


def take_subscript(t: Own[tuple[P | None, P | None]]) -> Int32:
    first = t[0]
    if first is not None:
        return first.x
    return Int32(0)


def test_all_last_use() -> None:
    # a, b are at last use; both moved into the storage-form param.
    a = P(1)
    b = P(2)
    print(take((a, b)))


def test_mixed_last_use_and_explicit_copy() -> None:
    # c at last use, d copied explicitly, then d at its true last use.
    # All elements are movable into the storage form (copy() returns Own[P]).
    c = P(3)
    d = P(4)
    print(take((c, copy(d))))
    print(take((d, None)))


def test_fresh_constructor_literals() -> None:
    # Rvalue elements, naturally movable.
    print(take((P(5), P(6))))


def test_none_only() -> None:
    print(take((None, None)))


def test_storage_form_source() -> None:
    # pairs[0] is a storage-form source -- tuple_to_pointer lift, no
    # per-element move because the list still owns its elements.
    p1 = P(7)
    p2 = P(8)
    pairs: list[tuple[P | None, P | None]] = [(p1, p2)]
    print(take(pairs[0]))


def test_subscript_access_in_body() -> None:
    # Confirms t[0] reads the storage-form slot correctly when the param
    # was constructed via tuple_to_storage_move.
    e = P(9)
    f = P(10)
    print(take_subscript((e, f)))


def main() -> None:
    test_all_last_use()
    test_mixed_last_use_and_explicit_copy()
    test_fresh_constructor_literals()
    test_none_only()
    test_storage_form_source()
    test_subscript_access_in_body()


main()
