# An augmented assignment through min / max / next (`max(a, b, key=f).v += 5`)
# would evaluate the call twice -- running the key twice, or advancing the
# iterator twice -- so it is refused until aug-assign binds its receiver once,
# a lowering limitation of these calls
# (BUGS.md#augassign-call-receiver-double-eval). `next(it, d).v += 100` is
# refused the same way.
class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def key_of(p: P) -> int:
    return p.v


def main() -> None:
    a = P(1)
    b = P(2)
    max(a, b, key=key_of).v += 5  # tpyc: error(/evaluated twice by an augmented assignment/)
    print(a.v, b.v)


main()
