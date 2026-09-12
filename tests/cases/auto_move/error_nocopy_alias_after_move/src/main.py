# @nocopy: alias used after owner move point -- move suppressed, error fires.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def close(h: Own[Handle]) -> int32:
    return h.fd


def main():
    h = Handle()
    h.fd = 42
    alias = h
    close(h)           # tpyc: error(/@nocopy.*used after/)
    print(alias.fd)


main()
