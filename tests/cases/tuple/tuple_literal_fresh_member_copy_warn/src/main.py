# A fresh tuple LITERAL with a reference lvalue member, stored into an owned
# list element, copies that member where CPython aliases -- so it warns per
# member (fresh rvalue / copy() exempt). Output is read-only: the warning is
# the acknowledgment of the copy, and printing the alias would diverge from
# CPython (a const param / comprehension-loop-var member and a dict-value tuple
# literal hit separate pre-existing build/type errors -- see BUGS.md).
from tpy import Int32, copy


class P:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def list_member() -> None:
    c = P(5)
    xs: list[tuple[Int32, P]] = [(1, c)]  # tpyc: warning(/copies P into owned storage/)
    print(c.x, len(xs))


def exempt_fresh(n: Int32) -> None:
    xs: list[tuple[Int32, P]] = [(1, P(9))]  # tpyc: ok
    print(len(xs))


def exempt_copy() -> None:
    c = P(2)
    xs: list[tuple[Int32, P]] = [(1, copy(c))]  # tpyc: ok
    print(c.x, len(xs))


def main() -> None:
    list_member()
    exempt_fresh(0)
    exempt_copy()


main()
