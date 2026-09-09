# docs/LANGUAGE_FEATURES.md: "In a position with no statement to hoist a needed
# temp into ... the call is rejected" -- here the need is a GENERATOR factory's,
# whose frame may borrow the slot, at a value-typed instantiation.
from typing import Iterator

from tpy import Int32


class Labels[T]:
    def __init__(self, first: T) -> None:
        self.first = first

    def gen_it(self, v: T) -> Iterator[T]:
        yield self.first
        yield v


def main() -> None:
    labels = Labels[Int32](1)
    n = 0
    # The subject: the generator factory's temp cannot be hoisted here.
    while len(list(labels.gen_it(2))) > 0 and n < 1:  # tpyc: error(/not yet supported/)
        n += 1
    print(n)


main()
