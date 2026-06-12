# A once-owned local reassigned to a borrow-alias, then consumed into an Own[T]
# sink, must copy into the sink -- never move-steal the aliased source.
from tpy import Own, nocopy


class Bag:
    xs: list[int]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]

    def itself(self) -> "Bag":
        return self


def take(b: Own[Bag]) -> Own[Bag]:
    return b


def call_source() -> None:
    g = Bag()
    b = Bag()
    b = g.itself()
    take(b)  # tpyc: warning(/copies Bag into owned storage/)
    print(len(g.xs))
    g.xs.append(9)
    print(len(g.xs))


def ternary_source(flag: bool) -> None:
    g = Bag()
    h = Bag()
    b = Bag()
    b = g.itself() if flag else h.itself()
    take(b)  # tpyc: warning(/copies Bag into owned storage/)
    # Mutate the live source so a steal (empty source) would diverge.
    if flag:
        g.xs.append(9)
        print(len(g.xs))
    else:
        h.xs.append(9)
        print(len(h.xs))


@nocopy
class Token:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def sink(t: Own[Token]) -> Own[Token]:
    return t


def owned_still_moves() -> None:
    # Token is @nocopy: a spurious copy here would be a compile error, so this
    # compiling proves a genuinely-owned local still auto-moves.
    t = Token(7)
    out = sink(t)
    print(out.n)


call_source()
ternary_source(True)
ternary_source(False)
owned_still_moves()
