# A walrus that rebinds an owning for target (a reference local) is refused
# like any walrus rebind of a non-value local
# (BUGS.md#walrus-rebind-nonvalue-local-rejects).
from typing import Iterator
from tpy import int32, Own


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def steps() -> Iterator[Own[P]]:
    yield P(1)


def main() -> None:
    for s in steps():
        if (s := P(2)).n > 0:  # tpyc: error(/walrus reassignment of non-value local 's'/)
            print(s.n)


main()
