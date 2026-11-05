# A pointer-variant union local reseated from a field of a temporary is refused
# (BUGS.md#reseat-from-temporary-member-rejects): it would point into the temporary.
from tpy import int32


class A:
    def __init__(self, n: int32) -> None:
        self.n = n


class B:
    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    u: A | B

    def __init__(self, n: int32) -> None:
        self.u = A(n)


def f(h: H, w: A | B) -> None:
    v = w
    v = H(5).u  # tpyc: error(/ptr_union\.borrow_source/)
    if isinstance(v, A):
        v.n += 1
    print(isinstance(h.u, A))


def main() -> None:
    h = H(1)
    f(h, h.u)


main()
