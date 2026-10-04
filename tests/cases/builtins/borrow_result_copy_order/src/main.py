# A COPY result hoisted into a statement temporary (max over an iterator at a
# parameter the callee writes) is evaluated before the operands to its left:
# TPy prints 1009 where CPython prints 1100. Operand order is the postponed
# evaluation-order policy (BUGS.md#subexpression-right-to-left-eval); this
# case pins the current render. CPython's output differs, hence no_cpython.
from typing import Iterator


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def key_of(p: P) -> int:
    return p.v


def walk(ps: list[P]) -> Iterator[P]:
    for p in ps:
        yield p


def bump_ret(p: P) -> int:
    p.v += 1000
    return p.v


def set_first(ps: list[P]) -> int:
    ps[0].v = 100
    return 0


def main() -> None:
    ps = [P(3), P(9)]
    # the hoisted copy runs before set_first
    t = set_first(ps) + bump_ret(max(walk(ps), key=key_of))  # tpyc: warning(/copies P into argument 'p'/)
    print("order", t)


main()
