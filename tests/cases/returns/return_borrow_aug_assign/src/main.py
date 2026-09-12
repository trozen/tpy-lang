# 8b + aug-assign: borrow registered via function return triggers aug-assign warning.
from tpy import int32


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


def get_first(items: list[Point]) -> Point:
    return items[0]  # return_borrows_from = {0}


def get_list(items: list[Point]) -> list[Point]:
    return items  # return_borrows_from = {0}


def test_aug_assign_warns() -> None:
    """ELEMENT borrow from function return: aug-assign on the source warns."""
    data = [Point(1, 2), Point(3, 4)]
    first = get_first(data)        # 8b: ELEMENT borrow on data
    data += [Point(5, 6)]          # tpyc: warning(/Mutation of 'data'.*'\+='/)
    print(len(data))               # 3


def test_iter_aug_assign_warns() -> None:
    """ITER borrow via 8b (for over returned list): aug-assign in body warns."""
    data = [Point(1, 2), Point(3, 4)]
    for p in get_list(data):
        data += [Point(5, 6)]      # tpyc: warning(/Mutation of 'data'.*'\+=' invalidates the iterator/)
        break
    print(len(data))               # 3


def test_no_borrow_no_warn() -> None:
    """No borrow active -- aug-assign is fine."""
    data: list[Point] = [Point(1, 2), Point(3, 4)]
    data += [Point(5, 6)]          # tpyc: ok
    print(len(data))               # 3


def test_borrow_cleared_no_warn() -> None:
    """Overwriting the borrower clears the borrow; aug-assign is fine after."""
    data = [Point(1, 2), Point(3, 4)]
    first = get_first(data)        # borrows data
    first = Point(9, 9)            # clears borrow
    data += [Point(5, 6)]          # tpyc: ok
    print(len(data))               # 3


def main() -> None:
    test_aug_assign_warns()
    test_iter_aug_assign_warns()
    test_no_borrow_no_warn()
    test_borrow_cleared_no_warn()


main()
