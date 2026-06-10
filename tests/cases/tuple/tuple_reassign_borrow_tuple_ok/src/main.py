# A REASSIGNED tuple local DECL-INITIALIZED from a borrow source aliases its
# elements (it does not own them), so the decl-init must NOT warn "copies into
# owned storage". Read-only: write-through of a multi-source borrow-tuple local
# is not yet supported (const propagates from the differing sources), so only
# the no-warning behavior is exercisable here.


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


class Holder:
    pair: tuple[int, Box]

    def __init__(self, b: Box):
        self.pair = (1, b)  # tpyc: warning(/copies Box into field/)


def f(h: Holder, h2: Holder) -> int:
    t: tuple[int, Box] = h.pair  # tpyc: ok
    t = h2.pair
    return t[1].val


def main() -> None:
    print(f(Holder(Box(5)), Holder(Box(7))))


main()
