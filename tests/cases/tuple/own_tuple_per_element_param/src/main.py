# Canonical per-element form `tuple[Own[T], ...]` as a function param.
# This is what sema lowers `Own[tuple[T, ...]]` to internally; users may
# also write it directly. The per-element ownership check at the call
# boundary applies to both spellings.
#
# This test pins the happy paths: last-use rvalues / fresh constructors
# pass through cleanly. The sibling error_tuple_per_element_own_param_*
# tests pin the borrowed-source diagnostic.
from tpy import int32, Own, copy


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def take(t: tuple[Own[P], Own[P]]) -> int32:
    a, b = t
    return a.x + b.x


def take_opt(t: tuple[Own[P] | None, Own[P] | None]) -> int32:
    a, b = t
    if a is not None and b is not None:
        return a.x + b.x
    if a is not None:
        return a.x
    return int32(0)


def test_record_elements_last_use() -> None:
    a = P(1)
    b = P(2)
    print(take((a, b)))


def test_record_elements_fresh_rvalues() -> None:
    print(take((P(3), P(4))))


def test_record_elements_explicit_copy() -> None:
    keep = P(5)
    print(take((copy(keep), P(6))))
    print(keep.x)


def test_optional_elements_last_use() -> None:
    a = P(7)
    b = P(8)
    print(take_opt((a, b)))


def test_optional_elements_with_none() -> None:
    a = P(9)
    print(take_opt((a, None)))
    print(take_opt((None, None)))


def main() -> None:
    test_record_elements_last_use()
    test_record_elements_fresh_rvalues()
    test_record_elements_explicit_copy()
    test_optional_elements_last_use()
    test_optional_elements_with_none()


main()
