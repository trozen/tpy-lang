# A lambda stored into a field by a method: the closure would copy its
# captures silently (BUGS.md#callable-local-lambda-copies-capture-silently).
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    getter: Callable[[], int32]

    def __init__(self) -> None:
        self.getter = lambda: 0

    def install(self, p: P) -> None:
        # The lambda captures the param `p`.
        self.getter = lambda: p.n  # tpyc: error(/expr\.lambda/)


def main() -> None:
    h = H()
    q = P(1)
    h.install(q)
    q.n = 5
    g = h.getter
    print(g())


main()
