# A bare `self` arm in a record ternary is a VALUE position, so the receiver
# pointer derefs (`(*this)`) and the ternary binds an ALIAS, as in CPython.
from tpy import int32, readonly


class Acc:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def bump_larger(self, o: "Acc") -> None:
        c = self if self.n >= o.n else o  # tpyc: ok -- self in the THEN arm
        c.n += 100

    def bump_smaller(self, o: "Acc") -> None:
        c = o if o.n >= self.n else self  # tpyc: ok -- self in the ELSE arm
        c.n += 100

    @readonly
    def larger_n(self, o: readonly["Acc"]) -> int32:
        c = self if self.n >= o.n else o  # tpyc: ok -- const receiver arm
        return c.n


def test_then_arm_aliases_self():
    a = Acc(3)
    b = Acc(1)
    a.bump_larger(b)
    print(a.n)  # 103 -- a copy would print 3
    print(b.n)


def test_then_arm_aliases_other():
    a = Acc(1)
    b = Acc(5)
    a.bump_larger(b)
    print(a.n)
    print(b.n)  # 105


def test_else_arm_aliases_self():
    a = Acc(9)
    b = Acc(2)
    a.bump_smaller(b)
    print(a.n)  # 109 -- a copy would print 9
    print(b.n)


def test_readonly_arm():
    a = Acc(7)
    b = Acc(4)
    print(a.larger_n(b))
    print(b.larger_n(a))


def main():
    test_then_arm_aliases_self()
    test_then_arm_aliases_other()
    test_else_arm_aliases_self()
    test_readonly_arm()


main()
