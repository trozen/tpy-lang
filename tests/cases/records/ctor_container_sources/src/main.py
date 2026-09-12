# Container fields fed from the three call/repeat sources: `[e] * n` in the
# member-init list, an Own[container]-returning free call, and a
# borrow-returning method (a warned, intended copy).
from tpy import int32, Own


def make_list(n: int32) -> Own[list[int32]]:
    out: list[int32] = []
    for i in range(n):
        out.append(i * 10)
    return out


class Holder:
    items: list[int32]

    def __init__(self) -> None:
        self.items = []

    def peek(self) -> list[int32]:
        return self.items


class Grid:
    cells: list[int32]
    tags: list[int32]
    data: list[int32]
    mirror: list[int32]

    def __init__(self, n: int32) -> None:
        self.cells = [0] * 8  # a repeat materializes the field's own container
        self.tags = [n] * 4  # ... and threads the field type for its element
        self.data = []
        self.mirror = []

    def fill_own(self, n: int32) -> None:
        self.data = make_list(n)  # the Own return lands by value, no copy

    # The mirror write COPIES where CPython aliases; sema warns on the line, so
    # the divergence is declared rather than silent. Nothing mutates `h.items`
    # afterwards, which is what keeps the printed output identical.
    def fill_borrow(self, h: Holder) -> None:
        self.mirror = h.peek()  # tpyc: warning(/copies list\[int32\] into field/)


def main() -> None:
    g = Grid(7)
    print(len(g.cells), g.cells[0], len(g.tags), g.tags[3])
    g.fill_own(3)
    print(len(g.data), g.data[2])
    h = Holder()
    h.items.append(42)
    g.fill_borrow(h)
    print(len(g.mirror), g.mirror[0])


main()
