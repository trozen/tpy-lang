# An operand at an operator dunder's pointer-variant union parameter
# (`Base | Mid`) lends its object in place, as the explicit call's argument
# does (a fresh one is error_union_landing_ptr_fresh_operand).
from typing import Protocol
from tpy import Own, ValueType, int32


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __lt__(self, other: "Base | Mid") -> bool:
        if isinstance(other, Mid):
            return self.n < other.n + 100
        return self.n < other.n

    def __gt__(self, other: "Base | Mid") -> bool:
        if isinstance(other, Mid):
            return self.n > other.n + 100
        return self.n > other.n


class Mid(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class Leaf(Mid):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class Acc:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    def __add__(self, other: Base | Mid) -> Own["Acc"]:
        out = Acc()
        if isinstance(other, Mid):
            out.total = self.total + other.n + 100
        else:
            out.total = self.total + other.n
        return out

    def __sub__(self, other: Base | Mid) -> int32:
        if isinstance(other, Mid):
            return other.n + 100
        return other.n

    def __radd__(self, other: Base | Mid) -> int32:
        if isinstance(other, Mid):
            return other.n + 100
        return other.n


def make_leaf() -> Own[Leaf]:
    return Leaf(9)


class HasN(Protocol):
    def get_n(self) -> int32: ...


class VBase(ValueType):
    n: int32

    # Takes the subclass so a converting construction would pick this, not
    # the slicing upcast CPython's object is.
    def __init__(self, src: HasN) -> None:
        self.n = src.get_n() + 1000

    def get_n(self) -> int32:
        return self.n


class VSub(VBase, ValueType):
    # Skips VBase.__init__, which would build from a subclass object.
    def __init__(self, n: int32) -> None:  # tpyc: warning(/does not call 'super/)
        self.n = n


class R:
    # Orders R after VSub, which its operator returns by value
    # (BUGS.md#friend-operator-returns-later-subclass).
    last: VSub

    def __init__(self) -> None:
        self.last = VSub(0)

    def __radd__(self, other: VBase | int32) -> VSub:
        return VSub(other.n + 10 if isinstance(other, VBase) else 0)


def fresh() -> None:
    # a fresh middle operand of a chained comparison is bound, then lent
    b = Base(5)
    end = Leaf(10)
    print("operands-chain-fresh", b < make_leaf() < end)  # tpyc: ok


def named(u: Base | Mid) -> None:
    a = Acc()
    mid = Mid(2)
    # an operand already of the `Mid` member
    print("operands-mid", a - mid)  # tpyc: ok
    leaf = Leaf(3)
    # in-place fallbacks to `__add__`: a named object, a union parameter
    a += leaf  # tpyc: warning(/falls back to '__add__'/)
    a += u  # tpyc: warning(/falls back to '__add__'/)
    print("operands-aug", a.total)
    # chained comparisons over pointer-variant operands
    b = Base(1)
    leaf2 = Leaf(7)
    print("operands-chain", b < leaf < leaf2)  # tpyc: ok
    # (its second pair runs `b.__gt__(u)`,
    # BUGS.md#union-left-operand-reflected-compare)
    print("operands-chain-union", b < u < b)  # tpyc: ok


def reflected_aug() -> None:
    v = VSub(5)
    # the reflected fallback hands `__radd__` the target as its base, as
    # the binary operator does, and the target keeps its object
    print("operands-radd", (v + R()).n)  # tpyc: ok
    v += R()  # tpyc: ok
    print("operands-radd-aug", v.n)


class R2:
    # Orders R2 after Mid, which its operator returns by value
    # (BUGS.md#friend-operator-returns-later-subclass).
    last: Mid

    def __init__(self) -> None:
        self.last = Mid(0)

    def __radd__(self, other: Base | Mid) -> Own[Mid]:
        if isinstance(other, Mid):
            return Mid(other.n + 10)
        return Mid(other.n + 20)


def reflected_aug_ptr() -> None:
    v = Mid(5)
    # the reflected fallback at a union of records lends the target as its
    # member, as the binary operator does
    print("operands-radd-ptr", (v + R2()).n)  # tpyc: ok
    # Mid has no `__iadd__`: the update is in place where CPython rebinds
    # `v`, which only an alias of `v` would see (union_landing_aug_alias)
    v += R2()  # tpyc: warning(/falls back to '__radd__'/)
    v.n += 1
    print("operands-radd-ptr-aug", v.n)


class R3:
    # Orders R3 after Leaf, which its operator returns by value
    # (BUGS.md#friend-operator-returns-later-subclass).
    last: Leaf

    def __init__(self) -> None:
        self.last = Leaf(0)

    def __radd__(self, other: Base | Mid) -> Own[Leaf]:
        if isinstance(other, Mid):
            return Leaf(other.n + 10)
        return Leaf(other.n + 20)


def reflected_aug_subclass() -> None:
    v = Leaf(5)
    # a subclass target lends itself as the `Mid` member
    v += R3()  # tpyc: warning(/falls back to '__radd__'/)
    print("operands-radd-subclass", v.n)


def main() -> None:
    fresh()
    named(Mid(20))
    reflected_aug()
    reflected_aug_ptr()
    reflected_aug_subclass()
