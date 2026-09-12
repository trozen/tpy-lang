# Returning a pair of @nocopy / Own elements via `tuple[Own[T], Own[T]]`.
# Both elements move into the value tuple at last-use of the local.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32) -> None:
        self.fd = fd


def make_pair() -> tuple[Own[Handle], Own[Handle]]:
    a = Handle(int32(1))
    b = Handle(int32(2))
    return (a, b)  # tpyc: ok


def main() -> None:
    pair = make_pair()
    print(pair[0].fd)
    print(pair[1].fd)


main()
