# A mixed ternary receiver inside a lambda body: there is no statement in the
# lambda to hoist the fresh arm's slot to, so the body is refused
# (BUGS.md#reference-ternary-position-gaps).
from typing import Callable
from tpy import int32, Own


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def get(self) -> int32:
        return self.n


def make() -> Own[C]:
    return C(10)


def lam(a: C) -> None:
    f: Callable[[bool], int32] = lambda c: (a if c else make()).get()  # tpyc: error(/lambda\.body:expr\.ifexpr/)
    print(f(True), f(False))


def main() -> None:
    lam(C(1))


main()
