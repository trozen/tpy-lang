# A generator expression over an `Iterator[Own[T]]` source must reject: the
# source hands each element over by value into a slot the next pull overwrites,
# and the genexpr yields it through the borrow slot (`val_or_ref<T>`). The
# "Not yet" line of docs/LANGUAGE_FEATURES.md's generator-expression entry
# documents the restriction.
from tpy import int32, Own
from typing import Iterator


class W:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def widgets(k: int32) -> Iterator[Own[W]]:
    for i in range(k):
        yield W(i)


def total(k: int32) -> int32:
    return sum(w.n for w in widgets(k))  # tpyc: error(/genexpr.owned_source/)


def main() -> None:
    print(total(3))


main()
