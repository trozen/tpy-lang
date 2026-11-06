# A nested def rebinding, through `nonlocal`, an enclosing local that refers
# to an object it does not own is refused, a fresh object included
# (BUGS.md#nonlocal-rebind-overwrites-in-place).
from tpy import int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    a: A

    def __init__(self) -> None:
        self.a = A(1)


def f(h: H) -> None:
    x = h.a

    def g() -> None:
        nonlocal x
        # The rebind of the borrowed local to a fresh object is the subject.
        print((x := A(9)).n)  # tpyc: error(/rebinding nonlocal 'x' is not supported yet: the write would replace the existing object rather than rebind the name/)
    g()
    x.n += 100
    print(h.a.n, x.n)


f(H())
