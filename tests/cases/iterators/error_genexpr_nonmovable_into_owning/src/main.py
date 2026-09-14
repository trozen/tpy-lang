# A generator expression whose source is an owning combinator (non-movable) fed
# to another lazy combinator, which would move it: a located reject, not a C++ build error.
from tpy import int32
from typing import Iterator


def gen() -> Iterator[int32]:
    yield 1
    yield 2


def total() -> int32:
    # `enumerate` moves the inner genexpr's closure into its own storage; the
    # closure holds `zip`'s owning flavor over two generator calls.
    return sum(i + s for i, s in enumerate(a + b for a, b in zip(gen(), gen())))  # tpyc: error(/genexpr.nonmovable_into_owning/)


def main() -> None:
    print(total())


main()
