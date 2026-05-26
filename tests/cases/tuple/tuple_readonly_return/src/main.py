# readonly[tuple[T, T]] return type: the tuple literal must construct
# const-borrow element slots (std::tuple<const P&, const P&>), not a
# value-form tuple that binds dangling const-refs into the return slot.
# Source reads from a readonly-borrowed container (const), so the slots
# must be const-ref to bind.
from tpy import Int32, readonly


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def pick(items: list[P]) -> readonly[tuple[P, P]]:
    return (items[0], items[1])


def main() -> None:
    items = [P(1), P(2), P(3)]
    pair = pick(items)
    print(pair[0].x)
    print(pair[1].x)


main()
