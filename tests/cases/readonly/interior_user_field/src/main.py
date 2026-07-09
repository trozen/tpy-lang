# unsafe_interior_mutable[Ptr[T]] on a user type: a @readonly method mutates through the
# interior field (the bump is bookkeeping outside the readonly boundary);
# observing the bumped value proves the mutation took effect.
from tpy import Int32, Ptr, nocopy, unsafe_interior_mutable, readonly
from tpy.unsafe import unsafe_take, unsafe_release


@nocopy
class Cell:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n = self.n + 1


@nocopy
class Counter:
    _cell: unsafe_interior_mutable[Ptr[Cell]]

    def __init__(self) -> None:
        self._cell = unsafe_take(Cell(0))

    def __del__(self) -> None:
        unsafe_release(self._cell)

    @readonly
    def tick(self) -> None:
        self._cell.bump()

    @readonly
    def value(self) -> Int32:
        return self._cell.n


def main() -> None:
    c = Counter()
    c.tick()
    c.tick()
    c.tick()
    print(c.value())


main()
