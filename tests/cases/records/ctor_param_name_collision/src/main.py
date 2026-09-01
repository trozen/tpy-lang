# A ctor param sharing its name with an earlier function's union param must
# classify against the ctor's own bindings (leaked set -> uncompilable MIL).
from typing import Iterator
from tpy import Int32, Own, copy


class A:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class B:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


def g(v: A | B) -> Iterator[bool]:
    yield isinstance(v, A)


class H:
    u: A | B
    n: Int32

    # Known sema false positive: the field-consumption check does not
    # credit copy() at a field store, though the MIL genuinely moves v.
    def __init__(self, v: Own[A | B]):  # tpyc: warning(/never consumed/)
        # The line under test: same-named `v` must NOT take the leaked
        # pointer-variant wrap. copy() is only here to silence a second
        # false positive -- the bare store warns "copies A | B into field"
        # although the emit moves it; the render is identical either way
        # (BUGS.md#own-union-field-store-copy-warning).
        self.u = copy(v)  # tpyc: ok
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
