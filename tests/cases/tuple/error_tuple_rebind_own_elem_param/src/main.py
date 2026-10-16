# A rebound tuple one binding of which borrows the element a parameter owns,
# passed whole to it: the element copies (warned) and the other is handed on,
# which the argument does not render yet
# (BUGS.md#ptr-tuple-name-own-element-param-rejects).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def mk(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def take(t: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/Own\[Box\] element 0 of tuple param 't' is never consumed/)
    t[1].n += 100
    a, b = t
    return a.n


def f(v: Box, w: Box, c: bool) -> None:
    p = (w, v)
    if c:
        p = mk(1, v)
    # Element 0 is 'w' at the literal's binding: 'p' does not own it.
    print(take(p), v.n, w.n)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/) error(/not yet supported by C\+\+ code generation \(stmt.expr_stmt:call.arg_shape.tuple\)/)


f(Box(3), Box(4), False)
