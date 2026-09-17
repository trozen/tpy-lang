# An Own-returning getter as a for-each iterable: the loop would bind a reference
# to the by-value getter result, so the iteration rejects
# (BUGS.md#own-property-iterable-materialize).
# Workaround: bind the result first -- `snap = s.snapshot` then `for x in snap:`,
# which owns it explicitly (tests/cases/records/own_property_value_decl).
# The frame route fences the same family wider (tests/cases/generators/
# error_gen_own_property_foreach and error_gen_value_property_foreach).
from tpy import int32, Own


class Snap:
    _items: list[int32]

    def __init__(self) -> None:
        self._items = [1]

    @property
    def snapshot(self) -> Own[list[int32]]:
        out: list[int32] = []
        for x in self._items:
            out.append(x)
        return out


def use(s: Snap) -> None:
    for x in s.snapshot:  # tpyc: error(/foreach.by_value_property_iter/)
        print(x)


def main() -> None:
    use(Snap())


main()
