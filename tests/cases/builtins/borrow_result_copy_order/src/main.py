# A COPY result (max over an iterator) passed inline to a parameter the callee
# writes: the callee writes the copy. The `+` operands run in the C++
# compiler's order (BUGS.md#subexpression-right-to-left-eval), so this case
# pins the render and prints nothing order-dependent. CPython's output differs
# (the callee writes the list element itself), hence no_cpython.
# The keyword section pins the copy warning at the parameter a keyword binds.
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


def bump_kw(*, p: P) -> None:
    p.v += 1000


def main() -> None:
    ps = [P(3), P(9)]
    # the copy is passed inline; which operand of `+` runs first is the compiler's
    t = set_first(ps) + bump_ret(max(walk(ps), key=key_of))  # tpyc: warning(/copies P into argument 'p'/)
    print("order", t > 1000)
    # by keyword: the copy is found at the parameter the keyword binds
    bump_kw(p=max(walk(ps), key=key_of))  # tpyc: warning(/copies P into argument 'p' of 'bump_kw/)
    print("keyword", ps[0].v, ps[1].v)


main()
