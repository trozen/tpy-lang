# Single-yield generators lower on the resumable frame like every other
# generator, so the shapes the retired single-yield emitter rejected are
# ordinary frame shapes: an `Own[container]` param as the for-head iterable,
# a module-global container as the iterable, and a generic generator method
# called at a VALUE-typed instantiation inside a while head (the frame copies
# the argument, so no temp needs hoisting there).
from typing import Iterator
from tpy import int32, Own

xs = [1, 2, 3]


class Labels[T]:
    def __init__(self, first: T) -> None:
        self.first = first

    def gen_it(self, v: T) -> Iterator[T]:
        yield self.first
        yield v


# Own[container] param as the for-head iterable
def drain(items: Own[list[int32]]) -> Iterator[int32]:  # tpyc: warning(/never consumed/)
    for x in items:
        yield x + len(items)


# module-global container as the iterable; an element of the global is
# overwritten between pulls, so the frame must iterate the global itself,
# not a copy
def over_global() -> Iterator[int32]:  # tpyc: ok
    for v in xs:
        yield v


# generic generator method at a value-typed instantiation in a while head
def value_instantiation_in_head() -> int32:
    labels = Labels[int32](1)
    n = 0
    while len(list(labels.gen_it(2))) > 0 and n < 2:  # tpyc: ok
        n += 1
    return n


def main() -> None:
    for u in drain([1, 2, 3]):
        print("own_container", u)
    total = 0
    for v in over_global():
        total = total + v
        if v == 1:
            xs[2] = 30
    print("over_global", total, xs[2])
    print("value_instantiation", value_instantiation_in_head())


main()
