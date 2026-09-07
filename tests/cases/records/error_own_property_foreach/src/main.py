# An Own-returning getter as a for-each iterable: the loop would bind a reference
# to the by-value getter result, so the iteration rejects.
from tpy import Int32, Own


class Snap:
    _items: list[Int32]

    def __init__(self) -> None:
        self._items = [1]

    @property
    def snapshot(self) -> Own[list[Int32]]:
        out: list[Int32] = []
        for x in self._items:
            out.append(x)
        return out


def use(s: Snap) -> None:
    for x in s.snapshot:  # tpyc: error(/foreach.own_property_iter/)
        print(x)


def main() -> None:
    use(Snap())


main()
