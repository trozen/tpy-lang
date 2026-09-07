# An UNPROVEN Optional receiver in front of a user `__deref__` chain carries the
# runtime check, which the deref-chain rows exclude. Concretely, `r.x` where
# `r: Ref | None` is never proven non-None before the field read; TPy
# rejects that access today.
from tpy import Int32, auto_readonly, copy


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


class Ref:
    _target: Point

    def __init__(self, target: Point) -> None:
        self._target = copy(target)

    @auto_readonly
    def __deref__(self) -> Point:
        return self._target


def use(r: Ref | None) -> None:
    print(r.x)  # tpyc: error(/field.receiver_shape/)


def main() -> None:
    use(Ref(Point(1, 2)))


main()
