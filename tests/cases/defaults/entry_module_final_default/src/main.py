# A materialized default naming a Final declared in THIS module must emit the
# bare name: qualifying it would spell the sema module id, which is `__main__`
# for the entry module where the C++ namespace is the structural name.
from typing import Final

from tpy import Int64

STEP: Final[Int64] = 42


def spaced(a: Int64, b: Int64 = STEP, *, c: Int64) -> Int64:
    return a * 10000 + b * 100 + c


class Holder:
    n: Int64

    def __init__(self, n: Int64 = STEP, *, tag: Int64) -> None:
        self.n = n * 10 + tag


def main() -> None:
    print(spaced(1, c=3))
    print(spaced(1, 7, c=3))
    print(Holder(tag=1).n)


main()
