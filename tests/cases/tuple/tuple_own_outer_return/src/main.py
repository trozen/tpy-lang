# Returning a pair of @nocopy elements via `Own[tuple[T, T]]` (Form B):
# the outer Own implies the value tuple owns its non-value elements.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32) -> None:
        self.fd = fd


def make_pair() -> Own[tuple[Handle, Handle]]:
    a = Handle(Int32(10))
    b = Handle(Int32(20))
    return (a, b)  # tpyc: ok


def main() -> None:
    pair = make_pair()
    print(pair[0].fd)
    print(pair[1].fd)


main()
