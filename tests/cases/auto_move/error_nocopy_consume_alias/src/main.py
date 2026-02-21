# T& alias of @nocopy can't be consumed (not movable)
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def close(h: Own[Handle]) -> Int32:
    return h.fd


def main():
    h = Handle()
    alias = h
    close(alias)  # tpyc: error(/@nocopy.*cannot be copied into/)


main()
