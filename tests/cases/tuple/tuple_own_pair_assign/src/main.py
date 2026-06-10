# Local var assignment with a `tuple[T, T]` annotation of @nocopy elements.
# Each element moves into the tuple at last-use of the local (the move comes
# from last-use, not from any annotation).
from tpy import Int32, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32) -> None:
        self.fd = fd


def main() -> None:
    a = Handle(Int32(7))
    b = Handle(Int32(8))
    pair: tuple[Handle, Handle] = (a, b)  # tpyc: ok
    print(pair[0].fd)
    print(pair[1].fd)


main()
