# Basic @nocopy: create, consume at last use via auto-move
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def close(h: Own[Handle]) -> int32:
    return h.fd


def main():
    h = Handle()
    h.fd = 42
    print(close(h))  # tpyc: ok


main()
