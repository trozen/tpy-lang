# A reference-returning method call or a ternary of reference lvalues is a
# borrow-alias (CPython aliases it), not an owned value. Binding it onward --
# either through a plain alias chain or a tuple-literal unpack's hidden temp --
# must keep aliasing, not move out of the C& reference (which would corrupt the
# source). Each case mutates through the new binding and observes the original.
from tpy import Int32


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def get(self) -> "Counter":
        return self

    def bump(self) -> None:
        self.n += 1


def alias_chain() -> None:
    g = Counter(5)
    t = g.get()   # t aliases g
    y = t         # last use of t: must still alias, not move out of g
    y.bump()
    print(g.n)    # 6


def unpack_method_and_ternary() -> None:
    g0 = Counter(1)
    g1 = Counter(2)
    cond = True
    a, b = (g0 if cond else g1), g1.get()
    a.bump()
    b.bump()
    print(g0.n)   # 2 -- a aliases g0
    print(g1.n)   # 3 -- b aliases g1


def reassign_to_ref() -> None:
    g = Counter(10)
    x = Counter(99)   # owned
    x = g.get()       # aug/reassign to a borrow-alias: must not stay movable
    y = x
    y.bump()
    print(g.n)        # 11


alias_chain()
unpack_method_and_ternary()
reassign_to_ref()
