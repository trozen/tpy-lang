# @nocopy type in Own[T] | None param: not at last use should error.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def close_own(h: Own[Handle]) -> Int32:
    return h.fd


def close_optional(h: Own[Handle] | None) -> Int32:
    if h is None:
        return Int32(-1)
    return close_own(h)


def main():
    h = Handle()
    h.fd = 42
    close_optional(h)       # tpyc: error(/@nocopy.*used after this point/)
    print(h.fd)


main()
