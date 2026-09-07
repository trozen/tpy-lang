# An Own-returning getter bound to a local: the alias would bind a reference to
# the by-value result, so the declaration rejects.
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
    v = s.snapshot  # tpyc: error(/decl.own_property_alias/)
    print(len(v))


def main() -> None:
    use(Snap())


main()
