# @nocopy: alias used after owner move point -- move suppressed, error fires.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def close(h: Own[Handle]) -> Int32:
    return h.fd


def main():
    h = Handle()
    h.fd = 42
    alias = h
    close(h)           # tpyc: error(/@nocopy.*used after/)
    print(alias.fd)


main()
