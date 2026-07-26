# INVERSE guard: a nested match binding a DIFFERENT name must not disturb the
# outer capture -- the outer binding keeps aliasing its own subject rather than
# being hoisted and re-seated. The nested-capture rebind detection must key on
# the name, not on the mere presence of a nested match in the arm.
class Inner:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n


class Holder:
    inner: Inner
    tag: int
    def __init__(self, inner: Inner, tag: int) -> None:
        self.inner = inner
        self.tag = tag


def keeps_alias(h: Holder, g: Holder) -> int:
    match h:
        case Holder(inner=q):
            match g:
                case Holder(tag=t):   # tpyc: ok -- different name
                    pass
            h.inner.n = 42            # mutate through the subject
            return q.n + t            # 42 + 7 -- q still aliases h.inner
    return -1


def main() -> None:
    print(keeps_alias(Holder(Inner(1), 3), Holder(Inner(2), 7)))


main()
