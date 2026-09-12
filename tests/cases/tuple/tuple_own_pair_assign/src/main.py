# Local var assignment with a `tuple[T, T]` annotation of @nocopy elements.
# Each element moves into the tuple at last-use of the local (the move comes
# from last-use, not from any annotation).
from tpy import int32, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32) -> None:
        self.fd = fd


def main() -> None:
    a = Handle(int32(7))
    b = Handle(int32(8))
    pair: tuple[Handle, Handle] = (a, b)  # tpyc: ok
    print(pair[0].fd)
    print(pair[1].fd)


main()
