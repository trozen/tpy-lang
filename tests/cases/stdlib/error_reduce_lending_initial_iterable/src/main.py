# Error: the 3-argument lending `reduce` (a combiner returning one of its
# arguments) takes a list: a generator's step does not outlive the loop, so
# its result could not lend it. Bind `list(...)` to a local first.
from functools import reduce
from typing import Iterator
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bigger(a: P, b: P) -> P:
    return a if a.n >= b.n else b


def gen_ps() -> Iterator[P]:
    yield P(1)
    yield P(3)


def main() -> None:
    p0 = P(2)
    # The subject: a lending combiner over a generator with an initial value.
    print(reduce(bigger, gen_ps(), p0).n)  # tpyc: error(/No matching overload for 'reduce'/)


main()
