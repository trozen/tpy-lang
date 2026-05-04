# `tuple[T, T]` field with @nocopy elements: same shape works because
# field-tuple assignment auto-moves last-use owned locals (no `Own[T]`
# annotation needed -- mirrors scalar `field: T` semantics).
from tpy import nocopy, Int32


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32) -> None:
        self.fd = fd


class Container:
    pair: tuple[Handle, Handle]

    def __init__(self) -> None:
        a = Handle(Int32(1))
        b = Handle(Int32(2))
        self.pair = (a, b)


def main() -> None:
    c = Container()
    print(c.pair[0].fd)
    print(c.pair[1].fd)


main()
