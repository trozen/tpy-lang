# The ELSE-side twin of error_if_cond_facts_two_subjects: the negated condition
# puts the narrowing facts on the else branch, where each of TWO subjects would
# need its own entry extraction, so the wider fact map rejects there too.
from tpy import Int32, ValueType


class A(ValueType):
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class B(ValueType):
    m: Int32

    def __init__(self, m: Int32) -> None:
        self.m = m


def both(u: A | B, v: A | B) -> Int32:
    if not (isinstance(u, A) and isinstance(v, A)):  # tpyc: error(/not yet supported.*if.cond_facts_unmirrored/)
        return 0
    else:
        return u.n + v.n


def main() -> None:
    print(both(A(1), A(2)))


main()
