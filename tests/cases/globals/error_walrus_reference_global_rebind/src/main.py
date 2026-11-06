# A walrus rebinding a reference `global` from a function is refused, as its
# `=` twin is: the walrus is that statement.
from tpy import int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    a: A
    b: A

    def __init__(self) -> None:
        self.a = A(1)
        self.b = A(2)


h = H()
g = h.a


def f() -> None:
    global g
    # The rebind of the module's record variable is the subject.
    print((g := h.b).n)  # tpyc: error(/Cannot reassign global variable 'g'/)


f()
