# A record field read through a ternary over two tuple elements
# (`(t[0] if c else t[1]).xs[1]`) has no declaration slot render and is
# rejected at the binding, rebind of `t` or not
# (BUGS.md#reference-ternary-position-gaps).
from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    xs: list[A]

    def __init__(self, n: int32) -> None:
        self.xs = [A(n), A(n + 1)]


def pick(c: bool) -> None:
    t = (H(1), H(5))
    saved = (t[0] if c else t[1]).xs[1]  # tpyc: error(/not yet supported by C\+\+ code generation \(stmt\.var_decl:decl\.slot_type\)/)
    if c:
        t = (H(10), H(20))
    saved.v += 100
    print("pick", saved.v, t[0].xs[1].v)


def main() -> None:
    pick(True)


main()
