# An `Own[...]`-returning @property iterated inside a resumable frame, whose
# iterator pair would outlive the temporary it is built off
# (BUGS.md#own-property-iterable-materialize).
# Value-returning sibling: error_gen_value_property_foreach.
from typing import Iterator

from tpy import int32, Own


class Snap:
    _items: list[int32]

    def __init__(self) -> None:
        self._items = [1, 2]

    @property
    def snapshot(self) -> Own[list[int32]]:
        out: list[int32] = []
        for x in self._items:
            out.append(x)
        return out


class Outer:
    inner: Snap

    def __init__(self) -> None:
        self.inner = Snap()


# Own getter, two hops -- the annotated leg, so it also pins the ORDER: the
# by-value verdict is asked before the placeability one, which would otherwise
# answer res.for_iter_borrow_unplaceable at this depth
def gen_deep_snapshot(o: Outer) -> Iterator[int32]:  # tpyc: error(/res.by_value_property_iter/)
    for x in o.inner.snapshot:
        yield x


# Own getter, one hop: the shape the entry was filed on; unannotated, since the
# compile stops at the first error
def gen_snapshot(s: Snap) -> Iterator[int32]:
    for x in s.snapshot:
        yield x


def main() -> None:
    for v in gen_deep_snapshot(Outer()):
        print(v)


main()
