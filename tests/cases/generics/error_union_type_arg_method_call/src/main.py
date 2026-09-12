# A method call on a generic record instantiated with a module-LOCAL union alias:
# the alias registers after lowering, so the receiver is outside the
# spelling-equal record slice and the call rejects.
from tpy import int32, Own


class Alpha:
    x: int32

    def __init__(self) -> None:
        self.x = 1


class Beta:
    y: int32

    def __init__(self) -> None:
        self.y = 2


Either = Alpha | Beta


class Holder[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v

    def show(self) -> None:
        print("held")


def touch(h: Holder[Either]) -> None:
    h.show()  # tpyc: error(/method.record.show/)


def main() -> None:
    touch(Holder[Either](Alpha()))


main()
