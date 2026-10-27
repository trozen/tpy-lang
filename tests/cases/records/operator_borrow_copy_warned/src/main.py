# An operator whose dunder hands back a borrow of its operand's storage in
# the pointer form (`-> A | None`, `-> A | B`) is that dunder's call, no new
# value: at an `Own` return it copies, warned as the `-> A` twin does. The
# copy is the warned divergence from CPython, so no output observes it.
from tpy import Own, int32


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


class B:
    def __init__(self, w: int32) -> None:
        self.w = w


class H:
    a: A

    def __init__(self) -> None:
        self.a = A(1)

    def __neg__(self) -> A | None:
        return self.a

    def __pos__(self) -> A | B:
        return self.a

    def __invert__(self) -> A:
        return self.a


# optional: the Optional borrow copies into the owned result
def neg(h: H) -> Own[A | None]:
    return -h  # tpyc: warning(/copies A \| None into owned storage/)


# union: the union borrow copies into the owned result
def pos(h: H) -> Own[A | B]:
    return +h  # tpyc: warning(/copies A \| B into owned storage/)


# reference: the plain-reference twin
def inv(h: H) -> Own[A]:
    return ~h  # tpyc: warning(/copies A into owned storage/)


def main() -> None:
    h = H()
    x = neg(h)
    if x is not None:
        print("optional", x.v)
    y = pos(h)
    if isinstance(y, A):
        print("union", y.v)
    print("reference", inv(h).v)


main()
