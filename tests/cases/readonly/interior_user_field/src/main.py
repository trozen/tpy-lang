# Refcount-style bookkeeping behind a Ptr field on a user type: a @readonly
# method mutates through the pointer (readonly does not reach through a Ptr,
# so the field needs no marker); observing the bumped value proves the
# mutation took effect.
from tpy import int32, Ptr, nocopy, readonly
from tpy.unsafe import unsafe_take, unsafe_release


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n = self.n + 1


@nocopy
class Counter:
    _cell: Ptr[Cell]

    def __init__(self) -> None:
        self._cell = unsafe_take(Cell(0))

    def __del__(self) -> None:
        unsafe_release(self._cell)

    @readonly
    def tick(self) -> None:
        self._cell.bump()

    @readonly
    def value(self) -> int32:
        return self._cell.n


def main() -> None:
    c = Counter()
    c.tick()
    c.tick()
    c.tick()
    print(c.value())


main()
