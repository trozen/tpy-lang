# Borrow-tuple sinks off DEEP lvalue sources rooted at a const receiver
# (self.store[k] in a readonly method, rows[i] off a const param) must spell
# const element pointers; bump() is the mutation inverse (receiver demoted,
# write observed through the field -- the reads alias, they don't copy).
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def weight_of(t: tuple[Int32, Box]) -> Int32:
    return t[0]


class Holder:
    store: dict[str, tuple[Int32, Box]]
    def __init__(self, a: Box, b: Box) -> None:
        self.store = {"a": (40, a), "b": (41, b)}  # tpyc: warning(/copies Box into owned storage/) warning(/copies Box into owned storage/)

    def get_weight(self, k: str) -> Int32:
        pair = self.store[k]
        if pair[0] < 0:
            pair = self.store["a"]
        return pair[0]

    def peek(self) -> Int32:
        pair = self.store["a"]
        return pair[0]

    def arg_weight(self, k: str) -> Int32:
        return weight_of(self.store[k])

    def bump(self, k: str) -> None:
        pair = self.store[k]
        pair[1].val = pair[1].val + 1


def first_weight(rows: list[tuple[Int32, Box]], again: bool) -> Int32:
    p = rows[0]
    if again:
        p = rows[1]
    return p[0]


def main() -> None:
    h = Holder(Box(5), Box(6))
    print(h.get_weight("b"))
    print(h.peek())
    print(h.arg_weight("b"))
    h.bump("a")
    print(h.store["a"][1].val)

    rows = [(40, Box(7)), (41, Box(8))]
    print(first_weight(rows, False))
    print(first_weight(rows, True))


main()
