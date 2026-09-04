# Both arms of a member-init record ternary are plain names: the C++ `?:` is
# then an LVALUE, not the prvalue the member-init row renders, so the shape
# must keep rejecting instead of borrowing the prvalue arm's render.


class V3:
    x: float

    def __init__(self, x_: float) -> None:
        self.x = x_


class Pair:
    a: V3

    def __init__(self, p: V3, q: V3, c: bool) -> None:
        self.a = p if c else q  # tpyc: error(/expr.ifexpr/)


def main() -> None:
    r = Pair(V3(1.0), V3(2.0), True)
    print(r.a.x)


main()
