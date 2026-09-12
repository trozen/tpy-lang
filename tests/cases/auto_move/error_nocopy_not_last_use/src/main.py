# @nocopy consumed but not at last use -- must error
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def close(h: Own[Handle]) -> int32:
    return h.fd


def main():
    h = Handle()
    h.fd = 42
    close(h)       # tpyc: error(/@nocopy.*used after this point/)
    print(h.fd)


main()
