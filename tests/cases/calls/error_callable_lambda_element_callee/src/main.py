# A lambda whose result is a call through a Ptr-returning Callable held in a
# list element is refused (rule R), and the message names the callee as
# written (`h.cbs[0]`), not as its `__call__`.
from typing import Callable
from tpy import int32, Ptr


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def lend(q: P) -> Ptr[P]:
    return q


class Holder:
    cbs: list[Callable[[P], Ptr[P]]]

    def __init__(self) -> None:
        self.cbs = [lend]


def use(k: Callable[[Holder, P], P]) -> None:
    print(k(Holder(), P(1)).n)


def main() -> None:
    # The subject: the callee is a subscript of a field.
    use(lambda h, q: h.cbs[0](q))  # tpyc: error(/a call through 'h.cbs\[0\]', whose signature cannot promise/)


main()
