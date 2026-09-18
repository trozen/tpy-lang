# The body-path half of the rule: a provable head is not enough -- the binding
# must also be on every path through the body. `range(2)` provably runs, but
# `q` is bound only under the `if`, so the read after the loop rejects (CPython
# raises UnboundLocalError on the same program).
from tpy import int32


class Pic:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def guarded(flag: bool) -> int32:
    for i in range(2):
        if flag:
            q = Pic(i)
    return q.n  # tpyc: error(/variable 'q' may not be assigned at this point/)


def main() -> None:
    print(guarded(False))


main()
