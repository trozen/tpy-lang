# readonly[tuple[T, T]] return type: the tuple literal must construct
# const-borrow element slots (std::tuple<const P&, const P&>), not a
# value-form tuple that binds dangling const-refs into the return slot.
# Source reads from a readonly-borrowed container (const), so the slots
# must be const-ref to bind.
from tpy import int32, readonly


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def pick(items: list[P]) -> readonly[tuple[P, P]]:
    return (items[0], items[1])


def main() -> None:
    items = [P(1), P(2), P(3)]
    pair = pick(items)
    print(pair[0].x)
    print(pair[1].x)
    # The tuple is readonly, so the mutation goes SOURCE -> alias: 99 proves
    # the tuple holds `const P*` into items; a deep copy would still read 1.
    items[0].x = 99
    print(pair[0].x)


main()
