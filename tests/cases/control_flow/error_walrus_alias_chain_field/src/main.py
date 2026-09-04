# A walrus borrow-alias whose FIELD source hangs off a chained receiver
# (`h.mid.inner`, not a plain name) has no lowering arm: only a NAME receiver is
# admitted, so the chain must keep rejecting rather than pick a neighbouring
# render.
class Rec:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


class Mid:
    inner: Rec

    def __init__(self) -> None:
        self.inner = Rec(1)


class Holder:
    mid: Mid

    def __init__(self) -> None:
        self.mid = Mid()


def f(h: Holder) -> int:
    if (q := h.mid.inner).n > 0:  # tpyc: error(/expr\.walrus/)
        q.n += 1
        return q.n
    return 0


def main() -> None:
    print(f(Holder()))


main()
