# A comprehension FILTER whose condition calls through a nested generic that
# needs an argument temp: the filter is a flush position, so the temp lands
# inside the loop body. It compiles and prints `3`.
from tpy import int32, Own


class Box[T]:
    val: T

    def __init__(self, val: Own[T]) -> None:
        self.val = val


def wrap[T](v: T) -> Own[Box[T]]:
    return Box[T](v)


def ok(b: Box[int32]) -> bool:
    return b.val > 0


def f(xs: list[int32]) -> int32:
    ys = [x for x in xs if ok(wrap(1))]
    return len(ys)


def main() -> None:
    print(f([1, 2, 3]))


main()
