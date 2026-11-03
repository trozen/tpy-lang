# A generic record over a tuple LITERAL holding a Ptr still rejects: the tuple
# literal with a Ptr element does not lower anywhere (BUGS.md#ptr-tuple-element-rejects).
from tpy import Ptr


class A:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class H[T]:
    p: T

    def __init__(self, p: T) -> None:
        self.p = p


def main() -> None:
    a = A(1)
    q: Ptr[A] = a
    h = H((q, 1))  # tpyc: error(/not yet supported by C\+\+ code generation/)
    print(h.p[1])


main()
