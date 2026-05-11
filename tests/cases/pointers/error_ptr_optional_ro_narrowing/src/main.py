# Regression guard for the readonly inner-narrowing direction of the
# `Ptr ↔ T | None` coercion. The compat rule allows `Ptr[T] -> readonly[T] | None`
# (adding const is safe) but must REJECT the converse `Ptr[readonly[T]] -> T | None`
# (dropping const is unsafe). An earlier symmetric helper accepted both
# directions silently; that regression was caught by /tpy-fuzzy-test.
from tpy import Ptr, readonly, take_ptr


class Point:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x


def consume_mutable(n: Point | None) -> None:
    if n is not None:
        print(n.x)


def main() -> None:
    p = Point(1)
    cp: Ptr[readonly[Point]] = take_ptr(p)
    consume_mutable(cp)  # tpyc: error(/expected Point.*got Ptr\[readonly\[Point\]\]/)


main()
