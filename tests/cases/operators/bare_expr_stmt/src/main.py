# An expression evaluated and DISCARDED at statement position: an arithmetic
# binop, a unary op, a bool op, a ternary, a bare comparison, a chained
# comparison, a field read, a bare literal and a bare name. Python evaluates
# each and throws the value away, so the checked operand renders must survive.
#
# The PURE forms (`n < 2`, `0 < n < 5`, `p.x`, `7`, `n`) render nothing but a
# value, so they emit as `(void)(...)` -- a bare `(n < 2);` trips GCC's
# -Wunused-value, which this suite builds with -Werror. This case is where
# that build is proven.
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def peek(n: Int32) -> Int32:
    print("peeked")
    return n


def main() -> None:
    n = 1
    ok = True
    p = Point(7)
    n + 1  # tpyc: ok
    -n  # tpyc: ok
    # The pure forms carry no effect of their own -- they exist to prove the
    # statement builds under the strict warning set.
    n < 2  # tpyc: ok
    0 < n < 5  # tpyc: ok
    p.x  # tpyc: ok
    # The sibling rows -- a bare literal (which the REPL appends to every
    # compile) and a bare name. Both take the same cast for the same reason.
    7  # tpyc: ok
    n  # tpyc: ok
    # The operand is still EVALUATED -- `peek` prints, so a dropped
    # statement would be visible in the output.
    ok and peek(n) > 0  # tpyc: ok
    peek(n) if ok else 0  # tpyc: ok
    z = 0
    try:
        # ... and the discarded division still raises.
        n // z  # tpyc: ok
        print("not reached")
    except ZeroDivisionError:
        print("divided by zero")
    print(n, p.x)


main()
