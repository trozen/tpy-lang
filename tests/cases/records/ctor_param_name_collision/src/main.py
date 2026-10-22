# A ctor param sharing its name with an earlier function's union param must
# classify against the ctor's own bindings (leaked set -> uncompilable MIL).
from typing import Iterator
from tpy import int32, Own


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


def g(v: A | B) -> Iterator[bool]:
    yield isinstance(v, A)


class H:
    u: A | B
    n: int32

    def __init__(self, v: Own[A | B]):
        # The line under test: same-named `v` must NOT take the leaked
        # pointer-variant wrap.
        self.u = v  # tpyc: ok
        self.n = 0


def main() -> None:
    for b in g(A(1)):
        print(b)
    h = H(B(7))
    print(h.n)
    u = h.u
    if isinstance(u, B):
        print(u.y)


main()
