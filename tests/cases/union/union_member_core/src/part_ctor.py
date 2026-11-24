# A subclass whose base declares a converting constructor taking it: the
# member value hands over its base part, never running that constructor --
# at a pointer variant (the address of the base part) and at an owned
# result (the base part moved out), as a base local's own initialization.
from tpy import Own, int32


class Base:
    n: int32

    def __init__(self, src: "Sub") -> None:
        self.n = src.n + 1000


class Sub(Base):
    # the subclass sets its fields itself
    def __init__(self, n: int32) -> None:  # tpyc: warning(/does not call/)
        self.n = n


def peek(x: Base | int32) -> int32:
    if isinstance(x, Base):
        return x.n
    return x


def peek_twin(x: Base) -> int32:
    return x.n


# (the twin `-> Own[Base]` is refused by sema,
# BUGS.md#own-base-subclass-check-bare-record-only)
def own_union(n: int32) -> Own[Base | int32]:
    # the base member keeps only the base part
    return Sub(n)  # tpyc: warning(/upcast narrows/)


def main() -> None:
    print("ctor-ptr", peek(Sub(5)), peek_twin(Sub(5)))  # tpyc: ok
    o = own_union(6)
    # a base local keeps only the base part
    b: Base = Sub(6)  # tpyc: warning(/upcast narrows/)
    if isinstance(o, Base):
        print("ctor-own", o.n, b.n)
