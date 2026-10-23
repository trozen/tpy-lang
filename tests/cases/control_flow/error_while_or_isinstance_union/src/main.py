# A `while` head is analyzed in the loop-entry state: the body is entered from
# either operand's TRUE path of `c or isinstance(u, A)`, and the body rebinds
# `u`, so `u` is not proven an `A` there (CPython: AttributeError on pass 2).
from tpy import int32


class A:
    v: int32

    def __init__(self) -> None:
        self.v = 1


class B:
    w: int32

    def __init__(self) -> None:
        self.w = 2


def f(c: bool) -> int32:
    t = 0
    i = 0
    u: A | B = A()
    while c or isinstance(u, A):
        # The subject: the read of a field only A has.
        t += u.v  # tpyc: error(/Cannot access field 'v' on type A \| B/)
        u = B()
        i += 1
        c = i < 3
    return t


def main() -> None:
    print(f(True))


main()
