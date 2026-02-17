# Basic @nocopy: create, consume at last use via auto-move
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def close(h: Own[Handle]) -> Int32:
    return h.fd


def main():
    h = Handle()
    h.fd = 42
    print(close(h))  # tpyc: ok


main()
