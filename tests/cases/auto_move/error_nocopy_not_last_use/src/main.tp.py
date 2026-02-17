# @nocopy consumed but not at last use -- must error
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def close(h: Own[Handle]) -> Int32:
    return h.fd


def main():
    h = Handle()
    h.fd = 42
    close(h)       # tpyc: error(/@nocopy.*used after this point/)
    print(h.fd)


main()
