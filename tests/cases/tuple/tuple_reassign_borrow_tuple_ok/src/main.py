# A REASSIGNED tuple local DECL-INITIALIZED from a borrow source aliases its
# elements (it does not own them), so the decl-init must NOT warn "copies into
# owned storage". Read-only here: a write through a multi-source borrow-tuple
# local compiles (each binding marks its source's root written, so the element
# pointers are mutable) and is pinned by tuple/borrow_tuple_rebind_marks_root.


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
