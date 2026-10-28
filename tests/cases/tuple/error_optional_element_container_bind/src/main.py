# A local bound to an Optional element of a tuple that lies in a list is
# refused: the pointer is not tracked as a borrow of the list, which an
# append would leave dangling (BUGS.md#tuple-elem-of-container-elem-bind-rejects).
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    ts: list[tuple[Box | None, int32]] = [(Box(1), 1)]
    e = ts[0][0]  # tpyc: error(/decl.slot_type/)
    ts.append((Box(2), 2))
    if e is not None:
        print(e.n)


main()
