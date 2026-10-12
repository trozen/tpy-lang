# A generator expression yielding a rebound owning tuple whole is refused, as
# the named generator's `yield p` is: the yield has no element-by-element
# conversion from the pointer tuple (BUGS.md#genexpr-yields-captured-reference-local).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def f(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # The generator expression hands out `p` itself.
    for t in (p for i in range(2)):  # tpyc: error(/not yet supported by C\+\+ code generation \(res\.btuple_yield_source\)/)
        t[0].n += 10
    print("gx4", p[0].n)


def main() -> None:
    f(Box(8))


main()
