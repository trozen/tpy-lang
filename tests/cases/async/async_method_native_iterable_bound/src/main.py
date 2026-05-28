# Async method on a class with a `NativeIterable`-bounded type param.
# Guards Fix B (record_type_param_bounds threaded to setup_body_scope):
# a non-suspending for-loop over `self.items: T` reads
# `current_type_param_bounds` from `_gen_for_each` and picks the
# begin/end peephole when the bound is NativeIterable. Without bounds,
# falls back to the universal `::tpy::__iter__` shape.
import asyncio
from tpy import Int32, NativeIterable


class Wrap[T: NativeIterable[Int32]]:
    items: T

    def __init__(self, items: T) -> None:
        self.items = items

    async def total(self) -> Int32:
        result: Int32 = 0
        for x in self.items:
            result += x
        return result


def main() -> None:
    xs: list[Int32] = [1, 2, 3, 4, 5]
    w = Wrap(xs)
    print(asyncio.run(w.total()))


main()
