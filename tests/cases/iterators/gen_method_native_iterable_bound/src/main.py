# Generator method on a class with a `NativeIterable`-bounded type param.
# The non-suspending for-loop is what reads `current_type_param_bounds`
# and picks the begin/end peephole; without Fix B threading bounds,
# falls back to the universal `::tpy::__iter__` shape.
from typing import Iterator
from tpy import Int32, NativeIterable


class Wrap[T: NativeIterable[Int32]]:
    items: T

    def __init__(self, items: T) -> None:
        self.items = items

    def summary(self) -> Iterator[Int32]:
        total: Int32 = 0
        count: Int32 = 0
        for x in self.items:
            total += x
            count += 1
        yield total
        yield count


def main() -> None:
    xs: list[Int32] = [1, 2, 3, 4, 5]
    w = Wrap(xs)
    for v in w.summary():
        print(v)


main()
