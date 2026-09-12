# A @nocopy concrete type used as the class T parameter must not satisfy
# the Copyable shadow bound on a user method, mirroring how Box.clone is
# gated. Verifies the marker arm rejects @nocopy types directly (without
# relying on Box/Rc as the carrier).
from tpy import int32, Own, Copyable, nocopy


@nocopy
class MoveOnly:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Cell[T]:
    value: T

    def __init__(self, value: Own[T]) -> None:
        self.value = value

    def duplicate[T: Copyable](self) -> T:
        return self.value


def main() -> None:
    c = Cell[MoveOnly](MoveOnly(int32(7)))
    _ = c.duplicate()  # tpyc: error(/Method 'duplicate' requires type parameter 'T' to satisfy 'Copyable'/)


main()
