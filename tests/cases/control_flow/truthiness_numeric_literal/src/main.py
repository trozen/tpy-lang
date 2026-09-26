# A numeric literal tested for truth folds to its constant truth in every
# truth-test position, so no bare literal reaches a C++ condition.
from typing import Iterator

from tpy import int32


def conditions(a: int32, c: bool) -> None:
    # if / while heads, including zero and negative literals.
    if 2.5:  # tpyc: ok
        print("if_float")
    if 0.0 or -0.0:  # tpyc: ok
        print("if_zero_float")
    if -2.5:  # tpyc: ok
        print("if_neg_float")
    n = 0
    while 1:  # tpyc: ok
        n += 1
        if n == 2:
            break
    print("while_int:", n)
    # A literal on either side of and/or.
    if a or 2.5:  # tpyc: ok
        print("or_float")
    if 2.5 and a:  # tpyc: ok
        print("and_float_left")
    if a and 2:  # tpyc: ok
        print("and_int")
    if a or 0:  # tpyc: ok
        print("or_zero")
    if a or 1e300:  # tpyc: ok
        print("or_big_float")
    # Unary plus and a fixed-int ctor of a literal.
    if a or +2.5:  # tpyc: ok
        print("or_unary_plus")
    if a and int32(2):  # tpyc: ok
        print("and_fixed_int")
    # not, a ternary test, a truth-tested ternary arm, assert.
    print("not_literal:", not 2.5, not (a or 0))  # tpyc: ok
    print("ternary_test:", 1 if 2.5 else 0)  # tpyc: ok
    if (a if c else 2.5):  # tpyc: ok
        print("ternary_arm")
    assert 2.5  # tpyc: ok
    print("assert_ok")
    # A falsy literal assert always raises.
    try:
        assert 0.0, "zero"  # tpyc: ok
    except AssertionError as e:
        print("assert_zero:", e)


def filters(a: int32) -> None:
    # Comprehension and generator-expression filters.
    xs = [x for x in [1, 2, 3] if a or 0.0]  # tpyc: ok
    print("comp_filter:", len(xs))
    print("genexpr_filter:", sum(1 for x in [1, 2, 3] if 2.5))  # tpyc: ok


def guard(k: int32) -> str:
    match k:
        # A match guard.
        case 1 if 0.0:  # tpyc: ok
            return "never"
        case _ if 2.5:  # tpyc: ok
            return "guard"
        case _:
            return "none"


def gen(a: int32) -> Iterator[int32]:
    # A generator (resumable-frame) body.
    if a or 2.5:  # tpyc: ok
        yield 1
    yield 2


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def run(self) -> None:
        # A method body.
        while self.n < 3 and 2.5:  # tpyc: ok
            self.n += 1


def main() -> None:
    for a, c in [(0, True), (3, False)]:
        print("a =", a, c)
        conditions(a, c)
        filters(a)
        print("gen:", [v for v in gen(a)])
    print("guard:", guard(1), guard(2))
    k = Counter()
    k.run()
    print("method:", k.n)


main()
# Module-level statements.
if 2.5 and 0:  # tpyc: ok
    print("module_never")
if 0.0 or 3:  # tpyc: ok
    print("module_if")
