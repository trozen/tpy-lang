# A write through a readonly[tuple[Record, ...]] element is rejected: the
# tuple borrows its reference elements, so its readonly must be enforced.
from tpy import Int32, readonly


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def f(pair: readonly[tuple[Counter, Counter]]) -> None:
    pair[0].n = 999  # tpyc: error(/Cannot mutate readonly reference/)


def main() -> None:
    a = Counter(3)
    f((a, a))


main()
