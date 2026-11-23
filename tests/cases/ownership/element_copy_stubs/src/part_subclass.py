# A builtin container's subclass copies as its container base does: `.copy()`
# on a list / set subclass copies its elements, and a generic subclass's
# elements are its BASE's type arguments, not its own, and a subclass
# passing its own `T` copies what `T` is at the instantiation. A base-part
# call (`super().copy()`, `list.copy(self)`) copies as `self.copy()` does.
# The copies are silent divergences (CPython shares the elements,
# BUGS.md#list-copy-concat-elements-copy-unwarned), so only sizes print.
from tpy import int32, uint64, Own


class A:
    def __init__(self, v: int32) -> None:
        self.v = v

    def __hash__(self) -> uint64:
        return hash(self.v)

    def __eq__(self, o: "A") -> bool:
        return self.v == o.v


class LA(list[A]):
    def dup(self) -> Own[list[A]]:
        # base-part call: the receiver is `self`
        return super().copy()  # tpyc: ok

    def dup_base(self) -> Own[list[A]]:
        # base-part call through the base class: the receiver is `self`
        return list.copy(self)  # tpyc: ok


class SA(set[A]):
    pass


class TL[T](list[A]):
    pass


class TG[T](list[T]):
    pass


def main() -> None:
    xs = LA()
    xs.append(A(1))
    # list subclass receiver
    ys = xs.copy()  # tpyc: ok
    s = SA()
    s.add(A(2))
    # set subclass receiver
    t = s.copy()  # tpyc: ok
    g = TL[int32]()
    g.append(A(3))
    # generic subclass: its elements are `A`, its own `int32` argument is none
    h = g.copy()  # tpyc: ok
    print("subclass:", len(xs), len(ys), len(s), len(t), len(g), len(h))
    print("subclass super:", len(xs.dup()))
    print("subclass base call:", len(xs.dup_base()))
    ga = TG[A]()
    ga.append(A(4))
    # own `T` at a class: its elements are copied
    gb = ga.copy()  # tpyc: ok
    gi = TG[int32]()
    gi.append(5)
    # own `T` at a number: nothing to warn
    gj = gi.copy()  # tpyc: ok
    gb.append(A(6))
    gj.append(7)
    print("subclass own T:", len(ga), len(gb), len(gi), len(gj))
