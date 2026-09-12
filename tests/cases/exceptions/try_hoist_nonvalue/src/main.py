# Try-body variables hoisted when all except handlers terminate,
# so they survive the C++ try{} block scope.
from tpy import int32


class Point:
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


def test_list_try_hoist() -> None:
    try:
        items: list[int32] = [1, 2, 3]
    except Exception:
        return
    print(items)


def test_record_try_hoist() -> None:
    try:
        p = Point(10, 20)
    except Exception:
        return
    print(p.x, p.y)


def test_value_type_try_hoist() -> None:
    try:
        x: int32 = 42
    except Exception:
        return
    print(x)


def main() -> None:
    test_list_try_hoist()
    test_record_try_hoist()
    test_value_type_try_hoist()


main()
