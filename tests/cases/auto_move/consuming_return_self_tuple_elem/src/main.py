# The third reader of "the receiver is owned": a tuple-literal element slot.
# `return (self, 1)` at `Own[tuple[Own[Self], Int32]]` used to be rejected as a
# borrowed source; the receiver of a consuming method is the frame's own value,
# so the element MOVES it instead. The `@nocopy` leg is the proof -- read-only
# output could not tell a move from a copy here, since the temporary receiver
# leaves no second handle to observe.
from typing import Self
from tpy import Int32, Own, nocopy


@nocopy
class Widget:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    # The subject: the receiver fills an owning tuple ELEMENT slot.
    def split(self: Own[Self]) -> Own[tuple[Own['Widget'], Int32]]:
        return (self, 1)  # tpyc: ok


class Plain:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def split(self: Own[Self]) -> Own[tuple[Own['Plain'], Int32]]:
        return (self, 2)  # tpyc: ok


def main() -> None:
    w, k = Widget(5).split()
    w.n = 9
    print(w.n, k)
    p, j = Plain(6).split()
    p.n = 8
    print(p.n, j)


main()
