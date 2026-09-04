# The adjacent shape for a compound narrowed condition: facts on TWO subjects.
# Each would need its own extraction alias at branch entry, and the branch-entry
# extraction here installs one, so the wider fact map rejects.
from tpy import Int32, ValueType


class A(ValueType):
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class B(ValueType):
    m: Int32

    def __init__(self, m: Int32) -> None:
        self.m = m


def two(u: A | B, v: A | B) -> Int32:
    if not isinstance(u, A) and not isinstance(v, A):  # tpyc: error(/not yet supported.*if.cond_facts_unmirrored/)
        return u.m + v.m
    return 0


def main() -> None:
    print(two(B(1), B(2)))


main()
