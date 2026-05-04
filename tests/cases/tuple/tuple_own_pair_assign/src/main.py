# Local var assignment with `tuple[Own[T], Own[T]]` annotation.
# Each element moves into the tuple at last-use of the local.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32) -> None:
        self.fd = fd


def main() -> None:
    a = Handle(Int32(7))
    b = Handle(Int32(8))
    pair: tuple[Own[Handle], Own[Handle]] = (a, b)  # tpyc: ok
    print(pair[0].fd)
    print(pair[1].fd)


main()
