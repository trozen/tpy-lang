# @nocopy type in Own[T] | None param: not at last use should error.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def close_own(h: Own[Handle]) -> int32:
    return h.fd


def close_optional(h: Own[Handle] | None) -> int32:
    if h is None:
        return int32(-1)
    return close_own(h)


def main():
    h = Handle()
    h.fd = 42
    close_optional(h)       # tpyc: error(/@nocopy.*used after this point/)
    print(h.fd)


main()
