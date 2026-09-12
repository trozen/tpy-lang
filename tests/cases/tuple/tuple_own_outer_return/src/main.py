# Returning a pair of @nocopy elements via `Own[tuple[T, T]]` (Form B):
# the outer Own implies the value tuple owns its non-value elements.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32) -> None:
        self.fd = fd


def make_pair() -> Own[tuple[Handle, Handle]]:
    a = Handle(int32(10))
    b = Handle(int32(20))
    return (a, b)  # tpyc: ok


def main() -> None:
    pair = make_pair()
    print(pair[0].fd)
    print(pair[1].fd)


main()
