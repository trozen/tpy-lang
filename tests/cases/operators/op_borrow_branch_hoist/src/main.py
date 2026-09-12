# An operator-result borrow first-declared in BOTH branches and read after
# them still aliases its operand (source mutation visible through it).
# Regression guard: this is the one shape that hoists the declaration out
# of the branches, which used to pick a pointer form the const borrow
# couldn't bind (C++ build failure); straight-line and single-branch
# bindings don't hoist and never tripped it.
from tpy import int32


class Acc:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __add__(self, o: "Acc") -> "Acc":
        return self if self.n >= o.n else o

    def __neg__(self) -> "Acc":
        return self


def pick(flag: bool):
    a = Acc(3)
    b = Acc(7)
    if flag:
        c = a + b
    else:
        c = -b
    print(c.n)
    a.n = 9
    b.n = 11
    print(c.n)


def main():
    pick(True)
    pick(False)


main()
