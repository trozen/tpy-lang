# Non-value types declared inside branches use std::optional<T> for hoisting
# (no unnecessary default construction or pointer indirection).
from typing import Optional
from tpy import int32, Own


class Point:
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


def make_list() -> Own[list[int32]]:
    return [1, 2, 3]


def test_list_one_branch(flag: bool) -> None:
    if flag:
        items: list[int32] = [10, 20, 30]
    else:
        return
    print(items)


def test_own_list_both_branches(flag: bool) -> None:
    if flag:
        items = make_list()
    else:
        items = [4, 5, 6]
    print(items)


def test_list_reassigned_mixed(flag: bool) -> None:
    base: list[int32] = [10, 20]
    if flag:
        items: list[int32] = [1, 2, 3]
    else:
        items = base
    items.append(99)
    print(items)


def test_record_one_branch(flag: bool) -> None:
    if flag:
        p = Point(1, 2)
    else:
        return
    print(p.x, p.y)


def test_record_both_branches(flag: bool) -> None:
    if flag:
        p = Point(1, 2)
    else:
        p = Point(3, 4)
    print(p.x, p.y)


def test_optional_record_one_branch(flag: bool) -> None:
    if flag:
        p: Optional[Point] = Point(5, 6)
    else:
        return
    if p is not None:
        print(p.x, p.y)


def main() -> None:
    test_list_one_branch(True)
    test_own_list_both_branches(True)
    test_own_list_both_branches(False)
    test_list_reassigned_mixed(True)
    test_list_reassigned_mixed(False)
    test_record_one_branch(True)
    test_record_both_branches(True)
    test_record_both_branches(False)
    test_optional_record_one_branch(True)


main()
