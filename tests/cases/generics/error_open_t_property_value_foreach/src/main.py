# The VALUE half of the instantiation rule: a bare-`T` getter is spelled
# `val_or_ref_t<T>`, so at a VALUE argument it hands its result back by value
# and the frame's by-value fence takes it (BUGS.md#own-property-iterable-materialize).
# The reference half -- the same getter at a container argument, which aliases
# on both routes -- is pinned by generics/inherited_accessor_subst.
from typing import Iterator

from tpy import int32


class Cell[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    @property
    def payload(self) -> T:
        return self.v


# generator whose loop body suspends, `T` bound to `str`
def chars(c: Cell[str]) -> Iterator[int32]:  # tpyc: error(/res.by_value_property_iter/)
    for ch in c.payload:
        print(ch)
        yield 1


def main() -> None:
    for n in chars(Cell[str]("ab")):
        print(n)


main()
